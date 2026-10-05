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
from django.test import TestCase
from django.utils import timezone

from store.models import NotificationDelivery, PaymentTransaction
from store.test_whatsapp import WhatsAppBase
from store.tests import _order, _pay_attempt


def run():
    out = io.StringIO()
    try:
        call_command('ops_status', stdout=out)
        code = 0
    except SystemExit as exc:
        code = int(exc.code or 0)
    return code, out.getvalue()


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

    def test_a_payment_left_waiting_is_reported_and_a_fresh_one_is_not(self):
        attempt = _pay_attempt(_order(self.company, total='50.00'))
        self.assertEqual(run()[0], 0, 'a payment opened a moment ago is a buyer typing a card')
        PaymentTransaction.objects.filter(pk=attempt.pk).update(created_at=timezone.now() - timedelta(hours=2))
        code, text = run()
        self.assertEqual(code, 1)
        self.assertIn('ATENCIÓN pagos: 1 pago', text)
        self.assertIn('sin respuesta de la pasarela', text)

    def test_a_whatsapp_message_that_failed_for_good_is_reported(self):
        delivery = self.delivery(self.new_order())
        NotificationDelivery.objects.filter(pk=delivery.pk).update(
            status=NotificationDelivery.Status.FAILED, next_attempt_at=None)
        code, text = run()
        self.assertEqual(code, 1)
        self.assertIn('ATENCIÓN WhatsApp: 1 mensaje', text)

    def test_whatsapp_messages_that_nobody_is_sending_are_reported(self):
        delivery = self.delivery(self.new_order())
        NotificationDelivery.objects.filter(pk=delivery.pk).update(
            status=NotificationDelivery.Status.PENDING, next_attempt_at=timezone.now() - timedelta(hours=1),
            created_at=timezone.now() - timedelta(hours=1))
        code, text = run()
        self.assertEqual(code, 1)
        self.assertIn('sin enviar', text)
        self.assertIn('send_pending_notifications', text)

    def test_no_line_names_a_customer_a_phone_or_a_token(self):
        NotificationDelivery.objects.filter(pk=self.delivery(self.new_order()).pk).update(
            status=NotificationDelivery.Status.FAILED, next_attempt_at=None, failure_reason='131026')
        _code, text = run()
        self.assertNotIn('987', text)
        self.assertNotIn(self.customer.first_name, text)
