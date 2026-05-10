from app.utils.item_pdf_import import (
    DuplicateReviewRow,
    ParsedInventoryItem,
    extract_item_rows_from_text,
    resolve_duplicate_rows,
)


def test_extract_item_rows_from_text_parses_expected_columns():
    text = """
Advanced Search - Printable View                  Number of records     3
Item Name                          Last Base Unit Price Recipe Base Unit

355mL Pepsi                                $0.561250 Each
Aluminum Foil                             $49.990000 Roll
Milk Jug - 1%                              $2.030000 L
"""

    rows = extract_item_rows_from_text(text)

    assert rows == [
        ParsedInventoryItem(
            name="355mL Pepsi", base_unit="each", cost=0.56125
        ),
        ParsedInventoryItem(
            name="Aluminum Foil", base_unit="roll", cost=49.99
        ),
        ParsedInventoryItem(name="Milk Jug - 1%", base_unit="l", cost=2.03),
    ]


def test_resolve_duplicate_rows_keeps_single_non_zero_cost():
    rows = [
        ParsedInventoryItem("473ml Farmery Pale Ale", "each", 0.0),
        ParsedInventoryItem("473ml Farmery Pale Ale", "each", 3.51),
    ]

    resolved, review = resolve_duplicate_rows(rows)

    assert resolved == [
        ParsedInventoryItem("473ml Farmery Pale Ale", "each", 3.51)
    ]
    assert review == []


def test_resolve_duplicate_rows_flags_ambiguous_duplicates():
    rows = [
        ParsedInventoryItem("Eggnog", "each", 3.99),
        ParsedInventoryItem("Eggnog", "ounce", 0.051476),
        ParsedInventoryItem("591ml Pepsi Zero", "each", 1.397917),
        ParsedInventoryItem("591ml Pepsi Zero", "each", 1.154167),
    ]

    resolved, review = resolve_duplicate_rows(rows)

    assert resolved == []
    assert review == [
        DuplicateReviewRow(
            name="591ml Pepsi Zero",
            base_unit="each",
            cost=1.154167,
            reason="duplicate name with conflicting costs",
        ),
        DuplicateReviewRow(
            name="591ml Pepsi Zero",
            base_unit="each",
            cost=1.397917,
            reason="duplicate name with conflicting costs",
        ),
        DuplicateReviewRow(
            name="Eggnog",
            base_unit="each",
            cost=3.99,
            reason="duplicate name with multiple base units",
        ),
        DuplicateReviewRow(
            name="Eggnog",
            base_unit="ounce",
            cost=0.051476,
            reason="duplicate name with multiple base units",
        ),
    ]
