import io
from pathlib import Path

import pytest
from werkzeug.datastructures import FileStorage

from app.models import Vendor
from app.services.purchase_imports import (
    CSVImportError,
    ParsedPurchaseLine,
    parse_purchase_order_csv,
)


def _make_pratts_file(csv_text: str) -> FileStorage:
    return FileStorage(stream=io.BytesIO(csv_text.encode()), filename="pratts.csv")


def _make_pratts_vendor() -> Vendor:
    return Vendor(first_name="Pratt", last_name="Supplies")


def _make_central_supply_file(csv_text: str) -> FileStorage:
    return FileStorage(stream=io.BytesIO(csv_text.encode()), filename="central.csv")


def _make_central_supply_vendor() -> Vendor:
    return Vendor(first_name="Central", last_name="Supply")


def test_parse_pratts_csv_success():
    csv_text = """Item,Pack,Size,Brand,Description,Quantity Shipped,Unit Price,Extended Price,PO Number
1001,1,12 oz,BrandA,First Item,4,2.50,10.00,PO-123
1002,2,6 ct,BrandB,,3,1.00,3.00,PO-123
"""
    parsed = parse_purchase_order_csv(_make_pratts_file(csv_text), _make_pratts_vendor())

    assert len(parsed.items) == 2
    assert parsed.order_number == "PO-123"
    assert parsed.expected_total == 13

    first: ParsedPurchaseLine = parsed.items[0]
    assert first.vendor_sku == "1001"
    assert first.vendor_description == "First Item"
    assert first.pack_size == "1 12 oz"
    assert first.quantity == 4
    assert first.unit_cost == 2.5

    second: ParsedPurchaseLine = parsed.items[1]
    assert second.vendor_sku == "1002"
    assert second.vendor_description == "1002"
    assert second.pack_size == "2 6 ct"
    assert second.quantity == 3
    assert second.unit_cost == 1


def test_parse_pratts_csv_missing_headers():
    csv_text = """Item,Size,Brand,Description,Qty Ship,Price,Ext Price
1001,1,BrandA,First Item,4,2.50,10.00
"""

    with pytest.raises(CSVImportError) as excinfo:
        parse_purchase_order_csv(_make_pratts_file(csv_text), _make_pratts_vendor())

    assert "Missing required Pratts columns" in str(excinfo.value)
    assert "pack" in str(excinfo.value)


def test_parse_pratts_csv_invalid_quantities():
    csv_text = """Item,Pack,Size,Brand,Description,Qty Ship,Price,Ext Price
1001,1,12 oz,BrandA,First Item,0,2.50,0.00
1002,2,6 ct,BrandB,Second Item,,1.00,0.00
1003,3,5 lb,BrandC,Third Item,-1,3.00,-3.00
"""

    with pytest.raises(CSVImportError) as excinfo:
        parse_purchase_order_csv(_make_pratts_file(csv_text), _make_pratts_vendor())

    assert "No purchasable lines found" in str(excinfo.value)


def test_parse_central_supply_csv_success():
    fixture_path = Path(__file__).parent / "fixtures" / "central_supply_sample.csv"
    parsed = parse_purchase_order_csv(
        _make_central_supply_file(fixture_path.read_text()),
        _make_central_supply_vendor(),
    )

    assert parsed.order_number == "ORD-9001"
    assert parsed.expected_total == 25.0

    quantities = [line.quantity for line in parsed.items]
    assert quantities == [2, 5, 3]

    descriptions = {line.vendor_description for line in parsed.items}
    assert descriptions == {"Alpha Item", "Beta Item", "Gamma Item"}

    pack_sizes = {line.pack_size for line in parsed.items}
    assert pack_sizes == {"1/12 oz", "2/6 ct", "1/1 lb"}


def test_parse_central_supply_missing_headers():
    csv_text = """Vendor SKU,Item Description,Order Qty,Extended Price
CS-001,Alpha Item,2,7.00
"""

    with pytest.raises(CSVImportError) as excinfo:
        parse_purchase_order_csv(
            _make_central_supply_file(csv_text), _make_central_supply_vendor()
        )

    assert "Missing required Central Supply columns" in str(excinfo.value)
    assert "price" in str(excinfo.value)


def test_parse_central_supply_ignores_invalid_quantities():
    csv_text = """Vendor SKU,Item Description,Order Qty,Unit Price,Extended Price
CS-001,Alpha Item,0,3.50,0.00
CS-002,Beta Item,,1.20,0.00
CS-003,Gamma Item,-1,4.00,-4.00
CS-004,Delta Item,4,2.00,8.00
"""

    parsed = parse_purchase_order_csv(
        _make_central_supply_file(csv_text), _make_central_supply_vendor()
    )

    assert len(parsed.items) == 1
    only_item: ParsedPurchaseLine = parsed.items[0]
    assert only_item.vendor_sku == "CS-004"
    assert only_item.quantity == 4
    assert parsed.expected_total == 8.0
