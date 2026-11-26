"""Isolated offline allocation proof; not production authorization."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

class Device:
    def __init__(self, path, device, grants):
        self.db = sqlite3.connect(path)
        self.db.executescript("""CREATE TABLE IF NOT EXISTS quota(resource TEXT PRIMARY KEY, remaining INTEGER NOT NULL CHECK(remaining>=0));
        CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY, resource TEXT NOT NULL, amount INTEGER NOT NULL);""")
        self.device = device
        with self.db:
            for resource, amount in grants.items():
                self.db.execute("INSERT OR IGNORE INTO quota VALUES (?,?)",(resource,amount))
    def consume(self, op, resource, amount):
        if type(amount) is not int or amount<=0:
            raise ValueError("INVALID_AMOUNT")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            old=self.db.execute("SELECT resource,amount FROM commands WHERE id=?",(op,)).fetchone()
            if old:
                if old!=(resource,amount): raise ValueError("CONFLICT")
                return
            cur=self.db.execute("UPDATE quota SET remaining=remaining-? WHERE resource=? AND remaining>=?",(amount,resource,amount))
            if cur.rowcount!=1: raise ValueError("AUTHORITY_LIMIT")
            self.db.execute("INSERT INTO commands VALUES(?,?,?)",(op,resource,amount))
    def close(self): self.db.close()

class Proof(unittest.TestCase):
    def test_independent_devices(self):
        with tempfile.TemporaryDirectory() as d:
            a=Device(Path(d)/"a.db","a",{"stock":1,"cash":60})
            b=Device(Path(d)/"b.db","b",{"stock":0,"cash":40})
            a.consume("sale","stock",1)
            with self.assertRaisesRegex(ValueError,"^AUTHORITY_LIMIT$"): b.consume("sale-b","stock",1)
            a.consume("pay","cash",60)
            b.consume("pay-b","cash",40)
            with self.assertRaisesRegex(ValueError,"^AUTHORITY_LIMIT$"): b.consume("extra","cash",1)
            a.close()
            a=Device(Path(d)/"a.db","a",{"stock":1,"cash":60})
            a.consume("sale","stock",1)
            with self.assertRaisesRegex(ValueError,"^CONFLICT$"): a.consume("sale","stock",2)
            with self.assertRaisesRegex(ValueError,"^AUTHORITY_LIMIT$"): a.consume("extra","stock",1)
            a.close(); b.close()

if __name__=="__main__": unittest.main()
