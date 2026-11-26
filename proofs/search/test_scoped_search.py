import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Event
from unittest.mock import patch

from proofs.search.local_search import Search
from proofs.search.scoped_search import find_authorized
from proofs.sync.authority_hub import AuthorityHub


class ScopedSearchProof(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(self.temp) / "hub.db"
        self.hub = AuthorityHub(self.path)
        self.addCleanup(self.hub.db.close)
        self.search = Search(self.hub.db)
        self.search.put("A", "1", "visible", "001", "بوجي", "النجم")
        self.search.put("A", "2", "branch-secret", "001", "بوجي", "النجم")
        self.search.put("B", "1", "company-secret", "001", "بوجي", "النجم")
        self.hub.grant_search_branch("A", "seller", "1")
        self.token = self.hub.issue_session("A", "seller")

    def test_scoped_results_revocation_and_no_cross_database(self):
        def find(branch):
            return find_authorized(self.hub, self.search, self.token, branch, "٠٠١")

        self.assertEqual([row[0] for row in find("1")], ["visible"])
        with self.assertRaisesRegex(ValueError, "^SEARCH_DENIED$"):
            find("2")
        other = self.hub.issue_session("B", "seller")
        with self.assertRaisesRegex(ValueError, "^SEARCH_DENIED$"):
            find_authorized(self.hub, self.search, other, "1", "001")
        other_db = self.enterContext(closing(sqlite3.connect(":memory:")))
        with self.assertRaisesRegex(ValueError, "^SEARCH_DATABASE_MISMATCH$"):
            find_authorized(self.hub, Search(other_db), self.token, "1", "001")
        self.hub.revoke_search_branch("A", "seller", "1")
        with self.assertRaisesRegex(ValueError, "^SEARCH_DENIED$"):
            find("1")
        self.hub.grant_search_branch("A", "seller", "1")
        self.hub.revoke_device("A", "seller")
        with self.assertRaisesRegex(ValueError, "^SYNC_DENIED$"):
            find("1")

    def test_revocation_during_read_applies_to_next_request(self):
        self.hub.db.execute("PRAGMA journal_mode=WAL")
        reached = Event()
        proceed = Event()
        original = Search.find

        def pause_after_authorization(search, company, branch, query, limit):
            reached.set()
            if not proceed.wait(5):
                raise TimeoutError("Search reader was not released")
            return original(search, company, branch, query, limit)

        def read():
            reader = AuthorityHub(self.path)
            try:
                return find_authorized(reader, Search(reader.db), self.token, "1", "001")
            finally:
                reader.db.close()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with patch.object(Search, "find", pause_after_authorization):
                pending = pool.submit(read)
                try:
                    self.assertTrue(reached.wait(5))
                    self.hub.revoke_search_branch("A", "seller", "1")
                finally:
                    proceed.set()
                self.assertEqual([row[0] for row in pending.result(timeout=5)], ["visible"])
        with self.assertRaisesRegex(ValueError, "^SEARCH_DENIED$"):
            find_authorized(self.hub, self.search, self.token, "1", "001")


if __name__ == "__main__":
    unittest.main()
