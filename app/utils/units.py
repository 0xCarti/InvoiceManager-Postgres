"""Utilities for working with the application's base units."""

from __future__ import annotations

import json
from typing import Dict, Iterator, List, Mapping, Tuple

# Display labels for each base unit supported by the system. The keys are stored
# in the database and should remain stable.
BASE_UNIT_LABELS: Dict[str, str] = {
    "ounce": "Ounce",
    "gram": "Gram",
    "each": "Each",
    "millilitre": "Millilitre",
}


# Keep the units ordered for deterministic form rendering.
BASE_UNITS: List[str] = list(BASE_UNIT_LABELS.keys())


def _choice(unit: str) -> Tuple[str, str]:
    return unit, BASE_UNIT_LABELS[unit]


BASE_UNIT_CHOICES: List[Tuple[str, str]] = [_choice(unit) for unit in BASE_UNITS]


# Default conversion map ensures each unit reports as itself.
DEFAULT_BASE_UNIT_CONVERSIONS: Dict[str, str] = {
    unit: unit for unit in BASE_UNITS
}


# Conversion factors used when translating between units. Each mapping is from
# the source unit to the target unit and represents the multiplier applied to a
# quantity expressed in the source unit in order to obtain the target unit.
_UNIT_CONVERSION_FACTORS: Dict[str, Dict[str, float]] = {
    "each": {"each": 1.0},
    "ounce": {
        "ounce": 1.0,
        "gram": 28.349523125,
        "millilitre": 29.5735295625,
    },
    "gram": {
        "gram": 1.0,
        "ounce": 1 / 28.349523125,
    },
    "millilitre": {
        "millilitre": 1.0,
        "ounce": 0.0338140227,
    },
}


def get_allowed_target_units(base_unit: str) -> List[str]:
    """Return the list of report units supported for a given base unit."""

    return list(_UNIT_CONVERSION_FACTORS.get(base_unit, {}).keys())


def get_conversion_factor(from_unit: str, to_unit: str) -> float | None:
    """Return the multiplier to convert ``from_unit`` quantities to ``to_unit``."""

    return _UNIT_CONVERSION_FACTORS.get(from_unit, {}).get(to_unit)


def convert_quantity(value: float, from_unit: str, to_unit: str) -> float:
    """Convert ``value`` from ``from_unit`` to ``to_unit`` for quantities."""

    if from_unit == to_unit:
        return value
    factor = get_conversion_factor(from_unit, to_unit)
    if factor is None:
        raise ValueError(f"Unsupported conversion from {from_unit} to {to_unit}")
    return value * factor


def convert_unit_cost(value: float, from_unit: str, to_unit: str) -> float:
    """Convert a per-unit cost from ``from_unit`` to ``to_unit``."""

    if from_unit == to_unit:
        return value
    factor = get_conversion_factor(from_unit, to_unit)
    if factor is None or factor == 0:
        raise ValueError(f"Unsupported conversion from {from_unit} to {to_unit}")
    return value / factor


def get_unit_label(unit: str | None) -> str:
    """Return the display label for ``unit``."""

    if not unit:
        return ""
    return BASE_UNIT_LABELS.get(unit, unit)


def parse_conversion_setting(value: str | None) -> Dict[str, str]:
    """Parse the stored JSON conversion setting into a mapping."""

    mapping = dict(DEFAULT_BASE_UNIT_CONVERSIONS)
    if not value:
        return mapping
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return mapping

    for unit in BASE_UNITS:
        target = data.get(unit)
        if target in get_allowed_target_units(unit):
            mapping[unit] = target
    return mapping


def serialize_conversion_setting(mapping: Mapping[str, str]) -> str:
    """Serialize a conversion mapping for storage in the database."""

    normalized = {
        unit: mapping.get(unit, unit)
        if mapping.get(unit, unit) in get_allowed_target_units(unit)
        else unit
        for unit in BASE_UNITS
    }
    return json.dumps(normalized, sort_keys=True)


def convert_quantity_for_reporting(
    quantity: float, base_unit: str | None, conversions: Mapping[str, str]
) -> Tuple[float, str | None]:
    """Convert ``quantity`` into the reporting unit specified for ``base_unit``."""

    if not base_unit:
        return quantity, base_unit
    target_unit = conversions.get(base_unit, base_unit)
    if target_unit == base_unit:
        return quantity, base_unit
    factor = get_conversion_factor(base_unit, target_unit)
    if factor is None:
        return quantity, base_unit
    return quantity * factor, target_unit


def convert_cost_for_reporting(
    unit_cost: float, base_unit: str | None, conversions: Mapping[str, str]
) -> float:
    """Convert a per-unit cost into the configured reporting unit."""

    if not base_unit:
        return unit_cost
    target_unit = conversions.get(base_unit, base_unit)
    if target_unit == base_unit:
        return unit_cost
    factor = get_conversion_factor(base_unit, target_unit)
    if factor is None or factor == 0:
        return unit_cost
    return unit_cost / factor


def iter_base_unit_fields(form) -> Iterator[Tuple[str, str, object]]:
    """Yield form fields related to base unit conversions."""

    for unit in BASE_UNITS:
        field = getattr(form, f"convert_{unit}", None)
        if field is not None:
            yield unit, BASE_UNIT_LABELS[unit], field
