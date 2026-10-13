"""
OPS-STATUS: what somebody operating the shop has to be told without asking.

`python manage.py ops_status` reads; it changes nothing. It answers with one line
per thing it looked at and exits non-zero when any of them needs a person:

  · migrations that were not applied (a deploy that stopped half way);
  · a payment notification that failed its integrity check;
  · a payment still waiting long after the buyer left (the notification from the
    gateway may never have arrived, and nothing asks the gateway — PAY-RECONCILE);
  · WhatsApp messages that failed for good, or that nobody is sending.

`deploy/healthcheck.sh` runs it next to the checks that only the host can make.
"""
import io
from datetime import timedelta
from unittest import mock

from django.core.management import call_command
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from store.models import NotificationDelivery, PaymentTransaction
from store.test_whatsapp import WhatsAppBase
from store.integrations import registry, service
from store.models import IntegrationConfig
from store.tests import IZIPAY_TEST_SETTINGS, _order, _pay_attempt


def run():
    """
    The health check of A PUBLISHED SHOP: `PAYMENTS_PUBLIC_TEST` off, as it is
    there. The test suite itself runs with that switch on (its simulated
    gateways charge anybody: `store.suite_runner`), and the command — rightly —
    raises the alarm about that; these tests are about everything else. What
    the command says with the switch on is tested where it is turned on
    (`test_payment_audit3`).
    """
    out = io.StringIO()
    try:
        with override_settings(PAYMENTS_PUBLIC_TEST=False):
            call_command('ops_status', stdout=out)
        code = 0
    except SystemExit as exc:
        code = int(exc.code or 0)
    return code, out.getvalue()


#: An installation that can send mail and charge, the way a published one is.
CONFIGURED = {**IZIPAY_TEST_SETTINGS, 'PAYMENT_PROVIDER': 'izipay',
              'EMAIL_BACKEND_LEGACY': 'django.core.mail.backends.smtp.EmailBackend',
              'EMAIL_HOST': 'smtp.entorno.invalid', 'EMAIL_HOST_USER': 'usuario-del-entorno',
              'EMAIL_HOST_PASSWORD': 'clave-del-entorno-NoEsReal'}


@override_settings(**CONFIGURED)
class OpsStatusTest(WhatsAppBase):
    def test_a_quiet_installation_is_all_right(self):
        code, text = run()
        self.assertEqual(code, 0, text)
        self.assertIn('OK    migraciones', text)
        self.assertIn('OK    pagos', text)
        self.assertIn('OK    WhatsApp', text)
        self.assertNotIn('ATENCIÓN', text)

    def test_it_writes_nothing(self):
        wrote = AssertionError('ops_status wrote to the database')
        with mock.patch('django.db.models.Model.save', side_effect=wrote), \
                mock.patch('django.db.models.QuerySet.update', side_effect=wrote), \
                mock.patch('django.db.models.QuerySet.delete', side_effect=wrote), \
                mock.patch('django.db.models.QuerySet.create', side_effect=wrote):
            self.assertEqual(run()[0], 0)

    def test_migrations_that_were_not_applied_are_reported(self):
        with mock.patch('store.management.commands.ops_status.pending_migrations', return_value=['store.0999_x']):
            code, text = run()
        self.assertEqual(code, 1)
        self.assertIn('ATENCIÓN migraciones: 1 sin aplicar', text)

    def test_a_payment_notification_that_failed_integrity_is_reported(self):
        attempt = _pay_attempt(_order(self.company, total='50.00'))
        PaymentTransaction.objects.filter(pk=attempt.pk).update(
            status=PaymentTransaction.Status.INTEGRITY_FAILED)
        code, text = run()
        self.assertEqual(code, 1)
        self.assertIn('ATENCIÓN pagos: 1 notificación', text)

    def test_an_old_integrity_failure_is_history_not_news(self):
        attempt = _pay_attempt(_order(self.company, total='50.00'))
        PaymentTransaction.objects.filter(pk=attempt.pk).update(
            status=PaymentTransaction.Status.INTEGRITY_FAILED, created_at=timezone.now() - timedelta(days=3))
        self.assertEqual(run()[0], 0)

    def test_a_payment_nobody_finished_is_a_note_not_an_alarm(self):
        """
        Every checkout opens a payment, and a buyer who closes the card form
        leaves it waiting for ever: that is an ordinary day. It cannot be told
        apart from a notification that never arrived, so it is said — for the day
        a customer claims to have paid — and it does not fail the check.
        """
        attempt = _pay_attempt(_order(self.company, total='50.00'))
        self.assertNotIn('NOTA', run()[1], 'a payment opened a moment ago is a buyer typing a card')
        PaymentTransaction.objects.filter(pk=attempt.pk).update(created_at=timezone.now() - timedelta(hours=2))
        code, text = run()
        self.assertEqual(code, 0, text)
        self.assertIn('NOTA  pagos: 1 pago', text)
        self.assertIn('sin respuesta de la pasarela', text)
        # What to do about it is said too: the gateway can be asked (PAY-RECONCILE).
        self.assertIn('reconcile_payments', text)
        self.assertNotIn('ATENCIÓN', text)

    def _deliveries(self, count):
        return [self.delivery(self.new_order()) for _ in range(count)]

    def _fail_for_good(self, deliveries):
        NotificationDelivery.objects.filter(pk__in=[d.pk for d in deliveries]).update(
            status=NotificationDelivery.Status.FAILED, next_attempt_at=None, failure_reason='131026')

    def test_one_message_that_could_not_be_delivered_is_a_note(self):
        """A number that is not on WhatsApp is a fact about that customer, shown in the order."""
        first, *others = self._deliveries(3)
        self._fail_for_good([first])
        code, text = run()
        self.assertEqual(code, 0, text)
        self.assertIn('NOTA  WhatsApp: 1 mensaje', text)

    def test_messages_failing_while_none_gets_through_is_an_alarm(self):
        """Three failures and not one success: a token that expired, a template that was rejected."""
        self._fail_for_good(self._deliveries(3))
        code, text = run()
        self.assertEqual(code, 1)
        self.assertIn('ATENCIÓN WhatsApp: 3 mensajes', text)
        self.assertIn('ninguno', text)

    def test_a_message_that_was_never_sent_is_reported(self):
        """
        Exactly as the application leaves it when nobody sends: PENDING, with no
        next attempt scheduled. The sender finds these by their age.
        """
        delivery = self.delivery(self.new_order())
        NotificationDelivery.objects.filter(pk=delivery.pk).update(
            status=NotificationDelivery.Status.PENDING, next_attempt_at=None,
            created_at=timezone.now() - timedelta(hours=1))
        code, text = run()
        self.assertEqual(code, 1)
        self.assertIn('sin enviar', text)
        self.assertIn('send_pending_notifications', text)

    def test_a_retry_that_is_overdue_is_reported(self):
        delivery = self.delivery(self.new_order())
        NotificationDelivery.objects.filter(pk=delivery.pk).update(
            status=NotificationDelivery.Status.FAILED, next_attempt_at=timezone.now() - timedelta(hours=1))
        code, text = run()
        self.assertEqual(code, 1)
        self.assertIn('sin enviar', text)

    def test_a_message_sent_a_moment_ago_is_not_overdue(self):
        self.delivery(self.new_order())
        self.assertEqual(run()[0], 0)

    def test_no_line_names_a_customer_a_phone_or_a_token(self):
        NotificationDelivery.objects.filter(pk=self.delivery(self.new_order()).pk).update(
            status=NotificationDelivery.Status.FAILED, next_attempt_at=None, failure_reason='131026')
        _code, text = run()
        self.assertNotIn('987', text)
        # As a word: the summary names «Google Analytics 4», and the customer is called Ana.
        self.assertNotRegex(text, rf'\b{self.customer.first_name}\b')


SMTP_PUBLIC = {'host': 'smtp.example.invalid', 'port': 587, 'security': 'starttls', 'username': 'usuario-smtp',
               'from_email': 'tienda@example.invalid', 'from_name': 'Tienda', 'timeout': 10}
SMTP_PASSWORD = 'clave-smtp-NoEsReal'
KEY = Fernet.generate_key().decode()


@override_settings(APP_CONFIG_ENCRYPTION_KEY=KEY, **CONFIGURED)
class IntegrationsStatusTest(TestCase):
    """
    What the console configured, as an operator needs it: which integrations
    run, from where, and whether one of them cannot. Never a value.
    """

    def setUp(self):
        self.master = get_user_model().objects.create_superuser('master', 'master@example.com', 'x')

    def smtp_from_the_console(self, status='ok'):
        provider = registry.get('smtp')
        draft = service.save_draft(provider, None, actor=self.master, public=SMTP_PUBLIC,
                                   secrets={'password': SMTP_PASSWORD})
        IntegrationConfig.objects.filter(pk=draft.pk).update(
            validated=True, last_test_status='ok', last_tested_at=timezone.now())
        active = service.activate(provider, None, actor=self.master, version=draft.version)
        if status != 'ok':
            IntegrationConfig.objects.filter(pk=active.pk).update(last_test_status=status)
        return active

    def line(self, text):
        return next(row for row in text.splitlines() if ' integraciones: ' in row and not row.startswith('ATENCIÓN'))

    def test_it_says_which_integrations_run_and_from_where(self):
        code, text = run()
        self.assertEqual(code, 0, text)
        summary = self.line(text)
        self.assertTrue(summary.startswith('OK    integraciones: '), summary)
        self.assertIn('Correo SMTP: activa (entorno)', summary)
        self.assertIn(f'{registry.get("izipay_checkout").label}: activa, TEST (entorno)', summary)
        self.assertIn(f'{registry.get("izipay_micuentaweb").label}: sin configurar', summary)

        self.smtp_from_the_console()
        self.assertIn('Correo SMTP: activa (consola)', self.line(run()[1]))

    def test_optional_integrations_that_nobody_configured_are_not_an_alarm(self):
        with override_settings(GOOGLE_OAUTH_CLIENT_ID='', FISCAL_ENABLED=False):
            code, text = run()
        self.assertEqual(code, 0, text)
        self.assertIn('Inicio de sesión con Google: sin configurar', text)
        self.assertIn('SUNAT · Facturación electrónica: sin configurar', text)
        self.assertIn('WhatsApp Business: ninguna empresa en la consola', text)

    def test_a_server_that_does_not_send_whatsapp_says_so_beside_it(self):
        with override_settings(WHATSAPP_PROVIDER='disabled'):
            self.assertIn('WhatsApp Business: ninguna empresa en la consola — apagado en este servidor (WHATSAPP_PROVIDER)',
                          run()[1])
        with override_settings(WHATSAPP_PROVIDER='cloud_api'):
            code, text = run()
        self.assertNotIn('apagado en este servidor', text)
        self.assertEqual(code, 0, text)

    def test_a_shop_that_cannot_send_mail_needs_somebody(self):
        with override_settings(EMAIL_BACKEND_LEGACY=''):
            code, text = run()
        self.assertEqual(code, 1)
        self.assertIn('ATENCIÓN integraciones: el correo no está configurado', text)

    def test_a_shop_that_cannot_charge_needs_somebody(self):
        with override_settings(IZIPAY_API_KEY='', IZIPAY_HASH_KEY=''):
            code, text = run()
        self.assertEqual(code, 1)
        self.assertIn('ATENCIÓN integraciones: la pasarela de pago', text)

        self.smtp_from_the_console()
        with override_settings(PAYMENT_PROVIDER=''):
            code, text = run()
        self.assertEqual(code, 1)
        self.assertIn('ATENCIÓN integraciones: no hay ninguna pasarela de pago activa', text)

    def test_a_console_configuration_without_its_root_key_needs_somebody(self):
        self.smtp_from_the_console()
        with override_settings(APP_CONFIG_ENCRYPTION_KEY='', DEBUG=False):
            code, text = run()
        self.assertEqual(code, 1)
        self.assertIn('APP_CONFIG_ENCRYPTION_KEY', text)

    def test_a_configuration_sealed_with_another_key_needs_somebody(self):
        self.smtp_from_the_console()
        with override_settings(APP_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            code, text = run()
        self.assertEqual(code, 1)
        self.assertIn('ATENCIÓN integraciones: Correo SMTP: lo guardado en la consola no se puede leer', text)

    def test_an_active_integration_whose_last_test_failed_needs_somebody(self):
        self.smtp_from_the_console(status='auth_failed')
        code, text = run()
        self.assertEqual(code, 1)
        self.assertIn('ATENCIÓN integraciones: Correo SMTP: la última prueba falló (auth_failed)', text)

    def test_one_switched_off_on_purpose_is_said_and_is_not_an_alarm_by_itself(self):
        active = self.smtp_from_the_console()
        IntegrationConfig.objects.filter(pk=active.pk).update(enabled=False)
        code, text = run()
        self.assertIn('Correo SMTP: desactivada (consola)', text)
        # …but mail is then not configured, and THAT is one.
        self.assertEqual(code, 1)
        self.assertIn('el correo no está configurado', text)

    def test_no_line_carries_a_value(self):
        self.smtp_from_the_console(status='auth_failed')
        _code, text = run()
        for value in (SMTP_PASSWORD, 'smtp.example.invalid', 'usuario-smtp', KEY, 'smtp.entorno.invalid',
                      'usuario-del-entorno', 'clave-del-entorno-NoEsReal',
                      IZIPAY_TEST_SETTINGS['IZIPAY_API_KEY'], IZIPAY_TEST_SETTINGS['IZIPAY_HASH_KEY'],
                      IZIPAY_TEST_SETTINGS['IZIPAY_MERCHANT_CODE']):
            self.assertNotIn(value, text)

    def test_it_still_writes_nothing(self):
        self.smtp_from_the_console()
        wrote = AssertionError('ops_status wrote to the database')
        with mock.patch('django.db.models.Model.save', side_effect=wrote), \
                mock.patch('django.db.models.QuerySet.update', side_effect=wrote):
            self.assertEqual(run()[0], 0)


@override_settings(APP_CONFIG_ENCRYPTION_KEY=KEY, **CONFIGURED)
class ConversionsStatusTest(TestCase):
    """ANALYTICS-MARKETING: the purchase conversions the server sends, as an operator needs to hear of them."""

    def setUp(self):
        from store.tests import _order, _pilot_company
        self.order = _order(_pilot_company(), total='50.00')

    def conversion(self, provider='meta', **fields):
        from store.models import ConversionDelivery
        return ConversionDelivery.objects.create(order=self.order, provider=provider, event_id='purchase.1', **fields)

    def test_with_none_waiting_or_failed_it_is_all_right(self):
        self.conversion(status='sent')
        code, text = run()
        self.assertEqual(code, 0, text)
        self.assertIn('OK    conversiones: sin conversiones fallidas ni atrasadas', text)

    def test_one_that_a_provider_refused_is_a_note_with_its_kind_and_no_more(self):
        self.conversion(status='failed', failure_kind='auth_failed', provider_code='190')
        code, text = run()
        self.assertEqual(code, 0, text)
        self.assertIn('NOTA  conversiones: 1 conversión de compra no se pudo enviar en las últimas 24 h (Meta: auth_failed)', text)

    def test_several_refused_and_none_through_needs_somebody(self):
        for provider in ('meta', 'tiktok', 'google_analytics'):
            self.conversion(provider, status='failed', failure_kind='auth_failed')
        code, text = run()
        self.assertEqual(code, 1)
        self.assertIn('ATENCIÓN conversiones: 3 conversiones de compra sin enviar en las últimas 24 h y ninguna enviada', text)

    def test_one_waiting_for_longer_than_the_timer_takes_needs_somebody(self):
        row = self.conversion(status='pending')
        type(row).objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(hours=1))
        code, text = run()
        self.assertEqual(code, 1)
        self.assertIn('send_pending_conversions', text)

    def test_one_that_is_waiting_for_its_next_attempt_is_not_overdue(self):
        self.conversion(status='pending', attempt_count=2, next_attempt_at=timezone.now() + timedelta(minutes=20))
        self.assertEqual(run()[0], 0)

    def test_the_line_names_no_order_and_no_identifier(self):
        self.conversion(status='failed', failure_kind='invalid', provider_code='100')
        _code, text = run()
        self.assertNotIn('purchase.1', text)
        # «24 h» is a fixed phrase of the line, not an identifier: without taking
        # it out, this failed whenever the order happened to be number 24.
        line = text.split('conversiones:')[1].split('\n')[0].replace('1 conversión', '').replace('24 h', '')
        self.assertNotIn(str(self.order.pk) + ' ', line)


@override_settings(**CONFIGURED)
class OrderReviewLinesTest(WhatsAppBase):
    """
    CLIENT-SEC-01 · two things worth knowing about the review of orders, never
    an alarm: how many wait and since when, and whether the assessor is silently
    not assessing. Counts and ages, never an order.
    """

    def checkout_order(self, **extra):
        """An online order as the checkout leaves it: with the cart it came from."""
        self.n = getattr(self, 'n', 0) + 1
        return _order(self.company, total='10.00', cart_session_key=f'carrito-{self.n}', **extra)

    def line(self):
        code, text = run()
        return code, [row for row in text.splitlines() if ' revisión de pedidos: ' in row], text

    def assessed(self, order, status):
        from store.models import OrderRiskAssessment

        return OrderRiskAssessment.objects.create(company=self.company, order=order, level='high', score=70, signals=[],
                                                  review_status=status)

    def test_nothing_waiting_and_everything_assessed(self):
        self.assessed(self.checkout_order(paid=True), 'not_required')
        self.assessed(self.checkout_order(), 'pending')                                   # never paid: not in the queue
        self.checkout_order(sales_channel='pos')                                          # a sale at the till is not assessed
        _order(self.company, total='10.00')                                               # not made by the checkout at all
        code, lines, text = self.line()
        self.assertEqual((code, lines), (0, ['OK    revisión de pedidos: sin pedidos por revisar ni pedidos en línea sin evaluar']), text)

    def test_reviews_waiting_say_how_many_and_since_when(self):
        from store.models import Order

        old = _order(self.company, total='10.00', paid=True)
        Order.objects.filter(pk=old.pk).update(paid_at=timezone.now() - timedelta(hours=50, minutes=5))
        self.assessed(old, 'pending')
        self.assessed(_order(self.company, total='10.00', paid=True), 'pending')
        self.assessed(_order(self.company, total='10.00', paid=True), 'approved')
        code, lines, text = self.line()
        self.assertEqual(code, 0, text)
        [line] = lines
        self.assertTrue(line.startswith('NOTA  revisión de pedidos: 2 pedidos pagados esperan revisión; el más antiguo, desde hace 50 h.'), line)
        for number in (str(old.pk),):
            self.assertNotIn(f'#{number}', text)

    def test_one_review_waiting_less_than_an_hour(self):
        self.assessed(_order(self.company, total='10.00', paid=True), 'pending')
        _code, [line], _text = self.line()
        self.assertTrue(line.startswith('NOTA  revisión de pedidos: 1 pedido pagado espera revisión; el más antiguo, desde hace menos de 1 h.'), line)

    def test_orders_made_before_the_assessor_existed_are_not_missing_an_assessment(self):
        from store.models import Order

        # The day this is deployed: every order of yesterday is «unassessed», and none of them should have been.
        for _ in range(3):
            self.checkout_order()
        code, lines, text = self.line()
        self.assertEqual((code, lines), (0, ['OK    revisión de pedidos: sin pedidos por revisar ni pedidos en línea sin evaluar']), text)
        # From the first assessment on, an order without one is said — and only those made after it.
        first = self.assessed(self.checkout_order(), 'not_required')
        Order.objects.exclude(pk=first.order_id).update(created_at=timezone.now() - timedelta(hours=2))
        type(first).objects.filter(pk=first.pk).update(created_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(self.line()[1], ['OK    revisión de pedidos: sin pedidos por revisar ni pedidos en línea sin evaluar'])
        self.checkout_order()
        _code, [line], _text = self.line()
        self.assertTrue(line.startswith('NOTA  revisión de pedidos: 1 pedido en línea de las últimas 24 h no tiene evaluación'), line)
        # It is the OLDEST assessment that says since when: a later one does not hide that order.
        self.assessed(self.checkout_order(), 'not_required')
        _code, [line], _text = self.line()
        self.assertTrue(line.startswith('NOTA  revisión de pedidos: 1 pedido en línea'), line)

    def started(self):
        """The assessor has been running for days: there is an old assessment."""
        from store.models import OrderRiskAssessment

        row = self.assessed(self.checkout_order(), 'not_required')
        OrderRiskAssessment.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(days=3))

    def test_online_orders_nobody_assessed_are_said(self):
        from store.models import Order

        self.started()
        self.checkout_order()
        _order(self.company, total='10.00', paid=True, idempotency_key='pedido-app-0001')  # the app's checkout
        old = self.checkout_order()
        Order.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(hours=25))
        code, lines, text = self.line()
        self.assertEqual(code, 0, text)                                                   # never an alarm
        [line] = lines
        self.assertTrue(line.startswith('NOTA  revisión de pedidos: 2 pedidos en línea de las últimas 24 h no tienen evaluación'), line)
        self.assertNotIn('ATENCIÓN', '\n'.join(lines))

    def test_both_at_once_are_two_lines(self):
        self.started()
        self.assessed(_order(self.company, total='10.00', paid=True), 'pending')
        self.checkout_order()
        code, lines, _text = self.line()
        self.assertEqual((code, len(lines)), (0, 2))
        self.assertTrue(all(line.startswith('NOTA  ') for line in lines))
