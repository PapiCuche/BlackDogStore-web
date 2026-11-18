"""
SERIAL-OPTIONAL-01 — a device that carries a valid IMEI may enter without a serial number.

    product «lleva IMEI»   IMEI valid   serial      -> result
    yes                    yes          absent      -> enters, `serial_number` NULL (never a placeholder)
    yes                    no           any         -> refused: the IMEI is what identifies it
    no                     any          absent      -> refused: the serial is its only identity

The IMEI is unique in the whole company (`unique_stock_unit_imei_per_company`), so a device without a serial is
still exactly one row. Two devices without a serial of the same product are two devices, never a «repeated serial».
Wherever a person reads which device it is, a device without a serial is named by its IMEI.
"""
from django.test import TestCase

from store import stock_unit_services as units
from store import unit_import_services
from store.models import StockMovement, StockUnit
from store.test_device_commerce import IMEIS, DeviceShop
from store.test_inventory_receipts import ReceiptBase


class ReceiveWithoutSerialTest(DeviceShop, TestCase):
    def setUp(self):
        self.build('serial-optional')

    def receive_rows(self, product, rows):
        return units.receive_units(branch=self.branch, product=product, units=rows, actor=self.staff,
                                   reason='Compra')

    def test_a_device_with_a_valid_imei_enters_without_a_serial(self):
        [unit] = self.receive_rows(self.new128, [{'imei': IMEIS[0]}])
        unit.refresh_from_db()
        self.assertIsNone(unit.serial_number)
        self.assertEqual(unit.imei, IMEIS[0])
        self.assertEqual(StockMovement.objects.filter(product=self.new128).get().quantity, 1)

    def test_a_blank_serial_is_stored_as_null_not_as_text(self):
        [unit] = self.receive_rows(self.new128, [{'serial_number': '   ', 'imei': IMEIS[1]}])
        unit.refresh_from_db()
        self.assertIsNone(unit.serial_number)

    def test_two_devices_without_serial_are_two_devices(self):
        created = self.receive_rows(self.new128, [{'imei': IMEIS[2]}, {'imei': IMEIS[3]}])
        self.assertEqual(len(created), 2)
        more = self.receive_rows(self.new128, [{'imei': IMEIS[4]}])
        self.assertEqual(len(more), 1)
        self.assertEqual(StockUnit.objects.filter(product=self.new128, serial_number__isnull=True).count(), 3)

    def test_the_same_imei_twice_is_still_refused(self):
        self.receive_rows(self.new128, [{'imei': IMEIS[5]}])
        with self.assertRaises(units.StockUnitError) as caught:
            self.receive_rows(self.new256, [{'imei': IMEIS[5]}])
        self.assertEqual(caught.exception.field, 'imei')

    def test_without_serial_and_without_imei_the_imei_is_asked(self):
        with self.assertRaises(units.StockUnitError) as caught:
            self.receive_rows(self.new128, [{}])
        self.assertEqual(caught.exception.field, 'imei')

    def test_a_product_without_imei_still_needs_its_serial(self):
        from store.models import Product
        Product.objects.filter(pk=self.new256.pk).update(requires_imei=False)
        self.new256.refresh_from_db()
        with self.assertRaises(units.StockUnitError) as caught:
            self.receive_rows(self.new256, [{'imei': IMEIS[6]}])
        self.assertEqual(caught.exception.field, 'serial_number')

    def test_a_device_without_serial_is_named_by_its_imei(self):
        [unit] = self.receive_rows(self.new128, [{'imei': IMEIS[7]}])
        self.assertIn(IMEIS[7], str(unit))
        self.assertNotIn('None', str(unit))
        self.assertEqual(unit.identifier, IMEIS[7])
        [other] = self.receive(self.new128)
        self.assertEqual(other.identifier, other.serial_number)

    def test_a_refusal_about_a_device_without_serial_names_its_imei(self):
        [unit] = self.receive_rows(self.new128, [{'imei': IMEIS[8]}])
        StockUnit.objects.filter(pk=unit.pk).update(status=StockUnit.Status.WRITTEN_OFF)
        with self.assertRaises(units.StockUnitError) as caught:
            units.update_unit(unit, actor=self.staff, cost='10.00')
        self.assertIn(IMEIS[8], str(caught.exception))
        self.assertNotIn('None', str(caught.exception))


class ReceiptWithoutSerialTest(ReceiptBase):
    def test_a_reception_of_phones_without_serial_is_accepted(self):
        body = self.body(self.dev(self.phone, {'imei': IMEIS[10], 'condition': 'new'},
                                  {'imei': IMEIS[11], 'condition': 'new'}))
        data = self.preview(self.costed, body, expect=200).json()
        self.assertTrue(data['ok'], data)
        self.assertEqual(data['lines'][0]['errors'], [])
        self.assertEqual(data['lines'][0]['warnings'], [])
        self.confirm(self.costed, body, expect=201)
        self.assertEqual(StockUnit.objects.filter(product=self.phone, serial_number__isnull=True).count(), 2)

    def test_a_laptop_without_serial_is_refused_on_its_field(self):
        body = self.body(self.dev(self.laptop, {'imei': '', 'condition': 'new'}))
        data = self.preview(self.costed, body, expect=200).json()
        self.assertFalse(data['ok'], data)
        self.assertTrue(any('serie' in e for e in data['lines'][0]['errors']), data['lines'][0]['errors'])


class UnitImportWithoutSerialTest(DeviceShop, TestCase):
    HEADERS = ['Código', 'Producto', 'Sucursal', 'Número de serie', 'IMEI', 'IMEI 2', 'Condición', 'Costo',
               'Motivo / referencia', 'Modelo', 'Capacidad', 'Color', 'SIM', 'Región', 'Línea',
               'Estado estético', 'Batería %', 'Precio de venta']

    def setUp(self):
        self.build('serial-optional-import')

    def units_file(self, rows):
        from store.test_cost_imports import upload
        job = unit_import_services.preview_units(
            company=self.company, actor=self.staff, upload=upload(self.HEADERS, rows, title='Equipos'),
            filename='equipos.xlsx', reachable_branch_ids=[self.branch.pk], default_branch=self.branch.pk,
            default_reason='Conteo inicial',
        )
        return job, list(job.rows.order_by('row_number'))

    def row(self, serial, imei, product):
        return ['', product.name, '', serial, imei, '', 'Nuevo', '', '', '', '', '', '', '', '', '', '', '']

    def test_rows_without_serial_but_with_imei_are_created(self):
        job, rows = self.units_file([self.row('', IMEIS[20], self.new128), self.row('', IMEIS[21], self.new128)])
        self.assertEqual([r.errors for r in rows], [[], []])
        self.assertEqual([r.action for r in rows], ['create', 'create'])
        unit_import_services.apply_units(job=job, actor=self.staff)
        self.assertEqual(
            set(StockUnit.objects.filter(product=self.new128).values_list('imei', 'serial_number')),
            {(IMEIS[20], None), (IMEIS[21], None)},
        )

    def test_a_row_without_serial_nor_imei_is_an_error(self):
        _, [row] = self.units_file([self.row('', '', self.new128)])
        self.assertEqual(row.action, 'error')


class StockUnitPayloadTest(DeviceShop, TestCase):
    def setUp(self):
        self.build('serial-optional-api')

    def test_the_panel_list_says_empty_not_null(self):
        from rest_framework.test import APIClient
        units.receive_units(branch=self.branch, product=self.new128, units=[{'imei': IMEIS[25]}],
                            actor=self.staff, reason='Compra')
        client = APIClient()
        client.force_authenticate(self.staff)
        res = client.get('/api/admin/inventory/units/', {'product': self.new128.pk})
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        rows = body['results'] if isinstance(body, dict) and 'results' in body else body
        [row] = [r for r in rows if r.get('imei') == IMEIS[25]]
        self.assertEqual(row['serial_number'], '')
