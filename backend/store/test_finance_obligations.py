"""
FIN-B1 — what is owed stays visible until it is paid or voided; the state is
derived in the company's local date (docs/adr-finance-obligations.md §3.4).
"""
from datetime import date, datetime, timezone as dt_timezone
from unittest import mock

from django.test import TestCase

from store import finance_test_support as fx
from store.finance import services
from store.models import AdminAuditLog, Obligation, PaymentMethod

D = fx.D
LIST = '/api/admin/finance/obligations/'


def detail(obligation, suffix=''):
    return f'{LIST}{obligation.pk}/{suffix}'


class PersistenceAndStateTest(TestCase):
    def setUp(self):
        self.company = fx.company()
        self.owner = fx.member(self.company, 'fin_owner', fx.ALL_FINANCE)
        self.rule = fx.rule(self.company, self.owner, today=date(2026, 1, 1))
        self.january = self.rule.periods.get(period_index=0)

    def test_overdue_unpaid_period_stays_open_and_listed(self):
        for day in (date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 5)):
            services.generate_all(today=day)
        self.january.refresh_from_db()
        self.assertEqual(self.january.lifecycle, Obligation.LIFECYCLE_OPEN)
        self.assertEqual(services.state(self.january, today=date(2026, 4, 5)), 'overdue')
        with mock.patch('store.finance.services.company_today', return_value=date(2026, 4, 5)):
            body = fx.client_for(self.owner).get(LIST, {'state': 'overdue'}).json()
        self.assertIn(self.january.pk, [row['id'] for row in body['results']])
        row = next(r for r in body['results'] if r['id'] == self.january.pk)
        self.assertEqual(row['state'], 'overdue')
        self.assertEqual(row['balance'], '1500.00')

    def test_overdue_state_uses_company_local_date(self):
        # 23:30 in Lima on the due date is 04:30 UTC the next day: still «vence hoy».
        late_evening = datetime(2026, 1, 6, 4, 30, tzinfo=dt_timezone.utc)
        with mock.patch('django.utils.timezone.now', return_value=late_evening):
            today = services.company_today(self.company)
        self.assertEqual(today, date(2026, 1, 5))
        self.assertEqual(services.state(self.january, today=today), 'due_today')
        self.assertEqual(services.state(self.january, today=date(2026, 1, 6)), 'overdue')
        self.assertEqual(services.state(self.january, today=date(2025, 12, 31)), 'due_soon')
        self.assertEqual(services.state(self.january, today=date(2025, 12, 1)), 'pending')

    def test_partial_state_is_a_flag_over_the_date_state(self):
        services.record_payment(self.january, amount=D('500.00'), method=PaymentMethod.CASH, actor=self.owner,
                                paid_on=date(2026, 1, 2))
        figures = services.figures(self.january)
        self.assertEqual(figures['balance'], D('1000.00'))
        self.assertTrue(figures['partially_paid'])

    def test_void_requires_reason_capability_and_no_payments(self):
        api = fx.client_for(self.owner)
        self.assertEqual(api.post(detail(self.january, 'void/'), {'reason': ''}, format='json').status_code, 400)
        viewer = fx.member(self.company, 'fin_viewer', ('finance.view',))
        self.assertEqual(fx.client_for(viewer).post(
            detail(self.january, 'void/'), {'reason': 'Duplicado'}, format='json').status_code, 403)
        february = self.rule.periods.get(period_index=1)
        services.record_payment(february, amount=D('10.00'), method=PaymentMethod.CASH, actor=self.owner,
                                paid_on=date(2026, 1, 2))
        self.assertEqual(api.post(detail(february, 'void/'), {'reason': 'Duplicado'}, format='json').status_code, 409)
        response = api.post(detail(self.january, 'void/'), {'reason': 'Duplicado'}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.january.refresh_from_db()
        self.assertEqual(self.january.lifecycle, Obligation.LIFECYCLE_VOIDED)
        self.assertEqual(self.january.void_reason, 'Duplicado')
        self.assertTrue(AdminAuditLog.objects.filter(
            company=self.company, action='finance_obligation_voided', target_id=str(self.january.pk)).exists())
        self.assertEqual(services.state(self.january, today=date(2026, 6, 1)), 'voided')

    def test_dispute_requires_reason_and_capability_and_keeps_balance(self):
        api = fx.client_for(self.owner)
        self.assertEqual(api.post(detail(self.january, 'dispute/'), {'reason': ''}, format='json').status_code, 400)
        manager = fx.member(self.company, 'fin_manager', ('finance.view', 'finance.manage'))
        self.assertEqual(fx.client_for(manager).post(
            detail(self.january, 'dispute/'), {'reason': 'El arrendador cobra de más'},
            format='json').status_code, 403)
        response = api.post(detail(self.january, 'dispute/'), {'reason': 'El arrendador cobra de más'}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.january.refresh_from_db()
        self.assertTrue(self.january.is_disputed)
        self.assertEqual(services.figures(self.january)['balance'], D('1500.00'))
        self.assertEqual(response.json()['disputed'], True)
        self.assertEqual(
            api.post(detail(self.january, 'dispute/resolve/'), {'resolution': 'Aclarado'},
                     format='json').status_code, 200)
        self.january.refresh_from_db()
        self.assertFalse(self.january.is_disputed)
        self.assertEqual(AdminAuditLog.objects.filter(
            company=self.company, action__startswith='finance_obligation_dispute').count(), 2)

    def test_totals_are_grouped_by_currency(self):
        services.create_obligation(self.company, actor=self.owner, concept='Licencia anual', amount=D('50.00'),
                                   currency='USD', due_date=date(2026, 1, 20), today=date(2026, 1, 1))
        with mock.patch('store.finance.services.company_today', return_value=date(2026, 1, 1)):
            body = fx.client_for(self.owner).get('/api/admin/finance/summary/').json()
        self.assertEqual(body['open'], {'PEN': '3000.00', 'USD': '50.00'})
        self.assertNotIn('total', body)

    def test_a_manual_payable_is_idempotent_by_key(self):
        api = fx.client_for(self.owner)
        payload = {'concept': 'Mantenimiento del aire', 'amount': '320.00', 'due_date': '2026-02-15'}
        first = api.post(LIST, payload, format='json', HTTP_IDEMPOTENCY_KEY='alta-1')
        again = api.post(LIST, payload, format='json', HTTP_IDEMPOTENCY_KEY='alta-1')
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(again.json()['id'], first.json()['id'])
        other = api.post(LIST, {**payload, 'amount': '999.00'}, format='json', HTTP_IDEMPOTENCY_KEY='alta-1')
        self.assertEqual(other.status_code, 409)
