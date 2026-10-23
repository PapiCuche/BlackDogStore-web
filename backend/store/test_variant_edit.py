"""
CATALOG-VARIANTS-UX · editing a variant that already exists.

A variant is a Product placed in a model with a line (new / used) and one
option per attribute. Editing it never creates a Product. What it may change
depends on its history:

  * no sale, no device, no reservation — anyone who manages products;
  * sold, or with devices received — its commercial identity (model, options)
    changes only by a superior with a reason, audited: the paper of past sales
    names the product, and the product's options are what it says it was;
  * a device on hold for a buyer or set aside — not now: the hold promised
    THAT variant;
  * the line (new ↔ used) with devices of its own — never: a device's
    condition is physical, not a label.
"""
from decimal import Decimal as D

from django.test import TestCase
from django.utils import timezone

from store import device_catalog
from store.models import AdminAuditLog, Order, OrderItem, Product, ProductVariantOption, StockUnit
from store.test_device_commerce import DeviceShop, _client
from store.tests import _p2d_member

REASON = 'El color estaba mal cargado desde la compra'


class _Edit(DeviceShop, TestCase):
    def setUp(self):
        self.build('variante-edit')
        self.api = _client(self.staff)
        self.boss, _ = _p2d_member(self.company, 'variante_jefe', [
            'company.view', 'company.manage', 'products.view', 'products.manage'])
        self.boss_api = _client(self.boss)
        self.q = f'?company={self.company.pk}'

    def put(self, product, client=None, **body):
        body.setdefault('model_id', self.model.pk)
        body.setdefault('condition_line', product.condition_line)
        body.setdefault('option_ids', [o.pk for o in device_catalog.variant_options_of(product)])
        return (client or self.api).put(f'/api/admin/products/{product.pk}/variant/{self.q}', body, format='json')

    def sold(self, product):
        order = Order.objects.create(
            company=self.company, fulfillment_branch=self.branch, customer_email='c@example.com',
            total=product.price, status=Order.Status.PAID, paid=True, paid_at=timezone.now(),
            fulfillment_status=Order.FulfillmentStatus.DELIVERED)          # a past sale, handed over
        OrderItem.objects.create(order=order, product=product, quantity=1, price=product.price)
        return order

    def row(self, product):
        detail = self.api.get(f'/api/admin/catalog/models/{self.model.pk}/{self.q}').json()
        return next(v for v in detail['variants'] if v['product_id'] == product.pk)


class FreeEditTest(_Edit):
    def test_a_variant_without_history_changes_its_options_in_place(self):
        before = Product.objects.count()
        response = self.put(self.new128, option_ids=[self.gb128.pk, self.blue.pk])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Product.objects.count(), before)                    # no new Product
        self.assertEqual(sorted(o.pk for o in device_catalog.variant_options_of(self.new128)),
                         sorted([self.gb128.pk, self.blue.pk]))

    def test_the_combination_of_another_variant_is_refused(self):
        response = self.put(self.new128, option_ids=[self.gb256.pk, self.black.pk])
        self.assertEqual((response.status_code, response.json()['code']), (400, 'duplicate'))

    def test_the_model_row_says_what_the_variant_carries(self):
        row = self.row(self.new128)
        self.assertEqual(row['history'], {'sold': 0, 'units': 0, 'reserved': 0, 'stock': 0, 'moved': False,
                                          'open_orders': 0, 'conditions': []})
        self.assertEqual((row['identity_change'], row['line_change']), ('free', 'free'))
        self.assertIn('is_published_online', row)


class HistoryTest(_Edit):
    def test_a_sold_variant_needs_a_superior_and_a_reason(self):
        self.sold(self.new128)
        response = self.put(self.new128, option_ids=[self.gb128.pk, self.blue.pk])
        self.assertEqual((response.status_code, response.json()['code']), (403, 'history_needs_superior'))
        response = self.put(self.new128, client=self.boss_api, option_ids=[self.gb128.pk, self.blue.pk])
        self.assertEqual((response.status_code, response.json()['code']), (400, 'history_reason_required'))
        response = self.put(self.new128, client=self.boss_api, option_ids=[self.gb128.pk, self.blue.pk],
                            reason=REASON)
        self.assertEqual(response.status_code, 200, response.content)
        audit = AdminAuditLog.objects.filter(action='product_variant_set', target_id=str(self.new128.pk)).latest('pk')
        self.assertEqual((audit.metadata['reason'], audit.metadata['history']['sold']), (REASON, 1))
        self.assertEqual(self.row(self.new128)['identity_change'], 'superior')

    def test_devices_received_count_as_history(self):
        self.receive(self.new128, 2)
        self.assertEqual(self.row(self.new128)['history']['units'], 2)
        self.assertEqual(self.put(self.new128, option_ids=[self.gb128.pk, self.blue.pk]).status_code, 403)

    def test_a_reserved_device_stops_any_identity_change(self):
        unit, = self.receive(self.new128)
        StockUnit.objects.filter(pk=unit.pk).update(status=StockUnit.Status.RESERVED)
        response = self.put(self.new128, client=self.boss_api, option_ids=[self.gb128.pk, self.blue.pk],
                            reason=REASON)
        self.assertEqual((response.status_code, response.json()['code']), (409, 'variant_reserved'))
        self.assertEqual(self.row(self.new128)['identity_change'], 'blocked')
        self.assertEqual(sorted(o.pk for o in device_catalog.variant_options_of(self.new128)),
                         sorted([self.gb128.pk, self.black.pk]))

    def test_the_line_never_changes_with_devices_of_its_own(self):
        self.receive(self.used128)
        response = self.put(self.used128, client=self.boss_api, condition_line='new', reason=REASON)
        self.assertEqual((response.status_code, response.json()['code']), (409, 'line_change_with_units'))
        self.assertEqual(self.row(self.used128)['line_change'], 'blocked')

    def test_moving_a_sold_variant_to_another_model_is_the_same_procedure(self):
        self.sold(self.new256)
        other = device_catalog.create_model(company=self.company, name='Teléfono Y')
        self.assertEqual(self.put(self.new256, model_id=other.pk).status_code, 403)
        self.assertEqual(self.put(self.new256, client=self.boss_api, model_id=other.pk, reason=REASON).status_code, 200)
        self.assertEqual(Product.objects.get(pk=self.new256.pk).product_model_id, other.pk)

    def test_an_unchanged_identity_with_history_is_not_a_change(self):
        self.sold(self.new128)
        response = self.put(self.new128)                           # same model, line and options
        self.assertEqual(response.status_code, 200, response.content)

    def test_sales_keep_their_price_and_the_product(self):
        order = self.sold(self.new128)
        self.put(self.new128, client=self.boss_api, option_ids=[self.gb128.pk, self.blue.pk], reason=REASON)
        item = order.items.get()
        self.assertEqual((item.product_id, item.price), (self.new128.pk, D('3000.00')))


class TenancyTest(_Edit):
    def test_an_option_or_model_of_another_company_is_not_found(self):
        from store.test_device_commerce import _p3_company

        other = _p3_company('variante-ajena', 'Ajena')
        foreign_model = device_catalog.create_model(company=other, name='Ajeno')
        self.assertEqual(self.put(self.new128, model_id=foreign_model.pk).status_code, 404)
        self.assertTrue(ProductVariantOption.objects.filter(product=self.new128).exists())


class BypassAndRaceTest(_Edit):
    """Review: clearing then re-attaching must not skip the rules; stock and open orders count."""

    def clear(self, product, client=None, **body):
        return (client or self.api).put(f'/api/admin/products/{product.pk}/variant/{self.q}',
                                        {'model_id': None, **body}, format='json')

    def test_a_sold_variant_is_not_taken_out_of_its_model_by_a_manager(self):
        self.sold(self.new128)
        response = self.clear(self.new128)
        self.assertEqual((response.status_code, response.json()['code']), (403, 'history_needs_superior'))
        self.assertEqual(Product.objects.get(pk=self.new128.pk).product_model_id, self.model.pk)
        self.assertEqual(self.clear(self.new128, client=self.boss_api, reason=REASON).status_code, 200)

    def test_a_variant_with_devices_or_holds_is_not_taken_out_at_all(self):
        unit, = self.receive(self.used128)
        response = self.clear(self.used128, client=self.boss_api, reason=REASON)
        self.assertEqual((response.status_code, response.json()['code']), (409, 'variant_in_use'))
        StockUnit.objects.filter(pk=unit.pk).update(status=StockUnit.Status.RESERVED)
        self.assertEqual(self.clear(self.used128, client=self.boss_api, reason=REASON).status_code, 409)
        self.assertEqual(Product.objects.get(pk=self.used128.pk).condition_line, 'used')

    def test_attaching_a_product_with_devices_does_not_relabel_their_line(self):
        loose = Product.objects.create(company=self.company, name='Suelto', slug='suelto-x', price=D('10.00'))
        Product.objects.filter(pk=loose.pk).update(is_serialized=True, requires_imei=True)
        loose.refresh_from_db()
        self.receive(loose)
        response = self.put(loose, condition_line='used', option_ids=[self.gb256.pk, self.blue.pk])
        self.assertEqual((response.status_code, response.json()['code']), (409, 'line_change_with_units'))

    def test_stock_of_a_quantity_variant_is_history(self):
        from store.tests import _m7_stock

        Product.objects.filter(pk=self.new256.pk).update(is_serialized=False, requires_imei=False)
        _m7_stock(self.branch, self.new256, 4)
        self.assertEqual(self.row(self.new256)['identity_change'], 'superior')
        self.assertEqual(self.put(self.new256, option_ids=[self.gb256.pk, self.blue.pk]).status_code, 403)

    def test_a_paid_order_not_handed_over_asks_for_a_superior(self):
        order = self.sold(self.new128)
        Order.objects.filter(pk=order.pk).update(fulfillment_status='preparing')
        self.assertEqual(self.row(self.new128)['history']['open_orders'], 1)
        self.assertEqual(self.put(self.new128, option_ids=[self.gb128.pk, self.blue.pk]).status_code, 403)
        response = self.put(self.new128, client=self.boss_api, option_ids=[self.gb128.pk, self.blue.pk], reason=REASON)
        self.assertEqual(response.status_code, 200, response.content)

    def test_an_abandoned_checkout_does_not_freeze_the_variant(self):
        order = self.sold(self.new128)
        Order.objects.filter(pk=order.pk).update(status=Order.Status.PENDING_PAYMENT, paid=False,
                                                 fulfillment_status='pending')
        self.assertEqual(self.row(self.new128)['history']['open_orders'], 0)
        response = self.put(self.new128, client=self.boss_api, option_ids=[self.gb128.pk, self.blue.pk], reason=REASON)
        self.assertEqual(response.status_code, 200, response.content)

    def test_a_loose_product_with_used_devices_is_placed_in_the_used_line(self):
        loose = Product.objects.create(company=self.company, name='Suelto usado', slug='suelto-usado', price=D('10.00'))
        Product.objects.filter(pk=loose.pk).update(is_serialized=True, requires_imei=True)
        loose.refresh_from_db()
        self.receive(loose, condition='used')
        response = self.put(loose, condition_line='used', option_ids=[self.gb256.pk, self.blue.pk])
        self.assertEqual(response.status_code, 200, response.content)


class AtomicSaveTest(_Edit):
    """
    CATALOG-EDITOR-FIX-01 — «Guardar cambios» of a variant is ONE save: model, line, options, price and
    publication together or nothing. The editor used to send two requests, so a refused price could leave
    the options changed and the screen half right.
    """

    def test_options_price_and_publication_are_saved_together_on_the_same_product(self):
        before = Product.objects.count()
        response = self.put(self.new128, option_ids=[self.gb128.pk, self.blue.pk], price='3100.00',
                            is_published_online=False)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body['product_id'], body['price'], body['is_published_online']),
                         (self.new128.pk, '3100.00', False))
        self.new128.refresh_from_db()
        self.assertEqual(Product.objects.count(), before)
        self.assertEqual((self.new128.price, self.new128.is_published_online), (D('3100.00'), False))
        self.assertEqual(sorted(o.pk for o in device_catalog.variant_options_of(self.new128)),
                         sorted([self.gb128.pk, self.blue.pk]))
        audit = AdminAuditLog.objects.filter(action='product_updated', target_id=str(self.new128.pk)).last()
        self.assertEqual(audit.metadata['changed_fields']['price'], {'old': '3000.00', 'new': '3100.00'})

    def test_a_refused_price_saves_nothing(self):
        response = self.put(self.new128, option_ids=[self.gb128.pk, self.blue.pk], price='-5')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()['field'], 'price')
        self.new128.refresh_from_db()
        self.assertEqual(self.new128.price, D('3000.00'))
        self.assertEqual(sorted(o.pk for o in device_catalog.variant_options_of(self.new128)),
                         sorted([self.gb128.pk, self.black.pk]))                  # the options did not move

    def test_only_price_and_publication_with_the_identity_as_it_is(self):
        response = self.put(self.new128, price='2999.90')
        self.assertEqual(response.status_code, 200, response.content)
        self.new128.refresh_from_db()
        self.assertEqual(self.new128.price, D('2999.90'))

    def test_a_reserved_variant_keeps_its_identity_and_its_price_with_it(self):
        unit, = self.receive(self.new128)
        StockUnit.objects.filter(pk=unit.pk).update(status=StockUnit.Status.RESERVED)
        refused = self.put(self.new128, client=self.boss_api, option_ids=[self.gb128.pk, self.blue.pk],
                           reason=REASON, price='1.00')
        self.assertEqual((refused.status_code, refused.json()['code']), (409, 'variant_reserved'))
        self.new128.refresh_from_db()
        self.assertEqual(self.new128.price, D('3000.00'))                        # not half of a refused save
        self.assertEqual(self.put(self.new128, price='3050.00').status_code, 200)  # the price alone may change

    def test_without_products_manage_nothing_changes(self):
        from store.tests import _p2d_member as member

        viewer, _ = member(self.company, 'variante_lector', ['company.view', 'products.view'])
        response = self.put(self.new128, client=_client(viewer), price='1.00')
        self.assertEqual(response.status_code, 403)
        self.new128.refresh_from_db()
        self.assertEqual(self.new128.price, D('3000.00'))

    def test_the_shop_shows_the_edited_variant(self):
        from django.test import override_settings

        with override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver']):
            self._shop_shows_the_edit()

    def _shop_shows_the_edit(self):
        self.put(self.new128, option_ids=[self.gb128.pk, self.blue.pk], price='3100.00')
        body = _client(host=self.host).get(f'/api/catalog/models/{self.model.slug}/').json()
        row = next(v for v in body['variants'] if v['product_id'] == self.new128.pk)
        self.assertEqual(row['price'], '3100.00')
        self.assertEqual(sorted(row['options'].values()), sorted([self.gb128.pk, self.blue.pk]))
        others = [v for v in body['variants'] if v['product_id'] != self.new128.pk]
        self.assertTrue(all(v['price'] != '3100.00' for v in others))           # nobody else's price moved
