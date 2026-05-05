"""Helpers for converting recipe rows into per-sale inventory usage."""

from __future__ import annotations


def recipe_item_base_units_per_sale(recipe_item) -> float:
    """Return the base-unit quantity consumed by one sold product."""

    if recipe_item is None:
        return 0.0

    quantity = float(getattr(recipe_item, "quantity", 0.0) or 0.0)
    if quantity <= 0:
        return 0.0

    unit = getattr(recipe_item, "unit", None)
    factor = float(getattr(unit, "factor", 1.0) or 1.0)

    product = getattr(recipe_item, "product", None)
    yield_quantity = float(getattr(product, "recipe_yield_quantity", 1.0) or 1.0)
    if yield_quantity <= 0:
        yield_quantity = 1.0

    return quantity * factor / yield_quantity
