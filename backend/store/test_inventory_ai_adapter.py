"""
INV-AI-01 — the adapter behind the switch. No real provider and no key exist:
the only provider is a test double. What is pinned here is what any provider
will have to go through: identifiers and contact data never leave the server,
the answer is data of a fixed shape and never an instruction, quotas, budget
and a circuit breaker stop spending, and every call leaves a usage row.
"""
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from store import inventory_ai, inventory_ai_safety as safety
from store.models import InventoryAISettings, InventoryAIUsage
from store.tests import _p2d_member, _saas_company

IMEI = '356938035643809'
TEXT = f'IPH 15 128gb 2300 negro\nNS: F2LXK9ABCD IMEI 1: {IMEI}\nLlamar a Juan 987654321 juan@mail.test'


class FakeProvider:
    name = 'fake'

    def __init__(self, answer=None, *, fail=False, cost='0.01'):
        self.answer = answer if answer is not None else {'candidates': [
            {'model': 'iPhone 15', 'capacity': '128 GB', 'color': 'Negro', 'condition': 'new',
             'supplier_cost': '2300', 'quantity': None, 'imei': '[IMEI_1]', 'serial': '[SERIE_1]'},
        ]}
        self.fail, self.cost, self.sent = fail, Decimal(cost), []

    def is_available(self):
        return True

    def has_quota(self, company, user):
        return True

    def extract(self, text, *, timeout):
        self.sent.append((text, timeout))
        if self.fail:
            raise TimeoutError('slow')
        return self.answer, self.cost


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _saas_company('Empresa IA', 'empresa-ia', tax_id='20000000081')
        self.user, _ = _p2d_member(self.company, 'ia_buyer', ['company.view', 'products.manage'])
        InventoryAISettings.objects.create(company=self.company, enabled=True)

    def run_with(self, provider, text=TEXT):
        with mock.patch.object(inventory_ai, 'get_provider', return_value=provider):
            return inventory_ai.extract(self.company, self.user, text, requested=True, permitted=True)


class RedactionTest(TestCase):
    def test_imei_serial_phone_and_email_never_leave(self):
        redacted, secrets = safety.redact(TEXT)
        for value in (IMEI, 'F2LXK9ABCD', '987654321', 'juan@mail.test'):
            self.assertNotIn(value, redacted)
        self.assertIn('[IMEI_1]', redacted)
        self.assertEqual(secrets['[IMEI_1]'], IMEI)

    def test_prices_and_capacities_are_kept(self):
        redacted, _ = safety.redact('IPH 17 pro 256gb LLA 4370 blanco · S25fe 8/256 1930 navy')
        for kept in ('4370', '256gb', '8/256', '1930'):
            self.assertIn(kept, redacted)


class AnswerShapeTest(TestCase):
    def test_only_known_fields_of_known_types_survive(self):
        rows = safety.clean_candidates({'candidates': [
            {'model': 'iPhone 15', 'supplier_cost': '2300', 'quantity': 2, 'delete_all': True, 'price': '9999'},
        ]})
        self.assertEqual(rows, [{'model': 'iPhone 15', 'supplier_cost': '2300', 'quantity': 2}])

    def test_an_answer_that_is_not_the_schema_is_refused(self):
        for bad in (None, [], 'texto', {'candidates': 'x'}, {'candidates': [1]}, {'action': 'set_price'}):
            with self.subTest(bad=bad), self.assertRaises(safety.BadAnswer):
                safety.clean_candidates(bad)

    def test_a_quantity_is_a_whole_number_or_unknown_never_invented(self):
        rows = safety.clean_candidates({'candidates': [{'model': 'A', 'quantity': '3'}, {'model': 'B', 'quantity': -1},
                                                       {'model': 'C', 'quantity': 1.5}, {'model': 'D'}]})
        self.assertEqual([row.get('quantity') for row in rows], [None, None, None, None])

    def test_more_than_the_limit_is_cut_and_said(self):
        rows = safety.clean_candidates({'candidates': [{'model': f'M{i}'} for i in range(safety.MAX_CANDIDATES + 5)]})
        self.assertEqual(len(rows), safety.MAX_CANDIDATES)


@override_settings(INVENTORY_AI_ENABLED=True)
class ExtractTest(_Base):
    def test_identifiers_are_sent_redacted_and_put_back_only_here(self):
        provider = FakeProvider()
        result = self.run_with(provider)
        sent, timeout = provider.sent[0]
        self.assertNotIn(IMEI, sent)
        self.assertEqual(timeout, safety.TIMEOUT_SECONDS)
        self.assertEqual(result.candidates[0]['imei'], IMEI)
        self.assertEqual(result.candidates[0]['supplier_cost'], '2300')
        self.assertTrue(result.used)

    def test_every_call_leaves_a_usage_row_without_the_text(self):
        self.run_with(FakeProvider(cost='0.02'))
        usage = InventoryAIUsage.objects.get()
        self.assertEqual((usage.company, usage.user, usage.ok, usage.cost_usd), (self.company, self.user, True,
                                                                                  Decimal('0.02')))
        self.assertEqual(usage.chars, len(TEXT))
        self.assertFalse(hasattr(usage, 'text'))

    def test_a_failing_provider_falls_back_and_is_recorded(self):
        result = self.run_with(FakeProvider(fail=True))
        self.assertEqual((result.used, result.reason, result.candidates), (False, 'failed', []))
        self.assertFalse(InventoryAIUsage.objects.get().ok)

    def test_a_bad_answer_is_not_used(self):
        result = self.run_with(FakeProvider(answer={'action': 'delete'}))
        self.assertEqual((result.used, result.reason), (False, 'bad_answer'))

    def test_too_much_text_is_not_sent(self):
        provider = FakeProvider()
        result = self.run_with(provider, text='x' * (safety.MAX_CHARS + 1))
        self.assertEqual((result.used, result.reason, provider.sent), (False, 'too_long', []))

    @override_settings(INVENTORY_AI_ENABLED=False)
    def test_with_the_switch_off_nothing_is_sent(self):
        provider = FakeProvider()
        result = self.run_with(provider)
        self.assertEqual((result.used, result.reason, provider.sent), (False, 'global_off', []))
        self.assertFalse(InventoryAIUsage.objects.exists())


@override_settings(INVENTORY_AI_ENABLED=True, INVENTORY_AI_USER_PER_MINUTE=2, INVENTORY_AI_USER_PER_DAY=20,
                   INVENTORY_AI_MONTHLY_BUDGET_USD='5.00')
class QuotaTest(_Base):
    def usage(self, *, minutes_ago=0, ok=True, cost='0.01', user=None):
        row = InventoryAIUsage.objects.create(company=self.company, user=user or self.user, chars=10, ok=ok,
                                              cost_usd=Decimal(cost))
        InventoryAIUsage.objects.filter(pk=row.pk).update(at=timezone.now() - timedelta(minutes=minutes_ago))

    def test_two_a_minute_per_person(self):
        self.usage(); self.usage()
        self.assertFalse(inventory_ai.within_quota(self.company, self.user))
        other, _ = _p2d_member(self.company, 'ia_other', ['company.view'])
        self.assertTrue(inventory_ai.within_quota(self.company, other))

    def test_twenty_a_day_per_person(self):
        for minutes in range(20):
            self.usage(minutes_ago=5 + minutes * 10)
        self.assertFalse(inventory_ai.within_quota(self.company, self.user))

    def test_the_monthly_budget_cuts_spending(self):
        self.usage(minutes_ago=90, cost='4.99')
        self.assertTrue(inventory_ai.within_quota(self.company, self.user))
        self.usage(minutes_ago=80, cost='0.01')
        self.assertFalse(inventory_ai.within_quota(self.company, self.user))

    def test_the_budget_alert_levels(self):
        self.usage(minutes_ago=90, cost='4.00')
        self.assertEqual(inventory_ai.budget_status(self.company)['alert'], 80)

    def test_three_failures_in_a_row_open_the_breaker(self):
        for minutes in (3, 2, 1):
            self.usage(minutes_ago=minutes, ok=False)
        self.assertTrue(inventory_ai.breaker_open())
        provider = FakeProvider()
        result = self.run_with(provider)
        self.assertEqual((result.reason, provider.sent), ('unavailable', []))


class ProviderRegistryTest(TestCase):
    def test_no_provider_by_default_and_no_real_one_is_built(self):
        self.assertIsNone(inventory_ai.get_provider())
        with override_settings(INVENTORY_AI_PROVIDER='anthropic'):
            self.assertIsNone(inventory_ai.get_provider())
