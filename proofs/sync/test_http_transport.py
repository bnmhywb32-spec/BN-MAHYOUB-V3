"""Loopback-only transport fixture for exercising real HTTP request boundaries."""

import hashlib
import json
import ssl
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from proofs.search.local_search import Search
from proofs.search.scoped_search import find_authorized
from proofs.sync.authority_hub import AuthorityHub
from proofs.sync.offline_sale import OfflineSale


def endpoint(database, *, require_client_certificate=False):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.connection.settimeout(5)
            try:
                fingerprint = None
                if require_client_certificate:
                    certificate = self.connection.getpeercert(binary_form=True)
                    if not certificate:
                        raise ValueError("SYNC_DENIED")
                    fingerprint = hashlib.sha256(certificate).hexdigest()
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
                        response = hub.submit(token, body["operation_id"], body["payload"], peer_fingerprint=fingerprint)
                    elif self.path == "/pull" and set(body) == {"after"}:
                        response = hub.pull(token, body["after"], peer_fingerprint=fingerprint)
                    elif self.path == "/search" and set(body) == {"branch", "query"}:
                        response = find_authorized(
                            hub, Search(hub.db), token, body["branch"], body["query"],
                            peer_fingerprint=fingerprint,
                        )
                    else:
                        raise ValueError("INVALID_REQUEST")
                finally:
                    hub.db.close()
            except (TypeError, ValueError) as error:
                code = str(error)
                status = {"SYNC_DENIED": 403, "SEARCH_DENIED": 403, "SYNC_CONFLICT": 409, "AUTHORITY_LIMIT": 409}.get(code, 400)
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
        queue = OfflineSale(path / "device.db", "A", 2)
        self.addCleanup(queue.close)
        hub.seed("A", "stock", 2)
        hub.allocate("A", "seller", "stock", 2)
        token = hub.issue_session("A", "seller", can_submit=True)
        observer = hub.issue_session("A", "seller")
        other = hub.issue_session("B", "seller", can_submit=True)
        payload = queue.sell("sale", 2)
        self.assertEqual(queue.sell("sale", 2), payload)
        self.assertEqual(queue.db.execute("SELECT remaining FROM offline_stock").fetchone(), (0,))
        self.assertEqual(queue.db.execute("SELECT COUNT(*), SUM(amount) FROM offline_journal").fetchone(), (4, 0))

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
                with ThreadPoolExecutor(max_workers=2) as pool:
                    responses = list(pool.map(lambda _: post("/submit", command, token), range(2)))
                status, first = responses[0]
                self.assertEqual(status, 200)
                self.assertEqual(responses[1], (200, first))
                self.assertEqual(first["revision"], 1)
                # Simulate losing acknowledgement at the client boundary.
                self.assertEqual(len(queue.pending()), 1)
                self.assertEqual(post("/submit", command, token), (200, first))
                self.assertEqual(
                    post("/submit", dict(command, operation_id="second-sale"), token),
                    (409, {"code": "AUTHORITY_LIMIT"}),
                )
                wrong_scope = dict(first, company="B")
                with self.assertRaisesRegex(ValueError, "^SYNC_SCOPE_MISMATCH$"):
                    queue.acknowledge("sale", wrong_scope)
                with self.assertRaisesRegex(ValueError, "^SYNC_SCOPE_MISMATCH$"):
                    queue.receive([wrong_scope])
                self.assertEqual(queue.revision(), 0)
                self.assertEqual(len(queue.pending()), 1)
                queue.acknowledge("sale", first)
                self.assertEqual(post("/pull", {"after": 0}, other), (200, []))
                self.assertEqual(post("/pull", {"after": 0}, observer), (200, [first]))
                queue.receive([first])
                self.assertEqual(queue.revision(), 1)
                self.assertEqual(queue.sell("sale", 2), payload)
                self.assertEqual(queue.pending(), [])
                self.assertEqual(hub.db.execute("SELECT COUNT(*), SUM(amount) FROM journal").fetchone(), (4, 0))
                hub.revoke_session(token)
                self.assertEqual(post("/submit", command, token), (403, {"code": "SYNC_DENIED"}))
                self.assertEqual(post("/pull", {"after": 0}, token), (403, {"code": "SYNC_DENIED"}))
            finally:
                server.shutdown()
                worker.join()

    def test_tls_peer_verification_and_revoked_session(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(temp)
        certificate = path / "test-cert.pem"
        key = path / "test-key.pem"
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-noenc",
                "-days", "1", "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
                "-keyout", str(key), "-out", str(certificate),
            ],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        hub = AuthorityHub(path / "authority.db")
        self.addCleanup(hub.db.close)
        hub.seed("A", "stock", 1)
        hub.allocate("A", "seller", "stock", 1)
        token = hub.issue_session("A", "seller", can_submit=True)
        queue = OfflineSale(path / "device.db", "A", 1)
        self.addCleanup(queue.close)
        payload = queue.sell("sale", 1)
        body = {"operation_id": "sale", "payload": payload}

        with ThreadingHTTPServer(("127.0.0.1", 0), endpoint(path / "authority.db")) as server:
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.minimum_version = ssl.TLSVersion.TLSv1_2
            tls.load_cert_chain(certificate, key)
            server.socket = tls.wrap_socket(server.socket, server_side=True)
            worker = Thread(target=server.serve_forever)
            worker.start()
            try:
                def post(host, context, credential):
                    request = Request(
                        f"https://{host}:{server.server_port}/submit",
                        json.dumps(body).encode(),
                        {"Authorization": f"Bearer {credential}", "Content-Type": "application/json"},
                    )
                    try:
                        with urlopen(request, timeout=5, context=context) as response:
                            return response.status, json.load(response)
                    except HTTPError as error:
                        with error:
                            return error.code, json.load(error)

                trusted = ssl.create_default_context(cafile=str(certificate))
                for host, context in (
                    ("localhost", ssl.create_default_context()),
                    ("127.0.0.1", trusted),
                ):
                    with self.subTest(host=host), self.assertRaises(URLError) as failure:
                        post(host, context, token)
                    self.assertIsInstance(failure.exception.reason, ssl.SSLCertVerificationError)

                self.assertEqual(hub.db.execute("SELECT COUNT(*) FROM operations").fetchone(), (0,))
                self.assertEqual(post("localhost", trusted, "invalid"), (403, {"code": "SYNC_DENIED"}))
                status, first = post("localhost", trusted, token)
                self.assertEqual(status, 200)
                self.assertEqual(post("localhost", trusted, token), (200, first))
                queue.acknowledge("sale", first)
                self.assertEqual(queue.pending(), [])
                self.assertEqual(hub.db.execute("SELECT COUNT(*), SUM(amount) FROM journal").fetchone(), (4, 0))
                hub.revoke_device("A", "seller")
                self.assertEqual(post("localhost", trusted, token), (403, {"code": "SYNC_DENIED"}))
            finally:
                server.shutdown()
                worker.join()

    def test_mutual_tls_binds_session_to_enrolled_device(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(temp)

        def run(*args):
            subprocess.run(["openssl", *args], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        ca_cert, ca_key = path / "ca.crt", path / "ca.key"
        run("req", "-x509", "-newkey", "rsa:2048", "-noenc", "-days", "1",
            "-subj", "/CN=V3 proof CA", "-keyout", str(ca_key), "-out", str(ca_cert))

        def certificate(name, extensions):
            key, request, cert = path / (name + ".key"), path / (name + ".csr"), path / (name + ".crt")
            ext = path / (name + ".ext")
            ext.write_text(extensions)
            run("req", "-newkey", "rsa:2048", "-noenc", "-subj", "/CN=" + name,
                "-keyout", str(key), "-out", str(request))
            run("x509", "-req", "-in", str(request), "-CA", str(ca_cert), "-CAkey", str(ca_key),
                "-CAcreateserial", "-days", "1", "-extfile", str(ext), "-out", str(cert))
            fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()
            return cert, key, fingerprint

        server_cert, server_key, _ = certificate(
            "localhost", "subjectAltName=DNS:localhost\nextendedKeyUsage=serverAuth\n"
        )
        cert_a, key_a, fingerprint_a = certificate("device-a", "extendedKeyUsage=clientAuth\n")
        cert_b, key_b, fingerprint_b = certificate("device-b", "extendedKeyUsage=clientAuth\n")
        hub = AuthorityHub(path / "authority.db")
        self.addCleanup(hub.db.close)
        hub.seed("A", "stock", 1)
        hub.allocate("A", "seller", "stock", 1)
        hub.enroll_device_certificate("A", "seller", fingerprint_a)
        hub.enroll_device_certificate("A", "other", fingerprint_b)
        hub.grant_search_branch("A", "seller", "1")
        Search(hub.db).put("A", "1", "visible", "001", "بوجي", "النجم")
        token = hub.issue_session("A", "seller", can_submit=True, peer_fingerprint=fingerprint_a)
        unbound = hub.issue_session("A", "seller", can_submit=True)
        queue = OfflineSale(path / "device.db", "A", 1)
        self.addCleanup(queue.close)
        sale = {"operation_id": "sale", "payload": queue.sell("sale", 1)}

        with ThreadingHTTPServer(("127.0.0.1", 0), endpoint(path / "authority.db", require_client_certificate=True)) as server:
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.minimum_version = ssl.TLSVersion.TLSv1_2
            tls.load_cert_chain(server_cert, server_key)
            tls.load_verify_locations(cafile=str(ca_cert))
            tls.verify_mode = ssl.CERT_REQUIRED
            server.socket = tls.wrap_socket(server.socket, server_side=True)
            worker = Thread(target=server.serve_forever)
            worker.start()
            try:
                def context(cert=None, key=None):
                    result = ssl.create_default_context(cafile=str(ca_cert))
                    if cert is not None:
                        result.load_cert_chain(cert, key)
                    return result

                def post(route, body, client, credential=token):
                    request = Request(
                        f"https://localhost:{server.server_port}{route}",
                        json.dumps(body).encode(),
                        {"Authorization": f"Bearer {credential}", "Content-Type": "application/json"},
                    )
                    try:
                        with urlopen(request, timeout=5, context=client) as response:
                            return response.status, json.load(response)
                    except HTTPError as error:
                        with error:
                            return error.code, json.load(error)

                with self.assertRaises((URLError, ssl.SSLError)):
                    post("/submit", sale, context())
                self.assertEqual(post("/submit", sale, context(cert_b, key_b)), (403, {"code": "SYNC_DENIED"}))
                self.assertEqual(post("/submit", sale, context(cert_a, key_a), unbound), (403, {"code": "SYNC_DENIED"}))
                self.assertEqual(hub.db.execute("SELECT COUNT(*) FROM operations").fetchone(), (0,))
                self.assertEqual(
                    post("/search", {"branch": "1", "query": "٠٠١"}, context(cert_a, key_a)),
                    (200, [["visible", "بوجي", "النجم"]]),
                )
                self.assertEqual(
                    post("/search", {"branch": "2", "query": "٠٠١"}, context(cert_a, key_a)),
                    (403, {"code": "SEARCH_DENIED"}),
                )
                status, first = post("/submit", sale, context(cert_a, key_a))
                self.assertEqual(status, 200)
                self.assertEqual(post("/submit", sale, context(cert_a, key_a)), (200, first))
                queue.acknowledge("sale", first)
                self.assertEqual(hub.db.execute("SELECT COUNT(*), SUM(amount) FROM journal").fetchone(), (4, 0))
                hub.revoke_device_certificate("A", "seller", fingerprint_a)
                self.assertEqual(post("/submit", sale, context(cert_a, key_a)), (403, {"code": "SYNC_DENIED"}))
                hub.enroll_device_certificate("A", "seller", fingerprint_a)
                self.assertEqual(post("/submit", sale, context(cert_a, key_a)), (403, {"code": "SYNC_DENIED"}))
            finally:
                server.shutdown()
                worker.join()


if __name__ == "__main__":
    unittest.main()
