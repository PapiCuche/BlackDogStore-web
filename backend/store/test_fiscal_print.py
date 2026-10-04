"""
La representación impresa frente a los Anexos I y II de la RS 114-2019/SUNAT
— PAYMENT-FISCAL-PRINT-01.

Los anexos marcan qué datos son «información mínima» del papel. Lo que aquí se
fija es lo que faltaba o estaba mal:

  · la etiqueta del documento del adquirente estaba escrita a mano («RUC»),
    también en una boleta a un DNI o a un carné de extranjería;
  · la leyenda no nombraba el comprobante («…de la factura electrónica»,
    «…de la boleta de venta electrónica»);
  · faltaban la unidad de medida por ítem y, en el ticket, el precio de venta
    unitario;
  · el importe en letras y la forma de pago estaban en el XML firmado y no
    llegaban al papel.

TODO SALE DEL XML FIRMADO O DE LA FILA CONGELADA, nunca de tablas vivas.
"""
import re
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from store.fiscal.testing import self_signed_pem
from store.fiscal_pdf_services import generate_fiscal_pdf, generate_fiscal_ticket_pdf
from store.fiscal_services import get_or_create_fiscal_document, sign_fiscal_document
from store.models import FiscalDocumentType, FiscalSeries, Order, OrderItem
from store.tests import _c1_product, _c1_stock, _p3_company, _pdf_text

ISSUER_RUC = '20100066603'


class FiscalPrintBase(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company(
            'fiscal-print', 'Tienda Impresa', tax_id=ISSUER_RUC, legal_name='TIENDA IMPRESA SAC')
        FiscalSeries.objects.create(
            company=self.company, document_type=FiscalDocumentType.INVOICE, series='F001')
        FiscalSeries.objects.create(
            company=self.company, document_type=FiscalDocumentType.RECEIPT, series='B001')
        self.product = _c1_product(self.company, 'Funda de silicona', '118.00')
        _c1_stock(self.company.default_inventory_branch, self.product, 50)

    def issue(self, *, receipt='boleta', document_type='dni', document_number='45678912',
              name='Rosa Quispe Mamani', quantity=1):
        total = Decimal('118.00') * quantity
        order = Order.objects.create(
            company=self.company, customer_name=name,
            document_type=document_type, document_number=document_number,
            receipt_type=receipt, total=total, discount_amount=Decimal('0.00'),
            subtotal_amount=total, taxable_amount=Decimal('100.00') * quantity,
            tax_amount=Decimal('18.00') * quantity, tax_rate=Decimal('0.18'),
            tax_treatment='taxed', currency='PEN',
            status=Order.Status.PAID, paid=True, paid_at=timezone.now(),
            fulfillment_branch=self.company.default_inventory_branch)
        OrderItem.objects.create(
            order=order, product=self.product, quantity=quantity, price=Decimal('118.00'))
        document, _ = get_or_create_fiscal_document(order)
        key, cert = self_signed_pem(ISSUER_RUC)
        return sign_fiscal_document(document, key_pem=key, cert_pem=cert)

    @staticmethod
    def readable(pdf_bytes) -> str:
        """`_pdf_text` deja las letras acentuadas como escapes octales de PDF."""
        return re.sub(
            r'\\(\d{3})', lambda m: bytes([int(m.group(1), 8)]).decode('cp1252'),
            _pdf_text(pdf_bytes))

    def a4(self, document):
        return self.readable(generate_fiscal_pdf(document))

    def ticket(self, document):
        return self.readable(generate_fiscal_ticket_pdf(document))


class CustomerDocumentLabelTest(FiscalPrintBase):
    """Campo 11 de ambos anexos: TIPO y número de documento del adquirente."""

    def test_an_invoice_to_a_company_says_ruc_and_razon_social(self):
        document = self.issue(
            receipt='factura', document_type='ruc', document_number='20000000001',
            name='CLIENTE DE PRUEBA SAC')

        for text in (self.a4(document), self.ticket(document)):
            self.assertIn('RUC:', text)
            self.assertIn('20000000001', text)
        self.assertIn('Razón social:', self.a4(document))

    def test_a_receipt_to_a_dni_says_dni_and_never_ruc(self):
        document = self.issue(document_type='dni', document_number='45678912')

        for text in (self.a4(document), self.ticket(document)):
            self.assertIn('DNI:', text)
            self.assertIn('45678912', text)
            # El RUC del emisor se imprime sin dos puntos; con ellos sería la
            # etiqueta del adquirente.
            self.assertNotIn('RUC:', text)
            self.assertNotIn('RUC cliente', text)
        self.assertNotIn('Razón social:', self.a4(document))

    def test_a_receipt_to_a_foreigner_names_the_carne_de_extranjeria(self):
        document = self.issue(document_type='ce', document_number='001234567')

        self.assertIn('Carné de extranjería:', self.a4(document))
        self.assertIn('C.E.:', self.ticket(document))
        for text in (self.a4(document), self.ticket(document)):
            self.assertIn('001234567', text)
            self.assertNotIn('RUC:', text)

    def test_a_receipt_without_a_document_prints_no_document_label(self):
        document = self.issue(document_type='', document_number='', name='')

        self.assertEqual(document.customer_doc_type, '0')
        for text in (self.a4(document), self.ticket(document)):
            for label in ('RUC:', 'DNI:', 'C.E.:', 'Carné de extranjería:', 'RUC cliente'):
                self.assertNotIn(label, text)
            self.assertIn('Cliente:', text)


class PrintedMinimumTest(FiscalPrintBase):
    """Lo que los anexos marcan como información mínima del papel."""

    def test_the_legend_names_the_document(self):
        invoice = self.issue(
            receipt='factura', document_type='ruc', document_number='20000000001',
            name='CLIENTE DE PRUEBA SAC')
        receipt = self.issue()

        for text in (self.a4(invoice), self.ticket(invoice)):
            self.assertIn('Representación impresa de la factura electrónica', text)
        for text in (self.a4(receipt), self.ticket(receipt)):
            self.assertIn('Representación impresa de la boleta de venta electrónica', text)

    def test_each_item_prints_its_unit_of_measure(self):
        document = self.issue(quantity=2)

        self.assertIn('NIU', self.a4(document))
        self.assertIn('NIU', self.ticket(document))

    def test_the_ticket_prints_the_unit_price_the_buyer_pays(self):
        text = self.ticket(self.issue(quantity=2))

        # Precio de venta unitario (con IGV) y valor de venta de la línea.
        self.assertIn('118.00', text)
        self.assertIn('200.00', text)
        self.assertIn('236.00', text)

    def test_the_amount_in_words_reaches_the_paper(self):
        document = self.issue()

        for text in (self.a4(document), self.ticket(document)):
            self.assertIn('SON: CIENTO DIECIOCHO CON 00/100 SOLES', text)

    def test_the_payment_form_reaches_the_paper(self):
        document = self.issue()

        for text in (self.a4(document), self.ticket(document)):
            self.assertIn('Forma de pago: Contado', text)

    def test_the_ticket_carries_the_digest_value_too(self):
        document = self.issue()

        self.assertTrue(document.digest_value)
        self.assertIn(document.digest_value, self.ticket(document))
