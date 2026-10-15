"""
DEVICE-COMMERCE-01 · DC2 — variantes y tienda de nuevos y seminuevos.

Lo que estas pruebas fijan:

  * una variante sigue siendo un producto: una opción por atributo, sin dos
    variantes iguales de un modelo en la misma línea, todo de una empresa;
  * lo público nunca lleva el IMEI completo, la serie, el costo ni el
    proveedor, y una batería sin medir dice «no informado» (null), nunca un número;
  * el carrito aparta UN equipo concreto (nuevo o seminuevo): dos compradores no
    se llevan el mismo, el apartado vence y vuelve al estante con su Kardex;
  * el pago vende EL equipo apartado (no el más antiguo), con su costo y su
    precio congelados, y dos seminuevos de una variante con precios distintos
    son dos líneas exactas;
  * todo es por empresa y por sucursal.
"""
from datetime import timedelta
from decimal import Decimal
from threading import Barrier, Thread

from django.core.cache import cache
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from store import checkout_services as checkout
from store import device_catalog
from store import inventory_services as inventory
from store import stock_unit_services as units
from store import unit_hold_services as holds
from store.models import (
    BranchStock, CartItem, CosmeticGrade, Order, OrderItem, OrderUnit, Product,
    ProductVariantOption, StockMovement, StockUnit, UnitPhoto,
)
from store.test_stock_units import imei
from store.tests import _p2d_member, _p3_company, _prod

D = Decimal
IMEIS = [imei(f'35693803564{n:03d}') for n in range(40)]


def _client(user=None, host=None):
    client = APIClient(HTTP_HOST=host) if host else APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


class DeviceShop:
    """One shop with a model «Teléfono X», capacities, colours, new and used variants."""

    def build(self, slug='dc2-tienda'):
        cache.clear()
        self.company = _p3_company(slug, 'Tienda DC2')
        self.branch = self.company.default_inventory_branch
        self.host = f'{slug}.tienda.pe'
        self.staff, _ = _p2d_member(self.company, f'{slug}_staff', [
            'company.view', 'products.view', 'products.manage', 'inventory.view', 'inventory.adjust'])
        self.capacity = device_catalog.create_attribute(company=self.company, name='Capacidad')
        self.colour = device_catalog.create_attribute(company=self.company, name='Color', kind='color')
        self.gb128 = device_catalog.create_option(company=self.company, attribute_id=self.capacity.pk, label='128 GB')
        self.gb256 = device_catalog.create_option(company=self.company, attribute_id=self.capacity.pk, label='256 GB')
        self.black = device_catalog.create_option(company=self.company, attribute_id=self.colour.pk,
                                                  label='Negro', swatch_hex='#111111')
        self.blue = device_catalog.create_option(company=self.company, attribute_id=self.colour.pk,
                                                 label='Azul', swatch_hex='#1d4ed8')
        self.model = device_catalog.create_model(company=self.company, name='Teléfono X')
        self.new128 = self.variant('Teléfono X 128 GB Negro', 'tx-128-negro', '3000.00', [self.gb128, self.black])
        self.new256 = self.variant('Teléfono X 256 GB Negro', 'tx-256-negro', '3500.00', [self.gb256, self.black])
        self.used128 = self.variant('Teléfono X 128 GB Azul usado', 'tx-128-azul-usado', '1.00',
                                    [self.gb128, self.blue], line='used')
        self.grade = CosmeticGrade.objects.get(company=self.company, code='excelente')
        self._imei = iter(IMEIS)

    def variant(self, name, slug, price, options, line='new'):
        product = _prod(self.company, name, slug, price=price, inventory=0)
        Product.objects.filter(pk=product.pk).update(is_serialized=True, requires_imei=True)
        return device_catalog.set_variant(
            product, model_id=self.model.pk, condition_line=line, option_ids=[o.pk for o in options],
        )

    def receive(self, product, n=1, **extra):
        rows = []
        for _ in range(n):
            value = next(self._imei)
            rows.append({'serial_number': f'SER{value[-8:]}', 'imei': value, **extra})
        return units.receive_units(branch=self.branch, product=product, units=rows, actor=self.staff,
                                   reason='Compra')

    def shelf(self, product):
        return inventory.branch_quantity(self.branch, product)

    def available(self, product):
        return StockUnit.objects.filter(product=product, status='available').count()


# ---------------------------------------------------------------------------
# Variants
# ---------------------------------------------------------------------------

class VariantRulesTest(DeviceShop, TestCase):
    def setUp(self):
        self.build()

    def test_a_variant_is_a_product_with_one_option_per_attribute(self):
        self.assertEqual(self.new128.product_model, self.model)
        self.assertEqual({o.pk for o in device_catalog.variant_options_of(self.new128)}, {self.gb128.pk, self.black.pk})
        self.assertTrue(self.new128.sells_by_unit)
        other = _prod(self.company, 'Otro', 'otro-dc2', inventory=0)
        with self.assertRaises(device_catalog.CatalogError):
            device_catalog.set_variant(other, model_id=self.model.pk, option_ids=[self.gb128.pk, self.gb256.pk])

    def test_two_variants_of_a_model_with_the_same_options_and_line_are_refused(self):
        twin = _prod(self.company, 'Gemelo', 'gemelo-dc2', inventory=0)
        with self.assertRaises(device_catalog.CatalogError) as caught:
            device_catalog.set_variant(twin, model_id=self.model.pk, option_ids=[self.black.pk, self.gb128.pk])
        self.assertEqual(caught.exception.code, 'duplicate')
        # …the same options in the OTHER line is another variant.
        device_catalog.set_variant(twin, model_id=self.model.pk, condition_line='used',
                                   option_ids=[self.black.pk, self.gb128.pk])
        # And the database holds it even past the service.
        third = _prod(self.company, 'Tercero', 'tercero-dc2', inventory=0)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.filter(pk=third.pk).update(
                product_model=self.model, condition_line='new', variant_key=self.new128.variant_key)

    def test_the_database_refuses_two_options_of_one_attribute(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProductVariantOption.objects.create(company=self.company, product=self.new128,
                                                attribute=self.capacity, option=self.gb256)

    def test_swatches_are_validated_and_only_for_colours(self):
        for bad in ('red', '#12345', '#GGGGGG', '111111', '#1234567'):
            with self.subTest(bad=bad), self.assertRaises(device_catalog.CatalogError):
                device_catalog.create_option(company=self.company, attribute_id=self.colour.pk,
                                             label=f'c{bad}', swatch_hex=bad)
        with self.assertRaises(device_catalog.CatalogError):
            device_catalog.create_option(company=self.company, attribute_id=self.colour.pk, label='Sin muestra')
        with self.assertRaises(device_catalog.CatalogError):
            device_catalog.create_option(company=self.company, attribute_id=self.capacity.pk, label='1 TB',
                                         swatch_hex='#000000')
        ok = device_catalog.create_option(company=self.company, attribute_id=self.colour.pk, label='Verde',
                                          swatch_hex='#16a34a')
        self.assertEqual(ok.swatch_hex, '#16A34A')
        with self.assertRaises(device_catalog.CatalogError):
            device_catalog.create_option(company=self.company, attribute_id=self.capacity.pk, label='128 gb')

    def test_options_and_models_of_another_company_do_not_exist_here(self):
        other = _p3_company('dc2-otra', 'Otra')
        foreign_attr = device_catalog.create_attribute(company=other, name='Capacidad')
        foreign_option = device_catalog.create_option(company=other, attribute_id=foreign_attr.pk, label='64 GB')
        foreign_model = device_catalog.create_model(company=other, name='Ajeno')
        product = _prod(self.company, 'Mío', 'mio-dc2', inventory=0)
        with self.assertRaises(device_catalog.CatalogError):
            device_catalog.set_variant(product, model_id=foreign_model.pk)
        with self.assertRaises(device_catalog.CatalogError):
            device_catalog.set_variant(product, model_id=self.model.pk, option_ids=[foreign_option.pk])
        with self.assertRaises(device_catalog.CatalogError):
            device_catalog.create_option(company=self.company, attribute_id=foreign_attr.pk, label='X')
        with self.assertRaises(ValueError):
            ProductVariantOption.objects.create(company=self.company, product=product,
                                                attribute=foreign_attr, option=foreign_option)

    def test_a_product_leaves_its_model_and_is_what_it_was(self):
        device_catalog.set_variant(self.new256, model_id=None)
        self.new256.refresh_from_db()
        self.assertEqual((self.new256.product_model_id, self.new256.variant_key), (None, ''))
        self.assertFalse(self.new256.sells_by_unit)
        self.assertFalse(ProductVariantOption.objects.filter(product=self.new256).exists())

    def test_grades_are_seeded_for_new_companies_only_and_idempotent(self):
        self.assertEqual(CosmeticGrade.objects.filter(company=self.company).count(), 4)
        self.assertEqual(device_catalog.seed_cosmetic_grades(self.company), 0)
        from store.models import Company
        bare = Company.objects.create(name='Sin escala', slug='dc2-sin', legal_name='x', tax_id='20111111119')
        self.assertFalse(CosmeticGrade.objects.filter(company=bare).exists())


class AdminCatalogApiTest(DeviceShop, TestCase):
    def setUp(self):
        self.build()
        self.api = _client(self.staff)

    def test_create_a_model_attach_a_variant_and_read_it_back(self):
        res = self.api.post('/api/admin/catalog/models/', {'name': 'Tableta Y'}, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        model_id = res.json()['id']
        product = _prod(self.company, 'Tableta Y 256', 'tableta-y-256', inventory=0)
        res = self.api.put(f'/api/admin/products/{product.pk}/variant/', {
            'model_id': model_id, 'condition_line': 'new', 'option_ids': [self.gb256.pk]}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        detail = self.api.get(f'/api/admin/catalog/models/{model_id}/').json()
        self.assertEqual(detail['variants'][0]['options'][0]['label'], '256 GB')

    def test_another_companys_rows_answer_404(self):
        other = _p3_company('dc2-ajena', 'Ajena')
        stranger, _ = _p2d_member(other, 'dc2_ajeno', ['company.view', 'products.view', 'products.manage',
                                                       'inventory.view', 'inventory.adjust'])
        api = _client(stranger)
        self.assertEqual(api.get(f'/api/admin/catalog/models/{self.model.pk}/').status_code, 404)
        self.assertEqual(api.patch(f'/api/admin/catalog/options/{self.black.pk}/', {'label': 'x'},
                                   format='json').status_code, 404)
        self.assertEqual(api.post(f'/api/admin/catalog/attributes/{self.colour.pk}/options/',
                                  {'label': 'x', 'swatch_hex': '#000000'}, format='json').status_code, 404)
        self.assertEqual(api.put(f'/api/admin/products/{self.new128.pk}/variant/', {'model_id': None},
                                 format='json').status_code, 404)
        unit, = self.receive(self.used128)
        self.assertEqual(api.patch(f'/api/admin/inventory/units/{unit.pk}/', {'battery_health': 90},
                                   format='json').status_code, 404)
        self.assertEqual(api.delete(f'/api/admin/inventory/units/{unit.pk}/photos/1/').status_code, 404)
        self.assertEqual(api.get('/api/admin/catalog/models/').json()['results'], [])

    def test_viewing_without_manage_cannot_write(self):
        viewer, _ = _p2d_member(self.company, 'dc2_lector', ['company.view', 'products.view'])
        api = _client(viewer)
        self.assertEqual(api.get('/api/admin/catalog/attributes/').status_code, 200)
        self.assertEqual(api.post('/api/admin/catalog/models/', {'name': 'No'}, format='json').status_code, 403)
        self.assertEqual(api.post('/api/admin/catalog/grades/seed/').status_code, 403)

    def test_unit_details_for_buyers_are_edited_and_validated(self):
        unit, = self.receive(self.used128)
        url = f'/api/admin/inventory/units/{unit.pk}/'
        res = self.api.patch(url, {'battery_health': 87, 'cosmetic_grade': self.grade.pk,
                                   'public_notes': 'Con caja', 'sale_price': '2499.90'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual((body['battery_health'], body['cosmetic_grade_label'], body['sale_price']),
                         (87, 'Excelente', '2499.90'))
        self.assertNotIn('cost', body)
        for bad in (101, -1, 'mucho', 87.5):
            with self.subTest(bad=bad):
                self.assertEqual(self.api.patch(url, {'battery_health': bad}, format='json').status_code, 400)
        foreign_grade = CosmeticGrade.objects.create(company=_p3_company('dc2-g', 'G'), code='raro', label='Raro')
        self.assertEqual(self.api.patch(url, {'cosmetic_grade': foreign_grade.pk}, format='json').status_code, 400)
        self.assertEqual(self.api.patch(url, {'battery_health': ''}, format='json').json()['battery_health'], None)
        listed = self.api.get('/api/admin/inventory/units/?branch=all&line=used').json()['results']
        self.assertEqual([row['id'] for row in listed], [unit.pk])

    def test_line_labels_and_the_grade_scale_from_the_panel(self):
        res = self.api.patch('/api/admin/catalog/settings/', {'used_line_label': '  Usados  '}, format='json')
        self.assertEqual((res.status_code, res.json()['used_line_label']), (200, 'Usados'))
        self.assertEqual(device_catalog.line_labels(type(self.company).objects.get(pk=self.company.pk)),
                         {'new': 'Nuevos', 'used': 'Usados'})
        res = self.api.post('/api/admin/catalog/grades/', {'label': 'Con detalles'}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (201, 'con-detalles'))
        self.assertEqual(self.api.post('/api/admin/catalog/grades/', {'label': 'Con detalles'},
                                       format='json').status_code, 400)
        self.assertEqual(self.api.post('/api/admin/catalog/grades/seed/').json()['created'], 0)

    def test_a_sold_units_price_cannot_change(self):
        unit, = self.receive(self.used128, sale_price='2000.00')
        StockUnit.objects.filter(pk=unit.pk).update(status='sold')
        with self.assertRaises(units.StockUnitError):
            units.update_unit(unit, sale_price='1.00')


# ---------------------------------------------------------------------------
# What the public reads
# ---------------------------------------------------------------------------

@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class PublicModelApiTest(DeviceShop, TestCase):
    def setUp(self):
        self.build()
        self.receive(self.new128, 2, cost='2500.00')
        self.used, self.unpriced, self.measured = self.receive(self.used128, 3, cost='1200.00')
        units.update_unit(self.used, sale_price='1999.00', cosmetic_grade=self.grade.pk, public_notes='Caja')
        units.update_unit(self.measured, sale_price='2199.00', battery_health=91)
        self.public = _client(host=self.host)

    def detail(self):
        res = self.public.get(f'/api/catalog/models/{self.model.slug}/')
        self.assertEqual(res.status_code, 200, res.content)
        return res

    def test_options_variants_and_availability(self):
        body = self.detail().json()
        names = [a['name'] for a in body['attributes']]
        self.assertEqual(names, ['Capacidad', 'Color'])
        colour = body['attributes'][1]
        self.assertEqual({o['label']: o['swatch_hex'] for o in colour['options']}, {'Negro': '#111111', 'Azul': '#1D4ED8'})
        variants = {v['product_id']: v for v in body['variants']}
        self.assertEqual(set(variants), {self.new128.pk, self.new256.pk})
        self.assertTrue(variants[self.new128.pk]['available'])
        # No stock of 256 GB: the combination is offered as unavailable, not hidden.
        self.assertFalse(variants[self.new256.pk]['available'])
        self.assertEqual(variants[self.new256.pk]['availability'], 'none')
        self.assertEqual(variants[self.new128.pk]['options'],
                         {str(self.capacity.pk): self.gb128.pk, str(self.colour.pk): self.black.pk})
        self.assertEqual(body['lines']['used'], {'label': 'Seminuevos', 'count': 2})

    def test_used_devices_one_by_one_never_with_the_full_imei_serial_or_cost(self):
        res = self.detail()
        body = res.json()
        cards = {card['id']: card for card in body['used_units']}
        # A used device without its own price is not offered.
        self.assertEqual(set(cards), {str(self.used.public_id), str(self.measured.public_id)})
        card = cards[str(self.used.public_id)]
        self.assertIsNone(card['battery_health'])            # «no informado», never invented
        self.assertEqual(cards[str(self.measured.public_id)]['battery_health'], 91)
        self.assertEqual(card['cosmetic_grade']['label'], 'Excelente')
        self.assertEqual((card['price'], card['notes']), ('1999.00', 'Caja'))
        self.assertEqual(card['imei_hint'], f'•••• {self.used.imei[-4:]}')
        text = res.content.decode()
        for unit in (self.used, self.unpriced, self.measured):
            self.assertNotIn(unit.imei, text)
            self.assertNotIn(unit.serial_number, text)
        self.assertNotIn('1200', text)
        self.assertNotIn('2500', text)
        for forbidden in ('"cost"', 'supplier', 'proveedor', 'serial', '"pk"'):
            self.assertNotIn(forbidden, text)
        self.assertEqual(set(card), {'id', 'product_id', 'options', 'battery_health', 'cosmetic_grade', 'price',
                                     'photos', 'notes', 'imei_hint'})

    def test_another_storefront_sees_nothing_of_it(self):
        _p3_company('dc2-vecina', 'Vecina')
        res = _client(host='dc2-vecina.tienda.pe').get(f'/api/catalog/models/{self.model.slug}/')
        self.assertEqual(res.status_code, 404)
        listing = _client(host='dc2-vecina.tienda.pe').get('/api/catalog/models/').json()
        self.assertEqual(listing['results'], [])

    def test_the_category_listing_counts_both_lines_with_the_shops_labels(self):
        from store.models import StorefrontPageSettings
        StorefrontPageSettings.objects.update_or_create(company=self.company,
                                                         defaults={'used_line_label': 'Reacondicionados'})
        body = self.public.get('/api/catalog/models/').json()
        self.assertEqual(body['lines'], {'new': {'label': 'Nuevos', 'count': 1},
                                         'used': {'label': 'Reacondicionados', 'count': 1}})
        [row] = body['results']
        self.assertEqual((row['has_new'], row['has_used'], row['from_price']), (True, True, '1999.00'))
        self.assertEqual(self.public.get('/api/catalog/models/?line=new').json()['results'][0]['from_price'],
                         '3000.00')

    def test_the_plain_catalogue_does_not_list_used_variants(self):
        slugs = {p['slug'] for p in self.public.get('/api/products/').json()['results']} \
            if isinstance(self.public.get('/api/products/').json(), dict) else \
            {p['slug'] for p in self.public.get('/api/products/').json()}
        self.assertIn(self.new128.slug, slugs)
        self.assertNotIn(self.used128.slug, slugs)
        product = self.public.get(f'/api/products/?slug={self.new128.slug}').json()
        product = product['results'][0] if isinstance(product, dict) else product[0]
        self.assertEqual((product['product_model'], product['condition_line'], product['sells_by_unit']),
                         (self.model.slug, 'new', True))

    def test_photos_are_public_images_of_the_company(self):
        from store import storefront_media
        stored = storefront_media.StorefrontImage.objects.create(
            company=self.company, public_id='a' * 32, storage_key='k', mime_type='image/webp', byte_size=1,
            width=10, height=10)
        UnitPhoto.objects.create(company=self.company, unit=self.used, image_url=stored.url, width=10, height=10)
        card = {c['id']: c for c in self.detail().json()['used_units']}[str(self.used.public_id)]
        self.assertEqual(card['photos'][0]['url'], stored.url)
        other = _p3_company('dc2-foto', 'Foto')
        with self.assertRaises(ValueError):
            UnitPhoto.objects.create(company=other, unit=self.used, image_url=stored.url)


# ---------------------------------------------------------------------------
# The cart holds a concrete device
# ---------------------------------------------------------------------------

# Holding every device of a shelf is what these tests do; the share limit has its own test.
@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'], DEVICE_WEB_HOLD_MAX_SHARE_PERCENT=100)
class HoldTest(DeviceShop, TestCase):
    def setUp(self):
        self.build()
        self.old, self.young = self.receive(self.new128, 2, cost='2500.00')
        StockUnit.objects.filter(pk=self.old.pk).update(received_at=timezone.now() - timedelta(days=30))
        self.used_a, self.used_b = self.receive(self.used128, 2)
        units.update_unit(self.used_a, sale_price='1999.00')
        units.update_unit(self.used_b, sale_price='1500.00')
        self.public = _client(host=self.host)

    def add(self, product, session='carrito-a', unit=None, client=None):
        body = {'session_key': session, 'product': product.pk}
        if unit is not None:
            body['unit'] = str(unit.public_id)
        return (client or self.public).post('/api/cart/add/', body, format='json')

    def test_adding_a_new_variant_holds_the_oldest_device_with_its_kardex(self):
        res = self.add(self.new128)
        self.assertEqual(res.status_code, 200, res.content)
        line = res.json()
        self.assertEqual((line['quantity'], line['line_total'], line['sells_by_unit']), (1, '3000.00', True))
        self.old.refresh_from_db()
        self.assertEqual((self.old.status, self.old.hold_price), ('reserved', D('3000.00')))
        self.assertEqual(self.old.hold_key, holds.key_of('carrito-a'))
        self.assertNotIn('carrito-a', self.old.hold_key)
        self.assertEqual((self.shelf(self.new128), self.available(self.new128)), (1, 1))
        self.assertTrue(StockMovement.objects.filter(product=self.new128, movement_type='manual_exit',
                                                     reason=holds.HOLD_REASON).exists())
        self.assertEqual(line['units'][0]['imei_hint'], f'•••• {self.old.imei[-4:]}')
        self.assertNotIn(self.old.imei, res.content.decode())

    def test_a_used_device_is_bought_by_name_and_only_once(self):
        self.assertEqual(self.add(self.used128).json()['code'], 'unit_required')
        self.assertEqual(self.add(self.used128, unit=self.used_b).status_code, 200)
        taken = self.add(self.used128, session='carrito-b', unit=self.used_b)
        self.assertEqual((taken.status_code, taken.json()['code']), (409, 'unit_taken'))
        self.assertEqual(self.add(self.used128, unit=self.used_b).status_code, 409)
        # A device of another company or a nonsense id is simply not there.
        for bad in ('no-es-uuid', '00000000-0000-0000-0000-000000000000'):
            res = self.public.post('/api/cart/add/', {'session_key': 'x', 'product': self.used128.pk, 'unit': bad},
                                   format='json')
            self.assertEqual(res.status_code, 404)

    def test_two_used_devices_of_one_variant_keep_their_own_prices(self):
        self.add(self.used128, unit=self.used_a)
        res = self.add(self.used128, unit=self.used_b)
        line = res.json()
        self.assertEqual((line['quantity'], line['line_total']), (2, '3499.00'))
        self.assertEqual(sorted(u['price'] for u in line['units']), ['1500.00', '1999.00'])
        listed = self.public.get('/api/cart/?session_key=carrito-a').json()
        listed = listed['results'] if isinstance(listed, dict) else listed
        self.assertEqual(listed[0]['line_total'], '3499.00')

    def test_expired_holds_go_back_to_the_shelf_on_read_and_by_command(self):
        from django.core.management import call_command
        import io
        self.add(self.new128)
        self.add(self.used128, unit=self.used_a, session='carrito-b')
        StockUnit.objects.filter(status='reserved').update(hold_until=timezone.now() - timedelta(minutes=1))
        # On read: reading the cart sends the shop's expired holds back.
        listed = self.public.get('/api/cart/?session_key=carrito-a').json()
        listed = listed['results'] if isinstance(listed, dict) else listed
        self.assertEqual(listed, [])
        self.old.refresh_from_db()
        self.assertEqual((self.old.status, self.old.hold_key), ('available', ''))
        self.assertFalse(StockUnit.objects.filter(status='reserved').exists())
        # By command: the ones nobody read.
        self.add(self.used128, unit=self.used_b, session='carrito-c')
        StockUnit.objects.filter(status='reserved').update(hold_until=timezone.now() - timedelta(minutes=1))
        out = io.StringIO()
        call_command('release_expired_unit_holds', stdout=out)
        self.assertIn('devueltos al estante: 1', out.getvalue())
        self.assertFalse(StockUnit.objects.filter(status='reserved').exists())
        for product in (self.new128, self.used128):
            self.assertEqual(self.shelf(product), self.available(product))

    def test_a_hold_past_its_time_is_not_the_carts_even_before_it_is_swept(self):
        self.add(self.used128, unit=self.used_a)
        self.assertEqual(list(holds.held_units('carrito-a', self.company)), [StockUnit.objects.get(pk=self.used_a.pk)])
        StockUnit.objects.filter(pk=self.used_a.pk).update(hold_until=timezone.now() - timedelta(seconds=1))
        self.assertEqual(list(holds.held_units('carrito-a', self.company)), [])

    def test_a_staff_reservation_never_expires_and_no_cart_touches_it(self):
        units.reserve(self.used_a, reason='Cliente en tienda', actor=self.staff)
        StockUnit.objects.filter(pk=self.used_a.pk).update(hold_until=timezone.now() - timedelta(days=1))
        holds.release_expired()
        self.used_a.refresh_from_db()
        self.assertEqual(self.used_a.status, 'reserved')
        self.assertEqual(self.add(self.used128, unit=self.used_a).status_code, 409)
        self.assertEqual(holds.release(company=self.company, session_key='', unit_public_id=str(self.used_a.public_id)), 0)

    def test_releasing_one_device_removing_the_line_and_the_limit(self):
        self.add(self.used128, unit=self.used_a)
        self.add(self.used128, unit=self.used_b)
        res = self.public.post('/api/cart/release-unit/', {'session_key': 'carrito-a',
                                                           'unit': str(self.used_a.public_id)}, format='json')
        self.assertEqual(res.json(), {'released': 1})
        self.assertEqual(CartItem.objects.get(session_key='carrito-a', product=self.used128).quantity, 1)
        # Another cart cannot release it.
        res = self.public.post('/api/cart/release-unit/', {'session_key': 'otro', 'unit': str(self.used_b.public_id)},
                               format='json')
        self.assertEqual(res.json(), {'released': 0})
        item = CartItem.objects.get(session_key='carrito-a', product=self.used128)
        self.assertEqual(self.public.patch(f'/api/cart/{item.pk}/?session_key=carrito-a', {'quantity': 3},
                                           format='json').json()['code'], 'by_unit')
        self.assertEqual(self.public.delete(f'/api/cart/{item.pk}/?session_key=carrito-a').status_code, 204)
        self.assertEqual(StockUnit.objects.filter(status='reserved').count(), 0)
        with self.settings(DEVICE_HOLD_MAX_PER_CART=2):
            self.add(self.used128, unit=self.used_a)
            self.add(self.new128)
            self.assertEqual(self.add(self.new128).json()['code'], 'too_many')

    def test_the_till_cannot_sell_a_device_a_buyer_holds(self):
        from store import unit_identification
        self.add(self.used128, unit=self.used_b)
        with self.assertRaises(unit_identification.UnitIdentificationError):
            unit_identification.sellable_unit(company=self.company, branch=self.branch, code=self.used_b.imei,
                                              method='scanner')


# ---------------------------------------------------------------------------
# Checkout and payment sell THE device
# ---------------------------------------------------------------------------

class SaleShop(DeviceShop):
    def setUp(self):
        self.build()
        self.old, self.young = self.receive(self.new128, 2)
        StockUnit.objects.filter(pk=self.old.pk).update(received_at=timezone.now() - timedelta(days=30), cost=D('2400'))
        StockUnit.objects.filter(pk=self.young.pk).update(cost=D('2600'))
        self.used_a, self.used_b = self.receive(self.used128, 2)
        units.update_unit(self.used_a, sale_price='1999.00', cost='1100.00')
        units.update_unit(self.used_b, sale_price='1500.00', cost='900.00')

    def checkout(self, session='carrito-a'):
        items = list(CartItem.objects.filter(session_key=session).select_related('product'))
        lines = checkout.lines_from_cart(self.company, self.branch, session, items)
        subtotal = checkout.validate_lines_and_subtotal(self.branch, lines)
        pricing = checkout.price_checkout(self.company, subtotal)
        return checkout.create_pending_order(
            company=self.company, branch=self.branch, lines=lines, pricing=pricing,
            details=checkout.CustomerDetails(
                name='Ana', email='ana@example.com', phone='987654321', document_type='DNI',
                document_number='12345678', delivery_method='pickup', receipt_type='boleta',
                accepted_terms=True, accepted_warranty_policy=True),
            cart_session_key=session,
        )

    def pay(self, order):
        """What `_confirm` does with the stock, in its transaction."""
        with transaction.atomic():
            inventory.record_sale_stock_movements(order)
            Order.objects.filter(pk=order.pk).update(status=Order.Status.PAID, paid=True, paid_at=timezone.now(),
                                                     payment_error=order.payment_error)


@override_settings(DEVICE_WEB_HOLD_MAX_SHARE_PERCENT=100)
class ExactUnitSaleTest(SaleShop, TestCase):
    def test_payment_sells_the_held_device_not_the_oldest(self):
        # The cart got the oldest; let a buyer hold the YOUNG one by freeing the old first.
        holds.hold(product=self.new128, branch=self.branch, session_key='otro')              # takes the old
        holds.hold(product=self.new128, branch=self.branch, session_key='carrito-a')         # takes the young
        holds.release(company=self.company, session_key='otro')                               # old back on the shelf
        order = self.checkout()
        [line] = order.items.all()
        self.assertEqual((line.stock_unit_id, line.price, line.quantity), (self.young.pk, D('3000.00'), 1))
        self.young.refresh_from_db()
        self.assertEqual(self.young.hold_order_id, order.pk)
        self.pay(order)
        self.young.refresh_from_db()
        self.old.refresh_from_db()
        self.assertEqual((self.young.status, self.young.order_id, self.young.hold_key), ('sold', order.pk, ''))
        self.assertEqual(self.old.status, 'available')
        line.refresh_from_db()
        self.assertEqual((line.unit_cost, line.cost_source), (D('2600.0000'), 'unit'))
        self.assertEqual(OrderUnit.objects.get(order=order).unit_id, self.young.pk)
        self.assertEqual(OrderUnit.objects.get(order=order).method, 'buyer')
        sale = StockMovement.objects.get(order=order, movement_type='sale_exit')
        self.assertEqual(sale.metadata['unit_ids'], [self.young.pk])
        self.assertEqual((self.shelf(self.new128), self.available(self.new128)), (1, 1))
        # Replayed notification: nothing moves twice.
        inventory.record_sale_stock_movements(order)
        self.assertEqual(StockMovement.objects.filter(order=order, movement_type='sale_exit').count(), 1)

    def test_two_used_devices_with_different_prices_are_two_exact_lines(self):
        holds.hold(product=self.used128, branch=self.branch, session_key='carrito-a',
                   unit_public_id=str(self.used_a.public_id))
        holds.hold(product=self.used128, branch=self.branch, session_key='carrito-a',
                   unit_public_id=str(self.used_b.public_id))
        order = self.checkout()
        self.assertEqual(order.total, D('3499.00'))
        lines = {line.stock_unit_id: line for line in order.items.all()}
        self.assertEqual({k: v.price for k, v in lines.items()},
                         {self.used_a.pk: D('1999.00'), self.used_b.pk: D('1500.00')})
        self.pay(order)
        for line in order.items.all():
            self.assertEqual(line.cost_source, 'unit')
        costs = {line.stock_unit_id: line.unit_cost for line in order.items.all()}
        self.assertEqual(costs, {self.used_a.pk: D('1100.0000'), self.used_b.pk: D('900.0000')})
        nets = sum(line.net_amount for line in order.items.all())
        self.assertEqual(nets, Order.objects.get(pk=order.pk).taxable_amount)
        self.assertEqual(StockMovement.objects.filter(order=order, movement_type='sale_exit').count(), 1)
        self.assertEqual(set(OrderUnit.objects.filter(order=order).values_list('unit_id', flat=True)),
                         {self.used_a.pk, self.used_b.pk})
        # The documents print each device on its own line.
        from store.pdf_services import order_lines
        printed = order_lines(Order.objects.get(pk=order.pk))
        self.assertEqual(sorted(line['unit_price'] for line in printed), [D('1500.00'), D('1999.00')])
        self.assertEqual(self.shelf(self.used128), self.available(self.used128))

    def test_a_price_changed_since_the_cart_is_told_before_anything_is_created(self):
        holds.hold(product=self.used128, branch=self.branch, session_key='carrito-a',
                   unit_public_id=str(self.used_a.public_id))
        units.update_unit(self.used_a, sale_price='2100.00')
        with self.assertRaises(checkout.CheckoutError) as caught:
            self.checkout()
        self.assertEqual(caught.exception.code, 'cart_changed')
        self.assertFalse(Order.objects.exists())
        order = self.checkout()            # the buyer saw it: the new price is charged
        self.assertEqual(order.total, D('2100.00'))

    def test_a_cart_cannot_drop_a_device_its_pending_order_may_still_pay_for(self):
        holds.hold(product=self.used128, branch=self.branch, session_key='carrito-a',
                   unit_public_id=str(self.used_a.public_id))
        order = self.checkout()
        self.assertEqual(holds.release(company=self.company, session_key='carrito-a'), 0)
        self.used_a.refresh_from_db()
        self.assertEqual((self.used_a.status, self.used_a.hold_order_id), ('reserved', order.pk))
        Order.objects.filter(pk=order.pk).update(status=Order.Status.EXPIRED)
        self.assertEqual(holds.release(company=self.company, session_key='carrito-a'), 1)

    def test_an_expired_hold_cannot_be_checked_out(self):
        holds.hold(product=self.new128, branch=self.branch, session_key='carrito-a')
        StockUnit.objects.filter(status='reserved').update(hold_until=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(checkout.CheckoutError):
            self.checkout()

    def test_a_hold_that_ran_out_before_the_payment_still_sells_that_device_if_free(self):
        holds.hold(product=self.used128, branch=self.branch, session_key='carrito-a',
                   unit_public_id=str(self.used_a.public_id))
        order = self.checkout()
        StockUnit.objects.filter(pk=self.used_a.pk).update(hold_until=timezone.now() - timedelta(minutes=1))
        holds.release_expired()
        self.pay(order)
        self.used_a.refresh_from_db()
        self.assertEqual((self.used_a.status, self.used_a.order_id), ('sold', order.pk))

    def test_a_device_taken_meanwhile_is_a_shortfall_not_another_device(self):
        holds.hold(product=self.used128, branch=self.branch, session_key='carrito-a',
                   unit_public_id=str(self.used_a.public_id))
        order = self.checkout()
        StockUnit.objects.filter(pk=self.used_a.pk).update(hold_until=timezone.now() - timedelta(minutes=1))
        holds.release_expired()
        holds.hold(product=self.used128, branch=self.branch, session_key='intruso',
                   unit_public_id=str(self.used_a.public_id))
        self.pay(order)
        order.refresh_from_db()
        self.assertIn('Stock insuficiente', order.payment_error)
        self.assertFalse(StockUnit.objects.filter(order=order).exists())
        self.assertEqual(StockUnit.objects.get(pk=self.used_b.pk).status, 'available')

    def test_the_app_checkout_refuses_a_product_sold_by_unit(self):
        with self.assertRaises(checkout.CheckoutError) as caught:
            checkout.resolve_lines_from_intents(self.company, [{'product_slug': self.new128.slug, 'quantity': 1}])
        self.assertEqual(caught.exception.code, 'unit_required')

    def test_the_buyers_choice_is_not_swapped_at_hand_over(self):
        from store import unit_identification
        holds.hold(product=self.used128, branch=self.branch, session_key='carrito-a',
                   unit_public_id=str(self.used_a.public_id))
        order = self.checkout()
        self.pay(order)
        order.refresh_from_db()
        self.assertEqual(unit_identification.pending_units(order), {})
        row = OrderUnit.objects.get(order=order)
        with self.assertRaises(unit_identification.UnitIdentificationError) as caught:
            unit_identification.forget(order=order, order_unit_id=row.pk, actor=self.staff)
        self.assertEqual(caught.exception.code, 'chosen_by_buyer')
        with self.assertRaises(unit_identification.UnitIdentificationError):
            unit_identification.candidate_for_order(order=order, code=self.used_b.imei, method='scanner')


class SerialPickStaysForProductsWithoutAModelTest(DeviceShop, TestCase):
    """A serialized product outside any model keeps SERIAL-PICK-01: the payment sets the oldest aside."""

    def setUp(self):
        self.build()

    def test_the_oldest_is_set_aside_and_nobody_is_named(self):
        loose = _prod(self.company, 'Reloj', 'reloj-dc2', price='900.00', inventory=0)
        Product.objects.filter(pk=loose.pk).update(is_serialized=True)
        loose.refresh_from_db()
        self.assertFalse(loose.sells_by_unit)
        first, second = self.receive(loose, 2)
        StockUnit.objects.filter(pk=first.pk).update(received_at=timezone.now() - timedelta(days=9))
        order = Order.objects.create(company=self.company, fulfillment_branch=self.branch, customer_email='x@e.com',
                                     total=D('900.00'), status=Order.Status.PENDING_PAYMENT)
        OrderItem.objects.create(order=order, product=loose, quantity=1, price=D('900.00'))
        inventory.record_sale_stock_movements(order)
        self.assertEqual(StockUnit.objects.get(pk=first.pk).status, 'sold')
        self.assertFalse(OrderUnit.objects.filter(order=order).exists())


class TwoBuyersOneDeviceTest(TransactionTestCase):
    """Two buyers press «Comprar este equipo» at the same instant: one holds it, the other is told."""

    def test_only_one_cart_holds_the_device(self):
        shop = DeviceShop()
        shop.build('dc2-carrera')
        unit, = shop.receive(shop.used128)
        units.update_unit(unit, sale_price='1000.00')
        barrier, outcomes = Barrier(2), []

        def buy(session):
            try:
                barrier.wait(timeout=5)
                holds.hold(product=Product.objects.get(pk=shop.used128.pk), branch=shop.branch,
                           session_key=session, unit_public_id=str(unit.public_id))
                outcomes.append('held')
            except holds.HoldError as exc:
                outcomes.append(exc.code)
            finally:
                connection.close()

        threads = [Thread(target=buy, args=(name,)) for name in ('carrito-1', 'carrito-2')]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertEqual(sorted(outcomes), ['held', 'unit_taken'])
        self.assertEqual(StockUnit.objects.filter(status='reserved').count(), 1)
        self.assertEqual(BranchStock.objects.get(branch=shop.branch, product=shop.used128).quantity, 0)
        self.assertEqual(StockMovement.objects.filter(product=shop.used128, movement_type='manual_exit').count(), 1)


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------

class ExcelColumnsTest(DeviceShop, TestCase):
    UNIT_HEADERS = ['Código', 'Producto', 'Sucursal', 'Número de serie', 'IMEI', 'IMEI 2', 'Condición', 'Costo',
                    'Motivo / referencia', 'Modelo', 'Capacidad', 'Color', 'Línea', 'Estado estético',
                    'Batería %', 'Precio de venta']

    def setUp(self):
        self.build()

    def units_file(self, headers, rows):
        from store import unit_import_services
        from store.test_cost_imports import upload
        job = unit_import_services.preview_units(
            company=self.company, actor=self.staff, upload=upload(headers, rows, title='Equipos'),
            filename='equipos.xlsx', reachable_branch_ids=[self.branch.pk], default_branch=self.branch.pk,
            default_reason='Compra',
        )
        return job, list(job.rows.order_by('row_number'))

    def test_the_templates_offer_the_columns(self):
        import io as _io
        import openpyxl
        from store import import_exports, unit_import_services
        sheet = openpyxl.load_workbook(_io.BytesIO(unit_import_services.template_bytes())).active
        self.assertEqual([c.value for c in sheet[1]], self.UNIT_HEADERS)
        sheet = openpyxl.load_workbook(_io.BytesIO(import_exports.product_template_bytes(self.company))).active
        self.assertEqual([c.value for c in sheet[1]][-4:], ['Modelo', 'Capacidad', 'Color', 'Línea'])

    def test_a_used_device_by_model_options_and_line_with_its_details(self):
        from store import unit_import_services
        value = IMEIS[30]
        job, [row] = self.units_file(self.UNIT_HEADERS, [[
            '', '', '', 'SERUSADO01', value, '', 'Usado', '1100', 'Compra', 'Teléfono X', '128 GB', 'Azul',
            'Seminuevo', 'Excelente', '88', '1899.90']])
        self.assertEqual(row.errors, [])
        self.assertEqual(row.normalized_data['product_id'], self.used128.pk)
        unit_import_services.apply_units(job=job, actor=self.staff)
        unit = StockUnit.objects.get(serial_number='SERUSADO01')
        self.assertEqual((unit.product_id, unit.battery_health, unit.cosmetic_grade_id, unit.sale_price),
                         (self.used128.pk, 88, self.grade.pk, D('1899.90')))
        self.assertEqual(self.shelf(self.used128), 1)

    def test_bad_values_are_row_errors_and_a_missing_battery_is_not_informed(self):
        job, rows = self.units_file(self.UNIT_HEADERS, [
            ['', '', '', 'SERUSADO02', IMEIS[31], '', '', '', 'Compra', 'Teléfono X', '128 GB', 'Azul',
             'Seminuevo', '', '120', ''],
            ['', '', '', 'SERUSADO03', IMEIS[32], '', '', '', 'Compra', 'Teléfono X', '128 GB', 'Rojo',
             'Seminuevo', '', '', ''],
            ['', '', '', 'SERUSADO04', IMEIS[33], '', '', '', 'Compra', 'Teléfono X', '128 GB', 'Azul',
             'Seminuevo', 'Impecable', '', ''],
            ['', '', '', 'SERUSADO05', IMEIS[34], '', '', '', 'Compra', 'Teléfono X', '128 GB', 'Azul',
             'Seminuevo', '', '', ''],
            ['', 'Teléfono X 128 GB Negro', '', 'SERNUEVO06', IMEIS[35], '', '', '', 'Compra', '', '', '',
             'Seminuevo', '', '', ''],
        ])
        self.assertTrue(rows[0].errors)                   # 120 %
        self.assertTrue(rows[1].errors)                   # no such option
        self.assertTrue(rows[2].errors)                   # no such grade
        self.assertEqual(rows[3].errors, [])
        self.assertNotIn('battery_health', rows[3].normalized_data)
        self.assertTrue(rows[4].errors)                   # a new product is not of the used line

    def test_a_new_device_ignores_a_sale_price_and_keeps_its_variant_price(self):
        job, [row] = self.units_file(self.UNIT_HEADERS, [[
            '', '', '', 'SERNUEVO07', IMEIS[36], '', '', '', 'Compra', 'Teléfono X', '256 GB', 'Negro',
            'Nuevo', '', '', '9.99']])
        self.assertEqual(row.errors, [])
        self.assertNotIn('sale_price', row.normalized_data)
        self.assertTrue(any('Precio de venta ignorado' in w for w in row.warnings))

    def test_an_old_device_file_loads_unchanged(self):
        from store import unit_import_services
        old_headers = ['Código', 'Modelo', 'Sucursal', 'Número de serie', 'IMEI', 'IMEI 2', 'Condición', 'Costo',
                       'Motivo / referencia']
        job, [row] = self.units_file(old_headers, [
            ['', 'Teléfono X 256 GB Negro', '', 'SERVIEJO01', IMEIS[37], '', 'Nuevo', '2000', 'Compra']])
        self.assertEqual((row.errors, row.normalized_data['product_id']), ([], self.new256.pk))
        unit_import_services.apply_units(job=job, actor=self.staff)
        unit = StockUnit.objects.get(serial_number='SERVIEJO01')
        self.assertEqual((unit.battery_health, unit.cosmetic_grade_id, unit.sale_price, unit.cost),
                         (None, None, None, D('2000.00')))

    def test_a_product_row_becomes_a_variant_and_old_product_files_are_unchanged(self):
        from store import import_services
        from store.test_cost_imports import upload
        headers = ['Código', 'Nombre', 'Precio de venta', 'Modelo', 'Capacidad', 'Color', 'Línea']
        job = import_services.preview_products(
            company=self.company, actor=self.staff, filename='p.xlsx', upload=upload(headers, [
                ['TX-256-AZ', 'Teléfono X 256 GB Azul', '3600', 'Teléfono X', '256 GB', 'Azul', 'Nuevo'],
                ['TX-DUP', 'Duplicado', '3000', 'Teléfono X', '128 GB', 'Negro', 'Nuevo'],
                ['TX-BAD', 'Malo', '3000', 'Teléfono Z', '128 GB', 'Negro', ''],
            ]))
        good, duplicate, bad = job.rows.order_by('row_number')
        self.assertEqual(good.errors, [])
        self.assertTrue(duplicate.errors)
        self.assertTrue(bad.errors)
        job.rows.filter(pk__in=[duplicate.pk, bad.pk]).delete()
        type(job).objects.filter(pk=job.pk).update(rows_error=0)
        job.refresh_from_db()
        import_services.apply_products(job=job, actor=self.staff)
        made = Product.objects.get(name='Teléfono X 256 GB Azul')
        self.assertEqual((made.product_model_id, made.condition_line), (self.model.pk, 'new'))
        self.assertEqual({o.label for o in device_catalog.variant_options_of(made)}, {'256 GB', 'Azul'})

        old = import_services.preview_products(
            company=self.company, actor=self.staff, filename='p.xlsx',
            upload=upload(['Código', 'Nombre', 'Precio de venta'], [['ACC-1', 'Cable', '20']]))
        [row] = old.rows.all()
        self.assertNotIn('variant', row.normalized_data)
        import_services.apply_products(job=old, actor=self.staff)
        self.assertIsNone(Product.objects.get(name='Cable').product_model_id)


# ---------------------------------------------------------------------------
# DC2 review — holds cannot be stolen, stretched or hoarded
# ---------------------------------------------------------------------------

class BindGuardTest(SaleShop, TestCase):
    """`bind_to_order` binds THIS cart's live holds and nothing else."""

    def test_a_hold_that_expired_and_was_taken_by_another_cart_is_not_bound(self):
        holds.hold(product=self.used128, branch=self.branch, session_key='carrito-a',
                   unit_public_id=str(self.used_a.public_id))
        items = list(CartItem.objects.filter(session_key='carrito-a').select_related('product'))
        lines = checkout.lines_from_cart(self.company, self.branch, 'carrito-a', items)
        # Between reading the cart and writing the order: expired, swept, held by B.
        StockUnit.objects.filter(pk=self.used_a.pk).update(hold_until=timezone.now() - timedelta(minutes=1))
        holds.release_expired()
        holds.hold(product=self.used128, branch=self.branch, session_key='carrito-b',
                   unit_public_id=str(self.used_a.public_id))
        pricing = checkout.price_checkout(self.company, D('1999.00'))
        with self.assertRaises(checkout.CheckoutError) as caught:
            checkout.create_pending_order(
                company=self.company, branch=self.branch, lines=lines, pricing=pricing,
                details=checkout.CustomerDetails(
                    name='Ana', email='ana@example.com', phone='987654321', document_type='DNI',
                    document_number='12345678', delivery_method='pickup', receipt_type='boleta',
                    accepted_terms=True, accepted_warranty_policy=True),
                cart_session_key='carrito-a',
            )
        self.assertEqual(caught.exception.code, 'cart_changed')
        self.assertFalse(Order.objects.exists())
        self.used_a.refresh_from_db()
        self.assertEqual((self.used_a.hold_key, self.used_a.hold_order_id), (holds.key_of('carrito-b'), None))


@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class HoldAbuseTest(DeviceShop, TestCase):
    """One client cannot empty a shelf through many carts, nor keep it forever."""

    def setUp(self):
        self.build()
        self.receive(self.new128, 10)
        self.public = _client(host=self.host)

    def add(self, session, ip='203.0.113.7', product=None):
        return self.public.post('/api/cart/add/', {'session_key': session, 'product': (product or self.new128).pk},
                                format='json', REMOTE_ADDR=ip)

    def test_one_origin_holds_at_most_three_devices_whatever_the_carts(self):
        for n in range(3):
            self.assertEqual(self.add(f'carro-{n}').status_code, 200)
        refused = self.add('carro-9')
        self.assertEqual((refused.status_code, refused.json()['code']), (409, 'too_many'))
        self.assertEqual(StockUnit.objects.filter(status='reserved').count(), 3)
        # The origin is a keyed hash, never the address.
        origins = set(StockUnit.objects.filter(status='reserved').values_list('hold_origin', flat=True))
        self.assertEqual(len(origins), 1)
        self.assertNotIn('203.0.113.7', origins.pop())
        # Somebody else is not affected.
        self.assertEqual(self.add('vecino', ip='198.51.100.4').status_code, 200)
        with self.settings(DEVICE_HOLD_MAX_PER_ORIGIN=5):
            self.assertEqual(self.add('carro-9').status_code, 200)

    def test_web_holds_take_at_most_half_of_a_shelf(self):
        held = [self.add(f'c{n}', ip=f'198.51.100.{n + 1}').status_code for n in range(6)]
        self.assertEqual(held, [200] * 5 + [409])
        self.assertEqual(self.add('c9', ip='198.51.100.99').json()['code'], 'held_by_others')
        self.assertEqual(StockUnit.objects.filter(status='reserved').count(), 5)
        # A shelf of one: the one device can be held (max(1, half)).
        self.receive(self.new256, 1)
        self.assertEqual(self.add('solo', ip='198.51.100.50', product=self.new256).status_code, 200)
        self.receive(self.new256, 1)     # 2 devices, one held: half is taken
        self.assertEqual(self.add('solo2', ip='198.51.100.51', product=self.new256).json()['code'],
                         'held_by_others')
        with self.settings(DEVICE_WEB_HOLD_MAX_SHARE_PERCENT=100):
            self.assertEqual(self.add('solo2', ip='198.51.100.51', product=self.new256).status_code, 200)

    def test_a_checkout_never_stretches_a_hold_past_its_ceiling(self):
        from store.views import CreateCheckoutSessionView  # noqa: F401 - the route exists
        unit = holds.hold(product=self.new128, branch=self.branch, session_key='carrito-a')
        started = timezone.now() - timedelta(minutes=50)
        StockUnit.objects.filter(pk=unit.pk).update(hold_started_at=started)
        ceiling = started + timedelta(minutes=holds.hold_minutes() + holds.order_hold_minutes())
        for _ in range(2):          # the second checkout of the same cart cannot push it further
            Order.objects.filter(cart_session_key='carrito-a').update(status=Order.Status.FAILED)
            items = list(CartItem.objects.filter(session_key='carrito-a').select_related('product'))
            lines = checkout.lines_from_cart(self.company, self.branch, 'carrito-a', items)
            pricing = checkout.price_checkout(self.company, D('3000.00'))
            checkout.create_pending_order(
                company=self.company, branch=self.branch, lines=lines, pricing=pricing,
                details=checkout.CustomerDetails(
                    name='Ana', email='ana@example.com', phone='987654321', document_type='DNI',
                    document_number='12345678', delivery_method='pickup', receipt_type='boleta',
                    accepted_terms=True, accepted_warranty_policy=True),
                cart_session_key='carrito-a',
            )
            unit.refresh_from_db()
            self.assertLessEqual(unit.hold_until, ceiling)
            self.assertEqual(unit.hold_started_at, started)
        # A fresh hold gets the full order time.
        fresh = holds.hold(product=self.new128, branch=self.branch, session_key='carrito-b')
        self.assertIsNotNone(fresh.hold_started_at)
        self.assertGreater(fresh.hold_until, timezone.now() + timedelta(minutes=holds.hold_minutes() - 1))


@override_settings(DEVICE_WEB_HOLD_MAX_SHARE_PERCENT=100)
class CartLimitRaceTest(TransactionTestCase):
    """Two adds of one cart at the same instant, for two products: the limit still holds."""

    def race(self, shop, calls):
        barrier, outcomes = Barrier(len(calls)), []

        def run(kwargs):
            try:
                barrier.wait(timeout=5)
                holds.hold(branch=shop.branch, **kwargs)
                outcomes.append('held')
            except holds.HoldError as exc:
                outcomes.append(exc.code)
            finally:
                connection.close()

        threads = [Thread(target=run, args=(kwargs,)) for kwargs in calls]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        return sorted(outcomes)

    def test_the_per_cart_and_per_origin_limits_are_counted_under_a_lock(self):
        shop = DeviceShop()
        shop.build('dc2-limite')
        shop.receive(shop.new128, 6)
        shop.receive(shop.new256, 6)
        for _ in range(2):
            holds.hold(product=shop.new128, branch=shop.branch, session_key='c', origin='o')
        new128, new256 = (Product.objects.get(pk=p.pk) for p in (shop.new128, shop.new256))
        self.assertEqual(self.race(shop, [
            {'product': new128, 'session_key': 'c'}, {'product': new256, 'session_key': 'c'},
        ]), ['held', 'too_many'])
        self.assertEqual(StockUnit.objects.filter(status='reserved', hold_key=holds.key_of('c')).count(), 3)
        # The same for one origin through two other carts.
        self.assertEqual(self.race(shop, [
            {'product': new128, 'session_key': 'd', 'origin': 'p'},
            {'product': new256, 'session_key': 'e', 'origin': 'p'},
            {'product': new256, 'session_key': 'f', 'origin': 'p'},
            {'product': new128, 'session_key': 'g', 'origin': 'p'},
        ]), ['held', 'held', 'held', 'too_many'])


@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'], DEVICE_HOLD_MAX_PER_ORIGIN=100,
                   DEVICE_HOLD_MAX_PER_CART=100, DEVICE_WEB_HOLD_MAX_SHARE_PERCENT=100)
class DeviceHoldThrottleTest(DeviceShop, TestCase):
    """Adding a by-unit product has its own, stricter budget per address."""

    def setUp(self):
        self.build()
        self.receive(self.new128, 12)
        self.accessory = _prod(self.company, 'Funda', 'funda-dc2', price='50.00', inventory=50)
        self.public = _client(host=self.host)

    def test_ten_device_adds_a_minute_per_address(self):
        codes = [self.public.post('/api/cart/add/', {'session_key': f's{n}', 'product': self.new128.pk},
                                  format='json', REMOTE_ADDR='203.0.113.9').status_code for n in range(11)]
        self.assertEqual(codes, [200] * 10 + [429])
        # Another address, and products that are not held, are not counted by it.
        self.assertEqual(self.public.post('/api/cart/add/', {'session_key': 'x', 'product': self.new128.pk},
                                          format='json', REMOTE_ADDR='198.51.100.9').status_code, 200)
        self.assertEqual(self.public.post('/api/cart/add/', {'session_key': 's1', 'product': self.accessory.pk},
                                          format='json', REMOTE_ADDR='203.0.113.9').status_code, 200)


class SupersededOrderTest(SaleShop, TestCase):
    """One cart, two checkouts: the device is the LATER order's, and is paid for once."""

    def setUp(self):
        super().setUp()
        holds.hold(product=self.used128, branch=self.branch, session_key='carrito-a',
                   unit_public_id=str(self.used_a.public_id))
        self.first = self.checkout()
        self.second = self.checkout()

    def test_the_first_order_cannot_open_a_payment_nor_sell_the_device(self):
        self.used_a.refresh_from_db()
        self.assertEqual(self.used_a.hold_order_id, self.second.pk)
        with self.assertRaises(checkout.CheckoutError) as caught:
            checkout.start_payment_attempt(self.first, credentials=None)
        self.assertEqual((caught.exception.code, caught.exception.status_code), ('order_superseded', 409))
        # A form of the first order that was already open is paid anyway: the device does not go.
        self.pay(self.first)
        self.first.refresh_from_db()
        self.assertIn(f'#{self.second.pk}', self.first.payment_error)
        self.used_a.refresh_from_db()
        self.assertEqual(self.used_a.status, 'reserved')
        self.assertFalse(StockMovement.objects.filter(order=self.first, movement_type='sale_exit').exists())
        # The later order sells it.
        self.pay(self.second)
        self.used_a.refresh_from_db()
        self.assertEqual((self.used_a.status, self.used_a.order_id), ('sold', self.second.pk))
        self.assertEqual(self.shelf(self.used128), self.available(self.used128))

    def test_a_later_order_that_failed_does_not_take_the_device_from_the_first(self):
        Order.objects.filter(pk=self.second.pk).update(status=Order.Status.FAILED)
        self.pay(self.first)
        self.used_a.refresh_from_db()
        self.assertEqual((self.used_a.status, self.used_a.order_id), ('sold', self.first.pk))


class OldProductFilesTest(DeviceShop, TestCase):
    """A product file that predates DC2 and happens to have «Modelo» or «Color» loads as it did."""

    def setUp(self):
        self.build()

    def preview(self, company, headers, rows):
        from store import import_services
        from store.test_cost_imports import upload
        return import_services.preview_products(company=company, actor=self.staff, filename='viejo.xlsx',
                                                upload=upload(headers, rows))

    def test_a_model_number_column_is_not_a_device_model(self):
        job = self.preview(self.company, ['Código', 'Nombre', 'Precio de venta', 'Modelo', 'Color'],
                           [['ACC-9', 'Funda transparente', '30', 'A2848', 'Negro']])
        [row] = job.rows.all()
        self.assertEqual(row.errors, [])
        self.assertNotIn('variant', row.normalized_data)
        notes = job.summary['format_notes']
        self.assertEqual(len([n for n in notes if 'Modelo' in n]), 1, notes)

    def test_a_company_without_device_models_ignores_the_columns(self):
        bare = _p3_company('dc2-sin-modelos', 'Sin modelos')
        staff, _ = _p2d_member(bare, 'dc2_sin_modelos', ['company.view', 'products.view', 'products.manage'])
        self.staff = staff
        job = self.preview(bare, ['Código', 'Nombre', 'Precio de venta', 'Modelo', 'Capacidad', 'Color', 'Línea'],
                           [['TEL-9', 'Teléfono', '900', 'Teléfono X', '128 GB', 'Negro', 'Nuevo']])
        [row] = job.rows.all()
        self.assertEqual(row.errors, [])
        self.assertNotIn('variant', row.normalized_data)


class RollbackCommandsTest(SaleShop, TestCase):
    """Before going back to the release before DC2: no web hold and no payable device order is left."""

    def setUp(self):
        super().setUp()
        holds.hold(product=self.used128, branch=self.branch, session_key='carrito-a',
                   unit_public_id=str(self.used_a.public_id))
        self.order = self.checkout()
        holds.hold(product=self.new128, branch=self.branch, session_key='carrito-b')
        units.reserve(self.used_b, reason='Cliente en tienda', actor=self.staff)
        self.plain = Order.objects.create(company=self.company, fulfillment_branch=self.branch,
                                          customer_email='x@e.com', total=D('10.00'),
                                          status=Order.Status.PENDING_PAYMENT)

    def call(self, *args):
        import io
        from django.core.management import call_command
        out = io.StringIO()
        call_command(*args, stdout=out)
        return out.getvalue()

    def test_every_web_hold_goes_back_audited_and_staff_reservations_stay(self):
        from store.models import AdminAuditLog
        self.assertIn('devueltos al estante: 0', self.call('release_expired_unit_holds'))
        self.assertIn('devueltos al estante: 2', self.call('release_expired_unit_holds', '--all-web'))
        self.assertEqual(list(StockUnit.objects.filter(status='reserved').values_list('pk', flat=True)),
                         [self.used_b.pk])
        self.assertEqual(AdminAuditLog.objects.filter(action='stock_unit_web_hold_released').count(), 2)
        for product in (self.new128, self.used128):
            self.assertEqual(self.shelf(product), self.available(product))

    def test_pending_orders_with_device_lines_are_listed_then_cancelled(self):
        from store.models import AdminAuditLog
        listed = self.call('cancel_pending_unit_orders')
        self.assertIn(f'#{self.order.pk}', listed)
        self.assertNotIn(f'#{self.plain.pk}', listed)
        self.assertIn('Simulación', listed)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)       # dry run by default
        self.call('cancel_pending_unit_orders', '--apply')
        self.order.refresh_from_db()
        self.plain.refresh_from_db()
        self.assertEqual((self.order.status, self.plain.status),
                         (Order.Status.CANCELLED, Order.Status.PENDING_PAYMENT))
        self.assertTrue(AdminAuditLog.objects.filter(action='order_cancelled_before_rollback',
                                                     target_id=str(self.order.pk)).exists())
        # Its device is no longer kept for it: the buyer's cart may drop it.
        self.assertEqual(holds.release(company=self.company, session_key='carrito-a'), 1)


class TillReleaseTest(SaleShop, TestCase):
    """The till may take back an abandoned web hold — audited — but not one a buyer is paying for."""

    def release(self, unit):
        return _client(self.staff).post(f'/api/admin/inventory/units/{unit.pk}/release/', {}, format='json')

    def test_a_cart_hold_is_released_by_staff_with_its_trace(self):
        from store.models import AdminAuditLog
        holds.hold(product=self.used128, branch=self.branch, session_key='carrito-a',
                   unit_public_id=str(self.used_a.public_id))
        self.assertEqual(self.release(self.used_a).status_code, 200)
        self.used_a.refresh_from_db()
        self.assertEqual((self.used_a.status, self.used_a.hold_key, self.used_a.hold_origin), ('available', '', ''))
        log = AdminAuditLog.objects.filter(action='stock_unit_released', target_id=str(self.used_a.pk)).get()
        self.assertEqual(log.actor_id, self.staff.pk)
        entry = StockMovement.objects.filter(product=self.used128, movement_type='manual_entry').latest('pk')
        self.assertEqual(entry.metadata.get('web_hold'), True)

    def test_a_hold_whose_order_may_still_be_paid_is_not_taken_from_the_buyer(self):
        holds.hold(product=self.used128, branch=self.branch, session_key='carrito-a',
                   unit_public_id=str(self.used_a.public_id))
        order = self.checkout()
        refused = self.release(self.used_a)
        self.assertEqual(refused.status_code, 400)
        self.assertIn('pagando', refused.json()['detail'])
        self.used_a.refresh_from_db()
        self.assertEqual(self.used_a.status, 'reserved')
        # Once that order cannot be paid any more, the till may take it.
        Order.objects.filter(pk=order.pk).update(status=Order.Status.FAILED)
        self.assertEqual(self.release(self.used_a).status_code, 200)
