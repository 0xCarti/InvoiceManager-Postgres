from app.utils.pos_import import (
    extract_terminal_sales_location,
    terminal_sales_cell_is_blank,
)


def test_extract_location_all_blank_cells():
    row = ["PRIVATE SUITES", "", "", None, "  ", 0, 0.0]
    assert extract_terminal_sales_location(row) == "PRIVATE SUITES"


def test_extract_location_handles_whitespace_only_cells():
    row = ["Keystone Kravings", "   ", None, "\t", "", 0.0]
    assert extract_terminal_sales_location(row) == "Keystone Kravings"


def test_extract_location_treats_numeric_rows_as_data():
    row = ["799", "17oz Draft Beer - Pilsner BWK", 9.44, 1.01, 17.0]
    assert extract_terminal_sales_location(row) is None


def test_extract_location_requires_text_header():
    row = [None, "", ""]
    assert extract_terminal_sales_location(row) is None


def test_terminal_sales_cell_is_blank_treats_excel_errors_as_empty():
    assert terminal_sales_cell_is_blank("#DIV/0!")
    assert terminal_sales_cell_is_blank(" #VALUE! ")
    assert terminal_sales_cell_is_blank("#n/a")


def test_extract_location_handles_excel_error_cells():
    row = ["Prairie Grill", "#DIV/0!", None, "  "]
    assert extract_terminal_sales_location(row) == "Prairie Grill"
