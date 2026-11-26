import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from proofs.sync.authority_hub import AuthorityHub
from proofs.sync.local_queue import Queue


class TransportProof(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        self.hub = AuthorityHub(self.path / "hub.db")
        self.addCleanup(self.hub.db.close)

    def queue(self, name, company="A"):
        queue = Queue(self.path / name, company)
        self.addCleanup(queue.close)
        return queue

    def test_lost_ack_retry_and_ordered_inbox(self):
        self.hub.seed("A", "cash", 100)
        self.hub.allocate("A", "a", "cash", 60)
        self.hub.allocate("A", "b", "cash", 40)
        with self.assertRaisesRegex(ValueError, "^AUTHORITY_LIMIT$"):
            self.hub.allocate("A", "c", "cash", 1)
        qa = self.queue("a.db")
        qb = self.queue("b.db")
        pa = {"resource": "cash", "amount": 60}
        pb = {"resource": "cash", "amount": 40}
        qa.enqueue(pa, "pay-a")
        qb.enqueue(pb, "pay-b")
        first = self.hub.submit(("A", "a"), "pay-a", pa)
        # Drop the first reply and recover the pending operation from disk.
        restarted = self.queue("a.db")
        self.assertEqual(len(restarted.pending()), 1)
        retry = self.hub.submit(("A", "a"), "pay-a", pa)
        self.assertEqual(retry, first)
        restarted.acknowledge("pay-a", retry)
        self.assertEqual(qa.pending(), [])
        qa.enqueue(pa, "pay-a")
        self.assertEqual(qa.pending(), [])
        with self.assertRaisesRegex(ValueError, "^SYNC_CONFLICT$"):
            qa.enqueue(pb, "pay-a")
        with self.assertRaisesRegex(ValueError, "^SYNC_DENIED$"):
            self.hub.submit(("B", "a"), "pay-a", pa)
        with self.assertRaisesRegex(ValueError, "^SYNC_CONFLICT$"):
            self.hub.submit(("A", "b"), "pay-a", pa)
        with self.assertRaisesRegex(ValueError, "^AUTHORITY_LIMIT$"):
            self.hub.submit(("A", "a"), "extra", {"resource": "cash", "amount": 1})
        second = self.hub.submit(("A", "b"), "pay-b", pb)
        qb.acknowledge("pay-b", second)
        self.assertEqual([first["revision"], second["revision"]], [1, 2])
        gap = dict(second, revision=3)
        with self.assertRaisesRegex(ValueError, "^SYNC_GAP$"):
            qb.receive([first, gap])
        self.assertEqual(qb.revision(), 0)
        self.assertEqual(qb.db.execute("SELECT COUNT(*) FROM inbox").fetchone()[0], 0)
        qb.receive([first])
        qb.receive([first, second])
        with self.assertRaisesRegex(ValueError, "^SYNC_REPLAY_CONFLICT$"):
            qb.receive([dict(first, payload=pb)])
        self.assertEqual(qb.revision(), 2)
        other = self.queue("b.db", "B")
        self.assertEqual(other.revision(), 0)

    def test_two_devices_cannot_allocate_last_stock(self):
        self.hub.seed("A", "stock", 1)

        def allocate(device):
            hub = AuthorityHub(self.path / "hub.db")
            try:
                hub.allocate("A", device, "stock", 1)
                return device
            except ValueError as error:
                self.assertEqual(str(error), "AUTHORITY_LIMIT")
            finally:
                hub.db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            winners = [device for device in pool.map(allocate, ("a", "b")) if device]
        self.assertEqual(len(winners), 1)
        winner = winners[0]
        command = {"resource": "stock", "amount": 1}
        receipt = self.hub.submit(("A", winner), "last-stock", command)
        self.assertEqual(receipt["revision"], 1)
        with self.assertRaisesRegex(ValueError, "^AUTHORITY_LIMIT$"):
            self.hub.submit(("A", winner), "extra", command)


if __name__ == "__main__":
    unittest.main()
