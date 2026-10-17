"""
SERIAL-PICK-01 · the device that leaves the shop is the one somebody identified.

A serialized product used to be sold as «one of them»: the system took the
OLDEST unit of the branch, and the paper printed that unit's IMEI whatever was
actually handed over. The owner's rule replaces that:

  * AT THE TILL nothing with a serial is sold without saying which device: by
    scanner, or typed by hand when the scanner does not read. Typed by hand, an
    IMEI is checked (15 digits and its Luhn digit), and it is written down that
    it was typed and by whom;
  * A WEB ORDER is identified the same way when it is handed over. Paying it
    only sets a unit aside; no paper names a device until a person has;
  * the price is the product's. A device has no price of its own.

`OrderUnit` is that record — which device, for which order, how it was
identified and by whom — and the only thing a document reads.
"""
from decimal import Decimal
from urllib.parse import urlencode

from django.core.cache import cache
from django.utils import timezone

from store import inventory_services as inventory
from store import order_fulfillment_services as fulfillment
from store import pos_services as pos
from store import unit_identification as picking
from store.fulfillment_test_support import legacy_move, place_at, walk_to
from store.models import (
    AdminAuditLog, Order, OrderItem, OrderUnit, Product, StockMovement, StockUnit,
)
from store.pdf_services import sold_units
from store.test_stock_units import IMEI_A, IMEI_B, IMEI_C, IMEI_D, UnitsBase
from store.tests import _c1_sale, _c13_combo, _p2d_member, _prod

SCANNER, MANUAL = 'scanner', 'manual'


class _Base(UnitsBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.cashier, _ = _p2d_member(self.company, 'caja_serial', ['company.view', 'sales.pos.use'])
        self.manager, _ = _p2d_member(self.company, 'pedidos_serial', ['company.view', 'sales.orders.view', 'sales.orders.manage'])
        # Two phones, received in this order: OLD is the one the old rule would have sold.
        self.old, self.new = self.receive(self.row('F2LXK1OLD1', IMEI_A), self.row('F2LXK1NEW2', IMEI_B))
        self.cable = _prod(self.company, 'Cable USB-C', 'cable-usb-c-serial', price='49.00', inventory=20)

    def sell(self, items, **extra):
        return _c1_sale(actor=self.cashier, company=self.company, branch=self.branch_a, items=items, **extra)

    def line(self, *units, product=None, quantity=None, method=SCANNER):
        return {'product': (product or self.phone).pk, 'quantity': quantity or len(units) or 1,
                'units': [{'id': unit.pk, 'method': method} for unit in units]}

    def status(self, unit):
        unit.refresh_from_db()
        return unit.status, unit.order_id


class TheTillTest(_Base):
    def test_a_device_is_not_sold_without_saying_which(self):
        orders = Order.objects.count()
        for items in ([{'product': self.phone.pk, 'quantity': 1}],
                      [{'product': self.phone.pk, 'quantity': 1, 'units': []}]):
            with self.subTest(items), self.assertRaises(pos.PosValidationError) as refused:
                self.sell(items)
            self.assertIn('iPhone 16', str(refused.exception))
        self.assertEqual(Order.objects.count(), orders)
        self.assertEqual(self.available(), 2)
        self.assert_invariant()

    def test_the_device_sold_is_the_one_identified_not_the_oldest(self):
        order, created = self.sell([self.line(self.new)])

        self.assertTrue(created)
        self.assertEqual(self.status(self.new), ('sold', order.pk))
        self.assertEqual(self.status(self.old), ('available', None))           # the oldest stays on the shelf
        record = OrderUnit.objects.get(order=order)
        self.assertEqual((record.unit, record.product, record.method, record.identified_by),
                         (self.new, self.phone, SCANNER, self.cashier))
        self.assertIsNotNone(record.left_at)                           # a till sale is handed over as it is charged
        self.assertEqual(record.company, self.company)
        movement = StockMovement.objects.get(order=order, movement_type=StockMovement.SALE_EXIT)
        self.assertEqual(movement.metadata['unit_ids'], [self.new.pk])
        self.assertEqual(self.quantity(), 1)
        self.assert_invariant()

    def test_as_many_devices_as_the_line_says(self):
        for label, items in (
            ('one device for two', [self.line(self.new, quantity=2)]),
            ('two devices for one', [self.line(self.old, self.new, quantity=1)]),
            ('the same device twice', [{'product': self.phone.pk, 'quantity': 2,
                                        'units': [{'id': self.new.pk, 'method': SCANNER}] * 2}]),
        ):
            with self.subTest(label), self.assertRaises(pos.PosValidationError):
                self.sell(items)
        self.assertEqual(self.available(), 2)

        order, _ = self.sell([self.line(self.old, self.new)])
        self.assertEqual(OrderUnit.objects.filter(order=order).count(), 2)
        self.assertEqual(self.available(), 0)
        self.assert_invariant()

    def test_two_lines_of_one_product_are_one_line_with_both_devices(self):
        order, _ = self.sell([self.line(self.old), self.line(self.new)])
        self.assertEqual(order.items.get().quantity, 2)
        self.assertEqual(set(OrderUnit.objects.filter(order=order).values_list('unit_id', flat=True)),
                         {self.old.pk, self.new.pk})

    def test_only_a_device_that_is_here_and_on_the_shelf(self):
        elsewhere, = self.receive(self.row('F2LXK1NORTE', IMEI_C), branch=self.branch_b)
        laptop, = self.receive(self.row('C02LAPTOP01'), product=self.laptop)
        foreign = StockUnit.objects.create(
            company=self.other, branch=self.foreign_branch, product=self.foreign_phone,
            serial_number='AJENO00001', imei=IMEI_D)
        reserved, = self.receive(self.row('F2LXK1RESV1', None), product=self.laptop)
        from store import stock_unit_services as units
        units.reserve(reserved, reason='Cliente lo aparta', actor=self.staff)

        for label, items in (
            ('of another branch', [self.line(elsewhere)]),
            ('of another product', [self.line(laptop)]),
            ('of another company', [self.line(foreign)]),
            ('set aside', [self.line(reserved, product=self.laptop)]),
            ('that does not exist', [{'product': self.phone.pk, 'quantity': 1, 'units': [{'id': 999999, 'method': SCANNER}]}]),
            ('not a number', [{'product': self.phone.pk, 'quantity': 1, 'units': [{'id': 'abc', 'method': SCANNER}]}]),
        ):
            with self.subTest(label), self.assertRaises((pos.PosValidationError, inventory.InventoryError)):
                self.sell(items)
        self.assertEqual(Order.objects.filter(sales_channel='pos').count(), 0)
        self.assertEqual(self.available(), 2)

    def test_a_device_already_sold_is_not_sold_again(self):
        self.sell([self.line(self.new)])
        with self.assertRaises((pos.PosValidationError, inventory.InventoryError)):
            self.sell([self.line(self.new)])
        self.assertEqual(OrderUnit.objects.filter(unit=self.new).count(), 1)

    def test_an_ordinary_product_takes_a_number_not_devices(self):
        with self.assertRaises(pos.PosValidationError):
            self.sell([{'product': self.cable.pk, 'quantity': 1, 'units': [{'id': self.new.pk, 'method': SCANNER}]}])
        order, _ = self.sell([{'product': self.cable.pk, 'quantity': 2}, self.line(self.new)])
        self.assertEqual(order.items.count(), 2)
        self.assertEqual(OrderUnit.objects.filter(order=order).count(), 1)

    def test_typed_by_hand_is_written_down_and_by_whom(self):
        order, _ = self.sell([self.line(self.new, method=MANUAL)])

        self.assertEqual(OrderUnit.objects.get(order=order).method, MANUAL)
        entry = AdminAuditLog.objects.filter(action='order_unit_identified').get()
        self.assertEqual(entry.actor, self.cashier)
        self.assertEqual(entry.company, self.company)
        self.assertEqual(entry.metadata['method'], MANUAL)
        self.assertEqual(entry.metadata['order_id'], order.pk)
        self.assertEqual(entry.metadata['unit_id'], self.new.pk)
        # The log says a device was typed in, not which IMEI: the unit is the record.
        self.assertNotIn(IMEI_B, str(entry.metadata))

    def test_how_it_was_identified_is_one_of_two_ways(self):
        for method in ('', 'auto', 'legacy', None, 7):
            with self.subTest(method), self.assertRaises(pos.PosValidationError):
                self.sell([{'product': self.phone.pk, 'quantity': 1, 'units': [{'id': self.new.pk, 'method': method}]}])
        self.assertEqual(self.available(), 2)

    def test_the_same_request_again_is_the_same_sale_and_another_device_is_not(self):
        first, created = self.sell([self.line(self.new)], idempotency_key='serial-pick-0001')
        again, repeated = self.sell([self.line(self.new)], idempotency_key='serial-pick-0001')
        self.assertEqual((first.pk, created, repeated), (again.pk, True, False))
        with self.assertRaises(pos.PosIdempotencyConflict):
            self.sell([self.line(self.old)], idempotency_key='serial-pick-0001')
        self.assertEqual(self.status(self.old), ('available', None))

    def test_the_price_is_the_products(self):
        order, _ = self.sell([self.line(self.new)])
        self.assertEqual(order.total, Decimal('4500.00'))
        self.assertFalse(hasattr(self.new, 'price_override'))


class TheComboTest(_Base):
    """A combo fills the basket in one tap: the till must still ask for the phone."""

    def test_a_combo_says_which_of_its_parts_need_a_device(self):
        from store import promotion_services

        _c13_combo(self.company, [(self.phone, 1), (self.cable, 1)], percent=10)
        [offer] = promotion_services.combo_availability(self.company, self.branch_a)
        parts = {part['product_id']: part for part in offer['components']}
        self.assertEqual(
            (parts[self.phone.pk]['is_serialized'], parts[self.phone.pk]['requires_imei']), (True, True))
        self.assertEqual(
            (parts[self.cable.pk]['is_serialized'], parts[self.cable.pk]['requires_imei']), (False, False))

    def test_a_combo_with_a_phone_is_not_sold_without_saying_which(self):
        _c13_combo(self.company, [(self.phone, 1), (self.cable, 1)], percent=10)
        with self.assertRaises(pos.PosValidationError):
            self.sell([{'product': self.phone.pk, 'quantity': 1}, {'product': self.cable.pk, 'quantity': 1}])
        order, _ = self.sell([self.line(self.new), {'product': self.cable.pk, 'quantity': 1}])
        self.assertEqual([row.unit_id for row in OrderUnit.objects.filter(order=order)], [self.new.pk])
        self.assertGreater(order.discount_amount, 0)


class LookingADeviceUpTest(_Base):
    def find(self, code, *, method=SCANNER, branch=None):
        return picking.sellable_unit(company=self.company, branch=branch or self.branch_a, code=code, method=method)

    def refused(self, code, **kwargs):
        with self.assertRaises(picking.UnitIdentificationError) as refused:
            self.find(code, **kwargs)
        return refused.exception.code

    def test_by_imei_as_a_scanner_or_a_person_gives_it(self):
        for code in (IMEI_B, f' {IMEI_B[:6]} {IMEI_B[6:]} ', f'{IMEI_B[:2]}-{IMEI_B[2:8]}-{IMEI_B[8:]}'):
            with self.subTest(code):
                self.assertEqual(self.find(code), self.new)
        self.assertEqual(self.find(IMEI_B, method=MANUAL), self.new)

    def test_by_its_second_imei_and_by_its_serial(self):
        dual, = self.receive({'serial_number': 'F2LXK1DUAL1', 'imei': IMEI_C, 'imei2': IMEI_D})
        self.assertEqual(self.find(IMEI_D), dual)
        self.assertEqual(self.find('f2lxk1dual1'), dual)
        self.assertEqual(self.find(' F2LXK1NEW2 '), self.new)

    def test_what_is_not_a_device_of_this_shop(self):
        wrong_digit = IMEI_B[:-1] + str((int(IMEI_B[-1]) + 1) % 10)
        self.assertEqual(self.refused(wrong_digit), 'invalid_code')                   # 15 digits, wrong check digit
        self.assertEqual(self.refused('NOEXISTE01'), 'not_found')
        self.assertEqual(self.refused(''), 'invalid_code')
        StockUnit.objects.create(company=self.other, branch=self.foreign_branch, product=self.foreign_phone,
                                 serial_number='AJENO00001', imei=IMEI_D)
        self.assertEqual(self.refused(IMEI_D), 'not_found')                           # another company's: it is not there

    def test_what_cannot_be_sold_from_here_says_why(self):
        elsewhere, = self.receive(self.row('F2LXK1NORTE', IMEI_C), branch=self.branch_b)
        self.assertEqual(self.refused(IMEI_C), 'wrong_branch')
        self.sell([self.line(self.new)])
        self.assertEqual(self.refused(IMEI_B), 'not_available')

    def test_typed_by_hand_a_phone_is_its_imei(self):
        """A serial typed by hand is one wrong character away from another device; an IMEI checks itself."""
        self.assertEqual(self.refused('F2LXK1NEW2', method=MANUAL), 'manual_needs_imei')
        self.assertEqual(self.find('F2LXK1NEW2', method=SCANNER), self.new)
        laptop, = self.receive(self.row('C02LAPTOP01'), product=self.laptop)
        self.assertEqual(self.find('C02LAPTOP01', method=MANUAL), laptop)             # a product without IMEI has only its serial

    def test_a_serial_two_products_share_is_not_guessed(self):
        self.receive(self.row('SERIE-REPETIDA', IMEI_C))
        self.receive(self.row('SERIE-REPETIDA'), product=self.laptop)
        self.assertEqual(self.refused('SERIE-REPETIDA'), 'ambiguous')
        self.assertEqual(self.find(IMEI_C).serial_number, 'SERIE-REPETIDA')

    def test_what_is_shown_to_confirm_names_the_device(self):
        shown = picking.describe(self.new)
        self.assertEqual(shown['unit_id'], self.new.pk)
        self.assertEqual(shown['product_id'], self.phone.pk)
        self.assertEqual(shown['product_name'], 'iPhone 16')
        self.assertEqual((shown['serial_number'], shown['imei']), ('F2LXK1NEW2', IMEI_B))
        self.assertEqual(shown['condition_label'], 'Nuevo')
        self.assertNotIn('cost', shown)
        self.assertNotIn('price_override', shown)


class TheWebOrderTest(_Base):
    def paid(self, quantity=1, product=None):
        order = self.paid_order(product=product, quantity=quantity)
        inventory.record_sale_stock_movements(order)                 # what the payment confirmation does
        return order

    def identify(self, order, code, method=SCANNER):
        return picking.identify_for_order(order=order, code=code, method=method, actor=self.manager)

    def refused(self, order, code, method=SCANNER):
        with self.assertRaises(picking.UnitIdentificationError) as refused:
            self.identify(order, code, method)
        return refused.exception.code

    def test_paying_sets_a_device_aside_and_names_none(self):
        order = self.paid()

        self.assertEqual(self.quantity(), 1)                         # the shelf already counts one less
        self.assert_invariant()
        self.assertEqual(OrderUnit.objects.filter(order=order).count(), 0)
        self.assertEqual(sold_units(order), {})                      # no paper names a device nobody identified
        self.assertEqual(picking.pending_units(order), {self.phone.pk: 1})

    def test_handing_over_the_one_set_aside(self):
        order = self.paid()
        self.assertEqual(self.status(self.old), ('sold', order.pk))  # the oldest was set aside

        record = self.identify(order, IMEI_A)

        self.assertEqual((record.unit, record.method, record.identified_by), (self.old, SCANNER, self.manager))
        self.assertEqual(sold_units(order), {self.phone.pk: [{'serial_number': 'F2LXK1OLD1', 'imei': IMEI_A, 'imei2': ''}]})
        self.assertEqual(picking.pending_units(order), {})
        self.assertEqual(StockMovement.objects.filter(product=self.phone).count(), 2)       # reception + sale: nothing else moved

    def test_handing_over_another_one_swaps_them(self):
        order = self.paid()
        before = StockMovement.objects.count()

        record = self.identify(order, IMEI_B, MANUAL)

        self.assertEqual(record.unit, self.new)
        self.assertEqual(self.status(self.new), ('sold', order.pk))
        self.assertEqual(self.status(self.old), ('available', None))  # back on the shelf, for the next sale
        self.old.refresh_from_db()
        self.assertIsNone(self.old.sold_at)
        self.assertEqual(self.quantity(), 1)                          # one came back, one left
        self.assert_invariant()
        swap = list(StockMovement.objects.order_by('pk')[before:])
        self.assertEqual(len(swap), 2)                                # and the Kardex says so, both ways
        self.assertEqual(sorted(m.metadata['unit_ids'][0] for m in swap), sorted([self.old.pk, self.new.pk]))
        self.assertTrue(all(m.metadata.get('swap_for_order') == order.pk for m in swap))
        self.assertEqual(StockMovement.objects.filter(order=order, movement_type=StockMovement.SALE_EXIT).count(), 1)
        self.assertEqual(sold_units(order)[self.phone.pk][0]['imei'], IMEI_B)
        entry = AdminAuditLog.objects.filter(action='order_unit_identified').get()
        self.assertEqual((entry.actor, entry.metadata['method'], entry.metadata['swapped_from']),
                         (self.manager, MANUAL, self.old.pk))

    def test_only_what_the_order_is_waiting_for(self):
        order = self.paid()
        third, = self.receive(self.row('F2LXK1THRD3', IMEI_D))         # so that NEW is not the last one on the shelf
        laptop, = self.receive(self.row('C02LAPTOP01'), product=self.laptop)
        elsewhere, = self.receive(self.row('F2LXK1NORTE', IMEI_C), branch=self.branch_b)

        self.assertEqual(self.refused(order, 'C02LAPTOP01'), 'wrong_product')
        self.assertEqual(self.refused(order, IMEI_C), 'wrong_branch')
        self.assertEqual(self.refused(order, 'NOEXISTE01'), 'not_found')
        self.identify(order, IMEI_A)
        self.assertEqual(self.refused(order, IMEI_A), 'already_identified')
        self.assertEqual(self.refused(order, IMEI_B), 'complete')    # one phone ordered, one identified
        self.assertEqual(self.status(self.new), ('available', None))

    def test_two_orders_waiting_may_take_each_others_box(self):
        """
        Set aside is not identified: which box the payment picked for which
        order is nobody's business. Whoever hands one over takes any of them.
        """
        mine, theirs = self.paid(), self.paid()                       # OLD set aside for mine, NEW for theirs
        self.assertEqual((self.status(self.old), self.status(self.new)), (('sold', mine.pk), ('sold', theirs.pk)))
        before = StockMovement.objects.count()

        record = self.identify(mine, IMEI_B)                          # the box the system had put with the other order

        self.assertEqual(record.unit, self.new)
        self.assertEqual((self.status(self.new), self.status(self.old)), (('sold', mine.pk), ('sold', theirs.pk)))
        self.assertEqual(StockMovement.objects.count(), before)       # nothing went back to the shelf: no Kardex line
        self.assert_invariant()
        self.assertEqual(picking.pending_units(theirs), {self.phone.pk: 1})
        self.identify(theirs, IMEI_A)                                  # and the other order is handed the other box
        self.assertEqual(picking.pending_units(theirs), {})
        logged = AdminAuditLog.objects.filter(action='order_unit_identified', target_id=mine.pk).first()
        self.assertEqual(logged.metadata.get('exchanged_with_order'), theirs.pk)

    def test_a_device_somebody_identified_for_another_order_is_not_taken_from_it(self):
        mine, theirs = self.paid(), self.paid()
        self.identify(theirs, IMEI_B)
        self.assertEqual(self.refused(mine, IMEI_B), 'other_order')
        self.assertEqual(self.status(self.new), ('sold', theirs.pk))
        # Nor one sold at the till.
        third, = self.receive(self.row('F2LXK1THRD3', IMEI_C))
        self.sell([self.line(third)])
        self.assertEqual(self.refused(mine, IMEI_C), 'other_order')

    def test_the_box_of_an_order_that_is_no_longer_waiting_is_not_up_for_grabs(self):
        """Cancelled with its device still out: that device comes back through a return, not through another order."""
        mine, theirs = self.paid(), self.paid()
        Order.objects.filter(pk=theirs.pk).update(fulfillment_status=Order.FulfillmentStatus.CANCELLED)
        self.assertEqual(self.refused(mine, IMEI_B), 'other_order')
        self.assertEqual(self.status(self.new), ('sold', theirs.pk))

    def test_the_order_is_locked_while_it_is_decided_that_it_may_leave(self):
        """The lock identifying and taking back a device hold: nobody un-names one between the check and the write."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        order = self.paid()
        self.identify(order, IMEI_A)
        # ORDER-FULFILLMENT-SAFETY-01: from the step before, so the capture
        # holds the hand-over alone, and nothing read before the lock.
        place_at(order, Order.FulfillmentStatus.READY_FOR_PICKUP)
        with CaptureQueriesContext(connection) as seen:
            fulfillment.change_fulfillment_status(order=order, new_status='delivered', actor=self.manager, company=self.company)
        statements = [q['sql'] for q in seen.captured_queries]
        lock = next(i for i, sql in enumerate(statements) if 'FOR UPDATE' in sql and '"store_order"' in sql.split('WHERE')[0])
        check = next(i for i, sql in enumerate(statements) if '"store_orderunit"' in sql)
        write = next(i for i, sql in enumerate(statements) if sql.startswith('UPDATE "store_order"'))
        self.assertLess(lock, check)
        self.assertLess(check, write)

    def test_a_wrong_one_can_be_taken_back_before_it_leaves(self):
        order = self.paid()
        record = self.identify(order, IMEI_B)

        picking.forget(order=order, order_unit_id=record.pk, actor=self.manager)

        self.assertEqual(OrderUnit.objects.filter(order=order).count(), 0)
        self.assertEqual(self.status(self.new), ('sold', order.pk))   # still set aside for this order, just not named
        self.assertEqual(picking.pending_units(order), {self.phone.pk: 1})
        self.assertTrue(AdminAuditLog.objects.filter(action='order_unit_forgotten').exists())

    def deliver(self, order, state='delivered'):
        # ORDER-FULFILLMENT-SAFETY-01: one step at a time, through the real
        # service — a refusal stops the walk on the step that was refused.
        return walk_to(order, state, actor=self.manager, company=self.company)

    def test_what_left_the_shop_is_not_renamed(self):
        order = self.paid()
        record = self.identify(order, IMEI_A)
        self.assertIsNone(record.left_at)
        self.deliver(order)
        record.refresh_from_db()
        self.assertIsNotNone(record.left_at)
        with self.assertRaises(picking.UnitIdentificationError) as refused:
            picking.forget(order=order, order_unit_id=record.pk, actor=self.manager)
        self.assertEqual(refused.exception.code, 'already_left')

    def test_moving_the_order_back_does_not_reopen_what_left(self):
        """«Delivered» then «preparing» is a status, not a time machine: the device that left is the one that left."""
        for order in (self.sell([self.line(self.old)])[0], self.paid()):
            if not OrderUnit.objects.filter(order=order).exists():
                self.identify(order, IMEI_B)
                self.deliver(order)
            record = OrderUnit.objects.get(order=order)
            # ORDER-FULFILLMENT-SAFETY-01: dispatch no longer goes back once the
            # goods left; such a move back is a past the old selector wrote.
            with self.assertRaises(fulfillment.FulfillmentBlocked) as blocked:
                fulfillment.change_fulfillment_status(order=order, new_status='preparing', actor=self.manager,
                                                      company=self.company, note='Vuelve a preparación')
            self.assertEqual(blocked.exception.code, 'transition_not_allowed')
            legacy_move(order, 'preparing', actor=self.manager, company=self.company)
            with self.assertRaises(picking.UnitIdentificationError) as refused:
                picking.forget(order=order, order_unit_id=record.pk, actor=self.manager)
            self.assertEqual(refused.exception.code, 'already_left')
            self.assertEqual(picking.pending_units(order), {})
            self.assertEqual(len(sold_units(order)[self.phone.pk]), 1)
            self.deliver(order)                                        # and it can be put back as delivered

    def test_it_does_not_leave_the_shop_without_being_identified(self):
        order = self.paid(quantity=2)
        # ORDER-FULFILLMENT-SAFETY-01: each is tried from the step right before
        # it, on a path that has it (only a shipping order is «sent»).
        for status, method, before in (('shipped', Order.DeliveryMethod.DELIVERY_AREQUIPA, 'preparing'),
                                       ('delivered', Order.DeliveryMethod.PICKUP_STORE, 'ready_for_pickup')):
            Order.objects.filter(pk=order.pk).update(delivery_method=method)
            place_at(order, before)
            with self.subTest(status), self.assertRaises(fulfillment.FulfillmentBlocked) as blocked:
                fulfillment.change_fulfillment_status(order=order, new_status=status, actor=self.manager, company=self.company)
            self.assertEqual(blocked.exception.code, 'units_pending')
            self.assertIn('2', blocked.exception.detail)
            order.refresh_from_db()
            self.assertEqual(order.fulfillment_status, before)
        place_at(order, 'pending')

        # Getting it ready is not handing it over. («Confirmado» is no longer a
        # step: ORDER-FULFILLMENT-SAFETY-01.)
        for status in ('preparing', 'ready_for_pickup'):
            fulfillment.change_fulfillment_status(order=order, new_status=status, actor=self.manager, company=self.company)

        self.identify(order, IMEI_A)
        with self.assertRaises(fulfillment.FulfillmentBlocked) as blocked:            # one of two is not both
            fulfillment.change_fulfillment_status(order=order, new_status='delivered', actor=self.manager, company=self.company)
        self.assertEqual(blocked.exception.code, 'units_pending')
        self.identify(order, IMEI_B)
        fulfillment.change_fulfillment_status(order=order, new_status='delivered', actor=self.manager, company=self.company)
        order.refresh_from_db()
        self.assertEqual(order.fulfillment_status, 'delivered')

    def test_an_order_without_devices_is_handed_over_as_always(self):
        order = Order.objects.create(
            company=self.company, fulfillment_branch=self.branch_a, customer_email='x@example.com',
            total=Decimal('49.00'), status=Order.Status.PAID, paid=True, paid_at=timezone.now())
        OrderItem.objects.create(order=order, product=self.cable, quantity=1, price=self.cable.price)
        self.deliver(order)
        self.assertEqual(picking.pending_units(order), {})

    def test_an_order_from_before_the_rule_is_not_held_back(self):
        """A sale recorded as `legacy` when the rule arrived counts as identified: it is history."""
        order = self.paid()
        OrderUnit.objects.create(company=self.company, order=order, product=self.phone, unit=self.old, method='legacy')
        self.assertEqual(picking.pending_units(order), {})
        self.deliver(order)

    def test_a_device_returned_later_is_still_the_one_that_sale_named(self):
        from store import stock_unit_services as units

        order = self.paid()
        self.identify(order, IMEI_A)
        self.deliver(order)
        units.return_unit(self.old, reason='Devolución del cliente', actor=self.staff)

        self.assertEqual(self.status(self.old), ('available', None))
        self.assertEqual(sold_units(order)[self.phone.pk][0]['imei'], IMEI_A)        # the paper of that sale does not change
        self.assertEqual(picking.pending_units(order), {})

    def test_a_device_returned_before_it_left_is_no_longer_the_one_of_that_order(self):
        """
        It went back to the shelf before anybody handed it over: the order names
        nothing again, cannot leave, and no paper says a device on the shelf was sold.
        """
        from store import stock_unit_services as units

        order = self.paid()
        self.identify(order, IMEI_A)
        units.return_unit(self.old, reason='El cliente pidió otro color', actor=self.staff)

        self.assertEqual(self.status(self.old), ('available', None))
        self.assertFalse(OrderUnit.objects.filter(order=order).exists())
        self.assertEqual(sold_units(order), {})
        self.assertEqual(picking.pending_units(order), {self.phone.pk: 1})
        with self.assertRaises(fulfillment.FulfillmentBlocked) as blocked:
            self.deliver(order)
        self.assertEqual(blocked.exception.code, 'units_pending')
        self.assertTrue(AdminAuditLog.objects.filter(action='order_unit_forgotten', target_id=order.pk).exists())

    def test_a_device_that_is_not_out_for_the_order_does_not_count_as_identified(self):
        """Whatever put it back on the shelf: a record is not a device in hand."""
        order = self.paid()
        self.identify(order, IMEI_A)
        StockUnit.objects.filter(pk=self.old.pk).update(status='available', order=None)
        self.assertEqual(picking.pending_units(order), {self.phone.pk: 1})
        with self.assertRaises(fulfillment.FulfillmentBlocked) as blocked:
            self.deliver(order)
        self.assertEqual(blocked.exception.code, 'units_pending')

    def test_an_order_whose_device_went_back_is_handed_another_one(self):
        """Not a dead end: the return put one back, the hand-over takes one out — one Kardex line."""
        from store import stock_unit_services as units

        for identified_first in (True, False):
            with self.subTest(identified_first=identified_first):
                self.receive(
                    self.row(f'F2LXK1RET{identified_first:d}A'), self.row(f'F2LXK1RET{identified_first:d}B'), product=self.laptop)
                order = self.paid(product=self.laptop)
                first = StockUnit.objects.get(order=order)             # the one the payment set aside
                second = StockUnit.objects.filter(product=self.laptop, status='available').order_by('-pk').first()
                if identified_first:
                    self.identify(order, first.serial_number)
                units.return_unit(first, reason='Volvió al estante', actor=self.staff)
                self.assertEqual(picking.pending_units(order), {self.laptop.pk: 1})
                before = StockMovement.objects.filter(product=self.laptop).count()

                record = self.identify(order, second.serial_number)

                self.assertEqual(record.unit, second)
                self.assertEqual(self.status(second), ('sold', order.pk))
                exits = StockMovement.objects.filter(product=self.laptop).order_by('pk')[before:]
                self.assertEqual([m.movement_type for m in exits], [StockMovement.MANUAL_EXIT])
                self.assert_invariant(product=self.laptop)
                self.deliver(order)

    def test_an_order_paid_before_its_product_had_serials_owes_no_device(self):
        """It left the stock as a count. A serial the product got later is not something that order can name."""
        from store import stock_unit_services as units

        watch = _prod(self.company, 'Reloj', 'reloj-serial-pick', price='900.00', inventory=1)
        order = self.paid(product=watch)                               # a plain exit: stock is now zero
        units.set_serialized(watch, serialized=True, actor=self.staff)
        self.receive(self.row('RELOJ00001'), product=Product.objects.get(pk=watch.pk))

        order = Order.objects.get(pk=order.pk)
        self.assertEqual(picking.pending_units(order), {})
        self.assertEqual(self.refused(order, 'RELOJ00001'), 'wrong_product')
        self.deliver(order)

    def test_an_unpaid_order_is_told_so(self):
        order = self.paid_order()
        Order.objects.filter(pk=order.pk).update(status=Order.Status.PENDING_PAYMENT, paid=False)
        self.assertEqual(self.refused(Order.objects.get(pk=order.pk), IMEI_A), 'not_paid')

    def test_the_shelf_is_locked_before_the_devices(self):
        """The module's one lock order (shelf, then units): a hand-over and a sale of the same model cannot deadlock."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        order = self.paid()
        with CaptureQueriesContext(connection) as seen:
            self.identify(order, IMEI_B)
        locks = [q['sql'] for q in seen.captured_queries if 'FOR UPDATE' in q['sql']]
        tables = ['order' if '"store_order"' in sql.split('FROM')[1].split('WHERE')[0] else
                  'shelf' if '"store_branchstock"' in sql.split('FROM')[1].split('WHERE')[0] else
                  'unit' if '"store_stockunit"' in sql.split('FROM')[1].split('WHERE')[0] else 'other' for sql in locks]
        self.assertEqual(tables[0], 'order')
        self.assertLess(tables.index('shelf'), tables.index('unit'), tables)
        # A lock on a device is a lock on that device, not on its product and branch.
        for sql in locks:
            if '"store_stockunit"' in sql.split('FROM')[1].split('WHERE')[0]:
                self.assertTrue(' JOIN ' not in sql or 'FOR UPDATE OF' in sql, sql)

    def test_a_serial_of_fifteen_digits_is_found_as_a_serial(self):
        laptop, = self.receive(self.row('123456789012345'), product=self.laptop)
        found = picking.sellable_unit(company=self.company, branch=self.branch_a, code='123456789012345', method=SCANNER)
        self.assertEqual(found, laptop)
        # Typed by hand too: this product has no IMEI to ask for.
        self.assertEqual(
            picking.sellable_unit(company=self.company, branch=self.branch_a, code='123456789012345', method=MANUAL), laptop)
        # And fifteen digits that are nobody's serial are still told what is wrong with them as an IMEI.
        with self.assertRaises(picking.UnitIdentificationError) as refused:
            picking.sellable_unit(company=self.company, branch=self.branch_a, code='123456789012346', method=SCANNER)
        self.assertEqual(refused.exception.code, 'invalid_code')

    def test_a_refusal_does_not_say_where_else_a_device_is(self):
        """A till is told nothing about other branches (`other_branches_with_stock`); neither is this."""
        order = self.paid()
        self.receive(self.row('F2LXK1NORTE', IMEI_C), branch=self.branch_b)
        self.receive(self.row('C02NORTE001'), product=self.laptop, branch=self.branch_b)
        for attempt in (
            lambda: picking.sellable_unit(company=self.company, branch=self.branch_a, code=IMEI_C, method=SCANNER),
            lambda: self.identify(order, IMEI_C),
            lambda: self.identify(order, 'C02NORTE001'),               # another product, in another branch
        ):
            with self.assertRaises(picking.UnitIdentificationError) as refused:
                attempt()
            self.assertEqual(refused.exception.code, 'wrong_branch')
            self.assertNotIn(self.branch_b.name, refused.exception.detail)
            self.assertNotIn(self.laptop.name, refused.exception.detail)

    def test_an_order_whose_stock_never_left_is_reprocessed_first(self):
        StockUnit.objects.filter(product=self.phone).update(status='written_off')
        from store.models import BranchStock
        BranchStock.objects.filter(product=self.phone).update(quantity=0)
        order = self.paid()                                           # nothing to set aside: flagged as a shortfall
        self.assertFalse(StockMovement.objects.filter(order=order).exists())
        StockUnit.objects.filter(pk=self.new.pk).update(status='available')
        BranchStock.objects.filter(product=self.phone, branch=self.branch_a).update(quantity=1)
        self.assertEqual(self.refused(order, IMEI_B), 'needs_stock_exit')


class WhenTheRuleArrivedTest(_Base):
    """Migration 0116: what was sold before the rule, and what was only paid for."""

    def arrive(self):
        from importlib import import_module

        from django.apps import apps

        import_module('store.migrations.0116_serial_pick').record_what_was_already_sold(apps, None)

    def sold_the_old_way(self, *, state):
        order = self.paid_order()
        inventory.record_sale_stock_movements(order)
        Order.objects.filter(pk=order.pk).update(fulfillment_status=state)
        return order

    def test_a_sale_already_handed_over_keeps_the_device_its_paper_named(self):
        order = self.sold_the_old_way(state='delivered')
        self.assertEqual(sold_units(order), {})
        self.arrive()
        [row] = OrderUnit.objects.filter(order=order)
        self.assertEqual((row.unit_id, row.method, row.identified_by_id), (self.old.pk, 'legacy', None))
        self.assertIsNotNone(row.left_at)                               # history: not to be taken back
        self.assertEqual([unit['imei'] for unit in sold_units(order)[self.phone.pk]], [IMEI_A])
        self.arrive()                                                   # run again: nothing twice
        self.assertEqual(OrderUnit.objects.filter(order=order).count(), 1)
        self.assertEqual(OrderUnit.objects.filter(order=order).count(), 1)

    def test_an_order_paid_and_not_handed_over_still_has_to_be_identified(self):
        """Nobody has handed that device over: the rule applies to it like to any other."""
        self.receive(self.row('F2LXK1MOR3', IMEI_C), self.row('F2LXK1MOR4', IMEI_D))
        waiting = [self.sold_the_old_way(state=state) for state in ('pending', 'confirmed', 'preparing', 'ready_for_pickup')]
        self.arrive()
        self.assertFalse(OrderUnit.objects.exists())
        order = waiting[0]
        self.assertEqual(picking.pending_units(order), {self.phone.pk: 1})
        self.assertEqual(self.status(self.old), (StockUnit.Status.SOLD, order.pk))   # still set aside
        place_at(order, 'ready_for_pickup')                  # the step before the hand-over (ORDER-FULFILLMENT-SAFETY-01)
        with self.assertRaises(fulfillment.FulfillmentBlocked) as blocked:
            fulfillment.change_fulfillment_status(
                order=order, new_status='delivered', actor=self.manager, company=self.company)
        self.assertEqual(blocked.exception.code, 'units_pending')

    def test_what_was_cancelled_after_the_fact_is_history_too(self):
        order = self.sold_the_old_way(state='cancelled')
        self.arrive()
        self.assertEqual([row.method for row in OrderUnit.objects.filter(order=order)], ['legacy'])


class OverHttpTest(_Base):
    """The same rules, as the panel and the till reach them."""

    def setUp(self):
        super().setUp()
        from rest_framework.test import APIClient

        self.till = APIClient()
        self.till.force_authenticate(self.cashier)
        self.desk = APIClient()
        self.desk.force_authenticate(self.manager)
        self.q = f'company={self.company.pk}'

    def lookup(self, code, method=SCANNER, client=None, branch=None):
        return (client or self.till).get(
            f'/api/admin/pos/units/lookup/?{self.q}&branch={(branch or self.branch_a).pk}&{urlencode({"code": code, "method": method})}')

    def sale(self, items, **extra):
        return self.till.post(f'/api/admin/pos/sales/?{self.q}', {
            'branch': self.branch_a.pk, 'items': items, 'payment_method': 'cash', 'amount_received': '10000.00',
            'terms_confirmed': True, 'idempotency_key': f'http-serial-{Order.objects.count():04d}',
            **extra}, format='json')

    # --- the till

    def test_the_till_is_told_which_products_need_a_device(self):
        found = self.till.get(f'/api/admin/pos/products/search/?{self.q}&branch={self.branch_a.pk}&q=iPhone').json()
        phone = next(row for row in found['results'] if row['id'] == self.phone.pk)
        self.assertTrue(phone['is_serialized'])
        self.assertTrue(phone['requires_imei'])
        cable = self.till.get(f'/api/admin/pos/products/search/?{self.q}&branch={self.branch_a.pk}&q=Cable').json()
        self.assertFalse(cable['results'][0]['is_serialized'])

    def test_a_scanned_device_comes_back_with_what_it_is(self):
        response = self.lookup(IMEI_B)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body['unit']['unit_id'], self.new.pk)
        self.assertEqual((body['unit']['product_name'], body['unit']['imei'], body['unit']['serial_number'], body['unit']['condition_label']),
                         ('iPhone 16', IMEI_B, 'F2LXK1NEW2', 'Nuevo'))
        self.assertEqual(body['product']['id'], self.phone.pk)
        self.assertEqual(body['product']['price'], '4500.00')
        self.assertEqual(body['method'], SCANNER)

    def test_what_cannot_be_sold_is_refused_with_its_reason(self):
        elsewhere, = self.receive(self.row('F2LXK1NORTE', IMEI_C), branch=self.branch_b)
        wrong_digit = IMEI_B[:-1] + str((int(IMEI_B[-1]) + 1) % 10)
        for code, method, status, reason in (
            ('NOEXISTE01', SCANNER, 404, 'not_found'),
            (wrong_digit, MANUAL, 400, 'invalid_code'),
            ('F2LXK1NEW2', MANUAL, 400, 'manual_needs_imei'),
            (IMEI_C, SCANNER, 409, 'wrong_branch'),
            (IMEI_B, 'telepatía', 400, 'invalid_method'),
        ):
            with self.subTest(code=code, method=method):
                response = self.lookup(code, method)
                self.assertEqual((response.status_code, response.json()['code']), (status, reason), response.content)

    def test_only_who_sells_looks_a_device_up_and_only_in_their_company(self):
        self.assertEqual(self.lookup(IMEI_B, client=self.desk).status_code, 403)
        foreign = StockUnit.objects.create(company=self.other, branch=self.foreign_branch, product=self.foreign_phone,
                                           serial_number='AJENO00001', imei=IMEI_D)
        response = self.lookup(IMEI_D)
        self.assertEqual((response.status_code, response.json()['code']), (404, 'not_found'))
        self.assertNotIn('AJENO00001', response.content.decode())
        self.assertEqual(foreign.status, 'available')

    def test_the_sale_needs_its_device_and_says_which_it_sold(self):
        refused = self.sale([{'product': self.phone.pk, 'quantity': 1}])
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertIn('Identifica el equipo', refused.json()['detail'])
        self.assertEqual(self.available(), 2)

        sold = self.sale([{'product': self.phone.pk, 'quantity': 1, 'units': [{'id': self.new.pk, 'method': MANUAL}]}])
        self.assertEqual(sold.status_code, 201, sold.content)
        line = sold.json()['items'][0]
        self.assertEqual(line['units'], [{'serial_number': 'F2LXK1NEW2', 'imei': IMEI_B, 'imei2': '', 'method': MANUAL}])
        self.assertEqual(self.status(self.old), ('available', None))

    def test_the_preview_prices_a_basket_whose_device_is_still_to_be_scanned(self):
        response = self.till.post(f'/api/admin/pos/preview/?{self.q}', {
            'branch': self.branch_a.pk, 'items': [{'product': self.phone.pk, 'quantity': 1}], 'payment_method': 'cash'}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['lines'][0]['price'], '4500.00')

    # --- the web order

    def web_order(self, quantity=1):
        order = self.paid_order(quantity=quantity)
        inventory.record_sale_stock_movements(order)
        return order

    def test_the_panel_sees_what_an_order_is_waiting_for(self):
        order = self.web_order()
        body = self.desk.get(f'/api/admin/orders/{order.pk}/?{self.q}').json()
        item = body['items'][0]
        self.assertTrue(item['is_serialized'])
        self.assertEqual((item['units'], item['units_pending']), ([], 1))
        self.assertEqual(body['units_pending'], 1)

    def test_the_panel_is_told_whether_this_person_may_say_which_device(self):
        from rest_framework.test import APIClient

        order = self.web_order()
        url = f'/api/admin/orders/{order.pk}/?{self.q}'
        self.assertTrue(self.desk.get(url).json()['units_can_change'])

        reader, _ = _p2d_member(self.company, 'lectura_serial', ['company.view', 'sales.orders.view'])
        looking = APIClient()
        looking.force_authenticate(reader)
        self.assertFalse(looking.get(url).json()['units_can_change'])

        picking.identify_for_order(order=order, code=IMEI_A, method=SCANNER, actor=self.manager)
        walk_to(order, 'delivered', actor=self.manager, company=self.company)
        self.assertFalse(self.desk.get(url).json()['units_can_change'])

    def test_identifying_then_handing_over(self):
        order = self.web_order()
        place_at(order, 'ready_for_pickup')                  # the step before the hand-over (ORDER-FULFILLMENT-SAFETY-01)
        blocked = self.desk.patch(f'/api/admin/orders/{order.pk}/fulfillment-status/?{self.q}', {'fulfillment_status': 'delivered'}, format='json')
        self.assertEqual((blocked.status_code, blocked.json()['code']), (409, 'units_pending'), blocked.content)

        seen = self.desk.get(f'/api/admin/orders/{order.pk}/units/lookup/?{self.q}&code={IMEI_B}&method={MANUAL}')
        self.assertEqual(seen.status_code, 200, seen.content)
        self.assertEqual(seen.json()['unit']['product_name'], 'iPhone 16')            # shown to confirm, nothing written yet
        self.assertEqual(OrderUnit.objects.count(), 0)

        done = self.desk.post(f'/api/admin/orders/{order.pk}/units/?{self.q}', {'code': IMEI_B, 'method': MANUAL}, format='json')
        self.assertEqual(done.status_code, 201, done.content)
        item = done.json()['items'][0]
        self.assertEqual(item['units_pending'], 0)
        self.assertEqual((item['units'][0]['imei'], item['units'][0]['method'], item['units'][0]['identified_by']),
                         (IMEI_B, MANUAL, self.manager.username))
        self.assertEqual(done.json()['units_pending'], 0)

        delivered = self.desk.patch(f'/api/admin/orders/{order.pk}/fulfillment-status/?{self.q}', {'fulfillment_status': 'delivered'}, format='json')
        self.assertEqual(delivered.status_code, 200, delivered.content)

    def test_the_panel_is_told_what_each_line_needs_and_what_can_still_be_taken_back(self):
        order = self.web_order()
        url = f'/api/admin/orders/{order.pk}/?{self.q}'
        self.assertTrue(self.desk.get(url).json()['items'][0]['requires_imei'])
        self.desk.post(f'/api/admin/orders/{order.pk}/units/?{self.q}', {'code': IMEI_A, 'method': SCANNER}, format='json')
        self.assertTrue(self.desk.get(url).json()['items'][0]['units'][0]['can_remove'])
        # ORDER-FULFILLMENT-SAFETY-01: it leaves through the real service; the
        # move back after it left is a past only the old selector wrote.
        walk_to(order, 'delivered', actor=self.manager, company=self.company)
        legacy_move(order, 'preparing', actor=self.manager, company=self.company)
        body = self.desk.get(url).json()
        self.assertFalse(body['items'][0]['units'][0]['can_remove'])      # it left: moving the order back does not reopen it
        record_id = body['items'][0]['units'][0]['id']
        refused = self.desk.delete(f'/api/admin/orders/{order.pk}/units/{record_id}/?{self.q}')
        self.assertEqual((refused.status_code, refused.json()['code']), (409, 'already_left'))

    # --- the app (API v1): one rule, two surfaces

    def v1(self, path):
        return f'/api/v1/internal/{self.company.slug}/{path}'

    def test_the_app_identifies_and_hands_over_like_the_panel(self):
        order = self.web_order()
        detail = self.desk.get(self.v1(f'orders/{order.pk}/')).json()
        self.assertEqual((detail['units'], detail['units_pending'], detail['units_can_change']), ([], 1, True))
        self.assertEqual(detail['units_pending_by_product'], {str(self.phone.pk): 1})

        place_at(order, 'ready_for_pickup')                  # the step before the hand-over (ORDER-FULFILLMENT-SAFETY-01)
        blocked = self.desk.patch(self.v1(f'orders/{order.pk}/fulfillment/'), {'fulfillment_status': 'delivered'}, format='json')
        self.assertEqual((blocked.status_code, blocked.json()['code'], blocked.json()['pending']), (409, 'units_pending', 1))

        typo = self.desk.get(self.v1(f'orders/{order.pk}/units/lookup/') + f'?code={IMEI_B[:-1]}0&method={MANUAL}')
        self.assertEqual((typo.status_code, typo.json()['code']), (400, 'invalid_code'))
        seen = self.desk.get(self.v1(f'orders/{order.pk}/units/lookup/') + f'?code={IMEI_B}&method={MANUAL}')
        self.assertEqual((seen.status_code, seen.json()['unit']['imei']), (200, IMEI_B))
        self.assertEqual(OrderUnit.objects.count(), 0)

        done = self.desk.post(self.v1(f'orders/{order.pk}/units/'), {'code': IMEI_B, 'method': MANUAL}, format='json')
        self.assertEqual(done.status_code, 201, done.content)
        [named] = done.json()['units']
        self.assertEqual((named['imei'], named['method'], named['identified_by'], named['can_remove']),
                         (IMEI_B, MANUAL, self.manager.username, True))
        self.assertEqual(done.json()['units_pending'], 0)

        gone = self.desk.delete(self.v1(f'orders/{order.pk}/units/{named["id"]}/'))
        self.assertEqual((gone.status_code, gone.json()['units_pending']), (200, 1))
        self.desk.post(self.v1(f'orders/{order.pk}/units/'), {'code': IMEI_A, 'method': SCANNER}, format='json')
        delivered = self.desk.patch(self.v1(f'orders/{order.pk}/fulfillment/'), {'fulfillment_status': 'delivered'}, format='json')
        self.assertEqual(delivered.status_code, 200, delivered.content)
        self.assertEqual((delivered.json()['units_can_change'], delivered.json()['units'][0]['can_remove']), (False, False))

    def test_in_the_app_too_only_who_manages_orders_and_only_their_own(self):
        from rest_framework.test import APIClient

        order = self.web_order()
        for path, call in (
            (f'orders/{order.pk}/units/', lambda c, u: c.post(u, {'code': IMEI_A, 'method': SCANNER}, format='json')),
            (f'orders/{order.pk}/units/lookup/', lambda c, u: c.get(u + f'?code={IMEI_A}&method={SCANNER}')),
            (f'orders/{order.pk}/units/1/', lambda c, u: c.delete(u)),
        ):
            self.assertEqual(call(self.till, self.v1(path)).status_code, 403, path)        # sells, does not manage orders
            outsider, _ = _p2d_member(self.other, f'ajeno_{len(path)}', ['company.view', 'sales.orders.view', 'sales.orders.manage'])
            foreign = APIClient()
            foreign.force_authenticate(outsider)
            self.assertIn(call(foreign, self.v1(path)).status_code, (403, 404), path)
            self.assertEqual(
                call(foreign, f'/api/v1/internal/{self.other.slug}/{path}').status_code, 404, path)   # their company: not their order
        self.assertEqual(OrderUnit.objects.count(), 0)

    def test_the_app_till_sells_a_device_it_looked_up(self):
        lookup = self.till.get(self.v1('sales/pos/units/lookup/') + f'?branch={self.branch_a.pk}&code={IMEI_B}&method={SCANNER}')
        self.assertEqual((lookup.status_code, lookup.json()['unit']['unit_id']), (200, self.new.pk))
        base = {'branch': self.branch_a.pk, 'payment_method': 'cash', 'amount_received': '10000.00',
                'terms_confirmed': True}
        without = self.till.post(self.v1('sales/pos/sales/'), {
            **base, 'items': [{'product': self.phone.pk, 'quantity': 1}], 'idempotency_key': 'v1-serial-0001'}, format='json')
        self.assertEqual(without.status_code, 400, without.content)
        sold = self.till.post(self.v1('sales/pos/sales/'), {
            **base, 'items': [self.line(self.new)], 'idempotency_key': 'v1-serial-0002'}, format='json')
        self.assertEqual(sold.status_code, 201, sold.content)
        self.assertEqual(OrderUnit.objects.get().unit, self.new)

    def test_a_wrong_one_is_refused_with_its_reason_and_can_be_taken_back(self):
        order = self.web_order()
        missing = self.desk.post(f'/api/admin/orders/{order.pk}/units/?{self.q}', {'code': 'NOEXISTE01', 'method': SCANNER}, format='json')
        self.assertEqual((missing.status_code, missing.json()['code']), (404, 'not_found'))
        done = self.desk.post(f'/api/admin/orders/{order.pk}/units/?{self.q}', {'code': IMEI_A, 'method': SCANNER}, format='json').json()
        record_id = done['items'][0]['units'][0]['id']
        again = self.desk.post(f'/api/admin/orders/{order.pk}/units/?{self.q}', {'code': IMEI_A, 'method': SCANNER}, format='json')
        self.assertEqual((again.status_code, again.json()['code']), (409, 'already_identified'))

        gone = self.desk.delete(f'/api/admin/orders/{order.pk}/units/{record_id}/?{self.q}')
        self.assertEqual(gone.status_code, 200, gone.content)
        self.assertEqual(gone.json()['units_pending'], 1)

    def test_only_who_manages_orders_and_only_their_own(self):
        order = self.web_order()
        url = f'/api/admin/orders/{order.pk}/units/?{self.q}'
        self.assertEqual(self.till.post(url, {'code': IMEI_A, 'method': SCANNER}, format='json').status_code, 403)

        from rest_framework.test import APIClient
        outsider, _ = _p2d_member(self.other, 'pedidos_ajeno', ['company.view', 'sales.orders.manage'])
        other = APIClient()
        other.force_authenticate(outsider)
        for query in (f'company={self.other.pk}', self.q):
            response = other.post(f'/api/admin/orders/{order.pk}/units/?{query}', {'code': IMEI_A, 'method': SCANNER}, format='json')
            self.assertIn(response.status_code, (403, 404))
        self.assertEqual(OrderUnit.objects.count(), 0)
