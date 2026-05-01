from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_menu_form_template_mentions_selected_only_toggle():
    template_path = ROOT / "app/templates/menus/edit_menu.html"
    content = template_path.read_text(encoding="utf-8")

    assert 'id="product-show-selected-toggle"' in content
    assert "Show Selected Only" in content


def test_menu_form_script_filters_selected_products():
    script_path = ROOT / "app/static/js/menu_form.js"
    content = script_path.read_text(encoding="utf-8")

    assert "var showSelectedOnly = false;" in content
    assert "product-show-selected-toggle" in content
    assert "var matchesSelection = !showSelectedOnly || option.selected;" in content
    assert 'productSelect.addEventListener("change", applyProductFilter);' in content
