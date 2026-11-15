"""Convert a Places Portal CSV or Parquet export to a searchable SQLite snapshot."""
from __future__ import annotations

import ast
import csv
import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit
from tempfile import NamedTemporaryFile


COLUMNS = (
    "fsq_place_id", "name", "locality", "region", "country", "website",
    "fsq_category_labels", "date_closed",
)


def _rows(source: Path) -> Iterator[dict]:
    if source.suffix.lower() == ".parquet":
        try:
            import duckdb
        except ImportError as exc:
            raise ValueError("Install the [places] extra to import Parquet") from exc
        with duckdb.connect() as db:
            selected = ", ".join(COLUMNS)
            cursor = db.execute(f"SELECT {selected} FROM read_parquet(?)", [str(source)])
            while batch := cursor.fetchmany(4096):
                yield from (dict(zip(COLUMNS, row)) for row in batch)
        return
    with source.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = set(COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError("Export is missing columns: " + ", ".join(sorted(missing)))
        yield from reader


def _categories(raw: object) -> list[str]:
    if isinstance(raw, str):
        if not raw.strip():
            return []
        try:
            raw = json.loads(raw)
        except ValueError:
            if raw.startswith("[") and raw.endswith("]"):
                try:
                    raw = ast.literal_eval(raw)
                except (ValueError, SyntaxError):
                    raw = [x.strip(" '\"") for x in raw[1:-1].split(",")]
            else:
                raw = [raw]
    return [str(item).strip() for item in raw if item] if isinstance(raw, (list, tuple)) else []


def _records(rows: Iterator[dict]) -> Iterator[tuple]:
    for row in rows:
        if not row["fsq_place_id"] or not row["name"] or row["date_closed"]:
            continue
        labels = _categories(row["fsq_category_labels"])
        yield (
            row["fsq_place_id"], row["name"], row["locality"] or "",
            row["region"] or "", row["country"] or "", row["website"] or "",
            " ".join(labels), json.dumps(labels, ensure_ascii=False),
        )


def import_foursquare(source: Path, destination: Path) -> int:
    if source.resolve() == destination.resolve():
        raise ValueError("The export and the snapshot must be different files")
    if source.suffix.lower() not in {".csv", ".parquet"}:
        raise ValueError("Supply a CSV or Parquet Places Portal export")
    return _build_snapshot(_rows(source), destination)


def refresh_foursquare(
    destination: Path, *, uri: str, warehouse: str, table: str, token: str, countries: list[str],
) -> int:
    """Refresh from the provider catalog using columns and market filters."""
    endpoint = urlsplit(uri)
    if (endpoint.scheme != "https" or not endpoint.hostname
            or not (endpoint.hostname == "foursquare.com" or endpoint.hostname.endswith(".foursquare.com"))
            or endpoint.username or endpoint.password):
        raise ValueError("Use the HTTPS catalog URI from the Foursquare Places Portal")
    if not countries or any(len(c) != 2 or not c.isascii() or not c.isalpha() for c in countries):
        raise ValueError("Supply at least one two-letter country code")
    try:
        from pyiceberg.catalog import load_catalog
        from pyiceberg.expressions import In
    except ImportError as exc:
        raise ValueError("Install the [places] extra to refresh from the Foursquare catalog") from exc

    def rows() -> Iterator[dict]:
        catalog = load_catalog("foursquare", type="rest", uri=uri, warehouse=warehouse, token=token,
                               **{"rest.auth.type": "oauth2"})
        scan = catalog.load_table(table).scan(
            row_filter=In("country", {country.upper() for country in countries}),
            selected_fields=COLUMNS,
        )
        for batch in scan.to_arrow_batch_reader():
            yield from batch.to_pylist()

    return _build_snapshot(rows(), destination)


def _build_snapshot(rows: Iterator[dict], destination: Path) -> int:
    with NamedTemporaryFile(dir=destination.parent, suffix=".sqlite", delete=False) as handle:
        temp = Path(handle.name)
    try:
        with closing(sqlite3.connect(temp)) as db, db:
            db.execute(
                "CREATE VIRTUAL TABLE places USING fts5("
                "fsq_place_id UNINDEXED, name, locality UNINDEXED, region UNINDEXED, "
                "country UNINDEXED, website UNINDEXED, category_text, category_json UNINDEXED)"
            )
            db.executemany("INSERT INTO places VALUES (?, ?, ?, ?, ?, ?, ?, ?)", _records(rows))
            count = db.execute("SELECT count(*) FROM places").fetchone()[0]
        temp.replace(destination)
        return count
    finally:
        temp.unlink(missing_ok=True)
