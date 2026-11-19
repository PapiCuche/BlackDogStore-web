"""
CN-PROMO-LINES — a credit note that cancels a factura whose lines carry a promotion.

A promotion is declared on the factura line it lowered (ERP-FISCAL-6): `cac:InvoiceLine/cac:AllowanceCharge`
with code `00` of the Catálogo N.º 53, `cac:Price/cbc:PriceAmount` = unit value BEFORE the discount and
`cbc:LineExtensionAmount` = value AFTER it. SUNAT's rule 3271 for a FACTURA subtracts that discount
(«Reglas de validación de CPE», AjustesValidacionesCPEv20250421.xlsx, sheet `Factura2_0`, row 278).

A credit note is different (same file, sheet `NotaCredito2_0`):

  * its item block (fields 22–36, rows 135–256) has no «cargos y descuentos por ítem»: the note
    declares no line AllowanceCharge (UBL 2.1 would accept one in `CreditNoteLineType`, after `TaxTotal`,
    but SUNAT defines no field, code or rule for it);
  * its rule 3271 (row 254, ERROR when the modified document is a factura `01`; observation 4288 on
    row 255 otherwise) is «LineExtensionAmount = Valor unitario por ítem × Cantidad, ± 1», with no
    discount term.

So the total-cancellation note mirrors each line of the original with the values AFTER the discount:
the same quantity, valor de venta, IGV, rate and precio unitario (with IGV), and as valor unitario the
line's valor de venta per unit (`LineExtensionAmount / quantity`, n(12,10), the same arithmetic the
factura uses for its own unit values). The amounts are the original's, line by line and in total.

Before the fix the note copied the original's unit value BEFORE the discount, so `rules.validate_note`
refused it at signing (after its number had been reserved): such a factura could not be cancelled.
"""
import hashlib
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from lxml import etree

from store.fiscal import builder as fiscal_builder
from store.fiscal import rules as fiscal_rules
from store.fiscal import schema as fiscal_schema
from store.fiscal.testing import self_signed_pem
from store.fiscal_note_services import _note_lines, _note_to_data, create_fiscal_note, sign_fiscal_note
from store.fiscal_pdf_services import generate_fiscal_pdf, generate_fiscal_ticket_pdf
from store.fiscal_services import (
    FiscalError, get_or_create_fiscal_document, sign_fiscal_document, submit_fiscal_document,
)
from store.models import (
    Customer, FiscalDocument, FiscalDocumentStatus, FiscalDocumentType, FiscalSeries, Order, OrderItem,
    PaymentMethod,
)
from store.test_document_design import page_texts
from store.tests import (
    _BETA_ISSUING, _C1_POS, _C12_DISCOUNT, _accepted, _c1_product, _c1_sale, _c1_stock, _c13_combo,
    _FakeProvider, _p2d_member, _p3_company,
)

RUC = '20100066603'
NS = fiscal_builder.NS
CENT = Decimal('0.01')
#: The instant every pinned note is issued at (09/03/2026 21:30 in Lima).
PINNED = datetime(2026, 3, 10, 2, 30, tzinfo=dt_timezone.utc)


def _text(node, path):
    return node.findtext(path, namespaces=NS)


def _lines(xml: str, tag: str):
    return etree.fromstring(xml.encode('utf-8')).findall(tag, NS)


@override_settings(**_BETA_ISSUING)
class PromotedCreditNoteBase(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('cn-promo', 'Empresa Nota Promo', tax_id=RUC,
                                   legal_name='EMPRESA NOTA PROMO SAC')
        for kind, series in ((FiscalDocumentType.INVOICE, 'F001'), (FiscalDocumentType.RECEIPT, 'B001'),
                             (FiscalDocumentType.CREDIT_NOTE, 'FN01'), (FiscalDocumentType.DEBIT_NOTE, 'FD01')):
            FiscalSeries.objects.create(company=self.company, document_type=kind, series=series)
        branch = self.company.default_inventory_branch
        self.case = _c1_product(self.company, 'Funda de silicona', '118.00')
        self.glass = _c1_product(self.company, 'Lámina de vidrio', '59.00')
        self.cable = _c1_product(self.company, 'Cable USB-C', '39.90')
        for product in (self.case, self.glass, self.cable):
            _c1_stock(branch, product, 50)
        _c13_combo(self.company, [(self.case, 1), (self.glass, 1)], fixed_price='139.90', name='Funda y lámina')
        _c13_combo(self.company, [(self.cable, 3)], fixed_price='100.00', name='Tres cables')
        self.seller, _ = _p2d_member(
            self.company, 'cn_promo_seller', ['company.view', _C1_POS, _C12_DISCOUNT, 'sales.fiscal.issue'])
        self.ruc_customer = Customer.objects.create(
            company=self.company, customer_type=Customer.TYPE_BUSINESS, business_name='CLIENTE DE PRUEBA SAC',
            document_type=Order.DocumentType.RUC, document_number='20000000001')
        self.key, self.cert = self_signed_pem(RUC)

    # -- originals ---------------------------------------------------------------

    def sale(self, items, receipt_type):
        kw = {'customer': self.ruc_customer.pk} if receipt_type == Order.ReceiptType.FACTURA else {}
        order, _ = _c1_sale(
            actor=self.seller, company=self.company, branch=self.company.default_inventory_branch,
            items=[{'product': product.pk, 'quantity': quantity} for product, quantity in items],
            payment_method=PaymentMethod.CARD, receipt_type=receipt_type, **kw)
        return order

    def issue(self, order, *, accept=True):
        document, _ = get_or_create_fiscal_document(order)
        document = sign_fiscal_document(document, key_pem=self.key, cert_pem=self.cert)
        if accept:
            document = submit_fiscal_document(document, _FakeProvider(_accepted()))
        return document

    def promoted_factura(self):
        """Funda + lámina in the combo (two lines with a 00 discount) and a cable at its list price."""
        return self.issue(self.sale([(self.case, 1), (self.glass, 1), (self.cable, 1)], Order.ReceiptType.FACTURA))

    def three_cables(self):
        """Three cables for 100.00: unit value before the discount 33.8133333333, after it 28.25."""
        return self.issue(self.sale([(self.cable, 3)], Order.ReceiptType.FACTURA))

    def plain_factura(self, quantity=3):
        """A factura without any discount, built like Fiscal5aNoteBase's: 3 × 10.00 (unit value 8.4733333333)."""
        order = Order.objects.create(
            company=self.company, customer_name='CLIENTE DE PRUEBA SAC',
            document_type=Order.DocumentType.RUC, document_number='20000000001',
            receipt_type=Order.ReceiptType.FACTURA, total=Decimal('30.00'), discount_amount=Decimal('0.00'),
            subtotal_amount=Decimal('30.00'), taxable_amount=Decimal('25.42'), tax_amount=Decimal('4.58'),
            tax_rate=Decimal('0.18'), tax_treatment='taxed', currency='PEN', status=Order.Status.PAID,
            paid=True, paid_at=timezone.now(), fulfillment_branch=self.company.default_inventory_branch)
        OrderItem.objects.create(order=order, product=self.cable, quantity=quantity, price=Decimal('10.00'))
        return self.issue(order)

    # -- notes ---------------------------------------------------------------------

    def credit_note(self, original, *, reason='01', key='cn'):
        note, _ = create_fiscal_note(original, note_type=FiscalDocumentType.CREDIT_NOTE, reason_code=reason,
                                     reason_description='ANULACION DE LA OPERACION', request_key=key)
        return note

    def signed_credit_note(self, original, **kw):
        return sign_fiscal_note(self.credit_note(original, **kw), key_pem=self.key, cert_pem=self.cert)


class PromotedCreditNoteTest(PromotedCreditNoteBase):
    def assert_mirrors(self, original, note):
        """Each note line carries the original's amounts after the discount, and no discount of its own."""
        self.assertEqual(note.status, FiscalDocumentStatus.SIGNED)
        fiscal_schema.validate_credit_note(note.signed_xml.encode('utf-8'))
        originals = _lines(original.signed_xml, 'cac:InvoiceLine')
        notes = _lines(note.signed_xml, 'cac:CreditNoteLine')
        self.assertEqual(len(notes), len(originals))
        for source, line in zip(originals, notes):
            with self.subTest(_text(source, 'cac:Item/cbc:Description')):
                for path in ('cbc:LineExtensionAmount', 'cac:TaxTotal/cbc:TaxAmount',
                             'cac:TaxTotal/cac:TaxSubtotal/cbc:TaxableAmount',
                             'cac:TaxTotal/cac:TaxSubtotal/cbc:TaxAmount',
                             'cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cbc:Percent',
                             'cac:PricingReference/cac:AlternativeConditionPrice/cbc:PriceAmount',
                             'cac:PricingReference/cac:AlternativeConditionPrice/cbc:PriceTypeCode',
                             'cac:Item/cbc:Description'):
                    self.assertEqual(_text(line, path), _text(source, path), path)
                self.assertEqual(_text(line, 'cbc:CreditedQuantity'), _text(source, 'cbc:InvoicedQuantity'))
                # NotaCredito2_0 defines no line discount: none is declared on the note.
                self.assertEqual(line.findall('cac:AllowanceCharge', NS), [])
                # NotaCredito2_0 row 254 (3271): LineExtensionAmount = valor unitario × cantidad.
                quantity = Decimal(_text(line, 'cbc:CreditedQuantity'))
                unit = Decimal(_text(line, 'cac:Price/cbc:PriceAmount'))
                self.assertEqual((quantity * unit).quantize(CENT), Decimal(_text(line, 'cbc:LineExtensionAmount')))
        root = etree.fromstring(note.signed_xml.encode('utf-8'))
        self.assertEqual(root.findall('cac:AllowanceCharge', NS), [])
        self.assertEqual(Decimal(_text(root, 'cac:LegalMonetaryTotal/cbc:PayableAmount')), original.total)
        self.assertEqual(Decimal(_text(root, 'cac:LegalMonetaryTotal/cbc:LineExtensionAmount')),
                         original.taxable_amount)
        self.assertEqual(Decimal(_text(root, 'cac:TaxTotal/cbc:TaxAmount')), original.tax_amount)
        self.assertEqual(Decimal(_text(root, 'cac:TaxTotal/cac:TaxSubtotal/cbc:TaxableAmount')),
                         original.taxable_amount)
        self.assertEqual((note.taxable_amount, note.tax_amount, note.total),
                         (original.taxable_amount, original.tax_amount, original.total))
        return originals, notes

    def test_a_promoted_factura_can_be_cancelled_in_full(self):
        original = self.promoted_factura()
        self.assertEqual(len(_lines(original.signed_xml, 'cac:InvoiceLine/cac:AllowanceCharge')), 2)
        originals, notes = self.assert_mirrors(original, self.signed_credit_note(original))
        # Discounted lines: valor de venta per unit, not the value before the discount.
        self.assertEqual([_text(n, 'cac:Price/cbc:PriceAmount') for n in notes], ['79.04', '39.52', '33.81'])
        self.assertEqual([_text(o, 'cac:Price/cbc:PriceAmount') for o in originals], ['100.00', '50.00', '33.81'])

    def test_a_total_return_is_mirrored_the_same_way(self):
        original = self.promoted_factura()
        self.assert_mirrors(original, self.signed_credit_note(original, reason='06'))

    def test_a_unit_value_with_decimals_reconciles_exactly(self):
        original = self.three_cables()
        (source,) = _lines(original.signed_xml, 'cac:InvoiceLine')
        self.assertEqual(_text(source, 'cac:Price/cbc:PriceAmount'), '33.8133333333')
        _originals, (line,) = self.assert_mirrors(original, self.signed_credit_note(original))
        self.assertEqual(_text(line, 'cac:Price/cbc:PriceAmount'), '28.25')

    def test_the_note_passes_the_local_rules_before_any_number_is_spent(self):
        original = self.promoted_factura()
        note = self.credit_note(original)
        fiscal_rules.validate_note(_note_to_data(note))      # raised «Línea 1: 1.00 × 100.00 = …» before the fix

    def test_the_note_renders_from_its_signed_xml(self):
        original = self.promoted_factura()
        note = self.signed_credit_note(original)
        a4, roll = (' '.join(page_texts(pdf)) for pdf in (generate_fiscal_pdf(note), generate_fiscal_ticket_pdf(note)))
        for text in (a4, roll):
            self.assertIn('NOTA DE CRÉDITO ELECTRÓNICA', text)
            self.assertIn(original.document_id, text)
            for amount in ('79.04', '39.52', '33.81', f'{original.total:.2f}'):
                self.assertIn(amount, text)

    def test_the_original_is_untouched(self):
        original = self.promoted_factura()
        before = (original.signed_xml_sha256, original.status, original.total)
        self.signed_credit_note(original)
        original.refresh_from_db()
        self.assertEqual((original.signed_xml_sha256, original.status, original.total), before)

    def test_a_debit_note_on_a_promoted_factura_is_a_plain_charge(self):
        original = self.promoted_factura()
        note, _ = create_fiscal_note(original, note_type=FiscalDocumentType.DEBIT_NOTE, reason_code='02',
                                     reason_description='AUMENTO EN EL VALOR', request_key='nd',
                                     taxable_amount=Decimal('10.00'), tax_amount=Decimal('1.80'))
        note = sign_fiscal_note(note, key_pem=self.key, cert_pem=self.cert)
        fiscal_schema.validate_debit_note(note.signed_xml.encode('utf-8'))
        (line,) = _lines(note.signed_xml, 'cac:DebitNoteLine')
        self.assertEqual(line.findall('cac:AllowanceCharge', NS), [])
        self.assertEqual(_text(line, 'cbc:LineExtensionAmount'), '10.00')

    def test_a_promoted_boleta_still_gets_no_note_in_this_phase(self):
        """NC-BOL is pending (Resumen Diario): refused before any number is reserved, discount or not."""
        original = self.issue(self.sale([(self.case, 1), (self.glass, 1)], Order.ReceiptType.BOLETA), accept=False)
        self.assertTrue(_lines(original.signed_xml, 'cac:InvoiceLine/cac:AllowanceCharge'))
        with self.assertRaisesMessage(FiscalError, 'Resumen Diario'):
            self.credit_note(original)
        self.assertFalse(FiscalDocument.objects.filter(original_document=original).exists())


class RefusedNoteSpendsNothingTest(PromotedCreditNoteBase):
    """
    A note the local rules refuse is refused when it is CREATED, inside the transaction that reserves its
    number: no note row, no correlativo consumed. Before, the row and the number were committed first and
    the rules only ran at signing (a burned, gapped correlativo and a note that could never be signed).

    The case used is real and still refused: a factura with a GLOBAL coupon discount (code 02). Its lines
    are declared before the discount and add up to more than its base; a credit note has no global
    discount to declare it with (NotaCredito2_0), so the note does not add up («Las líneas suman …»).
    """

    def coupon_factura(self):
        from store.models import Coupon

        Coupon.objects.create(company=self.company, code='DIEZ', discount_percent=10)
        order, _ = _c1_sale(
            actor=self.seller, company=self.company, branch=self.company.default_inventory_branch,
            items=[{'product': self.cable.pk, 'quantity': 1}], payment_method=PaymentMethod.CARD,
            receipt_type=Order.ReceiptType.FACTURA, customer=self.ruc_customer.pk, coupon_code='DIEZ')
        self.assertEqual(order.discount_source, 'coupon')
        return self.issue(order)

    def test_a_note_the_rules_refuse_creates_no_row_and_spends_no_number(self):
        original = self.coupon_factura()
        series = FiscalSeries.objects.get(company=self.company, series='FN01')
        with self.assertRaisesMessage(FiscalError, 'Las líneas suman'):
            self.credit_note(original)
        series.refresh_from_db()
        self.assertEqual(series.next_number, 1)
        self.assertFalse(FiscalDocument.objects.filter(original_document=original).exists())

    def test_after_a_refusal_the_next_note_takes_the_first_number(self):
        refused = self.coupon_factura()
        with self.assertRaises(FiscalError):
            self.credit_note(refused, key='refused')
        note = self.signed_credit_note(self.promoted_factura(), key='good')
        self.assertEqual((note.series, note.number), ('FN01', 1))


class PlainCreditNoteUnchangedTest(PromotedCreditNoteBase):
    #: SHA-256 of the UNSIGNED credit note XML of `plain_factura()` issued at PINNED, recorded at master
    #: 082bf9f before this change. A note on an original without line discounts must not move a byte.
    PLAIN_NOTE_SHA256 = '193d26e28bc69830f7f53fd088a7af01acd05fb62c63d66fd12c057af7ccf5e9'

    def test_the_lines_of_a_plain_original_are_copied_verbatim(self):
        original = self.plain_factura()
        (line,) = _note_lines(self.credit_note(original))
        self.assertEqual(str(line.unit_price), '8.4733333333')
        self.assertEqual(line.allowances, ())

    def test_the_note_of_a_plain_original_is_byte_identical(self):
        original = self.plain_factura()
        note = self.credit_note(original)
        FiscalDocument.objects.filter(pk=note.pk).update(issued_at=PINNED)
        note.refresh_from_db()
        xml = fiscal_builder.build_note_xml(_note_to_data(note))
        self.assertEqual(hashlib.sha256(xml).hexdigest(), self.PLAIN_NOTE_SHA256, xml.decode('utf-8'))
