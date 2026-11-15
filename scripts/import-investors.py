"""Generate the app's angel investor directory from its source workbook."""

from __future__ import annotations

import argparse
import gzip
import json
import re
from pathlib import Path

from openpyxl import load_workbook

COLUMNS = {
    "Firm": "firm",
    "Website": "website",
    "Contact": "contact",
    "Title": "title",
    "Email": "email",
    "Email Format Status": "email_format",
    "DNS MX Deliverability": "email_mx",
    "Email Verification Notes": "email_notes",
    "Phone Number": "phone",
    "All Matched Sectors": "sectors",
    "Total Investments": "total_investments",
    "Active Investments": "active_investments",
    "Dry Powder ($M)": "dry_powder_millions",
    "Notes": "notes",
    "Notes 2": "notes_2",
    "Description": "description",
    "Source File": "source_file",
}
ANGEL_TITLE = re.compile(r"\bangel investors?\b", re.I)


def load_records(path: Path) -> list[dict]:
    book = load_workbook(path, read_only=True, data_only=True, keep_links=False)
    try:
        directory = []
        for sheet in book:
            rows = sheet.iter_rows(values_only=True)
            labels = list(next(rows, ()))
            if "All Matched Sectors" not in labels:
                continue
            indexes = {key: labels.index(column) for column, key in COLUMNS.items()}
            for row_number, values in enumerate(rows, start=2):
                title = values[indexes["title"]]
                if not isinstance(title, str) or not ANGEL_TITLE.search(title):
                    continue
                investor = {}
                for key, column_index in indexes.items():
                    value = values[column_index]
                    if isinstance(value, str):
                        value = value.strip()
                    if value not in ("", None):
                        investor[key] = value
                listed = (item.strip() for item in investor.get("sectors", "").split(","))
                investor["sectors"] = list(dict.fromkeys([sheet.title, *(label for label in listed if label)]))
                investor["sheet"] = sheet.title
                investor["row"] = row_number
                directory.append(investor)
        return directory
    finally:
        book.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    records = load_records(args.workbook)
    if not records:
        parser.error("No rows with an Angel Investor title were found.")
    payload = json.dumps({"workbook": args.workbook.name, "records": records}, ensure_ascii=False, allow_nan=False)
    args.output.write_bytes(gzip.compress(payload.encode("utf-8"), mtime=0))
    print(f"Imported {len(records):,} angel investor records.")


if __name__ == "__main__":
    main()
