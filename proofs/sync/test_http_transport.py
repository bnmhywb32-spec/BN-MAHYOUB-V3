"""Loopback-only transport fixture for exercising real HTTP request boundaries."""

import json
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from proofs.sync.authority_hub import AuthorityHub
from proofs.sync.local_queue import Queue


def endpoint(database):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.connection.settimeout(5)
            try:
                scheme, _, token = self.headers.get("Authorization", "").partition(" ")
                if scheme != "Bearer":
                    raise ValueError("SYNC_DENIED")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 16384:
                    raise ValueError("INVALID_REQUEST")
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError("INVALID_REQUEST")
                hub = AuthorityHub(database)
                try:
                    if self.path == "/submit" and set(body) == {"operation_id", "payload"}:
                        response = hub.submit(token, body["operation_id"], body["payload"])
                    elif self.path == "/pull" and set(body) == {"after"}:
                        response = hub.pull(token, body["after"])
                    else:
                        raise ValueError("INVALID_REQUEST")
                finally:
                    hub.db.close()
            except (TypeError, ValueError) as error:
                code = str(error)
                status = {"SYNC_DENIED": 403, "SYNC_CONFLICT": 409, "AUTHORITY_LIMIT": 409}.get(code, 400)
                self.reply(status, {"code": code if status != 400 else "INVALID_REQUEST"})
            else:
                self.reply(200, response)

        def reply(self, status, response):
            data = json.dumps(response).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


class HttpTransportProof(unittest.TestCase):
    def test_sale_retry_then_revocation(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(temp)
        hub = AuthorityHub(path / "authority.db")
        self.addCleanup(hub.db.close)
        queue = Queue(path / "device.db", "A")
        self.addCleanup(queue.close)
        hub.seed("A", "stock", 2)
        hub.allocate("A", "seller", "stock", 2)
        token = hub.issue_session("A", "seller")
        observer = hub.issue_session("A", "observer")
        other = hub.issue_session("B", "seller")
        payload = {"kind": "sale", "resource": "stock", "amount": 2}
        queue.enqueue(payload, "sale")

        with ThreadingHTTPServer(("127.0.0.1", 0), endpoint(path / "authority.db")) as server:
            worker = Thread(target=server.serve_forever)
            worker.start()
            try:
                def post(route, body, credential):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}{route}",
                        json.dumps(body).encode(),
                        {"Authorization": f"Bearer {credential}", "Content-Type": "application/json"},
                    )
                    try:
                        with urlopen(request, timeout=5) as response:
                            return response.status, json.load(response)
                    except HTTPError as error:
                        with error:
                            return error.code, json.load(error)

                command = {"operation_id": "sale", "payload": payload}
                self.assertEqual(post("/submit", command, observer), (403, {"code": "SYNC_DENIED"}))
                self.assertEqual(post("/submit", command, other), (403, {"code": "SYNC_DENIED"}))
                status, first = post("/submit", command, token)
                self.assertEqual(status, 200)
                self.assertEqual(first["revision"], 1)
                # Simulate losing acknowledgement at the client boundary.
                self.assertEqual(len(queue.pending()), 1)
                self.assertEqual(post("/submit", command, token), (200, first))
                queue.acknowledge("sale", first)
                self.assertEqual(post("/pull", {"after": 0}, other), (200, []))
                self.assertEqual(post("/pull", {"after": 0}, observer), (200, [first]))
                queue.receive([first])
                self.assertEqual(queue.revision(), 1)
                self.assertEqual(queue.pending(), [])
                self.assertEqual(hub.db.execute("SELECT COUNT(*), SUM(amount) FROM journal").fetchone(), (4, 0))
                hub.revoke_session(token)
                self.assertEqual(post("/submit", command, token), (403, {"code": "SYNC_DENIED"}))
                self.assertEqual(post("/pull", {"after": 0}, token), (403, {"code": "SYNC_DENIED"}))
            finally:
                server.shutdown()
                worker.join()


if __name__ == "__main__":
    unittest.main()
