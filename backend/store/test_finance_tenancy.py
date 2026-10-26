"""
FIN-B1 — another company's debt does not exist, and a branch scope caps what a
member may see and touch (docs/adr-finance-obligations.md §8).
"""
from datetime import date
from unittest import mock

from django.test import TestCase

from store import finance_test_support as fx
from store.finance import services

D = fx.D
LIST = '/api/admin/finance/obligations/'


class OtherCompanyTest(TestCase):
    def setUp(self):
        self.mine = fx.company('fin-mine', 'Mía')
        self.theirs = fx.company('fin-theirs', 'Ajena')
        self.me = fx.member(self.mine, 'fin_me', fx.ALL_FINANCE)
        stranger = fx.member(self.theirs, 'fin_stranger', fx.ALL_FINANCE)
        self.foreign = fx.rule(self.theirs, stranger, today=date(2026, 1, 1)).periods.first()

    def test_other_company_obligation_is_404_everywhere(self):
        api = fx.client_for(self.me)
        base = f'{LIST}{self.foreign.pk}/'
        self.assertEqual(api.get(base).status_code, 404)
        for suffix, payload in (('payments/', {'amount': '1.00', 'method': 'cash'}),
                                ('void/', {'reason': 'x'}), ('dispute/', {'reason': 'x'}),
                                ('adjustments/', {'kind': 'increase', 'amount': '1.00', 'reason': 'x'})):
            self.assertEqual(api.post(base + suffix, payload, format='json').status_code, 404, suffix)
        self.assertEqual(self.foreign.payments.count(), 0)

    def test_other_company_obligations_are_not_listed_nor_totalled(self):
        with mock.patch('store.finance.services.company_today', return_value=date(2026, 1, 1)):
            api = fx.client_for(self.me)
            self.assertEqual(api.get(LIST).json()['results'], [])
            self.assertEqual(api.get('/api/admin/finance/summary/').json()['open'], {})

    def test_a_category_or_supplier_of_another_company_is_refused(self):
        foreign_category = services.create_category(self.theirs, direction='payable', name='Alquiler',
                                                    actor=None)
        with self.assertRaises(services.FinanceError):
            fx.rule(self.mine, self.me, category=foreign_category)

    def test_no_capability_is_403(self):
        nobody = fx.member(self.mine, 'fin_nobody', ('company.view',))
        self.assertEqual(fx.client_for(nobody).get(LIST).status_code, 403)


class BranchScopeTest(TestCase):
    def setUp(self):
        self.company = fx.company('fin-branches', 'Sucursales')
        self.north = fx.branch(self.company, 'Norte')
        self.south = fx.branch(self.company, 'Sur')
        self.admin = fx.member(self.company, 'fin_admin', fx.ALL_FINANCE)
        self.north_only = fx.member(self.company, 'fin_north', fx.ALL_FINANCE, branches=[self.north])
        self.north_rent = fx.rule(self.company, self.admin, branch=self.north, today=date(2026, 1, 1)).periods.first()
        self.south_rent = fx.rule(self.company, self.admin, branch=self.south, today=date(2026, 1, 1)).periods.first()
        self.general = fx.rule(self.company, self.admin, concept='Contador', today=date(2026, 1, 1)).periods.first()

    def test_unauthorised_branch_is_refused(self):
        api = fx.client_for(self.north_only)
        ids = [row['id'] for row in api.get(LIST).json()['results']]
        self.assertIn(self.north_rent.pk, ids)
        self.assertNotIn(self.south_rent.pk, ids)
        self.assertNotIn(self.general.pk, ids)
        self.assertEqual(api.get(f'{LIST}{self.south_rent.pk}/').status_code, 404)
        self.assertEqual(api.post(f'{LIST}{self.south_rent.pk}/payments/',
                                  {'amount': '1.00', 'method': 'cash'}, format='json').status_code, 404)

    def test_a_general_obligation_needs_company_wide_scope(self):
        api = fx.client_for(self.north_only)
        self.assertEqual(api.get(f'{LIST}{self.general.pk}/').status_code, 404)
        created = api.post(LIST, {'concept': 'Software', 'amount': '10.00', 'due_date': '2026-02-01'},
                           format='json')
        self.assertEqual(created.status_code, 403)
        in_branch = api.post(LIST, {'concept': 'Software', 'amount': '10.00', 'due_date': '2026-02-01',
                                    'branch': self.north.pk}, format='json')
        self.assertEqual(in_branch.status_code, 201, in_branch.content)
        elsewhere = api.post(LIST, {'concept': 'Software', 'amount': '10.00', 'due_date': '2026-02-01',
                                    'branch': self.south.pk}, format='json')
        self.assertEqual(elsewhere.status_code, 404)
        self.assertEqual(fx.client_for(self.admin).get(f'{LIST}{self.general.pk}/').status_code, 200)
