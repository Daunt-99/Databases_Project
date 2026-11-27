"""Book check-in logic for the library system (Milestone 2, Person 4)."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from typing import Iterator, Union

import db

MAX_CHECKIN_BATCH = 3


@contextmanager
def _write_transaction(conn) -> Iterator[None]:
    """Use a write transaction, or join the caller's existing transaction."""
    if conn.in_transaction:
        yield
    else:
        with db.transaction(conn):
            yield


def find_loans(isbn=None, card_id=None, name=None, active_only=True, conn=None) -> list:
    """Find loans by ISBN, borrower card ID and/or a substring of the borrower name.

    Any combination may be given; all supplied filters must match. Name matching
    is a case-insensitive substring match. By default only loans that are still
    out (date_in IS NULL) are returned; pass active_only=False to include
    returned loans. Returns a list of dicts ordered by due date:
    loan_id, isbn, title, card_id, bname, date_out, due_date, date_in.
    """
    sql = """SELECT l.loan_id, l.isbn, b.title, l.card_id, br.bname,
                    l.date_out, l.due_date, l.date_in
             FROM BOOK_LOANS l
             JOIN BOOK b      ON b.isbn = l.isbn
             JOIN BORROWER br ON br.card_id = l.card_id
             WHERE 1 = 1"""
    args: list = []
    if active_only:
        sql += " AND l.date_in IS NULL"
    if isbn and str(isbn).strip():
        sql += " AND l.isbn = ?"
        args.append(str(isbn).strip())
    if card_id and str(card_id).strip():
        sql += " AND UPPER(l.card_id) = UPPER(?)"
        args.append(str(card_id).strip())
    if name and str(name).strip():
        # instr() instead of LIKE so '%' and '_' in the input are literal.
        sql += " AND instr(lower(br.bname), ?) > 0"
        args.append(str(name).strip().lower())
    sql += " ORDER BY l.due_date, l.loan_id"

    owns = conn is None
    connection = db.get_connection() if owns else conn
    try:
        return [dict(zip(r.keys(), tuple(r))) for r in connection.execute(sql, args)]
    finally:
        if owns:
            connection.close()


def checkin(loan_ids, today: Union[str, date, datetime, None] = None, conn=None) -> int:
    """Check in 1-3 loans (sets date_in) and return how many were checked in.

    All-or-nothing: if any loan id is unknown or already returned, a
    db.LibraryError is raised and no loan is changed.
    """
    if isinstance(loan_ids, (int, str)):
        loan_ids = [loan_ids]
    try:
        ids = list(dict.fromkeys(int(i) for i in loan_ids))   # dedupe, keep order
    except (TypeError, ValueError):
        raise db.LibraryError("Loan IDs must be whole numbers.")
    if not 1 <= len(ids) <= MAX_CHECKIN_BATCH:
        raise db.LibraryError(
            f"Select between 1 and {MAX_CHECKIN_BATCH} loans to check in.")

    date_in = db.fmt_date(db.today() if today is None else today)

    owns = conn is None
    connection = db.get_connection() if owns else conn
    try:
        with _write_transaction(connection):
            for lid in ids:
                row = connection.execute(
                    "SELECT date_in FROM BOOK_LOANS WHERE loan_id = ?", (lid,)
                ).fetchone()
                if row is None:
                    raise db.LibraryError(f"Loan {lid} was not found.")
                if row[0] is not None:
                    raise db.LibraryError(f"Loan {lid} was already checked in.")
            try:
                for lid in ids:
                    connection.execute(
                        "UPDATE BOOK_LOANS SET date_in = ? WHERE loan_id = ?",
                        (date_in, lid))
            except sqlite3.IntegrityError:
                raise db.LibraryError(
                    "Check-in date cannot be earlier than the checkout date.")
        return len(ids)
    finally:
        if owns:
            connection.close()
