"""Build the Incorporation catalog from the business organization workbook."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from urllib.parse import urlsplit

from openpyxl import load_workbook

SECTION_COLUMNS = {
    "Entity Types": ("country", "entity", "topic", "label", "summary", "section", "access", "source_id"),
    "Formation Steps": ("country", "entity", "step", "summary", "section", "access", "source_id"),
    "Compliance": ("country", "entity", "label", "summary", "section", "access", "source_id"),
    "Dissolution": ("country", "entity", "label", "summary", "section", "access", "source_id"),
}


def rows(book, name):
    for row in book[name].iter_rows(min_row=6, values_only=True):
        if row[0] is not None:
            yield [value.strip() if isinstance(value, str) else value for value in row]


def build(path):
    book = load_workbook(path, data_only=True, read_only=True, keep_links=False)
    try:
        countries = {}
        for order, name, code, *_, scope, _ in rows(book, "Country Index"):
            countries[name] = {"code": code, "name": name, "order": order, "scope": scope,
                               "sources": [], "entries": [], "gaps": []}

        sources = {}
        for name, source_id, publisher, title, language, topics, access, date, url in rows(book, "Source Evidence"):
            address = urlsplit(url)
            if address.scheme != "https" or not address.hostname or address.username or address.password:
                raise ValueError(f"Invalid source URL for {source_id}")
            source = {"id": source_id, "publisher": publisher, "title": title, "language": language,
                      "topics": topics.split(", "), "access": access, "review_date": str(date.date()), "url": url}
            if source_id in sources:
                raise ValueError(f"Duplicate source ID {source_id}")
            countries[name]["sources"].append(source)
            sources[source_id] = name

        for worksheet, fields in SECTION_COLUMNS.items():
            for values in rows(book, worksheet):
                entry = dict(zip(fields, values, strict=True))
                name = entry.pop("country")
                if sources[entry["source_id"]] != name:
                    raise ValueError(f"Source ID belongs to another country: {entry['source_id']}")
                if worksheet == "Formation Steps":
                    entry["label"] = f"Step {entry['step']}"
                entry.setdefault("topic", "Formation" if worksheet == "Formation Steps" else worksheet)
                countries[name]["entries"].append(entry)

        for name, status, issue, topics, details in rows(book, "Gaps"):
            if name in countries:
                countries[name]["gaps"].append({"status": status, "issue": issue, "topics": topics, "details": details})
        return sorted(countries.values(), key=lambda country: country["order"])
    finally:
        book.close()


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("workbook", type=Path)
    cli.add_argument("output", type=Path)
    args = cli.parse_args()
    countries = build(args.workbook)
    result = {"workbook": args.workbook.name, "countries": countries}
    raw = json.dumps(result, ensure_ascii=False, allow_nan=False)
    args.output.write_bytes(gzip.compress(raw.encode("utf-8"), mtime=0))
    print(f"{len(countries)} countries, {sum(len(item['sources']) for item in countries)} sources, "
          f"{sum(len(item['entries']) for item in countries)} research entries.")


if __name__ == "__main__":
    main()
