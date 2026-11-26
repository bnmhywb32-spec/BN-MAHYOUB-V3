"""Synthetic local search proof, not production or a platform selection."""
import sqlite3
import unicodedata
import re
import time
import unittest

def normalize_digits(value):
    value = unicodedata.normalize("NFKC", value)
    result = []
    for character in value:
        digit = unicodedata.decimal(character, None)
        result.append(str(digit) if digit is not None else character)
    return "".join(result)


def normalize(value):
    value = normalize_digits(value).casefold()
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = value.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ـ": None}))
    return " ".join(re.findall(r"\w+", value))


class Search:
    def __init__(self, db):
        self.db = db
        db.executescript("""
        CREATE TABLE IF NOT EXISTS items(company TEXT, branch TEXT, sku TEXT, barcode TEXT, name TEXT, brand TEXT, aliases TEXT, PRIMARY KEY(company,branch,sku));
        CREATE INDEX IF NOT EXISTS barcode_idx ON items(company,branch,barcode);
        CREATE TABLE IF NOT EXISTS terms(company TEXT, branch TEXT, term TEXT COLLATE NOCASE, sku TEXT, PRIMARY KEY(company,branch,term,sku));
        CREATE INDEX IF NOT EXISTS terms_by_item ON terms(company,branch,sku);
        CREATE TABLE IF NOT EXISTS search_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        self._upgrade_index()

    def _upgrade_index(self):
        # The marker and both indexes are committed together, including for old databases.
        self.db.execute("SAVEPOINT search_upgrade")
        try:
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(items)")}
            if "barcode_normalized" not in columns:
                self.db.execute("ALTER TABLE items ADD COLUMN barcode_normalized TEXT")
            self.db.execute(
                "CREATE INDEX IF NOT EXISTS barcode_normalized_idx "
                "ON items(company,branch,barcode_normalized)"
            )
            version = self.db.execute(
                "SELECT value FROM search_metadata WHERE key='normalization_version'"
            ).fetchone()
            if version is not None and version != ("2",):
                raise ValueError("SEARCH_INDEX_VERSION_UNSUPPORTED")
            if version is None:
                self.db.execute("DELETE FROM terms")
                rows = self.db.execute("SELECT company,branch,sku,barcode,name,brand,aliases FROM items")
                for company, branch, sku, barcode, name, brand, aliases in rows:
                    self.db.execute(
                        "UPDATE items SET barcode_normalized=? WHERE company=? AND branch=? AND sku=?",
                        (normalize_digits(barcode) if barcode is not None else None, company, branch, sku),
                    )
                    tokens = set(normalize(" ".join(value or "" for value in (name, brand, aliases, sku))).split())
                    self.db.executemany(
                        "INSERT INTO terms VALUES(?,?,?,?)",
                        [(company, branch, token, sku) for token in tokens],
                    )
                self.db.execute(
                    "INSERT INTO search_metadata VALUES('normalization_version','2') "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
                )
        except BaseException:
            self.db.execute("ROLLBACK TO search_upgrade")
            raise
        finally:
            self.db.execute("RELEASE search_upgrade")

    def put(self, company, branch, sku, barcode, name, brand, aliases=""):
        self.put_many(((company, branch, sku, barcode, name, brand, aliases),))

    def put_many(self, items):
        """Publish the whole batch, including when the connection uses autocommit."""
        self.db.execute("SAVEPOINT catalog_update")
        try:
            for item in items:
                self._put(*item)
        except BaseException:
            self.db.execute("ROLLBACK TO catalog_update")
            raise
        finally:
            self.db.execute("RELEASE catalog_update")

    def _put(self, company, branch, sku, barcode, name, brand, aliases=""):
        tokens = set(normalize(" ".join([name, brand, aliases, sku])).split())
        self.db.execute(
            "INSERT OR REPLACE INTO items "
            "(company,branch,sku,barcode,name,brand,aliases,barcode_normalized) VALUES(?,?,?,?,?,?,?,?)",
            (company, branch, sku, barcode, name, brand, aliases,
             normalize_digits(barcode) if barcode is not None else None),
        )
        self.db.execute("DELETE FROM terms WHERE company=? AND branch=? AND sku=?", (company, branch, sku))
        self.db.executemany(
            "INSERT INTO terms VALUES(?,?,?,?)",
            [(company, branch, token, sku) for token in tokens],
        )

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
            "SELECT sku,name,brand FROM items WHERE company=? AND branch=? AND barcode_normalized=? "
            "ORDER BY CASE WHEN barcode=? THEN 0 ELSE 1 END,sku LIMIT ?",
            (company, branch, normalize_digits(query), query, limit),
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

    def test_arabic_normalization_and_digit_equivalence(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        search = Search(db)
        search.put("a", "1", "sku1", "00123", "بـُوجي", "النجم")
        search.put("a", "1", "sku2", "٤٥٦", "قلم", "النجم")
        search.put("a", "1", "sku3", None, "حبر", "النجم")
        search.put("b", "1", "hidden", "00123", "بوجي", "النجم")
        self.assertEqual([row[0] for row in search.find("a", "1", "بوجي")], ["sku1"])
        self.assertEqual([row[0] for row in search.find("a", "1", "٠٠١٢٣")], ["sku1"])
        self.assertEqual([row[0] for row in search.find("a", "1", "456")], ["sku2"])
        self.assertEqual([row[0] for row in search.find("a", "1", "٤٥٦")], ["sku2"])
        self.assertEqual([row[0] for row in search.find("a", "1", "حبر")], ["sku3"])
        search.put("a", "1", "sku4", "٠٠١٢٣", "دفتر", "النجم")
        self.assertEqual([row[0] for row in search.find("a", "1", "00123")][:2], ["sku1", "sku4"])
        self.assertEqual([row[0] for row in search.find("a", "1", "٠٠١٢٣")][:2], ["sku4", "sku1"])

    def test_old_catalog_index_migration_is_atomic(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.executescript("""
            CREATE TABLE items(company TEXT, branch TEXT, sku TEXT, barcode TEXT, name TEXT,
                brand TEXT, aliases TEXT, PRIMARY KEY(company,branch,sku));
            CREATE TABLE terms(company TEXT, branch TEXT, term TEXT COLLATE NOCASE, sku TEXT,
                PRIMARY KEY(company,branch,term,sku));
            INSERT INTO items VALUES('a','1','old','١٢٣','بـوجي','النجم','');
            INSERT INTO items VALUES('a','1','old-null',NULL,'قلم',NULL,NULL);
            INSERT INTO terms VALUES('a','1','ب','old');
            CREATE TRIGGER interrupt_upgrade BEFORE INSERT ON terms
                BEGIN SELECT RAISE(ABORT, 'injected index failure'); END;
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "injected index failure"):
            Search(db)
        self.assertEqual(db.execute("SELECT term FROM terms").fetchall(), [("ب",)])
        columns = {row[1] for row in db.execute("PRAGMA table_info(items)")}
        self.assertNotIn("barcode_normalized", columns)
        db.execute("DROP TRIGGER interrupt_upgrade")
        search = Search(db)
        self.assertEqual([row[0] for row in search.find("a", "1", "بوجي")], ["old"])
        self.assertEqual([row[0] for row in search.find("a", "1", "123")], ["old"])
        self.assertEqual([row[0] for row in search.find("a", "1", "قلم")], ["old-null"])
        self.assertEqual(db.execute(
            "SELECT value FROM search_metadata WHERE key='normalization_version'"
        ).fetchone(), ("2",))
        Search(db)
        self.assertEqual([row[0] for row in search.find("a", "1", "بوجي")], ["old"])

    def test_newer_normalization_version_is_not_overwritten(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        search = Search(db)
        search.put("a", "1", "item", "001", "قلم", "النجم")
        db.execute("UPDATE search_metadata SET value='999' WHERE key='normalization_version'")
        db.commit()
        with self.assertRaisesRegex(ValueError, "^SEARCH_INDEX_VERSION_UNSUPPORTED$"):
            Search(db)
        self.assertEqual(db.execute("SELECT value FROM search_metadata").fetchone(), ("999",))
        self.assertEqual(db.execute("SELECT name FROM items").fetchone(), ("قلم",))

    def test_bulk_update_keeps_items_and_index_atomic(self):
        for isolation_level in ("DEFERRED", None):
            with self.subTest(isolation_level=isolation_level):
                db = sqlite3.connect(":memory:", isolation_level=isolation_level)
                self.addCleanup(db.close)
                search = Search(db)
                search.put("a", "1", "old", "001", "قلم", "النجم")
                valid = ("a", "1", "old", "001", "دفتر", "النجم")
                invalid = ("a", "1", "new", "002", None, "النجم")
                with self.assertRaises(TypeError):
                    search.put_many([valid, invalid])
                self.assertEqual([row[0] for row in search.find("a", "1", "قلم")], ["old"])
                self.assertEqual(search.find("a", "1", "دفتر"), [])
                search.put_many([valid, ("a", "1", "new", "002", "حبر", "النجم")])
                self.assertEqual(search.find("a", "1", "قلم"), [])
                self.assertEqual([row[0] for row in search.find("a", "1", "دفتر")], ["old"])
                self.assertEqual([row[0] for row in search.find("a", "1", "حبر")], ["new"])

    def test_index_update_respects_callers_transaction(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        search = Search(db)
        db.execute("BEGIN")
        search.put("a", "1", "first", "001", "قلم", "النجم")
        self.assertEqual(len(search.find("a", "1", "قلم")), 1)
        db.rollback()
        self.assertEqual(search.find("a", "1", "قلم"), [])

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
