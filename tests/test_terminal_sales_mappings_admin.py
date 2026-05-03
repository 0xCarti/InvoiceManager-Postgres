import os

from app import db
from app.models import (
    Location,
    Product,
    TerminalSaleLocationAlias,
    TerminalSaleProductAlias,
)
from tests.utils import extract_csrf_token, login


def test_location_detail_can_remove_terminal_sales_mapping_and_legacy_admin_url_redirects(
    client, app
):
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

        location_id = location.id
        location_alias_id = location_alias.id

    with client:
        login(client, admin_email, admin_pass)
        resp = client.get("/controlpanel/terminal-sales-mappings", follow_redirects=True)
        assert resp.status_code == 200
        assert (
            b"Terminal sales location mappings now live on each location page."
            in resp.data
        )

        detail_page = client.get(f"/locations/{location_id}")
        csrf_token = extract_csrf_token(detail_page)

        resp = client.post(
            f"/locations/{location_id}/terminal_sale_aliases/{location_alias_id}/delete?next=/locations/{location_id}",
            data={"csrf_token": csrf_token},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"Terminal sales location mapping removed." in resp.data

    with app.app_context():
        assert TerminalSaleProductAlias.query.count() == 1
        assert TerminalSaleLocationAlias.query.count() == 0
