"""
ANALYTICS-MARKETING · a sale is measured once, and only when it is a sale.

    payment confirmed by the gateway's signed notification
      → one conversion per provider, written in the same transaction
      → sent after commit, retried from an outbox, never twice

What these tests pin:

  * the conversion is born where the order becomes paid — not when a browser
    opens the success page, not from a forged notification;
  * it respects what the buyer accepted when they started paying: analytics for
    Google, marketing for Meta and TikTok. Refused in the browser is refused on
    the server;
  * a provider that is down, refuses or crashes changes nothing about the
    payment, and its conversion is retried without being duplicated;
  * what is sent is the order — an id, amounts, products — and nothing about
    the person beyond the browser identifiers they consented to.

No request leaves this process: `urlopen` is simulated for the three providers,
and the gateway is the contract fake of the payment suites.
"""
import json
import urllib.error
from unittest import mock

from django.core.management import call_command
from django.test import override_settings
from rest_framework.test import APIClient

from store.models import CartItem, ConversionDelivery, MeasurementContext, Order, PaymentTransaction
from store.test_integrations_payments import _Base as _PaymentsBase
from store.test_measurement_providers import (
    GA, GA_SECRET, META, META_SECRET, TIKTOK, TIKTOK_SECRET, URLOPEN, http_error, reply,
)
from store.test_micuentaweb import CHECKOUT_URL

BROWSER = {
    'consent': {'analytics': True, 'marketing': True},
    'ga_client_id': '1234567890.1700000000', 'ga_session_id': '1700000123',
    'fbp': 'fb.1.1700000000000.1234567890', 'fbc': 'fb.1.1700000000000.AbCdEf123',
    'ttp': 'aBcDeFgHiJkLmNoPqRsT_uvw', 'ttclid': 'E.C.P.CmEKEAbCdEf0123456789',
}
BUYER = {
    'customer_name': 'Ana Torres Privada', 'customer_email': 'ana.privada@example.com',
    'customer_phone': '936449536', 'document_number': '87654321',
    'address_line': 'Calle Secreta 123', 'notes': 'Dejar con el portero',
}
USER_AGENT = 'Mozilla/5.0 (prueba) Navegador/1.0'
OKS = {'graph.facebook.com': {'events_received': 1}, 'business-api.tiktok.com': {'code': 0, 'message': 'OK'},
       'www.google-analytics.com': {}}


class _Base(_PaymentsBase):
    def setUp(self):
        super().setUp()
        self.gateway = self.go_live_with_checkout()
        self.sent = []
        self.answers = dict(OKS)

    # -- the three providers, in one fake --------------------------------------------
    def _urlopen(self, request, timeout=None):
        host = request.full_url.split('/')[2]
        if host not in self.answers:
            return self.gateway.urlopen(request, timeout=timeout)          # the payment gateway itself
        body = json.loads(request.data)
        self.sent.append({'host': host, 'url': request.full_url, 'body': body,
                          'headers': {k.lower(): v for k, v in request.header_items()}, 'timeout': timeout})
        answer = self.answers[host]
        if isinstance(answer, Exception):
            raise answer
        return reply(answer)

    def online(self):
        return mock.patch(URLOPEN, side_effect=self._urlopen)

    def to(self, host):
        return [call for call in self.sent if call['host'] == host]

    # -- the console ------------------------------------------------------------------
    def activate_provider(self, provider, public, secrets=None):
        current = self.console.get(self.url(provider)).json()['draft']
        body = {'public': public, 'secrets': secrets or {}, **({'version': current['version']} if current else {})}
        self.assertEqual(self.console.put(self.url(provider, 'draft'), body, format='json').status_code, 200)
        with mock.patch(URLOPEN, return_value=reply({'events_received': 1, 'code': 0, 'validationMessages': []})):
            self.console.post(self.url(provider, 'test'), {}, format='json')
        version = self.console.get(self.url(provider)).json()['draft']['version']
        response = self.console.post(self.url(provider, 'activate'), {'version': version}, format='json')
        self.assertEqual(response.status_code, 200, response.content)

    def all_server_side(self):
        self.activate_provider('google_analytics', GA, GA_SECRET)
        self.activate_provider('meta', META, META_SECRET)
        self.activate_provider('tiktok', TIKTOK, TIKTOK_SECRET)

    # -- the buyer --------------------------------------------------------------------
    def start_paying(self, measurement=BROWSER):
        body = {
            'session_key': self.session_key, **BUYER, 'document_type': 'dni',
            'delivery_method': 'delivery_arequipa', 'city': 'Arequipa', 'district': 'Cayma',
            'receipt_type': 'boleta', 'accepted_terms': True, 'accepted_warranty_policy': True,
        }
        if measurement is not None:
            body['measurement'] = measurement
        # A confirmed payment empties the cart: each purchase of a test starts with one.
        CartItem.objects.get_or_create(session_key=self.session_key, product=self.product, defaults={'quantity': 1})
        with self.online():
            response = self.client.post(CHECKOUT_URL, body, format='json', HTTP_USER_AGENT=USER_AGENT,
                                        REMOTE_ADDR='203.0.113.7')
        self.assertEqual(response.status_code, 200, response.data)
        return PaymentTransaction.objects.select_related('order').latest('pk')

    def notification(self, attempt, **changes):
        payload = self.gateway.payload(transaction_id=attempt.transaction_id, order_number=attempt.order_number,
                                       amount=attempt.amount)
        return self.gateway.envelope(payload, **changes)

    def gateway_confirms(self, attempt):
        with self.online(), self.captureOnCommitCallbacks(execute=True):
            response = self.client.post('/api/payments/izipay/notification/', self.notification(attempt), format='json')
        self.assertEqual(response.status_code, 200)
        attempt.order.refresh_from_db()
        return attempt.order

    def purchase(self, measurement=BROWSER):
        return self.gateway_confirms(self.start_paying(measurement))


class OneSaleOneConversionTest(_Base):
    def test_a_confirmed_payment_is_one_conversion_per_provider(self):
        self.all_server_side()
        order = self.purchase()

        self.assertTrue(order.paid)
        self.assertEqual({d.provider: d.status for d in ConversionDelivery.objects.filter(order=order)},
                         {'google_analytics': 'sent', 'meta': 'sent', 'tiktok': 'sent'})
        self.assertEqual([len(self.to(host)) for host in OKS], [1, 1, 1])

    def test_a_replayed_notification_is_not_a_second_conversion(self):
        self.all_server_side()
        attempt = self.start_paying()
        self.gateway_confirms(attempt)
        for _again in range(3):
            self.gateway_confirms(attempt)
        self.assertEqual(ConversionDelivery.objects.count(), 3)
        self.assertEqual([len(self.to(host)) for host in OKS], [1, 1, 1])

    def test_opening_the_success_page_is_not_a_purchase(self):
        self.all_server_side()
        attempt = self.start_paying()
        with self.online():
            for _poll in range(4):
                status = self.client.get(f'/api/payments/status/?reference={attempt.transaction_id}')
        self.assertEqual(status.json()['status'], 'pending_payment')
        self.assertNotIn('measurement', status.json())
        self.assertEqual(ConversionDelivery.objects.count(), 0)
        self.assertEqual(self.sent, [])

    def test_a_forged_notification_is_not_a_purchase(self):
        self.all_server_side()
        attempt = self.start_paying()
        forged = self.notification(attempt, signature=self.gateway.sign(
            self.gateway.payload(transaction_id=attempt.transaction_id, order_number=attempt.order_number,
                                 amount=attempt.amount), hash_key='la-clave-de-otro'))
        with self.online(), self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.post('/api/payments/izipay/notification/', forged, format='json').status_code, 400)
        self.assertEqual(ConversionDelivery.objects.count(), 0)
        self.assertEqual(self.sent, [])

    def test_the_browser_and_the_server_name_the_same_event(self):
        """Meta and TikTok discard the second of two events with the same name and id."""
        self.all_server_side()
        attempt = self.start_paying()
        order = self.gateway_confirms(attempt)
        told = self.client.get(f'/api/payments/status/?reference={attempt.transaction_id}').json()['measurement']

        self.assertEqual(told['event_id'], f'purchase.{order.pk}')
        self.assertEqual(self.to('graph.facebook.com')[0]['body']['data'][0]['event_id'], told['event_id'])
        self.assertEqual(self.to('business-api.tiktok.com')[0]['body']['data'][0]['event_id'], told['event_id'])
        self.assertEqual(self.to('www.google-analytics.com')[0]['body']['events'][0]['params']['transaction_id'],
                         told['transaction_id'])
        self.assertEqual((told['value'], told['currency'], told['items'][0]['quantity']), (float(order.total), 'PEN', 2))

    def test_a_pixel_only_provider_sends_nothing_from_the_server(self):
        self.activate_provider('meta', {'mode': 'pixel_only', 'pixel_id': META['pixel_id']})
        self.activate_provider('google_analytics', GA)                    # no API secret
        self.purchase()
        self.assertEqual(ConversionDelivery.objects.count(), 0)
        self.assertEqual(self.sent, [])


class WhatIsSentTest(_Base):
    def test_meta_receives_the_purchase_as_its_conversions_api_asks(self):
        self.activate_provider('meta', META, META_SECRET)
        order = self.purchase()
        [call] = self.to('graph.facebook.com')
        self.assertRegex(call['url'], r'^https://graph\.facebook\.com/v\d+\.\d+/123456789012345/events$')
        self.assertEqual(call['body']['access_token'], META_SECRET['access_token'])
        self.assertEqual(call['body']['test_event_code'], 'TEST12345')
        [event] = call['body']['data']
        self.assertEqual((event['event_name'], event['action_source']), ('Purchase', 'website'))
        self.assertEqual(event['event_time'], int(order.paid_at.timestamp()))
        self.assertEqual(event['user_data'], {
            'client_ip_address': '203.0.113.7', 'client_user_agent': USER_AGENT,
            'fbp': BROWSER['fbp'], 'fbc': BROWSER['fbc']})
        data = event['custom_data']
        self.assertEqual((data['currency'], data['value'], data['order_id']), ('PEN', float(order.total), str(order.pk)))
        self.assertEqual(data['contents'], [{'id': str(self.product.pk), 'quantity': 2, 'item_price': 149.0}])
        self.assertTrue(event['event_source_url'].endswith('/checkout/success'))

    def test_tiktok_receives_it_as_its_events_api_asks(self):
        self.activate_provider('tiktok', TIKTOK, TIKTOK_SECRET)
        order = self.purchase()
        [call] = self.to('business-api.tiktok.com')
        self.assertEqual(call['url'], 'https://business-api.tiktok.com/open_api/v1.3/event/track/')
        self.assertEqual(call['headers']['access-token'], TIKTOK_SECRET['access_token'])
        self.assertEqual((call['body']['event_source'], call['body']['event_source_id']), ('web', TIKTOK['pixel_code']))
        [event] = call['body']['data']
        self.assertEqual(event['event'], 'Purchase')
        self.assertEqual(event['user'], {'ip': '203.0.113.7', 'user_agent': USER_AGENT,
                                         'ttp': BROWSER['ttp'], 'ttclid': BROWSER['ttclid']})
        self.assertEqual((event['properties']['value'], event['properties']['currency']), (float(order.total), 'PEN'))
        self.assertEqual(event['properties']['contents'][0]['content_id'], str(self.product.pk))

    def test_google_receives_it_as_the_measurement_protocol_asks(self):
        self.activate_provider('google_analytics', GA, GA_SECRET)
        order = self.purchase()
        [call] = self.to('www.google-analytics.com')
        self.assertTrue(call['url'].startswith('https://www.google-analytics.com/mp/collect?'))
        self.assertIn('measurement_id=G-NOESREAL01', call['url'])
        self.assertEqual(call['body']['client_id'], BROWSER['ga_client_id'])
        [event] = call['body']['events']
        self.assertEqual(event['name'], 'purchase')
        params = event['params']
        self.assertEqual((params['transaction_id'], params['currency'], params['value']),
                         (str(order.pk), 'PEN', float(order.total)))
        self.assertEqual(params['session_id'], BROWSER['ga_session_id'])
        self.assertEqual(params['items'][0], {
            'item_id': str(self.product.pk), 'item_name': 'Contrato MCW', 'price': 149.0, 'quantity': 2})
        self.assertEqual(params['tax'], float(order.tax_amount))

    def test_nothing_about_the_person_is_sent(self):
        self.all_server_side()
        order = self.purchase()
        everything = json.dumps(self.sent, ensure_ascii=False)
        for private in (*BUYER.values(), 'Ana', 'Torres', 'Cayma', order.customer_email, order.customer_phone):
            self.assertNotIn(private, everything)
        for key in ('em', 'ph', 'fn', 'ln', 'email', 'phone', 'external_id', 'user_id'):
            self.assertNotIn(f'"{key}"', everything)

    def test_identifiers_that_are_not_what_a_provider_issues_are_dropped(self):
        self.all_server_side()
        hostile = {**BROWSER, 'fbp': '<script>alert(1)</script>', 'fbc': 'x' * 3000, 'ttp': {'$ne': 1},
                   'ga_client_id': "1'; DROP TABLE--", 'ttclid': 'https://evil.example/?t=1', 'extra': 'no se guarda'}
        order = self.purchase(hostile)
        self.assertTrue(order.paid)
        everything = json.dumps(self.sent)
        for value in ('script', 'DROP', 'evil.example', 'xxxxxxxx', 'no se guarda'):
            self.assertNotIn(value, everything)
        self.assertEqual(self.to('graph.facebook.com')[0]['body']['data'][0]['user_data'],
                         {'client_ip_address': '203.0.113.7', 'client_user_agent': USER_AGENT})

    def test_what_identified_the_browser_is_not_kept_once_it_was_used(self):
        self.all_server_side()
        self.purchase()
        self.assertFalse(MeasurementContext.objects.exists())


class ConsentTest(_Base):
    def test_nothing_was_accepted_so_nothing_is_stored_and_nothing_is_sent(self):
        self.all_server_side()
        identifiers = {k: v for k, v in BROWSER.items() if k != 'consent'}
        for measurement in (
            None,                                                                       # an old page, or no banner answered
            {'consent': {'analytics': False, 'marketing': False}, **identifiers},       # refused, identifiers sent anyway
            {'consent': 'yes', **identifiers},                                          # not the shape of a consent
            {'consent': {'analytics': 'true', 'marketing': 1}, **identifiers},          # truthy is not `true`
        ):
            order = self.purchase(measurement)
            self.assertTrue(order.paid)
        self.assertFalse(MeasurementContext.objects.exists())
        self.assertFalse(ConversionDelivery.objects.exists())
        self.assertEqual(self.sent, [])

    def test_analytics_only_reaches_google_and_nobody_else(self):
        self.all_server_side()
        self.purchase({**BROWSER, 'consent': {'analytics': True, 'marketing': False}})
        self.assertEqual([len(self.to(host)) for host in OKS], [0, 0, 1])
        # Not «planned and then skipped»: a conversion the buyer refused is never written down.
        self.assertEqual(list(ConversionDelivery.objects.values_list('provider', 'status')), [('google_analytics', 'sent')])
        # …and Google is told that this person refused the advertising uses.
        self.assertEqual(self.to('www.google-analytics.com')[0]['body']['consent'],
                         {'ad_user_data': 'DENIED', 'ad_personalization': 'DENIED'})

    def test_marketing_only_reaches_meta_and_tiktok_and_not_google(self):
        self.all_server_side()
        self.purchase({**BROWSER, 'consent': {'analytics': False, 'marketing': True}})
        self.assertEqual([len(self.to(host)) for host in OKS], [1, 1, 0])
        self.assertEqual(sorted(ConversionDelivery.objects.values_list('provider', flat=True)), ['meta', 'tiktok'])

    def test_without_marketing_consent_the_address_and_the_browser_are_not_even_stored(self):
        self.all_server_side()
        self.start_paying({**BROWSER, 'consent': {'analytics': True, 'marketing': False}})
        context = MeasurementContext.objects.get()
        self.assertEqual((context.ip, context.user_agent, context.fbp, context.ttp, context.ttclid), (None, '', '', '', ''))
        self.assertEqual(context.ga_client_id, BROWSER['ga_client_id'])


class NeverInTheWayTest(_Base):
    def test_a_provider_that_is_down_does_not_touch_the_payment_and_is_retried_once(self):
        self.all_server_side()
        self.answers['graph.facebook.com'] = urllib.error.URLError('sin red')
        order = self.purchase()

        self.assertTrue(order.paid)
        pending = ConversionDelivery.objects.get(provider='meta')
        self.assertEqual((pending.status, pending.attempt_count), ('pending', 1))
        self.assertIsNotNone(pending.next_attempt_at)
        self.assertTrue(MeasurementContext.objects.exists())               # still needed for the retry

        self.answers['graph.facebook.com'] = {'events_received': 1}
        ConversionDelivery.objects.update(next_attempt_at=None)
        with self.online():
            call_command('send_pending_conversions')
            call_command('send_pending_conversions')
        pending.refresh_from_db()
        self.assertEqual(pending.status, 'sent')
        self.assertEqual(len(self.to('graph.facebook.com')), 2)            # the failure, and ONE success
        self.assertEqual([len(self.to(host)) for host in ('business-api.tiktok.com', 'www.google-analytics.com')], [1, 1])
        self.assertFalse(MeasurementContext.objects.exists())

    def test_a_retry_carries_the_same_event_id(self):
        self.activate_provider('meta', META, META_SECRET)
        self.answers['graph.facebook.com'] = http_error(503, {})
        self.purchase()
        self.answers['graph.facebook.com'] = {'events_received': 1}
        ConversionDelivery.objects.update(next_attempt_at=None)
        with self.online():
            call_command('send_pending_conversions')
        first, second = (call['body']['data'][0]['event_id'] for call in self.to('graph.facebook.com'))
        self.assertEqual(first, second)

    def test_a_refused_token_is_final_and_says_what_kind_of_failure_it_was(self):
        self.activate_provider('meta', META, META_SECRET)
        self.answers['graph.facebook.com'] = http_error(400, {'error': {'code': 190, 'message': f'Bad token {META_SECRET["access_token"]}'}})
        order = self.purchase()
        self.assertTrue(order.paid)
        failed = ConversionDelivery.objects.get()
        self.assertEqual((failed.status, failed.failure_kind, failed.next_attempt_at), ('failed', 'auth_failed', None))
        with self.online():
            call_command('send_pending_conversions')
        self.assertEqual(len(self.to('graph.facebook.com')), 1)

    def test_an_adapter_that_crashes_does_not_touch_the_payment(self):
        self.all_server_side()
        with mock.patch('store.measurement.adapters.meta_send', side_effect=RuntimeError('boom')), \
                self.assertLogs('store.measurement', level='ERROR'):
            order = self.purchase()
        self.assertTrue(order.paid)
        self.assertEqual(ConversionDelivery.objects.get(provider='tiktok').status, 'sent')

    def test_measurement_failing_inside_the_payment_does_not_undo_the_payment(self):
        self.all_server_side()
        attempt = self.start_paying()
        with mock.patch('store.measurement.conversions._plan', side_effect=RuntimeError('boom')), \
                self.assertLogs('store.measurement', level='ERROR'):
            order = self.gateway_confirms(attempt)
        self.assertTrue(order.paid)
        self.assertEqual(PaymentTransaction.objects.get(pk=attempt.pk).status, PaymentTransaction.Status.AUTHORIZED)

    def test_a_bad_measurement_block_does_not_refuse_the_checkout(self):
        for garbage in ('x' * 50_000, {'consent': {'analytics': True}, 'ga_client_id': ['a'] * 1000}, 7, {}, 'granted', ['x']):
            attempt = self.start_paying(garbage)
            self.assertEqual(attempt.order.status, Order.Status.PENDING_PAYMENT)
        # The one that did carry a consent kept it, and none of what was not an identifier.
        [context] = MeasurementContext.objects.all()
        self.assertEqual((context.analytics, context.marketing, context.ga_client_id), (True, False, ''))

    def test_no_log_line_carries_a_token_or_a_browser_identifier(self):
        self.all_server_side()
        self.answers['graph.facebook.com'] = http_error(400, {'error': {'code': 190, 'message': META_SECRET['access_token']}})
        self.answers['business-api.tiktok.com'] = urllib.error.URLError(f'refused {TIKTOK_SECRET["access_token"]}')
        with self.assertLogs('store', level='DEBUG') as captured:
            self.purchase()
        text = '\n'.join(captured.output)
        for private in (*META_SECRET.values(), *TIKTOK_SECRET.values(), *GA_SECRET.values(), BROWSER['fbp'], BROWSER['ttclid'],
                        '203.0.113.7', BUYER['customer_email']):
            self.assertNotIn(private, text)


class RuntimeTest(_Base):
    def test_changing_the_token_in_the_console_changes_the_next_conversion(self):
        self.activate_provider('tiktok', TIKTOK, TIKTOK_SECRET)
        self.activate_provider('tiktok', TIKTOK, {'access_token': 'tiktok-token-NUEVO-NoEsReal'})
        self.purchase()
        self.assertEqual(self.to('business-api.tiktok.com')[0]['headers']['access-token'], 'tiktok-token-NUEVO-NoEsReal')

    def test_a_provider_switched_off_between_the_payment_and_the_retry_is_not_sent_to(self):
        self.activate_provider('meta', META, META_SECRET)
        self.answers['graph.facebook.com'] = http_error(503, {})
        self.purchase()
        self.console.post(self.url('meta', 'disable'), {}, format='json')
        ConversionDelivery.objects.update(next_attempt_at=None)
        self.answers['graph.facebook.com'] = {'events_received': 1}
        with self.online():
            call_command('send_pending_conversions')
        self.assertEqual(len(self.to('graph.facebook.com')), 1)
        self.assertEqual(ConversionDelivery.objects.get().status, 'skipped')

    def test_a_disabled_provider_gets_no_conversion(self):
        self.all_server_side()
        self.console.post(self.url('meta', 'disable'), {}, format='json')
        self.purchase()
        self.assertEqual(sorted(ConversionDelivery.objects.values_list('provider', flat=True)), ['google_analytics', 'tiktok'])

    @override_settings(MEASUREMENT_SEND_INLINE=False)
    def test_where_a_timer_sends_them_the_payment_request_sends_none(self):
        self.all_server_side()
        self.purchase()
        self.assertEqual(self.sent, [])
        self.assertEqual(set(ConversionDelivery.objects.values_list('status', flat=True)), {'pending'})
        with self.online():
            call_command('send_pending_conversions')
        self.assertEqual(set(ConversionDelivery.objects.values_list('status', flat=True)), {'sent'})
