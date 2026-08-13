from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_topbar_uses_shared_alignment_hooks_and_keeps_accessible_controls():
    template = _read("app/templates/base.html")

    assert "filename='css/navbar_alignment.css'" in template
    assert 'class="navbar navbar-light bg-light app-topbar"' in template
    assert 'class="container-fluid app-topbar-row"' in template
    assert 'id="sidebarMenuToggle"' in template
    assert 'aria-label="Toggle navigation"' in template
    assert 'class="navbar-icon-btn app-topbar-control"' in template
    assert 'aria-label="Dashboard home"' in template
    assert 'aria-label="Toggle favorites"' in template
    assert 'class="d-none d-md-flex app-topbar-favorites"' in template
    assert '<ul class="navbar-nav flex-row me-auto">' in template


def test_topbar_styles_keep_controls_grouped_left_to_right():
    stylesheet = _read("app/static/css/navbar_alignment.css")

    assert ".app-topbar .app-topbar-row" in stylesheet
    assert "--app-topbar-control-size: 2.75rem;" in stylesheet
    assert "justify-content: flex-start;" in stylesheet
    assert ".app-topbar .app-topbar-favorites" in stylesheet
    assert ".app-topbar .app-topbar-favorites .nav-link" in stylesheet
    assert ".app-row-actions" not in stylesheet


def test_action_alignment_styles_are_scoped_to_semantic_action_groups():
    stylesheet = _read("app/static/css/ui_alignment.css")

    assert ".table-responsive td .app-row-actions" in stylesheet
    assert ".table-responsive th .app-row-actions" in stylesheet
    assert "flex-wrap: nowrap;" in stylesheet
    assert ".mobile-toolbar.app-toolbar-panel" in stylesheet
    assert ".col-user-actions > form.d-inline" in stylesheet
    assert '[class*="actions"]' not in stylesheet
    assert ".d-flex >" not in stylesheet
    assert ".app-topbar" not in stylesheet


def test_known_mixed_button_rows_use_consistent_wrappers_and_density():
    invoices = _read("app/templates/invoices/view_invoices.html")
    sales_import = _read("app/templates/admin/sales_import_detail.html")
    confirmation = _read("app/templates/confirm_action.html")
    terminal_sales = _read("app/templates/events/upload_terminal_sales.html")

    assert invoices.count('class="mobile-actions mobile-card-actions"') >= 2
    assert 'class="btn btn-outline-secondary btn-sm dropdown-toggle"' in invoices
    assert "mr-2" not in invoices
    assert 'class="btn btn-outline-secondary btn-sm">Back to Imports</a>' in sales_import
    assert 'class="app-form-actions"' in confirmation
    assert "ml-2" not in confirmation
    assert 'class="d-flex flex-wrap align-items-center gap-2 mb-3"' in terminal_sales
