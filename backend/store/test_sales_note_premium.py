"""
DOC-NV-PREMIUM — the sales note's A4 and 80 mm PDFs, recomposed.

Presentation only: every datum and every amount of the note is the one it
printed before (parity, legend and design tests stay as they were). What is
pinned here is what the new composition promises:

  * the A4 header has no framed box; the tax id goes with the company and the
    date with the number; the cash received and the change come AFTER the total;
  * the one colour is a short mark, in the company's own brand accent or in
    ink — frozen with the note like its design, and never a shop's colour in
    the code;
  * the logo boxes of the note are a little larger, inside the same caps;
  * the recorded goldens hold exactly the words of the ones recorded before
    this change (`goldens/sales_note_pre_premium.json`): moved, never lost or
    added. Only the repeated table heading, footer and page numbers of a
    multi-page paper are left out of the count — their number follows the
    number of pages.
"""
import json
import re
from collections import Counter
from pathlib import Path

from django.test import SimpleTestCase
from reportlab.lib import colors
from reportlab.lib.rl_accel import fp_str

from store import document_design, document_style
from store.document_design import DEFAULT, DocumentDesign
from store.models import CompanyDocumentDesign, CompanySettings, SalesNote
from store.sales_note_services import (
    build_sales_note_context, generate_sales_note_pdf, note_accent, sales_note_document,
)
from store.test_document_design import SHADE_FILL, page_streams, page_texts
from store.test_document_goldens import GOLDEN_FILE, PinnedSales
from store.test_sales_note_parity import DiscountSales, flat
from store.ticket_services import generate_sales_note_ticket_pdf

PRE_PREMIUM_FILE = GOLDEN_FILE.parent / 'sales_note_pre_premium.json'
BLUE = '#1d4ed8'


def fill(hex_colour: str) -> bytes:
    """A fill colour as reportlab writes it in a page stream."""
    colour = colors.HexColor(hex_colour)
    return (fp_str(colour.red, colour.green, colour.blue) + ' rg').encode()


def text(pdf: bytes) -> str:
    return flat(' '.join(page_texts(pdf)))


# ---------------------------------------------------------------------------
# The A4
# ---------------------------------------------------------------------------

class PremiumA4Test(DiscountSales):
    def test_the_header_has_no_framed_box_and_the_tax_id_goes_with_the_company(self):
        _order, note = self.pos_sale([(self.case, 1)])
        pdf = generate_sales_note_pdf(note)
        stream = page_streams(pdf)[0]
        header = stream.split(b'(NOTA DE VENTA) Tj', 1)[0]
        self.assertNotIn(SHADE_FILL, header)                 # no shaded title row
        self.assertNotIn(b' l S', header)                    # no frame: nothing stroked before the title
        printed = text(pdf)
        self.assertLess(printed.index('Inventario S.A.C. · RUC 20800000001'), printed.index('NOTA DE VENTA'))

    def test_the_date_follows_the_number_and_the_cash_follows_the_total(self):
        order, note = self.promotion_sale()
        printed = text(generate_sales_note_pdf(note))
        number, date = printed.index(note.number), printed.index('Fecha de emisión')
        self.assertLess(number, date)
        self.assertLess(date, printed.index('CLIENTE'))
        total = printed.index('IMPORTE TOTAL')
        self.assertLess(total, printed.index('Efectivo recibido'))
        self.assertLess(printed.index('Efectivo recibido'), printed.index('Vuelto'))
        self.assertNotIn('Efectivo recibido', printed[:printed.index('Nº CÓDIGO')])
        self.assertIn(f'Efectivo recibido S/ {order.amount_received:.2f} Vuelto S/ {order.change_amount:.2f}',
                      printed)

    def test_a_long_custom_title_wraps_inside_its_column(self):
        ctx = build_sales_note_context(self.pos_sale([(self.case, 1)])[1])
        ctx['title'] = 'Nota de pedido de mostrador y entrega'
        ctx['design'] = DocumentDesign(document_title=ctx['title'])
        from store.document_layout import render_a4

        stream = b''.join(page_streams(render_a4(sales_note_document(ctx))))
        drawn = b' '.join(re.findall(rb'\(([^)]*)\) Tj', stream)).decode('latin-1')
        self.assertIn('NOTA DE PEDIDO', drawn)
        self.assertIn('ENTREGA', drawn)
        # Two lines of the title, each at the title's size: wrapped, not shrunk nor cut.
        self.assertGreaterEqual(len(re.findall(rb'19 Tf', stream)), 2)

    def test_density_and_emphasis_still_change_the_page(self):
        _order, note = self.pos_sale([(self.case, 1)])
        ctx = build_sales_note_context(note)
        from store.document_layout import render_a4

        def streams(**fields):
            return page_streams(render_a4(sales_note_document({**ctx, 'design': DocumentDesign(**fields)})))

        self.assertNotEqual(streams(density='compact'), streams())
        self.assertNotEqual(streams(density='airy'), streams())
        on = b''.join(streams())
        off = b''.join(streams(emphasize_total=False))
        self.assertIn(SHADE_FILL, on)
        self.assertNotIn(SHADE_FILL, off)


# ---------------------------------------------------------------------------
# The accent
# ---------------------------------------------------------------------------

class PaperAccentTest(SimpleTestCase):
    def test_the_companys_accent_or_ink(self):
        self.assertEqual(document_style.paper_accent('#1D4ED8'), BLUE)
        for neutral in ('', None, 'blue', '#12345', '#1d4ed8;', 'url(x)', '#FFFFFF', '#F8F8F8'):
            with self.subTest(neutral):
                self.assertEqual(document_style.paper_accent(neutral), document_style.INK)

    def test_no_shop_colour_lives_in_the_drawing_code(self):
        root = Path(__file__).resolve().parent
        for module in ('document_style.py', 'document_layout.py', 'note_layout.py', 'ticket_services.py',
                       'sales_note_services.py', 'document_design.py'):
            source = (root / module).read_text(encoding='utf-8').lower()
            for forbidden in ('d4af37', 'black dog', 'blackdog', 'manada'):
                with self.subTest(module=module, forbidden=forbidden):
                    self.assertNotIn(forbidden, source)


class AccentTest(PinnedSales):
    def accent(self, value):
        CompanySettings.objects.filter(company=self.company).update(accent_color=value)

    def test_without_an_accent_both_papers_are_neutral(self):
        _order, note = self.device_sale()
        self.assertEqual(note.metadata['accent_color'], '')
        for pdf in (generate_sales_note_pdf(note), generate_sales_note_ticket_pdf(note)):
            self.assertIn(fill(document_style.INK), b''.join(page_streams(pdf)))
            self.assertNotIn(fill(BLUE), b''.join(page_streams(pdf)))

    def test_the_companys_accent_marks_both_papers_once(self):
        self.accent(BLUE)
        _order, note = self.device_sale()
        for pdf in (generate_sales_note_pdf(note), generate_sales_note_ticket_pdf(note)):
            stream = b''.join(page_streams(pdf))
            self.assertEqual(stream.count(fill(BLUE)), 1)

    def test_the_accent_is_frozen_with_the_note(self):
        self.accent(BLUE)
        _order, note = self.device_sale()
        self.accent('#b91c1c')
        self.assertEqual(note_accent(note), BLUE)
        self.assertIn(fill(BLUE), b''.join(page_streams(generate_sales_note_pdf(note))))
        self.assertNotIn(fill('#b91c1c'), b''.join(page_streams(generate_sales_note_pdf(note))))

    def test_a_note_issued_before_takes_the_current_accent(self):
        _order, note = self.device_sale()
        metadata = dict(note.metadata)
        metadata.pop('accent_color')
        SalesNote.objects.filter(pk=note.pk).update(metadata=metadata)
        note.refresh_from_db()
        self.accent(BLUE)
        self.assertEqual(note_accent(note), BLUE)
        self.assertEqual(build_sales_note_context(note)['accent_color'], BLUE)


# ---------------------------------------------------------------------------
# The logo
# ---------------------------------------------------------------------------

class NoteLogoBoxTest(SimpleTestCase):
    def test_each_size_is_a_little_larger_on_the_note_inside_the_caps(self):
        for size in document_design.LOGO_SIZES:
            design = DocumentDesign(logo_size=size)
            with self.subTest(size):
                a4, roll = design.note_a4_logo_box_cm, design.note_ticket_logo_box_mm
                self.assertGreater(a4[0], design.a4_logo_box_cm[0])
                self.assertGreater(a4[1], design.a4_logo_box_cm[1])
                self.assertGreater(roll[0], design.ticket_logo_box_mm[0])
                self.assertGreater(roll[1], design.ticket_logo_box_mm[1])
                self.assertLessEqual(a4[1] * 10, document_design.LOGO_A4_MAX_HEIGHT_MM)
                self.assertLessEqual(roll[1], document_design.LOGO_TICKET_MAX_HEIGHT_MM)
                self.assertLessEqual(roll[0], 72)

    def test_a_width_in_millimetres_is_the_same_box_on_every_paper(self):
        design = DocumentDesign(logo_a4_width_mm=70, logo_ticket_width_mm=50)
        self.assertEqual(design.note_a4_logo_box_cm, design.a4_logo_box_cm)
        self.assertEqual(design.note_ticket_logo_box_mm, design.ticket_logo_box_mm)

    def test_the_order_receipt_keeps_its_box(self):
        self.assertEqual(DEFAULT.a4_logo_box_cm, document_style.LOGO_A4_CM)
        self.assertEqual(document_design.a4_slots(DEFAULT)['logo_box_cm'], document_style.LOGO_A4_CM)


class NoteLogoDrawnTest(PinnedSales):
    def test_the_note_draws_the_logo_larger_than_the_order_receipt(self):
        from store.pdf_services import generate_order_receipt_pdf
        from store.test_document_design_settings import image_widths

        self.with_logo()
        order, note = self.device_sale()
        self.assertGreater(image_widths(generate_sales_note_pdf(note))[0],
                           image_widths(generate_order_receipt_pdf(order))[0])


# ---------------------------------------------------------------------------
# The goldens: the same words as before
# ---------------------------------------------------------------------------

_PER_PAGE = (
    re.compile(r'Página \d+ de \d+'),
    re.compile(r'Nº CÓDIGO DESCRIPCIÓN U\.M\. CANT\. P\. UNIT\. IMPORTE(?: DSCTO\.)?'),
)


def words(texts: list[str], footer: str = '') -> Counter:
    """
    The words of a recorded paper. Separators (`·`, `—`) are not words; a `-`
    is (it signs a discount). A `Paragraph` reads «A&B» back as «A & B»:
    joined again. What every page repeats is left out.
    """
    joined = re.sub(r' ?& ?', '&', flat(' '.join(texts)))
    for pattern in _PER_PAGE:
        joined = pattern.sub(' ', joined)
    if footer:
        joined = joined.replace(footer, ' ')
    return Counter(token for token in joined.split() if token == '-' or any(c.isalnum() for c in token))


def footer_of(golden: dict) -> str:
    """The A4 footer line (`render_a4`'s `footer`): what is left of the last page before «Página»."""
    last = flat(golden['texts'][-1])
    match = re.search(r'(\S+ · RUC \d+ · Nota de \w+ NV-\d+) Página', last)
    return match.group(1) if match else ''


class GoldenWordsTest(SimpleTestCase):
    """The sales-note goldens say what the pre-premium ones said: moved, never lost or added."""

    def goldens(self):
        before = json.loads(PRE_PREMIUM_FILE.read_text(encoding='utf-8'))
        now = json.loads(GOLDEN_FILE.read_text(encoding='utf-8'))
        self.assertEqual(len(before), 10)
        return before, now

    def test_every_pre_premium_word_of_the_a4_is_still_printed_the_same_number_of_times(self):
        before, now = self.goldens()
        papers = [name for name in before if name.endswith(('_a4', '_a4_coupon', '_a4_promotion', '_many_lines'))]
        self.assertEqual(len(papers), 5)
        for name in papers:
            with self.subTest(name):
                footer = footer_of(before[name])
                self.assertTrue(footer)
                self.assertEqual(footer, footer_of(now[name]))
                self.assertEqual(now[name]['pages'], before[name]['pages'])
                self.assertEqual(words(now[name]['texts'], footer), words(before[name]['texts'], footer))

    def test_the_rolls_say_the_same_in_the_same_order(self):
        """Character for character; only where a word too long for the roll is cut may move (larger type)."""
        before, now = self.goldens()
        rolls = [name for name in before if 'ticket80' in name]
        self.assertEqual(len(rolls), 5)
        for name in rolls:
            with self.subTest(name):
                self.assertEqual(''.join(' '.join(now[name]['texts']).split()),
                                 ''.join(' '.join(before[name]['texts']).split()))
