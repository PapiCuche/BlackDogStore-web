"""
DOC-NV-PAGINATION — how the sales note's A4 breaks across pages, and the closing of its roll.

Drawn from plain data (`render_a4` of a premium description), so each case is deterministic
and needs no database. Read back page by page:

  * the totals never open a page alone: the last rows of the table travel with them, under the
    table's heading;
  * the closing blocks (notes, warranty, thanks, post-sale, commercial) first tighten their
    spacing to stay on the page; when they still do not fit, they move to the next page TOGETHER
    — a page never carries a stray line of them;
  * a closing block never splits across pages, and all of them share one alignment (the left
    margin);
  * on the roll, the warranty after the thank-you takes the air a message takes.
"""
import re
from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase

from store import note_layout
from store.document_layout import render_a4
from store.test_document_design import page_streams, page_texts

HEADING = 'Nº CÓDIGO DESCRIPCIÓN U.M. CANT. P. UNIT. IMPORTE DSCTO.'
# Each closing text starts with its own word, so each block can be found on its own.
THANKS = 'Agradecemos tu compra de prueba.'
POST_SALE = 'Posventa: mensaje de prueba para el cliente.'
COMMERCIAL = 'Comercial: mensaje de prueba de la tienda.'
WARRANTY = 'Garantía de prueba de doce meses.'
NOTES = 'Observación visible de prueba.'
#: (text, the size its block prints at on the A4)
CLOSING_SIZES = ((THANKS, 12), (POST_SALE, 9), (COMMERCIAL, 8.5), (WARRANTY, 8), (NOTES, 8.5))
CLOSING = (THANKS, POST_SALE, COMMERCIAL, WARRANTY, NOTES)


def flat(text: str) -> str:
    return ' '.join(text.split())


def line(number: int) -> dict:
    return {'number': number, 'code': f'COD-{number:03d}', 'description': f'Artículo de prueba {number:03d}',
            'units': [], 'details': [], 'unit': 'UND', 'list_price': Decimal('10.00'), 'unit_price': Decimal('10.00'),
            'discount': Decimal('0.00'), 'quantity': 1, 'amount': Decimal('10.00')}


def document(lines: int, *, closing=False, spacing=1.0, **extra) -> dict:
    described = {
        'composition': 'premium', 'title': 'NOTA DE VENTA', 'number': 'NV-000001', 'footer': 'Tienda · NV-000001',
        'identity': {'name': 'Tienda de prueba', 'tax_id': '20800000001', 'address': 'Calle de prueba 1'},
        'parties': [('Cliente', [('Nombre', 'Cliente de prueba')]), ('Datos de la venta', [('Pedido', '#1')])],
        'line_layout': 'retail', 'lines': [line(n) for n in range(1, lines + 1)],
        'summary': ['Productos: 1'], 'amount_in_words': 'DIEZ CON 00/100 SOLES',
        'totals': [('Subtotal', 'S/ 10.00'), ('Desglose referencial', ''), ('Op. gravada', 'S/ 8.47'),
                   ('IGV (18%)', 'S/ 1.53')],
        'total': 'S/ 10.00', 'spacing': spacing,
    }
    if closing:
        described.update(notes=NOTES, footnotes=[WARRANTY], closing_message=THANKS,
                         messages=[('post_sale', POST_SALE), ('commercial', COMMERCIAL)])
    described.update(extra)
    return described


def drawn_at(stream: bytes, text: str) -> tuple[float, float]:
    """(x, size) of the line of `text` that a Paragraph drew: the last position and font set before it."""
    index = stream.find(b'(' + re.match(r'[A-Za-z]+', text).group(0).encode())
    assert index > -1, text
    before = stream[:index]
    x = float(re.findall(rb'1 0 0 1 ([\d.-]+) [\d.-]+ Tm', before)[-1])
    size = float(re.findall(rb'/F\d+ ([\d.]+) Tf', before)[-1])
    return x, size


def pages(described: dict) -> list[str]:
    return [flat(text) for text in page_texts(render_a4(described))]


class TotalsKeepTheirRowsTest(SimpleTestCase):
    def test_no_page_starts_with_the_totals_alone(self):
        """From one row to two pages and a half, whatever the count: the totals come with rows and a heading."""
        for count in range(1, 75):
            with self.subTest(lines=count):
                texts = pages(document(count))
                with_total = [n for n, text in enumerate(texts) if 'IMPORTE TOTAL' in text]
                self.assertEqual(len(with_total), 1)
                page = texts[with_total[0]]
                before_total = page.split('Productos:')[0]
                self.assertRegex(before_total, r'Artículo de prueba \d{3} UND')     # at least one row
                self.assertIn(HEADING, before_total)                                # under the heading
                self.assertIn(f'Artículo de prueba {count:03d}', page)              # the last row with them
                for text in texts[1:]:
                    self.assertTrue(text.startswith(HEADING), text[:80])             # a new page opens on the heading

    def test_every_row_is_printed_once_in_order(self):
        texts = ' '.join(pages(document(60)))
        printed = [int(n) for n in re.findall(r'Artículo de prueba (\d{3})', texts)]
        self.assertEqual(printed, list(range(1, 61)))

    def test_the_last_row_closes_the_table_with_its_rule(self):
        """The heavier rule (0.8): under the heading of every page, and once under the last row."""
        for count in (5, 60):
            with self.subTest(lines=count):
                pdf = render_a4(document(count))
                stream = b''.join(page_streams(pdf))
                self.assertEqual(stream.count(b'.8 w'), len(page_texts(pdf)) + 1)
                self.assertIn(b'.5 w', stream)
        self.assertEqual(len(page_texts(render_a4(document(60)))), 3)


class ClosingGroupTest(SimpleTestCase):
    def placement(self, texts):
        """Page index of each closing text (each must be whole on one page)."""
        where = {}
        for text in CLOSING:
            found = [n for n, page in enumerate(texts) if text in page]
            self.assertEqual(len(found), 1, f'{text!r} on pages {found}')
            where[text] = found[0]
        return where

    def test_the_closing_blocks_stay_with_the_totals_or_move_together(self):
        for count in range(1, 40):
            for spacing in (0.75, 1.0, 1.3):
                with self.subTest(lines=count, spacing=spacing):
                    texts = pages(document(count, closing=True, spacing=spacing))
                    where = self.placement(texts)
                    total = next(n for n, page in enumerate(texts) if 'IMPORTE TOTAL' in page)
                    self.assertEqual(len(set(where.values())), 1, where)            # one page for all of them
                    self.assertIn(set(where.values()).pop(), (total, total + 1))
                    self.assertEqual(len(texts), max(where.values()) + 1)           # nothing after them

    def test_a_small_overflow_is_absorbed_by_tighter_spacing(self):
        """Some sales fit on one page only because the closing blocks tightened: none needs a second sheet."""
        absorbed = []
        for count in range(1, 30):
            described = document(count, closing=True, spacing=1.3)
            fitted = len(pages(described))
            with mock.patch.object(note_layout, '_closing_steps', lambda k: [k]):
                loose = len(pages(described))
            self.assertLessEqual(fitted, loose)
            if fitted < loose:
                absorbed.append(count)
        self.assertTrue(absorbed, 'no sale was saved a page by the tighter spacing')

    def test_the_type_never_shrinks(self):
        """Whatever the spacing (and when it has stepped down to fit), each block prints at its own size."""
        for count, spacing in ((1, 0.75), (1, 1.3), *((n, 1.3) for n in range(8, 16))):
            with self.subTest(lines=count, spacing=spacing):
                stream = b''.join(page_streams(render_a4(document(count, closing=True, spacing=spacing))))
                for text, size in CLOSING_SIZES:
                    self.assertEqual(drawn_at(stream, text)[1], size, text)

    def middle_band(self):
        """Notes long enough that the group fits a fresh page only at its tightest spacing."""
        from reportlab.lib.pagesizes import A4

        from store.document_layout import _MARGIN_CM, _bottom_margin

        usable = A4[0] - 2 * _MARGIN_CM * 28.3465
        from store.document_layout import _CLOSING_ORDER

        styles = note_layout._styles()
        for repeats in range(20, 200, 4):
            described = document(1, closing=True, spacing=1.3,
                                 notes=NOTES + ' ' + 'Texto de relleno para la observación larga. ' * repeats)
            page = A4[1] - _MARGIN_CM * 28.3465 - _bottom_margin(described, usable) - 12

            def height(spacing):
                blocks = note_layout._closing_blocks(described, styles, spacing, _CLOSING_ORDER)
                return note_layout._Closing._measure(blocks, usable)[1]

            if height(0.75) <= page < height(1.3):
                return described
        self.fail('no notes length puts the group in the middle band')

    def test_a_group_that_fits_a_page_only_when_tightened_moves_whole_and_tightens(self):
        described = self.middle_band()
        texts = pages(described)
        where = self.placement(texts)
        self.assertEqual(len(set(where.values())), 1, where)                 # every block on one page
        self.assertEqual(len(texts), 2)
        self.assertTrue(texts[1].startswith(flat('OBSERVACIONES ' + NOTES)), texts[1][:80])
        # The long notes are whole on that page too.
        self.assertEqual(flat(texts[1]).count('Texto de relleno para la observación larga.'),
                         flat(described['notes']).count('Texto de relleno para la observación larga.'))

    def test_a_group_opening_a_page_drops_the_space_above_its_first_block(self):
        described = self.middle_band()
        pdf = render_a4(described)
        second = page_streams(pdf)[1]
        # The first thing the page draws is the notes heading, at the top of the frame (no spacer above).
        first_text = re.search(rb'1 0 0 1 [\d.-]+ ([\d.-]+) cm', second)
        self.assertIsNotNone(first_text)
        full = described.copy()
        with mock.patch.object(note_layout._Closing, '_blocks', lambda self, spacing, at_top: self._build(spacing)):
            loose = page_streams(render_a4(full))[1]
        self.assertNotEqual(second, loose)                                   # the spacer was dropped

    def test_a_group_taller_than_a_page_flows_block_by_block(self):
        long_notes = 'Observación larga de prueba con muchas palabras para llenar renglones. ' * 60
        texts = pages(document(30, closing=True, notes=long_notes))
        joined = ' '.join(texts)
        for text in (THANKS, POST_SALE, COMMERCIAL, WARRANTY):
            self.assertEqual(joined.count(text), 1)
        self.assertEqual(flat(joined).count('Observación larga de prueba'), 60)

    def test_one_alignment_for_every_closing_block(self):
        """Left margin for all: each block starts at x 0 inside its group, whatever the order."""
        for order in (['notes', 'warranty', 'thank_you', 'post_sale', 'commercial'],
                      ['commercial', 'thank_you', 'warranty', 'notes', 'post_sale']):
            with self.subTest(order=order):
                stream = b''.join(page_streams(render_a4(document(3, closing=True, closing_order=order))))
                for text, _size in CLOSING_SIZES:
                    with self.subTest(block=text):
                        self.assertEqual(drawn_at(stream, text)[0], 0.0)

    def test_a_single_block_taller_than_a_page_still_prints_whole(self):
        """Pinned: one block (notes) longer than a sheet is not kept together — its paragraph splits."""
        notes = NOTES + ' ' + 'Palabra de relleno. ' * 1500
        texts = pages(document(2, closing=True, notes=notes))
        self.assertGreaterEqual(len(texts), 3)
        self.assertEqual(' '.join(texts).count('Palabra'), 1500)       # a page break may fall inside one
        for text in (THANKS, POST_SALE, COMMERCIAL, WARRANTY):
            self.assertEqual(' '.join(texts).count(text), 1)


class RollClosingAirTest(SimpleTestCase):
    def baselines(self, order, spacing='airy'):
        from store import document_preview
        from store.document_design import DocumentDesign
        from store.test_document_design_v2 import FullRecordingCanvas
        from store.ticket_services import _lay_out

        design = DocumentDesign(thank_you_message=THANKS, block_order=('customer', 'sale', *order), density=spacing)
        with mock.patch.object(document_preview, '_identity') as identity, \
                mock.patch.object(document_preview, '_presentation', return_value={
                    'logo_png': None, 'branch_name': '', 'branch_address': '', 'branch_phone': '',
                    'payment_label': 'Efectivo', 'channel_label': '', 'seller_name': ''}), \
                mock.patch.object(document_preview, '_accent', return_value=''):
            identity.return_value = mock.Mock(name='', legal_name='', tax_id='', legal_address='', city='', phone='',
                                              contact_email='', website_url='', warranty_policy_text=WARRANTY,
                                              warranty_policy_url='')
            ctx = document_preview._sales_note_ctx(None, document_preview._lines(0), design)
        canvas = FullRecordingCanvas()
        _lay_out(canvas, ctx, 204.1, 11.34, 2000.0)
        return {text: y for _x0, _x1, y, text in canvas.drawn}

    def test_the_warranty_after_the_thanks_gets_the_air_of_a_message(self):
        drawn = self.baselines(['thank_you', 'warranty', 'notes', 'post_sale', 'commercial'])
        distance = drawn[THANKS] - drawn[WARRANTY]
        # The thanks' step (9.5 × 1.35) less its size, plus the warranty's size, plus 6 pt × airy (1.3).
        self.assertAlmostEqual(distance, 9.5 * 1.35 - 9.5 + 6.5 + 6 * 1.3, places=2)

    def test_after_the_closing_rule_the_warranty_needs_no_extra_air(self):
        default = self.baselines(['warranty', 'notes', 'thank_you', 'post_sale', 'commercial'])
        distance = default['Productos / unidades'] - default[WARRANTY]
        # The counts row's step (7.5 × 1.35) less its size, the closing rule (5 pt each side × airy),
        # plus the warranty's size — and nothing else.
        self.assertAlmostEqual(distance, 7.5 * 1.35 - 7.5 + 2 * 5 * 1.3 + 6.5, places=2)
