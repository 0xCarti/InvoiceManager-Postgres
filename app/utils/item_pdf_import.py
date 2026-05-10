"""Helpers for converting printable PDF inventory exports into item import CSVs."""

from __future__ import annotations

import csv
import re
import shutil
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

ROW_PATTERN = re.compile(
    r"^(?P<name>.+?)\s+\$(?P<cost>-?\d+(?:\.\d+)?)\s+(?P<unit>[A-Za-z][A-Za-z /-]*)\s*$"
)


@dataclass(frozen=True)
class ParsedInventoryItem:
    """One parsed item row from the legacy PDF export."""

    name: str
    base_unit: str
    cost: float


@dataclass(frozen=True)
class DuplicateReviewRow:
    """A duplicate name that could not be resolved automatically."""

    name: str
    base_unit: str
    cost: float
    reason: str


def extract_item_rows_from_text(text: str) -> list[ParsedInventoryItem]:
    """Parse printable-view PDF text into item rows."""
    rows: list[ParsedInventoryItem] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = ROW_PATTERN.match(line)
        if not match:
            continue
        rows.append(
            ParsedInventoryItem(
                name=match.group("name").strip(),
                base_unit=match.group("unit").strip().lower(),
                cost=float(match.group("cost")),
            )
        )
    return rows


def resolve_duplicate_rows(
    rows: list[ParsedInventoryItem],
) -> tuple[list[ParsedInventoryItem], list[DuplicateReviewRow]]:
    """Collapse obvious duplicates and return the remaining review items."""
    grouped: dict[str, list[ParsedInventoryItem]] = defaultdict(list)
    for row in rows:
        grouped[row.name].append(row)

    resolved: list[ParsedInventoryItem] = []
    review: list[DuplicateReviewRow] = []

    for name, group in grouped.items():
        if len(group) == 1:
            resolved.append(group[0])
            continue

        units = {row.base_unit for row in group}
        non_zero = [row for row in group if row.cost > 0]
        costs = {row.cost for row in group}

        if len(units) == 1 and len(non_zero) == 1:
            resolved.append(non_zero[0])
            continue
        if len(units) == 1 and len(costs) == 1:
            resolved.append(group[0])
            continue

        if len(units) > 1:
            reason = "duplicate name with multiple base units"
        else:
            reason = "duplicate name with conflicting costs"

        for row in group:
            review.append(
                DuplicateReviewRow(
                    name=row.name,
                    base_unit=row.base_unit,
                    cost=row.cost,
                    reason=reason,
                )
            )

    resolved.sort(key=lambda row: row.name.casefold())
    review.sort(key=lambda row: (row.name.casefold(), row.base_unit, row.cost))
    return resolved, review


def extract_pdf_text(pdf_path: str | Path) -> str:
    """Extract text from the PDF using pdftotext or pdfplumber."""
    path = Path(pdf_path)
    pdftotext_path = _find_pdftotext()
    if pdftotext_path is not None:
        return subprocess.run(
            [pdftotext_path, "-layout", str(path), "-"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout

    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - fallback path
        raise RuntimeError(
            "Could not extract PDF text. Install pdfplumber or add pdftotext to PATH."
        ) from exc

    pages: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text(layout=True) or "")
    return "\n".join(pages)


def extract_item_rows_from_pdf(pdf_path: str | Path) -> list[ParsedInventoryItem]:
    """Extract and parse item rows directly from a PDF file."""
    return extract_item_rows_from_text(extract_pdf_text(pdf_path))


def write_item_import_csv(path: str | Path, rows: list[ParsedInventoryItem]) -> None:
    """Write item rows in the format expected by the app's CSV importer."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["name", "base_unit", "cost"])
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "name": row.name,
                    "base_unit": row.base_unit,
                    "cost": f"{row.cost:.6f}",
                }
            )


def write_duplicate_review_csv(
    path: str | Path, rows: list[DuplicateReviewRow]
) -> None:
    """Write unresolved duplicates for manual review."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["name", "base_unit", "cost", "reason"]
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "name": row.name,
                    "base_unit": row.base_unit,
                    "cost": f"{row.cost:.6f}",
                    "reason": row.reason,
                }
            )


def _find_pdftotext() -> str | None:
    candidate = shutil.which("pdftotext")
    if candidate:
        return candidate

    windows_candidate = Path(r"C:\poppler\Library\bin\pdftotext.exe")
    if windows_candidate.exists():
        return str(windows_candidate)
    return None
