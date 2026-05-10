"""Convert the legacy printable PDF inventory export into the app's item CSV format."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.utils.item_pdf_import import (
    extract_item_rows_from_pdf,
    resolve_duplicate_rows,
    write_duplicate_review_csv,
    write_item_import_csv,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Convert a printable PDF inventory export into the CSV format expected "
            "by the InvoiceManager item importer."
        )
    )
    parser.add_argument("pdf_path", help="Path to the source PDF export.")
    parser.add_argument("output_csv", help="Path to the generated item CSV.")
    parser.add_argument(
        "--review-csv",
        dest="review_csv",
        help=(
            "Optional path for duplicate rows that need manual review. "
            "Defaults to <output>_review.csv."
        ),
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    output_csv = Path(args.output_csv)
    review_csv = (
        Path(args.review_csv)
        if args.review_csv
        else output_csv.with_name(f"{output_csv.stem}_review.csv")
    )

    rows = extract_item_rows_from_pdf(args.pdf_path)
    resolved_rows, review_rows = resolve_duplicate_rows(rows)

    write_item_import_csv(output_csv, resolved_rows)
    write_duplicate_review_csv(review_csv, review_rows)

    print(f"Parsed {len(rows)} rows from {args.pdf_path}")
    print(f"Wrote {len(resolved_rows)} import-ready rows to {output_csv}")
    print(f"Wrote {len(review_rows)} review rows to {review_csv}")

    if review_rows:
        print("Resolve the review rows before importing if you need those items.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
