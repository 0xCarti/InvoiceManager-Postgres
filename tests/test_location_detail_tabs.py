from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = REPO_ROOT / "app" / "templates" / "locations" / "view_location.html"
ITEMS_PANEL_PATH = (
    REPO_ROOT / "app" / "templates" / "locations" / "_location_items_panel.html"
)
LOCATION_ROW_PATH = (
    REPO_ROOT / "app" / "templates" / "locations" / "_location_row.html"
)
LOCATIONS_LIST_PATH = (
    REPO_ROOT / "app" / "templates" / "locations" / "view_locations.html"
)


def _template() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def test_location_detail_uses_accessible_tab_structure():
    template = _template()
    tab_pairs = [
        ("location-setup-tab", "location-setup"),
        ("location-items-tab", "location-items"),
        ("location-submissions-tab", "location-submissions"),
        ("location-transfers-tab", "location-transfers"),
        ("location-events-tab", "location-events"),
        ("location-sales-tab", "terminal-sales-mappings"),
    ]

    assert 'id="location-section-tabs"' in template
    assert 'role="tablist" aria-label="Location sections"' in template
    for tab_id, pane_id in tab_pairs:
        assert f'id="{tab_id}"' in template
        assert f'data-bs-target="#{pane_id}"' in template
        assert f'aria-controls="{pane_id}"' in template
        assert f'id="{pane_id}" role="tabpanel"' in template
        assert f'aria-labelledby="{tab_id}"' in template

    assert template.count('data-location-section-tab="1" type="button"') == len(
        tab_pairs
    )
    assert template.count('class="nav-link active"') == 1
    assert template.count('class="tab-pane fade show active"') == 1


def test_location_detail_groups_existing_sections_into_tabs():
    template = _template()
    setup = template.split('id="location-setup"', 1)[1].split(
        'id="location-items"', 1
    )[0]
    items = template.split('id="location-items"', 1)[1].split(
        'id="location-submissions"', 1
    )[0]
    submissions = template.split('id="location-submissions"', 1)[1].split(
        'id="location-transfers"', 1
    )[0]
    transfers = template.split('id="location-transfers"', 1)[1].split(
        'id="location-events"', 1
    )[0]
    events = template.split('id="location-events"', 1)[1].split(
        'id="terminal-sales-mappings"', 1
    )[0]
    sales = template.split('id="terminal-sales-mappings"', 1)[1]

    assert "Location Setup" in setup
    assert "Products At This Location" in setup
    assert "locations/_location_items_panel.html" in items
    assert "Location Items" in ITEMS_PANEL_PATH.read_text(encoding="utf-8")
    assert "Recent Location Submissions" in submissions
    assert "Recent Transfers" in transfers
    assert "Recent Events" in events
    assert "Terminal Sales Mappings" in sales
    assert "Recent Imported Sales" in sales


def test_location_detail_removes_repetitive_summary_cards():
    template = _template()

    assert '<div class="row g-3 mb-4">' not in template
    assert 'class="card' not in template
    assert template.count("Current Menu") == 1
    assert template.count("Default Playlist") == 1
    assert "Pending Submission Reviews" not in template
    assert "latest_import_at" not in template
    assert "latest_count_submission_at" not in template
    assert "pending_count_submission_count" not in template
    assert "'Archived' if location.archived else 'Active'" not in template
    assert "'Yes' if location.is_spoilage else 'No'" not in template


def test_location_tabs_support_hash_activation_and_mobile_scrolling():
    template = _template()

    assert "const hash = window.location.hash;" in template
    assert "button.getAttribute('data-bs-target') === hash" in template
    assert "bootstrap.Tab.getOrCreateInstance(targetTab).show();" in template
    assert "targetTab.scrollIntoView({ block: 'nearest', inline: 'center' });" in template
    assert "history.replaceState(null, '', target);" in template
    assert "overflow-x: auto;" in template
    assert "flex-wrap: nowrap;" in template
    assert "flex: 0 0 auto;" in template
    assert "_anchor='terminal-sales-mappings'" in template
    assert "mobile-list-page app-page-shell" in template
    assert "location-detail-actions" in template
    assert "table-mobile-card" in template


def test_location_detail_removes_redundant_header_actions():
    template = _template()
    header = template.split('<section class="location-section-shell', 1)[0]
    submissions = template.split('id="location-submissions"', 1)[1].split(
        'id="location-transfers"', 1
    )[0]

    assert ">Manage Items</a>" not in header
    assert ">Location Submissions</a>" not in header
    assert "Review Queue" in submissions


def test_location_list_item_links_open_the_embedded_items_tab():
    row_template = LOCATION_ROW_PATH.read_text(encoding="utf-8")
    list_template = LOCATIONS_LIST_PATH.read_text(encoding="utf-8")

    assert "_anchor='location-items'" in row_template
    assert '/locations/${loc.id}#location-items' in list_template
    assert '/locations/${loc.id}/items' not in list_template
