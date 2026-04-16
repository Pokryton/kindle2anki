# Kindle2Anki

`k2a.py` turns lookups from a Kindle Vocabulary Builder database (`vocab.db`) into an Anki-importable TSV file.

For now, it only supports English. Definitions are fetched from the [Free Dictionary API](https://dictionaryapi.dev/).

## Usage

Run:

```console
./k2a.py /path/to/vocab.db
```

The script will:

1. List books that have lookup history.
2. Let you choose a book.
3. Ask where to save the output TSV file.

The exported TSV can be imported into Anki. The card HTML uses the styles defined in `k2a.css`, so it is best to create a dedicated note type in Anki and paste the contents of `k2a.css` into that note type's Styling section.

This project uses only the Python standard library, so no extra dependencies are required.
