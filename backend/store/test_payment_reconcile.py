"""
PAY-RECONCILE — preguntar a la pasarela por los pagos cuya notificación no llegó.

  https://secure.micuentaweb.pe/doc/es-PE/rest/V4.0/api/playground/Order/Get
  https://secure.micuentaweb.pe/doc/es-PE/rest/V4.0/api/kb/ipn_usage.html  (§5)

La notificación (IPN) sigue siendo la que paga un pedido. Pero la pasarela la da
por fallida si la tienda no contesta con un 2xx o un 3xx, y entonces el dinero
está cobrado y el pedido sigue esperando. `Order/Get` devuelve lo que la pasarela
tiene de una referencia; con eso, y con las MISMAS comprobaciones que pasa una
notificación, el pedido se paga una vez.

Lo que no cambia:

  * sólo pregunta el servidor, con la contraseña que nunca sale de él;
  * lo que diga la pasarela se compara con lo que la tienda abrió: tienda,
    referencia, entorno (TEST / PRODUCCIÓN), moneda e importe;
  * lo que no se puede decidir no se decide: el intento sigue esperando;
  * preguntar dos veces, o que la notificación llegue después, paga una vez.

Sin credenciales: al otro lado hay una pasarela falsa que exige el contrato
publicado. Que el entorno real conteste así sólo lo dice una prueba con claves.
"""
import base64
import io
import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock
from urllib.parse import urlencode

from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from store.models import AdminAuditLog, Order, PaymentTransaction, StockMovement
from store.payments import micuentaweb, reconcile
from store.payments.fake_micuentaweb import FakeMiCuentaWeb, _Reply
from store.test_micuentaweb import IPN_URL, MCW_SETTINGS, McwBase
from store.tests import IZIPAY_TEST_SETTINGS

GET_ORDER_URL = 'https://api.micuentaweb.pe/api-payment/V4/Order/Get'
PRODUCTION = {
    **MCW_SETTINGS,
    'MICUENTAWEB_PASSWORD': 'prodpassword_NoEsRealNoEsRealNoEsRealNoEsRealNoEsRe',
    'MICUENTAWEB_PUBLIC_KEY': '69876357:publickey_NoEsRealNoEsRealNoEsRealNoEsReal',
}


class ReconcileBase(McwBase):
    def setUp(self):
        super().setUp()
        self.attempt = self.pending()
        self.order = self.attempt.order
        self.fake.requests.clear()
        self.aged(minutes=10)

    def aged(self, attempt=None, **age):
        """El intento se abrió hace este rato."""
        attempt = attempt or self.attempt
        PaymentTransaction.objects.filter(pk=attempt.pk).update(created_at=timezone.now() - timedelta(**age))

    def paid(self, **overrides):
        values = {'order_id': self.attempt.transaction_id, 'amount_cents': 29800}
        values.update(overrides)
        return self.fake.transaction(**values)

    def ask(self, gateway=None, **options):
        with (gateway or self.fake).online():
            return reconcile.reconcile_micuentaweb(**options)

    def asked(self, gateway=None):
        return [r for r in (gateway or self.fake).requests if r['url'].endswith('/Order/Get')]

    def refresh(self):
        self.attempt.refresh_from_db()
        self.order.refresh_from_db()

    def assert_nothing_paid(self):
        self.refresh()
        self.assertFalse(self.order.paid)
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(self.stock(), 5)
        self.assertFalse(StockMovement.objects.filter(order=self.order).exists())

    def assert_still_waiting(self):
        self.assert_nothing_paid()
        self.assertEqual(self.attempt.status, PaymentTransaction.Status.PENDING)


class AskingTheGatewayTest(ReconcileBase):
    def test_a_charged_order_whose_notification_never_came_gets_paid(self):
        self.fake.hold(self.attempt.transaction_id, self.paid())

        summary = self.ask()

        self.assertEqual(summary.paid, 1)
        self.refresh()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertTrue(self.order.paid)
        self.assertEqual(self.attempt.status, PaymentTransaction.Status.AUTHORIZED)
        self.assertEqual(self.attempt.response_code, 'PAID')
        self.assertEqual(self.attempt.provider_unique_id, '9d6b9c5d4f7e4f0e8a1c2b3d4e5f6a7b')
        self.assertEqual(self.attempt.authorization_code, '3fe205')
        self.assertEqual(self.stock(), 3)

    def test_it_asks_as_the_documentation_says(self):
        self.fake.hold(self.attempt.transaction_id, self.paid())

        self.ask()

        self.assertEqual(self.fake.violations, [])
        (sent,) = self.asked()
        self.assertEqual(sent['url'], GET_ORDER_URL)
        self.assertEqual(sent['method'], 'POST')
        self.assertEqual(
            sent['headers']['authorization'],
            'Basic ' + base64.b64encode(b'69876357:' + MCW_SETTINGS['MICUENTAWEB_PASSWORD'].encode()).decode())
        self.assertEqual(sent['body'], {'orderId': self.attempt.transaction_id})
        self.assertLessEqual(sent['timeout'], 15)

    def test_an_answer_nobody_signed_is_not_recorded_as_a_verified_signature(self):
        """
        La respuesta de `Order/Get` no trae firma: se cree por a quién se le
        preguntó y con qué contraseña. El intento lo dice, y queda un registro
        de auditoría con la empresa del pedido.
        """
        self.fake.hold(self.attempt.transaction_id, self.paid())

        self.ask()

        self.refresh()
        self.assertFalse(self.attempt.signature_verified)
        log = AdminAuditLog.objects.get(action='payment_reconciled')
        self.assertEqual(log.company_id, self.order.company_id)
        self.assertEqual(log.target_id, str(self.order.pk))
        self.assertIsNone(log.actor)
        self.assertNotIn(MCW_SETTINGS['MICUENTAWEB_PASSWORD'], str(log.metadata))

    def test_asking_again_pays_once_and_asks_nothing_more_about_it(self):
        self.fake.hold(self.attempt.transaction_id, self.paid())

        self.ask()
        self.ask()

        self.assertEqual(len(self.asked()), 1)
        self.assertEqual(self.stock(), 3)
        self.assertEqual(StockMovement.objects.filter(order=self.order, movement_type='sale_exit').count(), 1)
        self.assertEqual(AdminAuditLog.objects.filter(action='payment_reconciled').count(), 1)

    def test_the_notification_arriving_afterwards_pays_nothing_twice(self):
        self.fake.hold(self.attempt.transaction_id, self.paid())
        self.ask()

        form = self.fake.ipn(self.fake.answer(order_id=self.attempt.transaction_id, amount_cents=29800))
        response = self.client.post(IPN_URL, data=urlencode(form), content_type='application/x-www-form-urlencoded')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.stock(), 3)
        self.assertEqual(StockMovement.objects.filter(order=self.order, movement_type='sale_exit').count(), 1)

    def test_a_notification_that_got_there_while_asking_is_the_one_that_paid(self):
        """La consulta y la notificación se cruzan: paga una, y no se anota una conciliación que no hubo."""
        self.fake.hold(self.attempt.transaction_id, self.paid())
        asking = micuentaweb.get_order_transactions

        def notified_meanwhile(**kwargs):
            answer = asking(**kwargs)
            form = self.fake.ipn(self.fake.answer(order_id=self.attempt.transaction_id, amount_cents=29800))
            self.client.post(IPN_URL, data=urlencode(form), content_type='application/x-www-form-urlencoded')
            return answer

        with mock.patch.object(micuentaweb, 'get_order_transactions', side_effect=notified_meanwhile):
            summary = self.ask()

        self.refresh()
        self.assertTrue(self.order.paid)
        self.assertTrue(self.attempt.signature_verified)
        self.assertEqual(summary.paid, 0)
        self.assertEqual(self.stock(), 3)
        self.assertFalse(AdminAuditLog.objects.filter(action='payment_reconciled').exists())

    def test_a_refused_try_followed_by_a_paid_one_is_a_paid_order(self):
        """El formulario deja reintentar: la pasarela guarda el rechazo y el pago bajo la misma referencia."""
        self.fake.hold(
            self.attempt.transaction_id,
            self.paid(status='UNPAID', uuid='a' * 32), self.paid(uuid='b' * 32))

        self.ask()

        self.refresh()
        self.assertTrue(self.order.paid)
        self.assertEqual(self.attempt.provider_unique_id, 'b' * 32)


class WhatIsNotBelievedTest(ReconcileBase):
    def test_what_the_gateway_holds_must_be_what_the_shop_opened(self):
        cases = {
            'importe': self.paid(amount_cents=100),
            'moneda': self.paid(currency='USD'),
            'otra tienda': self.paid(shop_id='11111111'),
            'otra referencia': self.paid(order_id='99999999999999999999'),
            # El pedido de la pasarela vale lo que el nuestro, pero lo cobrado es menos.
            'pago parcial': self.paid(amount_cents=100, order_total=29800),
            # El pedido está en soles y el cobro, en otra moneda.
            'cobro en otra moneda': {**self.paid(), 'currency': 'USD'},
        }
        for name, transaction in cases.items():
            with self.subTest(name):
                PaymentTransaction.objects.filter(pk=self.attempt.pk).update(
                    status=PaymentTransaction.Status.PENDING, failure_reason='')
                self.fake.hold(self.attempt.transaction_id, transaction)
                with self.assertLogs('store', level='WARNING'):
                    summary = self.ask()
                self.assertEqual(summary.paid, 0)
                self.assert_nothing_paid()
                self.assertNotEqual(self.attempt.status, PaymentTransaction.Status.AUTHORIZED)

    def test_an_answer_about_another_shop_or_another_reference_is_not_an_answer(self):
        """Lo que se lee es la respuesta a ESTA pregunta: de esta tienda, de esta referencia y sin error."""
        def another_shop(reply):
            reply['answer']['shopId'] = '11111111'
            for transaction in reply['answer']['transactions']:
                del transaction['shopId']

        def another_reference(reply):
            reply['answer']['orderId'] = '99999999999999999999'

        def an_error(reply):
            reply['status'] = 'ERROR'

        def no_list(reply):
            reply['answer']['transactions'] = None

        self.fake.hold(self.attempt.transaction_id, self.paid())
        original = self.fake._order_get
        for change in (another_shop, another_reference, an_error, no_list):
            with self.subTest(change.__name__):
                def altered(body, change=change):
                    reply = json.loads(original(body).read())
                    change(reply)
                    return _Reply(json.dumps(reply).encode())

                with mock.patch.object(self.fake, '_order_get', side_effect=altered):
                    with self.assertLogs('store', level='WARNING'):
                        summary = self.ask()
                self.assertEqual((summary.paid, summary.mismatched), (0, 0))
                self.assert_still_waiting()

    def test_a_test_transaction_never_pays_with_production_keys(self):
        with override_settings(**PRODUCTION):
            gateway = FakeMiCuentaWeb.from_settings(PRODUCTION)
            gateway.hold(self.attempt.transaction_id, self.paid(mode='TEST'))
            with self.assertLogs('store', level='WARNING'):
                summary = self.ask(gateway)

        self.assertEqual(len(self.asked(gateway)), 1)
        self.assertEqual(summary.paid, 0)
        self.assert_still_waiting()

    def test_a_production_transaction_never_pays_with_test_keys(self):
        self.fake.hold(self.attempt.transaction_id, self.paid(mode='PRODUCTION'))

        with self.assertLogs('store', level='WARNING'):
            summary = self.ask()

        self.assertEqual(summary.paid, 0)
        self.assert_still_waiting()

    def test_a_transaction_that_says_no_environment_pays_nothing(self):
        transaction = self.paid()
        del transaction['orderDetails']['mode']
        self.fake.hold(self.attempt.transaction_id, transaction)

        with self.assertLogs('store', level='WARNING'):
            self.ask()

        self.assert_still_waiting()

    def test_paid_without_money_behind_it_is_not_a_payment(self):
        """
        `status` PAID también lo llevan una verificación de tarjeta (ACCEPTED) y
        una preautorización (PRE_AUTHORISED), que no se capturan nunca.
        """
        for detailed in ('ACCEPTED', 'PRE_AUTHORISED', 'ALGO_NUEVO', ''):
            with self.subTest(detailed):
                transaction = self.paid()
                transaction['detailedStatus'] = detailed
                self.fake.hold(self.attempt.transaction_id, transaction)
                with self.assertLogs('store', level='WARNING'):
                    self.ask()
                self.assert_still_waiting()

    def test_two_payments_or_a_refund_under_one_reference_need_a_person(self):
        cases = {
            'dos pagos': [self.paid(uuid='a' * 32), self.paid(uuid='b' * 32)],
            'un reembolso': [self.paid(), self.paid(operation='CREDIT', uuid='c' * 32)],
            'una verificación': [self.paid(operation='VERIFICATION')],
        }
        for name, transactions in cases.items():
            with self.subTest(name):
                self.fake.hold(self.attempt.transaction_id, *transactions)
                with self.assertLogs('store', level='WARNING'):
                    summary = self.ask()
                self.assertEqual((summary.paid, summary.undecided), (0, 1))
                self.assert_still_waiting()

    def test_an_answer_that_is_not_what_the_documentation_describes_decides_nothing(self):
        as_text = self.paid()
        as_text['amount'] = as_text['orderDetails']['orderTotalAmount'] = '29800'
        for broken in (
            [{'status': 'PAID'}],
            [{**self.paid(), 'amount': '29800'}],
            [{**self.paid(), 'amount': True}],
            # La documentación dice entero. Un importe como texto no es un importe.
            [as_text],
            ['PAID'],
        ):
            with self.subTest(str(broken)[:40]):
                self.fake.held[self.attempt.transaction_id] = broken
                with self.assertLogs('store', level='WARNING'):
                    self.ask()
                self.assert_still_waiting()


class WhatIsNotDecidedYetTest(ReconcileBase):
    def test_nothing_at_the_gateway_leaves_the_attempt_waiting(self):
        """Un comprador que cerró el formulario sin intentarlo. No es un rechazo."""
        for mode in ('ok', 'error'):
            with self.subTest(mode):
                self.fake.order_get_mode = mode
                summary = self.ask()
                self.assertEqual(summary.paid, 0)
                self.assert_still_waiting()

    def test_a_payment_in_progress_changes_nothing(self):
        self.fake.hold(self.attempt.transaction_id, self.paid(status='RUNNING'))
        self.aged(hours=1)

        self.ask()

        self.assert_still_waiting()

    def test_a_refusal_beside_something_still_open_is_not_a_refusal(self):
        self.fake.hold(
            self.attempt.transaction_id,
            self.paid(status='RUNNING', uuid='a' * 32), self.paid(status='UNPAID', uuid='b' * 32))
        self.aged(minutes=45)

        self.ask()

        self.assert_still_waiting()

    def test_a_refused_card_is_not_final_while_the_form_can_still_charge(self):
        """El formulario vive 15 minutos y deja reintentar: un rechazo temprano no cierra nada."""
        self.fake.hold(self.attempt.transaction_id, self.paid(status='UNPAID'))

        self.ask()

        self.assert_still_waiting()

    def test_a_refused_card_is_final_once_the_form_is_gone(self):
        self.fake.hold(self.attempt.transaction_id, self.paid(status='UNPAID'))
        self.aged(minutes=45)

        summary = self.ask()

        self.assertEqual(summary.rejected, 1)
        self.assert_nothing_paid()
        self.assertEqual(self.attempt.status, PaymentTransaction.Status.REJECTED)
        self.assertFalse(self.attempt.signature_verified)

    def test_a_gateway_that_does_not_answer_changes_nothing_and_ends_the_run(self):
        second = PaymentTransaction.objects.create(
            order=self.order, provider='micuentaweb', transaction_id='20000000000000000002',
            order_number='20000000000000000002', amount=Decimal('298.00'), currency='PEN')
        self.aged(second, minutes=8)
        for mode in ('unreachable', 'timeout', 'http_error', 'not_json'):
            with self.subTest(mode):
                self.fake.requests.clear()
                self.fake.mode = mode
                with self.assertLogs('store', level='ERROR') as logs:
                    summary = self.ask()
                self.assertTrue(summary.unreachable)
                # No se insiste a una pasarela que no contesta.
                self.assertEqual(len(self.asked()), 1)
                self.assertNotIn(MCW_SETTINGS['MICUENTAWEB_PASSWORD'], '\n'.join(logs.output))
                self.assert_still_waiting()


class WhichAttemptsAreAskedTest(ReconcileBase):
    def test_the_notification_is_given_time_before_anybody_asks(self):
        self.fake.hold(self.attempt.transaction_id, self.paid())
        self.aged(seconds=30)

        summary = self.ask()

        self.assertEqual((summary.asked, len(self.asked())), (0, 0))
        self.assert_still_waiting()

    def test_an_old_attempt_is_only_asked_about_when_somebody_widens_the_window(self):
        self.fake.hold(self.attempt.transaction_id, self.paid())
        self.aged(hours=5)

        self.ask()
        self.assertEqual(len(self.asked()), 0)
        self.assert_still_waiting()

        self.ask(window=timedelta(hours=48))
        self.refresh()
        self.assertTrue(self.order.paid)

    def test_an_attempt_that_already_has_its_answer_is_not_asked_about(self):
        for status in (PaymentTransaction.Status.AUTHORIZED, PaymentTransaction.Status.REJECTED,
                       PaymentTransaction.Status.INTEGRITY_FAILED):
            with self.subTest(status):
                PaymentTransaction.objects.filter(pk=self.attempt.pk).update(status=status)
                self.ask()
        self.assertEqual(len(self.asked()), 0)

    def test_one_run_asks_about_a_bounded_number_of_attempts_one_after_another(self):
        for n in range(3):
            extra = PaymentTransaction.objects.create(
                order=self.order, provider='micuentaweb', transaction_id=f'3000000000000000000{n}',
                order_number=f'3000000000000000000{n}', amount=Decimal('298.00'), currency='PEN')
            self.aged(extra, minutes=20 + n)

        summary = self.ask(limit=2)

        self.assertEqual((summary.asked, len(self.asked())), (2, 2))
        # Los más recientes primero: ahí está el comprador que sigue mirando la pantalla.
        self.assertEqual(
            [r['body']['orderId'] for r in self.asked()], [self.attempt.transaction_id, '30000000000000000000'])
        # Y lo que quedó sin consultar se dice, para que nadie crea que se miró todo.
        self.assertEqual(summary.left, 2)
        self.assertIn('sin consultar: 2', summary.line())

    def test_only_the_product_that_is_active_is_asked(self):
        self.fake.hold(self.attempt.transaction_id, self.paid())
        with override_settings(**IZIPAY_TEST_SETTINGS):
            summary = self.ask()

        self.assertEqual((summary.asked, len(self.fake.requests)), (0, 0))
        self.assert_still_waiting()

    def test_an_attempt_of_the_other_product_is_never_asked_about_here(self):
        PaymentTransaction.objects.filter(pk=self.attempt.pk).update(provider='izipay')
        self.fake.hold(self.attempt.transaction_id, self.paid())

        self.ask()

        self.assertEqual(len(self.fake.requests), 0)
        self.assert_still_waiting()

    def test_without_credentials_nothing_is_asked_and_it_says_so(self):
        with override_settings(MICUENTAWEB_PASSWORD=''):
            with self.assertLogs('store', level='ERROR'):
                summary = self.ask()

        self.assertTrue(summary.unconfigured)
        self.assertEqual(len(self.fake.requests), 0)
        self.assert_still_waiting()


class ChangedKeysTest(ReconcileBase):
    """Cambiar de claves no debe perder un cobro abierto, ni dejar que otro entorno lo confirme."""

    def test_a_password_renewed_in_the_same_shop_still_finds_the_open_payment(self):
        renewed = {**MCW_SETTINGS, 'MICUENTAWEB_PASSWORD': 'testpassword_OtraQueTampocoEsRealOtraQueTampocoEsRe'}
        with override_settings(**renewed):
            gateway = FakeMiCuentaWeb.from_settings(renewed)
            gateway.hold(self.attempt.transaction_id, self.paid())
            self.ask(gateway)

        self.assertEqual(gateway.violations, [])
        self.refresh()
        self.assertTrue(self.order.paid)

    def test_the_keys_of_another_shop_cannot_confirm_what_this_shop_opened(self):
        other = {**MCW_SETTINGS, 'MICUENTAWEB_SHOP_ID': '11111111',
                 'MICUENTAWEB_PUBLIC_KEY': '11111111:testpublickey_NoEsRealNoEsRealNoEsRealNoEsReal'}
        with override_settings(**other):
            gateway = FakeMiCuentaWeb.from_settings(other)
            # La otra tienda tiene, con la misma referencia, un pago que no es de este pedido.
            gateway.hold(self.attempt.transaction_id, self.paid(amount_cents=100))
            with self.assertLogs('store', level='WARNING'):
                self.ask(gateway)

        self.assert_nothing_paid()

    def test_an_attempt_opened_in_test_is_not_confirmed_by_production(self):
        with override_settings(**PRODUCTION):
            gateway = FakeMiCuentaWeb.from_settings(PRODUCTION)
            summary = self.ask(gateway)

        # Producción no tiene nada bajo esa referencia: el formulario era de TEST.
        self.assertEqual(summary.paid, 0)
        self.assert_still_waiting()


class CommandTest(ReconcileBase):
    def run_command(self, *args):
        out = io.StringIO()
        with self.fake.online():
            call_command('reconcile_payments', *args, stdout=out)
        return out.getvalue()

    def test_the_command_pays_what_was_charged_and_says_what_it_did(self):
        self.fake.hold(self.attempt.transaction_id, self.paid())

        text = self.run_command()

        self.refresh()
        self.assertTrue(self.order.paid)
        self.assertIn('consultados: 1', text)
        self.assertIn('pagados: 1', text)
        self.assertNotIn(MCW_SETTINGS['MICUENTAWEB_PASSWORD'], text)
        self.assertNotIn(self.attempt.transaction_id, text)

    def test_with_nothing_to_ask_it_says_nothing(self):
        PaymentTransaction.objects.filter(pk=self.attempt.pk).update(status=PaymentTransaction.Status.AUTHORIZED)

        self.assertEqual(self.run_command(), '')
        self.assertEqual(len(self.fake.requests), 0)

    def test_the_window_can_be_widened_for_a_customer_who_says_they_paid(self):
        self.fake.hold(self.attempt.transaction_id, self.paid())
        self.aged(hours=30)

        self.assertEqual(self.run_command(), '')
        self.assertIn('pagados: 1', self.run_command('--hours', '48'))
