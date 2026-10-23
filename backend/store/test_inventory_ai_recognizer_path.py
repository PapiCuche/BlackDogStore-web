"""
INV-AI-01 × INV-TEXT-02 — the end-to-end proof that what an AI provider could receive is derived ONLY from
what the local recognizer produced, and that none of the sensitive values of a supplier's list travels
(ADR docs/adr-inv-ai-structured.md).

The chain, with nothing invented in between:
  1. Jest (`frontend/__tests__/inventory-ai-recognizer-path.test.ts`) runs the real recognizer
     (`previewSupplierText` + `matchPayload`) on a de-identified WhatsApp list and pins its output in
     `recognizer-output.json`.
  2. Here: each pinned candidate goes through the #142 server cleaner (`text_preview_match.clean`) and the
     catalogue match (`text_preview_match.match`, company-scoped), then `item_from_preview`, `build_request`,
     `payload_is_clean` and `propose`, with a FAKE in-test provider that records what it was given.
No real provider, no key, no network: the registry stays empty and the fake is patched in.
"""
import json
import re
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from store import device_catalog, inventory_ai, inventory_ai_payload as payload, text_preview_match
from store.models import InventoryAISettings, InventoryAIUsage, Product
from store.tests import _p2d_member, _p3_company, _prod

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / 'frontend' / '__tests__' / 'support' / 'inventory-ai'


def _fixture(name):
    return (FIXTURES / name).read_text(encoding='utf-8')


class RecordingProvider:
    """Test double: records exactly what it receives, answers one proposal per item."""

    def __init__(self):
        self.received = []

    def is_available(self):
        return True

    def has_quota(self, company, user):
        return True

    def propose(self, sent, *, timeout):
        self.received.append(sent)
        return {'results': [{'ref': item['ref'], 'model_words': item.get('model_words'), 'variant_index': None,
                             'confidence': 'low'} for item in sent['items']]}, '0.01'


def _leaves(value):
    if isinstance(value, dict):
        for key, inner in value.items():
            yield from _leaves(key)
            yield from _leaves(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _leaves(inner)
    else:
        yield value


def _word(value):
    return re.compile(rf'(?<![0-9A-Za-z]){re.escape(value)}(?![0-9A-Za-z])', re.I)


class _Path(TestCase):
    def setUp(self):
        if not (REPO / 'frontend').exists():
            self.skipTest('Only backend/ is in this tree.')
        cache.clear()
        self.pinned = json.loads(_fixture('recognizer-output.json'))
        self.sensitive = json.loads(_fixture('sensitive.json'))
        self.company = self.shop('ia-ruta-a', 'Tienda Ruta A')
        self.other = self.shop('ia-ruta-b', 'Tienda Ruta B')
        self.user, _ = _p2d_member(self.company, 'ia_ruta_a', ['company.view', 'products.view', 'products.manage'])
        InventoryAISettings.objects.create(company=self.company, enabled=True)
        # Another company with the SAME variant: its product must never be offered for this company.
        self.foreign = self.variant(self.other, 'iPhone 15 128 GB Negro Villacorta 987654321', 'ruta-b-15')

    def shop(self, slug, name):
        company = _p3_company(slug, name)
        capacity = device_catalog.create_attribute(company=company, name='Capacidad')
        colour = device_catalog.create_attribute(company=company, name='Color', kind='color')
        self_options = {label: device_catalog.create_option(company=company, attribute_id=capacity.pk, label=label)
                        for label in ('128 GB', '256 GB')}
        self_options['Negro'] = device_catalog.create_option(company=company, attribute_id=colour.pk, label='Negro',
                                                                swatch_hex='#111111')
        self_options['Morado'] = device_catalog.create_option(company=company, attribute_id=colour.pk, label='Morado',
                                                                 swatch_hex='#5b21b6')
        company._options = self_options
        company._models = {name: device_catalog.create_model(company=company, name=name)
                           for name in ('iPhone 15', 'iPhone 14 Pro')}
        return company

    def variant(self, company, name, slug, *, model='iPhone 15', options=('128 GB', 'Negro'), line='new'):
        product = _prod(company, name, slug, price='1.00', inventory=0)
        return device_catalog.set_variant(product, model_id=company._models[model].pk, condition_line=line,
                                          option_ids=[company._options[o].pk for o in options])

    def carry(self, company, user, candidates, *, provider=None):
        """The whole chain for one pinned list. Returns (request, provider, result)."""
        cleaned = [text_preview_match.clean(candidate) for candidate in candidates]
        matches = text_preview_match.match(company, candidates)
        items = [payload.item_from_preview(candidate) for candidate in cleaned]
        variant_ids = {index: [option['product_id'] for option in matches[candidate['key']]['options']]
                       for index, candidate in enumerate(cleaned)}
        request = payload.build_request(company, items, variant_ids)
        self.assertTrue(payload.payload_is_clean(request.payload))
        provider = provider or RecordingProvider()
        with mock.patch.object(inventory_ai, 'get_provider', return_value=provider):
            result = inventory_ai.propose(company, user, request, requested=True, permitted=True)
        return request, provider, result


@override_settings(INVENTORY_AI_ENABLED=True, INVENTORY_AI_USER_PER_MINUTE=10, INVENTORY_AI_USER_PER_DAY=100,
                   INVENTORY_AI_MONTHLY_BUDGET_USD='5.00', INVENTORY_AI_MAX_COST_PER_CALL='0.05')
class RecognizerToProviderTest(_Path):
    def setUp(self):
        super().setUp()
        self.mine_15 = self.variant(self.company, 'iPhone 15 128GB Negro', 'ruta-a-15')
        self.mine_14 = self.variant(self.company, 'iPhone 14 Pro 256GB Morado seminuevo', 'ruta-a-14',
                                    model='iPhone 14 Pro', options=('256 GB', 'Morado'), line='used')

    def run_lists(self):
        out = {}
        for name, body in self.pinned.items():
            out[name] = self.carry(self.company, self.user, body['candidates'])
        return out

    def test_the_provider_receives_exactly_the_checked_request_built_from_the_recognizer(self):
        for name, (request, provider, result) in self.run_lists().items():
            with self.subTest(list=name):
                self.assertTrue(result.used, result)
                self.assertEqual(provider.received, [json.loads(json.dumps(request.payload))])
                self.assertEqual(len(provider.received[0]['items']), len(self.pinned[name]['candidates']))
                self.assertEqual([p['index'] for p in result.proposals],
                                 list(range(len(self.pinned[name]['candidates']))))
        self.assertEqual(InventoryAIUsage.objects.filter(company=self.company, ok=True).count(), 2)

    def test_every_value_received_comes_from_its_recognized_candidate_or_this_companys_catalogue(self):
        catalogue = ' '.join(Product.objects.filter(company=self.company).values_list('name', flat=True)).lower()
        for name, (request, provider, _result) in self.run_lists().items():
            for index, item in enumerate(provider.received[0]['items']):
                candidate = self.pinned[name]['candidates'][index]
                with self.subTest(list=name, item=index):
                    self.assertEqual(request.refs[item['ref']], index)
                    self.assertEqual((item['kind'], item['line']), (candidate['kind'], candidate['line']))
                    self.assertIn(item.get('capacity_gb'), (None, candidate['capacityGb']))
                    self.assertIn(item.get('colour'), (None, candidate['colour'].lower() or None))
                    words = [item[key] for key in ('family',) if key in item] + item.get('model_words', [])
                    for word in words:
                        self.assertRegex(candidate['model'], _word(word))
                    for variant in item.get('variants', []):
                        for token in variant['tokens']:
                            self.assertIn(token.lower(), catalogue)

    def test_the_concrete_payload_of_the_list(self):
        (request, provider, _), (used, used_provider, _) = self.run_lists().values()
        got = [{k: v for k, v in item.items() if k != 'ref'} for item in provider.received[0]['items']]
        self.assertEqual(got[0], {'kind': 'device', 'line': 'new', 'family': 'iPhone',
                                  'model_words': ['16', 'Pro', 'Max'], 'capacity_gb': 256,
                                  'colour': 'titanio desierto'})
        self.assertEqual(got[1]['variants'], [{'index': 0, 'tokens': ['iPhone', '15', '128GB', 'negro']}])
        # «Galaxy S24 Ultra x3» and «iPhone 15 Pro (quedan 2)»: the quantity words make the model ambiguous, so
        # it is not sent at all — the family is, and the AI is asked to propose the words.
        self.assertEqual((got[2]['family'], got[2]['ambiguous']), ('Galaxy', ['model_words']))
        self.assertEqual((got[3]['family'], got[3]['ambiguous']), ('iPhone', ['model_words']))
        self.assertNotIn('model_words', got[2])
        self.assertEqual(got[4]['family'], 'Cargador')
        used_items = used_provider.received[0]['items']
        self.assertEqual(used_items[0]['variants'],
                         [{'index': 0, 'tokens': ['iPhone', '14', 'Pro', '256GB', 'morado']}])

    def test_no_identifier_person_contact_cost_price_quantity_or_battery_travels(self):
        received = json.dumps([p.received for _r, p, _x in self.run_lists().values()], ensure_ascii=False)
        s = self.sensitive
        for value in [*s['imeis'], *s['serials'], *s['phones'], *s['names'], *s['emails'], *s['battery'],
                      *s['costs'], 'x3', 'quedan', '%', '@', 'S/', '$']:
            with self.subTest(value=value):
                self.assertNotRegex(received, _word(value) if value[0].isalnum() else re.compile(re.escape(value)))
        for leaf in _leaves([p.received for _r, p, _x in self.run_lists().values()]):
            digits = re.sub(r'\D', '', str(leaf))
            for value in (*s['imeis'], *s['phones']):
                self.assertNotIn(value, digits)
        for key in ('cost', 'price', 'quantity', 'imei', 'serial', 'battery', 'source', 'supplier', 'origin'):
            self.assertNotRegex(received, rf'"{key}[a-z_]*"\s*:')
        for key in ('name', 'model', 'company', 'user', 'key', 'text', 'warnings'):
            self.assertNotRegex(received, rf'"{key}"\s*:')

    def test_only_vocabulary_numbers_and_refs_are_received(self):
        vocabulary = set(payload.FAMILIES + payload.MODEL_WORDS + payload.REGIONS + payload.COLOURS
                         + payload.KINDS + payload.LINES + payload.SIMS) | payload.MODEL_CODES
        keys = payload._ITEM_KEYS | {'schema_version', 'items', 'index', 'tokens'}
        for _request, provider, _result in self.run_lists().values():
            for leaf in _leaves(provider.received[0]):
                with self.subTest(leaf=leaf):
                    if isinstance(leaf, int) and not isinstance(leaf, bool):
                        self.assertLess(leaf, 4096)
                    else:
                        self.assertIsInstance(leaf, str)
                        self.assertTrue(leaf in vocabulary or leaf in keys or re.fullmatch(r'c\d{1,3}-[0-9a-f]{6}', leaf)
                                        or re.fullmatch(r'(?:16|32|64|128|256|512)GB|[12]TB', leaf), leaf)

    def test_another_companys_catalogue_is_never_offered_or_sent(self):
        received = json.dumps([p.received for _r, p, _x in self.run_lists().values()], ensure_ascii=False)
        self.assertNotIn('Villacorta', received)
        matches = text_preview_match.match(self.company, self.pinned['nuevos']['candidates'])
        offered = {o['product_id'] for m in matches.values() for o in m['options']}
        self.assertNotIn(self.foreign.pk, offered)
        self.assertIn(self.mine_15.pk, offered)
        # Even a foreign id handed straight to build_request is not looked up outside the company.
        items = [payload.item_from_preview(text_preview_match.clean(c)) for c in self.pinned['nuevos']['candidates']]
        request = payload.build_request(self.company, items, {1: [self.foreign.pk]})
        self.assertNotIn('variants', request.payload['items'][1])

    @override_settings(INVENTORY_AI_ENABLED=False)
    def test_with_the_platform_switch_off_the_recognized_list_goes_nowhere(self):
        for _request, provider, result in self.run_lists().values():
            self.assertEqual((result.used, result.reason, provider.received), (False, 'global_off', []))
        self.assertFalse(InventoryAIUsage.objects.exists())


class ItemFromPreviewTest(SimpleTestCase):
    """The mapping from a #142-cleaned candidate to a `clean_item` input. It invents nothing."""

    def clean(self, **candidate):
        return text_preview_match.clean({'key': 'k1', **candidate})

    def test_a_device_splits_into_family_and_model_words(self):
        item = payload.item_from_preview(self.clean(kind='device', line='used', model='iPhone 16 Pro Max',
                                                    capacityGb=256, colour='Titanio desierto'))
        self.assertEqual(item, {'kind': 'device', 'line': 'used', 'family': 'iPhone',
                                'model_words': ['16', 'Pro', 'Max'], 'capacity_gb': 256,
                                'colour': 'titanio desierto'})
        self.assertEqual(payload.clean_item(item), item)

    def test_two_word_families(self):
        for model, family, words in (('Apple Watch Ultra', 'Apple Watch', ['Ultra']), ('Mac mini', 'Mac mini', []),
                                     ('Power Bank', 'Power Bank', [])):
            with self.subTest(model=model):
                item = payload.item_from_preview(self.clean(kind='device', model=model))
                self.assertEqual((item['family'], item.get('model_words', [])), (family, words))

    def test_an_unknown_word_makes_the_model_ambiguous_never_sent(self):
        for model in ('Galaxy S24 Ultra x3', 'iPhone 15 Pro (quedan 2)', 'iPhone 15 Pro Teodora',
                      'Redmi Note 13'):
            with self.subTest(model=model):
                cleaned = payload.clean_item(payload.item_from_preview(self.clean(kind='device', model=model)))
                self.assertEqual(cleaned['ambiguous'], ['model_words'])
                self.assertNotIn('model_words', cleaned)

    def test_an_unknown_family_is_ambiguous_and_not_sent(self):
        cleaned = payload.clean_item(payload.item_from_preview(self.clean(kind='device', model='Teodora 15 Pro')))
        self.assertEqual(cleaned.get('ambiguous'), ['family', 'model_words'])
        self.assertNotIn('Teodora', json.dumps(cleaned))

    def test_a_model_with_an_identifier_sends_no_model_at_all(self):
        cleaned = self.clean(kind='device', model='iPhone 15 IMEI 358421073165298', capacityGb=128)
        self.assertTrue(cleaned['identifier'])
        item = payload.item_from_preview(cleaned)
        self.assertNotIn('family', item)
        self.assertNotIn('model_words', item)
        self.assertEqual(sorted(item['ambiguous']), ['family', 'model_words'])
        self.assertNotIn('358421073165298', json.dumps(payload.build_request(None, [item]).payload))

    def test_only_the_fields_of_the_cleaner_are_read(self):
        cleaned = {**self.clean(kind='device', model='iPhone 15'), 'supplierCost': '2350', 'imei': '358421073165298',
                   'source': 'iPhone 15 2350 Teodora'}
        item = payload.item_from_preview(cleaned)
        self.assertLessEqual(set(item), {'kind', 'line', 'family', 'model_words', 'capacity_gb', 'colour',
                                         'ambiguous'})
        self.assertNotRegex(json.dumps(item), r'2350|358421073165298|Teodora')

    def test_not_a_candidate_is_refused(self):
        for bad in (None, 'iPhone 15', ['iPhone']):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                payload.item_from_preview(bad)


@override_settings(INVENTORY_AI_ENABLED=True, INVENTORY_AI_USER_PER_MINUTE=10, INVENTORY_AI_USER_PER_DAY=100,
                   INVENTORY_AI_MONTHLY_BUDGET_USD='5.00', INVENTORY_AI_MAX_COST_PER_CALL='0.05')
class EveryGateStopsTheRecognizedListTest(_Path):
    """Each switch and the permission, on the real recognized list: nothing reaches the provider, nothing is spent."""

    def attempt(self, *, company=None, user=None, requested=True, permitted=True):
        company, user = company or self.company, user or self.user
        cleaned = [text_preview_match.clean(c) for c in self.pinned['nuevos']['candidates']]
        request = payload.build_request(company, [payload.item_from_preview(c) for c in cleaned])
        provider = RecordingProvider()
        with mock.patch.object(inventory_ai, 'get_provider', return_value=provider):
            result = inventory_ai.propose(company, user, request, requested=requested, permitted=permitted)
        return result, provider

    def test_not_asked_for_in_this_import(self):
        result, provider = self.attempt(requested=False)
        self.assertEqual((result.used, result.reason, provider.received), (False, 'not_requested', []))

    def test_the_company_did_not_turn_it_on(self):
        InventoryAISettings.objects.filter(company=self.company).update(enabled=False)
        result, provider = self.attempt()
        self.assertEqual((result.used, result.reason, provider.received), (False, 'company_off', []))

    def test_another_company_turning_it_on_does_not_open_this_one(self):
        user, _ = _p2d_member(self.other, 'ia_ruta_b', ['company.view', 'products.view', 'products.manage'])
        result, provider = self.attempt(company=self.other, user=user)
        self.assertEqual((result.used, result.reason, provider.received), (False, 'company_off', []))

    def test_someone_who_may_not_run_the_import(self):
        result, provider = self.attempt(permitted=False)
        self.assertEqual((result.used, result.reason, provider.received), (False, 'forbidden', []))

    def test_nobody_signed_in(self):
        from django.contrib.auth.models import AnonymousUser

        result, provider = self.attempt(user=AnonymousUser())
        self.assertEqual((result.used, result.reason, provider.received), (False, 'forbidden', []))

    def tearDown(self):
        self.assertFalse(InventoryAIUsage.objects.exists())
        super().tearDown()


class NoOtherWayOutTest(SimpleTestCase):
    """The AI contract and the #142 server half open nothing to the network; the provider registry is empty."""

    MODULES = ('inventory_ai.py', 'inventory_ai_payload.py', 'inventory_ai_safety.py', 'text_preview_match.py',
               'text_preview_views.py')
    NETWORK = ('requests', 'httpx', 'urllib.request', 'urllib3', 'http.client', 'socket', 'smtplib', 'aiohttp',
               'websocket', 'anthropic', 'openai', 'subprocess', 'store.integrations', 'store.messaging',
               'store.payments', 'store.measurement')

    def test_no_network_library_is_imported(self):
        import ast

        here = Path(__file__).resolve().parent
        for name in self.MODULES:
            imported = set()
            for node in ast.walk(ast.parse((here / name).read_text(encoding='utf-8'))):
                if isinstance(node, ast.Import):
                    imported |= {alias.name for alias in node.names}
                elif isinstance(node, ast.ImportFrom):
                    base = ('store.' if node.level else '') + (node.module or '')
                    imported |= {base} | {f'{base}.{alias.name}'.replace('store..', 'store.') for alias in node.names}
            with self.subTest(module=name):
                self.assertFalse({m for m in imported for n in self.NETWORK if m == n or m.startswith(n + '.')})

    def test_the_registry_is_empty_and_nothing_fills_it(self):
        self.assertEqual(inventory_ai._PROVIDERS, {})
        with override_settings(INVENTORY_AI_ENABLED=True, INVENTORY_AI_PROVIDER='fake'):
            self.assertIsNone(inventory_ai.get_provider())


class VocabularyIsGenericTest(SimpleTestCase):
    """
    The closed vocabularies are generic market words, not tenant data (ADR §6): several makers, no shop, no
    digits that could carry a number out. Only the closed MODEL_CODES and the capacities are numeric.
    """

    def test_words_are_letters_only_and_short(self):
        for word in payload.FAMILIES + payload.MODEL_WORDS + payload.COLOURS:
            with self.subTest(word=word):
                self.assertRegex(word, r'^[A-Za-zÁÉÍÓÚáéíóúñ][A-Za-zÁÉÍÓÚáéíóúñ -]{0,23}$')
                self.assertLessEqual(len(word.split()), 2)
        for region in payload.REGIONS:
            self.assertRegex(region, r'^[A-Z]{1,2}/A$')

    def test_more_than_one_maker_and_no_pilot_data(self):
        self.assertTrue({'iPhone', 'Galaxy', 'Pixel', 'Redmi', 'Motorola', 'Honor'} <= set(payload.FAMILIES))
        everything = ' '.join(payload.FAMILIES + payload.MODEL_WORDS + payload.COLOURS + payload.REGIONS).lower()
        for pilot in ('black dog', 'blackdog', 'tienda', 'store', 'sac', 'ruc'):
            self.assertNotRegex(everything, _word(pilot))
