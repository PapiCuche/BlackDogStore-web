"""
FIN-B1 — payments are append-only, never exceed what is owed, and a key names
ONE attempt on ONE obligation (docs/adr-finance-obligations.md §3.5–3.6).
"""
import threading
from datetime import date

from django.core.exceptions import ValidationError
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase

from store import finance_test_support as fx
from store.finance import services
from store.models import AdminAuditLog, Obligation, ObligationPayment, PaymentMethod

D = fx.D
CASH = PaymentMethod.CASH


class PaymentLedgerTest(TestCase):
    def setUp(self):
        self.company = fx.company()
        self.owner = fx.member(self.company, 'fin_owner', fx.ALL_FINANCE)
        self.rule = fx.rule(self.company, self.owner, today=date(2026, 1, 1))
        self.january = self.rule.periods.get(period_index=0)

    def pay(self, amount, obligation=None, **kw):
        kw.setdefault('paid_on', date(2026, 1, 3))
        return services.record_payment(obligation or self.january, amount=D(amount), method=CASH,
                                       actor=self.owner, **kw)

    def test_partial_then_total_payment_settles(self):
        self.pay('600.00')
        self.january.refresh_from_db()
        self.assertEqual(self.january.lifecycle, Obligation.LIFECYCLE_OPEN)
        self.pay('900.00')
        self.january.refresh_from_db()
        self.assertEqual(self.january.lifecycle, Obligation.LIFECYCLE_SETTLED)
        self.assertIsNotNone(self.january.settled_at)
        self.assertEqual(services.figures(self.january)['balance'], D('0.00'))
        self.assertEqual(services.state(self.january, today=date(2026, 3, 1)), 'paid')

    def test_overpayment_is_refused(self):
        self.pay('1000.00')
        with self.assertRaises(services.FinanceError) as caught:
            self.pay('600.00')
        self.assertIn('500.00', str(caught.exception))
        with self.assertRaises(services.FinanceError):
            self.pay('0.00')

    def test_a_settled_obligation_takes_no_more_payments(self):
        self.pay('1500.00')
        with self.assertRaises(services.FinanceError):
            self.pay('1.00')

    def test_a_payment_dated_in_the_future_is_refused(self):
        with self.assertRaises(services.FinanceError):
            self.pay('10.00', paid_on=date(2099, 1, 1))

    def test_same_idempotency_key_records_once(self):
        first = self.pay('100.00', idempotency_key='k-1')
        again = self.pay('100.00', idempotency_key='k-1')
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(self.january.payments.count(), 1)
        with self.assertRaises(services.FinanceConflict):
            self.pay('200.00', idempotency_key='k-1')

    def test_a_key_used_on_another_obligation_is_a_conflict_not_a_replay(self):
        february = self.rule.periods.get(period_index=1)
        self.pay('100.00', idempotency_key='k-1')
        with self.assertRaises(services.FinanceConflict):
            self.pay('100.00', obligation=february, idempotency_key='k-1')
        self.assertFalse(february.payments.exists())

    def test_reversal_reopens_keeps_history_and_is_idempotent(self):
        payment = self.pay('1500.00')
        with self.assertRaises(services.FinanceError):
            services.reverse_payment(payment, reason='', actor=self.owner)
        services.reverse_payment(payment, reason='Se digitó en la obligación equivocada', actor=self.owner)
        again = services.reverse_payment(payment, reason='otra vez', actor=self.owner)
        self.january.refresh_from_db()
        self.assertEqual(self.january.lifecycle, Obligation.LIFECYCLE_OPEN)
        self.assertEqual(services.figures(self.january)['balance'], D('1500.00'))
        self.assertEqual(again.reversal_reason, 'Se digitó en la obligación equivocada')
        self.assertEqual(self.january.payments.count(), 1)
        self.assertEqual(AdminAuditLog.objects.filter(
            company=self.company, action='finance_payment_reversed').count(), 1)

    def test_payment_rows_cannot_be_deleted_or_edited(self):
        payment = self.pay('100.00')
        with self.assertRaises(ValidationError):
            payment.delete()
        payment.amount = D('1.00')
        with self.assertRaises(ValidationError):
            payment.save()
        self.assertEqual(ObligationPayment.objects.get(pk=payment.pk).amount, D('100.00'))

    def test_variable_amount_changes_one_period_only(self):
        february = self.rule.periods.get(period_index=1)
        services.adjust(february, kind='amount_change', new_amount=D('1650.00'),
                        reason='Recibo de luz más alto', actor=self.owner)
        self.assertEqual(services.figures(february)['effective'], D('1650.00'))
        self.assertEqual(services.figures(self.january)['effective'], D('1500.00'))
        self.rule.refresh_from_db()
        self.assertEqual(self.rule.amount, D('1500.00'))

    def test_an_adjustment_cannot_leave_less_owed_than_paid(self):
        self.pay('1000.00')
        with self.assertRaises(services.FinanceError):
            services.adjust(self.january, kind='decrease', amount=D('600.00'), reason='Descuento', actor=self.owner)
        services.adjust(self.january, kind='decrease', amount=D('500.00'), reason='Descuento', actor=self.owner)
        self.january.refresh_from_db()
        self.assertEqual(self.january.lifecycle, Obligation.LIFECYCLE_SETTLED)

    def test_payments_api_needs_its_own_capability(self):
        viewer = fx.member(self.company, 'fin_viewer', ('finance.view',))
        url = f'/api/admin/finance/obligations/{self.january.pk}/payments/'
        payload = {'amount': '10.00', 'method': 'cash', 'paid_on': '2026-01-03'}
        self.assertEqual(fx.client_for(viewer).post(url, payload, format='json').status_code, 403)
        response = fx.client_for(self.owner).post(url, payload, format='json', HTTP_IDEMPOTENCY_KEY='p-1')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['obligation']['balance'], '1490.00')
        reverse = f"{url}{response.json()['payment']['id']}/reverse/"
        recorder = fx.member(self.company, 'fin_cashier', ('finance.view', 'finance.payments.record'))
        self.assertEqual(fx.client_for(recorder).post(reverse, {'reason': 'Error'}, format='json').status_code, 403)
        self.assertEqual(fx.client_for(self.owner).post(reverse, {'reason': 'Error'}, format='json').status_code, 200)


class ConcurrentPaymentTest(TransactionTestCase):
    """Two cashiers pay the last balance at the same instant: one wins."""

    def test_concurrent_full_payments_only_one_wins(self):
        if connection.vendor == 'sqlite':
            self.skipTest('SQLite has no row locks: a green result would prove nothing.')
        company = fx.company('fin-pay-race', 'Finanzas Pago')
        owner = fx.member(company, 'fin_pay_owner', fx.ALL_FINANCE)
        rule = fx.rule(company, owner, today=date(2026, 1, 1))
        january = rule.periods.get(period_index=0)
        barrier, wins, errors = threading.Barrier(2), [], []

        def pay():
            try:
                barrier.wait()
                wins.append(services.record_payment(january, amount=D('1500.00'), method=CASH, actor=owner,
                                                    paid_on=date(2026, 1, 3)).pk)
            except services.FinanceError as exc:
                errors.append(exc)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=pay) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(len(wins), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(services.figures(january)['paid'], D('1500.00'))


class ReviewPinsTest(TestCase):
    """#188 review: references, sub-cent amounts, replay the next day, reversed payments in lists."""

    def setUp(self):
        self.company = fx.company()
        self.owner = fx.member(self.company, 'fin_owner', fx.ALL_FINANCE)
        self.january = fx.rule(self.company, self.owner, today=date(2026, 1, 1)).periods.get(period_index=0)
        self.url = f'/api/admin/finance/obligations/{self.january.pk}/payments/'

    def test_a_bank_cci_is_a_valid_reference_and_a_card_number_is_a_400(self):
        api = fx.client_for(self.owner)
        ok = api.post(self.url, {'amount': '10.00', 'method': 'transfer', 'paid_on': '2026-01-03',
                                 'reference': '00219300123456789012'}, format='json')
        self.assertEqual(ok.status_code, 201, ok.content)
        card = api.post(self.url, {'amount': '10.00', 'method': 'card', 'paid_on': '2026-01-03',
                                   'reference': '4111 1111 1111 1111'}, format='json')
        self.assertEqual(card.status_code, 400)
        self.assertIn('tarjeta', card.json()['detail'])

    def test_sub_cent_amounts_are_refused_not_rounded(self):
        with self.assertRaises(services.FinanceError):
            services.record_payment(self.january, amount='10.005', method=CASH, actor=self.owner,
                                    paid_on=date(2026, 1, 3))
        self.assertFalse(self.january.payments.exists())

    def test_a_retry_the_next_day_without_a_date_replays(self):
        from unittest import mock

        with mock.patch('store.finance.services.company_today', return_value=date(2026, 1, 3)):
            first = services.record_payment(self.january, amount=D('10.00'), method=CASH, actor=self.owner,
                                            idempotency_key='k-dia')
        with mock.patch('store.finance.services.company_today', return_value=date(2026, 1, 4)):
            again = services.record_payment(self.january, amount=D('10.00'), method=CASH, actor=self.owner,
                                            idempotency_key='k-dia')
        self.assertEqual(first.pk, again.pk)

    def test_lists_and_summary_ignore_reversed_payments(self):
        from unittest import mock

        payment = services.record_payment(self.january, amount=D('1500.00'), method=CASH, actor=self.owner,
                                          paid_on=date(2026, 1, 3))
        services.reverse_payment(payment, reason='Error', actor=self.owner)
        api = fx.client_for(self.owner)
        with mock.patch('store.finance.services.company_today', return_value=date(2026, 1, 3)):
            row = next(r for r in api.get('/api/admin/finance/obligations/').json()['results']
                       if r['id'] == self.january.pk)
            summary = api.get('/api/admin/finance/summary/').json()
        self.assertEqual(row['balance'], '1500.00')
        self.assertEqual(row['state'], 'due_soon')
        self.assertEqual(summary['open'], {'PEN': '3000.00'})

    def test_audit_carries_the_branch(self):
        north = fx.branch(self.company, 'Norte')
        obligation = services.create_obligation(self.company, actor=self.owner, concept='Luz', amount=D('5.00'),
                                                due_date=date(2026, 2, 1), branch=north, today=date(2026, 1, 1))
        services.record_payment(obligation, amount=D('1.00'), method=CASH, actor=self.owner, paid_on=date(2026, 1, 1))
        rows = AdminAuditLog.objects.filter(company=self.company, target_id=str(obligation.pk))
        self.assertTrue(rows.exists())
        self.assertTrue(all(row.metadata.get('branch_id') == north.pk for row in rows))

    def test_the_rules_list_does_not_query_per_rule(self):
        api = fx.client_for(self.owner)
        api.get('/api/admin/finance/rules/')
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as one:
            api.get('/api/admin/finance/rules/')
        for n in range(4):
            fx.rule(self.company, self.owner, today=date(2026, 1, 1), concept=f'Regla {n}')
        with CaptureQueriesContext(connection) as five:
            api.get('/api/admin/finance/rules/')
        self.assertEqual(len(one.captured_queries), len(five.captured_queries))


class AdjustmentReplayTest(TestCase):
    def test_an_identical_adjustment_with_its_key_replays(self):
        company = fx.company('fin-adj', 'Ajustes')
        owner = fx.member(company, 'fin_adj_owner', fx.ALL_FINANCE)
        january = fx.rule(company, owner, today=date(2026, 1, 1)).periods.get(period_index=0)
        first = services.adjust(january, kind='increase', amount=D('10.00'), reason='Mora', actor=owner,
                                idempotency_key='aj-1')
        again = services.adjust(january, kind='increase', amount=D('10.00'), reason='Mora', actor=owner,
                                idempotency_key='aj-1')
        self.assertEqual(first.pk, again.pk)
        with self.assertRaises(services.FinanceConflict):
            services.adjust(january, kind='increase', amount=D('11.00'), reason='Mora', actor=owner,
                            idempotency_key='aj-1')
        self.assertEqual(services.figures(january)['effective'], D('1510.00'))
