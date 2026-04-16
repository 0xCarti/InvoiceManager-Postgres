from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def gl_codes():
    yield


def test_purchase_order_templates_expose_fast_add_actions():
    for relative_path in (
        "app/templates/purchase_orders/create_purchase_order.html",
        "app/templates/purchase_orders/edit_purchase_order.html",
    ):
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        assert content.count('data-role="purchase-order-add-item"') >= 2
        assert (
            "Name search keeps you in item entry. Barcode scans jump to Qty, then back to the next scan row."
            in content
        )


def test_purchase_order_templates_expose_vendor_sku_field():
    for relative_path in (
        "app/templates/purchase_orders/create_purchase_order.html",
        "app/templates/purchase_orders/edit_purchase_order.html",
    ):
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "Vendor SKU is optional on the PO and required when receiving." in content
        assert 'class="form-control vendor-sku-field"' in content
        assert 'placeholder="Vendor SKU"' in content


def test_purchase_order_form_script_keeps_a_ready_blank_row():
    content = (
        ROOT / "app/static/js/purchase_order_form.js"
    ).read_text(encoding="utf-8")
    assert "function ensureTrailingBlankRow()" in content
    assert "function focusReadyRow()" in content
    assert 'row.dataset.entryMode = entryMode;' in content
    assert 'currentRow.dataset.entryMode === "scan"' in content
    assert 'entryMode: "manual"' in content
    assert 'entryMode: getSelectionMode(firstOption)' in content
    assert 'event.key === "Enter"' in content
    assert "ensureTrailingBlankRow();" in content
    assert 'vendorSkuLabel.textContent = "Vendor SKU";' in content


def test_receive_invoice_template_shows_inline_deposit_field():
    content = (
        ROOT / "app/templates/purchase_orders/receive_invoice.html"
    ).read_text(encoding="utf-8")
    assert 'class="col deposit-col"' in content
    assert 'placeholder="Deposit"' in content
    assert "Add container deposit" not in content
    assert "toggle-deposit" not in content


def test_receive_invoice_template_exposes_vendor_sku_field():
    content = (
        ROOT / "app/templates/purchase_orders/receive_invoice.html"
    ).read_text(encoding="utf-8")
    assert "Vendor SKU can stay blank while the purchase order is saved" in content
    assert 'class="form-control vendor-sku"' in content
    assert 'placeholder="Vendor SKU"' in content
