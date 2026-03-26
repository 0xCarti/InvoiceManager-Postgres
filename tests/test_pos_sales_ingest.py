from pathlib import Path
from decimal import Decimal

from app.models import PosSalesImport
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
