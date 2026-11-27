"""
TRADE-IN VALUATION PRO · Phase 2 — the valuation rules and the calculation (docs/adr-trade-in-valuation.md §3.2, §6).

What these tests hold: the calculation follows the ADR's order to the cent (status rules always apply, one money rule
per factor, deductions on the base never compounded, no double penalty by component then by group, caps, never
negative, NOT ELIGIBLE has no value); rules are the company's, versioned, frozen when proposed, approved by somebody
else and never rewritten; another company's rules do not exist for you; and nothing here buys, pays or receives.
"""
import pathlib
import threading
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from store import finance_test_support as fx
from store import trade_in_rules as rules
from store import trade_in_tariff as tariff
from store import trade_in_valuation as valuation
from store.models import (
    AdminAuditLog,
    Order,
    StockMovement,
    StockUnit,
    TradeInFactor,
    TradeInFactorGroup,
    TradeInRule,
    TradeInRuleSetVersion,
)
from store.test_trade_in_tariff import APPROVE, APPROVE_OWN, MANAGE, VIEW, body

D = Decimal
V, G, F, R = valuation, valuation.Group, valuation.Factor, valuation.Rule
RULES = '/api/admin/trade-in/rules/'


# ---------------------------------------------------------------------------
# The pure calculation, case by case
# ---------------------------------------------------------------------------

GROUPS = {
    'bat': G(key='bat', kind='battery', name='Batería'),
    'cos': G(key='cos', kind='cosmetic', name='Estética'),
    'rep': G(key='rep', kind='repairs', name='Reparaciones', combine='sum'),
}
FACTORS = {
    'battery': F(code='battery', group='bat', label='Salud de batería', input='percent'),
    'screen': F(code='screen', group='cos', label='Pantalla', input='check', component='pantalla'),
    'frame': F(code='frame', group='cos', label='Marco', input='check', component='marco'),
    'screen_replaced': F(code='screen_replaced', group='rep', label='Pantalla reemplazada', input='boolean',
                         component='Pantalla'),
    'camera_replaced': F(code='camera_replaced', group='rep', label='Cámara reemplazada', input='boolean',
                         component='camara'),
    'box': F(code='box', group='cos', label='Caja', input='choice', options=('Completa', 'Sin caja')),
    'only_x': F(code='only_x', group='cos', label='Sólo modelo X', input='check', applies_model_name='Modelo X'),
    'back': F(code='back', group='cos', label='Tapa trasera', input='check', component='trasera'),
}


def calc(answers, rules_, *, base='1000.00', maximum='1200.00', groups=None, device=None):
    return V.calculate(base=D(base), maximum=D(maximum), answers=answers, groups=groups or GROUPS, factors=FACTORS,
                       rules=rules_, device=device or V.Device(brand='Marca', model_name='Modelo Y'))


class CalculationTest(SimpleTestCase):
    def test_no_answers_is_the_base_and_eligible(self):
        result = calc({}, [])
        self.assertEqual(result['totals']['computed_value'], '1000.00')
        self.assertEqual(result['eligibility'], V.ELIGIBLE)

    def test_percentages_are_on_the_base_never_compounded(self):
        result = calc({'battery': '70', 'screen': 'detail'}, [
            R(id=1, factor='battery', effect='percent', when_percent_lt=D('80'), percent=D('10')),
            R(id=2, factor='screen', effect='percent', when_outcome='detail', percent=D('10')),
        ])
        self.assertEqual(result['totals']['deductions'], '200.00')       # 10 % + 10 % of 1000, not 100 + 90
        self.assertEqual(result['totals']['computed_value'], '800.00')
        self.assertEqual(result['eligibility'], V.ELIGIBLE_WITH_DEDUCTIONS)

    def test_status_rules_all_apply_whatever_the_money_rule_priority(self):
        result = calc({'screen': 'not_working'}, [
            R(id=1, factor='screen', effect='fixed', when_outcome='not_working', amount=D('300'), priority=10),
            R(id=2, factor='screen', effect='requires_approval', when_outcome='not_working', priority=0),
            R(id=3, factor='screen', effect='pending_verification', when_outcome='not_working', priority=-5),
        ])
        self.assertEqual(result['flags'], ['pending_verification', 'requires_approval'])
        self.assertEqual(result['eligibility'], V.PENDING_VERIFICATION)    # precedence over approval
        self.assertEqual(result['totals']['computed_value'], '700.00')     # pending keeps a provisional value
        self.assertTrue(result['needs_approval'])

    def test_one_money_rule_per_factor_by_priority_then_larger_then_lower_id(self):
        def chosen(*candidates):
            return calc({'screen': 'detail'}, list(candidates))['lines'][0]['rule_id']

        self.assertEqual(chosen(R(id=1, factor='screen', effect='fixed', when_outcome='detail', amount=D('50')),
                                R(id=2, factor='screen', effect='fixed', when_outcome='detail', amount=D('10'),
                                  priority=1)), 2)
        self.assertEqual(chosen(R(id=1, factor='screen', effect='fixed', when_outcome='detail', amount=D('10')),
                                R(id=2, factor='screen', effect='fixed', when_outcome='detail', amount=D('50'))), 2)
        self.assertEqual(chosen(R(id=5, factor='screen', effect='fixed', when_outcome='detail', amount=D('50')),
                                R(id=4, factor='screen', effect='percent', when_outcome='detail', percent=D('5'))), 4)

    def test_the_same_component_counts_once_across_groups(self):
        result = calc({'screen': 'not_working', 'screen_replaced': True}, [
            R(id=1, factor='screen', effect='percent', when_outcome='not_working', percent=D('15')),       # 150
            R(id=2, factor='screen_replaced', effect='repair_cost', when_outcome='true', amount=D('120')),
        ])
        self.assertEqual(result['totals']['deductions'], '150.00')
        lines = {line['factor']: line for line in result['lines']}
        self.assertEqual(lines['screen_replaced']['suppressed_by'], 'screen')
        self.assertIsNone(lines['screen']['suppressed_by'])

    def test_max_keeps_the_largest_of_a_group_and_sum_adds(self):
        result = calc({'screen': 'detail', 'frame': 'detail', 'camera_replaced': 'true',
                       'screen_replaced': 'false'}, [
            R(id=1, factor='screen', effect='fixed', when_outcome='detail', amount=D('80')),
            R(id=2, factor='frame', effect='fixed', when_outcome='detail', amount=D('30')),
            R(id=3, factor='camera_replaced', effect='fixed', when_outcome='true', amount=D('40')),
        ])
        self.assertEqual(result['groups'], {'cos': '80.00', 'rep': '40.00'})
        both = calc({'camera_replaced': 'true', 'screen_replaced': 'true'}, [
            R(id=3, factor='camera_replaced', effect='fixed', when_outcome='true', amount=D('40')),
            R(id=4, factor='screen_replaced', effect='fixed', when_outcome='true', amount=D('60')),
        ])
        self.assertEqual(both['groups'], {'rep': '100.00'})                # `sum`

    def test_a_group_cap_and_the_base_cap(self):
        capped = dict(GROUPS, rep=G(key='rep', kind='repairs', name='Rep', combine='sum', cap_percent=D('5')))
        result = calc({'camera_replaced': 'true', 'screen_replaced': 'true'}, [
            R(id=3, factor='camera_replaced', effect='fixed', when_outcome='true', amount=D('40')),
            R(id=4, factor='screen_replaced', effect='fixed', when_outcome='true', amount=D('60')),
        ], groups=capped)
        self.assertEqual(result['groups'], {'rep': '50.00'})
        broken = calc({'screen': 'not_working', 'battery': '10'}, [
            R(id=1, factor='screen', effect='fixed', when_outcome='not_working', amount=D('900')),
            R(id=2, factor='battery', effect='fixed', when_percent_lt=D('50'), amount=D('900')),
        ])
        self.assertEqual(broken['totals']['deductions'], '1000.00')
        self.assertEqual(broken['totals']['computed_value'], '0.00')          # never negative

    def test_a_fault_is_never_dropped_by_a_line_its_own_group_discarded(self):
        """Review of #462 (owner's decision): combine within each group first, then one line per component."""
        result = calc({'screen': 'not_working', 'back': 'not_working', 'screen_replaced': 'true'}, [
            R(id=1, factor='screen', effect='fixed', when_outcome='not_working', amount=D('150')),    # cos, pantalla
            R(id=2, factor='back', effect='fixed', when_outcome='not_working', amount=D('200')),      # cos, trasera
            R(id=3, factor='screen_replaced', effect='fixed', when_outcome='true', amount=D('120')),  # rep, pantalla
        ])
        # «cos» keeps the back (200); the screen line is gone with its group, so the replaced screen (120) counts.
        self.assertEqual(result['groups'], {'cos': '200.00', 'rep': '120.00'})
        self.assertEqual(result['totals']['deductions'], '320.00')
        lines = {line['factor']: line for line in result['lines']}
        self.assertEqual(lines['screen']['suppressed_by'], 'back')
        self.assertIsNone(lines['screen_replaced']['suppressed_by'])

    def test_the_greedy_selection_counts_a_replaced_part_and_another_fault(self):
        """Re-review of #462: 150 + 100 in a `max` group and a replaced part of 200 on the 150's component → 300."""
        result = calc({'screen': 'not_working', 'frame': 'not_working', 'screen_replaced': 'true'}, [
            R(id=1, factor='screen', effect='fixed', when_outcome='not_working', amount=D('150')),    # cos, pantalla
            R(id=2, factor='frame', effect='fixed', when_outcome='not_working', amount=D('100')),     # cos, marco
            R(id=3, factor='screen_replaced', effect='fixed', when_outcome='true', amount=D('200')),  # rep, pantalla
        ])
        self.assertEqual(result['totals']['deductions'], '300.00')
        self.assertEqual(result['groups'], {'cos': '100.00', 'rep': '200.00'})
        lines = {line['factor']: line for line in result['lines']}
        self.assertEqual(lines['screen']['suppressed_by'], 'screen_replaced')     # its component was taken

    def test_lines_are_ranked_by_what_their_group_cap_still_lets_them_deduct(self):
        """Third review of #462: a replaced part of 120 in a group capped at 50 loses to a broken screen of 100."""
        capped = dict(GROUPS, rep=G(key='rep', kind='repairs', name='Rep', combine='sum', cap_percent=D('5')))
        result = calc({'screen': 'not_working', 'screen_replaced': 'true'}, [
            R(id=1, factor='screen', effect='fixed', when_outcome='not_working', amount=D('100')),    # cos, pantalla
            R(id=2, factor='screen_replaced', effect='fixed', when_outcome='true', amount=D('120')),  # rep (≤ 50)
        ], groups=capped)
        self.assertEqual(result['totals']['deductions'], '100.00')
        lines = {line['factor']: line for line in result['lines']}
        self.assertEqual(lines['screen_replaced']['suppressed_by'], 'screen')

    def test_ties_go_to_the_higher_priority_then_the_lower_rule_id(self):
        def winner(*candidates):
            result = calc({'screen': 'detail', 'frame': 'detail'}, list(candidates))
            return [line['factor'] for line in result['lines'] if not line['suppressed_by']]

        self.assertEqual(winner(R(id=1, factor='screen', effect='fixed', when_outcome='detail', amount=D('50')),
                                R(id=2, factor='frame', effect='fixed', when_outcome='detail', amount=D('50'),
                                  priority=3)), ['frame'])
        self.assertEqual(winner(R(id=9, factor='screen', effect='fixed', when_outcome='detail', amount=D('50')),
                                R(id=4, factor='frame', effect='fixed', when_outcome='detail', amount=D('50'))),
                         ['frame'])

    def test_half_up_at_the_cent_boundary(self):
        rule = R(id=1, factor='screen', effect='percent', when_outcome='detail', percent=D('0.5'))
        self.assertEqual(calc({'screen': 'detail'}, [rule], base='1.00')['lines'][0]['amount'], '0.01')   # 0.005
        self.assertEqual(calc({'screen': 'detail'}, [rule], base='0.98')['lines'][0]['amount'], '0.00')   # 0.0049

    def test_not_eligible_has_no_value(self):
        result = calc({'screen': 'not_working'}, [
            R(id=1, factor='screen', effect='not_eligible', when_outcome='not_working'),
            R(id=2, factor='screen', effect='fixed', when_outcome='not_working', amount=D('100')),
        ])
        self.assertEqual(result['eligibility'], V.NOT_ELIGIBLE)
        self.assertIsNone(result['totals']['computed_value'])
        self.assertEqual(result['eligibility_label'], 'NO ELEGIBLE')

    def test_ranges_choices_filters_and_rounding(self):
        rule = R(id=1, factor='battery', effect='fixed', when_percent_gte=D('70'), when_percent_lt=D('80'),
                 amount=D('50'))
        self.assertEqual(calc({'battery': '80'}, [rule])['lines'], [])     # [gte, lt)
        self.assertEqual(len(calc({'battery': '70'}, [rule])['lines']), 1)
        box = calc({'box': 'Sin caja'}, [R(id=1, factor='box', effect='percent', when_outcome='Sin caja',
                                             percent=D('3.333'))], base='999.99')
        self.assertEqual(box['lines'][0]['amount'], '33.33')                 # 33.3297 → 33.33
        filtered = calc({'only_x': 'not_working'},
                        [R(id=1, factor='only_x', effect='fixed', when_outcome='not_working', amount=D('9'))])
        self.assertEqual(filtered['lines'], [])                              # not asked of Modelo Y
        with self.assertRaises(V.ValuationError):
            calc({'unknown': 'works'}, [])
        for code, raw in (('battery', '120'), ('screen', 'broken'), ('box', 'Otra'), ('screen_replaced', 'tal vez')):
            with self.subTest(code=code), self.assertRaises(V.ValuationError):
                calc({code: raw}, [])


# ---------------------------------------------------------------------------
# Rules as company data: versions, approval, freezing, isolation
# ---------------------------------------------------------------------------

class RulesBase(TestCase):
    def setUp(self):
        self.company = fx.company('tir-a', 'Reglas A')
        self.other = fx.company('tir-b', 'Reglas B')
        self.editor = fx.member(self.company, 'tir_editor', [VIEW, MANAGE])
        self.approver = fx.member(self.company, 'tir_jefe', [VIEW, APPROVE])
        self.owner = fx.member(self.company, 'tir_dueno', [VIEW, MANAGE, APPROVE, APPROVE_OWN])
        self.reader = fx.member(self.company, 'tir_lector', [VIEW])
        self.stranger = fx.member(self.other, 'tir_otro', [VIEW, MANAGE, APPROVE, APPROVE_OWN])
        self.today = tariff.company_today(self.company)

    def api(self, user):
        return fx.client_for(user)

    def post(self, path, data, user=None, expect=201):
        res = self.api(user or self.editor).post(f'{RULES}{path}', data, format='json')
        self.assertEqual(res.status_code, expect, res.content)
        return res.json()

    def catalogue(self):
        cos = self.post('groups/', {'kind': 'cosmetic', 'name': 'Estética'})
        rep = self.post('groups/', {'kind': 'repairs', 'name': 'Reparaciones', 'combine': 'sum'})
        screen = self.post('factors/', {'group': cos['id'], 'label': 'Pantalla', 'code': 'pantalla',
                                        'input': 'check', 'component': 'pantalla'})
        replaced = self.post('factors/', {'group': rep['id'], 'label': 'Pantalla reemplazada', 'code': 'pantalla-cambiada',
                                          'input': 'boolean', 'component': 'pantalla'})
        return cos, rep, screen, replaced

    def approved_set(self):
        cos, rep, screen, replaced = self.catalogue()
        draft = self.post('versions/', {})
        self.post(f'versions/{draft["id"]}/rules/', {'factor': screen['id'], 'when_outcome': 'not_working',
                                                     'effect': 'percent', 'percent': '20'})
        self.post(f'versions/{draft["id"]}/rules/', {'factor': replaced['id'], 'when_outcome': 'true',
                                                     'effect': 'repair_cost', 'amount': '150.00'})
        self.post(f'versions/{draft["id"]}/propose/', {'reason': 'Primeras reglas'}, expect=200)
        self.post(f'versions/{draft["id"]}/approve/', {}, user=self.approver, expect=200)
        return draft['id'], cos, screen, replaced

    def entry(self):
        entry, version = tariff.create_entry(actor=self.editor, company=self.company, data=body())
        tariff.approve_version(actor=self.approver, company=self.company, version=version)
        return entry


class RulesFlowTest(RulesBase):
    def test_draft_propose_approve_and_preview(self):
        version_id, _cos, _screen, _replaced = self.approved_set()
        entry = self.entry()
        result = self.post('preview/', {'tariff_entry': entry.pk,
                                        'answers': {'pantalla': 'not_working', 'pantalla-cambiada': True}},
                           user=self.reader, expect=200)
        self.assertEqual(result['totals']['base'], '1200.00')
        self.assertEqual(result['totals']['deductions'], '240.00')         # 20 % beats 150 on the same component
        self.assertEqual(result['totals']['computed_value'], '960.00')
        self.assertEqual(result['rules']['version'], version_id)
        self.assertEqual(result['eligibility'], 'eligible_with_deductions')
        self.assertEqual(rules.effective_rule_set(self.company).pk, version_id)

    def test_every_set_waits_and_the_proposer_needs_approve_own_and_a_reason(self):
        _cos, _rep, screen, _ = self.catalogue()
        draft = self.post('versions/', {}, user=self.owner)
        self.post(f'versions/{draft["id"]}/rules/', {'factor': screen['id'], 'when_outcome': 'detail',
                                                     'effect': 'fixed', 'amount': '30'}, user=self.owner)
        proposed = self.post(f'versions/{draft["id"]}/propose/', {}, user=self.owner, expect=200)
        self.assertEqual(proposed['status'], 'pending')
        self.assertEqual(set(proposed['snapshot']['factors']), {'pantalla', 'pantalla-cambiada'})
        res = self.post(f'versions/{draft["id"]}/approve/', {}, user=self.owner, expect=400)
        self.assertEqual(res['code'], 'reason_required')
        done = self.post(f'versions/{draft["id"]}/approve/', {'note': 'Equipo pequeño'}, user=self.owner, expect=200)
        self.assertEqual(done['approval_kind'], 'self')
        audit = AdminAuditLog.objects.get(action='trade_in_rule_set_approved', company=self.company)
        self.assertTrue(audit.metadata['self_approval'])

    def test_a_proposer_without_approve_own_cannot_approve(self):
        both = fx.member(self.company, 'tir_ambos', [VIEW, MANAGE, APPROVE])
        _cos, _rep, screen, _ = self.catalogue()
        draft = self.post('versions/', {}, user=both)
        self.post(f'versions/{draft["id"]}/rules/', {'factor': screen['id'], 'when_outcome': 'detail',
                                                     'effect': 'none'}, user=both)
        self.post(f'versions/{draft["id"]}/propose/', {}, user=both, expect=200)
        self.assertEqual(self.post(f'versions/{draft["id"]}/approve/', {}, user=both, expect=403)['code'],
                         'own_change')

    def test_an_approved_set_is_frozen_even_if_the_catalogue_changes(self):
        version_id, cos, screen, replaced = self.approved_set()
        entry = self.entry()
        before = self.post('preview/', {'tariff_entry': entry.pk, 'answers': {'pantalla': 'not_working',
                                                                            'pantalla-cambiada': 'true'}},
                           user=self.reader, expect=200)
        # The company renames the component and caps the group afterwards: the approved set computes the same.
        self.api(self.editor).patch(f'{RULES}factors/{replaced["id"]}/', {'component': 'otra cosa'}, format='json')
        self.api(self.editor).patch(f'{RULES}groups/{cos["id"]}/', {'group_cap_percent': '1'}, format='json')
        after = self.post('preview/', {'tariff_entry': entry.pk, 'answers': {'pantalla': 'not_working',
                                                                           'pantalla-cambiada': 'true'}},
                          user=self.reader, expect=200)
        self.assertEqual(before['totals'], after['totals'])
        version = TradeInRuleSetVersion.objects.get(pk=version_id)
        rule = version.rules.first()
        rule.priority = 99
        with self.assertRaises(ValidationError):
            rule.save()
        with self.assertRaises(ValidationError):
            rule.delete()
        version.reason = 'otra'
        with self.assertRaises(ValidationError):
            version.save()
        with self.assertRaises(ValidationError):
            version.delete()

    def test_a_new_draft_copies_the_latest_approved_rules_and_one_draft_at_a_time(self):
        version_id, _cos, _screen, _replaced = self.approved_set()
        draft = self.post('versions/', {})
        self.assertEqual(draft['source_version'], version_id)
        self.assertEqual(len(draft['rules']), 2)
        self.assertEqual(self.post('versions/', {}, expect=409)['code'], 'draft_exists')
        empty = TradeInRuleSetVersion.objects.get(pk=draft['id'])
        self.post(f'versions/{empty.pk}/withdraw/', {'note': 'No'}, expect=200)
        self.assertEqual(len(self.post('versions/', {'empty': True})['rules']), 0)

    def test_the_newest_started_set_is_in_force_and_an_ended_one_never_hands_back(self):
        first, _cos, screen, _replaced = self.approved_set()
        draft = self.post('versions/', {})
        end = self.today + timedelta(days=2)
        self.post(f'versions/{draft["id"]}/propose/', {'valid_until': end.isoformat()}, expect=200)
        self.post(f'versions/{draft["id"]}/approve/', {}, user=self.approver, expect=200)
        self.assertEqual(rules.effective_rule_set(self.company, on=end).pk, draft['id'])
        self.assertIsNone(rules.effective_rule_set(self.company, on=end + timedelta(days=1)))
        self.assertNotEqual(first, draft['id'])

    def test_proposals_are_not_retroactive_and_a_late_approval_starts_today(self):
        _cos, _rep, screen, _ = self.catalogue()
        draft = self.post('versions/', {})
        self.post(f'versions/{draft["id"]}/rules/', {'factor': screen['id'], 'when_outcome': 'detail',
                                                     'effect': 'none'})
        res = self.post(f'versions/{draft["id"]}/propose/',
                        {'valid_from': (self.today - timedelta(days=1)).isoformat()}, expect=400)
        self.assertEqual(res['code'], 'retroactive')
        self.post(f'versions/{draft["id"]}/propose/', {}, expect=200)
        later = self.today + timedelta(days=3)
        with mock.patch('store.trade_in_tariff.company_today', return_value=later):
            done = self.post(f'versions/{draft["id"]}/approve/', {}, user=self.approver, expect=200)
        self.assertEqual(done['valid_from'], later.isoformat())

    def test_rules_check_their_condition_and_effect(self):
        _cos, _rep, screen, replaced = self.catalogue()
        battery_group = self.post('groups/', {'kind': 'battery', 'name': 'Batería'})
        battery = self.post('factors/', {'group': battery_group['id'], 'label': 'Salud', 'code': 'salud',
                                         'input': 'percent'})
        draft = self.post('versions/', {})
        path = f'versions/{draft["id"]}/rules/'
        for data, field in (
            ({'factor': screen['id'], 'when_outcome': 'roto', 'effect': 'none'}, 'when_outcome'),
            ({'factor': screen['id'], 'when_outcome': 'detail', 'effect': 'fixed'}, 'amount'),
            ({'factor': screen['id'], 'when_outcome': 'detail', 'effect': 'percent', 'percent': '120'}, 'percent'),
            ({'factor': battery['id'], 'when_outcome': 'works', 'effect': 'none'}, 'when_percent_gte'),
            ({'factor': battery['id'], 'when_percent_gte': '80', 'when_percent_lt': '70', 'effect': 'none'},
             'when_percent_lt'),
            ({'factor': replaced['id'], 'when_outcome': 'si', 'effect': 'none'}, 'when_outcome'),
        ):
            with self.subTest(data=data):
                self.assertEqual(self.post(path, data, expect=400)['field'], field)
        self.assertFalse(TradeInRule.objects.exists())
        self.assertEqual(self.post(f'versions/{draft["id"]}/propose/', {}, expect=409)['code'], 'empty')

    def test_reject_needs_a_reason_and_withdraw_is_for_the_author_or_an_approver(self):
        _cos, _rep, screen, _ = self.catalogue()
        draft = self.post('versions/', {})
        self.post(f'versions/{draft["id"]}/rules/', {'factor': screen['id'], 'when_outcome': 'detail',
                                                     'effect': 'none'})
        self.post(f'versions/{draft["id"]}/propose/', {}, expect=200)
        self.post(f'versions/{draft["id"]}/reject/', {}, user=self.approver, expect=400)
        other_editor = fx.member(self.company, 'tir_editor2', [VIEW, MANAGE])
        self.post(f'versions/{draft["id"]}/withdraw/', {}, user=other_editor, expect=403)
        done = self.post(f'versions/{draft["id"]}/reject/', {'note': 'Muy duro'}, user=self.approver, expect=200)
        self.assertEqual(done['status'], 'rejected')
        self.assertEqual(AdminAuditLog.objects.get(action='trade_in_rule_set_rejected').metadata['note'], 'Muy duro')


class RulesModelGuardsTest(RulesBase):
    def test_the_database_holds_effect_fields_condition_and_one_draft(self):
        cos = TradeInFactorGroup.objects.create(company=self.company, kind='cosmetic', name='E')
        factor = TradeInFactor.objects.create(company=self.company, group=cos, code='p', label='P', input='check')
        draft = TradeInRuleSetVersion.objects.create(company=self.company, number=1)
        for fields in ({'effect': 'fixed', 'when_outcome': 'detail'},
                       {'effect': 'percent', 'when_outcome': 'detail', 'percent': D('101')},
                       {'effect': 'none', 'when_outcome': 'detail', 'amount': D('1')},
                       {'effect': 'none'}):
            with self.subTest(fields=fields), self.assertRaises(IntegrityError), transaction.atomic():
                TradeInRule.objects.create(company=self.company, version=draft, factor=factor, **fields)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TradeInRuleSetVersion.objects.create(company=self.company, number=2)
        TradeInRuleSetVersion.objects.create(company=self.other, number=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TradeInFactorGroup.objects.create(company=self.company, kind='cosmetic', name='Caps',
                                              group_cap_percent=D('150'))

    def test_a_rule_stays_inside_its_company(self):
        cos = TradeInFactorGroup.objects.create(company=self.other, kind='cosmetic', name='E')
        foreign = TradeInFactor.objects.create(company=self.other, group=cos, code='p', label='P', input='check')
        draft = TradeInRuleSetVersion.objects.create(company=self.company, number=1)
        with self.assertRaises(ValidationError):
            TradeInRule.objects.create(company=self.company, version=draft, factor=foreign, effect='none',
                                       when_outcome='works')


class RulesPermissionsAndIsolationTest(RulesBase):
    def test_each_door_asks_its_capability(self):
        version_id, cos, screen, _ = self.approved_set()
        draft = self.post('versions/', {})
        nobody = fx.member(self.company, 'tir_caja', ['sales.pos.use'])
        cases = [
            (nobody, 'get', 'groups/', None, 403), (nobody, 'get', 'versions/', None, 403),
            (nobody, 'post', 'preview/', {}, 403),
            (self.reader, 'post', 'groups/', {'kind': 'custom', 'name': 'X'}, 403),
            (self.reader, 'post', 'factors/', {}, 403),
            (self.reader, 'post', 'versions/', {}, 403),
            (self.reader, 'post', f'versions/{draft["id"]}/rules/', {}, 403),
            (self.approver, 'post', f'versions/{draft["id"]}/propose/', {}, 403),
            (self.editor, 'post', f'versions/{version_id}/approve/', {}, 403),
            (self.editor, 'post', f'versions/{version_id}/reject/', {'note': 'x'}, 403),
        ]
        for user, method, path, data, expected in cases:
            with self.subTest(user=user.username, path=path):
                res = getattr(self.api(user), method)(f'{RULES}{path}', data, format='json')
                self.assertEqual(res.status_code, expected, res.content)

    def test_another_companys_rules_do_not_exist_for_you(self):
        version_id, cos, screen, _ = self.approved_set()
        draft = self.post('versions/', {})
        rule_id = draft['rules'][0]['id']
        theirs = self.api(self.stranger)
        self.assertEqual(theirs.get(f'{RULES}groups/').json()['results'], [])
        for method, path, data in (
            ('get', f'versions/{version_id}/', None),
            ('patch', f'groups/{cos["id"]}/', {'name': 'x'}),
            ('patch', f'factors/{screen["id"]}/', {'label': 'x'}),
            ('post', f'versions/{draft["id"]}/rules/', {'factor': screen['id']}),
            ('patch', f'rules/{rule_id}/', {'priority': 3}),
            ('delete', f'rules/{rule_id}/', None),
            ('post', f'versions/{draft["id"]}/propose/', {}),
            ('post', f'versions/{version_id}/approve/', {'note': 'x'}),
            ('post', f'versions/{draft["id"]}/withdraw/', {}),
        ):
            with self.subTest(method=method, path=path):
                res = getattr(theirs, method)(f'{RULES}{path}', data, format='json')
                self.assertEqual(res.status_code, 404, res.content)
        # Their own draft cannot name our factor either.
        own = theirs.post(f'{RULES}versions/', {}, format='json').json()
        res = theirs.post(f'{RULES}versions/{own["id"]}/rules/', {'factor': screen['id'], 'when_outcome': 'detail',
                                                                  'effect': 'none'}, format='json')
        self.assertEqual(res.status_code, 404)
        entry = self.entry()
        res = theirs.post(f'{RULES}preview/', {'tariff_entry': entry.pk, 'answers': {}}, format='json')
        self.assertEqual(res.status_code, 404)


class RulesGuardsTest(RulesBase):
    def test_no_order_stock_or_unit_is_written_and_writes_are_audited(self):
        counts = (Order.objects.count(), StockMovement.objects.count(), StockUnit.objects.count())
        self.approved_set()
        self.assertEqual(counts, (Order.objects.count(), StockMovement.objects.count(), StockUnit.objects.count()))
        self.assertFalse(AdminAuditLog.objects.filter(action__startswith='trade_in_', company__isnull=True).exists())
        self.assertTrue(AdminAuditLog.objects.filter(action='trade_in_rule_set_proposed', company=self.company).exists())

    def test_the_engine_names_no_maker_model_or_shop(self):
        root = pathlib.Path(rules.__file__).parent
        for name in ('trade_in_rules.py', 'trade_in_rules_views.py', 'trade_in_valuation.py'):
            text = (root / name).read_text(encoding='utf-8').lower()
            for word in ('iphone', 'apple', 'samsung', 'xiaomi', 'motorola', 'black dog', 'blackdog', 'icloud'):
                with self.subTest(file=name, word=word):
                    self.assertNotIn(word, text)


class RulesReviewFindingsTest(RulesBase):
    """Review of #462: conditions re-checked, every writer is an author, bounded integers, inactive questions."""

    def draft_with_rule(self, user=None, **rule):
        _cos, _rep, screen, replaced = self.catalogue()
        draft = self.post('versions/', {}, user=user)
        created = self.post(f'versions/{draft["id"]}/rules/', {'factor': screen['id'], 'when_outcome': 'detail',
                                                               'effect': 'fixed', 'amount': '30', **rule}, user=user)
        return draft, created, screen, replaced

    def test_changing_a_rules_question_rechecks_its_condition(self):
        draft, rule, screen, replaced = self.draft_with_rule()
        res = self.api(self.editor).patch(f'{RULES}rules/{rule["id"]}/', {'factor': replaced['id']}, format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['field'], 'when_outcome')
        res = self.api(self.editor).patch(f'{RULES}rules/{rule["id"]}/', {'priority': 7}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((res.json()['priority'], res.json()['when_outcome'], res.json()['amount']),
                         (7, 'detail', '30.00'))

    def test_removing_an_option_a_draft_rule_uses_is_refused(self):
        cos = self.post('groups/', {'kind': 'accessories', 'name': 'Caja'})
        box = self.post('factors/', {'group': cos['id'], 'label': 'Caja', 'code': 'caja', 'input': 'choice',
                                     'options': ['Completa', 'Sin caja']})
        draft = self.post('versions/', {})
        rule = self.post(f'versions/{draft["id"]}/rules/', {'factor': box['id'], 'when_outcome': 'Sin caja',
                                                            'effect': 'fixed', 'amount': '20'})
        res = self.api(self.editor).patch(f'{RULES}factors/{box["id"]}/', {'options': ['Completa']}, format='json')
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual((res.json()['code'], res.json()['rules']), ('options_in_use', [rule['id']]))
        # Changed behind the services' back: the proposal still refuses, naming the rule.
        TradeInFactor.objects.filter(pk=box['id']).update(options=['Completa'])
        res = self.post(f'versions/{draft["id"]}/propose/', {}, expect=409)
        self.assertEqual((res['code'], res['rules']), ('rules_invalid', [rule['id']]))

    def test_anyone_who_wrote_the_draft_needs_approve_own(self):
        writer_approver = fx.member(self.company, 'tir_escribe_aprueba', [VIEW, MANAGE, APPROVE])
        draft, _rule, screen, _ = self.draft_with_rule()                          # opened by the editor
        self.post(f'versions/{draft["id"]}/rules/', {'factor': screen['id'], 'when_outcome': 'not_working',
                                                     'effect': 'percent', 'percent': '25'}, user=writer_approver)
        self.post(f'versions/{draft["id"]}/propose/', {}, expect=200)              # proposed by the editor
        res = self.post(f'versions/{draft["id"]}/approve/', {}, user=writer_approver, expect=403)
        self.assertEqual(res['code'], 'own_change')
        done = self.post(f'versions/{draft["id"]}/approve/', {}, user=self.approver, expect=200)
        self.assertEqual(done['approval_kind'], 'approver')

    def test_editing_groups_or_questions_during_a_draft_makes_you_an_author(self):
        """Re-review of #462: an approver who changes a cap or a component while a draft is open wrote that draft."""
        editor_approver = fx.member(self.company, 'tir_cambia_tope', [VIEW, MANAGE, APPROVE])
        draft, _rule, screen, _ = self.draft_with_rule()
        group_id = TradeInFactor.objects.get(pk=screen['id']).group_id
        res = self.api(editor_approver).patch(f'{RULES}groups/{group_id}/', {'group_cap_percent': '5'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.post(f'versions/{draft["id"]}/propose/', {}, expect=200)                      # proposed by the editor
        res = self.post(f'versions/{draft["id"]}/approve/', {}, user=editor_approver, expect=403)
        self.assertEqual(res['code'], 'own_change')
        # The same for a question's component, and the frozen pending set is not touched by later edits.
        other = fx.member(self.company, 'tir_cambia_componente', [VIEW, MANAGE, APPROVE])
        self.api(other).patch(f'{RULES}factors/{screen["id"]}/', {'component': 'vidrio'}, format='json')
        pending = TradeInRuleSetVersion.objects.get(pk=draft['id'])
        self.assertNotIn(other.pk, pending.contributor_ids)
        self.assertEqual(self.post(f'versions/{draft["id"]}/approve/', {}, user=other, expect=200)['status'],
                         'approved')

    def test_catalogue_edits_with_no_draft_open_still_make_you_an_author(self):
        """Third review of #462: edits before the draft is opened change what it computes as much as edits during it."""
        version_id, cos, screen, _ = self.approved_set()
        editor_approver = fx.member(self.company, 'tir_edita_sin_borrador', [VIEW, MANAGE, APPROVE])
        self.assertFalse(TradeInRuleSetVersion.objects.filter(status='draft').exists())
        res = self.api(editor_approver).patch(f'{RULES}groups/{cos["id"]}/', {'group_cap_percent': '3'},
                                              format='json')
        self.assertEqual(res.status_code, 200, res.content)
        draft = self.post('versions/', {})                                       # the editor opens…
        self.post(f'versions/{draft["id"]}/propose/', {}, expect=200)            # …and proposes
        res = self.post(f'versions/{draft["id"]}/approve/', {}, user=editor_approver, expect=403)
        self.assertEqual(res['code'], 'own_change')
        # Edits before the previous proposal do not count: the first approver approves this one freely.
        self.assertEqual(self.post(f'versions/{draft["id"]}/approve/', {}, user=self.approver, expect=200)['status'],
                         'approved')
        self.assertNotEqual(version_id, draft['id'])

    def test_a_partial_range_edit_keeps_the_other_bound(self):
        battery_group = self.post('groups/', {'kind': 'battery', 'name': 'Batería'})
        battery = self.post('factors/', {'group': battery_group['id'], 'label': 'Salud', 'code': 'salud',
                                         'input': 'percent'})
        draft = self.post('versions/', {})
        rule = self.post(f'versions/{draft["id"]}/rules/', {'factor': battery['id'], 'when_percent_gte': '70',
                                                            'when_percent_lt': '80', 'effect': 'none'})
        res = self.api(self.editor).patch(f'{RULES}rules/{rule["id"]}/', {'when_percent_lt': '90'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((res.json()['when_percent_gte'], res.json()['when_percent_lt']), ('70.00', '90.00'))
        res = self.api(self.editor).patch(f'{RULES}rules/{rule["id"]}/', {'when_percent_lt': '60'}, format='json')
        self.assertEqual((res.status_code, res.json()['field']), (400, 'when_percent_lt'))

    def test_integers_out_of_range_are_a_400(self):
        draft, rule, screen, _ = self.draft_with_rule()
        for path, data, field in (
            (f'versions/{draft["id"]}/rules/', {'factor': screen['id'], 'when_outcome': 'works', 'effect': 'none',
                                                'priority': 2 ** 31}, 'priority'),
            ('groups/', {'kind': 'custom', 'name': 'Grande', 'sort_order': 2 ** 31}, 'sort_order'),
            ('groups/', {'kind': 'custom', 'name': 'Negativo', 'sort_order': -1}, 'sort_order'),
        ):
            with self.subTest(field=field, data=data):
                self.assertEqual(self.post(path, data, expect=400)['field'], field)
        res = self.api(self.editor).patch(f'{RULES}rules/{rule["id"]}/', {'priority': -(2 ** 31)}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_inactive_questions_block_a_proposal_and_are_not_copied(self):
        version_id, _cos, screen, replaced = self.approved_set()
        snapshot = TradeInRuleSetVersion.objects.get(pk=version_id).snapshot
        self.assertEqual({k: (f['is_active'], f['sort_order']) for k, f in snapshot['factors'].items()},
                         {'pantalla': (True, 0), 'pantalla-cambiada': (True, 0)})
        self.assertTrue(all(g['is_active'] for g in snapshot['groups'].values()))
        self.api(self.editor).patch(f'{RULES}factors/{replaced["id"]}/', {'is_active': False}, format='json')
        draft = self.post('versions/', {})
        self.assertEqual([d['factor'] for d in draft['dropped_rules']], ['pantalla-cambiada'])
        self.assertEqual(len(draft['rules']), 1)
        self.api(self.editor).patch(f'{RULES}groups/{_cos["id"]}/', {'is_active': False}, format='json')
        res = self.post(f'versions/{draft["id"]}/propose/', {}, expect=409)
        self.assertEqual((res['code'], res['rules']), ('inactive_in_rules', [draft['rules'][0]['id']]))

    def test_the_model_keeps_codes_kinds_and_transitions(self):
        _cos, _rep, screen, _ = self.catalogue()
        factor = TradeInFactor.objects.get(pk=screen['id'])
        factor.code = 'otra'
        with self.assertRaises(ValidationError):
            factor.save()
        factor.refresh_from_db()
        factor.input = 'boolean'
        with self.assertRaises(ValidationError):
            factor.save()
        res = self.api(self.editor).patch(f'{RULES}factors/{screen["id"]}/', {'code': 'x'}, format='json')
        self.assertEqual(res.json()['code'], 'identity_fixed')
        draft = TradeInRuleSetVersion.objects.create(company=self.company, number=1)
        draft.status, draft.valid_from = TradeInRuleSetVersion.STATUS_APPROVED, self.today
        with self.assertRaises(ValidationError):
            draft.save()

    def test_preview_writes_nothing_and_names_what_is_missing(self):
        entry = self.entry()
        res = self.post('preview/', {'tariff_entry': entry.pk, 'answers': {}}, user=self.reader, expect=409)
        self.assertEqual(res['code'], 'rules_missing')
        draft, _rule, _screen, _ = self.draft_with_rule()
        with CaptureQueriesContext(connection) as queries:
            result = rules.preview(actor=self.reader, company=self.company,
                                   data={'tariff_entry': entry.pk, 'answers': {'pantalla': 'detail'},
                                         'rule_set_version': draft['id']})
        self.assertFalse([q['sql'] for q in queries.captured_queries
                          if q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))])
        self.assertEqual((result['rules']['status'], result['totals']['deductions']), ('draft', '30.00'))
        self.post(f'versions/{draft["id"]}/propose/', {}, expect=200)
        pending = self.post('preview/', {'tariff_entry': entry.pk, 'answers': {'pantalla': 'detail'},
                                         'rule_set_version': draft['id']}, user=self.reader, expect=200)
        self.assertEqual(pending['rules']['status'], 'pending')
        self.api(self.editor).post(f'/api/admin/trade-in/tariff/{entry.pk}/deactivate/', {'reason': 'x'},
                                   format='json')
        res = self.post('preview/', {'tariff_entry': entry.pk, 'answers': {}, 'rule_set_version': draft['id']},
                        user=self.reader, expect=409)
        self.assertEqual(res['code'], 'tariff_missing')

    def test_one_pending_set_at_a_time(self):
        draft, _rule, screen, _ = self.draft_with_rule()
        self.post(f'versions/{draft["id"]}/propose/', {}, expect=200)
        second = self.post('versions/', {'empty': True})
        self.post(f'versions/{second["id"]}/rules/', {'factor': screen['id'], 'when_outcome': 'works',
                                                      'effect': 'none'})
        self.assertEqual(self.post(f'versions/{second["id"]}/propose/', {}, expect=409)['code'], 'pending_exists')


class TwoProposalsOfOneDraftTest(TransactionTestCase):
    """Two people propose the same draft at the same instant: the row lock lets one through; the other is told."""

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Only PostgreSQL has the row locks this test is about.')
        self.company = fx.company('tir-race', 'Carrera reglas')
        self.one = fx.member(self.company, 'tir_race_1', [VIEW, MANAGE])
        self.two = fx.member(self.company, 'tir_race_2', [VIEW, MANAGE])
        group = TradeInFactorGroup.objects.create(company=self.company, kind='cosmetic', name='E')
        factor = TradeInFactor.objects.create(company=self.company, group=group, code='p', label='P', input='check')
        self.draft = rules.create_draft(actor=self.one, company=self.company)
        rules.add_rule(actor=self.one, company=self.company, version=self.draft,
                       data={'factor': factor.pk, 'when_outcome': 'detail', 'effect': 'none'})

    def test_only_one_proposal_goes_through(self):
        start = threading.Barrier(2)
        outcomes = {}

        def propose(name, user):
            try:
                start.wait(5)
                outcomes[name] = rules.propose(actor=user, company=self.company, version=self.draft, data={}).status
            except Exception as exc:  # noqa: BLE001 — the test reads it
                outcomes[name] = getattr(exc, 'code', repr(exc))
            finally:
                connection.close()

        threads = [threading.Thread(target=propose, args=(name, user)) for name, user in (('one', self.one),
                                                                                          ('two', self.two))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(sorted(outcomes.values()), ['not_draft', 'pending'], outcomes)
