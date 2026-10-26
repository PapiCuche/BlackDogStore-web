"""
FIN-B1 — a rule is not a debt; each period is generated once, never deleted,
and a pause stops only what was never due (docs/adr-finance-obligations.md §4).
"""
import threading
from datetime import date

from django.db import connection, connections
from django.test import TestCase, TransactionTestCase

from store import finance_test_support as fx
from store.finance import services
from store.models import AdminAuditLog, Obligation, PaymentMethod

D = fx.D


def dues(rule):
    return list(rule.periods.order_by('period_index').values_list('due_date', 'lifecycle', 'void_kind'))


class GenerationTest(TestCase):
    def setUp(self):
        self.company = fx.company()
        self.owner = fx.member(self.company, 'fin_owner', fx.ALL_FINANCE)

    def test_monthly_rule_generates_one_period_per_month_idempotently(self):
        rule = fx.rule(self.company, self.owner, today=date(2026, 1, 1))
        for _ in range(3):
            services.generate_periods(rule, today=date(2026, 1, 10))
        self.assertEqual([d for d, _l, _k in dues(rule)], [date(2026, 1, 5), date(2026, 2, 5)])
        self.assertEqual(
            sorted(rule.periods.values_list('period_index', flat=True)), [0, 1])

    def test_each_period_carries_the_rule_amount_and_an_internal_reference(self):
        rule = fx.rule(self.company, self.owner, today=date(2026, 1, 1))
        period = rule.periods.order_by('period_index').first()
        self.assertEqual(period.amount_original, D('1500.00'))
        self.assertEqual(period.direction, 'payable')
        self.assertEqual(period.origin_type, Obligation.ORIGIN_FIXED)
        self.assertTrue(period.reference.startswith('OB-'))

    def test_a_rule_created_late_starts_at_the_first_period_still_to_come(self):
        rule = fx.rule(self.company, self.owner, today=date(2026, 10, 25))
        self.assertEqual([d for d, _l, _k in dues(rule)], [date(2026, 11, 5)])

    def test_past_periods_only_when_asked_and_audited(self):
        rule = fx.rule(self.company, self.owner, today=date(2026, 3, 20), generate_past=True)
        self.assertEqual([d for d, _l, _k in dues(rule)],
                         [date(2026, 1, 5), date(2026, 2, 5), date(2026, 3, 5), date(2026, 4, 5)])
        self.assertTrue(AdminAuditLog.objects.filter(
            company=self.company, action='finance_rule_created', metadata__generate_past=True).exists())

    def test_the_amount_of_the_rule_changes_only_future_periods(self):
        rule = fx.rule(self.company, self.owner, today=date(2026, 1, 1))
        services.update_rule(rule, actor=self.owner, amount=D('1600.00'))
        services.generate_periods(rule, today=date(2026, 2, 10))
        amounts = list(rule.periods.order_by('period_index').values_list('amount_original', flat=True))
        self.assertEqual(amounts, [D('1500.00'), D('1500.00'), D('1600.00')])

    def test_the_calendar_of_a_rule_with_periods_is_frozen(self):
        rule = fx.rule(self.company, self.owner, today=date(2026, 1, 1))
        with self.assertRaises(services.FinanceError):
            services.update_rule(rule, actor=self.owner, due_day=10)

    def test_paused_rule_stops_future_periods_only(self):
        rule = fx.rule(self.company, self.owner, today=date(2026, 1, 1))
        services.pause_rule(rule, reason='Local cerrado por obras', actor=self.owner, today=date(2026, 1, 12))
        services.generate_periods(rule, today=date(2026, 3, 1))
        # January was due before the pause: still owed. Nothing new is generated.
        self.assertEqual(dues(rule)[0], (date(2026, 1, 5), 'open', ''))
        self.assertEqual(rule.periods.count(), 2)

    def test_pause_voids_pre_generated_unpaid_periods_as_never_due(self):
        rule = fx.rule(self.company, self.owner, today=date(2026, 1, 1))
        services.pause_rule(rule, reason='Local cerrado por obras', actor=self.owner, today=date(2026, 1, 12))
        self.assertEqual(dues(rule)[1], (date(2026, 2, 5), 'voided', Obligation.VOID_NEVER_DUE))
        self.assertTrue(AdminAuditLog.objects.filter(
            company=self.company, action='finance_obligation_voided', metadata__kind='never_due').exists())

    def test_a_pre_generated_period_with_a_payment_is_not_voided_by_a_pause(self):
        rule = fx.rule(self.company, self.owner, today=date(2026, 1, 1))
        february = rule.periods.get(period_index=1)
        services.record_payment(february, amount=D('100.00'), method=PaymentMethod.CASH, actor=self.owner,
                                paid_on=date(2026, 1, 11))
        services.pause_rule(rule, reason='Obras', actor=self.owner, today=date(2026, 1, 12))
        february.refresh_from_db()
        self.assertEqual(february.lifecycle, Obligation.LIFECYCLE_OPEN)

    def test_resume_does_not_backfill_and_a_period_can_be_generated_by_hand(self):
        rule = fx.rule(self.company, self.owner, today=date(2026, 1, 1))
        services.pause_rule(rule, reason='Obras', actor=self.owner, today=date(2026, 1, 12))
        services.resume_rule(rule, actor=self.owner, today=date(2026, 3, 10))
        services.generate_periods(rule, today=date(2026, 3, 10))
        open_dues = list(rule.periods.filter(lifecycle='open').order_by('due_date').values_list('due_date', flat=True))
        self.assertEqual(open_dues, [date(2026, 1, 5), date(2026, 4, 5)])
        services.generate_period(rule, index=2, actor=self.owner)
        self.assertTrue(rule.periods.filter(period_index=2, due_date=date(2026, 3, 5)).exists())
        with self.assertRaises(services.FinanceError):
            services.generate_period(rule, index=2, actor=self.owner)

    def test_ending_a_rule_voids_what_falls_after_the_end(self):
        rule = fx.rule(self.company, self.owner, today=date(2026, 1, 1))
        services.end_rule(rule, end_date=date(2026, 1, 31), reason='Contrato terminado', actor=self.owner)
        services.generate_periods(rule, today=date(2026, 4, 1))
        self.assertEqual(dues(rule), [(date(2026, 1, 5), 'open', ''),
                                      (date(2026, 2, 5), 'voided', Obligation.VOID_NEVER_DUE)])

    def test_a_period_is_never_deleted(self):
        rule = fx.rule(self.company, self.owner, today=date(2026, 1, 1))
        period = rule.periods.first()
        with self.assertRaises(Exception):
            period.delete()
        self.assertTrue(Obligation.objects.filter(pk=period.pk).exists())


class TwoGeneratorsAtOnceTest(TransactionTestCase):
    """Two cron passes at the same instant: one period, no visible error."""

    def test_two_workers_generate_the_same_period_once(self):
        if connection.vendor == 'sqlite':
            self.skipTest('SQLite has no row locks: a green result would prove nothing.')
        company = fx.company('fin-race', 'Finanzas Carrera')
        owner = fx.member(company, 'fin_race_owner', fx.ALL_FINANCE)
        rule = fx.rule(company, owner, today=date(2026, 1, 1), anchor_date=date(2026, 3, 1))
        self.assertEqual(rule.periods.count(), 0)
        barrier, errors = threading.Barrier(2), []

        def run():
            try:
                barrier.wait()
                services.generate_periods(rule, today=date(2026, 3, 1))
            except Exception as exc:  # noqa: BLE001 — the assertion reads it
                errors.append(exc)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(rule.periods.values_list('period_index', flat=True)), [0, 1])


class GenerationCommandTest(TestCase):
    def test_the_command_is_idempotent_and_scoped(self):
        from io import StringIO
        from unittest import mock

        from django.core.management import call_command

        company = fx.company('fin-cmd', 'Comando')
        owner = fx.member(company, 'fin_cmd_owner', fx.ALL_FINANCE)
        rule = fx.rule(company, owner, today=date(2026, 1, 1))
        with mock.patch('store.finance.services.company_today', return_value=date(2026, 2, 20)):
            out = StringIO()
            call_command('generate_finance_obligations', '--company', 'fin-cmd', stdout=out)
            call_command('generate_finance_obligations', stdout=StringIO())
        self.assertIn('1', out.getvalue())
        self.assertEqual(sorted(rule.periods.values_list('period_index', flat=True)), [0, 1, 2])
