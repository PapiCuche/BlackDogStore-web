"""
FISCAL-DOUBLE-SUBMIT — the window after SUNAT answered. The attempt used to be
saved as finished (its own commit) and only then, in a second transaction, the
document marked accepted. A second «Enviar» that arrived in between saw no
send in progress and a document not yet accepted, and transmitted the same
invoice again. `C22A1ConcurrencyTest` caught it only when the scheduler hit
that window (CI, #142 shard 6/6). Here the window is opened on purpose.
"""
import threading
from decimal import Decimal

from django.core.cache import cache
from django.db import connection
from django.db.models.signals import post_save
from django.test import TransactionTestCase
from django.utils import timezone

from store.fiscal.testing import self_signed_pem
from store.fiscal_services import (
    FiscalSubmissionInProgress, get_or_create_fiscal_document, sign_fiscal_document, submit_fiscal_document,
)
from store.models import FiscalDocument, FiscalDocumentType, FiscalSeries, FiscalSubmissionAttempt, Order, OrderItem
from store.tests import _accepted, _c1_product, _c1_stock, _p3_company


class SecondSubmitAfterTheAnswerTest(TransactionTestCase):
    def setUp(self):
        if connection.vendor == 'sqlite':
            self.skipTest('Row locks are what this is about: PostgreSQL only.')
        cache.clear()
        self.company = _p3_company('fds-conc', 'Empresa Doble Envío', tax_id='20100066604',
                                   legal_name='EMPRESA DOBLE ENVIO SAC')
        FiscalSeries.objects.create(company=self.company, document_type=FiscalDocumentType.INVOICE,
                                    series='F001', next_number=1)
        product = _c1_product(self.company, 'Articulo FDS', '118.00')
        _c1_stock(self.company.default_inventory_branch, product, 5)
        order = Order.objects.create(
            company=self.company, customer_name='CLIENTE SAC', document_type=Order.DocumentType.RUC,
            document_number='20000000001', receipt_type=Order.ReceiptType.FACTURA, total=Decimal('118.00'),
            discount_amount=Decimal('0.00'), subtotal_amount=Decimal('118.00'), taxable_amount=Decimal('100.00'),
            tax_amount=Decimal('18.00'), tax_rate=Decimal('0.18'), tax_treatment='taxed', currency='PEN',
            status=Order.Status.PAID, paid=True, paid_at=timezone.now(),
            fulfillment_branch=self.company.default_inventory_branch)
        OrderItem.objects.create(order=order, product=product, quantity=1, price=Decimal('118.00'))
        doc, _ = get_or_create_fiscal_document(order)
        key, cert = self_signed_pem('20100066604')
        self.doc = sign_fiscal_document(doc, key_pem=key, cert_pem=cert)

    def test_a_second_send_right_after_the_answer_does_not_transmit_again(self):
        calls, outcomes, second = [], [], []

        class Counting:
            def submit_invoice(self, *, filename, zip_bytes):
                calls.append(filename)
                return _accepted()

        def send_again():
            try:
                outcomes.append(submit_fiscal_document(FiscalDocument.objects.get(pk=self.doc.pk), Counting()))
            except FiscalSubmissionInProgress as exc:
                outcomes.append(exc)
            finally:
                connection.close()

        def when_the_attempt_is_finished(sender, instance, update_fields=None, **kwargs):
            # The moment the first send's attempt is recorded as finished: a second «Enviar» arrives.
            if update_fields and 'finished_at' in update_fields and not second:
                thread = threading.Thread(target=send_again)
                second.append(thread)
                thread.start()
                thread.join(timeout=2)

        post_save.connect(when_the_attempt_is_finished, sender=FiscalSubmissionAttempt)
        try:
            submit_fiscal_document(self.doc, Counting())
        finally:
            post_save.disconnect(when_the_attempt_is_finished, sender=FiscalSubmissionAttempt)
        for thread in second:
            thread.join(timeout=20)

        self.assertEqual(len(calls), 1, f'se transmitió {len(calls)} veces el mismo comprobante')
        self.assertTrue(FiscalDocument.objects.get(pk=self.doc.pk).is_accepted)
        self.assertEqual(FiscalSubmissionAttempt.objects.filter(document=self.doc).count(), 1)
