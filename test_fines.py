"""Tests for fines.py. Run with:  python test_fines.py

Uses its own throwaway database that follows the Milestone 2 schema, so it runs even before
Person 1's schema.sql and db.py exist.
"""

import os
import sqlite3
import sys
import tempfile
import types
import unittest
from decimal import Decimal

import fines

SCHEMA = """
CREATE TABLE BORROWER (card_id TEXT PRIMARY KEY, ssn TEXT UNIQUE NOT NULL, bname TEXT NOT NULL,
                       address TEXT NOT NULL, phone TEXT);
CREATE TABLE BOOK_LOANS (loan_id INTEGER PRIMARY KEY AUTOINCREMENT, isbn TEXT NOT NULL,
                         card_id TEXT NOT NULL, date_out TEXT NOT NULL, due_date TEXT NOT NULL,
                         date_in TEXT);
CREATE TABLE FINES (loan_id INTEGER PRIMARY KEY, fine_amt INTEGER NOT NULL, paid INTEGER NOT NULL DEFAULT 0);
"""
TODAY = "2026-11-10"


def make_db(path=":memory:"):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO BORROWER VALUES (?,?,?,?,?)",
        [("ID000001", "111-11-1111", "Mark Morgan", "1 Main St", None),
         ("ID000002", "222-22-2222", "Eric Warren", "2 Main St", None)],
    )
    conn.commit()
    return conn


def loan(conn, isbn, card, due, date_in=None):
    cur = conn.execute(
        "INSERT INTO BOOK_LOANS (isbn, card_id, date_out, due_date, date_in) VALUES (?,?,?,?,?)",
        (isbn, card, "2026-10-01", due, date_in),
    )
    conn.commit()
    return cur.lastrowid


def fine_row(conn, loan_id):
    return conn.execute("SELECT fine_amt, paid FROM FINES WHERE loan_id=?", (loan_id,)).fetchone()


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.c = make_db()

    def test_returned_late_uses_date_in(self):
        l = loan(self.c, "A", "ID000001", "2026-11-01", "2026-11-05")  # 4 days
        fines.refresh_fines(TODAY, self.c)
        self.assertEqual(fine_row(self.c, l), (100, 0))

    def test_still_out_uses_today(self):
        l = loan(self.c, "A", "ID000001", "2026-11-01")  # 9 days to 11-10
        fines.refresh_fines(TODAY, self.c)
        self.assertEqual(fine_row(self.c, l), (225, 0))

    def test_not_late_gets_no_row(self):
        on_time = loan(self.c, "A", "ID000001", "2026-11-05", "2026-11-05")
        due_today = loan(self.c, "B", "ID000001", TODAY)
        not_due = loan(self.c, "C", "ID000001", "2026-11-20")
        fines.refresh_fines(TODAY, self.c)
        for l in (on_time, due_today, not_due):
            self.assertIsNone(fine_row(self.c, l))

    def test_unpaid_row_updated_not_duplicated(self):
        l = loan(self.c, "A", "ID000001", "2026-11-01")
        fines.refresh_fines("2026-11-04", self.c)
        self.assertEqual(fine_row(self.c, l), (75, 0))
        counts = fines.refresh_fines(TODAY, self.c)
        self.assertEqual(fine_row(self.c, l), (225, 0))
        self.assertEqual(counts["updated"], 1)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM FINES").fetchone()[0], 1)

    def test_same_day_rerun_changes_nothing(self):
        loan(self.c, "A", "ID000001", "2026-11-01")
        fines.refresh_fines(TODAY, self.c)
        counts = fines.refresh_fines(TODAY, self.c)
        self.assertEqual(counts, {"inserted": 0, "updated": 0, "unchanged": 1, "skipped_paid": 0})

    def test_paid_row_is_never_touched(self):
        l = loan(self.c, "A", "ID000001", "2026-11-01", "2026-11-03")  # 2 days = 50
        fines.refresh_fines(TODAY, self.c)
        self.c.execute("UPDATE FINES SET paid=1, fine_amt=999 WHERE loan_id=?", (l,))
        self.c.commit()
        counts = fines.refresh_fines(TODAY, self.c)
        self.assertEqual(fine_row(self.c, l), (999, 1))
        self.assertEqual(counts["skipped_paid"], 1)

    def test_returning_a_book_finalises_the_estimate(self):
        l = loan(self.c, "A", "ID000001", "2026-11-01")
        fines.refresh_fines(TODAY, self.c)  # estimate 225
        self.c.execute("UPDATE BOOK_LOANS SET date_in='2026-11-06' WHERE loan_id=?", (l,))
        fines.refresh_fines(TODAY, self.c)
        self.assertEqual(fine_row(self.c, l), (125, 0))

    def test_datetime_strings_work(self):
        l = loan(self.c, "A", "ID000001", "2026-11-01 00:00:00", "2026-11-03 14:30:00")
        fines.refresh_fines(TODAY, self.c)
        self.assertEqual(fine_row(self.c, l), (50, 0))


class PaymentTests(unittest.TestCase):
    def setUp(self):
        self.c = make_db()

    def test_pay_clears_all_fines_and_returns_decimal_total(self):
        a = loan(self.c, "A", "ID000001", "2026-11-01", "2026-11-05")  # 100
        b = loan(self.c, "B", "ID000001", "2026-11-02", "2026-11-03")  # 25
        other = loan(self.c, "C", "ID000002", "2026-11-01", "2026-11-02")  # someone else
        fines.refresh_fines(TODAY, self.c)
        r = fines.pay_fines("ID000001", TODAY, self.c)
        self.assertTrue(r.success, r.message)
        self.assertEqual(r.amount, Decimal("1.25"))
        self.assertEqual(fine_row(self.c, a)[1], 1)
        self.assertEqual(fine_row(self.c, b)[1], 1)
        self.assertEqual(fine_row(self.c, other)[1], 0)  # other borrower untouched

    def test_refused_when_fined_book_not_returned_and_nothing_changes(self):
        returned = loan(self.c, "A", "ID000001", "2026-11-01", "2026-11-05")
        out = loan(self.c, "B", "ID000001", "2026-11-01")
        fines.refresh_fines(TODAY, self.c)
        r = fines.pay_fines("ID000001", TODAY, self.c)
        self.assertFalse(r.success)
        self.assertIn("not been returned", r.message)
        self.assertEqual(fine_row(self.c, returned)[1], 0)  # no partial payment
        self.assertEqual(fine_row(self.c, out)[1], 0)

    def test_flag_is_on_by_default(self):
        self.assertTrue(fines.REFUSE_PAYMENT_IF_ANY_BOOK_OUT)

    def test_fine_on_returned_book_can_only_be_paid_after_other_book_is_returned(self):
        fined = loan(self.c, "A", "ID000001", "2026-11-01", "2026-11-05")  # returned late: $1.00
        other = loan(self.c, "B", "ID000001", "2026-11-30")                # still out, NOT late

        # 1. Blocked while the on-time book is still out, and nothing is marked paid.
        r = fines.pay_fines("ID000001", TODAY, self.c)
        self.assertFalse(r.success)
        self.assertIn("still has 1 book(s) checked out", r.message)
        self.assertEqual(r.amount, Decimal("1.00"))  # shows what is owed
        self.assertEqual(fine_row(self.c, fined), (100, 0))
        self.assertTrue(fines.has_unpaid_fines("ID000001", TODAY, self.c))

        # 2. Still blocked on a second attempt (nothing changed).
        self.assertFalse(fines.pay_fines("ID000001", TODAY, self.c).success)

        # 3. The borrower returns the other book (on time, so no new fine).
        self.c.execute("UPDATE BOOK_LOANS SET date_in='2026-11-08' WHERE loan_id=?", (other,))
        self.c.commit()

        # 4. Now the payment goes through and clears the fine.
        r = fines.pay_fines("ID000001", TODAY, self.c)
        self.assertTrue(r.success, r.message)
        self.assertEqual(r.amount, Decimal("1.00"))
        self.assertEqual(fine_row(self.c, fined), (100, 1))
        self.assertFalse(fines.has_unpaid_fines("ID000001", TODAY, self.c))

    def test_other_borrowers_book_out_does_not_block(self):
        loan(self.c, "A", "ID000001", "2026-11-01", "2026-11-05")
        loan(self.c, "B", "ID000002", "2026-11-30")  # someone else has a book out
        self.assertTrue(fines.pay_fines("ID000001", TODAY, self.c).success)

    def test_flag_off_follows_the_narrower_pdf_rule(self):
        loan(self.c, "A", "ID000001", "2026-11-01", "2026-11-03")
        loan(self.c, "B", "ID000001", "2026-11-30")  # on time, still out
        fines.REFUSE_PAYMENT_IF_ANY_BOOK_OUT = False
        try:
            r = fines.pay_fines("ID000001", TODAY, self.c)
        finally:
            fines.REFUSE_PAYMENT_IF_ANY_BOOK_OUT = True
        self.assertTrue(r.success, r.message)

    def test_pay_uses_current_amount_without_a_prior_refresh(self):
        l = loan(self.c, "A", "ID000001", "2026-11-01", "2026-11-05")  # never refreshed
        r = fines.pay_fines("ID000001", TODAY, self.c)
        self.assertTrue(r.success, r.message)
        self.assertEqual(r.amount, Decimal("1.00"))
        self.assertEqual(fine_row(self.c, l), (100, 1))

    def test_nothing_to_pay_and_unknown_card(self):
        self.assertFalse(fines.pay_fines("ID000001", TODAY, self.c).success)
        r = fines.pay_fines("NOPE", TODAY, self.c)
        self.assertFalse(r.success)
        self.assertIn("No borrower", r.message)
        self.assertFalse(fines.pay_fines("  ", TODAY, self.c).success)

    def test_cannot_pay_twice(self):
        loan(self.c, "A", "ID000001", "2026-11-01", "2026-11-05")
        self.assertTrue(fines.pay_fines("ID000001", TODAY, self.c).success)
        self.assertFalse(fines.pay_fines("ID000001", TODAY, self.c).success)


class ListTests(unittest.TestCase):
    def setUp(self):
        self.c = make_db()
        loan(self.c, "A", "ID000001", "2026-11-01", "2026-11-05")  # 100
        loan(self.c, "B", "ID000001", "2026-11-02", "2026-11-03")  # 25
        loan(self.c, "C", "ID000002", "2026-11-01")                 # 225, still out
        fines.refresh_fines(TODAY, self.c)

    def test_grouped_by_card_with_exact_sum(self):
        rows = fines.list_fines(conn=self.c)
        self.assertEqual([(r["card_id"], r["total"], r["fine_count"]) for r in rows],
                         [("ID000001", Decimal("1.25"), 2), ("ID000002", Decimal("2.25"), 1)])
        self.assertEqual(rows[0]["name"], "Mark Morgan")
        self.assertTrue(rows[0]["payable"])
        self.assertFalse(rows[1]["payable"])  # fined book still out

    def test_payable_is_false_while_any_book_is_out(self):
        loan(self.c, "E", "ID000001", "2026-11-30")  # on-time book out for ID000001
        rows = {r["card_id"]: r for r in fines.list_fines(conn=self.c)}
        self.assertEqual(rows["ID000001"]["books_out"], 1)
        self.assertFalse(rows["ID000001"]["payable"])

    def test_paid_hidden_by_default_and_shown_on_request(self):
        fines.pay_fines("ID000001", TODAY, self.c)
        self.assertEqual([r["card_id"] for r in fines.list_fines(conn=self.c)], ["ID000002"])
        both = fines.list_fines(include_paid=True, conn=self.c)
        self.assertEqual([(r["card_id"], r["paid"], r["total"]) for r in both],
                         [("ID000001", True, Decimal("1.25")), ("ID000002", False, Decimal("2.25"))])

    def test_paid_and_unpaid_for_same_borrower_stay_separate(self):
        fines.pay_fines("ID000001", TODAY, self.c)
        loan(self.c, "D", "ID000001", "2026-11-04", "2026-11-06")  # new 50 fine
        fines.refresh_fines(TODAY, self.c)
        rows = [r for r in fines.list_fines(include_paid=True, conn=self.c) if r["card_id"] == "ID000001"]
        self.assertEqual([(r["paid"], r["total"]) for r in rows],
                         [(False, Decimal("0.50")), (True, Decimal("1.25"))])

    def test_cents_are_exact(self):
        self.assertEqual(fines.cents_to_decimal(105), Decimal("1.05"))
        self.assertEqual(str(fines.cents_to_decimal(5)), "0.05")
        self.assertEqual(fines.format_dollars(fines.cents_to_decimal(123456)), "$1,234.56")


class HasUnpaidFinesTests(unittest.TestCase):
    def setUp(self):
        self.c = make_db()

    def test_false_with_no_loans_or_on_time_loans(self):
        self.assertFalse(fines.has_unpaid_fines("ID000001", TODAY, self.c))
        loan(self.c, "A", "ID000001", "2026-11-05", "2026-11-05")
        loan(self.c, "B", "ID000001", TODAY)  # due today is not late
        self.assertFalse(fines.has_unpaid_fines("ID000001", TODAY, self.c))

    def test_true_for_unpaid_row_false_after_payment(self):
        loan(self.c, "A", "ID000001", "2026-11-01", "2026-11-05")
        fines.refresh_fines(TODAY, self.c)
        self.assertTrue(fines.has_unpaid_fines("ID000001", TODAY, self.c))
        self.assertFalse(fines.has_unpaid_fines("ID000002", TODAY, self.c))
        fines.pay_fines("ID000001", TODAY, self.c)
        self.assertFalse(fines.has_unpaid_fines("ID000001", TODAY, self.c))

    def test_true_for_late_loan_not_yet_recorded_by_refresh(self):
        loan(self.c, "A", "ID000001", "2026-11-01")  # overdue, still out, no FINES row
        loan(self.c, "B", "ID000002", "2026-11-01", "2026-11-05")  # returned late, no row
        self.assertTrue(fines.has_unpaid_fines("ID000001", TODAY, self.c))
        self.assertTrue(fines.has_unpaid_fines("ID000002", TODAY, self.c))

    def test_safe_to_call_while_another_connection_holds_a_write_transaction(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "lib.db")
            writer = make_db(path)
            reader = sqlite3.connect(path)
            writer.execute("BEGIN IMMEDIATE")  # what checkout() will do
            try:
                self.assertFalse(fines.has_unpaid_fines("ID000001", TODAY, reader))
            finally:
                writer.rollback()
                reader.close()
                writer.close()


class DefaultConnectionTests(unittest.TestCase):
    """Calling without conn= must go through db.get_connection(), like Person 3's call."""

    def test_default_connection_path(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "lib.db")
            c = make_db(path)
            loan(c, "A", "ID000001", "2026-11-01", "2026-11-05")
            c.close()

            stub = types.ModuleType("db")
            stub.get_connection = lambda: sqlite3.connect(path)
            sys.modules["db"] = stub
            try:
                self.assertEqual(fines.refresh_fines(TODAY)["inserted"], 1)
                self.assertTrue(fines.has_unpaid_fines("ID000001", TODAY))
                self.assertEqual(len(fines.list_fines()), 1)
                self.assertTrue(fines.pay_fines("ID000001", TODAY).success)
                self.assertFalse(fines.has_unpaid_fines("ID000001", TODAY))
            finally:
                del sys.modules["db"]


if __name__ == "__main__":
    unittest.main(verbosity=2)
