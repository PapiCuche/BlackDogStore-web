"""
INV-AI-01 — the adapter behind the switch. No real provider and no key exist:
the only provider is a test double. What is pinned here is what any provider
will have to go through: identifiers and contact data never leave the server,
the answer is data of a fixed shape and never an instruction, quotas, budget
and a circuit breaker stop spending, and every call leaves a usage row.
"""
import re
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from store import inventory_ai, inventory_ai_safety as safety
from store.models import InventoryAISettings, InventoryAIUsage
from store.tests import _p2d_member, _saas_company

IMEI = '356938035643809'
_REAL_GET_PROVIDER = inventory_ai.get_provider
TEXT = f'IPH 15 128gb 2300 negro\nNS: F2LXK9ABCD IMEI 1: {IMEI}\nLlamar a Juan 987654321 juan@mail.test'


ITEM = {'kind': 'device', 'line': 'new', 'family': 'iPhone', 'model_words': ['15'], 'capacity_gb': 128, 'colour': 'negro'}


def structured(*items):
    from store import inventory_ai_payload

    return inventory_ai_payload.build_request(None, list(items) or [ITEM])


class FakeProvider:
    """A provider for the tests only: answers one proposal per item it was given."""
    name = 'fake'

    def __init__(self, answer=None, *, fail=False, cost='0.01'):
        self.answer, self.fail, self.cost, self.sent = answer, fail, Decimal(cost), []

    def is_available(self):
        return True

    def has_quota(self, company, user):
        return True

    def propose(self, payload, *, timeout):
        self.sent.append((payload, timeout))
        if self.fail:
            raise TimeoutError('slow')
        if self.answer is not None:
            return self.answer, self.cost
        return {'results': [{'ref': item['ref'], 'model_words': item.get('model_words'), 'variant_index': None,
                             'confidence': 'high'} for item in payload['items']]}, self.cost


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _saas_company('Empresa IA', 'empresa-ia', tax_id='20000000081')
        self.user, _ = _p2d_member(self.company, 'ia_buyer', ['company.view', 'products.manage'])
        InventoryAISettings.objects.create(company=self.company, enabled=True)

    def run_with(self, provider, request=None):
        with mock.patch.object(inventory_ai, 'get_provider', return_value=provider):
            return inventory_ai.propose(self.company, self.user, request or structured(),
                                        requested=True, permitted=True)


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


class RedactionAuditTest(TestCase):
    """Review of #148: identifiers are found however they are written, not only as 15 digits in a row."""

    def assert_gone(self, text, *values):
        redacted, secrets = safety.redact(text)
        for value in values:
            self.assertNotIn(value.lower(), redacted.lower(), redacted)
        return redacted, secrets

    def test_an_imei_written_with_spaces_dashes_or_dots(self):
        for written in ('35 693803 564380 9', '35-693803-564380-9', '3569 3803 5643 809', '35.693803.564380.9',
                        'IMEI1:356938035643809', 'imei 2 = 356938035643809', '35693803564380'):
            with self.subTest(written=written):
                redacted, _ = self.assert_gone(f'iPhone 13 {written} azul')
                self.assertIn('[IMEI_1]', redacted)
                self.assertNotIn('3569380356438', re.sub(r'\D', '', redacted))

    def test_serials_in_whatsapp_shapes(self):
        for written in ('NS: F2LXK9ABCD', 'N/S F2LXK9ABCD', 'S/N:F2LXK9ABCD', 'Serie F2LXK9ABCD',
                        'serial: f2lxk9abcd', 'Número de serie: F2LXK9ABCD', 'SN F2LXK9ABCD'):
            with self.subTest(written=written):
                redacted, _ = self.assert_gone(f'iPhone 13 128 GB {written} batería 93%', 'F2LXK9ABCD')
                self.assertIn('128 GB', redacted)
                self.assertIn('93%', redacted)

    def test_phones_in_peruvian_shapes(self):
        for written in ('987654321', '987 654 321', '987-654-321', '+51 987654321', '+51 987 654 321', '51987654321'):
            with self.subTest(written=written):
                self.assert_gone(f'Escribir al {written} hoy', '654')
                self.assert_gone(f'Precio 2300 o al {written}', '654')

    def test_names_after_the_words_that_introduce_them(self):
        redacted, _ = self.assert_gone('Cliente: Juan Pérez Quispe · Contacto Rosa Huamán · Sr. Carlos Díaz',
                                       'Juan', 'Pérez', 'Rosa', 'Huamán', 'Carlos', 'Díaz')
        self.assertIn('[DATO_PERSONAL]', redacted)

    def test_a_document_number_is_not_sent(self):
        self.assert_gone('DNI 45678912 · RUC 20123456789', '45678912', '20123456789')

    def test_commercial_words_and_prices_survive(self):
        text = 'IPH 17 pro chip 256gb LLA 4370 blanco\nS25fe 8/256 1930 navy\nIPH 15 128gb 2300 negro'
        redacted, secrets = safety.redact(text)
        self.assertEqual((redacted, secrets), (text, {}))


@override_settings(INVENTORY_AI_ENABLED=True)
class ProposeTest(_Base):
    def test_the_provider_receives_exactly_the_structured_request(self):
        provider = FakeProvider()
        request = structured()
        result = self.run_with(provider, request)
        sent, timeout = provider.sent[0]
        self.assertEqual(sent, request.payload)
        self.assertEqual(timeout, safety.TIMEOUT_SECONDS)
        self.assertEqual((result.used, result.proposals[0]['index'], result.proposals[0]['model_words']),
                         (True, 0, ['15']))

    def test_free_text_has_no_way_in(self):
        for not_structured in (TEXT, {'items': [{'ref': 'x', 'family': TEXT}]}, [ITEM]):
            with self.subTest(value=str(not_structured)[:30]), self.assertRaises(TypeError):
                inventory_ai.propose(self.company, self.user, not_structured, requested=True, permitted=True)

    def test_a_request_the_old_redaction_would_change_is_never_sent(self):
        from store import inventory_ai_payload

        smuggled = inventory_ai_payload.StructuredRequest(  # forged: not sealed, and its value is not vocabulary
            {'schema_version': 1, 'items': [{'ref': 'c1-aaaaaa', 'family': f'iPhone IMEI {IMEI}'}]}, {'c1-aaaaaa': 0})
        provider = FakeProvider()
        self.assertEqual(self.run_with(provider, smuggled).reason, 'unsafe_request')
        self.assertEqual(provider.sent, [])

    def test_every_call_leaves_a_usage_row_without_its_content(self):
        self.run_with(FakeProvider(cost='0.02'))
        usage = InventoryAIUsage.objects.get()
        self.assertEqual((usage.company, usage.user, usage.ok, usage.cost_usd), (self.company, self.user, True,
                                                                                  Decimal('0.05')))  # the reserve
        self.assertFalse(hasattr(usage, 'text'))

    def test_a_failing_provider_falls_back_and_is_recorded(self):
        result = self.run_with(FakeProvider(fail=True))
        self.assertEqual((result.used, result.reason, result.proposals), (False, 'failed', []))
        self.assertFalse(InventoryAIUsage.objects.get().ok)

    def test_a_bad_answer_is_not_used(self):
        result = self.run_with(FakeProvider(answer={'action': 'delete'}))
        self.assertEqual((result.used, result.reason), (False, 'bad_answer'))

    @override_settings(INVENTORY_AI_ENABLED=False)
    def test_with_the_switch_off_nothing_is_sent(self):
        provider = FakeProvider()
        result = self.run_with(provider)
        self.assertEqual((result.used, result.reason, provider.sent), (False, 'global_off', []))
        self.assertFalse(InventoryAIUsage.objects.exists())


@override_settings(INVENTORY_AI_ENABLED=True, INVENTORY_AI_USER_PER_MINUTE=2, INVENTORY_AI_USER_PER_DAY=20, INVENTORY_AI_MAX_COST_PER_CALL='0.01',
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


@override_settings(INVENTORY_AI_ENABLED=True, INVENTORY_AI_MONTHLY_BUDGET_USD='0.10', INVENTORY_AI_MAX_COST_PER_CALL='0.05',
                   INVENTORY_AI_USER_PER_MINUTE=10)
class BudgetCutTest(_Base):
    def test_a_call_reserves_its_maximum_so_the_budget_is_a_real_cut(self):
        # Each call counts at least its reserve (0.05), whatever the provider declares (review: under-reporting).
        self.assertTrue(self.run_with(FakeProvider(cost='0.01')).used)
        self.assertTrue(self.run_with(FakeProvider(cost='0.01')).used)
        result = self.run_with(FakeProvider(cost='0.01'))
        self.assertEqual(result.reason, 'quota_exhausted')
        self.assertEqual(sum(InventoryAIUsage.objects.values_list('cost_usd', flat=True)), Decimal('0.10'))

    def test_another_companys_spending_is_not_this_ones(self):
        other = _saas_company('Otra IA', 'otra-ia', tax_id='20000000082')
        InventoryAIUsage.objects.create(company=other, user=None, chars=1, ok=True, cost_usd=Decimal('9'))
        self.assertTrue(self.run_with(FakeProvider()).used)

    def test_a_call_in_progress_counts_its_reserve(self):
        InventoryAIUsage.objects.create(company=self.company, user=self.user, chars=1, ok=None,
                                        cost_usd=Decimal('0.08'))
        self.assertEqual(self.run_with(FakeProvider()).reason, 'quota_exhausted')


class ProviderRegistryTest(TestCase):
    def test_no_provider_by_default_and_no_real_one_is_built(self):
        self.assertIsNone(inventory_ai.get_provider())
        with override_settings(INVENTORY_AI_PROVIDER='anthropic'):
            self.assertIsNone(inventory_ai.get_provider())


@override_settings(INVENTORY_AI_ENABLED=True, INVENTORY_AI_MONTHLY_BUDGET_USD='0.05', INVENTORY_AI_MAX_COST_PER_CALL='0.05',
                   INVENTORY_AI_USER_PER_MINUTE=10)
class ConcurrentQuotaTest(TransactionTestCase):
    """Two calls at once with room for one: exactly one goes out (the reserve is taken under a lock)."""

    def test_two_calls_at_once_spend_the_last_of_the_budget_once(self):
        import threading
        import time

        from django.db import connection

        company = _saas_company('Empresa Carrera', 'empresa-carrera-ia', tax_id='20000000083')
        user, _ = _p2d_member(company, 'ia_race', ['company.view', 'products.manage'])
        InventoryAISettings.objects.create(company=company, enabled=True)

        class Slow(FakeProvider):
            def propose(self, payload, *, timeout):
                time.sleep(0.3)
                return super().propose(payload, timeout=timeout)

        barrier, outcomes = threading.Barrier(2), []

        def run():
            try:
                barrier.wait(timeout=5)
                outcomes.append(inventory_ai.propose(company, user, structured(), requested=True, permitted=True))
            finally:
                connection.close()

        # Patched ONCE, here: two threads patching and unpatching the same attribute can leave the fake in
        # place for every later test of the process (one restores the other's mock as the «original»).
        with mock.patch.object(inventory_ai, 'get_provider', return_value=Slow()):
            threads = [threading.Thread(target=run) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)
        self.assertIs(inventory_ai.get_provider, _REAL_GET_PROVIDER)
        self.assertEqual(sorted(result.used for result in outcomes), [False, True], outcomes)
        self.assertEqual(InventoryAIUsage.objects.filter(company=company).count(), 1)

    @override_settings(INVENTORY_AI_MONTHLY_BUDGET_USD='5.00', INVENTORY_AI_USER_PER_MINUTE=1)
    def test_two_calls_at_once_by_one_person_with_one_left_this_minute_go_out_once(self):
        """Per-person quota, not the budget, is the limit here: the row locks taken before counting make it a cut."""
        import threading
        import time

        from django.db import connection

        company = _saas_company('Empresa Cuota', 'empresa-cuota-ia', tax_id='20000000084')
        user, _ = _p2d_member(company, 'ia_quota_race', ['company.view', 'products.manage'])
        InventoryAISettings.objects.create(company=company, enabled=True)

        class Slow(FakeProvider):
            def propose(self, payload, *, timeout):
                time.sleep(0.3)
                return super().propose(payload, timeout=timeout)

        barrier, outcomes = threading.Barrier(2), []

        def run():
            try:
                barrier.wait(timeout=5)
                outcomes.append(inventory_ai.propose(company, user, structured(), requested=True, permitted=True))
            finally:
                connection.close()

        # Patched ONCE, here: two threads patching and unpatching the same attribute can leave the fake in
        # place for every later test of the process (one restores the other's mock as the «original»).
        with mock.patch.object(inventory_ai, 'get_provider', return_value=Slow()):
            threads = [threading.Thread(target=run) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)
        self.assertIs(inventory_ai.get_provider, _REAL_GET_PROVIDER)
        self.assertEqual(sorted((r.used, r.reason) for r in outcomes), [(False, 'quota_exhausted'), (True, None)])
        self.assertEqual(InventoryAIUsage.objects.filter(user=user).count(), 1)


class SecondAuditTest(TestCase):
    """Independent review of #148: each text that leaked, pinned."""

    def gone(self, text, *values):
        redacted, secrets = safety.redact(text)
        flat = re.sub(r'\s', '', redacted).lower()
        for value in values:
            self.assertNotIn(re.sub(r'\s', '', value).lower(), flat, redacted)
        return redacted

    def test_imeis_with_unicode_separators(self):
        for text in ('IMEI 49\xa0015420\xa0323751\xa08', 'IMEI 49015420\n323751 8', 'IMEI 49015420/323751/8',
                     'IMEI 35345678​9012345', 'IMEI 49–015420–323751–8', 'imei\t490154203237518'):
            with self.subTest(text=text):
                self.gone(text, '490154203', '0154203237518')

    def test_a_split_fourteen_digit_imei_in_its_usual_groups(self):
        self.gone('equipo 35 345678 901234 negro', '345678901234')

    def test_names_whatever_their_case(self):
        for text in ('cliente juan perez', 'CLIENTE JUAN PEREZ', 'sr. díaz', 'Contacto: rosa huamán'):
            with self.subTest(text=text):
                self.gone(text, text.split()[-1])

    def test_obfuscated_emails(self):
        for text in ('juan [at] gmail.com', 'juan(at)gmail(dot)com', 'juan＠gmail.com', 'juan arroba gmail punto com'):
            with self.subTest(text=text):
                self.gone(text, 'juan')

    def test_phones_with_dots_pairs_links_and_landlines(self):
        for text in ('999.111.222', '999 11 22 33', 'wa.me/51999111222', 'fijo 01 234 5678'):
            with self.subTest(text=text):
                self.gone(text, '111', '1122', '999111222', '2345678')

    def test_serials_with_separators(self):
        self.gone('SN: ABC-12345-XY batería 90%', 'ABC-12345-XY')
        self.gone('S/N: C02 XY 12345', 'C02XY12345')

    def test_dotted_dni_and_a_bare_ruc(self):
        self.gone('DNI 12.345.678', '12.345.678')
        self.gone('factura a 20123456789', '20123456789')

    def test_prices_lists_still_survive(self):
        text = 'precios 1200 1500 1800 2000 2200 · iPhone 13 128GB 3500 · 8/256 1930 · 4370 blanco'
        self.assertEqual(safety.redact(text)[0], text)

@override_settings(INVENTORY_AI_ENABLED=True, INVENTORY_AI_USER_PER_MINUTE=10)
class CostAndSettingsTest(_Base):
    def test_a_negative_or_unreadable_cost_never_lowers_the_spend(self):
        for cost in ('-5', 'NaN', {'x': 1}, 'Infinity'):
            with self.subTest(cost=cost):
                InventoryAIUsage.objects.all().delete()
                result = self.run_with(FakeProvider(cost='0'))  # warm path
                provider = FakeProvider()
                provider.cost = cost
                result = self.run_with(provider)
                self.assertFalse(result.used)
                self.assertEqual(result.reason, 'bad_cost')
                self.assertGreaterEqual(min(InventoryAIUsage.objects.values_list('cost_usd', flat=True)), 0)
                self.assertFalse(InventoryAIUsage.objects.filter(ok__isnull=True).exists())

    def test_a_cost_above_the_reserve_is_recorded_as_it_was(self):
        self.run_with(FakeProvider(cost='0.20'))
        self.assertEqual(InventoryAIUsage.objects.get().cost_usd, Decimal('0.20'))

    def test_a_provider_that_blows_up_leaves_no_open_reserve(self):
        class Boom(FakeProvider):
            def propose(self, payload, *, timeout):
                raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            self.run_with(Boom())
        self.assertFalse(InventoryAIUsage.objects.filter(ok__isnull=True).exists())

    def test_unreadable_budget_settings_fail_closed(self):
        for budget, reserve in (('Infinity', '0.05'), ('nan', '0.05'), ('5', '-1'), ('abc', '0.05')):
            with self.subTest(budget=budget, reserve=reserve), override_settings(
                    INVENTORY_AI_MONTHLY_BUDGET_USD=budget, INVENTORY_AI_MAX_COST_PER_CALL=reserve):
                self.assertFalse(inventory_ai.within_quota(self.company, self.user))

    def test_no_call_without_a_person(self):
        with mock.patch.object(inventory_ai, 'get_provider', return_value=FakeProvider()):
            result = inventory_ai.propose(self.company, None, structured(), requested=True, permitted=True)
        self.assertEqual((result.used, result.reason), (False, 'forbidden'))


class ThirdAuditTest(TestCase):
    """Second independent review of #148: every probed leak, the ReDoS input and the over-redaction limits."""

    def gone(self, text, *values):
        redacted, _ = safety.redact(text)
        flat = re.sub(r'\s', '', redacted).lower()
        for value in values:
            self.assertNotIn(re.sub(r'\s', '', value).lower(), flat, redacted)
        return redacted

    def test_long_unbroken_input_is_read_in_linear_time(self):
        import time

        for text in ('a' * safety.MAX_CHARS, '9' * safety.MAX_CHARS, ('a.' * (safety.MAX_CHARS // 2)),
                     ('ab@' * (safety.MAX_CHARS // 3))):
            start = time.monotonic()
            safety.redact(text)
            self.assertLess(time.monotonic() - start, 2, text[:10])

    def test_invisible_and_combining_marks_do_not_hide_an_imei(self):
        for text in ('IMEI 35\u2063345678901234\u2063 5', 'IMEI 3\u03015345678901234', 'IMEI\u200e 35\u200e3456789012345',
                     'IMEI \u0663\u0665\u0663\u0664\u0665\u0666\u0667\u0668\u0669\u0660\u0661\u0662\u0663\u0664\u0665'):
            with self.subTest(text=text):
                self.gone(text, '345678901', '901234')

    def test_imeis_with_words_or_separators_after_the_label(self):
        for text in ('IMEI del equipo: 3534 5678 9012 345', 'IMEI- 3534 5678 9012 345', 'IMEI_ 3534 5678 9012 345',
                     'IMEI: 3534 / 5678 9012345', 'IMEI 3534 5678 y 9012 345', 'IMEI:\n353456789012345'):
            with self.subTest(text=text):
                self.gone(text, '3534', '9012')

    def test_english_serial_labels(self):
        for text in ('Serial Number: ABC123456', 'Serial No. ABC123456', 'serie: ABC.123.456.789', 'S/N ABC.123.456',
                     'serie: AB-12345', 'serie: ABC12'):
            with self.subTest(text=text):
                self.gone(text, text.split()[-1])

    def test_serie_as_a_word_keeps_the_product(self):
        self.assertIn('Galaxy S25 FE', safety.redact('Serie Galaxy S25 FE 1930')[0])

    def test_names_on_the_next_line_or_long(self):
        for text in ('Cliente:\nJuan Pérez', 'Cliente: JUAN DE LA CRUZ', 'Cliente: Juan-Carlos Pérez',
                     "Cliente: O'Brien Smith", 'Dueño: Juan Pérez'):
            with self.subTest(text=text):
                self.gone(text, text.split()[-1])

    def test_more_e_mail_shapes(self):
        for text in ('juan @gmail.com', 'juan (arroba) gmail.com', 'juan [@] gmail [.] com', 'juan@gmail\u3002com'):
            with self.subTest(text=text):
                self.gone(text, 'juan')

    def test_phones_with_brackets_and_slashes(self):
        for text in ('(987) 654 321', '+51 (987) 654 321', '987/654/321', '(01) 234-5678'):
            with self.subTest(text=text):
                self.gone(text, '654', '2345678')

    def test_documents_with_dots_or_dashes(self):
        for text in ('DNI. 12345678', 'D.N.I 12345678', 'RUC 20-123456789', 'RUC: 20 123 456 789', 'pago 12.345.678'):
            with self.subTest(text=text):
                self.gone(text, '345678', '456789')

    def test_typical_lists_survive(self):
        for text in ('IPH 15 128 2300 negro', 'S25fe 8/256 1930 navy', 'precios 1200 1500 1800',
                     'Cargador 20W 65', 'Samsung A54 128GB 6GB RAM 1500', 'IPH 16 pro max 512gb esim 5890 titanio desierto'):
            with self.subTest(text=text):
                self.assertEqual(safety.redact(text)[0], text)

@override_settings(INVENTORY_AI_ENABLED=True, INVENTORY_AI_USER_PER_MINUTE=10)
class ThirdAuditExtractTest(_Base):
    def test_a_huge_reported_cost_is_refused_not_a_500(self):
        for cost in ('10000', '1E+400'):
            with self.subTest(cost=cost):
                provider = FakeProvider()
                provider.cost = cost
                self.assertEqual(self.run_with(provider).reason, 'bad_cost')

    def test_a_failed_call_counts_its_reserve(self):
        self.run_with(FakeProvider(fail=True))
        self.assertEqual(InventoryAIUsage.objects.get().cost_usd, Decimal('0.05'))

    def test_settings_out_of_range_fail_closed(self):
        for name, value in (('INVENTORY_AI_MAX_COST_PER_CALL', '10000'), ('INVENTORY_AI_USER_PER_MINUTE', 'x'),
                            ('INVENTORY_AI_USER_PER_DAY', None)):
            with self.subTest(name=name), override_settings(**{name: value}):
                self.assertFalse(inventory_ai.within_quota(self.company, self.user))


class FourthAuditTest(TestCase):
    def test_a_personal_line_after_a_lone_imei_label_still_goes_out(self):
        for text in ('IMEI:\nCliente: María Gómez 987654321', 'S/N:\nSr. Juan Pérez'):
            with self.subTest(text=text):
                redacted, _ = safety.redact(text)
                self.assertNotIn('Gómez', redacted)
                self.assertNotIn('Pérez', redacted)

    def test_the_imei_slot_number_never_eats_the_imei(self):
        redacted, secrets = safety.redact('IMEI 123456789012345')
        self.assertEqual(list(secrets.values()), ['123456789012345'])
        self.assertNotIn('1[', redacted)

@override_settings(INVENTORY_AI_ENABLED=True, INVENTORY_AI_USER_PER_MINUTE=10)
class FourthAuditExtractTest(_Base):
    def test_a_zero_budget_or_zero_reserve_is_closed(self):
        for budget, reserve in (('0', '0.05'), ('5', '0')):
            with self.subTest(budget=budget, reserve=reserve), override_settings(
                    INVENTORY_AI_MONTHLY_BUDGET_USD=budget, INVENTORY_AI_MAX_COST_PER_CALL=reserve):
                self.assertFalse(inventory_ai.within_quota(self.company, self.user))

    def test_an_anonymous_user_never_calls(self):
        from django.contrib.auth.models import AnonymousUser

        with mock.patch.object(inventory_ai, 'get_provider', return_value=FakeProvider()):
            result = inventory_ai.propose(self.company, AnonymousUser(), structured(), requested=True, permitted=True)
        self.assertEqual(result.reason, 'forbidden')
