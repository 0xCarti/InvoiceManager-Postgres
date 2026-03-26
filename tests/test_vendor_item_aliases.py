from app import db
from app.models import Item, ItemUnit, Vendor
from app.services.purchase_imports import (
    ParsedPurchaseLine,
    normalize_vendor_alias_text,
    resolve_vendor_purchase_lines,
    update_or_create_vendor_alias,
)


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
