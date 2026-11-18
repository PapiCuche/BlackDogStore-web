"""
INV-UNIT-CORRECT-01 · fase 1 — «Anular ingreso» (docs/adr-unit-correction.md §5.1, §5.3, §6).

A device that should never have entered (mistyped identifier, duplicate, wrong variant or branch, never arrived)
leaves the stock with a CORRECTION_NEGATIVE line, stays as a VOIDED row with its identifiers (the record of what was
voided), and FREES its IMEI and serial so the right device enters by the normal door. Only a device whose only Kardex
line is its entry may be voided (UC-D4 strict). Nothing is edited in place and nothing is deleted.
"""
from django.db import IntegrityError, transaction
from django.test import TestCase

from store import stock_unit_services as units
from store import unit_identification
from store.models import AdminAuditLog, BranchStock, StockMovement, StockUnit
from store.test_device_commerce import IMEIS, DeviceShop, _client
from store.tests import _p2d_member


class VoidBase(DeviceShop, TestCase):
    def setUp(self):
        self.build('unit-void')

    def one(self, product=None, **extra):
        [unit] = self.receive(product or self.new128, **extra)
        return unit

    def void(self, unit, reason='Se digitó mal el IMEI', reason_code='typo'):
        return units.void_unit(unit, reason=reason, reason_code=reason_code, actor=self.staff)

    def shelf_matches_units(self, product):
        shelf = BranchStock.objects.filter(branch=self.branch, product=product).values_list('quantity', flat=True)
        available = StockUnit.objects.filter(branch=self.branch, product=product, status='available').count()
        self.assertEqual((list(shelf) or [0])[0], available)


class VoidUnitTest(VoidBase):
    def test_voiding_takes_the_device_out_with_a_correction_line(self):
        unit = self.one()
        entry = StockMovement.objects.get(product=self.new128)
        voided = self.void(unit)
        self.assertEqual(voided.status, StockUnit.Status.VOIDED)
        self.assertIsNotNone(voided.voided_at)
        self.assertEqual(voided.voided_by, self.staff)
        self.assertEqual(voided.void_reason, 'Se digitó mal el IMEI')
        # The identifiers stay on the row: they are the record of what was voided.
        self.assertEqual((voided.imei, voided.serial_number), (unit.imei, unit.serial_number))
        line = StockMovement.objects.filter(product=self.new128).exclude(pk=entry.pk).get()
        self.assertEqual((line.movement_type, line.quantity), (StockMovement.CORRECTION_NEGATIVE, 1))
        self.assertEqual((line.reference_type, line.reference_id), ('stock_unit_void', str(unit.pk)))
        self.assertEqual(line.metadata.get('voids_movement_id'), entry.pk)
        self.assertEqual(line.metadata.get('reason_code'), 'typo')
        self.assertEqual(line.metadata.get('unit_ids'), [unit.pk])
        self.assertEqual(self.shelf(self.new128), 0)
        self.shelf_matches_units(self.new128)

    def test_the_audit_names_ids_never_identifiers(self):
        unit = self.one()
        self.void(unit)
        log = AdminAuditLog.objects.filter(action='stock_unit_voided').get()
        self.assertEqual(log.target_id, str(unit.pk))
        text = str(log.metadata)
        self.assertNotIn(unit.imei, text)
        self.assertNotIn(unit.serial_number, text)

    def test_the_same_imei_and_serial_enter_again_after_voiding(self):
        unit = self.one()
        self.void(unit)
        [again] = units.receive_units(branch=self.branch, product=self.new128, actor=self.staff, reason='Compra',
                                      units=[{'serial_number': unit.serial_number, 'imei': unit.imei}])
        self.assertEqual(again.status, StockUnit.Status.AVAILABLE)
        self.assertNotEqual(again.label_code, unit.label_code)
        self.shelf_matches_units(self.new128)

    def test_a_written_off_device_still_holds_its_identifiers(self):
        unit = self.one()
        units.write_off(unit, reason='Pantalla rota', actor=self.staff)
        with self.assertRaises(units.StockUnitError):
            units.receive_units(branch=self.branch, product=self.new128, actor=self.staff, reason='Compra',
                                units=[{'serial_number': 'OTRA0001', 'imei': unit.imei}])

    def test_voiding_twice_writes_nothing_the_second_time(self):
        unit = self.one()
        self.void(unit)
        movements = StockMovement.objects.count()
        again = self.void(unit)
        self.assertEqual(again.status, StockUnit.Status.VOIDED)
        self.assertEqual(StockMovement.objects.count(), movements)

    def test_a_reason_and_a_known_reason_code_are_required(self):
        unit = self.one()
        with self.assertRaises(units.StockUnitError) as caught:
            self.void(unit, reason='  ')
        self.assertEqual(caught.exception.field, 'reason')
        with self.assertRaises(units.StockUnitError) as caught:
            self.void(unit, reason_code='porque sí')
        self.assertEqual(caught.exception.field, 'reason_code')
        self.assertEqual(StockUnit.objects.get(pk=unit.pk).status, StockUnit.Status.AVAILABLE)


class NotCorrectableTest(VoidBase):
    def assert_refused(self, unit):
        movements = StockMovement.objects.count()
        with self.assertRaises(units.StockUnitError) as caught:
            self.void(unit)
        self.assertEqual(caught.exception.code, 'unit_not_correctable')
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(StockMovement.objects.count(), movements)

    def test_a_reserved_device_is_refused(self):
        unit = self.one()
        units.reserve(unit, reason='Para Ana', actor=self.staff)
        self.assert_refused(unit)

    def test_a_written_off_device_is_refused(self):
        unit = self.one()
        units.write_off(unit, reason='Robado', actor=self.staff)
        self.assert_refused(unit)

    def test_a_device_reserved_and_released_has_history(self):
        unit = self.one()
        units.reserve(unit, reason='Para Ana', actor=self.staff)
        units.release(unit, actor=self.staff)
        self.assertEqual(StockUnit.objects.get(pk=unit.pk).status, StockUnit.Status.AVAILABLE)
        self.assert_refused(unit)

    def test_a_device_whose_entry_line_is_missing_is_refused(self):
        unit = self.one()
        StockMovement.objects.filter(product=self.new128).update(metadata={})
        self.assert_refused(unit)


class VoidedReadersTest(VoidBase):
    def test_find_unit_says_the_device_was_voided(self):
        unit = self.one()
        self.void(unit)
        for code in (unit.imei, unit.serial_number, unit.label_code):
            with self.subTest(code), self.assertRaises(unit_identification.UnitIdentificationError) as caught:
                unit_identification.find_unit(self.company, code)
            self.assertEqual(caught.exception.code, 'not_found')
            self.assertIn('anulado', str(caught.exception))

    def test_find_unit_returns_the_live_device_never_ambiguous(self):
        unit = self.one()
        self.void(unit)
        [again] = units.receive_units(branch=self.branch, product=self.new128, actor=self.staff, reason='Compra',
                                      units=[{'serial_number': unit.serial_number, 'imei': unit.imei}])
        self.assertEqual(unit_identification.find_unit(self.company, unit.imei).pk, again.pk)
        self.assertEqual(unit_identification.find_unit(self.company, unit.serial_number).pk, again.pk)

    def test_the_cost_of_a_voided_device_is_history(self):
        unit = self.one()
        self.void(unit)
        with self.assertRaises(units.StockUnitError):
            units.update_unit(StockUnit.objects.get(pk=unit.pk), actor=self.staff, cost='10.00')

    def test_a_voided_device_does_not_count_as_a_device_of_the_variant(self):
        from store import device_catalog
        unit = self.one()
        self.void(unit)
        self.assertEqual(device_catalog.variant_history(self.new128)['units'], 0)


class VoidConstraintTest(VoidBase):
    def test_two_live_rows_with_the_same_imei_are_refused_by_the_database(self):
        unit = self.one()
        with self.assertRaises(IntegrityError), transaction.atomic():
            StockUnit.objects.create(company=self.company, branch=self.branch, product=self.new256,
                                     serial_number='DUP00001', imei=unit.imei)

    def test_a_voided_row_and_a_live_row_may_share_identifiers(self):
        unit = self.one()
        self.void(unit)
        StockUnit.objects.create(company=self.company, branch=self.branch, product=self.new128,
                                 serial_number=unit.serial_number, imei=unit.imei, status='written_off')
        self.assertEqual(StockUnit.objects.filter(imei=unit.imei).count(), 2)

    def test_a_voided_row_needs_its_date(self):
        unit = self.one()
        with self.assertRaises(IntegrityError), transaction.atomic():
            StockUnit.objects.filter(pk=unit.pk).update(status='voided')


class VoidApiTest(VoidBase):
    def url(self, unit):
        return f'/api/admin/inventory/units/{unit.pk}/void/'

    def test_staff_with_inventory_adjust_voids_through_the_panel(self):
        unit = self.one()
        res = _client(self.staff).post(self.url(unit), {'reason': 'Duplicado', 'reason_code': 'duplicate'},
                                       format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['status'], 'voided')
        self.assertNotIn('cost', res.json())

    def test_a_refusal_is_a_409_with_its_code(self):
        unit = self.one()
        units.reserve(unit, reason='Para Ana', actor=self.staff)
        res = _client(self.staff).post(self.url(unit), {'reason': 'Duplicado', 'reason_code': 'duplicate'},
                                       format='json')
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'unit_not_correctable')

    def test_without_inventory_adjust_it_is_forbidden(self):
        viewer, _ = _p2d_member(self.company, 'unit_void_viewer', ['company.view', 'inventory.view'])
        unit = self.one()
        res = _client(viewer).post(self.url(unit), {'reason': 'Duplicado', 'reason_code': 'duplicate'},
                                   format='json')
        self.assertEqual(res.status_code, 403, res.content)
        self.assertEqual(StockUnit.objects.get(pk=unit.pk).status, StockUnit.Status.AVAILABLE)

    def test_the_list_hides_voided_devices_unless_asked(self):
        unit, other = self.receive(self.new128, n=2)
        self.void(unit)
        client = _client(self.staff)
        ids = [r['id'] for r in client.get('/api/admin/inventory/units/').json()['results']]
        self.assertNotIn(unit.pk, ids)
        self.assertIn(other.pk, ids)
        ids = [r['id'] for r in client.get('/api/admin/inventory/units/', {'status': 'voided'}).json()['results']]
        self.assertEqual(ids, [unit.pk])


class FreedIdentifierEntersEverywhereTest(VoidBase):
    def test_the_receipt_check_and_the_excel_import_accept_a_freed_imei(self):
        unit = self.one()
        self.void(unit)
        cleaned = units.clean_unit(self.new128, {'serial_number': unit.serial_number, 'imei': unit.imei})
        self.assertEqual(units.find_conflict(self.company, self.new128, cleaned), ('', ''))
        self.assertEqual(units.serial_elsewhere(self.company, self.new256, unit.serial_number), '')


class VoidMigrationReverseGuardTest(VoidBase):
    """0175 backward: it STOPS, naming ids and never identifiers, while a voided row shares them with a live one."""

    def guard(self):
        import importlib

        from django.apps import apps
        return importlib.import_module('store.migrations.0175_stock_unit_void').refuse_shared_identifiers(apps, None)

    def test_without_shared_identifiers_the_reversal_may_proceed(self):
        self.void(self.one())
        self.guard()

    def test_a_voided_row_and_its_re_entered_device_stop_the_reversal(self):
        unit = self.one()
        self.void(unit)
        [again] = units.receive_units(branch=self.branch, product=self.new128, actor=self.staff, reason='Compra',
                                      units=[{'serial_number': unit.serial_number, 'imei': unit.imei}])
        with self.assertRaises(RuntimeError) as caught:
            self.guard()
        text = str(caught.exception)
        self.assertIn(str(unit.pk), text)
        self.assertIn(str(again.pk), text)
        self.assertNotIn(unit.imei, text)
        self.assertNotIn(unit.serial_number, text)


class VoidReviewTest(VoidBase):
    """Review of phase 1: what the audit keeps, repeats, edits, scope, follow-up and the Excel door."""

    def test_a_reason_naming_the_imei_never_reaches_the_audit_and_the_audit_links_the_lines(self):
        unit = self.one()
        entry = StockMovement.objects.get(product=self.new128)
        self.void(unit, reason=f'El IMEI correcto es {unit.imei}, serie {unit.serial_number}')
        log = AdminAuditLog.objects.filter(action='stock_unit_voided').get()
        text = str(log.metadata)
        self.assertNotIn(unit.imei, text)
        self.assertNotIn(unit.serial_number, text)
        line = StockMovement.objects.get(reference_type='stock_unit_void')
        self.assertEqual(log.metadata.get('movement_id'), line.pk)
        self.assertEqual(log.metadata.get('voids_movement_id'), entry.pk)
        self.assertEqual(log.metadata.get('reason_code'), 'typo')

    def test_voiding_again_without_a_code_answers_the_record(self):
        unit = self.one()
        self.void(unit)
        again = units.void_unit(unit, reason='', reason_code='', actor=self.staff)
        self.assertEqual(again.status, StockUnit.Status.VOIDED)

    def test_no_detail_of_a_voided_device_is_edited(self):
        unit = self.one()
        self.void(unit)
        with self.assertRaises(units.StockUnitError) as caught:
            units.update_unit(StockUnit.objects.get(pk=unit.pk), actor=self.staff, notes='otra nota')
        self.assertEqual(caught.exception.status, 409)

    def test_a_device_of_another_company_is_not_found(self):
        unit = self.one()
        other = DeviceShop()
        other.build('unit-void-otra')
        res = _client(other.staff).post(f'/api/admin/inventory/units/{unit.pk}/void/',
                                        {'reason': 'x', 'reason_code': 'typo'}, format='json')
        self.assertEqual(res.status_code, 404, res.content)
        self.assertEqual(StockUnit.objects.get(pk=unit.pk).status, StockUnit.Status.AVAILABLE)

    def test_the_reception_follow_up_no_longer_counts_a_voided_device(self):
        from store import receipt_services
        first, second = self.receive(self.used128, n=2)
        entry = StockMovement.objects.get(product=self.used128)
        record = {'branch_id': self.branch.pk, 'lines': [
            {'index': 0, 'product_id': self.used128.pk, 'movement_id': entry.pk, 'kind': 'units', 'received': 2}]}
        before = receipt_services.follow_up(self.company, record)[0]
        self.assertEqual(before['units_without_battery'], 2)
        self.void(first, reason_code='not_arrived', reason='Nunca llegó')
        after = receipt_services.follow_up(self.company, record)[0]
        self.assertEqual(after['units_without_battery'], 1)
        self.assertEqual(after['units_unpriced'], before['units_unpriced'] - 1)

    def test_the_excel_import_accepts_a_freed_imei_and_serial(self):
        from store import unit_import_services
        from store.test_cost_imports import upload
        unit = self.one()
        self.void(unit)
        headers = ['Código', 'Producto', 'Sucursal', 'Número de serie', 'IMEI', 'IMEI 2', 'Condición', 'Costo',
                   'Motivo / referencia']
        job = unit_import_services.preview_units(
            company=self.company, actor=self.staff,
            upload=upload(headers, [['', self.new128.name, '', unit.serial_number, unit.imei, '', 'Nuevo', '', '']],
                          title='Equipos'),
            filename='equipos.xlsx', reachable_branch_ids=[self.branch.pk], default_branch=self.branch.pk,
            default_reason='Compra',
        )
        [row] = list(job.rows.all())
        self.assertEqual(row.errors, [])
        self.assertEqual(row.action, 'create')

    def test_two_voided_rows_with_one_imei_still_say_voided(self):
        unit = self.one()
        self.void(unit)
        [again] = units.receive_units(branch=self.branch, product=self.new128, actor=self.staff, reason='Compra',
                                      units=[{'serial_number': 'OTRA0002', 'imei': unit.imei}])
        self.void(again)
        with self.assertRaises(unit_identification.UnitIdentificationError) as caught:
            unit_identification.find_unit(self.company, unit.imei)
        self.assertIn('anulado', str(caught.exception))
