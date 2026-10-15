"""
DEVICE-COMMERCE-01 · DC1 — el margen comercial del resumen de ventas.

    GET /api/admin/sales/margin/

`sales.analytics.view` Y `costs.view`. Las cifras salen de las instantáneas de
cada línea (costo y base sin IGV), nunca del catálogo de hoy.

Fixture con números conocidos (precios con IGV; base = precio / 1,18):

    Funda  118,00 (100,00 sin IGV) · Centro: 10 a 60 · Norte: 5 a 70
    Cable   59,00 ( 50,00 sin IGV) · Centro: 10 sin costo

    A  web  Centro  2 Funda            neto 200  costo 120
    B  POS  Norte   1 Funda            neto 100  costo  70
    C  web  Centro  1 Cable            neto  50  costo pendiente
    D  web  Centro  1 Funda REEMBOLSADO          (fuera de ventas)
    E  web  Centro  pendiente de pago            (fuera)
    F  web  Centro  cancelado sin pagar          (fuera)
    X  otra empresa, pagado                      (fuera)

    ventas netas 350 · brutas 413 · con costo 300 · costo 190
    margen 110 · 36,67 % · pendiente 1 línea / 50 · reembolsos 1 / 118
"""
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from store import inventory_services as inventory
from store.models import Order, OrderItem, StockMovement
from store.tax_services import breakdown_from_total
from store.tests import _c1_sale, _p2d_branch, _p2d_member, _p3_company, _prod

D = Decimal
URL = '/api/admin/sales/margin/'


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class MarginFixture(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('dc1-margen', 'Tienda Margen')
        self.other = _p3_company('dc1-margen-b', 'Otra Tienda')
        self.centro = self.company.default_inventory_branch
        self.norte = _p2d_branch(self.company, 'Norte')
        self.case = _prod(self.company, 'Funda', 'funda-margen', price='118.00', inventory=0)
        self.cable = _prod(self.company, 'Cable', 'cable-margen', price='59.00', inventory=0)
        self.entry(self.centro, self.case, 10, D('60'))
        self.entry(self.norte, self.case, 5, D('70'))
        self.entry(self.centro, self.cable, 10, None)

        self.web(self.centro, [(self.case, 2)])                          # A
        seller, _ = _p2d_member(self.company, 'dc1_margen_caja', ['company.view', 'sales.pos.use'])
        _c1_sale(actor=seller, company=self.company, branch=self.norte,  # B
                 items=[{'product': self.case.pk, 'quantity': 1}])
        self.web(self.centro, [(self.cable, 1)])                         # C
        refunded = self.web(self.centro, [(self.case, 1)])               # D
        Order.objects.filter(pk=refunded.pk).update(status=Order.Status.REFUNDED)
        self.web(self.centro, [(self.case, 1)], pay=False)               # E
        cancelled = self.web(self.centro, [(self.case, 1)], pay=False)   # F
        Order.objects.filter(pk=cancelled.pk).update(status=Order.Status.CANCELLED)

        foreign_product = _prod(self.other, 'Ajeno', 'ajeno-margen', price='1180.00', inventory=0)
        foreign_branch = self.other.default_inventory_branch
        self.entry(foreign_branch, foreign_product, 3, D('1'))
        self.web(foreign_branch, [(foreign_product, 1)], company=self.other)   # X

        self.owner, _ = _p2d_member(self.company, 'dc1_duena',
                                    ['company.view', 'sales.analytics.view', 'costs.view'])

    def entry(self, branch, product, quantity, cost):
        inventory.create_stock_movement(
            branch=branch, product_id=product.pk, movement_type=StockMovement.PURCHASE_ENTRY,
            quantity=quantity, reason='Compra', unit_cost=cost,
        )

    def web(self, branch, lines, *, pay=True, company=None):
        subtotal = sum((product.price * quantity for product, quantity in lines), D('0.00'))
        tax = breakdown_from_total(total=subtotal, subtotal=subtotal)
        order = Order.objects.create(
            company=company or self.company, fulfillment_branch=branch, customer_email='x@example.com',
            total=subtotal, subtotal_amount=tax.subtotal, taxable_amount=tax.taxable_amount,
            tax_amount=tax.tax_amount, tax_rate=tax.tax_rate, status=Order.Status.PENDING_PAYMENT,
        )
        for product, quantity in lines:
            OrderItem.objects.create(order=order, product=product, quantity=quantity, price=product.price)
        if pay:
            inventory.record_sale_stock_movements(order)
            Order.objects.filter(pk=order.pk).update(
                status=Order.Status.PAID, paid=True, paid_at=timezone.now())
        return order

    def get(self, client=None, **query):
        cache.clear()
        text = '&'.join(f'{k}={v}' for k, v in query.items())
        return (client or _client(self.owner)).get(URL + (f'?{text}' if text else ''))


class MarginFiguresTest(MarginFixture):
    def test_the_figures_of_the_fixture(self):
        res = self.get(branch='all')
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body['window_days'], 30)
        self.assertEqual(body['sales'], {'orders': 3, 'gross': '413.00', 'net': '350.00'})
        self.assertEqual(body['collected'], {'orders': 3, 'amount': '413.00'})
        self.assertEqual(body['net_sales_with_cost'], '300.00')
        self.assertEqual(body['cost_of_goods_sold'], '190.00')
        self.assertEqual(body['margin'], '110.00')
        self.assertEqual(body['margin_percent'], '36.67')
        self.assertEqual(body['pending_cost'], {'lines': 1, 'orders': 1, 'amount': '50.00'})
        self.assertEqual(body['reference_cost_lines'], 0)
        self.assertEqual(body['refunds'], {'orders': 1, 'amount': '118.00', 'net': '100.00'})
        self.assertNotIn('utilidad', res.content.decode().lower())

    def test_breakdowns_by_product_branch_and_channel(self):
        body = self.get(branch='all').json()
        products = {row['name']: row for row in body['by_product']}
        self.assertEqual(products['Funda'], {
            'key': self.case.pk, 'name': 'Funda', 'units': 3, 'net_sales': '300.00', 'cost': '190.00',
            'margin': '110.00', 'margin_percent': '36.67', 'pending_lines': 0, 'net_estimated': False,
        })
        self.assertEqual(products['Cable']['pending_lines'], 1)
        self.assertEqual(products['Cable']['margin_percent'], None)

        branches = {row['name']: row for row in body['by_branch']}
        self.assertEqual((branches[self.centro.name]['margin'], branches['Norte']['margin']),
                         ('80.00', '30.00'))
        channels = {row['key']: row for row in body['by_channel']}
        self.assertEqual((channels['online']['margin'], channels['pos']['margin']), ('80.00', '30.00'))
        self.assertEqual(channels['online']['pending_lines'], 1)

    def test_one_branch(self):
        body = self.get(branch=self.norte.pk).json()
        self.assertEqual(body['sales'], {'orders': 1, 'gross': '118.00', 'net': '100.00'})
        self.assertEqual(body['margin'], '30.00')
        self.assertEqual(body['pending_cost']['lines'], 0)
        # The refund was of the other branch (DC1 review: the filter is pinned).
        self.assertEqual(body['refunds'], {'orders': 0, 'amount': '0.00', 'net': '0.00'})
        self.assertEqual(self.get(branch=self.centro.pk).json()['refunds'], {'orders': 1, 'amount': '118.00', 'net': '100.00'})

    def test_a_line_from_before_dc1_counts_its_net_and_is_pending(self):
        """DC1 review — a line with no snapshot is not «0.00 net»: its net comes from the order, flagged pending."""
        charger = _prod(self.company, 'Cargador', 'cargador-margen', price='236.00', inventory=0)
        tax = breakdown_from_total(total=D('236.00'), subtotal=D('236.00'))
        old = Order.objects.create(
            company=self.company, fulfillment_branch=self.centro, customer_email='x@example.com',
            total=D('236.00'), subtotal_amount=tax.subtotal, taxable_amount=tax.taxable_amount,
            tax_amount=tax.tax_amount, tax_rate=tax.tax_rate,
            status=Order.Status.PAID, paid=True, paid_at=timezone.now(),
        )
        OrderItem.objects.create(order=old, product=charger, quantity=1, price=charger.price)
        body = self.get(branch='all').json()
        row = {r['name']: r for r in body['by_product']}['Cargador']
        self.assertEqual((row['net_sales'], row['cost'], row['margin'], row['pending_lines']),
                         ('200.00', '0.00', '0.00', 1))
        self.assertEqual(row['margin_percent'], None)
        self.assertTrue(row['net_estimated'])
        branches = {r['name']: r for r in body['by_branch']}
        self.assertEqual(branches[self.centro.name]['net_sales'], '450.00')    # 200 + 50 + 200
        self.assertEqual(body['pending_cost']['amount'], '250.00')

    def test_the_figures_do_not_move_when_the_catalogue_does(self):
        before = self.get(branch='all').json()
        from store.models import BranchStock, Product
        Product.objects.filter(pk=self.case.pk).update(price=D('999.00'), reference_cost=D('1.00'))
        Product.objects.filter(pk=self.cable.pk).update(reference_cost=D('5.00'))
        BranchStock.objects.update(average_cost=D('1.0000'))
        self.assertEqual(self.get(branch='all').json(), before)


class MarginAccessTest(MarginFixture):
    def test_analytics_without_costs_view_is_refused(self):
        user, _ = _p2d_member(self.company, 'dc1_analista', ['company.view', 'sales.analytics.view'])
        self.assertEqual(self.get(_client(user)).status_code, 403)

    def test_costs_view_without_analytics_is_refused(self):
        user, _ = _p2d_member(self.company, 'dc1_costos', ['company.view', 'costs.view'])
        self.assertEqual(self.get(_client(user)).status_code, 403)

    def test_a_legacy_admin_membership_sees_the_margin(self):
        from django.contrib.auth import get_user_model
        from store.models import Membership
        user = get_user_model().objects.create_user(username='dc1_legacy_admin', password='Pass123!')
        Membership.objects.create(user=user, company=self.company, role='admin')
        res = self.get(_client(user))
        self.assertEqual(res.status_code, 200, res.content)

    def test_one_answer_for_a_profile_only_admin(self):
        """The legacy bridge counts the same in every surface that asks (`user_may_view_costs`)."""
        from django.contrib.auth import get_user_model
        from store.cost_services import user_may_view_costs
        from store.models import UserProfile
        from store.tenancy import legacy_catalog_company

        admin = get_user_model().objects.create_user(username='dc1_perfil_admin', password='Pass123!')
        UserProfile.objects.update_or_create(user=admin, defaults={'role': UserProfile.ROLE_ADMIN})
        seller = get_user_model().objects.create_user(username='dc1_perfil_ventas', password='Pass123!')
        UserProfile.objects.update_or_create(user=seller, defaults={'role': UserProfile.ROLE_SALES})
        admin, seller = (get_user_model().objects.get(pk=u.pk) for u in (admin, seller))
        pilot = legacy_catalog_company(admin)
        self.assertIsNotNone(pilot)
        self.assertTrue(user_may_view_costs(admin, pilot))
        self.assertFalse(user_may_view_costs(seller, pilot))
        others = [c for c in (self.company, self.other) if c.pk != pilot.pk]
        self.assertFalse(any(user_may_view_costs(admin, company) for company in others))

    def test_anonymous(self):
        self.assertEqual(APIClient().get(URL).status_code, 401)

    def test_another_company_sees_only_its_own(self):
        user, _ = _p2d_member(self.other, 'dc1_otra', ['company.view', 'sales.analytics.view', 'costs.view'])
        body = self.get(_client(user), branch='all').json()
        self.assertEqual(body['sales'], {'orders': 1, 'gross': '1180.00', 'net': '1000.00'})
        self.assertEqual(body['cost_of_goods_sold'], '1.00')
        self.assertEqual({row['name'] for row in body['by_product']}, {'Ajeno'})

    def test_a_branch_out_of_reach_narrows_to_nothing(self):
        user, _ = _p2d_member(self.company, 'dc1_norte',
                              ['company.view', 'sales.analytics.view', 'costs.view'], branches=[self.norte])
        body = self.get(_client(user), branch='all').json()
        self.assertEqual(body['sales']['orders'], 1)
        self.assertEqual({row['name'] for row in body['by_branch']}, {'Norte'})
