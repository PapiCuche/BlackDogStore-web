"""
PAY-TEST-PUBLIC — las claves de TEST no son para los compradores.

Con claves de TEST activadas desde la consola, la tienda publicada aceptaba
tarjetas de prueba —que son públicas— de cualquiera, y el pedido quedaba pagado.
Ahora, mientras lo que cobra es una configuración de TEST puesta en la consola:

  * sólo abre un pago quien puede configurar la pasarela (un MASTER de la
    plataforma, con su sesión): es quien tiene que probarla;
  * a cualquier otro se le dice que el pago en línea todavía no está disponible,
    antes de crear un pedido y antes de hablar con la pasarela;
  * el intento guarda en qué entorno se abrió, y un pedido pagado en TEST deja
    un registro de auditoría con su empresa.

Con claves de PRODUCCIÓN no cambia nada. Y da igual de dónde vengan las de TEST
(consola o variables del servidor): ver `test_payment_audit2`. Las pasarelas
simuladas de las pruebas cobran a cualquiera porque el corredor de pruebas
enciende `PAYMENTS_PUBLIC_TEST`; estas clases lo apagan, como una tienda publicada.
"""
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient

from store.models import AdminAuditLog, Order, OrderDelivery, OrderRiskAssessment, PaymentTransaction
from store.payments.fake_micuentaweb import FakeMiCuentaWeb
from store.test_integrations_payments import CHECKOUT_PUBLIC, CHECKOUT_SECRETS, MCW_PUBLIC, _Base, fake_checkout
from store.test_micuentaweb import IPN_URL, McwBase

NOT_YET = 'El pago en línea todavía no está disponible en esta tienda.'
MCW_PRODUCTION = {**MCW_PUBLIC, 'public_key': '90000001:publickey_DelPanelNoEsRealNoEsReal'}
MCW_PRODUCTION_SECRETS = {'password': 'prodpassword_DelPanelNoEsRealNoEsRealNoEsReal'}


@override_settings(PAYMENTS_PUBLIC_TEST=False)
class TestKeysInTheConsoleTest(_Base):
    def as_buyer(self, user=None):
        self.client = APIClient()
        if user is not None:
            self.client.force_authenticate(user)

    def assert_refused_before_anything(self, response, fake):
        self.assertEqual(response.status_code, 503, response.data)
        self.assertEqual(response.data, {'detail': NOT_YET, 'code': 'payment_test_only'})
        self.assertFalse(Order.objects.exists())
        self.assertFalse(PaymentTransaction.objects.exists())
        self.assertEqual(fake.requests, [])

    def test_a_public_buyer_is_refused_before_the_gateway_hears_anything(self):
        for name, go_live in (('mi cuenta web', self.go_live_with_mcw), ('sdk web', self.go_live_with_checkout)):
            with self.subTest(name):
                fake = go_live()
                fake.requests.clear()
                self.as_buyer()
                self.fake = fake
                self.assert_refused_before_anything(self.checkout(), fake)

    def test_a_signed_in_customer_and_staff_who_cannot_configure_the_gateway_are_buyers_too(self):
        fake = self.go_live_with_mcw()
        fake.requests.clear()
        self.fake = fake
        User = get_user_model()
        for user in (User.objects.create_user('clienta', 'clienta@example.com', 'x'),
                     User.objects.create_user('cajero', 'cajero@example.com', 'x', is_staff=True)):
            with self.subTest(user.username):
                self.as_buyer(user)
                self.assert_refused_before_anything(self.checkout(), fake)

    def test_whoever_configures_the_gateway_can_pay_in_test(self):
        for name, go_live in (('mi cuenta web', self.go_live_with_mcw), ('sdk web', self.go_live_with_checkout)):
            with self.subTest(name):
                PaymentTransaction.objects.all().delete()
                OrderRiskAssessment.objects.all().delete()
                OrderDelivery.objects.all().delete()
                Order.objects.all().delete()
                self.fake = go_live()
                self.as_buyer(self.master)
                response = self.checkout()
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(PaymentTransaction.objects.get().environment, 'test')

    def test_production_keys_change_nothing_for_a_buyer(self):
        fake = FakeMiCuentaWeb.from_settings({
            'MICUENTAWEB_SHOP_ID': '90000001', 'MICUENTAWEB_PASSWORD': MCW_PRODUCTION_SECRETS['password'],
            'MICUENTAWEB_API_URL': 'https://api.micuentaweb.pe'})
        self.assertEqual(self.save('izipay_micuentaweb', MCW_PRODUCTION, MCW_PRODUCTION_SECRETS).status_code, 200)
        self.assertEqual(self.validate('izipay_micuentaweb', fake)['status'], 'unverified')
        self.assertEqual(self.activate('izipay_micuentaweb', confirm='PRODUCCION').status_code, 200)
        self.as_buyer()
        self.fake = fake

        response = self.checkout()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['environment'], 'production')
        self.assertEqual(PaymentTransaction.objects.get().environment, 'production')

    def test_an_order_paid_in_test_says_so_afterwards(self):
        self.fake = self.go_live_with_mcw()
        self.as_buyer(self.master)
        self.assertEqual(self.checkout().status_code, 200)
        attempt = PaymentTransaction.objects.select_related('order').get()

        form = self.fake.ipn(self.fake.answer(order_id=attempt.transaction_id, amount_cents=29800))
        response = APIClient().post(IPN_URL, data=urlencode(form), content_type='application/x-www-form-urlencoded')

        self.assertEqual(response.status_code, 200, response.content)
        attempt.refresh_from_db()
        self.assertEqual((attempt.status, attempt.environment), (PaymentTransaction.Status.AUTHORIZED, 'test'))
        log = AdminAuditLog.objects.get(action='payment_confirmed_in_test')
        self.assertEqual(log.company_id, attempt.order.company_id)
        self.assertEqual((log.target_type, log.target_id), ('order', str(attempt.order_id)))
        self.assertEqual(log.metadata, {'provider': 'micuentaweb', 'attempt': attempt.pk})
        # Repetida, no deja un segundo registro.
        APIClient().post(IPN_URL, data=urlencode(form), content_type='application/x-www-form-urlencoded')
        self.assertEqual(AdminAuditLog.objects.filter(action='payment_confirmed_in_test').count(), 1)

    def test_a_production_payment_leaves_no_such_record(self):
        fake = FakeMiCuentaWeb.from_settings({
            'MICUENTAWEB_SHOP_ID': '90000001', 'MICUENTAWEB_PASSWORD': MCW_PRODUCTION_SECRETS['password'],
            'MICUENTAWEB_API_URL': 'https://api.micuentaweb.pe'})
        self.save('izipay_micuentaweb', MCW_PRODUCTION, MCW_PRODUCTION_SECRETS)
        self.validate('izipay_micuentaweb', fake)
        self.activate('izipay_micuentaweb', confirm='PRODUCCION')
        self.as_buyer()
        self.fake = fake
        self.assertEqual(self.checkout().status_code, 200)
        attempt = PaymentTransaction.objects.get()

        form = fake.ipn(fake.answer(order_id=attempt.transaction_id, amount_cents=29800))
        response = APIClient().post(IPN_URL, data=urlencode(form), content_type='application/x-www-form-urlencoded')

        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(AdminAuditLog.objects.filter(action='payment_confirmed_in_test').exists())

    def test_a_retry_of_a_payment_is_refused_the_same_way(self):
        """`require_payment_provider_configured` es la única puerta: quien no la pasa no abre un intento."""
        from store import checkout_services as checkout

        self.go_live_with_mcw()
        for buyer in (None, get_user_model().objects.create_user('otra', 'otra@example.com', 'x')):
            with self.assertRaises(checkout.CheckoutError) as refused:
                checkout.require_payment_provider_configured(buyer)
            self.assertEqual((refused.exception.status_code, refused.exception.detail), (503, NOT_YET))
        self.assertIsNotNone(checkout.require_payment_provider_configured(self.master))


class KeysFromTheEnvironmentTest(McwBase):
    """
    CAMBIADA A PROPÓSITO (segunda auditoría). Antes fijaba que las claves de TEST
    de las variables del servidor cobraban a cualquiera: era el hueco. Lo que
    cobra en TEST no es para compradores venga de donde venga; las pasarelas
    simuladas de las pruebas cobran porque el corredor de pruebas enciende
    `PAYMENTS_PUBLIC_TEST`, no porque el entorno sea una excepción.
    """

    def test_the_suite_pays_through_the_simulated_gateway_because_the_switch_is_on(self):
        response = self.checkout()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(PaymentTransaction.objects.get().environment, 'test')

    def test_with_the_switch_off_the_same_keys_do_not_charge_a_buyer(self):
        with override_settings(PAYMENTS_PUBLIC_TEST=False):
            response = self.checkout()

        self.assertEqual(response.status_code, 503)
        self.assertFalse(PaymentTransaction.objects.exists())


@override_settings(PAYMENTS_PUBLIC_TEST=False)
class OneEnvironmentPerAttemptTest(_Base):
    """Un intento abierto en un entorno no lo confirma el otro, aunque la firma valga."""

    def test_an_attempt_opened_in_test_is_not_confirmed_once_production_is_configured(self):
        sandbox = self.go_live_with_checkout()
        self.client.force_authenticate(self.master)
        self.fake = sandbox
        self.assertEqual(self.checkout().status_code, 200)
        attempt = PaymentTransaction.objects.select_related('order').get()
        self.assertEqual(attempt.environment, 'test')
        # Horas después, las claves de producción del mismo comercio (y la misma clave hash).
        from datetime import timedelta

        from django.utils import timezone
        PaymentTransaction.objects.update(created_at=timezone.now() - timedelta(hours=3))
        production = {**CHECKOUT_PUBLIC, 'environment': 'production', 'token_url': 'https://api.izipay.invalid/token'}
        self.assertEqual(self.save('izipay_checkout', production, CHECKOUT_SECRETS).status_code, 200)
        self.validate('izipay_checkout', fake_checkout(production))
        self.assertEqual(self.activate('izipay_checkout', confirm='PRODUCCION').status_code, 200)

        gateway = fake_checkout(production)
        payload = gateway.payload(transaction_id=attempt.transaction_id, order_number=attempt.order_number,
                                  amount=attempt.amount)
        with self.assertLogs('store', level='ERROR'):
            response = APIClient().post('/api/payments/izipay/notification/', gateway.envelope(payload), format='json')

        self.assertEqual(response.status_code, 400)
        attempt.refresh_from_db()
        attempt.order.refresh_from_db()
        self.assertFalse(attempt.order.paid)
        self.assertEqual(attempt.status, PaymentTransaction.Status.INTEGRITY_FAILED)
        self.assertEqual(attempt.failure_reason, 'environment mismatch')

    def test_an_attempt_older_than_the_column_is_judged_as_before(self):
        fake = self.go_live_with_checkout()
        self.client.force_authenticate(self.master)
        self.fake = fake
        self.assertEqual(self.checkout().status_code, 200)
        PaymentTransaction.objects.update(environment='')
        attempt = PaymentTransaction.objects.get()

        payload = fake.payload(transaction_id=attempt.transaction_id, order_number=attempt.order_number,
                               amount=attempt.amount)
        response = APIClient().post('/api/payments/izipay/notification/', fake.envelope(payload), format='json')

        self.assertEqual(response.status_code, 200)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, PaymentTransaction.Status.AUTHORIZED)
