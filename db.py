"""
db.py - database foundation shared by every module in the project.

Connections are opened in autocommit mode (isolation_level=None). A single
INSERT/UPDATE outside `transaction()` is committed immediately. Anything that
must read-then-write atomically such as checkout, checkin, pay_fines, refresh_fines
must go inside `with transaction(conn):`, which takes SQLite's write lock up
front so two callers cannot interleave.

Money: FINES.fine_amt is INTEGER CENTS. Convert with dollars_to_cents() and
cents_to_dollars(), which use decimal.Decimal, never float.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Iterator, Union

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = PROJECT_DIR / "schema.sql"

# Override with the LIBRARY_DB environment variable (tests use a temp file).
DEFAULT_DB_PATH = PROJECT_DIR / "library.db"

LOAN_PERIOD_DAYS = 14          # due_date = date_out + 14 days
MAX_ACTIVE_LOANS = 3           # per borrower
FINE_CENTS_PER_DAY = 25        # $0.25 per day late
CARD_ID_PREFIX = "ID"          # card ids look like ID000001
CARD_ID_DIGITS = 6

DATE_FMT = "%Y-%m-%d"


class LibraryError(Exception):
    """Raised for any rule violation the librarian should see, e.g.
    raise LibraryError("Borrower ID000001 already has 3 books checked out.")
    The GUI (Milestone 3) catches this and shows str(e) to the user."""


def db_path() -> Path:
    """Path of the database file currently in use."""
    return Path(os.environ.get("LIBRARY_DB", DEFAULT_DB_PATH))


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------
def get_connection(path: Union[str, Path, None] = None) -> sqlite3.Connection:
    """Open a connection with the project's standard settings.

    - foreign keys enforced
    - rows returned as sqlite3.Row (access by column name or index)
    - autocommit mode; use transaction() for multi-statement work
    - waits up to 10 s for a lock instead of failing immediately
    """
    conn = sqlite3.connect(
        str(path or db_path()),
        isolation_level=None,   # we manage transactions explicitly
        timeout=10.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block atomically.

    BEGIN IMMEDIATE takes the write lock at the start, so a second connection
    trying to do the same thing waits until the first commits, then sees the
    updated data. Any exception rolls everything back and is re-raised.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def init_db(path: Union[str, Path, None] = None, reset: bool = False) -> Path:
    """Create the database file and all tables from schema.sql.

    Safe to call repeatedly (every statement is IF NOT EXISTS).
    reset=True deletes the existing database file first.
    Returns the path of the database file.
    """
    target = Path(path or db_path())
    if reset and target.exists():
        target.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)

    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    conn = get_connection(target)
    try:
        conn.executescript(schema)
    finally:
        conn.close()
    return target


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------
def today() -> date:
    """Today's date. Set LIBRARY_TODAY=YYYY-MM-DD to fake it in tests/demos."""
    fake = os.environ.get("LIBRARY_TODAY")
    return parse_date(fake) if fake else date.today()


def parse_date(value: Union[str, date, datetime]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(value.strip()[:10], DATE_FMT).date()


def fmt_date(d: Union[str, date, datetime]) -> str:
    return parse_date(d).strftime(DATE_FMT)


def due_date_for(date_out: Union[str, date]) -> str:
    """Due date string: exactly LOAN_PERIOD_DAYS calendar days after date_out."""
    return fmt_date(parse_date(date_out) + timedelta(days=LOAN_PERIOD_DAYS))


def days_late(due: Union[str, date], returned_or_today: Union[str, date]) -> int:
    """Whole days past due (0 if not late)."""
    return max(0, (parse_date(returned_or_today) - parse_date(due)).days)


# ---------------------------------------------------------------------------
# Money (fixed-decimal via integer cents)
# ---------------------------------------------------------------------------
_CENT = Decimal("0.01")


def dollars_to_cents(amount: Union[Decimal, str, int]) -> int:
    """Decimal('1.25') / '1.25' / 1 -> 125. Floats are rejected on purpose."""
    if isinstance(amount, float):
        raise TypeError("Use Decimal or str for money, not float")
    d = Decimal(str(amount)).quantize(_CENT, rounding=ROUND_HALF_UP)
    return int(d * 100)


def cents_to_dollars(cents: int) -> Decimal:
    """125 -> Decimal('1.25')."""
    return (Decimal(int(cents)) / 100).quantize(_CENT)


def format_money(cents: int) -> str:
    """125 -> '$1.25'."""
    return f"${cents_to_dollars(cents)}"


def fine_cents_for(due: Union[str, date], returned_or_today: Union[str, date]) -> int:
    """Fine in cents for a loan: days late * 25 cents."""
    return days_late(due, returned_or_today) * FINE_CENTS_PER_DAY


# ---------------------------------------------------------------------------
# Card ids
# ---------------------------------------------------------------------------
def format_card_id(n: int) -> str:
    """7 -> 'ID000007'. Matches the format of the existing borrower ids."""
    return f"{CARD_ID_PREFIX}{n:0{CARD_ID_DIGITS}d}"


if __name__ == "__main__":
    p = init_db()
    print(f"Initialized database at {p}")
