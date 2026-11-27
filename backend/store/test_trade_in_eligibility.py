"""
TRADE-IN VALUATION PRO · Phase 3 — eligibility and the assisted identity check (docs/adr-trade-in-valuation.md §3.3).

What these tests hold: a device's identifiers are never stored, answered or audited in full (last four and a keyed
digest only); the mandatory blockers cannot be switched off; a block is NOT ELIGIBLE, never a discount; every
identifier needs a valid record of a manual check in an official source (or a declaration about the device itself);
duplicates are found against the company's own stock and open checks only; nothing here receives or pays.
"""
import json
import pathlib
from datetime import timedelta
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from store import finance_test_support as fx
from store import trade_in_eligibility as eligibility
from store.capabilities import (
    CAPABILITIES,
    DEFAULT_ADMIN_CAPABILITY_CODES,
    EXPLICIT_GRANT_CODES,
    STATUS_ACTIVE,
    TRADE_IN_ASSESSMENT_CODES,
)
from store.device_identity import luhn_check_digit
from store.models import (
    AdminAuditLog,
    Order,
    Product,
    StockMovement,
    StockUnit,
    TradeInDeviceCheck,
    TradeInIdentityRecord,
)

VIEW, VALUE = 'sales.tradein.view', 'sales.tradein.value'
T_VIEW, T_MANAGE, T_APPROVE = 'sales.tradein.tariff.view', 'sales.tradein.tariff.manage', 'sales.tradein.tariff.approve'
URL = '/api/admin/trade-in/'


def imei(body='35693803564380'):
    return body + luhn_check_digit(body)


IMEI_A = imei('35693803564380')
IMEI_B = imei('35693803564381')
IMEI_C = imei('49015420323751')
BAD_IMEI = IMEI_A[:14] + str((int(IMEI_A[14]) + 1) % 10)


class EligibilityBase(TestCase):
    def setUp(self):
        self.company = fx.company('tie-a', 'Elegibilidad A')
        self.other = fx.company('tie-b', 'Elegibilidad B')
        self.branch = fx.branch(self.company, 'Centro')
        self.far = fx.branch(self.company, 'Lejos')
        self.other_branch = fx.branch(self.other, 'Otra')
        self.valuer = fx.member(self.company, 'tie_valua', [VIEW, VALUE])
        self.reader = fx.member(self.company, 'tie_lee', [VIEW])
        self.manager = fx.member(self.company, 'tie_fuentes', [T_VIEW, T_MANAGE, T_APPROVE])
        self.near_only = fx.member(self.company, 'tie_cerca', [VIEW, VALUE], branches=[self.branch])
        self.stranger = fx.member(self.other, 'tie_otro', [VIEW, VALUE, T_VIEW, T_MANAGE, T_APPROVE])
        self.source = self.post('verification-sources/', {
            'name': 'Consulta oficial de IMEI', 'url': 'https://consulta.example.org/imei', 'identifier': 'imei',
            'checks': ['theft_report']}, user=self.manager)
        self.serial_source = self.post('verification-sources/', {
            'name': 'Bloqueo de activación', 'url': 'https://consulta.example.org/serie', 'identifier': 'serial',
            'checks': ['activation_lock']}, user=self.manager)

    def api(self, user):
        return fx.client_for(user)

    def post(self, path, data, user=None, expect=201):
        res = self.api(user or self.valuer).post(f'{URL}{path}', data, format='json')
        self.assertEqual(res.status_code, expect, res.content)
        return res.json()

    def check(self, **over):
        data = {'branch': self.branch.pk, 'has_imei': True, 'imei': IMEI_A, 'description': 'Equipo del cliente'}
        data.update(over)
        return self.post('checks/', data)

    def answer_all(self, check_id, answer='clear', skip=()):
        for blocker in self.api(self.valuer).get(f'{URL}checks/{check_id}/').json()['blockers']:
            if blocker['source'] == 'computed' or blocker['code'] in skip:
                continue
            self.post(f'checks/{check_id}/blockers/', {'code': blocker['code'], 'answer': answer}, expect=200)

    def record(self, check_id, slot='imei', result='no_report', source=None, expect=200, **over):
        data = {'slot': slot, 'result': result,
                'source': source if source is not None else (self.serial_source['id'] if slot == 'serial'
                                                             else self.source['id'])}
        data.update(over)
        return self.post(f'checks/{check_id}/identity-records/', data, expect=expect)


class CapabilitiesTest(TestCase):
    def test_two_explicit_grants_nobody_gets_by_default(self):
        from importlib import import_module

        self.assertEqual(TRADE_IN_ASSESSMENT_CODES, {VIEW, VALUE})
        for code in TRADE_IN_ASSESSMENT_CODES:
            self.assertEqual(CAPABILITIES[code].status, STATUS_ACTIVE)
        self.assertTrue(TRADE_IN_ASSESSMENT_CODES <= EXPLICIT_GRANT_CODES)
        self.assertFalse(TRADE_IN_ASSESSMENT_CODES & DEFAULT_ADMIN_CAPABILITY_CODES)
        company = fx.company('tie-nueva', 'Nueva')
        self.assertFalse(TRADE_IN_ASSESSMENT_CODES & set(company.roles.get(slug='administrador').capabilities))
        self.assertEqual(import_module('store.migrations.0196_trade_in_eligibility').CODES, TRADE_IN_ASSESSMENT_CODES)

    def test_the_repair_touches_only_a_fresh_installs_seeded_administrador(self):
        from importlib import import_module

        from django.apps import apps

        from store.models import CompanyRole, Membership
        from store.tests import _p2d_company, _p2d_member

        company = _p2d_company('tie-0196')
        admin = CompanyRole.objects.get(company=company, slug='administrador')
        admin.capabilities = sorted({*admin.capabilities, VIEW, VALUE})
        admin.save(update_fields=['capabilities'])
        self.assertFalse(Membership.objects.exists())
        strip = import_module('store.migrations.0196_trade_in_eligibility').strip
        strip(apps, None)
        admin.refresh_from_db()
        self.assertFalse({VIEW, VALUE} & set(admin.capabilities))
        _p2d_member(company, 'tie-0196-user', ['company.view'])
        admin.capabilities = sorted({*admin.capabilities, VIEW})
        admin.save(update_fields=['capabilities'])
        strip(apps, None)
        admin.refresh_from_db()
        self.assertIn(VIEW, admin.capabilities)


class IdentifiersTest(EligibilityBase):
    def test_identifiers_are_masked_everywhere_and_never_stored_whole(self):
        created = self.check(imei2=IMEI_B)
        self.assertEqual((created['imei'], created['imei2']), (f'•••• {IMEI_A[-4:]}', f'•••• {IMEI_B[-4:]}'))
        row = TradeInDeviceCheck.objects.get(pk=created['id'])
        self.assertNotIn(IMEI_A, json.dumps(created))
        self.assertEqual(len(row.imei_digest), 64)
        self.assertNotEqual(row.imei_digest, IMEI_A)
        stored = json.dumps([str(v) for v in TradeInDeviceCheck.objects.values().get(pk=row.pk).values()])
        self.assertNotIn(IMEI_A, stored)
        self.assertNotIn(IMEI_B, stored)
        for audit in AdminAuditLog.objects.filter(action__startswith='trade_in_', company=self.company):
            text = json.dumps(audit.metadata)
            for secret in (IMEI_A, IMEI_B, row.imei_digest, IMEI_A[-4:]):
                self.assertNotIn(secret, text)
        self.assertNotIn(row.imei_digest, json.dumps(self.api(self.reader).get(f'{URL}checks/{row.pk}/').json()))

    def test_the_same_imei_digests_differently_per_company(self):
        self.assertNotEqual(eligibility.digest(self.company.pk, IMEI_A), eligibility.digest(self.other.pk, IMEI_A))

    def test_a_failed_check_digit_is_not_eligible_never_corrected(self):
        created = self.check(imei=BAD_IMEI)
        self.assertEqual(created['eligibility'], 'not_eligible')
        self.assertIn('imei_invalid', created['blocked'])

    def test_what_a_device_must_carry(self):
        for over, field in (({'imei': ''}, 'imei'), ({'imei': '123'}, 'imei'),
                            ({'has_imei': False, 'imei': ''}, 'serial'),
                            ({'has_imei': False, 'imei': IMEI_A, 'serial': 'C02XG0FDH7JY'}, 'imei'),
                            ({'imei2': IMEI_A}, 'imei2'),
                            ({'description': 'IMEI 356938035643809'}, 'description'),
                            ({'branch': self.other_branch.pk}, None)):
            with self.subTest(over=over):
                res = self.api(self.valuer).post(f'{URL}checks/', {'branch': self.branch.pk, 'has_imei': True,
                                                                   'imei': IMEI_A, **over}, format='json')
                if field is None:
                    self.assertEqual(res.status_code, 404, res.content)
                else:
                    self.assertEqual(res.status_code, 400, res.content)
                    self.assertEqual(res.json()['field'], field)
        self.assertFalse(TradeInDeviceCheck.objects.exists())


class DuplicateTest(EligibilityBase):
    def unit(self, company, branch, status, value):
        product = Product.objects.create(company=company, name=f'P{value[-4:]}{status}', slug=f'p-{value}-{status}',
                                         price='10.00', inventory=0)
        return StockUnit.objects.create(company=company, branch=branch, product=product, imei=value, status=status)

    def test_an_open_check_or_a_live_unit_with_the_imei_blocks(self):
        self.check()
        second = self.check()
        self.assertIn('imei_duplicate', second['blocked'])
        self.unit(self.company, self.branch, StockUnit.Status.AVAILABLE, IMEI_C)
        third = self.check(imei=IMEI_C)
        self.assertEqual(TradeInDeviceCheck.objects.get(pk=third['id']).duplicate_of, 'stock')

    def test_a_sold_unit_is_reported_and_another_companys_is_invisible(self):
        self.unit(self.company, self.branch, StockUnit.Status.SOLD, IMEI_B)
        self.unit(self.other, self.other_branch, StockUnit.Status.AVAILABLE, IMEI_C)
        sold = self.check(imei=IMEI_B)
        self.assertTrue(sold['known_to_company'])
        self.assertNotIn('imei_duplicate', sold['blocked'])
        theirs = self.check(imei=IMEI_C)
        self.assertNotIn('imei_duplicate', theirs['blocked'])
        self.assertFalse(theirs['known_to_company'])


class VerdictTest(EligibilityBase):
    def test_from_pending_to_eligible_with_answers_and_valid_records(self):
        created = self.check(imei2=IMEI_B)
        self.assertEqual(created['eligibility'], 'pending_verification')
        self.assertEqual(created['unchecked_identifiers'], ['imei', 'imei2'])
        self.answer_all(created['id'])
        self.record(created['id'])
        still = self.record(created['id'], slot='imei2', result='unavailable')
        self.assertEqual((still['eligibility'], still['unchecked_identifiers']), ('pending_verification', ['imei2']))
        done = self.record(created['id'], slot='imei2')
        self.assertEqual(done['eligibility'], 'eligible')
        self.assertEqual(done['label'], 'APTO')

    def test_a_block_is_not_eligible_whatever_else(self):
        created = self.check()
        self.answer_all(created['id'])
        self.record(created['id'])
        done = self.post(f'checks/{created["id"]}/blockers/', {'code': 'activation_lock', 'answer': 'blocked'},
                         expect=200)
        self.assertEqual((done['eligibility'], done['blocked']), ('not_eligible', ['activation_lock']))

    def test_a_reported_or_invalid_record_blocks(self):
        created = self.check()
        self.answer_all(created['id'])
        reported = self.record(created['id'], result='reported')
        self.assertIn('theft_or_loss_report', reported['blocked'])
        other = self.check(imei=IMEI_B)
        self.answer_all(other['id'])
        self.assertIn('imei_invalid', self.record(other['id'], result='invalid')['blocked'])

    def test_an_expired_record_is_no_verification(self):
        created = self.check()
        self.answer_all(created['id'])
        self.record(created['id'], checked_at=(timezone.now() - timedelta(hours=30)).isoformat())
        res = self.api(self.valuer).get(f'{URL}checks/{created["id"]}/').json()
        self.assertEqual((res['eligibility'], res['identifiers'][0]['state']), ('pending_verification', 'expired'))
        self.record(created['id'])
        self.assertEqual(self.api(self.valuer).get(f'{URL}checks/{created["id"]}/').json()['eligibility'], 'eligible')

    def test_a_device_without_imei_is_checked_by_serial_or_a_declaration(self):
        created = self.check(has_imei=False, imei='', serial='C02XG0FDH7JY')
        self.assertEqual(created['unchecked_identifiers'], ['serial'])
        self.answer_all(created['id'])
        self.record(created['id'], slot='imei', expect=400)
        res = self.record(created['id'], slot='serial', source=self.source['id'], expect=400)   # an IMEI source
        self.assertEqual(res['field'], 'source')
        done = self.record(created['id'], slot='serial', result='declared_clear', source='')
        self.assertEqual(done['eligibility'], 'eligible')

    def test_records_need_a_source_unless_declared_are_not_future_and_never_change(self):
        created = self.check()
        self.assertEqual(self.record(created['id'], source='', expect=400)['field'], 'source')
        future = (timezone.now() + timedelta(hours=2)).isoformat()
        self.assertEqual(self.record(created['id'], checked_at=future, expect=400)['field'], 'checked_at')
        self.record(created['id'])
        record = TradeInIdentityRecord.objects.get()
        record.note = 'otra'
        with self.assertRaises(ValidationError):
            record.save()
        with self.assertRaises(ValidationError):
            record.delete()

    def test_the_server_decides_the_computed_blockers(self):
        created = self.check()
        res = self.post(f'checks/{created["id"]}/blockers/', {'code': 'imei_duplicate', 'answer': 'clear'},
                        expect=400)
        self.assertEqual(res['code'], 'computed')

    def test_a_closed_check_takes_nothing_more(self):
        created = self.check()
        self.post(f'checks/{created["id"]}/close/', {}, expect=400)
        self.post(f'checks/{created["id"]}/close/', {'reason': 'El cliente se fue'}, expect=200)
        for path, data in (('blockers/', {'code': 'activation_lock', 'answer': 'clear'}),
                           ('identity-records/', {'slot': 'imei', 'result': 'no_report', 'source': self.source['id']})):
            with self.subTest(path=path):
                self.assertEqual(self.post(f'checks/{created["id"]}/{path}', data, expect=409)['code'], 'closed')


class PolicyTest(EligibilityBase):
    def put(self, data, expect=200):
        res = self.api(self.manager).put(f'{URL}policy/', data, format='json')
        self.assertEqual(res.status_code, expect, res.content)
        return res.json()

    def test_the_mandatory_floor_cannot_be_switched_off(self):
        res = self.put({'optional_blockers': ['mdm_enrolled', 'activation_lock']}, expect=400)
        self.assertEqual((res['code'], res['codes']), ('mandatory_blocker', ['activation_lock']))
        policy = self.put({'optional_blockers': ['mdm_enrolled', 'illicit_signs']})
        self.assertEqual(policy['optional_blockers'], ['illicit_signs', 'mdm_enrolled'])
        created = self.check()
        codes = {b['code'] for b in created['blockers']}
        self.assertNotIn('mdm_enrolled', codes)
        self.assertTrue(eligibility.MANDATORY <= codes)

    def test_the_companys_own_blockers_and_validity(self):
        self.put({'extra_blockers': [{'code': 'pantalla_quemada', 'label': 'Pantalla con quemado severo'}],
                  'identity_check_validity_hours': 2})
        created = self.check()
        self.answer_all(created['id'], skip={'pantalla_quemada'})
        self.record(created['id'])
        res = self.api(self.valuer).get(f'{URL}checks/{created["id"]}/').json()
        self.assertEqual((res['eligibility'], res['unverified']), ('pending_verification', ['pantalla_quemada']))
        blocked = self.post(f'checks/{created["id"]}/blockers/', {'code': 'pantalla_quemada', 'answer': 'blocked'},
                            expect=200)
        self.assertEqual(blocked['eligibility'], 'not_eligible')
        record = TradeInIdentityRecord.objects.get()
        self.assertEqual(record.valid_until - record.checked_at, timedelta(hours=2))
        for bad in ({'extra_blockers': [{'code': 'activation_lock', 'label': 'x'}]},
                    {'extra_blockers': [{'code': 'Mal Código', 'label': 'x'}]},
                    {'identity_check_validity_hours': 0}, {'identity_check_validity_hours': 1000}):
            with self.subTest(bad=bad):
                self.put(bad, expect=400)


class AuthorityAndIsolationTest(EligibilityBase):
    def test_each_door_asks_its_capability(self):
        created = self.check()
        cases = [
            (self.reader, 'post', 'checks/', {'branch': self.branch.pk, 'imei': IMEI_B}, 403),
            (self.reader, 'post', f'checks/{created["id"]}/blockers/', {}, 403),
            (self.reader, 'post', f'checks/{created["id"]}/identity-records/', {}, 403),
            (self.reader, 'post', f'checks/{created["id"]}/close/', {'reason': 'x'}, 403),
            (self.valuer, 'post', 'verification-sources/', {'name': 'x', 'url': 'https://x.example'}, 403),
            (self.manager, 'get', 'checks/', None, 403),
        ]
        for user, method, path, data, expected in cases:
            with self.subTest(user=user.username, path=path):
                res = getattr(self.api(user), method)(f'{URL}{path}', data, format='json')
                self.assertEqual(res.status_code, expected, res.content)

    def test_another_company_or_an_unreachable_branch_answers_404(self):
        created = self.check(branch=self.far.pk)
        for user in (self.stranger, self.near_only):
            for method, path, data in (('get', f'checks/{created["id"]}/', None),
                                       ('post', f'checks/{created["id"]}/blockers/',
                                        {'code': 'activation_lock', 'answer': 'clear'}),
                                       ('post', f'checks/{created["id"]}/close/', {'reason': 'x'})):
                with self.subTest(user=user.username, path=path):
                    res = getattr(self.api(user), method)(f'{URL}{path}', data, format='json')
                    self.assertEqual(res.status_code, 404, res.content)
        self.assertEqual(self.api(self.near_only).get(f'{URL}checks/').json()['results'], [])
        mine = self.check()
        res = self.api(self.valuer).post(f'{URL}checks/{mine["id"]}/identity-records/', {
            'slot': 'imei', 'result': 'no_report', 'source': self.source['id']}, format='json')
        self.assertEqual(res.status_code, 200)
        theirs_source = self.post('verification-sources/', {'name': 'Suya', 'url': 'https://suya.example'},
                                  user=self.stranger)
        res = self.api(self.valuer).post(f'{URL}checks/{mine["id"]}/identity-records/', {
            'slot': 'imei', 'result': 'no_report', 'source': theirs_source['id']}, format='json')
        self.assertEqual(res.status_code, 404)

    def test_sources_are_https_and_the_companys(self):
        res = self.api(self.manager).post(f'{URL}verification-sources/', {'name': 'Mala', 'url': 'http://x.example'},
                                          format='json')
        self.assertEqual((res.status_code, res.json()['field']), (400, 'url'))
        self.assertEqual(len(self.api(self.stranger).get(f'{URL}verification-sources/').json()['results']), 0)


class GuardsTest(EligibilityBase):
    def test_no_order_stock_or_unit_is_written(self):
        counts = (Order.objects.count(), StockMovement.objects.count(), StockUnit.objects.count())
        created = self.check()
        self.answer_all(created['id'])
        self.record(created['id'])
        self.assertEqual(counts, (Order.objects.count(), StockMovement.objects.count(), StockUnit.objects.count()))
        self.assertFalse(AdminAuditLog.objects.filter(action__startswith='trade_in_', company__isnull=True).exists())

    def test_no_api_is_called_and_no_maker_or_shop_is_named(self):
        root = pathlib.Path(eligibility.__file__).parent
        for name in ('trade_in_eligibility.py', 'trade_in_eligibility_views.py'):
            text = (root / name).read_text(encoding='utf-8').lower()
            for word in ('requests', 'urllib', 'httpx', 'urlopen', 'osiptel', 'iphone', 'apple', 'icloud',
                         'samsung', 'black dog', 'blackdog'):
                with self.subTest(file=name, word=word):
                    self.assertNotIn(word, text)

    def test_the_validity_follows_now(self):
        created = self.check()
        self.answer_all(created['id'])
        self.record(created['id'])
        check = TradeInDeviceCheck.objects.get(pk=created['id'])
        later = timezone.now() + timedelta(hours=25)
        with mock.patch('store.trade_in_eligibility.timezone.now', return_value=later):
            self.assertEqual(eligibility.evaluate(check)['eligibility'], 'pending_verification')
