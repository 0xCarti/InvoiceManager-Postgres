"""Report invoice_product rows missing product links.

This maintenance script surfaces orphaned invoice rows (`product_id IS NULL`) so
operators can repair data over time. Vendor invoice report generation remains
safe because report totals now fall back to stored line pricing values.
"""

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app import create_app
from app.models import InvoiceProduct


def main() -> int:
    app, _ = create_app([])

    with app.app_context():
        orphan_rows = (
            InvoiceProduct.query.filter(InvoiceProduct.product_id.is_(None))
            .order_by(InvoiceProduct.invoice_id.asc(), InvoiceProduct.id.asc())
            .all()
        )

        if not orphan_rows:
            print("No orphaned invoice_product rows found (product_id IS NULL).")
            return 0

        print(
            f"Found {len(orphan_rows)} orphaned invoice_product row(s) with "
            "product_id IS NULL:"
        )
        for row in orphan_rows:
            print(
                "- invoice_id={invoice_id}, invoice_product_id={row_id}, "
                "product_name={name!r}, quantity={qty}, unit_price={unit_price}, "
                "line_subtotal={line_subtotal}".format(
                    invoice_id=row.invoice_id,
                    row_id=row.id,
                    name=row.product_name,
                    qty=row.quantity,
                    unit_price=row.unit_price,
                    line_subtotal=row.line_subtotal,
                )
            )

        print(
            "\nGuidance: investigate and relink these rows when possible. "
            "Report generation remains safe because vendor invoice totals use "
            "stored line_subtotal/unit_price fallbacks when product links are missing."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
