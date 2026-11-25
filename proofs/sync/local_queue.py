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
        CREATE TABLE IF NOT EXISTS inbox(company TEXT, revision INTEGER, id TEXT,
          payload TEXT, PRIMARY KEY(company,revision), UNIQUE(company,id));
        CREATE TABLE IF NOT EXISTS cursor(company TEXT PRIMARY KEY, revision INTEGER);
        """)

    def enqueue(self, payload, operation_id=None):
        op = operation_id or str(uuid.uuid4())
        body = json.dumps(payload, sort_keys=True, separators=(',',':'), allow_nan=False)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO pending VALUES(?,?,?)",(self.company,op,body))
            old = self.db.execute("SELECT payload FROM pending WHERE company=? AND id=?",(self.company,op)).fetchone()
            if old[0] != body:
                raise ValueError("SYNC_CONFLICT")
        return op

    def pending(self):
        return self.db.execute("SELECT id,payload FROM pending WHERE company=? ORDER BY rowid",(self.company,)).fetchall()

    def acknowledge(self, operation_id, response):
        with self.db:
            row = self.db.execute("SELECT payload FROM pending WHERE company=? AND id=?",(self.company,operation_id)).fetchone()
            if row is None:
                return
            if response.get("operation_id") != operation_id or response.get("payload") != json.loads(row[0]):
                raise ValueError("SYNC_ACK_MISMATCH")
            self.db.execute("DELETE FROM pending WHERE company=? AND id=?",(self.company,operation_id))

    def revision(self):
        row = self.db.execute("SELECT revision FROM cursor WHERE company=?",(self.company,)).fetchone()
        return row[0] if row else 0

    def receive(self, rows):
        with self.db:
            last = self.revision()
            for row in rows:
                rev = row["revision"]
                if type(rev) is not int or rev <= last:
                    raise ValueError("SYNC_ORDER")
                self.db.execute("INSERT INTO inbox VALUES(?,?,?,?)",(self.company,rev,row["operation_id"],json.dumps(row["payload"],sort_keys=True)))
                last = rev
            self.db.execute("INSERT INTO cursor VALUES(?,?) ON CONFLICT(company) DO UPDATE SET revision=excluded.revision",(self.company,last))

    def close(self):
        self.db.close()
