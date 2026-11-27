"""
SUNAT-PROD-C3 — issuing against SUNAT PRODUCTION, gated and off by default
(`docs/sunat-estado-2026-11.md`, §6, row C3).

What is pinned here:

- by default everything stays BETA: no switch, no console mode, no environment variable
  opens production;
- a MASTER chooses production in the console, typing `PRODUCCION <RUC>`; it is refused,
  with a sentence naming the GO, while `FISCAL_PRODUCTION_ENABLED` is off, and refused
  while `fiscal_preflight --target production` is NO-GO for the company with that RUC;
- with everything GO, PRODUCTION is what decides the endpoint (a constant of the code),
  the series (rows of their own, never BETA's), the CDR binding and the environment
  recorded on the document and on its attempt;
- intended production never falls back to BETA: switched off later, it fails closed;
- a BETA document never leaves for production, nor the other way round;
- another company never issues with these credentials, in either environment;
- every mode change and every refused attempt is in the audit trail.

NO NETWORK: every test runs with `socket.connect` refused, and the one that sends replaces
`requests.post` with a canned SOAP answer. No real credential or certificate is used: the
certificates come from a throwaway CA generated at test time.
"""
import base64
import datetime
import io
import socket
import zipfile
from decimal import Decimal
from unittest import mock

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from store import fiscal_config
from store.fiscal.provider import BETA_ENDPOINT, PRODUCTION_CONSULT_ENDPOINT, PRODUCTION_ENDPOINT
from store.fiscal.testing import ca_issued_pem, self_signed_pem
from store.fiscal_services import FiscalError, submit_fiscal_document
from store.models import (
    AdminAuditLog, FiscalDocument, FiscalDocumentStatus, FiscalDocumentType, FiscalEnvironment, FiscalSeries,
    FiscalSubmissionAttempt, IntegrationConfig, Order, OrderItem,
)
from store.tests import _build_cdr_xml, _c1_product, _c1_stock, _p2d_member, _p3_company

URL = '/api/admin/integrations/sunat/'
RUC = '20100066603'
OTHER_RUC = '20999999991'
SOL_PASSWORD = 'clave-sol-de-prueba-NoEsReal'
P12_PASSWORD = 'clave-p12-de-prueba-NoEsReal'
WORD = f'PRODUCCION {RUC}'

NO_ENV = dict(
    FISCAL_ENABLED=False, FISCAL_ENVIRONMENT='beta', FISCAL_SOL_RUC='', FISCAL_SOL_USER='', FISCAL_SOL_PASSWORD='',
    FISCAL_CERT_PEM='', FISCAL_KEY_PEM='', FISCAL_CERT_P12_PATH='', FISCAL_CERT_P12_PASSWORD='',
    FISCAL_CONSULT_ENDPOINT='', APP_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode(),
)
#: Everything the installation needs for production, but the console mode.
READY = dict(FISCAL_PRODUCTION_ENABLED=True, FISCAL_CONSULT_ENABLED=True)


def _p12(key_cert) -> str:
    key_pem, cert_pem = key_cert
    from cryptography import x509

    key = serialization.load_pem_private_key(key_pem, password=None)
    cert = x509.load_pem_x509_certificate(cert_pem)
    data = pkcs12.serialize_key_and_certificates(
        b'cdt', key, cert, None, serialization.BestAvailableEncryption(P12_PASSWORD.encode()))
    return base64.b64encode(data).decode()


CA_ISSUED = ca_issued_pem(RUC)
SELF_SIGNED = self_signed_pem(RUC)


def _secrets(key_cert=CA_ISSUED):
    return {'sol_password': SOL_PASSWORD, 'certificate_p12': _p12(key_cert), 'certificate_password': P12_PASSWORD}


def _send_bill_answer(reference_id: str, receiver_ruc: str = RUC) -> bytes:
    """A `sendBillResponse` whose CDR answers for `reference_id`, as SUNAT's would."""
    cdr = _build_cdr_xml(reference_id=reference_id, receiver_ruc=receiver_ruc, sign=False)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr(f'R-{RUC}-01-{reference_id}.xml', cdr)
    content = base64.b64encode(buffer.getvalue()).decode()
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
        '<soapenv:Body><br:sendBillResponse xmlns:br="http://service.sunat.gob.pe">'
        f'<applicationResponse>{content}</applicationResponse>'
        '</br:sendBillResponse></soapenv:Body></soapenv:Envelope>'
    ).encode()


class _Raw:
    def __init__(self, body: bytes):
        self._body = io.BytesIO(body)

    def read(self, size=-1, decode_content=False):
        return self._body.read(size)


class _Http:
    """What `requests.post` returns: a status and a body read once, as a context manager."""

    def __init__(self, body: bytes):
        self.status_code = 200
        self.raw = _Raw(body)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@override_settings(**NO_ENV)
class _Base(TestCase):
    def setUp(self):
        cache.clear()
        # No test here may open a connection. A real send would fail loudly, not silently.
        guard = mock.patch.object(socket.socket, 'connect', side_effect=AssertionError('red prohibida'))
        guard.start()
        self.addCleanup(guard.stop)

        self.company = _p3_company('emisor-prod', 'Empresa Producción', tax_id=RUC, legal_name='EMPRESA PROD SAC')
        self.branch = self.company.default_inventory_branch
        self.beta_series = FiscalSeries.objects.create(
            company=self.company, document_type=FiscalDocumentType.INVOICE, series='F001', next_number=500,
            environment=FiscalEnvironment.BETA)
        self.product = _c1_product(self.company, 'Articulo Prod', '118.00')
        _c1_stock(self.branch, self.product, 10)

        self.master = get_user_model().objects.create_superuser('master', 'master@example.com', 'x')
        self.console = APIClient()
        self.console.force_authenticate(self.master)
        self.issuer, _ = _p2d_member(self.company, 'prod_emisor',
                                     ['company.view', 'sales.fiscal.view', 'sales.fiscal.issue'])
        self.cashier = APIClient()
        self.cashier.force_authenticate(user=self.issuer)

    # -- the console ---------------------------------------------------------------

    def detail(self):
        return self.console.get(URL).json()

    def save(self, environment, key_cert=CA_ISSUED, ruc=RUC):
        current = self.detail()['draft']
        body = {'public': {'environment': environment, 'ruc': ruc, 'sol_user': 'FACTURADOR'},
                'secrets': _secrets(key_cert)}
        if current:
            body['version'] = current['version']
        response = self.console.put(URL + 'draft/', body, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        return response

    def run_test(self):
        return self.console.post(URL + 'test/', {}, format='json').json()

    def activate(self, confirm):
        version = self.detail()['draft']['version']
        return self.console.post(URL + 'activate/', {'version': version, 'confirm': confirm}, format='json')

    def force_validated(self):
        """The draft as if its test had passed: what is under test is the activation guard itself."""
        IntegrationConfig.objects.filter(provider='sunat', slot=IntegrationConfig.SLOT_DRAFT).update(validated=True)

    def go_live(self, environment, key_cert=CA_ISSUED):
        self.save(environment, key_cert)
        outcome = self.run_test()
        self.assertEqual(outcome['status'], 'ok', outcome)
        word = self.detail()['draft']['activation_confirmation']['word']
        response = self.activate(word)
        self.assertEqual(response.status_code, 200, response.content)
        return word

    def production_series(self, **extra):
        return FiscalSeries.objects.create(
            company=self.company, document_type=FiscalDocumentType.INVOICE, series='F001',
            environment=FiscalEnvironment.PRODUCTION, **extra)

    # -- a sale ----------------------------------------------------------------------

    def order(self, company=None, branch=None, product=None):
        company = company or self.company
        order = Order.objects.create(
            company=company, customer_name='CLIENTE SAC', document_type=Order.DocumentType.RUC,
            document_number='20000000001', receipt_type=Order.ReceiptType.FACTURA, total=Decimal('118.00'),
            discount_amount=Decimal('0.00'), subtotal_amount=Decimal('118.00'), taxable_amount=Decimal('100.00'),
            tax_amount=Decimal('18.00'), tax_rate=Decimal('0.18'), tax_treatment='taxed', currency='PEN',
            status=Order.Status.PAID, paid=True, paid_at=timezone.now(), fulfillment_branch=branch or self.branch)
        OrderItem.objects.create(order=order, product=product or self.product, quantity=1, price=Decimal('118.00'))
        return order

    def refused_audits(self):
        return list(AdminAuditLog.objects.filter(action='integration_activation_refused').order_by('pk'))


class ByDefaultEverythingStaysBetaTest(_Base):
    def test_the_installation_switch_is_off_by_default(self):
        from backend import settings as project_settings

        self.assertFalse(project_settings.FISCAL_PRODUCTION_ENABLED)
        self.assertFalse(getattr(settings, 'FISCAL_PRODUCTION_ENABLED', False))

    def test_nothing_configured_is_beta(self):
        self.assertEqual(fiscal_config.resolve_environment(self.company), FiscalEnvironment.BETA)
        self.assertEqual(fiscal_config.resolve_environment(), FiscalEnvironment.BETA)

    @override_settings(**READY)
    def test_a_console_activated_without_choosing_is_beta_even_with_the_switch_on(self):
        self.go_live('beta', SELF_SIGNED)
        self.assertEqual(fiscal_config.resolve_environment(self.company), FiscalEnvironment.BETA)
        provider = fiscal_config.resolve_provider(self.company)
        self.assertEqual((provider._endpoint, provider.environment), (BETA_ENDPOINT, FiscalEnvironment.BETA))
        self.assertEqual(fiscal_config.resolve_series(self.company), self.beta_series)

    @override_settings(**READY, FISCAL_ENVIRONMENT='production')
    def test_an_environment_variable_never_opens_production(self):
        with self.assertRaises(fiscal_config.FiscalConfigError) as refused:
            fiscal_config.resolve_environment(self.company)
        self.assertIn('Integraciones', str(refused.exception))

    def test_the_production_endpoints_are_https_constants_of_the_code(self):
        self.assertEqual(PRODUCTION_ENDPOINT, 'https://e-factura.sunat.gob.pe/ol-ti-itcpfegem/billService')
        self.assertEqual(PRODUCTION_CONSULT_ENDPOINT,
                         'https://e-factura.sunat.gob.pe/ol-it-wsconscpegem/billConsultService')

    def test_the_console_offers_an_environment_and_never_an_address(self):
        response = self.console.put(URL + 'draft/', {
            'public': {'environment': 'production', 'ruc': RUC, 'sol_user': 'X',
                       'endpoint': 'https://atacante.example/billService'},
            'secrets': _secrets()}, format='json')
        self.assertEqual(response.status_code, 400)
        response = self.console.put(URL + 'draft/', {
            'public': {'environment': 'https://atacante.example', 'ruc': RUC, 'sol_user': 'X'},
            'secrets': _secrets()}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('environment', response.json()['errors'])


class WithTheSwitchOffProductionIsRefusedTest(_Base):
    def setUp(self):
        super().setUp()
        self.production_series()

    @override_settings(FISCAL_CONSULT_ENABLED=True)
    def test_the_test_button_names_the_go(self):
        self.save('production')
        outcome = self.run_test()
        self.assertEqual(outcome['status'], 'unavailable')
        self.assertIn('GO activación SUNAT producción', outcome['message'])
        self.assertIn('FISCAL_PRODUCTION_ENABLED', outcome['message'])

    @override_settings(FISCAL_CONSULT_ENABLED=True)
    def test_activating_is_refused_whatever_is_typed_and_the_refusal_is_audited(self):
        self.save('production')
        self.force_validated()
        response = self.activate(WORD)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('GO activación SUNAT producción', response.json()['errors']['environment'])
        self.assertFalse(IntegrationConfig.objects.filter(provider='sunat', slot=IntegrationConfig.SLOT_ACTIVE)
                         .exists())
        self.assertEqual(fiscal_config.resolve_environment(self.company), FiscalEnvironment.BETA)
        [refusal] = self.refused_audits()
        self.assertEqual((refusal.actor, refusal.metadata['mode'], refusal.metadata['change']),
                         (self.master, 'production', 'activate'))
        self.assertIn('FISCAL_PRODUCTION_ENABLED', refusal.metadata['detail'])
        self.assertNotIn(SOL_PASSWORD, str(refusal.metadata))

    def test_production_chosen_then_switched_off_fails_closed_and_never_falls_back_to_beta(self):
        with override_settings(**READY):
            self.go_live('production')
            self.assertEqual(fiscal_config.resolve_environment(self.company), FiscalEnvironment.PRODUCTION)
        # The operator switches the installation off again.
        with self.assertRaises(fiscal_config.FiscalConfigError) as refused:
            fiscal_config.resolve_environment(self.company)
        self.assertIn('FISCAL_PRODUCTION_ENABLED', str(refused.exception))
        with self.assertRaises(fiscal_config.FiscalConfigError):
            fiscal_config.resolve_series(self.company)
        with self.assertRaises(fiscal_config.FiscalConfigError):
            fiscal_config.resolve_provider(self.company)
        response = self.cashier.post(f'/api/admin/orders/{self.order().pk}/fiscal-document/')
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE, response.data)
        self.beta_series.refresh_from_db()
        self.assertEqual(self.beta_series.next_number, 500, 'se gastó un número de BETA')
        self.assertFalse(FiscalDocument.objects.exists())

    def test_switching_back_on_a_production_row_asks_the_gate_again(self):
        with override_settings(**READY):
            self.go_live('production')
            self.console.post(URL + 'disable/', {}, format='json')
        response = self.console.post(URL + 'enable/', {'confirm': WORD}, format='json')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('FISCAL_PRODUCTION_ENABLED', response.json()['errors']['environment'])
        self.assertEqual(self.refused_audits()[-1].metadata['change'], 'enable')


@override_settings(**READY)
class WithTheSwitchOnButPreflightNoGoTest(_Base):
    def assert_refused(self, fragment):
        outcome = self.run_test()
        self.assertEqual(outcome['status'], 'unavailable', outcome)
        self.assertIn(fragment, outcome['message'])
        self.force_validated()
        response = self.activate(WORD)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('fiscal_preflight', response.json()['errors']['environment'])
        self.assertIn(fragment, response.json()['errors']['environment'])
        self.assertEqual(fiscal_config.resolve_environment(self.company), FiscalEnvironment.BETA)
        self.assertEqual(self.refused_audits()[-1].metadata['mode'], 'production')

    def test_without_a_production_series(self):
        self.save('production')
        self.assert_refused('sin serie de factura')

    def test_with_a_self_signed_certificate(self):
        self.production_series()
        self.save('production', SELF_SIGNED)
        self.assert_refused('autofirmado')

    def test_with_a_certificate_without_the_ruc(self):
        self.production_series()
        self.save('production', ca_issued_pem(RUC, ou='SIN RUC'))
        self.assert_refused('campo OU')

    @override_settings(FISCAL_CONSULT_ENABLED=False)
    def test_without_the_cdr_consult(self):
        self.production_series()
        self.save('production')
        self.assert_refused('consulta')

    def test_with_two_companies_holding_the_ruc_nobody_is_chosen(self):
        self.production_series()
        _p3_company('gemela', 'Empresa Gemela', tax_id=RUC, legal_name='EMPRESA GEMELA SAC')
        self.save('production')
        self.force_validated()
        response = self.activate(WORD)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('más de una empresa', response.json()['errors']['environment'])

    def test_the_draft_is_judged_not_what_runs(self):
        """A good certificate running in BETA does not make a bad production draft pass."""
        self.production_series()
        self.go_live('beta')
        self.save('production', SELF_SIGNED)
        self.assert_refused('autofirmado')


@override_settings(**READY)
class WithEverythingGoProductionIsUsedTest(_Base):
    def setUp(self):
        super().setUp()
        self.series = self.production_series()

    def test_the_confirmation_is_the_word_and_the_ruc(self):
        self.save('production')
        self.assertEqual(self.run_test()['status'], 'ok')
        confirmation = self.detail()['draft']['activation_confirmation']
        self.assertEqual(confirmation['word'], WORD)
        self.assertIn('REALES', confirmation['message'])
        for typed in ('EMITIR', 'PRODUCCION', WORD.lower(), f'PRODUCCION {OTHER_RUC}'):
            with self.subTest(typed=typed):
                response = self.activate(typed)
                self.assertEqual(response.status_code, 400)
                self.assertIn('confirm', response.json()['errors'])
        self.assertEqual(self.activate(WORD).status_code, 200)
        self.assertEqual(fiscal_config.resolve_environment(self.company), FiscalEnvironment.PRODUCTION)

    def test_every_mode_change_is_audited(self):
        self.go_live('beta')
        self.go_live('production')
        change = AdminAuditLog.objects.get(action='integration_mode_changed')
        self.assertEqual((change.actor, change.metadata['previous'], change.metadata['mode']),
                         (self.master, 'test', 'production'))
        self.assertEqual(AdminAuditLog.objects.filter(action='integration_activated').latest('pk').metadata['mode'],
                         'production')

    def test_issuing_uses_production_for_the_series_the_endpoint_and_the_cdr(self):
        self.go_live('production')
        response = self.cashier.post(f'/api/admin/orders/{self.order().pk}/fiscal-document/')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        document = FiscalDocument.objects.get(pk=response.data['id'])
        self.assertEqual((document.environment, document.series_ref_id, document.document_id),
                         (FiscalEnvironment.PRODUCTION, self.series.pk, 'F001-1'))
        self.beta_series.refresh_from_db()
        self.assertEqual(self.beta_series.next_number, 500, 'la serie BETA no se toca en producción')

        with mock.patch('requests.post', return_value=_Http(_send_bill_answer('F001-1'))) as post:
            response = self.cashier.post(f'/api/admin/fiscal-documents/{document.pk}/submit/')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], PRODUCTION_ENDPOINT)
        document.refresh_from_db()
        self.assertEqual(document.status, FiscalDocumentStatus.ACCEPTED)
        attempt = FiscalSubmissionAttempt.objects.get(document=document)
        self.assertEqual(attempt.environment, FiscalEnvironment.PRODUCTION)

    def test_a_production_cdr_for_another_document_is_not_applied(self):
        self.go_live('production')
        response = self.cashier.post(f'/api/admin/orders/{self.order().pk}/fiscal-document/')
        document = FiscalDocument.objects.get(pk=response.data['id'])
        with mock.patch('requests.post', return_value=_Http(_send_bill_answer('F001-9'))):
            self.cashier.post(f'/api/admin/fiscal-documents/{document.pk}/submit/')
        document.refresh_from_db()
        self.assertNotIn(document.status, (FiscalDocumentStatus.ACCEPTED,
                                           FiscalDocumentStatus.ACCEPTED_WITH_OBSERVATION))

    def test_a_beta_document_never_leaves_for_production(self):
        beta = FiscalDocument.objects.create(
            order=self.order(), company=self.company, series_ref=self.beta_series,
            document_type=FiscalDocumentType.INVOICE, series='F001', number=499, issued_at=timezone.now(),
            environment=FiscalEnvironment.BETA, issuer_tax_id=RUC, issuer_legal_name='EMPRESA PROD SAC',
            customer_doc_type='6', customer_doc_number='20000000001', customer_legal_name='CLIENTE SAC',
            currency='PEN', taxable_amount=Decimal('100.00'), tax_amount=Decimal('18.00'),
            total=Decimal('118.00'), tax_rate=Decimal('0.18'), status=FiscalDocumentStatus.SIGNED,
            signed_xml='<Invoice/>')
        self.go_live('production')
        with self.assertRaises(fiscal_config.FiscalConfigError):
            fiscal_config.resolve_provider(self.company, environment=FiscalEnvironment.BETA)
        with mock.patch('requests.post') as post:
            response = self.cashier.post(f'/api/admin/fiscal-documents/{beta.pk}/submit/')
            self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE, response.data)
            # Even a provider built by hand is refused by the sending code.
            with self.assertRaises(FiscalError):
                submit_fiscal_document(beta, fiscal_config.resolve_provider(self.company))
        post.assert_not_called()
        self.assertFalse(FiscalSubmissionAttempt.objects.filter(document=beta).exists())

    def test_there_is_no_way_back_to_beta_once_production_issued(self):
        self.go_live('production')
        self.cashier.post(f'/api/admin/orders/{self.order().pk}/fiscal-document/')
        self.save('beta')
        self.assertEqual(self.run_test()['status'], 'ok')
        response = self.activate('EMITIR')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('desactiva la integración', response.json()['errors']['environment'])
        self.assertEqual(fiscal_config.resolve_environment(self.company), FiscalEnvironment.PRODUCTION)

    def test_renewing_the_certificate_in_production_does_not_wait_on_the_queue(self):
        self.go_live('production')
        # An invoice left unsent for five days: the full preflight says NO-GO for it…
        document = FiscalDocument.objects.get(pk=self.cashier.post(
            f'/api/admin/orders/{self.order().pk}/fiscal-document/').data['id'])
        FiscalDocument.objects.filter(pk=document.pk).update(issued_at=timezone.now() - datetime.timedelta(days=5))
        # …but a new certificate is what lets the next ones be signed: it is not held back.
        self.go_live('production', ca_issued_pem(RUC))
        self.assertEqual(fiscal_config.resolve_environment(self.company), FiscalEnvironment.PRODUCTION)


@override_settings(**READY)
class AnotherCompanyIsNeverTheProductionIssuerTest(_Base):
    def setUp(self):
        super().setUp()
        self.production_series()
        self.other = _p3_company('otra-prod', 'Otra Empresa', tax_id=OTHER_RUC, legal_name='OTRA SAC')
        self.other_beta = FiscalSeries.objects.create(
            company=self.other, document_type=FiscalDocumentType.INVOICE, series='F001', next_number=7)
        FiscalSeries.objects.create(company=self.other, document_type=FiscalDocumentType.INVOICE, series='F001',
                                    environment=FiscalEnvironment.PRODUCTION)
        self.go_live('production')

    def test_the_other_company_issues_in_neither_environment(self):
        self.assertEqual(fiscal_config.resolve_environment(self.company), FiscalEnvironment.PRODUCTION)
        for resolve in (fiscal_config.resolve_environment, fiscal_config.resolve_series,
                        fiscal_config.resolve_provider):
            with self.subTest(resolve=resolve.__name__), self.assertRaises(fiscal_config.FiscalConfigError) as no:
                resolve(self.other)
            self.assertNotIn(RUC, str(no.exception))
        self.other_beta.refresh_from_db()
        self.assertEqual(self.other_beta.next_number, 7)

    def test_its_preflight_for_production_is_no_go(self):
        from store import fiscal_preflight

        checks = fiscal_preflight.run(self.other, target=FiscalEnvironment.PRODUCTION)
        self.assertEqual(fiscal_preflight.verdict(checks), fiscal_preflight.NO_GO)
        self.assertTrue(any(c.area == 'credenciales' and c.level == fiscal_preflight.NO_GO for c in checks))
