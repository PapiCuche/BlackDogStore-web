"""
INTEGRATIONS-CONSOLE · SUNAT (facturación electrónica).

    a master types the SOL credentials, uploads the signing certificate, tests
    them and — with an explicit confirmation — activates them
    → the next document is signed with THAT certificate and sent with THOSE
      credentials

What this does NOT change: the only environment is SUNAT's BETA, the endpoint is
a table in the code, and one configuration serves the installation. The console
offers what the code can do and nothing else.

No call to SUNAT: these tests stop at what an emission is handed.
"""
import base64
import datetime
import json
from unittest import mock

from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from store import fiscal_config
from store.fiscal.provider import BETA_ENDPOINT
from store.integrations import registry
from store.models import AdminAuditLog, IntegrationConfig
from store.tests import _fc_build_p12

URL = '/api/admin/integrations/sunat/'
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
P12_PASSWORD = 'clave-del-p12-NoEsReal'
SOL_PASSWORD = 'clave-sol-del-panel-NoEsReal'
PUBLIC = {'ruc': '20123456789', 'sol_user': 'USUARIO1'}
NO_ENV = dict(
    FISCAL_ENABLED=False, FISCAL_ENVIRONMENT='beta', FISCAL_SOL_RUC='', FISCAL_SOL_USER='', FISCAL_SOL_PASSWORD='',
    FISCAL_CERT_PEM='', FISCAL_KEY_PEM='', FISCAL_CERT_P12_PATH='', FISCAL_CERT_P12_PASSWORD='',
)


def p12(key=KEY, password=P12_PASSWORD, **kwargs) -> str:
    return base64.b64encode(_fc_build_p12(key, password=password.encode() if password else None, **kwargs)).decode()


def secrets(**changes):
    return {'sol_password': SOL_PASSWORD, 'certificate_p12': p12(), 'certificate_password': P12_PASSWORD, **changes}


def fingerprint(cert_pem: bytes) -> str:
    return x509.load_pem_x509_certificate(cert_pem).fingerprint(hashes.SHA256()).hex()


def fingerprint_of_p12(encoded: str, password=P12_PASSWORD) -> str:
    from cryptography.hazmat.primitives.serialization import pkcs12

    _key, cert, _chain = pkcs12.load_key_and_certificates(base64.b64decode(encoded), password.encode())
    return cert.fingerprint(hashes.SHA256()).hex()


@override_settings(APP_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode(), **NO_ENV)
class _Base(TestCase):
    def setUp(self):
        self.master = get_user_model().objects.create_superuser('master', 'master@example.com', 'x')
        self.console = APIClient()
        self.console.force_authenticate(self.master)

    def detail(self):
        return self.console.get(URL).json()

    def save(self, public=PUBLIC, secret_values=None):
        current = self.detail()['draft']
        body = {'public': public, 'secrets': secrets() if secret_values is None else secret_values}
        if current:
            body['version'] = current['version']
        return self.console.put(URL + 'draft/', body, format='json')

    def run_test(self):
        return self.console.post(URL + 'test/', {}, format='json').json()

    def activate(self, **extra):
        version = self.detail()['draft']['version']
        return self.console.post(URL + 'activate/', {'version': version, **extra}, format='json')

    def go_live(self, public=PUBLIC, secret_values=None):
        response = self.save(public, secret_values)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.run_test()['status'], 'ok')
        word = self.detail()['draft']['activation_confirmation']['word']
        self.assertEqual(self.activate(confirm=word).status_code, 200)


class RuntimeTest(_Base):
    def test_with_nothing_configured_nothing_can_be_emitted(self):
        self.assertFalse(fiscal_config.fiscal_enabled())
        with self.assertRaises(fiscal_config.FiscalConfigError):
            fiscal_config.resolve_credentials(None)

    def test_an_emission_is_handed_the_credentials_and_the_certificate_of_the_console(self):
        uploaded = p12()
        self.go_live(secret_values=secrets(certificate_p12=uploaded))

        self.assertTrue(fiscal_config.fiscal_enabled())
        credentials = fiscal_config.resolve_credentials(None)
        self.assertEqual((credentials['ruc'], credentials['sol_user'], credentials['sol_password']),
                         ('20123456789', 'USUARIO1', SOL_PASSWORD))
        self.assertEqual(fingerprint(credentials['cert_pem']), fingerprint_of_p12(uploaded))
        self.assertIn(b'PRIVATE KEY', credentials['key_pem'])

        provider = fiscal_config.resolve_provider(None)
        self.assertEqual((provider._endpoint, provider._username, provider._password),
                         (BETA_ENDPOINT, '20123456789USUARIO1', SOL_PASSWORD))

    def test_the_console_wins_over_the_environment(self):
        with override_settings(FISCAL_ENABLED=True, FISCAL_SOL_RUC='20100066603', FISCAL_SOL_USER='MODDATOS',
                               FISCAL_SOL_PASSWORD='moddatos'):
            self.go_live()
            self.assertEqual(fiscal_config.resolve_credentials(None)['ruc'], '20123456789')

    def test_replacing_the_certificate_changes_the_next_emission_without_a_restart(self):
        self.go_live()
        before = fingerprint(fiscal_config.resolve_credentials(None)['cert_pem'])
        renewed = p12(OTHER_KEY)
        self.go_live(secret_values={'certificate_p12': renewed, 'certificate_password': P12_PASSWORD})
        after = fiscal_config.resolve_credentials(None)
        self.assertNotEqual(fingerprint(after['cert_pem']), before)
        self.assertEqual(fingerprint(after['cert_pem']), fingerprint_of_p12(renewed))
        self.assertEqual(after['sol_password'], SOL_PASSWORD)        # what was not replaced is kept

    def test_switching_it_off_stops_emission_and_does_not_fall_back(self):
        with override_settings(FISCAL_ENABLED=True, FISCAL_SOL_RUC='20100066603', FISCAL_SOL_USER='MODDATOS',
                               FISCAL_SOL_PASSWORD='moddatos'):
            self.go_live()
            self.console.post(URL + 'disable/', {}, format='json')
            self.assertFalse(fiscal_config.fiscal_enabled())
            with self.assertRaises(fiscal_config.FiscalConfigError):
                fiscal_config.resolve_credentials(None)

    def test_the_signing_certificate_can_be_inspected_without_its_key(self):
        self.go_live()
        metadata, validity = fiscal_config.inspect_signing_certificate()
        self.assertEqual(validity, 'valid')
        self.assertIn('CDT DE PRUEBA', metadata.subject)


class ExplicitEnablingTest(_Base):
    def test_saving_and_testing_enable_nothing(self):
        self.save()
        self.assertFalse(fiscal_config.fiscal_enabled())
        self.assertEqual(self.run_test()['status'], 'ok')
        self.assertFalse(fiscal_config.fiscal_enabled())

    def test_activating_needs_an_explicit_confirmation(self):
        self.save()
        self.run_test()
        confirmation = self.detail()['draft']['activation_confirmation']
        self.assertTrue(confirmation['word'])
        self.assertIn('SUNAT', confirmation['message'])

        for attempt in ({}, {'confirm': 'si'}, {'confirm': confirmation['word'].lower()}):
            refused = self.activate(**attempt)
            self.assertEqual(refused.status_code, 400)
            self.assertIn('confirm', refused.json()['errors'])
        self.assertFalse(fiscal_config.fiscal_enabled())

        self.assertEqual(self.activate(confirm=confirmation['word']).status_code, 200)
        self.assertTrue(fiscal_config.fiscal_enabled())

    def test_only_what_the_code_can_do_is_offered(self):
        provider = registry.get('sunat')
        self.assertEqual(provider.scope, registry.SCOPE_PLATFORM)
        self.assertEqual(list(provider.supported_modes), ['test'])
        self.assertNotIn('environment', [f.name for f in provider.fields])
        self.save()
        self.assertEqual(self.detail()['draft']['mode'], 'test')
        # And the console cannot be used to point emission somewhere else.
        response = self.console.put(URL + 'draft/', {
            'public': {**PUBLIC, 'environment': 'production', 'endpoint': 'https://e-factura.sunat.gob.pe'},
            'secrets': secrets()}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_a_production_environment_in_the_server_still_fails_closed(self):
        self.go_live()
        with override_settings(FISCAL_ENVIRONMENT='production'):
            with self.assertRaises(fiscal_config.FiscalConfigError):
                fiscal_config.resolve_provider(None)


class CertificateTest(_Base):
    def errors(self, **changes):
        response = self.save(secret_values=secrets(**changes))
        self.assertEqual(response.status_code, 400, response.content)
        return response

    def test_what_is_uploaded_has_to_be_a_certificate_with_its_key(self):
        self.assertIn('certificate_p12', self.errors(certificate_p12='esto no es base64 %%%').json()['errors'])
        garbage = base64.b64encode(b'no soy un PKCS#12').decode()
        self.assertIn('certificate_p12', self.errors(certificate_p12=garbage).json()['errors'])
        without_key = base64.b64encode(_fc_build_p12(KEY, with_key=False)).decode()
        self.assertIn('certificate_p12', self.errors(certificate_p12=without_key, certificate_password='').json()['errors'])

    def test_the_wrong_password_is_refused_without_repeating_it(self):
        response = self.errors(certificate_password='otra-clave-equivocada')
        self.assertIn('certificate_p12', response.json()['errors'])
        self.assertNotIn('otra-clave-equivocada', response.content.decode())
        self.assertNotIn(P12_PASSWORD, response.content.decode())

    def test_its_size_is_limited(self):
        huge = base64.b64encode(b'\x30' * (64 * 1024 + 1)).decode()
        self.assertIn('64 KiB', self.errors(certificate_p12=huge).json()['errors']['certificate_p12'])
        self.assertFalse(IntegrationConfig.objects.exists())
        self.assertEqual(registry.get('sunat').field('certificate_p12').describe()['max_bytes'], 64 * 1024)

    def test_the_limit_is_decided_before_decoding(self):
        """Something far larger is refused on its length alone."""
        field = registry.get('sunat').field('certificate_p12')
        with mock.patch('base64.b64decode') as decode:
            with self.assertRaises(ValueError):
                field.clean('A' * (4 * 1024 * 1024))
        decode.assert_not_called()

    def test_an_expired_certificate_does_not_pass_the_test(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        expired = p12(not_before=now - datetime.timedelta(days=400), not_after=now - datetime.timedelta(days=1))
        self.assertEqual(self.save(secret_values=secrets(certificate_p12=expired)).status_code, 200)
        result = self.run_test()
        self.assertEqual(result['status'], 'invalid')
        self.assertIn('venci', result['message'])
        self.assertEqual(self.activate(confirm=self.detail()['draft']['activation_confirmation']['word']).status_code, 409)

    def test_the_test_says_whose_certificate_it_is_and_until_when(self):
        self.save()
        result = self.run_test()
        self.assertEqual(result['status'], 'ok')
        self.assertIn('CDT DE PRUEBA', result['message'])
        self.assertIn('BETA', result['message'])

    def test_the_certificate_never_comes_back_and_nothing_of_it_is_shown(self):
        uploaded = p12()
        self.go_live(secret_values=secrets(certificate_p12=uploaded))
        text = self.console.get(URL).content.decode() + self.console.get('/api/admin/integrations/').content.decode()
        for value in (uploaded, uploaded[-4:] + '"', P12_PASSWORD, SOL_PASSWORD):
            self.assertNotIn(value, text)
        shown = self.detail()['active']['secrets']['certificate_p12']
        self.assertEqual(shown['configured'], True)
        self.assertEqual(shown.get('last_four', ''), '')
        self.assertNotIn(uploaded[:40], json.dumps(list(AdminAuditLog.objects.values_list('metadata', flat=True))))
        stored = IntegrationConfig.objects.get(slot='active')
        self.assertNotIn(uploaded[:40], stored.sealed_secrets)
        self.assertNotIn(uploaded[:40], json.dumps(stored.public))


class ValidationTest(_Base):
    def test_a_ruc_is_eleven_digits(self):
        for wrong in ('2012345678', '2012345678A', '20-12345678-9'):
            with self.subTest(wrong):
                self.assertIn('ruc', self.save({**PUBLIC, 'ruc': wrong}).json()['errors'])

    def test_incomplete_credentials_are_incomplete(self):
        self.save(secret_values={'sol_password': SOL_PASSWORD})
        self.assertEqual(self.run_test()['status'], 'incomplete')


class LegacyEnvironmentTest(_Base):
    ENV = dict(FISCAL_ENABLED=True, FISCAL_SOL_RUC='20100066603', FISCAL_SOL_USER='MODDATOS',
               FISCAL_SOL_PASSWORD='moddatos-NoEsReal', FISCAL_CERT_PEM='-----BEGIN CERTIFICATE-----\nx',
               FISCAL_KEY_PEM='-----BEGIN PRIVATE KEY-----\nx')

    def test_an_installation_configured_by_environment_keeps_emitting_and_shows_as_environment(self):
        with override_settings(**self.ENV):
            self.assertTrue(fiscal_config.fiscal_enabled())
            self.assertEqual(fiscal_config.resolve_credentials(None)['ruc'], '20100066603')
            detail = self.detail()
        self.assertEqual((detail['state'], detail['source']), ('ACTIVE', 'env'))
        self.assertEqual(detail['env']['public'], {'ruc': '20100066603', 'sol_user': 'MODDATOS'})
        self.assertIn('sol_password', detail['env']['secrets'])
        self.assertNotIn('moddatos-NoEsReal', json.dumps(detail))
        self.assertNotIn('BEGIN', json.dumps(detail))

    def test_credentials_in_the_environment_without_the_switch_are_not_a_configuration(self):
        with override_settings(**{**self.ENV, 'FISCAL_ENABLED': False}):
            self.assertEqual(self.detail()['state'], 'NOT_CONFIGURED')
            self.assertFalse(fiscal_config.fiscal_enabled())
