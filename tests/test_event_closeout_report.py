from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import pytest
from flask import template_rendered
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Event,
    EventLocation,
    EventLocationTerminalSalesSummary,
    EventStandSheetItem,
    Item,
    ItemUnit,
    Location,
    LocationStandItem,
    Product,
    ProductRecipeItem,
    TerminalSale,
    User,
)
from tests.utils import login


@contextmanager
def captured_templates(app):
    recorded = []

    def record(sender, template, context, **extra):
        recorded.append((template, context))

    template_rendered.connect(record, app)
    try:
        yield recorded
    finally:
        template_rendered.disconnect(record, app)


def test_closed_event_report_returns_totals_and_stand_sheet_data(app, client):
    with app.app_context():
        user = User(
            email="close-report@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        location = Location(name="Main Stand")
        product = Product(name="Hot Dog", price=5.0, cost=2.0)
        item = Item(name="591ml Pepsi", base_unit="each")
        secondary_item = Item(name="Zucchini Chips", base_unit="each")
        event = Event(
            name="Sample Event",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 2),
            closed=True,
            estimated_sales=Decimal("40.00"),
        )

        db.session.add_all([user, location, product, item, secondary_item, event])
        db.session.commit()

        unit = ItemUnit(
            item_id=item.id,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        recipe = ProductRecipeItem(
            product_id=product.id,
            item_id=item.id,
            unit_id=unit.id,
            quantity=1,
            countable=True,
        )
        secondary_unit = ItemUnit(
            item_id=secondary_item.id,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        location.products.append(product)
        stand_item = LocationStandItem(
            location_id=location.id,
            item_id=item.id,
            expected_count=0,
        )
        secondary_stand_item = LocationStandItem(
            location_id=location.id,
            item_id=secondary_item.id,
            expected_count=0,
        )
        db.session.add_all(
            [unit, recipe, stand_item, secondary_unit, secondary_stand_item]
        )
        db.session.commit()

        event_location = EventLocation(
            event_id=event.id,
            location_id=location.id,
            confirmed=True,
            notes="Final stand sheet note",
        )
        db.session.add(event_location)
        db.session.commit()

        db.session.add(
            TerminalSale(
                event_location_id=event_location.id,
                product_id=product.id,
                quantity=2.0,
            )
        )

        sheet = EventStandSheetItem(
            event_location_id=event_location.id,
            item_id=item.id,
            opening_count=10,
            transferred_in=5,
            transferred_out=3,
            adjustments=0,
            eaten=1,
            spoiled=1,
            closing_count=0,
        )
        db.session.add(sheet)
        db.session.add(
            EventStandSheetItem(
                event_location_id=event_location.id,
                item_id=secondary_item.id,
                opening_count=0,
                transferred_in=0,
                transferred_out=0,
                adjustments=0,
                eaten=0,
                spoiled=0,
                closing_count=0,
            )
        )
        db.session.commit()

        event_id = event.id
        item_id = item.id
        secondary_item_id = secondary_item.id
        user_email = user.email

    with captured_templates(app) as templates:
        login_response = login(client, user_email, "pass")
        assert login_response.status_code == 200
        response = client.get(f"/events/{event_id}/close-report")
        assert response.status_code == 200

    template_matches = [
        (template, context)
        for template, context in templates
        if template.name == "events/close_report.html"
    ]
    assert template_matches, "Expected the close report template to render"
    template, context = template_matches[0]

    totals = context["totals"]
    assert totals.terminal_amount == Decimal("10.00")
    assert totals.terminal_quantity == pytest.approx(2.0)
    assert totals.physical_quantity == pytest.approx(10.0)
    assert totals.physical_amount == Decimal("50.00")
    assert context["has_stand_data"] is True

    location_report = context["locations"][0]
    assert location_report.has_sheet_data is True
    assert location_report.terminal.amount == Decimal("10.00")
    assert location_report.physical.amount == Decimal("50.00")

    stand_entry = next(
        entry
        for entry in location_report.stand_items
        if entry["item"] and entry["item"].id == item_id
    )
    assert stand_entry["sheet_values"].opening_count == pytest.approx(10.0)
    assert stand_entry["physical_units"] == pytest.approx(10.0)
    assert stand_entry["physical_amount"] == Decimal("50.00")

    secondary_entry = next(
        entry
        for entry in location_report.stand_items
        if entry["item"] and entry["item"].id == secondary_item_id
    )
    assert secondary_entry["item"].name == "Zucchini Chips"

    stand_item_names = [entry["item"].name for entry in location_report.stand_items]
    assert stand_item_names == sorted(
        stand_item_names, key=str.casefold, reverse=True
    )


def test_close_event_preserves_manual_terminal_sales(app, client):
    with app.app_context():
        location = Location(name="Manual Stand")
        product = Product(name="Manual Item", price=7.5, cost=3.0)
        event = Event(
            name="Manual Sales Event",
            start_date=date(2024, 2, 1),
            end_date=date(2024, 2, 2),
            estimated_sales=Decimal("100.00"),
        )

        db.session.add_all([location, product, event])
        db.session.commit()

        event_location = EventLocation(
            event_id=event.id,
            location_id=location.id,
            confirmed=False,
        )
        db.session.add(event_location)
        db.session.commit()

        db.session.add(
            TerminalSale(
                event_location_id=event_location.id,
                product_id=product.id,
                quantity=3.0,
            )
        )
        db.session.commit()

        user = User(
            email="eventuser@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()

        event_id = event.id
        event_location_id = event_location.id
        product_id = product.id
        user_id = user.id

    login_response = login(client, "eventuser@example.com", "pass")
    assert login_response.status_code == 200
    assert b"Invalid email or password" not in login_response.data
    with client.session_transaction() as session_data:
        session_data["_user_id"] = str(user_id)
        session_data["_fresh"] = True

    confirm_response = client.post(
        f"/events/{event_id}/locations/{event_location_id}/confirm",
        data={"submit": "Confirm", "csrf_token": ""},
        follow_redirects=False,
    )
    assert confirm_response.status_code == 302
    assert "/events" in confirm_response.headers.get("Location", "")

    with app.app_context():
        el = db.session.get(EventLocation, event_location_id)
        assert el.confirmed is True
        summary = el.terminal_sales_summary
        assert summary is not None
        assert summary.total_quantity == pytest.approx(3.0)
        assert summary.total_amount == pytest.approx(22.5)


def test_confirm_location_populates_missing_file_totals(app, client):
    with app.app_context():
        location = Location(name="Summary Stand")
        product = Product(name="Popcorn", price=7.5, cost=3.0)
        location.products.append(product)
        event = Event(
            name="Summary Event",
            start_date=date(2024, 3, 1),
            end_date=date(2024, 3, 1),
        )
        event_location = EventLocation(event=event, location=location)
        user = User(
            email="summary@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add_all([location, product, event, event_location, user])
        db.session.commit()

        db.session.add(
            TerminalSale(
                event_location_id=event_location.id,
                product_id=product.id,
                quantity=4.0,
            )
        )
        db.session.add(
            EventLocationTerminalSalesSummary(
                event_location_id=event_location.id,
                source_location="Register 1",
            )
        )
        db.session.commit()

        event_id = event.id
        event_location_id = event_location.id
        user_email = user.email

    login_response = login(client, user_email, "pass")
    assert login_response.status_code == 200
    assert b"Invalid email or password" not in login_response.data

    confirm_response = client.post(
        f"/events/{event_id}/locations/{event_location_id}/confirm",
        data={"submit": "Confirm", "csrf_token": ""},
        follow_redirects=False,
    )
    assert confirm_response.status_code == 302

    with app.app_context():
        summary = db.session.get(
            EventLocationTerminalSalesSummary, event_location_id
        )
        assert summary is not None
        assert summary.total_quantity == pytest.approx(4.0)
        assert summary.total_amount == pytest.approx(30.0)
