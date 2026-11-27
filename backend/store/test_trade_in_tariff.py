"""
TRADE-IN VALUATION PRO · Phase 1 — the per-company reception tariff (docs/adr-trade-in-valuation.md).

What these tests hold, in the owner's words: each company keeps its own tariff (nothing of the pilot in code); the
five amounts are Decimal and kept apart; a price is never rewritten (versions, history, who and when); a relevant
change waits for somebody else's approval; validity is the company's calendar; search, filters, deactivation and
Excel both ways work; another company's tariff does not exist for you; and nothing here buys, pays, credits or
receives a device.
"""
import io
import pathlib
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from store import finance_test_support as fx
from store import trade_in_tariff as tariff
from store import trade_in_tariff_io as tariff_io
from store.capabilities import (
    ASSIGNABLE_CAPABILITY_CODES,
    CAPABILITIES,
    DEFAULT_ADMIN_CAPABILITY_CODES,
    EXPLICIT_GRANT_CODES,
    STATUS_ACTIVE,
    TRADE_IN_TARIFF_CODES,
)
from store.models import (
    AdminAuditLog,
    CompanySettings,
    Order,
    PaymentTransaction,
    ProductModel,
    StockMovement,
    StockUnit,
    TradeInPolicy,
    TradeInTariffEntry,
    TradeInTariffImport,
    TradeInTariffVersion,
)

URL = '/api/admin/trade-in/tariff/'
VIEW, MANAGE, APPROVE, APPROVE_OWN = (
    'sales.tradein.tariff.view', 'sales.tradein.tariff.manage', 'sales.tradein.tariff.approve',
    'sales.tradein.tariff.approve_own')
D = Decimal


def body(**over):
    data = {
        'brand': 'Marca Uno', 'model_name': 'Modelo X 12', 'capacity': '128 GB', 'sim': 'eSIM', 'region': '',
        'base_reception_value': '1200.00', 'refurbishment_cost': '150.00', 'resale_value': '1900.00',
        'target_margin': '300.00', 'max_reception_value': '1400.00', 'reason': 'Lista inicial',
    }
    data.update(over)
    return data


class TariffBase(TestCase):
    def setUp(self):
        self.company = fx.company('tiv-a', 'Tienda A')
        self.other = fx.company('tiv-b', 'Tienda B')
        self.editor = fx.member(self.company, 'tiv_editor', [VIEW, MANAGE])
        self.approver = fx.member(self.company, 'tiv_jefe', [VIEW, APPROVE])
        self.owner = fx.member(self.company, 'tiv_dueno', [VIEW, MANAGE, APPROVE, APPROVE_OWN])
        self.reader = fx.member(self.company, 'tiv_lector', [VIEW])
        self.nobody = fx.member(self.company, 'tiv_caja', ['sales.pos.use', 'sales.quotes.view'])
        self.stranger = fx.member(self.other, 'tiv_otro', [VIEW, MANAGE, APPROVE, APPROVE_OWN])
        self.today = tariff.company_today(self.company)

    def api(self, user):
        return fx.client_for(user)

    def create(self, user=None, **over):
        res = self.api(user or self.editor).post(URL, body(**over), format='json')
        self.assertEqual(res.status_code, 201, res.content)
        return res.json()

    def approve(self, version_id, user=None, expect=200, **data):
        res = self.api(user or self.approver).post(f'{URL}versions/{version_id}/approve/', data, format='json')
        self.assertEqual(res.status_code, expect, res.content)
        return res.json()

    def approved_entry(self, **over):
        entry = self.create(**over)
        self.approve(entry['pending']['id'])
        return TradeInTariffEntry.objects.get(pk=entry['id'])

    def propose(self, entry_id, user=None, expect=201, **over):
        data = {key: over.pop(key) for key in list(over)}
        money = {field: body()[field] for field in tariff.MONEY_FIELDS}
        payload = {**money, **data}
        res = self.api(user or self.editor).post(f'{URL}{entry_id}/versions/', payload, format='json')
        self.assertEqual(res.status_code, expect, res.content)
        return res.json()


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

class TariffCapabilitiesTest(TestCase):
    def test_four_active_assignable_sales_capabilities(self):
        self.assertEqual(TRADE_IN_TARIFF_CODES, {VIEW, MANAGE, APPROVE, APPROVE_OWN})
        for code in TRADE_IN_TARIFF_CODES:
            self.assertEqual(CAPABILITIES[code].status, STATUS_ACTIVE, code)
            self.assertEqual(CAPABILITIES[code].module, 'sales', code)
            self.assertIn(code, ASSIGNABLE_CAPABILITY_CODES)

    def test_nobody_gains_them_by_default(self):
        """D7: explicit grants only — not the Administrador preset, not a legacy role, not a new company."""
        from store.company_provisioning import PRESET_ROLES
        from store.tenancy import LEGACY_ROLE_CAPABILITIES

        self.assertTrue(TRADE_IN_TARIFF_CODES <= EXPLICIT_GRANT_CODES)
        self.assertFalse(TRADE_IN_TARIFF_CODES & DEFAULT_ADMIN_CAPABILITY_CODES)
        for _name, slug, _desc, caps in PRESET_ROLES:
            self.assertFalse(TRADE_IN_TARIFF_CODES & set(caps), slug)
        for role, caps in LEGACY_ROLE_CAPABILITIES.items():
            self.assertFalse(TRADE_IN_TARIFF_CODES & set(caps), role)
        company = fx.company('tiv-new', 'Nueva')
        admin = set(company.roles.get(slug='administrador').capabilities)
        self.assertFalse(TRADE_IN_TARIFF_CODES & admin)


class TariffFreshInstallRepairTest(TestCase):
    """0194 strips the four codes from a fresh install's seeded Administrador (0061 reads the live catalogue)."""

    def run_0194(self):
        from importlib import import_module

        from django.apps import apps

        import_module('store.migrations.0194_trade_in_tariff').strip(apps, None)

    def test_the_frozen_codes_are_todays_and_the_seeded_administrador_has_none(self):
        from importlib import import_module

        from store.models import Company, CompanyRole

        self.assertEqual(import_module('store.migrations.0194_trade_in_tariff').CODES, TRADE_IN_TARIFF_CODES)
        seeded = CompanyRole.objects.get(company=Company.objects.get(slug='black-dog-store'), slug='administrador')
        self.assertFalse(TRADE_IN_TARIFF_CODES & set(seeded.capabilities))

    def test_the_repair_touches_only_a_fresh_installs_seeded_administrador(self):
        from store.models import CompanyRole, Membership
        from store.tests import _p2d_company, _p2d_member

        company = _p2d_company('tiv-0194')
        admin = CompanyRole.objects.get(company=company, slug='administrador')
        admin.capabilities = sorted({*admin.capabilities, *TRADE_IN_TARIFF_CODES})
        admin.save(update_fields=['capabilities'])
        other = CompanyRole.objects.create(company=company, name='Tarifario', slug='tarifario-0194',
                                           capabilities=['company.view', VIEW])
        self.assertFalse(Membership.objects.exists())
        self.run_0194()
        admin.refresh_from_db()
        other.refresh_from_db()
        self.assertFalse(TRADE_IN_TARIFF_CODES & set(admin.capabilities))
        self.assertIn(VIEW, other.capabilities)
        # A database in use: a grant is a decision, and a re-run keeps it.
        _p2d_member(company, 'tiv-0194-user', ['company.view'])
        admin.capabilities = sorted({*admin.capabilities, VIEW})
        admin.save(update_fields=['capabilities'])
        self.run_0194()
        admin.refresh_from_db()
        self.assertIn(VIEW, admin.capabilities)


# ---------------------------------------------------------------------------
# The database holds the rules even without the services
# ---------------------------------------------------------------------------

class TariffModelRulesTest(TariffBase):
    def entry(self, company=None, key='m|x|||'):
        return TradeInTariffEntry.objects.create(company=company or self.company, brand='M', model_name='X',
                                                 device_key=key)

    def version(self, entry, number=1, **over):
        fields = {'company': entry.company, 'entry': entry, 'number': number,
                  'base_reception_value': D('100.00'), 'max_reception_value': D('150.00'),
                  'valid_from': self.today}
        fields.update(over)
        return TradeInTariffVersion.objects.create(**fields)

    def test_amounts_are_decimal_never_negative_and_base_within_max(self):
        entry = self.entry()
        for over in ({'base_reception_value': D('-1.00')}, {'max_reception_value': D('-1.00'),
                                                             'base_reception_value': D('-2.00')},
                     {'refurbishment_cost': D('-0.01')}, {'resale_value': D('-5')}, {'target_margin': D('-1')},
                     {'base_reception_value': D('200.00')}):
            with self.subTest(over=over), self.assertRaises(IntegrityError), transaction.atomic():
                self.version(entry, **over)
        version = self.version(entry)
        version.refresh_from_db()
        self.assertIsInstance(version.base_reception_value, Decimal)

    def test_window_is_ordered_and_one_pending_per_device(self):
        entry = self.entry()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.version(entry, valid_until=self.today - timedelta(days=1))
        self.version(entry)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.version(entry, number=2)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.version(entry, number=1, status=TradeInTariffVersion.STATUS_APPROVED)

    def test_one_device_key_per_company_but_two_companies_may_share_it(self):
        self.entry()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.entry()
        self.entry(company=self.other)

    def test_a_decided_version_is_never_rewritten_nor_deleted(self):
        entry = self.entry()
        version = self.version(entry, status=TradeInTariffVersion.STATUS_APPROVED)
        version.base_reception_value = D('1.00')
        with self.assertRaises(ValidationError):
            version.save()
        with self.assertRaises(ValidationError):
            version.save(update_fields=['decision_note'])
        with self.assertRaises(ValidationError):
            version.delete()

    def test_pending_terms_do_not_change_either(self):
        version = self.version(self.entry())
        version.max_reception_value = D('999.00')
        with self.assertRaises(ValidationError):
            version.save(update_fields=['max_reception_value'])

    def test_versions_and_models_stay_inside_their_company(self):
        entry = self.entry()
        with self.assertRaises(ValidationError):
            self.version(entry, company=self.other)
        foreign = ProductModel.objects.create(company=self.other, name='Otro', slug='otro')
        entry.product_model = foreign
        with self.assertRaises(ValidationError):
            entry.save()


# ---------------------------------------------------------------------------
# Versions, approval and policy (services)
# ---------------------------------------------------------------------------

class TariffApprovalFlowTest(TariffBase):
    def test_a_first_price_waits_for_an_approver_and_keeps_the_five_amounts_apart(self):
        entry = self.create()
        self.assertIsNone(entry['current'])
        pending = entry['pending']
        self.assertEqual(pending['status'], 'pending')
        self.assertEqual([r['code'] for r in pending['approval_reasons']], ['new_entry'])
        self.assertEqual((pending['base_reception_value'], pending['refurbishment_cost'], pending['resale_value'],
                          pending['target_margin'], pending['max_reception_value']),
                         ('1200.00', '150.00', '1900.00', '300.00', '1400.00'))
        self.assertEqual(pending['currency'], 'PEN')
        decided = self.approve(pending['id'])
        self.assertEqual(decided['version']['status'], 'approved')
        self.assertEqual(decided['version']['approval_kind'], 'approver')
        self.assertEqual(decided['entry']['current']['id'], pending['id'])
        self.assertEqual(decided['version']['decided_by'], 'tiv_jefe')

    def test_lowering_what_the_shop_pays_is_approved_by_the_policy(self):
        entry = self.approved_entry()
        version = self.propose(entry.pk, base_reception_value='1100.00', max_reception_value='1300.00')['version']
        self.assertEqual(version['status'], 'approved')
        self.assertEqual(version['approval_kind'], 'policy')
        self.assertEqual(version['approval_reasons'], [])
        self.assertEqual(tariff.effective_version(entry).number, 2)

    def test_a_rise_waits_and_the_old_price_stays_in_force_meanwhile(self):
        entry = self.approved_entry()
        version = self.propose(entry.pk, base_reception_value='1250.00', max_reception_value='1500.00')['version']
        self.assertEqual(version['status'], 'pending')
        self.assertEqual({r['code'] for r in version['approval_reasons']}, {'base_increase', 'max_increase'})
        self.assertEqual(tariff.effective_version(entry).number, 1)
        self.approve(version['id'])
        self.assertEqual(tariff.effective_version(entry).number, 2)

    def test_the_company_tolerance_and_the_all_mode(self):
        entry = self.approved_entry()
        res = self.api(self.approver).put('/api/admin/trade-in/policy/',
                                          {'tariff_increase_tolerance_percent': '10'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        within = self.propose(entry.pk, base_reception_value='1300.00', max_reception_value='1500.00')['version']
        self.assertEqual(within['status'], 'approved')          # +8.3 % / +7.1 %: within 10 %
        above = self.propose(entry.pk, base_reception_value='1300.00', max_reception_value='1700.00')['version']
        self.assertEqual([r['code'] for r in above['approval_reasons']], ['max_increase'])
        self.api(self.approver).post(f'{URL}versions/{above["id"]}/reject/', {'note': 'No'}, format='json')
        self.api(self.approver).put('/api/admin/trade-in/policy/', {'tariff_approval_mode': 'all'}, format='json')
        lower = self.propose(entry.pk, base_reception_value='900.00', max_reception_value='1000.00')['version']
        self.assertEqual([r['code'] for r in lower['approval_reasons']], ['policy_all'])
        self.assertEqual(AdminAuditLog.objects.filter(action='trade_in_policy_updated', company=self.company).count(),
                         2)

    def test_extending_the_validity_is_relevant(self):
        end = self.today + timedelta(days=30)
        entry = self.approved_entry(valid_until=end.isoformat())
        same_money_longer = self.propose(entry.pk, valid_until=(end + timedelta(days=30)).isoformat())['version']
        self.assertEqual([r['code'] for r in same_money_longer['approval_reasons']], ['validity_extended'])

    def test_nothing_to_change_retroactive_and_one_pending_at_a_time(self):
        entry = self.approved_entry()
        res = self.propose(entry.pk, expect=400, valid_from=self.today.isoformat())
        self.assertEqual(res['code'], 'unchanged')
        res = self.propose(entry.pk, expect=400, max_reception_value='1350.00',
                           valid_from=(self.today - timedelta(days=1)).isoformat())
        self.assertEqual(res['code'], 'retroactive')
        self.propose(entry.pk, base_reception_value='1300.00', max_reception_value='1500.00')
        res = self.propose(entry.pk, expect=409, base_reception_value='1350.00', max_reception_value='1500.00')
        self.assertEqual(res['code'], 'pending_exists')

    def test_bad_amounts_are_refused_with_the_field(self):
        for over, field in (({'base_reception_value': '-5'}, 'base_reception_value'),
                            ({'max_reception_value': '10.123'}, 'max_reception_value'),
                            ({'resale_value': 'mil'}, 'resale_value'),
                            ({'base_reception_value': '1500.00'}, 'base_reception_value'),
                            ({'max_reception_value': ''}, 'max_reception_value'),
                            ({'brand': ''}, 'brand')):
            with self.subTest(over=over):
                res = self.api(self.editor).post(URL, body(**over), format='json')
                self.assertEqual(res.status_code, 400, res.content)
                self.assertEqual(res.json()['field'], field)
        self.assertFalse(TradeInTariffEntry.objects.exists())

    def test_the_same_device_twice_is_refused_however_it_is_written(self):
        self.create()
        res = self.api(self.editor).post(URL, body(brand='MARCA  uno', model_name='modelo x 12', capacity='128gb'),
                                         format='json')
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'duplicate')

    def test_the_proposer_does_not_approve_without_approve_own_and_a_reason(self):
        both = fx.member(self.company, 'tiv_ambos', [VIEW, MANAGE, APPROVE])
        entry = self.create(user=both)
        res = self.approve(entry['pending']['id'], user=both, expect=403)
        self.assertEqual(res['code'], 'own_change')
        own = self.create(user=self.owner, model_name='Modelo Y')
        res = self.approve(own['pending']['id'], user=self.owner, expect=400)
        self.assertEqual(res['code'], 'reason_required')
        done = self.approve(own['pending']['id'], user=self.owner, note='Equipo pequeño, sin segundo aprobador')
        self.assertEqual(done['version']['approval_kind'], 'self')
        audit = AdminAuditLog.objects.get(action='trade_in_tariff_version_approved', target_id=str(own['pending']['id']))
        self.assertTrue(audit.metadata['self_approval'])
        self.assertEqual(audit.company, self.company)

    def test_reject_needs_a_reason_and_withdraw_is_for_the_proposer_or_an_approver(self):
        entry = self.create()
        version_id = entry['pending']['id']
        res = self.api(self.approver).post(f'{URL}versions/{version_id}/reject/', {}, format='json')
        self.assertEqual(res.status_code, 400)
        other_editor = fx.member(self.company, 'tiv_editor2', [VIEW, MANAGE])
        res = self.api(other_editor).post(f'{URL}versions/{version_id}/withdraw/', {}, format='json')
        self.assertEqual(res.status_code, 403, res.content)
        res = self.api(self.editor).post(f'{URL}versions/{version_id}/withdraw/', {}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['version']['status'], 'withdrawn')
        res = self.approve(version_id, expect=409)
        self.assertEqual(res['code'], 'not_pending')

    def test_deactivate_withdraws_the_pending_change_and_only_an_approver_reactivates(self):
        entry = self.approved_entry()
        self.propose(entry.pk, base_reception_value='1300.00', max_reception_value='1500.00')
        res = self.api(self.editor).post(f'{URL}{entry.pk}/deactivate/', {}, format='json')
        self.assertEqual(res.status_code, 400)
        res = self.api(self.editor).post(f'{URL}{entry.pk}/deactivate/', {'reason': 'Ya no se recibe'},
                                         format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(res.json()['is_active'])
        self.assertIsNone(res.json()['current'])
        self.assertIsNone(res.json()['pending'])
        entry.refresh_from_db()
        self.assertIsNone(tariff.effective_version(entry))
        self.propose(entry.pk, expect=409, base_reception_value='1000.00', max_reception_value='1100.00')
        res = self.api(self.editor).post(f'{URL}{entry.pk}/reactivate/', {'reason': 'Vuelve'}, format='json')
        self.assertEqual(res.status_code, 403)
        res = self.api(self.approver).post(f'{URL}{entry.pk}/reactivate/', {'reason': 'Vuelve'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['current']['number'], 1)

    def test_history_keeps_every_version_with_who_and_when(self):
        entry = self.approved_entry()
        self.propose(entry.pk, base_reception_value='1100.00', max_reception_value='1300.00')
        res = self.api(self.reader).get(f'{URL}{entry.pk}/')
        self.assertEqual(res.status_code, 200)
        versions = res.json()['versions']
        self.assertEqual([v['number'] for v in versions], [2, 1])
        self.assertEqual(versions[0]['previous_number'], 1)
        self.assertEqual(versions[1]['proposed_by'], 'tiv_editor')
        self.assertEqual(versions[1]['decided_by'], 'tiv_jefe')
        self.assertIsNotNone(versions[1]['decided_at'])
        self.assertEqual(res.json()['actions']['propose'], False)   # the reader may not

    def test_margin_below_target_is_a_warning_not_a_refusal(self):
        entry = self.create(resale_value='1600.00')        # 1600 - 150 - 1400 = 50 < 300
        self.assertEqual(entry['pending']['warnings'], ['margin_below_target'])


class TariffValidityTest(TariffBase):
    def test_a_future_price_takes_over_on_its_day_and_an_ended_one_stops(self):
        entry = self.approved_entry(valid_until=(self.today + timedelta(days=9)).isoformat())
        later = self.today + timedelta(days=5)
        version = self.propose(entry.pk, base_reception_value='900.00', max_reception_value='1000.00',
                               valid_from=later.isoformat())['version']
        # No end where v1 had one: a longer validity is relevant.
        self.assertEqual([r['code'] for r in version['approval_reasons']], ['validity_extended'])
        self.approve(version['id'])
        self.assertEqual(tariff.effective_version(entry, on=self.today).number, 1)
        self.assertEqual(tariff.effective_version(entry, on=later).number, 2)
        # v2 has no end; v1 ended on day 9: still v2 after it.
        self.assertEqual(tariff.effective_version(entry, on=self.today + timedelta(days=20)).number, 2)

    def test_without_a_newer_price_the_entry_has_none_after_its_end(self):
        entry = self.approved_entry(valid_until=(self.today + timedelta(days=2)).isoformat())
        self.assertIsNone(tariff.effective_version(entry, on=self.today + timedelta(days=3)))

    def test_today_is_the_companys_calendar(self):
        CompanySettings.objects.update_or_create(company=self.company, defaults={'timezone': 'Asia/Tokyo'})
        self.company.refresh_from_db()
        # 20:00 in Lima on the 10th is already the 11th in Tokyo.
        instant = datetime(2026, 12, 10, 20, 0, tzinfo=ZoneInfo('America/Lima'))
        self.assertEqual(tariff.company_today(self.company, now=instant), date(2026, 12, 11))
        CompanySettings.objects.filter(company=self.company).update(timezone='Not/AZone')
        self.company.refresh_from_db()
        self.assertEqual(tariff.company_today(self.company, now=instant),
                         timezone.localdate(instant, timezone.get_default_timezone()))


# ---------------------------------------------------------------------------
# API: permissions, isolation, search
# ---------------------------------------------------------------------------

class TariffPermissionsTest(TariffBase):
    def test_each_door_asks_its_capability(self):
        entry = self.approved_entry()
        pending = self.propose(entry.pk, base_reception_value='1300.00', max_reception_value='1500.00')['version']
        cases = [
            (self.nobody, 'get', URL, None, 403),
            (self.nobody, 'get', f'{URL}{entry.pk}/', None, 403),
            (self.nobody, 'get', f'{URL}export/', None, 403),
            (self.nobody, 'get', '/api/admin/trade-in/policy/', None, 403),
            (self.reader, 'post', URL, body(model_name='Otro'), 403),
            (self.reader, 'post', f'{URL}{entry.pk}/versions/', {'base_reception_value': '1'}, 403),
            (self.reader, 'patch', f'{URL}{entry.pk}/', {'product_model': None}, 403),
            (self.reader, 'post', f'{URL}{entry.pk}/deactivate/', {'reason': 'x'}, 403),
            (self.editor, 'post', f'{URL}versions/{pending["id"]}/approve/', {}, 403),
            (self.editor, 'post', f'{URL}versions/{pending["id"]}/reject/', {'note': 'x'}, 403),
            (self.editor, 'put', '/api/admin/trade-in/policy/', {'tariff_approval_mode': 'all'}, 403),
            (self.approver, 'post', f'{URL}{entry.pk}/versions/', {'base_reception_value': '1'}, 403),
        ]
        for user, method, url, data, expected in cases:
            with self.subTest(user=user.username, method=method, url=url):
                res = getattr(self.api(user), method)(url, data, format='json')
                self.assertEqual(res.status_code, expected, res.content)
        self.assertEqual(TradeInTariffVersion.objects.get(pk=pending['id']).status, 'pending')
        self.assertTrue(TradeInTariffEntry.objects.get(pk=entry.pk).is_active)

    def test_the_actions_say_what_the_server_allows(self):
        entry = self.create()
        actions = self.api(self.approver).get(f'{URL}{entry["id"]}/').json()['actions']
        self.assertTrue(actions['approve'])
        self.assertFalse(actions['propose'])
        actions = self.api(self.editor).get(f'{URL}{entry["id"]}/').json()['actions']
        self.assertFalse(actions['approve'])
        self.assertTrue(actions['withdraw'])


class TariffIsolationTest(TariffBase):
    def test_another_companys_tariff_does_not_exist_for_you(self):
        entry = self.create()
        theirs = self.api(self.stranger)
        self.assertEqual(theirs.get(URL).json()['count'], 0)
        for method, url, data in (
            ('get', f'{URL}{entry["id"]}/', None),
            ('patch', f'{URL}{entry["id"]}/', {'product_model': None}),
            ('post', f'{URL}{entry["id"]}/versions/', {'base_reception_value': '1', 'max_reception_value': '2'}),
            ('post', f'{URL}{entry["id"]}/deactivate/', {'reason': 'x'}),
            ('post', f'{URL}versions/{entry["pending"]["id"]}/approve/', {'note': 'x'}),
            ('post', f'{URL}versions/{entry["pending"]["id"]}/reject/', {'note': 'x'}),
            ('post', f'{URL}versions/{entry["pending"]["id"]}/withdraw/', {}),
        ):
            with self.subTest(method=method, url=url):
                res = getattr(theirs, method)(url, data, format='json')
                self.assertEqual(res.status_code, 404, res.content)
        self.assertEqual(TradeInTariffVersion.objects.get(pk=entry['pending']['id']).status, 'pending')
        # The same device may live in both tariffs, with different money.
        res = theirs.post(URL, body(base_reception_value='10.00', max_reception_value='20.00'), format='json')
        self.assertEqual(res.status_code, 201, res.content)

    def test_a_company_param_never_widens_access(self):
        self.create()
        res = self.api(self.stranger).get(URL, {'company': self.company.pk})
        self.assertEqual(res.status_code, 404)

    def test_linking_another_companys_catalogue_model_answers_like_a_missing_one(self):
        entry = self.create()
        foreign = ProductModel.objects.create(company=self.other, name='Ajeno', slug='ajeno')
        res = self.api(self.editor).patch(f'{URL}{entry["id"]}/', {'product_model': foreign.pk}, format='json')
        self.assertEqual(res.status_code, 404)
        own = ProductModel.objects.create(company=self.company, name='Modelo X 12', slug='modelo-x-12')
        res = self.api(self.editor).patch(f'{URL}{entry["id"]}/', {'product_model': own.pk}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['product_model_name'], 'Modelo X 12')
        res = self.api(self.editor).patch(f'{URL}{entry["id"]}/', {'brand': 'Otra'}, format='json')
        self.assertEqual(res.json()['code'], 'identity_fixed')


class TariffSearchTest(TariffBase):
    def setUp(self):
        super().setUp()
        self.create(brand='Marca Uno', model_name='Modelo X 12', capacity='128 GB')
        self.create(brand='Marca Uno', model_name='Modelo X 12', capacity='256 GB')
        self.create(brand='Marca Dos', model_name='Serie S 5', capacity='64 GB', sim='Dual SIM', region='LA')
        gone = self.create(brand='Marca Dos', model_name='Serie S 4', capacity='64 GB')
        self.api(self.editor).post(f'{URL}{gone["id"]}/deactivate/', {'reason': 'x'}, format='json')

    def names(self, **params):
        res = self.api(self.reader).get(URL, params)
        self.assertEqual(res.status_code, 200, res.content)
        return sorted(f'{r["model_name"]} {r["capacity"]}' for r in res.json()['results'])

    def test_filters_by_brand_model_capacity_sim_region_state_and_words(self):
        self.assertEqual(self.names(brand='marca uno'), ['Modelo X 12 128 GB', 'Modelo X 12 256 GB'])
        self.assertEqual(self.names(model='serie'), ['Serie S 5 64 GB'])
        self.assertEqual(self.names(capacity='256 gb'), ['Modelo X 12 256 GB'])
        self.assertEqual(self.names(sim='dual sim', region='la'), ['Serie S 5 64 GB'])
        self.assertEqual(self.names(active='false'), ['Serie S 4 64 GB'])
        self.assertEqual(len(self.names(active='all')), 4)
        self.assertEqual(self.names(q='dos 64'), ['Serie S 5 64 GB'])
        self.assertEqual(len(self.names(pending='true')), 3)
        res = self.api(self.reader).get(URL)
        self.assertEqual(res.json()['pending_count'], 3)
        self.assertEqual(res.json()['brands'], ['Marca Dos', 'Marca Uno'])
        self.assertEqual(self.api(self.reader).get(URL, {'active': 'quizas'}).status_code, 400)


# ---------------------------------------------------------------------------
# Excel both ways
# ---------------------------------------------------------------------------

def workbook(rows, headers=None):
    import openpyxl

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = 'Tarifario'
    sheet.append(headers or [title for title, _ in tariff_io.IMPORT_COLUMNS])
    for row in rows:
        sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def row(brand='Marca Uno', model='Modelo X 12', capacity='128 GB', base=1200, max_=1400, **over):
    values = {'brand': brand, 'model_name': model, 'capacity': capacity, 'sim': 'eSIM', 'region': '',
              'base_reception_value': base, 'refurbishment_cost': None, 'resale_value': None, 'target_margin': None,
              'max_reception_value': max_, 'valid_from': None, 'valid_until': None, 'reason': 'Carga'}
    values.update(over)
    return [values[field] for _, field in tariff_io.IMPORT_COLUMNS]


class TariffExcelTest(TariffBase):
    def upload(self, data, user=None, expect=201, name='tarifario.xlsx'):
        file = SimpleUploadedFile(name, data, content_type=tariff_io_xlsx())
        res = self.api(user or self.editor).post(f'{URL}import/preview/', {'file': file}, format='multipart')
        self.assertEqual(res.status_code, expect, res.content)
        return res.json()

    def apply(self, batch_id, user=None, expect=200):
        res = self.api(user or self.editor).post(f'{URL}import/{batch_id}/apply/', {}, format='json')
        self.assertEqual(res.status_code, expect, res.content)
        return res.json()

    def test_preview_writes_nothing_and_apply_follows_the_same_approval_rules(self):
        entry = self.approved_entry()
        data = workbook([
            row(base=1100, max_=1300),                                    # lower: approved by policy
            row(capacity='256 GB', base='S/ 1,500.00', max_='1,700.00'),  # new: pending
            row(brand='Marca Dos', model='Serie S 5', capacity='64 GB', base=500, max_=600, sim='Dual SIM'),
        ])
        before = TradeInTariffVersion.objects.count()
        preview = self.upload(data)
        self.assertEqual(TradeInTariffVersion.objects.count(), before)
        self.assertEqual(preview['summary']['create'], 2)
        self.assertEqual(preview['summary']['update'], 1)
        self.assertEqual(preview['summary']['needs_approval'], 2)
        self.assertEqual([r['action'] for r in preview['rows']], ['update', 'create', 'create'])
        self.assertEqual(len(preview['file_sha256']), 64)
        result = self.apply(preview['id'])['result']
        self.assertEqual(result, {'created': 2, 'updated': 1, 'approved_by_policy': 1, 'pending': 2})
        self.assertEqual(tariff.effective_version(entry).base_reception_value, D('1100.00'))
        created = TradeInTariffEntry.objects.get(company=self.company, capacity='256 GB')
        version = tariff.pending_version(created)
        self.assertEqual((version.base_reception_value, version.source, version.import_batch_id),
                         (D('1500.00'), 'import', preview['id']))
        self.assertEqual(self.apply(preview['id'], expect=409)['code'], 'already_applied')
        self.assertTrue(AdminAuditLog.objects.filter(action='trade_in_tariff_import_applied',
                                                     company=self.company).exists())

    def test_a_file_with_any_error_is_not_applied(self):
        data = workbook([
            row(),
            row(base=1500, max_=1400),                       # base above max
            row(capacity='64 GB', base=-1),                  # negative
            row(capacity='512 GB', base='5,500'),            # ambiguous amount
            row(capacity='1 TB', valid_from='31/02/2027'),   # not a date
            row(),                                           # duplicated in the file
            row(capacity='32 GB', base='=1+1'),              # a formula
        ])
        preview = self.upload(data)
        actions = [r['action'] for r in preview['rows']]
        self.assertEqual(actions, ['create'] + ['error'] * 6)
        self.assertEqual(self.apply(preview['id'], expect=400)['code'], 'preview_has_errors')
        self.assertFalse(TradeInTariffEntry.objects.exists())

    def test_a_preview_the_tariff_moved_away_from_or_too_old_is_refused(self):
        entry = self.approved_entry()
        preview = self.upload(workbook([row(base=1100, max_=1300)]))
        self.propose(entry.pk, base_reception_value='1000.00', max_reception_value='1200.00')
        self.assertEqual(self.apply(preview['id'], expect=409)['code'], 'preview_stale')
        fresh = self.upload(workbook([row(base=900, max_=1000)]))
        later = timezone.now() + timedelta(hours=2)
        with mock.patch('store.import_freshness.timezone.now', return_value=later):
            self.assertEqual(self.apply(fresh['id'], expect=409)['code'], 'preview_expired')
        self.assertEqual(tariff.effective_version(entry).base_reception_value, D('1000.00'))

    def test_the_export_is_the_template_and_reimporting_it_changes_nothing(self):
        self.approved_entry()
        self.create(capacity='256 GB', model_name='=Modelo X')   # pending, and a name that looks like a formula
        res = self.api(self.reader).get(f'{URL}export/', {'active': 'all'})
        self.assertEqual(res.status_code, 200)
        self.assertIn('attachment', res['Content-Disposition'])
        import openpyxl

        sheet = openpyxl.load_workbook(io.BytesIO(res.content))['Tarifario']
        headers = [cell.value for cell in sheet[1]]
        self.assertEqual(headers[:13], [title for title, _ in tariff_io.IMPORT_COLUMNS])
        self.assertIn('Aprobación', headers)
        self.assertEqual(sheet.max_row, 3)
        self.assertNotIn('f', [c.data_type for c in sheet['B']])
        preview = self.upload(res.content)
        self.assertEqual([r['action'] for r in preview['rows']], ['unchanged', 'unchanged'], preview['rows'])

    def test_a_reader_may_export_but_not_import_and_other_companies_imports_do_not_exist(self):
        self.upload(workbook([row()]), user=self.reader, expect=403)
        preview = self.upload(workbook([row()]))
        self.apply(preview['id'], user=self.stranger, expect=404)
        self.apply(preview['id'], user=self.reader, expect=403)
        self.assertFalse(TradeInTariffImport.objects.filter(status='applied').exists())

    def test_a_file_over_the_tariff_cap_is_refused_before_it_is_read(self):
        from store.trade_in_tariff_views import MAX_TARIFF_UPLOAD_BYTES

        with mock.patch('store.trade_in_tariff_views.MAX_TARIFF_UPLOAD_BYTES', 100):
            res = self.upload(workbook([row()]), expect=413)
        self.assertEqual(res['code'], 'file_too_large')
        self.assertFalse(TradeInTariffImport.objects.exists())
        self.assertLess(MAX_TARIFF_UPLOAD_BYTES, 1024 * 1024)

    def test_a_missing_required_column_or_a_macro_file_is_refused(self):
        res = self.upload(workbook([['Marca Uno', 'Modelo']], headers=['Marca', 'Modelo']), expect=400)
        self.assertEqual(res['code'], 'missing_columns')
        self.upload(b'not a workbook', expect=400, name='tarifario.xlsm')


def tariff_io_xlsx():
    return 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


# ---------------------------------------------------------------------------
# Guards: what the tariff never does
# ---------------------------------------------------------------------------

class TariffNeverMovesMoneyOrStockTest(TariffBase):
    def test_no_order_payment_stock_or_unit_is_ever_written(self):
        counts = (Order.objects.count(), PaymentTransaction.objects.count(), StockMovement.objects.count(),
                  StockUnit.objects.count())
        entry = self.approved_entry()
        self.propose(entry.pk, base_reception_value='1100.00', max_reception_value='1300.00')
        self.api(self.editor).post(f'{URL}{entry.pk}/deactivate/', {'reason': 'x'}, format='json')
        self.assertEqual(counts, (Order.objects.count(), PaymentTransaction.objects.count(),
                                  StockMovement.objects.count(), StockUnit.objects.count()))

    def test_the_tables_point_at_nothing_that_charges_or_stores(self):
        forbidden = {'Order', 'PaymentTransaction', 'StockMovement', 'StockUnit', 'BranchStock', 'Customer',
                     'SalesNote', 'FiscalDocument'}
        for model in (TradeInTariffEntry, TradeInTariffVersion, TradeInTariffImport, TradeInPolicy):
            for field in model._meta.get_fields():
                related = getattr(field, 'related_model', None)
                if related is not None and field.concrete:
                    self.assertNotIn(related.__name__, forbidden, f'{model.__name__}.{field.name}')

    def test_every_write_is_audited_with_the_company(self):
        entry = self.approved_entry()
        self.propose(entry.pk, base_reception_value='1100.00', max_reception_value='1300.00')
        actions = set(AdminAuditLog.objects.filter(company=self.company).values_list('action', flat=True))
        self.assertTrue({'trade_in_tariff_entry_created', 'trade_in_tariff_version_proposed',
                         'trade_in_tariff_version_approved'} <= actions)
        self.assertFalse(AdminAuditLog.objects.filter(action__startswith='trade_in_', company__isnull=True).exists())

    def test_the_engine_names_no_maker_model_or_shop(self):
        """SaaS: brands, models, prices and policies are the company's data, never the code's."""
        root = pathlib.Path(tariff.__file__).parent
        for name in ('trade_in_tariff.py', 'trade_in_tariff_io.py', 'trade_in_tariff_views.py'):
            text = (root / name).read_text(encoding='utf-8').lower()
            for word in ('iphone', 'apple', 'samsung', 'xiaomi', 'motorola', 'black dog', 'blackdog', 'icloud'):
                with self.subTest(file=name, word=word):
                    self.assertNotIn(word, text)
