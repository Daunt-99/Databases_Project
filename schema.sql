-- ============================================================
-- Library Management System - SQLite schema (Milestone 2)
-- ============================================================
-- Conventions used throughout the project:
--   * FINES.fine_amt is stored as INTEGER CENTS (e.g. $1.25 -> 125).
--     SQLite has no fixed-decimal type, and REAL would be a float, so integer
--     cents is the exact representation. Use db.cents_to_dollars() /
--     db.dollars_to_cents() to convert; never store a float.
--   * FINES.paid is INTEGER 0/1.
-- ============================================================

CREATE TABLE IF NOT EXISTS BOOK (
    isbn   TEXT PRIMARY KEY,
    title  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS AUTHORS (
    author_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS BOOK_AUTHORS (
    author_id  INTEGER NOT NULL REFERENCES AUTHORS(author_id) ON DELETE CASCADE,
    isbn       TEXT    NOT NULL REFERENCES BOOK(isbn)         ON DELETE CASCADE,
    PRIMARY KEY (author_id, isbn)
);

CREATE TABLE IF NOT EXISTS BORROWER (
    card_id  TEXT PRIMARY KEY,            -- e.g. 'ID000001'
    ssn      TEXT NOT NULL UNIQUE,        -- one card per person
    bname    TEXT NOT NULL CHECK (trim(bname)   <> ''),
    address  TEXT NOT NULL CHECK (trim(address) <> ''),
    phone    TEXT
);

CREATE TABLE IF NOT EXISTS BOOK_LOANS (
    loan_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    isbn      TEXT NOT NULL REFERENCES BOOK(isbn),
    card_id   TEXT NOT NULL REFERENCES BORROWER(card_id),
    date_out  TEXT NOT NULL,              -- 'YYYY-MM-DD'
    due_date  TEXT NOT NULL,              -- date_out + 14 days
    date_in   TEXT,                       -- NULL while the book is out
    CHECK (due_date >= date_out),
    CHECK (date_in IS NULL OR date_in >= date_out)
);

CREATE TABLE IF NOT EXISTS FINES (
    loan_id   INTEGER PRIMARY KEY REFERENCES BOOK_LOANS(loan_id),
    fine_amt  INTEGER NOT NULL DEFAULT 0 CHECK (fine_amt >= 0),  -- CENTS
    paid      INTEGER NOT NULL DEFAULT 0 CHECK (paid IN (0, 1))
);

-- ------------------------------------------------------------
-- Indexes
-- ------------------------------------------------------------
-- At most ONE open loan per book. This is the database-level guarantee
-- that two checkouts can never both succeed for the same book, even if
-- application code races (Person 3).
CREATE UNIQUE INDEX IF NOT EXISTS ux_one_open_loan_per_book
    ON BOOK_LOANS(isbn) WHERE date_in IS NULL;

CREATE INDEX IF NOT EXISTS ix_loans_card       ON BOOK_LOANS(card_id);
CREATE INDEX IF NOT EXISTS ix_loans_isbn       ON BOOK_LOANS(isbn);
CREATE INDEX IF NOT EXISTS ix_book_authors_isbn ON BOOK_AUTHORS(isbn);

-- ------------------------------------------------------------
-- Convenience view: fines with a human-readable dollar string.
-- printf with integer math avoids any float rounding.
-- ------------------------------------------------------------
CREATE VIEW IF NOT EXISTS FINES_DISPLAY AS
SELECT f.loan_id,
       l.card_id,
       f.fine_amt AS fine_cents,
       printf('%d.%02d', f.fine_amt / 100, f.fine_amt % 100) AS fine_dollars,
       f.paid
FROM FINES f
JOIN BOOK_LOANS l ON l.loan_id = f.loan_id;
