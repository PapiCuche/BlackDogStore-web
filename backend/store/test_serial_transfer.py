"""
SERIAL-TRANSFER-01 — devices with a serial travel between branches by READING them (docs/adr-serial-transfer.md).

    draft       a device is added by its code (IMEI, serial or internal code); it stays AVAILABLE and on sale (ST-D1)
    dispatch    each device leaves the origin shelf with its TRANSFER_OUT and becomes IN_TRANSIT (on no shelf)
    verify      at the destination each device is read again (ST-D2): nothing is received «by one click»
    receive     only when every device was read; it moves to the destination and becomes AVAILABLE there

A device in transit cannot be sold, set aside, voided or moved. A device sold after it entered the draft makes the
whole dispatch fail, naming it. Nothing is simulated: the Kardex carries the ids of the devices on both ends.
"""
from django.test import TestCase

from store import inventory_services as inventory
from store import stock_unit_services as units
from store import transfer_unit_services as transfer_units
from store.models import AdminAuditLog, BranchStock, StockMovement, StockTransfer, StockTransferUnit, StockUnit
from store.test_device_commerce import DeviceShop, _client
from store.tests import _m7_stock, _p2d_branch, _prod


class TransferBase(DeviceShop, TestCase):
    def setUp(self):
        self.build('serial-transfer')
        self.north = _p2d_branch(self.company, 'Norte Traslado')
        self.transfer = inventory.create_stock_transfer(
            company=self.company, source_branch=self.branch, destination_branch=self.north, actor=self.staff,
        )

    def add(self, unit, code=None, method='scanner'):
        return transfer_units.add_unit(self.transfer, code=code or unit.imei, method=method, actor=self.staff)

    def verify(self, unit, code=None, method='scanner'):
        return transfer_units.verify_unit(self.transfer, code=code or unit.imei, method=method, actor=self.staff)

    def shelf_at(self, branch, product):
        row = BranchStock.objects.filter(branch=branch, product=product).first()
        return row.quantity if row else 0

    def reload(self, unit):
        return StockUnit.objects.get(pk=unit.pk)


class DraftTest(TransferBase):
    def test_a_device_is_added_by_its_code_and_stays_on_sale(self):
        [unit] = self.receive(self.new128)
        self.add(unit)
        item = self.transfer.items.get()
        self.assertEqual((item.product_id, item.quantity), (self.new128.pk, 1))
        row = StockTransferUnit.objects.get()
        self.assertEqual((row.unit_id, row.method, row.added_by), (unit.pk, 'scanner', self.staff))
        self.assertEqual(self.reload(unit).status, StockUnit.Status.AVAILABLE)
        self.assertEqual(self.shelf_at(self.branch, self.new128), 1)
        self.assertFalse(StockMovement.objects.filter(transfer=self.transfer).exists())

    def test_reading_it_twice_does_not_add_it_twice(self):
        [unit] = self.receive(self.new128)
        self.add(unit)
        with self.assertRaises(inventory.TransferError) as caught:
            self.add(unit, code=unit.serial_number)
        self.assertIn('ya está en esta transferencia', str(caught.exception))
        self.assertEqual(self.transfer.items.get().quantity, 1)

    def test_a_device_in_another_branch_is_refused_without_saying_where(self):
        [unit] = self.receive(self.new128)
        StockUnit.objects.filter(pk=unit.pk).update(branch=self.north)
        with self.assertRaises(inventory.TransferError) as caught:
            self.add(unit)
        self.assertIn('no está en esta sucursal', str(caught.exception))
        self.assertNotIn(self.north.name, str(caught.exception))

    def test_a_device_set_aside_is_refused_with_its_state(self):
        [unit] = self.receive(self.new128)
        units.reserve(unit, reason='Para Ana', actor=self.staff)
        with self.assertRaises(inventory.TransferError) as caught:
            self.add(unit)
        self.assertIn('apartado', str(caught.exception))

    def test_typed_by_hand_a_device_with_imei_is_identified_by_it(self):
        [unit] = self.receive(self.new128)
        with self.assertRaises(inventory.TransferError):
            self.add(unit, code=unit.serial_number, method='manual')
        self.add(unit, code=unit.imei, method='manual')
        self.assertEqual(StockTransferUnit.objects.get().method, 'manual')

    def test_removing_the_last_device_removes_its_line(self):
        a, b = self.receive(self.new128, n=2)
        self.add(a)
        self.add(b)
        transfer_units.remove_unit(self.transfer, unit_id=a.pk, actor=self.staff)
        self.assertEqual(self.transfer.items.get().quantity, 1)
        transfer_units.remove_unit(self.transfer, unit_id=b.pk, actor=self.staff)
        self.assertFalse(self.transfer.items.exists())
        self.assertFalse(StockTransferUnit.objects.exists())

    def test_the_audit_names_ids_never_identifiers(self):
        [unit] = self.receive(self.new128)
        self.add(unit)
        log = AdminAuditLog.objects.filter(action='stock_transfer_unit_added').get()
        self.assertEqual(log.metadata.get('unit_id'), unit.pk)
        self.assertNotIn(unit.imei, str(log.metadata))
        self.assertNotIn(unit.serial_number, str(log.metadata))

    def test_no_device_is_added_once_dispatched(self):
        a, b = self.receive(self.new128, n=2)
        self.add(a)
        inventory.dispatch_transfer(self.transfer, actor=self.staff)
        with self.assertRaises(inventory.TransferError):
            self.add(b)


class DispatchReceiveTest(TransferBase):
    def test_the_whole_journey_moves_the_device_and_its_shelves(self):
        [unit] = self.receive(self.new128, cost='2800.00')
        self.add(unit)
        inventory.dispatch_transfer(self.transfer, actor=self.staff)
        travelling = self.reload(unit)
        self.assertEqual((travelling.status, travelling.branch_id), (StockUnit.Status.IN_TRANSIT, self.branch.pk))
        self.assertEqual((self.shelf_at(self.branch, self.new128), self.shelf_at(self.north, self.new128)), (0, 0))
        out = StockMovement.objects.get(transfer=self.transfer, movement_type=StockMovement.TRANSFER_OUT)
        self.assertEqual(out.metadata.get('unit_ids'), [unit.pk])

        with self.assertRaises(inventory.TransferError) as caught:
            inventory.receive_transfer(self.transfer, actor=self.staff)
        self.assertIn('1', str(caught.exception))
        self.verify(unit)
        inventory.receive_transfer(self.transfer, actor=self.staff)
        arrived = self.reload(unit)
        self.assertEqual((arrived.status, arrived.branch_id), (StockUnit.Status.AVAILABLE, self.north.pk))
        self.assertEqual(str(arrived.cost), '2800.00')
        self.assertEqual((self.shelf_at(self.branch, self.new128), self.shelf_at(self.north, self.new128)), (0, 1))
        into = StockMovement.objects.get(transfer=self.transfer, movement_type=StockMovement.TRANSFER_IN)
        self.assertEqual(into.metadata.get('unit_ids'), [unit.pk])
        row = StockTransferUnit.objects.get()
        self.assertEqual((row.received_method, row.received_by), ('scanner', self.staff))

    def test_a_device_sold_after_entering_the_draft_fails_the_whole_dispatch(self):
        a, b = self.receive(self.new128, n=2)
        self.add(a)
        self.add(b)
        units.write_off(b, reason='Se rompió', actor=self.staff)
        movements = StockMovement.objects.count()
        with self.assertRaises(inventory.InventoryError) as caught:
            inventory.dispatch_transfer(self.transfer, actor=self.staff)
        self.assertIn(b.identifier, str(caught.exception))
        self.assertEqual(StockMovement.objects.count(), movements)
        self.assertEqual(self.reload(a).status, StockUnit.Status.AVAILABLE)
        self.assertEqual(StockTransfer.objects.get(pk=self.transfer.pk).status, StockTransfer.STATUS_DRAFT)

    def test_a_device_in_transit_cannot_be_set_aside_voided_or_moved(self):
        [unit] = self.receive(self.new128)
        self.add(unit)
        inventory.dispatch_transfer(self.transfer, actor=self.staff)
        travelling = self.reload(unit)
        with self.assertRaises(units.StockUnitError):
            units.reserve(travelling, reason='x', actor=self.staff)
        with self.assertRaises(units.StockUnitError):
            units.void_unit(travelling, reason='x', reason_code='typo', actor=self.staff)
        with self.assertRaises(units.StockUnitError):
            units.move_unit_to_variant(travelling, product=self.new256, reason='x', actor=self.staff)
        self.assertTrue(units.holds_stock(self.new128))

    def test_verifying_a_device_that_is_not_travelling_here_is_refused(self):
        a, b = self.receive(self.new128, n=2)
        self.add(a)
        with self.assertRaises(inventory.TransferError):
            self.verify(a)            # still a draft
        inventory.dispatch_transfer(self.transfer, actor=self.staff)
        with self.assertRaises(inventory.TransferError) as caught:
            self.verify(b)
        self.assertIn('no viaja en esta transferencia', str(caught.exception))
        self.verify(a)
        self.verify(a)                # twice: still once
        self.assertEqual(StockTransferUnit.objects.filter(received_at__isnull=False).count(), 1)

    def test_devices_and_accessories_travel_together(self):
        cable = _prod(self.company, 'Cable Traslado', 'cable-traslado', price='20.00', inventory=0)
        _m7_stock(self.branch, cable, 10)
        inventory.set_transfer_item(self.transfer, product=cable, quantity=3)
        [unit] = self.receive(self.new128)
        self.add(unit)
        inventory.dispatch_transfer(self.transfer, actor=self.staff)
        self.verify(unit)
        inventory.receive_transfer(self.transfer, actor=self.staff)
        self.assertEqual((self.shelf_at(self.north, cable), self.shelf_at(self.north, self.new128)), (3, 1))

    def test_receiving_twice_changes_nothing(self):
        [unit] = self.receive(self.new128)
        self.add(unit)
        inventory.dispatch_transfer(self.transfer, actor=self.staff)
        self.verify(unit)
        inventory.receive_transfer(self.transfer, actor=self.staff)
        movements = StockMovement.objects.count()
        inventory.receive_transfer(self.transfer, actor=self.staff)
        self.assertEqual(StockMovement.objects.count(), movements)

    def test_the_quantity_lines_cannot_be_set_for_a_device(self):
        with self.assertRaises(inventory.TransferError):
            inventory.set_transfer_item(self.transfer, product=self.new128, quantity=2)


class TransferApiTest(TransferBase):
    def base(self):
        return f'/api/admin/inventory/transfers/{self.transfer.pk}'

    def test_scanning_adding_listing_and_removing_through_the_panel(self):
        [unit] = self.receive(self.new128)
        client = _client(self.staff)
        res = client.post(f'{self.base()}/units/', {'code': unit.imei, 'method': 'scanner'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        [item] = res.json()['items']
        self.assertEqual(item['units'][0]['id'], unit.pk)
        self.assertEqual(item['units'][0]['imei'], unit.imei)
        self.assertIsNone(item['units'][0]['received_at'])
        res = client.delete(f'{self.base()}/units/{unit.pk}/')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['items'], [])

    def test_a_refused_read_is_a_400_with_the_reason(self):
        client = _client(self.staff)
        res = client.post(f'{self.base()}/units/', {'code': '000000000000000', 'method': 'scanner'}, format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertTrue(res.json()['detail'])

    def test_replacing_the_quantity_lines_keeps_the_device_lines(self):
        cable = _prod(self.company, 'Cable PUT', 'cable-put', price='20.00', inventory=0)
        _m7_stock(self.branch, cable, 10)
        [unit] = self.receive(self.new128)
        self.add(unit)
        res = _client(self.staff).put(f'{self.base()}/items/', [{'product': cable.pk, 'quantity': 2}], format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(sorted(i['product'] for i in res.json()['items']), sorted([cable.pk, self.new128.pk]))
        self.assertTrue(StockTransferUnit.objects.filter(unit=unit).exists())

    def test_verifying_through_the_panel(self):
        [unit] = self.receive(self.new128)
        self.add(unit)
        inventory.dispatch_transfer(self.transfer, actor=self.staff)
        res = _client(self.staff).post(f'{self.base()}/units/verify/', {'code': unit.imei, 'method': 'scanner'},
                                       format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIsNotNone(res.json()['items'][0]['units'][0]['received_at'])
        self.assertEqual(res.json()['units_verified'], 1)


class TransferMigrationGuardTest(TransferBase):
    def guard(self):
        import importlib

        from django.apps import apps
        return importlib.import_module('store.migrations.0176_serial_transfer_units').refuse_devices_in_transfers(
            apps, None)

    def test_an_empty_history_may_be_reversed(self):
        self.guard()

    def test_devices_in_a_transfer_stop_the_reversal(self):
        [unit] = self.receive(self.new128)
        self.add(unit)
        with self.assertRaises(RuntimeError):
            self.guard()


class TransferReviewTest(TransferBase):
    """Review of SERIAL-TRANSFER-01: refusals over HTTP, variant moves, missing devices, odd routes, scope."""

    def base(self):
        return f'/api/admin/inventory/transfers/{self.transfer.pk}'

    def test_dispatch_with_a_refused_device_is_a_400_naming_it(self):
        a, b = self.receive(self.new128, n=2)
        self.add(a)
        self.add(b)
        units.write_off(b, reason='Se rompió', actor=self.staff)
        res = _client(self.staff).post(f'{self.base()}/dispatch/')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn(b.identifier, res.json()['detail'])

    def test_a_device_moved_to_another_variant_while_in_a_draft_is_named(self):
        [unit] = self.receive(self.new128)
        self.add(unit)
        with self.assertRaises(units.StockUnitError) as caught:
            units.move_unit_to_variant(self.reload(unit), product=self.new256, reason='x', actor=self.staff)
        self.assertIn('transferencia', str(caught.exception))
        # Even if it had moved by another way, the dispatch names it instead of failing obscurely.
        StockUnit.objects.filter(pk=unit.pk).update(product=self.new256)
        with self.assertRaises(inventory.TransferError) as caught:
            inventory.dispatch_transfer(self.transfer, actor=self.staff)
        self.assertIn(unit.identifier, str(caught.exception))

    def test_receiving_with_devices_missing_names_them(self):
        a, b = self.receive(self.new128, n=2)
        self.add(a)
        self.add(b)
        inventory.dispatch_transfer(self.transfer, actor=self.staff)
        self.verify(a)
        with self.assertRaises(inventory.TransferError) as caught:
            inventory.receive_transfer(self.transfer, actor=self.staff)
        self.assertIn(b.identifier, str(caught.exception))
        self.assertNotIn(a.identifier, str(caught.exception))

    def test_posting_to_one_device_is_not_allowed(self):
        [unit] = self.receive(self.new128)
        self.add(unit)
        res = _client(self.staff).post(f'{self.base()}/units/{unit.pk}/', {}, format='json')
        self.assertEqual(res.status_code, 405, res.content)

    def test_sending_a_device_line_as_a_quantity_is_refused(self):
        res = _client(self.staff).put(f'{self.base()}/items/', [{'product': self.new128.pk, 'quantity': 1}],
                                      format='json')
        self.assertEqual(res.status_code, 400, res.content)

    def test_a_caller_who_reaches_only_one_end_cannot_add(self):
        from store.tests import _p2d_member
        [unit] = self.receive(self.new128)
        one_end, _ = _p2d_member(self.company, 'transfer_one_end',
                                 ['company.view', 'inventory.view', 'inventory.adjust'], branches=[self.branch])
        res = _client(one_end).post(f'{self.base()}/units/', {'code': unit.imei, 'method': 'scanner'}, format='json')
        self.assertEqual(res.status_code, 403, res.content)
        self.assertFalse(StockTransferUnit.objects.exists())

    def test_cancelling_a_draft_with_devices_leaves_them_on_sale(self):
        [unit] = self.receive(self.new128)
        self.add(unit)
        inventory.cancel_transfer(self.transfer, actor=self.staff)
        self.assertEqual(self.reload(unit).status, StockUnit.Status.AVAILABLE)
        self.assertEqual(self.shelf_at(self.branch, self.new128), 1)

    def test_a_product_with_devices_in_transit_cannot_change_its_tracking(self):
        [unit] = self.receive(self.new128)
        self.add(unit)
        inventory.dispatch_transfer(self.transfer, actor=self.staff)
        res = _client(self.staff).get('/api/admin/inventory/units/products/', {'scope': 'all'})
        row = [r for r in res.json()['results'] if r['id'] == self.new128.pk][0]
        self.assertFalse(row['can_change_tracking'])

    def test_each_shelf_matches_its_available_devices_at_every_step(self):
        a, b = self.receive(self.new128, n=2)

        def check():
            for branch in (self.branch, self.north):
                live = StockUnit.objects.filter(branch=branch, product=self.new128, status='available').count()
                self.assertEqual(self.shelf_at(branch, self.new128), live, branch.name)

        self.add(a)
        check()
        inventory.dispatch_transfer(self.transfer, actor=self.staff)
        check()
        self.verify(a)
        inventory.receive_transfer(self.transfer, actor=self.staff)
        check()
