"""
CATALOG-COMMERCIAL-01 · A1b-2a — variantes creadas desde la paleta: borrador pendiente, habilitación
comercial, cinturones (DEC-CATALOG-VARIANT-DRAFT-01, propuesta del 2026-11-12, T1–T11).

Lo que estas pruebas fijan:

  * `POST /api/admin/catalog/models/<pk>/variants/` crea el `Product` que ES una combinación completa de
    la paleta: oculto, precio 0,00, sin serializar, `sale_mode='stock'`, `draft_origin='palette'`, código
    generado, sin stock; sólo `products.manage` (ni `inventory.adjust`); modelo u opciones ajenos → 404;
  * `draft_origin` es procedencia INMUTABLE y nunca autoriza; pendiente = `draft_origin != ''` y
    `sale_enabled_at IS NULL` (`Product.is_pending_draft` / `pending_draft_q()`), una sola definición;
  * `POST /api/admin/products/<pk>/enable-sale/` es la ÚNICA transición: exige configuración completa en la
    paleta, precio > 0 (seminuevos: precio real por equipo, nunca «sin precio» como autorización),
    categoría e imagen si la empresa lo pide; escribe `sale_enabled_at/by`; auditoría con `company`;
    segunda vez 409; dos a la vez → exactamente una habilita;
  * ninguna otra ruta habilita un borrador pendiente: formulario, editor de variante, bloque, carga,
    propuesta de catálogo, visibilidad del modelo, `set_sale_mode`, `sells_on_order`, caja, tienda,
    API v1 (incluso con `is_published_online` forzado por ORM);
  * las filas históricas (`draft_origin=''`) se comportan exactamente como hoy (seis casos de §D);
  * el contrato público no cambia.

Nada aquí es de Apple ni de la tienda piloto.
"""
from datetime import timedelta
from decimal import Decimal as D
from threading import Barrier, Thread

from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from store import (
    catalog_proposal, device_catalog, model_palette, pos_services, product_publication, registration_services,
    supply_services,
)
from store.models import (
    AdminAuditLog, BranchStock, Category, CompanySettings, ModelOption, Product, StockMovement, StockUnit,
)
from store.tenancy import company_storefront_products
from store.test_device_commerce import _client
from store.test_model_palette import PaletteShop
from store.tests import _p2d_member, _prod


def _unit(unit):
    """A device on a till line, identified by hand."""
    return {'id': unit.pk, 'method': 'manual'}


class DraftShop(PaletteShop):
    """`PaletteShop` with a palette on the model, a category, and staff of every relevant scope."""

    def build(self, slug='a1b2-borrador'):
        super().build(slug)
        self.palette(self.gb128, self.gb256, self.black, self.blue)
        self.category = Category.objects.create(company=self.company, name='Teléfonos', slug=f'{slug}-tel')
        self.manager, _ = _p2d_member(self.company, f'{slug}_manager', ['company.view', 'products.view', 'products.manage'])
        self.viewer, _ = _p2d_member(self.company, f'{slug}_viewer', ['company.view', 'products.view'])
        self.adjuster, _ = _p2d_member(self.company, f'{slug}_adjuster',
                                       ['company.view', 'products.view', 'inventory.view', 'inventory.adjust'])
        self.manage_api = _client(self.manager)
        self.view_api = _client(self.viewer)
        self.adjust_api = _client(self.adjuster)

    def draft(self, *options, line=Product.CONDITION_NEW, name=None, actor=None):
        options = options or (self.gb256, self.blue)
        return device_catalog.create_variant_product(
            self.company, self.model, condition_line=line, option_ids=[o.pk for o in options], name=name,
            actor=actor or self.manager, request=None)

    def priced(self, *options, line=Product.CONDITION_NEW, price='3700.00', category=True):
        product = self.draft(*options, line=line)
        Product.objects.filter(pk=product.pk).update(price=D(price), category=self.category if category else None)
        product.refresh_from_db()
        return product

    def enable(self, product, client=None, **body):
        return (client or self.manage_api).post(f'/api/admin/products/{product.pk}/enable-sale/{self.q}', body,
                                                format='json')

    def create(self, options, client=None, line='new', **extra):
        return (client or self.manage_api).post(
            f'/api/admin/catalog/models/{self.model.pk}/variants/{self.q}',
            {'condition_line': line, 'option_ids': [o.pk for o in options], **extra}, format='json')


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------

class CreateVariantTest(DraftShop, TestCase):
    def setUp(self):
        self.build('a1b2-crea')

    def test_the_service_creates_a_hidden_unpriced_unserialized_draft_that_is_the_combination(self):
        before = (BranchStock.objects.count(), StockUnit.objects.count(), StockMovement.objects.count())
        product = self.draft()
        self.assertEqual(product.name, 'Teléfono X 256 GB Azul')
        self.assertEqual((product.draft_origin, product.sale_enabled_at, product.sale_enabled_by_id),
                         ('palette', None, None))
        self.assertTrue(product.is_pending_draft)
        self.assertFalse(product.is_sold_online)
        self.assertEqual((product.is_published_online, product.price, product.is_serialized, product.requires_imei,
                          product.sale_mode, product.is_active),
                         (False, D('0.00'), False, False, Product.SALE_MODE_STOCK, True))
        self.assertEqual((product.product_model_id, product.condition_line, product.variant_key),
                         (self.model.pk, 'new', device_catalog.variant_key([self.gb256.pk, self.blue.pk])))
        self.assertEqual(sorted(o.pk for o in device_catalog.variant_options_of(product)),
                         sorted([self.gb256.pk, self.blue.pk]))
        self.assertTrue(product.barcodes.filter(is_active=True).exists())
        self.assertEqual(product.category_id, self.model.category_id)
        self.assertEqual((BranchStock.objects.count(), StockUnit.objects.count(), StockMovement.objects.count()), before)
        self.assertIn('no_price', product_publication.publication_problems(product))
        log = AdminAuditLog.objects.get(action='product_variant_created_from_palette', target_id=str(product.pk))
        self.assertEqual((log.company_id, log.actor_id), (self.company.pk, self.manager.pk))
        self.assertEqual(log.metadata['name_generated'], True)
        self.assertEqual(log.metadata['draft_origin'], 'palette')
        self.assertEqual(log.metadata['is_published_online'], False)
        self.assertTrue(AdminAuditLog.objects.filter(action='product_variant_set', target_id=str(product.pk),
                                                     company=self.company).exists())

    def test_the_name_follows_the_role_order_and_the_used_line_gets_the_suffix(self):
        self.palette(self.gb128, self.gb256, self.black, self.blue, self.esim, self.chip, self.lla, self.ja)
        # Roles in reading order whatever the attributes' own sort order says.
        from store.models import VariantAttribute
        VariantAttribute.objects.filter(pk=self.region.pk).update(sort_order=0)
        VariantAttribute.objects.filter(pk=self.capacity.pk).update(sort_order=9)
        product = self.draft(self.lla, self.esim, self.blue, self.gb256, line=Product.CONDITION_USED)
        self.assertEqual(product.name, 'Teléfono X 256 GB Azul eSIM LL/A Seminuevo')
        self.assertEqual(device_catalog.variant_name(self.model, device_catalog.variant_options_of(product),
                                                     condition_line=Product.CONDITION_USED), product.name)

    def test_the_operator_may_name_it_and_the_audit_says_so(self):
        product = self.draft(name='  Mi   variante ')
        self.assertEqual(product.name, 'Mi variante')
        log = AdminAuditLog.objects.get(action='product_variant_created_from_palette', target_id=str(product.pk))
        self.assertFalse(log.metadata['name_generated'])

    def test_the_proposal_and_the_endpoint_name_the_same_combination_alike(self):
        from store.models import VariantAttribute
        VariantAttribute.objects.filter(pk=self.capacity.pk).update(role='capacity')
        VariantAttribute.objects.filter(pk=self.colour.pk).update(role='color')
        plan = catalog_proposal.propose(self.company, [
            {'key': 'a', 'model': 'Teléfono X', 'capacityGb': 256, 'colour': 'azul', 'line': 'used'}])['plan']
        self.assertEqual(plan['variants'][0]['suggested_name'], 'Teléfono X 256 GB Azul Seminuevo')
        self.assertEqual(self.draft(self.gb256, self.blue, line='used').name, 'Teléfono X 256 GB Azul Seminuevo')

    def test_it_refuses_what_the_palette_refuses_and_never_half_writes(self):
        count = Product.objects.filter(company=self.company).count()
        for options, code in (((self.gb256, self.red), 'option_not_in_palette'), ((self.gb256,), 'variant_incomplete'),
                              ((self.gb128, self.black), 'duplicate')):
            with self.subTest(code=code), self.assertRaises(device_catalog.CatalogError) as caught:
                self.draft(*options)
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(Product.objects.filter(company=self.company).count(), count)
        self.assertFalse(Product.objects.filter(company=self.company, draft_origin='palette').exists())

    def test_the_endpoint_needs_products_manage_only_and_answers_404_for_what_is_foreign(self):
        res = self.create([self.gb256, self.blue])
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual((body['name'], body['draft_origin'], body['is_pending_draft'], body['price'],
                          body['is_published_online']), ('Teléfono X 256 GB Azul', 'palette', True, '0.00', False))
        self.assertEqual(self.create([self.gb128, self.blue], client=self.view_api).status_code, 403)
        self.assertEqual(self.create([self.gb128, self.blue], client=self.adjust_api).status_code, 403)
        self.assertEqual(self.create([self.gb128, self.blue], client=self.manage_api).status_code, 201)
        # Second time: duplicate.
        self.assertEqual(self.create([self.gb128, self.blue]).json()['code'], 'duplicate')
        self.assertEqual(self.create([self.gb256]).json()['code'], 'variant_incomplete')
        self.assertEqual(self.create([self.gb256, self.red]).json()['code'], 'option_not_in_palette')
        # A foreign model, and foreign options.
        other = DraftShop()
        other.build('a1b2-crea-b')
        res = self.manage_api.post(f'/api/admin/catalog/models/{other.model.pk}/variants/{self.q}',
                                   {'condition_line': 'new', 'option_ids': [other.gb256.pk, other.blue.pk]}, format='json')
        self.assertEqual(res.status_code, 404)
        res = self.create([other.gb256, other.blue])
        self.assertEqual(res.status_code, 404, res.content)
        self.assertFalse(Product.objects.filter(company=other.company, draft_origin='palette').exists())
        # Bad bodies are 400, never 500.
        for body in ({'option_ids': 'x'}, {'option_ids': [True]}, {'option_ids': [1], 'condition_line': 'broken'},
                     {'option_ids': [self.gb256.pk, self.blue.pk], 'name': 'x' * 300}):
            res = self.manage_api.post(f'/api/admin/catalog/models/{self.model.pk}/variants/{self.q}', body, format='json')
            self.assertEqual(res.status_code, 400, body)


# ---------------------------------------------------------------------------
# Enabling for sale
# ---------------------------------------------------------------------------

class EnableForSaleTest(DraftShop, TestCase):
    def setUp(self):
        self.build('a1b2-habilita')

    def test_an_unpriced_draft_is_not_ready(self):
        product = self.draft()
        res = self.enable(product)
        self.assertEqual((res.status_code, res.json()['code']), (400, 'not_ready'), res.content)
        self.assertIn('no_price', res.json()['problems'])
        self.assertIn('no_category', res.json()['problems'])
        self.assertNotIn('draft_pending', res.json()['problems'])
        product.refresh_from_db()
        self.assertTrue(product.is_pending_draft)
        self.assertFalse(AdminAuditLog.objects.filter(action='product_variant_enabled').exists())

    def test_pricing_is_audited_and_then_enabling_writes_the_transition_once(self):
        product = self.draft()
        res = self.view_api.patch(f'/api/admin/products/{product.pk}/{self.q}', {'price': '3700.00'}, format='json')
        self.assertEqual(res.status_code, 403)
        res = self.manage_api.patch(f'/api/admin/products/{product.pk}/{self.q}',
                                    {'price': '3700.00', 'category': self.category.pk}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(AdminAuditLog.objects.get(action='product_updated', target_id=str(product.pk),
                                                   company=self.company).metadata['changed_fields']['price']['new'], '3700.00')
        product.refresh_from_db()
        self.assertTrue(product.is_pending_draft)       # priced is not enabled
        self.assertEqual(self.enable(product, client=self.view_api).status_code, 403)
        self.assertEqual(self.enable(product, client=self.adjust_api).status_code, 403)
        res = self.enable(product)
        self.assertEqual(res.status_code, 200, res.content)
        product.refresh_from_db()
        self.assertFalse(product.is_pending_draft)
        self.assertEqual((product.draft_origin, product.sale_enabled_by_id, product.is_published_online),
                         ('palette', self.manager.pk, False))
        self.assertIsNotNone(product.sale_enabled_at)
        from django.utils.dateparse import parse_datetime
        self.assertEqual(parse_datetime(res.json()['sale_enabled_at']), product.sale_enabled_at)
        log = AdminAuditLog.objects.get(action='product_variant_enabled', target_id=str(product.pk))
        self.assertEqual((log.company_id, log.actor_id, log.metadata['published']), (self.company.pk, self.manager.pk, False))
        self.assertEqual(log.metadata['model_id'], self.model.pk)
        again = self.enable(product)
        self.assertEqual((again.status_code, again.json()['code']), (409, 'already_enabled'))
        self.assertEqual(AdminAuditLog.objects.filter(action='product_variant_enabled').count(), 1)

    def test_a_configuration_the_palette_no_longer_admits_is_not_ready(self):
        product = self.priced()
        ModelOption.objects.filter(model=self.model, option=self.blue).delete()
        res = self.enable(product)
        self.assertEqual((res.status_code, res.json()['problems']), (400, ['fuera_de_paleta']))
        # Incomplete: the palette gains a role the draft lacks.
        ModelOption.objects.create(company=self.company, model=self.model, option=self.blue)
        self.palette(self.gb128, self.gb256, self.black, self.blue, self.esim)
        res = self.enable(product)
        self.assertEqual((res.status_code, res.json()['problems']), (400, ['incompleta']))
        product.refresh_from_db()
        self.assertTrue(product.is_pending_draft)

    def test_a_used_draft_needs_real_unit_prices_never_the_exception_as_authorisation(self):
        product = self.priced(self.gb256, self.blue, line=Product.CONDITION_USED, price='0.00')
        # No device at all: nothing has a price.
        self.assertEqual(self.enable(product).json()['problems'], ['no_price'])
        # A device without a price: still not ready.
        Product.objects.filter(pk=product.pk).update(is_serialized=True, requires_imei=True)
        product.refresh_from_db()
        received = self.receive(product, 1, cost='900.00')
        self.assertEqual(self.enable(product).json()['problems'], ['no_price'])
        StockUnit.objects.filter(product=product).update(sale_price=D('1500.00'))
        res = self.enable(product)
        self.assertEqual(res.status_code, 200, res.content)
        product.refresh_from_db()
        self.assertEqual(product.price, D('0.00'))
        self.assertFalse(product.is_pending_draft)
        self.assertIsNotNone(received)

    def test_publish_true_publishes_this_product_only_in_the_same_transaction_or_nothing(self):
        product = self.priced(category=False)
        res = self.enable(product, publish=True)
        self.assertEqual((res.status_code, res.json()['code']), (400, 'not_ready'))
        self.assertEqual(res.json()['problems'], ['no_category'])
        product.refresh_from_db()
        self.assertTrue(product.is_pending_draft)
        self.assertFalse(product.is_published_online)
        Product.objects.filter(pk=product.pk).update(category=self.category)
        other = self.priced(self.gb128, self.blue)       # another pending draft of the same model
        type(self.model).objects.filter(pk=self.model.pk).update(is_published_online=False)
        res = self.enable(product, publish=True)
        self.assertEqual(res.status_code, 200, res.content)
        product.refresh_from_db()
        other.refresh_from_db()
        self.model.refresh_from_db()
        self.assertTrue(product.is_published_online)
        self.assertTrue(product.is_sold_online)
        self.assertFalse(product.is_pending_draft)
        self.assertTrue(other.is_pending_draft)
        self.assertFalse(other.is_published_online)
        self.assertFalse(self.model.is_published_online)
        self.assertTrue(AdminAuditLog.objects.get(action='product_variant_enabled', target_id=str(product.pk))
                        .metadata['published'])
        self.assertTrue(AdminAuditLog.objects.filter(action='product_updated', target_id=str(product.pk),
                                                     metadata__via='enable_sale').exists())

    def test_a_historical_row_and_a_foreign_row_cannot_be_enabled(self):
        self.assertFalse(self.new128.is_pending_draft)
        res = self.enable(self.new128)
        self.assertEqual((res.status_code, res.json()['code']), (400, 'not_a_draft'))
        other = DraftShop()
        other.build('a1b2-habilita-b')
        foreign = other.priced()
        self.assertEqual(self.enable(foreign).status_code, 404)
        foreign.refresh_from_db()
        self.assertTrue(foreign.is_pending_draft)
        self.assertEqual(self.enable(other.priced(other.gb128, other.blue), client=self.view_api).status_code, 403)

    def test_the_cycle_keeps_provenance_and_the_transition_and_never_returns_to_pending(self):
        product = self.priced()
        self.assertEqual(self.enable(product).status_code, 200)
        enabled_at = Product.objects.get(pk=product.pk).sale_enabled_at
        url = f'/api/admin/products/{product.pk}/{self.q}'
        # The form publishes WITHOUT a gate (2026-10-13 rule), hides, republishes.
        for value in (True, False, True):
            res = self.manage_api.patch(url, {'is_published_online': value}, format='json')
            self.assertEqual(res.status_code, 200, res.content)
            product.refresh_from_db()
            self.assertEqual(product.is_published_online, value)
            self.assertEqual((product.draft_origin, product.sale_enabled_at, product.is_pending_draft),
                             ('palette', enabled_at, False))
        self.assertIn(product, company_storefront_products(self.company))
        # The till sells it once it has stock.
        BranchStock.objects.update_or_create(branch=self.branch, product=product, defaults={'quantity': 2})
        sale = pos_services.build_pos_sale(operator=self.staff, company=self.company, branch=self.branch,
                                           items=[{'product': product.pk, 'quantity': 1}], require_units=False,
                                           validate_cash=False)
        self.assertEqual(sale['products'][product.pk].pk, product.pk)
        # And «bajo pedido» may now be chosen, with its own permission.
        supply_services.set_sale_mode(product, Product.SALE_MODE_STOCK_OR_ORDER, actor=self.manager)


class EnableRaceTest(TransactionTestCase):
    """Two people enable the same draft at the same instant: exactly one transition, one audit line."""

    def test_only_one_call_enables(self):
        shop = DraftShop()
        shop.build('a1b2-carrera')
        product = shop.priced()
        barrier, outcomes = Barrier(2), []

        def enable():
            try:
                barrier.wait(timeout=5)
                device_catalog.enable_variant_for_sale(Product.objects.get(pk=product.pk), actor=shop.manager, request=None)
                outcomes.append('enabled')
            except device_catalog.CatalogError as exc:
                outcomes.append(exc.code)
            finally:
                connection.close()

        threads = [Thread(target=enable) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertEqual(sorted(outcomes), ['already_enabled', 'enabled'])
        self.assertEqual(AdminAuditLog.objects.filter(action='product_variant_enabled', target_id=str(product.pk)).count(), 1)
        self.assertIsNotNone(Product.objects.get(pk=product.pk).sale_enabled_at)


# ---------------------------------------------------------------------------
# No route enables a pending draft (T3 / T5)
# ---------------------------------------------------------------------------

@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class BypassRoutesTest(DraftShop, TestCase):
    def setUp(self):
        self.build('a1b2-rutas')
        self.product = self.priced()        # priced, with a category: only the transition is missing

    def unchanged(self):
        self.product.refresh_from_db()
        self.assertTrue(self.product.is_pending_draft)
        self.assertFalse(self.product.is_published_online)

    def test_the_product_form_refuses_to_publish_it(self):
        res = self.manage_api.patch(f'/api/admin/products/{self.product.pk}/{self.q}',
                                    {'is_published_online': True}, format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn('is_published_online', res.json())
        self.unchanged()
        # Hiding, pricing and renaming are not publishing.
        res = self.manage_api.patch(f'/api/admin/products/{self.product.pk}/{self.q}',
                                    {'is_published_online': False, 'name': 'Otro nombre', 'price': '3800.00'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.unchanged()

    def test_the_variant_editor_refuses_it_and_the_variant_does_not_move_either(self):
        res = self.manage_api.put(f'/api/admin/products/{self.product.pk}/variant/{self.q}', {
            'model_id': self.model.pk, 'condition_line': 'new', 'option_ids': [self.gb128.pk, self.blue.pk],
            'is_published_online': True}, format='json')
        self.assertEqual((res.status_code, res.json()['code'], res.json()['field']),
                         (400, 'draft_pending', 'is_published_online'), res.content)
        self.unchanged()
        self.assertEqual(self.product.variant_key, device_catalog.variant_key([self.gb256.pk, self.blue.pk]))

    def test_the_bulk_action_refuses_it_and_the_ready_list_leaves_it_out(self):
        self.assertNotIn(self.product, product_publication.ready_to_publish(
            Product.objects.filter(company=self.company), self.company))
        result = product_publication.set_visibility(company=self.company, publish=True, ids=[self.product.pk])
        self.assertEqual(result['changed'], 0)
        self.assertEqual(result['refused'][0]['problems'], ['draft_pending'])
        result = product_publication.set_visibility(
            company=self.company, publish=True, select=lambda qs: qs.filter(pk=self.product.pk), expected_count=1)
        self.assertEqual((result['changed'], result['refused'][0]['problems']), (0, ['draft_pending']))
        self.unchanged()
        res = self.manage_api.get(f'/api/admin/products/{self.q}&ready_to_publish=true')
        self.assertNotIn(self.product.pk, [r['id'] for r in res.json()['results']])

    def test_reloading_an_export_that_asks_for_the_web_writes_the_price_but_never_publishes(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from store import import_services, inventory_exports
        from store.test_cost_imports import upload

        content, _n = inventory_exports.products_workbook(
            self.company, inventory_exports.ExportFilters(), inventory_exports.MODE_REIMPORT)
        import openpyxl, io
        sheet = openpyxl.load_workbook(io.BytesIO(content)).active
        headers = [c.value for c in sheet[1]]
        self.assertNotIn('Origen', headers)         # the reload file is the R4.5 file, column for column
        rows = {row[headers.index('Nombre')]: list(row) for row in sheet.iter_rows(min_row=2, values_only=True)}
        mine = rows[self.product.name]
        self.assertEqual((mine[headers.index('Destino')], mine[headers.index('Precio de venta')]),
                         ('Solo stock interno', 3700))
        mine[headers.index('Destino')] = 'Publicar en e-commerce'
        mine[headers.index('Precio de venta')] = '3900.00'
        job = import_services.preview_products(company=self.company, actor=self.staff, filename='r.xlsx',
                                               upload=upload(headers, [mine]))
        row = job.rows.get()
        self.assertEqual(list(row.errors), [])
        self.assertTrue(any('borrador' in w and 'habilita' in w for w in row.warnings), row.warnings)
        import_services.apply_products(job=job, actor=self.staff)
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, D('3900.00'))
        self.unchanged()

    def test_the_catalogue_proposal_finds_it_as_existing_and_changes_nothing(self):
        from store.models import VariantAttribute
        VariantAttribute.objects.filter(pk=self.capacity.pk).update(role='capacity')
        VariantAttribute.objects.filter(pk=self.colour.pk).update(role='color')
        rows = [{'key': 'a', 'model': 'Teléfono X', 'capacityGb': 256, 'colour': 'azul', 'line': 'new'}]
        plan = catalog_proposal.propose(self.company, rows)['plan']
        self.assertEqual((plan['variants'][0]['status'], plan['variants'][0]['product_id']), ('existing', self.product.pk))
        body = catalog_proposal.apply(company=self.company, rows=rows, approve={'models': [], 'variants': []},
                                      actor=self.staff)
        self.assertEqual(body['created']['variants'], [])
        self.unchanged()
        self.assertFalse(self.product.is_serialized)

    def test_publishing_the_model_does_not_show_it(self):
        device_catalog.set_models_visibility(company=self.company, ids=[self.model.pk], publish=True, actor=self.staff)
        public = _client(host=self.host)
        body = public.get(f'/api/catalog/models/{self.model.slug}/').json()
        self.assertNotIn(self.product.pk, [v['product_id'] for v in body['variants']])
        self.unchanged()

    def test_sale_mode_refuses_on_order_everywhere_and_sells_on_order_is_a_belt(self):
        with self.assertRaises(supply_services.SupplyError) as caught:
            supply_services.set_sale_mode(self.product, Product.SALE_MODE_STOCK_OR_ORDER, actor=self.manager)
        self.assertEqual(caught.exception.code, 'draft_pending')
        res = self.manage_api.patch(f'/api/admin/products/{self.product.pk}/sale-mode/{self.q}',
                                    {'sale_mode': 'stock_or_order'}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (400, 'draft_pending'), res.content)
        # The guided registration with a supplier inherits it, at its supplier step.
        supplier = supply_services.create_supplier(company=self.company, name='Proveedor Uno')
        wanted = {'agreed_cost': '3000.00', 'commercial_price': '3900.00', 'confirmed_quantity': 2,
                  'valid_from': timezone.localdate().isoformat(),
                  'valid_until': (timezone.localdate() + timedelta(days=10)).isoformat(), 'lead_time_days': 3}
        with self.assertRaises(registration_services.RegistrationError) as caught, transaction.atomic():
            registration_services._offer(self.company, supplier, self.product, wanted, may_view_costs=True,
                                         actor=self.staff, request=None)
        self.assertEqual((caught.exception.step, caught.exception.code), ('supplier', 'draft_pending'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.sale_mode, Product.SALE_MODE_STOCK)
        # An offer linked by hand, and a mode forced by ORM: still not on order, not available, not shown.
        offer = supply_services.create_offer(company=self.company, actor=self.staff, may_view_costs=True,
                                             supplier_id=supplier.pk, product_id=self.product.pk, **wanted)
        self.assertIsNotNone(offer)
        Product.objects.filter(pk=self.product.pk).update(sale_mode=Product.SALE_MODE_STOCK_OR_ORDER)
        self.product.refresh_from_db()
        self.assertFalse(supply_services.sells_on_order(self.product))
        self.assertIsNone(supply_services.availability(self.product, self.branch).origin)
        self.assertIsNone(supply_services.on_order_payload(self.product))
        with self.assertRaises(pos_services.PosValidationError):
            pos_services.build_pos_sale(operator=self.staff, company=self.company, branch=self.branch,
                                        items=[{'product': self.product.pk, 'quantity': 1, 'on_order': True}],
                                        require_units=False)
        # Enabled: the belt opens, and the mode that was forced counts as the operator's.
        device_catalog.enable_variant_for_sale(self.product, actor=self.manager, request=None)
        self.product.refresh_from_db()
        self.assertTrue(supply_services.sells_on_order(self.product))

    def test_the_till_does_not_find_it_by_quantity_nor_by_unit_even_with_stock_and_price(self):
        BranchStock.objects.update_or_create(branch=self.branch, product=self.product, defaults={'quantity': 3})
        with self.assertRaises(pos_services.PosValidationError) as caught:
            pos_services.build_pos_sale(operator=self.staff, company=self.company, branch=self.branch,
                                        items=[{'product': self.product.pk, 'quantity': 1}], require_units=False)
        self.assertIn('no encontrado', str(caught.exception))
        used = self.priced(self.gb256, self.black, line=Product.CONDITION_USED, price='0.00')
        Product.objects.filter(pk=used.pk).update(is_serialized=True, requires_imei=True)
        used.refresh_from_db()
        self.receive(used, 1, cost='800.00', sale_price='1200.00')
        unit = StockUnit.objects.get(product=used)
        with self.assertRaises(pos_services.PosValidationError) as caught:
            pos_services.build_pos_sale(operator=self.staff, company=self.company, branch=self.branch,
                                        items=[{'product': used.pk, 'quantity': 1, 'units': [_unit(unit)]}],
                                        validate_cash=False)
        self.assertIn('no encontrado', str(caught.exception))
        # The historical used variant of the shop, at product price 0, still sells by unit (§D case 1).
        self.receive(self.used128, 1, cost='800.00', sale_price='1300.00')
        hist = StockUnit.objects.get(product=self.used128)
        sale = pos_services.build_pos_sale(operator=self.staff, company=self.company, branch=self.branch,
                                           items=[{'product': self.used128.pk, 'quantity': 1, 'units': [_unit(hist)]}],
                                           validate_cash=False)
        self.assertEqual(sale['total'], D('1300.00'))

    def test_the_shop_the_cart_and_the_v1_api_never_see_it_even_with_the_flag_forced(self):
        public = _client(host=self.host)
        Product.objects.filter(pk=self.product.pk).update(is_published_online=True)   # the platform admin's shortcut
        self.product.refresh_from_db()
        self.assertTrue(self.product.is_published_online)
        self.assertFalse(self.product.is_sold_online)
        self.assertNotIn(self.product, company_storefront_products(self.company))
        self.assertEqual(public.get(f'/api/products/{self.product.pk}/').status_code, 404)
        res = public.post('/api/cart/add/', {'session_key': 'a1b2-sesion', 'product': self.product.pk, 'quantity': 1},
                          format='json')
        self.assertEqual(res.status_code, 404, res.content)
        res = _client().get(f'/api/v1/storefront/{self.company.slug}/products/')
        listed = res.json()['results'] if isinstance(res.json(), dict) else res.json()
        self.assertNotIn(self.product.slug, [p['slug'] for p in listed])
        self.assertEqual(_client().get(f'/api/v1/storefront/{self.company.slug}/products/{self.product.slug}/').status_code, 404)
        device_catalog.set_models_visibility(company=self.company, ids=[self.model.pk], publish=True, actor=self.staff)
        body = public.get(f'/api/catalog/models/{self.model.slug}/').json()
        self.assertNotIn(self.product.pk, [v['product_id'] for v in body['variants']])
        # The admin preview still says what it is.
        self.assertEqual(self.manage_api.get(f'/api/admin/products/{self.product.pk}/{self.q}').json()['is_pending_draft'], True)


# ---------------------------------------------------------------------------
# Historical rows behave exactly as today (§D)
# ---------------------------------------------------------------------------

@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class HistoricalRowsUntouchedTest(DraftShop, TestCase):
    def setUp(self):
        self.build('a1b2-historico')

    def publish_by_form(self, product):
        return self.manage_api.patch(f'/api/admin/products/{product.pk}/{self.q}', {'is_published_online': True},
                                     format='json')

    def test_old_code_inserts_are_never_pending(self):
        # What the previous release writes: no draft_origin, no transition.
        plain = _prod(self.company, 'Fila antigua', 'fila-antigua', price='0.00', inventory=0)
        self.assertEqual((plain.draft_origin, plain.sale_enabled_at, plain.is_pending_draft), ('', None, False))
        self.assertFalse(Product.objects.filter(pk=plain.pk).filter(Product.pending_draft_q()).exists())

    def test_a_used_variant_at_zero_sells_by_unit_and_publishes_from_the_form(self):
        Product.objects.filter(pk=self.used128.pk).update(price=D('0.00'), is_published_online=False)
        self.used128.refresh_from_db()
        self.assertFalse(self.used128.is_pending_draft)
        self.assertEqual(self.publish_by_form(self.used128).status_code, 200)
        self.receive(self.used128, 1, cost='800.00', sale_price='1300.00')
        unit = StockUnit.objects.get(product=self.used128)
        sale = pos_services.build_pos_sale(operator=self.staff, company=self.company, branch=self.branch,
                                           items=[{'product': self.used128.pk, 'quantity': 1, 'units': [_unit(unit)]}],
                                           validate_cash=False)
        self.assertEqual(sale['total'], D('1300.00'))

    def test_a_catalogue_proposal_row_at_zero_is_not_a_pending_draft_and_publishes_from_the_form(self):
        from store.models import VariantAttribute
        VariantAttribute.objects.filter(pk=self.capacity.pk).update(role='capacity')
        VariantAttribute.objects.filter(pk=self.colour.pk).update(role='color')
        rows = [{'key': 'a', 'model': 'Teléfono X', 'capacityGb': 256, 'colour': 'azul', 'line': 'new'}]
        plan = catalog_proposal.propose(self.company, rows)['plan']
        body = catalog_proposal.apply(company=self.company, rows=rows,
                                      approve={'models': [], 'variants': [plan['variants'][0]['ref']]}, actor=self.staff)
        product = Product.objects.get(pk=body['created']['variants'][0]['product_id'])
        self.assertEqual((product.price, product.draft_origin, product.is_pending_draft), (D('0.00'), '', False))
        self.assertEqual(self.publish_by_form(product).status_code, 200)       # 2026-10-13 rule, as today

    def test_an_import_row_at_zero_an_internal_only_product_and_a_hidden_republished_one_are_untouched(self):
        internal = _prod(self.company, 'Interno', 'interno', price='250.00', inventory=0)
        Product.objects.filter(pk=internal.pk).update(is_published_online=False)
        at_zero = _prod(self.company, 'Carga a cero', 'carga-cero', price='0.00', inventory=0)
        Product.objects.filter(pk=at_zero.pk).update(is_published_online=False)
        for product in (internal, at_zero):
            product.refresh_from_db()
            self.assertFalse(product.is_pending_draft)
            self.assertEqual(self.publish_by_form(product).status_code, 200, product.name)
            supply_services.set_sale_mode(product, Product.SALE_MODE_STOCK_OR_ORDER, actor=self.manager)
        # Hide and republish, without any gate.
        res = self.manage_api.patch(f'/api/admin/products/{internal.pk}/{self.q}', {'is_published_online': False}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.publish_by_form(internal).status_code, 200)
        # The bulk action keeps its own rule for them (no_price, never draft_pending).
        Product.objects.filter(pk=at_zero.pk).update(is_published_online=False)
        result = product_publication.set_visibility(company=self.company, publish=True, ids=[at_zero.pk])
        self.assertEqual(result['refused'][0]['problems'], ['no_price', 'no_category'])

    def test_a_published_variant_outside_the_palette_stays_published_and_sellable(self):
        self.palette(self.gb256, self.blue)
        self.new128.refresh_from_db()
        self.assertEqual(model_palette.variant_status(self.model, [self.gb128.pk, self.black.pk]), 'fuera_de_paleta')
        self.assertFalse(self.new128.is_pending_draft)
        self.assertTrue(self.new128.is_sold_online)
        self.assertIn(self.new128, company_storefront_products(self.company))
        BranchStock.objects.update_or_create(branch=self.branch, product=self.new128, defaults={'quantity': 1})
        sale = pos_services.build_pos_sale(operator=self.staff, company=self.company, branch=self.branch,
                                           items=[{'product': self.new128.pk, 'quantity': 1}], require_units=False,
                                           validate_cash=False)
        self.assertEqual(sale['total'], D('3000.00'))


# ---------------------------------------------------------------------------
# What the admin screens read; what the public does not
# ---------------------------------------------------------------------------

@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class PayloadsAndExportsTest(DraftShop, TestCase):
    def setUp(self):
        self.build('a1b2-pantallas')

    def test_the_palette_payload_tells_the_cockpit_what_each_variant_lacks(self):
        pending = self.draft()
        enabled = self.priced(self.gb128, self.blue)
        device_catalog.enable_variant_for_sale(enabled, actor=self.manager, request=None)
        body = self.view_api.get(f'/api/admin/catalog/models/{self.model.pk}/palette/{self.q}').json()
        rows = {v['product_id']: v for v in body['variants']}
        self.assertEqual(sorted(rows[pending.pk]), sorted([
            'product_id', 'name', 'condition_line', 'option_ids', 'status', 'draft_origin', 'draft_pending',
            'sale_enabled_at', 'has_image', 'price_set', 'is_published_online', 'is_active', 'is_serialized']))
        self.assertEqual((rows[pending.pk]['draft_pending'], rows[pending.pk]['price_set'], rows[pending.pk]['has_image'],
                          rows[pending.pk]['sale_enabled_at'], rows[pending.pk]['is_published_online']),
                         (True, False, False, None, False))
        self.assertEqual((rows[enabled.pk]['draft_pending'], rows[enabled.pk]['price_set']), (False, True))
        self.assertIsNotNone(rows[enabled.pk]['sale_enabled_at'])
        self.assertEqual((rows[self.new128.pk]['draft_pending'], rows[self.new128.pk]['draft_origin'],
                          rows[self.new128.pk]['price_set']), (False, '', True))
        # Summary: the pending draft and the unpriced historical used variant need a look; roles the palette lacks.
        self.assertEqual(body['summary']['needs_review'], 1)
        self.assertEqual(body['summary']['roles_missing'], ['sim', 'region'])
        combos = {c['key']: c for c in body['combinations']}
        self.assertEqual(combos[device_catalog.variant_key([self.gb256.pk, self.blue.pk])]['status'], 'variante_existente')

    def test_the_product_list_and_detail_carry_the_fields_for_the_panel(self):
        pending = self.draft()
        res = self.view_api.get(f'/api/admin/products/{self.q}&search=Azul')
        row = next(r for r in res.json()['results'] if r['id'] == pending.pk)
        self.assertEqual((row['draft_origin'], row['sale_enabled_at'], row['is_pending_draft']), ('palette', None, True))
        self.assertIn('draft_pending', row['publication_problems'])
        detail = self.view_api.get(f'/api/admin/products/{self.new128.pk}/{self.q}').json()
        self.assertEqual((detail['draft_origin'], detail['sale_enabled_at'], detail['is_pending_draft']), ('', None, False))

    def test_exports_list_the_draft_as_hidden_at_zero_and_the_report_names_its_origin(self):
        from store import inventory_exports
        import io, openpyxl

        pending = self.draft()
        for mode, origin in ((inventory_exports.MODE_REIMPORT, None), (inventory_exports.MODE_REPORT, 'Paleta · pendiente')):
            content, _n = inventory_exports.products_workbook(self.company, inventory_exports.ExportFilters(), mode)
            sheet = openpyxl.load_workbook(io.BytesIO(content)).active
            headers = [c.value for c in sheet[1]]
            rows = {row[headers.index('Nombre')]: dict(zip(headers, row))
                    for row in sheet.iter_rows(min_row=2, values_only=True)}
            mine = rows[pending.name]
            self.assertEqual((mine['Destino'], mine['Precio de venta']), ('Solo stock interno', 0))
            if origin is None:
                self.assertNotIn('Origen', headers)
            else:
                self.assertEqual(mine['Origen'], origin)
                self.assertEqual(rows[self.new128.name]['Origen'], None)
                self.assertIn('habilita la variante', mine['Problemas para publicar'])

    def test_the_public_contract_is_unchanged_with_a_pending_and_an_enabled_hidden_draft(self):
        self.receive(self.new128, 1, cost='2500.00')
        public = _client(host=self.host)
        before = public.get(f'/api/catalog/models/{self.model.slug}/').json()
        before.pop('as_of')
        self.draft()
        enabled = self.priced(self.gb128, self.blue)
        device_catalog.enable_variant_for_sale(enabled, actor=self.manager, request=None)
        after = public.get(f'/api/catalog/models/{self.model.slug}/').json()
        after.pop('as_of')
        self.assertEqual(after, before)
        for word in ('draft', 'pending', 'sale_enabled', 'palette'):
            self.assertNotIn(word, str(after))
        listing = public.get('/api/catalog/models/').json()
        self.assertEqual(sorted(listing['results'][0]), ['category', 'from_price', 'has_new', 'has_used', 'image_url',
                                                         'name', 'slug', 'sold_out'])
