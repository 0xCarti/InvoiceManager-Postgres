import json
import os
import re
import tracemalloc
import zlib
from datetime import datetime, timedelta, date
from typing import Callable
from html import unescape
from io import BytesIO
from tempfile import NamedTemporaryFile
from urllib.parse import quote

import pytest
from openpyxl import Workbook
from pypdf import PdfWriter
from pypdf.errors import PdfReadError
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from sqlalchemy import text
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Event,
    EventLocation,
    EventStandSheetItem,
    GLCode,
    Item,
    ItemUnit,
    Location,
    LocationStandItem,
    Menu,
    Product,
    ProductRecipeItem,
    TerminalSale,
    TerminalSaleLocationAlias,
    TerminalSaleProductAlias,
    User,
)
from app.routes.event_routes import (
    _calculate_physical_vs_terminal_variance,
    suggest_terminal_sales_location_mapping,
)
from app.utils.pos_import import normalize_pos_alias
from app.utils.units import DEFAULT_BASE_UNIT_CONVERSIONS, convert_quantity
from tests.utils import login


def setup_upload_env(app):
    with app.app_context():
        user = User(
            email="upload@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        east = Location(name="Popcorn East")
        west = Location(name="Popcorn West")
        prod1 = Product(name="591ml 7-Up", price=1.0, cost=0.5)
        prod2 = Product(name="Butter Topping Large", price=1.0, cost=0.5)
        db.session.add_all([user, east, west, prod1, prod2])
        db.session.commit()
        return user.email, east.id, west.id, prod1.id, prod2.id


@pytest.fixture
def sticky_bun_sales_bytes():
    wb = Workbook()
    ws = wb.active
    ws.append(["Bakery Cart"])
    ws.append(
        [
            "",
            "Sticky Bun",
            "$3.00",
            "",
            "EA",
            "$18.00",
            "",
            "$18.00",
            "",
        ]
    )
    ws.append(
        [
            "",
            "Muffin",
            "$2.50 ",
            "",
            " 12 EA",
            "$30.00",
            "",
            "$30.00",
            "",
        ]
    )
    tmp = BytesIO()
    wb.save(tmp)
    return tmp.getvalue()


def setup_event_env(app, base_unit="each"):
    with app.app_context():
        user = User(
            email="event@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        loc = Location(name="EventLoc")
        item = Item(name="EItem", base_unit=base_unit)
        product = Product(name="EProd", price=1.0, cost=0.5)
        db.session.add_all([user, loc, item, product])
        db.session.commit()
        unit_name = base_unit or "each"
        iu = ItemUnit(
            item_id=item.id,
            name=unit_name,
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        db.session.add(iu)
        db.session.add(
            LocationStandItem(
                location_id=loc.id, item_id=item.id, expected_count=10
            )
        )
        db.session.add(
            ProductRecipeItem(
                product_id=product.id,
                item_id=item.id,
                unit_id=iu.id,
                quantity=1,
                countable=True,
            )
        )
        loc.products.append(product)
        db.session.commit()
        return user.email, loc.id, product.id, item.id


def _build_inline_image_pdf(
    lines: list[str],
    include_image_terminator: bool = True,
    *,
    include_image_end_operator: bool = True,
) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=240)

    font = DictionaryObject()
    font[NameObject("/Type")] = NameObject("/Font")
    font[NameObject("/Subtype")] = NameObject("/Type1")
    font[NameObject("/BaseFont")] = NameObject("/Helvetica")
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )

    inline_bytes = b"\xff\xd8\xff\xd9" if include_image_terminator else b"\xff\xd8\xff"
    content_parts: list[str | bytes] = []
    for idx, line in enumerate(lines):
        y_position = 200 - idx * 18
        content_parts.append(f"BT /F1 12 Tf 40 {y_position} Td ({line}) Tj ET\n")
        if idx == 0:
            content_parts.append(
                "q\nBI\n/Width 1\n/Height 1\n/ColorSpace /DeviceRGB\n"
                "/BitsPerComponent 8\n/Filter /DCTDecode\nID\n"
            )
            content_parts.append(inline_bytes)
            if include_image_end_operator:
                content_parts.append("\nEI\nQ\n")

    content_bytes = b"".join(
        part if isinstance(part, bytes) else part.encode("latin1")
        for part in content_parts
    )
    stream = DecodedStreamObject()
    stream.set_data(content_bytes)
    content_ref = writer._add_object(stream)
    page[NameObject("/Contents")] = content_ref

    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _build_malicious_lzw_pdf() -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)

    stream = DecodedStreamObject()
    stream.set_data(b"\x00\x01invalid-lzw-data")
    stream[NameObject("/Filter")] = NameObject("/LZWDecode")
    content_ref = writer._add_object(stream)
    page[NameObject("/Contents")] = content_ref

    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _assert_memory_usage_below(limit_bytes: int, func: Callable[[], None]):
    tracemalloc.start()
    try:
        func()
        current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak <= limit_bytes, (
        f"Memory usage peaked at {peak} bytes while limit was {limit_bytes} bytes"
    )
def _build_malicious_flate_pdf() -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)

    stream = DecodedStreamObject()
    stream.set_data(b"\xff\xff\xffinvalid-flate")
    stream[NameObject("/Filter")] = NameObject("/FlateDecode")
    content_ref = writer._add_object(stream)
    page[NameObject("/Contents")] = content_ref

    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _prepare_upload_event(client, app, email: str, east_id: int, west_id: int, *, name="UploadPDF"):
    with client:
        login(client, email, "pass")
        client.post(
            "/events/create",
            data={
                "name": name,
                "start_date": "2025-06-20",
                "end_date": "2025-06-21",
                "event_type": "inventory",
            },
            follow_redirects=True,
        )

    with app.app_context():
        ev = Event.query.filter_by(name=name).first()
        eid = ev.id

    with client:
        login(client, email, "pass")
        client.post(
            f"/events/{eid}/add_location",
            data={"location_id": east_id},
            follow_redirects=True,
        )
        client.post(
            f"/events/{eid}/add_location",
            data={"location_id": west_id},
            follow_redirects=True,
        )

    return eid


def create_modal_product(client, name, price, **fields):
    form_data = {
        "name": name,
        "price": str(price),
        "cost": str(fields.pop("cost", price)),
        "recipe_yield_quantity": str(fields.pop("recipe_yield_quantity", 1)),
        "recipe_yield_unit": str(fields.pop("recipe_yield_unit", "")),
    }

    sales_gl_code = fields.pop("sales_gl_code", None)
    if sales_gl_code not in (None, ""):
        form_data["sales_gl_code"] = str(sales_gl_code)
    for key, value in fields.items():
        form_data[key] = str(value)
    response = client.post("/products/ajax/create", data=form_data)
    payload = response.get_json()
    assert payload and payload.get("success"), payload
    return int(payload["product"]["id"])


def test_event_lifecycle(client, app):
    email, loc_id, prod_id, item_id = setup_event_env(app)
    with client:
        login(client, email, "pass")
        client.post(
            "/events/create",
            data={
                "name": "Test Event",
                "start_date": "2023-01-01",
                "end_date": "2023-01-02",
                "event_type": "inventory",
            },
            follow_redirects=True,
        )

    with app.app_context():
        ev = Event.query.first()
        assert ev is not None
        eid = ev.id

    with client:
        login(client, email, "pass")
        client.post(
            f"/events/{eid}/add_location",
            data={
                "location_id": loc_id,
            },
            follow_redirects=True,
        )

    with app.app_context():
        el = EventLocation.query.filter_by(
            event_id=eid, location_id=loc_id
        ).first()
        assert el is not None
        elid = el.id

    with client:
        login(client, email, "pass")
        client.post(
            f"/events/{eid}/locations/{elid}/sales/add",
            data={f"qty_{prod_id}": 3},
            follow_redirects=True,
        )

    with client:
        login(client, email, "pass")
        client.post(
            f"/events/{eid}/locations/{elid}/confirm",
            data={"submit": "Confirm"},
            follow_redirects=True,
        )

    with app.app_context():
        sale = TerminalSale.query.filter_by(event_location_id=elid).first()
        assert sale is not None and sale.quantity == 3
        assert sale.sold_at is not None
        assert (datetime.utcnow() - sale.sold_at).total_seconds() < 10

    with client:
        login(client, email, "pass")
        client.get(f"/events/{eid}/close", follow_redirects=True)

    with app.app_context():
        lsi = LocationStandItem.query.filter_by(location_id=loc_id).first()
        assert lsi is None
        assert (
            TerminalSale.query.filter_by(event_location_id=elid).count() == 0
        )


def test_bulk_stand_sheet(client, app):
    email, loc_id, prod_id, item_id = setup_event_env(app, base_unit="ounce")
    with app.app_context():
        conversions = dict(DEFAULT_BASE_UNIT_CONVERSIONS)
        conversions["ounce"] = "gram"
        app.config["BASE_UNIT_CONVERSIONS"] = conversions
    with app.app_context():
        loc2 = Location(name="EventLoc2")
        db.session.add(loc2)
        db.session.commit()
        LocationStandItem(
            location_id=loc2.id,
            item_id=Item.query.first().id,
            expected_count=0,
        )
        loc2.products.append(Product.query.first())
        db.session.commit()
        loc2_id = loc2.id

    with client:
        login(client, email, "pass")
        client.post(
            "/events/create",
            data={
                "name": "BulkEvent",
                "start_date": "2023-02-01",
                "end_date": "2023-02-02",
                "event_type": "inventory",
            },
            follow_redirects=True,
        )

    with app.app_context():
        ev = Event.query.filter_by(name="BulkEvent").first()
        eid = ev.id

    with client:
        login(client, email, "pass")
        client.post(
            f"/events/{eid}/add_location",
            data={
                "location_id": loc_id,
            },
            follow_redirects=True,
        )
        client.post(
            f"/events/{eid}/add_location",
            data={
                "location_id": loc2_id,
            },
            follow_redirects=True,
        )
        resp = client.get(f"/events/{eid}/stand_sheets")
        assert resp.status_code == 200
        assert b"EventLoc" in resp.data and b"EventLoc2" in resp.data
        assert b"EItem (Gram)" in resp.data
        assert b"283.50" in resp.data
    with app.app_context():
        app.config["BASE_UNIT_CONVERSIONS"] = dict(DEFAULT_BASE_UNIT_CONVERSIONS)


def test_no_sales_after_confirmation(client, app):
    email, loc_id, prod_id, _ = setup_event_env(app)
    with client:
        login(client, email, "pass")
        client.post(
            "/events/create",
            data={
                "name": "ConfirmEvent",
                "start_date": "2023-03-01",
                "end_date": "2023-03-02",
                "event_type": "inventory",
            },
            follow_redirects=True,
        )

    with app.app_context():
        ev = Event.query.filter_by(name="ConfirmEvent").first()
        eid = ev.id

    with client:
        login(client, email, "pass")
        client.post(
            f"/events/{eid}/add_location",
            data={
                "location_id": loc_id,
            },
            follow_redirects=True,
        )

    with app.app_context():
        el = EventLocation.query.filter_by(
            event_id=eid, location_id=loc_id
        ).first()
        elid = el.id

    with client:
        login(client, email, "pass")
        client.post(
            f"/events/{eid}/locations/{elid}/confirm", follow_redirects=True
        )
        resp = client.get(f"/events/{eid}/locations/{elid}/sales/add")
        assert resp.status_code == 302


def test_undo_location_confirmation(client, app):
    email, loc_id, _, _ = setup_event_env(app)

    with client:
        login(client, email, "pass")
        client.post(
            "/events/create",
            data={
                "name": "Undo Confirmation Event",
                "start_date": "2023-05-01",
                "end_date": "2023-05-02",
                "event_type": "inventory",
            },
            follow_redirects=True,
        )

    with app.app_context():
        ev = Event.query.filter_by(name="Undo Confirmation Event").first()
        assert ev is not None
        event_id = ev.id

    with client:
        login(client, email, "pass")
        client.post(
            f"/events/{event_id}/add_location",
            data={"location_id": loc_id},
            follow_redirects=True,
        )

    with app.app_context():
        el = EventLocation.query.filter_by(
            event_id=event_id, location_id=loc_id
        ).first()
        assert el is not None
        el_id = el.id

    with client:
        login(client, email, "pass")
        client.post(
            f"/events/{event_id}/locations/{el_id}/confirm",
            data={"submit": "Confirm"},
            follow_redirects=True,
        )

    with app.app_context():
        el = db.session.get(EventLocation, el_id)
        assert el.confirmed is True

    with client:
        login(client, email, "pass")
        response = client.post(
            f"/events/{event_id}/locations/{el_id}/undo-confirm",
            data={"submit": "Undo Confirmation"},
            follow_redirects=True,
        )
        assert b"Location confirmation undone." in response.data

    with app.app_context():
        el = db.session.get(EventLocation, el_id)
        assert el.confirmed is False


def test_bulk_stand_sheets_render_multiple_pages(client, app):
    email, loc_id, prod_id, item_id = setup_event_env(app)
    with client:
        login(client, email, "pass")
        client.post(
            "/events/create",
            data={
                "name": "QR Event",
                "start_date": "2023-05-01",
                "end_date": "2023-05-02",
                "event_type": "inventory",
            },
            follow_redirects=True,
        )

    with app.app_context():
        ev = Event.query.first()
        eid = ev.id
        loc = db.session.get(Location, loc_id)
        loc_name = loc.name
        for i in range(21):
            item = Item(name=f"Extra{i}", base_unit="each")
            prod = Product(name=f"Prod{i}", price=1.0, cost=0.5)
            db.session.add_all([item, prod])
            db.session.flush()
            iu = ItemUnit(
                item_id=item.id,
                name="each",
                factor=1,
                receiving_default=True,
                transfer_default=True,
            )
            db.session.add(iu)
            db.session.add(
                LocationStandItem(
                    location_id=loc_id, item_id=item.id, expected_count=0
                )
            )
            db.session.add(
                ProductRecipeItem(
                    product_id=prod.id,
                    item_id=item.id,
                    unit_id=iu.id,
                    quantity=1,
                    countable=True,
                )
            )
            loc.products.append(prod)
        db.session.commit()

    with client:
        login(client, email, "pass")
        client.post(
            f"/events/{eid}/add_location",
            data={"location_id": loc_id},
            follow_redirects=True,
        )
        resp = client.get(f"/events/{eid}/stand_sheets")
        assert resp.status_code == 200
        assert resp.data.count(b"Opening Standsheet") == 1
        assert f"Location: {loc_name}".encode() in resp.data
        assert b"Upload Stand Sheet QR" not in resp.data


def test_save_stand_sheet(client, app):
    email, loc_id, prod_id, item_id = setup_event_env(app, base_unit="ounce")
    with app.app_context():
        conversions = dict(DEFAULT_BASE_UNIT_CONVERSIONS)
        conversions["ounce"] = "gram"
        app.config["BASE_UNIT_CONVERSIONS"] = conversions
    with client:
        login(client, email, "pass")
        client.post(
            "/events/create",
            data={
                "name": "SheetEvent",
                "start_date": "2023-03-01",
                "end_date": "2023-03-02",
                "event_type": "inventory",
            },
            follow_redirects=True,
        )

    with app.app_context():
        ev = Event.query.filter_by(name="SheetEvent").first()
        eid = ev.id

    with client:
        login(client, email, "pass")
        client.post(
            f"/events/{eid}/add_location",
            data={"location_id": loc_id},
            follow_redirects=True,
        )

    with client:
        login(client, email, "pass")
        open_report = convert_quantity(5, "ounce", "gram")
        in_report = convert_quantity(2, "ounce", "gram")
        out_report = convert_quantity(1, "ounce", "gram")
        eaten_report = convert_quantity(1, "ounce", "gram")
        close_report = convert_quantity(3, "ounce", "gram")
        client.post(
            f"/events/{eid}/stand_sheet/{loc_id}",
            data={
                f"open_{item_id}": f"{open_report:.4f}",
                f"in_{item_id}": f"{in_report:.4f}",
                f"out_{item_id}": f"{out_report:.4f}",
                f"eaten_{item_id}": f"{eaten_report:.4f}",
                f"spoiled_{item_id}": "0",
                f"close_{item_id}": f"{close_report:.4f}",
            },
            follow_redirects=True,
        )

    with app.app_context():
        el = EventLocation.query.filter_by(
            event_id=eid, location_id=loc_id
        ).first()
        sheet = EventStandSheetItem.query.filter_by(
            event_location_id=el.id, item_id=item_id
        ).first()
        assert sheet is not None
        assert sheet.opening_count == pytest.approx(5)
        assert sheet.transferred_in == pytest.approx(2)
        assert sheet.transferred_out == pytest.approx(1)
        assert sheet.eaten == pytest.approx(1)
        assert sheet.spoiled == pytest.approx(0)
        assert sheet.closing_count == pytest.approx(3)
    with app.app_context():
        app.config["BASE_UNIT_CONVERSIONS"] = dict(DEFAULT_BASE_UNIT_CONVERSIONS)


def test_terminal_sales_prefill(client, app):
    email, loc_id, prod_id, _ = setup_event_env(app)
    with client:
        login(client, email, "pass")
        client.post(
            "/events/create",
            data={
                "name": "PrefillEvent",
                "start_date": "2023-04-01",
                "end_date": "2023-04-02",
                "event_type": "inventory",
            },
            follow_redirects=True,
        )

    with app.app_context():
        ev = Event.query.filter_by(name="PrefillEvent").first()
        eid = ev.id

    with client:
        login(client, email, "pass")
        client.post(
            f"/events/{eid}/add_location",
            data={"location_id": loc_id},
            follow_redirects=True,
        )

    with app.app_context():
        el = EventLocation.query.filter_by(
            event_id=eid, location_id=loc_id
        ).first()
        elid = el.id

    with client:
        login(client, email, "pass")
        client.post(
            f"/events/{eid}/locations/{elid}/sales/add",
            data={f"qty_{prod_id}": 7},
            follow_redirects=True,
        )
        resp = client.get(f"/events/{eid}/locations/{elid}/sales/add")
        assert resp.status_code == 200
        assert b'value="7"' in resp.data or b'value="7.0"' in resp.data


def test_saving_terminal_sales_does_not_confirm_location(client, app):
    email, loc_id, prod_id, _ = setup_event_env(app)
    with client:
        login(client, email, "pass")
        client.post(
            "/events/create",
            data={
                "name": "NoConfirmSalesEvent",
                "start_date": "2023-04-05",
                "end_date": "2023-04-06",
                "event_type": "inventory",
            },
            follow_redirects=True,
        )

    with app.app_context():
        event = Event.query.filter_by(name="NoConfirmSalesEvent").first()
        assert event is not None
        eid = event.id

    with client:
        login(client, email, "pass")
        client.post(
            f"/events/{eid}/add_location",
            data={"location_id": loc_id},
            follow_redirects=True,
        )

    with app.app_context():
        el = EventLocation.query.filter_by(event_id=eid, location_id=loc_id).first()
        assert el is not None
        elid = el.id
        assert el.confirmed is False

    with client:
        login(client, email, "pass")
        client.post(
            f"/events/{eid}/locations/{elid}/sales/add",
            data={f"qty_{prod_id}": 5},
            follow_redirects=True,
        )

    with app.app_context():
        el = db.session.get(EventLocation, elid)
        assert el is not None
        assert el.confirmed is False
        sale = TerminalSale.query.filter_by(
            event_location_id=elid, product_id=prod_id
        ).first()
        assert sale is not None
        assert sale.quantity == pytest.approx(5)


def test_upload_sales_xls(client, app):
    email, east_id, west_id, prod1_id, prod2_id = setup_upload_env(app)
    with client:
        login(client, email, "pass")
        client.post(
            "/events/create",
            data={
                "name": "UploadXLS",
                "start_date": "2025-06-20",
                "end_date": "2025-06-21",
                "event_type": "inventory",
            },
            follow_redirects=True,
        )

    with app.app_context():
        ev = Event.query.filter_by(name="UploadXLS").first()
        eid = ev.id

    with client:
        login(client, email, "pass")
        client.post(
            f"/events/{eid}/add_location",
            data={"location_id": east_id},
            follow_redirects=True,
        )
        client.post(
            f"/events/{eid}/add_location",
            data={"location_id": west_id},
            follow_redirects=True,
        )

    wb = Workbook()
    ws = wb.active
    ws.append(["Popcorn East"])
    ws.append([1, "591ml 7-Up", None, None, 7])
    ws.append(["Popcorn West"])
    ws.append([1, "591ml 7-Up", None, None, 2])
    ws.append(["Pizza"])
    ws.append([1, "591ml 7-Up", None, None, 5])
    ws.append(["Grand Valley Dog"])
    ws.append([1, "591ml 7-Up", None, None, 3])
    tmp = BytesIO()
    wb.save(tmp)
    tmp.seek(0)
    data = {"file": (tmp, "sales.xls")}
    with client:
        login(client, email, "pass")
        resp = client.post(
            f"/events/{eid}/sales/upload",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        body = resp.data.decode()
        assert "Pizza" in body and "Grand Valley Dog" in body
        match = re.search(r'name="payload" value="([^"]+)"', body)
        assert match
        payload = unescape(match.group(1))

    with app.app_context():
        east_el = EventLocation.query.filter_by(
            event_id=eid, location_id=east_id
        ).first()
        west_el = EventLocation.query.filter_by(
            event_id=eid, location_id=west_id
        ).first()

    with client:
        login(client, email, "pass")
        resp = client.post(
            f"/events/{eid}/sales/upload",
            data={
                "step": "map",
                "payload": payload,
                f"mapping-{east_el.id}": "Popcorn East",
                f"mapping-{west_el.id}": "Popcorn West",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        prod1 = db.session.get(Product, prod1_id)
        sale_e = TerminalSale.query.filter_by(
            event_location_id=east_el.id, product_id=prod1.id
        ).first()
        sale_w = TerminalSale.query.filter_by(
            event_location_id=west_el.id, product_id=prod1.id
        ).first()
        assert sale_e and sale_e.quantity == 7 and sale_e.sold_at
        assert sale_w and sale_w.quantity == 2 and sale_w.sold_at


def test_upload_sales_pdf(client, app):
    email, east_id, west_id, prod1_id, prod2_id = setup_upload_env(app)
    eid = _prepare_upload_event(client, app, email, east_id, west_id)

    pdf_buf = BytesIO()
    c = canvas.Canvas(pdf_buf, pagesize=letter)
    lines = [
        "Popcorn East",
        "1 591ml 7-Up 4.00 3 7",
        "Popcorn West",
        "1 591ml 7-Up 4.00 3 2",
        "Pizza",
        "1 591ml 7-Up 4.00 3 5",
        "Grand Valley Dog",
        "1 591ml 7-Up 4.00 3 3",
    ]
    y = 750
    for line in lines:
        c.drawString(100, y, line)
        y -= 20
    c.showPage()
    c.save()
    pdf_buf.seek(0)
    data = {"file": (pdf_buf, "sales.pdf")}
    with client:
        login(client, email, "pass")
        resp = client.post(
            f"/events/{eid}/sales/upload",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200


def test_malicious_lzw_pdf_rejected_by_pdf_parser():
    import pdfplumber

    malicious_pdf = _build_malicious_lzw_pdf()

    with pytest.raises((PdfReadError, IndexError)):
        with pdfplumber.open(BytesIO(malicious_pdf)) as pdf:
            pdf.pages[0].extract_text()


def test_malicious_lzw_pdf_memory_usage_remains_bounded():
    import pdfplumber

    malicious_pdf = _build_malicious_lzw_pdf()

    def _parse_pdf():
        with pytest.raises((PdfReadError, IndexError)):
            with pdfplumber.open(BytesIO(malicious_pdf)) as pdf:
                pdf.pages[0].extract_text()

    _assert_memory_usage_below(8 * 1024 * 1024, _parse_pdf)
def test_malicious_flate_pdf_rejected_by_pdf_parser():
    import pdfplumber

    malicious_pdf = _build_malicious_flate_pdf()

    with pdfplumber.open(BytesIO(malicious_pdf)) as pdf:
        assert pdf.pages[0].extract_text() == ""


def test_upload_sales_pdf_with_inline_image(client, app):
    email, east_id, west_id, prod1_id, _prod2_id = setup_upload_env(app)
    eid = _prepare_upload_event(
        client, app, email, east_id, west_id, name="UploadInlinePDF"
    )

    pdf_bytes = _build_inline_image_pdf(
        [
            "Popcorn East",
            "1 591ml 7-Up 4.00 3 7",
            "Popcorn West",
            "1 591ml 7-Up 4.00 3 2",
        ]
    )
    data = {"file": (BytesIO(pdf_bytes), "inline.pdf")}
    with client:
        login(client, email, "pass")
        resp = client.post(
            f"/events/{eid}/sales/upload",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200

    body = resp.data.decode()
    payload_match = re.search(r'name="payload" value="([^"]+)"', body)
    assert payload_match
    payload = json.loads(unescape(payload_match.group(1)))
    rows = payload.get("rows") or []
    assert rows
    assert any(
        row.get("location") == "Popcorn East"
        and row.get("product") == "591ml 7-Up"
        and row.get("quantity") == 7
        for row in rows
    )
    assert any(
        row.get("location") == "Popcorn West"
        and row.get("product") == "591ml 7-Up"
        and row.get("quantity") == 2
        for row in rows
    )


def test_upload_sales_pdf_with_malicious_lzw_stream(client, app):
    email, east_id, west_id, _prod1_id, _prod2_id = setup_upload_env(app)
    eid = _prepare_upload_event(
        client, app, email, east_id, west_id, name="UploadLZWPDF"
    )

    malicious_pdf = _build_malicious_lzw_pdf()
    data = {"file": (BytesIO(malicious_pdf), "malicious-lzw.pdf")}
    with client:
        login(client, email, "pass")
        resp = client.post(
            f"/events/{eid}/sales/upload",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200

    assert b"No sales records were detected" in resp.data
    assert b"name=\"payload\"" not in resp.data

    with app.app_context():
        locations = EventLocation.query.filter_by(event_id=eid).all()
        location_ids = [loc.id for loc in locations]
        assert (
            TerminalSale.query.filter(
                TerminalSale.event_location_id.in_(location_ids)
            ).count()
            == 0
        )


def test_upload_sales_pdf_with_malformed_inline_image(client, app, monkeypatch):
    email, east_id, west_id, _prod1_id, _prod2_id = setup_upload_env(app)
    eid = _prepare_upload_event(
        client, app, email, east_id, west_id, name="UploadBrokenInlinePDF"
    )

    bad_pdf = _build_inline_image_pdf(
        [
            "Popcorn East",
            "1 591ml 7-Up 4.00 3 7",
            "Popcorn West",
            "1 591ml 7-Up 4.00 3 2",
        ],
        include_image_terminator=False,
        include_image_end_operator=False,
    )
    import pdfplumber

    def _raise_pdf_error(*_args, **_kwargs):
        raise ValueError("Inline image stream is malformed")

    monkeypatch.setattr(pdfplumber, "open", _raise_pdf_error)
    data = {"file": (BytesIO(bad_pdf), "inline-bad.pdf")}
    with client:
        login(client, email, "pass")
        resp = client.post(
            f"/events/{eid}/sales/upload",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200

    assert b"No sales records were detected" in resp.data
    assert b"name=\"payload\"" not in resp.data

    with app.app_context():
        locations = EventLocation.query.filter_by(event_id=eid).all()
        location_ids = [loc.id for loc in locations]
        assert (
            TerminalSale.query.filter(
                TerminalSale.event_location_id.in_(location_ids)
            ).count()
            == 0
        )


def test_upload_sales_pdf_with_malicious_flate_stream(client, app):
    email, east_id, west_id, _prod1_id, _prod2_id = setup_upload_env(app)
    eid = _prepare_upload_event(
        client, app, email, east_id, west_id, name="UploadFlatePDF"
    )

    malicious_pdf = _build_malicious_flate_pdf()
    data = {"file": (BytesIO(malicious_pdf), "malicious-flate.pdf")}
    with client:
        login(client, email, "pass")
        resp = client.post(
            f"/events/{eid}/sales/upload",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200

    assert b"No sales records were detected" in resp.data
    assert b"name=\"payload\"" not in resp.data

    with app.app_context():
        locations = EventLocation.query.filter_by(event_id=eid).all()
        location_ids = [loc.id for loc in locations]
        assert (
            TerminalSale.query.filter(
                TerminalSale.event_location_id.in_(location_ids)
            ).count()
            == 0
        )


def test_upload_sales_with_annotated_quantities(client, app, sticky_bun_sales_bytes):
    with app.app_context():
        user = User(
            email="bakery@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        location = Location(name="Bakery Cart")
        sticky = Product(name="Sticky Bun", price=3.0, cost=1.5)
        muffin = Product(name="Muffin", price=2.5, cost=1.0)
        event = Event(
            name="Bakery Day",
            start_date=date(2025, 8, 1),
            end_date=date(2025, 8, 1),
            event_type="inventory",
        )
        location.products.extend([sticky, muffin])
        db.session.add_all([user, location, sticky, muffin, event])
        event_location = EventLocation(event=event, location=location)
        db.session.add(event_location)
        db.session.commit()
        event_id = event.id
        event_location_id = event_location.id
        location_id = location.id
        user_email = user.email

    upload_stream = BytesIO(sticky_bun_sales_bytes)
    upload_stream.seek(0)
    data = {"file": (upload_stream, "sticky.xls")}
    with client:
        login(client, user_email, "pass")
        response = client.post(
            f"/events/{event_id}/sales/upload",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert response.status_code == 200
        match = re.search(r'name="payload" value="([^"]+)"', response.data.decode())
        assert match
        payload = unescape(match.group(1))

    payload_data = json.loads(payload)
    rows = payload_data.get("rows", [])
    assert len(rows) == 2
    sticky_row = next(row for row in rows if row["product"] == "Sticky Bun")
    muffin_row = next(row for row in rows if row["product"] == "Muffin")
    assert sticky_row["quantity"] == pytest.approx(6.0)
    assert muffin_row["quantity"] == pytest.approx(12.0)
    assert sticky_row.get("amount") == pytest.approx(18.0)
    assert muffin_row.get("amount") == pytest.approx(30.0)

    with client:
        login(client, user_email, "pass")
        mapping_response = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "map",
                "payload": payload,
                f"mapping-{event_location_id}": "Bakery Cart",
            },
            follow_redirects=True,
        )
        assert mapping_response.status_code == 200

    with app.app_context():
        event_location = db.session.get(EventLocation, event_location_id)
        sales = TerminalSale.query.filter_by(
            event_location_id=event_location_id
        ).all()
        assert len(sales) == 2
        quantities = {sale.product.name: sale.quantity for sale in sales}
        assert quantities["Sticky Bun"] == pytest.approx(6.0)
        assert quantities["Muffin"] == pytest.approx(12.0)
        summary = event_location.terminal_sales_summary
        assert summary is not None
        assert summary.total_quantity == pytest.approx(18.0)
        assert summary.total_amount == pytest.approx(48.0)


def test_upload_sales_manual_product_match(client, app):
    payload_rows = [
        {
            "location": "Main Bar",
            "product": "LQR - Seagrams VO Rye",
            "quantity": 9,
        }
    ]

    with app.app_context():
        user = User(
            email="fuzzy@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        location = Location(name="Main Bar")
        product = Product(name="LQR - Seagrams Rye", price=1.0, cost=0.5)
        event = Event(
            name="Fuzzy Event",
            start_date=date(2025, 7, 4),
            end_date=date(2025, 7, 5),
            event_type="inventory",
        )
        db.session.add_all([user, location, product, event])
        location.products.append(product)
        event_location = EventLocation(event=event, location=location)
        db.session.add(event_location)
        db.session.commit()

        event_id = event.id
        event_location_id = event_location.id
        product_id = product.id
        user_email = user.email

    with client:
        login(client, user_email, "pass")
        response = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "map",
                "payload": json.dumps(
                    {"rows": payload_rows, "filename": "terminal_sales.xlsx"}
                ),
                f"mapping-{event_location_id}": "Main Bar",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Match Unknown Products" in response.data

        resolution_response = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "map",
                "product-resolution-step": "1",
                "payload": json.dumps(
                    {"rows": payload_rows, "filename": "terminal_sales.xlsx"}
                ),
                f"mapping-{event_location_id}": "Main Bar",
                "product-match-0": str(product_id),
            },
            follow_redirects=True,
        )
        assert resolution_response.status_code == 200

    with app.app_context():
        sale = TerminalSale.query.filter_by(
            event_location_id=event_location_id
        ).first()
        assert sale is not None
        assert sale.product_id == product_id
        assert sale.quantity == 9

        alias = TerminalSaleProductAlias.query.filter_by(
            normalized_name="lqr seagrams vo rye"
        ).first()
        assert alias is not None
        assert alias.product_id == product_id

    payload_rows_repeat = [
        {
            "location": "Main Bar",
            "product": "LQR - Seagrams VO Rye",
            "quantity": 4,
        }
    ]

    with client:
        login(client, user_email, "pass")
        repeat_response = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "map",
                "payload": json.dumps(
                    {
                        "rows": payload_rows_repeat,
                        "filename": "terminal_sales.xlsx",
                    }
                ),
                f"mapping-{event_location_id}": "Main Bar",
            },
            follow_redirects=True,
        )
        assert repeat_response.status_code == 200

    with app.app_context():
        sale = TerminalSale.query.filter_by(
            event_location_id=event_location_id
        ).first()
        assert sale is not None
        assert sale.product_id == product_id
        assert sale.quantity == 4


def test_terminal_sales_menu_issue_requires_resolution(client, app):
    payload_rows = [
        {
            "location": "Stadium Stand",
            "product": "Nachos",
            "quantity": 5,
        }
    ]

    with app.app_context():
        user = User(
            email="menu-issue@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        location = Location(name="Stadium Stand")
        allowed_product = Product(name="Soft Pretzel", price=4.0, cost=1.5)
        new_product = Product(name="Nachos", price=6.0, cost=2.5)
        menu = Menu(name="Stadium Menu")
        menu.products.append(allowed_product)
        location.products.append(allowed_product)
        location.current_menu = menu
        event = Event(
            name="Menu Issue Event",
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 1),
            event_type="inventory",
        )
        db.session.add_all(
            [user, location, allowed_product, new_product, menu, event]
        )
        event_location = EventLocation(event=event, location=location)
        db.session.add(event_location)
        db.session.commit()

        event_id = event.id
        event_location_id = event_location.id
        location_id = location.id
        menu_id = menu.id
        new_product_id = new_product.id
        user_email = user.email

    payload = json.dumps({"rows": payload_rows, "filename": "terminal_sales.xlsx"})

    with client:
        login(client, user_email, "pass")
        map_response = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "map",
                "payload": payload,
                f"mapping-{event_location_id}": "Stadium Stand",
            },
            follow_redirects=True,
        )
        assert map_response.status_code == 200
        map_body = map_response.data.decode()
        assert "Add to menu" in map_body
        assert "This product is not on the menu" in map_body
        match = re.search(r'name="state_token" value="([^"]+)"', map_body)
        assert match
        state_token = unescape(match.group(1))

        with app.app_context():
            location_snapshot = db.session.get(Location, location_id)
            menu_snapshot = db.session.get(Menu, menu_id)
            assert location_snapshot is not None
            assert menu_snapshot is not None
            assert new_product_id not in {p.id for p in location_snapshot.products}
            assert new_product_id not in {p.id for p in menu_snapshot.products}

        add_response = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "resolve",
                "state_token": state_token,
                "payload": payload,
                "mapping_filename": "terminal_sales.xlsx",
                "action": f"menu:{new_product_id}:add",
            },
            follow_redirects=True,
        )
        assert add_response.status_code == 200
        add_body = add_response.data.decode()
        assert "Will add product to the menu" in add_body
        match = re.search(r'name="state_token" value="([^"]+)"', add_body)
        assert match
        state_token = unescape(match.group(1))

        finish_response = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "resolve",
                "state_token": state_token,
                "payload": payload,
                "mapping_filename": "terminal_sales.xlsx",
                "action": "finish",
            },
            follow_redirects=True,
        )
        assert finish_response.status_code == 200

    with app.app_context():
        refreshed_location = db.session.get(Location, location_id)
        refreshed_menu = db.session.get(Menu, menu_id)
        new_product_obj = db.session.get(Product, new_product_id)
        assert refreshed_location is not None
        assert refreshed_menu is not None
        assert new_product_obj is not None

        location_product_ids = {p.id for p in refreshed_location.products}
        assert new_product_id in location_product_ids

        menu_product_ids = {p.id for p in refreshed_menu.products}
        assert new_product_id in menu_product_ids

        sale = TerminalSale.query.filter_by(
            event_location_id=event_location_id, product_id=new_product_id
        ).one()
        assert sale.quantity == pytest.approx(5)


def test_terminal_sales_wizard_state_resume_and_new_product_menu_flow(client, app):
    payload_rows = [
        {
            "location": "Wizard Concessions",
            "product": "Arcade Pretzel",
            "quantity": 6,
        }
    ]

    with app.app_context():
        user = User(
            email="wizard-flow@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        location = Location(name="Wizard Concessions")
        allowed_product = Product(name="Butter Brew", price=5.0, cost=2.0)
        menu = Menu(name="Wizard Menu")
        menu.products.append(allowed_product)
        location.products.append(allowed_product)
        location.current_menu = menu
        event = Event(
            name="Wizard Flow Event",
            start_date=date(2026, 7, 15),
            end_date=date(2026, 7, 15),
            event_type="inventory",
        )
        db.session.add_all([user, location, allowed_product, menu, event])
        event_location = EventLocation(event=event, location=location)
        db.session.add(event_location)
        db.session.commit()

        event_id = event.id
        event_location_id = event_location.id
        location_id = location.id
        menu_id = menu.id
        user_email = user.email

    payload = json.dumps({"rows": payload_rows, "filename": "terminal_sales.xlsx"})

    with client:
        login(client, user_email, "pass")
        map_response = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "map",
                "payload": payload,
                f"mapping-{event_location_id}": "Wizard Concessions",
            },
            follow_redirects=True,
        )
        assert map_response.status_code == 200
        map_body = map_response.data.decode()
        assert "Match Unknown Products" in map_body
        token_match = re.search(r'name="state_token" value="([^"]+)"', map_body)
        assert token_match
        state_token = unescape(token_match.group(1))

        created_product_id = create_modal_product(
            client,
            name="Arcade Pretzel",
            price="6.00",
        )

        resume_response = client.get(
            f"/events/{event_id}/sales/upload?state_token={quote(state_token)}"
        )
        resume_body = resume_response.data.decode()
        assert "Match Unknown Products" in resume_body
        resume_token_match = re.search(
            r'name="state_token" value="([^"]+)"', resume_body
        )
        assert resume_token_match
        state_token = unescape(resume_token_match.group(1))

        resolution_response = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "map",
                "payload": payload,
                "stage": "products",
                "product-resolution-step": "1",
                "navigate": "finish",
                "state_token": state_token,
                f"mapping-{event_location_id}": "Wizard Concessions",
                "product-match-0": str(created_product_id),
                "created_product_ids": str(created_product_id),
            },
            follow_redirects=True,
        )
        assert resolution_response.status_code == 200
        resolution_body = resolution_response.data.decode()
        assert "Menu Availability" in resolution_body
        token_match = re.search(r'name="state_token" value="([^"]+)"', resolution_body)
        assert token_match
        state_token = unescape(token_match.group(1))

        with app.app_context():
            created_product = Product.query.filter_by(name="Arcade Pretzel").one()
            assert created_product.id == created_product_id

        assert f"menu:{created_product_id}:add" in resolution_body

        resume_menu = client.get(
            f"/events/{event_id}/sales/upload?state_token={quote(state_token)}"
        )
        assert resume_menu.status_code == 200
        resume_menu_body = resume_menu.data.decode()
        assert f"menu:{created_product_id}:add" in resume_menu_body
        resume_menu_token = re.search(
            r'name="state_token" value="([^"]+)"', resume_menu_body
        )
        assert resume_menu_token
        state_token = unescape(resume_menu_token.group(1))

        add_response = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "resolve",
                "state_token": state_token,
                "payload": payload,
                "mapping_filename": "terminal_sales.xlsx",
                "action": f"menu:{created_product_id}:add",
            },
            follow_redirects=True,
        )
        assert add_response.status_code == 200
        add_body = add_response.data.decode()
        assert "Will add product to the menu" in add_body
        token_match = re.search(r'name="state_token" value="([^"]+)"', add_body)
        assert token_match
        state_token = unescape(token_match.group(1))

        finish_response = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "resolve",
                "state_token": state_token,
                "payload": payload,
                "mapping_filename": "terminal_sales.xlsx",
                "action": "finish",
            },
            follow_redirects=True,
        )
        assert finish_response.status_code == 200

    with app.app_context():
        refreshed_location = db.session.get(Location, location_id)
        refreshed_menu = db.session.get(Menu, menu_id)
        assert refreshed_location is not None
        assert refreshed_menu is not None
        created_product = db.session.get(Product, created_product_id)
        assert created_product is not None
        assert created_product in refreshed_location.products
        assert created_product in refreshed_menu.products
        sale = TerminalSale.query.filter_by(
            event_location_id=event_location_id, product_id=created_product_id
        ).one()
        assert sale.quantity == pytest.approx(6)

def test_upload_sales_prompts_for_stale_alias(client, app):
    payload_rows = [
        {
            "location": "Main Bar",
            "product": "Alias Soda",
            "quantity": 2,
        }
    ]

    with app.app_context():
        user = User(
            email="stale-alias@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        location = Location(name="Main Bar")
        original_product = Product(name="Alias Soda Original", price=2.0, cost=1.0)
        replacement_product = Product(
            name="Alias Soda Replacement", price=2.5, cost=1.2
        )
        event = Event(
            name="Stale Alias Event",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 2),
            event_type="inventory",
        )
        db.session.add_all(
            [user, location, original_product, replacement_product, event]
        )
        location.products.append(replacement_product)
        event_location = EventLocation(event=event, location=location)
        db.session.add(event_location)
        db.session.commit()

        alias = TerminalSaleProductAlias(
            source_name="Alias Soda",
            normalized_name=normalize_pos_alias("Alias Soda"),
            product=original_product,
        )
        db.session.add(alias)
        db.session.commit()

        db.session.execute(
            text("DELETE FROM product WHERE id = :pid"),
            {"pid": original_product.id},
        )
        db.session.commit()

        stale_alias = TerminalSaleProductAlias.query.filter_by(
            normalized_name=normalize_pos_alias("Alias Soda")
        ).first()
        assert stale_alias is not None
        assert stale_alias.product is None

        event_id = event.id
        event_location_id = event_location.id
        replacement_product_id = replacement_product.id
        user_email = user.email

    with client:
        login(client, user_email, "pass")
        response = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "map",
                "payload": json.dumps(
                    {"rows": payload_rows, "filename": "terminal_sales.xlsx"}
                ),
                f"mapping-{event_location_id}": "Main Bar",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Match Unknown Products" in response.data

        resolution_response = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "map",
                "product-resolution-step": "1",
                "payload": json.dumps(
                    {"rows": payload_rows, "filename": "terminal_sales.xlsx"}
                ),
                f"mapping-{event_location_id}": "Main Bar",
                "product-match-0": str(replacement_product_id),
            },
            follow_redirects=True,
        )
        assert resolution_response.status_code == 200

    with app.app_context():
        alias = TerminalSaleProductAlias.query.filter_by(
            normalized_name=normalize_pos_alias("Alias Soda")
        ).first()
        assert alias is not None
        assert alias.product_id == replacement_product_id

        sale = TerminalSale.query.filter_by(
            event_location_id=event_location_id
        ).first()
        assert sale is not None
        assert sale.product_id == replacement_product_id
        assert sale.quantity == 2


def test_upload_sales_remembers_location_mapping(client, app):
    first_payload = [
        {
            "location": "Register 12",
            "product": "Popcorn Bucket",
            "quantity": 5,
        }
    ]

    with app.app_context():
        user = User(
            email="remember@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        location = Location(name="Main Concessions")
        product = Product(name="Popcorn Bucket", price=10.0, cost=4.0)
        location.products.append(product)
        event_one = Event(
            name="Concessions Night 1",
            start_date=date(2025, 6, 1),
            end_date=date(2025, 6, 1),
            event_type="inventory",
        )
        event_two = Event(
            name="Concessions Night 2",
            start_date=date(2025, 6, 2),
            end_date=date(2025, 6, 2),
            event_type="inventory",
        )
        db.session.add_all([user, location, product, event_one, event_two])
        first_el = EventLocation(event=event_one, location=location)
        second_el = EventLocation(event=event_two, location=location)
        db.session.add_all([first_el, second_el])
        db.session.commit()

        event_one_id = event_one.id
        event_two_location_id = second_el.id
        first_el_id = first_el.id
        location_id = location.id
        user_email = user.email

    with client:
        login(client, user_email, "pass")
        response = client.post(
            f"/events/{event_one_id}/sales/upload",
            data={
                "step": "map",
                "payload": json.dumps(
                    {"rows": first_payload, "filename": "terminal_sales.xls"}
                ),
                f"mapping-{first_el_id}": "Register 12",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

    with app.app_context():
        alias = TerminalSaleLocationAlias.query.filter_by(
            normalized_name="register 12"
        ).first()
        assert alias is not None
        assert alias.location_id == location_id

        sales_summary = {"Register 12": {}}
        mapping = suggest_terminal_sales_location_mapping(
            [db.session.get(EventLocation, event_two_location_id)], sales_summary
        )
        assert mapping[event_two_location_id] == "Register 12"


def test_upload_sales_skip_product(client, app):
    payload_rows = [
        {
            "location": "Main Bar",
            "product": "Imported Soda",
            "quantity": 3,
        }
    ]

    with app.app_context():
        user = User(
            email="skip@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        location = Location(name="Main Bar")
        event = Event(
            name="Skip Event",
            start_date=date(2025, 7, 4),
            end_date=date(2025, 7, 5),
            event_type="inventory",
        )
        db.session.add_all([user, location, event])
        event_location = EventLocation(event=event, location=location)
        db.session.add(event_location)
        db.session.commit()

        event_id = event.id
        event_location_id = event_location.id
        user_email = user.email

    with client:
        login(client, user_email, "pass")
        response = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "map",
                "payload": json.dumps(
                    {"rows": payload_rows, "filename": "terminal_sales.xlsx"}
                ),
                f"mapping-{event_location_id}": "Main Bar",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Match Unknown Products" in response.data

        skip_response = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "map",
                "product-resolution-step": "1",
                "payload": json.dumps(
                    {"rows": payload_rows, "filename": "terminal_sales.xlsx"}
                ),
                f"mapping-{event_location_id}": "Main Bar",
                "product-match-0": "__skip__",
            },
            follow_redirects=True,
        )
        assert skip_response.status_code == 200

    with app.app_context():
        sale = TerminalSale.query.filter_by(
            event_location_id=event_location_id
        ).first()
        assert sale is None


def test_upload_sales_create_product(client, app):
    payload_rows = [
        {
            "location": "Main Bar",
            "product": "Frozen Lemonade",
            "quantity": 6,
            "price": 7.5,
        }
    ]

    with app.app_context():
        user = User(
            email="create@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        location = Location(name="Main Bar")
        event = Event(
            name="Create Product Event",
            start_date=date(2025, 8, 1),
            end_date=date(2025, 8, 2),
            event_type="inventory",
        )
        db.session.add_all([user, location, event])
        event_location = EventLocation(event=event, location=location)
        db.session.add(event_location)
        db.session.commit()

        event_id = event.id
        event_location_id = event_location.id
        user_email = user.email
        location_id = location.id

    with client:
        login(client, user_email, "pass")
        response = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "map",
                "payload": json.dumps(
                    {"rows": payload_rows, "filename": "terminal_sales.xlsx"}
                ),
                f"mapping-{event_location_id}": "Main Bar",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Match Unknown Products" in response.data

        created_product_id = create_modal_product(
            client,
            name="Frozen Lemonade",
            price="7.50",
        )

        resolution_response = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "map",
                "product-resolution-step": "1",
                "payload": json.dumps(
                    {"rows": payload_rows, "filename": "terminal_sales.xlsx"}
                ),
                f"mapping-{event_location_id}": "Main Bar",
                "product-match-0": str(created_product_id),
                "created_product_ids": str(created_product_id),
            },
            follow_redirects=True,
        )
        assert resolution_response.status_code == 200

    with app.app_context():
        product = Product.query.filter_by(name="Frozen Lemonade").first()
        assert product is not None
        assert product.price == pytest.approx(7.5)

        location = db.session.get(Location, location_id)
        assert location is not None
        assert product in location.products

        sale = TerminalSale.query.filter_by(
            event_location_id=event_location_id, product_id=product.id
        ).first()
        assert sale is not None
        assert sale.quantity == 6

        alias = TerminalSaleProductAlias.query.filter_by(
            normalized_name="frozen lemonade"
        ).first()
        assert alias is not None
        assert alias.product_id == product.id


def test_upload_sales_create_product_prefers_price(client, app):
    payload_rows = [
        {
            "location": "Main Bar",
            "product": "Frozen Lemonade",
            "quantity": 6,
        },
        {
            "location": "Main Bar",
            "product": "Frozen Lemonade",
            "quantity": 2,
            "price": 8.0,
        },
    ]

    with app.app_context():
        user = User(
            email="later-price@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        location = Location(name="Main Bar")
        event = Event(
            name="Later Price Event",
            start_date=date(2025, 8, 3),
            end_date=date(2025, 8, 4),
            event_type="inventory",
        )
        db.session.add_all([user, location, event])
        event_location = EventLocation(event=event, location=location)
        db.session.add(event_location)
        db.session.commit()

        event_id = event.id
        event_location_id = event_location.id
        user_email = user.email

    with client:
        login(client, user_email, "pass")
        response = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "map",
                "payload": json.dumps(
                    {"rows": payload_rows, "filename": "terminal_sales.xlsx"}
                ),
                f"mapping-{event_location_id}": "Main Bar",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Match Unknown Products" in response.data

        created_product_id = create_modal_product(
            client,
            name="Frozen Lemonade",
            price="8.00",
        )

        resolution_response = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "map",
                "product-resolution-step": "1",
                "payload": json.dumps(
                    {"rows": payload_rows, "filename": "terminal_sales.xlsx"}
                ),
                f"mapping-{event_location_id}": "Main Bar",
                "product-match-0": str(created_product_id),
                "created_product_ids": str(created_product_id),
            },
            follow_redirects=True,
        )
        assert resolution_response.status_code == 200

    with app.app_context():
        product = Product.query.filter_by(name="Frozen Lemonade").first()
        assert product is not None
        assert product.price == pytest.approx(8.0)

        sale = TerminalSale.query.filter_by(
            event_location_id=event_location_id, product_id=product.id
        ).first()
        assert sale is not None
        assert sale.quantity == pytest.approx(8)


def test_upload_sales_create_product_uses_derived_amount_without_countable_assignment(
    client, app
):
    payload_rows = [
        {
            "location": "Main Bar",
            "product": "Frozen Lemonade",
            "quantity": 4,
            "amount": 30.0,
        }
    ]

    with app.app_context():
        user = User(
            email="derived@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        location = Location(name="Main Bar")
        event = Event(
            name="Derived Price Event",
            start_date=date(2025, 9, 1),
            end_date=date(2025, 9, 2),
            event_type="inventory",
        )
        db.session.add_all([user, location, event])
        event_location = EventLocation(event=event, location=location)
        db.session.add(event_location)
        db.session.commit()

        event_id = event.id
        event_location_id = event_location.id
        user_email = user.email

    with client:
        login(client, user_email, "pass")
        response = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "map",
                "payload": json.dumps(
                    {"rows": payload_rows, "filename": "terminal_sales.xlsx"}
                ),
                f"mapping-{event_location_id}": "Main Bar",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Match Unknown Products" in response.data

        created_product_id = create_modal_product(
            client,
            name="Frozen Lemonade",
            price="7.50",
        )

        resolution_response = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "map",
                "product-resolution-step": "1",
                "payload": json.dumps(
                    {"rows": payload_rows, "filename": "terminal_sales.xlsx"}
                ),
                f"mapping-{event_location_id}": "Main Bar",
                "product-match-0": str(created_product_id),
                "created_product_ids": str(created_product_id),
            },
            follow_redirects=True,
        )
        assert resolution_response.status_code == 200

    with app.app_context():
        product = Product.query.filter_by(name="Frozen Lemonade").first()
        assert product is not None
        assert product.price == pytest.approx(7.5)

        sale = TerminalSale.query.filter_by(
            event_location_id=event_location_id, product_id=product.id
        ).first()
        assert sale is not None
        assert sale.quantity == pytest.approx(4)


def test_modal_product_creation_includes_recipe_details(client, app):
    payload_rows = [
        {
            "location": "Main Bar",
            "product": "Frozen Lemonade Deluxe",
            "quantity": 5,
            "price": 9.25,
        }
    ]

    with app.app_context():
        user = User(
            email="modal-recipe@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        location = Location(name="Main Bar")
        event = Event(
            name="Recipe Creation Event",
            start_date=date(2025, 10, 1),
            end_date=date(2025, 10, 2),
            event_type="inventory",
        )
        item = Item(name="Lemon Mix", base_unit="bag")
        db.session.add_all([user, location, event, item])
        db.session.flush()
        item_unit = ItemUnit(item_id=item.id, name="Case", factor=6)
        db.session.add(item_unit)
        event_location = EventLocation(event=event, location=location)
        db.session.add(event_location)
        gl_code = GLCode.query.filter(GLCode.code.like("4%"))
        sales_gl_code = gl_code.order_by(GLCode.code).first()
        db.session.commit()

        event_id = event.id
        event_location_id = event_location.id
        user_email = user.email
        item_id = item.id
        item_unit_id = item_unit.id
        sales_gl_id = sales_gl_code.id if sales_gl_code else None

    with client:
        login(client, user_email, "pass")
        response = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "map",
                "payload": json.dumps(
                    {"rows": payload_rows, "filename": "terminal_sales.xlsx"}
                ),
                f"mapping-{event_location_id}": "Main Bar",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Match Unknown Products" in response.data

        created_product_id = create_modal_product(
            client,
            name="Frozen Lemonade Deluxe",
            price="9.25",
            sales_gl_code=sales_gl_id if sales_gl_id is not None else "",
            recipe_yield_quantity="4",
            recipe_yield_unit="cups",
            **{
                "items-0-item": item_id,
                "items-0-quantity": "3",
                "items-0-unit": item_unit_id,
                "items-0-countable": "y",
            },
        )

        resolution_response = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "map",
                "product-resolution-step": "1",
                "payload": json.dumps(
                    {"rows": payload_rows, "filename": "terminal_sales.xlsx"}
                ),
                f"mapping-{event_location_id}": "Main Bar",
                "product-match-0": str(created_product_id),
                "created_product_ids": str(created_product_id),
            },
            follow_redirects=True,
        )
        assert resolution_response.status_code == 200

    with app.app_context():
        product = Product.query.filter_by(name="Frozen Lemonade Deluxe").one()
        assert product.price == pytest.approx(9.25)
        if sales_gl_id is not None:
            assert product.sales_gl_code_id == sales_gl_id

        recipe = ProductRecipeItem.query.filter_by(product_id=product.id).one()
        assert recipe.item_id == item_id
        assert recipe.unit_id == item_unit_id
        assert recipe.countable is True
        assert recipe.quantity == pytest.approx(3)

        sale = TerminalSale.query.filter_by(
            event_location_id=event_location_id, product_id=product.id
        ).one()
        assert sale.quantity == pytest.approx(5)


def test_terminal_sale_last_sale(app):
    email, loc_id, prod_id, _ = setup_event_env(app)
    with app.app_context():
        loc = db.session.get(Location, loc_id)
        prod = db.session.get(Product, prod_id)
        event1 = Event(
            name="TS1",
            start_date=date(2023, 1, 1),
            end_date=date(2023, 1, 2),
            event_type="inventory",
        )
        el1 = EventLocation(event=event1, location=loc)
        sale1 = TerminalSale(
            event_location=el1,
            product=prod,
            quantity=1,
            sold_at=datetime.utcnow() - timedelta(days=1),
        )
        event2 = Event(
            name="TS2",
            start_date=date(2023, 1, 3),
            end_date=date(2023, 1, 4),
            event_type="inventory",
        )
        el2 = EventLocation(event=event2, location=loc)
        sale2 = TerminalSale(
            event_location=el2,
            product=prod,
            quantity=2,
        )
        db.session.add_all([event1, el1, sale1, event2, el2, sale2])
        db.session.commit()

        last_sale = (
            TerminalSale.query.filter_by(product_id=prod.id)
            .order_by(TerminalSale.sold_at.desc())
            .first()
        )
        assert last_sale.quantity == 2


def test_physical_terminal_variance_includes_adjustments(app):
    with app.app_context():
        event = Event(
            name="Variance Check",
            start_date=date(2023, 1, 1),
            end_date=date(2023, 1, 1),
            event_type="inventory",
        )
        location = Location(name="Prairie Grill")
        item = Item(name="Test Item", base_unit="each")
        product = Product(name="Test Product", price=5.0, cost=3.0)
        db.session.add_all([event, location, item, product])
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
        location.products.append(product)
        db.session.add_all([
            unit,
            recipe,
            LocationStandItem(
                location_id=location.id,
                item_id=item.id,
                expected_count=0,
            ),
        ])
        db.session.commit()

        event_location = EventLocation(
            event_id=event.id,
            location_id=location.id,
            confirmed=True,
        )
        db.session.add(event_location)
        db.session.commit()

        db.session.add_all(
            [
                TerminalSale(
                    event_location_id=event_location.id,
                    product_id=product.id,
                    quantity=15,
                ),
                EventStandSheetItem(
                    event_location_id=event_location.id,
                    item_id=item.id,
                    opening_count=10,
                    transferred_in=0,
                    transferred_out=0,
                    adjustments=5,
                    eaten=0,
                    spoiled=0,
                    closing_count=0,
                ),
            ]
        )
        db.session.commit()

        variance = _calculate_physical_vs_terminal_variance(event)
        assert variance == pytest.approx(0)
