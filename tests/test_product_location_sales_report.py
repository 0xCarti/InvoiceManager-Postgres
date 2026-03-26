from datetime import date, datetime

from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Customer,
    Event,
    EventLocation,
    Invoice,
    InvoiceProduct,
    Location,
    Product,
    TerminalSale,
    User,
)
from tests.utils import login


def setup_data(app):
    with app.app_context():
        user = User(
            email="plr@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        customer = Customer(first_name="Jane", last_name="Doe")
        p1 = Product(name="Prod1", price=10.0, cost=5.0)
        p2 = Product(name="Prod2", price=8.0, cost=4.0)
        p3 = Product(name="Prod3", price=5.0, cost=2.0)
        loc1 = Location(name="LocA")
        loc2 = Location(name="LocB")
        db.session.add_all([user, customer, p1, p2, p3, loc1, loc2])
        db.session.commit()

        invoice = Invoice(
            id="INV001",
            user_id=user.id,
            customer_id=customer.id,
            date_created=date(2023, 1, 5),
        )
        db.session.add(invoice)
        db.session.commit()
        db.session.add(
            InvoiceProduct(
                invoice_id=invoice.id,
                quantity=3,
                product_id=p1.id,
                product_name=p1.name,
                unit_price=p1.price,
                line_subtotal=30,
                line_gst=0,
                line_pst=0,
            )
        )
        db.session.commit()

        event = Event(name="Ev", start_date=date(2023, 1, 1), end_date=date(2023, 1, 10))
        db.session.add(event)
        db.session.commit()
        el1 = EventLocation(event_id=event.id, location_id=loc1.id)
        el2 = EventLocation(event_id=event.id, location_id=loc2.id)
        db.session.add_all([el1, el2])
        db.session.commit()

        db.session.add_all(
            [
                TerminalSale(
                    event_location_id=el1.id,
                    product_id=p1.id,
                    quantity=5,
                    sold_at=datetime(2023, 1, 7, 12, 0),
                ),
                TerminalSale(
                    event_location_id=el2.id,
                    product_id=p1.id,
                    quantity=2,
                    sold_at=datetime(2023, 1, 8, 12, 0),
                ),
                TerminalSale(
                    event_location_id=el1.id,
                    product_id=p2.id,
                    quantity=4,
                    sold_at=datetime(2023, 1, 9, 12, 0),
                ),
            ]
        )
        db.session.commit()

        return user.email


def test_product_location_sales_report(client, app):
    email = setup_data(app)
    with client:
        login(client, email, "pass")
        resp = client.post(
            "/reports/product-location-sales",
            data={"start_date": "2023-01-01", "end_date": "2023-01-31"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"Prod1" in resp.data
        assert b"Prod2" in resp.data
        assert b"Prod3" not in resp.data
        assert b"LocA" in resp.data
        assert b"LocB" in resp.data
