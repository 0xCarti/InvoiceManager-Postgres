"""Printable QR sign rendering for public location count entry."""

from __future__ import annotations

from io import BytesIO

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


_BG = HexColor("#f8fafc")
_BORDER = HexColor("#cbd5e1")
_TITLE = HexColor("#0f172a")
_TEXT = HexColor("#334155")
_MUTED = HexColor("#64748b")


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


def _fit_text(
    pdf: canvas.Canvas,
    text: str,
    *,
    font_name: str,
    max_width: float,
    initial_size: float,
    min_size: float,
) -> float:
    size = initial_size
    value = (text or "").strip()
    while size > min_size and stringWidth(value, font_name, size) > max_width:
        size -= 1
    return size


def render_location_count_sign_pdf(locations, qr_payloads: dict[int, str]) -> bytes:
    """Render one or more full-page count-entry QR signs."""

    if not locations:
        raise ValueError("At least one location is required.")

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    page_width, page_height = letter

    for index, location in enumerate(locations):
        if index:
            pdf.showPage()

        margin = 0.55 * inch
        card_x = margin
        card_y = margin
        card_width = page_width - (2 * margin)
        card_height = page_height - (2 * margin)
        pdf.setFillColor(_BG)
        pdf.setStrokeColor(_BORDER)
        pdf.roundRect(card_x, card_y, card_width, card_height, 18, stroke=1, fill=1)

        qr_size = 3.2 * inch
        qr_x = (page_width - qr_size) / 2
        qr_y = page_height - margin - qr_size - 2.3 * inch
        payload = qr_payloads.get(location.id) or ""
        _draw_qr_code(pdf, payload, qr_x, qr_y, qr_size)

        title = "Scan For Counts"
        pdf.setFillColor(_TITLE)
        pdf.setFont("Helvetica-Bold", 24)
        pdf.drawCentredString(page_width / 2, page_height - margin - 0.85 * inch, title)

        location_name = location.name or f"Location #{location.id}"
        fitted_size = _fit_text(
            pdf,
            location_name,
            font_name="Helvetica-Bold",
            max_width=card_width - 1.2 * inch,
            initial_size=28,
            min_size=18,
        )
        pdf.setFont("Helvetica-Bold", fitted_size)
        pdf.drawCentredString(page_width / 2, page_height - margin - 1.55 * inch, location_name)

        pdf.setFillColor(_TEXT)
        pdf.setFont("Helvetica", 13)
        pdf.drawCentredString(
            page_width / 2,
            qr_y - 0.45 * inch,
            "Use your phone camera to open the count page for this stand.",
        )
        pdf.drawCentredString(
            page_width / 2,
            qr_y - 0.75 * inch,
            "Enter your name, choose opening or closing count, and submit.",
        )

        pdf.setFillColor(_MUTED)
        pdf.setFont("Helvetica", 10)
        pdf.drawCentredString(
            page_width / 2,
            card_y + 0.55 * inch,
            "Opening must be submitted before closing becomes available.",
        )

    pdf.save()
    data = buffer.getvalue()
    buffer.close()
    return data
