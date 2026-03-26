from app import NAV_LINKS


def test_nav_links_endpoints_registered(app):
    endpoint_names = {rule.endpoint for rule in app.url_map.iter_rules()}

    assert "menu.view_menus" in endpoint_names
    assert "event.view_events" in endpoint_names

    missing = sorted(set(NAV_LINKS) - endpoint_names)
    assert missing == []


def test_restore_endpoint_expectations_include_core_navigation(app):
    expectations = app.config["RESTORE_ENDPOINT_EXPECTATIONS"]
    configured_endpoints = {
        endpoint
        for expectation in expectations
        if expectation.get("enabled", True)
        for endpoint in expectation.get("endpoints", [])
    }

    assert "menu.view_menus" in configured_endpoints
    assert "event.view_events" in configured_endpoints
