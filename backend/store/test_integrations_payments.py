"""
INTEGRATIONS-CONSOLE · payments.

    a master configures one of Izipay's two products in the console
    → validates it in TEST → activates it
    → the next checkout charges through THAT product, with THOSE keys

and switching product, or rotating a key, is the same three steps and no code.

The gateways here are the contract fakes of the payment suites: they answer
what the published contract says and record what they were sent.
"""
import ast
import base64
import inspect
import textwrap

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient

from store import checkout_services as checkout
from store.integrations import payments as payment_adapters
from store.models import AdminAuditLog, IntegrationConfig, Order, PaymentTransaction
from store.payments.fake_izipay import FakeIzipay
from store.payments.fake_micuentaweb import FakeMiCuentaWeb
from store.test_micuentaweb import CHECKOUT_URL, McwBase
from store.tests import IZIPAY_TEST_SETTINGS

#: What the ENVIRONMENT holds in these tests: nothing usable. Whatever charges
#: here was configured in the console.
EMPTY_ENV = {
    'PAYMENT_PROVIDER': 'izipay', 'IZIPAY_ENV': 'sandbox', 'IZIPAY_MERCHANT_CODE': '', 'IZIPAY_PUBLIC_KEY': '',
    'IZIPAY_API_KEY': '', 'IZIPAY_HASH_KEY': '', 'IZIPAY_TOKEN_URL': '', 'IZIPAY_IPN_URL': '',
    'MICUENTAWEB_SHOP_ID': '', 'MICUENTAWEB_PASSWORD': '', 'MICUENTAWEB_PUBLIC_KEY': '', 'MICUENTAWEB_IPN_URL': '',
}
CHECKOUT_PUBLIC = {
    'environment': 'sandbox', 'merchant_code': '9001001', 'public_key': 'clave-publica-del-panel',
    'token_url': 'https://izipay.invalid/token', 'currency': 'PEN',
    'ipn_url': 'https://tienda.invalid/api/payments/izipay/notification/',
}
CHECKOUT_SECRETS = {'api_key': 'api-key-del-panel-NoEsReal', 'hash_key': 'hash-key-del-panel-NoEsReal'}
MCW_PUBLIC = {
    'shop_id': '90000001', 'public_key': '90000001:testpublickey_DelPanelNoEsRealNoEsReal',
    'api_url': 'https://api.micuentaweb.pe', 'currency': 'PEN',
    'ipn_url': 'https://tienda.invalid/api/payments/micuentaweb/notification/',
}
MCW_SECRETS = {'password': 'testpassword_DelPanelNoEsRealNoEsRealNoEsReal'}


def _without_docstring(module):
    function = module.body[0]
    if isinstance(function.body[0], ast.Expr) and isinstance(getattr(function.body[0], 'value', None), ast.Constant):
        function.body = function.body[1:] or [ast.Pass()]
    return module


def fake_checkout(public=CHECKOUT_PUBLIC, secrets=CHECKOUT_SECRETS):
    return FakeIzipay.from_settings({
        'IZIPAY_ENV': public['environment'], 'IZIPAY_MERCHANT_CODE': public['merchant_code'],
        'IZIPAY_PUBLIC_KEY': public['public_key'], 'IZIPAY_API_KEY': secrets['api_key'],
        'IZIPAY_HASH_KEY': secrets['hash_key'], 'IZIPAY_TOKEN_URL': public['token_url'], 'IZIPAY_CURRENCY': 'PEN',
    })


def fake_mcw(public=MCW_PUBLIC, secrets=MCW_SECRETS):
    return FakeMiCuentaWeb.from_settings({
        'MICUENTAWEB_SHOP_ID': public['shop_id'], 'MICUENTAWEB_PASSWORD': secrets['password'],
        'MICUENTAWEB_PUBLIC_KEY': public['public_key'], 'MICUENTAWEB_HMAC_KEY': 'x',
        'MICUENTAWEB_API_URL': public['api_url'], 'MICUENTAWEB_CURRENCY': 'PEN',
    })


@override_settings(APP_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode(), **EMPTY_ENV)
class _Base(McwBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.master = get_user_model().objects.create_superuser('master', 'master@example.com', 'x')
        self.console = APIClient()
        self.console.force_authenticate(self.master)

    def url(self, provider, action=''):
        return f'/api/admin/integrations/{provider}/' + (f'{action}/' if action else '')

    def save(self, provider, public, secrets):
        current = self.console.get(self.url(provider)).json()['draft']
        body = {'public': public, 'secrets': secrets}
        if current:
            body['version'] = current['version']
        return self.console.put(self.url(provider, 'draft'), body, format='json')

    def validate(self, provider, fake):
        with fake.online():
            return self.console.post(self.url(provider, 'test'), {}, format='json').json()

    def activate(self, provider, **extra):
        version = self.console.get(self.url(provider)).json()['draft']['version']
        return self.console.post(self.url(provider, 'activate'), {'version': version, **extra}, format='json')

    def go_live_with_checkout(self, public=CHECKOUT_PUBLIC, secrets=CHECKOUT_SECRETS):
        fake = fake_checkout(public, secrets)
        self.assertEqual(self.save('izipay_checkout', public, secrets).status_code, 200)
        self.assertEqual(self.validate('izipay_checkout', fake)['status'], 'ok')
        self.assertEqual(self.activate('izipay_checkout').status_code, 200)
        return fake

    def go_live_with_mcw(self, public=MCW_PUBLIC, secrets=MCW_SECRETS):
        fake = fake_mcw(public, secrets)
        self.assertEqual(self.save('izipay_micuentaweb', public, secrets).status_code, 200)
        self.assertEqual(self.validate('izipay_micuentaweb', fake)['status'], 'ok')
        self.assertEqual(self.activate('izipay_micuentaweb').status_code, 200)
        return fake

    def buy(self, fake):
        self.fake = fake
        return self.checkout()


class RuntimeTest(_Base):
    def test_with_nothing_configured_anywhere_the_checkout_says_so_and_creates_no_order(self):
        with self.assertLogs('store', level='ERROR'):
            self.assertEqual(self.buy(fake_checkout()).status_code, 500)
        self.assertFalse(Order.objects.exists())

    def test_the_checkout_charges_through_the_product_and_keys_of_the_console(self):
        fake = self.go_live_with_checkout()
        fake.requests.clear()

        response = self.buy(fake)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['provider'], 'izipay')
        self.assertEqual(response.data['merchant_code'], '9001001')
        self.assertEqual(response.data['public_key'], 'clave-publica-del-panel')
        sent = fake.requests[-1]
        self.assertEqual(sent['headers']['authorization'], CHECKOUT_SECRETS['api_key'])
        self.assertEqual(sent['body']['merchantCode'], '9001001')
        self.assertEqual(sent['body']['urlIPN'], CHECKOUT_PUBLIC['ipn_url'])
        self.assertEqual(fake.violations, [])
        self.assertEqual(PaymentTransaction.objects.get().provider, 'izipay')

    def test_the_console_wins_over_what_the_environment_says(self):
        with override_settings(**IZIPAY_TEST_SETTINGS):
            fake = self.go_live_with_checkout()
            response = self.buy(fake)
        self.assertEqual(response.data['merchant_code'], '9001001')
        self.assertNotEqual(IZIPAY_TEST_SETTINGS['IZIPAY_MERCHANT_CODE'], '9001001')

    def test_rotating_a_key_in_the_console_changes_the_next_charge_without_a_restart(self):
        self.go_live_with_checkout()
        rotated = {**CHECKOUT_SECRETS, 'api_key': 'api-key-NUEVA-NoEsReal'}
        fake = self.go_live_with_checkout(secrets=rotated)
        fake.requests.clear()
        self.assertEqual(self.buy(fake).status_code, 200)
        self.assertEqual(fake.requests[-1]['headers']['authorization'], 'api-key-NUEVA-NoEsReal')

    def test_a_notification_is_verified_with_the_hash_key_of_the_console(self):
        fake = self.go_live_with_checkout()
        self.assertEqual(self.buy(fake).status_code, 200)
        attempt = PaymentTransaction.objects.select_related('order').get()
        payload = fake.payload(transaction_id=attempt.transaction_id, order_number=attempt.order_number,
                               amount=attempt.amount)

        forged = self.client.post('/api/payments/izipay/notification/',
                                  fake.envelope(payload, signature=fake.sign(payload, hash_key='otra-clave')),
                                  format='json')
        self.assertEqual(forged.status_code, 400)
        attempt.order.refresh_from_db()
        self.assertFalse(attempt.order.paid)

        genuine = self.client.post('/api/payments/izipay/notification/', fake.envelope(payload), format='json')
        self.assertEqual(genuine.status_code, 200)
        attempt.order.refresh_from_db()
        self.assertTrue(attempt.order.paid)

    def test_a_disabled_provider_charges_nothing_and_does_not_fall_back_to_the_environment(self):
        with override_settings(**IZIPAY_TEST_SETTINGS):
            self.go_live_with_checkout()
            self.console.post(self.url('izipay_checkout', 'disable'), {}, format='json')
            self.assertEqual(checkout.active_payment_provider(), '')
            with self.assertLogs('store', level='ERROR'):
                self.assertEqual(self.buy(fake_checkout()).status_code, 500)


    def test_keys_this_server_cannot_read_charge_nothing_and_create_no_order(self):
        with override_settings(**IZIPAY_TEST_SETTINGS):
            self.go_live_with_checkout()
            with override_settings(APP_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode()), \
                    self.assertLogs('store', level='ERROR'):
                fake = fake_checkout()
                self.assertEqual(self.buy(fake).status_code, 500)
            self.assertEqual(fake.requests, [])
        self.assertFalse(Order.objects.exists())


class SwitchingTest(_Base):
    def test_a_master_switches_product_from_the_console(self):
        self.go_live_with_checkout()
        self.assertEqual(checkout.active_payment_provider(), 'izipay')

        fake = self.go_live_with_mcw()
        fake.requests.clear()

        self.assertEqual(checkout.active_payment_provider(), 'micuentaweb')
        response = self.buy(fake)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['provider'], 'micuentaweb')
        self.assertIn('form_token', response.data)
        self.assertNotIn('authorization', response.data)
        self.assertEqual(
            fake.requests[-1]['headers']['authorization'],
            'Basic ' + base64.b64encode(b'90000001:' + MCW_SECRETS['password'].encode()).decode())

    def test_only_one_product_is_ever_active(self):
        self.go_live_with_checkout()
        self.go_live_with_mcw()
        enabled = list(IntegrationConfig.objects.filter(slot='active', enabled=True).values_list('provider', flat=True))
        self.assertEqual(enabled, ['izipay_micuentaweb'])
        self.assertEqual(self.console.get(self.url('izipay_checkout')).json()['state'], 'DISABLED')
        replaced = AdminAuditLog.objects.filter(action='integration_disabled', target_id='izipay_checkout').get()
        self.assertEqual(replaced.metadata['reason'], 'replaced_by:izipay_micuentaweb')

    def test_the_notification_route_of_the_product_that_is_not_active_does_not_exist(self):
        self.go_live_with_checkout()
        self.go_live_with_mcw()
        gone = self.client.post('/api/payments/izipay/notification/', {'payloadHttp': '{}', 'signature': 'x'}, format='json')
        self.assertEqual(gone.status_code, 404)

    def test_the_other_product_cannot_be_switched_back_on_beside_the_active_one(self):
        self.go_live_with_checkout()
        self.go_live_with_mcw()
        response = self.console.post(self.url('izipay_checkout', 'enable'), {}, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(checkout.active_payment_provider(), 'micuentaweb')

    def test_the_checkout_reaches_the_gateways_only_through_the_adapter_registry(self):
        """The domain names no vendor: a third gateway is an adapter and its declaration."""
        for function in (checkout.require_payment_provider_configured, checkout.start_payment_attempt,
                         checkout.payment_session_payload, checkout.active_payment_provider):
            code = ast.unparse(_without_docstring(ast.parse(textwrap.dedent(inspect.getsource(function)))))
            for vendor in ('izipay', 'micuentaweb'):
                self.assertNotIn(vendor, code.lower(), function.__name__)
        self.assertEqual({a.code for a in payment_adapters._ADAPTERS.values()}, {'izipay', 'micuentaweb'})


class TestAndProductionTest(_Base):
    PROD_PUBLIC = {**CHECKOUT_PUBLIC, 'environment': 'production', 'token_url': 'https://api.izipay.invalid/token'}

    def test_the_console_says_which_environment_a_configuration_is(self):
        self.save('izipay_checkout', CHECKOUT_PUBLIC, CHECKOUT_SECRETS)
        self.assertEqual(self.console.get(self.url('izipay_checkout')).json()['draft']['mode'], 'test')
        self.save('izipay_micuentaweb', MCW_PUBLIC, MCW_SECRETS)
        self.assertEqual(self.console.get(self.url('izipay_micuentaweb')).json()['draft']['mode'], 'test')

    def test_production_keys_are_never_sent_anywhere_by_the_test(self):
        fake = fake_checkout(self.PROD_PUBLIC)
        self.save('izipay_checkout', self.PROD_PUBLIC, CHECKOUT_SECRETS)
        result = self.validate('izipay_checkout', fake)
        self.assertEqual(result['status'], 'ok')
        self.assertIn('PRODUCCIÓN', result['message'])
        self.assertEqual(fake.requests, [])

    def test_production_cannot_be_activated_by_accident(self):
        self.save('izipay_checkout', self.PROD_PUBLIC, CHECKOUT_SECRETS)
        self.validate('izipay_checkout', fake_checkout(self.PROD_PUBLIC))
        refused = self.activate('izipay_checkout')
        self.assertEqual(refused.status_code, 400)
        self.assertIn('confirm', refused.json()['errors'])
        self.assertEqual(checkout.active_payment_provider(), 'izipay')        # still the (empty) environment
        self.assertFalse(IntegrationConfig.objects.filter(slot='active').exists())
        self.assertEqual(self.activate('izipay_checkout', confirm='PRODUCCION').status_code, 200)
        self.assertEqual(self.console.get(self.url('izipay_checkout')).json()['active']['mode'], 'production')

    def test_production_pointed_at_a_sandbox_is_refused(self):
        response = self.save('izipay_checkout', {**CHECKOUT_PUBLIC, 'environment': 'production',
                                                 'token_url': 'https://sandbox-api.izipay.invalid/token'}, CHECKOUT_SECRETS)
        self.assertIn('token_url', response.json()['errors'])

    def test_mi_cuenta_web_keys_from_two_environments_are_refused(self):
        response = self.save('izipay_micuentaweb', MCW_PUBLIC, {'password': 'prodpassword_NoEsRealNoEsReal'})
        self.assertIn('password', response.json()['errors'])
        response = self.save('izipay_micuentaweb', {**MCW_PUBLIC, 'public_key': '11111111:testpublickey_x'}, MCW_SECRETS)
        self.assertIn('public_key', response.json()['errors'])


class ValidationTest(_Base):
    def test_credentials_the_gateway_refuses_are_invalid_and_not_activated(self):
        self.save('izipay_checkout', CHECKOUT_PUBLIC, {**CHECKOUT_SECRETS, 'api_key': 'api-key-equivocada'})
        result = self.validate('izipay_checkout', fake_checkout())       # the gateway knows another key
        self.assertEqual(result['status'], 'invalid')
        self.assertEqual(self.activate('izipay_checkout').status_code, 409)

        self.save('izipay_micuentaweb', MCW_PUBLIC, {'password': 'testpassword_OtraQueNoEsLaDelFalso'})
        self.assertEqual(self.validate('izipay_micuentaweb', fake_mcw())['status'], 'invalid')

    def test_a_gateway_that_cannot_be_reached_or_is_broken_is_unavailable_not_invalid(self):
        for product, public, secrets, fake, attribute in (
            ('izipay_checkout', CHECKOUT_PUBLIC, CHECKOUT_SECRETS, fake_checkout(), 'token_mode'),
            ('izipay_micuentaweb', MCW_PUBLIC, MCW_SECRETS, fake_mcw(), 'mode'),
        ):
            self.save(product, public, secrets)
            for mode in ('unreachable', 'timeout', 'http_error', 'not_json'):
                with self.subTest(product=product, mode=mode):
                    setattr(fake, attribute, mode)
                    with self.assertLogs('store', level='ERROR'):
                        self.assertEqual(self.validate(product, fake)['status'], 'unavailable')

    def test_incomplete_credentials_are_incomplete(self):
        self.save('izipay_checkout', CHECKOUT_PUBLIC, {'api_key': 'sólo-una'})
        self.assertEqual(self.validate('izipay_checkout', fake_checkout())['status'], 'incomplete')

    def test_the_validation_creates_no_order_and_no_payment(self):
        self.go_live_with_checkout()
        self.assertFalse(Order.objects.exists())
        self.assertFalse(PaymentTransaction.objects.exists())

    def test_no_secret_comes_back(self):
        self.go_live_with_checkout()
        self.go_live_with_mcw()
        text = self.console.get('/api/admin/integrations/').content.decode() \
            + self.console.get(self.url('izipay_checkout')).content.decode() \
            + self.console.get(self.url('izipay_micuentaweb')).content.decode()
        for secret in (*CHECKOUT_SECRETS.values(), *MCW_SECRETS.values()):
            self.assertNotIn(secret, text)


class LegacyEnvironmentTest(_Base):
    def test_an_installation_configured_by_environment_keeps_charging(self):
        with override_settings(**IZIPAY_TEST_SETTINGS):
            self.assertEqual(checkout.active_payment_provider(), 'izipay')
            response = self.buy(FakeIzipay.from_settings(IZIPAY_TEST_SETTINGS))
            self.assertEqual(response.status_code, 200, response.data)
            detail = self.console.get(self.url('izipay_checkout')).json()
        self.assertEqual((detail['state'], detail['source']), ('ACTIVE', 'env'))
        self.assertEqual(detail['env']['secrets'], ['api_key', 'hash_key'])
        self.assertNotIn(IZIPAY_TEST_SETTINGS['IZIPAY_API_KEY'], str(detail))

    def test_the_environment_names_one_product_and_only_that_one_shows_as_configured(self):
        with override_settings(**IZIPAY_TEST_SETTINGS):
            self.assertEqual(self.console.get(self.url('izipay_micuentaweb')).json()['state'], 'NOT_CONFIGURED')
