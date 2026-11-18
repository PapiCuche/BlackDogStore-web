"""
TRANSFER-VALUATION-01 — a transfer moves value between branches; it never creates or destroys it.

    devices       the line that ARRIVES carries exactly the cost and source of the line that LEFT. A device without a
                  cost of its own is never given one (its `StockUnit.cost` stays NULL); the line's estimate, when the
                  exit used the catalogue's reference cost, is said ('reference') on both ends, not re-averaged.
    accessories   the units of unknown cost leave first (DC1). When one dispatch takes both kinds, they leave on two
                  Kardex lines — the pending part (reference or unknown) and the costed part (the branch average) —
                  and each arrives on its own line: the known value lands in the destination's average, the pending
                  units stay pending. Before, one line valued at the reference cost turned known value into pending.
    in transit    a device's cost cannot be corrected while it travels: the line that left and the line that arrives
                  must describe the same devices at the same cost.
"""
from decimal import Decimal as D

from django.test import TestCase

from store import inventory_services as inventory
from store import stock_unit_services as units
from store.models import BranchStock, Product, StockMovement, StockUnit
from store.test_device_commerce import assert_no_amount
from store.test_serial_transfer import TransferBase
from store.tests import _p2d_branch, _p2d_member, _p3_company, _prod


def lines(transfer, kind):
    return list(StockMovement.objects.filter(transfer=transfer, movement_type=kind).order_by('pk')
                .values_list('quantity', 'unit_cost', 'cost_source'))


class DeviceValuationTest(TransferBase):
    def travel(self, *devices):
        for unit in devices:
            self.add(unit)
        inventory.dispatch_transfer(self.transfer, actor=self.staff)
        for unit in devices:
            self.verify(unit)
        inventory.receive_transfer(self.transfer, actor=self.staff)

    def both_ends(self):
        return lines(self.transfer, StockMovement.TRANSFER_OUT), lines(self.transfer, StockMovement.TRANSFER_IN)

    def test_devices_of_known_cost_arrive_at_the_cost_they_left_with(self):
        a = self.receive(self.new128, cost='2800.00')[0]
        b = self.receive(self.new128, cost='3000.00')[0]
        self.travel(a, b)
        out, into = self.both_ends()
        self.assertEqual(out, [(2, D('2900.0000'), 'unit')])
        self.assertEqual(into, out)
        self.assertEqual([self.reload(u).cost for u in (a, b)], [D('2800.00'), D('3000.00')])

    def test_a_mixed_line_arrives_at_exactly_the_cost_it_left_with(self):
        Product.objects.filter(pk=self.new128.pk).update(reference_cost=D('2000.00'))
        costed = self.receive(self.new128, cost='2800.00')[0]
        bare = self.receive(self.new128)[0]
        self.travel(costed, bare)
        out, into = self.both_ends()
        self.assertEqual(out, [(2, D('2400.0000'), 'reference')])
        self.assertEqual(into, out)
        # Nobody's cost is invented: the device that had none still has none.
        self.assertEqual((self.reload(costed).cost, self.reload(bare).cost), (D('2800.00'), None))

    def test_a_mixed_line_without_a_reference_cost_is_unknown_on_both_ends(self):
        costed = self.receive(self.new128, cost='2800.00')[0]
        bare = self.receive(self.new128)[0]
        self.travel(costed, bare)
        out, into = self.both_ends()
        self.assertEqual(out, [(2, None, 'unknown')])
        self.assertEqual(into, out)

    def test_a_reference_cost_changed_in_transit_does_not_change_the_arrival(self):
        Product.objects.filter(pk=self.new128.pk).update(reference_cost=D('2000.00'))
        bare = self.receive(self.new128)[0]
        self.add(bare)
        inventory.dispatch_transfer(self.transfer, actor=self.staff)
        Product.objects.filter(pk=self.new128.pk).update(reference_cost=D('2600.00'))
        self.verify(bare)
        inventory.receive_transfer(self.transfer, actor=self.staff)
        out, into = self.both_ends()
        self.assertEqual(out, [(1, D('2000.0000'), 'reference')])
        self.assertEqual(into, out)

    def test_the_cost_of_a_device_in_transit_cannot_be_corrected(self):
        unit = self.receive(self.new128, cost='2800.00')[0]
        self.add(unit)
        inventory.dispatch_transfer(self.transfer, actor=self.staff)
        with self.assertRaises(units.StockUnitError) as caught:
            units.update_unit(self.reload(unit), actor=self.staff, cost='2500.00')
        self.assertIn('en tránsito', str(caught.exception))
        self.assertEqual(self.reload(unit).cost, D('2800.00'))

    def test_a_device_in_transit_may_still_have_its_other_details_edited(self):
        unit = self.receive(self.new128, cost='2800.00')[0]
        self.add(unit)
        inventory.dispatch_transfer(self.transfer, actor=self.staff)
        units.update_unit(self.reload(unit), actor=self.staff, cost='2800.00', notes='Caja abierta')
        self.assertEqual(self.reload(unit).notes, 'Caja abierta')


class AccessoryValuationTest(TransferBase):
    def setUp(self):
        super().setUp()
        self.cable = _prod(self.company, 'Cable Valor', 'cable-valor', price='30.00', inventory=0)

    def enter(self, quantity, cost=None):
        inventory.create_stock_movement(
            branch=self.branch, product_id=self.cable.pk, movement_type=StockMovement.PURCHASE_ENTRY,
            quantity=quantity, actor=self.staff, unit_cost=cost,
        )

    def row(self, branch):
        stock = BranchStock.objects.get(branch=branch, product=self.cable)
        return stock.quantity, stock.uncosted_quantity, stock.average_cost

    def send(self, quantity):
        inventory.set_transfer_item(self.transfer, product=self.cable, quantity=quantity)
        inventory.dispatch_transfer(self.transfer, actor=self.staff)
        inventory.receive_transfer(self.transfer, actor=self.staff)

    def known_value(self, branch):
        quantity, pending, average = self.row(branch)
        return D(quantity - pending) * (average or 0)

    def test_a_costed_shelf_travels_on_one_line_at_its_average(self):
        self.enter(5, '10.00')
        self.send(3)
        out = lines(self.transfer, StockMovement.TRANSFER_OUT)
        self.assertEqual(out, [(3, D('10.0000'), 'average')])
        self.assertEqual(lines(self.transfer, StockMovement.TRANSFER_IN), out)
        self.assertEqual(self.row(self.north), (3, 0, D('10.0000')))

    def test_pending_and_costed_units_leave_and_arrive_on_their_own_lines(self):
        Product.objects.filter(pk=self.cable.pk).update(reference_cost=D('7.00'))
        self.enter(2)              # no cost: pending
        self.enter(3, '10.00')     # costed
        before = self.known_value(self.branch)
        self.send(4)
        out = lines(self.transfer, StockMovement.TRANSFER_OUT)
        self.assertEqual(out, [(2, D('7.0000'), 'reference'), (2, D('10.0000'), 'average')])
        self.assertEqual(lines(self.transfer, StockMovement.TRANSFER_IN), out)
        self.assertEqual(self.row(self.branch), (1, 0, D('10.0000')))
        self.assertEqual(self.row(self.north), (4, 2, D('10.0000')))
        # Known value moved, none appeared or vanished.
        self.assertEqual(self.known_value(self.branch) + self.known_value(self.north), before)

    def test_without_a_reference_cost_the_pending_part_is_unknown(self):
        self.enter(1)
        self.enter(2, '12.00')
        self.send(3)
        out = lines(self.transfer, StockMovement.TRANSFER_OUT)
        self.assertEqual(out, [(1, None, 'unknown'), (2, D('12.0000'), 'average')])
        self.assertEqual(lines(self.transfer, StockMovement.TRANSFER_IN), out)
        self.assertEqual(self.row(self.north), (3, 1, D('12.0000')))

    def test_only_pending_units_leaving_stay_on_one_line(self):
        self.enter(3)
        self.enter(2, '10.00')
        self.send(2)
        self.assertEqual(lines(self.transfer, StockMovement.TRANSFER_OUT), [(2, None, 'unknown')])
        self.assertEqual(self.row(self.branch), (3, 1, D('10.0000')))
        self.assertEqual(self.row(self.north), (2, 2, None))

    def test_the_split_follows_the_shelf_at_dispatch_not_at_drafting(self):
        self.enter(1)
        self.enter(4, '10.00')
        inventory.set_transfer_item(self.transfer, product=self.cable, quantity=3)
        inventory.create_stock_movement(       # the pending unit is sold before the van leaves
            branch=self.branch, product_id=self.cable.pk, movement_type=StockMovement.SALE_EXIT,
            quantity=1, actor=self.staff,
        )
        inventory.dispatch_transfer(self.transfer, actor=self.staff)
        self.assertEqual(lines(self.transfer, StockMovement.TRANSFER_OUT), [(3, D('10.0000'), 'average')])

    def test_dispatching_and_receiving_twice_changes_nothing(self):
        self.enter(2)
        self.enter(3, '10.00')
        inventory.set_transfer_item(self.transfer, product=self.cable, quantity=4)
        first = inventory.dispatch_transfer(self.transfer, actor=self.staff)
        again = inventory.dispatch_transfer(self.transfer, actor=self.staff)
        self.assertEqual(sorted(m.pk for m in first), sorted(m.pk for m in again))
        into = inventory.receive_transfer(self.transfer, actor=self.staff)
        twice = inventory.receive_transfer(self.transfer, actor=self.staff)
        self.assertEqual(sorted(m.pk for m in into), sorted(m.pk for m in twice))
        self.assertEqual(len(into), 2)
        self.assertEqual(self.row(self.north), (4, 2, D('10.0000')))

    def test_accessories_and_devices_keep_their_own_values_in_one_transfer(self):
        self.enter(1)
        self.enter(1, '10.00')
        inventory.set_transfer_item(self.transfer, product=self.cable, quantity=2)
        unit = self.receive(self.new128, cost='2800.00')[0]
        self.add(unit)
        inventory.dispatch_transfer(self.transfer, actor=self.staff)
        self.verify(unit)
        inventory.receive_transfer(self.transfer, actor=self.staff)
        out = lines(self.transfer, StockMovement.TRANSFER_OUT)
        self.assertEqual(sorted(out, key=str), sorted(lines(self.transfer, StockMovement.TRANSFER_IN), key=str))
        self.assertEqual(len(out), 3)


class ValuationVisibilityTest(TransferBase):
    """The values above are for `costs.view`: the transfer and the Kardex never show them to anybody else."""

    def test_no_cost_reaches_a_caller_without_costs_view(self):
        from store.test_device_commerce import _client

        cable = _prod(self.company, 'Cable Visto', 'cable-visto', price='30.00', inventory=0)
        inventory.create_stock_movement(branch=self.branch, product_id=cable.pk, quantity=1, actor=self.staff,
                                        movement_type=StockMovement.PURCHASE_ENTRY)
        inventory.create_stock_movement(branch=self.branch, product_id=cable.pk, quantity=2, actor=self.staff,
                                        movement_type=StockMovement.PURCHASE_ENTRY, unit_cost='17.31')
        unit = self.receive(self.new128, cost='2833.17')[0]
        inventory.set_transfer_item(self.transfer, product=cable, quantity=3)
        self.add(unit)
        inventory.dispatch_transfer(self.transfer, actor=self.staff)
        client = _client(self.staff)          # inventory, no `costs.view`
        for url in (f'/api/admin/inventory/transfers/{self.transfer.pk}/', '/api/admin/inventory/movements/'):
            res = client.get(url)
            self.assertEqual(res.status_code, 200, res.content)
            assert_no_amount(self, res.json(), '17.31', '2833.17')
            self.assertNotIn('unit_cost', res.content.decode())


class OneCompanyTest(TestCase):
    def test_a_transfer_never_reaches_another_companys_branch(self):
        mine = _p3_company('valor-a', 'Valor A')
        theirs = _p3_company('valor-b', 'Valor B')
        staff, _ = _p2d_member(mine, 'valor_a_staff', ['inventory.view', 'inventory.adjust'])
        with self.assertRaises(inventory.TransferError):
            inventory.create_stock_transfer(company=mine, source_branch=mine.default_inventory_branch,
                                            destination_branch=_p2d_branch(theirs, 'Ajena'), actor=staff)
        self.assertFalse(StockUnit.objects.exists())
