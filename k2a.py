#!/usr/bin/env python3

import argparse
import csv
import html
import re
import sqlite3
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List books with lookups, choose one, then export Anki TSV."
    )
    parser.add_argument(
        "db",
        type=Path,
        help="Path to Kindle vocab.db SQLite file",
    )
    return parser.parse_args()


def fetch_books(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    query = """
    SELECT DISTINCT
      b.id,
      b.title,
      b.authors
    FROM LOOKUPS AS l
    INNER JOIN BOOK_INFO AS b ON b.id = l.book_key
    ORDER BY b.title COLLATE NOCASE ASC;
    """
    return conn.execute(query).fetchall()


def fetch_lookups_for_book(
    conn: sqlite3.Connection, book_id: str
) -> list[tuple[str, str, str]]:
    query = """
    SELECT
      w.word,
      w.stem,
      l.usage
    FROM LOOKUPS AS l
    INNER JOIN WORDS AS w ON w.id = l.word_key
    WHERE l.book_key = ?
    ORDER BY l.timestamp ASC;
    """
    return conn.execute(query, (book_id,)).fetchall()


def choose_book(books: list[tuple[str, str, str]]) -> tuple[str, str, str] | None:
    for idx, (_, title, authors) in enumerate(books, start=1):
        print(
            f"{idx}. {title.strip() or '<untitled>'} ({authors.strip() or 'Unknown author'})"
        )

    while True:
        choice = input("Select a book by number (or 'q' to quit): ").strip()
        if choice.lower() in {"q", "quit"}:
            return None
        if not choice.isdigit():
            print("Invalid input. Enter a number.")
            continue

        selected_index = int(choice)
        if 1 <= selected_index <= len(books):
            return books[selected_index - 1]

        print(f"Out of range. Enter a number between 1 and {len(books)}.")


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", name).strip()
    return cleaned or "anki_cards"


def prompt_output_path(default_title: str) -> Path:
    default_name = f"{safe_filename(default_title)}.tsv"
    raw = input(f"Output path [{default_name}]: ").strip()
    return Path(raw) if raw else Path(default_name)


def confirm_overwrite(path: Path) -> bool:
    while True:
        choice = input(f"File '{path}' exists. Overwrite? [y/N]: ").strip().lower()
        if choice in {"y", "yes"}:
            return True
        if choice in {"", "n", "no"}:
            return False
        print("Invalid input. Enter 'y' or 'n'.")


def emphasize_word_in_usage(word: str, usage: str) -> str:
    usage_html = html.escape(usage)
    if not word:
        return usage_html

    word_html = html.escape(word)
    pattern = re.compile(rf"\b({re.escape(word_html)})\b", re.IGNORECASE)
    emphasized = pattern.sub(r"<b>\1</b>", usage_html)
    if emphasized != usage_html:
        return emphasized

    # Fallback for tokens where word boundary is unreliable (e.g., punctuation-heavy words).
    fallback = re.compile(re.escape(word_html), re.IGNORECASE)
    return fallback.sub(lambda m: f"<b>{m.group(0)}</b>", usage_html)


def build_front(stem: str, word: str, usage: str) -> str:
    stem_text = html.escape(stem or word)
    usage_text = emphasize_word_in_usage(word, usage)
    return (
        '<div style="text-align:center;font-size:30px;font-weight:700;">'
        f"{stem_text}"
        "</div>\n<hr>\n"
        '<div style="text-align:left;">'
        f"{usage_text}"
        "</div>"
    )


def build_back(stem: str, word: str) -> str:
    stem_text = html.escape(stem or word)
    return (
        '<div style="text-align:center;font-size:30px;font-weight:700;">'
        f"{stem_text}"
        "</div>"
    )


def write_anki_tsv(output_path: Path, rows: list[tuple[str, str]]) -> None:
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t", lineterminator="\n")
        for front, back in rows:
            writer.writerow([front, back])


def main() -> int:
    args = parse_args()
    if not args.db.exists():
        print(f"Database file not found: {args.db}", file=sys.stderr)
        return 1

    with sqlite3.connect(args.db) as conn:
        books = fetch_books(conn)
        if not books:
            print("No books found with lookups in LOOKUPS table.")
            return 0

        selected = choose_book(books)
        if selected is None:
            print("Cancelled.")
            return 0

        book_id, title, authors = selected
        title = title.strip()
        authors = authors.strip()
        print(f"\nSelected: {title or '<untitled>'} ({authors or 'Unknown author'})")
        output_path = prompt_output_path(title or "anki_cards")
        rows = fetch_lookups_for_book(conn, book_id)

    if not rows:
        print(f"No lookups found for selected book id={book_id}")
        return 0

    cards: list[tuple[str, str]] = []
    for word, stem, usage in rows:
        base_word = (word or "").strip()
        base_stem = (stem or "").strip()
        base_usage = (usage or "").strip()
        if not base_word and not base_stem:
            continue
        front = build_front(base_stem, base_word, base_usage)
        back = build_back(base_stem, base_word)
        cards.append((front, back))

    if not cards:
        print("No valid cards generated from lookups.")
        return 0

    if output_path.exists() and not confirm_overwrite(output_path):
        print("Cancelled.")
        return 0

    write_anki_tsv(output_path, cards)
    print(f"Exported {len(cards)} cards to {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
