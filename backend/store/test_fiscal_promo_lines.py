"""
FISCAL-A4-PROMO-LINES — a boleta or factura whose lines carry a promotion must be reconcilable on paper.

A promotion is declared on each line it lowered: `cac:InvoiceLine/cac:AllowanceCharge` with
`ChargeIndicator=false` and code `00` of the Catálogo N.º 53 (ERP-FISCAL-6). On such a line the XML says

    cac:Price/cbc:PriceAmount             valor unitario BEFORE the discount, without IGV (n(12,10))
    cac:AllowanceCharge/cbc:Amount        the discount of the line, without IGV
    cbc:LineExtensionAmount               valor de venta AFTER the discount, without IGV
    cac:PricingReference/…/cbc:PriceAmount  precio unitario paid, WITH IGV (type 01, n(12,10))

and SUNAT checks Cant. × valor unitario − descuentos 00 = valor de venta (regla 3271). The A4 printed the
first, third and fourth but not the second, so «2 × 42.29 = 42.29» could not be read off the paper.

What is fixed here, and what is not allowed to move:

  * every figure of the A4 table comes from the signed XML, nothing is recomputed from the sale;
  * a document with a line discount gets a «Dscto. s/IGV» column and its last column reads «V. venta»
    (as on the roll); the other columns keep the order of a document without discounts; its unit values
    print with the decimals the XML carries, so the line adds up exactly;
  * a document WITHOUT line discounts prints the same text as before: the existing goldens (text of
    every page, page count and MediaBox — not the PDF bytes, which carry their creation date) are
    unchanged;
  * the 80 mm roll and the ESC/POS ticket already reconcile (Σ Cant. × P. unit. = TOTAL, Σ V. venta =
    Op. gravada) and do not change: their goldens are recorded here and must stay as they are;
  * the XML, its digest, the QR, the status, the series and number, the amounts, the legend and the BETA
    mark are untouched (a frozen pre-change golden proves every word printed before is still printed).
"""
import json
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path

from django.test import SimpleTestCase, override_settings

from store.fiscal.representation import parse_signed_invoice_for_representation
from store.fiscal_pdf_services import (
    build_fiscal_context, generate_fiscal_pdf, generate_fiscal_ticket_pdf, render_fiscal_a4,
)
from store.fiscal_services import get_or_create_fiscal_document, sign_fiscal_document
from store.fiscal.testing import self_signed_pem
from store.models import Customer, Order, PaymentMethod
from store.printing import escpos_reader
from store.test_document_design import page_texts
from store.test_document_goldens import (
    GOLDEN_FILE, FiscalFixtures, check_escpos_golden, check_golden,
)
from store.test_fiscal_design import A4_LEFT, CM, text_runs, tokens
from store.test_fiscal_print import ISSUER_RUC
from store.tests import _BETA_ISSUING, _C1_POS, _C12_DISCOUNT, _c1_product, _c1_sale, _c1_stock, _c13_combo, _p2d_member

PRE_CHANGE_FILE = Path(__file__).resolve().parent / 'goldens' / 'fiscal_pre_promo_lines.json'
CENT = Decimal('0.01')

#: One row of the A4 table as the text reader reads it: quantity, unit, description, then
#: V. unitario, P. unitario, [Dscto. s/IGV], V. venta. A line without a discount leaves that cell empty.
A4_ROW = r'{qty} {unit} {description} S/ (\S+) S/ (\S+)(?: S/ (\S+))? S/ (\S+)'


@override_settings(**_BETA_ISSUING)
class PromotedFiscalFixtures(FiscalFixtures):
    """
    The pinned fiscal fixtures (`FiscalFixtures`), plus sales made by the real counter path with a combo
    promotion, so the XML carries real line allowances (`_order_to_invoice_data`, ERP-FISCAL-6).
    """

    def setUp(self):
        super().setUp()
        branch = self.company.default_inventory_branch
        self.glass = _c1_product(self.company, 'Lámina de vidrio', '59.00')
        self.charger = _c1_product(self.company, 'Cargador 20 W', '99.90')
        self.cable = _c1_product(self.company, 'Cable USB-C', '39.90')
        for product in (self.glass, self.charger, self.cable):
            _c1_stock(branch, product, 50)
        _c13_combo(self.company, [(self.product, 1), (self.glass, 1)], fixed_price='139.90',
                   name='Funda y lámina')
        _c13_combo(self.company, [(self.cable, 3)], fixed_price='100.00', name='Tres cables')
        self.seller, _ = _p2d_member(
            self.company, 'promo_seller', ['company.view', _C1_POS, _C12_DISCOUNT, 'sales.fiscal.issue'])
        self.ruc_customer = Customer.objects.create(
            company=self.company, customer_type=Customer.TYPE_BUSINESS,
            business_name='CLIENTE DE PRUEBA SAC', document_type=Order.DocumentType.RUC,
            document_number='20000000001')

    def promoted(self, items, receipt_type):
        kw = {'customer': self.ruc_customer.pk} if receipt_type == Order.ReceiptType.FACTURA else {}
        order, _ = _c1_sale(
            actor=self.seller, company=self.company, branch=self.company.default_inventory_branch,
            items=[{'product': product.pk, 'quantity': quantity} for product, quantity in items],
            payment_method=PaymentMethod.CARD, receipt_type=receipt_type, **kw)
        document, _ = get_or_create_fiscal_document(order)
        key, cert = self_signed_pem(ISSUER_RUC)
        return sign_fiscal_document(document, key_pem=key, cert_pem=cert)

    def promoted_invoice(self):
        """Funda + lámina in the combo, a cable at its list price: two lines with a discount, one without."""
        return self.promoted([(self.product, 1), (self.glass, 1), (self.cable, 1)], Order.ReceiptType.FACTURA)

    def promoted_receipt(self):
        return self.promoted([(self.product, 1), (self.glass, 1), (self.charger, 1)], Order.ReceiptType.BOLETA)

    def three_cables(self):
        """Three cables for 100.00: the unit values of the XML carry ten decimals (33.8133333333)."""
        return self.promoted([(self.cable, 3)], Order.ReceiptType.FACTURA)

    @staticmethod
    def xml_lines(document):
        return parse_signed_invoice_for_representation(document.signed_xml).lines

    @staticmethod
    def flat_text(pdf: bytes) -> str:
        return ' '.join(page_texts(pdf))


def _qty(value: Decimal) -> str:
    from store import document_style

    return document_style.quantity(value)


class RepresentationReadsTheLineAllowanceTest(PromotedFiscalFixtures):
    def test_each_line_allowance_is_read_from_the_signed_xml(self):
        document = self.promoted_invoice()
        lines = self.xml_lines(document)
        self.assertEqual(len(lines), 3)
        for line in lines[:2]:
            (allowance,) = line.allowances
            self.assertFalse(allowance.charge)
            self.assertEqual(allowance.reason_code, '00')
            # BaseAmount is the line value before the discount, Amount the discount: what is left is the
            # valor de venta. Read, not derived: the three are three elements of the XML.
            self.assertEqual(allowance.base_amount - allowance.amount, line.line_amount)
            self.assertEqual(line.line_discount, allowance.amount)
        self.assertEqual(lines[2].allowances, ())
        self.assertIsNone(lines[2].line_discount)

    def test_a_document_without_discounts_reads_no_allowance(self):
        for line in self.xml_lines(self.invoice()):
            self.assertEqual(line.allowances, ())
            self.assertIsNone(line.line_discount)


class PromotedA4ReconcilesTest(PromotedFiscalFixtures):
    """The A4 of a promoted document, read back as text, adds up line by line and in the totals."""

    def printed_rows(self, document):
        text = self.flat_text(generate_fiscal_pdf(document))
        rows = []
        for line in self.xml_lines(document):
            pattern = A4_ROW.format(qty=re.escape(_qty(line.quantity)), unit=re.escape(line.unit_code),
                                    description=re.escape(line.description))
            match = re.search(pattern, text)
            self.assertIsNotNone(match, f'row of {line.description!r} not found in {text!r}')
            unit, price, discount, value = match.groups()
            rows.append({'xml': line, 'price': Decimal(price), 'unit': Decimal(unit),
                         'discount': Decimal(discount) if discount else None, 'value': Decimal(value)})
        return text, rows

    def assert_reconciles(self, document):
        text, rows = self.printed_rows(document)
        self.assertIn('Cant. Und. Descripción V. unitario P. unitario Dscto. s/IGV V. venta', text)
        for row in rows:
            line = row['xml']
            with self.subTest(line.description):
                # Every figure is the XML's own, with the decimals the XML has.
                self.assertEqual(str(row['unit']), str(line.unit_price))
                self.assertEqual(str(row['price']), str(line.unit_price_with_tax))
                self.assertEqual(row['value'], line.line_amount)
                self.assertEqual(row['discount'], line.line_discount)
                # Cant. × V. unitario − Dscto. = V. venta (regla 3271), to the cent of the XML's amounts.
                gross = (line.quantity * row['unit']).quantize(CENT)
                self.assertEqual(gross - (row['discount'] or 0), row['value'])
        taxable = Decimal(re.search(r'Op\. gravada: S/ (\S+)', text).group(1))
        self.assertEqual(taxable, sum(row['value'] for row in rows))
        self.assertTrue(any(row['discount'] for row in rows))
        return rows

    def test_a_promoted_factura_reconciles(self):
        rows = self.assert_reconciles(self.promoted_invoice())
        self.assertEqual([row['discount'] is not None for row in rows], [True, True, False])

    def test_a_promoted_boleta_reconciles(self):
        self.assert_reconciles(self.promoted_receipt())

    def test_unit_values_print_with_the_decimals_of_the_xml(self):
        """3 × 33.81 − 16.69 = 84.74, not 84.75: rounding the unit value to two decimals breaks the line."""
        document = self.three_cables()
        (line,) = self.xml_lines(document)
        self.assertEqual(str(line.unit_price), '33.8133333333')
        self.assertEqual(str(line.unit_price_with_tax), '33.3333333333')
        (row,) = self.assert_reconciles(document)
        self.assertEqual(str(row['unit']), '33.8133333333')

    def test_a_long_unit_value_stays_inside_its_column(self):
        """A ten-decimal unit value is drawn whole (one run), inside its column, never over the next one."""
        from store.fiscal_pdf_services import _A4_PROMO_LINE_WIDTHS_CM

        ctx = build_fiscal_context(self.three_cables())
        big = Decimal('9999999.99')
        ctx['lines'] = [dict(ctx['lines'][0], unit_price=Decimal('84745.7627118644'),
                             unit_price_with_tax=Decimal('99999.9999999999'), discount=big, amount=big,
                             quantity=Decimal('999.50'))]
        edges = [A4_LEFT]
        for width in _A4_PROMO_LINE_WIDTHS_CM:
            edges.append(edges[-1] + width * CM)
        columns = {name: (edges[index], edges[index + 1])
                   for index, name in ((3, 'unit'), (4, 'price'), (5, 'discount'), (6, 'value'))}
        expected = {'price': 'S/ 99999.9999999999', 'unit': 'S/ 84745.7627118644',
                    'discount': 'S/ 9999999.99', 'value': 'S/ 9999999.99'}
        runs = text_runs(render_fiscal_a4(ctx))[0]
        for name, figure in expected.items():
            with self.subTest(name):
                low, high = columns[name]
                inside = [run for run in runs if run[0] == figure and low - 0.05 <= run[1] and run[2] <= high + 0.05]
                self.assertTrue(inside, (figure, columns[name], [run for run in runs if run[0] == figure]))


class PromotedA4LayoutTest(PromotedFiscalFixtures):
    def test_the_unit_columns_keep_the_order_of_a_document_without_discounts(self):
        plain = self.flat_text(generate_fiscal_pdf(self.invoice()))
        promoted = self.flat_text(generate_fiscal_pdf(self.promoted_invoice()))
        order = ('V. unitario', 'P. unitario')
        self.assertLess(plain.index(order[0]), plain.index(order[1]))
        self.assertLess(promoted.index(order[0]), promoted.index(order[1]))

    def test_the_quantity_column_holds_999_75_like_the_plain_table(self):
        from store.fiscal_pdf_services import _A4_LINE_WIDTHS_CM, _A4_PROMO_LINE_WIDTHS_CM

        self.assertEqual(_A4_PROMO_LINE_WIDTHS_CM[0], _A4_LINE_WIDTHS_CM[0])
        self.assertAlmostEqual(sum(_A4_PROMO_LINE_WIDTHS_CM), 17.0)
        ctx = build_fiscal_context(self.promoted_invoice())
        ctx['lines'] = [dict(ctx['lines'][0], quantity=Decimal('999.75'))]
        runs = [run for run in text_runs(render_fiscal_a4(ctx))[0] if run[0] == '999.75']
        self.assertEqual(len(runs), 1)
        _text, left, right = runs[0]
        self.assertTrue(A4_LEFT - 0.05 <= left and right <= A4_LEFT + _A4_PROMO_LINE_WIDTHS_CM[0] * CM + 0.05,
                        (left, right))


class PlainA4UnchangedTest(PromotedFiscalFixtures):
    def test_without_line_discounts_the_table_is_the_one_of_always(self):
        text = self.flat_text(generate_fiscal_pdf(self.invoice()))
        self.assertIn('Cant. Und. Descripción V. unitario P. unitario Importe', text)
        self.assertNotIn('Dscto.', text)
        self.assertNotIn('V. venta', text)

    def test_a_sample_context_without_the_discount_key_prints_the_plain_table(self):
        ctx = build_fiscal_context(self.invoice())
        ctx['lines'] = [{key: value for key, value in line.items() if key != 'discount'} for line in ctx['lines']]
        self.assertNotIn('Dscto.', self.flat_text(render_fiscal_a4(ctx)))


class PromotedRollReconcilesTest(PromotedFiscalFixtures):
    """
    The 80 mm PDF and the ESC/POS ticket print Cant. × P. unit. (with IGV) and the V. venta (without IGV).
    They already add up: Σ V. venta = Op. gravada, Op. gravada + IGV = TOTAL, and each Cant. × P. unit.
    is the line's V. venta plus its IGV (both read from the XML), within the two decimals the roll prints
    the unit price with (half a cent per unit).
    """

    ROW = r'{description}\s+{qty} {unit} x S/ (\S+)\s+S/ (\S+)'

    def assert_reconciles(self, document, text, totals):
        lines = self.xml_lines(document)
        values = []
        for line in lines:
            pattern = self.ROW.format(description=re.escape(line.description), qty=re.escape(_qty(line.quantity)),
                                      unit=re.escape(line.unit_code))
            match = re.search(pattern, text)
            self.assertIsNotNone(match, f'row of {line.description!r} not found in {text!r}')
            price, value = (Decimal(group) for group in match.groups())
            with self.subTest(line.description):
                self.assertEqual(value, line.line_amount)
                self.assertLessEqual(abs(line.quantity * price - (line.line_amount + line.tax_amount)),
                                     line.quantity * Decimal('0.005'))
            values.append(value)
        taxable, tax, total = (Decimal(re.search(pattern, text).group(1)) for pattern in totals)
        self.assertEqual(taxable, sum(values))
        self.assertEqual(taxable + tax, total)

    def test_the_80mm_roll_reconciles(self):
        for document in (self.promoted_invoice(), self.promoted_receipt(), self.three_cables()):
            with self.subTest(document.document_id):
                text = self.flat_text(generate_fiscal_ticket_pdf(document))
                self.assert_reconciles(document, text, (r'Op\. gravada S/ (\S+)', r'IGV \(18%\) S/ (\S+)',
                                                        r'TOTAL S/ (\S+)'))

    def test_the_escpos_ticket_reconciles(self):
        from store.printing import escpos

        for document in (self.promoted_invoice(), self.promoted_receipt(), self.three_cables()):
            with self.subTest(document.document_id):
                printed = escpos_reader.lines(escpos.fiscal_ticket(document))
                text = ' '.join(re.sub(r'<[^>]*>', '', line) for line in printed)
                self.assert_reconciles(document, text, (r'Op\. gravada\s+S/ (\S+)', r'IGV \(18%\)\s+S/ (\S+)',
                                                        r'TOTAL\s+S/ (\S+)'))


class PromotedFiscalDataUnchangedTest(PromotedFiscalFixtures):
    def test_printing_a_promoted_document_changes_nothing_stored(self):
        document = self.promoted_invoice()
        before = (document.signed_xml, document.digest_value, document.status, document.series,
                  document.number, document.total, document.taxable_amount, document.tax_amount)
        ctx = build_fiscal_context(document)
        generate_fiscal_pdf(document)
        generate_fiscal_ticket_pdf(document)
        document.refresh_from_db()
        self.assertEqual(before, (document.signed_xml, document.digest_value, document.status, document.series,
                                  document.number, document.total, document.taxable_amount, document.tax_amount))
        self.assertEqual(build_fiscal_context(document)['qr_payload'], ctx['qr_payload'])


class PromotedFiscalGoldenTest(PromotedFiscalFixtures):
    """The promoted factura and boleta, recorded: A4 (with the discount column), 80 mm and ESC/POS."""

    def test_promoted_invoice_a4(self):
        document = self.promoted_invoice()
        check_golden(self, 'fiscal_factura_promo_a4', generate_fiscal_pdf(document),
                     normalise=self.normaliser(document))

    def test_promoted_receipt_a4(self):
        document = self.promoted_receipt()
        check_golden(self, 'fiscal_boleta_promo_a4', generate_fiscal_pdf(document),
                     normalise=self.normaliser(document))

    def test_promoted_invoice_ticket80(self):
        document = self.promoted_invoice()
        check_golden(self, 'fiscal_factura_promo_ticket80', generate_fiscal_ticket_pdf(document),
                     normalise=self.normaliser(document))

    def test_promoted_receipt_escpos(self):
        from store.printing import escpos

        document = self.promoted_receipt()
        normalise = self.normaliser(document)
        check_escpos_golden(self, 'fiscal_boleta_promo_escpos', escpos.fiscal_ticket(document),
                            normalise=lambda line: re.sub(r'\d{4}-\d{2}-\d{2}', '<FECHA>', normalise(line)))


class PromotedGoldenTokensTest(PromotedFiscalFixtures):
    """
    The promoted A4 goldens against the same documents printed BEFORE this change (frozen in
    `goldens/fiscal_pre_promo_lines.json`): every word printed then is still printed, the same number of
    times, except the header «Importe», now «V. venta»; what is added is the «Dscto. s/IGV» header and, on each
    discounted line, its discount as the XML states it.
    """

    def test_only_the_discount_column_and_its_header_are_added(self):
        before = json.loads(PRE_CHANGE_FILE.read_text(encoding='utf-8'))
        now = json.loads(GOLDEN_FILE.read_text(encoding='utf-8'))
        for name, document in (('fiscal_factura_promo_a4', self.promoted_invoice()),
                               ('fiscal_boleta_promo_a4', self.promoted_receipt())):
            with self.subTest(name):
                pages = now[name]['pages']
                self.assertEqual(pages, before[name]['pages'])
                then, today = tokens(' '.join(before[name]['texts'])), tokens(' '.join(now[name]['texts']))
                discounts = [line.line_discount for line in self.xml_lines(document) if line.line_discount]
                self.assertEqual(then - today, Counter({'Importe': pages}))
                self.assertEqual(today - then, Counter({'Dscto.': pages, 's/IGV': pages, 'V.': pages, 'venta': pages})
                                 + Counter({'S/': len(discounts)}) + Counter(f'{d:.2f}' for d in discounts))


# ---------------------------------------------------------------------------
# The parser alone, on hand-written XML (no database)
# ---------------------------------------------------------------------------

_ROOTS = {
    'Invoice': ('urn:oasis:names:specification:ubl:schema:xsd:Invoice-2', 'InvoiceLine', 'InvoicedQuantity'),
    'CreditNote': ('urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2', 'CreditNoteLine',
                   'CreditedQuantity'),
}


def _allowance(charge='false', code='00', amount='10.00', base='50.00', factor=None):
    parts = [f'<cbc:ChargeIndicator>{charge}</cbc:ChargeIndicator>',
             f'<cbc:AllowanceChargeReasonCode>{code}</cbc:AllowanceChargeReasonCode>']
    if factor is not None:
        parts.append(f'<cbc:MultiplierFactorNumeric>{factor}</cbc:MultiplierFactorNumeric>')
    parts.append(f'<cbc:Amount currencyID="PEN">{amount}</cbc:Amount>')
    if base is not None:
        parts.append(f'<cbc:BaseAmount currencyID="PEN">{base}</cbc:BaseAmount>')
    return '<cac:AllowanceCharge>' + ''.join(parts) + '</cac:AllowanceCharge>'


def _document(*allowances, root='Invoice') -> bytes:
    """A minimal signed-looking document with ONE line of 2 units carrying `allowances`."""
    namespace, line, quantity = _ROOTS[root]
    return (
        f'<{root} xmlns="{namespace}" '
        'xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" '
        'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">'
        '<cbc:ID>F001-1</cbc:ID><cbc:IssueDate>2026-03-09</cbc:IssueDate>'
        '<cbc:DocumentCurrencyCode>PEN</cbc:DocumentCurrencyCode>'
        f'<cac:{line}><cbc:ID>1</cbc:ID><cbc:{quantity} unitCode="NIU">2.00</cbc:{quantity}>'
        '<cbc:LineExtensionAmount currencyID="PEN">40.00</cbc:LineExtensionAmount>'
        + ''.join(allowances) +
        '<cac:Item><cbc:Description>Artículo</cbc:Description></cac:Item>'
        '<cac:Price><cbc:PriceAmount currencyID="PEN">25.00</cbc:PriceAmount></cac:Price>'
        f'</cac:{line}></{root}>'
    ).encode('utf-8')


class LineAllowanceParserTest(SimpleTestCase):
    def line(self, *allowances, root='Invoice'):
        (line,) = parse_signed_invoice_for_representation(_document(*allowances, root=root)).lines
        return line

    def test_two_00_discounts_on_a_line_are_summed(self):
        line = self.line(_allowance(amount='6.00', base='50.00'), _allowance(amount='4.00', base='44.00'))
        self.assertEqual(len(line.allowances), 2)
        self.assertEqual(line.line_discount, Decimal('10.00'))

    def test_charges_and_other_codes_are_read_but_not_counted_as_the_line_discount(self):
        line = self.line(_allowance(charge='true', code='47', amount='3.00'),
                         _allowance(code='01', amount='2.00'), _allowance(code='02', amount='1.00'))
        self.assertEqual([(a.charge, a.reason_code, a.amount) for a in line.allowances],
                         [(True, '47', Decimal('3.00')), (False, '01', Decimal('2.00')),
                          (False, '02', Decimal('1.00'))])
        self.assertIsNone(line.line_discount)

    def test_only_the_00_discounts_count_when_mixed(self):
        line = self.line(_allowance(charge='true', code='00', amount='3.00'), _allowance(amount='10.00'))
        self.assertEqual(line.line_discount, Decimal('10.00'))

    def test_a_credit_note_line_with_a_discount_is_read_too(self):
        line = self.line(_allowance(amount='10.00'), root='CreditNote')
        self.assertEqual(line.quantity, Decimal('2.00'))
        self.assertEqual(line.line_discount, Decimal('10.00'))

    def test_a_missing_base_amount_is_none_and_the_discount_still_counts(self):
        (allowance,) = self.line(_allowance(base=None)).allowances
        self.assertIsNone(allowance.base_amount)
        self.assertEqual(self.line(_allowance(base=None)).line_discount, Decimal('10.00'))

    def test_a_malformed_multiplier_factor_does_not_block_reading(self):
        line = self.line(_allowance(factor='not-a-number'))
        self.assertEqual(line.line_discount, Decimal('10.00'))

    def test_a_line_without_allowances_has_no_discount(self):
        line = self.line()
        self.assertEqual(line.allowances, ())
        self.assertIsNone(line.line_discount)
