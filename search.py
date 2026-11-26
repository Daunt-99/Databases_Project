"""Milestone 2 catalog search for SQLite."""

from contextlib import closing
from itertools import groupby


def search(text, conn=None):
    """Return one dictionary per book. Blank input returns an empty list.

    Each whitespace-separated term must match part of the ISBN, title or an
    author name. Terms may match different fields. Pass a connection to use
    an existing database, or omit it to use db.get_connection().
    """
    terms = text.casefold().split()
    if not terms:
        return []
    if conn is None:
        from db import get_connection

        with closing(get_connection()) as connection:
            return search(text, connection)

    with closing(conn.cursor()) as cursor:
        cursor.row_factory = None
        cursor.execute("""
            SELECT b.isbn, b.title,
                   CASE WHEN EXISTS (
                       SELECT 1 FROM BOOK_LOANS l
                       WHERE l.isbn = b.isbn AND l.date_in IS NULL
                   ) THEN 'OUT' ELSE 'IN' END,
                   a.name
            FROM BOOK b
            LEFT JOIN BOOK_AUTHORS ba ON ba.isbn = b.isbn
            LEFT JOIN AUTHORS a ON a.author_id = ba.author_id
            ORDER BY b.title COLLATE NOCASE, b.isbn, a.name COLLATE NOCASE, a.author_id
        """)
        results = []
        for (isbn, title, availability), rows in groupby(cursor, key=lambda row: row[:3]):
            authors = [row[3] for row in rows if row[3]]
            fields = [value.casefold() for value in (isbn, title, *authors)]
            if all(any(term in field for field in fields) for term in terms):
                result = {
                    "isbn": isbn, "title": title, "authors": ", ".join(authors),
                    "availability": availability,
                }
                if text.strip().casefold() == isbn.casefold():
                    return [result]
                results.append(result)
        return results
