"""
FIN-E — `backfill_repair_receivables` and the locks around the receivable of a repair
(docs/adr-finance-obligations.md §6.2, §3.6: RepairOrder → Obligation).

The concurrency tests are `TransactionTestCase`s (in `flushing`,
`backend/scripts/ci_shard_weights.json`); no `reset_sequences` (TEST-FIXTURE-SEQ-01).
"""
import threading
from datetime import date, datetime, timezone as dt_timezone
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase

from store import finance_test_support as fx
from store import service_services as svc
from store.finance import receivables
from store.models import AdminAuditLog, Obligation, RepairStatusCode, RepairStatusHistory

WAIT = 10
BLOCKED = 1.5


def run(*args):
    out = StringIO()
    call_command('backfill_repair_receivables', *args, stdout=out)
    return out.getvalue()


class OrdersApprovedBeforeFinance:
    """Orders approved before FIN-E existed: approved quotes, no obligation."""

    def build_world(self, slug='fin-bf', name='Taller Relleno'):
        self.w = fx.RepairWorld(slug, name)
        self.approved = self.w.order('500.00')
        self.paid = self.w.order('300.00')
        self.w.pay(self.paid, '300.00')
        self.cancelled = self.w.order('100.00')
        svc.transition_repair_order(repair_order=self.cancelled, to_status=RepairStatusCode.CANCELLED,
                                    actor=self.w.staff)
        self.zero = self.w.order('0.00')
        self.rejected = self.w.order('100.00', approve=False)
        self.w.decide(self.rejected.quotes.get(), self.w.customer, self.w.client_user, 'reject')
        self.sent = self.w.order('100.00', approve=False)
        Obligation.objects.filter(company=self.w.company).delete()  # as if FIN-E was not deployed


class BackfillCommandTest(OrdersApprovedBeforeFinance, TestCase):
    def setUp(self):
        self.build_world()

    def test_creates_one_receivable_per_eligible_order_and_skips_the_rest(self):
        out = run()
        eligible = {self.approved.pk, self.paid.pk}
        created = set(Obligation.objects.values_list('repair_order_id', flat=True))
        self.assertEqual(created, eligible)
        self.assertIn('created=2', out)
        row = Obligation.objects.get(repair_order=self.approved)
        self.assertEqual((row.origin_key, row.customer_id, row.branch_id),
                         (f'repair:{self.approved.pk}', self.w.customer.pk, self.w.branch.pk))
        self.assertIsNone(row.amount_original)

    def test_is_idempotent(self):
        run()
        before = list(Obligation.objects.order_by('pk').values_list('pk', 'reference'))
        out = run()
        self.assertIn('created=0', out)
        self.assertEqual(list(Obligation.objects.order_by('pk').values_list('pk', 'reference')), before)
        self.assertEqual(Obligation.objects.count(), 2)

    def test_dry_run_reports_and_writes_nothing(self):
        audits = AdminAuditLog.objects.filter(action='finance_receivable_derived').count()
        out = run('--dry-run')
        self.assertIn('created=2', out)
        self.assertIn('dry-run', out)
        self.assertEqual(Obligation.objects.count(), 0)
        self.assertEqual(AdminAuditLog.objects.filter(action='finance_receivable_derived').count(), audits)

    def test_gives_the_due_date_from_the_history_in_the_company_timezone(self):
        self.w.force_status(self.approved, RepairStatusCode.QUALITY_CONTROL)
        with mock.patch('django.utils.timezone.now',
                        return_value=datetime(2026, 3, 11, 4, 30, tzinfo=dt_timezone.utc)):
            self.w.move(self.approved, RepairStatusCode.READY_FOR_PICKUP)
        run()
        self.assertEqual(Obligation.objects.get(repair_order=self.approved).due_date, date(2026, 3, 10))
        self.assertIsNone(Obligation.objects.get(repair_order=self.paid).due_date)

    def test_one_company_at_a_time_and_never_across(self):
        other = OrdersApprovedBeforeFinance()
        other.build_world('fin-bf2', 'Otro taller')
        run('--company', self.w.company.slug)
        self.assertEqual(Obligation.objects.filter(company=other.w.company).count(), 0)
        self.assertEqual(Obligation.objects.filter(company=self.w.company).count(), 2)
        run()
        self.assertEqual(Obligation.objects.filter(company=other.w.company).count(), 2)
        for row in Obligation.objects.all():
            self.assertEqual(row.company_id, row.repair_order.company_id)
            self.assertEqual(row.company_id, row.customer.company_id)

    def test_an_unknown_company_is_an_error_not_a_full_run(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            run('--company', 'no-existe')
        self.assertEqual(Obligation.objects.count(), 0)

    def test_does_not_send_or_change_anything_else(self):
        orders_before = list(self.approved.__class__.objects.order_by('pk').values_list('pk', 'status'))
        run()
        self.assertEqual(list(self.approved.__class__.objects.order_by('pk').values_list('pk', 'status')),
                         orders_before)
        self.assertEqual(RepairStatusHistory.objects.filter(to_status='cancelled').count(), 1)


class LocksTest(TransactionTestCase):
    """Lock order RepairOrder → Obligation: approving and backfilling the same repair make ONE receivable."""

    def setUp(self):
        if connection.vendor == 'sqlite':
            self.skipTest('SQLite has no row locks: a green result would prove nothing.')
        self.w = fx.RepairWorld('fin-lock', 'Taller Candados')

    def _thread(self, fn, outcome):
        def body():
            try:
                outcome['value'] = fn()
            except BaseException as exc:  # noqa: BLE001 - the test reports it
                outcome['error'] = exc
            finally:
                connections.close_all()

        thread = threading.Thread(target=body)
        thread.start()
        return thread

    def test_two_workers_ensuring_the_same_repair_make_one_receivable(self):
        order = self.w.order('500.00')
        Obligation.objects.all().delete()
        barrier = threading.Barrier(2)
        outcomes = [{}, {}]

        def ensure():
            barrier.wait(WAIT)
            return receivables.ensure_repair_receivable_for(order.pk)

        threads = [self._thread(ensure, outcome) for outcome in outcomes]
        for thread in threads:
            thread.join(WAIT * 2)
        for outcome in outcomes:
            self.assertNotIn('error', outcome, outcome)
        self.assertEqual(Obligation.objects.filter(repair_order=order).count(), 1)
        self.assertEqual({o['value'].pk for o in outcomes}, {Obligation.objects.get(repair_order=order).pk})

    def test_a_second_worker_waits_for_the_approval_that_holds_the_order(self):
        order = self.w.order('500.00', approve=False)
        quote = order.quotes.get()
        reached, release = threading.Event(), threading.Event()
        original = AdminAuditLog.log

        def log(*args, **kwargs):
            if kwargs.get('action') == 'service_quote_approved':
                reached.set()
                release.wait(WAIT)
            return original(*args, **kwargs)

        approval, backfill = {}, {}
        with mock.patch.object(AdminAuditLog, 'log', side_effect=log):
            first = self._thread(lambda: self.w.decide(quote, self.w.customer, self.w.client_user, 'approve'),
                                 approval)
            self.assertTrue(reached.wait(WAIT))
            second = self._thread(lambda: receivables.ensure_repair_receivable_for(order.pk), backfill)
            second.join(BLOCKED)
            self.assertTrue(second.is_alive(), 'the second worker must wait for the order lock')
            release.set()
            first.join(WAIT)
            second.join(WAIT)
        self.assertNotIn('error', approval, approval)
        self.assertNotIn('error', backfill, backfill)
        self.assertEqual(Obligation.objects.filter(repair_order=order).count(), 1)
        self.assertEqual(backfill['value'].pk, Obligation.objects.get(repair_order=order).pk)
