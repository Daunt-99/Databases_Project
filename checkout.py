"""Book checkout logic for the library system."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from typing import Iterator, Union

import db
from fines import has_unpaid_fines


@contextmanager
def _checkout_transaction(conn) -> Iterator[None]:
    """Use a write transaction, or join the caller's existing transaction."""
    if conn.in_transaction:
        yield
    else:
        with db.transaction(conn):
            yield


def checkout(
    isbn: str,
    card_id: str,
    today: Union[str, date, datetime, None] = None,
    conn=None,
) -> int:
    """Check out one book and return the new BOOK_LOANS.loan_id.

    Raises db.LibraryError with a librarian-facing message if the book or
    borrower does not exist, the book is already out, the borrower already
    has the maximum number of active loans, or the borrower has an unpaid fine.

    The default call is checkout(isbn, card_id). ``today`` and ``conn`` are
    optional to support deterministic runs and callers that manage connections.
    If ``conn`` is already in a transaction, the insert participates in it and
    the caller remains responsible for committing or rolling it back.
    """
    isbn = "" if isbn is None else str(isbn).strip()
    card_id = "" if card_id is None else str(card_id).strip()
    if not isbn:
        raise db.LibraryError("Enter an ISBN to check out a book.")
    if not card_id:
        raise db.LibraryError("Enter a borrower card ID to check out a book.")

    date_out = db.fmt_date(db.today() if today is None else today)
    due_date = db.due_date_for(date_out)

    owns_connection = conn is None
    connection = db.get_connection() if owns_connection else conn
    try:
        with _checkout_transaction(connection):
            if connection.execute(
                "SELECT 1 FROM BOOK WHERE isbn = ?", (isbn,)
            ).fetchone() is None:
                raise db.LibraryError(f"Book ISBN {isbn} was not found.")

            if connection.execute(
                "SELECT 1 FROM BORROWER WHERE card_id = ?", (card_id,)
            ).fetchone() is None:
                raise db.LibraryError(f"Borrower card {card_id} was not found.")

            if connection.execute(
                "SELECT 1 FROM BOOK_LOANS WHERE isbn = ? AND date_in IS NULL LIMIT 1",
                (isbn,),
            ).fetchone() is not None:
                raise db.LibraryError(f"Book ISBN {isbn} is already checked out.")

            active_loans = connection.execute(
                "SELECT COUNT(*) FROM BOOK_LOANS WHERE card_id = ? AND date_in IS NULL",
                (card_id,),
            ).fetchone()[0]
            if active_loans >= db.MAX_ACTIVE_LOANS:
                raise db.LibraryError(
                    f"Borrower {card_id} already has {db.MAX_ACTIVE_LOANS} books checked out."
                )

            if has_unpaid_fines(card_id, today=date_out, conn=connection):
                raise db.LibraryError(
                    f"Borrower {card_id} has unpaid fines and cannot check out a book."
                )

            cursor = connection.execute(
                """INSERT INTO BOOK_LOANS (isbn, card_id, date_out, due_date)
                   VALUES (?, ?, ?, ?)""",
                (isbn, card_id, date_out, due_date),
            )
            return int(cursor.lastrowid)
    finally:
        if owns_connection:
            connection.close()
