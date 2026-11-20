"""
CATALOG-REFERENCE-01 · PR B — importar modelos del catálogo de referencia a UNA empresa (`docs/adr-catalog-reference.md`).

    PREVISUALIZAR NO ESCRIBE. APLICAR CREA SÓLO MODELO, OPCIONES, PALETA Y EXCLUSIONES — OCULTOS, UNA VEZ.

Lo que estas pruebas fijan:

  * la vista previa clasifica cada modelo elegido: nuevo / reutilizado / conflicto / pendiente de revisión;
  * aplicar crea el modelo oculto, los atributos por rol que falten (reutiliza el que ya tiene ese rol), las opciones
    (reutiliza por `reference_key`, luego por etiqueta plegada; una opción renombrada por la empresa sigue renombrada),
    la paleta del modelo y los pares SIM × región que el modelo no tiene;
  * NUNCA crea `Product`, `BranchStock`, `StockUnit` ni movimientos, ni cambia precios, fotos o publicación de lo que existe;
  * cada modelo tiene sus colores y capacidades: lo que no es suyo lo rechaza `check_variant`;
  * aplicar dos veces no cambia nada (también dos personas a la vez); la clave de idempotencia repite la respuesta;
  * un modelo `partial` / `pending_review` sólo se aplica con aprobación explícita;
  * otra empresa no ve ni recibe nada; la empresa sale del contexto del servidor, nunca del cuerpo;
  * permisos: ver = `products.view`, aplicar = `products.manage`;
  * el motor no nombra fabricante, modelo ni color.

Los datos son inventados (`test_catalog_reference_load`); AGENTS §2.
"""
import inspect
import re
import threading
from pathlib import Path

from django.db import connection as db_connection
from django.test import TestCase, TransactionTestCase, override_settings

from store import catalog_reference_import as importer
from store import catalog_reference_load as loader
from store import device_catalog, model_palette
from store.models import (
    AdminAuditLog, BranchStock, ModelOption, ModelOptionExclusion, Product, ProductModel, ReferenceModel,
    ReferenceModelNumber, ReferenceOption, StockMovement, StockUnit, VariantAttribute, VariantOption,
)
from store.test_catalog_reference_load import dataset
from store.test_device_commerce import _client
from store.tests import _p2d_member, _p3_company, _prod

LIST = '/api/admin/catalog/reference/models/'
PREVIEW = '/api/admin/catalog/reference/preview/'
APPLY = '/api/admin/catalog/reference/apply/'


class _Shop:
    def start(self, slug):
        loader.load(dataset())
        self.company = _p3_company(slug, 'Tienda Ref')
        self.staff, _ = _p2d_member(self.company, f'{slug}_staff', ['company.view', 'products.view', 'products.manage'])
        self.q = f'?company={self.company.pk}'
        self.api = _client(self.staff)

    def preview(self, keys, client=None, company=None):
        q = f'?company={(company or self.company).pk}'
        return (client or self.api).post(PREVIEW + q, {'keys': keys}, format='json')

    def plan(self, keys):
        response = self.preview(keys)
        self.assertEqual(response.status_code, 200, response.content)
        return {item['key']: item for item in response.json()['items']}

    def apply(self, keys, approve=None, client=None, key=None, company=None, **extra):
        q = f'?company={(company or self.company).pk}'
        headers = {'HTTP_IDEMPOTENCY_KEY': key} if key else {}
        return (client or self.api).post(APPLY + q, {'keys': keys, 'approve': approve or [], **extra},
                                         format='json', **headers)

    def applied(self, keys, approve=None):
        response = self.apply(keys, approve)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def counts(self, company=None):
        company = company or self.company
        return (ProductModel.objects.filter(company=company).count(),
                VariantAttribute.objects.filter(company=company).count(),
                VariantOption.objects.filter(company=company).count(),
                ModelOption.objects.filter(company=company).count(),
                ModelOptionExclusion.objects.filter(company=company).count())

    def stock(self):
        return (Product.objects.count(), BranchStock.objects.count(), StockUnit.objects.count(),
                StockMovement.objects.count())

    def tenant_model(self, key):
        return ProductModel.objects.get(company=self.company, reference_key=key)

    def option(self, reference_key):
        return VariantOption.objects.get(company=self.company, reference_key=reference_key)

    def make(self, model, *reference_keys):
        return device_catalog.create_variant_product(
            self.company, model, condition_line='new',
            option_ids=[self.option(k).pk for k in reference_keys], actor=self.staff, request=None)


class PreviewTest(_Shop, TestCase):
    def setUp(self):
        self.start('ref-prev')

    def test_the_list_shows_the_reference_models_and_what_this_company_linked(self):
        response = self.api.get(LIST + self.q)
        self.assertEqual(response.status_code, 200, response.content)
        rows = {row['key']: row for row in response.json()['results']}
        self.assertEqual(set(rows), {'phone-9', 'phone-9-pro', 'phone-1'})
        self.assertEqual(rows['phone-1']['completeness'], 'partial')
        self.assertIsNone(rows['phone-9']['linked_model_id'])

    def test_the_preview_writes_nothing_and_lists_what_would_be_created(self):
        before = (self.counts(), self.stock(), AdminAuditLog.objects.count())
        items = self.plan(['phone-9'])
        self.assertEqual((before[0], before[1], before[2]), (self.counts(), self.stock(), AdminAuditLog.objects.count()))
        item = items['phone-9']
        self.assertEqual((item['state'], item['model_status'], item['model_id']), ('new', 'new', None))
        self.assertEqual(sorted(item['attributes_new']), ['capacity', 'color', 'region', 'sim'])
        options = {o['reference_key']: o for o in item['options']}
        self.assertEqual(options['color:ink']['label'], 'Tinta')
        self.assertTrue(all(o['status'] == 'new' for o in options.values()))
        # 3 SIM × 3 regions, 3 real pairs → 6 impossible pairs.
        self.assertEqual(item['exclusions_add'], 6)
        self.assertTrue(item['changes'])

    def test_default_is_no_model(self):
        response = self.api.post(PREVIEW + self.q, {}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['items'], [])

    def test_an_unknown_or_bad_key_is_400(self):
        self.assertEqual(self.preview(['phone-99']).json()['code'], 'unknown_reference')
        for body in ('phone-9', [1], [{'k': 1}], ['x' * 200]):
            self.assertEqual(self.preview(body).status_code, 400, body)

    def test_a_partial_model_is_pending_review(self):
        item = self.plan(['phone-1'])['phone-1']
        self.assertEqual((item['state'], item['needs_approval']), ('pending_review', True))
        self.assertEqual(item['missing'], ['colours are not published'])

    def test_counts_by_state(self):
        response = self.preview(['phone-9', 'phone-1'])
        self.assertEqual(response.json()['counts'], {'new': 1, 'reused': 0, 'conflict': 0, 'pending_review': 1})


class ApplyTest(_Shop, TestCase):
    def setUp(self):
        self.start('ref-apply')

    def test_apply_creates_a_hidden_model_with_its_palette_and_exclusions_and_no_products_or_stock(self):
        stock = self.stock()
        body = self.applied(['phone-9'])
        self.assertEqual(self.stock(), stock)
        model = self.tenant_model('phone-9')
        self.assertEqual(model.name, 'Phone 9')
        self.assertFalse(model.is_published_online)
        self.assertEqual(model.variants.count(), 0)
        roles = dict(VariantAttribute.objects.filter(company=self.company).values_list('role', 'kind'))
        self.assertEqual(roles, {'capacity': 'text', 'color': 'color', 'sim': 'text', 'region': 'text'})
        palette = set(ModelOption.objects.filter(model=model).values_list('option__reference_key', flat=True))
        self.assertEqual(palette, {'capacity:128gb', 'capacity:256gb', 'color:ink', 'color:sand', 'sim:esim-only',
                                   'sim:nano-sim-esim', 'sim:dual-nano-sim', 'region:country-a-country-b',
                                   'region:default', 'region:country-c'})
        self.assertEqual(ModelOptionExclusion.objects.filter(model=model).count(), 6)
        self.assertEqual(body['created']['models'][0]['model_id'], model.pk)
        self.assertTrue(AdminAuditLog.objects.filter(company=self.company, action='catalog_reference_applied').exists())

    def test_sim_and_region_options_take_the_spanish_labels_of_the_reference(self):
        data = dataset()
        for entry in data['models'][0]['sim']:
            entry['label_es'] = 'SIM ' + entry['key'].upper()
            entry['region_label_es'] = 'Región ' + entry['regions'][:9]
        loader.load(data)
        self.applied(['phone-9'])
        self.assertEqual(self.option('sim:esim-only').label, 'SIM ESIM-ONLY')
        self.assertEqual(self.option('region:default').label, 'Región default (')
        self.assertEqual(ReferenceOption.objects.get(model__key='phone-9', role='sim', key='esim-only').label_es,
                         'SIM ESIM-ONLY')

    def test_each_model_keeps_its_own_colours_and_capacities(self):
        self.applied(['phone-9', 'phone-9-pro'])
        nine, nine_pro = self.tenant_model('phone-9'), self.tenant_model('phone-9-pro')

        def colours(m):
            return set(ModelOption.objects.filter(model=m, option__attribute__role='color')
                       .values_list('option__reference_key', flat=True))

        self.assertEqual(colours(nine), {'color:ink', 'color:sand'})
        self.assertEqual(colours(nine_pro), {'color:ink', 'color:sky'})
        # One shared tenant option for the colour both models have.
        self.assertEqual(VariantOption.objects.filter(company=self.company, reference_key='color:ink').count(), 1)

    def test_a_capacity_the_model_does_not_have_is_refused(self):
        self.applied(['phone-9', 'phone-9-pro'])
        with self.assertRaises(device_catalog.CatalogError) as caught:
            self.make(self.tenant_model('phone-9'), 'capacity:512gb', 'color:ink', 'sim:nano-sim-esim', 'region:default')
        self.assertEqual(caught.exception.code, 'option_not_in_palette')

    def test_an_impossible_sim_region_pair_is_refused_and_the_real_one_is_not(self):
        self.applied(['phone-9'])
        nine = self.tenant_model('phone-9')
        with self.assertRaises(device_catalog.CatalogError) as caught:
            self.make(nine, 'capacity:128gb', 'color:ink', 'sim:esim-only', 'region:country-c')
        self.assertEqual(caught.exception.code, 'combination_excluded')
        product = self.make(nine, 'capacity:128gb', 'color:ink', 'sim:esim-only', 'region:country-a-country-b')
        self.assertFalse(product.is_published_online)

    def test_the_matrix_offers_only_combinations_that_exist(self):
        self.applied(['phone-9'])
        body = model_palette.payload(self.tenant_model('phone-9'))
        # 2 capacities × 2 colours × 3 real SIM/region pairs.
        self.assertEqual(body['summary']['combinations'], 12)
        esim, country_c = self.option('sim:esim-only').pk, self.option('region:country-c').pk
        self.assertFalse(any({esim, country_c} <= set(c['option_ids']) for c in body['combinations']))

    def test_new_capacities_are_ordered_by_size_not_by_their_label(self):
        """G (visual review): imported capacities read 256 GB, 512 GB, 1 TB — not «1 TB» first by alphabet — in the
        company's order that the till, the palette and the public page use."""
        self.applied(['phone-9-pro'])
        capacity = VariantAttribute.objects.get(company=self.company, role='capacity')
        labels = list(VariantOption.objects.filter(company=self.company, attribute=capacity)
                      .order_by('sort_order', 'label', 'pk').values_list('label', flat=True))
        self.assertEqual(labels, ['256 GB', '512 GB', '1 TB'])

    def test_applying_twice_changes_nothing(self):
        self.applied(['phone-9', 'phone-9-pro'])
        before, logs = self.counts(), AdminAuditLog.objects.exclude(action='catalog_reference_applied').count()
        items = self.plan(['phone-9', 'phone-9-pro'])
        self.assertEqual({i['state'] for i in items.values()}, {'reused'})
        self.assertFalse(any(i['changes'] for i in items.values()))
        body = self.applied(['phone-9', 'phone-9-pro'])
        self.assertEqual(self.counts(), before)
        self.assertEqual(AdminAuditLog.objects.exclude(action='catalog_reference_applied').count(), logs)
        self.assertEqual(body['created'], {'models': [], 'attributes': [], 'options': []})
        self.assertEqual((body['palette_added'], body['exclusions_added'], body['linked']),
                         (0, 0, {'models': 0, 'options': 0}))

    def test_the_idempotency_key_replays_and_refuses_another_request(self):
        first = self.apply(['phone-9'], key='k-1')
        self.assertEqual(first.status_code, 200, first.content)
        again = self.apply(['phone-9'], key='k-1')
        self.assertEqual((again.status_code, again.json()['replayed']), (200, True))
        self.assertEqual(again.json()['created'], first.json()['created'])
        other = self.apply(['phone-9-pro'], key='k-1')
        self.assertEqual((other.status_code, other.json()['code']), (409, 'idempotency_conflict'))
        self.assertFalse(ProductModel.objects.filter(company=self.company, reference_key='phone-9-pro').exists())

    def test_a_partial_model_needs_explicit_approval(self):
        before = self.counts()
        response = self.apply(['phone-1'])
        self.assertEqual((response.status_code, response.json()['code']), (400, 'approval_required'))
        self.assertEqual(self.counts(), before)
        self.applied(['phone-1'], approve=['phone-1'])
        self.assertFalse(self.tenant_model('phone-1').is_published_online)

    def test_an_approval_for_a_key_not_chosen_is_400(self):
        response = self.apply(['phone-9'], approve=['phone-1'])
        self.assertEqual((response.status_code, response.json()['code']), (400, 'unknown_reference'))

    def test_the_company_in_the_body_is_ignored(self):
        other = _p3_company('ref-apply-b', 'Otra')
        self.apply(['phone-9'], company_id=other.pk, company=None)
        self.assertEqual(self.counts(other), (0, 0, 0, 0, 0))
        self.assertTrue(ProductModel.objects.filter(company=self.company, reference_key='phone-9').exists())

    def test_a_retired_reference_model_cannot_be_imported(self):
        ReferenceModel.objects.filter(key='phone-9').update(in_dataset=False)
        self.assertEqual(self.preview(['phone-9']).json()['code'], 'unknown_reference')

    def test_a_retired_option_is_not_imported(self):
        ReferenceOption.objects.filter(model__key='phone-9', role='color', key='sand').update(in_dataset=False)
        self.applied(['phone-9'])
        self.assertFalse(VariantOption.objects.filter(company=self.company, reference_key='color:sand').exists())

    def test_reference_rows_are_not_touched_by_an_import(self):
        before = (ReferenceModel.objects.count(), ReferenceOption.objects.count(), ReferenceModelNumber.objects.count())
        self.applied(['phone-9'])
        self.assertEqual(before, (ReferenceModel.objects.count(), ReferenceOption.objects.count(),
                                  ReferenceModelNumber.objects.count()))


class ExistingCatalogTest(_Shop, TestCase):
    """A company that already has part of the catalogue: reuse, never duplicate, never rewrite."""

    def setUp(self):
        self.start('ref-exist')
        self.colour = device_catalog.create_attribute(company=self.company, name='Tono', kind='color', sort_order=1)
        VariantAttribute.objects.filter(pk=self.colour.pk).update(role='color')
        self.capacity = device_catalog.create_attribute(company=self.company, name='Memoria', role='capacity')
        self.gb128 = device_catalog.create_option(company=self.company, attribute_id=self.capacity.pk, label='128GB')
        self.ink = device_catalog.create_option(company=self.company, attribute_id=self.colour.pk, label='tinta',
                                                swatch_hex='#123456')
        self.model = device_catalog.create_model(company=self.company, name='PHONE  9', is_published_online=True,
                                                 image_url='')
        product = _prod(self.company, 'Phone 9 128 tinta', 'p9-128-tinta', price='999.00', inventory=0)
        self.variant = device_catalog.set_variant(product, model_id=self.model.pk, condition_line='new',
                                                  option_ids=[self.gb128.pk, self.ink.pk])

    def test_the_existing_model_and_options_are_reused_and_linked(self):
        item = self.plan(['phone-9'])['phone-9']
        self.assertEqual((item['state'], item['model_status'], item['model_id']), ('reused', 'reused', self.model.pk))
        options = {o['reference_key']: o for o in item['options']}
        self.assertEqual((options['color:ink']['status'], options['color:ink']['option_id']), ('reused', self.ink.pk))
        self.assertEqual(options['capacity:128gb']['option_id'], self.gb128.pk)
        self.assertEqual(sorted(item['attributes_new']), ['region', 'sim'])
        self.applied(['phone-9'])
        self.model.refresh_from_db()
        self.assertEqual((self.model.reference_key, self.model.name, self.model.is_published_online),
                         ('phone-9', 'PHONE 9', True))
        self.ink.refresh_from_db()
        self.assertEqual((self.ink.reference_key, self.ink.label, self.ink.swatch_hex), ('color:ink', 'tinta', '#123456'))
        self.assertEqual(VariantAttribute.objects.filter(company=self.company, role='color').count(), 1)
        self.variant.refresh_from_db()
        self.assertEqual((str(self.variant.price), self.variant.name), ('999.00', 'Phone 9 128 tinta'))
        self.assertEqual(Product.objects.filter(company=self.company).count(), 1)

    def test_a_model_on_sale_with_prices_photos_and_stock_imported_twice_keeps_all_of_it(self):
        """G (owner's priority 6): a realistic tenant model — priced, with photos and devices in stock — imported
        twice. No variant or option is duplicated; prices, photos and publication stay; nothing of stock moves."""
        from store import stock_unit_services as units
        from store.models import ProductImage
        from store.test_stock_units import imei

        Product.objects.filter(pk=self.variant.pk).update(is_serialized=True, requires_imei=True,
                                                          image_url='https://cdn.example.com/p9-tinta.png')
        ProductModel.objects.filter(pk=self.model.pk).update(image_url='https://cdn.example.com/p9.png')
        ProductImage.objects.create(company=self.company, product=self.variant,
                                    image_url='https://cdn.example.com/p9-tinta-dorso.png', sort_order=0)
        units.receive_units(branch=self.company.default_inventory_branch, product=Product.objects.get(pk=self.variant.pk),
                            actor=self.staff, reason='Compra',
                            units=[{'imei': imei(f'35209899{n:06d}')} for n in range(2)])

        def snapshot():
            return (list(Product.objects.filter(company=self.company).order_by('pk').values_list(
                        'pk', 'name', 'price', 'image_url', 'is_published_online', 'variant_key', 'product_model_id')),
                    list(ProductImage.objects.filter(company=self.company).values_list('product_id', 'image_url')),
                    ProductModel.objects.filter(pk=self.model.pk).values_list(
                        'name', 'image_url', 'is_published_online').get())

        stock, before = self.stock(), snapshot()
        self.applied(['phone-9'])
        after_first = self.counts()
        second = self.applied(['phone-9'])
        self.assertEqual(self.stock(), stock)            # zero Product, BranchStock, StockUnit or StockMovement
        self.assertEqual(snapshot(), before)             # prices, photos, names and publication untouched
        self.assertEqual(self.counts(), after_first)     # the second import writes no catalogue row
        self.assertEqual(second['created'], {'models': [], 'attributes': [], 'options': []})
        keys = list(VariantOption.objects.filter(company=self.company).exclude(reference_key='')
                    .values_list('reference_key', flat=True))
        self.assertEqual(len(keys), len(set(keys)), 'an option was duplicated')
        self.assertEqual(Product.objects.filter(product_model=self.model).count(), 1)
        self.assertEqual(ProductModel.objects.filter(company=self.company).count(), 1)

    def test_a_renamed_option_stays_renamed_and_is_never_duplicated(self):
        self.applied(['phone-9'])
        self.ink.refresh_from_db()
        device_catalog.update_option(self.ink, label='Negro tinta')
        self.applied(['phone-9', 'phone-9-pro'])
        self.ink.refresh_from_db()
        self.assertEqual(self.ink.label, 'Negro tinta')
        self.assertEqual(VariantOption.objects.filter(company=self.company, reference_key='color:ink').count(), 1)
        self.assertFalse(VariantOption.objects.filter(company=self.company, label__iexact='tinta').exists())
        self.assertIn(self.ink.pk, ModelOption.objects.filter(model=self.tenant_model('phone-9-pro'))
                      .values_list('option_id', flat=True))

    def test_a_renamed_model_is_found_by_its_reference(self):
        self.applied(['phone-9'])
        self.model.refresh_from_db()
        device_catalog.update_model(self.model, name='Mi teléfono nueve')
        item = self.plan(['phone-9'])['phone-9']
        self.assertEqual((item['state'], item['model_id']), ('reused', self.model.pk))

    def test_a_model_holding_another_reference_is_a_conflict_and_apply_refuses(self):
        ProductModel.objects.filter(pk=self.model.pk).update(reference_key='something-else')
        item = self.plan(['phone-9'])['phone-9']
        self.assertEqual(item['state'], 'conflict')
        self.assertTrue(item['reason'])
        before = self.counts()
        response = self.apply(['phone-9'])
        self.assertEqual((response.status_code, response.json()['code']), (400, 'conflict'))
        self.assertEqual(self.counts(), before)

    def test_an_option_holding_another_reference_is_a_conflict(self):
        VariantOption.objects.filter(pk=self.ink.pk).update(reference_key='color:other')
        item = self.plan(['phone-9'])['phone-9']
        self.assertEqual(item['state'], 'conflict')
        self.assertEqual({o['reference_key']: o['status'] for o in item['options']}['color:ink'], 'conflict')

    def test_an_option_alias_with_the_label_is_a_conflict(self):
        from store import catalog_aliases

        catalog_aliases.create_alias(company=self.company, scope='option', alias='arena', option_id=self.ink.pk,
                                     actor=self.staff)
        item = self.plan(['phone-9'])['phone-9']
        self.assertEqual({o['reference_key']: o['status'] for o in item['options']}['color:sand'], 'conflict')
        self.assertEqual(item['state'], 'conflict')


class ReviewFindingsTest(_Shop, TestCase):
    """What the independent review of #371 found, held by a test each."""

    def setUp(self):
        self.start('ref-review')

    def test_two_references_reaching_one_unlinked_option_by_label_are_a_conflict(self):
        # An existing colour «Tinta», not linked yet; two reference keys of two models read as «Tinta».
        colour = device_catalog.create_attribute(company=self.company, name='Color', kind='color', role='color')
        ink = device_catalog.create_option(company=self.company, attribute_id=colour.pk, label='Tinta',
                                           swatch_hex='#111111')
        data = dataset()
        data['models'][1]['colors'][0] = {'key': 'deep-ink', 'label_en': 'Deep Ink', 'label_es': 'Tinta',
                                          'official': {'en': 'Deep Ink', 'es_419': 'Tinta', 'es_es': 'Tinta'}}
        loader.load(data)
        items = self.plan(['phone-9', 'phone-9-pro'])
        self.assertNotEqual(items['phone-9']['state'], 'conflict')
        self.assertEqual(items['phone-9-pro']['state'], 'conflict')
        statuses = {o['reference_key']: o['status'] for o in items['phone-9-pro']['options']}
        self.assertEqual(statuses['color:deep-ink'], 'conflict')
        # Applied alone, the first links it; the second is then a conflict too (never reused without its link).
        self.applied(['phone-9'])
        ink.refresh_from_db()
        self.assertEqual(ink.reference_key, 'color:ink')
        self.assertEqual(self.plan(['phone-9-pro'])['phone-9-pro']['state'], 'conflict')

    def test_an_attribute_the_palette_lacks_is_warned_when_the_model_has_variants(self):
        capacity = device_catalog.create_attribute(company=self.company, name='Capacidad', role='capacity')
        gb128 = device_catalog.create_option(company=self.company, attribute_id=capacity.pk, label='128 GB')
        model = device_catalog.create_model(company=self.company, name='Phone 9')
        model_palette.set_palette(model, option_ids=[gb128.pk])
        product = _prod(self.company, 'Phone 9 128', 'p9-128', price='10.00', inventory=0)
        device_catalog.set_variant(product, model_id=model.pk, condition_line='new', option_ids=[gb128.pk])
        item = self.plan(['phone-9'])['phone-9']
        self.assertEqual(item['state'], 'reused')
        warning = ' '.join(item['warnings'])
        for role in ('Color', 'SIM', 'Región'):
            self.assertIn(role, warning)
        self.assertIn('incompleta', warning)

    def test_no_warning_when_the_reused_model_has_no_variants(self):
        device_catalog.create_model(company=self.company, name='Phone 9')
        self.assertEqual(self.plan(['phone-9'])['phone-9']['warnings'], [])

    def test_a_palette_saved_in_between_is_never_overwritten(self):
        # The plan read an empty palette; somebody saves one before the import writes: 409, nothing replaced.
        model = device_catalog.create_model(company=self.company, name='Phone 9')
        capacity = device_catalog.create_attribute(company=self.company, name='Capacidad', role='capacity')
        mine = device_catalog.create_option(company=self.company, attribute_id=capacity.pk, label='64 GB')
        real = model_palette.set_palette

        def saved_in_between(target, **kwargs):
            ModelOption.objects.get_or_create(company=self.company, model=target, option=mine)
            return real(target, **kwargs)

        from unittest import mock

        with mock.patch.object(model_palette, 'set_palette', saved_in_between):
            response = self.apply(['phone-9'])
        self.assertEqual((response.status_code, response.json()['code']), (409, 'stale'))
        self.assertEqual(list(ModelOption.objects.filter(model=model).values_list('option_id', flat=True)), [])

    def test_the_company_lock_does_not_block_rows_that_only_reference_the_company(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as captured:
            self.applied(['phone-9'])
        company_locks = [q['sql'] for q in captured.captured_queries
                         if 'FOR' in q['sql'] and '"store_company"' in q['sql'].split('FOR')[0]
                         and 'UPDATE' in q['sql'].split('FOR')[-1]]
        self.assertTrue(company_locks)
        self.assertTrue(all('FOR NO KEY UPDATE' in sql for sql in company_locks), company_locks)


class TenancyAndPermissionsTest(_Shop, TestCase):
    def setUp(self):
        self.start('ref-ten')
        self.other = _p3_company('ref-ten-b', 'Otra B')
        self.other_staff, _ = _p2d_member(self.other, 'ref_ten_b_staff', ['company.view', 'products.view',
                                                                           'products.manage'])

    def test_another_companys_catalogue_is_untouched(self):
        self.apply(['phone-9'], client=_client(self.other_staff), company=self.other)
        theirs = self.counts(self.other)
        self.applied(['phone-9', 'phone-9-pro'])
        self.assertEqual(self.counts(self.other), theirs)
        # Each company has its own rows, never the other's.
        mine = set(VariantOption.objects.filter(company=self.company).values_list('pk', flat=True))
        self.assertFalse(mine & set(VariantOption.objects.filter(company=self.other).values_list('pk', flat=True)))
        self.assertTrue(set(ModelOption.objects.filter(company=self.company).values_list('option_id', flat=True))
                        <= mine)

    def test_a_company_that_never_imports_sees_nothing(self):
        self.applied(['phone-9'])
        self.assertEqual(self.counts(self.other), (0, 0, 0, 0, 0))
        item = self.plan(['phone-9'])['phone-9']
        self.assertEqual(item['state'], 'reused')
        response = self.preview(['phone-9'], client=_client(self.other_staff), company=self.other)
        self.assertEqual(response.json()['items'][0]['state'], 'new')

    def test_acting_on_another_company_is_refused(self):
        response = self.apply(['phone-9'], company=self.other)
        self.assertIn(response.status_code, (403, 404))
        self.assertEqual(self.counts(self.other), (0, 0, 0, 0, 0))

    def test_viewers_preview_but_cannot_apply(self):
        viewer, _ = _p2d_member(self.company, 'ref_ten_viewer', ['company.view', 'products.view'])
        client = _client(viewer)
        self.assertEqual(client.get(LIST + self.q).status_code, 200)
        self.assertEqual(self.preview(['phone-9'], client=client).status_code, 200)
        self.assertEqual(self.apply(['phone-9'], client=client).status_code, 403)
        self.assertEqual(self.counts(), (0, 0, 0, 0, 0))

    def test_without_catalogue_capabilities_nothing_is_readable(self):
        nobody, _ = _p2d_member(self.company, 'ref_ten_nobody', ['company.view'])
        client = _client(nobody)
        self.assertEqual(client.get(LIST + self.q).status_code, 403)
        self.assertEqual(self.preview(['phone-9'], client=client).status_code, 403)
        self.assertEqual(_client().post(APPLY + self.q, {'keys': ['phone-9']}, format='json').status_code, 401)


class ApplyAtOnceTest(_Shop, TransactionTestCase):
    def setUp(self):
        self.start('ref-race')

    def test_two_people_applying_the_same_models_create_them_once(self):
        barrier, results = threading.Barrier(2), [None, None]
        stock = self.stock()

        def run(index):
            try:
                client = _client(self.staff)
                barrier.wait(timeout=10)
                results[index] = client.post(APPLY + self.q, {'keys': ['phone-9', 'phone-9-pro']}, format='json')
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
        self.assertEqual(ProductModel.objects.filter(company=self.company).count(), 2)
        self.assertEqual(VariantOption.objects.filter(company=self.company, reference_key='color:ink').count(), 1)
        self.assertEqual(sorted(len(r.json()['created']['models']) for r in results), [0, 2])
        self.assertEqual(self.stock(), stock)


class ApplyAgainstAModelLockTest(_Shop, TransactionTestCase):
    """
    Review of #371 (deadlock): a screen that locks a model and then writes a row referencing the company (an audit
    entry) must not deadlock against an import that holds the company lock and then needs that model.
    """

    def setUp(self):
        self.start('ref-deadlock')

    def test_an_import_and_a_model_save_both_finish(self):
        import time

        from django.db import transaction

        self.applied(['phone-9'])
        model = self.tenant_model('phone-9')
        ids = sorted(ModelOption.objects.filter(model=model).values_list('option_id', flat=True))
        model_palette.set_palette(model, option_ids=ids[1:])   # a re-import must lock the model to add it back
        locked, results = threading.Event(), {}

        def screen():
            try:
                with transaction.atomic():
                    ProductModel.objects.select_for_update().get(pk=model.pk)
                    locked.set()
                    time.sleep(1.5)
                    AdminAuditLog.log(actor=None, action='screen_save', target_type='product_model',
                                      target_id=model.pk, company=self.company)
                results['screen'] = 'ok'
            except Exception as failed:  # noqa: BLE001 — the outcome under test
                results['screen'] = repr(failed)[:200]
            finally:
                db_connection.close()

        def apply():
            try:
                locked.wait(10)
                importer.apply(company=self.company, keys=['phone-9'], actor=self.staff)
                results['import'] = 'ok'
            except Exception as failed:  # noqa: BLE001 — the outcome under test
                results['import'] = repr(failed)[:200]
            finally:
                db_connection.close()

        threads = [threading.Thread(target=screen), threading.Thread(target=apply)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual(results, {'screen': 'ok', 'import': 'ok'})
        self.assertEqual(sorted(ModelOption.objects.filter(model=model).values_list('option_id', flat=True)), ids)


class CommittedFileImportTest(TestCase):
    """The committed files import whole into an empty company without a conflict, and a second apply changes nothing."""

    def test_every_complete_model_of_each_file_imports_cleanly(self):
        from store import catalog_reference as ref

        data_dir = Path(__file__).resolve().parent.parent / 'catalog_reference'
        company = _p3_company('ref-real', 'Real')
        staff, _ = _p2d_member(company, 'ref_real_staff', ['company.view', 'products.view', 'products.manage'])
        for path in sorted(data_dir.glob('*.json')):
            data = ref.load(path)
            loader.load(data)
            keys = [m['key'] for m in data['models']][:importer.MAX_KEYS]
            approve = [m['key'] for m in data['models'] if m['completeness'] != 'complete']
            plan = importer.preview(company, keys)
            self.assertEqual(plan['counts']['conflict'], 0, [i['reason'] for i in plan['items'] if i['reason']])
            importer.apply(company=company, keys=keys, approve=approve, actor=staff)
            again = importer.preview(company, keys)
            self.assertFalse(any(i['changes'] for i in again['items']), path.name)
        self.assertFalse(Product.objects.filter(company=company).exists())
        self.assertFalse(ProductModel.objects.filter(company=company, is_published_online=True).exists())


@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class RealDatasetConfiguratorTest(TestCase):
    """G (owner's priority 4): two neighbouring models of the COMMITTED file, imported into one company, offer exactly
    their own capacities, colours, SIM and regions — in the till's configurator and on the public model page — and
    nothing of the other model (the company shares one option per reference key between them)."""

    KEYS = ('iphone-17-pro', 'iphone-16')

    def setUp(self):
        import json

        from store import catalog_reference as ref

        path = Path(__file__).resolve().parent.parent / 'catalog_reference' / 'apple-iphone.json'
        loader.load(ref.load(path))
        self.raw = {m['key']: m for m in json.loads(path.read_text(encoding='utf-8'))['models']}
        self.company = _p3_company('refreal', 'Tienda real')
        self.staff, _ = _p2d_member(self.company, 'refreal_staff', [
            'company.view', 'products.view', 'products.manage', 'sales.pos.use', 'inventory.view'])
        importer.apply(company=self.company, keys=list(self.KEYS), actor=self.staff)
        from store.models import Category

        self.category = Category.objects.create(company=self.company, name='Teléfonos', slug='refreal-telefonos')

    def expected(self, key):
        from store import catalog_reference as ref

        model = self.raw[key]
        return {
            'capacity': {ref.capacity_label(gb) for gb in model['capacities_gb']},
            'color': {c['label_es'] for c in model['colors']},
            'sim': {s['label_es'] for s in model['sim']},
            'region': {s['region_label_es'] for s in model['sim']},
        }

    def test_the_till_offers_exactly_each_models_dataset_options(self):
        branch = self.company.default_inventory_branch
        api = _client(self.staff)
        for key in self.KEYS:
            model = ProductModel.objects.get(company=self.company, reference_key=key)
            res = api.get(f'/api/admin/pos/models/{model.pk}/configurations/?company={self.company.pk}&branch={branch.pk}')
            self.assertEqual(res.status_code, 200, res.content)
            offered = {a['role']: {o['label'] for o in a['options']} for a in res.json()['attributes']}
            self.assertEqual(offered, self.expected(key), key)

    def test_the_public_page_offers_exactly_each_models_capacities_and_colours(self):
        """Review of #377: the variants are made straight from the FILE (each capacity × colour of the dataset, with the
        dataset's first SIM entry), not from what the till offers, and the public page is compared with the file."""
        from store import catalog_reference as ref

        public = _client(host=f'{self.company.slug}.tienda.pe')
        option = lambda role, key: VariantOption.objects.get(company=self.company, reference_key=f'{role}:{key}')
        for key in self.KEYS:
            raw = self.raw[key]
            model = ProductModel.objects.get(company=self.company, reference_key=key)
            entry = raw['sim'][0]
            sim, region = option('sim', entry['key']), option('region', ref.region_key(entry['regions']))
            for gb in raw['capacities_gb']:
                for colour in raw['colors']:
                    ids = [option('capacity', ref.capacity_key(gb)).pk, option('color', colour['key']).pk, sim.pk, region.pk]
                    product = device_catalog.create_variant_product(self.company, model, condition_line='new',
                                                                    option_ids=ids, actor=self.staff, request=None)
                    # The palette's draft goes through its one commercial transition, as from the panel.
                    Product.objects.filter(pk=product.pk).update(price='999.00', category=self.category)
                    device_catalog.enable_variant_for_sale(product, publish=True, actor=self.staff, request=None)
            ProductModel.objects.filter(pk=model.pk).update(is_published_online=True)
            res = public.get(f'/api/catalog/models/{model.slug}/')
            self.assertEqual(res.status_code, 200, res.content)
            shown = {a.get('role'): [o['label'] for o in sorted(a['options'], key=lambda o: o['sort_order'])]
                     for a in res.json()['attributes']}
            # Exactly the file's capacities, read by size, and exactly the file's colours (its Spanish names).
            self.assertEqual(shown['capacity'], [ref.capacity_label(gb) for gb in raw['capacities_gb']], key)
            self.assertEqual(set(shown['color']), {c['label_es'] for c in raw['colors']}, key)
            self.assertEqual(len(res.json()['variants']), len(raw['capacities_gb']) * len(raw['colors']), key)
        # The two models share the company's options by reference key, yet neither shows the other's.
        self.assertNotEqual(self.expected(self.KEYS[0])['color'], self.expected(self.KEYS[1])['color'])
        self.assertFalse(Product.objects.filter(company=self.company, is_serialized=True).exists())
        self.assertFalse(StockUnit.objects.filter(company=self.company).exists())


class NoVocabularyInTheEngineTest(TestCase):
    """AGENTS §2, second exception: the names live only in `backend/catalog_reference/`, never in the engine."""

    WORDS = (r'\b(negro|blanco|azul|naranja|plata|borgo[nñ]a|glaciar|black|white|blue|silver|gold|apple|iphone|ipad|'
             r'samsung|galaxy|black dog|lla|japan|japon|china|united states)\b')

    def code(self, source):
        source = re.sub(r'(?s)""".*?"""', '', source)
        return '\n'.join(line.split('#', 1)[0] for line in source.splitlines())

    def test_the_importer_loader_views_and_command_name_nothing(self):
        from store import catalog_reference_views
        from store.management.commands import load_reference_catalog

        for module in (importer, loader, catalog_reference_views, load_reference_catalog, model_palette):
            source = self.code(Path(module.__file__).read_text(encoding='utf-8'))
            self.assertIsNone(re.search(self.WORDS, source, re.I), (module.__name__, re.search(self.WORDS, source, re.I)))

    def test_the_new_models_name_nothing(self):
        for cls in (ReferenceModel, ReferenceOption, ReferenceModelNumber, ModelOptionExclusion):
            source = self.code(inspect.getsource(cls))
            self.assertIsNone(re.search(self.WORDS, source, re.I), (cls.__name__, re.search(self.WORDS, source, re.I)))
