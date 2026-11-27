import os
import tempfile
import unittest

import db
from borrowers import create_borrower
from checkin import checkin, find_loans


class Base(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))
        db.init_db(self.path, reset=True)
        self.conn = db.get_connection(self.path)
        self.addCleanup(self.conn.close)
        self.conn.executescript("""
            INSERT INTO BOOK VALUES ('0001', 'Alpha'), ('0002', 'Beta'), ('0003', 'Gamma'),
                                    ('0004', 'Delta');
            INSERT INTO BORROWER VALUES
              ('ID000001', '850-47-3740', 'Mark Morgan', '1 A St', NULL),
              ('ID000002', '256-95-4382', 'Eric Warren', '2 B St', NULL);
            INSERT INTO BOOK_LOANS(isbn, card_id, date_out, due_date, date_in) VALUES
              ('0001', 'ID000001', '2026-11-01', '2026-11-15', NULL),
              ('0002', 'ID000001', '2026-11-01', '2026-11-15', NULL),
              ('0003', 'ID000002', '2026-11-01', '2026-11-15', NULL),
              ('0004', 'ID000002', '2026-10-01', '2026-10-15', '2026-10-10');
        """)


class FindLoansTests(Base):
    def test_by_each_filter(self):
        self.assertEqual(len(find_loans(isbn="0001", conn=self.conn)), 1)
        self.assertEqual(len(find_loans(card_id="id000001", conn=self.conn)), 2)
        self.assertEqual(len(find_loans(name="WARR", conn=self.conn)), 1)

    def test_combined_and_active_only(self):
        self.assertEqual(find_loans(isbn="0001", card_id="ID000002", conn=self.conn), [])
        self.assertEqual(len(find_loans(card_id="ID000002", conn=self.conn)), 1)
        self.assertEqual(len(find_loans(card_id="ID000002", active_only=False, conn=self.conn)), 2)

    def test_wildcards_are_literal(self):
        self.assertEqual(find_loans(name="%", conn=self.conn), [])


class CheckinTests(Base):
    def test_checkin_one_and_many(self):
        self.assertEqual(checkin([1, 2], today="2026-11-10", conn=self.conn), 2)
        self.assertEqual(find_loans(card_id="ID000001", conn=self.conn), [])
        row = self.conn.execute("SELECT date_in FROM BOOK_LOANS WHERE loan_id=1").fetchone()
        self.assertEqual(row[0], "2026-11-10")

    def test_book_can_be_checked_out_again_after_checkin(self):
        checkin(1, today="2026-11-10", conn=self.conn)
        self.conn.execute("INSERT INTO BOOK_LOANS(isbn, card_id, date_out, due_date) "
                          "VALUES ('0001','ID000002','2026-11-11','2026-11-25')")

    def test_all_or_nothing(self):
        with self.assertRaises(db.LibraryError):
            checkin([1, 999], conn=self.conn)
        self.assertEqual(len(find_loans(card_id="ID000001", conn=self.conn)), 2)

    def test_already_returned_and_limits(self):
        with self.assertRaises(db.LibraryError):
            checkin([4], conn=self.conn)
        with self.assertRaises(db.LibraryError):
            checkin([], conn=self.conn)
        with self.assertRaises(db.LibraryError):
            checkin([1, 2, 3, 4], conn=self.conn)

    def test_date_before_checkout_rejected(self):
        with self.assertRaises(db.LibraryError):
            checkin(1, today="2026-01-01", conn=self.conn)


class CreateBorrowerTests(Base):
    def test_creates_with_next_card_id_and_clean_fields(self):
        cid = create_borrower("jane  doe", "123456789", "9 Main St", "(214) 555-0000", conn=self.conn)
        self.assertEqual(cid, "ID000003")
        row = self.conn.execute("SELECT * FROM BORROWER WHERE card_id=?", (cid,)).fetchone()
        self.assertEqual((row["bname"], row["ssn"]), ("Jane Doe", "123-45-6789"))

    def test_duplicate_ssn_rejected_any_format(self):
        with self.assertRaises(db.LibraryError) as cm:
            create_borrower("Someone Else", "850473740", "x", conn=self.conn)
        self.assertIn("already exists", str(cm.exception))

    def test_required_fields(self):
        for args in [("", "111223333", "a"), ("A B", "", "a"), ("A B", "111223333", "  "),
                     ("A B", "12345", "a")]:
            with self.assertRaises(db.LibraryError):
                create_borrower(*args, conn=self.conn)

    def test_ids_increase(self):
        a = create_borrower("A One", "111-22-3333", "x", conn=self.conn)
        b = create_borrower("B Two", "444-55-6666", "y", conn=self.conn)
        self.assertEqual((a, b), ("ID000003", "ID000004"))


if __name__ == "__main__":
    unittest.main()
