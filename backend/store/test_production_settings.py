import io
import json
import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

from store import xlsx_reader

BACKEND_DIR = Path(__file__).resolve().parent.parent

PRODUCTION = {
    'DEBUG': '0',
    'SECRET_KEY': 'una-clave-de-prueba-que-no-es-ninguna-de-las-inseguras-0123456789',
    'ALLOWED_HOSTS': 'tienda.example',
    'CORS_ALLOWED_ORIGINS': 'https://tienda.example',
    'FRONTEND_URL': 'https://tienda.example',
    'CHECKOUT_RETURN_URL': 'https://tienda.example',
    'EMAIL_BACKEND': 'django.core.mail.backends.console.EmailBackend',
    'DATABASE_URL': 'sqlite:///:memory:',
}

PROBE = '''
import json
from backend import settings as s
print(json.dumps({
    "samesite": s.JWT_COOKIE_SAMESITE,
    "renderers": list(s.REST_FRAMEWORK.get("DEFAULT_RENDERER_CLASSES", [])),
    "frontend_url": s.FRONTEND_URL,
    "logging_handlers": sorted(s.LOGGING.get("handlers", {})) if hasattr(s, "LOGGING") else None,
    "root_level": s.LOGGING.get("root", {}).get("level") if hasattr(s, "LOGGING") else None,
    "loggers": sorted(s.LOGGING.get("loggers", {})) if hasattr(s, "LOGGING") else None,
    "email_timeout": getattr(s, "EMAIL_TIMEOUT", None),
    "email_use_tls": getattr(s, "EMAIL_USE_TLS", None),
    "email_use_ssl": getattr(s, "EMAIL_USE_SSL", None),
}))
'''


def load_settings(**overrides):
    """
    Import the settings module in a fresh interpreter with exactly this
    environment. Settings are evaluated once per process, so the only honest way
    to ask "what does production get?" is to start one.
    """
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'SYSTEMROOT')}
    env.update(PRODUCTION)
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    # Values a developer keeps in backend/.env must not leak into the probe.
    for key in ('JWT_COOKIE_SAMESITE',):
        env.setdefault(key, 'Lax')
    return subprocess.run(
        [sys.executable, '-c', PROBE], cwd=BACKEND_DIR, env=env,
        capture_output=True, text=True, timeout=60,
    )


class ProductionSettingsTest(SimpleTestCase):
    """
    What a production process is configured with, asked of a real process.

    SEC-SET-03, SEC-SET-06, SEC-SET-09, SEC-SET-10 / ENV-02.
    """

    def ok(self, **overrides):
        result = load_settings(**overrides)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        return json.loads(result.stdout.strip().splitlines()[-1])

    def refused(self, needle, **overrides):
        result = load_settings(**overrides)
        self.assertNotEqual(result.returncode, 0, 'la configuración debió rechazarse')
        self.assertIn('ImproperlyConfigured', result.stderr)
        self.assertIn(needle, result.stderr)

    def test_a_complete_production_environment_loads(self):
        self.ok()

    # SEC-SET-03 ------------------------------------------------------------

    def test_production_refuses_to_guess_how_mail_leaves(self):
        """
        The default mail backend prints every message to stdout: verification,
        password-reset and invitation links, token included. In development that
        is the point. In production a missing line must not turn the container
        log into a list of working links.
        """
        self.refused('EMAIL_BACKEND', EMAIL_BACKEND=None)
        self.refused('EMAIL_BACKEND', EMAIL_BACKEND='')

    def test_production_accepts_a_mail_backend_that_was_chosen(self):
        self.ok(EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend')
        self.ok(EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend', EMAIL_HOST='smtp.example')

    def test_a_mail_server_that_does_not_answer_cannot_hold_a_request_for_ever(self):
        """
        MAIL-TIMEOUT. Django waits for an SMTP server without limit unless told
        otherwise, and a registration or a password reset sends its message
        inside the request. With eight threads, a provider that stops answering
        would take the API down one sign-up at a time.
        """
        self.assertEqual(self.ok()['email_timeout'], 10)
        self.assertEqual(self.ok(EMAIL_TIMEOUT='25')['email_timeout'], 25)
        self.refused('EMAIL_TIMEOUT', EMAIL_TIMEOUT='0')

    def test_a_provider_that_only_offers_implicit_tls_can_be_configured(self):
        """MAIL-SSL. Port 465 speaks TLS from the first byte; STARTTLS (587) was the only option."""
        smtp = {'EMAIL_BACKEND': 'django.core.mail.backends.smtp.EmailBackend', 'EMAIL_HOST': 'smtp.example'}
        settings = self.ok(**smtp, EMAIL_PORT='465', EMAIL_USE_SSL='1', EMAIL_USE_TLS='0')
        self.assertIs(settings['email_use_ssl'], True)
        self.assertIs(settings['email_use_tls'], False)
        self.assertIs(self.ok(**smtp)['email_use_ssl'], False)
        self.assertIs(self.ok(**smtp)['email_use_tls'], True)

    def test_both_kinds_of_tls_at_once_is_refused_at_start_up(self):
        """Django refuses the pair only when the first message is sent. Better to learn it when starting."""
        self.refused('EMAIL_USE_SSL', EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
                     EMAIL_HOST='smtp.example', EMAIL_USE_SSL='1', EMAIL_USE_TLS='1')

    def test_development_keeps_the_console_default(self):
        self.ok(DEBUG='1', EMAIL_BACKEND=None)

    def test_samesite_none_is_refused(self):
        """
        With SameSite=None the refresh cookie travels on cross-site requests,
        and the refresh endpoint's CSRF check is conditional on the access
        cookie: the setting alone removed the only defence that path has.
        """
        for value in ('None', 'none', 'NONE'):
            with self.subTest(value=value):
                self.refused('JWT_COOKIE_SAMESITE', JWT_COOKIE_SAMESITE=value)

    def test_an_unknown_samesite_value_is_refused(self):
        self.refused('JWT_COOKIE_SAMESITE', JWT_COOKIE_SAMESITE='Laxo')

    def test_samesite_is_refused_in_development_too(self):
        self.refused('JWT_COOKIE_SAMESITE', DEBUG='1', JWT_COOKIE_SAMESITE='None')

    def test_lax_and_strict_are_accepted_in_any_case(self):
        self.assertEqual(self.ok(JWT_COOKIE_SAMESITE='lax')['samesite'], 'Lax')
        self.assertEqual(self.ok(JWT_COOKIE_SAMESITE='STRICT')['samesite'], 'Strict')

    # SEC-SET-09 ------------------------------------------------------------

    def test_production_answers_json_only(self):
        self.assertEqual(
            self.ok()['renderers'], ['rest_framework.renderers.JSONRenderer'])

    def test_development_keeps_the_browsable_api(self):
        renderers = self.ok(DEBUG='1')['renderers']
        self.assertIn('rest_framework.renderers.BrowsableAPIRenderer', renderers)
        self.assertIn('rest_framework.renderers.JSONRenderer', renderers)

    # SEC-SET-10 / ENV-02 ---------------------------------------------------

    def test_production_refuses_a_missing_public_url(self):
        for name in ('FRONTEND_URL', 'CHECKOUT_RETURN_URL'):
            with self.subTest(name=name):
                self.refused(name, **{name: None})

    def test_production_refuses_a_localhost_public_url(self):
        for name in ('FRONTEND_URL', 'CHECKOUT_RETURN_URL'):
            for value in ('http://localhost:3000', 'http://127.0.0.1:3000', 'https://localhost'):
                with self.subTest(name=name, value=value):
                    self.refused(name, **{name: value})

    def test_development_keeps_the_localhost_default(self):
        data = self.ok(DEBUG='1', FRONTEND_URL=None, CHECKOUT_RETURN_URL=None)
        self.assertEqual(data['frontend_url'], 'http://localhost:3000')

    # SEC-SET-06 ------------------------------------------------------------

    def test_production_logs_to_the_console_from_info_up(self):
        data = self.ok()
        self.assertEqual(data['logging_handlers'], ['console'])
        self.assertEqual(data['root_level'], 'INFO')
        self.assertIn('django.security', data['loggers'])


class _CountingUpload(io.BytesIO):
    """An upload that knows its size and counts the bytes actually read."""

    def __init__(self, size):
        super().__init__(b'x' * size)
        self.name = 'catalogo.xlsx'
        self.size = size
        self.bytes_read = 0

    def read(self, *args):
        chunk = super().read(*args)
        self.bytes_read += len(chunk)
        return chunk


class XlsxUploadSizeTest(SimpleTestCase):
    """
    SEC-SET-05 — an oversized workbook is refused before it is read.

    `check_upload` read the whole file into memory and only then compared its
    length with the limit, so the limit protected nothing.
    """

    def test_an_oversized_upload_is_refused_without_reading_it(self):
        upload = _CountingUpload(xlsx_reader.MAX_UPLOAD_BYTES + 1)
        with self.assertRaises(xlsx_reader.XlsxTooLarge):
            xlsx_reader.check_upload(upload)
        self.assertEqual(upload.bytes_read, 0)

    def test_an_upload_that_lies_about_its_size_is_still_bounded(self):
        upload = _CountingUpload(xlsx_reader.MAX_UPLOAD_BYTES * 2)
        upload.size = 10
        with self.assertRaises(xlsx_reader.XlsxTooLarge):
            xlsx_reader.check_upload(upload)
        self.assertLessEqual(upload.bytes_read, xlsx_reader.MAX_UPLOAD_BYTES + 1)

    def test_an_upload_without_a_declared_size_is_bounded_too(self):
        upload = _CountingUpload(xlsx_reader.MAX_UPLOAD_BYTES * 2)
        del upload.size
        with self.assertRaises(xlsx_reader.XlsxTooLarge):
            xlsx_reader.check_upload(upload)
        self.assertLessEqual(upload.bytes_read, xlsx_reader.MAX_UPLOAD_BYTES + 1)
