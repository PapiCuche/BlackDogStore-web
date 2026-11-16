"""
INV-FAST-B1 · SIM y región en «Proponer lo que falta» (decisión del propietario, 2026-11-16).

    SIM Y REGIÓN SON OPCIONES DE LA VARIANTE. NUNCA SE CREA UN ATRIBUTO.
    UNA OPCIÓN DE SIM O DE REGIÓN QUE FALTA SE CREA SÓLO SI LA PERSONA LA APRUEBA.

Lo que estas pruebas fijan:

  * el texto de SIM y de región se lee contra el atributo de la empresa CON ESE ROL: la etiqueta plegada
    primero, luego los alias de la empresa (`configuration_resolver.resolve_option`);
  * encontrada → la opción existente; ambigua → la fila no se propone y dice por qué; desconocida → una
    opción «missing» que `apply` sólo crea si viene en `approve.options` (si no, `option_not_approved`);
  * una empresa sin atributo de SIM (o de región) no lo obtiene: la fila se propone SIN esa opción y avisa dónde configurarlo;
  * la variante que ya es esa combinación (también con SIM y región) se reutiliza, nunca se duplica;
  * un IMEI o una serie escondidos en la SIM o la región: la fila no se propone;
  * crear nunca mueve stock ni Kardex ni publica; lo creado queda oculto; idempotente y bajo el candado de la empresa.
"""
import threading

from django.db import connection as db_connection
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from store import catalog_aliases, device_catalog
from store.models import (
    AdminAuditLog, BranchStock, Product, ProductBarcode, ProductModel, StockMovement, StockUnit, VariantAttribute,
    VariantOption,
)
from store.test_model_palette import PaletteShop
from store.tests import _p2d_member, _p3_company

PROPOSE = '/api/admin/inventory/text-preview/catalog-proposal/'
APPLY = '/api/admin/inventory/text-preview/catalog-proposal/apply/'


def row(key, model, gb, colour, line='new', **extra):
    return {'key': key, 'model': model, 'capacityGb': gb, 'colour': colour, 'line': line, **extra}


class _Base(PaletteShop):
    def start(self, slug):
        self.build(slug)
        self.api = APIClient()
        self.api.force_authenticate(self.staff)
        # A variant that already IS «256 GB · Azul · eSIM · J/A», new line.
        self.jp = self.variant('Teléfono X 256 GB Azul eSIM J/A', 'tx-256-azul-esim-ja', '4000.00',
                               [self.gb256, self.blue, self.esim, self.ja])

    def propose(self, rows, client=None):
        return (client or self.api).post(PROPOSE, {'rows': rows}, format='json')

    def apply(self, rows, approve, client=None, key=None, **extra):
        headers = {'HTTP_IDEMPOTENCY_KEY': key} if key else {}
        return (client or self.api).post(APPLY, {'rows': rows, 'approve': approve, **extra}, format='json', **headers)

    def plan(self, rows):
        response = self.propose(rows)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def everything(self, plan, *, options=False):
        approve = {'models': [m['ref'] for m in plan['models'] if m['status'] == 'missing'],
                   'variants': [v['ref'] for v in plan['variants'] if v['status'] == 'missing']}
        if options:
            approve['options'] = [o['ref'] for o in plan['options'] if o['status'] == 'missing']
        return approve

    def counts(self, company=None):
        company = company or self.company
        return (ProductModel.objects.filter(company=company).count(),
                VariantAttribute.objects.filter(company=company).count(),
                VariantOption.objects.filter(company=company).count(),
                Product.objects.filter(company=company).count(),
                ProductBarcode.objects.filter(company=company).count())

    def stock_rows(self):
        return (StockMovement.objects.count(), StockUnit.objects.count(), BranchStock.objects.count())


class ProposeSimRegionTest(_Base, TestCase):
    def setUp(self):
        self.start('sr-prop')

    def test_sim_and_region_are_accepted_fields(self):
        response = self.propose([row('a', 'Teléfono X', 256, 'azul', sim='eSIM', region='J/A')])
        self.assertEqual(response.status_code, 200, response.content)

    def test_an_existing_variant_with_sim_and_region_is_reused(self):
        rows = [row('a', 'Teléfono X', 256, 'azul', sim='eSIM', region='J/A')]
        body = self.plan(rows)
        self.assertEqual(len(body['plan']['variants']), 1)
        variant = body['plan']['variants'][0]
        self.assertEqual((variant['status'], variant['product_id']), ('existing', self.jp.pk))
        self.assertEqual(len(variant['option_refs']), 4)
        before = self.counts()
        response = self.apply(rows, {'models': [], 'variants': [variant['ref']]})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['created']['variants'], [])
        self.assertEqual(self.counts(), before)

    def test_the_same_options_without_sim_and_region_are_another_variant(self):
        variant = self.plan([row('a', 'Teléfono X', 256, 'azul')])['plan']['variants'][0]
        self.assertEqual(variant['status'], 'missing')

    def test_a_company_alias_resolves_the_region(self):
        catalog_aliases.create_alias(company=self.company, scope='option', alias='japan', option_id=self.ja.pk,
                                     actor=self.staff)
        variant = self.plan([row('a', 'Teléfono X', 256, 'azul', sim='eSIM', region='japan')])['plan']['variants'][0]
        self.assertEqual((variant['status'], variant['product_id']), ('existing', self.jp.pk))

    def test_case_and_spacing_differences_resolve(self):
        variant = self.plan([row('a', 'Teléfono X', 256, 'azul', sim='ESIM', region=' j/a ')])['plan']['variants'][0]
        self.assertEqual((variant['status'], variant['product_id']), ('existing', self.jp.pk))

    def test_existing_options_in_a_new_combination_create_nothing_but_the_variant(self):
        body = self.plan([row('a', 'Teléfono X', 128, 'negro', sim='chip', region='LL/A')])
        variant = body['plan']['variants'][0]
        self.assertEqual(variant['status'], 'missing')
        self.assertEqual(variant['creates']['options'], [])
        self.assertEqual(variant['suggested_name'], 'Teléfono X 128 GB Negro Chip LL/A')
        self.assertTrue(all(o['status'] == 'existing' for o in body['plan']['options']))

    def test_an_unknown_sim_is_a_missing_option_that_needs_explicit_approval(self):
        body = self.plan([row('a', 'Teléfono X', 128, 'negro', sim='dual')])
        missing = [o for o in body['plan']['options'] if o['status'] == 'missing']
        self.assertEqual(len(missing), 1)
        self.assertEqual((missing[0]['label'], missing[0]['attribute_id'], missing[0]['role']),
                         ('Dual', self.sim.pk, 'sim'))
        self.assertTrue(missing[0]['needs_approval'])
        self.assertEqual(body['plan']['variants'][0]['creates']['options'], [missing[0]['ref']])

    def test_capacity_and_colour_options_do_not_ask_for_their_own_approval(self):
        body = self.plan([row('a', 'Teléfono X', 512, 'rojo vivo')])
        self.assertTrue(body['plan']['options'])
        self.assertFalse(any(o['needs_approval'] for o in body['plan']['options']))

    def test_an_ambiguous_sim_is_never_picked(self):
        # Legacy data: two labels of the SIM attribute that fold to one text.
        VariantOption.objects.create(company=self.company, attribute=self.sim, label='Físico')
        VariantOption.objects.create(company=self.company, attribute=self.sim, label='Fisico')
        body = self.plan([row('a', 'Teléfono X', 128, 'negro', sim='fisico')])
        self.assertIsNone(body['rows'][0]['variant_ref'])
        self.assertIn('no se puede decidir', body['rows'][0]['reason'])
        self.assertEqual(body['plan']['variants'], [])

    def test_a_company_without_a_sim_attribute_still_gets_the_row_proposed_with_a_warning(self):
        # Review: the pilot has no SIM/region attributes; skipping would make every «eSIM» row unproposable.
        # Never an attribute created: the variant is proposed WITHOUT the SIM, and the row says how to separate it.
        VariantAttribute.objects.filter(pk=self.sim.pk).update(role='other')
        before = self.counts()
        body = self.plan([row('a', 'Teléfono X', 128, 'negro', sim='eSIM')])
        [read] = body['rows']
        self.assertIsNotNone(read['variant_ref'])
        self.assertTrue(any('SIM' in w and 'Catálogo › Modelos' in w for w in read['warnings']), read['warnings'])
        self.assertFalse(any(o['attribute_id'] == self.sim.pk for o in body['plan']['options']))
        self.assertEqual(self.counts(), before)

    def test_a_company_without_a_region_attribute_still_gets_the_row_proposed_with_a_warning(self):
        VariantAttribute.objects.filter(pk=self.region.pk).update(role='other')
        body = self.plan([row('a', 'Teléfono X', 128, 'negro', region='LL/A')])
        [read] = body['rows']
        self.assertIsNotNone(read['variant_ref'])
        self.assertTrue(any('región' in w for w in read['warnings']), read['warnings'])

    def test_another_companys_options_are_never_used(self):
        other = _p3_company('sr-other', 'Otra')
        attr = device_catalog.create_attribute(company=other, name='SIM', role='sim')
        theirs = device_catalog.create_option(company=other, attribute_id=attr.pk, label='Dual')
        catalog_aliases.create_alias(company=other, scope='option', alias='japan', option_id=theirs.pk)
        body = self.plan([row('a', 'Teléfono X', 128, 'negro', sim='Dual'),
                          row('b', 'Teléfono X', 128, 'azul', region='japan')])
        ids = {o['option_id'] for o in body['plan']['options']}
        self.assertNotIn(theirs.pk, ids)
        by_label = {o['label']: o for o in body['plan']['options']}
        self.assertEqual((by_label['Dual']['status'], by_label['Dual']['attribute_id']), ('missing', self.sim.pk))
        self.assertEqual((by_label['Japan']['status'], by_label['Japan']['attribute_id']), ('missing', self.region.pk))

    def test_an_identifier_hidden_in_sim_or_region_skips_the_row(self):
        for field in ('sim', 'region'):
            with self.subTest(field):
                body = self.plan([row('a', 'Teléfono X', 128, 'negro', **{field: '356938035643809'})])
                self.assertIsNone(body['rows'][0]['variant_ref'])
                self.assertIn('identificador', body['rows'][0]['reason'])
                self.assertEqual(body['plan']['variants'], [])
                self.assertFalse([o for o in body['plan']['options'] if o['status'] == 'missing'])
                self.assertNotIn('356938035643809', str(body))

    def test_a_proposal_writes_nothing(self):
        before = (self.counts(), AdminAuditLog.objects.count())
        self.plan([row('a', 'Teléfono X', 128, 'negro', sim='dual', region='chile')])
        self.assertEqual((self.counts(), AdminAuditLog.objects.count()), before)


class ApplySimRegionTest(_Base, TestCase):
    def setUp(self):
        self.start('sr-apply')

    def test_a_new_variant_with_existing_options_is_created_hidden_without_stock(self):
        rows = [row('a', 'Teléfono X', 128, 'negro', sim='chip', region='LL/A')]
        body = self.plan(rows)
        stock = self.stock_rows()
        response = self.apply(rows, self.everything(body['plan']))
        self.assertEqual(response.status_code, 200, response.content)
        made = response.json()['created']
        self.assertEqual((len(made['variants']), made['options']), (1, []))
        product = Product.objects.get(pk=made['variants'][0]['product_id'])
        self.assertFalse(product.is_published_online)
        self.assertEqual(product.variant_key, device_catalog.variant_key(
            [self.gb128.pk, self.black.pk, self.chip.pk, self.lla.pk]))
        self.assertEqual(self.stock_rows(), stock)

    def test_an_unknown_sim_is_refused_without_approval_and_created_with_it(self):
        rows = [row('a', 'Teléfono X', 128, 'negro', sim='dual')]
        plan = self.plan(rows)['plan']
        before = (self.counts(), AdminAuditLog.objects.count())
        refused = self.apply(rows, self.everything(plan))
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertEqual(refused.json()['code'], 'option_not_approved')
        self.assertEqual((self.counts(), AdminAuditLog.objects.count()), before)
        stock = self.stock_rows()
        response = self.apply(rows, self.everything(plan, options=True))
        self.assertEqual(response.status_code, 200, response.content)
        made = response.json()['created']
        self.assertEqual([o['label'] for o in made['options']], ['Dual'])
        option = VariantOption.objects.get(pk=made['options'][0]['option_id'])
        self.assertEqual((option.company_id, option.attribute_id), (self.company.pk, self.sim.pk))
        self.assertEqual(len(made['variants']), 1)
        self.assertFalse(Product.objects.get(pk=made['variants'][0]['product_id']).is_published_online)
        self.assertEqual(self.stock_rows(), stock)
        # No attribute was created on the way.
        self.assertEqual(VariantAttribute.objects.filter(company=self.company).count(), before[0][1])

    def test_an_unknown_region_is_refused_without_approval_and_created_with_it(self):
        rows = [row('a', 'Teléfono X', 256, 'negro', sim='eSIM', region='chile')]
        plan = self.plan(rows)['plan']
        refused = self.apply(rows, self.everything(plan))
        self.assertEqual(refused.json()['code'], 'option_not_approved')
        response = self.apply(rows, self.everything(plan, options=True))
        self.assertEqual(response.status_code, 200, response.content)
        made = response.json()['created']
        self.assertEqual([(o['label'], o['attribute_id']) for o in made['options']], [('Chile', self.region.pk)])

    def test_an_unknown_option_reference_is_refused(self):
        rows = [row('a', 'Teléfono X', 128, 'negro', sim='dual')]
        approve = {**self.everything(self.plan(rows)['plan']), 'options': ['option:999:nada']}
        response = self.apply(rows, approve)
        self.assertEqual((response.status_code, response.json()['code']), (400, 'unknown_ref'))
        for shape in ('x', [1], {'a': 1}):
            with self.subTest(shape=repr(shape)):
                self.assertEqual(self.apply(rows, {**approve, 'options': shape}).status_code, 400)

    def test_capacity_and_colour_options_keep_todays_behaviour(self):
        rows = [row('a', 'Teléfono X', 512, 'rojo vivo')]
        response = self.apply(rows, self.everything(self.plan(rows)['plan']))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(response.json()['created']['options']), 2)

    def test_applying_twice_creates_nothing_new(self):
        rows = [row('a', 'Teléfono X', 128, 'negro', sim='dual', region='chile'),
                row('b', 'Teléfono X', 256, 'azul', sim='eSIM', region='J/A')]
        plan = self.plan(rows)['plan']
        first = self.apply(rows, self.everything(plan, options=True))
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual((len(first.json()['created']['options']), len(first.json()['created']['variants'])), (2, 1))
        after = self.counts()
        second = self.apply(rows, self.everything(plan, options=True))
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(second.json()['created'], {'models': [], 'options': [], 'variants': []})
        self.assertEqual(self.counts(), after)
        self.assertEqual({v['product_id'] for v in first.json()['variants']},
                         {v['product_id'] for v in second.json()['variants']})

    def test_the_same_idempotency_key_replays_and_another_request_conflicts(self):
        rows = [row('a', 'Teléfono X', 128, 'negro', sim='dual')]
        plan = self.plan(rows)['plan']
        approve = self.everything(plan, options=True)
        first = self.apply(rows, approve, key='sr-1')
        after = self.counts()
        again = self.apply(rows, approve, key='sr-1')
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.json()['replayed'])
        self.assertEqual(again.json()['created'], first.json()['created'])
        self.assertEqual(self.counts(), after)
        # Same rows, a different SIM: another request under the same key.
        other = self.apply([row('a', 'Teléfono X', 128, 'negro', sim='chip')], approve, key='sr-1')
        self.assertEqual(other.status_code, 409, other.content)

    def test_it_needs_products_manage(self):
        rows = [row('a', 'Teléfono X', 128, 'negro', sim='dual')]
        approve = self.everything(self.plan(rows)['plan'], options=True)
        who, _ = _p2d_member(self.company, 'sr-no-manage', ['company.view', 'products.view', 'inventory.adjust'])
        client = APIClient()
        client.force_authenticate(who)
        before = self.counts()
        self.assertEqual(self.apply(rows, approve, client=client).status_code, 403)
        self.assertEqual(self.counts(), before)

    def test_every_write_is_audited(self):
        rows = [row('a', 'Teléfono X', 128, 'negro', sim='dual')]
        self.apply(rows, self.everything(self.plan(rows)['plan'], options=True))
        actions = set(AdminAuditLog.objects.filter(company=self.company).values_list('action', flat=True))
        self.assertTrue({'variant_option_created', 'product_created', 'catalog_proposal_applied'} <= actions, actions)


class SimRegionApprovalsAtOnceTest(_Base, TransactionTestCase):
    def setUp(self):
        self.start('sr-race')

    def test_two_operators_approving_the_same_list_create_it_once(self):
        rows = [row('a', 'Teléfono X', 128, 'negro', sim='dual', region='chile')]
        approve = self.everything(self.plan(rows)['plan'], options=True)
        barrier, results = threading.Barrier(2), [None, None]

        def run(index):
            try:
                client = APIClient()
                client.force_authenticate(self.staff)
                barrier.wait(timeout=10)
                results[index] = client.post(APPLY, {'rows': rows, 'approve': approve}, format='json')
            except Exception as failed:  # noqa: BLE001 — the outcome under test
                results[index] = failed
            finally:
                db_connection.close()

        threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        for response in results:
            self.assertEqual(getattr(response, 'status_code', response), 200, results)
        self.assertEqual(VariantOption.objects.filter(company=self.company, label__in=['Dual', 'Chile']).count(), 2)
        self.assertEqual(sorted(len(r.json()['created']['variants']) for r in results), [0, 1])
        self.assertEqual(Product.objects.filter(company=self.company, name__contains='Dual').count(), 1)
