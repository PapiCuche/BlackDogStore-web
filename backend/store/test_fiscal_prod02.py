"""
FISCAL-PROD-02 — fail closed before a correlative is spent, and never resend blind.

Findings of `docs/adr-fiscal-prod.md` §3, each pinned here before its fix:

- H-01: with issuing switched off (or without credentials) the four «emit» endpoints
  used to reserve the correlative first and discover the switch afterwards.
- H-11: an order paid with TEST keys could receive a PRODUCTION fiscal document.
- H-03: a provider exception outside the transport left the attempt open, and after
  ten minutes the same invoice went out again without asking SUNAT.
- H-04: a read timeout, a 5xx or an unreadable answer were retried as if nothing had
  been transmitted.

No test here talks to SUNAT: every provider is a fake, and `requests.post` is
patched where the transport itself is under test.
"""
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from store.fiscal.testing import self_signed_pem
from store.fiscal_services import FiscalError, get_or_create_fiscal_document, sign_fiscal_document, submit_fiscal_document
from store.fiscal_void_services import attest_not_granted
from store.models import (
    FiscalDailySummary, FiscalDocument, FiscalDocumentType, FiscalEnvironment, FiscalSeries, FiscalVoidCommunication,
    Order, OrderItem, PaymentTransaction,
)
from store.tests import _accepted, _c1_product, _c1_stock, _FakeProvider, _p2d_member, _p3_company

RUC = '20100066603'
KEY, CERT = self_signed_pem(RUC)
CREDENTIALS = dict(
    FISCAL_SOL_RUC=RUC, FISCAL_SOL_USER='MODDATOS', FISCAL_SOL_PASSWORD='moddatos',
    FISCAL_CERT_PEM=CERT.decode(), FISCAL_KEY_PEM=KEY.decode(),
)
ON = dict(FISCAL_ENABLED=True, **CREDENTIALS)
OFF = dict(FISCAL_ENABLED=False, **CREDENTIALS)
ON_WITHOUT_CREDENTIALS = dict(FISCAL_ENABLED=True, FISCAL_SOL_RUC='', FISCAL_SOL_USER='', FISCAL_SOL_PASSWORD='',
                              FISCAL_CERT_PEM='', FISCAL_KEY_PEM='')


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('fp02', 'Empresa FP02', tax_id=RUC, legal_name='EMPRESA FP02 SAC')
        self.branch = self.company.default_inventory_branch
        self.series = {
            kind: FiscalSeries.objects.create(company=self.company, document_type=kind, series=name)
            for kind, name in (
                (FiscalDocumentType.INVOICE, 'F001'), (FiscalDocumentType.RECEIPT, 'B001'),
                (FiscalDocumentType.CREDIT_NOTE, 'FN01'), (FiscalDocumentType.DEBIT_NOTE, 'FD01'),
            )
        }
        self.product = _c1_product(self.company, 'Articulo FP02', '118.00')
        _c1_stock(self.branch, self.product, 50)
        self.issuer, _ = _p2d_member(self.company, 'fp02_emisor',
                                     ['company.view', 'sales.fiscal.view', 'sales.fiscal.issue'])

    def order(self, receipt=Order.ReceiptType.FACTURA):
        factura = receipt == Order.ReceiptType.FACTURA
        order = Order.objects.create(
            company=self.company, customer_name='CLIENTE SAC' if factura else 'CLIENTE',
            document_type=Order.DocumentType.RUC if factura else Order.DocumentType.DNI,
            document_number='20000000001' if factura else '45678912', receipt_type=receipt,
            total=Decimal('118.00'), discount_amount=Decimal('0.00'), subtotal_amount=Decimal('118.00'),
            taxable_amount=Decimal('100.00'), tax_amount=Decimal('18.00'), tax_rate=Decimal('0.18'),
            tax_treatment='taxed', currency='PEN', status=Order.Status.PAID, paid=True, paid_at=timezone.now(),
            fulfillment_branch=self.branch)
        OrderItem.objects.create(order=order, product=self.product, quantity=1, price=Decimal('118.00'))
        return order

    def signed(self, order):
        document, _ = get_or_create_fiscal_document(order)
        return sign_fiscal_document(document, key_pem=KEY, cert_pem=CERT)

    def accepted(self):
        document = submit_fiscal_document(self.signed(self.order()), _FakeProvider(_accepted()))
        self.assertTrue(document.is_accepted)
        return document

    def client_for(self, user=None):
        client = APIClient()
        client.force_authenticate(user=user or self.issuer)
        return client

    def numbers(self):
        return {kind: FiscalSeries.objects.get(pk=s.pk).next_number for kind, s in self.series.items()}


class IssuingSwitchedOffSpendsNothingTest(_Base):
    """H-01. The switch and the credentials are checked BEFORE the correlative is reserved."""

    def assert_refused_and_untouched(self, response, before, documents):
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE, response.data)
        self.assertEqual(self.numbers(), before, 'se gastó un correlativo con la emisión apagada')
        self.assertEqual(FiscalDocument.objects.filter(company=self.company).count(), documents)

    def test_the_order_endpoint_reserves_nothing_when_issuing_is_off(self):
        order = self.order()
        before = self.numbers()
        with override_settings(**OFF):
            response = self.client_for().post(f'/api/admin/orders/{order.pk}/fiscal-document/')
        self.assert_refused_and_untouched(response, before, 0)

    def test_the_order_endpoint_reserves_nothing_without_credentials(self):
        order = self.order()
        before = self.numbers()
        with override_settings(**ON_WITHOUT_CREDENTIALS):
            response = self.client_for().post(f'/api/admin/orders/{order.pk}/fiscal-document/')
        self.assert_refused_and_untouched(response, before, 0)

    def test_the_order_endpoint_still_issues_when_issuing_is_on(self):
        order = self.order()
        with override_settings(**ON):
            response = self.client_for().post(f'/api/admin/orders/{order.pk}/fiscal-document/')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['identifier'], 'F001-1')

    def test_a_credit_note_reserves_nothing_when_issuing_is_off(self):
        original = self.accepted()
        before = self.numbers()
        with override_settings(**OFF):
            response = self.client_for().post(
                f'/api/admin/fiscal-documents/{original.pk}/credit-notes/',
                {'reason_code': '01', 'reason_description': 'ANULACION DE LA OPERACION'}, format='json')
        self.assert_refused_and_untouched(response, before, 1)

    def test_a_debit_note_reserves_nothing_when_issuing_is_off(self):
        original = self.accepted()
        before = self.numbers()
        with override_settings(**OFF):
            response = self.client_for().post(
                f'/api/admin/fiscal-documents/{original.pk}/debit-notes/',
                {'reason_code': '01', 'reason_description': 'INTERESES POR MORA',
                 'taxable_amount': '10.00', 'tax_amount': '1.80'}, format='json')
        self.assert_refused_and_untouched(response, before, 1)

    def test_a_void_creates_nothing_when_issuing_is_off(self):
        original = self.accepted()
        attest_not_granted(original, actor=self.issuer, reason='NUNCA SE ENTREGO')
        with override_settings(**OFF):
            response = self.client_for().post(
                f'/api/admin/fiscal-documents/{original.pk}/void/',
                {'reason': 'EMITIDA POR ERROR'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE, response.data)
        self.assertFalse(FiscalVoidCommunication.objects.filter(company=self.company).exists())

    def test_a_void_still_works_when_issuing_is_on(self):
        original = self.accepted()
        attest_not_granted(original, actor=self.issuer, reason='NUNCA SE ENTREGO')
        with override_settings(**ON):
            response = self.client_for().post(
                f'/api/admin/fiscal-documents/{original.pk}/void/',
                {'reason': 'EMITIDA POR ERROR'}, format='json')
        self.assertIn(response.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED), response.data)
        self.assertTrue(FiscalVoidCommunication.objects.filter(company=self.company).exists())

    def _boleta_for_today(self):
        boleta = self.signed(self.order(Order.ReceiptType.BOLETA))
        return boleta, timezone.localtime(boleta.issued_at).date().isoformat()

    def test_a_daily_summary_creates_nothing_when_issuing_is_off(self):
        _boleta, today = self._boleta_for_today()
        with override_settings(**OFF):
            response = self.client_for().post('/api/admin/fiscal-summaries/', {'reference_date': today},
                                              format='json')
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE, response.data)
        self.assertFalse(FiscalDailySummary.objects.filter(company=self.company).exists())

    def test_a_daily_summary_creates_nothing_without_credentials(self):
        """H-07 rides along: an unsigned summary used to keep its boletas hostage."""
        _boleta, today = self._boleta_for_today()
        with override_settings(**ON_WITHOUT_CREDENTIALS):
            response = self.client_for().post('/api/admin/fiscal-summaries/', {'reference_date': today},
                                              format='json')
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE, response.data)
        self.assertFalse(FiscalDailySummary.objects.filter(company=self.company).exists())

    def test_a_daily_summary_still_works_when_issuing_is_on(self):
        _boleta, today = self._boleta_for_today()
        with override_settings(**ON):
            response = self.client_for().post('/api/admin/fiscal-summaries/', {'reference_date': today},
                                              format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(FiscalDailySummary.objects.filter(company=self.company).count(), 1)


class TestPaymentNeverGetsAProductionDocumentTest(_Base):
    """
    H-11. A TEST payment is not money (`PaymentTransaction.environment`). A BETA document
    for it is consistent — both sides are rehearsals — but a PRODUCTION document would
    declare to SUNAT a sale that never happened. Production cannot be switched on in this
    version, so the production side is reached by patching the server's environment
    resolver: the guard is what is under test, not the switch.
    """

    def paid_with(self, environment):
        order = self.order()
        PaymentTransaction.objects.create(
            order=order, provider='izipay', transaction_id=f'TX-{environment or "legacy"}',
            order_number=f'ORD-{order.pk}', amount=order.total, currency='PEN', environment=environment,
            status=PaymentTransaction.Status.AUTHORIZED, confirmed_at=timezone.now())
        return order

    def production(self):
        FiscalSeries.objects.create(company=self.company, document_type=FiscalDocumentType.INVOICE,
                                    series='F001', environment=FiscalEnvironment.PRODUCTION)
        return mock.patch('store.fiscal_config.resolve_environment', return_value=FiscalEnvironment.PRODUCTION)

    def production_numbers(self):
        return FiscalSeries.objects.get(company=self.company, environment=FiscalEnvironment.PRODUCTION).next_number

    def test_a_test_payment_is_refused_a_production_document(self):
        order = self.paid_with('test')
        with self.production():
            with self.assertRaisesMessage(FiscalError, 'pruebas'):
                get_or_create_fiscal_document(order)
        self.assertEqual(self.production_numbers(), 1)
        self.assertFalse(FiscalDocument.objects.filter(order=order).exists())

    def test_a_payment_that_does_not_say_production_is_refused_too(self):
        """Anything that does not say PRODUCTION is TEST (`integrations.payments.environment_of`)."""
        order = self.paid_with('')
        with self.production():
            with self.assertRaises(FiscalError):
                get_or_create_fiscal_document(order)
        self.assertEqual(self.production_numbers(), 1)

    def test_a_production_payment_gets_its_production_document(self):
        order = self.paid_with('production')
        with self.production():
            document, created = get_or_create_fiscal_document(order)
        self.assertTrue(created)
        self.assertEqual(document.environment, FiscalEnvironment.PRODUCTION)

    def test_a_till_sale_without_a_gateway_attempt_is_not_affected(self):
        order = self.order()
        with self.production():
            document, _ = get_or_create_fiscal_document(order)
        self.assertEqual(document.environment, FiscalEnvironment.PRODUCTION)

    def test_in_beta_a_test_payment_still_gets_its_beta_document(self):
        order = self.paid_with('test')
        document, created = get_or_create_fiscal_document(order)
        self.assertTrue(created)
        self.assertEqual(document.environment, FiscalEnvironment.BETA)

    def test_the_issue_endpoint_answers_400_and_spends_nothing(self):
        order = self.paid_with('test')
        with override_settings(**ON), self.production():
            response = self.client_for().post(f'/api/admin/orders/{order.pk}/fiscal-document/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.assertIn('pruebas', response.data['detail'])
        self.assertEqual(self.production_numbers(), 1)


class SendBillTransportIsHonestAboutUncertaintyTest(SimpleTestCase):
    """
    H-04 at the transport. Only a failure in the CONNECTION phase proves the body never
    left (ADR-28). The summary and the void already said so; `sendBill` did not.
    """

    def setUp(self):
        from store.fiscal.provider import SunatSoapProvider

        self.provider = SunatSoapProvider(endpoint='https://x/billService', ruc=RUC,
                                          sol_user='MODDATOS', sol_password='moddatos')

    def send(self, *, exc=None, response=None):
        from store.fiscal.provider import SunatSoapProvider

        if exc is not None:
            with mock.patch('requests.post', side_effect=exc):
                return self.provider.submit_invoice(filename='x.ZIP', zip_bytes=b'z')
        with mock.patch.object(SunatSoapProvider, '_post', return_value=response):
            return self.provider.submit_invoice(filename='x.ZIP', zip_bytes=b'z')

    def test_a_connect_timeout_was_never_sent(self):
        import requests

        from store.fiscal.provider import ProviderOutcome

        self.assertEqual(self.send(exc=requests.exceptions.ConnectTimeout('c')).outcome,
                         ProviderOutcome.TRANSPORT_ERROR)

    def test_a_read_timeout_is_uncertain(self):
        import requests

        from store.fiscal.provider import ProviderOutcome

        self.assertEqual(self.send(exc=requests.exceptions.ReadTimeout('r')).outcome,
                         ProviderOutcome.TRANSPORT_UNKNOWN)

    def test_a_reset_after_sending_is_uncertain(self):
        import requests

        from store.fiscal.provider import ProviderOutcome

        self.assertEqual(self.send(exc=requests.exceptions.ConnectionError('reset')).outcome,
                         ProviderOutcome.TRANSPORT_UNKNOWN)

    def test_a_5xx_is_uncertain(self):
        from store.fiscal.provider import ProviderOutcome

        self.assertEqual(self.send(response=(b'<x/>', 502)).outcome, ProviderOutcome.TRANSPORT_UNKNOWN)


@override_settings(**ON)
class UncertainSendIsNeverResentBlindTest(_Base):
    """
    H-03 and H-04 in the domain. An uncertain send leaves the document where only a CDR
    lookup can settle it: the same invoice is not transmitted again. FP-4's own state
    (`SUBMISSION_UNKNOWN`) needs a migration; until then the uncertainty is a fact of the
    last attempt — its `result` — and the document stays `SUBMISSION_ERROR`.
    """

    def uncertain(self, result):
        from store.fiscal.provider import ProviderResult

        document = self.signed(self.order())
        return submit_fiscal_document(document, _FakeProvider(ProviderResult(outcome=result, safe_message='x')))

    def test_an_uncertain_send_is_not_transmitted_again(self):
        from store.fiscal.provider import ProviderOutcome
        from store.fiscal_services import FiscalSubmissionUncertain
        from store.models import FiscalDocumentStatus

        for outcome in (ProviderOutcome.TRANSPORT_UNKNOWN, ProviderOutcome.UNKNOWN_RESPONSE):
            with self.subTest(outcome=outcome):
                document = self.uncertain(outcome)
                self.assertEqual(document.status, FiscalDocumentStatus.SUBMISSION_ERROR)
                again = _FakeProvider(_accepted())
                with self.assertRaises(FiscalSubmissionUncertain):
                    submit_fiscal_document(document, again)
                self.assertEqual(again.calls, 0, 'se reenvió a ciegas un envío incierto')
                self.assertEqual(document.attempts.count(), 1)

    def test_a_failure_that_never_left_is_still_retried(self):
        from store.fiscal.provider import ProviderOutcome

        document = self.uncertain(ProviderOutcome.TRANSPORT_ERROR)
        again = _FakeProvider(_accepted())
        document = submit_fiscal_document(document, again)
        self.assertEqual(again.calls, 1)
        self.assertTrue(document.is_accepted)

    def test_a_provider_exception_closes_the_attempt_and_blocks_a_blind_resend(self):
        from store.fiscal_services import FiscalSubmissionUncertain
        from store.models import FiscalDocumentStatus

        class Exploding:
            calls = 0

            def submit_invoice(self, *, filename, zip_bytes):
                Exploding.calls += 1
                raise RuntimeError('fallo al leer la respuesta')

        document = submit_fiscal_document(self.signed(self.order()), Exploding())
        self.assertEqual(document.status, FiscalDocumentStatus.SUBMISSION_ERROR)
        attempt = document.attempts.get()
        self.assertIsNotNone(attempt.finished_at, 'el intento quedó abierto')
        self.assertEqual(attempt.result, 'provider_exception')
        self.assertNotIn('fallo al leer', attempt.safe_message, 'el texto de la excepción no se guarda')

        later = timezone.now() + timedelta(minutes=30)
        again = _FakeProvider(_accepted())
        with mock.patch('django.utils.timezone.now', return_value=later):
            with self.assertRaises(FiscalSubmissionUncertain):
                submit_fiscal_document(document, again)
        self.assertEqual(again.calls, 0)

    def test_an_abandoned_attempt_does_not_unlock_a_resend(self):
        from store.fiscal_services import STALE_ATTEMPT_MINUTES, FiscalSubmissionUncertain
        from store.models import FiscalSubmissionAttempt

        document = self.signed(self.order())
        FiscalSubmissionAttempt.objects.create(
            document=document, attempt_number=1, environment=document.environment,
            started_at=timezone.now() - timedelta(minutes=STALE_ATTEMPT_MINUTES + 1), result='in_progress')
        again = _FakeProvider(_accepted())
        with self.assertRaises(FiscalSubmissionUncertain):
            submit_fiscal_document(document, again)
        self.assertEqual(again.calls, 0)

    def test_a_cdr_lookup_settles_an_uncertain_send(self):
        from store.fiscal.provider import ProviderOutcome
        from store.fiscal_services import reconcile_fiscal_document
        from store.tests import _FakeConsultProvider, _resolved_cdr

        document = self.uncertain(ProviderOutcome.TRANSPORT_UNKNOWN)
        outcome = reconcile_fiscal_document(document, _FakeConsultProvider(_resolved_cdr()))
        self.assertTrue(outcome.document.is_accepted)
        again = _FakeProvider(_accepted())
        submit_fiscal_document(outcome.document, again)
        self.assertEqual(again.calls, 0)

    def test_an_abandoned_attempt_can_be_reconciled(self):
        """The document is still SIGNED (the process died mid-send) but it may exist in SUNAT."""
        from store.fiscal_services import STALE_ATTEMPT_MINUTES, reconcile_fiscal_document
        from store.models import FiscalSubmissionAttempt
        from store.tests import _FakeConsultProvider, _resolved_cdr

        document = self.signed(self.order())
        FiscalSubmissionAttempt.objects.create(
            document=document, attempt_number=1, environment=document.environment,
            started_at=timezone.now() - timedelta(minutes=STALE_ATTEMPT_MINUTES + 1), result='in_progress')
        outcome = reconcile_fiscal_document(document, _FakeConsultProvider(_resolved_cdr()))
        self.assertTrue(outcome.document.is_accepted)

    def test_the_payload_offers_reconcile_not_retry(self):
        from store.fiscal.provider import ProviderOutcome
        from store.fiscal_services import STALE_ATTEMPT_MINUTES
        from store.fiscal_views import document_payload
        from store.models import FiscalSubmissionAttempt

        payload = document_payload(self.uncertain(ProviderOutcome.TRANSPORT_UNKNOWN))
        self.assertTrue(payload['submission_uncertain'])
        self.assertFalse(payload['can_retry'])
        self.assertTrue(payload['can_reconcile'])

        abandoned = self.signed(self.order())
        FiscalSubmissionAttempt.objects.create(
            document=abandoned, attempt_number=1, environment=abandoned.environment,
            started_at=timezone.now() - timedelta(minutes=STALE_ATTEMPT_MINUTES + 1), result='in_progress')
        payload = document_payload(abandoned)
        self.assertFalse(payload['can_submit'])
        self.assertTrue(payload['can_reconcile'])

        retryable = document_payload(self.uncertain(ProviderOutcome.TRANSPORT_ERROR))
        self.assertTrue(retryable['can_retry'])
        self.assertFalse(retryable['submission_uncertain'])

    def test_the_submit_endpoint_answers_409_without_calling(self):
        from store.fiscal.provider import ProviderOutcome

        document = self.uncertain(ProviderOutcome.TRANSPORT_UNKNOWN)
        again = _FakeProvider(_accepted())
        with mock.patch('store.fiscal_views.resolve_provider', return_value=again):
            response = self.client_for().post(f'/api/admin/fiscal-documents/{document.pk}/submit/')
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, response.data)
        self.assertEqual(again.calls, 0)

    def test_a_provider_exception_through_the_endpoint_is_recorded_not_a_500(self):
        from store.models import AdminAuditLog

        document = self.signed(self.order())

        class Exploding:
            def submit_invoice(self, *, filename, zip_bytes):
                raise ValueError('sobre ilegible')

        with mock.patch('store.fiscal_views.resolve_provider', return_value=Exploding()):
            response = self.client_for().post(f'/api/admin/fiscal-documents/{document.pk}/submit/')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertTrue(response.data['submission_uncertain'])
        self.assertTrue(AdminAuditLog.objects.filter(
            company=self.company, target_id=document.pk, action='fiscal_document_submission_error').exists())
