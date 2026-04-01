from pathlib import Path
from decimal import Decimal

from app.models import PosSalesImport, PosSalesImportLocation, PosSalesImportRow, db
from app.services import pos_sales_ingest
from app.services.pos_sales_ingest import ingest_pos_sales_attachment
from app.utils.pos_import import parse_terminal_sales_email_rows


def test_ingest_pos_sales_attachment_is_idempotent_for_duplicate_message_and_attachment(
    app, tmp_path
):
    spreadsheet = Path(__file__).resolve().parents[1] / "game_sales.xls"
    content = spreadsheet.read_bytes()

    with app.app_context():
        first, first_duplicate = ingest_pos_sales_attachment(
            source_provider="mailgun",
            source_message_id="<idempotent-message>",
            filename="game_sales.xls",
            content=content,
            storage_dir=tmp_path / "mailgun_staging",
        )
        second, second_duplicate = ingest_pos_sales_attachment(
            source_provider="mailgun",
            source_message_id="<idempotent-message>",
            filename="game_sales.xls",
            content=content,
            storage_dir=tmp_path / "mailgun_staging",
        )

        assert first_duplicate is False
        assert second_duplicate is True
        assert first.id == second.id
        assert PosSalesImport.query.count() == 1


def test_parse_rows_compute_unit_price_using_net_inc_plus_abs_discount():
    rows = [
        ["MAIN STAND", "", "", "", "", "", "", "", ""],
        ["Product Code", "Product Name", "", "", "Qty", "", "", "Net Inc", "Discount"],
        ["100", "Lemonade", "", "", "2", "", "", "10.50", "-1.25"],
        ["101", "Promo Water", "", "", "0", "", "", "2.00", "-0.50"],
    ]
    parsed = parse_terminal_sales_email_rows(rows)
    lemonade = parsed["MAIN STAND"]["rows"][0]
    promo = parsed["MAIN STAND"]["rows"][1]

    assert lemonade["line_total"] == Decimal("11.75")
    assert lemonade["unit_price"] == Decimal("5.875")
    assert lemonade["quantity"] == Decimal("2")

    assert promo["line_total"] == Decimal("2.5")
    assert promo["unit_price"] == Decimal("2.5")
    assert promo["quantity"] == Decimal("0")


def test_stage_pos_sales_import_handles_stock_item_sales_location_layout(
    app, monkeypatch
):
    header = [""] * 22
    header[5] = "Unit Price inc"
    header[7] = "Unit Tax"
    header[8] = "Quantity"
    header[12] = "Net inc"
    header[13] = "Discounts"

    def make_row(
        *,
        location=None,
        product_code="",
        product_name="",
        quantity="",
        net_inc="",
        discounts="",
    ):
        if location is not None:
            return [location] + [""] * 21
        row = [""] * 22
        row[0] = product_code
        row[2] = product_name
        row[8] = quantity
        row[12] = net_inc
        row[13] = discounts
        return row

    workbook_rows = [
        header,
        make_row(location="AG CENTRE"),
        make_row(
            product_code="65",
            product_name="591ml Pepsi",
            quantity="5",
            net_inc="19.18",
            discounts="3.32",
        ),
        make_row(quantity="5", net_inc="19.18", discounts="3.32"),
        make_row(location="TAP ROOM"),
        make_row(
            product_code="240",
            product_name="Apple Juice",
            quantity="2",
            net_inc="10.00",
            discounts="0.00",
        ),
        make_row(quantity="2", net_inc="10.00", discounts="0.00"),
    ]

    monkeypatch.setattr(
        pos_sales_ingest,
        "iter_pos_excel_rows",
        lambda filepath, extension: iter(workbook_rows),
    )

    with app.app_context():
        sales_import = PosSalesImport(
            source_provider="mailgun",
            message_id="<stock-item-layout>",
            attachment_filename="Stock Item Sales Location.xls",
            attachment_sha256="abc123",
            attachment_storage_path="/tmp/stock-item-sales-location.xls",
            status="pending",
        )
        db.session.add(sales_import)
        db.session.flush()

        pos_sales_ingest.stage_pos_sales_import(
            sales_import, "ignored.xls", ".xls"
        )
        db.session.commit()

        locations = (
            PosSalesImportLocation.query.filter_by(import_id=sales_import.id)
            .order_by(PosSalesImportLocation.parse_index.asc())
            .all()
        )
        rows = (
            PosSalesImportRow.query.filter_by(import_id=sales_import.id)
            .order_by(
                PosSalesImportRow.location_import_id.asc(),
                PosSalesImportRow.parse_index.asc(),
            )
            .all()
        )

        assert [location.source_location_name for location in locations] == [
            "AG CENTRE",
            "TAP ROOM",
        ]
        assert [row.source_product_name for row in rows] == [
            "591ml Pepsi",
            "Apple Juice",
        ]
        assert locations[0].total_quantity == 5.0
        assert locations[0].net_inc == 19.18
        assert locations[0].discounts_abs == 3.32
        assert locations[0].computed_total == 22.5
        assert rows[0].quantity == 5.0
        assert rows[0].computed_unit_price == 4.5
