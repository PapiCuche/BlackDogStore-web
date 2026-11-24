"""
INV-LOAD-01 — cargar el stock REAL de la empresa (equipos nuevos y usados) sin compras ficticias.

    1. El inventario inicial entra en VARIAS tandas: un producto en una sucursal admite otra carga inicial mientras
       su Kardex en esa sucursal sólo tenga líneas de inventario inicial. Una venta, una compra, un traslado o una
       corrección la cierran, con el mensaje de siempre. Misma regla en «Recibir mercadería» y en la carga de equipos.
    2. La plantilla de equipos lleva «Proveedor» (nombre o RUC, sólo de esta empresa), «Fecha de ingreso» (no futura,
       no antes de 2000; vacía = hoy), «Notas internas» y «Detalles para el comprador».
    3. Un número de serie que ya existe en OTRO producto es un AVISO de la fila, no un error.
    4. La previsualización simula el resultado por sucursal × producto (antes → después) y cuenta los movimientos por
       tipo; lo que la simulación dice es lo que el registro hace.
    5. «Registrar equipo» pide el tipo de ingreso (inventario inicial / compra), y opcionalmente proveedor y fecha.
    6. Unas notas demasiado largas son un error claro, nunca un recorte silencioso.

Los IMEI son sintéticos (dígito de control Luhn), nunca de un equipo real.
"""
import datetime
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from openpyxl import Workbook, load_workbook

from store import inventory_services as inventory
from store import receipt_services
from store import stock_unit_services as units
from store import unit_import_services as unit_import
from store.models import BranchStock, StockMovement, StockUnit, Supplier
from store.test_inventory_receipts import ReceiptBase, unit as receipt_unit
from store.test_stock_units import UnitsBase, imei
from store.test_unit_import import PREVIEW_URL, TEMPLATE_URL, XLSX, ImportBase, apply_url

#: Luhn-valid synthetic IMEIs, none of them used by the older suites.
IMEIS = [imei(f'35693803564{n:03d}') for n in range(500, 560)]

COLUMNS = ['Código', 'Sucursal', 'Número de serie', 'IMEI', 'Condición', 'Costo', 'Motivo / referencia',
           'Proveedor', 'Fecha de ingreso', 'Notas internas', 'Detalles para el comprador']


def book(rows, headers=COLUMNS):
    wb = Workbook()
    sheet = wb.active
    sheet.title = 'Equipos'
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(h, '') for h in headers])
    out = io.BytesIO()
    wb.save(out)
    return SimpleUploadedFile('equipos.xlsx', out.getvalue(), content_type=XLSX)


def today():
    return timezone.localdate()


class LoadBase(ImportBase):
    def setUp(self):
        super().setUp()
        self._imeis = iter(IMEIS)
        self.supplier = Supplier.objects.create(company=self.company, name='Importadora Andina',
                                                document_number='20123456789')
        self.foreign_supplier = Supplier.objects.create(company=self.other, name='Proveedor Ajeno')

    def phone_cells(self, serial, branch=None, **over):
        cells = {'Código': 'IP16-256-N', 'Sucursal': (branch or self.branch_a).name, 'Número de serie': serial,
                 'IMEI': next(self._imeis), 'Condición': 'Nuevo', 'Motivo / referencia': 'Conteo inicial'}
        cells.update(over)
        return cells

    def laptop_cells(self, serial, branch=None, **over):
        cells = {'Código': 'MBA-13-M3', 'Sucursal': (branch or self.branch_a).name, 'Número de serie': serial,
                 'Condición': 'Nuevo', 'Motivo / referencia': 'Conteo inicial'}
        cells.update(over)
        return cells

    def upload(self, rows, client=None, **form):
        response = (client or self.api).post(PREVIEW_URL, {'file': book(rows), **form}, format='multipart')
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def apply(self, payload, client=None, expect=200):
        response = (client or self.api).post(apply_url(payload['id']))
        self.assertEqual(response.status_code, expect, response.content)
        return response.json()

    def row_of(self, payload, number):
        return next(row for row in payload['rows'] if row['row'] == number)

    def kinds(self, product=None, branch=None):
        return list(StockMovement.objects.filter(
            product=product or self.phone, branch=branch or self.branch_a,
        ).order_by('pk').values_list('movement_type', flat=True))


# ---------------------------------------------------------------------------
# 1. Opening stock in batches
# ---------------------------------------------------------------------------

class OpeningInBatchesImportTest(LoadBase):
    def test_two_opening_files_for_the_same_shelf_are_both_initial_stock(self):
        first = self.upload([self.phone_cells('OPB-0001'), self.phone_cells('OPB-0002')], entry_kind='opening')
        self.assertEqual(first['counts']['error'], 0, first['rows'])
        self.apply(first)

        second = self.upload([self.phone_cells('OPB-0003')], entry_kind='opening')
        self.assertEqual(second['counts']['error'], 0, second['rows'])
        self.apply(second)

        self.assertEqual(self.kinds(), [StockMovement.INITIAL_STOCK, StockMovement.INITIAL_STOCK])
        self.assertFalse(StockMovement.objects.filter(movement_type=StockMovement.PURCHASE_ENTRY).exists())
        self.assertEqual(inventory.branch_quantity(self.branch_a, self.phone), 3)

    def test_a_receipt_opening_then_a_file_opening(self):
        units.receive_units(branch=self.branch_a, product=self.phone, actor=self.staff, reason='Inventario inicial',
                            units=[{'serial_number': 'OPB-0101', 'imei': next(self._imeis)}],
                            movement_type=StockMovement.INITIAL_STOCK)
        payload = self.upload([self.phone_cells('OPB-0102')], entry_kind='opening')
        self.assertEqual(payload['counts']['error'], 0, payload['rows'])
        self.apply(payload)
        self.assertEqual(self.kinds(), [StockMovement.INITIAL_STOCK] * 2)

    def assert_opening_closed(self):
        payload = self.upload([self.phone_cells('OPB-CLOSED')], entry_kind='opening')
        self.assertEqual(payload['counts']['error'], 1)
        errors = ' '.join(payload['rows'][0]['errors'])
        self.assertIn('inventario inicial', errors)
        self.assertIn('compra', errors)

    def test_a_purchase_closes_the_opening(self):
        self.apply(self.upload([self.phone_cells('OPB-0201')], entry_kind='opening'))
        self.apply(self.upload([self.phone_cells('OPB-0202')]))                 # a purchase
        self.assert_opening_closed()

    def test_a_sale_closes_the_opening(self):
        self.apply(self.upload([self.phone_cells('OPB-0301'), self.phone_cells('OPB-0302')], entry_kind='opening'))
        inventory.record_sale_stock_movements(self.paid_order(), strict=True)
        self.assert_opening_closed()

    def test_a_write_off_closes_the_opening(self):
        self.apply(self.upload([self.phone_cells('OPB-0401')], entry_kind='opening'))
        units.write_off(StockUnit.objects.get(serial_number='OPB-0401'), reason='Pantalla rota', actor=self.staff)
        self.assert_opening_closed()

    def test_another_branch_keeps_its_own_opening(self):
        self.apply(self.upload([self.phone_cells('OPB-0501')]))                 # purchase in A
        payload = self.upload([self.phone_cells('OPB-0502', branch=self.branch_b)], entry_kind='opening')
        self.assertEqual(payload['counts']['error'], 0, payload['rows'])

    def test_stock_no_opening_line_explains_still_refuses(self):
        self.apply(self.upload([self.phone_cells('OPB-0601')], entry_kind='opening'))
        BranchStock.objects.filter(branch=self.branch_a, product=self.phone).update(quantity=5)
        payload = self.upload([self.phone_cells('OPB-0602')], entry_kind='opening')
        self.assertEqual(payload['counts']['error'], 1, payload['rows'])


class OpeningInBatchesReceiptTest(ReceiptBase):
    def setUp(self):
        super().setUp()
        from store.tests import _prod

        self.adapter = _prod(self.company, 'Adaptador 20W', 'adaptador-tandas', price='79.00', inventory=0)

    def test_two_opening_receipts_of_the_same_shelf(self):
        self.confirm(self.costed, self.body(self.qty(self.adapter, 3), entry_kind='opening'), expect=201)
        body = self.body(self.qty(self.adapter, 2), entry_kind='opening')
        self.assertTrue(self.preview(self.costed, body, expect=200).json()['ok'])
        self.confirm(self.costed, body, expect=201)
        kinds = list(StockMovement.objects.filter(product=self.adapter).values_list('movement_type', flat=True))
        self.assertEqual(kinds, [StockMovement.INITIAL_STOCK] * 2)
        self.assertEqual(BranchStock.objects.get(branch=self.centro, product=self.adapter).quantity, 5)

    def test_devices_in_two_opening_receipts(self):
        for serial, value in (('OPR-1', IMEIS[40]), ('OPR-2', IMEIS[41])):
            self.confirm(self.costed, self.body(self.dev(self.phone, receipt_unit(serial, value)),
                                                entry_kind='opening'), expect=201)
        kinds = list(StockMovement.objects.filter(product=self.phone).values_list('movement_type', flat=True))
        self.assertEqual(kinds, [StockMovement.INITIAL_STOCK] * 2)

    def test_an_exit_after_the_opening_closes_it(self):
        self.confirm(self.costed, self.body(self.qty(self.adapter, 3), entry_kind='opening'), expect=201)
        inventory.apply_manual_stock_movement(
            branch=self.centro, product_id=self.adapter.pk, movement_type=StockMovement.MANUAL_EXIT, quantity=1,
            reason='Muestra', actor=self.costed)
        data = self.preview(self.costed, self.body(self.qty(self.adapter, 1), entry_kind='opening'),
                            expect=200).json()
        self.assertFalse(data['ok'])
        self.assertIn(receipt_services.OPENING_HAS_HISTORY, data['lines'][0]['codes'])

    def test_unit_notes_over_the_limit_are_an_error_not_a_cut(self):
        res = self.preview(self.costed, self.body(self.dev(self.phone, receipt_unit('OPR-N', IMEIS[42],
                                                                                    notes='x' * 301))))
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn('notes', res.json().get('field', ''))


# ---------------------------------------------------------------------------
# 2. The new columns
# ---------------------------------------------------------------------------

class TemplateColumnsTest(LoadBase):
    def test_the_template_offers_the_new_columns_with_notes(self):
        response = self.api.get(TEMPLATE_URL)
        sheet = load_workbook(io.BytesIO(response.content))['Equipos']
        headers = [cell.value for cell in sheet[1]]
        for header in ('Proveedor', 'Fecha de ingreso', 'Notas internas', 'Detalles para el comprador'):
            self.assertIn(header, headers)
            self.assertTrue(sheet.cell(row=1, column=headers.index(header) + 1).comment, header)


class SupplierColumnTest(LoadBase):
    def test_by_name_or_document_and_written_on_the_unit(self):
        payload = self.upload([self.phone_cells('SUP-0001', Proveedor='  importadora   ANDINA '),
                               self.phone_cells('SUP-0002', Proveedor='20123456789')])
        self.assertEqual(payload['counts']['error'], 0, payload['rows'])
        self.assertEqual(self.row_of(payload, 2)['data']['supplier'], 'Importadora Andina')
        self.apply(payload)
        self.assertEqual(set(StockUnit.objects.filter(product=self.phone).values_list('supplier_id', flat=True)),
                         {self.supplier.pk})

    def test_a_supplier_of_another_company_is_a_row_error(self):
        payload = self.upload([self.phone_cells('SUP-0101', Proveedor='Proveedor Ajeno')])
        self.assertEqual(payload['counts']['error'], 1)
        self.assertIn('proveedor', ' '.join(payload['rows'][0]['errors']).lower())

    def test_an_unknown_or_inactive_supplier_is_a_row_error(self):
        Supplier.objects.create(company=self.company, name='Dormido', is_active=False)
        payload = self.upload([self.phone_cells('SUP-0201', Proveedor='Nadie SAC'),
                               self.phone_cells('SUP-0202', Proveedor='Dormido')])
        self.assertEqual(payload['counts']['error'], 2, payload['rows'])

    def test_without_the_column_nothing_changes(self):
        payload = self.upload([self.phone_cells('SUP-0301')])
        self.apply(payload)
        self.assertIsNone(StockUnit.objects.get(serial_number='SUP-0301').supplier_id)


class EntryDateColumnTest(LoadBase):
    def test_a_past_date_is_the_received_date(self):
        day = today() - datetime.timedelta(days=40)
        payload = self.upload([self.phone_cells('DAT-0001', **{'Fecha de ingreso': day.isoformat()}),
                               self.phone_cells('DAT-0002', **{'Fecha de ingreso': day.strftime('%d/%m/%Y')}),
                               self.phone_cells('DAT-0003', **{'Fecha de ingreso': datetime.datetime.combine(
                                   day, datetime.time())})])                     # an Excel date cell
        self.assertEqual(payload['counts']['error'], 0, payload['rows'])
        self.apply(payload)
        for unit in StockUnit.objects.filter(product=self.phone):
            self.assertEqual(timezone.localtime(unit.received_at).date(), day)

    def test_empty_is_today(self):
        self.apply(self.upload([self.phone_cells('DAT-0101')]))
        self.assertEqual(timezone.localtime(StockUnit.objects.get(serial_number='DAT-0101').received_at).date(),
                         today())

    def test_future_too_old_or_unreadable_is_a_row_error(self):
        payload = self.upload([
            self.phone_cells('DAT-0201', **{'Fecha de ingreso': (today() + datetime.timedelta(days=1)).isoformat()}),
            self.phone_cells('DAT-0202', **{'Fecha de ingreso': '1999-12-31'}),
            self.phone_cells('DAT-0203', **{'Fecha de ingreso': 'ayer'}),
        ])
        self.assertEqual(payload['counts']['error'], 3, payload['rows'])
        self.assertIn('futura', ' '.join(self.row_of(payload, 2)['errors']))


class NotesColumnsTest(LoadBase):
    def test_internal_notes_are_stored(self):
        self.apply(self.upload([self.phone_cells('NOT-0001', **{'Notas internas': 'Caja con golpe'})]))
        self.assertEqual(StockUnit.objects.get(serial_number='NOT-0001').notes, 'Caja con golpe')

    def test_notes_over_the_limit_are_a_row_error(self):
        payload = self.upload([self.phone_cells('NOT-0101', **{'Notas internas': 'x' * 301})])
        self.assertEqual(payload['counts']['error'], 1)

    def test_buyer_details_on_a_new_device_are_a_warning_and_ignored(self):
        payload = self.upload([self.phone_cells('NOT-0201', **{'Detalles para el comprador': 'Sin rayones'})])
        self.assertEqual(payload['counts']['error'], 0, payload['rows'])
        self.assertTrue(payload['rows'][0]['warnings'])
        self.apply(payload)
        self.assertEqual(StockUnit.objects.get(serial_number='NOT-0201').public_notes, '')


class CostColumnTest(LoadBase):
    def test_a_cost_without_permission_is_still_a_row_error_and_never_echoed(self):
        payload = self.upload([self.phone_cells('CST-0001', Costo='3800.00')])
        self.assertEqual(payload['counts']['error'], 1)
        self.assertNotIn('3800', str(payload))

    def test_with_permission_it_is_written(self):
        client = self.cost_client()
        payload = self.upload([self.phone_cells('CST-0101', Costo='3800.00')], client=client)
        self.assertEqual(payload['counts']['error'], 0, payload['rows'])
        self.apply(payload, client=client)
        self.assertEqual(str(StockUnit.objects.get(serial_number='CST-0101').cost), '3800.00')


# ---------------------------------------------------------------------------
# 3. serial_elsewhere
# ---------------------------------------------------------------------------

class SerialElsewhereImportTest(LoadBase):
    def test_a_serial_of_another_product_is_a_warning(self):
        units.receive_units(branch=self.branch_a, product=self.laptop, actor=self.staff, reason='Compra',
                            units=[{'serial_number': 'DUP-0001'}])
        payload = self.upload([self.phone_cells('DUP-0001')])
        row = payload['rows'][0]
        self.assertEqual(row['action'], 'create', row)
        self.assertTrue(any('MacBook Air' in warning for warning in row['warnings']), row['warnings'])

    def test_the_same_serial_under_two_products_in_one_file(self):
        payload = self.upload([self.laptop_cells('DUP-0101'), self.phone_cells('DUP-0101')])
        self.assertEqual(payload['counts']['error'], 0, payload['rows'])
        self.assertTrue(self.row_of(payload, 3)['warnings'])


# ---------------------------------------------------------------------------
# 4. The simulation
# ---------------------------------------------------------------------------

class SimulationTest(LoadBase):
    def shelves(self, payload):
        return {(s['branch_id'], s['product_id']): s for s in payload['summary']['simulation']['shelves']}

    def test_the_simulation_is_what_the_apply_does(self):
        units.receive_units(branch=self.branch_a, product=self.phone, actor=self.staff, reason='Compra',
                            units=[{'serial_number': 'SIM-OLD', 'imei': next(self._imeis)}])
        payload = self.upload([
            self.phone_cells('SIM-0001'), self.phone_cells('SIM-0002'),
            self.phone_cells('SIM-0003', branch=self.branch_b),
            self.laptop_cells('SIM-0004'), self.laptop_cells('SIM-0005', **{'Motivo / referencia': 'Otro documento'}),
        ])
        self.assertEqual(payload['counts']['error'], 0, payload['rows'])
        simulation = payload['summary']['simulation']
        shelves = self.shelves(payload)
        phone_a = shelves[(self.branch_a.pk, self.phone.pk)]
        self.assertEqual((phone_a['units_before'], phone_a['units_after']), (1, 3))
        self.assertEqual((phone_a['stock_before'], phone_a['stock_after']), (1, 3))
        self.assertEqual(simulation['movements'], {'purchase_entry': 4, 'initial_stock': 0})
        self.assertEqual(simulation['units'], {'purchase_entry': 5, 'initial_stock': 0})

        before = StockMovement.objects.count()
        applied = self.apply(payload)
        for (branch_id, product_id), shelf in shelves.items():
            stock = BranchStock.objects.get(branch_id=branch_id, product_id=product_id).quantity
            available = StockUnit.objects.filter(branch_id=branch_id, product_id=product_id,
                                                 status=StockUnit.Status.AVAILABLE).count()
            self.assertEqual((stock, available), (shelf['stock_after'], shelf['units_after']), shelf)
        written = StockMovement.objects.order_by('pk')[before:]
        self.assertEqual(len(written), simulation['movements']['purchase_entry'])
        self.assertEqual(applied['summary']['applied']['movements'], sum(simulation['movements'].values()))

    def test_an_opening_file_counts_initial_stock(self):
        payload = self.upload([self.phone_cells('SIM-0101'), self.phone_cells('SIM-0102')], entry_kind='opening')
        simulation = payload['summary']['simulation']
        self.assertEqual(simulation['entry_type'], StockMovement.INITIAL_STOCK)
        self.assertEqual(simulation['movements'], {'purchase_entry': 0, 'initial_stock': 1})
        self.apply(payload)
        self.assertEqual(self.kinds(), [StockMovement.INITIAL_STOCK])

    def test_different_suppliers_or_dates_are_different_receptions(self):
        day = (today() - datetime.timedelta(days=3)).isoformat()
        payload = self.upload([self.phone_cells('SIM-0201'), self.phone_cells('SIM-0202', Proveedor='Importadora Andina'),
                               self.phone_cells('SIM-0203', **{'Fecha de ingreso': day})])
        self.assertEqual(payload['summary']['simulation']['movements']['purchase_entry'], 3)
        self.apply(payload)
        self.assertEqual(len(self.kinds()), 3)


# ---------------------------------------------------------------------------
# 5. «Registrar equipo»
# ---------------------------------------------------------------------------

class RegisterEntryTypeTest(UnitsBase):
    URL = '/api/admin/inventory/units/'

    def setUp(self):
        super().setUp()
        from rest_framework.test import APIClient

        self.api = APIClient()
        self.api.force_authenticate(user=self.staff)
        self.supplier = Supplier.objects.create(company=self.company, name='Mayorista Sur')
        self.foreign_supplier = Supplier.objects.create(company=self.other, name='Mayorista Ajeno')
        self._imeis = iter(IMEIS[30:])

    def post(self, serial, **extra):
        body = {'product_id': self.phone.pk, 'branch': self.branch_a.pk, 'reason': 'Conteo inicial',
                'units': [{'serial_number': serial, 'imei': next(self._imeis)}], **extra}
        return self.api.post(self.URL, body, format='json')

    def test_without_entry_kind_it_is_still_a_purchase(self):
        self.assertEqual(self.post('REG-0001').status_code, 201)
        self.assertEqual(StockMovement.objects.get(product=self.phone).movement_type, StockMovement.PURCHASE_ENTRY)

    def test_opening_twice_is_initial_stock_twice(self):
        for serial in ('REG-0101', 'REG-0102'):
            res = self.post(serial, entry_kind='opening')
            self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(list(StockMovement.objects.filter(product=self.phone).values_list('movement_type', flat=True)),
                         [StockMovement.INITIAL_STOCK] * 2)

    def test_opening_after_a_purchase_is_refused(self):
        self.post('REG-0201')
        res = self.post('REG-0202', entry_kind='opening')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json().get('code'), receipt_services.OPENING_HAS_HISTORY)
        self.assertEqual(StockUnit.objects.filter(product=self.phone).count(), 1)

    def test_an_unknown_entry_kind_is_refused(self):
        self.assertEqual(self.post('REG-0301', entry_kind='gift').status_code, 400)

    def test_supplier_and_entry_date(self):
        day = today() - datetime.timedelta(days=10)
        res = self.post('REG-0401', supplier=self.supplier.pk, received_on=day.isoformat())
        self.assertEqual(res.status_code, 201, res.content)
        unit = StockUnit.objects.get(serial_number='REG-0401')
        self.assertEqual(unit.supplier_id, self.supplier.pk)
        self.assertEqual(timezone.localtime(unit.received_at).date(), day)

    def test_a_supplier_of_another_company_is_404(self):
        res = self.post('REG-0501', supplier=self.foreign_supplier.pk)
        self.assertEqual(res.status_code, 404, res.content)
        self.assertFalse(StockUnit.objects.filter(serial_number='REG-0501').exists())

    def test_a_future_entry_date_is_refused(self):
        res = self.post('REG-0601', received_on=(today() + datetime.timedelta(days=2)).isoformat())
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json().get('field'), 'received_on')


# ---------------------------------------------------------------------------
# 6. Notes are validated, not cut
# ---------------------------------------------------------------------------

class NotesLengthTest(UnitsBase):
    def test_receive_units_refuses_long_notes(self):
        with self.assertRaises(units.StockUnitError) as caught:
            self.receive(self.row('LEN-0001', IMEIS[50], notes='x' * 301))
        self.assertEqual(caught.exception.field, 'notes')
        self.assertFalse(StockUnit.objects.filter(serial_number='LEN-0001').exists())

    def test_three_hundred_characters_fit(self):
        [unit] = self.receive(self.row('LEN-0002', IMEIS[51], notes='y' * 300))
        self.assertEqual(len(unit.notes), 300)
