"""SQLite proof of bounded allocations and ordered settlement under isolated device sessions."""

import hashlib
import json
import secrets
import sqlite3
from itertools import groupby
from pathlib import Path

from proofs.sync.sale import MAX_SALE_QUANTITY, sale_journal


class AuthorityHub:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA foreign_keys = ON")
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
            CREATE TABLE IF NOT EXISTS devices(
                company TEXT, device TEXT, enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
                PRIMARY KEY(company, device)
            );
            CREATE TABLE IF NOT EXISTS sessions(
                digest TEXT PRIMARY KEY, company TEXT NOT NULL, device TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS session_writers(
                digest TEXT PRIMARY KEY REFERENCES sessions(digest) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS operations(
                company TEXT, id TEXT, device TEXT NOT NULL, revision INTEGER NOT NULL,
                payload TEXT NOT NULL, received_at TEXT NOT NULL,
                PRIMARY KEY(company, id), UNIQUE(company, revision)
            );
            CREATE TABLE IF NOT EXISTS journal(
                company TEXT NOT NULL, operation_id TEXT NOT NULL, account TEXT NOT NULL,
                amount INTEGER NOT NULL CHECK(typeof(amount)='integer'),
                PRIMARY KEY(company, operation_id, account),
                FOREIGN KEY(company, operation_id) REFERENCES operations(company, id)
            );
        """)

    @classmethod
    def open_verified_backup(cls, path, checkpoints, *, authorization, history):
        """Open a staged snapshot only if independently retained receipts are present."""
        if not authorization or set(history) != set(authorization) or not set(checkpoints) <= set(authorization):
            raise ValueError("RECOVERY_CHECKPOINT_REQUIRED")
        uri = Path(path).resolve().as_uri() + "?mode=rw"
        snapshot = sqlite3.connect(uri, uri=True)
        try:
            snapshot.execute("PRAGMA foreign_keys = ON")
            snapshot.execute("BEGIN IMMEDIATE")
            if snapshot.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("RECOVERY_CORRUPT")
            companies = {row[0] for row in snapshot.execute(
                "SELECT company FROM devices UNION SELECT company FROM sessions UNION "
                "SELECT company FROM balances UNION SELECT company FROM grants UNION "
                "SELECT company FROM operations"
            )}
            posting_companies = {row[0] for row in snapshot.execute(
                "SELECT DISTINCT company FROM operations"
            )}
            if not companies <= set(authorization) or not posting_companies <= set(checkpoints):
                raise ValueError("RECOVERY_CHECKPOINT_REQUIRED")
            for company, expected in checkpoints.items():
                row = snapshot.execute(
                    "SELECT id, payload, revision, received_at FROM operations "
                    "WHERE company=? AND revision=?",
                    (company, expected["revision"]),
                ).fetchone()
                if row is None or cls._receipt(company, *row) != expected:
                    raise ValueError("RECOVERY_CHECKPOINT_MISMATCH")
            for company, expected in authorization.items():
                if cls._authorization_digest(snapshot, company) != expected:
                    raise ValueError("RECOVERY_AUTHORIZATION_MISMATCH")
            if snapshot.execute(
                "SELECT 1 FROM operations GROUP BY company "
                "HAVING MIN(revision) <> 1 OR MAX(revision) <> COUNT(*) LIMIT 1"
            ).fetchone() is not None:
                raise ValueError("RECOVERY_REVISION_GAP")
            cls._verify_recovered_journal(snapshot)
            for company, expected in history.items():
                if cls._history_digest(snapshot, company) != expected:
                    raise ValueError("RECOVERY_HISTORY_MISMATCH")
            snapshot.commit()
        except BaseException:
            snapshot.close()
            raise
        restored = cls.__new__(cls)
        restored.db = snapshot
        return restored

    @staticmethod
    def _verify_recovered_journal(db):
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("RECOVERY_JOURNAL_MISMATCH")
        rows = db.execute(
            "SELECT o.company, o.id, o.payload, j.account, j.amount FROM operations AS o "
            "LEFT JOIN journal AS j ON j.company=o.company AND j.operation_id=o.id "
            "ORDER BY o.company, o.id, j.account"
        )
        for (_, _, body), postings in groupby(rows, key=lambda row: row[:3]):
            payload = json.loads(body)
            expected = sale_journal(payload["amount"]) if payload.get("kind") == "sale" else ()
            actual = tuple((account, amount) for *_, account, amount in postings if account is not None)
            if actual != tuple(sorted(expected)):
                raise ValueError("RECOVERY_JOURNAL_MISMATCH")

    @staticmethod
    def _authorization_digest(db, company):
        devices = db.execute(
            "SELECT device, enabled FROM devices WHERE company=? ORDER BY device", (company,)
        ).fetchall()
        sessions = db.execute(
            "SELECT s.digest, s.device, w.digest IS NOT NULL FROM sessions AS s "
            "LEFT JOIN session_writers AS w ON w.digest=s.digest "
            "WHERE s.company=? ORDER BY s.digest", (company,)
        ).fetchall()
        balances = db.execute(
            "SELECT resource, free FROM balances WHERE company=? ORDER BY resource", (company,)
        ).fetchall()
        grants = db.execute(
            "SELECT device, resource, remaining FROM grants WHERE company=? ORDER BY device, resource",
            (company,),
        ).fetchall()
        data = json.dumps([devices, sessions, balances, grants], separators=(",", ":")).encode()
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _history_digest(db, company):
        digest = hashlib.sha256()
        rows = db.execute(
            "SELECT id, device, revision, payload, received_at FROM operations "
            "WHERE company=? ORDER BY revision", (company,)
        )
        for row in rows:
            record = json.dumps(row, ensure_ascii=True, separators=(",", ":")).encode()
            digest.update(len(record).to_bytes(8, "big"))
            digest.update(record)
        return digest.hexdigest()

    def recovery_checkpoint(self, company):
        """Capture one company's latest receipt and trust fingerprints in one read transaction."""
        with self.db:
            self.db.execute("BEGIN")
            row = self.db.execute(
                "SELECT id, payload, revision, received_at FROM operations "
                "WHERE company=? ORDER BY revision DESC LIMIT 1", (company,)
            ).fetchone()
            return {
                "receipt": self._receipt(company, *row) if row else None,
                "authorization": self._authorization_digest(self.db, company),
                "history": self._history_digest(self.db, company),
            }

    def authorization_checkpoint(self, company):
        """Capture authorization and reserved balances for independent retention."""
        with self.db:
            self.db.execute("BEGIN")
            return self._authorization_digest(self.db, company)

    def seed(self, company, resource, amount):
        with self.db:
            self.db.execute("INSERT INTO balances VALUES (?, ?, ?)", (company, resource, amount))

    def allocate(self, company, device, resource, amount):
        self._positive(amount)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self._require_active_device(company, device)
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

    @staticmethod
    def _digest(token):
        if not isinstance(token, str) or len(token) != 43:
            raise ValueError("SYNC_DENIED")
        return hashlib.sha256(token.encode()).hexdigest()

    def issue_session(self, company, device, *, can_submit=False):
        # Administrative fixture; a production issuer would authenticate the user and device.
        token = secrets.token_urlsafe(32)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("INSERT OR IGNORE INTO devices VALUES (?, ?, 1)", (company, device))
            self._require_active_device(company, device)
            digest = self._digest(token)
            self.db.execute("INSERT INTO sessions VALUES (?, ?, ?)", (digest, company, device))
            if can_submit:
                self.db.execute("INSERT INTO session_writers VALUES (?)", (digest,))
        return token

    def revoke_session(self, token):
        with self.db:
            self.db.execute("DELETE FROM sessions WHERE digest=?", (self._digest(token),))

    def revoke_device(self, company, device):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute(
                "INSERT INTO devices VALUES (?, ?, 0) "
                "ON CONFLICT(company, device) DO UPDATE SET enabled=0",
                (company, device),
            )
            self.db.execute("DELETE FROM sessions WHERE company=? AND device=?", (company, device))
            # Offline reservations stay encumbered until a separately proven reconciliation.

    def _require_active_device(self, company, device):
        disabled = self.db.execute(
            "SELECT 1 FROM devices WHERE company=? AND device=? AND enabled=0",
            (company, device),
        ).fetchone()
        if disabled:
            raise ValueError("SYNC_DENIED")

    def _scope(self, token, *, write=False):
        digest = self._digest(token)
        scope = self.db.execute(
            "SELECT company, device FROM sessions WHERE digest=? "
            "AND (?=0 OR EXISTS(SELECT 1 FROM session_writers WHERE digest=?))",
            (digest, write, digest),
        ).fetchone()
        if scope is None:
            raise ValueError("SYNC_DENIED")
        return scope

    def pull(self, token, after=0):
        if type(after) is not int or not 0 <= after <= 2**63 - 1:
            raise ValueError("INVALID_REVISION")
        with self.db:
            self.db.execute("BEGIN")
            company, _ = self._scope(token)
            rows = self.db.execute(
                "SELECT id, payload, revision, received_at FROM operations "
                "WHERE company=? AND revision>? ORDER BY revision LIMIT 100",
                (company, after),
            ).fetchall()
            return [self._receipt(company, op, body, rev, stamp) for op, body, rev, stamp in rows]

    @staticmethod
    def _receipt(company, operation_id, body, revision, received_at):
        return dict(company=company, operation_id=operation_id, payload=json.loads(body),
                    revision=revision, received_at=received_at)

    def submit(self, token, operation_id, payload):
        if not isinstance(operation_id, str) or not 0 < len(operation_id) <= 128:
            raise ValueError("INVALID_OPERATION")
        if not isinstance(payload, dict) or set(payload) not in (
            {"resource", "amount"}, {"resource", "amount", "kind"}
        ):
            raise ValueError("INVALID_PAYLOAD")
        resource, amount = payload["resource"], payload["amount"]
        if not isinstance(resource, str) or not resource:
            raise ValueError("INVALID_PAYLOAD")
        self._positive(amount)
        sale = "kind" in payload
        if sale and (payload["kind"] != "sale" or resource != "stock"):
            raise ValueError("INVALID_PAYLOAD")
        if sale and amount > MAX_SALE_QUANTITY:
            raise ValueError("INVALID_AMOUNT")
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            company, device = self._scope(token, write=True)
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
                if sale:
                    self.db.executemany(
                        "INSERT INTO journal VALUES (?, ?, ?, ?)",
                        [(company, operation_id, account, value) for account, value in sale_journal(amount)],
                    )
            return self._receipt(company, operation_id, body, revision, received_at)
