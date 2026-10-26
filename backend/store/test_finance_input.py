"""
FIN-B1 — a malformed JSON body is a 400, never a 500, and a repair-derived row
is not reachable through the fixed-payment actions (docs/adr-finance-obligations.md §3.5).
"""
from datetime import date
from unittest import mock

from django.test import TestCase

from store import finance_test_support as fx
from store.finance import services
from store.models import Obligation

OBLIGATIONS = '/api/admin/finance/obligations/'
RULES = '/api/admin/finance/rules/'
CATEGORIES = '/api/admin/finance/categories/'


class MalformedBodyTest(TestCase):
    def setUp(self):
        self.company = fx.company()
        self.owner = fx.member(self.company, 'fin_input', fx.ALL_FINANCE)
        self.january = fx.rule(self.company, self.owner, today=date(2026, 1, 1)).periods.get(period_index=0)
        self.api = fx.client_for(self.owner)
        patcher = mock.patch('store.finance.services.company_today', return_value=date(2026, 1, 5))
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, url, payload):
        return self.api.post(url, payload, format='json')

    def test_a_numeric_reference_is_kept_as_text(self):
        response = self.post(f'{OBLIGATIONS}{self.january.pk}/payments/',
                             {'amount': '10.00', 'method': 'cash', 'reference': 12345678901234})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(self.january.payments.get().reference, '12345678901234')

    def test_a_non_text_value_in_a_text_field_is_a_400(self):
        base = f'{OBLIGATIONS}{self.january.pk}/'
        for url, payload in (
            (f'{base}payments/', {'amount': '10.00', 'method': 'cash', 'reference': {'a': 1}}),
            (f'{base}payments/', {'amount': '10.00', 'method': ['cash']}),
            (f'{base}void/', {'reason': 12.5}),
            (f'{base}void/', {'reason': ['x']}),
            (f'{base}dispute/', {'reason': {'x': 1}}),
            (OBLIGATIONS, {'concept': {'x': 1}, 'amount': '10.00', 'due_date': '2026-02-01'}),
            (CATEGORIES, {'direction': 'payable', 'name': ['x']}),
        ):
            response = self.post(url, payload)
            self.assertEqual(response.status_code, 400, (url, payload, response.content))

    def test_a_body_that_is_not_an_object_is_a_400(self):
        base = f'{OBLIGATIONS}{self.january.pk}/'
        for url in (f'{base}payments/', f'{base}void/', f'{base}adjustments/', OBLIGATIONS, RULES, CATEGORIES):
            for payload in ([1, 2], 'text', 5):
                response = self.post(url, payload)
                self.assertEqual(response.status_code, 400, (url, payload, response.content))
        self.assertEqual(self.january.payments.count(), 0)

    def test_a_repair_derived_row_takes_no_fixed_payment_action(self):
        Obligation.objects.filter(pk=self.january.pk).update(origin_type=Obligation.ORIGIN_REPAIR)
        base = f'{OBLIGATIONS}{self.january.pk}/'
        for suffix in ('dispute/', 'void/', 'payments/', 'adjustments/'):
            response = self.post(base + suffix, {'reason': 'x', 'amount': '1.00', 'method': 'cash',
                                                 'kind': 'increase'})
            self.assertEqual(response.status_code, 404, suffix)


class GenerateByHandTest(TestCase):
    def test_a_period_older_than_the_past_limit_is_refused(self):
        company = fx.company()
        owner = fx.member(company, 'fin_old', fx.ALL_FINANCE)
        rule = fx.rule(company, owner, today=date(2026, 1, 1))
        with self.assertRaises(services.FinanceError):
            services.generate_period(rule, index=2, actor=owner, today=date(2030, 1, 1))
        self.assertFalse(rule.periods.filter(period_index=2).exists())
        services.generate_period(rule, index=2, actor=owner, today=date(2026, 3, 1))
        self.assertTrue(rule.periods.filter(period_index=2).exists())


class JunkInputTest(TestCase):
    def setUp(self):
        self.company = fx.company()
        self.owner = fx.member(self.company, 'fin_junk', fx.ALL_FINANCE)
        self.rule = fx.rule(self.company, self.owner, today=date(2026, 1, 1))
        self.api = fx.client_for(self.owner)

    def test_junk_frequency_and_branch_are_a_400_on_create(self):
        base = {'concept': 'Luz', 'amount': '10.00', 'frequency': 'monthly', 'anchor_date': '2026-01-01',
                'due_day': 5}
        for junk in (['monthly'], {'a': 1}, 5):
            response = self.api.post(RULES, {**base, 'frequency': junk}, format='json')
            self.assertEqual(response.status_code, 400, (junk, response.content))
        for junk in ('x', {'a': 1}, ['1']):
            for url, payload in ((RULES, base), (OBLIGATIONS, {'concept': 'Luz', 'amount': '10.00',
                                                                 'due_date': '2026-02-01'})):
                response = self.api.post(url, {**payload, 'branch': junk}, format='json')
                self.assertEqual(response.status_code, 400, (url, junk, response.content))

    def test_a_huge_period_index_or_page_is_refused_not_a_500(self):
        response = self.api.post(f'{RULES}{self.rule.pk}/generate/', {'period_index': 10 ** 20}, format='json')
        self.assertEqual(response.status_code, 400, response.content)
        response = self.api.get(OBLIGATIONS, {'page': 10 ** 20})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['results'], [])

    def test_a_yearly_index_past_year_9999_is_a_400(self):
        yearly = fx.rule(self.company, self.owner, today=date(2026, 1, 1), frequency='yearly',
                         concept='Seguro anual', due_day=1)
        for index in (9000, 12000):
            response = self.api.post(f'{RULES}{yearly.pk}/generate/', {'period_index': index}, format='json')
            self.assertEqual(response.status_code, 400, (index, response.content))
