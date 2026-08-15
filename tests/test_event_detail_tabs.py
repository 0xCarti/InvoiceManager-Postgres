from pathlib import Path


TEMPLATE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "templates"
    / "events"
    / "view_event.html"
)


def _template() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def _panel(template: str, panel_id: str, next_panel_id: str | None = None) -> str:
    start_marker = f'id="{panel_id}"\n                role="tabpanel"'
    start = template.index(start_marker)
    if next_panel_id is None:
        return template[start:]
    end_marker = f'id="{next_panel_id}"\n                role="tabpanel"'
    end = template.index(end_marker, start)
    return template[start:end]


def test_event_detail_has_accessible_primary_tabs_and_nested_day_tabs():
    template = _template()

    assert 'id="event-section-tabs"' in template
    assert 'aria-label="Event sections"' in template
    assert 'id="event-locations-tab"' in template
    assert 'data-bs-target="#event-locations"' in template
    assert 'aria-controls="event-locations"' in template
    assert 'id="event-documents-tab"' in template
    assert 'data-bs-target="#event-documents"' in template
    assert 'aria-controls="event-documents"' in template
    assert 'id="event-reports-tab"' in template
    assert 'data-bs-target="#event-reports"' in template
    assert 'aria-controls="event-reports"' in template

    for fixed_id in (
        "event-section-tabs",
        "event-locations-tab",
        "event-documents-tab",
        "event-reports-tab",
        "event-locations",
        "event-documents",
        "event-reports",
        "event-day-tabs",
    ):
        assert template.count(f'id="{fixed_id}"') == 1

    locations = _panel(template, "event-locations", "event-documents")
    assert template.count('class="tab-pane fade show active pt-3"') == 1
    assert 'aria-labelledby="event-locations-tab"' in locations
    assert 'id="event-day-tabs"' in locations
    assert 'aria-label="Open days"' in locations


def test_event_features_remain_in_their_relevant_primary_panel():
    template = _template()
    locations = _panel(template, "event-locations", "event-documents")
    documents = _panel(template, "event-documents", "event-reports")
    reports = _panel(template, "event-reports")

    assert "Add Location" in locations
    assert "Upload Sales" in locations
    assert "event-day-tabs" in locations
    assert "data-email-stand-sheet" not in locations
    assert 'data-event-document-form="1"' not in locations

    assert 'data-event-document-form="1"' in documents
    assert "Use current filename" in documents
    assert "download_event_document" in documents
    assert "delete_event_document_file" in documents
    assert "event-day-tabs" not in documents

    assert "Stand Sheets" in reports
    assert "data-email-stand-sheet" in reports
    assert "Closed Event Report" in reports
    assert "Count Sheet Report" in reports
    assert "Summary Source 18" in reports
    assert "Inventory Comparison" in reports


def test_event_location_names_link_to_location_details_when_permitted():
    template = _template()

    assert (
        "can_view_location = can_access_endpoint('locations.view_location', 'GET')"
        in template
    )
    assert (
        "url_for('locations.view_location', location_id=entry.location.id)"
        in template
    )
    assert (
        "url_for('locations.view_location', location_id=conflict.location_id)"
        in template
    )


def test_document_redirect_hash_activates_the_documents_tab():
    template = _template()

    assert "const hash = window.location.hash;" in template
    assert "button.getAttribute('data-bs-target') === hash" in template
    assert "bootstrap.Tab.getOrCreateInstance(targetSectionTab).show();" in template
    assert "bootstrap.Tab.getOrCreateInstance(targetDayTab).show();" in template
    assert "history.replaceState(null, '', target);" in template


def test_day_tabs_have_date_stable_ids_and_hash_activation():
    template = _template()

    assert 'id="event-day-tab-{{ day.date.isoformat() }}"' in template
    assert 'data-bs-target="#event-day-pane-{{ day.date.isoformat() }}"' in template
    assert 'id="event-day-pane-{{ day.date.isoformat() }}"' in template
    assert 'data-event-day-tab="1"' in template
    assert "const dayTabs = Array.from(" in template
    assert "const targetDayTab = dayTabs.find(function (button)" in template


def test_primary_tabs_have_mobile_layout_and_day_tabs_scroll_safely():
    template = _template()

    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in template
    assert ".event-section-tabs .nav-link" in template
    assert "min-height: 2.75rem;" in template
    assert ".event-day-tabs-scroll" in template
    assert "overflow-x: auto;" in template
    assert "flex-wrap: nowrap;" in template
