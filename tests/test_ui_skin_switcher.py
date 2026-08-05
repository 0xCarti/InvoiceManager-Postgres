import os

from tests.utils import login


def test_base_loads_skin_assets_without_showing_anonymous_toggle(client):
    response = client.get("/auth/login")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "js/ui_skin.js" in html
    assert "css/dispatch-board.css" in html
    assert 'data-ui-skin-enabled="false"' in html
    assert 'id="uiSkinToggle"' not in html


def test_authenticated_shell_exposes_accessible_dispatch_switch(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with client:
        login(client, admin_email, admin_pass)
        response = client.get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="uiSkinToggle"' in html
    assert 'role="switch"' in html
    assert 'aria-checked="false"' in html
    assert 'aria-label="Dispatch Board interface"' in html
    assert 'data-ui-skin-enabled="true"' in html
    assert "assetflow.uiSkin.v1.user." in html
    assert "AssetFlow dashboard home" in html


def test_dispatch_assets_are_self_hosted_and_scoped(client):
    script = client.get("/static/js/ui_skin.js")
    stylesheet = client.get("/static/css/dispatch-board.css")

    assert script.status_code == 200
    assert stylesheet.status_code == 200
    assert b"assetflow.uiSkin.v1" in script.data
    assert b'dataset.uiSkin = DISPATCH_SKIN' in script.data
    assert b'html[data-ui-skin="dispatch"]' in stylesheet.data
