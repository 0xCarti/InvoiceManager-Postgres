from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _template_text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_new_feature_templates_keep_mobile_responsive_helpers():
    expectations = {
        "app/templates/admin/sales_imports.html": [
            "table-mobile-card",
            "sales-import-card-actions",
            "mobile-list-page app-page-shell",
        ],
        "app/templates/admin/sales_import_detail.html": [
            "sales-import-detail-page",
            "sales-import-header-actions",
        ],
        "app/templates/locations/view_location.html": [
            "table-mobile-card",
            "location-detail-actions",
            "mobile-list-page app-page-shell",
        ],
        "app/templates/communications/center.html": [
            "communications-hero-actions",
            "communications-hero-stats",
            "px-3 px-lg-0 communications-page",
        ],
        "app/templates/communications/messages.html": [
            "table-mobile-card",
            "mobile-toolbar",
            "messages-page-actions",
        ],
        "app/templates/equipment/catalog.html": [
            "table-mobile-card",
            "catalog-page-actions",
        ],
        "app/templates/equipment/view_asset.html": [
            "table-mobile-card",
            "asset-page-actions",
        ],
        "app/templates/equipment/view_intake_batch.html": [
            "table-mobile-card",
            "intake-batch-actions",
        ],
        "app/templates/schedules/team_schedule.html": [
            "schedule-page-actions",
            "schedule-filter-actions",
            "d-lg-none d-grid gap-3",
        ],
        "app/templates/schedules/template_detail.html": [
            "table-mobile-card",
            "mobile-actions mobile-card-actions",
        ],
        "app/templates/schedules/setup.html": [
            "table-mobile-card",
            "mobile-list-page app-page-shell",
        ],
        "app/templates/schedules/tradeboard.html": [
            "table-mobile-card",
            "mobile-list-page app-page-shell",
        ],
        "app/templates/schedules/availability.html": [
            "table-mobile-card",
            "mobile-list-page app-page-shell",
        ],
        "app/templates/schedules/time_off.html": [
            "table-mobile-card",
            "mobile-list-page app-page-shell",
        ],
        "app/templates/schedules/user_settings.html": [
            "table-mobile-card",
            "schedule-inline-form",
        ],
    }

    for relative_path, snippets in expectations.items():
        content = _template_text(relative_path)
        for snippet in snippets:
            assert snippet in content, f"Missing responsive helper '{snippet}' in {relative_path}"


def test_mobile_table_wrap_keeps_horizontal_scroll_enabled():
    content = _template_text("app/static/css/mobile-responsive.css")
    block = content.split(".mobile-list-page .mobile-table-wrap {", 1)[1].split("}", 1)[0]

    assert "overflow-x: auto;" in block
    assert "-webkit-overflow-scrolling: touch;" in block
    assert "overflow: hidden;" not in block


def test_core_list_templates_use_mobile_table_wrap():
    templates = (
        "app/templates/invoices/view_invoices.html",
        "app/templates/purchase_orders/view_purchase_orders.html",
        "app/templates/items/view_items.html",
        "app/templates/products/view_products.html",
        "app/templates/purchase_invoices/view_purchase_invoices.html",
    )

    for relative_path in templates:
        content = _template_text(relative_path)
        assert 'class="table-responsive mobile-table-wrap"' in content
