"""Person 2: author parsing.

Reads books-1.csv and produces (isbn13, author_name) pairs for Person 3.
Books with no author are emitted as (isbn13, None) so Person 3 can write a NULL author_id.

Usage: python parse_authors.py books-1.csv author_pairs.csv
"""
import csv
import re
import sys
import unicodedata

from books import isbn10_to_isbn13, normalize_isbn10, normalize_isbn13, read_rows

# Swap these for Person 5's shared helpers once they exist.
try:
    from helpers import clean_unicode, title_case  # noqa: F401
except ImportError:
    def clean_unicode(s):
        return unicodedata.normalize("NFKC", s)

    def title_case(name):
        # Only touch ALL-CAPS / all-lowercase names; leave McDonald, van Allsburg, etc. alone.
        if name.isupper() or name.islower():
            name = " ".join(w.capitalize() for w in name.split(" "))
            name = re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), name)
        return name

GEN_SUFFIX = re.compile(r"^(jr|sr|ii|iii|iv)\.?\s+", re.I)
GEN_ONLY = re.compile(r"^(jr|sr|ii|iii|iv)\.?$", re.I)
CREDENTIAL_TOKEN = r"(?:ph\.?d|m\.?d|d\.?d|ed\.?d|esq)\.?(?=\s|$)"
CREDENTIAL = re.compile(rf"^{CREDENTIAL_TOKEN}\s*", re.I)
CRED_ONLY = re.compile(rf"^(?:{CREDENTIAL_TOKEN}\s*)+$", re.I)
TRAILING_CREDENTIALS = re.compile(rf"(?:\s+{CREDENTIAL_TOKEN})+$", re.I)
SURNAME = r"[^\W\d_]+(?:[-'’][^\W\d_]+)*"
INITIAL = r"[^\W\d_]\."
FRAGMENT_END = r"(?=\s*(?:[,;]|$))"
CREDENTIAL_BEFORE_SURNAME = re.compile(
    rf",\s*{CREDENTIAL_TOKEN}\s+({SURNAME}){FRAGMENT_END}", re.I
)
SURNAME_FIRST = re.compile(
    rf"(?:^|(?<=[,;]))\s*({SURNAME})\s*,\s*((?:{INITIAL}\s*)+){FRAGMENT_END}"
)
# Keep the U.S. abbreviation in organization names.
NAME_INITIALS = re.compile(
    rf"(?<![\w.])(?!(?i:U\.S\.))(?:{INITIAL}\s*)+(?=[^\W\d_](?:[^\W\d_]|['’]))"
)


def _strip_parens(s):
    s = re.sub(r"\([^)]*\)", "", s)   # (editor), (eds.)
    s = re.sub(r"\([^)]*$", "", s)    # truncated "(Co"
    return s


def parse_authors(raw):
    """Return a clean, de-duplicated list of author names for one book."""
    raw = clean_unicode(raw or "")
    # Repair comma formats such as "J. Patrick, Ph.D. Gannon" and "Cooper,J.F.".
    raw = CREDENTIAL_BEFORE_SURNAME.sub(r" \1", raw)
    raw = SURNAME_FIRST.sub(lambda match: f"{match[2].strip()} {match[1]}", raw)
    pieces = [p.strip() for p in re.split(r"[;,]", raw)]
    pieces = [p for p in pieces if p]

    merged = []
    for p in pieces:
        if CRED_ONLY.match(p):
            continue                      # "Ph.D.", "D.D." fragments
        m = GEN_SUFFIX.match(p)
        if m and merged:
            # "Walter" + "Sr. Scott" -> "Walter Scott Sr."
            g = m.group(1).lower()
            suffix = g.capitalize() + "." if g in ("jr", "sr") else g.upper()
            merged[-1] = f"{merged[-1]} {p[m.end():]} {suffix}"
            continue
        if GEN_ONLY.match(p) and merged:
            merged[-1] += " " + p
            continue
        p = CREDENTIAL.sub("", p)          # leading "PhD. Riaz Khadem"
        merged.append(p)

    out, seen = [], set()
    for name in merged:
        name = _strip_parens(name)
        name = re.sub(r"\s+", " ", name).strip(" ;,").lstrip(".")
        name = TRAILING_CREDENTIALS.sub("", name)
        if not name:
            continue
        # Insert the missing space after initials ("W.Crain" -> "W. Crain") BEFORE
        # casing, so the shared name_case() sees the finished tokens.
        name = NAME_INITIALS.sub(
            lambda match: " ".join(re.findall(INITIAL, match[0].upper())) + " ",
            name,
        )
        name = title_case(name)
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            out.append(name)
    return out


def main(src, dst):
    rows = read_rows(src)
    n_pairs = n_none = n_no_isbn = 0
    with open(dst, "w", encoding="utf-8", newline="") as g:
        w = csv.writer(g)
        w.writerow(["isbn13", "author_name"])
        for _, row in rows:
            isbn = normalize_isbn13(row.get("ISBN13"))
            if not isbn:
                isbn10 = normalize_isbn10(row.get("ISBN10"))
                if isbn10:
                    isbn = isbn10_to_isbn13(isbn10)
            if not isbn:
                n_no_isbn += 1
                continue
            authors = parse_authors(row.get("AUTHOR"))
            if not authors:
                w.writerow([isbn, ""])            # NULL author
                n_none += 1
            for a in authors:
                w.writerow([isbn, a])
                n_pairs += 1
    print(f"books={len(rows)} pairs={n_pairs} no_author={n_none} no_isbn={n_no_isbn}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
