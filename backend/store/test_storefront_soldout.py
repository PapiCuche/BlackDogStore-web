"""
SUPPLIER-DEVICE-FLOW-02 · C — PUBLICADO NO ES DISPONIBLE (owner, 2026-10-20).

Lo que estas pruebas fijan, en la tienda pública:

  * hay cuatro estados y sólo cuatro: stock propio, bajo pedido (una oferta
    vigente, con saldo, del modo «stock o pedido»), AGOTADO y no publicado;
  * lo agotado SIGUE en el catálogo: el modelo en la lista (también un modelo
    sólo de seminuevos sin equipos), la variante en el modelo con su precio y
    sus fotos, el producto en la lista y en la búsqueda — y nada se puede
    apartar ni pagar;
  * lo no publicado (o inactivo) no existe para el público;
  * una oferta vencida, agotada o inactiva nunca cuenta como disponible;
  * el stock propio va primero: con estante, la web no vende del proveedor
    salvo que la empresa lo permita;
  * nada de lo anterior expone el costo, el proveedor, el IMEI ni la serie.
"""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from store import checkout_services as checkout
from store import device_catalog
from store import stock_unit_services as units
from store import supply_services as supply
from store.models import CartItem, Product, ProductImage, StockUnit
from store.test_device_commerce import DeviceShop, _client, assert_no_amount
from store.tests import _prod

#: The keys a model card of the public list carries. Nothing else leaves.
LIST_CARD_KEYS = {'slug', 'name', 'image_url', 'category', 'from_price', 'has_new', 'has_used', 'sold_out'}

# `"availability": "supplier"` is the public word for «on order»; a supplier's KEY or name is not.
FORBIDDEN_WORDS = ('"cost"', 'agreed_cost', '"supplier":', 'supplier_', 'proveedor', 'serial', 'imei"', 'confirmed_quantity',
                   'committed_quantity', 'Importadora')


def today():
    return timezone.localdate()


def _rows(body):
    return body['results'] if isinstance(body, dict) else body


class SoldOutShop(DeviceShop):
    def setUp(self):
        self.build('soldout-tienda')
        self.public = _client(host=self.host)
        self.supplier = supply.create_supplier(company=self.company, name='Importadora')

    def detail(self, model=None):
        res = self.public.get(f'/api/catalog/models/{(model or self.model).slug}/')
        self.assertEqual(res.status_code, 200, res.content)
        return res

    def listing(self, query=''):
        res = self.public.get(f'/api/catalog/models/{query}')
        self.assertEqual(res.status_code, 200, res.content)
        return res

    def card(self, query='', model=None):
        rows = [r for r in self.listing(query).json()['results'] if r['slug'] == (model or self.model).slug]
        return rows[0] if rows else None

    def variant_row(self, product, body=None):
        body = body or self.detail().json()
        return next((v for v in body['variants'] if v['product_id'] == product.pk), None)

    def offer(self, product, **extra):
        values = dict(agreed_cost='2800.00', commercial_price='3400.00', confirmed_quantity=2,
                      valid_from=today() - timedelta(days=1), valid_until=today() + timedelta(days=7),
                      lead_time_days=10)
        values.update(extra)
        Product.objects.filter(pk=product.pk).update(sale_mode=Product.SALE_MODE_STOCK_OR_ORDER)
        product.refresh_from_db()
        return supply.create_offer(company=self.company, supplier_id=self.supplier.pk, product_id=product.pk,
                                   actor=self.staff, may_view_costs=True, **values)

    def used_only_model(self, name='Teléfono Viejo'):
        model = device_catalog.create_model(company=self.company, name=name, image_url='/api/storefront/images/viejo')
        product = _prod(self.company, f'{name} 128 GB Azul usado', f'{model.slug}-usado', price='1.00', inventory=0)
        Product.objects.filter(pk=product.pk).update(is_serialized=True, requires_imei=True)
        product = device_catalog.set_variant(product, model_id=model.pk, condition_line='used',
                                             option_ids=[self.gb128.pk, self.blue.pk])
        return model, product


@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class SoldOutVariantTest(SoldOutShop, TestCase):
    """A published variant with nothing to sell is shown, with its price and photos, as AGOTADO."""

    def setUp(self):
        super().setUp()
        self.receive(self.new128, 1, cost='2500.00')
        ProductImage.objects.create(company=self.company, product=self.new256,
                                    image_url='/api/storefront/images/tx-256', is_primary=True)

    def test_the_sold_out_variant_keeps_price_and_photos_and_says_none(self):
        body = self.detail().json()
        row = self.variant_row(self.new256, body)
        self.assertIsNotNone(row, 'a published sold-out variant stays on the model page')
        self.assertEqual((row['available'], row['availability'], row['on_order']), (False, 'none', None))
        self.assertEqual(row['price'], '3500.00')
        self.assertEqual([image['url'] for image in row['images']], ['/api/storefront/images/tx-256'])
        self.assertFalse(body['sold_out'], 'the model still sells its 128 GB')

    def test_a_model_with_every_variant_sold_out_stays_listed_as_sold_out(self):
        StockUnit.objects.filter(product=self.new128).update(status='sold')
        from store.models import BranchStock
        BranchStock.objects.filter(product=self.new128).update(quantity=0)
        card = self.card()
        self.assertIsNotNone(card, 'published does not mean available, and sold out does not mean hidden')
        self.assertTrue(card['sold_out'])
        self.assertEqual(card['from_price'], '3000.00', 'the last configured price')
        self.assertEqual(set(card), LIST_CARD_KEYS)
        body = self.detail().json()
        self.assertTrue(body['sold_out'])
        self.assertEqual({v['availability'] for v in body['variants']}, {'none'})

    def test_a_model_with_something_to_sell_is_not_sold_out(self):
        card = self.card()
        self.assertFalse(card['sold_out'])
        self.assertEqual(set(card), LIST_CARD_KEYS)

    def test_the_sold_out_variant_cannot_be_held(self):
        res = self.public.post('/api/cart/add/', {'session_key': 's1', 'product': self.new256.pk}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'sold_out'), res.content)
        self.assertFalse(CartItem.objects.filter(session_key='s1').exists())
        self.assertFalse(StockUnit.objects.filter(product=self.new256).exists(), 'no unit is invented')
        on_order = self.public.post('/api/cart/add/', {'session_key': 's1', 'product': self.new256.pk,
                                                       'on_order': True}, format='json')
        self.assertEqual((on_order.status_code, on_order.json()['code']), (409, 'not_on_order'))
        self.assertFalse(CartItem.objects.filter(session_key='s1').exists())

    def test_unpublished_or_inactive_variants_do_not_exist_for_the_public(self):
        Product.objects.filter(pk=self.new256.pk).update(is_published_online=False)
        self.assertIsNone(self.variant_row(self.new256))
        Product.objects.filter(pk=self.new256.pk).update(is_published_online=True, is_active=False)
        self.assertIsNone(self.variant_row(self.new256))

    def test_an_unpublished_model_is_hidden_even_with_stock(self):
        type(self.model).objects.filter(pk=self.model.pk).update(is_published_online=False)
        self.assertIsNone(self.card())
        self.assertEqual(self.public.get(f'/api/catalog/models/{self.model.slug}/').status_code, 404)


@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class UsedOnlyModelTest(SoldOutShop, TestCase):
    """A published model sold only as used devices, with none on the shelf: listed as AGOTADO, never a unit."""

    def setUp(self):
        super().setUp()
        self.old, self.old_used = self.used_only_model()

    def test_it_stays_listed_under_its_line_as_sold_out(self):
        card = self.card(model=self.old)
        self.assertIsNotNone(card)
        self.assertEqual((card['has_new'], card['has_used'], card['sold_out']), (False, True, True))
        self.assertEqual(card['image_url'], '/api/storefront/images/viejo')
        self.assertIsNone(card['from_price'], 'a used variant has no price of its own')
        self.assertIsNotNone(self.card('?line=used', model=self.old))
        self.assertIsNone(self.card('?line=new', model=self.old))

    def test_a_device_without_a_price_is_still_not_offered(self):
        [unit] = self.receive(self.old_used, 1, cost='900.00')
        card = self.card(model=self.old)
        self.assertTrue(card['sold_out'])
        body = self.detail(self.old).json()
        self.assertEqual((body['used_units'], body['variants'], body['sold_out']), ([], [], True))
        text = self.detail(self.old).content.decode()
        self.assertNotIn(unit.imei, text)
        self.assertNotIn(unit.serial_number, text)
        assert_no_amount(self, body, '900')
        held = self.public.post('/api/cart/add/', {'session_key': 's2', 'product': self.old_used.pk}, format='json')
        self.assertEqual((held.status_code, held.json()['code']), (400, 'unit_required'), held.content)
        named = self.public.post('/api/cart/add/', {'session_key': 's2', 'product': self.old_used.pk,
                                                    'unit': str(unit.public_id)}, format='json')
        self.assertEqual((named.status_code, named.json()['code']), (404, 'not_found'), named.content)
        self.assertFalse(CartItem.objects.filter(session_key='s2').exists())
        self.assertFalse(StockUnit.objects.filter(pk=unit.pk, status='reserved').exists())

    def test_with_a_priced_device_it_is_offered_again(self):
        [unit] = self.receive(self.old_used, 1, cost='900.00')
        units.update_unit(unit, sale_price='1500.00')
        card = self.card(model=self.old)
        self.assertEqual((card['has_used'], card['sold_out'], card['from_price']), (True, False, '1500.00'))

    def test_a_model_with_new_stock_and_no_used_devices_keeps_its_line_tags(self):
        self.receive(self.new128, 1)
        card = self.card()
        self.assertEqual((card['has_new'], card['has_used'], card['sold_out']), (True, False, False))


@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class OnOrderNeverFromAnExpiredOfferTest(SoldOutShop, TestCase):
    """Only a valid, active, confirmed offer with balance sells on order; anything else is AGOTADO."""

    def assert_sold_out(self, product):
        row = self.variant_row(product)
        self.assertEqual((row['available'], row['availability'], row['on_order']), (False, 'none', None))
        listed = _rows(self.public.get(f'/api/products/?slug={product.slug}').json())
        self.assertEqual((listed[0]['inventory'], listed[0]['on_order']), (0, None))
        self.assertTrue(self.card()['sold_out'])

    def test_a_valid_offer_sells_on_order_with_lead_time_and_price_only(self):
        self.offer(self.new256)
        res = self.detail()
        row = self.variant_row(self.new256, res.json())
        self.assertEqual((row['available'], row['availability']), (True, 'supplier'))
        self.assertEqual(row['on_order'], {'lead_time_days': 10, 'price': '3400.00'})
        self.assertFalse(self.card()['sold_out'])
        assert_no_amount(self, res.json(), '2800')
        text = res.content.decode()
        for word in FORBIDDEN_WORDS:
            self.assertNotIn(word, text)
        listed = self.public.get('/api/catalog/models/')
        assert_no_amount(self, listed.json(), '2800')
        for word in FORBIDDEN_WORDS:
            self.assertNotIn(word, listed.content.decode())

    def test_an_expired_offer_is_never_available(self):
        self.offer(self.new256, valid_from=today() - timedelta(days=10), valid_until=today() - timedelta(days=1))
        self.assert_sold_out(self.new256)

    def test_an_offer_not_yet_valid_is_not_available(self):
        self.offer(self.new256, valid_from=today() + timedelta(days=1), valid_until=today() + timedelta(days=9))
        self.assert_sold_out(self.new256)

    def test_an_exhausted_or_inactive_offer_is_not_available(self):
        made = self.offer(self.new256)
        type(made).objects.filter(pk=made.pk).update(committed_quantity=2)
        self.assert_sold_out(self.new256)
        type(made).objects.filter(pk=made.pk).update(committed_quantity=0, is_active=False)
        self.assert_sold_out(self.new256)

    def test_an_offer_on_a_stock_only_product_is_not_available(self):
        self.offer(self.new256)
        Product.objects.filter(pk=self.new256.pk).update(sale_mode=Product.SALE_MODE_STOCK)
        self.assert_sold_out(self.new256)

    def test_the_cart_refuses_an_expired_offer(self):
        self.offer(self.new256, valid_from=today() - timedelta(days=10), valid_until=today() - timedelta(days=1))
        res = self.public.post('/api/cart/add/', {'session_key': 's3', 'product': self.new256.pk, 'on_order': True},
                               format='json')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'not_on_order'))


@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class OwnStockFirstTest(SoldOutShop, TestCase):
    """With a device on the shelf the shop sells its own; the supplier only where the company allows it."""

    def setUp(self):
        super().setUp()
        self.receive(self.new256, 1, cost='2500.00')
        self.offer(self.new256)

    def test_own_stock_wins_over_a_valid_offer(self):
        row = self.variant_row(self.new256)
        self.assertEqual((row['available'], row['availability']), (True, 'own_stock'))
        self.assertNotIn('over_stock', row['on_order'] or {})
        res = self.public.post('/api/cart/add/', {'session_key': 's4', 'product': self.new256.pk, 'on_order': True},
                               format='json')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'stock_available'), res.content)
        held = self.public.post('/api/cart/add/', {'session_key': 's4', 'product': self.new256.pk}, format='json')
        self.assertEqual(held.status_code, 200, held.content)
        self.assertEqual(CartItem.objects.get(session_key='s4').supply_origin, CartItem.SUPPLY_OWN)


@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class SoldOutProductListTest(SoldOutShop, TestCase):
    """Plain products: a sold-out one stays in the list and the search, says so, and is not paid for."""

    def setUp(self):
        super().setUp()
        self.cable = _prod(self.company, 'Cable USB-C', 'soldout-cable', price='59.00', inventory=0)
        self.hidden = _prod(self.company, 'Cable oculto', 'soldout-oculto', price='59.00', inventory=4)
        Product.objects.filter(pk=self.hidden.pk).update(is_published_online=False)

    def slugs(self, query=''):
        return {p['slug'] for p in _rows(self.public.get(f'/api/products/{query}').json())}

    def test_sold_out_products_stay_listed_and_unpublished_ones_do_not(self):
        listed = {p['slug']: p for p in _rows(self.public.get('/api/products/').json())}
        self.assertIn(self.cable.slug, listed)
        self.assertEqual((listed[self.cable.slug]['inventory'], listed[self.cable.slug]['price']), (0, '59.00'))
        self.assertNotIn(self.hidden.slug, listed)
        self.assertNotIn(self.used128.slug, listed, 'used units are sold on the model page')
        self.assertNotIn(self.cable.slug, self.slugs('?in_stock=true'))

    def test_search_finds_sold_out_products(self):
        self.assertIn(self.cable.slug, self.slugs('?search=usb-c'))
        self.assertNotIn(self.hidden.slug, self.slugs('?search=cable'))

    def test_search_matches_the_published_models_name(self):
        Product.objects.filter(pk=self.new256.pk).update(name='Edición 256 GB Negro')
        self.assertIn(self.new256.slug, self.slugs('?search=teléfono x'))
        type(self.model).objects.filter(pk=self.model.pk).update(is_published_online=False)
        self.assertNotIn(self.new256.slug, self.slugs('?search=teléfono x'),
                         'an unpublished model name is not a search key')

    def test_the_cart_refuses_a_sold_out_product(self):
        res = self.public.post('/api/cart/add/', {'session_key': 's5', 'product': self.cable.pk, 'quantity': 1},
                               format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertFalse(CartItem.objects.filter(session_key='s5').exists())

    def test_a_line_that_sold_out_in_the_cart_says_so_and_the_checkout_refuses_it(self):
        from store import inventory_services as inventory
        from store.models import StockMovement
        inventory.create_stock_movement(branch=self.branch, product_id=self.cable.pk,
                                        movement_type=StockMovement.PURCHASE_ENTRY, quantity=1, reason='Compra')
        res = self.public.post('/api/cart/add/', {'session_key': 's6', 'product': self.cable.pk, 'quantity': 1},
                               format='json')
        self.assertEqual(res.status_code, 200, res.content)
        inventory.create_stock_movement(branch=self.branch, product_id=self.cable.pk,
                                        movement_type=StockMovement.MANUAL_EXIT, quantity=1, reason="Merma")
        [line] = _rows(self.public.get('/api/cart/?session_key=s6').json())
        self.assertEqual((line['available'], line['product']['inventory'], line['supply']), (True, 0, {'origin': 'own'}))
        items = list(CartItem.objects.filter(session_key='s6').select_related('product'))
        lines = checkout.lines_from_cart(self.company, self.branch, 's6', items)
        with self.assertRaises(checkout.CheckoutError):
            checkout.validate_lines_and_subtotal(self.branch, lines)
