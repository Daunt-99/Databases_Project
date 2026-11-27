"""Book check-in logic for the library system (Milestone 2, Person 4)."""

from __future__ import annotations

import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime
from typing import Iterator, Union

import db

MAX_CHECKIN_BATCH = 3


def _fold(text) -> str:
    """Unicode-aware key for matching: composed form + casefold.

    SQLite's lower() only handles ASCII, so 'Élodie' would not match 'élodie'
    if the comparison were done in SQL.
    """
    return unicodedata.normalize("NFC", str(text)).casefold()


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
    is a Unicode-aware, case-insensitive substring match ('élodie' finds 'Élodie'). By default only loans that are still
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
    sql += " ORDER BY l.due_date, l.loan_id"
    name_key = _fold(str(name).strip()) if name and str(name).strip() else None

    owns = conn is None
    connection = db.get_connection() if owns else conn
    try:
        rows = [dict(zip(r.keys(), tuple(r))) for r in connection.execute(sql, args)]
        if name_key is not None:
            # Done in Python (not SQL) for Unicode case-folding; also keeps
            # '%' and '_' in the input literal.
            rows = [r for r in rows if name_key in _fold(r["bname"])]
        return rows
    finally:
        if owns:
            connection.close()


def checkin(loan_ids, today: Union[str, date, datetime, None] = None, conn=None) -> int:
    """Check in 1-3 loans (sets date_in) and return how many were checked in.

    All-or-nothing: if any loan id is unknown, already returned, or the check-in
    date is before its checkout date, db.LibraryError is raised and no loan is
    changed (also when called inside the caller's own transaction).
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
            # Validate EVERY selected loan before changing any of them, so a
            # bad loan can never leave earlier ones half-updated (important
            # when we are joined to a caller's transaction and cannot roll back).
            for lid in ids:
                row = connection.execute(
                    "SELECT date_out, date_in FROM BOOK_LOANS WHERE loan_id = ?", (lid,)
                ).fetchone()
                if row is None:
                    raise db.LibraryError(f"Loan {lid} was not found.")
                if row[1] is not None:
                    raise db.LibraryError(f"Loan {lid} was already checked in.")
                if date_in < row[0]:
                    raise db.LibraryError(
                        f"Loan {lid}: check-in date {date_in} cannot be earlier "
                        f"than the checkout date {row[0]}.")
            try:
                for lid in ids:
                    connection.execute(
                        "UPDATE BOOK_LOANS SET date_in = ? WHERE loan_id = ?",
                        (date_in, lid))
            except sqlite3.IntegrityError as e:
                raise db.LibraryError(f"Could not check in: {e}")
        return len(ids)
    finally:
        if owns:
            connection.close()
