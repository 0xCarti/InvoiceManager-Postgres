from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "app" / "templates" / "events" / "printable_daily_stand_sheet.html"


def test_daily_stand_sheet_has_fixed_landscape_print_layout():
    source = TEMPLATE.read_text(encoding="utf-8")

    assert "@page" in source
    assert "size: letter landscape" in source
    assert "table-layout: fixed" in source
    assert "display: table-header-group" in source
    assert "break-inside: avoid" in source
    assert "transform: scale" not in source
    assert source.count("<col style=\"width:") == 10
    assert '<col style="width: 10%">' in source
    assert "Pre-Event<br>Count" in source


def test_daily_stand_sheet_is_static_accessible_and_printable():
    source = TEMPLATE.read_text(encoding="utf-8")

    assert "<caption" in source
    assert 'scope="col"' in source
    assert 'scope="colgroup"' in source
    assert 'data-print-daily-sheet' in source
    assert "window.print()" in source
    assert "<input" not in source
    assert "sticky_standsheet_headers.js" not in source
    assert "Undated manual event adjustments are excluded" in source
