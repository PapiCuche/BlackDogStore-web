"""
FIN-E — a repair's balance is a receivable that is DERIVED at read time
(docs/adr-finance-obligations.md §6.1–§6.2, §11.1 cases 11–15).

Only identity, due date and dispute are stored on the obligation; the amount,
what was paid and the balance come from the repair's own quote and payment
ledger. Payments on a repair go through `record_service_payment` and nowhere
else.
"""
from datetime import date, datetime, timezone as dt_timezone
from unittest import mock

from django.test import TestCase

from store import finance_test_support as fx
from store import service_services as svc
from store.finance import receivables, services
from store.models import (
    AdminAuditLog, Obligation, ObligationPayment, RepairPayment, RepairStatusCode,
)

D = fx.D
LIST = '/api/admin/finance/obligations/'
SUMMARY = '/api/admin/finance/summary/'


def receivable_of(order):
    return Obligation.objects.get(repair_order=order)


def api_row(api, order, *, query=''):
    """The obligation list row of a repair, or None when the list hides it."""
    ids = {o.pk for o in Obligation.objects.filter(repair_order=order)}
    rows = api.get(f'{LIST}?page_size=200{query}').json()['results']
    return next((row for row in rows if row['id'] in ids), None)


def at(year, month, day, hour=12, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=dt_timezone.utc)


class DerivationTest(TestCase):
    def setUp(self):
        self.w = fx.RepairWorld()
        self.api = fx.client_for(fx.member(self.w.company, 'fin_e_admin', fx.RECEIVABLES))

    def test_approving_the_quote_creates_the_receivable_with_identity_only(self):
        order = self.w.order('500.00')
        row = receivable_of(order)
        self.assertEqual(row.origin_key, f'repair:{order.pk}')
        self.assertEqual(row.origin_type, Obligation.ORIGIN_REPAIR)
        self.assertEqual(row.direction, 'receivable')
        self.assertEqual((row.customer_id, row.branch_id, row.company_id),
                         (order.customer_id, order.branch_id, order.company_id))
        self.assertIsNone(row.amount_original)
        self.assertIsNone(row.due_date)
        self.assertTrue(row.reference.startswith('OB-'))
        self.assertTrue(AdminAuditLog.objects.filter(company=self.w.company,
                                                     action='finance_receivable_derived').exists())

    def test_the_receivable_is_created_in_the_transaction_that_approves(self):
        order = self.w.order('500.00', approve=False)
        quote = order.quotes.get()
        original = AdminAuditLog.log

        def boom(*args, **kwargs):
            if kwargs.get('action') == 'service_quote_approved':
                raise RuntimeError('rollback the approval')
            return original(*args, **kwargs)

        with mock.patch.object(AdminAuditLog, 'log', side_effect=boom):
            with self.assertRaises(RuntimeError):
                self.w.decide(quote, self.w.customer, self.w.client_user, 'approve')
        quote.refresh_from_db()
        self.assertEqual(quote.status, 'sent')
        self.assertFalse(Obligation.objects.filter(repair_order=order).exists())

    def test_advance_payment_leaves_the_balance_and_stores_no_amount(self):
        order = self.w.order('500.00')
        self.w.pay(order, '200.00')
        row = api_row(self.api, order)
        self.assertEqual((row['effective'], row['paid'], row['balance']), ('500.00', '200.00', '300.00'))
        self.assertEqual(row['state'], 'pending')
        self.assertIsNone(receivable_of(order).amount_original)
        self.assertEqual(ObligationPayment.objects.count(), 0)

    def test_reversing_the_advance_restores_the_full_balance_in_the_repair_ledger_only(self):
        order = self.w.order('500.00')
        payment = self.w.pay(order, '200.00')
        svc.reverse_service_payment(payment=payment, reason='Error de caja', actor=self.w.staff)
        row = api_row(self.api, order)
        self.assertEqual((row['paid'], row['balance']), ('0.00', '500.00'))
        self.assertEqual(ObligationPayment.objects.count(), 0)

    def test_a_quote_that_is_not_approved_creates_nothing(self):
        draft = self.w.order('300.00', approve=False, publish=False)
        sent = self.w.order('300.00', approve=False)
        rejected = self.w.order('300.00', approve=False)
        self.w.decide(rejected.quotes.get(), self.w.customer, self.w.client_user, 'reject')
        self.assertEqual(Obligation.objects.filter(repair_order__in=[draft, sent, rejected]).count(), 0)

    def test_a_quote_of_total_zero_creates_nothing(self):
        order = self.w.order('0.00')
        self.assertEqual(order.quotes.get().status, 'approved')
        self.assertFalse(Obligation.objects.filter(repair_order=order).exists())

    def test_a_cancelled_order_leaves_the_pending_lists_and_history_says_origin_cancelled(self):
        order = self.w.order('500.00')
        self.w.pay(order, '100.00')
        svc.transition_repair_order(repair_order=order, to_status=RepairStatusCode.CANCELLED, actor=self.w.staff)
        for state in ('pending', 'open', 'overdue', 'due_today', 'due_soon'):
            self.assertIsNone(api_row(self.api, order, query=f'&state={state}'), state)
        self.assertEqual(self.api.get(SUMMARY).json()['open'], {})
        history = api_row(self.api, order)
        self.assertEqual(history['state'], 'origin_cancelled')
        self.assertIsNotNone(api_row(self.api, order, query='&state=origin_cancelled'))

    def test_a_cancelled_order_never_gets_a_receivable_late(self):
        order = self.w.order('500.00')
        svc.transition_repair_order(repair_order=order, to_status=RepairStatusCode.CANCELLED, actor=self.w.staff)
        Obligation.objects.filter(repair_order=order).delete()  # as if it had never been derived
        self.assertIsNone(receivables.ensure_repair_receivable_for(order.pk))
        self.assertFalse(Obligation.objects.filter(repair_order=order).exists())

    def test_requote_keeps_the_last_approved_balance_and_never_adds_revisions(self):
        order = self.w.order('500.00')
        self.w.pay(order, '200.00')
        fresh = svc.reopen_approved_quote(quote=svc.approved_quote(order), actor=self.w.staff, reason='Más trabajo')
        row = api_row(self.api, order)
        self.assertEqual(row['state'], 'quote_review')
        self.assertEqual((row['effective'], row['balance']), ('500.00', '300.00'))
        svc.remove_quote_item(item=fresh.items.get())
        fresh.refresh_from_db()
        svc.add_quote_item(quote=fresh, description='Trabajo', quantity=1, unit_price=D('450.00'))
        fresh.refresh_from_db()
        svc.publish_quote(quote=fresh, actor=self.w.staff)
        fresh.refresh_from_db()
        self.w.decide(fresh, self.w.customer, self.w.client_user, 'approve')
        row = api_row(self.api, order)
        self.assertEqual(row['state'], 'pending')
        self.assertEqual((row['effective'], row['balance']), ('450.00', '250.00'))
        self.assertEqual(Obligation.objects.filter(repair_order=order).count(), 1)

    def test_overpayment_after_a_cheaper_requote_is_paid_with_the_credit_apart(self):
        order = self.w.order('500.00')
        self.w.pay(order, '400.00')
        fresh = self.w.requote(order, '300.00')
        self.w.decide(fresh, self.w.customer, self.w.client_user, 'approve')
        row = api_row(self.api, order)
        self.assertEqual(row['state'], 'paid')
        self.assertEqual((row['effective'], row['paid'], row['balance'], row['credit']),
                         ('300.00', '400.00', '0.00', '100.00'))
        self.assertEqual(self.api.get(SUMMARY).json()['open'], {})

    def test_a_requote_to_zero_with_nothing_paid_hides_the_receivable_but_with_money_it_stays(self):
        free = self.w.order('500.00')
        self.w.decide(self.w.requote(free, '0.00'), self.w.customer, self.w.client_user, 'approve')
        row = receivable_of(free)
        self.assertIsNone(api_row(self.api, free))
        self.assertEqual(self.api.get(f'{LIST}{row.pk}/').status_code, 404)
        self.assertEqual(self.api.post(f'{LIST}{row.pk}/dispute/', {'reason': 'x'}, format='json').status_code, 404)
        self.assertEqual(self.api.get(SUMMARY).json()['open'], {})
        kept = self.w.order('500.00')
        self.w.pay(kept, '100.00')
        self.w.decide(self.w.requote(kept, '0.00'), self.w.customer, self.w.client_user, 'approve')
        shown = api_row(self.api, kept)
        self.assertEqual((shown['state'], shown['balance'], shown['credit']), ('paid', '0.00', '100.00'))

    def test_a_fully_paid_repair_is_paid(self):
        order = self.w.order('500.00')
        self.w.pay(order, '500.00')
        row = api_row(self.api, order)
        self.assertEqual((row['state'], row['balance'], row['credit']), ('paid', '0.00', '0.00'))
        self.assertEqual(api_row(self.api, order, query='&state=paid')['id'], row['id'])

    def test_a_paid_sale_or_unpaid_order_never_creates_a_receivable(self):
        from store.tests import _p3_order

        for status_ in ('paid', 'pending_payment', 'failed', 'expired'):
            _p3_order(self.w.company, status=status_)
        self.assertEqual(Obligation.objects.filter(direction='receivable').count(), 0)

    def test_the_customer_is_the_repairs_customer_and_another_company_s_is_refused(self):
        order = self.w.order('500.00')
        self.assertEqual(receivable_of(order).customer_id, self.w.customer.pk)
        other = fx.RepairWorld('fin-rep-other', 'Taller Ajeno')
        with self.assertRaises(services.FinanceError):
            receivables.create_receivable(
                self.w.company, actor=None, customer=other.customer, concept='Deuda', amount='10.00',
                due_date=date(2026, 5, 1), reason='Cobro pendiente',
            )


class HookFailureTest(TestCase):
    """A failure deriving the receivable must never undo the customer's approval, and is never silent."""

    def setUp(self):
        self.w = fx.RepairWorld('fin-hook', 'Taller Gancho')

    def test_a_failing_derivation_keeps_the_approval_logs_and_the_backfill_recovers(self):
        from django.db import DatabaseError

        from store.models import RepairOrder

        with mock.patch('store.finance.receivables.ensure_repair_receivable',
                        side_effect=DatabaseError('boom')), \
                self.assertLogs('store.service_services', level='ERROR') as logs:
            order = self.w.order('500.00')
        self.assertTrue(any('cuenta por cobrar' in line for line in logs.output))
        order.refresh_from_db()
        self.assertEqual(order.status, RepairStatusCode.APPROVED)
        self.assertTrue(svc.approved_quote(order))
        self.assertFalse(Obligation.objects.filter(repair_order=order).exists())
        self.assertTrue(RepairOrder.objects.filter(pk=order.pk).exists())  # the transaction is still usable
        receivables.ensure_repair_receivable_for(order.pk)
        self.assertEqual(Obligation.objects.filter(repair_order=order).count(), 1)

    def test_a_failing_due_date_sync_keeps_the_status_change_and_logs(self):
        from django.db import DatabaseError

        order = self.w.order('500.00')
        self.w.force_status(order, RepairStatusCode.QUALITY_CONTROL)
        with mock.patch('store.finance.receivables.sync_due_date', side_effect=DatabaseError('boom')), \
                self.assertLogs('store.service_services', level='ERROR') as logs:
            self.w.move(order, RepairStatusCode.READY_FOR_PICKUP, at=at(2026, 3, 10, 15, 0))
        self.assertTrue(any('vencimiento' in line for line in logs.output))
        order.refresh_from_db()
        self.assertEqual(order.status, RepairStatusCode.READY_FOR_PICKUP)
        self.assertIsNone(receivable_of(order).due_date)


class PaymentRoutesTest(TestCase):
    """Cobrar a repair goes through `record_service_payment` and nowhere else."""

    def setUp(self):
        self.w = fx.RepairWorld('fin-route', 'Taller Rutas')
        self.api = fx.client_for(fx.member(self.w.company, 'fin_route_admin', fx.RECEIVABLES))
        self.order = self.w.order('500.00')
        self.row = receivable_of(self.order)

    def test_finance_cannot_write_a_payment_adjustment_or_void_on_a_repair(self):
        base = f'{LIST}{self.row.pk}/'
        before = RepairPayment.objects.count()
        for suffix, payload in (('payments/', {'amount': '10.00', 'method': 'cash'}),
                                ('adjustments/', {'kind': 'decrease', 'amount': '10.00', 'reason': 'x'}),
                                ('void/', {'reason': 'x'})):
            response = self.api.post(base + suffix, payload, format='json')
            self.assertEqual(response.status_code, 400, (suffix, response.content))
        self.assertEqual(ObligationPayment.objects.count(), 0)
        self.assertEqual(RepairPayment.objects.count(), before)
        self.assertEqual(api_row(self.api, self.order)['balance'], '500.00')

    def test_a_collection_in_the_repair_ledger_moves_the_finance_balance(self):
        self.w.pay(self.order, '125.50')
        body = self.api.get(f'{LIST}{self.row.pk}/').json()
        self.assertEqual((body['paid'], body['balance']), ('125.50', '374.50'))
        self.assertEqual(body['repair']['id'], self.order.pk)
        self.assertEqual(body['customer']['id'], self.w.customer.pk)
        self.row.refresh_from_db()
        self.assertEqual(self.row.lifecycle, Obligation.LIFECYCLE_OPEN)

    def test_a_dispute_on_a_repair_keeps_its_balance(self):
        response = self.api.post(f'{LIST}{self.row.pk}/dispute/', {'reason': 'El cliente discute el cargo'},
                                 format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['disputed'])
        self.assertEqual(response.json()['balance'], '500.00')


class DueDateTest(TestCase):
    def setUp(self):
        self.w = fx.RepairWorld('fin-due', 'Taller Plazos')
        self.api = fx.client_for(fx.member(self.w.company, 'fin_due_admin', fx.RECEIVABLES))
        self.order = self.w.order('500.00')
        self.w.force_status(self.order, RepairStatusCode.QUALITY_CONTROL)

    def ready(self, when):
        self.w.move(self.order, RepairStatusCode.READY_FOR_PICKUP, at=when)
        return receivable_of(self.order)

    def test_without_a_due_date_it_is_never_overdue(self):
        with mock.patch('store.finance.services.company_today', return_value=date(2030, 1, 1)):
            row = api_row(self.api, self.order)
            self.assertIsNone(row['due_date'])
            self.assertEqual(row['state'], 'pending')
            self.assertIsNone(api_row(self.api, self.order, query='&state=overdue'))
            self.assertEqual(self.api.get(SUMMARY).json()['overdue'], {})
            self.assertEqual(self.api.get(SUMMARY).json()['open'], {'PEN': '500.00'})

    def test_reaching_ready_sets_the_local_date_plus_n_days_with_n_zero(self):
        self.assertEqual(receivables.REPAIR_DUE_DAYS, 0)
        row = self.ready(at(2026, 3, 10, 15, 0))
        self.assertEqual(row.due_date, date(2026, 3, 10))

    def test_lima_evening_is_still_that_local_day(self):
        # 23:59:59 in Lima is 04:59:59 UTC of the next day.
        row = self.ready(at(2026, 3, 11, 4, 59, 59))
        self.assertEqual(row.due_date, date(2026, 3, 10))

    def test_lima_midnight_is_the_next_local_day(self):
        row = self.ready(at(2026, 3, 11, 5, 0, 0))
        self.assertEqual(row.due_date, date(2026, 3, 11))

    def test_a_company_in_another_timezone_uses_its_own_local_day(self):
        settings_row = self.w.company.settings
        settings_row.timezone = 'Asia/Tokyo'
        settings_row.save(update_fields=['timezone'])
        # 15:30 UTC is 00:30 of the next day in Tokyo, still the 10th in Lima.
        row = self.ready(at(2026, 3, 10, 15, 30))
        self.assertEqual(row.due_date, date(2026, 3, 11))

    def test_n_days_are_added_to_the_local_date(self):
        with mock.patch.object(receivables, 'REPAIR_DUE_DAYS', 3):
            row = self.ready(at(2026, 3, 10, 15, 0))
        self.assertEqual(row.due_date, date(2026, 3, 13))

    def test_state_around_the_due_date(self):
        self.ready(at(2026, 3, 10, 15, 0))
        expectations = {
            date(2026, 3, 9): 'due_soon', date(2026, 3, 10): 'due_today', date(2026, 3, 11): 'overdue',
            date(2026, 6, 1): 'overdue',
        }
        for today, expected in expectations.items():
            with mock.patch('store.finance.services.company_today', return_value=today):
                self.assertEqual(api_row(self.api, self.order)['state'], expected, today)
        # A balance of zero is never overdue, whatever the date.
        self.w.pay(self.order, '500.00')
        with mock.patch('store.finance.services.company_today', return_value=date(2026, 6, 1)):
            self.assertEqual(api_row(self.api, self.order)['state'], 'paid')

    def test_the_first_arrival_wins_a_second_one_never_moves_it(self):
        self.ready(at(2026, 3, 10, 15, 0))
        self.w.force_status(self.order, RepairStatusCode.QUALITY_CONTROL)
        row = self.ready(at(2026, 3, 20, 15, 0))
        self.assertEqual(row.due_date, date(2026, 3, 10))

    def test_a_later_change_of_n_never_moves_a_due_date_already_set(self):
        row = self.ready(at(2026, 3, 10, 15, 0))
        self.assertEqual(row.due_date, date(2026, 3, 10))
        with mock.patch.object(receivables, 'REPAIR_DUE_DAYS', 5):
            self.assertFalse(receivables.backfill_repair_receivables(company=self.w.company)['due_dates_set'])
            self.w.force_status(self.order, RepairStatusCode.QUALITY_CONTROL)
            self.w.move(self.order, RepairStatusCode.READY_FOR_PICKUP, at=at(2026, 3, 20, 15, 0))
        row.refresh_from_db()
        self.assertEqual(row.due_date, date(2026, 3, 10))

    def test_delivered_with_a_balance_and_no_ready_event_is_due_that_day(self):
        self.w.force_status(self.order, RepairStatusCode.READY_FOR_PICKUP)
        self.w.move(self.order, RepairStatusCode.DELIVERED, at=at(2026, 4, 2, 15, 0))
        self.assertEqual(receivable_of(self.order).due_date, date(2026, 4, 2))

    def test_an_order_without_a_receivable_is_untouched_by_the_hook(self):
        free = self.w.order('0.00')
        self.w.force_status(free, RepairStatusCode.QUALITY_CONTROL)
        self.w.move(free, RepairStatusCode.READY_FOR_PICKUP, at=at(2026, 3, 10, 15, 0))
        self.assertFalse(Obligation.objects.filter(repair_order=free).exists())


class ParityWithTheRepairLedgerTest(TestCase):
    """The list reads the same numbers `service_payment_summary` computes, in every scenario."""

    def setUp(self):
        self.w = fx.RepairWorld('fin-parity', 'Taller Paridad')
        self.api = fx.client_for(fx.member(self.w.company, 'fin_parity_admin', fx.RECEIVABLES))

    def scenarios(self):
        unpaid = self.w.order('500.00')
        partial = self.w.order('500.00')
        self.w.pay(partial, '120.00')
        paid = self.w.order('300.00')
        self.w.pay(paid, '300.00')
        reversed_ = self.w.order('250.00')
        svc.reverse_service_payment(payment=self.w.pay(reversed_, '100.00'), reason='Error', actor=self.w.staff)
        over = self.w.order('500.00')
        self.w.pay(over, '400.00')
        self.w.decide(self.w.requote(over, '300.00'), self.w.customer, self.w.client_user, 'approve')
        review = self.w.order('500.00')
        self.w.pay(review, '50.00')
        svc.reopen_approved_quote(quote=svc.approved_quote(review), actor=self.w.staff, reason='Cambia')
        return [unpaid, partial, paid, reversed_, over, review]

    def test_the_list_equals_the_repair_summary(self):
        for order in self.scenarios():
            summary = svc.service_payment_summary(order)
            row = api_row(self.api, order)
            detail = self.api.get(f'{LIST}{row["id"]}/').json()
            quote = svc.financial_quote(order)
            if quote is None:  # in review: the last approved revision stands
                quoted = order.quotes.filter(approved_at__isnull=False).order_by('-revision').first().total
                paid = summary['confirmed_paid']
                expected = (quoted, paid, max(quoted - paid, D('0')), max(paid - quoted, D('0')))
            else:
                expected = (summary['quoted_total'], summary['confirmed_paid'], summary['outstanding'],
                            summary['credit'])
            for payload in (row, detail):
                self.assertEqual(
                    (D(payload['effective']), D(payload['paid']), D(payload['balance']), D(payload['credit'])),
                    expected, (order.number, payload['state']))

    def test_totals_are_the_sum_of_the_open_balances_per_currency(self):
        self.scenarios()
        total = D('0.00')
        for row in self.api.get(f'{LIST}?page_size=200').json()['results']:
            if row['state'] in ('pending', 'quote_review', 'due_soon', 'due_today', 'overdue'):
                total += D(row['balance'])
        self.assertEqual(self.api.get(SUMMARY).json()['open'], {'PEN': str(total)})
