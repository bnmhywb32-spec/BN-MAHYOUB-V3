"""Synthetic local search proof, not production or a platform selection."""
import sqlite3
import unicodedata
import re
import time
import unittest

def normalize(value):
    value = unicodedata.normalize("NFKC", value).casefold()
    value = "".join(c for c in value if not unicodedata.combining(c))
    return " ".join(re.findall(r"\w+", value.translate(str.maketrans("أإآىـ", "اااي "))))

class Search:
    def __init__(self, db):
        self.db = db
        db.executescript("""
        CREATE TABLE IF NOT EXISTS items(company TEXT, branch TEXT, sku TEXT, barcode TEXT, name TEXT, brand TEXT, aliases TEXT, PRIMARY KEY(company,branch,sku));
        CREATE INDEX IF NOT EXISTS barcode_idx ON items(company,branch,barcode);
        CREATE TABLE IF NOT EXISTS terms(company TEXT, branch TEXT, term TEXT COLLATE NOCASE, sku TEXT, PRIMARY KEY(company,branch,term,sku));
        CREATE INDEX IF NOT EXISTS terms_by_item ON terms(company,branch,sku);
        """)
    def put(self, company, branch, sku, barcode, name, brand, aliases=""):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO items VALUES(?,?,?,?,?,?,?)",(company,branch,sku,barcode,name,brand,aliases))
            self.db.execute("DELETE FROM terms WHERE company=? AND branch=? AND sku=?",(company,branch,sku))
            self.db.executemany("INSERT INTO terms VALUES(?,?,?,?)",[(company,branch,t,sku) for t in set(normalize(" ".join([name,brand,aliases,sku])).split())])
    def find(self, company, branch, query, limit=20):
        if type(limit) is not int or not 1<=limit<=100: raise ValueError("INVALID_LIMIT")
        query = query.strip()
        if len(query) > 256:
            raise ValueError("INVALID_QUERY")
        if not query:
            return []
        tokens = list(dict.fromkeys(normalize(query).split()))
        if len(tokens) > 12:
            raise ValueError("INVALID_QUERY")
        exact = self.db.execute(
            "SELECT sku,name,brand FROM items WHERE company=? AND branch=? AND barcode=? ORDER BY sku LIMIT ?",
            (company, branch, query, limit),
        ).fetchall()
        if len(exact) == limit:
            return exact
        by_sku = self.db.execute(
            "SELECT sku,name,brand FROM items WHERE company=? AND branch=? AND sku=?",
            (company, branch, query),
        ).fetchone()
        if by_sku is not None and by_sku not in exact:
            exact.append(by_sku)
        if len(exact) == limit or not tokens:
            return exact
        # Escape underscore, the only LIKE wildcard retained by normalization.
        clauses = " INTERSECT ".join(
            "SELECT sku FROM terms WHERE company=? AND branch=? AND term LIKE ? ESCAPE '!'"
            for _ in tokens
        )
        args = [value for token in tokens for value in (company, branch, token.replace("_", "!_") + "%")]
        rows = self.db.execute(
            "SELECT sku,name,brand FROM items WHERE company=? AND branch=? AND sku IN (" +
            clauses + ") ORDER BY sku LIMIT ?", [company, branch, *args, limit]
        ).fetchall()
        return list(dict.fromkeys(exact + rows))[:limit]

class Proof(unittest.TestCase):
    def test_search(self):
        s=Search(sqlite3.connect(":memory:"))
        s.put("a","1","000001","123","بوجي","النجم","شمعة")
        s.put("a","1","000002","124","بوجي","الصقر")
        s.put("b","1","000003","123","بوجي","النجم")
        s.put("a","2","000004","123","بوجي","النجم")
        for q in ("بوجي النجم","النجم بوج","شمعة","123","000001"):
            self.assertEqual([r[0] for r in s.find("a","1",q)],["000001"])
        s.put("a","1","000001","123","كويل","النجم")
        self.assertEqual(s.find("a","1","شمعة"),[])
    def test_exact_barcode_priority_and_legacy_index_backfill(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.executescript("""
            CREATE TABLE terms(company TEXT, branch TEXT, term TEXT COLLATE NOCASE, sku TEXT,
                PRIMARY KEY(company,branch,term,sku));
            INSERT INTO terms VALUES ('a','1','old','item');
        """)
        search = Search(db)
        search.put("a", "1", "item", "001", "قلم", "النجم")
        search.put("a", "1", "001", "999", "دفتر", "النجم")
        search.put("b", "1", "other", "001", "قلم", "النجم")
        self.assertEqual([row[0] for row in search.find("a", "1", "001")], ["item", "001"])
        self.assertEqual([row[0] for row in search.find("a", "1", "001", limit=1)], ["item"])
        self.assertEqual(search.find("a", "1", "old"), [])
        plan = db.execute(
            "EXPLAIN QUERY PLAN DELETE FROM terms WHERE company=? AND branch=? AND sku=?",
            ("a", "1", "item"),
        ).fetchall()
        self.assertTrue(any("terms_by_item" in node[-1] for node in plan), plan)

    def test_fixture(self):
        s=Search(sqlite3.connect(":memory:"))
        for i in range(10000): s.put("a","1",f"{i:06d}",str(100000+i),"بوجي","النجم")
        for _ in range(10): s.find("a","1","بوجي النجم")
        times=[]
        for _ in range(100):
            start=time.perf_counter()
            self.assertEqual(len(s.find("a","1","بوجي النجم")),20)
            times.append((time.perf_counter()-start)*1000)
        p95=sorted(times)[94]
        print(f"compound search p95={p95:.3f} ms (in-memory synthetic fixture)")
        self.assertLess(p95,500)

if __name__=="__main__": unittest.main()
