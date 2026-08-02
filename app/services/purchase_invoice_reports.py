from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Dict

from app.models import GLCode, PurchaseInvoice


_CENT = Decimal("0.01")


def _to_decimal(value) -> Decimal:
    return Decimal(str(value or 0))


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _allocate_amount(total: Decimal, weights: Dict[str, Decimal]):
    allocations = {key: Decimal("0.00") for key in weights}
    if total == 0 or not weights:
        return allocations

    total = _quantize(total)
    weight_total = sum(weights.values())
    if weight_total <= 0:
        return allocations

    remainders = []
    allocated = Decimal("0.00")
    for key, weight in weights.items():
        raw_share = (total * weight) / weight_total
        rounded_share = raw_share.quantize(_CENT, rounding=ROUND_DOWN)
        allocations[key] = rounded_share
        allocated += rounded_share
        remainders.append((raw_share - rounded_share, key))

    remainder = total - allocated
    cents = int(
        ((remainder / _CENT).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    )
    if cents:
        remainders.sort(reverse=True)
        for _, key in remainders[:cents]:
            allocations[key] += _CENT

    return allocations


def invoice_gl_code_rows(invoice: PurchaseInvoice):
    buckets: Dict[str, Dict[str, Decimal]] = {}

    for item in invoice.items:
        line_location_id = item.location_id or invoice.location_id
        gl = item.resolved_purchase_gl_code(line_location_id)
        if gl is not None:
            code_key = gl.code
            display_code = gl.code
            description = gl.description or ""
        else:
            code_key = "__unassigned__"
            display_code = "Unassigned"
            description = ""

        entry = buckets.setdefault(
            code_key,
            {
                "code": display_code,
                "description": description,
                "base_amount": Decimal("0.00"),
                "delivery": Decimal("0.00"),
                "pst": Decimal("0.00"),
                "gst": Decimal("0.00"),
            },
        )

        # Keep GL base amounts aligned with invoice line totals so report totals
        # always reconcile to the invoice totals shown on the invoice screen.
        line_total = _quantize(_to_decimal(item.line_total))
        entry["base_amount"] += line_total

    if not buckets:
        buckets["__unassigned__"] = {
            "code": "Unassigned",
            "description": "",
            "base_amount": Decimal("0.00"),
            "delivery": Decimal("0.00"),
            "pst": Decimal("0.00"),
            "gst": Decimal("0.00"),
        }

    gst_code = "102702"
    gst_gl = GLCode.query.filter_by(code=gst_code).first()
    gst_entry = buckets.get(gst_code)
    if gst_entry is None:
        buckets[gst_code] = {
            "code": gst_code,
            "description": (gst_gl.description if gst_gl else ""),
            "base_amount": Decimal("0.00"),
            "delivery": Decimal("0.00"),
            "pst": Decimal("0.00"),
            "gst": Decimal("0.00"),
        }
        gst_entry = buckets[gst_code]
    elif gst_gl and not gst_entry.get("description"):
        gst_entry["description"] = gst_gl.description

    pst_total = _quantize(_to_decimal(invoice.pst))
    delivery_total = _quantize(_to_decimal(invoice.delivery_charge))
    gst_total = _quantize(_to_decimal(invoice.gst))

    proration_weights = {
        key: data["base_amount"]
        for key, data in buckets.items()
        if key != gst_code and data["base_amount"] > 0
    }

    if (not proration_weights) and (pst_total > 0 or delivery_total > 0):
        proration_weights = {"__unassigned__": Decimal("1.00")}
        if "__unassigned__" not in buckets:
            buckets["__unassigned__"] = {
                "code": "Unassigned",
                "description": "",
                "base_amount": Decimal("0.00"),
                "delivery": Decimal("0.00"),
                "pst": Decimal("0.00"),
                "gst": Decimal("0.00"),
            }

    pst_allocations = _allocate_amount(pst_total, proration_weights)
    delivery_allocations = _allocate_amount(delivery_total, proration_weights)

    rows = []
    totals = {
        "base_amount": Decimal("0.00"),
        "delivery": Decimal("0.00"),
        "pst": Decimal("0.00"),
        "gst": Decimal("0.00"),
        "total": Decimal("0.00"),
    }

    for key in sorted(
        buckets.keys(), key=lambda c: (c == gst_code, c == "__unassigned__", c)
    ):
        data = buckets[key]
        data["pst"] = pst_allocations.get(key, Decimal("0.00"))
        data["delivery"] = delivery_allocations.get(key, Decimal("0.00"))
        if key == gst_code:
            data["gst"] = gst_total

        line_total = (
            data["base_amount"]
            + data["delivery"]
            + data["pst"]
            + data["gst"]
        )
        line_total = _quantize(line_total)

        totals["base_amount"] += data["base_amount"]
        totals["delivery"] += data["delivery"]
        totals["pst"] += data["pst"]
        totals["gst"] += data["gst"]
        totals["total"] += line_total

        rows.append(
            {
                "code": data["code"],
                "description": data["description"],
                "base_amount": data["base_amount"],
                "delivery": data["delivery"],
                "pst": data["pst"],
                "gst": data["gst"],
                "total": line_total,
            }
        )

    totals = {key: _quantize(value) for key, value in totals.items()}

    return rows, totals
