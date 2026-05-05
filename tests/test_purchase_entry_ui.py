from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def gl_codes():
    yield


def test_purchase_order_templates_expose_fast_add_actions():
    create_content = (
        ROOT / "app/templates/purchase_orders/create_purchase_order.html"
    ).read_text(encoding="utf-8")
    edit_content = (
        ROOT / "app/templates/purchase_orders/edit_purchase_order.html"
    ).read_text(encoding="utf-8")

    for content in (create_content, edit_content):
        assert content.count('data-role="purchase-order-add-item"') >= 2
        assert (
            "Name search keeps you in item entry. Barcode scans jump to Qty, then back to the next scan row."
            in content
        )

    assert "New purchase orders start as Requested." in create_content
    assert create_content.count("app-form-card") >= 2
    assert edit_content.count("app-form-card") >= 2


def test_purchase_order_templates_expose_vendor_sku_field():
    for relative_path in (
        "app/templates/purchase_orders/create_purchase_order.html",
        "app/templates/purchase_orders/edit_purchase_order.html",
    ):
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "Vendor SKU is optional on the PO and required when receiving." in content
        assert content.count('placeholder="Vendor SKU"') == 1
        assert 'class="form-control vendor-sku-field"' in content


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
    assert 'params.set("vendor_id", vendorSelect.value);' in content
    assert "preferredVendorSku" in content
    assert "preferredVendorDescription" in content
    assert "preferredPackSize" in content
    assert 'row.classList.add(\n                "row",\n                "g-3"' in content
    assert '"col-xl-4"' in content
    assert '"col-xl-2"' in content


def test_purchase_order_templates_pass_vendor_context_to_form_script():
    for relative_path in (
        "app/templates/purchase_orders/create_purchase_order.html",
        "app/templates/purchase_orders/edit_purchase_order.html",
    ):
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "vendorSelect: document.getElementById('{{ form.vendor.id }}')" in content


def test_purchase_orders_list_template_includes_upload_script_and_accessible_file_input():
    content = (
        ROOT / "app/templates/purchase_orders/view_purchase_orders.html"
    ).read_text(encoding="utf-8")
    assert "purchase_order_upload.js" in content
    assert 'id="upload-po-import-profile"' in content
    assert 'data-import-profiles=' in content
    assert 'id="upload-po-file-input"' in content
    assert "visually-hidden" in content
    assert 'accept=".csv,.xlsx,.xls"' in content


def test_purchase_order_templates_hide_equipment_intake_actions():
    for relative_path in (
        "app/templates/purchase_orders/edit_purchase_order.html",
        "app/templates/purchase_orders/view_purchase_orders.html",
    ):
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "Create Equipment Intake" not in content
        assert "equipment.create_equipment_intake_batch" not in content


def test_purchase_order_upload_script_supports_drop_fallback():
    content = (
        ROOT / "app/static/js/purchase_order_upload.js"
    ).read_text(encoding="utf-8")
    assert "pendingDroppedFile" in content
    assert "populateImportProfiles" in content
    assert "JSON.parse(rawProfiles)" in content
    assert "fileInput.showPicker" in content
    assert "new DataTransfer()" in content
    assert "window.location.assign(response.url);" in content


def test_vendor_alias_resolution_template_and_script_wire_dynamic_unit_options():
    template_content = (
        ROOT / "app/templates/purchase_orders/resolve_vendor_items.html"
    ).read_text(encoding="utf-8")
    script_content = (
        ROOT / "app/static/js/vendor_alias_resolution.js"
    ).read_text(encoding="utf-8")

    assert "data-units-map=" in template_content
    assert "alias-item-select" in template_content
    assert "alias-unit-select" in template_content
    assert "initVendorAliasResolution({" in template_content

    assert "function populateUnits(select, itemId, preferredUnitId)" in script_content
    assert "const rawUnits = unitsMap[itemId] || [];" in script_content
    assert "if (Array.isArray(unit)) {" in script_content
    assert "receiving_default: Boolean(unit[2])," in script_content
    assert "itemSelect.addEventListener('change'" in script_content
    assert "populateUnits(unitSelect, event.target.value);" in script_content


def test_receive_invoice_template_shows_inline_deposit_field():
    content = (
        ROOT / "app/templates/purchase_orders/receive_invoice.html"
    ).read_text(encoding="utf-8")
    assert 'class="col deposit-col"' in content
    assert 'placeholder="Deposit"' in content
    assert "Add container deposit" not in content
    assert "toggle-deposit" not in content
    assert "Invoice Details" in content
    assert 'class="app-metric-grid"' in content


def test_receive_invoice_template_exposes_vendor_sku_field():
    content = (
        ROOT / "app/templates/purchase_orders/receive_invoice.html"
    ).read_text(encoding="utf-8")
    assert "Vendor SKU can stay blank while the purchase order is saved" in content
    assert 'class="form-control vendor-sku"' in content
    assert 'placeholder="Vendor SKU"' in content
