from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "relative_path",
    [
        "app/templates/items/recipe_calculator.html",
        "app/templates/events/stand_sheet.html",
        "app/templates/events/count_sheet.html",
        "app/templates/invoices/create_invoice.html",
        "app/templates/invoices/view_invoices.html",
        "app/templates/purchase_orders/receive_invoice.html",
    ],
)
def test_decimal_inputs_in_templates_marked_for_numeric_handler(relative_path):
    template_path = ROOT / relative_path
    assert template_path.is_file(), f"Missing template: {relative_path}"
    content = template_path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"<input\b[^>]*inputmode\s*=\s*['\"]decimal['\"][^>]*>",
        re.IGNORECASE | re.DOTALL,
    )
    matches = pattern.findall(content)
    assert matches, f"No decimal inputs found in {relative_path}"
    for tag in matches:
        assert "data-numeric-input" in tag, f"Decimal input missing data attribute in {relative_path}: {tag}"


def test_transfer_workflow_dynamic_inputs_marked():
    script_path = ROOT / "app/static/js/transfer_workflow.js"
    content = script_path.read_text(encoding="utf-8")
    assert "unitQtyInput.setAttribute('data-numeric-input', '1')" in content
    assert "baseQtyInput.setAttribute('data-numeric-input', '1')" in content
