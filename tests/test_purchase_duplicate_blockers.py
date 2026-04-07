from werkzeug.security import generate_password_hash

from app.models import User, Vendor
from tests.permission_helpers import make_super_admin
from tests.utils import extract_csrf_token, login


_PURCHASE_UPLOAD_SESSION_KEY = "purchase_order_upload"


def test_continue_import_blocked_when_duplicate_blockers_remain(client, app):
    with app.app_context():
        user = User(
            email="blockers@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        from app import db

        db.session.add(user)
        db.session.commit()
        make_super_admin(user)

    with client:
        login(client, "blockers@example.com", "pass")
        with client.session_transaction() as session_data:
            session_data[_PURCHASE_UPLOAD_SESSION_KEY] = {
                "vendor_id": 1,
                "items": [{"item_id": 123, "quantity": 1}],
                "duplicate_blockers": [
                    {
                        "id": "b1",
                        "category": "producer_address",
                        "row_label": "Row 1",
                    }
                ],
            }

        response = client.get("/purchase_orders/create", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["Location"].endswith(
            "/purchase_orders/resolve_vendor_items#duplicate-blockers-table"
        )


def test_continue_import_returns_blocked_rows_payload_for_json(client, app):
    with app.app_context():
        user = User(
            email="json-blockers@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        from app import db

        db.session.add(user)
        db.session.commit()
        make_super_admin(user)

    with client:
        login(client, "json-blockers@example.com", "pass")
        with client.session_transaction() as session_data:
            session_data[_PURCHASE_UPLOAD_SESSION_KEY] = {
                "vendor_id": 1,
                "items": [{"item_id": 123, "quantity": 1}],
                "duplicate_blockers": [
                    {
                        "id": "b1",
                        "row_id": "row-101",
                        "category": "producer_address",
                        "row_label": "Row 1",
                        "conflict_keys": {"producer": "Acme", "address": "1 Main"},
                    },
                    {
                        "id": "b2",
                        "row_id": "row-102",
                        "category": "duplicate_persistence",
                        "row_label": "Row 2",
                        "key_fields": {"remittance_key": "R-001"},
                    },
                    {
                        "id": "b3",
                        "row_id": "row-103",
                        "category": "staging_integrity",
                        "row_label": "Row 3",
                        "conflict_keys": {"invoice_number": "INV-9"},
                    },
                ],
            }

        response = client.get(
            "/purchase_orders/create",
            headers={"Accept": "application/json"},
            follow_redirects=False,
        )
        assert response.status_code == 409
        payload = response.get_json()
        assert payload["error"] == "Finalize preflight blocked by staging conflicts."
        assert payload["blocked_rows"][0]["destination"]["view"] == "resolve_producer_address"
        assert payload["blocked_rows"][0]["row_id"] == "row-101"
        assert payload["blocked_rows"][1]["destination"]["view"] == "duplicate_resolution"
        assert payload["blocked_rows"][1]["conflict_keys"]["remittance_key"] == "R-001"
        assert payload["blocked_rows"][2]["destination"]["view"] == "staging_cleanup"


def test_duplicate_blocker_decision_persists_before_continue(client, app):
    with app.app_context():
        user = User(
            email="resolve-blockers@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        from app import db

        db.session.add(user)
        db.session.commit()
        make_super_admin(user)

    with client:
        login(client, "resolve-blockers@example.com", "pass")
        with client.session_transaction() as session_data:
            session_data[_PURCHASE_UPLOAD_SESSION_KEY] = {
                "vendor_id": 1,
                "items": [{"item_id": 456, "quantity": 1}],
                "duplicate_blockers": [
                    {
                        "id": "b2",
                        "category": "duplicate_persistence",
                        "row_label": "Row 2",
                        "supports_merge": True,
                    }
                ],
            }

        page = client.get("/purchase_orders/resolve_vendor_items")
        csrf_token = extract_csrf_token(page, required=False)

        form_data = {
            "step": "resolve_duplicate_blocker",
            "blocker_id": "b2",
            "blocker_action": "skip_row",
        }
        if csrf_token:
            form_data["csrf_token"] = csrf_token

        post_response = client.post(
            "/purchase_orders/resolve_vendor_items",
            data=form_data,
            follow_redirects=True,
        )
        assert post_response.status_code == 200
        assert b"Blocked row skipped for this import." in post_response.data

        with client.session_transaction() as session_data:
            persisted = session_data.get(_PURCHASE_UPLOAD_SESSION_KEY) or {}
            assert persisted.get("duplicate_blockers") == []

        continue_response = client.get("/purchase_orders/create", follow_redirects=False)
        assert continue_response.status_code == 200


def test_non_blocking_duplicate_warnings_do_not_block_continue(client, app):
    with app.app_context():
        user = User(
            email="non-blocking@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        vendor = Vendor(first_name="Non", last_name="Blocking")
        from app import db

        db.session.add_all([user, vendor])
        db.session.commit()
        make_super_admin(user)
        vendor_id = vendor.id

    with client:
        login(client, "non-blocking@example.com", "pass")
        with client.session_transaction() as session_data:
            session_data[_PURCHASE_UPLOAD_SESSION_KEY] = {
                "vendor_id": vendor_id,
                "items": [{"item_id": 123, "quantity": 1}],
                "duplicate_blockers": [
                    {
                        "id": "warn-1",
                        "row_id": "row-201",
                        "category": "duplicate_persistence",
                        "row_label": "Row 201",
                        "blocks_import": False,
                    }
                ],
            }

        response = client.get("/purchase_orders/create", follow_redirects=False)
        assert response.status_code == 200


def test_resolve_page_groups_blocking_and_non_blocking_messages(client, app):
    with app.app_context():
        user = User(
            email="grouped-warnings@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        vendor = Vendor(first_name="Grouped", last_name="Warnings")
        from app import db

        db.session.add_all([user, vendor])
        db.session.commit()
        make_super_admin(user)
        vendor_id = vendor.id

    with client:
        login(client, "grouped-warnings@example.com", "pass")
        with client.session_transaction() as session_data:
            session_data[_PURCHASE_UPLOAD_SESSION_KEY] = {
                "vendor_id": vendor_id,
                "items": [
                    {
                        "item_id": None,
                        "vendor_sku": "SKU-1",
                        "vendor_description": "Unresolved item",
                        "pack_size": "1 EA",
                        "quantity": 1,
                        "unit_cost": 1.23,
                    }
                ],
                "duplicate_blockers": [
                    {
                        "id": "block-1",
                        "row_id": "row-301",
                        "category": "producer_address",
                        "row_label": "Row 301",
                    },
                    {
                        "id": "warn-1",
                        "row_id": "row-302",
                        "category": "staging_integrity",
                        "row_label": "Row 302",
                        "blocks_import": False,
                    },
                ],
            }

        response = client.get("/purchase_orders/resolve_vendor_items")
        assert response.status_code == 200
        assert b"Blocking errors" in response.data
        assert b"Blocks import" in response.data
        assert b"Non-blocking warnings" in response.data
        assert b"Does not block import" in response.data
        assert b"Row 301" in response.data
        assert b"Row 302" in response.data
