from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _css_text() -> str:
    return (
        REPO_ROOT / "app/static/css/mobile-responsive.css"
    ).read_text(encoding="utf-8")


def _template_text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_shared_table_responsive_rules_allow_horizontal_scroll():
    content = _css_text()
    wrapper_block = content.split(".table-responsive {", 1)[1].split("}", 1)[0]
    table_block = content.split(".table-responsive > .table {", 1)[1].split("}", 1)[0]

    assert "overflow-x: auto;" in wrapper_block
    assert "max-width: 100%;" in wrapper_block
    assert "-webkit-overflow-scrolling: touch;" in wrapper_block
    assert "width: max-content;" in table_block
    assert "min-width: 100%;" in table_block


def test_desktop_list_table_cells_do_not_force_wrapping_globally():
    content = _css_text()
    pre_mobile_block = content.split("@media (max-width: 767.98px)", 1)[0]

    assert ".table-mobile-wrap th," not in pre_mobile_block
    assert ".table-mobile-card th," not in pre_mobile_block


def test_desktop_table_actions_keep_single_row_for_scrollable_lists():
    content = _css_text()
    desktop_block = content.split("@media (min-width: 768px) {", 1)[1].split("}", 1)[0]

    assert ".table-responsive td .mobile-actions," in desktop_block
    assert ".table-responsive th .mobile-actions" in desktop_block
    assert "flex-wrap: nowrap;" in desktop_block


def test_core_list_templates_keep_shared_scroll_wrapper():
    templates = (
        "app/templates/invoices/view_invoices.html",
        "app/templates/purchase_orders/view_purchase_orders.html",
        "app/templates/items/view_items.html",
        "app/templates/products/view_products.html",
        "app/templates/purchase_invoices/view_purchase_invoices.html",
        "app/templates/customers/view_customers.html",
        "app/templates/vendors/view_vendors.html",
    )

    for relative_path in templates:
        content = _template_text(relative_path)
        assert 'class="table-responsive mobile-table-wrap"' in content
