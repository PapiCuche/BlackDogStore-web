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
import base64
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
#: The shape of a real root key (32 random bytes, URL-safe base64) and not one.
ROOT_KEY = base64.urlsafe_b64encode(b'NoEsRealNoEsRealNoEsRealNoEsReal').decode()

BASE = {
    'SITE_DOMAIN': 'tienda.example.pe',
    'SECRET_KEY': SECRET_KEY,
    'POSTGRES_PASSWORD': DB_PASSWORD,
    'APP_CONFIG_ENCRYPTION_KEY': ROOT_KEY,
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
SECRETS = (SECRET_KEY, DB_PASSWORD, MAIL_PASSWORD, IZIPAY_API_KEY, IZIPAY_HASH_KEY, MCW_PASSWORD, MCW_HMAC, ROOT_KEY)


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

    # -- INTEGRATIONS-CONSOLE: the root key, and what may be typed in the console ----

    MAIL = ('EMAIL_BACKEND', 'EMAIL_HOST', 'EMAIL_PORT', 'EMAIL_USE_TLS', 'EMAIL_HOST_USER', 'EMAIL_HOST_PASSWORD',
            'DEFAULT_FROM_EMAIL')

    def without(self, values, *names):
        return {k: v for k, v in values.items() if k not in names}

    def test_the_root_key_of_the_secret_store_is_owed_and_generated_on_the_server(self):
        result = self.run_preflight(self.without({**BASE, **IZIPAY}, 'APP_CONFIG_ENCRYPTION_KEY'))
        self.assertEqual(result.returncode, 1)
        owed = '\n'.join(self.lines(result, 'BLOCKED/OWNER-DATA'))
        self.assertIn('APP_CONFIG_ENCRYPTION_KEY', owed)
        self.assertIn('se genera en el servidor', owed)
        shipped = self.run_preflight({}, text=EXAMPLE.read_text(encoding='utf-8'))
        self.assertIn('APP_CONFIG_ENCRYPTION_KEY', '\n'.join(self.lines(shipped, 'BLOCKED/OWNER-DATA')))

    def test_a_root_key_that_is_not_one_is_refused_and_not_repeated(self):
        for wrong in ('PEGADO-POR-ERROR-NoEsReal', SECRET_KEY, ROOT_KEY[:-4], ROOT_KEY + ',' + ROOT_KEY):
            with self.subTest(len(wrong)):
                result = self.run_preflight({**BASE, **IZIPAY, 'APP_CONFIG_ENCRYPTION_KEY': wrong})
                self.assertEqual(result.returncode, 1)
                self.assertIn('APP_CONFIG_ENCRYPTION_KEY', '\n'.join(self.lines(result, 'ATENCIÓN')))
                self.assertNotIn('PEGADO-POR-ERROR', result.stdout + result.stderr)

    def test_the_previous_root_keys_have_to_be_keys_too(self):
        good = self.run_preflight({**BASE, **IZIPAY, 'APP_CONFIG_ENCRYPTION_KEYS_PREVIOUS': f'{ROOT_KEY}, {ROOT_KEY}'})
        self.assertEqual(good.returncode, 0, good.stdout)
        bad = self.run_preflight({**BASE, **IZIPAY, 'APP_CONFIG_ENCRYPTION_KEYS_PREVIOUS': 'PEGADO-POR-ERROR-NoEsReal'})
        self.assertEqual(bad.returncode, 1)
        self.assertIn('APP_CONFIG_ENCRYPTION_KEYS_PREVIOUS', '\n'.join(self.lines(bad, 'ATENCIÓN')))
        self.assertNotIn('PEGADO-POR-ERROR', bad.stdout)

    def test_mail_and_payments_may_be_left_for_the_console_and_are_still_owed(self):
        result = self.run_preflight(self.without(BASE, *self.MAIL))
        self.assertEqual(result.returncode, 0, result.stdout)
        owed = self.lines(result, 'BLOCKED/OWNER-DATA')
        self.assertEqual(len(owed), 2, owed)
        for line, word in zip(sorted(owed), ('Izipay', 'correo')):
            self.assertIn(word, line)
            self.assertIn('Configuración › Integraciones', line)
        self.assertIn('CONFIGURACIÓN: SUFICIENTE PARA ARRANCAR', result.stdout)
        self.assertIn('antes de abrir', result.stdout)
        self.assertNotIn('CONFIGURACIÓN: COMPLETA', result.stdout)

    def test_mail_named_in_the_file_and_left_half_written_still_stops_everything(self):
        """`EMAIL_BACKEND=smtp` without its host is a backend that does not start."""
        result = self.run_preflight(self.without({**BASE, **IZIPAY}, 'EMAIL_HOST', 'EMAIL_HOST_PASSWORD'))
        self.assertEqual(result.returncode, 1)
        self.assertIn('CONFIGURACIÓN: INCOMPLETA', result.stdout)
        half = self.run_preflight(self.without({**BASE, **IZIPAY}, 'EMAIL_BACKEND', 'EMAIL_HOST_PASSWORD'))
        self.assertEqual(half.returncode, 1)

    def test_the_example_as_shipped_leaves_mail_and_payments_to_the_console(self):
        """Copied and filled in with only what the server needs to start, it starts."""
        text = EXAMPLE.read_text(encoding='utf-8')
        self.assertNotRegex(text, r'(?m)^EMAIL_BACKEND=')
        self.assertNotRegex(text, r'(?m)^PAYMENT_PROVIDER=')
        filled = (text.replace('SITE_DOMAIN=', 'SITE_DOMAIN=tienda.example.pe', 1)
                  .replace('\nSECRET_KEY=', f'\nSECRET_KEY={SECRET_KEY}', 1)
                  .replace('\nPOSTGRES_PASSWORD=', f'\nPOSTGRES_PASSWORD={DB_PASSWORD}', 1)
                  .replace('\nAPP_CONFIG_ENCRYPTION_KEY=', f'\nAPP_CONFIG_ENCRYPTION_KEY={ROOT_KEY}', 1)
                  .replace('\nORDER_NOTIFICATION_EMAIL=', '\nORDER_NOTIFICATION_EMAIL=pedidos@example.pe', 1))
        result = self.run_preflight({}, text=filled)
        self.assertEqual(self.lines(result, 'ATENCIÓN'), [], result.stdout)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('CONFIGURACIÓN: SUFICIENTE PARA ARRANCAR', result.stdout)

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

    # -- REVIEW: it must read the file the way Compose and Django read it -------

    PASTED = 'PEGADO-POR-ERROR-NoEsReal'

    def no_echo(self, values, *args, **kwargs):
        result = self.run_preflight(values, *args, **kwargs)
        self.assertNotIn(self.PASTED, result.stdout + result.stderr, 'a value from the file was printed')
        self.assertNotIn('Traceback', result.stdout + result.stderr)
        return result

    def test_a_mail_host_that_is_not_a_host_name_is_refused_and_not_repeated(self):
        url = {**BASE, **IZIPAY, 'EMAIL_HOST': f'smtp://apikey:{self.PASTED}@smtp.example.pe'}
        result = self.no_echo(url)
        self.assertEqual(result.returncode, 1)
        self.assertIn('EMAIL_HOST', '\n'.join(self.lines(result, 'ATENCIÓN')))

    def test_a_port_or_a_timeout_that_is_not_a_number_is_refused_and_not_repeated(self):
        for name, value in (('EMAIL_PORT', f'587 {self.PASTED}'), ('EMAIL_PORT', 'abc'), ('EMAIL_PORT', '0'),
                            ('EMAIL_TIMEOUT', self.PASTED), ('EMAIL_TIMEOUT', '0')):
            for args in ((), ('--smtp',)):
                result = self.no_echo({**BASE, **IZIPAY, name: value}, *args)
                self.assertEqual(result.returncode, 1, f'{name}={value!r} {args}')
                self.assertIn(name, '\n'.join(self.lines(result, 'ATENCIÓN')))

    def test_a_domain_that_is_not_a_host_name_is_not_repeated(self):
        for domain in (f'{self.PASTED}.pegado por error', f'tienda.pe {self.PASTED}', '10.0.0.1', 'tienda..pe', 'tienda.pe.'):
            result = self.no_echo({**BASE, **IZIPAY, 'SITE_DOMAIN': domain}, '--dns')
            self.assertEqual(result.returncode, 1, domain)
            self.assertIn('SITE_DOMAIN', '\n'.join(self.lines(result, 'ATENCIÓN')))
        for domain in ('tienda.pe', 'tienda.com.pe', 'mi-tienda.example.pe', 'xn--caf-dma.example.pe'):
            result = self.run_preflight({**BASE, **IZIPAY, 'SITE_DOMAIN': domain, 'IZIPAY_IPN_URL': ''})
            self.assertEqual(result.returncode, 0, domain + result.stdout)

    def test_an_empty_switch_is_off_the_way_django_reads_it(self):
        """`EMAIL_USE_TLS=` reaches Django as an empty string, and an empty string is False."""
        result = self.attention({**BASE, **IZIPAY, 'EMAIL_USE_TLS': ''}, 'sin cifrar')
        self.assertNotIn('STARTTLS)', result.stdout)

    def test_every_spelling_of_yes_that_django_accepts_is_a_yes(self):
        for spelling in ('1', 'true', 'True', 'yes', 'on', 'y', 'ok'):
            result = self.run_preflight({**BASE, **IZIPAY, 'EMAIL_USE_TLS': spelling})
            self.assertEqual(result.returncode, 0, spelling + result.stdout)
        self.attention({**BASE, **IZIPAY, 'FISCAL_ENABLED': 'y'}, 'FISCAL_ENABLED')

    def test_a_comment_after_a_value_is_not_part_of_the_value(self):
        text = ''.join(f'{k}={v}  # nota\n' for k, v in {**BASE, **IZIPAY}.items())
        self.assertEqual(self.run_preflight({}, text=text).returncode, 0)
        self.attention({**BASE, **IZIPAY, 'EMAIL_PORT': '465 # ssl'}, '465')
        result = self.run_preflight({**BASE, **IZIPAY, 'EMAIL_HOST': ' # pendiente'})
        self.assertIn('correo', '\n'.join(self.lines(result, 'BLOCKED/OWNER-DATA')))

    def test_a_line_written_for_a_shell_is_read_too(self):
        text = ''.join(f'export {k}={v}\n' for k, v in {**BASE, **IZIPAY}.items())
        self.assertEqual(self.run_preflight({}, text=text).returncode, 0)

    def test_the_payment_product_defaults_as_the_application_defaults_it(self):
        without = {k: v for k, v in {**BASE, **IZIPAY}.items() if k != 'PAYMENT_PROVIDER'}
        result = self.run_preflight(without)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('OK    Izipay «SDK web / Checkout»', result.stdout)

    def test_mi_cuenta_web_is_complete_without_the_key_nothing_reads(self):
        without = {k: v for k, v in {**BASE, **MICUENTAWEB}.items() if k != 'MICUENTAWEB_HMAC_KEY'}
        self.assertEqual(self.run_preflight(without).returncode, 0)

    def test_whatsapp_is_on_unless_the_file_says_otherwise(self):
        """The application's default is the real provider: a missing line is not «off»."""
        without = {k: v for k, v in {**BASE, **IZIPAY}.items() if k != 'WHATSAPP_PROVIDER'}
        self.attention(without, 'WHATSAPP_PROVIDER')
        self.attention({**BASE, **IZIPAY, 'WHATSAPP_PROVIDER': ''}, 'WHATSAPP_PROVIDER')

    def test_a_value_compose_would_rewrite_is_pointed_out(self):
        """Compose replaces `$NAME` inside this file. A password with a `$` would reach the shop as another one."""
        result = self.attention({**BASE, **IZIPAY, 'EMAIL_HOST_PASSWORD': 'abc$DEFghi-NoEsReal'}, 'EMAIL_HOST_PASSWORD')
        self.assertNotIn('abc$DEF', result.stdout)
        quoted = ''.join(f"{k}='{v}'\n" for k, v in {**BASE, **IZIPAY, 'EMAIL_HOST_PASSWORD': 'abc$DEFghi-NoEsReal'}.items())
        self.assertEqual(self.run_preflight({}, text=quoted).returncode, 0)

    def test_a_database_password_that_would_break_its_address_is_refused(self):
        self.attention({**BASE, **IZIPAY, 'POSTGRES_PASSWORD': 'con/barra@y#almohadilla-0123456789'}, 'POSTGRES_PASSWORD')

    def test_a_file_that_is_not_text_is_said_plainly(self):
        self.env_file.write_bytes(b'SITE_DOMAIN=tienda.pe\nSECRET_KEY=\xff\xfe\xfa\n')
        os.chmod(self.env_file, 0o600)
        result = subprocess.run([sys.executable, str(PREFLIGHT), '--env', str(self.env_file)],
                                cwd=ROOT, capture_output=True, text=True, timeout=60, env={'PATH': os.environ['PATH']})
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('Traceback', result.stdout + result.stderr)
        self.assertIn('UTF-8', result.stdout)

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

    def test_the_real_password_is_never_sent_over_a_connection_it_just_called_unsafe(self):
        """
        `--smtp` used to sign in right after warning that the mail was not
        encrypted — sending the real password in the clear to say so.
        """
        unreachable = {**BASE, **IZIPAY, 'EMAIL_HOST': 'smtp.example.invalid', 'EMAIL_USE_TLS': '0'}
        result = self.run_preflight(unreachable, '--smtp')
        self.assertIn('sin cifrar', '\n'.join(self.lines(result, 'ATENCIÓN')))
        self.assertIn('no se prueba el servidor de correo', result.stdout)
        self.assertNotIn('no se pudo conectar', result.stdout)

    def test_a_mail_server_on_this_same_machine_may_be_plain(self):
        port = self._sink()
        result = self.run_preflight(self._mail(port), '--smtp')
        self.assertEqual(self.lines(result, 'ATENCIÓN'), [])

    def test_a_password_the_protocol_cannot_carry_is_said_without_showing_any_of_it(self):
        port = self._sink()
        result = self.run_preflight(self._mail(port, EMAIL_HOST_PASSWORD='contrase\u00f1a-NoEsReal'), '--smtp')
        self.assertNotIn('Traceback', result.stdout + result.stderr)
        self.assertNotIn('\\xf1', result.stdout + result.stderr)
        self.assertNotIn('position', result.stdout + result.stderr)
        self.assertEqual(result.returncode, 1)

    def test_an_address_to_write_to_that_is_not_one_is_refused_before_connecting(self):
        port = self._sink()
        result = self.run_preflight(self._mail(port), '--smtp-send-to', 'a@example.pe\nBcc: b@example.pe')
        self.assertNotIn('Traceback', result.stdout + result.stderr)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(list((Path(self.tmp.name) / 'mail').glob('*.eml')), [])
