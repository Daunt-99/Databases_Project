# CS-4347 Library System

A library management back end in Python and SQLite. Librarians (not borrowers)
are the users. Milestone 1 normalizes the raw data into 3NF CSV files;
Milestone 2 loads them into a SQLite database and adds the library logic.
The GUI is Milestone 3, so every feature here is a plain function that
returns a value or raises `db.LibraryError` with a message a librarian can read.

## Requirements
- Python 3 (developed and tested on 3.12) and SQLite (bundled with Python).
- No third-party packages: standard library only.

## Quick start (Milestone 2)
From the project root:

    python integrate_validate.py                        # Milestone 1: writes the CSVs to output/
    python loader.py output --db library.db --reset     # build the database from those CSVs

Run the tests (each uses its own temporary database):

    python -m unittest test_search test_fines test_checkin_borrowers test_end_to_end

Try it from a Python prompt:

    import search, checkout, checkin, borrowers, fines

    search.search("grisham")                            # ISBN, title, authors, IN/OUT
    card = borrowers.create_borrower("Jane Doe", "123-45-6789", "1 Main St", "(214) 555-0000")
    loan = checkout.checkout("9780385501200", card)     # due date = today + 14 days
    checkin.find_loans(card_id=card)                    # also by isbn= or name=
    checkin.checkin([loan])                             # 1-3 loan ids
    fines.refresh_fines()                               # daily batch update
    fines.list_fines()                                  # grouped by card, unpaid only
    fines.pay_fines(card)

Fines can also be run from the command line:
`python fines.py refresh | list | list-all | pay CARD_ID | check CARD_ID`.

Set `LIBRARY_DB=path/to/file.db` to use a different database file, and
`LIBRARY_TODAY=YYYY-MM-DD` to pretend it is another day (handy for demoing late fines).

## Milestone 2 files
- schema.sql      - tables: BOOK, AUTHORS, BOOK_AUTHORS, BORROWER, BOOK_LOANS, FINES
- db.py           - connection helper, transactions, date and money helpers, LibraryError
- loader.py       - imports the Milestone 1 CSVs into SQLite
- search.py       - `search(text)`: case-insensitive substring search over ISBN, title and author
- checkout.py     - `checkout(isbn, card_id)`: 3-loan limit, book availability, unpaid-fine check
- checkin.py      - `find_loans(...)` and `checkin(loan_ids)`
- borrowers.py    - `create_borrower(name, ssn, address, phone)`
- fines.py        - `refresh_fines`, `pay_fines`, `list_fines`, `has_unpaid_fines`
- test_search.py, test_fines.py, test_checkin_borrowers.py - unit tests
- test_end_to_end.py - final end-to-end run of all modules on the Milestone 1 data

## Milestone 2 rules and design decisions
- **Search:** the text is split into words and every word must appear in the ISBN,
  title or an author name (different words may match different fields). Books with no
  author still appear. A book is OUT while it has a loan with no return date.
- **Checkout:** fails with a clear message if the borrower already has 3 active loans,
  the book is already out (even to the same borrower), or the borrower has an unpaid fine.
- **Check-in:** loans can be found by ISBN, card ID and/or part of the borrower's name
  (case-insensitive and Unicode-aware, so "élodie" matches "Élodie"). 1 to 3 loans can be
  checked in at once. The check is all-or-nothing: if any selected loan is invalid,
  none of them is changed.
- **Borrowers:** name, SSN and address are required. SSNs are stored as 123-45-6789 and
  must be unique (one card per person). New card IDs continue the existing format, for
  example ID001001 follows the 1,000 loaded borrowers.
- **Fines:** $0.25 per day late, stored as integer cents so totals are exact (no floats).
  Returned late: (date_in - due_date) days. Still out and late: (today - due_date) days.
  `refresh_fines()` updates an unpaid row only if the amount changed and never touches a
  paid row. Fines are paid per borrower, all at once (no partial payment), and only when
  none of that borrower's books are still out.
- **Transactions:** read-then-write operations (checkout, check-in, creating a borrower,
  fines) run inside a write transaction so two librarians cannot collide.

## Milestone 1 - Data normalization

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
