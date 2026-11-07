# Databases_Project
# CS-4347 Library System - Milestone 1

Normalizes the raw books and borrowers data into 3NF CSV files.

## How to run
From the project root (Python 3, no extra packages needed):

    python integrate_validate.py

This runs the full pipeline, checks the results, and creates
milestone1_submission.zip. The four CSVs are written to output/.

## Files
- books.py            - cleans books, produces books.csv (ISBN-13, title)
- parse_authors.py    - splits author strings into (isbn, author) pairs
- build_authors.py    - builds authors.csv and book_authors.csv
- clean_borrower.py   - builds borrowers.csv
- integrate_validate.py - shared helpers, runs everything, validates, zips

## Output
- books.csv         - isbn13, title
- authors.csv       - author_id, name
- book_authors.csv  - isbn13, author_id
- borrowers.csv     - Card_id, Ssn, Bname, Address, Phone

## Design decisions
- ISBNs are kept as text so leading zeros and "X" are preserved.
- ISBN-13 is the book ID; a bad ISBN-13 is recomputed from the ISBN-10.
- Authors are split on commas/semicolons and matched case-insensitively.
- Organizations are treated as ordinary authors with their own author_id.
- Books with no known author stay in books.csv but have no row in
  book_authors.csv (a blank author_id breaks the composite primary key).
- Titles and names use one shared case convention; accents are kept.
- Trailing series text like "(Amos Walker Series)" is removed from titles.
