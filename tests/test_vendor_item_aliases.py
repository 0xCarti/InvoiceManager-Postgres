from werkzeug.security import generate_password_hash

from app import db
from app.models import Item, ItemUnit, User, Vendor, VendorItemAlias
from app.services.purchase_imports import (
    ParsedPurchaseLine,
    normalize_vendor_alias_text,
    resolve_vendor_purchase_lines,
    update_or_create_vendor_alias,
)
from tests.permission_helpers import grant_permissions
from tests.utils import extract_csrf_token, login


def test_resolve_vendor_purchase_lines_uses_sku_alias(app):
    with app.app_context():
        vendor = Vendor(first_name="Test", last_name="Vendor")
        item = Item(name="Tomatoes", base_unit="ea", cost=0.0)
        unit = ItemUnit(item=item, name="Case", factor=1, receiving_default=True)
        db.session.add_all([vendor, item, unit])
        db.session.flush()

        alias = update_or_create_vendor_alias(
            vendor=vendor,
            item_id=item.id,
            item_unit_id=unit.id,
            vendor_sku="12345",
            vendor_description="Ripe Tomatoes",
            pack_size="10lb",
            default_cost=21.5,
        )
        alias.normalized_description = normalize_vendor_alias_text(alias.vendor_description)
        db.session.add(alias)
        db.session.commit()

        parsed_line = ParsedPurchaseLine(
            vendor_sku="12345",
            vendor_description="Ripe Tomatoes",
            pack_size=None,
            quantity=4,
            unit_cost=19.99,
        )

        resolved = resolve_vendor_purchase_lines(vendor, [parsed_line])
        assert len(resolved) == 1
        assert resolved[0].item_id == item.id
        assert resolved[0].unit_id == unit.id
        assert resolved[0].cost == parsed_line.unit_cost


def test_resolve_vendor_purchase_lines_matches_description(app):
    with app.app_context():
        vendor = Vendor(first_name="Alt", last_name="Vendor")
        item = Item(name="Mixed Nuts", base_unit="ea", cost=0.0)
        unit = ItemUnit(item=item, name="Bag", factor=1, receiving_default=True)
        db.session.add_all([vendor, item, unit])
        db.session.flush()

        alias = update_or_create_vendor_alias(
            vendor=vendor,
            item_id=item.id,
            item_unit_id=None,
            vendor_sku=None,
            vendor_description="Mixed Nuts 12oz",
            pack_size=None,
            default_cost=11.0,
        )
        alias.normalized_description = normalize_vendor_alias_text(alias.vendor_description)
        db.session.add(alias)
        db.session.commit()

        parsed_line = ParsedPurchaseLine(
            vendor_sku=None,
            vendor_description="Mixed Nuts 12oz",
            pack_size=None,
            quantity=2,
            unit_cost=None,
        )

        resolved = resolve_vendor_purchase_lines(vendor, [parsed_line])
        assert len(resolved) == 1
        assert resolved[0].item_id == item.id
        assert resolved[0].unit_id == unit.id
        assert resolved[0].cost == alias.default_cost


def test_update_vendor_alias_keeps_existing_sku_when_new_sku_is_seen(app):
    with app.app_context():
        vendor = Vendor(first_name="History", last_name="Vendor")
        item = Item(name="Roma Tomatoes", base_unit="ea", cost=0.0)
        unit = ItemUnit(item=item, name="Case", factor=1, receiving_default=True)
        db.session.add_all([vendor, item, unit])
        db.session.flush()

        first_alias = update_or_create_vendor_alias(
            vendor=vendor,
            item_id=item.id,
            item_unit_id=unit.id,
            vendor_sku="OLD-100",
            vendor_description="Roma Tomatoes 25lb",
            pack_size="25 lb",
            default_cost=21.0,
        )
        db.session.add(first_alias)
        db.session.commit()

        second_alias = update_or_create_vendor_alias(
            vendor=vendor,
            item_id=item.id,
            item_unit_id=unit.id,
            vendor_sku="NEW-200",
            vendor_description="Roma Tomatoes 25lb",
            pack_size="25 lb",
            default_cost=23.5,
        )
        db.session.add(second_alias)
        db.session.commit()

        aliases = VendorItemAlias.query.filter_by(
            vendor_id=vendor.id, item_id=item.id
        ).all()
        assert {alias.vendor_sku for alias in aliases} == {"OLD-100", "NEW-200"}
        assert (
            VendorItemAlias.query.filter_by(
                vendor_id=vendor.id,
                normalized_description=normalize_vendor_alias_text(
                    "Roma Tomatoes 25lb"
                ),
            ).count()
            == 1
        )

        resolved = resolve_vendor_purchase_lines(
            vendor,
            [
                ParsedPurchaseLine(
                    vendor_sku="NEW-200",
                    vendor_description="Roma Tomatoes 25lb",
                    pack_size="25 lb",
                    quantity=1,
                    unit_cost=None,
                ),
                ParsedPurchaseLine(
                    vendor_sku=None,
                    vendor_description="Roma Tomatoes 25lb",
                    pack_size="25 lb",
                    quantity=1,
                    unit_cost=None,
                ),
            ],
        )
        assert [line.item_id for line in resolved] == [item.id, item.id]


def test_vendor_alias_save_redirects_back_to_return_url(client, app):
    with app.app_context():
        vendor = Vendor(first_name="Route", last_name="Vendor")
        item = Item(name="Route Item", base_unit="ea", cost=0.0)
        unit = ItemUnit(
            item=item,
            name="Case",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        user = User(
            email="vendor-alias-manager@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add_all([vendor, item, unit, user])
        db.session.commit()
        grant_permissions(
            user,
            "vendor_item_aliases.manage",
            group_name="Vendor Alias Managers",
            description="Can manage vendor aliases.",
        )
        item_id = item.id
        vendor_id = vendor.id
        unit_id = unit.id

    with client:
        login(client, "vendor-alias-manager@example.com", "pass")
        response = client.get(
            f"/controlpanel/vendor-item-aliases?item_id={item_id}&next=/items/{item_id}"
        )
        token = extract_csrf_token(response)
        save = client.post(
            f"/controlpanel/vendor-item-aliases?item_id={item_id}&next=/items/{item_id}",
            data={
                "csrf_token": token,
                "return_to": f"/items/{item_id}",
                "vendor_id": vendor_id,
                "vendor_sku": "ROUTE-1",
                "vendor_description": "Route Managed Alias",
                "pack_size": "10 lb",
                "item_id": item_id,
                "item_unit_id": unit_id,
                "default_cost": "12.50",
            },
            follow_redirects=False,
        )

    assert save.status_code == 302
    assert save.headers["Location"].endswith(f"/items/{item_id}")

    with app.app_context():
        alias = VendorItemAlias.query.filter_by(
            vendor_id=vendor_id, item_id=item_id, vendor_sku="ROUTE-1"
        ).first()
        assert alias is not None
        assert alias.vendor_description == "Route Managed Alias"


def test_vendor_alias_list_filters_by_vendor_item_and_query(client, app):
    with app.app_context():
        vendor_a = Vendor(first_name="Alpha", last_name="Foods")
        vendor_b = Vendor(first_name="Beta", last_name="Foods")
        item_a = Item(name="Tomatoes", base_unit="ea", cost=0.0)
        item_b = Item(name="Onions", base_unit="ea", cost=0.0)
        user = User(
            email="vendor-alias-filter@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add_all([vendor_a, vendor_b, item_a, item_b, user])
        db.session.flush()
        db.session.add_all(
            [
                VendorItemAlias(
                    vendor_id=vendor_a.id,
                    item_id=item_a.id,
                    vendor_sku="ALPHA-1",
                    vendor_description="Roma Tomatoes",
                    normalized_description="roma tomatoes",
                    pack_size="25 lb",
                ),
                VendorItemAlias(
                    vendor_id=vendor_b.id,
                    item_id=item_b.id,
                    vendor_sku="BETA-9",
                    vendor_description="Yellow Onions",
                    normalized_description="yellow onions",
                    pack_size="10 lb",
                ),
            ]
        )
        db.session.commit()
        grant_permissions(
            user,
            "vendor_item_aliases.view",
            group_name="Vendor Alias Filter Viewers",
            description="Can view filtered vendor alias listings.",
        )
        vendor_a_id = vendor_a.id
        item_a_id = item_a.id

    with client:
        login(client, "vendor-alias-filter@example.com", "pass")
        response = client.get(
            "/controlpanel/vendor-item-aliases",
            query_string={
                "filter_vendor_id": vendor_a_id,
                "filter_item_id": item_a_id,
                "filter_query": "roma",
            },
            follow_redirects=True,
        )

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Roma Tomatoes" in page
    assert "ALPHA-1" in page
    assert "Yellow Onions" not in page
    assert "BETA-9" not in page
    assert "Showing 1 of 2 alias mappings." in page
