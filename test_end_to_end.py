"""
test_end_to_end.py - final end-to-end run (Person 1).

Loads the Milestone 1 CSVs from output/ into a temporary database and
runs every Milestone 2 requirement through the team's public functions:
search, checkout, find_loans/checkin, create_borrower and fines.

    python integrate_validate.py          # once, writes the CSVs to output/
    python -m unittest test_end_to_end -v

Every test starts from a fresh copy of the loaded database so tests are
independent and library.db is never touched. Dates get passed explicitly
(today=...) so results are the same anyday tests are run.
"""

import os
import shutil
import sys
import tempfile
import threading
import unittest
from decimal import Decimal
from pathlib import Path

import db
import loader

ROOT = Path(__file__).resolve().parent
CSV_DIR = ROOT / "output"

import borrowers  # noqa: E402  (Person 4)
import checkin    # noqa: E402  (Person 4)
import checkout   # noqa: E402  (Person 3)
import fines      # noqa: E402  (Person 5)
import search     # noqa: E402  (Person 2)

# Real rows from the Milestone 1 data
GRISHAM = ["9780385501200", "9780440237228", "9780385470810", "9780440211723"]
THREE_AUTHORS = "9780007155095"          # The Essential Spike Milligan
NO_AUTHOR = "9780007101955"              # At Ruby's
CARD, CARD2, CARD3 = "ID000001", "ID000002", "ID000003"   # Mark Morgan, Eric Warren, ...

DAY0 = "2026-01-05"      # checkout day
DUE = "2026-01-19"       # DAY0 + 14
LATE5 = "2026-01-24"     # 5 days late -> $1.25 per book
LATE6 = "2026-01-25"     # 6 days late -> $1.50 per book


class EndToEnd(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not (CSV_DIR / "books.csv").exists():
            raise unittest.SkipTest("output/books.csv not found: run "
                                    "'python integrate_validate.py' first")
        cls.tmp = tempfile.mkdtemp(prefix="library_e2e_")
        cls.base_db = Path(cls.tmp) / "base.db"
        cls.load_report = loader.load_all(CSV_DIR, cls.base_db)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        # test_fines.py swaps sys.modules["db"] for a stub and deletes it after;
        # make sure everything below uses the one real db module.
        sys.modules["db"] = db
        self.db_file = Path(self.tmp) / f"{self._testMethodName}.db"
        shutil.copyfile(self.base_db, self.db_file)
        self._old_env = os.environ.get("LIBRARY_DB")
        os.environ["LIBRARY_DB"] = str(self.db_file)   # default connection for every module
        self.conn = db.get_connection()

    def tearDown(self):
        self.conn.close()
        if self._old_env is None:
            os.environ.pop("LIBRARY_DB", None)
        else:
            os.environ["LIBRARY_DB"] = self._old_env

    def one(self, sql, *args):
        return self.conn.execute(sql, args).fetchone()[0]

    # ------------------------------------------------------------- loading
    def test_01_milestone1_data_loads_completely(self):
        rep = self.load_report
        self.assertEqual(rep.skipped, {}, str(rep))
        self.assertEqual(rep.counts, {"BOOK": 25000, "AUTHORS": 15459,
                                      "BOOK_AUTHORS": 30242, "BORROWER": 1000})
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.one("SELECT COUNT(*) FROM BOOK WHERE length(isbn) <> 13"), 0)

    # -------------------------------------------------------------- search
    def test_02_search_substring_case_insensitive_one_row_per_book(self):
        rows = search.search("gRiShAm")
        isbns = [r["isbn"] for r in rows]
        self.assertTrue(set(GRISHAM) <= set(isbns))
        self.assertEqual(len(isbns), len(set(isbns)), "duplicate rows for a book")
        self.assertTrue(all(set(r) >= {"isbn", "title", "authors", "availability"} for r in rows))
        self.assertTrue(all(r["availability"] == "IN" for r in rows))
        # matches title text and part of an ISBN too
        self.assertIn(GRISHAM[0], [r["isbn"] for r in search.search("painted hou")])
        self.assertIn(GRISHAM[0], [r["isbn"] for r in search.search("85501")])

    def test_03_search_groups_authors_and_keeps_authorless_books(self):
        [row] = [r for r in search.search(THREE_AUTHORS)]
        self.assertEqual(len(row["authors"].split(", ")), 3)
        [row] = search.search(NO_AUTHOR)                 # full ISBN -> exactly one row
        self.assertEqual(row["authors"], "")
        self.assertIn(NO_AUTHOR, [r["isbn"] for r in search.search("at ruby")])

    # ------------------------------------------------------------ checkout
    def test_04_checkout_rules(self):
        ids = [checkout.checkout(i, CARD, today=DAY0) for i in GRISHAM[:3]]
        row = self.conn.execute("SELECT date_out, due_date, date_in FROM BOOK_LOANS "
                                "WHERE loan_id = ?", (ids[0],)).fetchone()
        self.assertEqual(tuple(row), (DAY0, DUE, None))
        self.assertEqual(search.search(GRISHAM[0])[0]["availability"], "OUT")

        cases = {
            "4th active loan": (GRISHAM[3], CARD),
            "book out to someone else": (GRISHAM[0], CARD2),
            "book already out to same borrower": (GRISHAM[0], CARD),
            "unknown book": ("9999999999999", CARD2),
            "unknown borrower": (GRISHAM[3], "ID999999"),
        }
        for label, (isbn, card) in cases.items():
            with self.subTest(label):
                with self.assertRaises(db.LibraryError):
                    checkout.checkout(isbn, card, today=DAY0)
        self.assertEqual(self.one("SELECT COUNT(*) FROM BOOK_LOANS"), 3)

    def test_05_concurrent_checkouts_of_one_book_only_one_wins(self):
        results, barrier = [], threading.Barrier(5)

        def worker(card):
            barrier.wait()
            try:
                results.append(("ok", checkout.checkout(GRISHAM[0], card, today=DAY0)))
            except db.LibraryError as e:
                results.append(("refused", str(e)))

        threads = [threading.Thread(target=worker, args=(f"ID00000{i}",)) for i in range(1, 6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(results), 5, results)
        self.assertEqual([r[0] for r in results].count("ok"), 1, results)
        self.assertEqual(self.one("SELECT COUNT(*) FROM BOOK_LOANS WHERE isbn = ? "
                                  "AND date_in IS NULL", GRISHAM[0]), 1)

    # ------------------------------------------------------------- checkin
    def test_06_find_loans_and_checkin(self):
        ids = [checkout.checkout(i, CARD, today=DAY0) for i in GRISHAM[:3]]
        other = checkout.checkout(GRISHAM[3], CARD2, today=DAY0)

        with self.assertRaises(db.LibraryError):           # 4 real open loans: too many
            checkin.checkin([*ids, other], today=DUE)
        self.assertEqual(self.one("SELECT COUNT(*) FROM BOOK_LOANS WHERE date_in IS NULL"), 4)

        self.assertEqual(len(checkin.find_loans(card_id=CARD)), 3)
        self.assertEqual(len(checkin.find_loans(card_id=CARD.lower())), 3)
        self.assertEqual([r["loan_id"] for r in checkin.find_loans(isbn=GRISHAM[1])], [ids[1]])
        by_name = checkin.find_loans(name="MORG")          # Mark Morgan
        self.assertEqual({r["loan_id"] for r in by_name}, set(ids))
        self.assertTrue(all(set(r) >= {"loan_id", "isbn", "title", "card_id", "bname"}
                            for r in by_name))

        self.assertEqual(checkin.checkin([ids[0]], today=DUE), 1)
        self.assertEqual(search.search(GRISHAM[0])[0]["availability"], "IN")
        self.assertEqual(len(checkin.find_loans(card_id=CARD)), 2)

        for label, bad in {"already checked in": [ids[0]], "empty": [],
                           "more than 3": [1, 2, 3, 4], "unknown id": [999999],
                           "mix of good and bad": [ids[1], 999999]}.items():
            with self.subTest(label):
                with self.assertRaises(db.LibraryError):
                    checkin.checkin(bad, today=DUE)
        # the failed mixed batch changed nothing
        self.assertEqual(len(checkin.find_loans(card_id=CARD)), 2)
        self.assertEqual(checkin.checkin(ids[1:], today=DUE), 2)

    # ----------------------------------------------------------- borrowers
    def test_07_create_borrower(self):
        card = borrowers.create_borrower("Jane Doe", "123-45-6789", "1 Main St, Dallas, TX",
                                         "(214) 555-0000")
        self.assertEqual(card, "ID001001")                  # follows the 1,000 loaded cards
        self.assertEqual(borrowers.create_borrower("John Roe", "123456780", "2 Elm St"),
                         "ID001002")
        existing_ssn = self.one("SELECT ssn FROM BORROWER WHERE card_id = ?", CARD)
        for label, args in {"duplicate SSN": ("X", "123-45-6789", "Y"),
                            "duplicate loaded SSN, no dashes": ("X", existing_ssn.replace("-", ""), "Y"),
                            "missing name": ("", "111-22-3333", "Y"),
                            "missing SSN": ("X", "", "Y"),
                            "missing address": ("X", "111-22-3334", "  ")}.items():
            with self.subTest(label):
                with self.assertRaises(db.LibraryError):
                    borrowers.create_borrower(*args)
        self.assertEqual(self.one("SELECT COUNT(*) FROM BORROWER"), 1002)
        # a new borrower can use the library straight away
        self.assertTrue(checkout.checkout(GRISHAM[0], card, today=DAY0))

    # --------------------------------------------------------------- fines
    def test_08_fines_full_lifecycle(self):
        ids = [checkout.checkout(i, CARD, today=DAY0) for i in GRISHAM[:3]]
        checkin.checkin([ids[0]], today=LATE5)               # returned 5 days late

        counts = fines.refresh_fines(today=LATE5)
        self.assertEqual(counts["inserted"], 3)             # 1 returned + 2 still out, all late
        self.assertEqual(self.one("SELECT typeof(fine_amt) FROM FINES LIMIT 1"), "integer")
        self.assertEqual(self.one("SELECT SUM(fine_amt) FROM FINES"), 3 * 125)
        [row] = [r for r in fines.list_fines() if r["card_id"] == CARD]
        self.assertEqual(row["total"], Decimal("3.75"))
        self.assertTrue(fines.has_unpaid_fines(CARD, today=LATE5))

        # unpaid fines block checkout (borrower has a free slot after the return)
        with self.assertRaises(db.LibraryError):
            checkout.checkout(GRISHAM[3], CARD, today=LATE5)
        # can't pay while books are still out; nothing gets marked paid
        res = fines.pay_fines(CARD, today=LATE5)
        self.assertFalse(res.success)
        self.assertEqual(self.one("SELECT SUM(paid) FROM FINES"), 0)

        # next day: fines on books still out grow, the returned one does not
        counts = fines.refresh_fines(today=LATE6)
        self.assertEqual((counts["inserted"], counts["updated"]), (0, 2))
        amts = dict(self.conn.execute("SELECT loan_id, fine_amt FROM FINES").fetchall())
        self.assertEqual(amts, {ids[0]: 125, ids[1]: 150, ids[2]: 150})

        # return the rest, then pay everything in one payment
        checkin.checkin(ids[1:], today=LATE6)
        fines.refresh_fines(today=LATE6)
        res = fines.pay_fines(CARD, today=LATE6)
        self.assertTrue(res.success, res.message)
        self.assertEqual(res.amount, Decimal("4.25"))
        self.assertFalse(fines.has_unpaid_fines(CARD, today=LATE6))
        self.assertNotIn(CARD, [r["card_id"] for r in fines.list_fines()])
        paid = [r for r in fines.list_fines(include_paid=True) if r["card_id"] == CARD]
        self.assertEqual(sum(r["total"] for r in paid), Decimal("4.25"))

        # paid fines are never changed by later refreshes
        counts = fines.refresh_fines(today="2026-03-01")
        self.assertEqual(self.one("SELECT SUM(fine_amt) FROM FINES"), 425)
        self.assertGreaterEqual(counts["skipped_paid"], 3)

        # and the borrower can check out again
        self.assertTrue(checkout.checkout(GRISHAM[3], CARD, today="2026-03-01"))

    def test_09_on_time_returns_create_no_fines(self):
        lid = checkout.checkout(GRISHAM[0], CARD3, today=DAY0)
        checkin.checkin([lid], today=DUE)                    # returned on the due date
        fines.refresh_fines(today="2026-02-15")
        self.assertEqual(self.one("SELECT COUNT(*) FROM FINES WHERE fine_amt > 0"), 0)
        self.assertFalse(fines.has_unpaid_fines(CARD3, today="2026-02-15"))


    def test_10_team_rule_no_payment_while_any_book_out(self):
        """fines.REFUSE_PAYMENT_IF_ANY_BOOK_OUT: an on-time book still out blocks payment."""
        late = checkout.checkout(GRISHAM[0], CARD2, today="2025-12-20")     # due 2026-01-03
        on_time = checkout.checkout(GRISHAM[1], CARD2, today="2026-01-02")  # due 2026-01-16
        checkin.checkin([late], today="2026-01-08")          # 5 days late: $1.25, returned
        fines.refresh_fines(today="2026-01-08")              # other book not due yet: no fine
        self.assertEqual(self.one("SELECT COUNT(*) FROM FINES"), 1)
        res = fines.pay_fines(CARD2, today="2026-01-08")
        self.assertFalse(res.success)                        # blocked by the on-time book
        self.assertEqual(self.one("SELECT paid FROM FINES"), 0)
        checkin.checkin([on_time], today="2026-01-08")
        res = fines.pay_fines(CARD2, today="2026-01-08")
        self.assertTrue(res.success, res.message)
        self.assertEqual(res.amount, Decimal("1.25"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
