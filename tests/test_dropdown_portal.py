from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_product_row_action_menu_uses_document_level_portal():
    row_template = _read("app/templates/products/_product_row.html")
    portal_script = _read("app/static/js/responsive_tables.js")
    stylesheet = _read("app/static/css/mobile-responsive.css")

    assert 'class="dropdown" data-dropdown-portal' in row_template
    assert "app-row-action-menu" in row_template
    assert 'document.addEventListener("show.bs.dropdown"' in portal_script
    assert "document.body.appendChild(menu)" in portal_script
    assert 'document.addEventListener("hidden.bs.dropdown"' in portal_script
    assert "placeholder.replaceWith(menu)" in portal_script
    assert 'document.addEventListener("keydown"' in portal_script
    assert "portalToggles.get(menu)" in portal_script
    assert ".app-row-action-menu" in stylesheet


def test_product_edit_handler_follows_portaled_menu():
    products_template = _read("app/templates/products/view_products.html")

    assert "document.addEventListener('click', function(event)" in products_template
    assert "event.target.closest('.edit-product-link')" in products_template
    assert "productTableEl.addEventListener('click'" not in products_template
