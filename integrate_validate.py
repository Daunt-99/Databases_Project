"""Person 5: shared helpers, validation, pipeline integration, and packaging.

Run `python integrate_validate.py` from the project root to execute the complete pipeline.
"""
"""Shared cleaning, case, ISBN, and CSV helpers for Milestone 1.

The helpers are intentionally dependency-free.  ISBNs are always handled as
strings so leading zeroes and the ISBN-10 check character ``X`` are retained.
"""
import csv
import re
import unicodedata

# ---------------------------------------------------------------------------
# Unicode cleaning
# ---------------------------------------------------------------------------

_REPLACEMENTS = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2033": '"',
    "\u2013": "-", "\u2014": "-", "\u2212": "-", "\u2010": "-", "\u2011": "-",
    "\u2026": "...",
    "\u00a0": " ", "\u2002": " ", "\u2003": " ", "\u2009": " ", "\u202f": " ",
    "\ufeff": "", "\u200b": "", "\u200c": "", "\u200d": "", "\u00ad": "",
    "\ufffd": "",
}
_REPL_RE = re.compile("|".join(map(re.escape, _REPLACEMENTS)))
_MOJIBAKE_RE = re.compile(
    "[\u00c2-\u00f4][\u0080-\u00bf\u0152\u0153\u0160\u0161\u0178\u017d\u017e\u0192\u02c6\u02dc"
    "\u2013\u2014\u2018-\u201e\u2020-\u2022\u2026\u2030\u2039\u203a\u20ac\u2122]{1,3}"
)
_WS_RE = re.compile(r"\s+")
# These fragments represent characters already destroyed in the supplied
# source data.  repair_known() restores the small set of names identified.
_LOST_CHAR_RE = re.compile(r"\u00ef\u00bf(?:1/2|\u00bd)?|\u00c3\.\.")


def _fix_mojibake(text):
    """Repair common UTF-8 text that was decoded as cp1252/latin-1."""
    def repair(match):
        chunk = match.group(0)
        for encoding in ("cp1252", "latin-1"):
            try:
                return chunk.encode(encoding).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
        return chunk

    return _MOJIBAKE_RE.sub(repair, text)


def clean_unicode(value, ascii_fold=False):
    """Normalize text, repair common mojibake, remove invisible junk, trim it.

    Accented characters are retained by default.  Set ``ascii_fold=True`` only
    when a downstream system explicitly requires ASCII-only text.
    """
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    if text.lower() in ("nan", "none", "null"):
        return ""

    text = _fix_mojibake(text)
    text = _LOST_CHAR_RE.sub("", text)
    text = unicodedata.normalize("NFC", text)
    text = _REPL_RE.sub(lambda match: _REPLACEMENTS[match.group(0)], text)
    text = "".join(
        char for char in text
        if char in "\t\n " or unicodedata.category(char)[0] != "C"
    )

    if ascii_fold:
        text = unicodedata.normalize("NFKD", text)
        text = "".join(char for char in text if not unicodedata.combining(char))
        text = text.encode("ascii", "ignore").decode("ascii")
    return _WS_RE.sub(" ", text).strip()


_KNOWN_REPAIRS = [
    (re.compile(r"\bBront(?=\W|$)"), "Bront\u00eb"),
    (re.compile(r"^mile Zola\b"), "\u00c9mile Zola"),
    (re.compile(r"\bDobre\b"), "Dobr\u00e9e"),
    (re.compile(r"\bDumas Pre\b"), "Dumas P\u00e8re"),
    (re.compile(r"\bSidonieGabrielle\b"), "Sidonie-Gabrielle"),
]


def repair_known(text):
    """Restore known author names whose characters were lost in the source."""
    result = text
    for pattern, replacement in _KNOWN_REPAIRS:
        result = pattern.sub(replacement, result)
    return result


# ---------------------------------------------------------------------------
# Case conventions
# ---------------------------------------------------------------------------

_SMALL_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "en", "for", "from", "if", "in",
    "into", "nor", "of", "on", "or", "per", "the", "to", "vs", "via", "with",
}
_ACRONYMS = {
    "USA", "US", "UK", "UN", "FBI", "CIA", "NASA", "TV", "DVD", "CD", "PC",
    "SQL", "HTML", "IBM", "DNA", "AIDS", "UFO", "ESL", "GRE", "SAT", "MBA",
    "NFL", "NBA", "NHL", "MLB", "WWII", "WWI", "ABC", "BBC", "CNN", "NYC",
    "DC", "HIV", "ER", "FAQ", "CPA", "ADHD", "IQ", "ID", "OK",
}
_ROMAN_RE = re.compile(r"^(?=[IVXLCDM]+$)M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")
_NAME_PARTICLES = {
    "van", "von", "der", "den", "de", "del", "della", "di", "da", "du", "la",
    "le", "bin", "ibn", "al", "el", "ten", "ter",
}
_NAME_SUFFIXES = {
    "jr": "Jr.", "sr": "Sr.", "ii": "II", "iii": "III", "iv": "IV",
    "ph.d": "Ph.D.", "m.d": "M.D.", "d.d": "D.D.", "ed.d": "Ed.D.",
    "esq": "Esq.",
}


def _cap_first_alpha(token):
    """Capitalize the first letter and lowercase the remainder."""
    for index, char in enumerate(token):
        if char.isalpha():
            return token[:index] + char.upper() + token[index + 1:].lower()
    return token


def _title_token(token, force_cap):
    core = token.strip("\"'()[]{}.,;:!?")
    if not core:
        return token
    if core.upper() in _ACRONYMS and core.isupper():
        return token
    if core.lower() in ("ii", "iii", "iv"):
        return token.upper()
    if _ROMAN_RE.match(core) and core.isupper() and len(core) > 1:
        return token
    if "-" in token:
        parts = token.split("-")
        return "-".join(
            _title_token(part, force_cap if index == 0 else True)
            for index, part in enumerate(parts)
        )
    if not force_cap and core.lower() in _SMALL_WORDS:
        return token.lower()
    return _cap_first_alpha(token)


def title_case(text):
    """Apply the shared book-title convention (small words stay lowercase).

    Small words are lowercase unless first/last or immediately after strong
    punctuation.  Common acronyms and Roman numerals retain their capitals.
    """
    text = clean_unicode(text)
    if not text:
        return ""
    tokens = text.split(" ")
    result = []
    last_index = len(tokens) - 1
    for index, token in enumerate(tokens):
        previous = tokens[index - 1] if index else ""
        force = (
            index == 0 or index == last_index
            or previous.endswith((":", "?", "!", ".")) or previous == "-"
        )
        result.append(_title_token(token, force))
    return " ".join(result)


def _name_part(part, first):
    lower = part.lower()
    if re.fullmatch(r"(?:[a-z]\.)+[a-z]?\.?", lower):  # E.J.W. / J.R.R.
        return part.upper()
    if re.fullmatch(r"[a-z]\.?", lower):
        return part.upper() if part.endswith(".") else part.upper() + "."

    bare = lower.strip(".,")
    if bare in _NAME_SUFFIXES:
        return _NAME_SUFFIXES[bare]
    if not first and bare in _NAME_PARTICLES:
        return lower
    if "-" in part:
        return "-".join(_name_part(piece, True) for piece in part.split("-"))
    if "'" in part and 1 <= part.index("'") <= 2 and len(part) > part.index("'") + 1:
        head, tail = part.split("'", 1)
        return head.capitalize() + "'" + _name_part(tail, True)
    if lower in _ACRONYMS:
        return lower.upper()
    if (lower.startswith("mc") and len(lower) > 2
            and (part[2:].islower() or part.isupper())):
        return "Mc" + part[2:].capitalize()

    # Preserve deliberate internal capitals (e.g. LaHaye, LeCarre, eBay).
    # Ordinary title case and all-uppercase/all-lowercase words are normalized.
    if any(char.isupper() for char in part[1:]) and any(char.islower() for char in part):
        return part
    return _cap_first_alpha(part)


def name_case(text):
    """Standardize person names while preserving intentional internal capitals."""
    text = clean_unicode(text)
    if not text:
        return ""
    return " ".join(_name_part(part, index == 0) for index, part in enumerate(text.split(" ")))


# ---------------------------------------------------------------------------
# ISBN helpers
# ---------------------------------------------------------------------------

def normalize_isbn(value):
    """Return an ISBN as text; never cast it to int/float or lose leading zeroes."""
    text = clean_unicode(value).replace("-", "").replace(" ", "").upper()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


def isbn10_valid(isbn):
    if not re.fullmatch(r"\d{9}[\dX]", isbn):
        return False
    total = sum((10 - index) * (10 if char == "X" else int(char))
                for index, char in enumerate(isbn))
    return total % 11 == 0


def isbn13_valid(isbn):
    if not re.fullmatch(r"\d{13}", isbn):
        return False
    total = sum(int(char) * (1 if index % 2 == 0 else 3)
                for index, char in enumerate(isbn[:12]))
    return (10 - total % 10) % 10 == int(isbn[12])


def isbn10_to_isbn13(isbn10):
    """Convert a syntactically valid ISBN-10 to its 978 ISBN-13 equivalent."""
    isbn10 = normalize_isbn(isbn10)
    if len(isbn10) == 9 and isbn10.isdigit():
        isbn10 = isbn10.zfill(10)
    if not re.fullmatch(r"\d{9}[\dX]", isbn10):
        return ""
    core = "978" + isbn10[:9]
    check = sum(int(char) * (1 if index % 2 == 0 else 3)
                for index, char in enumerate(core))
    return core + str((10 - check % 10) % 10)


# ---------------------------------------------------------------------------
# CSV I/O
# ---------------------------------------------------------------------------

def sniff_delimiter(path, default=","):
    """Detect comma or tab from the first line of a CSV/TSV file."""
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        first_line = handle.readline()
    tabs, commas = first_line.count("\t"), first_line.count(",")
    if tabs > commas:
        return "\t"
    return "," if commas else default


def read_table(path, delimiter=None):
    """Read a headered CSV/TSV as dictionaries of strings (never numbers)."""
    delimiter = delimiter or sniff_delimiter(path)
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def read_rows(path):
    """Read a headerless project deliverable into lists of strings."""
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return [row for row in csv.reader(handle) if row]


def write_table(path, rows, header=None):
    """Write UTF-8 CSV with LF endings; None values become empty fields."""
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        if header:
            writer.writerow(header)
        for row in rows:
            writer.writerow(["" if value is None else value for value in row])


# Compatibility module aliases: Person 5 remains a single Python file while
# existing stage scripts can continue importing their established interfaces.
import sys as _sys
import types as _types
_sys.modules.setdefault("common", _sys.modules[__name__])

"""Validate the four Milestone 1 CSV deliverables.

Usage:
    python integrate_validate.py --validate-only

The validation-only option checks the existing output/ files without
running the upstream stages or creating the submission ZIP.

Exit status is 0 when there are no errors. Warnings identify source-data issues
that do not necessarily indicate a pipeline failure.
"""
import argparse
import csv
import os
import re
import sys
from collections import Counter


MOJIBAKE = re.compile(
    r"[\u00c3\u00c2\u00e2][\u0080-\u00bf\u20ac\u2122]|\ufffd|\u00ef\u00bf|\u00c3\.\."
)
SERIES_SUFFIX = re.compile(r"\s*\([^)]*\)\s*$")
SSN_RE = re.compile(r"^\d{3}-\d{2}-\d{4}$")
PHONE_RE = re.compile(r"^\(\d{3}\) \d{3}-\d{4}$")
CARD_ID_RE = re.compile(r"^ID\d{6}$")
NULL_AUTHOR_IDS = {"", "NULL", r"\N"}


class Report:
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.passed = []

    def ok(self, message):
        self.passed.append(message)

    def err(self, message, examples=()):
        self.errors.append((message, list(examples)[:5]))

    def warn(self, message, examples=()):
        self.warnings.append((message, list(examples)[:5]))

    def check(self, condition, success_message, error_message, examples=()):
        if condition:
            self.ok(success_message)
        else:
            self.err(error_message, examples)
        return condition

    def show(self):
        for message in self.passed:
            print(f"  PASS  {message}")
        for message, examples in self.warnings:
            print(f"  WARN  {message}")
            for example in examples:
                print(f"          e.g. {example!r}")
        for message, examples in self.errors:
            print(f"  FAIL  {message}")
            for example in examples:
                print(f"          e.g. {example!r}")
        print(
            f"\n{len(self.passed)} passed, {len(self.warnings)} warnings, "
            f"{len(self.errors)} failed"
        )


def _load(path, report, name):
    if not os.path.isfile(path):
        report.err(f"{name} is missing: {path}")
        return None
    try:
        rows = read_rows(path)
    except (OSError, UnicodeError, csv.Error) as error:  # csv imported below for error type
        report.err(f"could not read {name}: {error}")
        return None
    report.ok(f"{name} found ({len(rows)} rows)")
    report.check(bool(rows), f"{name} is not empty", f"{name} is empty")
    return rows


def _source_isbn_expectations(path):
    """Mirror Person 1's ISBN-13 preference and ISBN-10 fallback logic.

    A syntactically/check-digit-valid source ISBN-13 is used as-is. If it is
    invalid or blank, a usable ISBN-10 is converted to its 978 ISBN-13 form.
    This lets validation distinguish a bad source ISBN-13 from a lost book.
    """
    rows = read_table(path)
    if not rows:
        return None

    columns = list(rows[0].keys())
    isbn13_column = next(
        (column for column in columns if column and column.strip().lower() in ("isbn13", "isbn-13")),
        None,
    )
    isbn10_column = next(
        (column for column in columns if column and column.strip().lower() in ("isbn10", "isbn-10", "isbn")),
        None,
    )
    if not isbn13_column and not isbn10_column:
        return None

    expected = set()
    leading_zero_expected = set()
    invalid_source_13 = []
    unconvertible = []
    original_isbns = []

    for row_number, row in enumerate(rows, start=2):
        raw13 = normalize_isbn(row.get(isbn13_column, "")) if isbn13_column else ""
        raw10 = normalize_isbn(row.get(isbn10_column, "")) if isbn10_column else ""
        original_isbns.append(raw13)

        if raw13 and isbn13_valid(raw13):
            resolved = raw13
        else:
            if raw13:
                invalid_source_13.append((row_number, raw13))
            resolved = isbn10_to_isbn13(raw10) if raw10 else ""
            if not resolved:
                if raw13 or raw10:
                    unconvertible.append((row_number, raw13, raw10))
                continue

        expected.add(resolved)
        if raw10.startswith("0"):
            leading_zero_expected.add(resolved)

    duplicate_source_13 = [isbn for isbn, count in Counter(x for x in original_isbns if x).items()
                           if count > 1]
    return {
        "expected": expected,
        "leading_zero_expected": leading_zero_expected,
        "invalid_source_13": invalid_source_13,
        "unconvertible": unconvertible,
        "duplicate_source_13": duplicate_source_13,
        "rows": len(rows),
    }


def validate(out_dir="output", src_books=None, src_borrowers=None):
    report = Report()
    books = _load(os.path.join(out_dir, "books.csv"), report, "books.csv")
    authors = _load(os.path.join(out_dir, "authors.csv"), report, "authors.csv")
    links = _load(os.path.join(out_dir, "book_authors.csv"), report, "book_authors.csv")
    borrowers = _load(os.path.join(out_dir, "borrowers.csv"), report, "borrowers.csv")

    # ---------------- books ----------------
    isbns = set()
    if books is not None:
        bad_columns = [row for row in books if len(row) != 2]
        report.check(
            not bad_columns,
            "books.csv has exactly 2 columns on every row",
            "books.csv rows should be (isbn13, title)",
            bad_columns,
        )
        isbn_list = [row[0] for row in books if row]
        isbns = set(isbn_list)
        duplicates = [isbn for isbn, count in Counter(isbn_list).items() if count > 1]
        report.check(not duplicates, "no duplicate ISBNs in books.csv",
                     f"{len(duplicates)} duplicate ISBNs in books.csv", duplicates)

        bad_length = [isbn for isbn in isbn_list if not re.fullmatch(r"\d{13}", isbn)]
        report.check(not bad_length, "every book ISBN is exactly 13 digits (leading zeroes retained)",
                     f"{len(bad_length)} book ISBNs are not 13 digits", bad_length)
        bad_check_digits = [isbn for isbn in isbn_list
                            if re.fullmatch(r"\d{13}", isbn) and not isbn13_valid(isbn)]
        if bad_check_digits:
            report.warn(f"{len(bad_check_digits)} output ISBN-13s fail the check-digit test",
                        bad_check_digits)
        else:
            report.ok("every output ISBN-13 passes its check digit")

        titles = [row[1] for row in books if len(row) == 2]
        blank_titles = [row[0] for row in books if len(row) == 2 and not row[1].strip()]
        report.check(not blank_titles, "no blank book titles",
                     f"{len(blank_titles)} blank book titles", blank_titles)
        bad_unicode = [title for title in titles if MOJIBAKE.search(title)]
        report.check(not bad_unicode, "no mojibake / replacement characters in titles",
                     f"{len(bad_unicode)} titles still contain Unicode junk", bad_unicode)
        dirty_titles = [title for title in titles if title != clean_unicode(title)]
        report.check(not dirty_titles, "titles have no stray whitespace or control characters",
                     f"{len(dirty_titles)} titles are not clean_unicode()-stable", dirty_titles)
        series = [title for title in titles if SERIES_SUFFIX.search(title)]
        if series:
            report.warn(f"{len(series)} titles still end in parenthetical text", series)
        else:
            report.ok("no trailing parenthetical text remains in titles")
        all_caps = [title for title in titles
                    if len(title) > 6 and title == title.upper() and title != title.lower()]
        report.check(not all_caps, "no ALL-CAPS titles",
                     f"{len(all_caps)} all-caps titles", all_caps)
        all_lower = [title for title in titles
                     if len(title) > 6 and title == title.lower() and title != title.upper()]
        report.check(not all_lower, "no all-lowercase titles",
                     f"{len(all_lower)} all-lowercase titles", all_lower)

        if src_books and os.path.isfile(src_books):
            source = _source_isbn_expectations(src_books)
            if source is None:
                report.warn("could not identify ISBN-10 / ISBN-13 columns in the source books file")
            else:
                missing = sorted(source["expected"] - isbns)
                extra = sorted(isbns - source["expected"])
                report.check(not missing, "no usable source ISBN was dropped",
                             f"{len(missing)} expected source ISBNs are missing from books.csv", missing)
                report.check(not extra, "books.csv contains no ISBN not derivable from the source",
                             f"{len(extra)} output ISBNs are not derivable from the source", extra)
                retained_zero_ids = source["leading_zero_expected"] & isbns
                missing_zero_ids = sorted(source["leading_zero_expected"] - isbns)
                report.check(
                    not missing_zero_ids,
                    f"all {len(retained_zero_ids)} source ISBN-10 values beginning with 0 retain their mapped ISBN-13",
                    f"{len(missing_zero_ids)} books with a leading-zero ISBN-10 are missing",
                    missing_zero_ids,
                )
                if source["invalid_source_13"]:
                    report.warn(
                        f"{len(source['invalid_source_13'])} source ISBN-13 value(s) failed validation; "
                        "Person 1's ISBN-10 fallback was used where possible",
                        source["invalid_source_13"],
                    )
                if source["unconvertible"]:
                    report.warn(
                        f"{len(source['unconvertible'])} source row(s) had no usable ISBN-13 or ISBN-10",
                        source["unconvertible"],
                    )
                if source["duplicate_source_13"]:
                    report.warn(
                        f"source contains {len(source['duplicate_source_13'])} duplicated ISBN-13 value(s); "
                        "output should contain one row per unique ISBN",
                        source["duplicate_source_13"],
                    )

    # ---------------- authors ----------------
    author_ids = set()
    if authors is not None:
        bad_columns = [row for row in authors if len(row) != 2]
        report.check(not bad_columns, "authors.csv has exactly 2 columns on every row",
                     "authors.csv rows should be (author_id, name)", bad_columns)
        ids = [row[0] for row in authors if row]
        author_ids = set(ids)
        non_integer = [author_id for author_id in ids if not author_id.isdigit()]
        report.check(not non_integer, "author_ids are integers",
                     f"{len(non_integer)} non-integer author_ids", non_integer)
        duplicate_ids = [author_id for author_id, count in Counter(ids).items() if count > 1]
        report.check(not duplicate_ids, "author_ids are unique",
                     f"{len(duplicate_ids)} duplicate author_ids", duplicate_ids)

        names = [row[1] for row in authors if len(row) == 2]
        blank_names = [row for row in authors if len(row) == 2 and not row[1].strip()]
        report.check(not blank_names, "no blank author names",
                     f"{len(blank_names)} blank author names", blank_names)
        duplicate_names = [name for name, count in Counter(n.casefold() for n in names).items()
                           if count > 1]
        report.check(not duplicate_names, "no author appears twice (case-insensitive)",
                     f"{len(duplicate_names)} duplicate author names", duplicate_names)
        bad_unicode = [name for name in names if MOJIBAKE.search(name) or name != clean_unicode(name)]
        report.check(not bad_unicode, "author names are clean Unicode",
                     f"{len(bad_unicode)} author names contain Unicode junk or stray whitespace", bad_unicode)
        nonstandard_case = [name for name in names
                            if name != name_case(repair_known(clean_unicode(name)))]
        report.check(not nonstandard_case, "author names follow the shared person-name case convention",
                     f"{len(nonstandard_case)} author names are not normalized by name_case()",
                     nonstandard_case)

    # ---------------- book_authors ----------------
    if links is not None:
        bad_columns = [row for row in links if len(row) != 2]
        report.check(not bad_columns, "book_authors.csv has exactly 2 columns on every row",
                     "book_authors.csv rows should be (isbn13, author_id)", bad_columns)
        pairs = [(row[0], row[1]) for row in links if len(row) == 2]
        duplicate_pairs = [pair for pair, count in Counter(pairs).items() if count > 1]
        report.check(not duplicate_pairs, "no duplicate (isbn, author_id) rows",
                     f"{len(duplicate_pairs)} duplicate (isbn, author_id) rows", duplicate_pairs)

        blank_link_isbns = [isbn for isbn, _ in pairs if not isbn]
        report.check(not blank_link_isbns, "no blank ISBNs in book_authors.csv",
                     f"{len(blank_link_isbns)} blank ISBNs in book_authors.csv", blank_link_isbns)
        if books is not None:
            orphan_isbns = sorted({isbn for isbn, _ in pairs} - isbns)
            report.check(not orphan_isbns, "every ISBN in book_authors exists in books.csv",
                         f"{len(orphan_isbns)} ISBNs in book_authors are not in books.csv", orphan_isbns)
            malformed_isbns = [isbn for isbn, _ in pairs if not re.fullmatch(r"\d{13}", isbn)]
            report.check(not malformed_isbns, "book_authors ISBNs are 13-digit text values",
                         f"{len(malformed_isbns)} book_authors ISBNs are not 13 digits", malformed_isbns)

        invalid_author_values = [author_id for _, author_id in pairs
                                 if author_id.strip().upper() not in NULL_AUTHOR_IDS
                                 and not author_id.isdigit()]
        report.check(not invalid_author_values, "author_id is numeric or NULL in every link",
                     f"{len(invalid_author_values)} invalid author_id values in book_authors.csv",
                     invalid_author_values)
        used_author_ids = {author_id for _, author_id in pairs
                           if author_id.strip().upper() not in NULL_AUTHOR_IDS}
        if authors is not None:
            bad_references = sorted(used_author_ids - author_ids)
            report.check(not bad_references, "every author_id in book_authors exists in authors.csv",
                         f"{len(bad_references)} author_ids in book_authors are missing from authors.csv",
                         bad_references)
            unused_ids = sorted(author_ids - used_author_ids)
            if unused_ids:
                report.warn(f"{len(unused_ids)} authors are not linked to any book", unused_ids)

        if books is not None:
            linked_isbns = {isbn for isbn, _ in pairs}
            books_without_link = sorted(isbns - linked_isbns)
            null_link_count = sum(1 for _, author_id in pairs
                                  if author_id.strip().upper() in NULL_AUTHOR_IDS)
            report.check(
                not books_without_link,
                f"every book appears in book_authors.csv ({null_link_count} NULL-author link(s))",
                f"{len(books_without_link)} books have no book_authors.csv row",
                books_without_link,
            )

    # ---------------- borrowers ----------------
    if borrowers is not None:
        bad_column_counts = [row for row in borrowers if len(row) != 5]
        report.check(not bad_column_counts, "borrowers.csv has exactly 5 columns",
                     f"{len(bad_column_counts)} borrower rows do not have 5 columns",
                     bad_column_counts)
        if borrowers:
            cards = [row[0] for row in borrowers if row]
            bad_cards = [card for card in cards if not CARD_ID_RE.fullmatch(card)]
            report.check(not bad_cards, "Card_ids follow ID plus six digits",
                         f"{len(bad_cards)} malformed Card_ids", bad_cards)
            duplicate_cards = [card for card, count in Counter(cards).items() if count > 1]
            report.check(not duplicate_cards, "Card_ids are unique",
                         f"{len(duplicate_cards)} duplicate Card_ids", duplicate_cards)

            borrower_names = [row[2] for row in borrowers if len(row) >= 3]
            blank_names = [name for name in borrower_names if not name.strip()]
            report.check(not blank_names, "no blank borrower names",
                         f"{len(blank_names)} blank borrower names", blank_names)

            ssns = [row[1] for row in borrowers if len(row) >= 2]
            bad_ssns = [ssn for ssn in ssns if not SSN_RE.fullmatch(ssn)]
            report.check(not bad_ssns, "all SSNs use ###-##-#### format",
                         f"{len(bad_ssns)} malformed SSNs", bad_ssns)
            duplicate_ssns = [ssn for ssn, count in Counter(ssns).items() if count > 1]
            report.check(not duplicate_ssns, "SSNs are unique",
                         f"{len(duplicate_ssns)} duplicate SSNs", duplicate_ssns)

            phones = [row[4] for row in borrowers if len(row) >= 5 and row[4].strip()]
            bad_phones = [phone for phone in phones if not PHONE_RE.fullmatch(phone)]
            report.check(not bad_phones, "all nonblank phone numbers use (###) ###-#### format",
                         f"{len(bad_phones)} malformed phone numbers", bad_phones)

            borrower_fields = [value for row in borrowers for value in row]
            bad_unicode = [value for value in borrower_fields
                           if MOJIBAKE.search(value) or value != clean_unicode(value)]
            report.check(not bad_unicode, "borrower fields are clean Unicode",
                         f"{len(bad_unicode)} borrower fields contain Unicode junk or stray whitespace",
                         bad_unicode)

        if src_borrowers and os.path.isfile(src_borrowers):
            source_borrowers = read_table(src_borrowers)
            report.check(
                len(source_borrowers) == len(borrowers),
                f"borrower row count matches source ({len(source_borrowers)})",
                f"borrowers.csv has {len(borrowers)} rows but source has {len(source_borrowers)}",
            )

    return report


def validation_main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", default="output", help="folder holding the four deliverable CSVs")
    parser.add_argument("--src", default=os.path.join("input", "books-1.csv"),
                        help="original books input, used to validate ISBN preservation")
    parser.add_argument("--src-borrowers", default=os.path.join("input", "borrowers.csv"),
                        help="original borrowers input, used to validate row count")
    args = parser.parse_args(argv)

    print(f"Validating {args.out}/")
    report = validate(args.out, args.src, args.src_borrowers)
    report.show()
    return 1 if report.errors else 0



"""Run the complete Library Milestone 1 normalization pipeline.

Run from this directory (or invoke this file by path):
    python integrate_validate.py                  # run all stages, validate, and package
    python integrate_validate.py --no-package     # run and validate without creating a zip
    python integrate_validate.py --skip-run       # validate existing output/ files and package
    python integrate_validate.py --validate-only  # validate existing output/ files only

The stage scripts belong to Persons 1-4 and are left unchanged.  This driver
passes their actual input/output paths explicitly so the pipeline works with
the repository's input/ directory and output/ directory.
"""
import argparse
import os
import runpy
import types
import subprocess
import sys
import zipfile


ROOT = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR = os.path.join(ROOT, "input")
OUTPUT_DIR = os.path.join(ROOT, "output")
ZIP_NAME = "milestone1_submission.zip"

STAGE_CALLS = [
    ("Person 1 - books", ["books.py", os.path.join(INPUT_DIR, "books-1.csv"),
                           os.path.join(OUTPUT_DIR, "books.csv")]),
    ("Person 2 - author parsing", ["parse_authors.py", os.path.join(INPUT_DIR, "books-1.csv"),
                                    os.path.join(OUTPUT_DIR, "author_pairs.csv")]),
    ("Person 3 - author tables", ["build_authors.py", os.path.join(OUTPUT_DIR, "author_pairs.csv"),
                                   os.path.join(OUTPUT_DIR, "books.csv"), OUTPUT_DIR]),
    ("Person 4 - borrowers", ["clean_borrower.py", os.path.join(INPUT_DIR, "borrowers.csv"),
                               os.path.join(OUTPUT_DIR, "borrowers.csv")]),
]

DELIVERABLES = ("books.csv", "authors.csv", "book_authors.csv", "borrowers.csv")
SOURCE_FILES = (
    "integrate_validate.py", "books.py", "parse_authors.py", "build_authors.py",
    "clean_borrower.py", "README.md",
)
RAW_INPUTS = ("books-1.csv", "borrowers.csv")


def run_stages():
    """Execute Persons 1-4 in dependency order, stopping on the first failure."""
    for label, arguments in STAGE_CALLS:
        script_path = os.path.join(ROOT, arguments[0])
        if not os.path.isfile(script_path):
            raise SystemExit(f"[run] required stage script is missing: {script_path}")
        if not os.path.isfile(arguments[1]):
            raise SystemExit(f"[run] required input is missing for {label}: {arguments[1]}")

        print(f"\n[run] {label}", flush=True)
        if arguments[0] == "parse_authors.py":
            # Person 2 imports `helpers`; expose Person 5's compatible helper
            # interface in memory so no extra Person 5 .py file is required.
            helper_module = types.ModuleType("helpers")
            helper_module.clean_unicode = clean_unicode
            helper_module.title_case = lambda value: name_case(repair_known(clean_unicode(value)))
            sys.modules["helpers"] = helper_module
            old_argv = sys.argv[:]
            try:
                sys.argv = [script_path, *arguments[1:]]
                runpy.run_path(script_path, run_name="__main__")
            finally:
                sys.argv = old_argv
                sys.modules.pop("helpers", None)
        else:
            command = [sys.executable, script_path, *arguments[1:]]
            result = subprocess.run(command, cwd=ROOT)
            if result.returncode != 0:
                raise SystemExit(
                    f"[run] {label} failed with exit code {result.returncode}; stopping."
                )


def collect_outputs():
    """Confirm all four deliverables exist in output/ under their schema names."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    missing = [name for name in DELIVERABLES
               if not os.path.isfile(os.path.join(OUTPUT_DIR, name))]
    if missing:
        print("[collect] missing deliverables: " + ", ".join(missing))
        return False
    for name in DELIVERABLES:
        print(f"[collect] ready: output/{name}")
    return True


def package():
    """Create the assignment archive: CSV deliverables at root, code in src/."""
    missing_sources = [name for name in SOURCE_FILES
                       if not os.path.isfile(os.path.join(ROOT, name))]
    missing_inputs = [name for name in RAW_INPUTS
                      if not os.path.isfile(os.path.join(INPUT_DIR, name))]
    if missing_sources or missing_inputs:
        details = []
        if missing_sources:
            details.append("source files missing: " + ", ".join(missing_sources))
        if missing_inputs:
            details.append("raw inputs missing: " + ", ".join(missing_inputs))
        raise SystemExit("[package] " + "; ".join(details))

    zip_path = os.path.join(ROOT, ZIP_NAME)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in DELIVERABLES:
            archive.write(os.path.join(OUTPUT_DIR, name), name)
        for name in SOURCE_FILES:
            archive.write(os.path.join(ROOT, name), f"src/{name}")
        for name in RAW_INPUTS:
            archive.write(os.path.join(INPUT_DIR, name), f"src/input/{name}")

    with zipfile.ZipFile(zip_path, "r") as archive:
        print(f"\n[package] wrote {ZIP_NAME} ({os.path.getsize(zip_path):,} bytes)")
        for info in archive.infolist():
            print(f"    {info.filename} ({info.file_size:,} bytes)")
    return zip_path


def _validator_args():
    return [
        "--out", OUTPUT_DIR,
        "--src", os.path.join(INPUT_DIR, "books-1.csv"),
        "--src-borrowers", os.path.join(INPUT_DIR, "borrowers.csv"),
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--skip-run", action="store_true",
                        help="do not run stage scripts; validate existing output/")
    parser.add_argument("--no-package", action="store_true",
                        help="run and validate, but do not create the submission zip")
    parser.add_argument("--validate-only", action="store_true",
                        help="validate existing output/ files without running or packaging")
    args = parser.parse_args(argv)

    if args.validate_only:
        return validation_main(_validator_args())

    if not args.skip_run:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        run_stages()

    complete = collect_outputs()
    print("\n[validate]")
    validation_code = validation_main(_validator_args())
    if validation_code or not complete:
        print("\n[integrate_validate] validation failed or deliverables are missing; zip not created.")
        return 1

    if not args.no_package:
        package()
    return 0


if __name__ == "__main__":
    sys.exit(main())
