import os

from app import db
from app.models import (
    Location,
    Product,
    TerminalSaleLocationAlias,
    TerminalSaleProductAlias,
)
from tests.utils import login


def test_admin_can_remove_terminal_sales_mappings(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        product = Product(name="Hot Dog", price=5.0, cost=2.0)
        location = Location(name="Main Stand")
        db.session.add_all([product, location])
        db.session.flush()
        product_alias = TerminalSaleProductAlias(
            source_name="Hotdog",
            normalized_name="hotdog",
            product=product,
        )
        location_alias = TerminalSaleLocationAlias(
            source_name="Stand #1",
            normalized_name="stand_1",
            location=location,
        )
        db.session.add_all([product_alias, location_alias])
        db.session.commit()

        product_alias_id = product_alias.id
        location_alias_id = location_alias.id

    with client:
        login(client, admin_email, admin_pass)
        resp = client.get("/controlpanel/terminal-sales-mappings")
        assert resp.status_code == 200

        resp = client.post(
            "/controlpanel/terminal-sales-mappings",
            data={
                "product-selected_ids": [str(product_alias_id)],
                "product-delete_selected": "y",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

        resp = client.post(
            "/controlpanel/terminal-sales-mappings",
            data={
                "location-delete_all": "y",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        assert TerminalSaleProductAlias.query.count() == 0
        assert TerminalSaleLocationAlias.query.count() == 0
