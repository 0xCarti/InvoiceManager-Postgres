"""Helpers for product sellable amount pricing and snapshots."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Iterable

from app import db
from app.models import Product, ProductSellableAmount
from app.utils.numeric import coerce_float


def _to_decimal(value, default: str = "0.00") -> Decimal:
    try:
        if value is None or value == "":
            return Decimal(default)
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def amount_price_float(amount: ProductSellableAmount | None) -> float:
    if amount is None:
        return 0.0
    return float(amount.price or 0.0)


def amount_quantity_float(amount: ProductSellableAmount | None) -> float:
    if amount is None:
        return 1.0
    quantity = coerce_float(amount.quantity, default=1.0) or 1.0
    return quantity if quantity > 0 else 1.0


def product_default_sellable_amount(
    product: Product | None,
) -> ProductSellableAmount | None:
    if product is None:
        return None
    return product.default_sellable_amount


def ensure_default_sellable_amount(
    product: Product,
    *,
    price=None,
    name: str = "Each",
) -> ProductSellableAmount:
    amount = product_default_sellable_amount(product)
    if amount is not None:
        amount.active = True
        amount.is_default = True
        return amount

    amount = ProductSellableAmount(
        product=product,
        name=name or "Each",
        quantity=1.0,
        price=_to_decimal(price if price is not None else product.price),
        active=True,
        is_default=True,
        position=0,
    )
    db.session.add(amount)
    product.price = amount.price_float
    product.invoice_sale_price = amount.price
    return amount


def find_sellable_amount_for_price(
    product: Product | None,
    price,
    *,
    tolerance: float = 0.01,
) -> ProductSellableAmount | None:
    if product is None:
        return None
    target = coerce_float(price)
    if target is None:
        return product_default_sellable_amount(product)
    for amount in product.active_sellable_amounts:
        if abs(amount_price_float(amount) - target) <= tolerance:
            return amount
    return None


def choose_sellable_amount_for_price(
    product: Product | None,
    price,
) -> ProductSellableAmount | None:
    matched = find_sellable_amount_for_price(product, price)
    if matched is not None:
        return matched
    return product_default_sellable_amount(product)


def update_default_sellable_price(product: Product, price) -> ProductSellableAmount:
    amount = ensure_default_sellable_amount(product, price=price)
    amount.price = _to_decimal(price)
    amount.active = True
    amount.is_default = True
    product.price = amount.price_float
    product.invoice_sale_price = amount.price
    for other in product.sellable_amounts:
        if other is not amount:
            other.is_default = False
    return amount


def sync_product_legacy_prices(product: Product) -> None:
    amount = ensure_default_sellable_amount(product)
    product.price = amount.price_float
    product.invoice_sale_price = amount.price


def sellable_amount_snapshot(
    amount: ProductSellableAmount | None,
) -> dict[str, object]:
    return {
        "sellable_amount_id": amount.id if amount is not None else None,
        "sellable_amount_name": amount.name if amount is not None else None,
        "sellable_quantity": amount_quantity_float(amount),
        "unit_price": amount_price_float(amount),
    }


def sale_product_quantity(line_quantity, amount: ProductSellableAmount | None) -> float:
    quantity = coerce_float(line_quantity, default=0.0) or 0.0
    return quantity * amount_quantity_float(amount)


def terminal_sale_unit_price(sale) -> float:
    snapshot_price = coerce_float(getattr(sale, "unit_price_snapshot", None))
    if snapshot_price is not None:
        return snapshot_price
    amount = getattr(sale, "sellable_amount", None)
    if amount is not None:
        return amount_price_float(amount)
    product = getattr(sale, "product", None)
    if product is not None:
        return product.default_sellable_price
    return 0.0


def terminal_sale_line_total(sale) -> float:
    snapshot_total = coerce_float(getattr(sale, "line_total_snapshot", None))
    if snapshot_total is not None:
        return snapshot_total
    quantity = coerce_float(getattr(sale, "quantity", None), default=0.0) or 0.0
    return quantity * terminal_sale_unit_price(sale)


def normalize_sellable_amount_entries(
    entries: Iterable[dict[str, object]],
    *,
    fallback_price=None,
) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for index, entry in enumerate(entries):
        raw_name = str(entry.get("name") or "").strip()
        name = raw_name or "Each"
        amount_id = entry.get("amount_id") or None
        quantity = coerce_float(entry.get("quantity"))
        price = coerce_float(entry.get("price"))
        active = bool(entry.get("active", True))
        is_default = bool(entry.get("is_default"))
        has_any_value = bool(
            amount_id or raw_name or quantity is not None or price is not None
        )
        if not has_any_value:
            continue
        if quantity is None or quantity <= 0:
            quantity = 1.0
        if price is None:
            fallback = coerce_float(fallback_price)
            price = fallback if fallback is not None else 0.0
        normalized.append(
            {
                "amount_id": int(amount_id) if str(amount_id or "").isdigit() else None,
                "name": name,
                "quantity": quantity,
                "price": price,
                "active": active,
                "is_default": is_default,
                "position": index,
            }
        )

    if not normalized:
        fallback = coerce_float(fallback_price)
        normalized.append(
            {
                "amount_id": None,
                "name": "Each",
                "quantity": 1.0,
                "price": fallback if fallback is not None else 0.0,
                "active": True,
                "is_default": True,
                "position": 0,
            }
        )

    if not any(entry["active"] and entry["is_default"] for entry in normalized):
        for entry in normalized:
            if entry["active"]:
                entry["is_default"] = True
                break
    return normalized


def replace_product_sellable_amounts(
    product: Product,
    entries: Iterable[dict[str, object]],
) -> None:
    existing = {amount.id: amount for amount in product.sellable_amounts if amount.id}
    seen_ids: set[int] = set()
    normalized = normalize_sellable_amount_entries(
        entries, fallback_price=product.price
    )

    for entry in normalized:
        amount_id = entry["amount_id"]
        amount = existing.get(amount_id) if amount_id is not None else None
        if amount is None:
            amount = ProductSellableAmount(product=product)
            db.session.add(amount)
        else:
            seen_ids.add(amount.id)
        amount.name = str(entry["name"] or "Each").strip() or "Each"
        amount.quantity = float(entry["quantity"] or 1.0)
        amount.price = _to_decimal(entry["price"])
        amount.active = bool(entry["active"])
        amount.is_default = bool(entry["active"] and entry["is_default"])
        amount.position = int(entry["position"] or 0)

    for amount in product.sellable_amounts:
        if amount.id and amount.id not in seen_ids and amount not in db.session.new:
            amount.active = False
            amount.is_default = False

    default = product.default_sellable_amount
    if default is None:
        default = ensure_default_sellable_amount(product)
    for amount in product.sellable_amounts:
        if amount is not default:
            amount.is_default = False
    default.active = True
    default.is_default = True
    sync_product_legacy_prices(product)
