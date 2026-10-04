"""
Izipay, contra un Izipay falso — PAYMENT-FISCAL-PRINT-01.

Dos mitades de la misma integración:

  · PEDIR EL TOKEN. Hasta ahora `request_session_token` se sustituía entera en
    cada prueba, así que su URL, sus cabeceras, su cuerpo y el sobre de la
    respuesta no los ejercitaba nadie. Aquí el checkout corre de verdad hasta el
    socket, y en el socket hay un Izipay falso que comprueba el contrato.

  · RECIBIR EL RESULTADO. El falso firma como firma Izipay, y también envía lo
    que enviaría quien intercepta un mensaje: editado después de firmar, firmado
    con otra clave, repetido, o repetido con otro contenido.

Ninguna de estas pruebas necesita credenciales. La única que habla con el
sandbox real (`IzipaySandboxSmokeTest`) se omite sin ellas: BLOCKED/CREDENTIALS.
"""
import json
import os
import unittest
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from store.models import CartItem, Order, OrderItem, PaymentTransaction, Product, StockMovement
from store.payments import izipay
from store.payments.fake_izipay import DECLINED, FakeIzipay
from store.tests import IZIPAY_TEST_SETTINGS, _pay_attempt, _pilot_company, _seeded

CHECKOUT_URL = '/api/payments/create-checkout-session/'


@override_settings(**IZIPAY_TEST_SETTINGS)
class IzipayTokenContractTest(TestCase):
    """El checkout llega hasta el socket; al otro lado hay un Izipay falso."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.fake = FakeIzipay.from_settings(IZIPAY_TEST_SETTINGS)
        self.product = _seeded(Product.objects.create(
            company=_pilot_company(), name='Contrato Izipay', slug='contrato-izipay',
            price=Decimal('149.00'), inventory=5,
        ))
        self.session_key = 'contrato-izipay-0001'
        CartItem.objects.create(session_key=self.session_key, product=self.product, quantity=2)

    def _checkout(self, **overrides):
        body = {
            'session_key': self.session_key,
            'customer_name': 'Ana Torres', 'customer_email': 'ana@example.com',
            'customer_phone': '936449536', 'document_type': 'dni', 'document_number': '12345678',
            'delivery_method': 'pickup_store', 'receipt_type': 'boleta',
            'accepted_terms': True, 'accepted_warranty_policy': True,
        }
        body.update(overrides)
        return self.client.post(CHECKOUT_URL, body, format='json')

    def test_the_request_the_checkout_sends_keeps_the_token_contract(self):
        with self.fake.online():
            response = self._checkout()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.fake.violations, [])
        self.assertEqual(response.data['authorization'], self.fake.token)

        sent = self.fake.requests[0]
        attempt = PaymentTransaction.objects.get()
        # Lo que se pidió es lo que quedó escrito ANTES de pedirlo.
        self.assertEqual(sent['body']['transactionId'], attempt.transaction_id)
        self.assertEqual(sent['body']['order']['orderNumber'], attempt.order_number)
        self.assertEqual(sent['body']['order']['amount'], '298.00')
        self.assertEqual(Decimal(sent['body']['order']['amount']), attempt.order.total)

    def test_the_api_key_travels_only_to_the_gateway(self):
        with self.fake.online():
            response = self._checkout()

        self.assertEqual(self.fake.requests[0]['headers']['authorization'], self.fake.api_key)
        answered = json.dumps(response.data)
        self.assertNotIn(self.fake.api_key, answered)
        self.assertNotIn(self.fake.hash_key, answered)

    def test_a_token_at_the_top_of_the_reply_is_accepted_too(self):
        self.fake.token_mode = 'flat'
        with self.fake.online():
            response = self._checkout()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['authorization'], self.fake.token)

    def test_when_the_gateway_does_not_issue_a_token_no_payment_starts(self):
        for mode in ('no_token', 'not_json', 'http_error', 'unreachable', 'timeout'):
            with self.subTest(mode=mode):
                self.fake.token_mode = mode
                with self.fake.online():
                    response = self._checkout()

                self.assertEqual(response.status_code, 502, response.data)
                order = Order.objects.order_by('-pk').first()
                self.assertEqual(order.status, Order.Status.FAILED)
                self.assertFalse(order.paid)
                self.assertEqual(
                    PaymentTransaction.objects.get(order=order).status,
                    PaymentTransaction.Status.REJECTED)
                # El carrito sigue ahí: el comprador puede volver a intentarlo.
                self.assertTrue(CartItem.objects.filter(session_key=self.session_key).exists())
                self.assertNotIn(self.fake.api_key, json.dumps(response.data))

    def test_a_network_failure_does_not_write_the_api_key_in_the_log(self):
        self.fake.token_mode = 'unreachable'
        with self.fake.online(), self.assertLogs('store.payments.izipay', level='ERROR') as logs:
            self._checkout()

        self.assertNotIn(self.fake.api_key, '\n'.join(logs.output))

    def test_the_fake_refuses_a_request_that_breaks_the_contract(self):
        # La prueba de la prueba: si el falso lo aceptara todo, las de arriba
        # no demostrarían nada.
        credentials = izipay.load_credentials()
        with self.fake.online():
            with self.assertRaises(izipay.IzipayError):
                izipay.request_session_token(
                    credentials=credentials, transaction_id='1' * 20,
                    payload={'transactionId': '2' * 20, 'action': 'pay'},
                )
        self.assertIn('the transactionId header equals the one in the body', self.fake.violations)
        self.assertIn('the body carries an order', self.fake.violations)
        self.assertIn('merchantCode is this merchant', self.fake.violations)


@override_settings(**IZIPAY_TEST_SETTINGS)
class IzipayNotificationContractTest(TestCase):
    """Lo que Izipay envía, lo que enviaría un atacante y lo que llega dos veces."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.fake = FakeIzipay.from_settings(IZIPAY_TEST_SETTINGS)
        company = _pilot_company()
        self.product = _seeded(Product.objects.create(
            company=company, name='Notificación Izipay', slug='notificacion-izipay',
            price=Decimal('250.00'), inventory=10,
        ))
        self.order = Order.objects.create(
            company=company, customer_email='pago@example.com', total=Decimal('500.00'),
            cart_session_key='notificacion-izipay-0001', status=Order.Status.PENDING_PAYMENT,
        )
        OrderItem.objects.create(order=self.order, product=self.product, quantity=2, price=self.product.price)
        self.attempt = _pay_attempt(self.order, transaction_id='9' * 20, order_number='8' * 12)

    def _state(self):
        self.order.refresh_from_db()
        self.attempt.refresh_from_db()
        return self.order.status, self.order.paid, self.attempt.status

    def _sold(self):
        return StockMovement.objects.filter(order=self.order).count()

    # -- lo que sí paga --------------------------------------------------------

    def test_a_signed_authorisation_pays_the_order(self):
        response = self.fake.deliver(self.client, self.fake.notification(self.attempt))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self._state(), (Order.Status.PAID, True, PaymentTransaction.Status.AUTHORIZED))
        self.assertTrue(self.attempt.signature_verified)
        self.assertEqual(self._sold(), 1)

    def test_a_payload_without_a_merchant_is_still_bound_by_the_signature(self):
        # Izipay no siempre repite el comercio dentro del mensaje. Lo que lo
        # ata a esta tienda es la firma: sólo quien tiene la clave la produce.
        genuine = self.fake.notification(self.attempt, include_merchant=False)
        self.assertEqual(self.fake.deliver(self.client, genuine).status_code, 200)
        self.assertEqual(self._state()[0], Order.Status.PAID)

    # -- lo que no paga --------------------------------------------------------

    def test_a_declined_payment_leaves_the_order_waiting(self):
        response = self.fake.deliver(self.client, self.fake.notification(self.attempt, code=DECLINED))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self._state(),
            (Order.Status.PENDING_PAYMENT, False, PaymentTransaction.Status.REJECTED))
        self.assertEqual(self._sold(), 0)

    def test_a_communication_error_is_not_an_answer_about_the_payment(self):
        for code in ('021', 'COMMUNICATION_ERROR'):
            with self.subTest(code=code):
                envelope = self.fake.notification(self.attempt)
                envelope['code'] = code
                response = self.fake.deliver(self.client, envelope)

                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    self._state(),
                    (Order.Status.PENDING_PAYMENT, False, PaymentTransaction.Status.PENDING))

    def test_a_payload_edited_after_signing_is_refused(self):
        genuine = self.fake.notification(self.attempt, code=DECLINED)

        def approve(payload):
            payload['code'] = '00'
            payload['response']['order'][0]['stateMessage'] = 'Autorizado'

        response = self.fake.deliver(self.client, self.fake.tampered(genuine, approve))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            self._state(),
            (Order.Status.PENDING_PAYMENT, False, PaymentTransaction.Status.PENDING))

    def test_a_smaller_amount_written_over_a_signed_payment_is_refused(self):
        genuine = self.fake.notification(self.attempt)

        def cheaper(payload):
            payload['response']['order'][0]['amount'] = '5.00'

        response = self.fake.deliver(self.client, self.fake.tampered(genuine, cheaper))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._state()[0], Order.Status.PENDING_PAYMENT)

    def test_the_copy_in_clear_cannot_contradict_the_signed_payload(self):
        # El sobre repite el contenido fuera de la firma. Quien lo edite ahí
        # no cambia nada: sólo cuenta lo firmado.
        envelope = self.fake.notification(self.attempt, code=DECLINED)
        envelope['code'] = '00'
        envelope['response']['order'][0]['stateMessage'] = 'Autorizado'

        self.fake.deliver(self.client, envelope)

        self.assertEqual(
            self._state(),
            (Order.Status.PENDING_PAYMENT, False, PaymentTransaction.Status.REJECTED))

    def test_a_signature_made_with_another_key_is_refused(self):
        forged = self.fake.signed_by_someone_else(self.fake.notification(self.attempt))

        self.assertEqual(self.fake.deliver(self.client, forged).status_code, 400)
        self.assertEqual(
            self._state(),
            (Order.Status.PENDING_PAYMENT, False, PaymentTransaction.Status.PENDING))

    def test_a_signed_payload_that_is_not_a_payment_is_refused(self):
        for label, payload in (
            ('no es JSON', 'esto no es JSON'),
            ('sin orden', json.dumps({'code': '00', 'transactionId': self.attempt.transaction_id,
                                      'response': {'order': []}})),
            ('importe que no es un número', self.fake.payload(
                transaction_id=self.attempt.transaction_id,
                order_number=self.attempt.order_number, amount='mucho')),
        ):
            with self.subTest(label):
                envelope = {
                    'code': '00', 'payloadHttp': payload, 'signature': self.fake.sign(payload),
                    'transactionId': self.attempt.transaction_id,
                }
                response = self.fake.deliver(self.client, envelope)

                self.assertEqual(response.status_code, 400)
                self.assertEqual(self._state()[0], Order.Status.PENDING_PAYMENT)

    def test_an_amount_that_no_longer_matches_the_attempt_is_refused(self):
        # El total del pedido cambió después de abrir el intento: el comprador
        # autorizó la cifra antigua.
        Order.objects.filter(pk=self.order.pk).update(total=Decimal('650.00'))

        response = self.fake.deliver(
            self.client, self.fake.notification(self.attempt, amount=Decimal('650.00')))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            self._state(),
            (Order.Status.PENDING_PAYMENT, False, PaymentTransaction.Status.INTEGRITY_FAILED))

    # -- lo que llega dos veces ------------------------------------------------

    def test_the_same_notification_twice_pays_once(self):
        envelope = self.fake.notification(self.attempt)
        first = self.fake.deliver(self.client, envelope)
        second = self.fake.deliver(self.client, envelope)

        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(
            self._state(), (Order.Status.PAID, True, PaymentTransaction.Status.AUTHORIZED))
        self.assertEqual(self._sold(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 8)

    def test_a_decline_after_the_authorisation_does_not_undo_the_payment(self):
        self.fake.deliver(self.client, self.fake.notification(self.attempt))
        self.fake.deliver(self.client, self.fake.notification(self.attempt, code=DECLINED))

        self.assertEqual(
            self._state(), (Order.Status.PAID, True, PaymentTransaction.Status.AUTHORIZED))

    def test_a_contradicting_replay_does_not_rewrite_an_authorised_payment(self):
        """
        Pagado y registrado. Después llega otro mensaje, bien firmado, con el
        mismo identificador y otro importe. No paga nada —el pedido ya lo
        está— pero tampoco puede reescribir el registro del pago bueno: el
        intento sigue AUTORIZADO, con su código de autorización.
        """
        self.fake.deliver(self.client, self.fake.notification(self.attempt))
        self.attempt.refresh_from_db()
        authorised_code = self.attempt.authorization_code

        response = self.fake.deliver(
            self.client, self.fake.notification(self.attempt, amount=Decimal('1.00')))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            self._state(), (Order.Status.PAID, True, PaymentTransaction.Status.AUTHORIZED))
        self.assertEqual(self.attempt.authorization_code, authorised_code)
        self.assertEqual(self.attempt.failure_reason, '')
        self.assertEqual(self._sold(), 1)

    def test_an_authorisation_for_an_unknown_transaction_pays_nothing(self):
        stranger = self.fake.envelope(self.fake.payload(
            transaction_id='7' * 20, order_number=self.attempt.order_number, amount='500.00'))

        self.assertEqual(self.fake.deliver(self.client, stranger).status_code, 404)
        self.assertEqual(self._state()[0], Order.Status.PENDING_PAYMENT)


@unittest.skipUnless(
    os.environ.get('IZIPAY_SANDBOX_SMOKE') == '1',
    'BLOCKED/CREDENTIALS: el sandbox real de Izipay exige credenciales del comercio. '
    'Con ellas en el entorno: IZIPAY_SANDBOX_SMOKE=1 manage.py test store.test_izipay_contract.IzipaySandboxSmokeTest',
)
class IzipaySandboxSmokeTest(TestCase):
    """
    La misma petición, contra el sandbox de verdad. No corre sin credenciales.

    Lo único que comprueba es lo único que el falso no puede: que el contrato
    que el falso exige es el que Izipay acepta hoy.
    """

    def test_the_sandbox_issues_a_token_for_the_request_the_checkout_builds(self):
        from store.checkout_services import build_payment_config

        credentials = izipay.load_credentials()
        self.assertEqual(credentials.environment, 'sandbox')
        company = _pilot_company()
        order = Order.objects.create(
            company=company, customer_name='Prueba Sandbox', customer_email='sandbox@example.com',
            customer_phone='900000000', total=Decimal('1.00'), status=Order.Status.PENDING_PAYMENT,
        )
        attempt = _pay_attempt(
            order, transaction_id=izipay.new_transaction_id(), order_number=izipay.new_order_number())
        token = izipay.request_session_token(
            credentials=credentials, transaction_id=attempt.transaction_id,
            payload=build_payment_config(
                order, credentials=credentials, transaction_id=attempt.transaction_id,
                order_number=attempt.order_number),
        )
        self.assertTrue(token)
