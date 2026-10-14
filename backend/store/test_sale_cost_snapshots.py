"""
DEVICE-COMMERCE-01 · DC1 — lo que costó lo vendido, congelado en la línea.

Cuando una venta deja el estante (pago web confirmado, o la venta del punto de
venta), cada línea guarda:

  * `unit_cost` y `cost_source` — el promedio de la sucursal ('average'), el
    costo específico de los equipos ('unit'), el de referencia del catálogo
    ('reference') o nada ('unknown', «costo pendiente»);
  * `net_amount` — su parte de la base imponible del pedido, repartida de modo
    que las líneas SUMAN EXACTAMENTE `Order.taxable_amount`;
  * `discount_net` — su parte del descuento del pedido, sin IGV, proporcional;
  * `unit_price_net` — el precio unitario sin IGV antes del descuento.

Nada de eso cambia después: ni si el catálogo cambia el costo o el precio, ni
si el promedio se mueve. Las líneas anteriores a DC1 se quedan sin costo.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from store import inventory_services as inventory
from store import margin_services
from store import stock_unit_services as units
from store.models import BranchStock, Order, OrderItem, Product, StockMovement
from store.tax_services import breakdown_from_total
from store.test_stock_units import IMEI_A, IMEI_B
from store.tests import _c1_sale, _p2d_member, _p3_company, _prod

D = Decimal


class AllocationMathTest(TestCase):
    def test_largest_remainder_sums_exactly_and_breaks_ties_by_key(self):
        # 10.00 in thirds: 3.33 + 3.33 + 3.34? No — the extra cent goes to the
        # largest remainder, ties to the lowest key.
        self.assertEqual(
            margin_services.allocate(D('10.00'), [(1, D('1')), (2, D('1')), (3, D('1'))]),
            {1: D('3.34'), 2: D('3.33'), 3: D('3.33')},
        )

    def test_proportional_to_weights(self):
        self.assertEqual(
            margin_services.allocate(D('100.00'), [(7, D('300')), (3, D('100'))]),
            {7: D('75.00'), 3: D('25.00')},
        )

    def test_the_largest_fraction_wins_the_cent(self):
        # 1.00 split 1:2 → 0.333… and 0.666…; the second has the larger fraction.
        self.assertEqual(
            margin_services.allocate(D('1.00'), [(1, D('1')), (2, D('2'))]),
            {1: D('0.33'), 2: D('0.67')},
        )

    def test_nothing_to_share_or_no_weight(self):
        self.assertEqual(margin_services.allocate(D('0.00'), [(1, D('5'))]), {1: D('0.00')})
        self.assertEqual(
            margin_services.allocate(D('0.05'), [(2, D('0')), (1, D('0'))]),
            {1: D('0.05'), 2: D('0.00')},
        )


class _Shop(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('dc1-venta', 'Tienda Venta')
        self.branch = self.company.default_inventory_branch
        self.case = _prod(self.company, 'Funda', 'funda-venta', price='59.90', inventory=0)
        self.cable = _prod(self.company, 'Cable', 'cable-venta', price='25.00', inventory=0)
        self.charger = _prod(self.company, 'Cargador', 'cargador-venta', price='99.00', inventory=0)

    def stock(self, product, quantity, cost=None):
        inventory.create_stock_movement(
            branch=self.branch, product_id=product.pk, movement_type=StockMovement.PURCHASE_ENTRY,
            quantity=quantity, reason='Compra', unit_cost=cost,
        )

    def web_order(self, lines, *, discount=D('0.00')):
        """A storefront order with its frozen breakdown, as checkout leaves it."""
        subtotal = sum((product.price * quantity for product, quantity in lines), D('0.00'))
        total = subtotal - discount
        tax = breakdown_from_total(total=total, subtotal=subtotal, discount_amount=discount)
        order = Order.objects.create(
            company=self.company, fulfillment_branch=self.branch, customer_email='x@example.com',
            total=total, discount_amount=discount, subtotal_amount=tax.subtotal,
            taxable_amount=tax.taxable_amount, tax_amount=tax.tax_amount, tax_rate=tax.tax_rate,
            status=Order.Status.PENDING_PAYMENT,
        )
        for product, quantity in lines:
            OrderItem.objects.create(order=order, product=product, quantity=quantity, price=product.price)
        return order

    def pay(self, order):
        """What the payment confirmation does, in the same order."""
        inventory.record_sale_stock_movements(order)
        Order.objects.filter(pk=order.pk).update(status=Order.Status.PAID, paid=True, paid_at=timezone.now())
        order.refresh_from_db()
        return order

    def line(self, order, product):
        return OrderItem.objects.get(order=order, product=product)


class WebPaymentSnapshotTest(_Shop):
    def test_a_pending_order_has_no_snapshot_yet(self):
        self.stock(self.case, 5, D('20'))
        order = self.web_order([(self.case, 1)])
        item = self.line(order, self.case)
        self.assertEqual((item.unit_cost, item.cost_source, item.net_amount), (None, 'unknown', None))

    def test_payment_freezes_the_branch_average(self):
        self.stock(self.case, 5, D('20'))
        self.stock(self.case, 5, D('30'))
        order = self.pay(self.web_order([(self.case, 2)]))
        item = self.line(order, self.case)
        self.assertEqual((item.unit_cost, item.cost_source), (D('25.0000'), 'average'))

    def test_without_average_the_reference_cost_or_pending(self):
        self.stock(self.case, 5)
        self.stock(self.cable, 5)
        Product.objects.filter(pk=self.case.pk).update(reference_cost=D('15.00'))
        order = self.pay(self.web_order([(self.case, 1), (self.cable, 1)]))
        self.assertEqual(
            (self.line(order, self.case).unit_cost, self.line(order, self.case).cost_source),
            (D('15.0000'), 'reference'))
        self.assertEqual(
            (self.line(order, self.cable).unit_cost, self.line(order, self.cable).cost_source),
            (None, 'unknown'))

    def test_line_nets_add_up_to_the_order_base_and_discount_is_shared(self):
        self.stock(self.case, 10, D('20'))
        self.stock(self.cable, 10, D('8'))
        self.stock(self.charger, 10, D('50'))
        order = self.pay(self.web_order(
            [(self.case, 3), (self.cable, 1), (self.charger, 2)], discount=D('17.77')))
        items = list(order.items.order_by('pk'))

        self.assertEqual(sum(i.net_amount for i in items), order.taxable_amount)
        # Before the discount, the nets add up to the subtotal without tax…
        before = (order.subtotal_amount / D('1.18')).quantize(D('0.01'), rounding=ROUND_HALF_UP)
        self.assertEqual(sum(i.net_amount + i.discount_net for i in items), before)
        # …and the discount, without tax, is the difference — shared by weight.
        self.assertEqual(sum(i.discount_net for i in items), before - order.taxable_amount)
        for item in items:
            self.assertGreaterEqual(item.discount_net, D('0'))
            gross_net = item.net_amount + item.discount_net
            self.assertEqual(item.unit_price_net, (gross_net / item.quantity).quantize(D('0.0001')))

    def test_a_shortfall_line_waits_and_is_valued_when_its_exit_is_written(self):
        self.stock(self.case, 1, D('20'))
        order = self.pay(self.web_order([(self.case, 2)]))
        item = self.line(order, self.case)
        self.assertEqual((item.unit_cost, item.cost_source), (None, 'unknown'))
        # The nets do not wait: they are the order's, not the shelf's.
        self.assertEqual(item.net_amount, order.taxable_amount)

        self.stock(self.case, 5, D('26'))     # (1 × 20 + 5 × 26) / 6 = 25
        inventory.reprocess_order_stock_exit(order)
        item.refresh_from_db()
        self.assertEqual((item.unit_cost, item.cost_source), (D('25.0000'), 'average'))

    def test_a_snapshot_never_changes(self):
        self.stock(self.case, 10, D('20'))
        order = self.pay(self.web_order([(self.case, 2)], discount=D('5.00')))
        before = OrderItem.objects.filter(order=order).values(
            'unit_cost', 'cost_source', 'net_amount', 'discount_net', 'unit_price_net').get()

        Product.objects.filter(pk=self.case.pk).update(reference_cost=D('1.00'), price=D('999.00'))
        self.stock(self.case, 10, D('80'))
        BranchStock.objects.filter(product=self.case).update(average_cost=D('77.0000'))
        inventory.record_sale_stock_movements(order)      # a replayed notification
        inventory.reprocess_order_stock_exit(order)

        after = OrderItem.objects.filter(order=order).values(
            'unit_cost', 'cost_source', 'net_amount', 'discount_net', 'unit_price_net').get()
        self.assertEqual(after, before)
        self.assertEqual(before['unit_cost'], D('20.0000'))

    def test_the_writers_themselves_refuse_to_overwrite(self):
        # Not only "nobody calls them twice": called again, they change nothing.
        self.stock(self.case, 10, D('20'))
        order = self.pay(self.web_order([(self.case, 2)], discount=D('5.00')))
        item = self.line(order, self.case)
        frozen = (item.unit_cost, item.cost_source, item.net_amount, item.discount_net, item.unit_price_net)

        other = StockMovement(unit_cost=D('999.0000'), cost_source='average')
        margin_services.snapshot_cost(item, other)
        Order.objects.filter(pk=order.pk).update(taxable_amount=D('1.00'), total=D('1.18'))
        order.refresh_from_db()
        margin_services.snapshot_nets(order)

        item.refresh_from_db()
        self.assertEqual(
            (item.unit_cost, item.cost_source, item.net_amount, item.discount_net, item.unit_price_net), frozen)

    def test_historical_lines_stay_pending(self):
        # A line written before DC1 (no snapshot) is not valued from today's catalogue.
        self.stock(self.case, 3, D('20'))
        order = self.web_order([(self.case, 1)])
        Order.objects.filter(pk=order.pk).update(status=Order.Status.PAID, paid=True, paid_at=timezone.now())
        Product.objects.filter(pk=self.case.pk).update(reference_cost=D('10.00'))
        item = self.line(order, self.case)
        self.assertEqual((item.unit_cost, item.cost_source), (None, 'unknown'))


class DeviceSnapshotTest(_Shop):
    def setUp(self):
        super().setUp()
        self.phone = _prod(self.company, 'iPhone 16', 'iphone-venta', price='4500.00', inventory=0)
        Product.objects.filter(pk=self.phone.pk).update(is_serialized=True, requires_imei=True)
        self.phone.refresh_from_db()
        units.receive_units(branch=self.branch, product=self.phone, reason='Compra', units=[
            {'serial_number': 'SER-0001', 'imei': IMEI_A, 'cost': '3800.00'},
            {'serial_number': 'SER-0002', 'imei': IMEI_B, 'cost': '3900.00'},
        ])

    def test_a_line_of_several_devices_is_valued_at_the_mean_of_their_specific_costs(self):
        order = self.pay(self.web_order([(self.phone, 2)]))
        item = self.line(order, self.phone)
        self.assertEqual((item.unit_cost, item.cost_source), (D('3850.0000'), 'unit'))


class PosSnapshotTest(_Shop):
    def setUp(self):
        super().setUp()
        self.seller, _ = _p2d_member(self.company, 'dc1_caja', ['company.view', 'sales.pos.use'])

    def test_a_counter_sale_freezes_cost_and_nets_with_its_discount(self):
        self.stock(self.case, 10, D('20'))
        self.stock(self.cable, 10, D('8'))
        order, _ = _c1_sale(
            actor=self.seller, company=self.company, branch=self.branch,
            items=[{'product': self.case.pk, 'quantity': 3}, {'product': self.cable.pk, 'quantity': 1}],
            manual_discount_type='amount', manual_discount_value='10', discount_reason='cliente',
            may_apply_manual_discount=True,
        )
        items = {i.product_id: i for i in order.items.all()}
        self.assertEqual((items[self.case.pk].unit_cost, items[self.case.pk].cost_source),
                         (D('20.0000'), 'average'))
        self.assertEqual(items[self.cable.pk].unit_cost, D('8.0000'))
        self.assertEqual(sum(i.net_amount for i in items.values()), order.taxable_amount)
        self.assertGreater(items[self.case.pk].discount_net, items[self.cable.pk].discount_net)


class PromotionLineNetsTest(_Shop):
    """
    DC1 review — a promotion lowers ITS line, not the whole order by weight.

    The fiscal F6 rule (`fiscal_services._promotion_line_discounts`): each
    line's base is shared over its gross AFTER its own promotion; the order
    discount without tax follows each line's promotion amount.
    """

    def promotion_order(self, lines, promos):
        from store.models import AppliedPromotion, DiscountSource, Promotion

        discount = sum(promos.values(), D('0.00'))
        order = self.web_order(lines, discount=discount)
        Order.objects.filter(pk=order.pk).update(discount_source=DiscountSource.PROMOTION)
        order.refresh_from_db()
        for product, amount in promos.items():
            promotion = Promotion.objects.create(
                company=self.company, name=f'Combo {order.pk}-{product.pk}',
                promotion_type=Promotion.BUNDLE_FIXED_PRICE, fixed_price=D('1.00'),
            )
            AppliedPromotion.objects.create(
                company=self.company, order=order, promotion=promotion,
                promotion_name_snapshot='Combo', promotion_type_snapshot=Promotion.BUNDLE_FIXED_PRICE,
                applications=1, regular_amount=product.price, discount_amount=amount,
                metadata={'components': [
                    {'product_id': product.pk, 'quantity_used': 1, 'unit_price': str(product.price)},
                ]},
            )
        return order

    def test_the_reviewers_example(self):
        funda = _prod(self.company, 'Funda', 'funda-promo', price='118.00', inventory=0)
        iphone = _prod(self.company, 'iPhone', 'iphone-promo', price='4720.00', inventory=0)
        order = self.promotion_order([(funda, 1), (iphone, 1)], {funda: D('18.00')})
        margin_services.snapshot_nets(order)
        funda_line, iphone_line = self.line(order, funda), self.line(order, iphone)
        self.assertEqual((funda_line.net_amount, iphone_line.net_amount), (D('84.75'), D('4000.00')))
        # 4838 / 1.18 = 4100.00 before the discount; 4084.75 after: all 15.25 is the case's.
        self.assertEqual((funda_line.discount_net, iphone_line.discount_net), (D('15.25'), D('0.00')))
        self.assertEqual(funda_line.net_amount + iphone_line.net_amount, order.taxable_amount)

    def test_it_agrees_with_the_fiscal_line_bases(self):
        from store import fiscal_services

        funda = _prod(self.company, 'Funda', 'funda-promo2', price='118.00', inventory=0)
        iphone = _prod(self.company, 'iPhone', 'iphone-promo2', price='4720.00', inventory=0)
        order = self.promotion_order([(funda, 1), (iphone, 1)], {funda: D('18.00')})
        margin_services.snapshot_nets(order)
        items = list(order.items.select_related('product').order_by('pk'))
        grosses = [item.price * item.quantity for item in items]
        promo = fiscal_services._promotion_line_discounts(order, items, grosses)
        self.assertEqual(promo, [D('18.00'), D('0.00')])
        line = {item.product_id: item for item in OrderItem.objects.filter(order=order)}
        self.assertEqual(line[iphone.pk].discount_net, D('0.00'))
        self.assertGreater(line[funda.pk].discount_net, D('0.00'))

    def test_random_orders_always_add_up(self):
        """Property: whatever the lines and promotions, the nets add up to the base and never go negative."""
        import random

        rng = random.Random(20261014)
        for case in range(40):
            products = [
                _prod(self.company, f'P{case}-{n}', f'p-{case}-{n}',
                      price=str(D(rng.randint(100, 500000)) / 100), inventory=0)
                for n in range(rng.randint(1, 4))
            ]
            lines = [(product, 1) for product in products]
            promos = {}
            for product in products:
                if rng.random() < 0.5:
                    promos[product] = (product.price * D(rng.randint(1, 60)) / 100).quantize(D('0.01'))
            if not promos:
                continue
            order = self.promotion_order(lines, promos)
            margin_services.snapshot_nets(order)
            rows = list(OrderItem.objects.filter(order=order))
            with self.subTest(case=case):
                self.assertEqual(sum(row.net_amount for row in rows), order.taxable_amount)
                for row in rows:
                    self.assertGreaterEqual(row.net_amount, D('0.00'))
                    self.assertGreaterEqual(row.discount_net, D('0.00'))
                    if row.product not in promos:
                        self.assertEqual(row.discount_net, D('0.00'))
