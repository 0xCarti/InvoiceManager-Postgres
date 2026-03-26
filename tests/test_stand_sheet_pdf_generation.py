from __future__ import annotations

from datetime import date, datetime

from flask import request

from app import db
from app.models import Event, EventLocation, Item, Location, Product, ProductRecipeItem
from app.routes import event_routes, location_routes
from app.services.pdf import render_stand_sheet_pdf


def test_location_stand_sheet_pdf_contains_items(app, monkeypatch):
    item_name = "PDF Lemonade"

    captured_base_url = {}
    captured_styles = {}

    class FakeHTML:
        def __init__(self, string: str, base_url: str | None = None):
            captured_base_url["base_url"] = base_url
            self.string = string

        def write_pdf(self, stream, stylesheets=None):
            stream.write(b"%PDF-FAKE\n")
            stream.write(self.string.encode())
            captured_styles["stylesheets"] = stylesheets

    class FakeCSS:
        def __init__(self, string: str):
            captured_styles["string"] = string

    monkeypatch.setattr("app.services.pdf.CSS", FakeCSS)
    monkeypatch.setattr("app.services.pdf.HTML", FakeHTML)

    with app.app_context():
        location = Location(name="PDF Location")
        product = Product(name="PDF Product", price=5.0, cost=0.0)
        item = Item(name=item_name, base_unit="each")

        db.session.add_all([location, product, item])
        db.session.flush()

        db.session.add(
            ProductRecipeItem(
                product=product,
                item=item,
                quantity=1.0,
                countable=True,
            )
        )

        location.products.append(product)
        db.session.commit()
        location_id = location.id

    with app.test_request_context("/"):
        location = db.session.get(Location, location_id)
        stand_items = location_routes._build_location_stand_sheet_items(location)

        pdf_bytes = render_stand_sheet_pdf(
            [
                (
                    "locations/stand_sheet_pdf.html",
                    {
                        "location": location,
                        "stand_items": stand_items,
                        "pdf_export": True,
                    },
                )
            ],
            base_url=request.url_root,
        )
        assert item_name.encode() in pdf_bytes
        assert captured_base_url["base_url"] == request.url_root
        assert captured_styles["string"] == "@page { size: letter landscape; }"
        assert isinstance(captured_styles["stylesheets"], list)


def test_event_stand_sheet_pdf_contains_items(app, monkeypatch):
    item_name = "Event Pretzel"
    today = date.today()

    captured_base_url = {}
    captured_styles = {}

    class FakeHTML:
        def __init__(self, string: str, base_url: str | None = None):
            captured_base_url["base_url"] = base_url
            self.string = string

        def write_pdf(self, stream, stylesheets=None):
            stream.write(b"%PDF-FAKE\n")
            stream.write(self.string.encode())
            captured_styles["stylesheets"] = stylesheets

    class FakeCSS:
        def __init__(self, string: str):
            captured_styles["string"] = string

    monkeypatch.setattr("app.services.pdf.CSS", FakeCSS)
    monkeypatch.setattr("app.services.pdf.HTML", FakeHTML)

    with app.app_context():
        location = Location(name="Event PDF Location")
        product = Product(name="Event PDF Product", price=7.0, cost=0.0)
        item = Item(name=item_name, base_unit="each")

        db.session.add_all([location, product, item])
        db.session.flush()

        db.session.add(
            ProductRecipeItem(
                product=product,
                item=item,
                quantity=1.0,
                countable=True,
            )
        )

        location.products.append(product)

        event = Event(name="PDF Event", start_date=today, end_date=today)
        event_location = EventLocation(event=event, location=location)

        db.session.add_all([event, event_location])
        db.session.commit()
        event_id = event.id

    with app.test_request_context("/"):
        event = db.session.get(Event, event_id)

        data = []
        for event_location in event.locations:
            loc, stand_items = event_routes._get_stand_items(
                event_location.location_id, event.id
            )
            data.append({"location": loc, "stand_items": stand_items})

        pdf_bytes = render_stand_sheet_pdf(
            [
                (
                    "events/bulk_stand_sheets_pdf.html",
                    {
                        "event": event,
                        "data": data,
                        "generated_at_local": datetime.now().strftime(
                            "%m/%d/%Y %I:%M %p"
                        ),
                        "pdf_export": True,
                    },
                )
            ],
            base_url=request.url_root,
        )
        assert item_name.encode() in pdf_bytes
        assert captured_base_url["base_url"] == request.url_root
        assert captured_styles["string"] == "@page { size: letter landscape; }"
        assert isinstance(captured_styles["stylesheets"], list)
