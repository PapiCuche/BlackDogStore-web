"""
Integrity of the repair payment ledger (FINANCE-OBLIGATIONS-01 audit,
docs/adr-finance-obligations.md §1.4).

H-1: a reversal and a delivery of the same repair are serialised by the
RepairOrder lock.

The delivery gate (`_deliver_repair`) reads the balance while holding the
order lock, on the promise that nobody can reverse the payment that made it
"paid" meanwhile. `reverse_service_payment` used to lock only the payment row,
so it could stamp the reversal in the middle of a delivery: the device left
with a balance outstanding in a company that requires payment first, and the
history said the payment was reversed BEFORE the handover the gate allowed.

Lock order, everywhere: RepairOrder → payments.

Idempotency scope: a key the company already used on ANOTHER repair is a
conflict, never a replay of that other repair's payment or delivery.

`ReversalAgainstDeliveryTest` is a `TransactionTestCase` (in `flushing`, `backend/scripts/ci_shard_weights.json`):
a plain `TestCase` would never let a second thread see the first one's rows.
No `reset_sequences` (TEST-FIXTURE-SEQ-01).
"""
import threading
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase

from store import service_services
from store.company_provisioning import provision_company_access_defaults
from store.models import (
    AdminAuditLog, CompanySettings, Device, Membership, PaymentMethod, RepairDelivery,
    RepairOrder, RepairStatusCode,
)
from store.tests import _m7_user, _saas_company, _v1_customer

WAIT = 10       # seconds: generous, only reached when something is broken
BLOCKED = 1.5   # seconds a thread must stay waiting to count as blocked


class RepairFixture:
    """A company that requires payment before delivery, a customer and a device."""

    def build(self):
        cache.clear()
        self.company = _saas_company('Taller H1', 'h1-taller', tax_id='20900000141')
        provision_company_access_defaults(self.company)
        self.branch = self.company.branches.order_by('pk').first()
        settings_row, _ = CompanySettings.objects.get_or_create(company=self.company)
        settings_row.require_service_payment_before_delivery = True
        settings_row.save(update_fields=['require_service_payment_before_delivery'])

        self.client_user = _m7_user('h1_cliente')
        self.customer = _v1_customer(self.company, self.client_user, first_name='Ana')
        self.device = Device.objects.create(
            company=self.company, customer=self.customer,
            device_type=Device.TYPE_PHONE, brand='G', model='X',
        )
        self.staff = _m7_user('h1_tecnico')
        Membership.objects.create(user=self.staff, company=self.company, role='sales')

    def _paid_and_ready(self, total, *, pay=True):
        svc = service_services
        order = svc.create_repair_order(
            company=self.company, branch=self.branch, customer=self.customer,
            device=self.device, reported_issue='No enciende.', actor=self.staff,
        )
        svc.transition_repair_order(repair_order=order, to_status=RepairStatusCode.DIAGNOSING, actor=self.staff)
        order.refresh_from_db()
        diagnostic = svc.create_diagnostic(
            repair_order=order, description='Batería.', recommended_action='Cambiar.', actor=self.staff,
        )
        quote = svc.create_quote(repair_order=order, diagnostic=diagnostic, actor=self.staff)
        svc.add_quote_item(quote=quote, description='Trabajo', quantity=1, unit_price=total)
        quote.refresh_from_db()
        svc.publish_quote(quote=quote, actor=self.staff)
        quote.refresh_from_db()
        svc.record_quote_decision(quote=quote, customer=self.customer, user=self.client_user, decision='approve')
        order.refresh_from_db()
        if pay:
            svc.record_service_payment(
                repair_order=order, amount=Decimal(total), method=PaymentMethod.CASH, actor=self.staff,
            )
        # Fixture shortcut: the repair, QA and pickup steps are not what this
        # test is about; delivery reads only the status projection.
        RepairOrder.objects.filter(pk=order.pk).update(status=RepairStatusCode.READY_FOR_PICKUP)
        order.refresh_from_db()
        self.assertEqual(order.status, RepairStatusCode.READY_FOR_PICKUP)
        return order


class ReversalAgainstDeliveryTest(RepairFixture, TransactionTestCase):

    def setUp(self):
        if connection.vendor == 'sqlite':
            self.skipTest('SQLite has no row locks: a green result would prove nothing.')
        self.build()
        self.order = self._paid_and_ready('500.00')
        self.assertEqual(service_services.service_payment_summary(self.order)['outstanding'], Decimal('0.00'))
        self.payment = self.order.payments.get()

    def _pause_at(self, action):
        """Hold the transaction that writes this audit action open until released."""
        reached, release = threading.Event(), threading.Event()
        original = AdminAuditLog.log

        def log(*args, **kwargs):
            if kwargs.get('action') == action:
                reached.set()
                release.wait(WAIT)
            return original(*args, **kwargs)

        return mock.patch.object(AdminAuditLog, 'log', side_effect=log), reached, release

    def _run(self, fn, outcome):
        def body():
            try:
                outcome['value'] = fn()
            except Exception as exc:  # noqa: BLE001 — the assertion reads it
                outcome['error'] = exc
            finally:
                connections.close_all()
        thread = threading.Thread(target=body)
        thread.start()
        return thread

    def _reverse(self):
        return service_services.reverse_service_payment(payment=self.payment, reason='Error de digitación', actor=self.staff)

    def _deliver(self):
        return service_services.deliver_repair(repair_order=self.order, recipient_name='Ana', actor=self.staff)

    def test_a_delivery_waits_for_a_reversal_in_progress_and_is_refused(self):
        patcher, reached, release = self._pause_at('service_payment_reversed')
        reversal, delivery = {}, {}
        with patcher:
            first = self._run(self._reverse, reversal)
            self.assertTrue(reached.wait(WAIT), 'la reversión no llegó a sellar el pago')
            second = self._run(self._deliver, delivery)
            second.join(BLOCKED)
            still_waiting = second.is_alive()
            release.set()
            first.join(WAIT)
            second.join(WAIT)

        self.assertNotIn('error', reversal)
        self.assertIsInstance(delivery.get('error'), service_services.PaymentRequired,
                              'un equipo salió con saldo mientras se revertía su pago')

        self.assertTrue(still_waiting, 'la entrega no esperó a la reversión en curso')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, RepairStatusCode.READY_FOR_PICKUP)
        self.assertFalse(RepairDelivery.objects.filter(repair_order=self.order).exists())

    def test_a_reversal_waits_for_a_delivery_in_progress(self):
        patcher, reached, release = self._pause_at('service_repair_delivered')
        delivery, reversal = {}, {}
        with patcher:
            first = self._run(self._deliver, delivery)
            self.assertTrue(reached.wait(WAIT), 'la entrega no llegó a registrarse')
            second = self._run(self._reverse, reversal)
            second.join(BLOCKED)
            still_waiting = second.is_alive()
            release.set()
            first.join(WAIT)
            second.join(WAIT)

        self.assertTrue(still_waiting, 'la reversión no esperó a la entrega en curso')
        self.assertNotIn('error', delivery)
        self.assertNotIn('error', reversal)
        self.payment.refresh_from_db()
        handed_over = RepairDelivery.objects.get(repair_order=self.order)
        self.assertGreaterEqual(self.payment.reversed_at, handed_over.delivered_at)


class IdempotencyKeyScopeTest(RepairFixture, TestCase):
    """
    The key is unique per COMPANY (`unique_repair_payment_idempotency_key_per_company`),
    so the same key on another repair used to be answered with the OTHER
    repair's row whenever amount, method and reference matched: the counter
    saw success and nothing was recorded on this repair.
    """

    def setUp(self):
        self.build()
        self.first = self._paid_and_ready('100.00', pay=False)
        self.second = self._paid_and_ready('100.00', pay=False)

    def _pay(self, order, key):
        return service_services.record_service_payment(
            repair_order=order, amount=Decimal('100.00'), method=PaymentMethod.CASH,
            idempotency_key=key, actor=self.staff,
        )

    def test_a_key_used_on_another_repair_is_a_conflict_not_a_replay(self):
        paid = self._pay(self.first, 'pago-1')
        with self.assertRaises(service_services.PaymentConflict):
            self._pay(self.second, 'pago-1')
        self.assertEqual(paid.repair_order_id, self.first.pk)
        self.assertFalse(self.second.payments.exists())

    def test_the_same_key_on_the_same_repair_is_still_a_replay(self):
        paid = self._pay(self.first, 'pago-1')
        self.assertEqual(self._pay(self.first, 'pago-1').pk, paid.pk)
        self.assertEqual(self.first.payments.count(), 1)

    def test_a_retry_of_a_payment_fingerprinted_before_the_fix_is_still_a_replay(self):
        # Rows written before the fingerprint named the repair keep the old
        # one; a retry of THAT attempt, on THAT repair, must not turn into a 409.
        paid = self._pay(self.first, 'pago-1')
        legacy = service_services._payment_fingerprint(
            amount=Decimal('100.00'), method=PaymentMethod.CASH, reference='',
        )
        type(paid).objects.filter(pk=paid.pk).update(request_fingerprint=legacy)
        self.assertEqual(self._pay(self.first, 'pago-1').pk, paid.pk)
        with self.assertRaises(service_services.PaymentConflict):
            self._pay(self.second, 'pago-1')

    def _deliver(self, order, key):
        return service_services.deliver_repair(
            repair_order=order, recipient_name='Ana', idempotency_key=key, actor=self.staff,
        )

    def test_a_delivery_key_used_on_another_repair_is_a_conflict(self):
        self._pay(self.first, 'pago-1')
        self._pay(self.second, 'pago-2')
        handed = self._deliver(self.first, 'entrega-1')
        with self.assertRaises(service_services.DeliveryConflict):
            self._deliver(self.second, 'entrega-1')
        self.assertEqual(handed.repair_order_id, self.first.pk)
        self.assertFalse(RepairDelivery.objects.filter(repair_order=self.second).exists())
        self.assertEqual(self._deliver(self.first, 'entrega-1').pk, handed.pk)
