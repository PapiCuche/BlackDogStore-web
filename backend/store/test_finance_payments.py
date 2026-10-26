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
