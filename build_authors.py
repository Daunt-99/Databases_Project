#!/usr/bin/env python3
"""Person 3: build authors.csv and book_authors.csv.

Reads  : author_pairs.csv  (from Person 2: header "isbn13,author_name"; empty author_name = no author)
         books.csv         (from Person 1, optional but recommended: used to drop links to ISBNs
                            that are not in the final books table, so no orphan rows are written)
Writes : authors.csv       ->  1,"John Grisham"        (author_id, name)
         book_authors.csv  ->  9780440234746,1         (isbn, author_id)
         Books with no known author are NOT written to book_authors.csv (the composite primary key
         (isbn, author_id) rejects a blank author_id). They stay in books.csv.
Same style as the PDF example and Person 1's books.csv: no header row.

Usage: python build_authors.py [author_pairs.csv] [books.csv] [out_dir]
"""
import csv
import os
import sys


def build_author_tables(pairs):
    """pairs: iterable of (isbn, author_name). Returns (authors, links).

    authors: [(author_id, name)]  - one row per unique name (case-insensitive)
    links:   [(isbn, author_id)]  - one row per book/author pair; author_id "" = book with no author
             (main() leaves these out of book_authors.csv)
    """
    ids = {}        # casefolded name -> author_id
    authors = []    # (author_id, name); the first spelling seen is kept
    links = set()   # a set drops duplicate (isbn, author_id) pairs

    for isbn, name in pairs:
        isbn = (isbn or "").strip()
        name = (name or "").strip()
        if not isbn:
            continue
        if not name:
            links.add((isbn, ""))                  # book with no author -> NULL author_id
            continue
        key = name.casefold()
        if key not in ids:
            ids[key] = len(ids) + 1                # 1, 2, 3, ... in order of first appearance
            authors.append((ids[key], name))
        links.add((isbn, ids[key]))

    links = sorted(links, key=lambda l: (l[0], l[1] if l[1] != "" else 0))
    return authors, links


def read_pairs(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        # tolerate a file with no header row
        if header and header[0].strip().lower() not in ("isbn13", "isbn"):
            yield header[0], header[1] if len(header) > 1 else ""
        for r in reader:
            if r:
                yield r[0], r[1] if len(r) > 1 else ""


def read_book_isbns(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {r[0].strip() for r in csv.reader(f) if r}


def write_authors(path, authors):
    with open(path, "w", encoding="utf-8", newline="") as f:
        for author_id, name in authors:
            f.write(f'{author_id},"{name.replace(chr(34), chr(34) * 2)}"\n')


def write_links(path, links):
    with open(path, "w", encoding="utf-8", newline="") as f:
        for isbn, author_id in links:
            f.write(f"{isbn},{author_id}\n")


def main():
    pairs_path = sys.argv[1] if len(sys.argv) > 1 else "author_pairs.csv"
    books_path = sys.argv[2] if len(sys.argv) > 2 else "books.csv"
    out_dir = sys.argv[3] if len(sys.argv) > 3 else "."
    os.makedirs(out_dir, exist_ok=True)

    pairs = list(read_pairs(pairs_path))

    # Only keep pairs whose book exists in books.csv (foreign-key safety).
    dropped = set()
    if os.path.exists(books_path):
        valid = read_book_isbns(books_path)
        dropped = {i.strip() for i, _ in pairs if i.strip() not in valid}
        pairs = [(i, n) for i, n in pairs if i.strip() in valid]
    else:
        print(f"WARNING: {books_path} not found - skipping ISBN check against books table")

    authors, links = build_author_tables(pairs)
    write_authors(os.path.join(out_dir, "authors.csv"), authors)
    no_author = sorted(l[0] for l in links if l[1] == "")
    links = [l for l in links if l[1] != ""]                # omit blank author_id rows
    write_links(os.path.join(out_dir, "book_authors.csv"), links)

    # Self-checks
    author_ids = {a[0] for a in authors}
    assert all(l[1] in author_ids for l in links), "link points to missing author"
    assert len(links) == len(set(links)), "duplicate link rows"

    print(f"authors.csv       : {len(authors)} authors")
    print(f"book_authors.csv  : {len(links)} links for {len({l[0] for l in links})} books")
    print(f"Books with no known author (kept in books.csv, omitted from book_authors.csv): {len(no_author)}")
    if dropped:
        print(f"Dropped pairs for {len(dropped)} ISBN(s) not in books.csv: {sorted(dropped)[:10]}")


if __name__ == "__main__":
    main()
