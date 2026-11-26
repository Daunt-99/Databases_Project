"""
loader.py - import the Milestone 1 CSV files into SQLite.

Usage:
    python loader.py <csv_folder> [--db library.db] [--reset]

Expected files in <csv_folder> (names are matched case-insensitively,
singular or plural is fine):

    book.csv          isbn, title
    authors.csv       author_id, name
    book_authors.csv  author_id, isbn
    borrower.csv      card_id, ssn, bname, address, phone
    book_loans.csv    (optional) loan_id, isbn, card_id, date_out, due_date, date_in
    fines.csv         (optional) loan_id, fine_amt (dollars), paid
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import re
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from db import (
    dollars_to_cents,
    format_card_id,
    get_connection,
    init_db,
    transaction,
)

# ---------------------------------------------------------------------------
# Header aliases (after normalising: lowercase, letters/digits only)
# ---------------------------------------------------------------------------
ALIASES: Dict[str, List[str]] = {
    "isbn":      ["isbn", "isbn10", "bookisbn", "isbn13"],
    "title":     ["title", "booktitle", "name"],
    "author_id": ["authorid", "authorsid", "id"],
    "name":      ["name", "author", "authorname", "fullname"],
    "card_id":   ["cardid", "cardno", "cardnumber", "id0000id", "borrowerid", "id"],
    "ssn":       ["ssn"],
    "bname":     ["bname", "name", "borrowername", "fullname"],
    "first":     ["firstname", "fname", "first"],
    "last":      ["lastname", "lname", "last"],
    "address":   ["address", "street", "streetaddress"],
    "city":      ["city"],
    "state":     ["state"],
    "phone":     ["phone", "phonenumber", "telephone"],
    "loan_id":   ["loanid", "id"],
    "date_out":  ["dateout"],
    "due_date":  ["duedate"],
    "date_in":   ["datein"],
    "fine_amt":  ["fineamt", "fineamount", "fine", "amount"],
    "paid":      ["paid", "ispaid"],
    "authors":   ["author", "authors"],   # raw books.csv only
}

FILE_STEMS = {
    "book":         ["book", "books"],
    "authors":      ["authors", "author"],
    "book_authors": ["bookauthors", "bookauthor"],
    "borrower":     ["borrower", "borrowers"],
    "book_loans":   ["bookloans", "bookloan", "loans"],
    "fines":        ["fines", "fine"],
}


# Column order used when a file has NO header row (the Milestone 1 deliverables
# follow the table order in the assignment PDF). book_authors rows are checked
# per row, so either "isbn,author_id" or "author_id,isbn" order works.
POSITIONAL = {
    "book":         ["isbn", "title"],
    "authors":      ["authorid", "name"],
    "book_authors": ["isbn", "authorid"],
    "borrower":     ["cardid", "ssn", "bname", "address", "phone"],
    "book_loans":   ["loanid", "isbn", "cardid", "dateout", "duedate", "datein"],
    "fines":        ["loanid", "fineamt", "paid"],
}
KNOWN_HEADERS = {a for names in ALIASES.values() for a in names} | {
    "isbn13", "email", "cover", "publisher", "pages"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


# ---------------------------------------------------------------------------
# Value cleaning
# ---------------------------------------------------------------------------
def clean_text(v: Optional[str]) -> str:
    return html.unescape((v or "").strip())


def clean_isbn(v: Optional[str]) -> str:
    """Strip hyphens/spaces; restore leading zeros lost by Excel (9 -> 10 digits)."""
    s = re.sub(r"[\s-]", "", (v or "")).upper()
    if s.isdigit() and len(s) < 10:
        s = s.zfill(10)
    return s


def clean_card_id(v: Optional[str]) -> str:
    """'ID000001' stays; '1' or '000001' becomes 'ID000001'."""
    s = (v or "").strip().upper()
    if s.isdigit():
        return format_card_id(int(s))
    return s


def clean_date(v: Optional[str]) -> Optional[str]:
    s = (v or "").strip()
    if not s or s.upper() in ("NULL", "NONE"):
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:10], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"unrecognised date {s!r}")


def clean_bool(v: Optional[str]) -> int:
    return 1 if (v or "").strip().lower() in ("1", "true", "t", "yes", "y") else 0


# ---------------------------------------------------------------------------
# CSV reading
# ---------------------------------------------------------------------------
def read_csv(path: Path, kind: Optional[str] = None) -> List[Dict[str, str]]:
    """Read a CSV/TSV file into a list of dicts keyed by normalised header.

    If the first row is data rather than a header (its first cell is not a
    known column name), the columns are named from POSITIONAL[kind].
    """
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    first_line = text.splitlines()[0] if text else ""
    delim = "\t" if first_line.count("\t") > first_line.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    try:
        first = next(reader)
    except StopIteration:
        return []
    rows = []
    if first and _norm(first[0]) in KNOWN_HEADERS:
        header = [_norm(h) for h in first]
        pending = []
    elif kind in POSITIONAL:
        header = POSITIONAL[kind]
        pending = [first]
    else:
        raise ValueError(f"{path.name}: no header row and unknown file type")
    for rec in [*pending, *reader]:
        if not any(c.strip() for c in rec):
            continue
        rows.append({header[i]: rec[i] if i < len(rec) else "" for i in range(len(header))})
    return rows


def pick(row: Dict[str, str], field: str, exclude: Iterable[str] = ()) -> Optional[str]:
    """Return the value of the first header in row that is an alias of field."""
    for alias in ALIASES[field]:
        if alias in row and alias not in exclude:
            return row[alias]
    return None


def find_file(folder: Path, kind: str) -> Optional[Path]:
    wanted = FILE_STEMS[kind]
    for p in sorted(folder.iterdir()):
        if p.suffix.lower() in (".csv", ".tsv", ".txt") and _norm(p.stem) in wanted:
            return p
    return None


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------
class LoadReport:
    def __init__(self) -> None:
        self.counts: Dict[str, int] = {}
        self.skipped: Dict[str, int] = {}
        self.warnings: List[str] = []

    def add(self, table: str, inserted: bool) -> None:
        bucket = self.counts if inserted else self.skipped
        bucket[table] = bucket.get(table, 0) + 1

    def warn(self, msg: str) -> None:
        if len(self.warnings) < 50:
            self.warnings.append(msg)

    def __str__(self) -> str:
        lines = ["Loaded:"]
        for t in ("BOOK", "AUTHORS", "BOOK_AUTHORS", "BORROWER", "BOOK_LOANS", "FINES"):
            if t in self.counts or t in self.skipped:
                lines.append(f"  {t:<13} {self.counts.get(t, 0):>7} inserted"
                             f"   {self.skipped.get(t, 0):>5} skipped")
        if self.warnings:
            lines.append(f"Warnings (first {len(self.warnings)}):")
            lines += [f"  - {w}" for w in self.warnings]
        return "\n".join(lines)


def _insert(conn, report: LoadReport, table: str, sql: str, params) -> bool:
    cur = conn.execute(sql, params)
    ok = cur.rowcount == 1
    report.add(table, ok)
    return ok


def load_books(conn, rows, report: LoadReport) -> None:
    raw_author_col = rows and pick(rows[0], "authors") is not None and "title" in rows[0]
    author_ids: Dict[str, int] = {}
    for r in rows:
        isbn = clean_isbn(pick(r, "isbn"))
        title = clean_text(pick(r, "title"))
        if not isbn or not title:
            report.warn(f"book row missing isbn or title: {r}")
            report.add("BOOK", False)
            continue
        _insert(conn, report, "BOOK",
                "INSERT OR IGNORE INTO BOOK(isbn, title) VALUES (?, ?)", (isbn, title))

        if raw_author_col:  # original books.csv: "Author A,Author B"
            for name in (clean_text(a) for a in (pick(r, "authors") or "").split(",")):
                if not name:
                    continue
                if name not in author_ids:
                    conn.execute("INSERT OR IGNORE INTO AUTHORS(name) VALUES (?)", (name,))
                    aid = conn.execute("SELECT author_id FROM AUTHORS WHERE name = ?",
                                       (name,)).fetchone()[0]
                    author_ids[name] = aid
                    report.add("AUTHORS", True)
                _insert(conn, report, "BOOK_AUTHORS",
                        "INSERT OR IGNORE INTO BOOK_AUTHORS(author_id, isbn) VALUES (?, ?)",
                        (author_ids[name], isbn))


def load_authors(conn, rows, report: LoadReport) -> None:
    for r in rows:
        aid = (pick(r, "author_id") or "").strip()
        name = clean_text(pick(r, "name"))
        if not aid.isdigit() or not name:
            report.warn(f"author row needs numeric author_id and name: {r}")
            report.add("AUTHORS", False)
            continue
        _insert(conn, report, "AUTHORS",
                "INSERT OR IGNORE INTO AUTHORS(author_id, name) VALUES (?, ?)",
                (int(aid), name))


def load_book_authors(conn, rows, report: LoadReport) -> None:
    books = {r[0] for r in conn.execute("SELECT isbn FROM BOOK")}
    authors = {r[0] for r in conn.execute("SELECT author_id FROM AUTHORS")}
    for r in rows:
        aid = (pick(r, "author_id") or "").strip()
        isbn = clean_isbn(pick(r, "isbn"))
        # Headerless files may be "author_id,isbn" instead of "isbn,author_id".
        if not (aid.isdigit() and int(aid) in authors) and isbn.isdigit() \
                and int(isbn) in authors and clean_isbn(aid) in books:
            aid, isbn = isbn, clean_isbn(aid)
        if not aid.isdigit() or int(aid) not in authors or isbn not in books:
            report.warn(f"book_authors row refers to unknown author/book: {r}")
            report.add("BOOK_AUTHORS", False)
            continue
        _insert(conn, report, "BOOK_AUTHORS",
                "INSERT OR IGNORE INTO BOOK_AUTHORS(author_id, isbn) VALUES (?, ?)",
                (int(aid), isbn))


def load_borrowers(conn, rows, report: LoadReport) -> None:
    for r in rows:
        card = clean_card_id(pick(r, "card_id"))
        ssn = clean_text(pick(r, "ssn"))
        name = clean_text(pick(r, "bname"))
        if not name:
            name = " ".join(x for x in (clean_text(pick(r, "first")),
                                        clean_text(pick(r, "last"))) if x)
        street = clean_text(pick(r, "address"))
        city, state = clean_text(pick(r, "city")), clean_text(pick(r, "state"))
        address = ", ".join(x for x in (street, city, state) if x) if (city or state) else street
        phone = clean_text(pick(r, "phone")) or None

        if not (card and ssn and name and address):
            report.warn(f"borrower row missing card_id/ssn/name/address: {r}")
            report.add("BORROWER", False)
            continue
        if not _insert(conn, report, "BORROWER",
                       "INSERT OR IGNORE INTO BORROWER(card_id, ssn, bname, address, phone) "
                       "VALUES (?, ?, ?, ?, ?)", (card, ssn, name, address, phone)):
            report.warn(f"duplicate card_id or ssn skipped: {card} / {ssn}")


def load_loans(conn, rows, report: LoadReport) -> None:
    for r in rows:
        try:
            params = (
                int(pick(r, "loan_id")),
                clean_isbn(pick(r, "isbn")),
                clean_card_id(pick(r, "card_id")),
                clean_date(pick(r, "date_out")),
                clean_date(pick(r, "due_date")),
                clean_date(pick(r, "date_in")),
            )
            _insert(conn, report, "BOOK_LOANS",
                    "INSERT OR IGNORE INTO BOOK_LOANS"
                    "(loan_id, isbn, card_id, date_out, due_date, date_in) "
                    "VALUES (?, ?, ?, ?, ?, ?)", params)
        except Exception as e:  # bad row: report and continue
            report.warn(f"loan row rejected ({e}): {r}")
            report.add("BOOK_LOANS", False)


def load_fines(conn, rows, report: LoadReport) -> None:
    for r in rows:
        try:
            amt = (pick(r, "fine_amt") or "0").replace("$", "").strip()
            params = (int(pick(r, "loan_id")), dollars_to_cents(Decimal(amt)),
                      clean_bool(pick(r, "paid")))
            _insert(conn, report, "FINES",
                    "INSERT OR IGNORE INTO FINES(loan_id, fine_amt, paid) VALUES (?, ?, ?)",
                    params)
        except (InvalidOperation, TypeError, ValueError, Exception) as e:
            report.warn(f"fine row rejected ({e}): {r}")
            report.add("FINES", False)


def load_all(csv_dir, db=None, reset: bool = False) -> LoadReport:
    """Create the schema (if needed) and import every CSV found in csv_dir."""
    folder = Path(csv_dir)
    if not folder.is_dir():
        raise FileNotFoundError(f"CSV folder not found: {folder}")

    path = init_db(db, reset=reset)
    conn = get_connection(path)
    report = LoadReport()
    files = {k: find_file(folder, k) for k in FILE_STEMS}
    if not files["book"] and not files["borrower"]:
        raise FileNotFoundError(f"No book or borrower CSV found in {folder}")

    try:
        with transaction(conn):   # all-or-nothing import, and much faster
            if files["book"]:
                load_books(conn, read_csv(files["book"], "book"), report)
            if files["authors"]:
                load_authors(conn, read_csv(files["authors"], "authors"), report)
            if files["book_authors"]:
                load_book_authors(conn, read_csv(files["book_authors"], "book_authors"), report)
            if files["borrower"]:
                load_borrowers(conn, read_csv(files["borrower"], "borrower"), report)
            if files["book_loans"]:
                load_loans(conn, read_csv(files["book_loans"], "book_loans"), report)
            if files["fines"]:
                load_fines(conn, read_csv(files["fines"], "fines"), report)
    finally:
        conn.close()
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Load Milestone 1 CSVs into SQLite.")
    ap.add_argument("csv_dir", help="folder containing the CSV files")
    ap.add_argument("--db", help="database file (default: library.db or $LIBRARY_DB)")
    ap.add_argument("--reset", action="store_true", help="delete the database first")
    args = ap.parse_args(argv)
    try:
        report = load_all(args.csv_dir, args.db, args.reset)
    except Exception as e:
        print(f"Load failed: {e}", file=sys.stderr)
        return 1
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
