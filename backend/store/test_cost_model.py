"""
DEVICE-COMMERCE-01 · DC1 — el modelo de costo.

Decisiones del propietario (2026-10-14):

  * los productos por cantidad (accesorios) se valoran al COSTO PROMEDIO
    PONDERADO DE CADA SUCURSAL;
  * los equipos con serie, al COSTO ESPECÍFICO de cada unidad;
  * nunca se recalcula la historia desde el catálogo.

El promedio vive en `BranchStock.average_cost` y lo mantiene el único escritor
del Kardex (`inventory_services.create_stock_movement`), bajo el mismo
`select_for_update` que la cantidad. Cada línea de Kardex guarda el costo con
el que se movió (`unit_cost`) y de dónde salió (`cost_source`).
"""
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from store import cost_services
from store import inventory_services as inventory
from store import stock_unit_services as units
from store.models import AdminAuditLog, BranchStock, Order, OrderItem, Product, StockMovement, StockUnit
from store.test_stock_units import IMEI_A, IMEI_B, IMEI_C
from store.tests import _p2d_branch, _p3_company, _prod

D = Decimal


class AverageMathTest(TestCase):
    """The arithmetic alone, pinned."""

    def test_the_first_cost_is_the_average(self):
        self.assertEqual(cost_services.weighted_average(0, None, 10, D('100')), D('100.0000'))

    def test_a_second_entry_weights_by_quantity(self):
        self.assertEqual(cost_services.weighted_average(10, D('100'), 5, D('130')), D('110.0000'))

    def test_four_decimals_half_up(self):
        # (1 × 10 + 2 × 10.01) / 3 = 10.00666… → 10.0067
        self.assertEqual(cost_services.weighted_average(1, D('10'), 2, D('10.01')), D('10.0067'))
        # (1 × 0.0002 + 1 × 0.0003) / 2 = 0.00025 → 0.0003 (half up; banker's would say 0.0002)
        self.assertEqual(cost_services.weighted_average(1, D('0.0002'), 1, D('0.0003')), D('0.0003'))

    def test_an_empty_shelf_or_an_unknown_average_takes_the_new_cost(self):
        self.assertEqual(cost_services.weighted_average(0, D('50'), 3, D('80')), D('80.0000'))
        # Units of unknown cost on the shelf are assumed at the first known one.
        self.assertEqual(cost_services.weighted_average(7, None, 3, D('80')), D('80.0000'))

    def test_a_zero_cost_is_a_cost(self):
        self.assertEqual(cost_services.weighted_average(1, D('10'), 1, D('0')), D('5.0000'))

    def test_nonsense_is_refused(self):
        for qty_in, unit_cost in ((0, D('1')), (-1, D('1')), (1, D('-0.01'))):
            with self.subTest(qty_in=qty_in, unit_cost=unit_cost), self.assertRaises(ValueError):
                cost_services.weighted_average(0, None, qty_in, unit_cost)

    def test_devices_arriving_without_a_cost_of_their_own_take_the_cost_they_left_with(self):
        """DC2 second review — a device that left at the reference cost arrives at it, said so."""
        arrived = cost_services.resolve(
            is_entry=True, carries_average_in=False, quantity=1, stock_before=0, average_before=None,
            reference_cost=None, stated_cost=D('900'), stated_source='reference', unit_costs=[None])
        self.assertEqual((arrived.unit_cost, arrived.source), (D('900.0000'), 'reference'))
        # Devices with their own cost keep it: the stated cost does not overwrite theirs.
        own = cost_services.resolve(
            is_entry=True, carries_average_in=False, quantity=1, stock_before=0, average_before=None,
            reference_cost=None, stated_cost=D('900'), stated_source='reference', unit_costs=[D('1000')])
        self.assertEqual((own.unit_cost, own.source), (D('1000.0000'), 'unit'))
        # Nothing stated and nothing known: unknown, as before.
        bare = cost_services.resolve(
            is_entry=True, carries_average_in=False, quantity=1, stock_before=0, average_before=None,
            reference_cost=D('5'), unit_costs=[None])
        self.assertEqual((bare.unit_cost, bare.source), (None, 'unknown'))


class _Shop(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('dc1-costo', 'Tienda Costo')
        self.other = _p3_company('dc1-ajena', 'Tienda Ajena')
        self.centro = self.company.branches.first() or _p2d_branch(self.company, 'Centro')
        self.norte = _p2d_branch(self.company, 'Norte')
        self.case = _prod(self.company, 'Funda', 'funda-dc1', price='59.90', inventory=0)

    def move(self, movement_type, quantity, *, branch=None, product=None, **extra):
        return inventory.create_stock_movement(
            branch=branch or self.centro, product_id=(product or self.case).pk,
            movement_type=movement_type, quantity=quantity, reason='Prueba', **extra,
        )

    def average(self, branch=None, product=None):
        return BranchStock.objects.get(branch=branch or self.centro, product=product or self.case).average_cost


class AverageCostOnTheKardexTest(_Shop):
    def test_a_sequence_of_entries_and_exits(self):
        first = self.move(StockMovement.PURCHASE_ENTRY, 10, unit_cost=D('100'))
        self.assertEqual((first.unit_cost, first.cost_source), (D('100.0000'), 'entry'))
        self.assertEqual(self.average(), D('100.0000'))

        self.move(StockMovement.PURCHASE_ENTRY, 5, unit_cost=D('130'))
        self.assertEqual(self.average(), D('110.0000'))

        out = self.move(StockMovement.MANUAL_EXIT, 4)
        self.assertEqual((out.unit_cost, out.cost_source), (D('110.0000'), 'average'))
        # An exit at the average leaves the average where it was.
        self.assertEqual(self.average(), D('110.0000'))

        self.move(StockMovement.PURCHASE_ENTRY, 9, unit_cost=D('90'))
        # (11 × 110 + 9 × 90) / 20 = 101
        self.assertEqual(self.average(), D('101.0000'))

    def test_an_entry_without_cost_keeps_the_average_and_is_marked_unknown(self):
        self.move(StockMovement.PURCHASE_ENTRY, 2, unit_cost=D('40'))
        blind = self.move(StockMovement.MANUAL_ENTRY, 3)
        self.assertEqual((blind.unit_cost, blind.cost_source), (None, 'unknown'))
        self.assertEqual(self.average(), D('40.0000'))

    def test_a_return_carries_the_average_without_moving_it(self):
        self.move(StockMovement.PURCHASE_ENTRY, 2, unit_cost=D('40'))
        back = self.move(StockMovement.RETURN_ENTRY, 1)
        self.assertEqual((back.unit_cost, back.cost_source), (D('40.0000'), 'average'))
        self.assertEqual(self.average(), D('40.0000'))

    def test_an_empty_shelf_keeps_its_last_average_until_the_next_entry_replaces_it(self):
        self.move(StockMovement.PURCHASE_ENTRY, 2, unit_cost=D('40'))
        self.move(StockMovement.MANUAL_EXIT, 2)
        self.assertEqual(self.average(), D('40.0000'))
        back = self.move(StockMovement.RETURN_ENTRY, 1)
        self.assertEqual(back.unit_cost, D('40.0000'))
        self.move(StockMovement.MANUAL_EXIT, 1)
        self.move(StockMovement.PURCHASE_ENTRY, 1, unit_cost=D('55'))
        self.assertEqual(self.average(), D('55.0000'))

    def test_stock_of_unknown_cost_stays_pending_when_a_known_cost_arrives(self):
        self.move(StockMovement.INITIAL_STOCK, 6)               # what is on the shelf today
        self.assertIsNone(self.average())
        self.move(StockMovement.PURCHASE_ENTRY, 2, unit_cost=D('25'))
        # The average is of the two that have a cost; the six stay pending (DC1 review).
        self.assertEqual(self.average(), D('25.0000'))
        self.assertEqual(self.uncosted(), 6)

    def test_without_an_average_an_exit_uses_the_reference_cost_marked_as_such(self):
        self.move(StockMovement.INITIAL_STOCK, 6)
        unknown = self.move(StockMovement.MANUAL_EXIT, 1)
        self.assertEqual((unknown.unit_cost, unknown.cost_source), (None, 'unknown'))

        Product.objects.filter(pk=self.case.pk).update(reference_cost=D('20.00'))
        referenced = self.move(StockMovement.MANUAL_EXIT, 1)
        self.assertEqual((referenced.unit_cost, referenced.cost_source), (D('20.0000'), 'reference'))
        # The reference is not a cost anybody paid: it never becomes the average.
        self.assertIsNone(self.average())

        self.move(StockMovement.PURCHASE_ENTRY, 1, unit_cost=D('30'))
        # The four of unknown cost leave first: each of those exits is an estimate.
        for _ in range(4):
            estimated = self.move(StockMovement.MANUAL_EXIT, 1)
            self.assertEqual((estimated.unit_cost, estimated.cost_source), (D('20.0000'), 'reference'))
        averaged = self.move(StockMovement.MANUAL_EXIT, 1)
        self.assertEqual((averaged.unit_cost, averaged.cost_source), (D('30.0000'), 'average'))

    def uncosted(self, branch=None, product=None):
        return BranchStock.objects.get(branch=branch or self.centro, product=product or self.case).uncosted_quantity

    def valuation(self):
        summary = inventory.get_inventory_summary(
            company=self.company, branches=[self.centro], include_cost=True,
        )
        return summary['inventory_cost_value'], summary['inventory_cost_pending_units']

    def test_stock_that_came_in_without_cost_is_never_valued_at_a_stale_average(self):
        """DC1 review, the probe: 10 in at 10, 10 out, 5 in without cost."""
        self.move(StockMovement.PURCHASE_ENTRY, 10, unit_cost=D('10'))
        self.move(StockMovement.MANUAL_EXIT, 10)
        blind = self.move(StockMovement.MANUAL_ENTRY, 5)
        self.assertEqual((blind.unit_cost, blind.cost_source), (None, 'unknown'))
        # Nothing on the shelf has a known cost: the average is unknown, and
        # the five are pending — not 50.00 at the old average.
        self.assertIsNone(self.average())
        self.assertEqual(self.uncosted(), 5)
        self.assertEqual(self.valuation(), ('0.00', 5))
        out = self.move(StockMovement.MANUAL_EXIT, 1)
        self.assertEqual((out.unit_cost, out.cost_source), (None, 'unknown'))
        self.assertEqual(self.uncosted(), 4)

        # A costed entry restarts the average for ITS units only; the four of
        # unknown cost stay pending, and they are taken as leaving first.
        self.move(StockMovement.PURCHASE_ENTRY, 2, unit_cost=D('20'))
        self.assertEqual(self.average(), D('20.0000'))
        self.assertEqual(self.valuation(), ('40.00', 4))
        out = self.move(StockMovement.MANUAL_EXIT, 3)
        self.assertEqual((out.unit_cost, out.cost_source), (None, 'unknown'))
        self.assertEqual(self.valuation(), ('40.00', 1))
        mixed = self.move(StockMovement.MANUAL_EXIT, 2)       # one pending + one at 20: not a known cost
        self.assertEqual((mixed.unit_cost, mixed.cost_source), (None, 'unknown'))
        self.assertEqual(self.valuation(), ('20.00', 0))
        known = self.move(StockMovement.MANUAL_EXIT, 1)
        self.assertEqual((known.unit_cost, known.cost_source), (D('20.0000'), 'average'))

    def test_a_shelf_with_costed_and_uncosted_units_values_only_the_costed(self):
        self.move(StockMovement.PURCHASE_ENTRY, 4, unit_cost=D('10'))
        self.move(StockMovement.MANUAL_ENTRY, 3)
        self.assertEqual(self.average(), D('10.0000'))
        self.assertEqual(self.valuation(), ('40.00', 3))
        # A return comes back at the average of the costed part.
        back = self.move(StockMovement.RETURN_ENTRY, 1)
        self.assertEqual((back.unit_cost, back.cost_source), (D('10.0000'), 'average'))
        self.assertEqual(self.valuation(), ('50.00', 3))

    def test_a_negative_cost_is_refused_and_writes_nothing(self):
        with self.assertRaises(inventory.InvalidMovementError):
            self.move(StockMovement.PURCHASE_ENTRY, 1, unit_cost=D('-1'))
        self.assertFalse(StockMovement.objects.filter(product=self.case).exists())

    def test_a_zero_cost_entry_counts(self):
        self.move(StockMovement.PURCHASE_ENTRY, 1, unit_cost=D('10'))
        self.move(StockMovement.PURCHASE_ENTRY, 1, unit_cost=D('0'))
        self.assertEqual(self.average(), D('5.0000'))

    def test_each_branch_has_its_own_average(self):
        self.move(StockMovement.PURCHASE_ENTRY, 1, unit_cost=D('10'))
        self.move(StockMovement.PURCHASE_ENTRY, 1, unit_cost=D('30'), branch=self.norte)
        self.assertEqual(self.average(), D('10.0000'))
        self.assertEqual(self.average(self.norte), D('30.0000'))

    def test_a_transfer_arrives_at_the_cost_it_left_with(self):
        self.move(StockMovement.PURCHASE_ENTRY, 10, unit_cost=D('100'))
        self.move(StockMovement.PURCHASE_ENTRY, 2, unit_cost=D('200'), branch=self.norte)

        transfer = inventory.create_stock_transfer(
            company=self.company, source_branch=self.centro, destination_branch=self.norte,
        )
        inventory.set_transfer_item(transfer, product=self.case, quantity=3)
        [out] = inventory.dispatch_transfer(transfer)
        self.assertEqual((out.unit_cost, out.cost_source), (D('100.0000'), 'average'))
        # The origin changes its average only by entries, not by giving stock away.
        Product.objects.filter(pk=self.case.pk).update(reference_cost=D('1.00'))
        self.move(StockMovement.PURCHASE_ENTRY, 7, unit_cost=D('500'))   # origin moves on meanwhile

        [arrived] = inventory.receive_transfer(transfer)
        self.assertEqual((arrived.unit_cost, arrived.cost_source), (D('100.0000'), 'average'))
        # (2 × 200 + 3 × 100) / 5 = 140
        self.assertEqual(self.average(self.norte), D('140.0000'))

    def test_a_transfer_that_left_at_the_reference_cost_does_not_feed_the_average(self):
        self.move(StockMovement.INITIAL_STOCK, 3)
        Product.objects.filter(pk=self.case.pk).update(reference_cost=D('12.00'))
        transfer = inventory.create_stock_transfer(
            company=self.company, source_branch=self.centro, destination_branch=self.norte,
        )
        inventory.set_transfer_item(transfer, product=self.case, quantity=1)
        inventory.dispatch_transfer(transfer)
        [arrived] = inventory.receive_transfer(transfer)
        self.assertEqual((arrived.unit_cost, arrived.cost_source), (D('12.0000'), 'reference'))
        self.assertIsNone(self.average(self.norte))


class StockBackFromASaleTest(_Shop):
    """
    Stock that comes back from an order's sale, with no cost of its own, comes
    back at the cost its sale exit left with — whatever the entry's type is
    called (integration review of PR #123, P2-2: #119 adds a cancellation
    return that states no cost and is not in `RETURN_TYPES`).
    """

    def setUp(self):
        super().setUp()
        self.move(StockMovement.PURCHASE_ENTRY, 5, unit_cost=D('10'))
        self.order = Order.objects.create(
            company=self.company, fulfillment_branch=self.centro, customer_email='x@example.com',
            total=D('119.80'), status=Order.Status.PAID, paid=True, paid_at=timezone.now(),
        )
        self.sale = self.move(StockMovement.SALE_EXIT, 2, order=self.order)
        self.move(StockMovement.PURCHASE_ENTRY, 3, unit_cost=D('30'))          # 3 × 10 + 3 × 30 → 20

    def test_an_entry_of_any_type_for_the_order_takes_the_exits_cost(self):
        self.assertEqual(self.average(), D('20.0000'))
        for movement_type in (StockMovement.MANUAL_ENTRY, StockMovement.RETURN_ENTRY):
            with self.subTest(movement_type):
                back = self.move(movement_type, 1, order=self.order)
                self.assertEqual((back.unit_cost, back.cost_source), (D('10.0000'), 'average'))
                self.assertEqual(back.metadata.get('cost_from_movement_ids'), [self.sale.pk])
        # (6 × 20 + 2 × 10) / 8 = 17.5 — known cost, nothing pending.
        stock = BranchStock.objects.get(branch=self.centro, product=self.case)
        self.assertEqual((stock.average_cost, stock.uncosted_quantity), (D('17.5000'), 0))

    def test_a_stated_cost_still_wins(self):
        back = self.move(StockMovement.MANUAL_ENTRY, 1, order=self.order, unit_cost=D('12'))
        self.assertEqual((back.unit_cost, back.cost_source), (D('12.0000'), 'entry'))

    def test_a_purchase_for_the_order_is_not_a_return(self):
        """The on-order receipt of DC3 states its offer's cost — or none: then it is unknown, not the shelf's."""
        bought = self.move(StockMovement.PURCHASE_ENTRY, 1, order=self.order)
        self.assertEqual((bought.unit_cost, bought.cost_source), (None, 'unknown'))

    def test_an_exit_of_unknown_cost_gives_nothing_to_take(self):
        StockMovement.objects.filter(pk=self.sale.pk).update(unit_cost=None, cost_source='unknown')
        back = self.move(StockMovement.MANUAL_ENTRY, 1, order=self.order)
        self.assertEqual((back.unit_cost, back.cost_source), (None, 'unknown'))

    def test_an_exit_at_the_reference_cost_comes_back_pending(self):
        StockMovement.objects.filter(pk=self.sale.pk).update(unit_cost=D('9'), cost_source='reference')
        back = self.move(StockMovement.MANUAL_ENTRY, 1, order=self.order)
        self.assertEqual((back.unit_cost, back.cost_source), (D('9.0000'), 'reference'))
        self.assertEqual(BranchStock.objects.get(branch=self.centro, product=self.case).uncosted_quantity, 1)


class SpecificCostOfDevicesTest(_Shop):
    def setUp(self):
        super().setUp()
        self.phone = _prod(self.company, 'iPhone 16', 'iphone-16-dc1', price='4500.00', inventory=0)
        Product.objects.filter(pk=self.phone.pk).update(is_serialized=True, requires_imei=True)
        self.phone.refresh_from_db()

    def receive(self, *rows):
        return units.receive_units(branch=self.centro, product=self.phone, units=list(rows),
                                   reason='Compra')

    def test_a_reception_carries_the_mean_of_its_units_and_the_branch_keeps_no_average(self):
        self.receive({'serial_number': 'SER-0001', 'imei': IMEI_A, 'cost': '3800.00'},
                     {'serial_number': 'SER-0002', 'imei': IMEI_B, 'cost': '3900.00'})
        movement = StockMovement.objects.get(product=self.phone)
        self.assertEqual((movement.unit_cost, movement.cost_source), (D('3850.0000'), 'unit'))
        self.assertIsNone(self.average(product=self.phone))

    def test_one_unit_without_cost_makes_the_line_unknown(self):
        self.receive({'serial_number': 'SER-0001', 'imei': IMEI_A, 'cost': '3800.00'},
                     {'serial_number': 'SER-0002', 'imei': IMEI_B})
        movement = StockMovement.objects.get(product=self.phone)
        self.assertEqual((movement.unit_cost, movement.cost_source), (None, 'unknown'))

    def test_a_unit_leaves_at_its_own_cost(self):
        first, second = self.receive({'serial_number': 'SER-0001', 'imei': IMEI_A, 'cost': '3800.00'},
                                     {'serial_number': 'SER-0002', 'imei': IMEI_B, 'cost': '3900.00'})
        gone = units.write_off(second, reason='Roto')
        exit_line = StockMovement.objects.filter(product=self.phone, movement_type='damaged_exit').get()
        self.assertEqual((exit_line.unit_cost, exit_line.cost_source), (D('3900.0000'), 'unit'))
        self.assertEqual(gone.cost, D('3900.00'))


class SoldUnitCostIsFrozenTest(_Shop):
    def setUp(self):
        super().setUp()
        self.phone = _prod(self.company, 'iPhone 16', 'iphone-16-dc1b', price='4500.00', inventory=0)
        Product.objects.filter(pk=self.phone.pk).update(is_serialized=True, requires_imei=True)
        self.phone.refresh_from_db()
        self.unit, = units.receive_units(
            branch=self.centro, product=self.phone, reason='Compra',
            units=[{'serial_number': 'SER-0001', 'imei': IMEI_C, 'cost': '3800.00'}],
        )
        order = Order.objects.create(
            company=self.company, fulfillment_branch=self.centro, customer_email='x@example.com',
            total=self.phone.price, status=Order.Status.PAID, paid=True, paid_at=timezone.now(),
        )
        OrderItem.objects.create(order=order, product=self.phone, quantity=1, price=self.phone.price)
        inventory.record_sale_stock_movements(order)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.status, StockUnit.Status.SOLD)

    def test_the_cost_of_a_sold_unit_cannot_be_rewritten(self):
        with self.assertRaises(units.StockUnitError):
            units.update_unit(self.unit, cost='1.00')
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.cost, D('3800.00'))

    def test_other_details_of_a_sold_unit_still_change(self):
        units.update_unit(self.unit, notes='Vendido con funda')
        units.update_unit(self.unit, cost='3800.00')      # the same value is not a change
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.notes, 'Vendido con funda')


class UncostedAfterRollbackTest(_Shop):
    """
    Back to a release before DC1 with the cost migrations applied, and forward
    again: that release wrote Kardex lines without a cost and never touched the
    average or the pending part. `uncosted_after_rollback` reports those shelves
    and, with --apply, makes what came in without a cost pending — never a cost
    invented, and a second run changes nothing.
    """

    def setUp(self):
        super().setUp()
        self.move(StockMovement.PURCHASE_ENTRY, 5, unit_cost=D('10'))
        self.since = timezone.now()

    def old_release(self, movement_type, quantity, product=None):
        """What the release before DC1 writes: the line with no cost, the quantity, nothing else."""
        from django.db.models import F
        product = product or self.case
        stock = BranchStock.objects.get(branch=self.centro, product=product)
        delta = quantity if movement_type in StockMovement.ENTRY_TYPES else -quantity
        StockMovement.objects.create(
            company=self.company, branch=self.centro, product=product, movement_type=movement_type,
            quantity=quantity, stock_before=stock.quantity, stock_after=stock.quantity + delta, reason='Antes')
        BranchStock.objects.filter(pk=stock.pk).update(quantity=F('quantity') + delta)

    def run_command(self, *args):
        import io
        from django.core.management import call_command
        out = io.StringIO()
        call_command('uncosted_after_rollback', '--since', self.since.isoformat(), *args, stdout=out)
        return out.getvalue()

    def stock(self):
        return BranchStock.objects.get(branch=self.centro, product=self.case)

    def test_the_report_names_the_shelf_and_writes_nothing(self):
        self.old_release(StockMovement.MANUAL_ENTRY, 3)
        out = self.run_command()
        self.assertIn('Funda: 1 movimiento(s) sin costo, 3 unidad(es) entraron sin costo; stock 8, '
                      'costo pendiente 0 → 3', out)
        self.assertEqual((self.stock().uncosted_quantity, self.stock().average_cost), (0, D('10.0000')))
        self.assertTrue(StockMovement.objects.filter(cost_source='').exists())

    def test_apply_makes_what_came_in_without_a_cost_pending_once(self):
        self.old_release(StockMovement.MANUAL_ENTRY, 3)
        self.old_release(StockMovement.MANUAL_EXIT, 2)
        out = self.run_command('--apply')
        self.assertIn('costo pendiente 0 → 3', out)
        stock = self.stock()
        # 6 on the shelf: 3 pending (as if both exits took costed units), 3 of known cost at 10.
        self.assertEqual((stock.quantity, stock.uncosted_quantity, stock.average_cost), (6, 3, D('10.0000')))
        self.assertFalse(StockMovement.objects.filter(cost_source='').exists())
        self.assertFalse(StockMovement.objects.filter(unit_cost__isnull=True).exclude(cost_source='unknown').exists())
        log = AdminAuditLog.objects.get(action='uncosted_recomputed_after_rollback')
        self.assertEqual((log.metadata['uncosted_before'], log.metadata['uncosted_after']), (0, 3))
        # Again: nothing left to count.
        self.assertIn('Actualizados: 0', self.run_command('--apply'))
        self.assertEqual(self.stock().uncosted_quantity, 3)
        self.assertEqual(AdminAuditLog.objects.filter(action='uncosted_recomputed_after_rollback').count(), 1)

    def test_no_unit_of_known_cost_left_clears_the_average(self):
        self.old_release(StockMovement.MANUAL_ENTRY, 2)
        self.old_release(StockMovement.MANUAL_EXIT, 6)
        self.run_command('--apply')
        # 1 left; 2 came in without a cost — never more pending than the shelf holds.
        self.assertEqual((self.stock().uncosted_quantity, self.stock().average_cost), (1, None))

    def test_lines_before_since_are_history_and_untouched(self):
        from datetime import timedelta
        self.since = timezone.now() + timedelta(minutes=1)
        self.old_release(StockMovement.MANUAL_ENTRY, 3)
        self.assertIn('Actualizados: 0', self.run_command('--apply'))
        self.assertEqual(StockMovement.objects.filter(cost_source='').count(), 1)
        self.assertEqual(self.stock().uncosted_quantity, 0)

    def test_devices_are_reported_never_written(self):
        phone = _prod(self.company, 'iPhone 16', 'iphone-16-rb', price='4500.00', inventory=0)
        Product.objects.filter(pk=phone.pk).update(is_serialized=True)
        BranchStock.objects.get_or_create(branch=self.centro, product=phone)
        self.old_release(StockMovement.MANUAL_ENTRY, 1, product=phone)
        out = self.run_command('--apply')
        self.assertIn('equipos con costo por unidad', out)
        self.assertEqual(StockMovement.objects.filter(product=phone, cost_source='').count(), 1)

    def test_a_since_that_is_not_a_date_is_refused(self):
        from django.core.management import CommandError, call_command
        with self.assertRaises(CommandError):
            call_command('uncosted_after_rollback', '--since', 'ayer')
