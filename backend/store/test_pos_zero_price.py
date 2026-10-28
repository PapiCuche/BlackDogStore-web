"""
INV-FAST-B (review of #196, P2-2) — the till does not sell what has no price.

INV-FAST-B1 creates variants at 0.00 until somebody prices them and B2 registers
units under them, so a variant can be active WITH stock and a price of zero. The
till prices a line at `Product.price`; without this guard it would sell a phone for
nothing. Preview, sale and the mobile till share `build_pos_sale`, so the guard
lives there. A replay of a sale that was already made answers before pricing.
"""
from rest_framework.test import APIClient

from store.models import BranchStock, Order, Product
from store.test_pos_reconcile import _web_login
from store.tests import Ip1PosBase

MESSAGE = 'Este producto no tiene precio: configúralo antes de venderlo'


class ZeroPriceAtTheTillTest(Ip1PosBase):
    def setUp(self):
        super().setUp()
        self.client = _web_login('ip1_caja')
        self.free = Product.objects.create(
            company=self.company, name='Sin precio', slug='sin-precio', price='0.00', inventory=0)
        BranchStock.objects.update_or_create(branch=self.branch_a, product=self.free, defaults={'quantity': 5})
        self.v1 = APIClient()
        self.v1.force_authenticate(self.staff)

    def items(self, *products):
        return [{'product': p.pk, 'quantity': 1} for p in products]

    def test_a_sale_of_a_product_without_price_is_refused_and_writes_nothing(self):
        before = (Order.objects.count(), BranchStock.objects.get(branch=self.branch_a, product=self.free).quantity)
        res = self.client.post('/api/admin/pos/sales/', self.sale_body(items=self.items(self.free)), format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn(MESSAGE, res.json()['detail'])
        self.assertEqual((Order.objects.count(),
                          BranchStock.objects.get(branch=self.branch_a, product=self.free).quantity), before)

    def test_one_such_line_refuses_the_whole_basket(self):
        res = self.client.post('/api/admin/pos/sales/',
                               self.sale_body(items=self.items(self.cable, self.free)), format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn('Sin precio', res.json()['detail'])
        self.assertEqual(Order.objects.count(), 0)

    def test_the_preview_refuses_it_too_so_the_line_cannot_be_added(self):
        res = self.client.post('/api/admin/pos/preview/', {
            'branch': self.branch_a.pk, 'items': self.items(self.free)}, format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn(MESSAGE, res.json()['detail'])

    def test_the_mobile_till_refuses_it_in_preview_and_sale(self):
        base = f'/api/v1/internal/{self.company.slug}/sales/pos/'
        body = {'branch': self.branch_a.pk, 'items': self.items(self.free)}
        self.assertEqual(self.v1.post(base + 'preview/', body, format='json').status_code, 400)
        sale = self.v1.post(base + 'sales/', {**self.sale_body(items=self.items(self.free))}, format='json')
        self.assertEqual(sale.status_code, 400, sale.content)
        self.assertEqual(Order.objects.count(), 0)

    def test_other_prices_are_unaffected(self):
        res = self.client.post('/api/admin/pos/sales/', self.sale_body(), format='json')
        self.assertIn(res.status_code, (200, 201), res.content)
        self.assertEqual(Order.objects.count(), 1)

    def test_a_discount_that_makes_the_total_zero_is_not_a_missing_price(self):
        res = self.client.post('/api/admin/pos/preview/', {
            'branch': self.branch_a.pk, 'items': self.items(self.cable)}, format='json')
        self.assertEqual(res.status_code, 200, res.content)

    def test_a_sale_already_made_is_replayed_even_if_the_price_was_cleared_since(self):
        first = self.client.post('/api/admin/pos/sales/', self.sale_body(), format='json')
        self.assertIn(first.status_code, (200, 201), first.content)
        Product.objects.filter(pk=self.cable.pk).update(price='0.00')
        again = self.client.post('/api/admin/pos/sales/', self.sale_body(), format='json')
        self.assertIn(again.status_code, (200, 201), again.content)
        self.assertEqual(again.json()['order_id'], first.json()['order_id'])
        self.assertEqual(Order.objects.count(), 1)
