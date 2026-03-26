from datetime import date
from secrets import token_urlsafe

from app import db
from app.models import (
    Event,
    EventLocation,
    Location,
    Product,
    TerminalSalesResolutionState,
)
from app.routes.event_routes import (
    _TERMINAL_SALES_STATE_KEY,
    _should_store_terminal_summary,
    _terminal_sales_serializer,
)


def _create_event(app):
    with app.app_context():
        event = Event(
            name="Terminal Upload",
            start_date=date.today(),
            end_date=date.today(),
        )
        prairie = Location(name="Prairie Grill")
        keystone = Location(name="Keystone Kravings")
        db.session.add_all([event, prairie, keystone])
        db.session.flush()

        prairie_el = EventLocation(event_id=event.id, location_id=prairie.id)
        keystone_el = EventLocation(event_id=event.id, location_id=keystone.id)
        db.session.add_all([prairie_el, keystone_el])
        db.session.commit()

        return event.id


def test_stale_terminal_sales_state_is_rejected_after_reset(client, app):
    event_id = _create_event(app)

    with app.app_context():
        from app.models import User

        user = User(email="test@example.com", password="", active=True)
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True

    with app.test_request_context():
        serializer = _terminal_sales_serializer()
        token_id = token_urlsafe(16)
        state_token = serializer.dumps({"event_id": event_id, "token_id": token_id})

    with client.session_transaction() as sess:
        store = dict(sess.get(_TERMINAL_SALES_STATE_KEY, {}))
        store[str(event_id)] = token_id
        sess[_TERMINAL_SALES_STATE_KEY] = store

    # Simulate clicking "Start Over", which should clear the stored token.
    client.get(f"/events/{event_id}/sales/upload")

    with client.session_transaction() as sess:
        store_after_reset = sess.get(_TERMINAL_SALES_STATE_KEY, {})
        assert str(event_id) not in store_after_reset

    stale_post = client.post(
        f"/events/{event_id}/sales/upload",
        data={
            "step": "resolve",
            "state_token": state_token,
            "payload": "{}",
            "mapping_filename": "terminal.xls",
        },
        follow_redirects=True,
    )

    page = stale_post.get_data(as_text=True)
    assert "resolution session is no longer valid" in page
    assert "Sales File" in page


def test_should_store_summary_when_totals_present():
    loc_sales = {
        "total": 0.0,
        "total_amount": 125.0,
        "net_including_tax_total": None,
        "discount_total": None,
    }

    assert _should_store_terminal_summary(loc_sales, False, []) is True


def test_should_store_summary_when_unmatched_entries_exist():
    assert _should_store_terminal_summary({}, False, [{"product_name": "Popcorn"}]) is True


def test_should_not_store_summary_when_no_data():
    assert _should_store_terminal_summary({}, False, []) is False


def test_large_queue_assign_to_menu_preserves_state(client, app):
    event_id = _create_event(app)

    with app.app_context():
        from app.models import User

        event_location = EventLocation.query.filter_by(event_id=event_id).first()
        assert event_location is not None
        user = User(email="queue@example.com", password="", active=True)
        product = Product(name="Menu Popcorn", price=5.0)
        db.session.add_all([user, product])
        db.session.commit()

        user_id = user.id
        product_id = product.id
        event_location_id = event_location.id
        token_id = token_urlsafe(16)

        queue = [
            {
                "event_location_id": event_location_id,
                "location_name": f"Prairie Grill #{idx}",
                "sales_location": f"PRAIRIE-{idx}",
                "price_issues": [],
                "menu_issues": [
                    {
                        "product_id": product_id,
                        "product_name": product.name,
                        "menu_name": "Concessions",
                        "sales_location": f"PRAIRIE-{idx}",
                        "resolution": None,
                    }
                ],
            }
            for idx in range(120)
        ]

        payload = {
            "stage": "menus",
            "queue": queue,
            "pending_sales": [],
            "pending_totals": [],
            "selected_locations": [],
            "issue_index": 0,
            "ignored_sales_locations": [],
            "selected_mapping": {},
            "menu_candidates": [],
            "menu_candidate_selection": {},
            "mapping_filename": "terminal.xls",
            "payload": {"rows": [], "filename": "terminal.xls"},
            "token_id": token_id,
        }

        db.session.add(
            TerminalSalesResolutionState(
                event_id=event_id,
                user_id=user_id,
                token_id=token_id,
                payload=payload,
            )
        )
        db.session.commit()

    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        state_store = dict(sess.get(_TERMINAL_SALES_STATE_KEY, {}))
        state_store[str(event_id)] = {"token_id": token_id}
        sess[_TERMINAL_SALES_STATE_KEY] = state_store

    with app.test_request_context():
        serializer = _terminal_sales_serializer()
        state_token = serializer.dumps({"event_id": event_id, "token_id": token_id})

    response = client.post(
        f"/events/{event_id}/sales/upload",
        data={
            "step": "resolve",
            "state_token": state_token,
            "payload": "{}",
            "mapping_filename": "terminal.xls",
            "action": f"menu:{product_id}:add",
        },
    )

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "session is no longer valid" not in page

    with app.app_context():
        stored_state = TerminalSalesResolutionState.query.filter_by(
            event_id=event_id, user_id=user_id, token_id=token_id
        ).one()
        assert len(stored_state.payload.get("queue", [])) == 120
        first_issue = stored_state.payload["queue"][0]["menu_issues"][0]
        assert first_issue.get("resolution") == "add"

    with client.session_transaction() as sess:
        persisted = sess.get(_TERMINAL_SALES_STATE_KEY, {})
        assert persisted[str(event_id)]["token_id"] == token_id
