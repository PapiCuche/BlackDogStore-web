"""
FIN-B1 — owner checks on pauses (#188 review): a pause never destroys, voids or
hides what was accrued; projected periods it covers are «En pausa» by DERIVED
state, queryable and traceable; dates are the company's own, around midnight.
"""
from datetime import date, datetime, timezone as dt_timezone
from unittest import mock

from django.test import TestCase

from store import finance_test_support as fx
from store.finance import services
from store.models import AdminAuditLog, CompanySettings, Obligation, PaymentMethod

D = fx.D
LIST = '/api/admin/finance/obligations/'


def state_of(obligation, today):
    row = services.with_figures(Obligation.objects.filter(pk=obligation.pk)).get()
    return services.state(row, today=today)


class PauseKeepsWhatWasAccruedTest(TestCase):
    def setUp(self):
        self.company = fx.company()
        self.owner = fx.member(self.company, 'fin_owner', fx.ALL_FINANCE)
        # Created on Jan 1st: January (due 5th) and February (due 5th) exist.
        self.rule = fx.rule(self.company, self.owner, today=date(2026, 1, 1))
        self.january = self.rule.periods.get(period_index=0)
        self.february = self.rule.periods.get(period_index=1)

    def test_pause_and_resume_void_nothing_and_keep_every_row(self):
        services.pause_rule(self.rule, reason='Obras', actor=self.owner, today=date(2026, 1, 12))
        services.resume_rule(self.rule, actor=self.owner, today=date(2026, 3, 10))
        self.assertEqual(set(self.rule.periods.values_list('lifecycle', flat=True)), {Obligation.LIFECYCLE_OPEN})
        self.assertEqual(self.rule.periods.count(), 2)
        self.assertFalse(AdminAuditLog.objects.filter(company=self.company,
                                                      action='finance_obligation_voided').exists())

    def test_an_overdue_period_before_the_pause_stays_owed(self):
        services.pause_rule(self.rule, reason='Obras', actor=self.owner, today=date(2026, 1, 12))
        self.assertEqual(state_of(self.january, date(2026, 2, 10)), 'overdue')

    def test_a_projected_period_in_the_pause_is_listed_as_paused_and_not_counted(self):
        services.pause_rule(self.rule, reason='Obras', actor=self.owner, today=date(2026, 1, 12))
        api = fx.client_for(self.owner)
        with mock.patch('store.finance.services.company_today', return_value=date(2026, 2, 10)):
            paused = api.get(LIST, {'state': 'paused'}).json()['results']
            overdue = api.get(LIST, {'state': 'overdue'}).json()['results']
            summary = api.get('/api/admin/finance/summary/').json()
            detail = api.get(f'{LIST}{self.february.pk}/').json()
        self.assertEqual([r['id'] for r in paused], [self.february.pk])
        self.assertEqual(paused[0]['state'], 'paused')
        self.assertEqual([r['id'] for r in overdue], [self.january.pk])
        self.assertEqual(summary['open'], {'PEN': '1500.00'})
        self.assertEqual(summary['overdue'], {'PEN': '1500.00'})
        self.assertEqual(detail['balance'], '1500.00')

    def test_after_resume_what_falls_after_is_owed_and_what_fell_inside_stays_paused(self):
        services.pause_rule(self.rule, reason='Obras', actor=self.owner, today=date(2026, 1, 12))
        services.resume_rule(self.rule, actor=self.owner, today=date(2026, 2, 5))  # resumed ON February's due date
        self.assertEqual(state_of(self.february, date(2026, 2, 10)), 'overdue')
        rule = fx.rule(self.company, self.owner, today=date(2026, 1, 1), concept='Internet')
        services.pause_rule(rule, reason='Mudanza', actor=self.owner, today=date(2026, 1, 12))
        services.resume_rule(rule, actor=self.owner, today=date(2026, 2, 6))  # the day after
        self.assertEqual(state_of(rule.periods.get(period_index=1), date(2026, 2, 10)), 'paused')
        self.assertTrue(AdminAuditLog.objects.filter(company=self.company, action='finance_rule_resumed').exists())

    def test_a_period_with_a_payment_is_never_paused(self):
        services.record_payment(self.february, amount=D('100.00'), method=PaymentMethod.CASH, actor=self.owner,
                                paid_on=date(2026, 1, 3))
        services.pause_rule(self.rule, reason='Obras', actor=self.owner, today=date(2026, 1, 12))
        self.assertEqual(state_of(self.february, date(2026, 2, 10)), 'overdue')
        with mock.patch('store.finance.services.company_today', return_value=date(2026, 2, 10)):
            api = fx.client_for(self.owner)
            summary = api.get('/api/admin/finance/summary/').json()
            overdue = [r['id'] for r in api.get(LIST, {'state': 'overdue'}).json()['results']]
            paused = [r['id'] for r in api.get(LIST, {'state': 'paused'}).json()['results']]
        self.assertEqual(summary['open'], {'PEN': '2900.00'})
        self.assertIn(self.february.pk, overdue)
        self.assertEqual(paused, [])

    def test_a_period_with_an_adjustment_is_never_paused(self):
        services.adjust(self.february, kind='amount_change', new_amount=D('1600.00'), reason='Reajuste',
                        actor=self.owner)
        services.pause_rule(self.rule, reason='Obras', actor=self.owner, today=date(2026, 1, 12))
        self.assertEqual(state_of(self.february, date(2026, 2, 10)), 'overdue')
        with mock.patch('store.finance.services.company_today', return_value=date(2026, 2, 10)):
            ids = [r['id'] for r in fx.client_for(self.owner).get(LIST, {'state': 'overdue'}).json()['results']]
        self.assertIn(self.february.pk, ids)

    def test_a_reversed_payment_does_not_keep_a_period_out_of_the_pause(self):
        payment = services.record_payment(self.february, amount=D('100.00'), method=PaymentMethod.CASH,
                                          actor=self.owner, paid_on=date(2026, 1, 3))
        services.reverse_payment(payment, reason='Error', actor=self.owner)
        services.pause_rule(self.rule, reason='Obras', actor=self.owner, today=date(2026, 1, 12))
        self.assertEqual(state_of(self.february, date(2026, 2, 10)), 'paused')
        self.assertEqual(self.february.payments.count(), 1)


class PauseDatesAreTheCompanysTest(TestCase):
    """A pause starts and ends on the company's own date, not on the server's."""

    def company_in(self, slug, tz):
        company = fx.company(slug, slug)
        CompanySettings.objects.filter(company=company).update(timezone=tz)
        company.refresh_from_db()
        owner = fx.member(company, f'{slug}_owner', fx.ALL_FINANCE)
        return company, owner

    def test_pausing_just_after_midnight_in_tokyo_is_still_the_previous_day_in_lima(self):
        # 2026-03-04 16:30 UTC = Mar 5th 01:30 in Tokyo, Mar 4th 11:30 in Lima.
        instant = datetime(2026, 3, 4, 16, 30, tzinfo=dt_timezone.utc)
        results = {}
        for slug, tz in (('fin-lima', 'America/Lima'), ('fin-tokyo', 'Asia/Tokyo')):
            company, owner = self.company_in(slug, tz)
            rule = fx.rule(company, owner, today=date(2026, 3, 1))  # March (due 5th) exists
            with mock.patch('django.utils.timezone.now', return_value=instant):
                pause = services.pause_rule(rule, reason='Obras', actor=owner)
            results[tz] = (pause.paused_from, state_of(rule.periods.get(due_date=date(2026, 3, 5)), date(2026, 3, 10)))
        # Lima paused on the 4th: March 5th falls inside the pause. Tokyo paused ON
        # the 5th: a period due the day a pause begins is still owed.
        self.assertEqual(results['America/Lima'], (date(2026, 3, 4), 'paused'))
        self.assertEqual(results['Asia/Tokyo'], (date(2026, 3, 5), 'overdue'))

    def test_resuming_just_before_midnight_in_lima(self):
        # 2026-03-06 04:30 UTC = Mar 5th 23:30 in Lima: resumed ON March 5th.
        company, owner = self.company_in('fin-lima2', 'America/Lima')
        rule = fx.rule(company, owner, today=date(2026, 3, 1))
        services.pause_rule(rule, reason='Obras', actor=owner, today=date(2026, 3, 2))
        with mock.patch('django.utils.timezone.now', return_value=datetime(2026, 3, 6, 4, 30, tzinfo=dt_timezone.utc)):
            pause = services.resume_rule(rule, actor=owner)
        self.assertEqual(pause.resumed_on, date(2026, 3, 5))
        self.assertEqual(state_of(rule.periods.get(due_date=date(2026, 3, 5)), date(2026, 3, 10)), 'overdue')

    def test_ending_on_the_company_date_voids_only_what_is_not_yet_due(self):
        # 2026-03-04 16:30 UTC: Tokyo is already on the 5th, so March is due TODAY there and is kept;
        # in Lima it is still the 4th and March (due tomorrow) is not yet due: voided as never due.
        instant = datetime(2026, 3, 4, 16, 30, tzinfo=dt_timezone.utc)
        kept = {}
        for slug, tz in (('fin-end-lima', 'America/Lima'), ('fin-end-tokyo', 'Asia/Tokyo')):
            company, owner = self.company_in(slug, tz)
            rule = fx.rule(company, owner, today=date(2026, 3, 1))
            with mock.patch('django.utils.timezone.now', return_value=instant):
                services.end_rule(rule, end_date=date(2026, 3, 1), reason='Fin', actor=owner)
            kept[tz] = rule.periods.get(due_date=date(2026, 3, 5)).lifecycle
        self.assertEqual(kept, {'America/Lima': Obligation.LIFECYCLE_VOIDED, 'Asia/Tokyo': Obligation.LIFECYCLE_OPEN})
