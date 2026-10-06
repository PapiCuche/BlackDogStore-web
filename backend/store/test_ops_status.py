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
    out = io.StringIO()
    try:
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
        self.assertNotIn(self.customer.first_name, text)


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
