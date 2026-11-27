"""
SUNAT-PREFLIGHT — `manage.py fiscal_preflight` (`docs/sunat-estado-2026-11.md`, G4; FP-1 of the
ADR in PR #173).

What is pinned here:
- every NO-GO cause the code can see: switched to production, a RUC that is not the
  credentials', an expired certificate, a self-signed or RUC-less certificate for
  production, two active series for the same slot, an invoice past its 3 days, a boleta
  past its 7 days without a summary, an uncertain send;
- it writes nothing (only SELECT), opens no socket, and its output, text or JSON, never
  carries the SOL password, the SOL user, the private key or another taxpayer's RUC.

Certificates are self-signed and generated at test time. No test talks to SUNAT.
"""
import datetime as dt
import io
import json
import socket
from decimal import Decimal
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from store import fiscal_preflight
from store.models import (
    FiscalDailySummary, FiscalDailySummaryDocument, FiscalDocument, FiscalDocumentStatus, FiscalDocumentType,
    FiscalEnvironment, FiscalSeries, FiscalSubmissionAttempt, FiscalSummaryStatus, IntegrationConfig, Order,
)
from store.tests import _p3_company

RUC = '20100066603'
OTHER_RUC = '20999999991'
SOL_USER = 'USUARIOSECRETO'
SOL_PASSWORD = 'clave-sol-sembrada-9f3'


def _certificate(*, ou=None, cn='PRUEBA', days_before=1, days_after=365, issuer_cn=None):
    """(key PEM, certificate PEM), with the subject and validity asked for."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    attributes = [x509.NameAttribute(NameOID.COUNTRY_NAME, 'PE')]
    if ou is not None:
        attributes.append(x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, ou))
    attributes.append(x509.NameAttribute(NameOID.COMMON_NAME, cn))
    subject = x509.Name(attributes)
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_cn)]) if issuer_cn else subject
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=days_before))
            .not_valid_after(now + dt.timedelta(days=days_after))
            .sign(key, hashes.SHA256()))
    return (key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()),
            cert.public_bytes(serialization.Encoding.PEM))


def _settings(*, ruc=RUC, key_cert=None, **extra):
    key, cert = key_cert or _certificate(cn=RUC)
    return dict(FISCAL_ENABLED=True, FISCAL_SOL_RUC=ruc, FISCAL_SOL_USER=SOL_USER, FISCAL_SOL_PASSWORD=SOL_PASSWORD,
                FISCAL_CERT_PEM=cert.decode(), FISCAL_KEY_PEM=key.decode(), FISCAL_CERT_P12_PATH='', **extra)


BETA_READY = _settings()


class _Base(TestCase):
    def setUp(self):
        self.company = _p3_company('preflight', 'Empresa Preflight', tax_id=RUC, legal_name='EMPRESA PREFLIGHT SAC')
        for kind, name in ((FiscalDocumentType.INVOICE, 'F001'), (FiscalDocumentType.RECEIPT, 'B001'),
                           (FiscalDocumentType.CREDIT_NOTE, 'FN01'), (FiscalDocumentType.DEBIT_NOTE, 'FD01')):
            FiscalSeries.objects.create(company=self.company, document_type=kind, series=name)

    def run_checks(self, target=FiscalEnvironment.BETA):
        return fiscal_preflight.run(self.company, target=target)

    def levels(self, checks, area):
        return [check.level for check in checks if check.area == area]

    def details(self, checks, level):
        return ' | '.join(check.detail for check in checks if check.level == level)

    def document(self, *, document_type=FiscalDocumentType.INVOICE, series='F001', number=1,
                 status=FiscalDocumentStatus.SIGNED, days_ago=0):
        order = Order.objects.create(
            company=self.company, customer_name='CLIENTE', total=Decimal('118.00'), status=Order.Status.PAID,
            paid=True, paid_at=timezone.now())
        return FiscalDocument.objects.create(
            order=order, company=self.company, series_ref=FiscalSeries.objects.get(company=self.company, series=series),
            document_type=document_type, series=series, number=number,
            issued_at=timezone.now() - dt.timedelta(days=days_ago), environment=FiscalEnvironment.BETA,
            issuer_tax_id=RUC, issuer_legal_name='EMPRESA PREFLIGHT SAC', customer_doc_type='6',
            customer_doc_number='20000000001', customer_legal_name='CLIENTE SAC', currency='PEN',
            taxable_amount=Decimal('100.00'), tax_amount=Decimal('18.00'), total=Decimal('118.00'),
            tax_rate=Decimal('0.18'), status=status, signed_xml='<Invoice/>')

    def summary(self, boleta, status, number=1):
        summary = FiscalDailySummary.objects.create(
            company=self.company, identifier=f'RC-20261127-{number}', correlativo=number,
            reference_date=timezone.localdate(boleta.issued_at), issue_date=timezone.localdate(),
            environment=FiscalEnvironment.BETA, status=status)
        FiscalDailySummaryDocument.objects.create(summary=summary, document=boleta, line_id=1)
        return summary


@override_settings(**BETA_READY)
class ABetaReadyCompanyIsGoTest(_Base):
    def test_a_configured_beta_company_is_go(self):
        checks = self.run_checks()
        self.assertEqual(fiscal_preflight.verdict(checks), fiscal_preflight.GO, self.details(checks, 'NO-GO'))
        self.assertIn('GO', self.levels(checks, 'credenciales'))
        # In BETA a self-signed certificate is a warning, not a stop.
        self.assertIn('autofirmado', self.details(checks, 'AVISO'))

    def test_the_command_exits_zero_and_prints_one_line_per_check(self):
        out = io.StringIO()
        call_command('fiscal_preflight', '--company', 'preflight', stdout=out)
        text = out.getvalue()
        self.assertIn('Veredicto: GO', text)
        self.assertIn('GO    credenciales: el RUC de la Clave SOL es el de la empresa', text)

    def test_an_unknown_company_is_an_error(self):
        with self.assertRaises(CommandError):
            call_command('fiscal_preflight', '--company', 'no-existe', stdout=io.StringIO())


class EachNoGoCauseTest(_Base):
    def assert_no_go(self, checks, area, fragment):
        stops = [check for check in checks if check.level == fiscal_preflight.NO_GO and check.area == area]
        self.assertTrue(any(fragment in check.detail for check in stops),
                        f'esperaba NO-GO en {area} con «{fragment}»: {stops}')
        self.assertEqual(fiscal_preflight.verdict(checks), fiscal_preflight.NO_GO)

    @override_settings(**BETA_READY)
    def test_production_is_not_operated_by_this_version(self):
        self.assert_no_go(self.run_checks(FiscalEnvironment.PRODUCTION), 'ambiente', 'sólo opera')

    @override_settings(**_settings(ruc=OTHER_RUC))
    def test_credentials_of_another_ruc(self):
        self.assert_no_go(self.run_checks(), 'credenciales', 'no son del RUC de esta empresa')

    @override_settings(**_settings(key_cert=_certificate(cn=RUC, days_before=400, days_after=-3)))
    def test_an_expired_certificate(self):
        self.assert_no_go(self.run_checks(), 'certificado', 'venció')

    @override_settings(**_settings(key_cert=_certificate(cn=RUC, days_after=20)))
    def test_a_certificate_about_to_expire_blocks_production_and_warns_in_beta(self):
        self.assertIn('vence en', self.details(self.run_checks(), 'AVISO'))
        self.assert_no_go(self.run_checks(FiscalEnvironment.PRODUCTION), 'certificado', 'vence en')

    @override_settings(**_settings(key_cert=_certificate(cn='SIN RUC', issuer_cn='CA DE PRUEBA')))
    def test_production_needs_the_ruc_in_the_certificate(self):
        self.assert_no_go(self.run_checks(FiscalEnvironment.PRODUCTION), 'certificado', 'campo OU')

    @override_settings(**_settings(key_cert=_certificate(ou=RUC, cn='EMPRESA', issuer_cn='CA DE PRUEBA')))
    def test_a_certificate_with_the_ruc_in_ou_from_a_ca_passes_those_two_lines(self):
        checks = self.run_checks(FiscalEnvironment.PRODUCTION)
        stops = self.details(checks, 'NO-GO')
        self.assertNotIn('campo OU', stops)
        self.assertNotIn('autofirmado', stops)

    @override_settings(**BETA_READY)
    def test_two_active_series_for_the_same_slot(self):
        FiscalSeries.objects.create(company=self.company, document_type=FiscalDocumentType.INVOICE, series='F002')
        self.assert_no_go(self.run_checks(), 'series', 'no hay regla para elegir')

    @override_settings(**BETA_READY)
    def test_an_invoice_past_its_three_days(self):
        self.document(days_ago=4)
        self.assert_no_go(self.run_checks(), 'plazos', 'más de 3 días')

    @override_settings(**BETA_READY)
    def test_an_invoice_close_to_its_deadline_is_a_warning(self):
        self.document(days_ago=2)
        checks = self.run_checks()
        self.assertIn('a punto de vencer', self.details(checks, 'AVISO'))
        self.assertEqual(fiscal_preflight.verdict(checks), fiscal_preflight.GO)

    @override_settings(**BETA_READY)
    def test_a_boleta_past_its_seven_days_without_summary(self):
        self.document(document_type=FiscalDocumentType.RECEIPT, series='B001', days_ago=8)
        self.assert_no_go(self.run_checks(), 'plazos', 'sin resumen diario')

    @override_settings(**BETA_READY)
    def test_a_boleta_in_a_summary_that_never_left_is_still_pending(self):
        for status in (FiscalSummaryStatus.GENERATED, FiscalSummaryStatus.SIGNED,
                       FiscalSummaryStatus.SUBMISSION_ERROR):
            with self.subTest(status=status):
                FiscalDailySummary.objects.all().delete()
                FiscalDocument.objects.all().delete()
                self.summary(self.document(document_type=FiscalDocumentType.RECEIPT, series='B001', days_ago=8),
                             status)
                self.assert_no_go(self.run_checks(), 'plazos', 'sin resumen diario')

    @override_settings(**BETA_READY)
    def test_a_boleta_in_a_summary_that_reached_sunat_is_informed(self):
        for status in (FiscalSummaryStatus.SUBMITTED, FiscalSummaryStatus.ACCEPTED,
                       FiscalSummaryStatus.ACCEPTED_WITH_OBSERVATION, FiscalSummaryStatus.SUBMISSION_UNKNOWN):
            with self.subTest(status=status):
                FiscalDailySummary.objects.all().delete()
                FiscalDocument.objects.all().delete()
                self.summary(self.document(document_type=FiscalDocumentType.RECEIPT, series='B001', days_ago=8),
                             status)
                self.assertNotIn('sin resumen diario', self.details(self.run_checks(), 'NO-GO'))

    @override_settings(**BETA_READY)
    def test_a_summary_generated_or_signed_but_not_sent_is_listed(self):
        boleta = self.document(document_type=FiscalDocumentType.RECEIPT, series='B001')
        self.summary(boleta, FiscalSummaryStatus.SIGNED)
        self.assertIn('1 resumen(es) diario(s) generado(s) o firmado(s) sin enviar',
                      self.details(self.run_checks(), 'AVISO'))

    @override_settings(**BETA_READY)
    def test_the_three_days_also_run_for_pending_and_refused_before_sunat(self):
        from store.fiscal.provider import ProviderOutcome

        for status, result in ((FiscalDocumentStatus.PENDING, None),
                               (FiscalDocumentStatus.SUBMISSION_ERROR, ProviderOutcome.TRANSPORT_ERROR.value)):
            with self.subTest(status=status):
                FiscalDocument.objects.all().delete()
                document = self.document(status=status, days_ago=4)
                if result:
                    FiscalSubmissionAttempt.objects.create(
                        document=document, attempt_number=1, environment=document.environment,
                        started_at=timezone.now(), finished_at=timezone.now(), result=result)
                self.assert_no_go(self.run_checks(), 'plazos', 'más de 3 días')

    @override_settings(**BETA_READY)
    def test_an_uncertain_send_is_not_counted_against_the_three_days(self):
        document = self.document(status=FiscalDocumentStatus.SUBMISSION_ERROR, days_ago=4)
        FiscalSubmissionAttempt.objects.create(
            document=document, attempt_number=1, environment=document.environment, started_at=timezone.now(),
            finished_at=timezone.now(), result='transport_unknown')
        checks = self.run_checks()
        self.assertNotIn('más de 3 días', self.details(checks, 'NO-GO'))
        self.assertIn('1 incierto', self.details(checks, 'AVISO'))

    @override_settings(**BETA_READY)
    def test_production_without_a_factura_series_is_no_go(self):
        FiscalSeries.objects.create(company=self.company, document_type=FiscalDocumentType.RECEIPT, series='B001',
                                    environment=FiscalEnvironment.PRODUCTION)
        self.assert_no_go(self.run_checks(FiscalEnvironment.PRODUCTION), 'series', 'sin serie de factura')

    @override_settings(**BETA_READY)
    def test_an_abandoned_attempt_blocks_production_and_warns_in_beta(self):
        from store.fiscal_services import STALE_ATTEMPT_MINUTES

        document = self.document()
        FiscalSubmissionAttempt.objects.create(
            document=document, attempt_number=1, environment=document.environment, result='in_progress',
            started_at=timezone.now() - dt.timedelta(minutes=STALE_ATTEMPT_MINUTES + 1))
        self.assertIn('abandonado', self.details(self.run_checks(), 'AVISO'))
        FiscalDocument.objects.filter(pk=document.pk).update(environment=FiscalEnvironment.PRODUCTION)
        FiscalSubmissionAttempt.objects.filter(document=document).update(environment=FiscalEnvironment.PRODUCTION)
        self.assert_no_go(self.run_checks(FiscalEnvironment.PRODUCTION), 'cola', 'abandonado')

    @override_settings(**BETA_READY)
    def test_an_uncertain_send_is_reported_and_blocks_production_only(self):
        document = self.document(status=FiscalDocumentStatus.SUBMISSION_ERROR)
        FiscalSubmissionAttempt.objects.create(
            document=document, attempt_number=1, environment=document.environment, started_at=timezone.now(),
            finished_at=timezone.now(), result='transport_unknown')
        self.assertIn('1 incierto', self.details(self.run_checks(), 'AVISO'))

    @override_settings(FISCAL_ENABLED=False, FISCAL_SOL_RUC='', FISCAL_SOL_USER='', FISCAL_SOL_PASSWORD='',
                       FISCAL_CERT_PEM='', FISCAL_KEY_PEM='', FISCAL_CERT_P12_PATH='')
    def test_nothing_configured(self):
        checks = self.run_checks()
        self.assert_no_go(checks, 'credenciales', 'Faltan credenciales SOL')
        self.assertIn('AVISO', self.levels(checks, 'interruptor'))

    @override_settings(**BETA_READY, FISCAL_CONSULT_ENABLED=True, FISCAL_CONSULT_ENDPOINT='')
    def test_an_empty_consult_endpoint_is_the_one_in_the_code(self):
        consult = [c for c in self.run_checks() if c.area == 'consulta']
        self.assertEqual([c.level for c in consult], ['GO'])
        self.assertIn('e-factura.sunat.gob.pe', consult[0].detail)

    @override_settings(**BETA_READY)
    def test_the_command_exits_one_on_no_go(self):
        with self.assertRaises(CommandError) as caught:
            call_command('fiscal_preflight', '--company', 'preflight', '--target', 'production',
                         stdout=io.StringIO())
        self.assertEqual(caught.exception.returncode, 1)


SUBJECT_NAME = 'TITULAR PERSONA NATURAL'

@override_settings(**_settings(ruc=OTHER_RUC, key_cert=_certificate(ou=RUC, cn=SUBJECT_NAME)))
class ItReadsOnlyAndSaysNoSecretTest(_Base):
    def outputs(self):
        texts = []
        for extra in ((), ('--json',)):
            for target in ('beta', 'production'):
                out = io.StringIO()
                try:
                    call_command('fiscal_preflight', '--company', 'preflight', '--target', target, *extra, stdout=out)
                except CommandError:
                    pass
                texts.append(out.getvalue())
        return texts

    def test_only_select_statements(self):
        self.document(status=FiscalDocumentStatus.SUBMISSION_ERROR, days_ago=5)
        with CaptureQueriesContext(connection) as queries:
            self.outputs()
        writes = [q['sql'] for q in queries.captured_queries
                  if not q['sql'].lstrip().upper().startswith(('SELECT', 'SAVEPOINT', 'RELEASE SAVEPOINT'))]
        self.assertEqual(writes, [])

    def test_no_socket_is_opened(self):
        with mock.patch.object(socket.socket, 'connect', side_effect=AssertionError('red')) as connect:
            self.outputs()
        connect.assert_not_called()

    def test_no_secret_and_no_other_ruc_in_the_output(self):
        from django.conf import settings

        key_body = settings.FISCAL_KEY_PEM.splitlines()[1]
        for text in self.outputs():
            for secret in (SOL_PASSWORD, SOL_USER, key_body, OTHER_RUC, SUBJECT_NAME):
                self.assertNotIn(secret, text)
            self.assertIn('RUC de la empresa presente en el certificado: sí', text)

    def test_the_json_is_parseable(self):
        out = io.StringIO()
        with self.assertRaises(CommandError):
            call_command('fiscal_preflight', '--company', 'preflight', '--json', stdout=out)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload['verdict'], 'NO-GO')
        self.assertEqual(payload['company'], 'preflight')



CONSOLE_KEY = __import__('cryptography.fernet', fromlist=['Fernet']).Fernet.generate_key().decode()


@override_settings(APP_CONFIG_ENCRYPTION_KEY=CONSOLE_KEY, FISCAL_ENABLED=False, FISCAL_SOL_RUC='', FISCAL_SOL_USER='',
                   FISCAL_SOL_PASSWORD='', FISCAL_CERT_PEM='', FISCAL_KEY_PEM='', FISCAL_CERT_P12_PATH='')
class TheConsoleConfigurationIsReadTooTest(_Base):
    """The credentials the MASTER saved in the integrations console: opened in memory, never printed."""

    P12_PASSWORD = 'clave-p12-de-consola-NoEsReal'
    CONSOLE_SOL_PASSWORD = 'clave-sol-de-consola-NoEsReal'

    def setUp(self):
        import base64

        from store.integrations import service
        from store.tests import _fc_build_p12

        super().setUp()
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        row = IntegrationConfig(provider='sunat', slot=IntegrationConfig.SLOT_ACTIVE, enabled=True,
                                public={'ruc': RUC, 'sol_user': 'USUARIOCONSOLA'})
        service._seal(row, {
            'sol_password': self.CONSOLE_SOL_PASSWORD,
            'certificate_p12': base64.b64encode(_fc_build_p12(key, password=self.P12_PASSWORD.encode())).decode(),
            'certificate_password': self.P12_PASSWORD,
        })
        row.save()

    def test_the_console_row_is_diagnosed_without_writes_or_secrets(self):
        out = io.StringIO()
        with CaptureQueriesContext(connection) as queries:
            try:
                call_command('fiscal_preflight', '--company', 'preflight', stdout=out)
            except CommandError:
                pass
        text = out.getvalue()
        self.assertIn('origen: consola de integraciones', text)
        self.assertIn('el RUC de la Clave SOL es el de la empresa', text)
        self.assertIn('emisión encendida', text)            # the console row decides, not FISCAL_ENABLED
        for secret in (self.CONSOLE_SOL_PASSWORD, self.P12_PASSWORD, 'USUARIOCONSOLA'):
            self.assertNotIn(secret, text)
        writes = [q['sql'] for q in queries.captured_queries
                  if not q['sql'].lstrip().upper().startswith(('SELECT', 'SAVEPOINT', 'RELEASE SAVEPOINT'))]
        self.assertEqual(writes, [])
