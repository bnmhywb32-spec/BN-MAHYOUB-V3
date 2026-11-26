"""Synthetic transport proof; not a production financial posting engine."""
import sqlite3
import json
import uuid

class Queue:
    def __init__(self, path, company):
        self.company = company
        self.db = sqlite3.connect(path)
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS pending(company TEXT, id TEXT, payload TEXT,
          PRIMARY KEY(company,id));
        CREATE TABLE IF NOT EXISTS receipts(company TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL, response TEXT NOT NULL, PRIMARY KEY(company,id));
        CREATE TABLE IF NOT EXISTS inbox(company TEXT, revision INTEGER, id TEXT,
          payload TEXT, PRIMARY KEY(company,revision), UNIQUE(company,id));
        CREATE TABLE IF NOT EXISTS cursor(company TEXT PRIMARY KEY, revision INTEGER);
        """)

    def enqueue(self, payload, operation_id=None):
        op = operation_id or str(uuid.uuid4())
        body = json.dumps(payload, sort_keys=True, separators=(',',':'), allow_nan=False)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self._enqueue_locked(op, body)
        return op

    def _enqueue_locked(self, op, body):
        receipt = self.db.execute("SELECT payload FROM receipts WHERE company=? AND id=?",(self.company,op)).fetchone()
        if receipt:
            if receipt[0] != body:
                raise ValueError("SYNC_CONFLICT")
            return
        self.db.execute("INSERT OR IGNORE INTO pending VALUES(?,?,?)",(self.company,op,body))
        old = self.db.execute("SELECT payload FROM pending WHERE company=? AND id=?",(self.company,op)).fetchone()
        if old[0] != body:
            raise ValueError("SYNC_CONFLICT")

    def pending(self):
        return self.db.execute("SELECT id,payload FROM pending WHERE company=? ORDER BY rowid",(self.company,)).fetchall()

    def acknowledge(self, operation_id, response):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            encoded = json.dumps(response,sort_keys=True,separators=(',',':'),allow_nan=False)
            prior = self.db.execute("SELECT response FROM receipts WHERE company=? AND id=?",(self.company,operation_id)).fetchone()
            if prior:
                if prior[0] != encoded:
                    raise ValueError("SYNC_ACK_MISMATCH")
                return
            row = self.db.execute("SELECT payload FROM pending WHERE company=? AND id=?",(self.company,operation_id)).fetchone()
            if row is None:
                raise ValueError("SYNC_UNKNOWN_OPERATION")
            if response.get("operation_id") != operation_id or json.dumps(response.get("payload"),sort_keys=True,separators=(',',':'),allow_nan=False) != row[0] or not isinstance(response.get("received_at"),str) or not response["received_at"]:
                raise ValueError("SYNC_ACK_MISMATCH")
            self.db.execute("INSERT INTO receipts VALUES(?,?,?,?)",(self.company,operation_id,row[0],encoded))
            self.db.execute("DELETE FROM pending WHERE company=? AND id=?",(self.company,operation_id))

    def revision(self):
        row = self.db.execute("SELECT revision FROM cursor WHERE company=?",(self.company,)).fetchone()
        return row[0] if row else 0

    def receive(self, rows):
        # Requires a gap-free server stream; reject gaps until server is repaired.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            last = self.revision()
            for row in rows:
                rev = row["revision"]
                op = row["operation_id"]
                body = json.dumps(row["payload"],sort_keys=True,separators=(',',':'),allow_nan=False)
                if type(rev) is not int or rev < 1:
                    raise ValueError("SYNC_ORDER")
                if rev <= last:
                    old = self.db.execute("SELECT id,payload FROM inbox WHERE company=? AND revision=?",(self.company,rev)).fetchone()
                    if old is None or old[0] != op or json.dumps(json.loads(old[1]),sort_keys=True,separators=(',',':'),allow_nan=False) != body:
                        raise ValueError("SYNC_REPLAY_CONFLICT")
                    continue
                if rev != last + 1:
                    raise ValueError("SYNC_GAP")
                if self.db.execute("SELECT 1 FROM inbox WHERE company=? AND id=?",(self.company,op)).fetchone():
                    raise ValueError("SYNC_REPLAY_CONFLICT")
                self.db.execute("INSERT INTO inbox VALUES(?,?,?,?)",(self.company,rev,op,body))
                last = rev
            self.db.execute("INSERT INTO cursor VALUES(?,?) ON CONFLICT(company) DO UPDATE SET revision=excluded.revision",(self.company,last))

    def close(self):
        self.db.close()
