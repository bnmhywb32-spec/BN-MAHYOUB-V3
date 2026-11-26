"""Offline sale fixture: stock, balanced journal and pending delivery share a transaction."""

import json

from proofs.sync.local_queue import Queue
from proofs.sync.sale import MAX_SALE_QUANTITY, sale_journal


class OfflineSale(Queue):
    def __init__(self, path, company, allocation):
        if type(allocation) is not int or not 0 <= allocation <= 2**63 - 1:
            raise ValueError("INVALID_AMOUNT")
        super().__init__(path, company)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS offline_stock(
                company TEXT PRIMARY KEY, remaining INTEGER NOT NULL CHECK(remaining >= 0)
            );
            CREATE TABLE IF NOT EXISTS offline_sales(
                company TEXT, id TEXT, quantity INTEGER NOT NULL,
                PRIMARY KEY(company, id)
            );
            CREATE TABLE IF NOT EXISTS offline_journal(
                company TEXT, id TEXT, account TEXT, amount INTEGER NOT NULL,
                PRIMARY KEY(company, id, account)
            );
        """)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO offline_stock VALUES (?, ?)", (company, allocation))

    def sell(self, operation_id, quantity):
        if not isinstance(operation_id, str) or not 0 < len(operation_id) <= 128:
            raise ValueError("INVALID_OPERATION")
        if type(quantity) is not int or not 0 < quantity <= MAX_SALE_QUANTITY:
            raise ValueError("INVALID_AMOUNT")
        payload = {"kind": "sale", "resource": "stock", "amount": quantity}
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            existing = self.db.execute(
                "SELECT quantity FROM offline_sales WHERE company=? AND id=?",
                (self.company, operation_id),
            ).fetchone()
            if existing:
                if existing[0] != quantity:
                    raise ValueError("SYNC_CONFLICT")
                return payload
            updated = self.db.execute(
                "UPDATE offline_stock SET remaining=remaining-? WHERE company=? AND remaining>=?",
                (quantity, self.company, quantity),
            )
            if updated.rowcount != 1:
                raise ValueError("AUTHORITY_LIMIT")
            self.db.execute("INSERT INTO offline_sales VALUES (?, ?, ?)", (self.company, operation_id, quantity))
            self.db.executemany(
                "INSERT INTO offline_journal VALUES (?, ?, ?, ?)",
                [(self.company, operation_id, account, value) for account, value in sale_journal(quantity)],
            )
            if not self._enqueue_locked(operation_id, body):
                raise ValueError("SYNC_CONFLICT")
        return payload
