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


RULES = '/api/admin/finance/rules/'


class RuleCreateScopeTest(TestCase):
    """
    FIN-RULE-CREATE — «Nuevo pago fijo» from the panel goes through POST rules/: the branch the client names
    selects, the server authorises; a general rule needs company-wide scope; another company's category does
    not exist; and creating needs `finance.manage`.
    """

    def setUp(self):
        self.company = fx.company('fin-rule-new', 'Reglas nuevas')
        self.north = fx.branch(self.company, 'Norte')
        self.south = fx.branch(self.company, 'Sur')
        self.north_only = fx.member(self.company, 'fin_rule_north', fx.ALL_FINANCE, branches=[self.north])
        patcher = mock.patch('store.finance.services.company_today', return_value=date(2026, 1, 1))
        patcher.start()
        self.addCleanup(patcher.stop)

    def body(self, **over):
        payload = {'concept': 'Alquiler Norte', 'payee_name': 'Inmobiliaria', 'amount': '1500.00',
                   'frequency': 'monthly', 'due_day': 5, 'anchor_date': '2026-01-01', 'end_date': None,
                   'planned_method': 'transfer', 'notes': '', 'category': None}
        payload.update(over)
        return payload

    def test_own_branch_201_with_its_first_periods(self):
        response = fx.client_for(self.north_only).post(RULES, self.body(branch=self.north.pk), format='json')
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        self.assertEqual(data['branch'], {'id': self.north.pk, 'name': 'Norte'})
        self.assertEqual(data['planned_method'], 'transfer')
        self.assertTrue(data['calendar_locked'])
        rule = self.company.finance_rules.get(pk=data['id'])
        # Generated at once, up to the 35-day horizon: 5 January and 5 February.
        self.assertEqual(sorted(rule.periods.values_list('due_date', flat=True)),
                         [date(2026, 1, 5), date(2026, 2, 5)])
        from store.models import AdminAuditLog

        log = AdminAuditLog.objects.get(action='finance_rule_created', target_id=str(rule.pk))
        self.assertEqual(log.company_id, self.company.pk)

    def test_other_branch_is_404_and_creates_nothing(self):
        response = fx.client_for(self.north_only).post(RULES, self.body(branch=self.south.pk), format='json')
        self.assertEqual(response.status_code, 404, response.content)
        self.assertFalse(self.company.finance_rules.exists())

    def test_general_rule_needs_company_wide_scope(self):
        response = fx.client_for(self.north_only).post(RULES, self.body(branch=None), format='json')
        self.assertEqual(response.status_code, 403, response.content)
        self.assertFalse(self.company.finance_rules.exists())
        admin = fx.member(self.company, 'fin_rule_admin', fx.ALL_FINANCE)
        response = fx.client_for(admin).post(RULES, self.body(branch=None), format='json')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertIsNone(response.json()['branch'])

    def test_another_companys_category_is_404(self):
        theirs = fx.company('fin-rule-theirs', 'Ajena')
        foreign = services.create_category(theirs, direction='payable', name='Alquiler', actor=None)
        response = fx.client_for(self.north_only).post(
            RULES, self.body(branch=self.north.pk, category=foreign.pk), format='json')
        self.assertEqual(response.status_code, 404, response.content)
        self.assertFalse(self.company.finance_rules.exists())

    def test_without_finance_manage_is_403(self):
        reader = fx.member(self.company, 'fin_rule_reader', ('finance.view',), branches=[self.north])
        response = fx.client_for(reader).post(RULES, self.body(branch=self.north.pk), format='json')
        self.assertEqual(response.status_code, 403, response.content)
        self.assertFalse(self.company.finance_rules.exists())
