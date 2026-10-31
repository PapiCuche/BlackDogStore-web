"""
DOC-UX-02 (PR-A) — a paper must not break, and must not lie, because of the TEXT it is given.

Four things the audit found by trying them:

  1. The fiscal A4 handed free text to reportlab's `Paragraph` as MARKUP. A product
     called `Case <b>Pro` raised ValueError → 500 «No se pudo generar la
     representación impresa», and a name with `&` or `<i>` printed as a style.
  2. `_Cursor.row` of the 80 mm roll drew label and value without measuring: a long
     seller name or payment method printed one over the other.
  3. A quantity of 3 printed as `3.00` / `1.00 NIU` on the fiscal A4, the fiscal roll
     and the thermal print. (Presentation only: the XML and the stored rows keep
     the Decimal; see `document_style.quantity`.)
  4. A character outside the built-in font's WinAnsi coverage (`₂`, an emoji) came
     out as a black box: reportlab falls back to ZapfDingbats and draws its
     «■». See `document_style.printable` for what replaces it.
"""
from decimal import Decimal

from django.test import SimpleTestCase

from store.models import Company, FiscalDocument, Product
from store.printing.escpos import fiscal_ticket
from store.test_fiscal_print import FiscalPrintBase


def squashed(text: str) -> str:
    """Without spaces: reportlab draws a Paragraph in fragments and the extraction joins them with one."""
    return text.replace(' ', '')


class FiscalMarkupTest(FiscalPrintBase):
    """Everything a person types is TEXT on the fiscal paper, never markup."""

    def issue_with_hostile_text(self):
        Company.objects.filter(pk=self.company.pk).update(legal_name='Casa A & B <i>SAC</i>')
        self.company.refresh_from_db()                # the order reads `order.company`
        Product.objects.filter(pk=self.product.pk).update(name='Case <b>Pro')
        document = self.issue(receipt='factura', document_type='ruc', document_number='20000000001',
                              name='Cliente <u>& Hijos')
        FiscalDocument.objects.filter(pk=document.pk).update(
            issuer_trade_name='Marca <font size=40>X', issuer_address='Av. A & B 123 <br/> piso 2')
        document.refresh_from_db()
        return document

    def test_an_unclosed_tag_in_a_description_prints_literally_instead_of_failing(self):
        document = self.issue_with_hostile_text()

        text = self.a4(document)                     # it raised ValueError before

        self.assertIn('Case<b>Pro', squashed(text))

    def test_every_free_text_of_the_a4_is_printed_as_typed(self):
        document = self.issue_with_hostile_text()

        text = self.a4(document)

        for typed in ('Casa A & B <i>SAC</i>', 'Marca <font size=40>X', 'Av. A & B 123 <br/> piso 2',
                      'Cliente <u>& Hijos', 'Case <b>Pro'):
            self.assertIn(squashed(typed), squashed(text), typed)

    def test_the_roll_and_the_thermal_print_already_took_it_as_text(self):
        document = self.issue_with_hostile_text()

        self.assertIn('Case <b>Pro', self.ticket(document))
        self.assertIn(b'Case <b>Pro', fiscal_ticket(document))

    def test_the_markup_escape_does_not_change_an_ordinary_document(self):
        document = self.issue()

        text = self.a4(document)

        for ordinary in ('TIENDA IMPRESA SAC', 'Funda de silicona', 'Rosa Quispe Mamani', 'B001-1'):
            self.assertIn(ordinary, text)


class RecordingCanvas:
    """Just enough of a reportlab canvas to know where each string was put."""

    def __init__(self):
        self.drawn = []              # (x_left, x_right, baseline, text)
        self._font = ('Helvetica', 7.5)

    def setFont(self, name, size):
        self._font = (name, size)

    def _width(self, text):
        from reportlab.pdfbase.pdfmetrics import stringWidth
        return stringWidth(text, *self._font)

    def drawString(self, x, y, text):
        self.drawn.append((x, x + self._width(text), y, text))

    def drawRightString(self, x, y, text):
        self.drawn.append((x - self._width(text), x, y, text))

    def drawCentredString(self, x, y, text):
        half = self._width(text) / 2
        self.drawn.append((x - half, x + half, y, text))


class TicketRowTest(SimpleTestCase):
    LEFT, WIDTH = 11.34, 204.1                       # 72 mm of paper, 4 mm of margin

    def cursor(self):
        from store.ticket_services import _Cursor

        canvas = RecordingCanvas()
        return canvas, _Cursor(canvas, self.LEFT, 400.0, self.WIDTH)

    def assert_no_overlap_and_inside(self, canvas):
        for index, (x0, x1, y, text) in enumerate(canvas.drawn):
            self.assertGreaterEqual(x0, self.LEFT - 0.01, f'{text!r} leaves the paper on the left')
            self.assertLessEqual(x1, self.LEFT + self.WIDTH + 0.01, f'{text!r} leaves the paper on the right')
            for other in canvas.drawn[index + 1:]:
                if abs(other[2] - y) < 0.5:          # the same line of the roll
                    self.assertTrue(x1 <= other[0] or other[1] <= x0,
                                    f'{text!r} overprints {other[3]!r}')

    def test_a_short_row_keeps_label_left_and_value_right_on_one_line(self):
        canvas, cur = self.cursor()

        cur.row('Fecha:', '09/03/2026 21:30', size=6.5)

        self.assertEqual([entry[3] for entry in canvas.drawn], ['Fecha:', '09/03/2026 21:30'])
        self.assertEqual(len({entry[2] for entry in canvas.drawn}), 1)
        self.assertAlmostEqual(cur.used, 6.5 * 1.35)

    def test_a_long_value_does_not_print_over_its_label(self):
        canvas, cur = self.cursor()

        cur.row('Atendido por:', 'María Fernanda de los Ángeles Quispe Mamani de Valdivia', size=6.5)

        self.assert_no_overlap_and_inside(canvas)
        self.assertIn('Atendido por:', ' '.join(entry[3] for entry in canvas.drawn))
        joined = ' '.join(entry[3] for entry in canvas.drawn)
        self.assertIn('Valdivia', joined)                      # nothing was cut off
        self.assertGreater(cur.used, 6.5 * 1.35)               # the roll grew by the lines it needed

    def test_a_long_label_does_not_print_over_its_value(self):
        canvas, cur = self.cursor()

        cur.row('Comprobante solicitado por el cliente al momento de pagar:', 'Boleta', size=6.5)

        self.assert_no_overlap_and_inside(canvas)
        self.assertIn('Boleta', [entry[3] for entry in canvas.drawn])

    def test_label_and_value_both_long_stack_without_losing_a_word(self):
        canvas, cur = self.cursor()
        label = 'Método de pago acordado con el cliente en la tienda'
        value = 'Transferencia interbancaria desde la cuenta de la empresa del cliente'

        cur.row(label, value, size=6.5)

        self.assert_no_overlap_and_inside(canvas)
        words = ' '.join(entry[3] for entry in canvas.drawn).split()
        for word in (label + ' ' + value).split():
            self.assertIn(word, words)

    def test_one_unbroken_word_is_split_to_fit_the_paper(self):
        canvas, cur = self.cursor()

        cur.row('Serie:', 'X' * 80, size=6.5)

        self.assert_no_overlap_and_inside(canvas)
        self.assertEqual(''.join(entry[3] for entry in canvas.drawn).count('X'), 80)

    def test_measuring_and_drawing_agree_on_the_height(self):
        from store.ticket_services import _Cursor

        for label, value in (('Fecha:', '09/03/2026'), ('Atendido por:', 'Ñ ' * 60),
                             ('L' * 90, 'v'), ('Etiqueta larga de prueba ' * 4, 'Valor largo de prueba ' * 4)):
            measuring = _Cursor(None, self.LEFT, 400.0, self.WIDTH)
            measuring.row(label, value, size=6.5)
            _canvas, drawing = self.cursor()
            drawing.row(label, value, size=6.5)
            self.assertAlmostEqual(measuring.used, drawing.used, msg=label[:20])


class QuantityTest(FiscalPrintBase):
    """3 is written 3 on every printed representation; the XML keeps what it had."""

    def test_the_helper_writes_a_whole_quantity_without_decimals(self):
        from store.document_style import quantity

        self.assertEqual(quantity(Decimal('3.00')), '3')
        self.assertEqual(quantity(Decimal('1.00')), '1')
        self.assertEqual(quantity(7), '7')
        self.assertEqual(quantity(Decimal('10')), '10')              # never «1E+1»
        self.assertEqual(quantity(Decimal('1E+3')), '1000')

    def test_the_helper_keeps_a_real_fraction_to_the_last_significant_digit(self):
        from store.document_style import quantity

        self.assertEqual(quantity(Decimal('1.50')), '1.5')
        self.assertEqual(quantity(Decimal('0.125')), '0.125')
        self.assertEqual(quantity(Decimal('2.500')), '2.5')

    def test_the_fiscal_a4_prints_3_and_not_3_00(self):
        document = self.issue(quantity=3)

        text = self.a4(document)

        self.assertIn('3 NIU', text)
        self.assertNotIn('3.00', text.replace('300.00', '').replace('354.00', ''))

    def test_the_fiscal_roll_prints_3_niu_and_not_3_00_niu(self):
        document = self.issue(quantity=3)

        text = self.ticket(document)

        self.assertIn('3 NIU x S/ 118.00', text)
        self.assertNotIn('3.00 NIU', text)

    def test_the_thermal_print_prints_1_niu_and_not_1_00_niu(self):
        document = self.issue(quantity=1)

        raw = fiscal_ticket(document)

        self.assertIn(b' 1 NIU x S/ 118.00', raw)
        self.assertNotIn(b'1.00 NIU', raw)

    def test_printing_leaves_the_signed_xml_and_the_row_as_they_were(self):
        document = self.issue(quantity=3)
        before = (document.signed_xml, document.digest_value, document.total)

        self.a4(document)
        self.ticket(document)
        fiscal_ticket(document)
        document.refresh_from_db()

        self.assertEqual((document.signed_xml, document.digest_value, document.total), before)
        self.assertRegex(document.signed_xml, r'<cbc:InvoicedQuantity[^>]*>3(\.0+)?</cbc:InvoicedQuantity>')


class CharsetTest(SimpleTestCase):
    """What the built-in font cannot draw is replaced by something safe, never by «■»."""

    def test_printable_keeps_everything_winansi_can_draw(self):
        from store.document_style import printable

        text = 'Cámara 12 MP · Garantía — “ñandú” € ™ …'
        self.assertEqual(printable(text), text)

    def test_printable_keeps_the_readable_part_of_a_compatibility_character(self):
        from store.document_style import printable

        self.assertEqual(printable('CO₂ y H₂O'), 'CO2 y H2O')
        self.assertEqual(printable('Nº 5 №7 ﬁbra'), 'Nº 5 No7 fibra')

    def test_printable_turns_what_has_no_reading_into_one_question_mark(self):
        from store.document_style import printable

        self.assertEqual(printable('Funda 😀 azul'), 'Funda ? azul')
        self.assertEqual(printable('👍🏽'), '?')                 # one emoji with its skin tone is one «?»
        self.assertEqual(printable('Hola 世界'), 'Hola ?')
        self.assertEqual(printable('a​b️c'), 'abc')    # invisible marks vanish

    def test_an_a4_with_those_characters_draws_no_fallback_glyph(self):
        from store.document_layout import render_a4
        from store.test_document_design import text_of

        pdf = render_a4(_minimal_document(description='Funda CO₂ 😀 azul'))

        self.assertNotIn(b'ZapfDingbats', pdf)
        self.assertIn('Funda CO2 ? azul', text_of(pdf))

    def test_the_roll_draws_no_fallback_glyph_either(self):
        from reportlab.pdfgen import canvas as pdfcanvas

        import io

        from store.ticket_services import _Cursor
        from store.test_document_design import text_of

        buffer = io.BytesIO()
        pdf = pdfcanvas.Canvas(buffer, pagesize=(226.8, 200))
        cur = _Cursor(pdf, 11.3, 190, 204)
        cur.line('Funda CO₂ 😀 azul', size=7)
        cur.row('Etiqueta ₂', 'Valor 😀', size=7)
        pdf.showPage()
        pdf.save()

        self.assertNotIn(b'ZapfDingbats', buffer.getvalue())
        text = text_of(buffer.getvalue())
        self.assertIn('Funda CO2 ? azul', text)
        self.assertIn('Etiqueta 2', text)

    def test_the_thermal_print_degrades_the_same_way(self):
        from store.printing.escpos import Receipt

        receipt = Receipt()
        receipt.line('CO₂ 😀')

        self.assertIn(b'CO2 ?', receipt.to_bytes())


def _minimal_document(*, description='Funda'):
    return {
        'title': 'NOTA DE VENTA', 'number': 'NV-000001', 'identity': {'name': 'Tienda'},
        'lines': [{'number': 1, 'code': '', 'description': description, 'units': [], 'unit': 'UND',
                   'list_price': Decimal('10.00'), 'discount': Decimal('0.00'),
                   'unit_price': Decimal('10.00'), 'quantity': 1, 'amount': Decimal('10.00')}],
        'totals': [], 'total': 'S/ 10.00',
    }
