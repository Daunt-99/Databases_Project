import sqlite3
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from search import search


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.executescript("""
            CREATE TABLE BOOK (isbn TEXT PRIMARY KEY, title TEXT);
            CREATE TABLE AUTHORS (author_id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE BOOK_AUTHORS (isbn TEXT, author_id INTEGER);
            CREATE TABLE BOOK_LOANS (loan_id INTEGER PRIMARY KEY, isbn TEXT, date_in TEXT);
            INSERT INTO BOOK VALUES ('001X', 'Night Sky'), ('002X', 'Guide to 001X');
            INSERT INTO AUTHORS VALUES (1, 'Zoë Hill'), (2, 'Ana Jones');
            INSERT INTO BOOK_AUTHORS VALUES ('001X', 1), ('001X', 2);
            INSERT INTO BOOK_LOANS VALUES (1, '001X', '2026-01-01'),
                                         (2, '001X', NULL), (3, '002X', '2026-01-02');
        """)

    def test_mixed_fields_and_coauthors(self):
        self.assertEqual(search("01x IGHT ZOË jone", self.conn), [
            dict(isbn="001X", title="Night Sky", authors="Ana Jones, Zoë Hill", availability="OUT")
        ])

    def test_authorless_book_with_returned_loan(self):
        self.assertEqual(search("guide", self.conn), [
            dict(isbn="002X", title="Guide to 001X", authors="", availability="IN")
        ])

    def test_full_isbn(self):
        self.assertEqual([r["isbn"] for r in search(" 001x ", self.conn)], ["001X"])

    def test_literal_and_empty_queries(self):
        for query in ("%", "_", " ", "missing"):
            with self.subTest(query=query):
                self.assertEqual(search(query, self.conn), [])

    def test_project_connection_helper(self):
        with patch.dict(sys.modules, db=SimpleNamespace(get_connection=lambda: self.conn)):
            self.assertEqual(search("jones")[0]["isbn"], "001X")


if __name__ == "__main__":
    unittest.main()
