"""PDF label rendering helpers for equipment assets."""

from __future__ import annotations

from io import BytesIO
from textwrap import wrap

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import Color, HexColor
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


_LABEL_BORDER = HexColor("#cbd5e1")
_LABEL_TITLE = HexColor("#0f172a")
_LABEL_MUTED = HexColor("#475569")
_LABEL_BG = Color(1, 1, 1)


def _clip_text(text: str, font_name: str, font_size: float, max_width: float) -> str:
    value = (text or "").strip()
    if not value:
        return ""
    if stringWidth(value, font_name, font_size) <= max_width:
        return value
    ellipsis = "..."
    trimmed = value
    while trimmed and stringWidth(
        f"{trimmed}{ellipsis}", font_name, font_size
    ) > max_width:
        trimmed = trimmed[:-1]
    return f"{trimmed.rstrip()}{ellipsis}" if trimmed else ellipsis


def _wrap_text(
    text: str,
    font_name: str,
    font_size: float,
    max_width: float,
    max_lines: int,
) -> list[str]:
    value = (text or "").strip()
    if not value:
        return []
    rough_chars = max(10, int(max_width / max(font_size * 0.52, 1)))
    lines = []
    for line in wrap(value, width=rough_chars):
        lines.append(_clip_text(line, font_name, font_size, max_width))
        if len(lines) >= max_lines:
            break
    if not lines:
        return [_clip_text(value, font_name, font_size, max_width)]
    return lines


def _draw_qr_code(pdf: canvas.Canvas, payload: str, x: float, y: float, size: float) -> None:
    widget = qr.QrCodeWidget(payload)
    bounds = widget.getBounds()
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    drawing = Drawing(
        size,
        size,
        transform=[size / width, 0, 0, size / height, 0, 0],
    )
    drawing.add(widget)
    renderPDF.draw(drawing, pdf, x, y)


def render_equipment_label_pdf(assets, qr_payloads: dict[int, str]) -> bytes:
    """Return a printable PDF containing QR labels for equipment assets."""

    if not assets:
        raise ValueError("At least one equipment asset is required.")

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    page_width, page_height = letter

    columns = 2
    rows = 4
    labels_per_page = columns * rows
    margin_x = 0.5 * inch
    margin_y = 0.5 * inch
    gutter_x = 0.2 * inch
    gutter_y = 0.18 * inch
    label_width = (page_width - (2 * margin_x) - gutter_x) / columns
    label_height = (page_height - (2 * margin_y) - ((rows - 1) * gutter_y)) / rows

    for index, asset in enumerate(assets):
        page_index = index % labels_per_page
        if index and page_index == 0:
            pdf.showPage()

        row_index = page_index // columns
        col_index = page_index % columns
        x = margin_x + col_index * (label_width + gutter_x)
        y = page_height - margin_y - ((row_index + 1) * label_height) - (row_index * gutter_y)

        pdf.setFillColor(_LABEL_BG)
        pdf.setStrokeColor(_LABEL_BORDER)
        pdf.roundRect(x, y, label_width, label_height, 10, stroke=1, fill=1)

        padding = 0.14 * inch
        qr_size = 1.18 * inch
        qr_x = x + padding
        qr_y = y + label_height - qr_size - padding
        payload = qr_payloads.get(asset.id) or asset.asset_tag
        _draw_qr_code(pdf, payload, qr_x, qr_y, qr_size)

        text_x = qr_x + qr_size + 0.14 * inch
        text_width = label_width - (text_x - x) - padding
        cursor_y = y + label_height - padding - 2

        pdf.setFillColor(_LABEL_TITLE)
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawString(text_x, cursor_y - 10, _clip_text(asset.asset_tag, "Helvetica-Bold", 12, text_width))

        cursor_y -= 22
        pdf.setFont("Helvetica-Bold", 9.5)
        for line in _wrap_text(asset.display_name, "Helvetica-Bold", 9.5, text_width, 2):
            pdf.drawString(text_x, cursor_y - 8, line)
            cursor_y -= 11

        secondary_lines = []
        model_name = asset.model_display_name
        if model_name and model_name != asset.display_name:
            secondary_lines.extend(
                _wrap_text(model_name, "Helvetica", 8, text_width, 2)
            )
        if asset.serial_number:
            secondary_lines.append(f"Serial: {asset.serial_number}")
        secondary_lines.append(f"Status: {asset.status_label}")
        if asset.location_label:
            secondary_lines.extend(
                _wrap_text(
                    f"Location: {asset.location_label}",
                    "Helvetica",
                    8,
                    text_width,
                    2,
                )
            )
        if asset.custodian_label:
            secondary_lines.extend(
                _wrap_text(
                    f"Custodian: {asset.custodian_label}",
                    "Helvetica",
                    8,
                    text_width,
                    2,
                )
            )

        pdf.setFillColor(_LABEL_MUTED)
        pdf.setFont("Helvetica", 8)
        for line in secondary_lines[:6]:
            pdf.drawString(
                text_x,
                cursor_y - 7,
                _clip_text(line, "Helvetica", 8, text_width),
            )
            cursor_y -= 10

        pdf.setFont("Helvetica", 7)
        pdf.drawString(qr_x, y + padding - 1, "Scan for asset details")

    pdf.save()
    data = buffer.getvalue()
    buffer.close()
    return data
