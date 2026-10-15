"""
ORDER-CANCEL-01 × DEVICE-COMMERCE-01 — what a cancellation does to costs,
on-order lines and devices sold one by one.

  * The stock that comes back carries the cost its sale exit left with
    (`cost_services`: «back from an order's sale»): a cancel round trip leaves
    the shelf's average and its «costo pendiente» part where they were.
    A device comes back at its own cost.
  * An on-order line (DC3) that was committed against a supplier's offer gives
    the offer back what it committed — once, audited — and becomes «No se pudo
    abastecer»; a line «por confirmar» committed nothing and only changes state.
    Goods already received come back to the shelf like any sale.
  * A line sold as an exact device (DC2) returns THAT device.
"""
from decimal import Decimal as D

from django.test import TestCase

from store import inventory_services as inventory
from store import order_cancellation as cancelling
from store import supply_services as supply
from store import unit_hold_services as holds
from store.models import AdminAuditLog, BranchStock, CartItem, OrderItem, StockMovement, StockUnit, SupplierOffer
from store.test_device_commerce import SaleShop
from store.test_supplier_offers import SupplyShop

REASON = 'El cliente desistió de la compra'


class _CostShop(SupplyShop):
    def setUp(self):
        self.build('dc-cancel')
        self.stock(self.case, 10)                       # 10 at 60.0000

    def row(self):
        return BranchStock.objects.get(branch=self.branch, product=self.case)

    def shelf_order(self, quantity=3):
        CartItem.objects.create(session_key='carrito-a', product=self.case, quantity=quantity)
        order = self.checkout()
        self.assertEqual(self.pay(order), 'paid')
        return order


class CancelCostContinuityTest(_CostShop, TestCase):
    def test_a_cancel_round_trip_leaves_the_average_and_the_pending_part_untouched(self):
        before = self.row()
        order = self.shelf_order(3)

        cancelling.cancel_paid_order(order, self.staff, REASON)

        after = self.row()
        self.assertEqual((after.quantity, after.average_cost, after.uncosted_quantity),
                         (before.quantity, before.average_cost, before.uncosted_quantity))
        back = StockMovement.objects.get(order=order, movement_type=StockMovement.ORDER_CANCEL_RETURN)
        sale = StockMovement.objects.get(order=order, movement_type=StockMovement.SALE_EXIT)
        self.assertEqual((back.unit_cost, back.cost_source), (sale.unit_cost, sale.cost_source))
        self.assertEqual(back.unit_cost, D('60.0000'))
        self.assertEqual(back.metadata['cost_from_movement_ids'], [sale.pk])

    def test_goods_back_after_a_dearer_purchase_value_the_shelf_as_if_the_sale_never_happened(self):
        order = self.shelf_order(3)                      # 7 left at 60
        self.stock(self.case, 10)                        # + 10 at 60 (the helper's cost)
        inventory.create_stock_movement(
            branch=self.branch, product_id=self.case.pk, movement_type=StockMovement.PURCHASE_ENTRY,
            quantity=3, reason='Compra cara', unit_cost=D('100'))   # 20 at (17×60 + 3×100)/20 = 66

        cancelling.cancel_paid_order(order, self.staff, REASON)

        # 23 units: 20 at 66 + 3 back at the 60 they left with = (1320 + 180) / 23.
        after = self.row()
        self.assertEqual((after.quantity, after.uncosted_quantity), (23, 0))
        self.assertEqual(after.average_cost, D('65.2174'))

    def test_a_sale_of_pending_cost_comes_back_pending_never_as_known(self):
        BranchStock.objects.filter(branch=self.branch, product=self.case).update(uncosted_quantity=10,
                                                                                 average_cost=None)
        order = self.shelf_order(2)
        self.assertEqual(self.row().uncosted_quantity, 8)

        cancelling.cancel_paid_order(order, self.staff, REASON)

        after = self.row()
        self.assertEqual((after.quantity, after.uncosted_quantity, after.average_cost), (10, 10, None))


class CancelOnOrderLineTest(_CostShop, TestCase):
    def on_order(self, quantity=2):
        self.add_on_order(quantity=quantity)
        order = self.checkout()
        self.assertEqual(self.pay(order), 'paid')
        return order

    def test_a_committed_line_gives_the_offer_back_what_it_committed_once_and_audited(self):
        order = self.on_order(2)
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.committed_quantity, 2)

        cancelling.cancel_paid_order(order, self.staff, REASON)

        self.offer.refresh_from_db()
        self.assertEqual(self.offer.committed_quantity, 0)
        [line] = order.items.all()
        self.assertEqual(line.supply_status, OrderItem.SUPPLY_FAILED)
        self.assertIn(REASON, line.supply_note)
        audit = AdminAuditLog.objects.get(action='order_supply_failed', target_id=str(order.pk))
        self.assertEqual((audit.metadata['released'], audit.metadata['source']), (2, 'order_cancellation'))
        cancelled = AdminAuditLog.objects.get(action='order_cancelled', target_id=str(order.pk))
        self.assertEqual(cancelled.metadata['supply_released'],
                         [{'line_id': line.pk, 'offer_id': self.offer.pk, 'quantity': 2, 'previous_status': 'pending'}])
        # Nothing was on the shelf for it, and nothing comes back to it.
        self.assertFalse(StockMovement.objects.filter(order=order).exists())

        # Once: a second cancellation is refused, and the staff's «No se pudo
        # abastecer» finds nothing left to release.
        with self.assertRaises(cancelling.OrderCancelError):
            cancelling.cancel_paid_order(order, self.staff, REASON)
        with self.assertRaises(supply.SupplyError):
            supply.fail_line(line, actor=self.staff, reason='otra vez')
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.committed_quantity, 0)

    def test_a_line_to_confirm_committed_nothing_and_releases_nothing(self):
        self.add_on_order(quantity=2)
        order = self.checkout()
        SupplierOffer.objects.filter(pk=self.offer.pk).update(committed_quantity=5)   # sold out meanwhile
        self.pay(order)
        [line] = order.items.all()
        self.assertEqual(line.supply_status, OrderItem.SUPPLY_TO_CONFIRM)

        cancelling.cancel_paid_order(order, self.staff, REASON)

        self.offer.refresh_from_db()
        self.assertEqual(self.offer.committed_quantity, 5)
        line.refresh_from_db()
        self.assertEqual(line.supply_status, OrderItem.SUPPLY_FAILED)
        audit = AdminAuditLog.objects.get(action='order_supply_failed', target_id=str(order.pk))
        self.assertEqual(audit.metadata['released'], 0)

    def test_received_goods_come_back_to_the_shelf_at_the_cost_they_left_with(self):
        order = self.on_order(2)
        [line] = order.items.all()
        supply.receive_line(line, actor=self.staff)
        before = self.row()
        self.offer.refresh_from_db()
        committed = self.offer.committed_quantity

        cancelling.cancel_paid_order(order, self.staff, REASON)

        after = self.row()
        self.assertEqual(after.quantity, before.quantity + 2)
        self.assertEqual((after.average_cost, after.uncosted_quantity), (before.average_cost, 0))
        sale = StockMovement.objects.get(order=order, movement_type=StockMovement.SALE_EXIT)
        back = StockMovement.objects.get(order=order, movement_type=StockMovement.ORDER_CANCEL_RETURN)
        self.assertEqual(back.unit_cost, sale.unit_cost)
        # The supplier delivered: what was committed stays committed.
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.committed_quantity, committed)
        line.refresh_from_db()
        self.assertEqual(line.supply_status, OrderItem.SUPPLY_RECEIVED)

    def test_a_cancelled_order_takes_no_supply_action(self):
        order = self.on_order(1)
        cancelling.cancel_paid_order(order, self.staff, REASON)
        [line] = order.items.all()
        for action in (lambda: supply.confirm_line(line, actor=self.staff),
                       lambda: supply.receive_line(line, actor=self.staff)):
            with self.assertRaises(supply.SupplyError):
                action()


class CancelExactUnitTest(SaleShop, TestCase):
    def test_the_device_the_buyer_paid_for_is_the_one_that_comes_back_at_its_own_cost(self):
        holds.hold(product=self.new128, branch=self.branch, session_key='otro')          # the old one
        holds.hold(product=self.new128, branch=self.branch, session_key='carrito-a')     # the young one
        holds.release(company=self.company, session_key='otro')
        order = self.checkout()
        self.pay(order)
        order.refresh_from_db()
        self.young.refresh_from_db()
        self.assertEqual((self.young.status, self.young.order_id), ('sold', order.pk))
        manager = self.staff

        cancelling.cancel_paid_order(order, manager, REASON)

        self.young.refresh_from_db()
        self.old.refresh_from_db()
        self.assertEqual((self.young.status, self.young.order_id), (StockUnit.Status.AVAILABLE, None))
        self.assertEqual(self.old.status, StockUnit.Status.AVAILABLE)
        back = StockMovement.objects.get(order=order, movement_type=StockMovement.ORDER_CANCEL_RETURN)
        self.assertEqual((back.unit_cost, back.cost_source, back.metadata['unit_ids']),
                         (D('2600.0000'), 'unit', [self.young.pk]))
        self.assertEqual((self.shelf(self.new128), self.available(self.new128)), (2, 2))
        # The order line still names the device it sold: history is not rewritten.
        self.assertEqual(order.items.get().stock_unit_id, self.young.pk)
