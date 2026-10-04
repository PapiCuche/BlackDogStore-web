"""
De la tienda en línea al papel, sin credenciales — PAYMENT-FISCAL-PRINT-01.

La cadena entera con las piezas de verdad y sólo dos sustitutos, los dos fuera
del producto: un Izipay falso en el socket y una impresora simulada.

    carrito → checkout → Izipay falso firma el pago → pedido PAGADO
            → comprobante electrónico firmado → PDF A4 y ticket de 80 mm
            → trabajo de impresión → el agente lo entrega a la impresora

Y lo contrario: un pago rechazado, manipulado o repetido no produce ni un
comprobante de más ni un ticket de más.
"""
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from print_agent.agent import Agent
from store.fiscal.testing import self_signed_pem
from store.fiscal_pdf_services import generate_fiscal_pdf, generate_fiscal_ticket_pdf
from store.fiscal_services import FiscalError, get_or_create_fiscal_document, sign_fiscal_document
from store.models import (
    CartItem, FiscalDocument, FiscalDocumentType, FiscalSeries, Order, PaymentTransaction,
    Printer, PrintJob,
)
from store.payments.fake_izipay import DECLINED, FakeIzipay
from store.printing import services as printing
from store.test_print_agent import FakePrinter
from store.tests import IZIPAY_TEST_SETTINGS, _c1_product, _c1_stock, _p3_company, _storefront_of

ISSUER_RUC = '20100066603'
CHECKOUT_URL = '/api/payments/create-checkout-session/'


@override_settings(**IZIPAY_TEST_SETTINGS)
class EcommerceToPrinterTest(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company(
            'pago-impreso', 'Tienda En Línea', tax_id=ISSUER_RUC, legal_name='TIENDA EN LINEA SAC')
        self.branch = self.company.default_inventory_branch
        FiscalSeries.objects.create(
            company=self.company, document_type=FiscalDocumentType.RECEIPT, series='B001')
        self.product = _c1_product(self.company, 'Cargador USB-C', '59.00')
        _c1_stock(self.branch, self.product, 20)
        Printer.objects.create(
            company=self.company, branch=self.branch, name='Mostrador', host='192.168.1.50')
        self.agent_row, self.token = printing.create_agent(
            company=self.company, branch=self.branch, name='Mostrador', actor=None)

        self.fake = FakeIzipay.from_settings(IZIPAY_TEST_SETTINGS)
        self.client = APIClient()
        self.session_key = 'pago-impreso-0001'
        CartItem.objects.create(session_key=self.session_key, product=self.product, quantity=2)
        pinned = _storefront_of(self.company)
        pinned.enable()
        self.addCleanup(pinned.disable)

        self.paper = FakePrinter()
        self.addCleanup(self.paper.close)

    # -- piezas ---------------------------------------------------------------

    def checkout(self):
        with self.fake.online():
            response = self.client.post(CHECKOUT_URL, {
                'session_key': self.session_key, 'customer_name': 'Rosa Quispe',
                'customer_email': 'rosa@example.com', 'customer_phone': '936449536',
                'document_type': 'dni', 'document_number': '45678912',
                'delivery_method': 'pickup_store', 'receipt_type': 'boleta',
                'accepted_terms': True, 'accepted_warranty_policy': True,
            }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.fake.violations, [])
        return PaymentTransaction.objects.select_related('order').get(
            transaction_id=response.data['transaction_id'])

    def issue(self, order):
        """Lo que hace «Preparar» en el panel, con un certificado de pruebas."""
        document, _created = get_or_create_fiscal_document(order)
        key, cert = self_signed_pem(ISSUER_RUC)
        return sign_fiscal_document(document, key_pem=key, cert_pem=cert)

    def agent(self):
        """El agente de verdad, hablando con este servidor y con la impresora simulada."""
        api = APIClient()

        def http(method, url, token, body=None, timeout=20.0):
            path = url.split('https://tienda.invalid', 1)[1]
            response = api.post(path, body or {}, format='json',
                                HTTP_AUTHORIZATION=f'PrintAgent {token}')
            return response.status_code, response.data

        def to_fake_printer(host, port, payload, timeout):
            # La dirección que dio el servidor es la del local; la impresora
            # simulada escucha aquí.
            self.sent_to = (host, port)
            from print_agent.agent import send_to_printer

            send_to_printer('127.0.0.1', self.paper.port, payload, timeout)

        import os
        import tempfile

        journal = os.path.join(tempfile.mkdtemp(prefix='e2e-agent-'), 'journal.json')
        return Agent({'server': 'https://tienda.invalid', 'token': self.token, 'journal': journal},
                     http=http, printer=to_fake_printer)

    def wait_for_paper(self, count=1):
        import threading

        for _ in range(300):
            if len(self.paper.received) >= count:
                return
            threading.Event().wait(0.01)

    # -- la cadena ------------------------------------------------------------

    def test_a_paid_online_order_ends_as_a_ticket_on_the_shop_printer(self):
        attempt = self.checkout()
        order = attempt.order
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(PrintJob.objects.count(), 0)

        # 1. Izipay confirma, firmado. Sólo esto vuelve el pedido pagado.
        response = self.fake.deliver(self.client, self.fake.notification(attempt))
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual((order.status, order.paid), (Order.Status.PAID, True))
        self.assertEqual(order.total, Decimal('118.00'))
        self.assertEqual(order.fulfillment_branch, self.branch)

        # 2. El comprobante electrónico, firmado.
        document = self.issue(order)
        self.assertEqual(
            (document.document_type, document.document_id, document.customer_doc_type),
            ('03', 'B001-1', '1'))
        self.assertEqual(document.total, Decimal('118.00'))

        # 3. Su representación impresa, en los dos formatos.
        self.assertTrue(generate_fiscal_pdf(document).startswith(b'%PDF'))
        self.assertTrue(generate_fiscal_ticket_pdf(document).startswith(b'%PDF'))

        # 4. Un trabajo de impresión, y sólo uno, para la impresora de su local.
        job = PrintJob.objects.get()
        self.assertEqual(
            (job.fiscal_document, job.branch, job.status, job.reason),
            (document, self.branch, PrintJob.Status.PENDING, PrintJob.Reason.AUTO))

        # 5. El agente del local lo recoge y lo entrega a la impresora.
        self.assertEqual(self.agent().run_once(), 'printed')
        self.wait_for_paper()
        job.refresh_from_db()
        self.assertEqual(job.status, PrintJob.Status.PRINTED)
        self.assertEqual(self.sent_to, ('192.168.1.50', 9100))
        printed = self.paper.received[0]
        self.assertTrue(printed.startswith(b'\x1b@'))
        for expected in (b'TIENDA EN LINEA SAC', b'B001-1', b'DNI:', b'45678912',
                         b'Cargador USB-C', b'S/ 118.00'):
            self.assertIn(expected, printed)
        self.assertNotIn(b'RUC:', printed)

    def test_a_repeated_notification_prints_nothing_more(self):
        attempt = self.checkout()
        envelope = self.fake.notification(attempt)
        self.fake.deliver(self.client, envelope)
        order = Order.objects.get(pk=attempt.order_id)
        document = self.issue(order)
        agent = self.agent()
        self.assertEqual(agent.run_once(), 'printed')
        self.wait_for_paper()

        # La misma notificación otra vez, y el comprobante «emitido» otra vez.
        self.assertEqual(self.fake.deliver(self.client, envelope).status_code, 200)
        again = self.issue(Order.objects.get(pk=order.pk))

        self.assertEqual(again.pk, document.pk)
        self.assertEqual(FiscalDocument.objects.filter(order=order).count(), 1)
        self.assertEqual(PrintJob.objects.count(), 1)
        self.assertEqual(agent.run_once(), 'idle')
        self.assertEqual(len(self.paper.received), 1)

    def test_a_payment_that_did_not_happen_leaves_no_document_and_no_ticket(self):
        attempt = self.checkout()
        genuine = self.fake.notification(attempt)

        def cheaper(payload):
            payload['response']['order'][0]['amount'] = '1.00'

        for label, envelope in (
            ('rechazado', self.fake.notification(attempt, code=DECLINED)),
            ('manipulado', self.fake.tampered(genuine, cheaper)),
            ('firmado con otra clave', self.fake.signed_by_someone_else(genuine)),
        ):
            with self.subTest(label):
                self.fake.deliver(self.client, envelope)
                order = Order.objects.get(pk=attempt.order_id)
                self.assertFalse(order.paid)
                with self.assertRaises(FiscalError):
                    get_or_create_fiscal_document(order)
                self.assertEqual(FiscalDocument.objects.count(), 0)
                self.assertEqual(PrintJob.objects.count(), 0)
                self.assertEqual(self.agent().run_once(), 'idle')
        self.assertEqual(self.paper.received, [])
