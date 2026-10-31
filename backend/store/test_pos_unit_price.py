"""
INV-COM-01C — a used device is sold at the till at ITS price (`StockUnit.sale_price`), as the web already does.

Until now the till priced every line by `Product.price` and ignored the device's own price: a used variant created
by the catalogue (price 0.00) could not be sold at all, and one with a reference price sold every unit at that price.

The rule, one authority for the preview and the sale (`pos_services.unit_prices` through `build_pos_sale`):

  * a device of a USED-line product costs its `sale_price`; a missing, zero or invalid price refuses THAT device;
  * a new device and an accessory keep the product's price; a line on order keeps its offer's;
  * the cost never decides the price, and nothing the client sends does either;
  * each used device is its own line (`OrderItem.stock_unit`, price, cost, Kardex exit and identification), and the
    exit of a product is ONE movement — the (order, product) idempotency of the Kardex still holds;
  * a price changed after the preview is never charged silently: `expected_total` makes it a 409;
  * promotions never apply to a device priced on its own; a manual discount spreads over the lines as always.
"""
from decimal import Decimal

from django.core.cache import cache

from store import inventory_services as inventory
from store import pos_services as pos
from store.models import (
    AdminAuditLog, Order, OrderItem, OrderUnit, Product, StockMovement, StockUnit,
)
from store.test_stock_units import IMEI_A, IMEI_B, IMEI_C, IMEI_D, UnitsBase
from store.tests import _c1_sale, _c13_combo, _p2d_branch, _p2d_member, _prod

SCANNER = 'scanner'


class UsedBase(UnitsBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.cashier, _ = _p2d_member(self.company, 'caja_precio', ['company.view', 'sales.pos.use'])
        self.boss, _ = _p2d_member(self.company, 'jefe_precio', [
            'company.view', 'sales.pos.use', 'sales.discounts.apply', 'sales.notes.manage', 'products.manage'])
        # A used variant: its catalogue price is NOT what a device costs (0.00, like the assisted catalogue makes them).
        self.used = _prod(self.company, 'iPhone 15 128 GB Seminuevo', 'iphone-15-seminuevo', price='0.00', inventory=0)
        Product.objects.filter(pk=self.used.pk).update(
            is_serialized=True, requires_imei=True, condition_line=Product.CONDITION_USED)
        self.used.refresh_from_db()
        self.cable = _prod(self.company, 'Cable USB-C', 'cable-usb-c-precio', price='49.00', inventory=20)

    def receive_used(self, *rows, **extra):
        return self.receive(*rows, product=self.used, **extra)

    def sell(self, items, **extra):
        extra.setdefault('actor', self.cashier)
        return _c1_sale(company=self.company, branch=self.branch_a, items=items, **extra)

    def line(self, *units, product=None, method=SCANNER):
        return {'product': (product or self.used).pk, 'quantity': len(units) or 1,
                'units': [{'id': unit.pk, 'method': method} for unit in units]}

    def exits(self, order):
        return list(StockMovement.objects.filter(order=order, movement_type=StockMovement.SALE_EXIT))

    def priced_used(self):
        a, b = self.receive_used(
            self.row('USADO00001', IMEI_A, condition='used', cost='1500.00', sale_price='2300.00'),
            self.row('USADO00002', IMEI_B, condition='used', cost='1700.00', sale_price='2650.50'),
        )
        return a, b


class TheUnitsOwnPriceTest(UsedBase):
    def test_a_used_device_is_charged_at_its_own_price_not_the_products(self):
        Product.objects.filter(pk=self.used.pk).update(price=Decimal('999.00'))   # a reference price: must be ignored
        a, _ = self.priced_used()
        order, created = self.sell([self.line(a)])
        self.assertTrue(created)
        item = order.items.get()
        self.assertEqual((item.stock_unit_id, item.quantity, item.price), (a.pk, 1, Decimal('2300.00')))
        self.assertEqual(order.total, Decimal('2300.00'))

    def test_two_devices_of_one_variant_keep_their_own_prices_and_leave_as_two_real_units(self):
        a, b = self.priced_used()
        stock_before = self.quantity(self.used)
        order, _ = self.sell([self.line(a, b)])

        items = {i.stock_unit_id: i for i in order.items.all()}
        self.assertEqual(len(items), 2)
        self.assertEqual((items[a.pk].price, items[b.pk].price), (Decimal('2300.00'), Decimal('2650.50')))
        self.assertEqual(order.total, Decimal('4950.50'))
        # Stock: two real units out, ONE Kardex exit that names both (the (order, product) key still holds).
        self.assertEqual(self.quantity(self.used), stock_before - 2)
        [exit_] = self.exits(order)
        self.assertEqual((exit_.quantity, sorted(exit_.metadata['unit_ids'])), (2, sorted([a.pk, b.pk])))
        for unit in (a, b):
            unit.refresh_from_db()
            self.assertEqual((unit.status, unit.order_id), ('sold', order.pk))
        self.assertEqual(OrderUnit.objects.filter(order=order).count(), 2)
        self.assert_invariant(self.used)

    def test_each_line_keeps_its_devices_own_cost_for_the_margin(self):
        a, b = self.priced_used()
        order, _ = self.sell([self.line(a, b)])
        costs = {i.stock_unit_id: i.unit_cost for i in order.items.all()}
        self.assertEqual(costs, {a.pk: Decimal('1500.0000'), b.pk: Decimal('1700.0000')})

    def test_the_cost_never_decides_the_price(self):
        a, = self.receive_used(self.row('USADO00003', IMEI_C, condition='used', cost='5000.00', sale_price='900.00'))
        order, _ = self.sell([self.line(a)])
        self.assertEqual(order.items.get().price, Decimal('900.00'))

    def test_the_price_is_frozen_with_the_sale_whatever_happens_to_the_ficha_afterwards(self):
        a, _ = self.priced_used()
        order, _ = self.sell([self.line(a)])
        StockUnit.objects.filter(pk=a.pk).update(sale_price=Decimal('1.00'))
        self.assertEqual(order.items.get().price, Decimal('2300.00'))
        self.assertEqual(Order.objects.get(pk=order.pk).total, Decimal('2300.00'))


class AUnitWithoutPriceIsNotSoldTest(UsedBase):
    def test_a_device_without_a_valid_price_is_refused_and_nothing_is_written(self):
        for label, value, imei_value in (('missing', None, IMEI_C), ('zero', Decimal('0.00'), IMEI_D)):
            with self.subTest(label):
                unit, = self.receive_used(self.row(f'SIN{label.upper()}01', imei_value, condition='used'))
                StockUnit.objects.filter(pk=unit.pk).update(sale_price=value)
                orders, exits = Order.objects.count(), StockMovement.objects.filter(movement_type='sale_exit').count()
                with self.assertRaises(pos.PosValidationError) as refused:
                    self.sell([self.line(unit)])
                self.assertIn('todavía no tiene precio de venta', str(refused.exception))
                unit.refresh_from_db()
                self.assertEqual(unit.status, 'available')
                self.assertEqual((Order.objects.count(), StockMovement.objects.filter(movement_type='sale_exit').count()),
                                 (orders, exits))

    def test_one_unpriced_device_refuses_the_whole_basket(self):
        a, _ = self.priced_used()
        bare, = self.receive_used(self.row('USADO00004', IMEI_C, condition='used'))
        orders = Order.objects.count()
        with self.assertRaises(pos.PosValidationError):
            self.sell([self.line(a, bare), {'product': self.cable.pk, 'quantity': 1}])
        self.assertEqual(Order.objects.count(), orders)
        a.refresh_from_db()
        self.assertEqual(a.status, 'available')

    def test_the_preview_refuses_it_too_with_the_same_words(self):
        bare, = self.receive_used(self.row('USADO00005', IMEI_C, condition='used'))
        with self.assertRaises(pos.PosValidationError) as refused:
            pos.build_pos_sale(operator=self.cashier, company=self.company, branch=self.branch_a,
                               items=[self.line(bare)], validate_cash=False)
        self.assertIn('Solicita que un usuario autorizado lo configure', str(refused.exception))


class OtherProductsKeepTheirPriceTest(UsedBase):
    def test_a_new_serialized_device_and_an_accessory_keep_the_products_price(self):
        self.receive(self.row('NUEVO00001', IMEI_C), self.row('NUEVO00002', IMEI_D))   # self.phone: new line, 4500.00
        order, _ = self.sell([
            {'product': self.phone.pk, 'quantity': 2, 'units': [{'id': u.pk, 'method': SCANNER}
                                                                for u in StockUnit.objects.filter(product=self.phone)]},
            {'product': self.cable.pk, 'quantity': 3},
        ])
        by_product = {i.product_id: i for i in order.items.all()}
        self.assertEqual(len(by_product), 2)                      # the new devices stay ONE merged line
        self.assertEqual((by_product[self.phone.pk].quantity, by_product[self.phone.pk].price), (2, Decimal('4500.00')))
        self.assertEqual((by_product[self.cable.pk].quantity, by_product[self.cable.pk].price), (3, Decimal('49.00')))
        self.assertEqual(order.total, Decimal('9147.00'))

    def test_a_basket_of_used_new_and_accessory_is_one_coherent_sale(self):
        a, b = self.priced_used()
        self.receive(self.row('NUEVO00003', IMEI_C))
        new_unit = StockUnit.objects.get(serial_number='NUEVO00003')
        order, _ = self.sell([
            self.line(a), self.line(b),
            {'product': self.phone.pk, 'quantity': 1, 'units': [{'id': new_unit.pk, 'method': SCANNER}]},
            {'product': self.cable.pk, 'quantity': 2},
        ])
        self.assertEqual(order.total, Decimal('2300.00') + Decimal('2650.50') + Decimal('4500.00') + Decimal('98.00'))
        self.assertEqual(order.items.count(), 4)
        self.assertEqual(StockMovement.objects.filter(order=order, movement_type=StockMovement.SALE_EXIT).count(), 3)
        self.assertEqual(OrderUnit.objects.filter(order=order).count(), 3)
        self.assert_invariant(self.used)
        self.assert_invariant(self.phone)


class WhatTheClientCannotImposeTest(UsedBase):
    def test_a_price_sent_by_the_client_is_ignored(self):
        a, _ = self.priced_used()
        line = self.line(a)
        line['price'] = '1.00'
        line['unit_price'] = '1.00'
        line['units'][0]['price'] = '1.00'
        order, _ = self.sell([line])
        self.assertEqual(order.items.get().price, Decimal('2300.00'))

    def test_only_a_device_here_free_and_of_this_company_is_priced(self):
        elsewhere, = self.receive_used(self.row('USADO00010', IMEI_C, condition='used', sale_price='1000.00'),
                                       branch=self.branch_b)
        foreign_product = _prod(self.other, 'iPhone ajeno usado', 'iphone-ajeno-usado', price='0.00', inventory=0)
        Product.objects.filter(pk=foreign_product.pk).update(
            is_serialized=True, requires_imei=True, condition_line=Product.CONDITION_USED)
        foreign = StockUnit.objects.create(
            company=self.other, branch=self.foreign_branch, product=foreign_product, serial_number='AJENO00010',
            imei=IMEI_D, sale_price=Decimal('1.00'))
        held, = self.receive_used(self.row('USADO00011', None, condition='used', sale_price='1000.00')) \
            if not self.used.requires_imei else self.receive_used(
                self.row('USADO00011', '356938035643999', condition='used', sale_price='1000.00'))
        from store import stock_unit_services as units
        units.reserve(held, reason='Apartado', actor=self.staff)
        for label, items in (
            ('of another branch', [self.line(elsewhere)]),
            ('of another company', [self.line(foreign, product=foreign_product)]),
            ('set aside', [self.line(held)]),
            ('does not exist', [{'product': self.used.pk, 'quantity': 1, 'units': [{'id': 999999, 'method': SCANNER}]}]),
        ):
            with self.subTest(label), self.assertRaises((pos.PosValidationError, inventory.InventoryError)):
                self.sell(items)
        self.assertEqual(Order.objects.filter(sales_channel='pos').count(), 0)


class EachLayerGuardsTheDeviceOnItsOwnTest(UsedBase):
    """The pricing authority and the stock writer each refuse a device that is not sellable here: neither leans on the other."""

    def test_the_price_authority_refuses_a_device_of_another_branch_or_set_aside(self):
        from store import stock_unit_services as units
        elsewhere, = self.receive_used(self.row('USADO00020', IMEI_C, condition='used', sale_price='1000.00'),
                                       branch=self.branch_b)
        held, = self.receive_used(self.row('USADO00021', '356938035643999', condition='used', sale_price='1000.00'))
        units.reserve(held, reason='Apartado', actor=self.staff)
        for label, unit in (('of another branch', elsewhere), ('set aside', held)):
            items = [self.line(unit)]
            with self.subTest(label), self.assertRaises(pos.PosValidationError):
                pos.unit_prices(self.company, self.branch_a, pos.resolve_pos_products(self.company, items), items)

    def test_the_stock_writer_refuses_a_device_that_stopped_being_available_after_it_was_priced(self):
        from unittest import mock
        from store import stock_unit_services as units
        a, = self.receive_used(self.row('USADO00022', IMEI_A, condition='used', sale_price='1000.00'))
        units.reserve(a, reason='Apartado', actor=self.staff)
        stale = lambda *args, **kwargs: {self.used.pk: [(a, Decimal('1000.00'))]}   # noqa: E731 — priced before it was set aside
        with mock.patch.object(pos, 'unit_prices', side_effect=stale), self.assertRaises(inventory.InventoryError):
            self.sell([self.line(a)])
        self.assertEqual(Order.objects.filter(sales_channel='pos').count(), 0)
        a.refresh_from_db()
        self.assertNotEqual(a.status, StockUnit.Status.SOLD)


class DiscountsAndPromotionsTest(UsedBase):
    def test_a_manual_discount_spreads_over_the_lines_and_the_totals_hold(self):
        a, b = self.priced_used()
        order, _ = self.sell(
            [self.line(a, b), {'product': self.cable.pk, 'quantity': 1}], actor=self.boss,
            manual_discount_type='percent', manual_discount_value='10', discount_reason='Cliente frecuente',
            may_apply_manual_discount=True)
        subtotal = Decimal('2300.00') + Decimal('2650.50') + Decimal('49.00')
        self.assertEqual(order.discount_amount, (subtotal * Decimal('0.10')).quantize(Decimal('0.01')))
        self.assertEqual(order.total, subtotal - order.discount_amount)
        # The frozen line nets are the prices without IGV: they add up to the subtotal's net, whatever the split.
        nets = sum(Decimal(i.unit_price_net) * i.quantity for i in order.items.all())
        self.assertAlmostEqual(float(nets), float(subtotal / Decimal('1.18')), delta=0.05)
        self.assertEqual(order.tax_amount + order.taxable_amount, order.total)

    def test_a_promotion_never_discounts_a_device_priced_on_its_own_but_still_fires_on_the_rest(self):
        a, b = self.priced_used()
        case = _prod(self.company, 'Funda combo', 'funda-combo-precio', price='100.00', inventory=10)
        glass = _prod(self.company, 'Vidrio combo', 'vidrio-combo-precio', price='50.00', inventory=10)
        _c13_combo(self.company, [(case, 1), (glass, 1)], fixed_price='120.00')
        order, _ = self.sell([self.line(a, b), {'product': case.pk, 'quantity': 1}, {'product': glass.pk, 'quantity': 1}])
        self.assertEqual(order.discount_amount, Decimal('30.00'))                 # the combo of the accessories only
        self.assertEqual(order.total, Decimal('2300.00') + Decimal('2650.50') + Decimal('120.00'))

    def test_a_combo_that_includes_the_used_variant_does_not_apply_to_its_devices(self):
        a, b = self.priced_used()
        case = _prod(self.company, 'Funda combo 2', 'funda-combo-dos', price='100.00', inventory=10)
        _c13_combo(self.company, [(self.used, 1), (case, 1)], fixed_price='2000.00')
        order, _ = self.sell([self.line(a), {'product': case.pk, 'quantity': 1}])
        self.assertEqual(order.discount_amount, Decimal('0.00'))
        self.assertEqual(order.total, Decimal('2400.00'))


class PriceChangedAfterThePreviewTest(UsedBase):
    def test_the_preview_and_the_sale_come_from_the_same_authority(self):
        a, b = self.priced_used()
        priced = pos.build_pos_sale(operator=self.cashier, company=self.company, branch=self.branch_a,
                                    items=[self.line(a, b)], validate_cash=False)
        order, _ = self.sell([self.line(a, b)])
        self.assertEqual(priced['total'], order.total)
        self.assertEqual(sorted(str(u) for _p, _q, u, *_ in priced['lines']), ['2300.00', '2650.50'])

    def test_a_price_changed_after_the_preview_is_not_charged_silently(self):
        a, _ = self.priced_used()
        seen = pos.build_pos_sale(operator=self.cashier, company=self.company, branch=self.branch_a,
                                  items=[self.line(a)], validate_cash=False)['total']
        StockUnit.objects.filter(pk=a.pk).update(sale_price=Decimal('2500.00'))     # somebody edited the ficha
        orders = Order.objects.count()
        with self.assertRaises(pos.PosPriceChanged) as changed:
            self.sell([self.line(a)], expected_total=seen)
        self.assertEqual((changed.exception.expected, changed.exception.current), (Decimal('2300.00'), Decimal('2500.00')))
        self.assertEqual(Order.objects.count(), orders)
        a.refresh_from_db()
        self.assertEqual(a.status, 'available')
        # Having read the new total, the cashier confirms it explicitly.
        order, _ = self.sell([self.line(a)], expected_total=Decimal('2500.00'))
        self.assertEqual(order.total, Decimal('2500.00'))

    def test_without_expected_total_the_sale_is_priced_by_the_server_as_before(self):
        a, _ = self.priced_used()
        order, _ = self.sell([self.line(a)])
        self.assertEqual(order.total, Decimal('2300.00'))

    def test_a_retry_of_a_sale_already_made_answers_with_it_even_if_the_price_changed_since(self):
        a, _ = self.priced_used()
        first, created = self.sell([self.line(a)], idempotency_key='retry-precio-0001', expected_total=Decimal('2300.00'))
        StockUnit.objects.filter(pk=a.pk).update(sale_price=Decimal('3000.00'))
        again, created_again = self.sell([self.line(a)], idempotency_key='retry-precio-0001', expected_total=Decimal('2300.00'))
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(again.pk, first.pk)
        self.assertEqual(Order.objects.filter(pos_idempotency_key='retry-precio-0001').count(), 1)
        self.assertEqual(self.exits(first)[0].quantity, 1)
        self.assertEqual(len(self.exits(first)), 1)


class AfterTheSaleTest(UsedBase):
    def test_the_sale_payload_names_each_device_on_its_own_line(self):
        from store import pos_payloads
        a, b = self.priced_used()
        order, _ = self.sell([self.line(a, b)])
        payload = pos_payloads.sale_payload(order, self.branch_a, created=True, may_see_commission=False)
        rows = {row['price']: row for row in payload['items']}
        self.assertEqual(set(rows), {'2300.00', '2650.50'})
        self.assertEqual([u['serial_number'] for u in rows['2300.00']['units']], ['USADO00001'])
        self.assertEqual([u['serial_number'] for u in rows['2650.50']['units']], ['USADO00002'])

    def test_the_preview_payload_gives_the_price_of_each_device(self):
        from store import pos_payloads
        a, b = self.priced_used()
        priced = pos.build_pos_sale(operator=self.cashier, company=self.company, branch=self.branch_a,
                                    items=[self.line(a, b)], validate_cash=False)
        lines = pos_payloads.preview_payload(priced, may_see_commission=False)['lines']
        self.assertEqual(sorted((row['price'], row['unit']) for row in lines), [('2300.00', a.pk), ('2650.50', b.pk)])
        self.assertTrue(all(row['quantity'] == 1 for row in lines))

    def test_the_unit_lookup_shows_the_ficha_and_the_price_without_cost(self):
        from store import pos_payloads, unit_identification
        a, _ = self.priced_used()
        StockUnit.objects.filter(pk=a.pk).update(battery_health=91)
        a.refresh_from_db()
        payload = pos_payloads.unit_lookup_payload(a, self.branch_a, SCANNER)
        info = payload['unit']
        self.assertEqual((info['sale_price'], info['battery_health'], info['priced_by']), ('2300.00', 91, 'unit'))
        self.assertNotIn('cost', info)
        self.assertNotIn('1500', str(payload))
        bare, = self.receive_used(self.row('USADO00020', IMEI_C, condition='used'))
        self.assertIsNone(pos_payloads.unit_lookup_payload(bare, self.branch_a, SCANNER)['unit']['sale_price'])
        del unit_identification

    def test_a_sales_note_prints_each_device_at_its_price(self):
        a, b = self.priced_used()
        order, _ = self.sell([self.line(a, b)], actor=self.boss, receipt_type='sales_note')
        from store.pdf_services import order_lines
        lines = order_lines(order)
        self.assertEqual(sorted(line['unit_price'] for line in lines), [Decimal('2300.00'), Decimal('2650.50')])
        self.assertEqual(sorted(line['units'][0]['serial_number'] for line in lines), ['USADO00001', 'USADO00002'])
        self.assertEqual(sum(line['subtotal'] for line in lines), order.total)

    def test_cancelling_the_sale_puts_both_devices_back(self):
        from store import order_cancellation
        a, b = self.priced_used()
        order, _ = self.sell([self.line(a, b)])
        order_cancellation.cancel_order(order, self.boss, 'El cliente se arrepintió', goods_returned=True)
        for unit in (a, b):
            unit.refresh_from_db()
            self.assertEqual((unit.status, unit.order_id), ('available', None))
        self.assert_invariant(self.used)
        self.assertEqual(self.quantity(self.used), 2)

    def test_the_audit_line_carries_counts_and_totals_but_no_identifier(self):
        a, _ = self.priced_used()
        order, _ = self.sell([self.line(a)])
        entry = AdminAuditLog.objects.get(action='pos_sale_completed', target_id=order.pk)
        self.assertEqual(entry.metadata['units'], 1)
        self.assertNotIn(IMEI_A, str(entry.metadata))
