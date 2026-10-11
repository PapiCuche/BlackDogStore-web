"""
PAGOS · lo que encontró la auditoría independiente de PAY-RECONCILE.

Cada clase es un hallazgo. Las sondas de la auditoría demostraban el DEFECTO
sobre el código anterior; estas pruebas fijan lo contrario.

  1. Un rechazo firmado no cierra un intento cuyo formulario aún puede cobrar.
  2. Un segundo cobro sobre un pedido ya pagado es un cobro duplicado, no un
     pago; y no se abre otro formulario sin preguntar antes por el anterior.
  3. Nadie se queda sin turno: lo que ya no puede cobrarse y la pasarela no
     tiene, se cierra; se pregunta primero por quien lleva más sin respuesta.
  4. Una respuesta que no se puede decidir queda escrita y da la alarma.
  5. El registro de una conciliación nace con el pago, en su transacción.
  6. Si la pasarela rechaza a quien llama, la pasada termina.
  7. Una respuesta cortada o desmedida es «no contestó», no una excepción.
  8. Un intento guarda entorno y tienda, y otra configuración no lo confirma.
  9. Un intento ya pagado no se vuelve a pagar, pase lo que pase con el pedido.
 10. La contraseña no sigue una redirección ni va a un servidor que no sea el oficial.
 11. Las credenciales no se imprimen.
"""
import http.server
import io
import threading
from datetime import timedelta
from decimal import Decimal
from unittest import mock, skipUnless
from urllib.parse import urlencode

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, connections
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone

from store import checkout_services as checkout
from store import views
from store.models import AdminAuditLog, Company, Order, PaymentTransaction, StockMovement
from store.payments import micuentaweb, reconcile
from store.payments.fake_izipay import FakeIzipay
from store.payments.fake_micuentaweb import FakeMiCuentaWeb
from store.test_micuentaweb import IPN_URL, MCW_SETTINGS, McwBase
from store.test_ops_status import run as health_check
from store.test_payment_reconcile import ReconcileBase
from store.tests import IZIPAY_TEST_SETTINGS, M5CheckoutBase, _m5_checkout_url

Status = PaymentTransaction.Status
OTHER_SHOP = {**MCW_SETTINGS, 'MICUENTAWEB_SHOP_ID': '11111111',
              'MICUENTAWEB_PUBLIC_KEY': '11111111:testpublickey_NoEsRealNoEsRealNoEsRealNoEsReal'}


class Base(ReconcileBase):
    def notify(self, gateway=None, **overrides):
        gateway = gateway or self.fake
        values = {'order_id': self.attempt.transaction_id, 'amount_cents': 29800}
        values.update(overrides)
        form = gateway.ipn(gateway.answer(**values))
        return self.client.post(IPN_URL, data=urlencode(form), content_type='application/x-www-form-urlencoded')

    def another_attempt(self, reference='20000000000000000002', **age):
        second = PaymentTransaction.objects.create(
            order=self.order, provider='micuentaweb', transaction_id=reference, order_number=reference,
            amount=Decimal('298.00'), currency='PEN', environment='test', shop='69876357')
        self.aged(second, **(age or {'minutes': 8}))
        return second

    def audits(self, action):
        return AdminAuditLog.objects.filter(action=action)


# -- 1 ---------------------------------------------------------------------------------

class RefusedIsNotFinalWhileTheFormLivesTest(Base):
    def test_a_signed_refusal_leaves_the_attempt_waiting_while_its_form_can_still_charge(self):
        self.assertEqual(self.notify(status='UNPAID').status_code, 200)

        self.assert_still_waiting()

    def test_refused_then_paid_in_the_same_form_with_the_paid_notification_lost_gets_paid(self):
        """El hueco: el rechazo cerraba el intento y a un intento cerrado ya nadie le preguntaba."""
        self.notify(status='UNPAID', uuid='a' * 32)
        self.fake.hold(self.attempt.transaction_id,
                       self.paid(status='UNPAID', uuid='a' * 32), self.paid(uuid='b' * 32))

        summary = self.ask()

        self.assertEqual(summary.paid, 1)
        self.refresh()
        self.assertTrue(self.order.paid)
        self.assertEqual((self.attempt.status, self.attempt.provider_unique_id), (Status.AUTHORIZED, 'b' * 32))
        self.assertEqual(self.stock(), 3)

    def test_refused_then_paid_with_the_paid_notification_arriving_pays_once(self):
        self.notify(status='UNPAID', uuid='a' * 32)
        self.assertEqual(self.notify(uuid='b' * 32).status_code, 200)
        self.fake.hold(self.attempt.transaction_id,
                       self.paid(status='UNPAID', uuid='a' * 32), self.paid(uuid='b' * 32))

        summary = self.ask()

        self.assertEqual((summary.asked, summary.paid), (0, 0))
        self.refresh()
        self.assertTrue(self.order.paid)
        self.assertEqual(StockMovement.objects.filter(order=self.order, movement_type='sale_exit').count(), 1)

    def test_a_refusal_nobody_followed_is_closed_once_the_form_is_gone(self):
        self.notify(status='UNPAID')
        self.fake.hold(self.attempt.transaction_id, self.paid(status='UNPAID'))
        self.assertEqual(self.ask().rejected, 0)
        self.assert_still_waiting()

        self.aged(minutes=45)
        self.assertEqual(self.ask().rejected, 1)
        self.refresh()
        self.assertEqual(self.attempt.status, Status.REJECTED)

    def test_a_signed_refusal_of_a_form_that_is_gone_closes_the_attempt_at_once(self):
        self.aged(minutes=45)
        self.assertEqual(self.notify(status='UNPAID').status_code, 200)

        self.refresh()
        self.assertEqual(self.attempt.status, Status.REJECTED)
        self.assertTrue(self.attempt.signature_verified)

    def test_the_form_lives_thirty_minutes_and_only_for_this_product(self):
        self.assertEqual(micuentaweb.FORM_LIFETIME, timedelta(minutes=30))
        self.assertEqual(views.MiCuentaWebNotificationView.form_lifetime, timedelta(minutes=30))
        # «SDK web / Checkout» no cambia: ahí un rechazo cierra el intento.
        self.assertIsNone(views.IzipayNotificationView.form_lifetime)
        for minutes, final in ((29, False), (31, True)):
            with self.subTest(minutes):
                PaymentTransaction.objects.filter(pk=self.attempt.pk).update(status=Status.PENDING)
                self.aged(minutes=minutes)
                self.notify(status='UNPAID')
                self.refresh()
                self.assertEqual(self.attempt.status == Status.REJECTED, final)


@override_settings(**IZIPAY_TEST_SETTINGS)
class TheOtherProductIsLeftAsItWasTest(McwBase):
    """«SDK web / Checkout»: ahí un rechazo firmado cierra el intento al momento, como siempre."""

    def test_a_signed_refusal_closes_its_attempt_at_once(self):
        gateway = FakeIzipay.from_settings(IZIPAY_TEST_SETTINGS)
        self.fake = gateway
        self.assertEqual(self.checkout().status_code, 200)
        attempt = PaymentTransaction.objects.get()

        payload = gateway.payload(transaction_id=attempt.transaction_id, order_number=attempt.order_number,
                                  amount=attempt.amount, code='05')
        response = self.client.post('/api/payments/izipay/notification/', gateway.envelope(payload), format='json')

        self.assertEqual(response.status_code, 200, response.content)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, Status.REJECTED)
        self.assertTrue(attempt.signature_verified)


# -- 2 ---------------------------------------------------------------------------------

class ASecondChargeIsNotAPaymentTest(Base):
    def setUp(self):
        super().setUp()
        self.second = self.another_attempt()
        self.assertEqual(self.notify().status_code, 200)             # el primer intento, pagado por su notificación

    def assert_filed_as_duplicate(self, source, signed):
        self.second.refresh_from_db()
        self.assertEqual(self.second.status, Status.DUPLICATE)
        self.assertEqual(self.second.signature_verified, signed)
        self.assertEqual(self.second.provider_unique_id, 'c' * 32)
        log = self.audits('payment_duplicate').get()
        self.assertEqual(log.company_id, self.order.company_id)
        self.assertEqual((log.target_type, log.target_id), ('order', str(self.order.pk)))
        self.assertEqual(log.metadata, {'provider': 'micuentaweb', 'attempt': self.second.pk, 'source': source,
                                        'gateway_transaction': 'c' * 32})
        # El pedido no se mueve: sigue pagado una vez, con su stock descontado una vez.
        self.assertEqual(self.stock(), 3)
        self.assertEqual(StockMovement.objects.filter(order=self.order, movement_type='sale_exit').count(), 1)
        self.assertEqual(PaymentTransaction.objects.filter(order=self.order, status=Status.AUTHORIZED).count(), 1)

    def test_a_second_paid_attempt_notified_is_a_duplicate_charge(self):
        with self.assertLogs('store.views', level='ERROR') as logs:
            response = self.notify(order_id=self.second.transaction_id, uuid='c' * 32)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Cobro duplicado', '\n'.join(logs.output))
        self.assert_filed_as_duplicate('notification', True)

    def test_a_second_paid_attempt_found_by_asking_is_a_duplicate_charge_and_counted_as_one(self):
        self.fake.hold(self.second.transaction_id, self.paid(order_id=self.second.transaction_id, uuid='c' * 32))

        with self.assertLogs('store.views', level='ERROR'):
            summary = self.ask()

        self.assertEqual((summary.paid, summary.duplicates), (0, 1))
        self.assertIn('COBROS DUPLICADOS: 1', summary.line())
        self.assertFalse(self.audits('payment_reconciled').exists())
        self.assert_filed_as_duplicate('Order/Get', False)

    def test_the_health_check_raises_the_alarm_for_a_duplicate_charge(self):
        with self.assertLogs('store.views', level='ERROR'):
            self.notify(order_id=self.second.transaction_id, uuid='c' * 32)

        _code, text = health_check()

        self.assertIn('ATENCIÓN pagos: 1 cobro duplicado', text)

    def test_the_same_message_again_does_not_file_it_twice(self):
        with self.assertLogs('store.views', level='ERROR'):
            self.notify(order_id=self.second.transaction_id, uuid='c' * 32)
        self.notify(order_id=self.second.transaction_id, uuid='c' * 32)

        self.assertEqual(self.audits('payment_duplicate').count(), 1)


class TwoAttemptsBothPaidInOneRunTest(Base):
    def test_one_is_the_payment_and_the_other_is_the_duplicate(self):
        second = self.another_attempt()
        self.fake.hold(self.attempt.transaction_id, self.paid(uuid='a' * 32))
        self.fake.hold(second.transaction_id, self.paid(order_id=second.transaction_id, uuid='c' * 32))

        with self.assertLogs('store.views', level='ERROR'):
            summary = self.ask()

        self.assertEqual((summary.paid, summary.duplicates), (1, 1))
        self.assertEqual(self.audits('payment_reconciled').count(), 1)
        self.assertEqual(self.audits('payment_duplicate').count(), 1)
        self.assertEqual(self.stock(), 3)


class AnotherFormForTheSameOrderTest(Base):
    """Un pedido con un formulario que aún puede cobrar: antes de abrir otro, se pregunta por el primero."""

    def open_another(self):
        with self.fake.online():
            return checkout.start_payment_attempt(self.order, credentials=micuentaweb.load_credentials())

    def created(self):
        return [r for r in self.fake.requests if r['url'].endswith('/Charge/CreatePayment')]

    def test_if_the_earlier_one_was_paid_there_is_no_second_form(self):
        self.fake.hold(self.attempt.transaction_id, self.paid())

        with self.assertRaises(checkout.CheckoutError) as refused:
            self.open_another()

        self.assertEqual((refused.exception.status_code, refused.exception.code), (409, 'order_already_paid'))
        self.refresh()
        self.assertTrue(self.order.paid)
        self.assertEqual(PaymentTransaction.objects.count(), 1)
        self.assertEqual(self.created(), [])

    def test_if_the_gateway_cannot_be_asked_there_is_no_second_form(self):
        for mode in ('unreachable', 'http_401', 'int_error'):
            with self.subTest(mode):
                self.fake.requests.clear()
                self.fake.mode = mode
                with self.assertLogs('store', level='WARNING'), self.assertRaises(checkout.CheckoutError) as refused:
                    self.open_another()
                self.assertEqual((refused.exception.status_code, refused.exception.code), (409, 'payment_in_progress'))
                self.assertEqual(PaymentTransaction.objects.count(), 1)
                self.assertEqual(self.created(), [])

    def test_if_the_earlier_one_holds_something_unsettled_there_is_no_second_form(self):
        self.fake.hold(self.attempt.transaction_id, self.paid(status='RUNNING'))

        with self.assertRaises(checkout.CheckoutError) as refused:
            self.open_another()

        self.assertEqual(refused.exception.code, 'payment_in_progress')
        self.assertEqual(PaymentTransaction.objects.count(), 1)

    def test_if_nothing_was_paid_the_second_form_opens_after_asking(self):
        session = self.open_another()

        self.assertEqual(session.form_token, self.fake.form_token)
        self.assertEqual(PaymentTransaction.objects.count(), 2)
        urls = [r['url'].rsplit('/', 2)[-2:] for r in self.fake.requests]
        self.assertEqual(urls, [['Order', 'Get'], ['Charge', 'CreatePayment']])
        self.assertEqual(self.fake.requests[0]['body'], {'orderId': self.attempt.transaction_id})

    def test_an_earlier_form_that_can_no_longer_charge_is_not_asked_about(self):
        self.aged(minutes=45)

        self.open_another()

        self.assertEqual(len(self.asked()), 0)
        self.assertEqual(PaymentTransaction.objects.count(), 2)


@override_settings(**MCW_SETTINGS)
class AReplayFromTheAppTest(M5CheckoutBase):
    """El camino que de verdad abre dos formularios para un pedido: la app repite su petición con la misma clave."""

    def test_a_replay_of_an_order_the_gateway_already_charged_gets_no_second_form(self):
        fake = FakeMiCuentaWeb.from_settings(MCW_SETTINGS)
        with fake.online():
            first = self.client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')
        self.assertIn(first.status_code, (200, 201), first.content)
        attempt = PaymentTransaction.objects.get()
        fake.hold(attempt.transaction_id, fake.transaction(order_id=attempt.transaction_id, amount_cents=400000))

        with fake.online():
            replay = self.client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')

        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertIsNone(replay.json()['payment'])
        self.assertEqual(replay.json()['status'], 'paid')
        self.assertEqual(PaymentTransaction.objects.count(), 1)
        self.assertTrue(Order.objects.get().paid)

    def test_a_replay_of_an_order_nobody_paid_still_gets_its_form(self):
        fake = FakeMiCuentaWeb.from_settings(MCW_SETTINGS)
        with fake.online():
            self.client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')
            replay = self.client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')

        self.assertEqual(replay.json()['payment']['form_token'], fake.form_token)
        self.assertEqual(PaymentTransaction.objects.count(), 2)


# -- 3 ---------------------------------------------------------------------------------

class NobodyIsStarvedTest(Base):
    def abandoned_checkouts(self, count, start=5):
        for n in range(count):
            reference = f'400000000000000000{n:02d}'
            extra = PaymentTransaction.objects.create(
                order=self.order, provider='micuentaweb', transaction_id=reference, order_number=reference,
                amount=Decimal('298.00'), currency='PEN')
            self.aged(extra, minutes=start + n)

    def test_twenty_newer_abandoned_checkouts_do_not_keep_an_older_paid_one_waiting(self):
        self.aged(minutes=40)
        self.fake.hold(self.attempt.transaction_id, self.paid())
        self.abandoned_checkouts(20)

        self.ask()

        self.refresh()
        self.assertTrue(self.order.paid)

    def test_whoever_was_never_asked_goes_before_whoever_was(self):
        self.abandoned_checkouts(3)
        first = [r['body']['orderId'] for r in (self.ask(limit=2), self.asked())[1]]
        self.fake.requests.clear()
        second = [r['body']['orderId'] for r in (self.ask(limit=2), self.asked())[1]]

        # Los dos más antiguos primero; en la pasada siguiente, los dos que no se habían preguntado.
        self.assertEqual(first, [self.attempt.transaction_id, '40000000000000000002'])
        self.assertEqual(second, ['40000000000000000001', '40000000000000000000'])

    def test_one_run_asks_about_twenty_attempts_at_most(self):
        self.assertEqual(reconcile.LIMIT, 20)
        self.abandoned_checkouts(24)

        summary = self.ask()

        self.assertEqual((summary.asked, summary.left), (20, 5))

    def test_an_attempt_nobody_tried_is_closed_once_its_form_is_gone_and_asked_about_no_more(self):
        self.aged(minutes=45)

        summary = self.ask()

        self.assertEqual((summary.abandoned, summary.waiting), (1, 0))
        self.assertIn('cerrados sin intento: 1', summary.line())
        self.refresh()
        self.assertEqual(self.attempt.status, Status.ABANDONED)
        self.assertFalse(self.order.paid)
        self.fake.requests.clear()
        self.assertEqual(self.ask().asked, 0)

    def test_while_its_form_lives_it_stays_open_and_remembers_when_it_was_asked(self):
        summary = self.ask()

        self.assertEqual((summary.abandoned, summary.waiting), (0, 1))
        self.refresh()
        self.assertEqual(self.attempt.status, Status.PENDING)
        self.assertIsNotNone(self.attempt.gateway_checked_at)

    def test_a_notification_that_arrives_after_all_still_pays_a_closed_attempt(self):
        self.aged(minutes=45)
        self.ask()

        self.assertEqual(self.notify().status_code, 200)

        self.refresh()
        self.assertTrue(self.order.paid)
        self.assertEqual(self.attempt.status, Status.AUTHORIZED)

    def test_the_window_and_the_wait_are_what_the_documents_say(self):
        self.assertEqual((reconcile.MIN_AGE, reconcile.WINDOW), (timedelta(minutes=3), timedelta(hours=2)))
        for minutes, asked in ((2, 0), (4, 1), (119, 1), (121, 0)):
            with self.subTest(minutes):
                self.fake.requests.clear()
                PaymentTransaction.objects.filter(pk=self.attempt.pk).update(status=Status.PENDING)
                self.aged(minutes=minutes)
                self.assertEqual(self.ask().asked, asked)


class OneReferenceTest(Base):
    def command(self, *args):
        out = io.StringIO()
        with self.fake.online():
            call_command('reconcile_payments', *args, stdout=out)
        return out.getvalue()

    def test_a_customer_who_paid_days_ago_is_found_by_the_reference_of_the_payment(self):
        self.aged(hours=80)
        self.fake.hold(self.attempt.transaction_id, self.paid())
        other = self.another_attempt('20000000000000000009', hours=80)

        text = self.command('--reference', self.attempt.transaction_id)

        self.assertIn('pagados: 1', text)
        self.assertEqual([r['body']['orderId'] for r in self.asked()], [self.attempt.transaction_id])
        self.refresh()
        self.assertTrue(self.order.paid)
        other.refresh_from_db()
        self.assertEqual(other.status, Status.PENDING)

    def test_an_attempt_closed_as_abandoned_or_refused_can_still_be_asked_about_by_reference(self):
        for closed in (Status.ABANDONED, Status.REJECTED):
            with self.subTest(closed):
                self.fake.requests.clear()
                PaymentTransaction.objects.filter(pk=self.attempt.pk).update(status=closed)
                self.assertEqual(self.ask().asked, 0)
                self.assertEqual(self.ask(reference=self.attempt.transaction_id).asked, 1)

    def test_an_attempt_that_has_its_payment_is_not_asked_about_again(self):
        self.notify()

        text = self.command('--reference', self.attempt.transaction_id)

        self.assertIn('No hay ningún intento', text)
        self.assertEqual(self.asked(), [])

    def test_a_reference_of_the_other_product_or_of_nobody_asks_nothing(self):
        PaymentTransaction.objects.filter(pk=self.attempt.pk).update(provider='izipay')
        for reference in (self.attempt.transaction_id, '00000000000000000000'):
            self.assertIn('No hay ningún intento', self.command('--reference', reference))
        self.assertEqual(self.fake.requests, [])

    def test_zero_hours_is_refused_instead_of_meaning_two(self):
        for hours in ('0', '-3'):
            with self.assertRaises(CommandError):
                self.command('--hours', hours)
        self.assertEqual(self.fake.requests, [])


# -- 4 ---------------------------------------------------------------------------------

class AnAnswerNobodyCanDecideTest(Base):
    def setUp(self):
        super().setUp()
        self.fake.hold(self.attempt.transaction_id, self.paid(uuid='a' * 32), self.paid(uuid='b' * 32))

    def test_it_is_written_down_the_first_time_and_only_then(self):
        for _ in range(3):
            with self.assertLogs('store', level='WARNING'):
                self.ask()

        log = self.audits('payment_undecided').get()
        self.assertEqual(log.company_id, self.order.company_id)
        self.assertEqual((log.target_type, log.target_id), ('order', str(self.order.pk)))
        self.assertEqual(log.metadata, {'provider': 'micuentaweb', 'attempt': self.attempt.pk,
                                        'reason': 'Hay más de un cobro pagado bajo esta referencia.'})
        self.assert_still_waiting()
        self.assertTrue(self.attempt.failure_reason.startswith('undecided: '))

    def test_the_health_check_raises_the_alarm(self):
        self.assertNotIn('que no se pudo decidir', health_check()[1])
        with self.assertLogs('store', level='WARNING'):
            self.ask()

        self.assertIn('ATENCIÓN pagos: 1 pago con una respuesta de la pasarela que no se pudo decidir', health_check()[1])

    def test_once_it_is_settled_the_alarm_stops(self):
        with self.assertLogs('store', level='WARNING'):
            self.ask()
        self.notify()

        self.assertNotIn('que no se pudo decidir', health_check()[1])


# -- 5 ---------------------------------------------------------------------------------

class TheRecordIsBornWithThePaymentTest(Base):
    def test_the_audit_record_says_who_was_asked_and_which_transaction_answered(self):
        self.fake.hold(self.attempt.transaction_id, self.paid())

        self.ask()

        self.assertEqual(self.audits('payment_reconciled').get().metadata, {
            'provider': 'micuentaweb', 'attempt': self.attempt.pk, 'source': 'Order/Get',
            'gateway_transaction': '9d6b9c5d4f7e4f0e8a1c2b3d4e5f6a7b', 'mode': 'TEST',
        })

    def test_if_the_payment_does_not_happen_neither_does_its_record(self):
        self.fake.hold(self.attempt.transaction_id, self.paid())
        with mock.patch.object(views, 'emit_payment_confirmed', side_effect=RuntimeError('se cayó a mitad')):
            with self.assertRaises(RuntimeError):
                self.ask()

        self.assertFalse(self.audits('payment_reconciled').exists())
        self.assert_still_waiting()

    def test_a_settled_attempt_asked_about_again_is_not_a_second_reconciliation(self):
        self.fake.hold(self.attempt.transaction_id, self.paid())
        self.ask()

        credentials = micuentaweb.load_credentials()
        with self.fake.online():
            outcome = reconcile.ask(self.attempt, credentials)

        self.assertEqual(outcome, reconcile.ALREADY)
        self.assertEqual(self.audits('payment_reconciled').count(), 1)


@skipUnless(connection.vendor == 'postgresql', 'several writers at once need a database that takes them')
@override_settings(**MCW_SETTINGS)
class SeveralRunsAtOnceTest(TransactionTestCase):
    """Lo de verdad: tres pasadas a la vez, cada una con su conexión."""

    def setUp(self):
        # Sus propios datos: las pruebas con commits de verdad vacían la base entre una y otra.
        cache.clear()
        self.fake = FakeMiCuentaWeb.from_settings(MCW_SETTINGS)
        order = Order.objects.create(
            company=Company.objects.create(name='Tienda a la vez', slug='tienda-a-la-vez-pagos'),
            total=Decimal('298.00'), customer_email='ana@example.com')
        self.attempt = PaymentTransaction.objects.create(
            order=order, provider='micuentaweb', transaction_id='60000000000000000001',
            order_number='60000000000000000001', amount=Decimal('298.00'), currency='PEN',
            environment='test', shop='69876357')
        PaymentTransaction.objects.filter(pk=self.attempt.pk).update(created_at=timezone.now() - timedelta(minutes=10))
        self.fake.hold(self.attempt.transaction_id,
                       self.fake.transaction(order_id=self.attempt.transaction_id, amount_cents=29800))

    def test_three_runs_at_once_are_one_payment_one_record_and_one_count(self):
        barrier, results, errors = threading.Barrier(3), [], []

        def run():
            try:
                barrier.wait(timeout=10)
                results.append(reconcile.reconcile_micuentaweb())
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))
            finally:
                connections.close_all()

        with self.fake.online(), mock.patch.object(views, 'send_order_emails_after_payment'):
            threads = [threading.Thread(target=run) for _ in range(3)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)

        self.assertEqual(errors, [])
        self.assertEqual(sum(summary.paid for summary in results), 1)
        self.assertEqual(AdminAuditLog.objects.filter(action='payment_reconciled').count(), 1)
        self.assertTrue(Order.objects.get(pk=self.attempt.order_id).paid)
        self.assertEqual(PaymentTransaction.objects.get(pk=self.attempt.pk).status, Status.AUTHORIZED)


# -- 6 and 7 -----------------------------------------------------------------------------

class AGatewayThatRefusesOrBreaksTest(Base):
    def setUp(self):
        super().setUp()
        self.second = self.another_attempt()

    def test_a_refusal_about_the_caller_ends_the_run_and_says_so(self):
        for mode in ('http_401', 'http_403', 'http_429', 'int_error'):
            with self.subTest(mode):
                self.fake.requests.clear()
                self.fake.mode = mode
                with self.assertLogs('store', level='WARNING') as logs:
                    summary = self.ask()
                self.assertTrue(summary.refused)
                self.assertFalse(summary.unreachable)
                self.assertEqual((summary.asked, summary.left, len(self.asked())), (1, 1, 1))
                self.assertIn('rechazó la consulta', summary.line())
                self.assertNotIn(MCW_SETTINGS['MICUENTAWEB_PASSWORD'], '\n'.join(logs.output))
                self.assert_still_waiting()

    def test_an_error_about_one_reference_does_not_end_the_run(self):
        """Un 4xx o un error de la pasarela sobre ESA referencia no es «la pasarela no responde»."""
        for setting in ({'mode': 'http_404'}, {'order_get_mode': 'error'}):
            with self.subTest(str(setting)):
                self.fake.requests.clear()
                self.fake.mode, self.fake.order_get_mode = 'ok', 'ok'
                for name, value in setting.items():
                    setattr(self.fake, name, value)
                with self.assertLogs('store', level='WARNING'):
                    summary = self.ask()
                self.assertEqual((summary.asked, summary.errors, len(self.asked())), (2, 2, 2))
                self.assertFalse(summary.unreachable or summary.refused)
                self.assertEqual(sorted(summary.unresolved), sorted([self.attempt.pk, self.second.pk]))
                self.assertIn('errores de la pasarela: 2', summary.line())
                self.assert_still_waiting()

    def test_what_the_gateway_wrote_in_its_error_never_reaches_the_log(self):
        self.fake.order_get_mode = 'error'
        self.fake.order_get_error = 'PSP_' + 'X' * 60
        with self.assertLogs('store', level='WARNING') as logs:
            self.ask()

        text = '\n'.join(logs.output)
        self.assertNotIn('no such order', text)                              # the gateway's own message
        self.assertNotIn(MCW_SETTINGS['MICUENTAWEB_PASSWORD'], text)         # …which here even quotes the password
        self.assertIn('errorCode=PSP_' + 'X' * 16 + ')', text)               # the code, cut at 20 characters

    def test_an_answer_cut_short_garbled_or_enormous_is_a_gateway_that_did_not_answer(self):
        # «padded»: cortada por el tope seguiría siendo JSON válido. No se lee a medias: no es una respuesta.
        for mode in ('cut_short', 'garbled', 'huge', 'padded'):
            with self.subTest(mode):
                self.fake.requests.clear()
                self.fake.mode = mode
                with self.assertLogs('store', level='ERROR'):
                    summary = self.ask()
                self.assertTrue(summary.unreachable)
                self.assertEqual((summary.asked, summary.left), (1, 1))
                self.assert_still_waiting()

    def test_no_answer_is_read_beyond_a_bound(self):
        from store.payments import fake_micuentaweb

        fake_micuentaweb.READ_LIMITS.clear()
        self.ask()

        self.assertEqual(fake_micuentaweb.READ_LIMITS, [micuentaweb.MAX_ANSWER_BYTES + 1] * 2)
        self.assertEqual(micuentaweb.MAX_ANSWER_BYTES, 2 * 1024 * 1024)

    def test_the_checkout_survives_a_garbled_answer_too(self):
        self.fake.mode = 'garbled'
        with self.assertLogs('store', level='ERROR'), self.assertRaises(micuentaweb.MiCuentaWebUnreachable):
            with self.fake.online():
                micuentaweb.create_payment(credentials=micuentaweb.load_credentials(), order_id='1' * 20,
                                           amount=Decimal('1.80'))


# -- 8 ---------------------------------------------------------------------------------

class AnotherConfigurationDoesNotConfirmItTest(Base):
    def test_the_attempt_keeps_the_environment_and_the_shop_it_was_opened_with(self):
        self.assertEqual((self.attempt.environment, self.attempt.shop), ('test', '69876357'))

    def test_the_same_reference_in_another_shop_is_another_payment(self):
        with override_settings(**OTHER_SHOP):
            gateway = FakeMiCuentaWeb.from_settings(OTHER_SHOP)
            gateway.hold(self.attempt.transaction_id, self.paid(shop_id='11111111'))
            with self.assertLogs('store.views', level='ERROR') as logs:
                summary = self.ask(gateway)

        self.assertEqual((summary.paid, summary.mismatched), (0, 1))
        self.assertIn('Consulta a la pasarela con fallo de integridad', '\n'.join(logs.output))
        self.assert_nothing_paid()
        self.assertEqual((self.attempt.status, self.attempt.failure_reason), (Status.INTEGRITY_FAILED, 'shop mismatch'))
        self.assertFalse(self.attempt.signature_verified)
        log = self.audits('payment_integrity_failed').get()
        self.assertEqual(log.company_id, self.order.company_id)
        self.assertEqual(log.metadata, {'provider': 'micuentaweb', 'attempt': self.attempt.pk,
                                        'reason': 'shop mismatch', 'source': 'Order/Get'})

    def test_nor_does_a_notification_signed_by_the_other_shop_confirm_it(self):
        with override_settings(**OTHER_SHOP):
            gateway = FakeMiCuentaWeb.from_settings(OTHER_SHOP)
            with self.assertLogs('store.views', level='ERROR') as logs:
                response = self.notify(gateway)

        self.assertEqual(response.status_code, 400)
        self.assertIn('IPN con fallo de integridad', '\n'.join(logs.output))
        self.assert_nothing_paid()
        self.assertEqual(self.attempt.failure_reason, 'shop mismatch')
        self.assertEqual(self.audits('payment_integrity_failed').get().metadata['source'], 'notification')

    def test_an_attempt_opened_in_production_is_not_confirmed_by_test_keys(self):
        PaymentTransaction.objects.filter(pk=self.attempt.pk).update(environment='production')
        with self.assertLogs('store.views', level='ERROR'):
            self.assertEqual(self.notify().status_code, 400)                 # una notificación de TEST, bien firmada
        self.assert_nothing_paid()
        self.assertEqual(self.attempt.failure_reason, 'environment mismatch')

        PaymentTransaction.objects.filter(pk=self.attempt.pk).update(status=Status.PENDING)
        self.fake.hold(self.attempt.transaction_id, self.paid())             # y una transacción de TEST
        with self.assertLogs('store.views', level='ERROR'):
            self.assertEqual(self.ask().mismatched, 1)
        self.assert_nothing_paid()

    def test_an_attempt_older_than_these_columns_is_judged_as_it_always_was(self):
        PaymentTransaction.objects.filter(pk=self.attempt.pk).update(environment='', shop='')
        self.fake.hold(self.attempt.transaction_id, self.paid())

        self.assertEqual(self.ask().paid, 1)


# -- 9 ---------------------------------------------------------------------------------

class SettledOnceTest(Base):
    def test_a_repeated_paid_notification_does_not_pay_again_an_order_that_left_paid(self):
        self.notify()
        for status in (Order.Status.REFUNDED, Order.Status.CANCELLED):
            with self.subTest(status):
                Order.objects.filter(pk=self.order.pk).update(status=status, paid=False)
                self.assertEqual(self.notify().status_code, 200)
                self.refresh()
                self.assertEqual((self.order.status, self.order.paid), (status, False))
        self.assertEqual(StockMovement.objects.filter(order=self.order, movement_type='sale_exit').count(), 1)


# -- 10 and 11 ---------------------------------------------------------------------------

class WhereThePasswordGoesTest(SimpleTestCase):
    def credentials(self, **overrides):
        values = {'MICUENTAWEB_SHOP_ID': '69876357', 'MICUENTAWEB_PASSWORD': MCW_SETTINGS['MICUENTAWEB_PASSWORD'],
                  'MICUENTAWEB_PUBLIC_KEY': MCW_SETTINGS['MICUENTAWEB_PUBLIC_KEY']}
        values.update(overrides)
        return micuentaweb.credentials_from(values)

    def test_only_to_the_server_the_documentation_names_and_only_over_https(self):
        self.assertEqual(self.credentials().api_url, 'https://api.micuentaweb.pe')
        self.assertEqual(self.credentials(MICUENTAWEB_API_URL='https://api.micuentaweb.pe/').api_url,
                         'https://api.micuentaweb.pe')
        for elsewhere in ('http://api.micuentaweb.pe', 'https://api.micuentaweb.pe.example.com',
                          'https://example.com/api.micuentaweb.pe', 'https://api.micuentaweb.pe@example.com'):
            with self.subTest(elsewhere), self.assertRaises(micuentaweb.MiCuentaWebError):
                self.credentials(MICUENTAWEB_API_URL=elsewhere)

    def test_a_redirect_is_not_followed_so_the_password_is_sent_once_and_nowhere_else(self):
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append((self.path, self.headers.get('Authorization')))
                self.rfile.read(int(self.headers.get('Content-Length') or 0))
                # 302: the one a stock client follows after a POST, carrying its headers along.
                self.send_response(302)
                self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}/elsewhere')
                self.end_headers()

            def do_GET(self):
                seen.append((self.path, self.headers.get('Authorization')))
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status":"SUCCESS","answer":{}}')

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            credentials = micuentaweb.MiCuentaWebCredentials(
                environment='test', shop_id='69876357', password='testpassword_NoEsReal', public_key='',
                api_url=f'http://127.0.0.1:{server.server_port}', currency='PEN', ipn_url='')
            with self.assertLogs('store', level='ERROR'), self.assertRaises(micuentaweb.MiCuentaWebUnreachable):
                micuentaweb.get_order_transactions(credentials=credentials, order_id='1' * 20, timeout=5)
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual([path for path, _ in seen], ['/api-payment/V4/Order/Get'])

    def test_the_credentials_do_not_print_their_password(self):
        credentials = self.credentials()
        for text in (repr(credentials), str(credentials), f'{credentials}', f'{credentials!r}'):
            self.assertNotIn(MCW_SETTINGS['MICUENTAWEB_PASSWORD'], text)
        self.assertIn('69876357', repr(credentials))
        self.assertEqual(credentials.password, MCW_SETTINGS['MICUENTAWEB_PASSWORD'])


class TheConsoleSaysItOnTheFieldTest(SimpleTestCase):
    def test_an_api_address_that_is_not_the_official_one_is_refused_when_saving(self):
        from store.integrations import registry
        from store.integrations.registry import ConfigError

        provider = registry.get('izipay_micuentaweb')
        public = {'shop_id': '69876357', 'public_key': MCW_SETTINGS['MICUENTAWEB_PUBLIC_KEY']}
        provider.clean({**public, 'api_url': 'https://api.micuentaweb.pe'}, {})
        provider.clean(public, {})
        with self.assertRaises(ConfigError) as refused:
            provider.clean({**public, 'api_url': 'https://pasarela.example.com'}, {})
        self.assertIn('api_url', refused.exception.errors)


# -- rules that no test pinned -------------------------------------------------------------

class WhatIsBelievedOfANotificationTest(Base):
    def test_a_hash_made_with_the_password_but_declared_as_the_browsers_is_refused(self):
        form = self.fake.ipn(self.fake.answer(order_id=self.attempt.transaction_id, amount_cents=29800),
                             hash_key='sha256_hmac')
        response = self.client.post(IPN_URL, data=urlencode(form), content_type='application/x-www-form-urlencoded')

        self.assertEqual(response.status_code, 400)
        self.assert_nothing_paid()

    def test_an_answer_that_names_no_shop_is_not_one_of_this_products_answers(self):
        answer = self.fake.answer(order_id=self.attempt.transaction_id, amount_cents=29800)
        del answer['shopId']
        form = self.fake.ipn(answer)
        with self.assertLogs('store.views', level='ERROR'):
            response = self.client.post(IPN_URL, data=urlencode(form), content_type='application/x-www-form-urlencoded')

        self.assertEqual(response.status_code, 400)
        self.assert_nothing_paid()
        self.assertEqual(self.attempt.failure_reason, 'merchant missing')

    def test_an_amount_that_is_text_is_not_an_amount(self):
        answer = self.fake.answer(order_id=self.attempt.transaction_id, amount_cents=29800)
        answer['orderDetails']['orderTotalAmount'] = '29800'
        form = self.fake.ipn(answer)

        response = self.client.post(IPN_URL, data=urlencode(form), content_type='application/x-www-form-urlencoded')

        self.assertEqual(response.status_code, 400)
        self.assert_still_waiting()

    def test_a_result_that_does_not_say_it_was_signed_is_not_taken_for_signed(self):
        self.assertFalse(views._signed(object()))
        self.assertFalse(views._signed(mock.Mock(signed='yes')))
        self.assertTrue(views._signed(mock.Mock(signed=True)))

    def test_what_was_not_signed_is_never_recorded_as_signed_whatever_became_of_it(self):
        """Fallo de integridad y cobro duplicado, encontrados preguntando: sin firma verificada."""
        self.fake.hold(self.attempt.transaction_id, self.paid(amount_cents=100))
        with self.assertLogs('store.views', level='ERROR'):
            self.ask()
        self.refresh()
        self.assertEqual((self.attempt.status, self.attempt.signature_verified), (Status.INTEGRITY_FAILED, False))

    def test_a_gateway_that_does_not_answer_leaves_the_rest_counted_as_not_asked(self):
        self.another_attempt()
        self.fake.mode = 'unreachable'
        with self.assertLogs('store', level='ERROR'):
            summary = self.ask()

        self.assertEqual((summary.asked, summary.left), (1, 1))
        self.assertIn('sin consultar: 1', summary.line())
