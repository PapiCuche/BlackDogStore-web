"""
SUNAT-CDR-BIND — the CDR that `sendBill` returns is bound to the document (H-06), and an
empty consult endpoint means the one in the code (H-14).

`docs/sunat-estado-2026-11.md`, G1 and G2:

- H-06: reconciliation, the daily summary and the voiding communication already refused a
  CDR that did not answer for their document (`cdr_matches_document`). The direct answer of
  `sendBill` did not: whatever CDR came back decided the status, even one for another
  series-number, another RUC or another document type.
- H-14: `settings.py` declares `FISCAL_CONSULT_ENDPOINT` with '' as its default, so
  `getattr(settings, ..., PRODUCTION_CONSULT_ENDPOINT)` found the empty setting and never
  the constant. With the consult switched on, the provider got an empty URL and the view
  answered 500. Now empty means the code's endpoint for PRODUCTION documents only: SUNAT
  BETA has no consult service, and a BETA document is never looked up in production.

No test here talks to SUNAT: every provider is a fake, and building the consult provider
makes no call.
"""
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from store.fiscal.provider import PRODUCTION_CONSULT_ENDPOINT, ProviderOutcome, ProviderResult
from store.fiscal.testing import self_signed_pem
from store.fiscal_config import FiscalConfigError, consult_endpoint, resolve_consult_provider
from store.fiscal_services import (
    CDR_MISMATCH_RESULT, FiscalSubmissionUncertain, get_or_create_fiscal_document,
    reconcile_fiscal_document, sign_fiscal_document, submit_fiscal_document,
)
from store.models import (
    AdminAuditLog, FiscalDocument, FiscalDocumentStatus, FiscalDocumentType, FiscalEnvironment, FiscalSeries, Order,
    OrderItem,
)
from store.tests import (
    _FakeConsultProvider, _FakeProvider, _accepted, _build_cdr_xml, _c1_product, _c1_stock, _p2d_member,
    _p3_company, _resolved_cdr,
)

RUC = '20100066603'
KEY, CERT = self_signed_pem(RUC)
ON = dict(
    FISCAL_ENABLED=True, FISCAL_SOL_RUC=RUC, FISCAL_SOL_USER='MODDATOS', FISCAL_SOL_PASSWORD='moddatos',
    FISCAL_CERT_PEM=CERT.decode(), FISCAL_KEY_PEM=KEY.decode(),
)


def _answer(*, reference_id='F001-1', receiver_ruc=RUC, doc_type='01', cdr_code='0', read_code='0',
            outcome=ProviderOutcome.ACCEPTED):
    """What `sendBill` handed back: a CDR, and the verdict `_read_cdr` read from it."""
    cdr = _build_cdr_xml(reference_id=reference_id, response_code=cdr_code, receiver_ruc=receiver_ruc,
                         doc_type=doc_type, sign=False)
    return ProviderResult(outcome=outcome, response_code=read_code, safe_message='ok', cdr_xml=cdr,
                          cdr_zip=b'PK-zip', request_sha256='a' * 64, response_sha256='b' * 64)


#: The structure of the CDR SUNAT BETA returned for F001-34630 on 2026-11-27 (signature removed,
#: identifiers templated). `{ID}` is the series-number it answers for.
REAL_BETA_SHAPE = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<ar:ApplicationResponse xmlns:ar="urn:oasis:names:specification:ubl:schema:xsd:ApplicationResponse-2"'
    b' xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"'
    b' xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"'
    b' xmlns:ext="urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2">'
    b'<ext:UBLExtensions><ext:UBLExtension><ext:ExtensionContent/></ext:UBLExtension></ext:UBLExtensions>'
    b'<cbc:UBLVersionID>2.0</cbc:UBLVersionID><cbc:CustomizationID>1.0</cbc:CustomizationID>'
    b'<cbc:ID>1795769200626</cbc:ID><cbc:IssueDate>2026-11-27</cbc:IssueDate>'
    b'<cbc:IssueTime>00:00:00</cbc:IssueTime><cbc:ResponseDate>2026-11-27</cbc:ResponseDate>'
    b'<cbc:ResponseTime>03:46:40</cbc:ResponseTime>'
    b'<cbc:Note>4252 - EL DATO INGRESADO NO CUMPLE</cbc:Note>'
    b'<cac:Signature><cbc:ID>SignSUNAT</cbc:ID><cac:SignatoryParty><cac:PartyIdentification>'
    b'<cbc:ID>20131312955</cbc:ID></cac:PartyIdentification><cac:PartyName><cbc:Name>SUNAT</cbc:Name>'
    b'</cac:PartyName></cac:SignatoryParty></cac:Signature>'
    b'<cac:SenderParty><cac:PartyIdentification><cbc:ID>20131312955</cbc:ID></cac:PartyIdentification>'
    b'</cac:SenderParty>'
    b'<cac:ReceiverParty><cac:PartyIdentification><cbc:ID>20100066603</cbc:ID></cac:PartyIdentification>'
    b'</cac:ReceiverParty>'
    b'<cac:DocumentResponse><cac:Response><cbc:ReferenceID>{ID}</cbc:ReferenceID>'
    b'<cbc:ResponseCode>0</cbc:ResponseCode>'
    b'<cbc:Description>La Factura numero {ID}, ha sido aceptada</cbc:Description></cac:Response>'
    b'<cac:DocumentReference><cbc:ID>{ID}</cbc:ID></cac:DocumentReference>'
    b'<cac:RecipientParty><cac:PartyIdentification><cbc:ID>6-20000000001</cbc:ID>'
    b'</cac:PartyIdentification></cac:RecipientParty></cac:DocumentResponse>'
    b'</ar:ApplicationResponse>'
)


@override_settings(**ON)
class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('cdrbind', 'Empresa CDR', tax_id=RUC, legal_name='EMPRESA CDR SAC')
        self.branch = self.company.default_inventory_branch
        FiscalSeries.objects.create(company=self.company, document_type=FiscalDocumentType.INVOICE, series='F001')
        self.product = _c1_product(self.company, 'Articulo CDR', '118.00')
        _c1_stock(self.branch, self.product, 10)
        self.issuer, _ = _p2d_member(self.company, 'cdrbind_emisor',
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


class ASendBillCdrIsBoundToItsDocumentTest(_Base):
    def assert_discarded(self, document, answer):
        document = submit_fiscal_document(document, _FakeProvider(answer))
        stored = FiscalDocument.objects.get(pk=document.pk)
        self.assertEqual(stored.status, FiscalDocumentStatus.SUBMISSION_ERROR)
        self.assertFalse(stored.is_accepted)
        self.assertIsNone(stored.cdr_accepted_at)
        # Somebody else's CDR is not this document's evidence.
        self.assertEqual(stored.cdr_xml, '')
        self.assertIsNone(stored.cdr_raw)
        self.assertIsNone(stored.cdr_zip)
        self.assertEqual(stored.sunat_response_code, '')
        attempt = stored.attempts.get()
        self.assertEqual(attempt.result, CDR_MISMATCH_RESULT)
        self.assertIsNotNone(attempt.finished_at)
        self.assertIn('CDR descartado', attempt.safe_message)
        return stored

    def test_a_cdr_for_another_number_is_not_applied(self):
        stored = self.assert_discarded(self.signed(), _answer(reference_id='F001-99'))
        self.assertIn('F001-99', stored.sunat_response_message)

    def test_a_cdr_for_another_ruc_is_not_applied_and_that_ruc_is_never_shown(self):
        stored = self.assert_discarded(self.signed(), _answer(receiver_ruc='20999999991'))
        self.assertNotIn('20999999991', stored.sunat_response_message)
        self.assertNotIn('20999999991', stored.attempts.get().safe_message)
        self.assertIn('otro emisor', stored.sunat_response_message)

    def test_the_discarded_cdr_leaves_its_hash_on_the_attempt(self):
        import hashlib

        answer = _answer(reference_id='F001-99')
        stored = self.assert_discarded(self.signed(), answer)
        self.assertIn(hashlib.sha256(answer.cdr_xml).hexdigest(), stored.attempts.get().safe_message)

    def test_a_cdr_for_another_document_type_is_not_applied(self):
        self.assert_discarded(self.signed(), _answer(doc_type='07'))

    def test_a_cdr_whose_two_readings_disagree_is_not_applied(self):
        # The strict reading says 2335 (rejected); the verdict handed in says 0 (accepted).
        self.assert_discarded(self.signed(), _answer(cdr_code='2335', read_code='0'))

    def test_a_rejection_for_another_document_does_not_burn_this_one(self):
        self.assert_discarded(self.signed(), _answer(
            reference_id='F001-7', cdr_code='2335', read_code='2335', outcome=ProviderOutcome.REJECTED))

    def test_an_unreadable_identity_is_not_applied(self):
        answer = ProviderResult(outcome=ProviderOutcome.ACCEPTED, response_code='0', safe_message='ok',
                                cdr_xml=b'<ApplicationResponse><ResponseCode>0</ResponseCode></ApplicationResponse>')
        self.assert_discarded(self.signed(), answer)

    def test_the_text_of_a_foreign_cdr_is_cleaned_before_it_is_shown(self):
        stored = self.assert_discarded(self.signed(), _answer(reference_id='F001-1"<script>'))
        self.assertNotIn('<', stored.sunat_response_message)
        self.assertNotIn('"', stored.sunat_response_message)

    def test_the_shape_of_a_real_beta_cdr_is_applied(self):
        """
        The shape SUNAT BETA returned on 2026-11-27 for an accepted factura: `Response/ReferenceID`
        present, `DocumentReference` with its ID and NO `DocumentTypeCode`, the receiver as the bare
        RUC, the buyer as «6-RUC», and observations as top-level `cbc:Note`. Rebuilt here without
        SUNAT's signature.
        """
        cdr = REAL_BETA_SHAPE.replace(b'{ID}', b'F001-1')
        answer = ProviderResult(outcome=ProviderOutcome.ACCEPTED_WITH_OBSERVATION, response_code='0',
                                safe_message='ok', notes=('4252 - EL DATO INGRESADO NO CUMPLE',),
                                cdr_xml=cdr, cdr_zip=b'PK-zip')
        document = submit_fiscal_document(self.signed(), _FakeProvider(answer))
        stored = FiscalDocument.objects.get(pk=document.pk)
        self.assertEqual(stored.status, FiscalDocumentStatus.ACCEPTED_WITH_OBSERVATION)
        self.assertEqual(bytes(stored.cdr_raw), cdr)

    def test_the_real_beta_shape_for_another_number_is_discarded(self):
        cdr = REAL_BETA_SHAPE.replace(b'{ID}', b'F001-2')
        answer = ProviderResult(outcome=ProviderOutcome.ACCEPTED, response_code='0', safe_message='ok', cdr_xml=cdr)
        self.assert_discarded(self.signed(), answer)

    def test_its_own_cdr_is_still_applied(self):
        document = submit_fiscal_document(self.signed(), _FakeProvider(_answer()))
        stored = FiscalDocument.objects.get(pk=document.pk)
        self.assertTrue(stored.is_accepted)
        self.assertIsNotNone(stored.cdr_raw)
        self.assertIsNotNone(stored.cdr_accepted_at)
        self.assertEqual(stored.attempts.get().result, ProviderOutcome.ACCEPTED.value)

    def test_a_discarded_cdr_is_never_resent_blind_but_can_be_reconciled(self):
        document = self.assert_discarded(self.signed(), _answer(reference_id='F001-99'))
        again = _FakeProvider(_accepted())
        with self.assertRaises(FiscalSubmissionUncertain):
            submit_fiscal_document(document, again)
        self.assertEqual(again.calls, 0, 'se reenvió a ciegas tras un CDR ajeno')

        outcome = reconcile_fiscal_document(document, _FakeConsultProvider(_resolved_cdr()))
        self.assertEqual(outcome.action, 'reconciled')
        self.assertTrue(FiscalDocument.objects.get(pk=document.pk).is_accepted)

    def test_the_endpoint_offers_reconcile_and_audits_the_mismatch(self):
        document = self.signed()
        client = APIClient()
        client.force_authenticate(user=self.issuer)
        with mock.patch('store.fiscal_views.resolve_provider',
                        return_value=_FakeProvider(_answer(reference_id='F001-99'))):
            response = client.post(f'/api/admin/fiscal-documents/{document.pk}/submit/')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertFalse(response.data['is_accepted'])
        self.assertTrue(response.data['submission_uncertain'])
        self.assertTrue(response.data['can_reconcile'])
        self.assertFalse(response.data['can_retry'])
        entry = AdminAuditLog.objects.get(
            company=self.company, target_id=document.pk, action='fiscal_document_submission_error')
        self.assertEqual(entry.metadata['attempt_result'], CDR_MISMATCH_RESULT)


@override_settings(**ON, FISCAL_CONSULT_ENABLED=True)
class TheConsultEndpointDependsOnTheDocumentsEnvironmentTest(_Base):
    """H-14, and its review: empty means the code's endpoint only for PRODUCTION documents."""

    def test_empty_means_the_documented_endpoint_for_production(self):
        for configured in ('', '   '):
            with self.subTest(configured=configured), override_settings(FISCAL_CONSULT_ENDPOINT=configured):
                self.assertEqual(consult_endpoint(FiscalEnvironment.PRODUCTION), PRODUCTION_CONSULT_ENDPOINT)
                provider = resolve_consult_provider(self.company, environment=FiscalEnvironment.PRODUCTION)
                self.assertEqual(provider._endpoint, PRODUCTION_CONSULT_ENDPOINT)

    def test_a_beta_document_is_never_consulted_against_production(self):
        for configured in ('', '   '):
            with self.subTest(configured=configured), override_settings(FISCAL_CONSULT_ENDPOINT=configured):
                with self.assertRaises(FiscalConfigError):
                    consult_endpoint(FiscalEnvironment.BETA)
                with self.assertRaises(FiscalConfigError):
                    resolve_consult_provider(self.company, environment=FiscalEnvironment.BETA)

    def test_an_explicit_https_endpoint_is_honoured_for_both(self):
        url = 'https://consulta.example.test/billConsultService'
        with override_settings(FISCAL_CONSULT_ENDPOINT=url):
            for environment in (FiscalEnvironment.BETA, FiscalEnvironment.PRODUCTION):
                with self.subTest(environment=environment):
                    provider = resolve_consult_provider(self.company, environment=environment)
                    self.assertEqual(provider._endpoint, url)

    def test_a_plain_http_endpoint_fails_closed_without_repeating_it(self):
        with override_settings(FISCAL_CONSULT_ENDPOINT='http://consulta.example.test/x'):
            for environment in (FiscalEnvironment.BETA, FiscalEnvironment.PRODUCTION):
                with self.subTest(environment=environment), self.assertRaises(FiscalConfigError) as caught:
                    resolve_consult_provider(self.company, environment=environment)
                self.assertNotIn('consulta.example.test', str(caught.exception))

    def uncertain(self):
        return submit_fiscal_document(self.signed(), _FakeProvider(ProviderResult(
            outcome=ProviderOutcome.TRANSPORT_UNKNOWN, safe_message='x')))

    def reconcile(self, document):
        client = APIClient()
        client.force_authenticate(user=self.issuer)
        return client.post(f'/api/admin/fiscal-documents/{document.pk}/reconcile/')

    def test_the_reconcile_view_refuses_a_beta_document_without_an_endpoint(self):
        document = self.uncertain()
        with override_settings(FISCAL_CONSULT_ENDPOINT=''), \
                mock.patch('store.fiscal.provider.SunatConsultProvider.get_document_cdr') as network:
            response = self.reconcile(document)
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE, response.data)
        network.assert_not_called()
        self.assertEqual(FiscalDocument.objects.get(pk=document.pk).status, FiscalDocumentStatus.SUBMISSION_ERROR)

    def test_the_reconcile_view_answers_503_not_500_for_a_bad_endpoint(self):
        document = self.uncertain()
        with override_settings(FISCAL_CONSULT_ENDPOINT='http://consulta.example.test/x'):
            response = self.reconcile(document)
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE, response.data)
        self.assertEqual(FiscalDocument.objects.get(pk=document.pk).status, FiscalDocumentStatus.SUBMISSION_ERROR)
