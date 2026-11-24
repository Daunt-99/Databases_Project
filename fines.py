"""
Fines module (Milestone 2, Person 5).

Public functions
    refresh_fines(today=None, conn=None, card_id=None) -> dict
    pay_fines(card_id, today=None, conn=None)           -> PaymentResult
    list_fines(include_paid=False, conn=None)           -> list[dict]
    has_unpaid_fines(card_id, today=None, conn=None)    -> bool   (Person 3 calls this)

Rules implemented (from the Milestone 2 PDF)
    * $0.25 per day late.
    * Returned late:      fine = (date_in - due_date) days * $0.25
    * Still out and late: fine = (today   - due_date) days * $0.25
    * Existing FINES row: paid = 0 -> update fine_amt only if it changed (never a second row)
                          paid = 1 -> do nothing
    * Payment is refused while any fine belongs to a book that has not been returned.
    * No partial payment: a successful payment marks ALL of the borrower's unpaid fines paid.
    * Listing is grouped by card_id with SUM(fine_amt) and hides paid fines by default.

Money format (agree this with Person 1's schema.sql)
    FINES.fine_amt is stored as INTEGER CENTS (125 means $1.25), so SUM() is exact and there is
    no float rounding. Everything this module returns to callers is a decimal.Decimal in dollars
    with two places, e.g. Decimal("1.25").

Assumed schema (column names from the PDF; table/column names are case-insensitive in SQLite)
    BORROWER(card_id, ssn, bname, address, phone)
    BOOK_LOANS(loan_id, isbn, card_id, date_out, due_date, date_in)   dates as 'YYYY-MM-DD'
    FINES(loan_id PRIMARY KEY, fine_amt INTEGER, paid INTEGER 0/1)

Connections
    Every function takes an optional sqlite3 connection. If you omit it, the module calls
    db.get_connection() (Person 1's helper) and closes that connection when it is done.
    If you are already inside a transaction (e.g. checkout), pass your own connection.
"""

from collections import namedtuple
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

FINE_RATE_CENTS_PER_DAY = 25  # $0.25 per day

# The PDF only forbids paying fines for books that are not yet returned. Set this to True
# for the stricter reading "refuse if the borrower has ANY book still out", even an
# on-time one with no fine.
REFUSE_PAYMENT_IF_ANY_BOOK_OUT = False

PaymentResult = namedtuple("PaymentResult", ["success", "message", "amount"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def cents_to_decimal(cents):
    """125 -> Decimal('1.25')."""
    return (Decimal(int(cents or 0)) / Decimal(100)).quantize(Decimal("0.01"))


def format_dollars(amount):
    return f"${amount:,.2f}"


def _parse_date(value):
    """Accept 'YYYY-MM-DD', 'YYYY-MM-DD HH:MM:SS', date or datetime."""
    if isinstance(value, date):
        return date(value.year, value.month, value.day)
    return date.fromisoformat(str(value)[:10])


def _today_iso(today):
    return (_parse_date(today) if today else date.today()).isoformat()


@contextmanager
def _connection(conn):
    """Yield a usable connection; close it afterwards only if we opened it."""
    if conn is not None:
        yield conn
        return
    from db import get_connection  # Person 1's helper

    own = get_connection()
    try:
        yield own
    finally:
        own.close()


@contextmanager
def _write_transaction(conn):
    """BEGIN IMMEDIATE so two writers cannot interleave. If the caller already opened a
    transaction on this connection, join it and leave commit/rollback to them."""
    started = not conn.in_transaction
    if started:
        conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except Exception:
        if started:
            conn.rollback()
        raise
    else:
        if started:
            conn.commit()


# ---------------------------------------------------------------------------
# refresh_fines  (the "nightly batch")
# ---------------------------------------------------------------------------

def refresh_fines(today=None, conn=None, card_id=None):
    """Create or update FINES rows for every late loan. Safe to run as often as you like.

    card_id limits the refresh to one borrower. Returns counts:
    {"inserted", "updated", "unchanged", "skipped_paid"}.
    """
    today_iso = _today_iso(today)
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "skipped_paid": 0}

    with _connection(conn) as db:
        with _write_transaction(db):
            _refresh(db, today_iso, card_id, counts)
    return counts


def _refresh(db, today_iso, card_id, counts):
    # A loan is late when due_date is before the day it was returned, or before today if
    # it is still out. The late check runs in SQL so only late loans are fetched.
    sql = """
        SELECT l.loan_id, l.due_date, COALESCE(l.date_in, ?) AS end_day,
               f.loan_id IS NOT NULL AS has_row, f.fine_amt, f.paid
        FROM BOOK_LOANS l
        LEFT JOIN FINES f ON f.loan_id = l.loan_id
        WHERE l.due_date IS NOT NULL
          AND date(l.due_date) < date(COALESCE(l.date_in, ?))
    """
    params = [today_iso, today_iso]
    if card_id is not None:
        sql += " AND l.card_id = ?"
        params.append(card_id)

    for loan_id, due, end_day, has_row, old_amt, paid in db.execute(sql, params).fetchall():
        days_late = (_parse_date(end_day) - _parse_date(due)).days
        new_amt = days_late * FINE_RATE_CENTS_PER_DAY

        if not has_row:
            db.execute(
                "INSERT INTO FINES (loan_id, fine_amt, paid) VALUES (?, ?, 0)",
                (loan_id, new_amt),
            )
            counts["inserted"] += 1
        elif paid:
            counts["skipped_paid"] += 1  # already paid: never touch it
        elif old_amt != new_amt:
            db.execute("UPDATE FINES SET fine_amt = ? WHERE loan_id = ?", (new_amt, loan_id))
            counts["updated"] += 1
        else:
            counts["unchanged"] += 1


# ---------------------------------------------------------------------------
# has_unpaid_fines  (called by checkout)
# ---------------------------------------------------------------------------

def has_unpaid_fines(card_id, today=None, conn=None):
    """True if the borrower owes any unpaid fine.

    Read-only on purpose, so it is safe to call while checkout holds a write transaction.
    It also counts a late loan that refresh_fines() has not recorded yet, so a borrower
    cannot dodge the check by returning a late book just before the nightly refresh.
    """
    today_iso = _today_iso(today)
    sql = """
        SELECT 1
        FROM BOOK_LOANS l
        LEFT JOIN FINES f ON f.loan_id = l.loan_id
        WHERE l.card_id = ?
          AND COALESCE(f.paid, 0) = 0
          AND (f.loan_id IS NOT NULL
               OR date(l.due_date) < date(COALESCE(l.date_in, ?)))
        LIMIT 1
    """
    with _connection(conn) as db:
        return db.execute(sql, (card_id, today_iso)).fetchone() is not None


# ---------------------------------------------------------------------------
# pay_fines
# ---------------------------------------------------------------------------

def pay_fines(card_id, today=None, conn=None):
    """Pay ALL of a borrower's unpaid fines at once. Returns PaymentResult(success, message, amount).

    Refused (nothing is marked paid) when:
      * the card does not exist,
      * the borrower has nothing to pay,
      * any unpaid fine belongs to a book that has not been returned yet.
    """
    card_id = (card_id or "").strip()
    if not card_id:
        return PaymentResult(False, "Please enter a card ID.", Decimal("0.00"))
    today_iso = _today_iso(today)

    with _connection(conn) as db:
        with _write_transaction(db):
            if db.execute("SELECT 1 FROM BORROWER WHERE card_id = ?", (card_id,)).fetchone() is None:
                return PaymentResult(False, f"No borrower with card ID {card_id}.", Decimal("0.00"))

            # Bring the amounts up to date first so the borrower pays the current figure.
            _refresh(db, today_iso, card_id, {"inserted": 0, "updated": 0, "unchanged": 0, "skipped_paid": 0})

            total, count = db.execute(
                """SELECT COALESCE(SUM(f.fine_amt), 0), COUNT(*)
                   FROM FINES f JOIN BOOK_LOANS l ON l.loan_id = f.loan_id
                   WHERE l.card_id = ? AND f.paid = 0""",
                (card_id,),
            ).fetchone()
            if count == 0:
                return PaymentResult(False, f"Card {card_id} has no unpaid fines.", Decimal("0.00"))

            unreturned = db.execute(
                """SELECT COUNT(*)
                   FROM FINES f JOIN BOOK_LOANS l ON l.loan_id = f.loan_id
                   WHERE l.card_id = ? AND f.paid = 0 AND l.date_in IS NULL""",
                (card_id,),
            ).fetchone()[0]
            if unreturned:
                return PaymentResult(
                    False,
                    f"Cannot accept payment: {unreturned} fined book(s) have not been returned yet. "
                    "Check them in first.",
                    cents_to_decimal(total),
                )

            if REFUSE_PAYMENT_IF_ANY_BOOK_OUT:
                still_out = db.execute(
                    "SELECT COUNT(*) FROM BOOK_LOANS WHERE card_id = ? AND date_in IS NULL", (card_id,)
                ).fetchone()[0]
                if still_out:
                    return PaymentResult(
                        False,
                        f"Cannot accept payment: borrower still has {still_out} book(s) checked out.",
                        cents_to_decimal(total),
                    )

            db.execute(
                """UPDATE FINES SET paid = 1
                   WHERE paid = 0
                     AND loan_id IN (SELECT loan_id FROM BOOK_LOANS WHERE card_id = ?)""",
                (card_id,),
            )
            amount = cents_to_decimal(total)
            return PaymentResult(
                True, f"Payment of {format_dollars(amount)} accepted for card {card_id}.", amount
            )


# ---------------------------------------------------------------------------
# list_fines
# ---------------------------------------------------------------------------

def list_fines(include_paid=False, conn=None):
    """Fines grouped by borrower, with the amounts summed.

    One dict per (borrower, paid status):
      card_id, name, paid (bool), total (Decimal), fine_count, unreturned_count, payable (bool)
    payable means the borrower could pay this row right now (unpaid and every fined book returned).
    By default paid fines are hidden; pass include_paid=True to show them too.
    Run refresh_fines() first if you want amounts for still-out books to be current.
    """
    sql = """
        SELECT l.card_id, b.bname, f.paid,
               SUM(f.fine_amt), COUNT(*),
               SUM(CASE WHEN l.date_in IS NULL THEN 1 ELSE 0 END)
        FROM FINES f
        JOIN BOOK_LOANS l ON l.loan_id = f.loan_id
        LEFT JOIN BORROWER b ON b.card_id = l.card_id
    """
    if not include_paid:
        sql += " WHERE f.paid = 0"
    sql += " GROUP BY l.card_id, f.paid ORDER BY l.card_id, f.paid"

    with _connection(conn) as db:
        rows = db.execute(sql).fetchall()

    return [
        {
            "card_id": card_id,
            "name": name,
            "paid": bool(paid),
            "total": cents_to_decimal(total),
            "fine_count": count,
            "unreturned_count": unreturned,
            "payable": (not paid) and unreturned == 0,
        }
        for card_id, name, paid, total, count, unreturned in rows
    ]


# ---------------------------------------------------------------------------
# Tiny command-line front end for manual testing
# ---------------------------------------------------------------------------

def _main(argv):
    usage = "usage: python fines.py refresh | list | list-all | pay CARD_ID | check CARD_ID"
    if not argv:
        print(usage)
        return 1
    cmd = argv[0]
    if cmd == "refresh":
        print(refresh_fines())
    elif cmd in ("list", "list-all"):
        for r in list_fines(include_paid=(cmd == "list-all")):
            status = "PAID  " if r["paid"] else "UNPAID"
            print(f'{r["card_id"]:<10} {str(r["name"]):<24} {status} {format_dollars(r["total"]):>9}'
                  f'  ({r["fine_count"]} fine(s))')
    elif cmd == "pay" and len(argv) == 2:
        print(pay_fines(argv[1]).message)
    elif cmd == "check" and len(argv) == 2:
        print("owes fines" if has_unpaid_fines(argv[1]) else "no unpaid fines")
    else:
        print(usage)
        return 1
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_main(sys.argv[1:]))
