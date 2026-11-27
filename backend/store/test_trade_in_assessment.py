"""
TRADE-IN VALUATION PRO · Phase 4 — the assessment and its calculation (docs/adr-trade-in-valuation.md §5, §6, §8.1).

What these tests hold: the selector's choice finds ONE tariff entry or says why not; a calculation reads the security
verdict first and a block has no value at all; every applicable question must be answered; the tariff and rule
versions are frozen and an approval refuses a stale calculation; an offer above the computed value needs another
person and never passes the tariff's maximum; trading towards a device shows a difference, never a discount; another
company or branch does not exist for you; nothing is written to stock, payables, payments or fiscal tables.
"""
import json
import threading
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from store import finance_test_support as fx
from store import trade_in_assessment as assessments
from store import trade_in_eligibility as eligibility
from store import trade_in_rules as rules
from store import trade_in_tariff as tariff
from store.capabilities import DEFAULT_ADMIN_CAPABILITY_CODES, EXPLICIT_GRANT_CODES
from store.device_identity import luhn_check_digit
from store.models import (
    AdminAuditLog,
    Customer,
    Order,
    PaymentTransaction,
    Product,
    ProductModel,
    StockMovement,
    StockUnit,
    TradeInAssessment,
    TradeInAssessmentAnswer,
)

D = Decimal
VIEW, VALUE, APPROVE = 'sales.tradein.view', 'sales.tradein.value', 'sales.tradein.approve'
T_ALL = ['sales.tradein.tariff.view', 'sales.tradein.tariff.manage', 'sales.tradein.tariff.approve']
URL = '/api/admin/trade-in/'


def imei(body):
    return body + luhn_check_digit(body)


class AssessmentBase(TestCase):
    def setUp(self):
        self.company = fx.company('tia-a', 'Evaluaciones A')
        self.other = fx.company('tia-b', 'Evaluaciones B')
        self.branch = fx.branch(self.company, 'Centro')
        self.far = fx.branch(self.company, 'Lejos')
        self.valuer = fx.member(self.company, 'tia_valua', [VIEW, VALUE])
        self.approver = fx.member(self.company, 'tia_aprueba', [VIEW, APPROVE])
        self.reader = fx.member(self.company, 'tia_lee', [VIEW])
        self.admin = fx.member(self.company, 'tia_tarifa', T_ALL)
        self.boss = fx.member(self.company, 'tia_jefe', T_ALL)
        self.near_only = fx.member(self.company, 'tia_cerca', [VIEW, VALUE], branches=[self.branch])
        self.stranger = fx.member(self.other, 'tia_otro', [VIEW, VALUE, APPROVE])
        self.model = ProductModel.objects.create(company=self.company, name='Modelo X 12', slug='modelo-x-12')
        self.entry = self.priced(product_model=self.model.pk)
        self.screen, self.battery = self.ruleset()
        self.source = eligibility.save_source(actor=self.admin, company=self.company, data={
            'name': 'Fuente oficial', 'url': 'https://consulta.example.org', 'identifier': 'imei'})
        self.customer = Customer.objects.create(company=self.company, first_name='Cliente', last_name='Uno',
                                                document_type='dni', document_number='45678912')
        self.counter = 0

    # -- fixtures through the services ---------------------------------------------------------------
    def priced(self, base='1000.00', maximum='1200.00', **identity):
        data = {'brand': 'Marca Uno', 'model_name': 'Modelo X 12', 'capacity': '128 GB', 'sim': 'eSIM',
                'base_reception_value': base, 'max_reception_value': maximum, 'resale_value': '1777.77',
                'refurbishment_cost': '111.11', 'target_margin': '222.22', **identity}
        entry, version = tariff.create_entry(actor=self.admin, company=self.company, data=data)
        tariff.approve_version(actor=self.boss, company=self.company, version=version)
        return entry

    def ruleset(self):
        cos = rules.create_group(actor=self.admin, company=self.company,
                                 data={'kind': 'cosmetic', 'name': 'Estética'})
        bat = rules.create_group(actor=self.admin, company=self.company, data={'kind': 'battery', 'name': 'Batería'})
        screen = rules.create_factor(actor=self.admin, company=self.company, data={
            'group': cos.pk, 'label': 'Pantalla', 'code': 'pantalla', 'input': 'check', 'component': 'pantalla'})
        battery = rules.create_factor(actor=self.admin, company=self.company, data={
            'group': bat.pk, 'label': 'Salud de batería', 'code': 'bateria', 'input': 'percent'})
        draft = rules.create_draft(actor=self.admin, company=self.company)
        rules.add_rule(actor=self.admin, company=self.company, version=draft, data={
            'factor': screen.pk, 'when_outcome': 'detail', 'effect': 'percent', 'percent': '10'})
        rules.add_rule(actor=self.admin, company=self.company, version=draft, data={
            'factor': screen.pk, 'when_outcome': 'not_working', 'effect': 'requires_approval'})
        rules.add_rule(actor=self.admin, company=self.company, version=draft, data={
            'factor': battery.pk, 'when_percent_lt': '80', 'effect': 'fixed', 'amount': '50.00'})
        rules.propose(actor=self.admin, company=self.company, version=draft, data={})
        rules.approve(actor=self.boss, company=self.company, version=draft)
        return screen, battery

    def clear_check(self, branch=None, clean=True):
        self.counter += 1
        check = eligibility.create_check(actor=self.valuer, company=self.company, data={
            'branch': (branch or self.branch).pk, 'imei': imei(f'{35693803564300 + self.counter}')})
        if clean:
            for code in eligibility.due_blockers(check):
                if code not in eligibility.COMPUTED:
                    eligibility.answer_blocker(actor=self.valuer, company=self.company, check=check,
                                               data={'code': code, 'answer': 'clear'})
            eligibility.record_identity(actor=self.valuer, company=self.company, check=check,
                                        data={'slot': 'imei', 'result': 'no_report', 'source': self.source.pk})
        return check

    def api(self, user):
        return fx.client_for(user)

    def post(self, path, data, user=None, expect=201, method='post'):
        res = getattr(self.api(user or self.valuer), method)(f'{URL}{path}', data, format='json')
        self.assertEqual(res.status_code, expect, res.content)
        return res.json()

    def opened(self, check=None, **over):
        check = check or self.clear_check()
        data = {'device_check': check.pk, 'tariff_entry': self.entry.pk, 'customer': self.customer.pk, **over}
        return self.post('assessments/', data)

    def answered(self, answers=None, **over):
        created = self.opened(**over)
        self.post(f'assessments/{created["id"]}/answers/',
                  {'answers': answers or {'pantalla': 'detail', 'bateria': '75'}}, method='put', expect=200)
        return created

    def calculated(self, answers=None, data=None, **over):
        created = self.answered(answers, **over)
        return self.post(f'assessments/{created["id"]}/calculate/', data or {}, expect=200)


class TariffMatchTest(AssessmentBase):
    def match(self, expect=200, **params):
        res = self.api(self.valuer).get(f'{URL}tariff/match/', {'product_model': self.model.pk, **params})
        self.assertEqual(res.status_code, expect, res.content)
        return res.json()

    def test_exact_then_wildcard_then_by_name(self):
        self.assertEqual(self.match(capacity='128GB', sim='esim')['id'], self.entry.pk)
        wildcard = self.priced(capacity='256 GB', sim='')
        self.assertEqual(self.match(capacity='256 gb', sim='Dual SIM', region='LA')['id'], wildcard.pk)
        other_model = ProductModel.objects.create(company=self.company, name='Serie S 5', slug='serie-s-5')
        by_name = self.priced(model_name='SERIE  s 5', capacity='64 GB', sim='')
        res = self.api(self.valuer).get(f'{URL}tariff/match/', {'product_model': other_model.pk, 'capacity': '64GB'})
        self.assertEqual(res.json()['id'], by_name.pk)

    def test_ambiguous_and_missing_are_said_never_chosen(self):
        other_model = ProductModel.objects.create(company=self.company, name='Serie S 5', slug='serie-s-5b')
        first = self.priced(brand='Marca Uno', model_name='Serie S 5', capacity='64 GB', sim='')
        second = self.priced(brand='Marca Dos', model_name='Serie S 5', capacity='64 GB', sim='')
        res = self.api(self.valuer).get(f'{URL}tariff/match/', {'product_model': other_model.pk, 'capacity': '64 GB'})
        self.assertEqual((res.status_code, res.json()['code']), (409, 'tariff_ambiguous'))
        self.assertEqual(res.json()['entries'], sorted([first.pk, second.pk]))
        self.assertEqual(self.match(expect=409, capacity='1 TB')['code'], 'tariff_missing')
        foreign = ProductModel.objects.create(company=self.other, name='Modelo X 12', slug='x')
        res = self.api(self.valuer).get(f'{URL}tariff/match/', {'product_model': foreign.pk})
        self.assertEqual(res.status_code, 404)

    def test_an_assessment_can_open_from_the_selector(self):
        check = self.clear_check()
        created = self.post('assessments/', {'device_check': check.pk, 'product_model': self.model.pk,
                                             'capacity': '128 GB', 'sim': 'eSIM'})
        self.assertEqual(created['tariff_entry'], self.entry.pk)


class CalculationFlowTest(AssessmentBase):
    def test_answer_calculate_and_freeze(self):
        done = self.calculated()
        self.assertEqual(done['status'], 'calculated')
        self.assertEqual(done['eligibility'], 'eligible_with_deductions')
        self.assertEqual(done['computed_value'], '850.00')           # 1000 − 10 % − 50
        self.assertEqual(done['offered_value'], '850.00')
        row = TradeInAssessment.objects.get(pk=done['id'])
        self.assertIsInstance(row.computed_value, Decimal)
        self.assertEqual(row.tariff_version, tariff.effective_version(self.entry))
        self.assertEqual(row.rule_set_version, rules.effective_rule_set(self.company))
        self.assertEqual(done['calculation']['security']['eligibility'], 'eligible')
        text = json.dumps(done)
        for internal in ('1777.77', '111.11', '222.22'):
            self.assertNotIn(internal, text)

    def test_every_applicable_question_must_be_answered(self):
        created = self.answered({'pantalla': 'works'})
        res = self.post(f'assessments/{created["id"]}/calculate/', {}, expect=400)
        self.assertEqual((res['code'], res['questions']), ('unanswered', ['bateria']))

    def test_the_customer_is_required_by_default_and_the_policy_can_lift_it(self):
        created = self.answered(customer='')
        self.assertEqual(self.post(f'assessments/{created["id"]}/calculate/', {}, expect=400)['code'],
                         'customer_required')
        tariff.update_policy(actor=self.boss, company=self.company, data={'require_identified_customer': False})
        self.post(f'assessments/{created["id"]}/calculate/', {}, expect=200)

    def test_a_block_has_no_value_and_a_pending_check_a_provisional_one(self):
        blocked = self.clear_check()
        eligibility.answer_blocker(actor=self.valuer, company=self.company, check=blocked,
                                   data={'code': 'activation_lock', 'answer': 'blocked'})
        done = self.calculated(check=blocked)
        self.assertEqual((done['eligibility'], done['computed_value'], done['offered_value']),
                         ('not_eligible', None, None))
        snap = done['calculation']
        self.assertEqual((snap['eligibility'], snap['totals']['computed_value'], snap['totals']['offered_value'],
                          snap['needs_approval']), ('not_eligible', None, None, False))
        res = self.post(f'assessments/{done["id"]}/calculate/', {'offered_value': '100.00',
                                                                 'adjustment_reason': 'x'}, expect=400)
        self.assertEqual(res['code'], 'not_eligible')
        pending = self.calculated(check=self.clear_check(clean=False))
        self.assertEqual((pending['eligibility'], pending['computed_value']), ('pending_verification', '850.00'))

    def test_rules_status_flags_need_an_approver(self):
        done = self.calculated({'pantalla': 'not_working', 'bateria': '90'})
        self.assertEqual((done['eligibility'], done['needs_approval']), ('requires_approval', True))

    def test_an_edit_sends_a_calculation_back_to_draft(self):
        done = self.calculated()
        back = self.post(f'assessments/{done["id"]}/answers/', {'answers': {'bateria': '95'}}, method='put',
                         expect=200)
        self.assertEqual((back['status'], back['computed_value'], back['calculation']), ('draft', None, None))

    def test_missing_tariff_or_rules(self):
        created = self.answered()
        tariff.deactivate_entry(actor=self.admin, company=self.company, entry=self.entry, reason='Ya no')
        self.assertEqual(self.post(f'assessments/{created["id"]}/calculate/', {}, expect=409)['code'],
                         'tariff_missing')


class OfferAndApprovalTest(AssessmentBase):
    def test_lowering_needs_a_reason_raising_needs_an_approver_and_never_passes_the_maximum(self):
        created = self.answered()
        path = f'assessments/{created["id"]}/calculate/'
        self.assertEqual(self.post(path, {'offered_value': '800.00'}, expect=400)['code'], 'reason_required')
        lower = self.post(path, {'offered_value': '800.00', 'adjustment_reason': 'Rayón profundo'}, expect=200)
        self.assertEqual((lower['offered_value'], lower['needs_approval']), ('800.00', False))
        res = self.post(path, {'offered_value': '1200.01', 'adjustment_reason': 'Cliente frecuente'}, expect=400)
        self.assertEqual(res['code'], 'above_maximum')
        raised = self.post(path, {'offered_value': '900.00', 'adjustment_reason': 'Cliente frecuente'}, expect=200)
        self.assertEqual((raised['eligibility'], raised['needs_approval']), ('requires_approval', True))
        self.assertEqual(self.post(f'assessments/{created["id"]}/approve/', {}, user=self.valuer, expect=403)['detail'],
                         'No tienes permisos para esta operación.')
        both = fx.member(self.company, 'tia_ambos', [VIEW, VALUE, APPROVE])
        mine = self.calculated(data={'offered_value': '900.00', 'adjustment_reason': 'x'})
        TradeInAssessment.objects.filter(pk=mine['id']).update(calculated_by=both, created_by=both,
                                                               contributor_ids=[both.pk])
        self.assertEqual(self.post(f'assessments/{mine["id"]}/approve/', {}, user=both, expect=403)['code'],
                         'own_change')
        done = self.post(f'assessments/{created["id"]}/approve/', {}, user=self.approver, expect=200)
        self.assertEqual(done['status'], 'approved')
        res = self.post(f'assessments/{created["id"]}/answers/', {'answers': {'bateria': '99'}}, method='put',
                        expect=409)
        self.assertEqual(res['code'], 'not_editable')

    def test_nothing_to_approve_is_said(self):
        done = self.calculated()
        self.assertEqual(self.post(f'assessments/{done["id"]}/approve/', {}, user=self.approver, expect=409)['code'],
                         'not_needed')

    def test_a_stale_calculation_is_not_approved(self):
        done = self.calculated({'pantalla': 'not_working', 'bateria': '90'})
        tariff.propose_version(actor=self.admin, company=self.company, entry=self.entry,
                               data={'base_reception_value': '900.00', 'max_reception_value': '1100.00'})
        res = self.post(f'assessments/{done["id"]}/approve/', {}, user=self.approver, expect=409)
        self.assertEqual((res['code'], res['reasons']), ('calculation_stale', ['tariff']))
        again = self.post(f'assessments/{done["id"]}/calculate/', {}, expect=200)
        draft = rules.create_draft(actor=self.admin, company=self.company)
        rules.propose(actor=self.admin, company=self.company, version=draft, data={})
        rules.approve(actor=self.boss, company=self.company, version=draft)
        res = self.post(f'assessments/{again["id"]}/approve/', {}, user=self.approver, expect=409)
        self.assertEqual(res['reasons'], ['rules'])

    def test_a_device_that_stopped_being_clear_is_not_approved(self):
        check = self.clear_check()
        done = self.calculated({'pantalla': 'not_working', 'bateria': '90'}, check=check)
        eligibility.record_identity(actor=self.valuer, company=self.company, check=check,
                                    data={'slot': 'imei', 'result': 'reported', 'source': self.source.pk})
        res = self.post(f'assessments/{done["id"]}/approve/', {}, user=self.approver, expect=409)
        self.assertEqual(res['code'], 'not_eligible')


class TradeTowardsTest(AssessmentBase):
    def test_the_difference_is_shown_never_a_discount(self):
        product = Product.objects.create(company=self.company, name='Equipo nuevo', slug='nuevo', price='4500.00',
                                         inventory=0)
        done = self.calculated(purpose='trade_towards', target_product=product.pk)
        self.assertEqual((done['target_price'], done['difference']), ('4500.00', '3650.00'))
        cheap = Product.objects.create(company=self.company, name='Accesorio', slug='acc', price='100.00',
                                       inventory=0)
        low = self.calculated(purpose='trade_towards', target_product=cheap.pk)
        self.assertEqual(low['difference'], '0.00')
        self.assertEqual(Order.objects.count(), 0)

    def test_targets_only_when_trading_and_quotes_need_their_authority(self):
        product = Product.objects.create(company=self.company, name='Equipo', slug='eq', price='10.00', inventory=0)
        check = self.clear_check()
        res = self.post('assessments/', {'device_check': check.pk, 'tariff_entry': self.entry.pk,
                                         'target_product': product.pk}, expect=400)
        self.assertEqual(res['field'], 'purpose')
        res = self.post('assessments/', {'device_check': check.pk, 'tariff_entry': self.entry.pk,
                                         'purpose': 'trade_towards', 'target_sales_quote': 1}, expect=403)


class AuthorityAndIsolationTest(AssessmentBase):
    def test_one_live_assessment_per_check(self):
        check = self.clear_check()
        first = self.opened(check=check)
        self.assertEqual(self.post('assessments/', {'device_check': check.pk, 'tariff_entry': self.entry.pk},
                                   expect=409)['code'], 'check_in_use')
        self.post(f'assessments/{first["id"]}/cancel/', {'reason': 'Se abrió por error'}, expect=200)
        self.opened(check=check)
        self.assertEqual(TradeInAssessment.objects.get(pk=first['id']).label, 'EVA-000001')

    def test_each_door_asks_its_capability(self):
        created = self.answered()
        for user, method, path, data, expected in (
            (self.reader, 'post', 'assessments/', {}, 403),
            (self.reader, 'post', f'assessments/{created["id"]}/calculate/', {}, 403),
            (self.reader, 'put', f'assessments/{created["id"]}/answers/', {'answers': {}}, 403),
            (self.valuer, 'post', f'assessments/{created["id"]}/approve/', {}, 403),
            (self.admin, 'get', 'assessments/', None, 403),
        ):
            with self.subTest(user=user.username, path=path):
                res = getattr(self.api(user), method)(f'{URL}{path}', data, format='json')
                self.assertEqual(res.status_code, expected, res.content)

    def test_another_company_or_branch_answers_404(self):
        created = self.answered(check=self.clear_check(branch=self.far))
        for user in (self.stranger, self.near_only):
            for method, path in (('get', f'assessments/{created["id"]}/'),
                                 ('post', f'assessments/{created["id"]}/calculate/'),
                                 ('post', f'assessments/{created["id"]}/approve/')):
                if user is self.near_only and path.endswith('approve/'):
                    continue                                   # no approve capability: 403 before any lookup
                with self.subTest(user=user.username, path=path):
                    res = getattr(self.api(user), method)(f'{URL}{path}', {}, format='json')
                    self.assertEqual(res.status_code, 404, res.content)
        foreign_check = eligibility.create_check(actor=self.stranger, company=self.other, data={
            'branch': fx.branch(self.other, 'Otra').pk, 'imei': imei('49015420323751')})
        res = self.api(self.valuer).post(f'{URL}assessments/', {'device_check': foreign_check.pk,
                                                                'tariff_entry': self.entry.pk}, format='json')
        self.assertEqual(res.status_code, 404)

    def test_approve_is_an_explicit_grant_with_its_repair(self):
        from importlib import import_module

        self.assertIn(APPROVE, EXPLICIT_GRANT_CODES)
        self.assertNotIn(APPROVE, DEFAULT_ADMIN_CAPABILITY_CODES)
        self.assertEqual(import_module('store.migrations.0197_trade_in_assessment').CODES, {APPROVE})


class GuardsTest(AssessmentBase):
    def test_nothing_is_bought_received_or_paid(self):
        counts = (Order.objects.count(), StockMovement.objects.count(), StockUnit.objects.count(),
                  PaymentTransaction.objects.count())
        done = self.calculated(data={'offered_value': '900.00', 'adjustment_reason': 'x'})
        self.post(f'assessments/{done["id"]}/approve/', {}, user=self.approver, expect=200)
        self.assertEqual(counts, (Order.objects.count(), StockMovement.objects.count(), StockUnit.objects.count(),
                                  PaymentTransaction.objects.count()))
        self.assertFalse(AdminAuditLog.objects.filter(action__startswith='trade_in_assessment',
                                                      company__isnull=True).exists())

    def test_an_approved_assessment_is_frozen_in_the_model(self):
        done = self.calculated(data={'offered_value': '900.00', 'adjustment_reason': 'x'})
        self.post(f'assessments/{done["id"]}/approve/', {}, user=self.approver, expect=200)
        row = TradeInAssessment.objects.get(pk=done['id'])
        row.offered_value = D('1.00')
        with self.assertRaises(ValidationError):
            row.save()
        answer = TradeInAssessmentAnswer.objects.filter(assessment=row).first()
        with self.assertRaises(ValidationError):
            answer.delete()

    def test_the_engine_names_no_maker_or_shop(self):
        import pathlib

        root = pathlib.Path(assessments.__file__).parent
        for name in ('trade_in_assessment.py', 'trade_in_assessment_views.py'):
            text = (root / name).read_text(encoding='utf-8').lower()
            for word in ('iphone', 'apple', 'samsung', 'black dog', 'blackdog', 'icloud'):
                with self.subTest(file=name, word=word):
                    self.assertNotIn(word, text)


class ReviewFindingsTest(AssessmentBase):
    """Review of #464: what may be approved, the final snapshot, closed checks, identifiers, prices, authorship."""

    def requiring(self, check=None):
        return self.calculated({'pantalla': 'not_working', 'bateria': '90'}, check=check)

    def test_a_pending_valuation_is_never_approved_and_a_pending_snapshot_is_stale(self):
        check = self.clear_check(clean=False)
        done = self.requiring(check=check)
        self.assertEqual(done['eligibility'], 'pending_verification')
        res = self.post(f'assessments/{done["id"]}/approve/', {}, user=self.approver, expect=409)
        self.assertEqual(res['code'], 'pending_verification')
        for code in eligibility.due_blockers(check):
            if code not in eligibility.COMPUTED:
                eligibility.answer_blocker(actor=self.valuer, company=self.company, check=check,
                                           data={'code': code, 'answer': 'clear'})
        eligibility.record_identity(actor=self.valuer, company=self.company, check=check,
                                    data={'slot': 'imei', 'result': 'no_report', 'source': self.source.pk})
        res = self.post(f'assessments/{done["id"]}/approve/', {}, user=self.approver, expect=409)
        self.assertEqual((res['code'], res['reasons']), ('calculation_stale', ['security']))
        again = self.post(f'assessments/{done["id"]}/calculate/', {}, expect=200)
        self.assertEqual(self.post(f'assessments/{again["id"]}/approve/', {}, user=self.approver,
                                   expect=200)['status'], 'approved')

    def test_an_expired_identity_record_or_a_deactivated_entry_stop_the_approval(self):
        done = self.requiring()
        later = timezone.now() + timedelta(hours=30)
        with mock.patch('store.trade_in_eligibility.timezone.now', return_value=later):
            res = self.post(f'assessments/{done["id"]}/approve/', {}, user=self.approver, expect=409)
        self.assertEqual(res['code'], 'pending_verification')
        tariff.deactivate_entry(actor=self.admin, company=self.company, entry=self.entry, reason='Ya no')
        res = self.post(f'assessments/{done["id"]}/approve/', {}, user=self.approver, expect=409)
        self.assertEqual((res['code'], res['reasons']), ('calculation_stale', ['tariff']))

    def test_closing_the_check_cancels_its_assessment_and_a_closed_check_is_never_approved(self):
        check = self.clear_check()
        done = self.requiring(check=check)
        TradeInAssessment.objects.filter(pk=done['id'])  # still calculated
        from store.models import TradeInDeviceCheck

        TradeInDeviceCheck.objects.filter(pk=check.pk).update(status='closed')
        res = self.post(f'assessments/{done["id"]}/approve/', {}, user=self.approver, expect=409)
        self.assertEqual(res['code'], 'check_closed')
        other = self.clear_check()
        live = self.requiring(check=other)
        eligibility.close_check(actor=self.valuer, company=self.company, check=other, reason='Se fue el cliente')
        self.assertEqual(TradeInAssessment.objects.get(pk=live['id']).status, 'cancelled')
        self.assertTrue(AdminAuditLog.objects.filter(action='trade_in_assessment_cancelled',
                                                     metadata__because='device_check_closed').exists())

    def test_reasons_and_notes_never_carry_the_devices_identifiers(self):
        check = self.clear_check()
        created = self.answered(check=check)
        full = imei(f'{35693803564300 + self.counter}')
        res = self.post(f'assessments/{created["id"]}/calculate/', {
            'offered_value': '900.00', 'adjustment_reason': f'IMEI {full}'}, expect=400)
        self.assertEqual(res['code'], 'identifier_in_text')
        done = self.post(f'assessments/{created["id"]}/calculate/', {
            'offered_value': '900.00', 'adjustment_reason': 'Cliente frecuente'}, expect=200)
        res = self.post(f'assessments/{done["id"]}/approve/', {'note': f'ok {full[:11]}'}, user=self.approver,
                        expect=400)
        self.assertEqual(res['code'], 'identifier_in_text')
        texts = json.dumps([done, *AdminAuditLog.objects.filter(company=self.company).values_list('metadata',
                                                                                              flat=True)])
        for secret in (full, check.imei_digest):
            self.assertNotIn(secret, texts)

    def test_whoever_answered_does_not_approve(self):
        answering_approver = fx.member(self.company, 'tia_responde', [VIEW, VALUE, APPROVE])
        created = self.answered({'pantalla': 'not_working', 'bateria': '90'})
        self.post(f'assessments/{created["id"]}/answers/', {'answers': {'bateria': '91'}}, method='put',
                  user=answering_approver, expect=200)
        done = self.post(f'assessments/{created["id"]}/calculate/', {}, expect=200)
        res = self.post(f'assessments/{done["id"]}/approve/', {}, user=answering_approver, expect=403)
        self.assertEqual(res['code'], 'own_change')

    def test_the_price_is_the_tills_and_inactive_or_used_targets_are_refused(self):
        inactive = Product.objects.create(company=self.company, name='Viejo', slug='viejo', price='10.00',
                                          inventory=0, is_active=False)
        used = Product.objects.create(company=self.company, name='Seminuevo', slug='semi', price='10.00',
                                      inventory=0, is_serialized=True, condition_line=Product.CONDITION_USED)
        for product, code in ((inactive, 'product_inactive'), (used, 'target_unit_unsupported')):
            with self.subTest(code=code):
                res = self.post('assessments/', {'device_check': self.clear_check().pk, 'tariff_entry': self.entry.pk,
                                                 'purpose': 'trade_towards', 'target_product': product.pk},
                                expect=409 if code == 'product_inactive' else 400)
                self.assertEqual(res['code'], code)

    def test_a_linked_sales_quote_is_live_of_the_branch_and_only_quote_makers_read_its_total(self):
        from store.models import SalesQuote

        maker = fx.member(self.company, 'tia_cotiza', [VIEW, VALUE, 'sales.quotes.manage', 'sales.quotes.view'])
        today = tariff.company_today(self.company)
        quote = SalesQuote.objects.create(company=self.company, branch=self.branch, number='COT-000001',
                                          sequence_value=1, status='approved', valid_until=today + timedelta(days=5),
                                          total=D('4000.00'))
        elsewhere = SalesQuote.objects.create(company=self.company, branch=self.far, number='COT-000002',
                                              sequence_value=2, status='approved',
                                              valid_until=today + timedelta(days=5), total=D('1.00'))
        res = self.post('assessments/', {'device_check': self.clear_check().pk, 'tariff_entry': self.entry.pk,
                                         'customer': self.customer.pk, 'purpose': 'trade_towards',
                                         'target_sales_quote': elsewhere.pk}, user=maker, expect=400)
        self.assertEqual(res['code'], 'sales_quote_other_branch')
        created = self.post('assessments/', {'device_check': self.clear_check().pk, 'tariff_entry': self.entry.pk,
                                             'customer': self.customer.pk, 'purpose': 'trade_towards',
                                             'target_sales_quote': quote.pk}, user=maker)
        self.post(f'assessments/{created["id"]}/answers/', {'answers': {'pantalla': 'works', 'bateria': '90'}},
                  method='put', user=maker, expect=200)
        done = self.post(f'assessments/{created["id"]}/calculate/', {}, user=maker, expect=200)
        self.assertEqual((done['target_price'], done['difference']), ('4000.00', '3000.00'))
        hidden = self.api(self.reader).get(f'{URL}assessments/{created["id"]}/').json()
        self.assertEqual((hidden['target_price'], hidden['difference']), (None, None))

    def test_an_assessment_is_never_deleted(self):
        created = self.opened()
        with self.assertRaises(ValidationError):
            TradeInAssessment.objects.get(pk=created['id']).delete()

    def test_no_payable_credit_or_fiscal_row_is_written(self):
        from store.models import FiscalDocument, Obligation, SalesNote

        counts = (Obligation.objects.count(), FiscalDocument.objects.count(), SalesNote.objects.count())
        done = self.requiring()
        self.post(f'assessments/{done["id"]}/approve/', {}, user=self.approver, expect=200)
        self.assertEqual(counts, (Obligation.objects.count(), FiscalDocument.objects.count(),
                                  SalesNote.objects.count()))


class ConcurrentNumberingTest(TransactionTestCase):
    """Two assessments opened at the same instant get two numbers, never the same one."""

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Only PostgreSQL has the advisory locks this test is about.')
        base = AssessmentBase('setUp')
        base.setUp()
        self.base = base

    def test_two_at_once(self):
        base = self.base
        checks = [base.clear_check(), base.clear_check()]
        start = threading.Barrier(2)
        numbers = []

        def open_one(check):
            try:
                start.wait(5)
                created = assessments.create_assessment(actor=base.valuer, company=base.company, data={
                    'device_check': check.pk, 'tariff_entry': base.entry.pk})
                numbers.append(created.number)
            except Exception as exc:  # noqa: BLE001 — the test reads it
                numbers.append(repr(exc))
            finally:
                connection.close()

        threads = [threading.Thread(target=open_one, args=(check,)) for check in checks]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(sorted(numbers), [1, 2], numbers)
