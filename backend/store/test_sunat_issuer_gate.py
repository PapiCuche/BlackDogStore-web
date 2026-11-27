"""
SUNAT-ISSUER-GATE — a company never issues with another taxpayer's credentials or with a
certificate out of force (`docs/sunat-estado-2026-11.md`, G3).

The SOL credentials and the certificate belong to the installation, and nothing checked
that their RUC was the RUC of the company selling. With two companies, the second one
signed and sent in the name of the first. A certificate that had expired was discovered
only after the correlative was spent.

Every path that asks for credentials — issuing, notes, voids, daily summaries, sending,
the cash desk — asks `resolve_credentials(company)`, which now also checks:

1. the company has a RUC and it is the RUC of the SOL credentials;
2. the certificate is in force;
3. in PRODUCTION only, the company's RUC is in the certificate's subject (SUNAT asks for
   it in OU), and the certificate is not self-signed (C3). Tested directly here; the
   whole production path is in `test_sunat_production.py`.

No test here talks to SUNAT. Certificates are self-signed and generated at test time.
"""
import datetime as dt
from decimal import Decimal
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from store.fiscal.provider import ProviderOutcome, ProviderResult, SunatSoapProvider
from store.fiscal_config import FiscalConfigError, issuer_problems, resolve_credentials
from store.models import (
    AdminAuditLog, FiscalDocument, FiscalDocumentType, FiscalEnvironment, FiscalSeries, FiscalSubmissionAttempt,
    Order, OrderItem,
)
from store.tests import _c1_product, _c1_stock, _p2d_member, _p3_company

RUC = '20100066603'
OTHER_RUC = '20999999991'


def _certificate(*, ou=None, cn=RUC, days_before=1, days_after=365, ca=False):
    """
    (key PEM, certificate PEM) with the validity and subject asked for: self-signed, or
    issued by a throwaway test CA (`ca=True`), which is what production signs with (C3).
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    signer = rsa.generate_private_key(public_exponent=65537, key_size=2048) if ca else key
    attributes = [x509.NameAttribute(NameOID.COUNTRY_NAME, 'PE'),
                  x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'ENTORNO DE PRUEBAS')]
    if ou is not None:
        attributes.append(x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, ou))
    attributes.append(x509.NameAttribute(NameOID.COMMON_NAME, cn))
    name = x509.Name(attributes)
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'CA DE PRUEBA')]) if ca else name
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(issuer).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=days_before))
            .not_valid_after(now + dt.timedelta(days=days_after))
            .sign(signer, hashes.SHA256()))
    return (key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()),
            cert.public_bytes(serialization.Encoding.PEM))


def _settings(*, ruc=RUC, key_cert=None):
    key, cert = key_cert or _certificate()
    return dict(FISCAL_ENABLED=True, FISCAL_SOL_RUC=ruc, FISCAL_SOL_USER='MODDATOS', FISCAL_SOL_PASSWORD='moddatos',
                FISCAL_CERT_PEM=cert.decode(), FISCAL_KEY_PEM=key.decode())


MATCHING = _settings()
FOREIGN = _settings(ruc=OTHER_RUC)
EXPIRED = _settings(key_cert=_certificate(days_before=400, days_after=-10))
NOT_YET = _settings(key_cert=_certificate(days_before=-5, days_after=365))


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('gate', 'Empresa Gate', tax_id=RUC, legal_name='EMPRESA GATE SAC')
        self.branch = self.company.default_inventory_branch
        self.series = FiscalSeries.objects.create(
            company=self.company, document_type=FiscalDocumentType.INVOICE, series='F001')
        self.product = _c1_product(self.company, 'Articulo Gate', '118.00')
        _c1_stock(self.branch, self.product, 10)
        self.issuer, _ = _p2d_member(self.company, 'gate_emisor',
                                     ['company.view', 'sales.fiscal.view', 'sales.fiscal.issue'])
        self.client = APIClient()
        self.client.force_authenticate(user=self.issuer)

    def order(self):
        order = Order.objects.create(
            company=self.company, customer_name='CLIENTE SAC', document_type=Order.DocumentType.RUC,
            document_number='20000000001', receipt_type=Order.ReceiptType.FACTURA, total=Decimal('118.00'),
            discount_amount=Decimal('0.00'), subtotal_amount=Decimal('118.00'), taxable_amount=Decimal('100.00'),
            tax_amount=Decimal('18.00'), tax_rate=Decimal('0.18'), tax_treatment='taxed', currency='PEN',
            status=Order.Status.PAID, paid=True, paid_at=timezone.now(), fulfillment_branch=self.branch)
        OrderItem.objects.create(order=order, product=self.product, quantity=1, price=Decimal('118.00'))
        return order

    def issue(self, order):
        return self.client.post(f'/api/admin/orders/{order.pk}/fiscal-document/')

    def assert_refused(self, response, fragment):
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE, response.data)
        self.assertIn(fragment, response.data['detail'])
        self.series.refresh_from_db()
        self.assertEqual(self.series.next_number, 1, 'se gastó un correlativo')
        self.assertFalse(FiscalDocument.objects.filter(company=self.company).exists())


class AnotherTaxpayersCredentialsNeverIssueTest(_Base):
    def test_matching_credentials_still_issue(self):
        with override_settings(**MATCHING):
            response = self.issue(self.order())
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['identifier'], 'F001-1')

    def test_credentials_of_another_ruc_reserve_nothing(self):
        with override_settings(**FOREIGN):
            response = self.issue(self.order())
        self.assert_refused(response, 'no son del RUC de esta empresa')
        # The refusal is audited with the company, and never names the other RUC.
        entry = AdminAuditLog.objects.get(company=self.company, action='fiscal_issue_refused')
        self.assertNotIn(OTHER_RUC, str(entry.metadata))
        self.assertNotIn(OTHER_RUC, str(response.data))

    def test_a_company_without_ruc_reserves_nothing(self):
        type(self.company).objects.filter(pk=self.company.pk).update(tax_id='')
        with override_settings(**MATCHING):
            response = self.issue(self.order())
        self.assert_refused(response, 'no tiene RUC')

    def test_two_companies_one_installation_only_the_owner_of_the_ruc_issues(self):
        other = _p3_company('gate-b', 'Empresa B', tax_id=OTHER_RUC, legal_name='EMPRESA B SAC')
        with override_settings(**MATCHING):
            resolve_credentials(self.company, signing=True)
            with self.assertRaises(FiscalConfigError):
                resolve_credentials(other, signing=True)

    def signed_document(self):
        with override_settings(**MATCHING):
            response = self.issue(self.order())
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        return FiscalDocument.objects.get(pk=response.data['id'])

    def test_a_document_is_not_sent_once_the_credentials_change_hands(self):
        document = self.signed_document()
        with override_settings(**FOREIGN):
            response = self.client.post(f'/api/admin/fiscal-documents/{document.pk}/submit/')
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE, response.data)
        self.assertFalse(FiscalSubmissionAttempt.objects.filter(document=document).exists())
        # The refusal of the provider is audited too, without the other RUC.
        entry = AdminAuditLog.objects.get(company=self.company, action='fiscal_submit_refused',
                                          target_id=document.pk)
        self.assertNotIn(OTHER_RUC, str(entry.metadata))

    def test_an_existing_document_is_compared_with_its_own_issuer_ruc(self):
        document = self.signed_document()
        # The company's RUC changes afterwards; the document keeps the one it was issued with,
        # and the credentials of that RUC may still send it. Others may not.
        type(self.company).objects.filter(pk=self.company.pk).update(tax_id=OTHER_RUC)
        with override_settings(**MATCHING), mock.patch.object(
                SunatSoapProvider, 'submit_invoice', return_value=ProviderResult(
                    outcome=ProviderOutcome.TRANSPORT_ERROR, safe_message='x')) as sent:
            response = self.client.post(f'/api/admin/fiscal-documents/{document.pk}/submit/')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        sent.assert_called_once()
        with override_settings(**FOREIGN):
            response = self.client.post(f'/api/admin/fiscal-documents/{document.pk}/submit/')
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE, response.data)

    def test_a_reconciliation_with_another_rucs_credentials_is_refused_and_audited(self):
        document = self.signed_document()
        with override_settings(**FOREIGN, FISCAL_CONSULT_ENABLED=True,
                               FISCAL_CONSULT_ENDPOINT='https://consulta.example.test/billConsultService'), \
                mock.patch('store.fiscal.provider.SunatConsultProvider.get_document_cdr') as network:
            response = self.client.post(f'/api/admin/fiscal-documents/{document.pk}/reconcile/')
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE, response.data)
        network.assert_not_called()
        entry = AdminAuditLog.objects.get(company=self.company, action='fiscal_reconcile_refused',
                                          target_id=document.pk)
        self.assertNotIn(OTHER_RUC, str(entry.metadata))

    def test_no_company_means_no_credentials(self):
        with override_settings(**MATCHING):
            for signing in (True, False):
                with self.subTest(signing=signing), self.assertRaises(FiscalConfigError):
                    resolve_credentials(None, signing=signing)

    def test_the_cash_desk_is_not_ready_with_another_rucs_credentials(self):
        from store.pos_services import _issuing_ready

        with override_settings(**MATCHING):
            self.assertTrue(_issuing_ready(self.company))
        with override_settings(**FOREIGN):
            self.assertFalse(_issuing_ready(self.company))


class ACertificateOutOfForceNeverSignsTest(_Base):
    def test_an_expired_certificate_reserves_nothing(self):
        with override_settings(**EXPIRED):
            response = self.issue(self.order())
        self.assert_refused(response, 'venció')

    def test_a_certificate_not_yet_valid_reserves_nothing(self):
        with override_settings(**NOT_YET):
            response = self.issue(self.order())
        self.assert_refused(response, 'todavía no es válido')

    def test_the_expiry_date_is_the_servers_date(self):
        from django.utils import timezone as dj_timezone

        from store.fiscal.certificate import certificate_metadata_from_pem

        meta = certificate_metadata_from_pem(EXPIRED['FISCAL_CERT_PEM'].encode())
        with override_settings(**EXPIRED):
            response = self.issue(self.order())
        self.assertIn(f'{dj_timezone.localtime(meta.not_after):%d/%m/%Y}', response.data['detail'])

    def test_what_was_signed_in_force_can_still_be_sent_after_expiry(self):
        with override_settings(**MATCHING):
            response = self.issue(self.order())
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        document = FiscalDocument.objects.get(pk=response.data['id'])
        with override_settings(**EXPIRED), mock.patch.object(
                SunatSoapProvider, 'submit_invoice', return_value=ProviderResult(
                    outcome=ProviderOutcome.TRANSPORT_ERROR, safe_message='x')) as sent:
            response = self.client.post(f'/api/admin/fiscal-documents/{document.pk}/submit/')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        sent.assert_called_once()

    def test_sending_polling_and_consulting_do_not_ask_for_a_certificate_in_force(self):
        from store.fiscal_config import resolve_provider

        with override_settings(**EXPIRED):
            self.assertEqual(resolve_credentials(self.company, signing=False)['ruc'], RUC)
            resolve_provider(self.company)              # RC/RA submit and poll, invoice submit
            with self.assertRaises(FiscalConfigError):
                resolve_credentials(self.company, signing=True)


class TheProductionCertificateNamesTheIssuerTest(SimpleTestCase):
    """
    The production rules, tested directly. Certificates come from a test CA unless the
    test is about a self-signed one: production refuses those too (C3).
    """

    class Company:
        tax_id = RUC

    def problems(self, key_cert, environment=FiscalEnvironment.PRODUCTION, ruc=RUC):
        _key, cert = key_cert
        return issuer_problems(self.Company(), {'ruc': ruc, 'cert_pem': cert}, environment=environment)

    def test_production_refuses_a_self_signed_certificate(self):
        problems = self.problems(_certificate(ou=RUC, cn='EMPRESA GATE SAC'))
        self.assertEqual(len(problems), 1)
        self.assertIn('autofirmado', problems[0])

    def test_renaming_the_issuer_does_not_hide_a_self_signed_certificate(self):
        """Its signature verifies with its own key: still self-signed, whatever the issuer is called."""
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = dt.datetime.now(dt.timezone.utc)
        cert = (x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, RUC)]))
                .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'CA QUE NO EXISTE')]))
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=365))
                .sign(key, hashes.SHA256()))
        problems = self.problems((None, cert.public_bytes(serialization.Encoding.PEM)))
        self.assertTrue(any('autofirmado' in p for p in problems), problems)

    def test_beta_still_signs_with_a_self_signed_certificate(self):
        self.assertEqual(self.problems(_certificate(cn=RUC), FiscalEnvironment.BETA), [])

    def test_production_needs_the_ruc_in_the_subject(self):
        problems = self.problems(_certificate(cn='PRUEBA SIN RUC', ca=True))
        self.assertEqual(len(problems), 1)
        self.assertIn('sujeto del certificado', problems[0])

    def test_the_ruc_in_ou_satisfies_production(self):
        self.assertEqual(self.problems(_certificate(ou=RUC, cn='EMPRESA GATE SAC', ca=True)), [])

    def test_a_ruc_in_ou_with_its_label_satisfies_production(self):
        self.assertEqual(self.problems(_certificate(ou=f'RUC {RUC}', cn='EMPRESA GATE SAC', ca=True)), [])

    def test_another_companys_certificate_does_not_satisfy_production(self):
        problems = self.problems(_certificate(ou=OTHER_RUC, cn='EMPRESA B SAC'))
        self.assertTrue(any('campo OU' in p for p in problems))

    def test_a_ruc_inside_a_longer_number_does_not_count(self):
        for ou in (f'1{RUC}', f'{RUC}9'):
            with self.subTest(ou=ou):
                problems = self.problems(_certificate(ou=ou, cn='EMPRESA GATE SAC'))
                self.assertTrue(any('sujeto del certificado' in p for p in problems))

    def test_sending_in_production_does_not_reread_the_certificate(self):
        _key, cert = _certificate(cn='SIN RUC', days_before=400, days_after=-1)
        self.assertEqual(issuer_problems(self.Company(), {'ruc': RUC, 'cert_pem': cert},
                                         environment=FiscalEnvironment.PRODUCTION, signing=False), [])

    def test_beta_does_not_ask_for_the_ruc_in_the_subject(self):
        self.assertEqual(self.problems(_certificate(cn='PRUEBA SIN RUC'), FiscalEnvironment.BETA), [])

    def test_every_problem_is_reported_at_once(self):
        problems = self.problems(_certificate(cn='X', days_before=400, days_after=-1, ca=True), ruc=OTHER_RUC)
        self.assertEqual(len(problems), 3)
