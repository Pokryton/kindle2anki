#!/usr/bin/env python3

import argparse
import csv
import html
import json
import re
import sqlite3
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List books with lookups, choose one, then export Anki TSV."
    )
    parser.add_argument(
        "db",
        type=Path,
        help="Path to Kindle vocab.db file",
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


def first_non_empty(values: list[str | None]) -> str:
    for v in values:
        if v and v.strip():
            return v.strip()
    return ""


def fetch_definition_entry(word: str) -> dict | None:
    if not word:
        return None

    url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{quote(word)}"
    req = Request(url, headers={"User-Agent": "k2a/0.1"})
    try:
        with urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError, URLError, TimeoutError, json.JSONDecodeError:
        return None

    if not isinstance(payload, list) or not payload:
        return None
    first = payload[0]
    if not isinstance(first, dict):
        return None
    return first


def render_definitions(definitions: list[dict]) -> str:
    if not definitions:
        return ""

    parts: list[str] = ["<ol>"]
    for d in definitions:
        if not isinstance(d, dict):
            continue
        definition = html.escape((d.get("definition") or "").strip())
        example = html.escape((d.get("example") or "").strip())
        synonyms = [html.escape(s) for s in d.get("synonyms", []) if isinstance(s, str)]
        antonyms = [html.escape(a) for a in d.get("antonyms", []) if isinstance(a, str)]

        parts.append("<li>")
        parts.append(definition or "No definition text.")
        detail_items: list[str] = []
        if example:
            detail_items.append(f"<li>Example: <i>{example}</i></li>")
        if synonyms:
            detail_items.append("<li>Synonyms: " + ", ".join(synonyms) + "</li>")
        if antonyms:
            detail_items.append("<li>Antonyms: " + ", ".join(antonyms) + "</li>")
        if detail_items:
            parts.append("<ul>")
            parts.extend(detail_items)
            parts.append("</ul>")
        parts.append("</li>")

    parts.append("</ol>")
    return "".join(parts)


def render_meanings(meanings: list[dict]) -> str:
    if not meanings:
        return ""

    parts: list[str] = ['<ol style="list-style-type: upper-roman;">']
    for meaning in meanings:
        if not isinstance(meaning, dict):
            continue
        pos = html.escape((meaning.get("partOfSpeech") or "").strip())
        definitions = meaning.get("definitions", [])
        if not isinstance(definitions, list):
            definitions = []

        parts.append("<li>")
        if pos:
            parts.append(f"<i>{pos}</i>")
        def_block = render_definitions(definitions)
        if def_block:
            parts.append(def_block)
        else:
            parts.append("<div>No definitions available.</div>")
        parts.append("</li>")

    parts.append("</ol>")
    return "".join(parts)


def build_back_html(headword: str, entry: dict | None) -> str:
    title = html.escape(headword)
    parts: list[str] = []

    if not entry:
        parts.append(f'<div style="text-align:left;font-weight:700;">{title}</div>')
        parts.append("<div>Definition lookup failed.</div>")
        return "".join(parts)

    phonetics = entry.get("phonetics", [])
    phonetic_from_list = ""
    if isinstance(phonetics, list):
        texts = []
        for p in phonetics:
            if isinstance(p, dict):
                text = p.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
        if texts:
            phonetic_from_list = " / ".join(dict.fromkeys(texts))

    phonetic = first_non_empty(
        [
            entry.get("phonetic") if isinstance(entry.get("phonetic"), str) else "",
            phonetic_from_list,
        ]
    )

    header = f'<span style="font-weight:700;">{title}</span>'
    if phonetic:
        header += (
            ' <span style="color:#666;"><i>' + html.escape(phonetic) + "</i></span>"
        )

    parts.append(f'<div style="text-align:left;">{header}</div>')

    origin = entry.get("origin")
    if isinstance(origin, str) and origin.strip():
        parts.append("<div><b>Origin:</b> " + html.escape(origin.strip()) + "</div>")

    meanings = entry.get("meanings", [])
    if not isinstance(meanings, list):
        meanings = []

    if meanings:
        parts.append(render_meanings(meanings))
    else:
        parts.append("<div>No meanings available.</div>")

    return "".join(parts)


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
    for i, (word, stem, usage) in enumerate(rows):
        if i >= 10:
            break  # FIXME: testing, remove it in later release
        base_word = (word or "").strip()
        base_stem = (stem or "").strip()
        base_usage = (usage or "").strip()
        if not base_word or not base_stem:
            continue
        front = build_front(base_stem, base_word, base_usage)
        back = build_back_html(base_stem, fetch_definition_entry(base_stem))
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
