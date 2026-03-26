"""Helpers for rendering stand sheet templates into PDF documents."""

from __future__ import annotations

from io import BytesIO
from typing import Mapping, Sequence, Tuple

from flask import current_app, render_template, request
from pydyf import Stream as PDFStream
from pydyf import _to_bytes as _pdf_to_bytes
from pypdf import PdfReader, PdfWriter
from weasyprint import CSS, HTML
from weasyprint.formatting_structure.boxes import TableCellBox

PDFPage = Tuple[str, Mapping[str, object]]


# WeasyPrint 62 expects ``pydyf.Stream`` to provide a ``transform`` helper, but
# ``pydyf`` 0.12 removed that method.  When the newer dependency is installed,
# the PDF rendering path raises ``AttributeError`` and generates blank files.
# Add a backwards-compatible shim so WeasyPrint can apply page transforms again
# even with newer ``pydyf`` releases.
if not hasattr(PDFStream, "transform"):

    def _stream_transform(
        self, a: float = 1, b: float = 0, c: float = 0, d: float = 1,
        e: float = 0, f: float = 0,
    ) -> None:
        self.stream.append(
            b" ".join(_pdf_to_bytes(v) for v in (a, b, c, d, e, f)) + b" cm"
        )

    PDFStream.transform = _stream_transform

if not hasattr(PDFStream, "push_state"):

    def _stream_push_state(self) -> None:
        self.stream.append(b"q")

    def _stream_pop_state(self) -> None:
        self.stream.append(b"Q")

    PDFStream.push_state = _stream_push_state
    PDFStream.pop_state = _stream_pop_state

if not hasattr(PDFStream, "text_matrix"):

    def _stream_text_matrix(
        self, a: float, b: float, c: float, d: float, e: float, f: float
    ) -> None:
        # Older pydyf versions exposed ``text_matrix`` while newer ones only
        # provide ``set_text_matrix``.
        if hasattr(self, "set_text_matrix"):
            self.set_text_matrix(a, b, c, d, e, f)
        else:
            self.stream.append(
                b" ".join(_pdf_to_bytes(v) for v in (a, b, c, d, e, f))
                + b" Tm"
            )

    PDFStream.text_matrix = _stream_text_matrix

# Some WeasyPrint builds omit border radius attributes on ``TableCellBox`` and
# emit ``AttributeError`` during stand sheet rendering. Align the class with
# other box types by providing zero-radius defaults when they are missing.
if not hasattr(TableCellBox, "border_top_left_radius"):
    TableCellBox.border_top_left_radius = (0, 0)
    TableCellBox.border_top_right_radius = (0, 0)
    TableCellBox.border_bottom_left_radius = (0, 0)
    TableCellBox.border_bottom_right_radius = (0, 0)


def _render_html_to_pdf(
    html: str,
    *,
    base_url: str | None = None,
    stylesheets: Sequence[CSS] | None = None,
) -> bytes:
    """Render an HTML string to a PDF byte string."""
    resolved_base_url = base_url
    if resolved_base_url is None:
        try:
            resolved_base_url = request.url_root
        except RuntimeError:
            resolved_base_url = None

    if resolved_base_url is None:
        resolved_base_url = current_app.root_path

    output = BytesIO()
    try:
        HTML(string=html, base_url=resolved_base_url).write_pdf(
            output, stylesheets=stylesheets
        )
        return output.getvalue()
    finally:
        output.close()


def _ensure_landscape_orientation(pdf_bytes: bytes) -> bytes:
    """Rotate portrait pages so stand sheets always render in landscape."""

    input_stream = BytesIO(pdf_bytes)
    reader = PdfReader(input_stream)
    writer = PdfWriter()
    rotated = False

    for page in reader.pages:
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)

        # ``PageObject.rotate`` returns a new page in recent ``pypdf`` versions
        # instead of mutating in place. Always use the returned page to ensure
        # the rotation is applied before writing to the merged document.
        rotated_page = page
        if width < height:
            rotated_page = page.rotate(90)
            rotated = True

        writer.add_page(rotated_page)

    if not rotated:
        writer.close()
        input_stream.close()
        return pdf_bytes

    output_stream = BytesIO()
    try:
        writer.write(output_stream)
        return output_stream.getvalue()
    finally:
        output_stream.close()
        writer.close()
        input_stream.close()


def render_stand_sheet_pdf(
    pages: Sequence[PDFPage], *, base_url: str | None = None
) -> bytes:
    """Render one or more stand sheet templates into a merged PDF.

    Args:
        pages: A sequence of ``(template_name, context)`` tuples representing the
            Jinja templates and values that should be rendered into the
            resulting PDF.  Each template is rendered with ``render_template``
            and converted to PDF before the pages are combined.

    Returns:
        A ``bytes`` object containing the merged PDF document.

    Raises:
        ValueError: If ``pages`` is empty.
    """

    if not pages:
        raise ValueError("At least one template must be provided")

    pdf_pages = []
    landscape_stylesheet = CSS(string="@page { size: letter landscape; }")
    for template_name, context in pages:
        html = render_template(template_name, **context)
        pdf_pages.append(
            _ensure_landscape_orientation(
                _render_html_to_pdf(
                    html, base_url=base_url, stylesheets=[landscape_stylesheet]
                )
            )
        )

    if len(pdf_pages) == 1:
        return pdf_pages[0]

    writer = PdfWriter()
    streams = []
    try:
        for pdf_bytes in pdf_pages:
            stream = BytesIO(pdf_bytes)
            streams.append(stream)
            writer.append(stream)
        output = BytesIO()
        writer.write(output)
        data = output.getvalue()
        output.close()
        return data
    finally:
        writer.close()
        for stream in streams:
            stream.close()
