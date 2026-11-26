import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from proofs.sync.authority_hub import AuthorityHub
from proofs.sync.local_queue import Queue
from proofs.sync.offline_sale import OfflineSale


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
        session_a = self.hub.issue_session("A", "a", can_submit=True)
        session_b = self.hub.issue_session("A", "b", can_submit=True)
        other_session = self.hub.issue_session("B", "a")
        first = self.hub.submit(session_a, "pay-a", pa)
        # Drop the first reply and recover the pending operation from disk.
        restarted = self.queue("a.db")
        self.assertEqual(len(restarted.pending()), 1)
        retry = self.hub.submit(session_a, "pay-a", pa)
        self.assertEqual(retry, first)
        restarted.acknowledge("pay-a", retry)
        self.assertEqual(qa.pending(), [])
        qa.enqueue(pa, "pay-a")
        self.assertEqual(qa.pending(), [])
        with self.assertRaisesRegex(ValueError, "^SYNC_CONFLICT$"):
            qa.enqueue(pb, "pay-a")
        with self.assertRaisesRegex(ValueError, "^SYNC_DENIED$"):
            self.hub.submit(other_session, "pay-a", pa)
        with self.assertRaisesRegex(ValueError, "^SYNC_CONFLICT$"):
            self.hub.submit(session_b, "pay-a", pa)
        with self.assertRaisesRegex(ValueError, "^AUTHORITY_LIMIT$"):
            self.hub.submit(session_a, "extra", {"resource": "cash", "amount": 1})
        second = self.hub.submit(session_b, "pay-b", pb)
        qb.acknowledge("pay-b", second)
        self.assertEqual([first["revision"], second["revision"]], [1, 2])
        gap = dict(second, revision=3)
        with self.assertRaisesRegex(ValueError, "^SYNC_GAP$"):
            qb.receive([first, gap])
        self.assertEqual(qb.revision(), 0)
        self.assertEqual(qb.db.execute("SELECT COUNT(*) FROM inbox").fetchone()[0], 0)
        qb.receive([first])
        qb.receive(self.hub.pull(session_b))
        self.assertEqual(self.hub.pull(other_session), [])
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
        session = self.hub.issue_session("A", winner, can_submit=True)
        command = {"resource": "stock", "amount": 1}
        receipt = self.hub.submit(session, "last-stock", command)
        self.assertEqual(receipt["revision"], 1)
        with self.assertRaisesRegex(ValueError, "^AUTHORITY_LIMIT$"):
            self.hub.submit(session, "extra", command)

    def test_atomic_sale_and_authorization_survive_backup(self):
        self.hub.seed("A", "stock", 2)
        self.hub.allocate("A", "a", "stock", 2)
        session = self.hub.issue_session("A", "a", can_submit=True)
        revoked = self.hub.issue_session("A", "a", can_submit=True)
        self.hub.revoke_session(revoked)
        queue = self.queue("sale.db")
        payload = {"kind": "sale", "resource": "stock", "amount": 2}
        queue.enqueue(payload, "sale")
        with self.assertRaisesRegex(ValueError, "^SYNC_DENIED$"):
            self.hub.submit(revoked, "sale", payload)
        self.hub.db.execute("""
            CREATE TEMP TRIGGER fail_journal BEFORE INSERT ON journal
            WHEN NEW.account='cogs' BEGIN SELECT RAISE(ABORT, 'injected journal failure'); END
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "^injected journal failure$"):
            self.hub.submit(session, "sale", payload)
        self.assertEqual(self.hub.pull(session), [])
        self.assertEqual(self.hub.db.execute("SELECT COUNT(*) FROM journal").fetchone(), (0,))
        self.assertEqual(self.hub.db.execute("SELECT remaining FROM grants").fetchone(), (2,))
        self.assertEqual(len(queue.pending()), 1)
        self.hub.db.execute("DROP TRIGGER fail_journal")
        stale = self.path / "stale.db"
        stale_db = sqlite3.connect(stale)
        try:
            self.hub.db.backup(stale_db)
        finally:
            stale_db.close()
        start = time.perf_counter()
        first = self.hub.submit(session, "sale", payload)
        settlement_ms = (time.perf_counter() - start) * 1000
        self.assertEqual(first["revision"], 1)
        checkpoint = self.hub.recovery_checkpoint("A")
        authorization = {"A": checkpoint["authorization"]}
        history = {"A": checkpoint["history"]}
        with self.assertRaisesRegex(ValueError, "^RECOVERY_CHECKPOINT_MISMATCH$"):
            AuthorityHub.open_verified_backup(stale, {"A": first}, authorization=authorization, history=history)
        with self.assertRaisesRegex(ValueError, "^RECOVERY_CHECKPOINT_REQUIRED$"):
            AuthorityHub.open_verified_backup(stale, {}, authorization={}, history={})
        backup_path = self.path / "restored.db"
        start = time.perf_counter()
        backup = sqlite3.connect(backup_path)
        try:
            self.hub.db.backup(backup)
            self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone(), ("ok",))
        finally:
            backup.close()
        restored = AuthorityHub.open_verified_backup(backup_path, {"A": first}, authorization=authorization, history=history)
        self.addCleanup(restored.db.close)
        retry = restored.submit(session, "sale", payload)
        restore_ms = (time.perf_counter() - start) * 1000
        self.assertEqual(retry, first)
        queue.acknowledge("sale", retry)
        queue.receive(restored.pull(session))
        self.assertEqual(queue.pending(), [])
        self.assertEqual(queue.revision(), 1)
        self.assertEqual(restored.db.execute("SELECT remaining FROM grants").fetchone(), (0,))
        rows = dict(restored.db.execute("SELECT account, amount FROM journal"))
        self.assertEqual(rows, {"cash": 200, "revenue": -200, "cogs": 120, "inventory": -120})
        self.assertEqual(sum(rows.values()), 0)
        with self.assertRaisesRegex(ValueError, "^SYNC_DENIED$"):
            restored.pull(revoked)
        print(f"single local settlement={settlement_ms:.3f} ms; backup + integrity + replay={restore_ms:.3f} ms")

    def test_device_revocation_keeps_its_offline_reservation_encumbered(self):
        self.hub.seed("A", "stock", 2)
        self.hub.allocate("A", "offline", "stock", 2)
        tokens = [self.hub.issue_session("A", "offline") for _ in range(2)]
        other_company = self.hub.issue_session("B", "offline")
        self.hub.revoke_device("A", "offline")
        for token in tokens:
            with self.assertRaisesRegex(ValueError, "^SYNC_DENIED$"):
                self.hub.pull(token)
        with self.assertRaisesRegex(ValueError, "^SYNC_DENIED$"):
            self.hub.issue_session("A", "offline")
        with self.assertRaisesRegex(ValueError, "^AUTHORITY_LIMIT$"):
            self.hub.allocate("A", "replacement", "stock", 1)
        self.assertEqual(self.hub.pull(other_company), [])
        self.assertEqual(self.hub.db.execute("SELECT remaining FROM grants").fetchone(), (2,))

    def test_hundred_disk_backed_sale_settlements(self):
        count = 100
        self.hub.seed("A", "stock", count)
        self.hub.allocate("A", "seller", "stock", count)
        token = self.hub.issue_session("A", "seller", can_submit=True)
        sale = {"kind": "sale", "resource": "stock", "amount": 1}
        times = []
        for index in range(count):
            start = time.perf_counter()
            receipt = self.hub.submit(token, str(index), sale)
            times.append((time.perf_counter() - start) * 1000)
            self.assertEqual(receipt["revision"], index + 1)
        self.assertEqual(len(self.hub.pull(token)), count)
        self.assertEqual(self.hub.db.execute("SELECT COUNT(*), SUM(amount) FROM journal").fetchone(), (400, 0))
        self.assertEqual(self.hub.db.execute("SELECT remaining FROM grants").fetchone(), (0,))
        p95 = sorted(times)[94]
        print(f"disk-backed SQLite settlement p95={p95:.3f} ms (100 isolated sales)")
        self.assertLess(p95, 1000)

    def test_offline_sale_and_outbox_are_atomic_on_restart(self):
        path = self.path / "device.db"
        device = self.enterContext(closing(OfflineSale(path, "A", 1)))
        device.db.execute("""
            CREATE TEMP TRIGGER fail_delivery BEFORE INSERT ON pending
            BEGIN SELECT RAISE(ABORT, 'injected outbox failure'); END
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "^injected outbox failure$"):
            device.sell("sale", 1)
        self.assertEqual(device.db.execute("SELECT remaining FROM offline_stock").fetchone(), (1,))
        self.assertEqual(device.db.execute("SELECT COUNT(*) FROM offline_sales").fetchone(), (0,))
        self.assertEqual(device.db.execute("SELECT COUNT(*) FROM offline_journal").fetchone(), (0,))
        self.assertEqual(device.pending(), [])
        device.db.execute("DROP TRIGGER fail_delivery")
        payload = device.sell("sale", 1)
        restarted = self.enterContext(closing(OfflineSale(path, "A", 1)))
        self.assertEqual(restarted.sell("sale", 1), payload)
        self.assertEqual(len(restarted.pending()), 1)
        with self.assertRaisesRegex(ValueError, "^SYNC_CONFLICT$"):
            restarted.sell("sale", 2)
        with self.assertRaisesRegex(ValueError, "^AUTHORITY_LIMIT$"):
            restarted.sell("more", 1)

    def test_existing_generic_queue_entry_cannot_become_an_offline_sale(self):
        path = self.path / "shared-queue.db"
        transport = self.queue(path.name)
        payload = {"kind": "sale", "resource": "stock", "amount": 1}
        transport.enqueue(payload, "existing")
        device = self.enterContext(closing(OfflineSale(path, "A", 1)))
        with self.assertRaisesRegex(ValueError, "^SYNC_CONFLICT$"):
            device.sell("existing", 1)
        self.assertEqual(device.db.execute("SELECT remaining FROM offline_stock").fetchone(), (1,))
        self.assertEqual(device.db.execute("SELECT COUNT(*) FROM offline_journal").fetchone(), (0,))
        self.assertEqual(len(device.pending()), 1)

    def test_receipts_from_before_company_binding_replay_without_rewriting(self):
        queue = self.queue("old-receipts.db")
        queue.db.execute(
            "INSERT INTO receipts VALUES (?, ?, ?, ?)",
            ("A", "old", '{"amount":1}', '{"operation_id":"old","payload":{"amount":1},"received_at":"old"}'),
        )
        queue.db.commit()
        recovered = self.queue("old-receipts.db")
        response = dict(company="A", operation_id="old", payload={"amount": 1}, received_at="old")
        recovered.acknowledge("old", response)
        with self.assertRaisesRegex(ValueError, "^SYNC_SCOPE_MISMATCH$"):
            recovered.acknowledge("old", dict(response, company="B"))
        with self.assertRaisesRegex(ValueError, "^SYNC_ACK_MISMATCH$"):
            recovered.acknowledge("old", dict(response, received_at="changed"))
        stored = recovered.db.execute("SELECT response FROM receipts").fetchone()[0]
        self.assertNotIn('"company"', stored)

    def test_restore_cannot_resurrect_a_session_after_the_last_sale(self):
        self.hub.seed("A", "stock", 1)
        self.hub.allocate("A", "seller", "stock", 1)
        token = self.hub.issue_session("A", "seller", can_submit=True)
        receipt = self.hub.submit(token, "one", {"kind": "sale", "resource": "stock", "amount": 1})
        path = self.path / "pre-revocation.db"
        with closing(sqlite3.connect(path)) as snapshot:
            self.hub.db.backup(snapshot)
        self.hub.revoke_device("A", "seller")
        with self.assertRaisesRegex(ValueError, "^RECOVERY_AUTHORIZATION_MISMATCH$"):
            AuthorityHub.open_verified_backup(
                path, {"A": receipt}, authorization={"A": self.hub.authorization_checkpoint("A")},
                history={"A": self.hub.recovery_checkpoint("A")["history"]}
            )

    def test_restore_requires_authorization_for_companies_without_sales(self):
        self.hub.issue_session("B", "observer")
        path = self.path / "two-companies.db"
        with closing(sqlite3.connect(path)) as snapshot:
            self.hub.db.backup(snapshot)
        with self.assertRaisesRegex(ValueError, "^RECOVERY_CHECKPOINT_REQUIRED$"):
            AuthorityHub.open_verified_backup(
                path, {}, authorization={"A": self.hub.authorization_checkpoint("A")},
                history={"A": self.hub.recovery_checkpoint("A")["history"]}
            )
        with closing(AuthorityHub.open_verified_backup(
            path, {}, authorization={"B": self.hub.authorization_checkpoint("B")},
            history={"B": self.hub.recovery_checkpoint("B")["history"]}
        ).db):
            pass

    def test_restore_cannot_release_stock_allocated_after_last_receipt(self):
        self.hub.seed("A", "stock", 2)
        self.hub.allocate("A", "seller", "stock", 1)
        token = self.hub.issue_session("A", "seller", can_submit=True)
        receipt = self.hub.submit(token, "one", {"kind": "sale", "resource": "stock", "amount": 1})
        path = self.path / "pre-allocation.db"
        with closing(sqlite3.connect(path)) as snapshot:
            self.hub.db.backup(snapshot)
        self.hub.allocate("A", "offline", "stock", 1)
        with self.assertRaisesRegex(ValueError, "^RECOVERY_AUTHORIZATION_MISMATCH$"):
            AuthorityHub.open_verified_backup(
                path, {"A": receipt}, authorization={"A": self.hub.authorization_checkpoint("A")},
                history={"A": self.hub.recovery_checkpoint("A")["history"]}
            )

    def test_restore_rejects_sqlite_valid_snapshot_with_missing_journal_entry(self):
        self.hub.seed("A", "stock", 1)
        self.hub.allocate("A", "seller", "stock", 1)
        token = self.hub.issue_session("A", "seller", can_submit=True)
        receipt = self.hub.submit(token, "one", {"kind": "sale", "resource": "stock", "amount": 1})
        path = self.path / "missing-posting.db"
        with closing(sqlite3.connect(path)) as snapshot:
            self.hub.db.backup(snapshot)
            with snapshot:
                snapshot.execute("DELETE FROM journal WHERE account='cash'")
            self.assertEqual(snapshot.execute("PRAGMA integrity_check").fetchone(), ("ok",))
        with self.assertRaisesRegex(ValueError, "^RECOVERY_JOURNAL_MISMATCH$"):
            AuthorityHub.open_verified_backup(
                path, {"A": receipt}, authorization={"A": self.hub.authorization_checkpoint("A")},
                history={"A": self.hub.recovery_checkpoint("A")["history"]}
            )

    def test_restore_rejects_revision_gap_despite_latest_receipt_and_balanced_journal(self):
        self.hub.seed("A", "stock", 2)
        self.hub.allocate("A", "seller", "stock", 2)
        token = self.hub.issue_session("A", "seller", can_submit=True)
        sale = {"kind": "sale", "resource": "stock", "amount": 1}
        self.hub.submit(token, "first", sale)
        receipt = self.hub.submit(token, "second", sale)
        path = self.path / "gap.db"
        with closing(sqlite3.connect(path)) as snapshot:
            self.hub.db.backup(snapshot)
            with snapshot:
                snapshot.execute("DELETE FROM journal WHERE operation_id='first'")
                snapshot.execute("DELETE FROM operations WHERE id='first'")
            self.assertEqual(snapshot.execute("PRAGMA integrity_check").fetchone(), ("ok",))
        with self.assertRaisesRegex(ValueError, "^RECOVERY_REVISION_GAP$"):
            AuthorityHub.open_verified_backup(
                path, {"A": receipt}, authorization={"A": self.hub.authorization_checkpoint("A")},
                history={"A": self.hub.recovery_checkpoint("A")["history"]}
            )

    def test_restore_rejects_modified_prior_sale_with_balanced_journal(self):
        self.hub.seed("A", "stock", 2)
        self.hub.allocate("A", "seller", "stock", 2)
        token = self.hub.issue_session("A", "seller", can_submit=True)
        sale = {"kind": "sale", "resource": "stock", "amount": 1}
        self.hub.submit(token, "first", sale)
        self.hub.submit(token, "second", sale)
        checkpoint = self.hub.recovery_checkpoint("A")
        path = self.path / "altered-history.db"
        with closing(sqlite3.connect(path)) as snapshot:
            self.hub.db.backup(snapshot)
            with snapshot:
                snapshot.execute(
                    "UPDATE operations SET payload=? WHERE company=? AND id=?",
                    ('{"amount":2,"kind":"sale","resource":"stock"}', "A", "first"),
                )
                snapshot.execute("UPDATE journal SET amount=amount*2 WHERE operation_id='first'")
            self.assertEqual(snapshot.execute("PRAGMA integrity_check").fetchone(), ("ok",))
            self.assertEqual(snapshot.execute("SELECT SUM(amount) FROM journal").fetchone(), (0,))
        with self.assertRaisesRegex(ValueError, "^RECOVERY_HISTORY_MISMATCH$"):
            AuthorityHub.open_verified_backup(
                path, {"A": checkpoint["receipt"]},
                authorization={"A": checkpoint["authorization"]},
                history={"A": checkpoint["history"]},
            )


if __name__ == "__main__":
    unittest.main()
