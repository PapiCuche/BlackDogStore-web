"""
DOC-TICKET-PREMIUM — the 80 mm PDF printed from the browser must fit what the head reaches.

The roll is 80 mm, but the head of an 80 mm thermal printer prints 72 mm (576 dots at 203 dpi), and the
driver decides WHERE those 72 mm are. The Advance ADV-8010N's Windows driver reports them from the left
edge (HORZRES 576, PHYSICALOFFSETX 0: 0–72 mm); other drivers centre them (4–76 mm). The PDF used to be
80 mm wide with 4 mm margins, so its content ran from 4 to 76 mm: on the Advance the last 4 mm — the
amounts, aligned right — fell off the paper (owner's photo, printed from the browser).

A page as wide as the printable strip (72 mm) with 4 mm margins puts the content between 4 and 68 mm,
inside both strips, and a browser that «fits to the printable area» no longer shrinks it.

Physically verified on the Advance (one synthetic calibration ticket, ESC/POS RAW, no sale): 48 columns,
the 576-dot bar, double width, Ñ and the cut all print in full. The RAW path does not cut; the PDF did.
"""
import io
import re
from dataclasses import replace
from decimal import Decimal

from django.test import TestCase
from PIL import Image
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas
from unittest import mock

from store import document_preview, fiscal_pdf_services, ticket_services
from store.document_design import design_for
from store.tests import _p3_company

#: Where the content may be: inside 0–72 mm (left-aligned driver) AND 4–76 mm (centred driver).
SAFE_LEFT, SAFE_RIGHT, PRINTABLE = 4 * mm, 68 * mm, 72 * mm
EPSILON = 0.05


def _wide_logo() -> bytes:
    """A wide, dark logo: at the widest design (72 mm) it would overflow a narrower content."""
    image = Image.new('L', (900, 200), 255)
    image.paste(0, (0, 0, 900, 200))
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


class _Extents:
    """Every horizontal span the ticket draws, on the page (calls under a rotation are skipped)."""

    def __init__(self):
        self.spans = []

    def __enter__(self):
        spans = self.spans

        def flat(canvas):
            return canvas._currentMatrix == (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

        def text(kind, original):
            def wrapper(canvas, x, y, value, *args, **kwargs):
                if flat(canvas):
                    width = stringWidth(str(value), canvas._fontname, canvas._fontsize)
                    start = {'left': x, 'right': x - width, 'centre': x - width / 2}[kind]
                    spans.append((start, start + width, str(value)))
                return original(canvas, x, y, value, *args, **kwargs)
            return wrapper

        def image(original):
            def wrapper(canvas, img, x, y, width=None, height=None, *args, **kwargs):
                if flat(canvas):
                    spans.append((x, x + (width or 0), '<image>'))
                return original(canvas, img, x, y, width, height, *args, **kwargs)
            return wrapper

        def line(original):
            def wrapper(canvas, x1, y1, x2, y2, *args, **kwargs):
                if flat(canvas):
                    spans.append((min(x1, x2), max(x1, x2), '<line>'))
                return original(canvas, x1, y1, x2, y2, *args, **kwargs)
            return wrapper

        self._patches = [
            mock.patch.object(Canvas, 'drawString', text('left', Canvas.drawString)),
            mock.patch.object(Canvas, 'drawRightString', text('right', Canvas.drawRightString)),
            mock.patch.object(Canvas, 'drawCentredString', text('centre', Canvas.drawCentredString)),
            mock.patch.object(Canvas, 'drawImage', image(Canvas.drawImage)),
            mock.patch.object(Canvas, 'line', line(Canvas.line)),
        ]
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in self._patches:
            patch.stop()
        return False


def _page_width(pdf: bytes) -> float:
    match = re.search(rb'/MediaBox\s*\[\s*0\s+0\s+([\d.]+)', pdf)
    return float(match.group(1))


def _page_height(pdf: bytes) -> float:
    match = re.search(rb'/MediaBox\s*\[\s*0\s+0\s+[\d.]+\s+([\d.]+)', pdf)
    return float(match.group(1))


class TicketFitsThePrintableStripTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = _p3_company('ticket-ancho', 'Tienda de Ancho')

    def lines(self):
        # The extremes of the owner's photo: a long name and an amount in the hundreds of thousands.
        return document_preview._lines(2) + [(
            'Funda de silicona con MagSafe para teléfono de pantalla grande, edición limitada «Ñandú»',
            'COD-LARGO-000001', 12, Decimal('98765.43'), [])]

    def assert_fits(self, pdf: bytes, spans):
        self.assertLessEqual(_page_width(pdf), PRINTABLE + EPSILON, 'the page is wider than the printable strip')
        self.assertTrue(spans)
        outside = [(round(a / mm, 2), round(b / mm, 2), what[:40]) for a, b, what in spans
                   if a < SAFE_LEFT - EPSILON or b > SAFE_RIGHT + EPSILON]
        self.assertEqual(outside, [], 'drawn outside 4–68 mm (millimetres)')

    def widest_design(self):
        return replace(design_for(self.company), logo_ticket_width_mm=72)

    def test_the_sales_note_ticket_fits(self):
        ctx = document_preview._sales_note_ctx(self.company, self.lines(), self.widest_design())
        ctx['logo_png'] = _wide_logo()
        with _Extents() as drawn:
            pdf = ticket_services.ticket_pdf_from_ctx(ctx)
        self.assert_fits(pdf, drawn.spans)
        self.assertIn('<image>', [what for _a, _b, what in drawn.spans])

    def test_the_boleta_and_factura_tickets_fit(self):
        for kind in ('boleta', 'factura'):
            with self.subTest(kind=kind):
                ctx = document_preview._fiscal_ctx(self.company, kind, self.lines(), self.widest_design())
                ctx['logo_png'] = _wide_logo()
                with _Extents() as drawn:
                    pdf = fiscal_pdf_services.render_fiscal_ticket(ctx)
                self.assert_fits(pdf, drawn.spans)

    def test_the_constants_describe_the_strip(self):
        width = float(ticket_services.TICKET_WIDTH_MM)
        margin = float(ticket_services.TICKET_MARGIN_MM)
        self.assertLessEqual(width, 72)
        self.assertGreaterEqual(margin, 4)
        # The quote ticket (quote_ticket.py) reads the same two constants, so it moves with them.
        self.assertLessEqual(width - margin, 68)

    def test_the_fiscal_ticket_does_not_depend_on_the_digest_characters(self):
        # The digest of the signed XML changes with every signature, and Helvetica's base64 letters are not
        # equally wide. On the 64 mm content «Valor resumen: <digest>» sometimes took two lines and sometimes
        # one (a flaky golden, and a paper that changed with the hash). Now the digest has its own line.
        # 44 characters: the SHA-256 DigestValue of the signing profile (fiscal/signing.py). A wide and a
        # narrow one: next to the label the wide one wraps, on its own line both fit.
        heights = []
        for digest in ('A' * 43 + '=', 'i' * 43 + '='):
            ctx = document_preview._fiscal_ctx(self.company, 'factura', document_preview._lines(1))
            ctx['digest_value'] = digest
            heights.append(_page_height(fiscal_pdf_services.render_fiscal_ticket(ctx)))
        self.assertEqual(heights[0], heights[1])
