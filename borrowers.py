"""Borrower management for the library system (Milestone 2, Person 4)."""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from typing import Iterator

import db


@contextmanager
def _write_transaction(conn) -> Iterator[None]:
    """Use a write transaction, or join the caller's existing transaction."""
    if conn.in_transaction:
        yield
    else:
        with db.transaction(conn):
            yield


def clean_ssn(ssn) -> str:
    """Return the SSN as 123-45-6789 (same format as the loaded borrowers)."""
    digits = re.sub(r"\D", "", "" if ssn is None else str(ssn))
    if len(digits) != 9:
        raise db.LibraryError("SSN must contain exactly 9 digits.")
    return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"


def next_card_id(conn) -> str:
    """Next card id: highest existing number + 1, e.g. ID001001."""
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(card_id, ?) AS INTEGER)) FROM BORROWER",
        (len(db.CARD_ID_PREFIX) + 1,),
    ).fetchone()
    return db.format_card_id((row[0] or 0) + 1)


def create_borrower(name, ssn, address, phone=None, conn=None) -> str:
    """Create a borrower and return the new card_id.

    Name, SSN and address are required. One card per person: an SSN that is
    already registered is rejected with db.LibraryError.
    """
    name = " ".join(("" if name is None else str(name)).split())
    address = " ".join(("" if address is None else str(address)).split())
    phone = ("" if phone is None else str(phone)).strip() or None
    if not name:
        raise db.LibraryError("Name is required.")
    if not (ssn is not None and str(ssn).strip()):
        raise db.LibraryError("SSN is required.")
    if not address:
        raise db.LibraryError("Address is required.")
    ssn = clean_ssn(ssn)
    name = name.title()          # one case convention, like the loaded data

    owns = conn is None
    connection = db.get_connection() if owns else conn
    try:
        with _write_transaction(connection):
            existing = connection.execute(
                "SELECT card_id FROM BORROWER WHERE ssn = ?", (ssn,)).fetchone()
            if existing is not None:
                raise db.LibraryError(
                    f"A borrower with SSN {ssn} already exists "
                    f"(card {existing[0]}). Each person may have only one library card.")
            card_id = next_card_id(connection)
            try:
                connection.execute(
                    "INSERT INTO BORROWER (card_id, ssn, bname, address, phone) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (card_id, ssn, name, address, phone))
            except sqlite3.IntegrityError as e:
                raise db.LibraryError(f"Could not create borrower: {e}")
        return card_id
    finally:
        if owns:
            connection.close()
