from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def gl_codes():
    yield


@pytest.mark.parametrize(
    "relative_path",
    [
        "app/templates/items/recipe_calculator.html",
        "app/templates/events/stand_sheet.html",
        "app/templates/events/count_sheet.html",
        "app/templates/invoices/_invoice_form_scripts.html",
        "app/templates/purchase_orders/receive_invoice.html",
    ],
)
def test_decimal_inputs_in_templates_marked_for_numeric_handler(relative_path):
    template_path = ROOT / relative_path
    assert template_path.is_file(), f"Missing template: {relative_path}"
    content = template_path.read_text(encoding="utf-8")
    assert 'data-numeric-input="1"' in content
    assert 'inputmode="decimal"' not in content


def test_transfer_workflow_dynamic_inputs_marked():
    script_path = ROOT / "app/static/js/transfer_workflow.js"
    content = script_path.read_text(encoding="utf-8")
    assert "unitQtyInput.setAttribute('data-numeric-input', '1')" in content
    assert "baseQtyInput.setAttribute('data-numeric-input', '1')" in content
    assert "inputmode', 'decimal'" not in content


@pytest.mark.parametrize(
    "relative_path",
    [
        "app/templates/events/add_terminal_sales.html",
        "app/templates/items/item_form.html",
        "app/templates/items/view_items.html",
        "app/templates/products/view_products.html",
        "app/templates/products/_create_product_form.html",
        "app/templates/purchase_invoices/view_purchase_invoices.html",
        "app/templates/purchase_orders/recommendations.html",
    ],
)
def test_formula_templates_do_not_render_raw_number_inputs(relative_path):
    template_path = ROOT / relative_path
    assert template_path.is_file(), f"Missing template: {relative_path}"
    content = template_path.read_text(encoding="utf-8")
    assert '<input type="number"' not in content
    assert 'data-numeric-input="1"' in content


@pytest.mark.parametrize(
    "relative_path",
    [
        "app/static/js/countable_item_quick_create.js",
        "app/static/js/menu_form.js",
        "app/static/js/purchase_order_form.js",
        "app/static/js/transfer_workflow.js",
        "app/static/js/vendor_alias_resolution.js",
    ],
)
def test_dynamic_formula_inputs_do_not_create_numeric_only_keyboards(relative_path):
    script_path = ROOT / relative_path
    assert script_path.is_file(), f"Missing script: {relative_path}"
    content = script_path.read_text(encoding="utf-8")
    assert 'inputmode", "decimal"' not in content
    assert "inputmode', 'decimal'" not in content
    assert '.type = "number"' not in content
    assert ".type = 'number'" not in content
    assert '<input type="number"' not in content


def test_settings_integer_fields_opt_into_formula_handler():
    template_path = ROOT / "app/templates/admin/settings.html"
    content = template_path.read_text(encoding="utf-8")
    assert "auto_backup_interval_value(class='form-control', type='text', inputmode='text'" in content
    assert "max_backups(class='form-control', type='text', inputmode='text'" in content
