
"""
Books pipeline (ISBN + title)

Reads books-1.csv and writes books.csv with two columns: ISBN13, Title.
Uses only the Python standard library.

Usage:
    python books.py [input_file] [output_file]
"""

import csv
import re
import sys
import unicodedata

INPUT_FILE = "books-1.csv"
OUTPUT_FILE = "books.csv"
REJECTS_FILE = "books_rejected.log"


CHAR_REPLACEMENTS = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"',
    "\u2013": "-", "\u2014": "-", "\u2212": "-",
    "\u2026": "...",
    "\u00a0": " ",
}


TRAILING_GROUP = re.compile(r"\s*([\(\[](?:[^()\[\]]|[\(\[][^()\[\]]*[\)\]])*[\)\]])\s*$")

_LANGUAGES = (
    r"spanish|french|german|italian|portuguese|dutch|finnish|swedish|norwegian|danish|"
    r"russian|polish|czech|greek|latin|chinese|japanese|korean|arabic|hebrew|turkish|"
    r"hungarian|romanian|catalan|bilingual|english|international|us|u\.s\.|uk|u\.k\."
)
_EDITION_MODIFIERS = (
    r"\d+(?:st|nd|rd|th)(?:\s+anniversary)?|first|second|third|fourth|fifth|revised|"
    r"expanded|updated|new|special|collector'?s|anniversary|abridged|unabridged|"
    r"annotated|illustrated|large[\s-]print|deluxe|limited|library|school(?:\s*&\s*library)?|"
    r"paperback|hardcover|trade|student|teacher'?s|critical|kindle|audio|movie\s+tie-in|"
    r"turtleback(?:\s+school\s*&\s*library\s+binding)?|authorized(?:,\s*trade)?"
)

EDITION_LABEL = re.compile(
    r"^[\(\[]\s*(?:"
    rf"(?:{_LANGUAGES})(?:\s+language)?\s+(?:edition|ed\.?|version)"
    rf"|(?:{_EDITION_MODIFIERS})(?:\s+(?:{_EDITION_MODIFIERS}))*\s+(?:edition|ed\.?|version)"
    r"|abridged|unabridged|revised|large print|bilingual"
    r")\s*[\)\]]$",
    re.IGNORECASE,
)

SMALL_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into",
    "nor", "of", "on", "or", "over", "the", "to", "up", "via", "with", "vs",
}
ACRONYMS = {"uk", "us", "usa", "tv", "dvd", "cd"}
ROMAN_NUMERALS = {"ii", "iii", "iv", "vi", "vii", "viii", "ix", "xi", "xii"}


def clean_unicode(text):
    """Fix mojibake, normalize quotes/dashes, strip control chars, collapse spaces."""
    if text is None:
        return ""
    text = str(text)

    
    if any(marker in text for marker in ("Ã", "Â", "â€")):
        try:
            text = text.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass

    text = unicodedata.normalize("NFKC", text)
    for bad, good in CHAR_REPLACEMENTS.items():
        text = text.replace(bad, good)

    
    text = text.replace("\ufffd", "")
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C" or ch in "\t")
    return re.sub(r"\s+", " ", text).strip()


def _cap_first_letter(segment):
    for i, ch in enumerate(segment):
        if ch.isdigit():  
            return segment
        if ch.isalpha():
            return segment[:i] + ch.upper() + segment[i + 1:]
    return segment


def smart_title_case(text):
    """One case convention for all titles: Title Case with small words lowercase.

    Only the first letter of each word is touched, so "God's" stays "God's".
    """
    words = text.split(" ")
    out = []
    force_cap = True  
    for i, word in enumerate(words):
        low = word.lower()
        bare = re.sub(r"[^a-z]", "", low)
        is_last = i == len(words) - 1

        if bare in ROMAN_NUMERALS or bare in ACRONYMS:
            new = word.upper()
        elif bare in SMALL_WORDS and not force_cap and not is_last and word[:1] not in "([\"'":
            new = low
        else:
            
            new = "-".join(_cap_first_letter(p) for p in low.split("-"))

        out.append(new)
        force_cap = word.endswith(":") or word.endswith("-")
    return " ".join(out)


def strip_series_tags(title):
    """Remove trailing (...) / [...] groups, but keep edition labels."""
    kept = []
    while True:
        m = TRAILING_GROUP.search(title)
        if not m or not title[:m.start()].strip():  
            break
        group = m.group(1)
        title = title[:m.start()].rstrip()
        if EDITION_LABEL.match(group):
            kept.insert(0, group)
    return " ".join([title] + kept)


def clean_title(raw_title):
    return smart_title_case(strip_series_tags(clean_unicode(raw_title)))


def isbn13_check_digit(first12):
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(first12))
    return str((10 - total % 10) % 10)


def is_valid_isbn13(isbn13):
    return (
        len(isbn13) == 13
        and isbn13.isdigit()
        and isbn13[-1] == isbn13_check_digit(isbn13[:12])
    )


def normalize_isbn10(raw):
    """Return a clean 10-char ISBN-10, or '' if it can't be trusted."""
    s = re.sub(r"[\s\-]", "", str(raw or "")).upper()
    if len(s) == 9 and s.isdigit():  
        s = s.zfill(10)
    if re.fullmatch(r"\d{9}[\dX]", s):
        return s
    return ""


def normalize_isbn13(raw):
    s = re.sub(r"[\s\-]", "", str(raw or ""))
    if len(s) == 12 and s.isdigit():  
        s = s.zfill(13)
    return s if is_valid_isbn13(s) else ""


def isbn10_to_isbn13(isbn10):
    core = "978" + isbn10[:9]
    return core + isbn13_check_digit(core)



def read_rows(path):
    """Read the input file as strings. Works whether it is tab- or comma-separated."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        first_line = f.readline()
        f.seek(0)
        delimiter = "\t" if first_line.count("\t") > first_line.count(",") else ","
        reader = csv.reader(f, delimiter=delimiter)
        rows = list(reader)

    if not rows:
        return []

    header = [h.strip().upper() for h in rows[0]]
    if "ISBN13" in header and "TITLE" in header:
        data = rows[1:]
    else:
        
        header = ["ISBN10", "ISBN13", "TITLE", "AUTHOR", "PUBLISHER", "PAGES"]
        data = rows

    result = []
    for line_no, row in enumerate(data, start=2):
        padded = row + [""] * (len(header) - len(row))
        result.append((line_no, dict(zip(header, padded))))
    return result


def build_books(rows):
    """Return (books, rejects). books is a list of (isbn13, title)."""
    books = []
    rejects = []
    seen = {}
    stats = {"computed_isbn13": 0, "duplicates": 0, "no_isbn": 0, "no_title": 0}

    for line_no, row in rows:
        isbn10 = normalize_isbn10(row.get("ISBN10"))
        isbn13 = normalize_isbn13(row.get("ISBN13"))

        
        if not isbn13 and isbn10:
            isbn13 = isbn10_to_isbn13(isbn10)
            stats["computed_isbn13"] += 1

        if not isbn13:
            stats["no_isbn"] += 1
            rejects.append(f"line {line_no}: no usable ISBN -> {row}")
            continue

        title = clean_title(row.get("TITLE"))
        if not title:
            stats["no_title"] += 1
            rejects.append(f"line {line_no}: empty title for ISBN {isbn13}")
            continue

        if isbn13 in seen:
            stats["duplicates"] += 1
            if seen[isbn13] != title:
                rejects.append(
                    f"line {line_no}: duplicate ISBN {isbn13}, kept '{seen[isbn13]}', dropped '{title}'"
                )
            continue

        seen[isbn13] = title
        books.append((isbn13, title))

    return books, rejects, stats


def write_books(books, path):
    """Write rows like: 9780440234746,"The Testament"  (no header, as in the PDF)."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        for isbn13, title in books:
            escaped = title.replace('"', '""')
            f.write(f'{isbn13},"{escaped}"\n')


def main():
    in_path = sys.argv[1] if len(sys.argv) > 1 else INPUT_FILE
    out_path = sys.argv[2] if len(sys.argv) > 2 else OUTPUT_FILE

    rows = read_rows(in_path)
    books, rejects, stats = build_books(rows)
    write_books(books, out_path)

    with open(REJECTS_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(rejects) + ("\n" if rejects else ""))

    print(f"Rows read:             {len(rows)}")
    print(f"Books written:         {len(books)}  ->  {out_path}")
    print(f"ISBN-13 computed:      {stats['computed_isbn13']}")
    print(f"Duplicates dropped:    {stats['duplicates']}")
    print(f"Rows with no ISBN:     {stats['no_isbn']}")
    print(f"Rows with no title:    {stats['no_title']}")
    print(f"Details of problems:   {REJECTS_FILE}")


if __name__ == "__main__":
    main()
