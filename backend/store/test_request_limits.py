"""
BODY-LIMIT: how large a request may be, decided once and enforced three times.

    reverse proxy (deploy/Caddyfile)  →  Django (this middleware)  →  the view

The view knows the rule it can explain ("la imagen pesa más de 8 MB"). The two
layers in front of it exist so that a body nobody could want is refused before it
is read: Django parses a multipart upload to disk, and a JSON body into memory,
without any limit of its own.

These tests pin the table, the middleware, and that the proxy file says the same.
"""
import io
import re
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase
from django.urls import get_resolver, reverse
from rest_framework.views import APIView

from store import evidence_images, import_media, request_limits, storefront_media, xlsx_reader
from store.views import MAX_NOTIFICATION_BYTES

KB = 1024
MB = 1024 * KB
CADDYFILE = Path(settings.BASE_DIR).parent / 'deploy' / 'Caddyfile'


class _Unreadable(io.BytesIO):
    """A request body that fails the test if anything reads it."""

    def read(self, *args, **kwargs):  # pragma: no cover - reaching it is the failure
        raise AssertionError('the body was read before the size was refused')


def _call(path, declared, *, method='POST'):
    """Run the middleware alone. Returns (response, whether the view was reached)."""
    reached = []

    def view(request):
        reached.append(True)
        return HttpResponse('ok')

    request = RequestFactory().generic(method, path, data=b'', content_type='application/json')
    request.META['CONTENT_LENGTH'] = declared
    request._stream = _Unreadable()
    response = request_limits.RequestBodyLimitMiddleware(view)(request)
    return response, bool(reached)


class LimitTableTest(SimpleTestCase):
    def test_a_route_nobody_listed_gets_the_small_default(self):
        self.assertEqual(request_limits.default_max_bytes(), 1 * MB)
        for path in ('/api/auth/login/', '/api/auth/login', '/api/cart/add/',
                     '/api/v1/internal/acme/pos/sales/'):
            self.assertEqual(request_limits.limit_for(path), 1 * MB, path)

    def test_each_upload_route_admits_what_its_view_accepts_plus_the_envelope(self):
        slack = request_limits.MULTIPART_SLACK
        image = storefront_media.max_upload_bytes() + slack
        evidence = evidence_images.max_upload_bytes() + slack
        workbook = xlsx_reader.MAX_UPLOAD_BYTES + slack
        bulk = xlsx_reader.MAX_UPLOAD_BYTES + import_media.max_total_bytes() + slack
        cases = {
            reverse('admin-storefront-images'): image,
            reverse('admin-product-images', args=[7]): image,
            reverse('v1-internal-service-evidence', args=['acme', 7]): evidence,
            reverse('admin-import-inspect'): workbook,
            reverse('admin-stock-import-preview'): workbook,
            reverse('admin-unit-import-preview'): workbook,
            reverse('admin-product-import-preview'): bulk,
        }
        for path, expected in cases.items():
            self.assertEqual(request_limits.limit_for(path), expected, path)
            # Caddy sees the path before it adds the trailing slash.
            self.assertEqual(request_limits.limit_for(path.rstrip('/')), expected, path)

    def test_the_defaults_are_the_documented_sizes(self):
        self.assertEqual(request_limits.limit_for(reverse('admin-storefront-images')), 9 * MB)
        self.assertEqual(
            request_limits.limit_for(reverse('v1-internal-service-evidence', args=['acme', 7])),
            26 * MB,
        )
        self.assertEqual(request_limits.limit_for(reverse('admin-import-inspect')), 11 * MB)
        self.assertEqual(request_limits.limit_for(reverse('admin-product-import-preview')), 111 * MB)

    def test_notifications_from_outside_have_their_own_size(self):
        for name in ('izipay-notification', 'micuentaweb-notification'):
            self.assertEqual(request_limits.limit_for(reverse(name)), MAX_NOTIFICATION_BYTES)
        self.assertEqual(MAX_NOTIFICATION_BYTES, 128 * KB)
        self.assertEqual(
            request_limits.limit_for(reverse('v1-webhook-whatsapp', args=['acme'])), 3 * MB,
        )

    def test_a_neighbouring_route_does_not_inherit_an_upload_size(self):
        default = request_limits.default_max_bytes()
        for path in (
            reverse('admin-product-images-order', args=[7]),
            reverse('admin-product-image', args=[7, 3]),
            reverse('v1-internal-service-evidence-detail', args=['acme', 7, 3]),
            reverse('admin-product-import-apply', args=[7]),
            reverse('admin-unit-import-apply', args=[7]),
            '/api/admin/storefront/images/extra/',
            '/x/api/admin/storefront/images/',
        ):
            self.assertEqual(request_limits.limit_for(path), default, path)

    def test_what_django_holds_in_memory_covers_the_largest_json_body(self):
        largest_unparsed = max(
            request_limits.default_max_bytes(),
            request_limits.limit_for(reverse('v1-webhook-whatsapp', args=['acme'])),
        )
        self.assertGreaterEqual(settings.DATA_UPLOAD_MAX_MEMORY_SIZE, largest_unparsed)

    def test_every_view_that_takes_files_has_a_tier(self):
        """
        A new upload endpoint without a tier would be capped at the default and
        fail in production only. Views that restrict their parsers to multipart,
        plus the one that reads `request.FILES` with the default parsers, must
        each be reachable through a route above the default.
        """
        def walk(patterns, prefix=''):
            for entry in patterns:
                if hasattr(entry, 'url_patterns'):
                    yield from walk(entry.url_patterns, prefix + str(entry.pattern))
                else:
                    yield prefix + str(entry.pattern), entry

        default_parsers = list(APIView.parser_classes)
        takes_files = set()
        for _, entry in walk(get_resolver().url_patterns):
            cls = getattr(entry.callback, 'view_class', None) or getattr(entry.callback, 'cls', None)
            if cls is not None and list(getattr(cls, 'parser_classes', default_parsers)) != default_parsers:
                takes_files.add(cls.__name__)
        self.assertEqual(takes_files, {
            'AdminStorefrontImageUploadView',
            'AdminProductImageListView', 'AdminProductImageOrderView', 'AdminProductImageDetailView',
            'AdminImportInspectView', 'AdminProductImportPreviewView',
            'AdminStockImportPreviewView', 'AdminUnitImportPreviewView',
        }, 'a view changed its parsers: give its upload route a tier in request_limits')


class MiddlewareTest(SimpleTestCase):
    def test_a_body_over_the_limit_is_refused_before_it_is_read(self):
        response, reached = _call('/api/auth/login/', str(1 * MB + 1))
        self.assertEqual(response.status_code, 413)
        self.assertFalse(reached)
        self.assertEqual(response['Content-Type'], 'application/json')
        self.assertIn('demasiado grande', response.content.decode())

    def test_a_body_at_the_limit_goes_through(self):
        response, reached = _call('/api/auth/login/', str(1 * MB))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(reached)

    def test_an_upload_route_takes_its_own_size_and_no_more(self):
        path = reverse('admin-product-import-preview')
        self.assertTrue(_call(path, str(100 * MB))[1])
        response, reached = _call(path, str(200 * MB))
        self.assertEqual(response.status_code, 413)
        self.assertFalse(reached)

    def test_a_length_that_is_not_a_number_is_a_bad_request(self):
        for declared in ('abc', '-1', '1e9'):
            response, reached = _call('/api/auth/login/', declared)
            self.assertEqual(response.status_code, 400, declared)
            self.assertFalse(reached)

    def test_requests_without_a_body_are_not_touched(self):
        request = RequestFactory().get('/api/products/')
        response = request_limits.RequestBodyLimitMiddleware(lambda r: HttpResponse('ok'))(request)
        self.assertEqual(response.status_code, 200)

    def test_it_is_installed_and_answers_on_the_real_stack(self):
        self.assertIn('store.request_limits.RequestBodyLimitMiddleware', settings.MIDDLEWARE)
        response = self.client.post(
            '/api/auth/login/', data=b'{}', content_type='application/json',
            CONTENT_LENGTH=str(50 * MB),
        )
        self.assertEqual(response.status_code, 413)


@skipUnless(CADDYFILE.exists(), 'deploy/Caddyfile is not part of this checkout')
class ProxyAgreesTest(SimpleTestCase):
    """
    The proxy file cannot import Python, so the same numbers are written twice.
    This is the check that they are the same numbers.
    """

    UNITS = {'KiB': KB, 'MiB': MB}

    def _proxy_table(self):
        text = CADDYFILE.read_text(encoding='utf-8')
        matchers = dict(re.findall(r'^\s*@(limit_\w+) path_regexp (\S+)\s*$', text, re.M))
        sizes = {}
        for name, amount, unit in re.findall(
            r'handle @(limit_\w+) \{\s*import too_large \w+ \d+\s*request_body \{\s*max_size (\d+)(KiB|MiB)', text,
        ):
            sizes[name] = int(amount) * self.UNITS[unit]
        return matchers, sizes

    def test_the_proxy_has_the_same_tiers_with_the_same_sizes(self):
        matchers, sizes = self._proxy_table()
        expected = {f'limit_{tier.name}': tier for tier in request_limits.tiers()}
        self.assertEqual(set(matchers), set(expected))
        self.assertEqual(set(sizes), set(expected))
        for name, tier in expected.items():
            self.assertEqual(matchers[name], tier.pattern, name)
            self.assertEqual(sizes[name], tier.max_bytes, name)

    def test_everything_else_gets_the_default_on_both_applications(self):
        text = CADDYFILE.read_text(encoding='utf-8')
        fallbacks = re.findall(r'handle \{\s*import too_large \w+ \d+\s*request_body \{\s*max_size (\d+)(KiB|MiB)', text)
        # One fallback for the API, one for the pages served by Next.
        self.assertEqual(len(fallbacks), 2)
        for amount, unit in fallbacks:
            self.assertEqual(int(amount) * self.UNITS[unit], request_limits.default_max_bytes())

    def test_the_proxy_refuses_on_the_announced_length_itself(self):
        """
        BODY-LIMIT-502. Django refuses an oversized request on its declared
        length and closes without reading it; the proxy, still sending the body,
        then answered 502 instead of 413 about one time in ten. The proxy now
        compares the announced length itself, with the same numbers, and never
        calls an application for a request it can already refuse.
        """
        text = CADDYFILE.read_text(encoding='utf-8')
        declared = {name: int(size) for name, size in re.findall(r'(?m)^\s*import too_large (\w+) (\d+)\s*$', text)}
        expected = {tier.name: tier.max_bytes for tier in request_limits.tiers()}
        expected['api'] = expected['pages'] = request_limits.default_max_bytes()
        self.assertEqual(declared, expected)
        self.assertEqual(len(declared), text.count('reverse_proxy '))
        snippet = re.search(r'\(too_large\) \{(.*?)\n\}', text, re.S).group(1)
        self.assertIn('int({header.Content-Length}) > {args[1]}', snippet)
        self.assertRegex(snippet, r'respond @over_\{args\[0\]\} .* 413')

    def test_no_request_reaches_an_application_without_a_limit(self):
        text = CADDYFILE.read_text(encoding='utf-8')
        self.assertEqual(text.count('reverse_proxy '), text.count('max_size '))

    def test_the_proxy_does_not_wait_forever_for_a_request(self):
        """
        PROXY-TIMEOUTS. Without them a client that opens a connection and sends
        nothing, or sends one byte a minute, keeps it for as long as it likes.
        """
        text = CADDYFILE.read_text(encoding='utf-8')
        block = re.search(r'timeouts \{(.*?)\}', text, re.S)
        self.assertIsNotNone(block, 'the proxy file sets no timeouts')
        for name in ('read_header', 'read_body', 'idle'):
            self.assertRegex(block.group(1), rf'\b{name} \d+[sm]\b')

    def _proxy_blocks(self):
        """{matcher name or 'default': the text of its handle block}, API side only."""
        text = CADDYFILE.read_text(encoding='utf-8')
        api = text[text.index('handle @api {'):text.index('www.{$SITE_DOMAIN}')]
        blocks = dict(re.findall(r'handle @(limit_\w+) \{(.*?)\n\t\t\}', api, re.S))
        blocks['default'] = re.search(r'\n\t\thandle \{(.*?)\n\t\t\}', api, re.S).group(1)
        return blocks

    def test_the_proxy_reads_a_body_whole_wherever_django_would_read_it_without_a_session(self):
        """
        SLOW-BODY. Django answers with eight threads. A body that trickles in
        holds one of them for as long as it lasts, and eight such requests leave
        the API without threads. Wherever a body is read before anybody has
        proved who they are — every ordinary route, and the two that take calls
        from outside: the payment notification and the WhatsApp webhook — the
        proxy reads it first, so the application only ever receives a request
        that has finished arriving.
        """
        blocks = self._proxy_blocks()
        sizes = {1024: 'KiB', 1024 * 1024: 'MiB'}

        def written(max_bytes):
            unit = 1024 * 1024 if max_bytes % (1024 * 1024) == 0 else 1024
            return f'{max_bytes // unit}{sizes[unit]}'

        self.assertRegex(blocks['default'], r'\brequest_buffers 1MiB\b')
        open_to_anyone = {tier.name for tier in request_limits.tiers() if tier.read_without_session}
        self.assertEqual(open_to_anyone, {'payment_notification', 'whatsapp_webhook'})
        for tier in request_limits.tiers():
            block = blocks[f'limit_{tier.name}']
            if tier.read_without_session:
                self.assertRegex(block, rf'\brequest_buffers {written(tier.max_bytes)}\b', tier.name)
            else:
                # An upload is large; Django does not read it until the session,
                # the CSRF header and the permission have been checked.
                self.assertNotIn('request_buffers', block, tier.name)

    def test_what_the_proxy_itself_logs_carries_no_address(self):
        """
        LOG-REDACT, at the edge. When Caddy cannot reach an application it logs
        the request it was serving, with its full address and its referer: the
        tracking link, or what somebody typed into a search box.
        """
        text = CADDYFILE.read_text(encoding='utf-8')
        log = re.search(r'\n\tlog default \{(.*?)\n\t\}', text, re.S)
        self.assertIsNotNone(log, 'the proxy file does not configure its log')
        self.assertRegex(log.group(1), r'request>uri\s+delete')
        self.assertRegex(log.group(1), r'request>headers>Referer\s+delete')
