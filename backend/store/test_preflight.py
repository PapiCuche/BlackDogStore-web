"""
PREFLIGHT: what the owner still has to provide, said by a program instead of a person.

`deploy/preflight.py` reads `deploy/.env.production` (or the file it is given) and
answers, line by line, what is there, what is missing and what contradicts itself:
the domain, the secrets, the mail server, WHICH of Izipay's two products and whether
its keys are a consistent set, and the optional integrations.

It never prints a value. Its output is meant to be pasted into a chat or a ticket,
and the file it reads is the one place where every production secret lives.

It checks configuration. It does not replace the gateway's TEST payment or the
rehearsal, and it says so.
"""
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR).parent
PREFLIGHT = ROOT / 'deploy' / 'preflight.py'
EXAMPLE = ROOT / 'deploy' / '.env.production.example'

#: Values that must never appear in what the program prints.
SECRET_KEY = 'clave-secreta-de-prueba-NoEsReal-0123456789-abcdefghijklmnopqrstuvwxyz-ABCD'
DB_PASSWORD = 'contrasena-de-la-base-0123456789'
MAIL_PASSWORD = 'clave-del-correo-NoEsReal'
IZIPAY_API_KEY = 'api-key-NoEsReal-0123456789'
IZIPAY_HASH_KEY = 'hash-key-NoEsReal-0123456789'
MCW_PASSWORD = 'testpassword_NoEsRealNoEsRealNoEsReal'
MCW_HMAC = 'hmac-NoEsReal-0123456789'

BASE = {
    'SITE_DOMAIN': 'tienda.example.pe',
    'SECRET_KEY': SECRET_KEY,
    'POSTGRES_PASSWORD': DB_PASSWORD,
    'EMAIL_BACKEND': 'django.core.mail.backends.smtp.EmailBackend',
    'EMAIL_HOST': 'smtp.example.pe',
    'EMAIL_PORT': '587',
    'EMAIL_USE_TLS': '1',
    'EMAIL_HOST_USER': 'tienda@example.pe',
    'EMAIL_HOST_PASSWORD': MAIL_PASSWORD,
    'DEFAULT_FROM_EMAIL': 'tienda@example.pe',
    'ORDER_NOTIFICATION_EMAIL': 'pedidos@example.pe',
    'WHATSAPP_PROVIDER': 'disabled',
    'FISCAL_ENABLED': '0',
}
IZIPAY = {
    'PAYMENT_PROVIDER': 'izipay',
    'IZIPAY_ENV': 'sandbox',
    'IZIPAY_MERCHANT_CODE': '9000001',
    'IZIPAY_PUBLIC_KEY': 'clave-publica-NoEsReal',
    'IZIPAY_API_KEY': IZIPAY_API_KEY,
    'IZIPAY_HASH_KEY': IZIPAY_HASH_KEY,
    'IZIPAY_TOKEN_URL': 'https://sandbox-api.example.invalid/token',
    'IZIPAY_IPN_URL': 'https://tienda.example.pe/api/payments/izipay/notification/',
}
MICUENTAWEB = {
    'PAYMENT_PROVIDER': 'micuentaweb',
    'MICUENTAWEB_SHOP_ID': '90000001',
    'MICUENTAWEB_PASSWORD': MCW_PASSWORD,
    'MICUENTAWEB_PUBLIC_KEY': '90000001:testpublickey_NoEsRealNoEsReal',
    'MICUENTAWEB_HMAC_KEY': MCW_HMAC,
    'MICUENTAWEB_IPN_URL': 'https://tienda.example.pe/api/payments/micuentaweb/notification/',
}
SECRETS = (SECRET_KEY, DB_PASSWORD, MAIL_PASSWORD, IZIPAY_API_KEY, IZIPAY_HASH_KEY, MCW_PASSWORD, MCW_HMAC)


@skipUnless(EXAMPLE.exists(), 'deploy/ is not part of this checkout')
class PreflightTest(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env_file = Path(self.tmp.name) / 'env.production'

    def run_preflight(self, values, *args, mode=0o600, text=None):
        self.env_file.write_text(text if text is not None else ''.join(f'{k}={v}\n' for k, v in values.items()))
        os.chmod(self.env_file, mode)
        result = subprocess.run(
            [sys.executable, str(PREFLIGHT), '--env', str(self.env_file), *args],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
            env={'PATH': os.environ['PATH'], 'LC_ALL': 'C.UTF-8', 'PYTHONIOENCODING': 'utf-8'},
        )
        for secret in SECRETS:
            self.assertNotIn(secret, result.stdout + result.stderr, 'a secret value was printed')
        return result

    def lines(self, result, kind):
        return [line for line in result.stdout.splitlines() if line.startswith(kind)]

    # -- what is missing ------------------------------------------------------

    def test_the_example_file_as_shipped_lists_everything_the_owner_owes(self):
        result = self.run_preflight({}, text=EXAMPLE.read_text(encoding='utf-8'))
        self.assertEqual(result.returncode, 1)
        owed = '\n'.join(self.lines(result, 'BLOCKED/OWNER-DATA'))
        for item in ('dominio', 'SECRET_KEY', 'POSTGRES_PASSWORD', 'correo', 'Izipay'):
            self.assertIn(item, owed)
        optional = '\n'.join(self.lines(result, 'BLOCKED/OPTIONAL'))
        for item in ('Google', 'WhatsApp', 'SUNAT'):
            self.assertIn(item, optional)
        self.assertIn('CONFIGURACIÓN: INCOMPLETA', result.stdout)

    def test_it_names_the_variables_to_fill_in(self):
        result = self.run_preflight(BASE)
        izipay = next(line for line in self.lines(result, 'BLOCKED/OWNER-DATA') if 'Izipay' in line)
        self.assertIn('PAYMENT_PROVIDER', izipay)
        self.assertIn('cuál de los dos productos', izipay)

    def test_a_complete_file_for_the_checkout_product_is_complete_but_for_the_optionals(self):
        result = self.run_preflight({**BASE, **IZIPAY})
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.lines(result, 'BLOCKED/OWNER-DATA'), [])
        self.assertEqual(self.lines(result, 'ATENCIÓN'), [])
        self.assertIn('OK    Izipay «SDK web / Checkout», entorno sandbox', result.stdout)
        self.assertIn('CONFIGURACIÓN: COMPLETA SALVO OPCIONALES', result.stdout)

    def test_a_complete_file_for_mi_cuenta_web_says_which_keys_it_holds(self):
        result = self.run_preflight({**BASE, **MICUENTAWEB})
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('OK    Izipay «Mi Cuenta Web», claves de TEST', result.stdout)

    def test_it_never_claims_the_installation_is_ready_to_deploy(self):
        result = self.run_preflight({**BASE, **IZIPAY, 'GOOGLE_OAUTH_CLIENT_ID': '1-x.apps.googleusercontent.com'})
        self.assertNotIn('READY FOR DEPLOY', result.stdout)
        self.assertIn('No sustituye', result.stdout)

    # -- what contradicts itself ----------------------------------------------

    def attention(self, values, needle, **kwargs):
        result = self.run_preflight(values, **kwargs)
        self.assertEqual(result.returncode, 1, result.stdout)
        found = '\n'.join(self.lines(result, 'ATENCIÓN'))
        self.assertIn(needle, found, result.stdout)
        return result

    def test_keys_of_both_products_at_once_are_refused(self):
        both = {**BASE, **IZIPAY, **{k: v for k, v in MICUENTAWEB.items() if k != 'PAYMENT_PROVIDER'}}
        self.attention(both, 'dos productos')

    def test_mi_cuenta_web_keys_from_two_environments_are_refused(self):
        mixed = {**BASE, **MICUENTAWEB, 'MICUENTAWEB_PUBLIC_KEY': '90000001:publickey_NoEsRealNoEsReal'}
        self.attention(mixed, 'TEST y PRODUCCIÓN')

    def test_a_public_key_of_another_shop_is_refused(self):
        other = {**BASE, **MICUENTAWEB, 'MICUENTAWEB_PUBLIC_KEY': '11111111:testpublickey_NoEsRealNoEsReal'}
        self.attention(other, 'otra tienda')

    def test_production_pointed_at_a_sandbox_is_refused(self):
        self.attention({**BASE, **IZIPAY, 'IZIPAY_ENV': 'production'}, 'sandbox')

    def test_a_notification_address_on_another_domain_is_refused(self):
        wrong = {**BASE, **IZIPAY, 'IZIPAY_IPN_URL': 'https://otra.example.pe/api/payments/izipay/notification/'}
        self.attention(wrong, 'notificación')

    def test_a_domain_that_is_not_one_is_refused(self):
        for domain in ('https://tienda.example.pe', 'localhost', 'tienda.test', 'www.tienda.example.pe'):
            self.attention({**BASE, **IZIPAY, 'SITE_DOMAIN': domain}, 'SITE_DOMAIN')

    def test_a_weak_or_development_secret_is_refused(self):
        self.attention({**BASE, **IZIPAY, 'SECRET_KEY': 'changeme-dev-only'}, 'SECRET_KEY')
        self.attention({**BASE, **IZIPAY, 'SECRET_KEY': 'corta'}, 'SECRET_KEY')
        self.attention({**BASE, **IZIPAY, 'POSTGRES_PASSWORD': 'postgres'}, 'POSTGRES_PASSWORD')

    def test_mail_that_would_be_written_to_the_log_is_refused(self):
        console = {**BASE, **IZIPAY, 'EMAIL_BACKEND': 'django.core.mail.backends.console.EmailBackend'}
        self.attention(console, 'registro')

    def test_a_mail_port_and_its_kind_of_tls_must_agree(self):
        self.attention({**BASE, **IZIPAY, 'EMAIL_PORT': '465'}, '465')
        self.attention({**BASE, **IZIPAY, 'EMAIL_USE_TLS': '0'}, 'sin cifrar')
        ok = self.run_preflight({**BASE, **IZIPAY, 'EMAIL_PORT': '465', 'EMAIL_USE_TLS': '0', 'EMAIL_USE_SSL': '1'})
        self.assertEqual(ok.returncode, 0, ok.stdout)

    def test_a_file_other_users_can_read_is_refused(self):
        self.attention({**BASE, **IZIPAY}, 'chmod 600', mode=0o644)

    def test_whatsapp_switched_on_without_credentials_is_refused(self):
        self.attention({**BASE, **IZIPAY, 'WHATSAPP_PROVIDER': 'cloud_api'}, 'WhatsApp')
        configured = {**BASE, **IZIPAY, 'WHATSAPP_PROVIDER': 'cloud_api', 'WHATSAPP_TOKEN_TIENDA': 'x' * 40,
                      'WHATSAPP_SECRET_TIENDA': 'y' * 32, 'WHATSAPP_VERIFY_TIENDA': 'z' * 24}
        result = self.run_preflight(configured)
        self.assertIn('OK    WhatsApp', result.stdout)

    def test_quotes_and_spaces_around_a_value_are_not_part_of_it(self):
        text = ''.join(f'{k} = "{v}"\n' for k, v in {**BASE, **IZIPAY}.items())
        result = self.run_preflight({}, text=text)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_a_missing_file_says_how_to_create_it(self):
        result = subprocess.run(
            [sys.executable, str(PREFLIGHT), '--env', str(Path(self.tmp.name) / 'no-existe')],
            cwd=ROOT, capture_output=True, text=True, timeout=60, env={'PATH': os.environ['PATH']})
        self.assertEqual(result.returncode, 1)
        self.assertIn('cp deploy/.env.production.example', result.stdout)

    # -- it knows every setting the example offers ----------------------------

    def test_every_variable_of_the_example_file_is_known_to_it(self):
        """A setting added to the example without teaching the check about it would never be looked at."""
        names = set(re.findall(r'(?m)^#?\s?([A-Z][A-Z0-9_]+)=', EXAMPLE.read_text(encoding='utf-8')))
        source = PREFLIGHT.read_text(encoding='utf-8')
        unknown = sorted(name for name in names if f"'{name}'" not in source and not name.endswith('_<EMPRESA>'))
        self.assertEqual(unknown, [])

    # -- the mail server, asked for real --------------------------------------

    def _sink(self, credentials=None):
        sys.path.insert(0, str(ROOT / 'deploy'))
        self.addCleanup(sys.path.remove, str(ROOT / 'deploy'))
        from rehearsal_smtp_sink import Sink

        server = Sink(0, str(Path(self.tmp.name) / 'mail'), credentials, host='127.0.0.1')
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        return server.server_address[1]

    def _mail(self, port, **overrides):
        return {**BASE, **IZIPAY, 'EMAIL_HOST': '127.0.0.1', 'EMAIL_PORT': str(port), 'EMAIL_USE_TLS': '0', **overrides}

    def test_asked_to_it_signs_in_to_the_mail_server_and_sends_nothing(self):
        port = self._sink(('tienda@example.pe', MAIL_PASSWORD))
        result = self.run_preflight(self._mail(port), '--smtp')
        self.assertIn('OK    el servidor de correo acepta la conexión y las credenciales', result.stdout)
        self.assertEqual(list((Path(self.tmp.name) / 'mail').glob('*.eml')), [])

    def test_a_refused_password_is_said_without_repeating_it(self):
        port = self._sink(('tienda@example.pe', 'otra-clave'))
        result = self.run_preflight(self._mail(port), '--smtp')
        refused = '\n'.join(self.lines(result, 'ATENCIÓN'))
        self.assertIn('rechazó las credenciales', refused)

    def test_a_mail_server_that_is_not_there_is_said(self):
        result = self.run_preflight(self._mail(1), '--smtp')
        self.assertIn('no se pudo conectar', '\n'.join(self.lines(result, 'ATENCIÓN')))

    def test_a_test_message_is_sent_only_to_the_address_given(self):
        port = self._sink()
        result = self.run_preflight(self._mail(port), '--smtp-send-to', 'prueba@example.pe')
        self.assertIn('OK    mensaje de prueba entregado al servidor de correo', result.stdout)
        [message] = list((Path(self.tmp.name) / 'mail').glob('*.eml'))
        text = message.read_text()
        self.assertIn('prueba@example.pe', text.splitlines()[0])
        self.assertIn('tienda.example.pe', text)
