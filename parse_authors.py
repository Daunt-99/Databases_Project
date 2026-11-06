"""Person 2: author parsing.

Reads books-1.csv and produces (isbn13, author_name) pairs for Person 3.
Books with no author are emitted as (isbn13, None) so Person 3 can write a NULL author_id.

Usage: python parse_authors.py books-1.csv author_pairs.csv
"""
import csv
import re
import sys
import unicodedata

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
CREDENTIAL = re.compile(r"^(ph\.?d|m\.?d|d\.?d|ed\.?d|esq)\.?\s*", re.I)
CRED_ONLY = re.compile(r"^((ph\.?d|m\.?d|d\.?d|ed\.?d|esq)\.?\s*)+$", re.I)


def _strip_parens(s):
    s = re.sub(r"\([^)]*\)", "", s)   # (editor), (eds.)
    s = re.sub(r"\([^)]*$", "", s)    # truncated "(Co"
    return s


def parse_authors(raw):
    """Return a clean, de-duplicated list of author names for one book."""
    raw = clean_unicode(raw or "")
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
        if not name:
            continue
        name = title_case(name)
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            out.append(name)
    return out


def main(src, dst):
    n_books = n_pairs = n_none = 0
    with open(src, encoding="utf-8", newline="") as f, \
         open(dst, "w", encoding="utf-8", newline="") as g:
        w = csv.writer(g)
        w.writerow(["isbn13", "author_name"])
        for row in csv.DictReader(f):
            isbn = row["ISBN13"].strip()          # keep as text
            authors = parse_authors(row["Author"])
            n_books += 1
            if not authors:
                w.writerow([isbn, ""])            # NULL author
                n_none += 1
            for a in authors:
                w.writerow([isbn, a])
                n_pairs += 1
    print(f"books={n_books} pairs={n_pairs} no_author={n_none}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
