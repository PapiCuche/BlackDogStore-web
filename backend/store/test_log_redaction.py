"""
LOG-REDACT: what the production logs may not keep.

The access log records the request line and the referer of every call. A repair's
tracking link, a staff invitation, the token Meta uses to verify a webhook, an IMEI
typed into a search box: all of them travel in a URL, so all of them were being
written to a log that outlives the request and is read by whoever operates the
server.

Rule: the path keeps its shape and loses the token; a query string keeps its keys,
and the values of the few keys known to be harmless.
"""
import logging
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from backend import log_redaction as redaction

TOKEN = 'Zk3pQ9vX2mB7cN1aT5yR8wE4uI6oP0sLdFgHjKlMnBv'
IMEI = '356938035643809'
MASK = redaction.MASK


class RedactionTest(SimpleTestCase):
    def test_a_tracking_link_keeps_its_route_and_loses_its_code(self):
        for uri, expected in (
            (f'/api/v1/tracking/{TOKEN}/', f'/api/v1/tracking/{MASK}/'),
            (f'/api/v1/tracking/{TOKEN}', f'/api/v1/tracking/{MASK}'),
            (f'/api/v1/tracking/{TOKEN}/evidence/7/content/', f'/api/v1/tracking/{MASK}/evidence/7/content/'),
            (f'/api/v1/tracking/{TOKEN}/quotes/3/decision/', f'/api/v1/tracking/{MASK}/quotes/3/decision/'),
            (f'https://tienda.example/seguimiento/{TOKEN}', f'https://tienda.example/seguimiento/{MASK}'),
        ):
            self.assertEqual(redaction.redact_uri(uri), expected)

    def test_query_values_are_dropped_unless_known_to_be_harmless(self):
        cases = {
            f'/api/v1/webhooks/whatsapp/acme/?hub.mode=subscribe&hub.verify_token={TOKEN}&hub.challenge=123':
                f'/api/v1/webhooks/whatsapp/acme/?hub.mode=subscribe&hub.verify_token={MASK}&hub.challenge={MASK}',
            f'/api/staff/invitations/accept/?token={TOKEN}':
                f'/api/staff/invitations/accept/?token={MASK}',
            f'/api/v1/internal/acme/service/devices/lookup/?serial_number=F2LX&imei={IMEI}':
                f'/api/v1/internal/acme/service/devices/lookup/?serial_number={MASK}&imei={MASK}',
            '/api/admin/customers/?search=71234567&page=2':
                f'/api/admin/customers/?search={MASK}&page=2',
            f'/api/cart/?session_key={TOKEN}': f'/api/cart/?session_key={MASK}',
            f'/api/payments/status/?reference={TOKEN}': f'/api/payments/status/?reference={MASK}',
            f'https://tienda.example/auth?next=/invitacion?token={TOKEN}':
                f'https://tienda.example/auth?next={MASK}',
        }
        for uri, expected in cases.items():
            self.assertEqual(redaction.redact_uri(uri), expected, uri)

    def test_what_helps_to_operate_stays_readable(self):
        for uri in (
            '/api/products/',
            '/api/admin/inventory/units/?branch=all&product=7&status=available&page=2',
            '/api/admin/orders/9/sales-note/pdf/?formato=ticket80&company=1',
            '/api/v1/webhooks/whatsapp/acme/',
            '-',
            '',
        ):
            self.assertEqual(redaction.redact_uri(uri), uri)

    def test_an_empty_value_and_a_bare_key_do_not_break_it(self):
        self.assertEqual(redaction.redact_uri('/api/x/?token=&page=1&flag'), '/api/x/?token=&page=1&flag')

    def test_a_request_line_is_redacted_as_a_whole(self):
        line = f'GET /api/v1/tracking/{TOKEN}/?x={IMEI} HTTP/1.1'
        self.assertEqual(redaction.redact_request_line(line), f'GET /api/v1/tracking/{MASK}/?x={MASK} HTTP/1.1')


class DjangoLogFilterTest(SimpleTestCase):
    def _format(self, message, *args):
        record = logging.LogRecord('django.request', logging.WARNING, __file__, 1, message, args, None)
        self.assertTrue(redaction.RedactingFilter().filter(record))
        return record.getMessage()

    def test_the_path_django_writes_for_a_refused_request_loses_its_token(self):
        text = self._format('%s: %s', 'Too Many Requests', f'/api/v1/tracking/{TOKEN}/')
        self.assertEqual(text, f'Too Many Requests: /api/v1/tracking/{MASK}/')
        self.assertNotIn(TOKEN, text)

    def test_other_messages_are_left_alone(self):
        self.assertEqual(self._format('login_failed channel=%s ip=%s', 'web', '10.0.0.1'),
                         'login_failed channel=web ip=10.0.0.1')

    def test_every_console_record_goes_through_it(self):
        handler = settings.LOGGING['handlers']['console']
        self.assertIn('redact', handler.get('filters', []))
        self.assertEqual(settings.LOGGING['filters']['redact']['()'], 'backend.log_redaction.RedactingFilter')


class AccessLogTest(SimpleTestCase):
    def _atoms(self, raw_uri, referer='-'):
        from gunicorn.config import Config

        from backend.gunicorn_logging import RedactingLogger

        class Response:
            status = '200 OK'
            sent = 10
            headers = []

        class Request:
            headers = [('REFERER', referer)]

        path, _, query = raw_uri.partition('?')
        environ = {
            'REQUEST_METHOD': 'GET', 'RAW_URI': raw_uri, 'SERVER_PROTOCOL': 'HTTP/1.1',
            'PATH_INFO': path, 'QUERY_STRING': query, 'HTTP_REFERER': referer, 'REMOTE_ADDR': '10.0.0.1',
        }
        import datetime
        return RedactingLogger(Config()).atoms(Response(), Request(), environ, datetime.timedelta(seconds=1))

    def test_the_access_log_never_holds_a_tracking_code(self):
        atoms = self._atoms(f'/api/v1/tracking/{TOKEN}/?imei={IMEI}', f'https://tienda.example/seguimiento/{TOKEN}')
        line = ' '.join(str(atoms[key]) for key in ('r', 'U', 'q', 'f', '{referer}i'))
        self.assertNotIn(TOKEN, line)
        self.assertNotIn(IMEI, line)
        self.assertEqual(atoms['r'], f'GET /api/v1/tracking/{MASK}/?imei={MASK} HTTP/1.1')
        self.assertEqual(atoms['f'], f'https://tienda.example/seguimiento/{MASK}')

    def test_an_ordinary_request_is_logged_as_before(self):
        atoms = self._atoms('/api/products/?page=2')
        self.assertEqual(atoms['r'], 'GET /api/products/?page=2 HTTP/1.1')
        self.assertEqual(atoms['s'], '200')

    def test_production_starts_gunicorn_with_this_logger_and_no_control_socket(self):
        dockerfile = (Path(settings.BASE_DIR) / 'Dockerfile.prod').read_text(encoding='utf-8')
        self.assertIn('"--logger-class", "backend.gunicorn_logging.RedactingLogger"', dockerfile)
        # gunicorn 26 opens a control socket under /app, which the unprivileged
        # user cannot write: an ERROR on every start for a feature nobody uses.
        self.assertIn('"--no-control-socket"', dockerfile)
