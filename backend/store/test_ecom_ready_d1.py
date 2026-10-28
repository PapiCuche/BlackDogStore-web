"""
ECOM-READY-D1 — «completar ficha y publicar» desde una sola pantalla.

Lo que estas pruebas fijan:

  * la vista previa para el cliente (`GET /api/admin/products/<id>/storefront-preview/`)
    es EXACTAMENTE el producto público (`ProductSerializer`), también cuando el
    producto está oculto, y lleva su estado de visibilidad y de disponibilidad;
  * es del personal y de su empresa (otra empresa = 404), sin costo, IMEI ni serie;
  * «listos para publicar» es la regla `publication_problems` hecha filtro, y
    «Publicar listos» pasa por la acción en bloque de siempre: lo que tiene
    problemas nunca se publica, el conteo desfasado responde 409 y queda auditoría.
"""
import json
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase, override_settings

from store import product_publication
from store.models import (
    AdminAuditLog, Category, CompanySettings, Product, ProductImage, StockUnit,
)
from store.serializers import ProductSerializer
from store.test_device_commerce import DeviceShop, _client
from store.test_supplier_offers import SupplyShop
from store.tests import _p2d_branch, _p2d_member, _p3_company, _prod

D = Decimal
PICTURE = 'https://imagenes.example.test/producto.png'


def preview_url(product):
    return f'/api/admin/products/{product.pk}/storefront-preview/'


class PreviewBase(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('ecom-d1-a', 'Tienda A')
        self.other = _p3_company('ecom-d1-b', 'Tienda B')
        self.branch = self.company.default_inventory_branch
        self.category = Category.objects.create(company=self.company, name='Accesorios', slug='accesorios')
        self.viewer, _ = _p2d_member(self.company, 'd1_viewer', ['company.view', 'products.view'])
        self.manager, _ = _p2d_member(self.company, 'd1_manager', ['company.view', 'products.view', 'products.manage'])
        self.blind, _ = _p2d_member(self.company, 'd1_blind', ['company.view'])
        self.api = _client(self.viewer)

    def product(self, name='Funda', slug='funda', **over):
        product = _prod(self.company, name, slug, category=self.category, price='59.00', inventory=0)
        for field, value in over.items():
            setattr(product, field, value)
        if over:
            product.save()
        return product

    def get(self, product, client=None):
        return (client or self.api).get(preview_url(product))


@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class PreviewShapeTest(PreviewBase):
    def test_the_card_is_exactly_the_public_serializer_of_the_product(self):
        product = self.product(description='Funda de silicona')
        body = self.get(product).json()
        self.assertEqual(set(body['product']), set(ProductSerializer.Meta.fields))
        public = product.__class__.objects.get(pk=product.pk)
        public.available_stock = 0
        self.assertEqual(body['product'], json.loads(json.dumps(ProductSerializer(public).data)))

    def test_it_matches_what_the_storefront_returns_for_a_published_product(self):
        product = self.product()
        body = self.get(product).json()
        public = _client(host='ecom-d1-a.tienda.pe').get(f'/api/products/?slug={product.slug}')
        self.assertEqual(public.status_code, 200)
        data = public.json()
        row = data['results'][0] if isinstance(data, dict) else data[0]
        self.assertEqual(body['product'], row)

    def test_the_top_level_keys_are_an_allow_list(self):
        body = self.get(self.product()).json()
        self.assertEqual(
            set(body), {'product', 'visibility', 'availability', 'publication_problems', 'offered_units'})

    def test_nothing_internal_travels(self):
        product = self.product()
        Product.objects.filter(pk=product.pk).update(reference_cost=D('7.77'))
        text = json.dumps(self.get(product).json()).lower()
        for needle in ('reference_cost', '7.77', 'cost', 'imei', 'serial', 'supplier', 'agreed'):
            self.assertNotIn(needle, text, needle)

    def test_a_hidden_product_can_still_be_previewed_by_staff(self):
        product = self.product(is_published_online=False)
        response = self.get(product)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['visibility']['state'], 'hidden')
        self.assertFalse(response.json()['visibility']['visible'])
        self.assertFalse(response.json()['visibility']['published'])
        self.assertEqual(response.json()['visibility']['label'], 'Oculto: el cliente no lo ve')

    def test_a_published_product_says_visible(self):
        vis = self.get(self.product()).json()['visibility']
        self.assertEqual((vis['state'], vis['visible'], vis['label']), ('visible', True, 'Visible'))

    def test_an_inactive_product_is_not_visible_even_if_published(self):
        vis = self.get(self.product(is_active=False)).json()['visibility']
        self.assertEqual((vis['state'], vis['visible']), ('inactive', False))
        # Still marked for the web: hiding it must stay possible from the panel.
        self.assertTrue(vis['published'])
        self.assertEqual(vis['label'], 'Inactivo: el cliente no lo ve')

    def test_the_pending_requirements_travel_with_it(self):
        product = self.product(price=D('0.00'), category=None)
        self.assertEqual(self.get(product).json()['publication_problems'], ['no_price', 'no_category'])


class PreviewAccessTest(PreviewBase):
    def test_a_product_of_another_company_is_a_404(self):
        foreign = _prod(self.other, 'Ajeno', 'ajeno', price='10.00', inventory=0)
        self.assertEqual(self.get(foreign).status_code, 404)
        self.assertEqual(self.get(foreign).json(), self.api.get('/api/admin/products/99999999/storefront-preview/').json())

    def test_without_products_view_it_is_refused(self):
        self.assertEqual(self.get(self.product(), client=_client(self.blind)).status_code, 403)

    def test_an_anonymous_visitor_is_refused(self):
        self.assertIn(self.get(self.product(), client=_client()).status_code, (401, 403))

    def test_a_viewer_without_manage_can_preview(self):
        self.assertEqual(self.get(self.product()).status_code, 200)

    def test_it_only_reads(self):
        product = self.product()
        self.assertEqual(self.api.post(preview_url(product), {}, format='json').status_code, 405)
        self.assertEqual(self.api.patch(preview_url(product), {}, format='json').status_code, 405)


class PreviewAvailabilityTest(PreviewBase):
    def availability(self, product):
        return self.get(product).json()['availability']

    def test_no_stock_does_not_look_available_for_immediate_delivery(self):
        av = self.availability(self.product())
        self.assertEqual(av['state'], 'none')
        self.assertEqual(av['label'], 'Sin existencias: no se ofrece para entrega inmediata')

    def test_stock_in_the_dispatch_branch_is_immediate_delivery(self):
        from store import inventory_services as inventory
        from store.models import StockMovement

        product = self.product()
        inventory.create_stock_movement(
            branch=self.branch, product_id=product.pk, movement_type=StockMovement.PURCHASE_ENTRY,
            quantity=4, reason='Compra', unit_cost=D('30'))
        av = self.availability(product)
        self.assertEqual(av['state'], 'own_stock')
        self.assertIn('entrega inmediata', av['label'])
        self.assertEqual(self.get(product).json()['product']['inventory'], 4)

    def test_stock_in_another_branch_is_not_immediate_delivery(self):
        from store import inventory_services as inventory
        from store.models import StockMovement

        other = _p2d_branch(self.company, 'Otra')
        product = self.product()
        inventory.create_stock_movement(
            branch=other, product_id=product.pk, movement_type=StockMovement.PURCHASE_ENTRY,
            quantity=4, reason='Compra', unit_cost=D('30'))
        self.assertEqual(self.availability(product)['state'], 'none')

    def test_a_company_without_dispatch_branch_says_so(self):

        product = self.product()  # `_prod` seeds the default branch: build it first
        _p2d_branch(self.company, 'Segunda')
        self.company.default_inventory_branch = None
        self.company.save()
        av = self.availability(product)
        self.assertEqual(av['state'], 'none')
        self.assertEqual(av['reason'], 'no_dispatch_branch')

    def test_on_order_keeps_its_contract(self):
        shop = SupplyShop()
        shop.build('ecom-d1-supply')
        api = _client(shop.staff)
        body = api.get(preview_url(shop.case)).json()
        self.assertEqual(body['availability']['state'], 'supplier')
        self.assertEqual(body['availability']['label'], 'Bajo pedido: entrega en 4 días')
        self.assertEqual(body['product']['on_order'], {'lead_time_days': 4, 'price': '110.00'})
        text = json.dumps(body)
        for needle in ('Mayorista', 'agreed_cost', 'confirmed_quantity', '"70.00"'):
            self.assertNotIn(needle, text)


class PreviewUsedUnitsTest(TestCase):
    def setUp(self):
        self.shop = DeviceShop()
        self.shop.build('ecom-d1-dev')
        self.api = _client(self.shop.staff)

    def preview(self, product):
        return self.api.get(preview_url(product)).json()

    def test_units_without_a_price_are_listed_with_their_reason(self):
        priced, bare = self.shop.receive(self.shop.used128, 2)
        from store import stock_unit_services as units
        units.update_unit(priced, sale_price='2000.00', battery_health=90)
        body = self.preview(self.shop.used128)
        rows = {r['label_code']: r for r in body['offered_units']}
        self.assertEqual(len(rows), 2)
        offered = rows[StockUnit.objects.get(pk=priced.pk).label_code]
        missing = rows[StockUnit.objects.get(pk=bare.pk).label_code]
        self.assertTrue(offered['offered'])
        self.assertEqual((offered['price'], offered['battery_health']), ('2000.00', 90))
        self.assertFalse(missing['offered'])
        self.assertEqual(missing['reason'], 'no_sale_price')
        self.assertEqual(missing['reason_label'], 'Falta el precio de este equipo')
        self.assertEqual(body['availability']['state'], 'own_stock')
        self.assertEqual(body['availability']['offered_count'], 1)

    def test_with_no_priced_unit_nothing_is_offered(self):
        self.shop.receive(self.shop.used128, 1)
        av = self.preview(self.shop.used128)['availability']
        self.assertEqual((av['state'], av['offered_count']), ('none', 0))

    def test_no_imei_no_serial_not_even_a_hint(self):
        units_ = self.shop.receive(self.shop.used128, 1)
        text = json.dumps(self.preview(self.shop.used128))
        unit = StockUnit.objects.get(pk=units_[0].pk)
        for secret in (unit.imei, unit.serial_number, unit.imei[-4:], 'imei'):
            self.assertNotIn(secret, text.replace(unit.label_code or '~', ''), secret)

    def test_a_new_variant_has_no_unit_list(self):
        self.shop.receive(self.shop.new128, 1)
        self.assertEqual(self.preview(self.shop.new128)['offered_units'], [])

    def test_the_unit_list_is_the_dispatch_branch_only(self):
        other = _p2d_branch(self.shop.company, 'Otra')
        from store import stock_unit_services as units
        from store.test_device_commerce import IMEIS
        units.receive_units(branch=other, product=self.shop.used128, actor=self.shop.staff, reason='Compra',
                            units=[{'serial_number': 'SERZZ', 'imei': IMEIS[39]}])
        self.assertEqual(self.preview(self.shop.used128)['offered_units'], [])


class ReadyBase(PreviewBase):
    def setUp(self):
        super().setUp()
        self.api = _client(self.manager)
        self.n = 0

    def make(self, name, published=False, **over):
        self.n += 1
        fields = {'price': D('50.00'), 'image_url': PICTURE, 'category': self.category,
                  'is_published_online': published}
        fields.update(over)
        return Product.objects.create(company=self.company, name=name, slug=f'rd-{self.n}', **fields)

    def ready_ids(self, qs='ready_to_publish=true'):
        response = self.api.get(f'/api/admin/products/?{qs}&page_size=100')
        self.assertEqual(response.status_code, 200)
        return {row['id'] for row in response.json()['results']}



class ReadyFilterTest(ReadyBase):
    def test_the_filter_is_the_publication_rule_made_a_query(self):
        for flag in (False, True):
            CompanySettings.objects.update_or_create(
                company=self.company, defaults={'publication_requires_image': flag})
            ok = self.make(f'Listo {flag}')
            gallery = self.make(f'Galeria {flag}', image_url='')
            ProductImage.objects.create(
                company=self.company, product=gallery, image_url='/api/storefront/images/abc', is_primary=True)
            cases = [
                ok, gallery,
                self.make(f'Sin precio {flag}', price=D('0.00')),
                self.make(f'Sin categoria {flag}', category=None),
                self.make(f'Inactivo {flag}', is_active=False),
                self.make(f'Sin imagen {flag}', image_url=''),
                self.make(f'Ya publicado {flag}', published=True),
            ]
            expected = {
                p.pk for p in cases
                if not p.is_published_online and not product_publication.publication_problems(p)
            }
            self.assertEqual(self.ready_ids() & {p.pk for p in cases}, expected, flag)
            if flag:
                self.assertNotIn(cases[5].pk, expected)

    def test_other_companys_ready_products_never_show(self):
        foreign = Product.objects.create(
            company=self.other, name='Ajeno', slug='ajeno', price=D('5.00'), image_url=PICTURE,
            is_published_online=False)
        self.assertNotIn(foreign.pk, self.ready_ids())

    def test_a_ready_filter_that_means_nothing_is_refused_for_a_write(self):
        self.make('Listo')
        response = self.api.post('/api/admin/products/bulk-visibility/', {
            'action': 'publish', 'filter': {'ready_to_publish': 'maybe'}, 'expected_count': 1}, format='json')
        self.assertEqual(response.status_code, 400)


class PublishReadyTest(ReadyBase):
    URL = '/api/admin/products/bulk-visibility/'

    def publish_ready(self, expected, extra=None):
        body = {'action': 'publish', 'filter': {'ready_to_publish': 'true', **(extra or {})},
                'expected_count': expected}
        return self.api.post(self.URL, body, format='json')

    def test_publishing_the_ready_ones_publishes_exactly_them_and_audits(self):
        ready = [self.make('Listo A'), self.make('Listo B')]
        bad = [self.make('Sin precio', price=D('0.00')), self.make('Sin categoria', category=None)]
        response = self.publish_ready(2)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()['changed'], response.json()['refused']), (2, []))
        self.assertTrue(all(Product.objects.get(pk=p.pk).is_published_online for p in ready))
        self.assertFalse(any(Product.objects.get(pk=p.pk).is_published_online for p in bad))
        line = AdminAuditLog.objects.get(action='products_visibility_changed')
        self.assertEqual(line.company, self.company)
        self.assertEqual(line.metadata['changed'], 2)
        self.assertEqual(line.metadata['filter'], {'ready_to_publish': 'true'})
        self.assertEqual(set(line.metadata['changed_ids']), {p.pk for p in ready})

    def test_a_stale_count_is_a_409_and_nothing_changes(self):
        ready = self.make('Listo A')
        self.make('Listo B')
        response = self.publish_ready(1)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['current_count'], 2)
        self.assertFalse(Product.objects.get(pk=ready.pk).is_published_online)
        self.assertFalse(AdminAuditLog.objects.filter(action='products_visibility_changed').exists())

    def test_a_product_with_problems_is_never_published_by_ids_either(self):
        good, bad = self.make('Listo'), self.make('Sin precio', price=D('0.00'))
        response = self.api.post(self.URL, {'action': 'publish', 'ids': [good.pk, bad.pk]}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r['id'] for r in response.json()['refused']], [bad.pk])
        self.assertEqual(response.json()['refused'][0]['problems'], ['no_price'])
        self.assertFalse(Product.objects.get(pk=bad.pk).is_published_online)
        self.assertTrue(Product.objects.get(pk=good.pk).is_published_online)

    def test_a_variant_born_at_price_zero_is_refused_until_it_is_priced(self):
        variant = self.make('Variante nueva', price=D('0.00'))
        self.assertEqual(self.ready_ids(), set())
        self.api.patch(f'/api/admin/products/{variant.pk}/', {'price': '99.90'}, format='json')
        self.assertEqual(self.ready_ids(), {variant.pk})

    def test_hiding_is_always_allowed(self):
        broken = self.make('Roto', published=True, price=D('0.00'), category=None, is_active=False)
        response = self.api.post(self.URL, {'action': 'hide', 'ids': [broken.pk]}, format='json')
        self.assertEqual(response.json()['changed'], 1)
        self.assertFalse(Product.objects.get(pk=broken.pk).is_published_online)

    def test_a_viewer_cannot_publish(self):
        self.make('Listo')
        response = _client(self.viewer).post(self.URL, {
            'action': 'publish', 'filter': {'ready_to_publish': 'true'}, 'expected_count': 1}, format='json')
        self.assertEqual(response.status_code, 403)
