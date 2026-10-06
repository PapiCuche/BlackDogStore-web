"""
INTEGRATIONS-CONSOLE · mail.

The point of this module is not that an SMTP configuration can be SAVED. It is
that the running system USES it:

    a master configures SMTP in the console → tests it → activates it
    → somebody registers → the verification mail leaves through THAT server

and that changing it changes where the next message goes, with no restart.

The mail server here is a real SMTP conversation over a real socket with a
stand-in that stores what it receives (`deploy/rehearsal_smtp_sink.py`).
"""
import smtplib
import ssl
import sys
import tempfile
import threading
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from cryptography.fernet import Fernet

from store import integrations
from store.integrations import mail as runtime_mail
from store.integrations.providers import smtp

ROOT = Path(settings.BASE_DIR).parent
RUNTIME = 'store.integrations.mail.RuntimeEmailBackend'
PASSWORD = 'clave-del-correo-NoEsReal-5521'
OTHER_PASSWORD = 'otra-clave-NoEsReal-9087'


def start_sink(testcase, credentials=None, stall=False):
    """A stand-in mail server on a free local port. Returns `(port, folder)`."""
    sys.path.insert(0, str(ROOT / 'deploy'))
    testcase.addCleanup(sys.path.remove, str(ROOT / 'deploy'))
    from rehearsal_smtp_sink import Sink

    folder = tempfile.TemporaryDirectory()
    testcase.addCleanup(folder.cleanup)
    server = Sink(0, folder.name, credentials, host='127.0.0.1', stall=stall)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    testcase.addCleanup(server.server_close)
    testcase.addCleanup(server.shutdown)
    return server.server_address[1], Path(folder.name)


def received(folder):
    return [path.read_text() for path in sorted(folder.glob('*.eml'))]


@override_settings(
    APP_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode(), EMAIL_BACKEND=RUNTIME,
    EMAIL_BACKEND_LEGACY='', DEFAULT_FROM_EMAIL='no-reply@localhost', REQUIRE_EMAIL_VERIFICATION=True,
    FRONTEND_URL='https://tienda.example',
)
class _Base(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()       # the registration limit is per minute and per address
        self.master = get_user_model().objects.create_superuser('master', 'master@example.com', 'x')
        self.client = APIClient()
        self.client.force_authenticate(self.master)

    def public(self, port, **overrides):
        return {'host': '127.0.0.1', 'port': port, 'security': 'none', 'username': 'tienda',
                'from_email': 'tienda@example.pe', 'from_name': 'Tienda de Prueba', 'timeout': 3, **overrides}

    def save(self, public, password=PASSWORD, version=None):
        body = {'public': public, 'secrets': {} if password is None else {'password': password}}
        if version is not None:
            body['version'] = version
        return self.client.put('/api/admin/integrations/smtp/draft/', body, format='json')

    def test_connection(self, **body):
        return self.client.post('/api/admin/integrations/smtp/test/', body, format='json')

    def activate(self):
        version = self.client.get('/api/admin/integrations/smtp/').json()['draft']['version']
        return self.client.post('/api/admin/integrations/smtp/activate/', {'version': version}, format='json')

    def configure(self, port, password=PASSWORD, **overrides):
        self.assertEqual(self.save(self.public(port, **overrides), password).status_code, 200)
        self.assertEqual(self.test_connection().json()['status'], 'ok')
        self.assertEqual(self.activate().status_code, 200)

    def register(self, username):
        return APIClient().post('/api/auth/register/', {
            'username': username, 'email': f'{username}@example.pe', 'first_name': 'Ana', 'last_name': 'Prueba',
            'password': 'Una-clave-larga-91', 'password_confirm': 'Una-clave-larga-91',
        }, format='json')


class RuntimeTest(_Base):
    def test_a_registration_leaves_through_the_server_configured_in_the_console(self):
        port, folder = start_sink(self, ('tienda', PASSWORD))
        self.configure(port)

        self.assertEqual(self.register('ana').status_code, 201)

        [message] = received(folder)
        self.assertIn('ana@example.pe', message.splitlines()[0])
        self.assertIn('Tienda de Prueba', message)
        self.assertIn('tienda@example.pe', message)
        self.assertIn('https://tienda.example/auth/verify-email?token=', message)
        self.assertEqual(mail.outbox if hasattr(mail, 'outbox') else [], [])

    def test_changing_the_active_server_changes_where_the_next_message_goes(self):
        """No restart: the backend asks on every send."""
        first_port, first = start_sink(self, ('tienda', PASSWORD))
        second_port, second = start_sink(self, ('tienda', OTHER_PASSWORD))
        self.configure(first_port)
        self.register('uno')
        self.configure(second_port, password=OTHER_PASSWORD)
        self.register('dos')

        self.assertEqual(len(received(first)), 1)
        [message] = received(second)
        self.assertIn('dos@example.pe', message.splitlines()[0])

    def test_every_kind_of_mail_uses_it(self):
        """Password recovery and a staff invitation go the same way as a registration."""
        from store.emails import send_password_reset_email
        from store.models import StaffInvitation
        from store.staff_views import _send_invitation_email
        from store.tests import _p3_company

        port, folder = start_sink(self, ('tienda', PASSWORD))
        self.configure(port)
        user = get_user_model().objects.create_user('luis', 'luis@example.pe', 'x')
        send_password_reset_email(user, 'token-de-prueba')
        company = _p3_company('integ-mail', 'Empresa Correo')
        invitation = StaffInvitation(company=company, email='nueva@example.pe', first_name='Eva')
        from django.utils import timezone
        invitation.expires_at = timezone.now()
        _send_invitation_email(invitation, 'token-de-invitacion')

        messages = received(folder)
        self.assertEqual(len(messages), 2)
        self.assertIn('/auth/reset-password?token=token-de-prueba', messages[0])
        self.assertIn('/invitacion?token=token-de-invitacion', messages[1])
        for message in messages:
            self.assertIn('tienda@example.pe', message.splitlines()[0])

    def test_a_draft_that_was_not_activated_sends_nothing(self):
        port, folder = start_sink(self, ('tienda', PASSWORD))
        self.save(self.public(port))
        self.test_connection()
        with self.assertLogs('store.integrations', level='ERROR'):
            self.register('eva')
        self.assertEqual(received(folder), [])

    def test_a_disabled_configuration_sends_nothing(self):
        port, folder = start_sink(self, ('tienda', PASSWORD))
        self.configure(port)
        self.client.post('/api/admin/integrations/smtp/disable/', {}, format='json')
        with self.assertLogs('store.integrations', level='ERROR'):
            self.register('eva')
        self.assertEqual(received(folder), [])

    def test_off_is_off_whatever_the_environment_names(self):
        """A development backend in the environment is not a way round the switch."""
        port, _folder = start_sink(self, ('tienda', PASSWORD))
        self.configure(port)
        self.client.post('/api/admin/integrations/smtp/disable/', {}, format='json')
        with override_settings(EMAIL_BACKEND_LEGACY='django.core.mail.backends.locmem.EmailBackend'):
            self.assertFalse(runtime_mail.is_configured())
            with self.assertRaises(runtime_mail.MailNotConfigured), self.assertLogs('store.integrations', level='ERROR'):
                mail.send_mail('asunto', 'cuerpo', None, ['x@example.pe'])
        self.assertEqual(mail.outbox, [])

    def test_a_configuration_this_server_cannot_read_is_no_configuration_and_no_crash(self):
        """The root key was changed, or the database came from another server."""
        port, folder = start_sink(self, ('tienda', PASSWORD))
        self.configure(port)
        with override_settings(APP_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode(),
                               EMAIL_BACKEND_LEGACY='django.core.mail.backends.locmem.EmailBackend'):
            with self.assertLogs('store.integrations', level='ERROR') as captured:
                self.assertFalse(runtime_mail.is_configured())
                with self.assertRaises(runtime_mail.MailNotConfigured):
                    mail.send_mail('asunto', 'cuerpo', None, ['x@example.pe'])
                self.assertEqual(self.register('otra_clave').status_code, 201)
            self.assertIn('cannot be read', '\n'.join(captured.output))
        self.assertEqual(received(folder), [])
        self.assertEqual(mail.outbox, [])           # and the environment's backend was not a way round it

    def test_with_nothing_configured_mail_is_an_error_and_never_the_console(self):
        self.assertFalse(runtime_mail.is_configured())
        with self.assertRaises(runtime_mail.MailNotConfigured):
            mail.send_mail('asunto', 'cuerpo', None, ['x@example.pe'])
        # The request that triggered it still answers: the send sites catch and log.
        with self.assertLogs('store.integrations', level='ERROR') as captured:
            self.assertEqual(self.register('sin_correo').status_code, 201)
        self.assertIn('no SMTP configuration is active', '\n'.join(captured.output))

    def test_the_password_never_reaches_a_log(self):
        """Through the path that logs: a server that refuses the password, quoting it back."""
        port, _folder = start_sink(self, ('tienda', 'la-buena'))
        self.save(self.public(port))                    # PASSWORD is not the one the server takes
        with self.assertLogs('store', level='DEBUG') as captured:
            import logging
            logging.getLogger('store.integrations').info('prueba de conexión')
            self.assertEqual(self.test_connection().json()['status'], 'auth_failed')
            with mock.patch('store.integrations.providers.smtp.connect',
                            side_effect=RuntimeError(f'535 bad password {PASSWORD}')):
                self.assertEqual(self.test_connection().json()['status'], 'error')
        text = '\n'.join(captured.output)
        self.assertIn('integration test crashed', text)
        self.assertNotIn(PASSWORD, text)


class LegacyEnvironmentTest(_Base):
    def env(self, port, **extra):
        return override_settings(
            EMAIL_BACKEND_LEGACY=smtp.SMTP_BACKEND, EMAIL_HOST='127.0.0.1', EMAIL_PORT=port,
            EMAIL_HOST_USER='tienda', EMAIL_HOST_PASSWORD=PASSWORD, EMAIL_USE_TLS=False, EMAIL_USE_SSL=False,
            EMAIL_TIMEOUT=3, DEFAULT_FROM_EMAIL='entorno@example.pe', **extra)

    def test_an_installation_configured_by_environment_keeps_sending(self):
        port, folder = start_sink(self, ('tienda', PASSWORD))
        with self.env(port):
            self.assertEqual(self.register('ana').status_code, 201)
            self.assertEqual(integrations.source('smtp'), 'env')
        [message] = received(folder)
        self.assertIn('entorno@example.pe', message.splitlines()[0])

    def test_the_console_says_it_comes_from_the_environment_and_shows_no_password(self):
        with self.env(2525):
            detail = self.client.get('/api/admin/integrations/smtp/').json()
        self.assertEqual((detail['state'], detail['source']), ('ACTIVE', 'env'))
        self.assertEqual(detail['env']['public']['host'], '127.0.0.1')
        self.assertEqual(detail['env']['secrets'], ['password'])
        self.assertNotIn(PASSWORD, str(detail))

    def test_the_console_takes_over_from_the_environment_once_activated(self):
        env_port, env_folder = start_sink(self, ('tienda', PASSWORD))
        panel_port, panel_folder = start_sink(self, ('tienda', OTHER_PASSWORD))
        with self.env(env_port):
            self.configure(panel_port, password=OTHER_PASSWORD)
            self.register('ana')
            self.assertEqual(integrations.source('smtp'), 'panel')
        self.assertEqual(received(env_folder), [])
        self.assertEqual(len(received(panel_folder)), 1)

    def test_a_master_can_move_the_environment_into_the_console_without_seeing_the_password(self):
        port, folder = start_sink(self, ('tienda', PASSWORD))
        with self.env(port):
            imported = self.client.post('/api/admin/integrations/smtp/import-env/', {}, format='json')
            self.assertEqual(imported.status_code, 200)
            self.assertNotIn(PASSWORD, imported.content.decode())
            self.assertEqual(imported.json()['draft']['secrets']['password']['configured'], True)
            self.assertEqual(self.test_connection().json()['status'], 'ok')
            self.assertEqual(self.activate().status_code, 200)
        # The environment is gone; the console's copy keeps working.
        self.register('ana')
        self.assertEqual(len(received(folder)), 1)

    def test_a_development_backend_chosen_on_purpose_is_still_used(self):
        with override_settings(EMAIL_BACKEND_LEGACY='django.core.mail.backends.locmem.EmailBackend'):
            mail.outbox = []
            mail.send_mail('asunto', 'cuerpo', None, ['x@example.pe'])
            self.assertEqual(len(mail.outbox), 1)
            self.assertTrue(runtime_mail.is_configured())


    def test_testing_the_draft_when_there_is_none_does_not_test_the_environment_instead(self):
        port, _folder = start_sink(self, ('tienda', PASSWORD))
        with self.env(port):
            response = self.client.post('/api/admin/integrations/smtp/test/', {'target': 'draft'}, format='json')
            self.assertEqual(response.status_code, 404)
            # Asked about what runs, the environment IS what runs.
            running = self.client.post('/api/admin/integrations/smtp/test/', {'target': 'active'}, format='json')
            self.assertEqual(running.json()['status'], 'ok')


class ConnectionTestTest(_Base):
    def test_a_working_server_is_correct(self):
        port, folder = start_sink(self, ('tienda', PASSWORD))
        self.save(self.public(port))
        result = self.test_connection().json()
        self.assertEqual((result['ok'], result['status']), (True, 'ok'))
        self.assertEqual(received(folder), [], 'testing the connection sent a message')

    def test_a_test_message_is_delivered_to_the_address_given(self):
        port, folder = start_sink(self, ('tienda', PASSWORD))
        self.save(self.public(port))
        result = self.test_connection(send_to='prueba@example.pe').json()
        self.assertEqual(result['status'], 'ok')
        [message] = received(folder)
        self.assertIn('prueba@example.pe', message.splitlines()[0])
        self.assertNotIn('http', message.split('\n\n', 1)[1])

    def test_a_malformed_test_address_is_refused(self):
        port, _ = start_sink(self)
        self.save(self.public(port))
        response = self.test_connection(send_to='a@example.pe\nBcc: b@example.pe')
        self.assertEqual(response.status_code, 400)

    def test_a_wrong_password_is_an_authentication_error_that_repeats_nothing(self):
        port, _ = start_sink(self, ('tienda', 'la-buena'))
        self.save(self.public(port))
        response = self.test_connection()
        self.assertEqual(response.json()['status'], 'auth_failed')
        self.assertNotIn(PASSWORD, response.content.decode())
        self.assertNotIn('5.7.8', response.content.decode())

    def test_a_server_that_is_not_there_is_unreachable(self):
        self.save(self.public(1))
        self.assertEqual(self.test_connection().json()['status'], 'unreachable')

    def test_a_server_that_stops_answering_is_a_timeout(self):
        port, _ = start_sink(self, stall=True)
        self.save(self.public(port, timeout=1))
        self.assertEqual(self.test_connection().json()['status'], 'timeout')

    def _conversation(self, security, port):
        """Which smtplib calls a configuration makes, without a network."""
        calls = []

        class Fake:
            def __init__(self, host, port, timeout=None, context=None):
                calls.append((type(self).__name__, host, port, timeout))

            def ehlo(self):
                calls.append('ehlo')

            def starttls(self, context=None):
                calls.append('starttls')

            def login(self, user, password):
                calls.append(('login', user, password))

            def quit(self):
                calls.append('quit')

            def close(self):
                pass

        with mock.patch.object(smtplib, 'SMTP', type('SMTP', (Fake,), {})), \
                mock.patch.object(smtplib, 'SMTP_SSL', type('SMTP_SSL', (Fake,), {})):
            server = smtp.connect({'host': 'smtp.example.pe', 'port': port, 'security': security,
                                   'username': 'tienda', 'timeout': 7}, PASSWORD)
            server.quit()
        return calls

    def test_starttls_upgrades_the_connection_before_signing_in(self):
        calls = self._conversation('starttls', 587)
        self.assertEqual(calls[0], ('SMTP', 'smtp.example.pe', 587, 7))
        self.assertLess(calls.index('starttls'), calls.index(('login', 'tienda', PASSWORD)))

    def test_port_465_speaks_tls_from_the_first_byte(self):
        calls = self._conversation('ssl', 465)
        self.assertEqual(calls[0], ('SMTP_SSL', 'smtp.example.pe', 465, 7))
        self.assertNotIn('starttls', calls)

    def test_a_certificate_that_does_not_verify_is_a_tls_error(self):
        with mock.patch.object(smtp, 'connect', side_effect=ssl.SSLCertVerificationError('x')):
            self.save({**self.public(587), 'host': 'smtp.example.pe', 'security': 'starttls'})
            self.assertEqual(self.test_connection().json()['status'], 'tls_invalid')


class ValidationTest(_Base):
    def remote(self, **overrides):
        return {**self.public(587), 'host': 'smtp.example.pe', 'security': 'starttls', **overrides}

    def errors(self, **overrides):
        response = self.save(self.remote(**overrides))
        return response.json().get('errors', {}) if response.status_code == 400 else {}

    def test_what_is_refused(self):
        self.assertIn('port', self.errors(port=0))
        self.assertIn('port', self.errors(port=70000))
        self.assertIn('port', self.errors(port='abc'))
        self.assertIn('host', self.errors(host='smtp://usuario:clave@smtp.example.pe'))
        self.assertIn('from_email', self.errors(from_email='no-es-un-correo'))
        self.assertIn('timeout', self.errors(timeout=0))
        self.assertIn('timeout', self.errors(timeout=600))
        self.assertIn('security', self.errors(security='otra'))
        self.assertIn('security', self.errors(port=465, security='starttls'))

    def test_no_encryption_is_only_for_a_server_on_this_machine(self):
        self.assertIn('security', self.errors(security='none'))
        self.assertEqual(self.save(self.public(2525)).status_code, 200)

    def test_a_user_needs_its_password_and_a_password_its_user(self):
        response = self.save(self.remote(), password=None)
        self.assertIn('password', response.json()['errors'])
        response = self.save(self.remote(username=''))
        self.assertIn('username', response.json()['errors'])

    def test_the_password_is_write_only(self):
        self.save(self.remote())
        detail = self.client.get('/api/admin/integrations/smtp/').json()
        self.assertNotIn(PASSWORD, str(detail))
        self.assertEqual(detail['draft']['secrets']['password']['configured'], True)
        self.assertNotIn(PASSWORD[-4:], str(detail))
        self.assertNotIn('password', detail['draft']['public'])
