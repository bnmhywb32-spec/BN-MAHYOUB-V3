"""SQLite proof of bounded allocations and ordered settlement under a trusted session fixture."""

import json
import sqlite3


class AuthorityHub:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS balances(
                company TEXT, resource TEXT, free INTEGER NOT NULL CHECK(free >= 0),
                PRIMARY KEY(company, resource)
            );
            CREATE TABLE IF NOT EXISTS grants(
                company TEXT, device TEXT, resource TEXT,
                remaining INTEGER NOT NULL CHECK(remaining >= 0),
                PRIMARY KEY(company, device, resource)
            );
            CREATE TABLE IF NOT EXISTS operations(
                company TEXT, id TEXT, device TEXT NOT NULL, revision INTEGER NOT NULL,
                payload TEXT NOT NULL, received_at TEXT NOT NULL,
                PRIMARY KEY(company, id), UNIQUE(company, revision)
            );
        """)

    def seed(self, company, resource, amount):
        with self.db:
            self.db.execute("INSERT INTO balances VALUES (?, ?, ?)", (company, resource, amount))

    def allocate(self, company, device, resource, amount):
        self._positive(amount)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            updated = self.db.execute(
                "UPDATE balances SET free=free-? WHERE company=? AND resource=? AND free>=?",
                (amount, company, resource, amount),
            )
            if updated.rowcount != 1:
                raise ValueError("AUTHORITY_LIMIT")
            self.db.execute("INSERT INTO grants VALUES (?, ?, ?, ?)", (company, device, resource, amount))

    @staticmethod
    def _positive(amount):
        if type(amount) is not int or not 0 < amount <= 2**63 - 1:
            raise ValueError("INVALID_AMOUNT")

    def submit(self, session, operation_id, payload):
        company, device = session
        if not isinstance(payload, dict) or set(payload) != {"resource", "amount"}:
            raise ValueError("INVALID_PAYLOAD")
        resource, amount = payload["resource"], payload["amount"]
        if not isinstance(resource, str) or not resource:
            raise ValueError("INVALID_PAYLOAD")
        self._positive(amount)
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            grant = self.db.execute(
                "SELECT 1 FROM grants WHERE company=? AND device=? AND resource=?",
                (company, device, resource),
            ).fetchone()
            if grant is None:
                raise ValueError("SYNC_DENIED")
            previous = self.db.execute(
                "SELECT device, payload, revision, received_at FROM operations WHERE company=? AND id=?",
                (company, operation_id),
            ).fetchone()
            if previous:
                actor, old_body, revision, received_at = previous
                if (actor, old_body) != (device, body):
                    raise ValueError("SYNC_CONFLICT")
            else:
                updated = self.db.execute(
                    "UPDATE grants SET remaining=remaining-? "
                    "WHERE company=? AND device=? AND resource=? AND remaining>=?",
                    (amount, company, device, resource, amount),
                )
                if updated.rowcount != 1:
                    raise ValueError("AUTHORITY_LIMIT")
                revision = self.db.execute(
                    "SELECT COALESCE(MAX(revision), 0)+1 FROM operations WHERE company=?",
                    (company,),
                ).fetchone()[0]
                received_at = self.db.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]
                self.db.execute(
                    "INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?)",
                    (company, operation_id, device, revision, body, received_at),
                )
            return dict(operation_id=operation_id, payload=payload, revision=revision, received_at=received_at)
