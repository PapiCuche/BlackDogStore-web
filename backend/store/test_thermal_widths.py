"""
THERMAL-512 — the printable width of each thermal printer.

An 80 mm roll is not always printed edge to edge: many 80 mm heads print 72 mm (576 dots, 48 columns of
Font A), but some print only 64 mm (512 dots, 42 columns). A 58 mm roll prints 48 mm (384 dots, 32 columns).
A 576-dot image or a 48-column line on a 512-dot head is clipped or wraps by itself, and the columns of the
ticket break. Each printer now says its printable width (`Printer.print_width_dots`; 0 = by its roll, as
before), and the ticket is laid out for it:

  * every line fits the columns (a double-size one half of them), and no text is lost: the same characters,
    in the same order, whatever the width;
  * no image is wider than the dots (a logo shrinks keeping its proportions, never taller than 24 mm);
  * the QR, with its quiet zone, fits the dots;
  * the default (576 dots, 48 columns on 80 mm; 384 dots, 32 columns on 58 mm) prints the same bytes as before:
    the goldens of `escpos.json` do not move.

NOT VERIFIED ON A PRINTER: the bytes are read back with `printing.escpos_reader`.
"""
from decimal import Decimal

import qrcode
from django.test import SimpleTestCase

from store.models import Printer
from store.printing import escpos, escpos_reader
from store.printing.escpos import Receipt
from store.test_document_goldens import PinnedSales, UPDATE, check_escpos_golden
from store.test_escpos_logo import png
from store import test_fiscal_escpos_design as fiscal_escpos_tests
from store.test_fiscal_escpos_design import FiscalEscposBase
from store.test_fiscal_design import LONG, LONG_MESSAGES, MESSAGES
from store.test_sales_note_parity import DiscountSales, _order_id

WIDTHS = (576, 512, 384)
BIG = Decimal('9999999.99')


def qr_modules(payload: str) -> int:
    """The side of the QR the printer draws for `payload` (model 2, correction M, byte mode), in modules."""
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=0)
    code.add_data(qrcode.util.QRData(payload.encode('utf-8'), mode=qrcode.util.MODE_8BIT_BYTE))
    code.make(fit=True)
    return code.modules_count


def assert_fits(test, data: bytes, dots: int) -> None:
    """Every line within the columns of `dots`, every image within its dots, every QR (and its quiet zone) too."""
    columns = escpos.PRINT_WIDTHS[dots]
    for item in escpos_reader.read(data):
        if not isinstance(item, escpos_reader.Line):
            continue
        text = ''.join(piece for piece in item.pieces if isinstance(piece, str))
        test.assertLessEqual(len(text), columns // 2 if item.large else columns, f'{dots} dots: {text!r}')
        for piece in item.pieces:
            if isinstance(piece, escpos_reader.Raster):
                test.assertLessEqual(piece.width, dots)
                test.assertLessEqual(piece.height, escpos.MAX_IMAGE_HEIGHT_DOTS)
            elif isinstance(piece, escpos_reader.QR):
                test.assertGreaterEqual(piece.module, 1)
                test.assertLessEqual(piece.module * (qr_modules(piece.payload) + 2 * escpos.QR_QUIET_MODULES), dots,
                                     f'{dots} dots: QR of {len(piece.payload)} bytes, module {piece.module}')


def said(data: bytes) -> str:
    """Every printed character but the spaces, in order: rules (they follow the width) and images left out."""
    out = []
    for item in escpos_reader.read(data):
        if not isinstance(item, escpos_reader.Line):
            continue
        for piece in item.pieces:
            if isinstance(piece, escpos_reader.QR):
                out.append(f'<QR {piece.payload}>')
            elif isinstance(piece, str) and piece.strip('-'):
                out.append(''.join(piece.split()))
    return ''.join(out)


def rules(data: bytes) -> set:
    return {piece for item in escpos_reader.read(data) if isinstance(item, escpos_reader.Line)
            for piece in item.pieces if isinstance(piece, str) and piece and not piece.strip('-')}


class WidthTableTest(SimpleTestCase):
    def test_the_three_widths_and_their_columns(self):
        self.assertEqual(escpos.PRINT_WIDTHS, {576: 48, 512: 42, 384: 32})

    def test_the_printable_dots_of_a_printer(self):
        self.assertEqual(escpos.printable_dots(80), 576)
        self.assertEqual(escpos.printable_dots(58), 384)
        self.assertEqual(escpos.printable_dots(80, 0), 576)
        self.assertEqual(escpos.printable_dots(80, 512), 512)
        self.assertEqual(escpos.printable_dots(80, 576), 576)
        self.assertEqual(escpos.printable_dots(58, 384), 384)
        # Never wider than its roll, and an unknown value is the roll's.
        self.assertEqual(escpos.printable_dots(58, 576), 384)
        self.assertEqual(escpos.printable_dots(58, 512), 384)
        self.assertEqual(escpos.printable_dots(80, 999), 576)
        self.assertEqual(escpos.printable_dots(99), 576)

    def test_a_receipt_takes_its_columns_from_its_dots(self):
        for dots, columns in escpos.PRINT_WIDTHS.items():
            with self.subTest(dots=dots):
                roll = Receipt(dots=dots)
                self.assertEqual((roll.dots, roll.columns), (dots, columns))
                self.assertIn('-' * columns, escpos_reader.text(roll.rule().to_bytes()))
        self.assertEqual((Receipt().dots, Receipt().columns), (576, 48))
        self.assertEqual((Receipt(paper_width_mm=58).dots, Receipt(paper_width_mm=58).columns), (384, 32))
        # A 58 mm roll is never laid out wider than it is.
        self.assertEqual(Receipt(paper_width_mm=58, dots=512).dots, 384)

    def test_the_default_dots_print_the_same_bytes_as_before(self):
        def roll(**kw):
            return (Receipt(**kw).image(png((600, 100))).line('Tienda', align='center', bold=True)
                    .line('NV-000001', align='center', bold=True, large=True).row('TOTAL', 'S/ 9999999.99', large=True)
                    .row(' 999.5 NIU x S/ 9999999.99', 'S/ 9999999.99').rule().qr('pay|load').cut().to_bytes())

        self.assertEqual(roll(dots=576), roll())
        self.assertEqual(roll(paper_width_mm=80, dots=576), roll(paper_width_mm=80))
        self.assertEqual(roll(paper_width_mm=58, dots=384), roll(paper_width_mm=58))


class ReceiptAtEachWidthTest(SimpleTestCase):
    def test_a_wide_logo_shrinks_to_the_dots_keeping_its_proportions(self):
        for dots in WIDTHS:
            with self.subTest(dots=dots):
                data = Receipt(dots=dots).image(png((1200, 200)), max_dots=72 * 8, enlarge=True).to_bytes()
                (raster,) = [p for i in escpos_reader.read(data) if isinstance(i, escpos_reader.Line)
                             for p in i.pieces if isinstance(p, escpos_reader.Raster)]
                self.assertLessEqual(raster.width, dots)
                self.assertGreaterEqual(raster.width, dots - 8)
                self.assertAlmostEqual(raster.width / raster.height, 6, delta=0.15)

    def test_a_tall_logo_is_never_taller_than_24_mm(self):
        for dots in WIDTHS:
            with self.subTest(dots=dots):
                data = Receipt(dots=dots).image(png((300, 900)), max_dots=72 * 8, max_height=400,
                                                enlarge=True).to_bytes()
                assert_fits(self, data, dots)
                (raster,) = [p for i in escpos_reader.read(data) if isinstance(i, escpos_reader.Line)
                             for p in i.pieces if isinstance(p, escpos_reader.Raster)]
                self.assertEqual(raster.height, escpos.MAX_IMAGE_HEIGHT_DOTS)

    def test_the_qr_keeps_its_module_when_it_fits_and_shrinks_when_it_does_not(self):
        sunat = '20100066603|03|B001|1|18.00|118.00|2026-01-01|1|45678912|' + 'x' * 28 + '='
        for dots in WIDTHS:
            with self.subTest(dots=dots):
                data = Receipt(dots=dots).qr(sunat).to_bytes()
                assert_fits(self, data, dots)
                (qr,) = [p for i in escpos_reader.read(data) if isinstance(i, escpos_reader.Line)
                         for p in i.pieces if isinstance(p, escpos_reader.QR)]
                self.assertEqual(qr.module, 6)                     # a SUNAT payload fits every width at 6
        long = 'A' * 400                                            # version 15 at M: 77 modules
        for dots in WIDTHS:
            with self.subTest(dots=dots, payload='long'):
                data = Receipt(dots=dots).qr(long).to_bytes()
                assert_fits(self, data, dots)
        (qr,) = [p for i in escpos_reader.read(Receipt(dots=384).qr(long).to_bytes())
                 if isinstance(i, escpos_reader.Line) for p in i.pieces if isinstance(p, escpos_reader.QR)]
        self.assertLess(qr.module, 6)

    def test_an_amount_is_never_split_across_lines(self):
        for dots in WIDTHS:
            with self.subTest(dots=dots):
                data = (Receipt(dots=dots).row(' 999.5 NIU x S/ 9999999.99', 'S/ 9999999.99')
                        .row(' 1 x US$ 9999999.99', 'US$ 9999999.99')
                        .row('TOTAL', 'S/ 9999999.99', bold=True, large=True).to_bytes())
                assert_fits(self, data, dots)
                lines = escpos_reader.text(data)
                self.assertEqual(sum(line.count('S/ 9999999.99') for line in lines), 3, lines)
                self.assertEqual(sum(line.count('US$ 9999999.99') for line in lines), 2, lines)
                self.assertTrue(any(line.rstrip().endswith('S/ 9999999.99') for line in lines))

    def test_rows_stay_right_aligned(self):
        for dots, columns in escpos.PRINT_WIDTHS.items():
            with self.subTest(dots=dots):
                lines = escpos_reader.text(Receipt(dots=dots).row('Subtotal', 'S/ 1234.50').to_bytes())
                self.assertEqual(lines, ['Subtotal' + ' ' * (columns - 8 - 10) + 'S/ 1234.50'])

    def test_the_cut_sequence_is_unchanged(self):
        for dots in WIDTHS:
            with self.subTest(dots=dots):
                self.assertTrue(Receipt(dots=dots).line('x').cut().to_bytes().endswith(b'\x1bd\x03\x1dV\x42\x00'))


# ---------------------------------------------------------------------------
# The sales note
# ---------------------------------------------------------------------------

class SalesNoteWidthsTest(DiscountSales):
    def assert_every_width(self, ctx):
        printed = {dots: escpos.sales_note_ticket_from_ctx(ctx, dots=dots) for dots in WIDTHS}
        for dots, data in printed.items():
            with self.subTest(dots=dots):
                assert_fits(self, data, dots)
                self.assertEqual(said(data), said(printed[576]))
                self.assertEqual(rules(data), {'-' * escpos.PRINT_WIDTHS[dots]})
        return printed

    def big_ctx(self, ctx):
        """`ctx` (a `build_sales_note_context`) with long names and 9 999 999.99 in every amount."""
        items = []
        for item in ctx['items']:
            discounted = item['discount'] > 0
            items.append(dict(item, name=f"{item['name']} {LONG}", list_price=BIG, regular_amount=BIG,
                              discount=BIG if discounted else item['discount'], subtotal=BIG))
        tax = dict(ctx['tax'], subtotal=BIG, total=BIG)
        return dict(
            ctx, items=items, tax=tax, logo_png=png((1200, 200)),
            customer_name='Cliente de prueba con nombres y apellidos muy largos ' * 2,
            store_name='TIENDA DE PRUEBA CON UN NOMBRE COMERCIAL MUY LARGO', notes='Entregar en recepción. ' * 12,
            warranty_note='Garantía de doce meses. ' * 6, seller_name='Vendedora con un nombre bastante largo',
            discount_rows=[(label, BIG) for label, _amount in ctx['discount_rows']],
            cash_rows=[(label, BIG) for label, _amount in ctx['cash_rows']])

    def test_a_promotion_paid_in_cash_fits_every_width(self):
        from store.sales_note_services import build_sales_note_context

        _order, note = self.promotion_sale()
        ctx = build_sales_note_context(note)
        self.assertTrue(ctx['cash_rows'])
        self.assert_every_width(ctx)
        printed = self.assert_every_width(self.big_ctx(ctx))
        for dots, data in printed.items():
            with self.subTest(dots=dots):
                lines = escpos_reader.text(data)
                self.assertGreaterEqual(sum(line.count('S/ 9999999.99') for line in lines), 8)

    def test_a_coupon_sale_fits_every_width(self):
        from store.sales_note_services import build_sales_note_context

        _order, note = self.coupon_sale()
        ctx = build_sales_note_context(note)
        self.assertTrue(ctx['discount_rows'])
        self.assert_every_width(ctx)
        self.assert_every_width(self.big_ctx(ctx))

    def test_the_512_goldens(self):
        _order, note = self.promotion_sale()
        check_escpos_golden(self, 'sales_note_escpos_promotion_512', escpos.sales_note_ticket(note, dots=512),
                            normalise=_order_id)
        _order, note = self.coupon_sale()
        check_escpos_golden(self, 'sales_note_escpos_coupon_512', escpos.sales_note_ticket(note, dots=512),
                            normalise=_order_id)


class SalesNoteDeviceWidthsTest(PinnedSales):
    def test_a_device_sale_with_its_logo_fits_every_width(self):
        from store.models import CompanyDocumentDesign

        self.with_logo()
        CompanyDocumentDesign.objects.create(company=self.company, logo_ticket_width_mm=72, density='airy')
        _order, note = self.device_sale()
        printed = {dots: escpos.sales_note_ticket(note, dots=dots) for dots in WIDTHS}
        for dots, data in printed.items():
            with self.subTest(dots=dots):
                assert_fits(self, data, dots)
                self.assertEqual(said(data), said(printed[576]))

    def test_the_512_golden(self):
        _order, note = self.device_sale()
        lines = check_escpos_golden(self, 'sales_note_escpos_512', escpos.sales_note_ticket(note, dots=512))
        if not UPDATE:
            self.assertIn('-' * 42, lines)
            self.assertNotIn('-' * 48, lines)


# ---------------------------------------------------------------------------
# The boleta and the factura
# ---------------------------------------------------------------------------

class FiscalWidthsTest(FiscalEscposBase):
    def roll_normaliser(self, document):
        import re

        normalise = self.normaliser(document)
        return lambda line: re.sub(r'\d{4}-\d{2}-\d{2}', '<FECHA>', normalise(line))

    def assert_every_width(self, ctx):
        printed = {dots: escpos.fiscal_ticket_from_ctx(ctx, dots=dots) for dots in WIDTHS}
        for dots, data in printed.items():
            with self.subTest(dots=dots):
                assert_fits(self, data, dots)
                self.assertEqual(said(data), said(printed[576]))
        return printed

    def test_a_boleta_and_a_factura_with_messages_fit_every_width(self):
        for kind, document in (('boleta', self.receipt()), ('factura', self.invoice())):
            with self.subTest(kind=kind):
                self.assert_every_width(self.ctx(document, logo=png((1200, 200)), **MESSAGES,
                                                 logo_ticket_width_mm=72))

    def test_long_names_big_amounts_and_many_items_fit_every_width(self):
        # The stress ctx of the 48-column layout test (not imported: the runner would run its tests twice).
        ctx = fiscal_escpos_tests.FiscalEscposLayoutTest.big_ctx(self)
        ctx['logo_png'] = png((1200, 200))
        printed = self.assert_every_width(ctx)
        for dots, data in printed.items():
            with self.subTest(dots=dots):
                lines = escpos_reader.text(data)
                # Every amount whole on one line: 2 per item row (price, amount) and the three totals.
                self.assertEqual(sum(line.count('S/ 9999999.99') for line in lines), 2 * 36 + 3)
                squeezed = ''.join(''.join(lines).split())
                for whole in (LONG, *LONG_MESSAGES['thank_you_message'].split('\n'), LONG_MESSAGES['footer_text'],
                              f"Valor resumen: {ctx['digest_value']}", f"{ctx['legend']}."):
                    self.assertIn(''.join(whole.split()), squeezed)

    def pinned_roll(self, document, dots):
        """
        The roll of `document` with its digest as a fixed text of the same length. On 42 columns «Valor resumen:»
        and the digest wrap over two lines, where the per-line normaliser of the goldens cannot find it.
        """
        from store.fiscal_pdf_services import build_fiscal_context

        ctx = build_fiscal_context(document)
        digest = ctx['digest_value']
        pinned = 'X' * len(digest)
        ctx.update(digest_value=pinned, qr_payload=ctx['qr_payload'].replace(digest, pinned))
        return escpos.fiscal_ticket_from_ctx(ctx, dots=dots)

    def test_the_512_goldens(self):
        document = self.receipt()
        check_escpos_golden(self, 'fiscal_boleta_escpos_512', self.pinned_roll(document, 512),
                            normalise=self.roll_normaliser(document))
        self.save_design(self.company, **MESSAGES, logo_ticket_width_mm=50)
        document = self.invoice()
        check_escpos_golden(self, 'designed_fiscal_factura_escpos_512', self.pinned_roll(document, 512),
                            normalise=self.roll_normaliser(document))

    def test_the_pinned_digest_changes_nothing_else(self):
        document = self.receipt()
        digest = document.digest_value
        real = escpos_reader.text(escpos.fiscal_ticket(document, dots=576))
        pinned = escpos_reader.text(self.pinned_roll(document, 576))
        self.assertEqual([line.replace(digest, 'X' * len(digest)) for line in real], pinned)


# ---------------------------------------------------------------------------
# The printer
# ---------------------------------------------------------------------------

from store.test_printing_api import CLAIM, PRINTERS, PrintApiBase  # noqa: E402


class PrinterWidthTest(PrintApiBase):
    def claimed_bytes(self):
        import base64

        job = self.as_agent().post(CLAIM).data['job']
        return base64.b64decode(job['payload_base64'])

    def test_a_printer_prints_by_its_roll_until_it_is_told_otherwise(self):
        printer = Printer.objects.get(pk=self.printer.pk)
        self.assertEqual(printer.print_width_dots, 0)
        self.issue()
        data = self.claimed_bytes()
        assert_fits(self, data, 576)
        self.assertIn('-' * 48, escpos_reader.text(data))

    def test_a_512_dot_printer_gets_a_42_column_ticket(self):
        Printer.objects.filter(pk=self.printer.pk).update(print_width_dots=512)
        self.issue(receipt='factura', document_type='ruc', document_number='20000000001',
                   name='CLIENTE DE PRUEBA SAC')
        data = self.claimed_bytes()
        assert_fits(self, data, 512)
        lines = escpos_reader.text(data)
        self.assertIn('-' * 42, lines)
        self.assertNotIn('-' * 48, lines)

    def test_an_old_document_reprints_on_any_width(self):
        """A boleta issued while the printer printed 48 columns reprints whole on 42 and on a 58 mm roll."""
        from store.models import PrintJob
        from store.printing import services as printing

        document = self.issue()
        first = printing.render(printing.claim_next(self.agent))
        for paper, width in ((80, 512), (58, 0), (58, 384), (80, 576)):
            with self.subTest(paper=paper, width=width):
                Printer.objects.filter(pk=self.printer.pk).update(paper_width_mm=paper, print_width_dots=width)
                job = PrintJob.objects.select_related('printer').get(fiscal_document=document)
                data = printing.render(job)
                dots = escpos.printable_dots(paper, width)
                assert_fits(self, data, dots)
                self.assertEqual(said(data), said(first))

    def test_the_panel_shows_and_sets_the_width(self):
        client = self.as_user(self.manager)
        row = client.get(PRINTERS + self.q()).data['results'][0]
        self.assertEqual((row['print_width_dots'], row['printable_dots'], row['columns']), (0, 576, 48))

        res = client.patch(f'{PRINTERS}{self.printer.pk}/{self.q()}', {'print_width_dots': 512}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual((res.data['print_width_dots'], res.data['printable_dots'], res.data['columns']),
                         (512, 512, 42))

        res = client.patch(f'{PRINTERS}{self.printer.pk}/{self.q()}',
                           {'paper_width_mm': 58, 'print_width_dots': 0}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual((res.data['paper_width_mm'], res.data['printable_dots'], res.data['columns']), (58, 384, 32))

    def test_a_width_the_roll_cannot_print_is_refused(self):
        client = self.as_user(self.manager)
        url = f'{PRINTERS}{self.printer.pk}/{self.q()}'
        for body in ({'print_width_dots': 600}, {'print_width_dots': '512'}, {'print_width_dots': True},
                     {'print_width_dots': 384}, {'paper_width_mm': 58, 'print_width_dots': 512}):
            with self.subTest(body=body):
                res = client.patch(url, body, format='json')
                self.assertEqual(res.status_code, 400, res.data)
                self.assertIn('print_width_dots', res.data)
        Printer.objects.filter(pk=self.printer.pk).update(print_width_dots=512)
        # Moving a 512-dot printer to a 58 mm roll without saying its width is refused too.
        res = client.patch(url, {'paper_width_mm': 58}, format='json')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(Printer.objects.get(pk=self.printer.pk).paper_width_mm, 80)

    def test_a_new_printer_can_be_registered_at_512(self):
        res = self.as_user(self.manager).post(PRINTERS + self.q(), {
            'branch': self.branch.pk, 'name': 'Térmica 64 mm', 'host': '192.168.1.60', 'auto_print': False,
            'print_width_dots': 512,
        }, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(Printer.objects.get(pk=res.data['id']).print_width_dots, 512)
        self.assertEqual(res.data['columns'], 42)


# ---------------------------------------------------------------------------
# The preview
# ---------------------------------------------------------------------------

from store.test_document_preview_escpos import ThermalPreviewBase, texts  # noqa: E402


class ThermalPreviewWidthTest(ThermalPreviewBase):
    def test_the_preview_draws_the_width_asked_for(self):
        for dots, (paper, columns) in {576: (80, 48), 512: (80, 42), 384: (58, 32)}.items():
            with self.subTest(dots=dots):
                body = self.payload(print_width_dots=dots)
                self.assertEqual((body['paper_width_mm'], body['columns'], body['dots']), (paper, columns, dots))
                self.assertIn('-' * columns, texts(body))
                for line in body['lines']:
                    if line['type'] == 'text':
                        self.assertLessEqual(len(line['text']), columns // 2 if line['large'] else columns)
                    elif line['type'] == 'image':
                        self.assertLessEqual(line['width'], dots)

    def test_without_a_width_it_is_the_80_mm_roll_as_before(self):
        self.assertEqual(self.payload(), self.payload(print_width_dots=576))

    def test_a_width_no_printer_has_is_refused(self):
        for value in (600, '512', 0, True, None):
            with self.subTest(value=value):
                response = self.thermal(print_width_dots=value)
                self.assertEqual(response.status_code, 400)
                self.assertIn('print_width_dots', response.json())
