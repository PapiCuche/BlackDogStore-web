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
            total=product.price, status=Order.Status.PAID, paid=True, paid_at=timezone.now())
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
        self.assertEqual(row['history'], {'sold': 0, 'units': 0, 'reserved': 0})
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
