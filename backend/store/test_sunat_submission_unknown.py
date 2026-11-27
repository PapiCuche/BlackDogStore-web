"""
SUNAT G5 (docs/sunat-estado-2026-11.md; migration 0199) — an uncertain invoice send has its own status, and a
CDR that did not answer for its document is kept on the attempt.

Until 0199 an uncertain send (read timeout, cut after sending, 5xx, unreadable answer, adapter failure mid-send,
abandoned attempt, foreign CDR) left the document in `SUBMISSION_ERROR`, the same status as a send that provably
never left, and the difference was read from the last attempt. Now:

- never left (`TRANSPORT_ERROR`)                → SUBMISSION_ERROR   → the same document is sent again;
- transmitted without a usable verdict          → SUBMISSION_UNKNOWN → only reconciled by its CDR, never resent;
- an abandoned attempt / a legacy uncertain row → becomes SUBMISSION_UNKNOWN the moment somebody asks to send;
- a foreign CDR                                 → SUBMISSION_UNKNOWN, its bytes (bounded) and hash on the attempt,
  never in an API.

No test here talks to SUNAT: every provider is a fake.
"""
from datetime import timedelta
from decimal import Decimal

from django.apps import apps as global_apps
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from store.fiscal.provider import ProviderOutcome, ProviderResult
from store.fiscal.testing import self_signed_pem
from store.fiscal_services import (
    STALE_ATTEMPT_MINUTES, FiscalSubmissionUncertain, get_or_create_fiscal_document, reconcile_fiscal_document,
    sign_fiscal_document, submission_uncertainty, submit_fiscal_document,
)
from store.models import (
    FiscalDocument, FiscalDocumentStatus, FiscalDocumentType, FiscalSeries, FiscalSubmissionAttempt, Order,
    OrderItem,
)
from store.tests import (
    _FakeConsultProvider, _FakeProvider, _accepted, _build_cdr_xml, _c1_product, _c1_stock, _p2d_member,
    _p3_company, _resolved_cdr,
)

RUC = '20100066603'
KEY, CERT = self_signed_pem(RUC)
ON = dict(FISCAL_ENABLED=True, FISCAL_SOL_RUC=RUC, FISCAL_SOL_USER='MODDATOS', FISCAL_SOL_PASSWORD='moddatos',
          FISCAL_CERT_PEM=CERT.decode(), FISCAL_KEY_PEM=KEY.decode())


class _Raising:
    calls = 0

    def submit_invoice(self, *, filename, zip_bytes):
        _Raising.calls += 1
        raise RuntimeError('fallo al leer la respuesta')


@override_settings(**ON)
class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('g5', 'Empresa G5', tax_id=RUC, legal_name='EMPRESA G5 SAC')
        self.branch = self.company.default_inventory_branch
        FiscalSeries.objects.create(company=self.company, document_type=FiscalDocumentType.INVOICE, series='F001')
        self.product = _c1_product(self.company, 'Articulo G5', '118.00')
        _c1_stock(self.branch, self.product, 20)
        self.issuer, _ = _p2d_member(self.company, 'g5_emisor',
                                     ['company.view', 'sales.fiscal.view', 'sales.fiscal.issue'])

    def signed(self):
        order = Order.objects.create(
            company=self.company, customer_name='CLIENTE SAC', document_type=Order.DocumentType.RUC,
            document_number='20000000001', receipt_type=Order.ReceiptType.FACTURA, total=Decimal('118.00'),
            discount_amount=Decimal('0.00'), subtotal_amount=Decimal('118.00'), taxable_amount=Decimal('100.00'),
            tax_amount=Decimal('18.00'), tax_rate=Decimal('0.18'), tax_treatment='taxed', currency='PEN',
            status=Order.Status.PAID, paid=True, paid_at=timezone.now(), fulfillment_branch=self.branch)
        OrderItem.objects.create(order=order, product=self.product, quantity=1, price=Decimal('118.00'))
        document, _ = get_or_create_fiscal_document(order)
        return sign_fiscal_document(document, key_pem=KEY, cert_pem=CERT)

    def sent(self, result_or_provider):
        provider = result_or_provider if hasattr(result_or_provider, 'submit_invoice') \
            else _FakeProvider(result_or_provider)
        document = submit_fiscal_document(self.signed(), provider)
        return FiscalDocument.objects.get(pk=document.pk)

    def client_for(self):
        client = APIClient()
        client.force_authenticate(user=self.issuer)
        return client


class EachOutcomeLandsInItsStatusTest(_Base):
    def test_a_send_that_never_left_is_a_submission_error_and_is_sent_again(self):
        document = self.sent(ProviderResult(outcome=ProviderOutcome.TRANSPORT_ERROR, safe_message='x'))
        self.assertEqual(document.status, FiscalDocumentStatus.SUBMISSION_ERROR)
        self.assertEqual(submission_uncertainty(document), '')
        again = _FakeProvider(_accepted())
        self.assertTrue(submit_fiscal_document(document, again).is_accepted)
        self.assertEqual(again.calls, 1)

    def test_a_transmission_without_verdict_is_unknown(self):
        for outcome in (ProviderOutcome.TRANSPORT_UNKNOWN, ProviderOutcome.UNKNOWN_RESPONSE):
            with self.subTest(outcome=outcome):
                document = self.sent(ProviderResult(outcome=outcome, safe_message='x'))
                self.assertEqual(document.status, FiscalDocumentStatus.SUBMISSION_UNKNOWN)
                self.assertTrue(submission_uncertainty(document))

    def test_an_adapter_failure_mid_send_is_unknown(self):
        document = self.sent(_Raising())
        self.assertEqual(document.status, FiscalDocumentStatus.SUBMISSION_UNKNOWN)
        self.assertEqual(document.attempts.get().result, 'provider_exception')

    def test_a_foreign_cdr_is_unknown_and_kept_on_the_attempt(self):
        import hashlib

        cdr = _build_cdr_xml(reference_id='F001-99', sign=False)
        document = self.sent(ProviderResult(outcome=ProviderOutcome.ACCEPTED, response_code='0',
                                            safe_message='ok', cdr_xml=cdr))
        self.assertEqual(document.status, FiscalDocumentStatus.SUBMISSION_UNKNOWN)
        self.assertIsNone(document.cdr_raw)                       # not this document's evidence
        attempt = document.attempts.get()
        self.assertEqual(attempt.result, 'cdr_mismatch')
        self.assertEqual(bytes(attempt.discarded_cdr), cdr)
        self.assertEqual(attempt.discarded_cdr_sha256, hashlib.sha256(cdr).hexdigest())


class AnUnknownSendIsOnlyReconciledTest(_Base):
    def unknown(self):
        return self.sent(ProviderResult(outcome=ProviderOutcome.TRANSPORT_UNKNOWN, safe_message='x'))

    def test_it_is_never_transmitted_again(self):
        document = self.unknown()
        again = _FakeProvider(_accepted())
        with self.assertRaises(FiscalSubmissionUncertain):
            submit_fiscal_document(document, again)
        self.assertEqual(again.calls, 0)
        self.assertEqual(document.attempts.count(), 1)

    def test_the_status_alone_is_enough_to_refuse(self):
        """A row marked by 0199 whose attempts say nothing more is still uncertain."""
        document = self.unknown()
        FiscalSubmissionAttempt.objects.filter(document=document).update(result='transport_error')
        again = _FakeProvider(_accepted())
        with self.assertRaises(FiscalSubmissionUncertain):
            submit_fiscal_document(document, again)
        self.assertEqual(again.calls, 0)
        self.assertEqual(submission_uncertainty(document), FiscalDocumentStatus.SUBMISSION_UNKNOWN.value)

    def test_a_cdr_lookup_settles_it_either_way(self):
        document = self.unknown()
        outcome = reconcile_fiscal_document(document, _FakeConsultProvider(_resolved_cdr()))
        self.assertEqual(outcome.action, 'reconciled')
        self.assertTrue(FiscalDocument.objects.get(pk=document.pk).is_accepted)

        rejected = self.unknown()
        outcome = reconcile_fiscal_document(rejected, _FakeConsultProvider(_resolved_cdr(
            reference_id=rejected.document_id, outcome=ProviderOutcome.REJECTED, response_code='2335')))
        self.assertEqual(outcome.new_status, FiscalDocumentStatus.REJECTED)

    def test_the_payload_and_the_paper_say_so(self):
        from store.fiscal_pdf_services import _STATUS_TEXT
        from store.fiscal_views import document_payload

        payload = document_payload(self.unknown())
        self.assertEqual(payload['status'], FiscalDocumentStatus.SUBMISSION_UNKNOWN)
        self.assertTrue(payload['submission_uncertain'])
        self.assertTrue(payload['can_reconcile'])
        self.assertFalse(payload['can_retry'])
        self.assertFalse(payload['can_submit'])
        self.assertTrue(payload['can_download_pdf'])
        self.assertEqual(_STATUS_TEXT[FiscalDocumentStatus.SUBMISSION_UNKNOWN], 'Pendiente de confirmación de SUNAT')

    def test_the_submit_endpoint_answers_409_without_calling(self):
        from unittest import mock

        document = self.unknown()
        again = _FakeProvider(_accepted())
        with mock.patch('store.fiscal_views.resolve_provider', return_value=again):
            response = self.client_for().post(f'/api/admin/fiscal-documents/{document.pk}/submit/')
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, response.data)
        self.assertEqual(again.calls, 0)

    def test_accounting_names_it_uncertain(self):
        from store.accounting.fiscal_states import DOCUMENT_STATUS, LABELS
        from store.accounting.pending import UNCERTAIN

        code = DOCUMENT_STATUS[FiscalDocumentStatus.SUBMISSION_UNKNOWN]
        self.assertIn(code, UNCERTAIN)
        self.assertIn('no se reenvía', LABELS[code])


class DoubtBecomesAStatusTest(_Base):
    def test_an_abandoned_attempt_turns_the_document_unknown_when_a_send_is_asked(self):
        document = self.signed()
        FiscalSubmissionAttempt.objects.create(
            document=document, attempt_number=1, environment=document.environment, result='in_progress',
            started_at=timezone.now() - timedelta(minutes=STALE_ATTEMPT_MINUTES + 1))
        again = _FakeProvider(_accepted())
        with self.assertRaises(FiscalSubmissionUncertain):
            submit_fiscal_document(document, again)
        self.assertEqual(again.calls, 0)
        self.assertEqual(FiscalDocument.objects.get(pk=document.pk).status, FiscalDocumentStatus.SUBMISSION_UNKNOWN)

    def test_a_legacy_uncertain_row_turns_unknown_too(self):
        document = self.sent(ProviderResult(outcome=ProviderOutcome.TRANSPORT_UNKNOWN, safe_message='x'))
        FiscalDocument.objects.filter(pk=document.pk).update(status=FiscalDocumentStatus.SUBMISSION_ERROR)
        with self.assertRaises(FiscalSubmissionUncertain):
            submit_fiscal_document(FiscalDocument.objects.get(pk=document.pk), _FakeProvider(_accepted()))
        self.assertEqual(FiscalDocument.objects.get(pk=document.pk).status, FiscalDocumentStatus.SUBMISSION_UNKNOWN)


class TheDiscardedCdrIsBoundedAndPrivateTest(_Base):
    def test_a_cdr_over_the_bound_keeps_only_its_hash(self):
        big = _build_cdr_xml(reference_id='F001-99', sign=False).replace(
            b'</ar:ApplicationResponse>',
            b'<!--' + b'x' * FiscalSubmissionAttempt.MAX_DISCARDED_CDR_BYTES + b'--></ar:ApplicationResponse>')
        document = self.sent(ProviderResult(outcome=ProviderOutcome.ACCEPTED, response_code='0',
                                            safe_message='ok', cdr_xml=big))
        attempt = document.attempts.get()
        self.assertEqual(attempt.result, 'cdr_mismatch')
        self.assertIsNone(attempt.discarded_cdr)
        self.assertEqual(len(attempt.discarded_cdr_sha256), 64)

    def test_the_database_refuses_an_oversized_row(self):
        document = self.signed()
        with self.assertRaises(IntegrityError), transaction.atomic():
            FiscalSubmissionAttempt.objects.create(
                document=document, attempt_number=1, environment=document.environment, result='cdr_mismatch',
                started_at=timezone.now(), discarded_cdr=b'x' * (FiscalSubmissionAttempt.MAX_DISCARDED_CDR_BYTES + 1))

    def test_no_api_answer_carries_it(self):
        from unittest import mock

        marker = b'MARCADOR-CDR-DESCARTADO'
        cdr = _build_cdr_xml(reference_id='F001-99', sign=False).replace(
            b'</ar:ApplicationResponse>', b'<!--' + marker + b'--></ar:ApplicationResponse>')
        client = self.client_for()
        document = self.signed()
        with mock.patch('store.fiscal_views.resolve_provider', return_value=_FakeProvider(ProviderResult(
                outcome=ProviderOutcome.ACCEPTED, response_code='0', safe_message='ok', cdr_xml=cdr))):
            submitted = client.post(f'/api/admin/fiscal-documents/{document.pk}/submit/')
        self.assertIsNotNone(FiscalSubmissionAttempt.objects.get(document=document).discarded_cdr)
        answers = [submitted,
                   client.get(f'/api/admin/orders/{document.order_id}/fiscal-document/'),
                   client.get(f'/api/admin/fiscal-documents/{document.pk}/cdr/'),
                   client.get(f'/api/admin/fiscal-documents/{document.pk}/xml/')]
        for answer in answers:
            with self.subTest(url=answer.wsgi_request.path):
                self.assertNotIn(marker, answer.content)
                self.assertNotIn(b'discarded_cdr', answer.content)


class TheMigrationMarksOnlyUncertainRowsTest(_Base):
    def migration(self):
        from importlib import import_module

        return import_module('store.migrations.0199_fiscal_submission_unknown')

    def test_forward_marks_legacy_uncertain_rows_and_reverse_undoes_it(self):
        uncertain = self.sent(ProviderResult(outcome=ProviderOutcome.TRANSPORT_UNKNOWN, safe_message='x'))
        never_left = self.sent(ProviderResult(outcome=ProviderOutcome.TRANSPORT_ERROR, safe_message='x'))
        FiscalDocument.objects.filter(pk=uncertain.pk).update(status=FiscalDocumentStatus.SUBMISSION_ERROR)

        self.migration().mark_uncertain(global_apps, None)
        self.assertEqual(FiscalDocument.objects.get(pk=uncertain.pk).status, FiscalDocumentStatus.SUBMISSION_UNKNOWN)
        self.assertEqual(FiscalDocument.objects.get(pk=never_left.pk).status, FiscalDocumentStatus.SUBMISSION_ERROR)

        self.migration().unmark_uncertain(global_apps, None)
        self.assertEqual(FiscalDocument.objects.get(pk=uncertain.pk).status, FiscalDocumentStatus.SUBMISSION_ERROR)

    def test_reverse_refuses_while_a_discarded_cdr_is_kept(self):
        self.sent(ProviderResult(outcome=ProviderOutcome.ACCEPTED, response_code='0', safe_message='ok',
                                 cdr_xml=_build_cdr_xml(reference_id='F001-99', sign=False)))
        with self.assertRaises(RuntimeError) as caught:
            self.migration().refuse_reverse_with_discarded_cdrs(global_apps, None)
        self.assertIn('0199', str(caught.exception))
        FiscalSubmissionAttempt.objects.update(discarded_cdr=None)
        self.assertIsNone(self.migration().refuse_reverse_with_discarded_cdrs(global_apps, None))
