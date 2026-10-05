"""
MI CUENTA WEB — el flujo que documenta Izipay en su API REST V4.

  https://secure.micuentaweb.pe/doc/es-PE/rest/V4.0/javascript/redirection/presentation.html

Es OTRO producto oficial de Izipay, distinto del «SDK web / Checkout» que la
plataforma ya integraba: otras credenciales, otro guion en el navegador, otra
forma de crear el pago y otra firma. Vive detrás de `PAYMENT_PROVIDER`, y una
instalación usa UNO: nunca los dos a la vez.

Lo que no cambia, con un proveedor o con el otro:

  * la tarjeta nunca pasa por este backend;
  * lo que diga el navegador no marca nada como pagado;
  * un pedido se paga cuando llega la notificación del servidor de la pasarela,
    firmada, y coincide con lo que la base ya sabía;
  * la misma notificación dos veces paga una vez.

Sin credenciales: al otro lado del socket hay una pasarela falsa que comprueba
el contrato publicado. La única prueba que habla con el entorno real
(`MiCuentaWebSandboxSmokeTest`) se omite sin ellas: BLOCKED/CREDENTIALS.
"""
import base64
import json
import os
import unittest
from decimal import Decimal

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from store.models import CartItem, Order, PaymentTransaction, Product, StockMovement
from store.payments import micuentaweb
from store.payments.fake_micuentaweb import FakeMiCuentaWeb
from store.tests import (
    IZIPAY_TEST_SETTINGS, M5CheckoutBase, _m5_checkout_url, _pilot_company, _seeded,
)

CHECKOUT_URL = '/api/payments/create-checkout-session/'
IPN_URL = '/api/payments/micuentaweb/notification/'

MCW_SETTINGS = {
    'PAYMENT_PROVIDER': 'micuentaweb',
    'MICUENTAWEB_SHOP_ID': '69876357',
    'MICUENTAWEB_PASSWORD': 'testpassword_NoEsRealNoEsRealNoEsRealNoEsRealNoEsRe',
    'MICUENTAWEB_PUBLIC_KEY': '69876357:testpublickey_NoEsRealNoEsRealNoEsRealNoEsReal',
    'MICUENTAWEB_HMAC_KEY': 'hmac-no-es-real',
    'MICUENTAWEB_API_URL': 'https://api.micuentaweb.pe',
    'MICUENTAWEB_CURRENCY': 'PEN',
    'MICUENTAWEB_IPN_URL': 'https://tienda.invalid/api/payments/micuentaweb/notification/',
}


class AmountTest(SimpleTestCase):
    def test_money_travels_as_an_integer_in_the_smallest_unit(self):
        self.assertEqual(micuentaweb.amount_in_cents(Decimal('1.80')), 180)
        self.assertEqual(micuentaweb.amount_in_cents(Decimal('149.90')), 14990)
        self.assertEqual(micuentaweb.amount_in_cents(Decimal('4000')), 400000)
        self.assertIsInstance(micuentaweb.amount_in_cents(Decimal('0.10')), int)

    def test_the_signature_is_the_documented_hex_hmac(self):
        # Vector calculado a mano con la biblioteca estándar, no con el adaptador.
        import hashlib
        import hmac as _hmac
        expected = _hmac.new(b'clave', '{"a":1}'.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(micuentaweb.sign('{"a":1}', 'clave'), expected)
        self.assertTrue(micuentaweb.verify_signature('{"a":1}', expected, 'clave'))
        self.assertFalse(micuentaweb.verify_signature('{"a":1}', expected, ''))
        self.assertFalse(micuentaweb.verify_signature('{"a":2}', expected, 'clave'))
        self.assertFalse(micuentaweb.verify_signature('{"a":1}', 'ñandú', 'clave'))


@override_settings(**MCW_SETTINGS)
class McwBase(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.fake = FakeMiCuentaWeb.from_settings(MCW_SETTINGS)
        self.product = _seeded(Product.objects.create(
            company=_pilot_company(), name='Contrato MCW', slug='contrato-mcw',
            price=Decimal('149.00'), inventory=5,
        ))
        self.session_key = 'contrato-mcw-0001'
        CartItem.objects.create(session_key=self.session_key, product=self.product, quantity=2)

    def checkout(self):
        with self.fake.online():
            return self.client.post(CHECKOUT_URL, {
                'session_key': self.session_key,
                'customer_name': 'Ana Torres', 'customer_email': 'ana@example.com',
                'customer_phone': '936449536', 'document_type': 'dni', 'document_number': '12345678',
                'delivery_method': 'pickup_store', 'receipt_type': 'boleta',
                'accepted_terms': True, 'accepted_warranty_policy': True,
            }, format='json')

    def pending(self):
        response = self.checkout()
        self.assertEqual(response.status_code, 200, response.data)
        return PaymentTransaction.objects.select_related('order').get()

    def notify(self, form):
        """Como lo envía la pasarela: un formulario `application/x-www-form-urlencoded`, sin sesión."""
        from urllib.parse import urlencode
        return self.client.post(
            IPN_URL, data=urlencode(form), content_type='application/x-www-form-urlencoded')

    def stock(self):
        self.product.refresh_from_db()
        return self.product.inventory


class CreatePaymentContractTest(McwBase):
    def test_the_checkout_creates_the_payment_as_the_documentation_says(self):
        response = self.checkout()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.fake.violations, [])
        sent = self.fake.requests[0]
        attempt = PaymentTransaction.objects.get()
        self.assertEqual(sent['url'], 'https://api.micuentaweb.pe/api-payment/V4/Charge/CreatePayment')
        self.assertEqual(
            sent['headers']['authorization'],
            'Basic ' + base64.b64encode(b'69876357:' + MCW_SETTINGS['MICUENTAWEB_PASSWORD'].encode()).decode())
        self.assertEqual(sent['body']['amount'], 29800)                 # 2 × 149.00, en céntimos
        self.assertEqual(sent['body']['currency'], 'PEN')
        self.assertEqual(sent['body']['orderId'], attempt.transaction_id)
        self.assertEqual(sent['body']['customer']['email'], 'ana@example.com')
        self.assertEqual(sent['body']['ipnTargetUrl'], MCW_SETTINGS['MICUENTAWEB_IPN_URL'])
        self.assertLessEqual(sent['timeout'], 15)
        # Lo que se pidió es lo que quedó escrito ANTES de pedirlo.
        self.assertEqual(attempt.provider, 'micuentaweb')
        self.assertEqual(attempt.amount, Decimal('298.00'))
        self.assertEqual(attempt.status, PaymentTransaction.Status.PENDING)

    def test_the_browser_gets_the_form_token_and_public_values_only(self):
        response = self.checkout()

        self.assertEqual(response.data['provider'], 'micuentaweb')
        self.assertEqual(response.data['form_token'], self.fake.form_token)
        self.assertEqual(response.data['public_key'], MCW_SETTINGS['MICUENTAWEB_PUBLIC_KEY'])
        self.assertEqual(response.data['environment'], 'test')
        text = json.dumps(response.data)
        for secret in (MCW_SETTINGS['MICUENTAWEB_PASSWORD'], MCW_SETTINGS['MICUENTAWEB_HMAC_KEY']):
            self.assertNotIn(secret, text)

    def test_creating_a_payment_moves_no_stock_and_pays_nothing(self):
        attempt = self.pending()
        self.assertEqual(attempt.order.status, Order.Status.PENDING_PAYMENT)
        self.assertFalse(attempt.order.paid)
        self.assertEqual(self.stock(), 5)

    def test_when_the_gateway_refuses_no_payment_starts(self):
        for mode in ('error', 'not_json', 'http_error', 'unreachable', 'timeout'):
            with self.subTest(mode):
                PaymentTransaction.objects.all().delete()
                Order.objects.all().delete()
                self.fake.mode = mode
                with self.assertLogs('store', level='ERROR') as logs:
                    response = self.checkout()
                self.assertEqual(response.status_code, 502, response.data)
                self.assertNotIn('form_token', response.data)
                self.assertEqual(PaymentTransaction.objects.get().status, PaymentTransaction.Status.REJECTED)
                self.assertNotIn(MCW_SETTINGS['MICUENTAWEB_PASSWORD'], '\n'.join(logs.output))

    def test_test_keys_and_production_keys_are_never_mixed(self):
        mixed = {**MCW_SETTINGS, 'MICUENTAWEB_PUBLIC_KEY': '69876357:publickey_DeProduccionNoEsReal'}
        with override_settings(**mixed):
            with self.assertLogs('store', level='ERROR'):
                response = self.checkout()
        self.assertEqual(response.status_code, 500)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(self.fake.requests, [])

    def test_a_missing_credential_refuses_the_checkout_before_an_order_exists(self):
        for name in ('MICUENTAWEB_SHOP_ID', 'MICUENTAWEB_PASSWORD', 'MICUENTAWEB_PUBLIC_KEY'):
            with self.subTest(name), override_settings(**{**MCW_SETTINGS, name: ''}):
                with self.assertLogs('store', level='ERROR'):
                    self.assertEqual(self.checkout().status_code, 500)
                self.assertFalse(Order.objects.exists())


class NotificationTest(McwBase):
    def setUp(self):
        super().setUp()
        self.attempt = self.pending()
        self.order = self.attempt.order

    def answer(self, **overrides):
        values = {'order_id': self.attempt.transaction_id, 'amount_cents': 29800}
        values.update(overrides)
        return self.fake.answer(**values)

    def refresh(self):
        self.attempt.refresh_from_db()
        self.order.refresh_from_db()

    def assert_nothing_paid(self):
        self.refresh()
        self.assertFalse(self.order.paid)
        self.assertEqual(self.stock(), 5)
        self.assertFalse(StockMovement.objects.filter(order=self.order).exists())

    def test_a_signed_paid_notification_pays_the_order(self):
        response = self.notify(self.fake.ipn(self.answer()))

        self.assertEqual(response.status_code, 200, response.content)
        self.refresh()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertTrue(self.order.paid)
        self.assertEqual(self.attempt.status, PaymentTransaction.Status.AUTHORIZED)
        self.assertTrue(self.attempt.signature_verified)
        self.assertEqual(self.attempt.response_code, 'PAID')
        self.assertEqual(self.attempt.provider_unique_id, '9d6b9c5d4f7e4f0e8a1c2b3d4e5f6a7b')
        self.assertEqual(self.attempt.authorization_code, '3fe205')
        self.assertEqual(self.stock(), 3)

    def test_the_same_notification_twice_pays_once(self):
        form = self.fake.ipn(self.answer())
        self.assertEqual(self.notify(form).status_code, 200)
        self.assertEqual(self.notify(form).status_code, 200)

        self.assertEqual(self.stock(), 3)
        self.assertEqual(StockMovement.objects.filter(order=self.order, movement_type='sale_exit').count(), 1)

    def test_what_the_browser_holds_cannot_pay_the_order(self):
        """
        Tras pagar, el navegador recibe su propia copia firmada — con la clave
        HMAC, no con la contraseña. Reenviarla al punto de notificación no vale:
        ahí sólo se cree lo que firma el servidor de la pasarela.
        """
        response = self.notify(self.fake.browser_return(self.answer()))

        self.assertEqual(response.status_code, 400)
        self.assert_nothing_paid()

    def test_an_answer_edited_after_signing_is_refused(self):
        other = Order.objects.create(company=self.order.company, total=Decimal('1.00'),
                                     customer_email='x@example.com')
        for change in ({'orderTotalAmount': 100}, {'orderId': '99999999999999999999'}, {'mode': 'PRODUCTION'}):
            with self.subTest(change):
                response = self.notify(self.fake.ipn_edited_after_signing(self.answer(), **change))
                self.assertEqual(response.status_code, 400)
        self.assert_nothing_paid()
        self.assertFalse(Order.objects.get(pk=other.pk).paid)

    def test_a_signature_made_with_another_key_or_another_algorithm_is_refused(self):
        for form in (
            self.fake.ipn(self.answer(), key='otra-clave'),
            self.fake.ipn(self.answer(), algorithm='md5'),
            {**self.fake.ipn(self.answer()), 'kr-hash': 'ñandú'},
            {k: v for k, v in self.fake.ipn(self.answer()).items() if k != 'kr-hash'},
            {'kr-answer': 'no es json', 'kr-hash': self.fake.sign('no es json', self.fake.password),
             'kr-hash-algorithm': 'sha256_hmac', 'kr-hash-key': 'password'},
            {},
        ):
            with self.subTest(sorted(form)):
                self.assertEqual(self.notify(form).status_code, 400)
        self.assert_nothing_paid()

    def test_the_same_form_sent_as_multipart_is_read_the_same(self):
        response = self.client.post(IPN_URL, self.fake.ipn(self.answer()), format='multipart')

        self.assertEqual(response.status_code, 200, response.content)
        self.refresh()
        self.assertTrue(self.order.paid)

    def test_an_oversized_notification_is_refused_before_it_is_read(self):
        """Sin sesión y sin límite de peticiones: lo que protege la memoria es el tope del cuerpo."""
        huge = 'kr-answer=' + 'A' * (300 * 1024) + '&kr-hash=x&kr-hash-algorithm=sha256_hmac&kr-hash-key=password'
        response = self.client.post(IPN_URL, data=huge, content_type='application/x-www-form-urlencoded')

        self.assertEqual(response.status_code, 413)
        self.assert_nothing_paid()

    def test_a_crafted_charset_is_a_refusal_not_a_crash(self):
        from urllib.parse import urlencode
        body = urlencode(self.fake.ipn(self.answer()))
        for charset in ('hex', 'rot13', 'utf-16'):
            with self.subTest(charset):
                response = self.client.post(
                    IPN_URL, data=body, content_type=f'application/x-www-form-urlencoded; charset={charset}')
                self.assertIn(response.status_code, (200, 400))
        # La firma cubre los bytes recibidos: leídos como UTF-8, el mensaje bueno paga.
        self.refresh()
        self.assertTrue(self.order.paid)

    def test_a_well_signed_answer_that_contradicts_the_order_is_an_integrity_failure(self):
        cases = {
            'amount': self.answer(amount_cents=100),
            'currency': self.answer(currency='USD'),
            'shop': self.answer(shop_id='11111111'),
        }
        for name, answer in cases.items():
            with self.subTest(name):
                PaymentTransaction.objects.filter(pk=self.attempt.pk).update(
                    status=PaymentTransaction.Status.PENDING, failure_reason='')
                response = self.notify(self.fake.ipn(answer))
                self.assertEqual(response.status_code, 400)
                self.attempt.refresh_from_db()
                self.assertEqual(self.attempt.status, PaymentTransaction.Status.INTEGRITY_FAILED)
        self.assert_nothing_paid()

    def test_a_test_payment_never_pays_an_order_of_a_production_shop(self):
        production = {
            **MCW_SETTINGS,
            'MICUENTAWEB_PASSWORD': 'prodpassword_NoEsRealNoEsRealNoEsRealNoEsRealNoEsRe',
            'MICUENTAWEB_PUBLIC_KEY': '69876357:publickey_NoEsRealNoEsRealNoEsRealNoEsReal',
        }
        with override_settings(**production):
            gateway = FakeMiCuentaWeb.from_settings(production)
            response = self.notify(gateway.ipn(self.answer(mode='TEST')))

        self.assertEqual(response.status_code, 400)
        self.assert_nothing_paid()

    def test_a_refused_payment_leaves_the_order_waiting(self):
        response = self.notify(self.fake.ipn(self.answer(status='UNPAID')))

        self.assertEqual(response.status_code, 200)
        self.refresh()
        self.assertEqual(self.attempt.status, PaymentTransaction.Status.REJECTED)
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)
        self.assert_nothing_paid()

    def test_a_payment_still_in_progress_changes_nothing_yet(self):
        response = self.notify(self.fake.ipn(self.answer(status='RUNNING')))

        self.assertEqual(response.status_code, 200)
        self.refresh()
        self.assertEqual(self.attempt.status, PaymentTransaction.Status.PENDING)
        self.assert_nothing_paid()

    def test_a_refusal_after_the_payment_does_not_undo_it(self):
        self.notify(self.fake.ipn(self.answer()))
        self.notify(self.fake.ipn(self.answer(status='UNPAID')))

        self.refresh()
        self.assertTrue(self.order.paid)
        self.assertEqual(self.attempt.status, PaymentTransaction.Status.AUTHORIZED)

    def test_a_paid_answer_for_an_unknown_order_pays_nothing(self):
        response = self.notify(self.fake.ipn(self.answer(order_id='00000000000000000000')))
        self.assertEqual(response.status_code, 404)
        self.assert_nothing_paid()

    def test_an_attempt_of_the_other_provider_is_not_reachable_from_here(self):
        PaymentTransaction.objects.filter(pk=self.attempt.pk).update(provider='izipay')
        response = self.notify(self.fake.ipn(self.answer()))
        self.assertEqual(response.status_code, 404)
        self.assert_nothing_paid()


class OneProviderAtATimeTest(McwBase):
    """`PAYMENT_PROVIDER` elige UNO. El punto de notificación del otro no existe."""

    def test_with_mi_cuenta_web_active_the_checkout_sdk_webhook_is_gone(self):
        response = self.client.post(
            '/api/payments/izipay/notification/', {'payloadHttp': '{}', 'signature': 'x'}, format='json')
        self.assertEqual(response.status_code, 404)

    def test_with_the_checkout_sdk_active_the_mi_cuenta_web_webhook_is_gone(self):
        attempt = self.pending()
        form = self.fake.ipn(self.fake.answer(order_id=attempt.transaction_id, amount_cents=29800))
        with override_settings(**IZIPAY_TEST_SETTINGS):
            response = self.notify(form)
        self.assertEqual(response.status_code, 404)
        attempt.order.refresh_from_db()
        self.assertFalse(attempt.order.paid)

    def test_an_unknown_provider_refuses_the_checkout(self):
        with override_settings(PAYMENT_PROVIDER='otro'):
            with self.assertLogs('store', level='ERROR'):
                self.assertEqual(self.checkout().status_code, 500)
        self.assertFalse(Order.objects.exists())


@override_settings(**MCW_SETTINGS)
class NativeCheckoutTest(M5CheckoutBase):
    """La app recibe lo mismo que el navegador: el producto configurado y sólo valores públicos."""

    def test_the_app_gets_the_same_session_as_the_browser(self):
        fake = FakeMiCuentaWeb.from_settings(MCW_SETTINGS)
        with fake.online():
            response = self.client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')

        self.assertIn(response.status_code, (200, 201), response.content)
        payment = response.json()['payment']
        self.assertEqual(fake.violations, [])
        self.assertEqual(payment['provider'], 'micuentaweb')
        self.assertEqual(payment['form_token'], fake.form_token)
        self.assertEqual(
            sorted(payment), ['environment', 'form_token', 'provider', 'public_key', 'transaction_id'])
        self.assertEqual(fake.requests[0]['body']['amount'], 400000)
        self.assertNotIn(MCW_SETTINGS['MICUENTAWEB_PASSWORD'], response.content.decode())


@unittest.skipUnless(
    os.environ.get('MICUENTAWEB_SANDBOX_SHOP_ID') and os.environ.get('MICUENTAWEB_SANDBOX_PASSWORD'),
    'BLOCKED/CREDENTIALS: hacen falta MICUENTAWEB_SANDBOX_SHOP_ID y MICUENTAWEB_SANDBOX_PASSWORD '
    '(claves de TEST del Back Office de Mi Cuenta Web).',
)
class MiCuentaWebSandboxSmokeTest(SimpleTestCase):
    """La única prueba que habla con la pasarela real. Sólo con claves de TEST."""

    def test_the_test_environment_issues_a_form_token_for_the_request_the_adapter_builds(self):
        password = os.environ['MICUENTAWEB_SANDBOX_PASSWORD']
        self.assertTrue(password.startswith('testpassword_'), 'sólo con la contraseña de TEST')
        credentials = micuentaweb.MiCuentaWebCredentials(
            environment='test', shop_id=os.environ['MICUENTAWEB_SANDBOX_SHOP_ID'], password=password,
            public_key='', api_url='https://api.micuentaweb.pe', currency='PEN', ipn_url='',
        )
        token = micuentaweb.create_payment(
            credentials=credentials, order_id=micuentaweb.new_order_id(),
            amount=Decimal('1.80'), customer_email='prueba@example.com',
        )
        self.assertTrue(token)
