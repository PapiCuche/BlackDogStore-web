"""
COST-WRITE-AUTHORITY (decisión C del propietario) — quien no tiene autoridad
sobre los costos no los escribe por ninguna puerta.

    «Rechazar en servidor valores de costo enviados por usuarios sin
    autorización para gestionarlos. [...] Conservar recepción sin acceso a
    costos cuando esté permitida.»

La autoridad es `costs.view` («Ver costos y márgenes»): el sistema no tiene un
permiso aparte para gestionar costos, y es la misma que ya exigían la
recepción (`costs_forbidden`), la corrección del costo de un equipo y el costo
de referencia del producto.

  * «Registrar equipos» (`POST /api/admin/inventory/units/`): un costo no
    vacío sin `costs.view` → 400 `costs_forbidden`, sin escribir nada. Sin
    costo (vacío o ausente) se registra como siempre.
  * Cargas de Excel (equipos, stock, productos): en la PREVISUALIZACIÓN, una
    celda de costo llena sin `costs.view` es un error de esa fila — el mismo
    texto, diga lo que diga la celda — y una carga con errores no se aplica.
    Al APLICAR se vuelve a preguntar: un trabajo previsualizado con costos por
    quien sí puede no lo aplica con ellos quien no puede (400
    `costs_forbidden`, nada escrito, el trabajo sigue previsualizado).
  * Quien tiene `costs.view` sigue igual.
"""
from decimal import Decimal

from django.test import TestCase

from store import cost_services, import_services, stock_import_services, unit_import_services
from store import inventory_services as inventory
from store.models import BulkImportJob, Product, ProductBarcode, StockMovement, StockUnit
from store.test_cost_imports import _client, _Shop, upload
from store.test_stock_units import IMEI_A, IMEI_B, IMEI_C, UnitsBase

D = Decimal
UNITS = '/api/admin/inventory/units/'


# ---------------------------------------------------------------------------
# 1 · «Registrar equipos»
# ---------------------------------------------------------------------------

class RegisterUnitsCostAuthorityTest(UnitsBase):
    """`self.staff` holds the legacy inventory role: it moves stock, it does not hold `costs.view`."""

    def setUp(self):
        super().setUp()
        from rest_framework.test import APIClient

        self.api = APIClient()
        self.api.force_authenticate(user=self.staff)

    def post(self, *rows):
        return self.api.post(UNITS, {
            'product_id': self.phone.pk, 'branch': self.branch_a.pk, 'reason': 'Compra',
            'units': list(rows),
        }, format='json')

    def test_a_cost_without_costs_view_is_refused_and_nothing_is_written(self):
        movements = StockMovement.objects.count()
        res = self.post(self.row('SERIEC0001', IMEI_A, cost='3700.00'))
        self.assertEqual(res.status_code, 400, res.content)
        body = res.json()
        self.assertEqual(body['code'], 'costs_forbidden')
        self.assertEqual(body['field'], 'cost')
        self.assertIn('Ver costos y márgenes', body['detail'])
        self.assertNotIn('3700', res.content.decode())
        self.assertFalse(StockUnit.objects.filter(serial_number='SERIEC0001').exists())
        self.assertEqual(StockMovement.objects.count(), movements)
        self.assertEqual(self.quantity(), 0)

    def test_one_costed_row_refuses_the_whole_batch(self):
        res = self.post(self.row('SERIEC0002', IMEI_A), self.row('SERIEC0003', IMEI_B, cost='1'))
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'costs_forbidden')
        self.assertFalse(StockUnit.objects.filter(serial_number__in=['SERIEC0002', 'SERIEC0003']).exists())

    def test_any_non_empty_cost_counts_even_zero_or_nonsense(self):
        for index, value in enumerate(['0', '-1', 'caro', 12]):
            res = self.post(self.row(f'SERIEC01{index}', IMEI_A, cost=value))
            self.assertEqual((res.status_code, res.json().get('code')), (400, 'costs_forbidden'), value)

    def test_receiving_without_a_cost_stays_possible(self):
        res = self.post(self.row('SERIEC0004', IMEI_A), self.row('SERIEC0005', IMEI_B, cost=''),
                        self.row('SERIEC0006', IMEI_C, cost=None))
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(
            list(StockUnit.objects.filter(serial_number__startswith='SERIEC000').values_list('cost', flat=True)),
            [None, None, None])
        self.assertEqual(self.quantity(), 3)

    def test_costs_view_still_registers_with_the_cost(self):
        from store.tests import _assign, _role

        _assign(self.membership, _role(self.company, 'Rol con costos', slug='rol-con-costos', capabilities=[
            'company.view', 'inventory.view', 'inventory.adjust', 'costs.view']))
        # Holding the authority, a malformed cost is still a malformed cost (the check the old
        # `refused(cost='-1')` of test_stock_units made for everybody).
        bad = self.post(self.row('SERIEC0009', IMEI_B, cost='-1'))
        self.assertEqual(bad.status_code, 400, bad.content)
        self.assertEqual(bad.json()['field'], 'cost')
        self.assertNotIn('code', bad.json())
        res = self.post(self.row('SERIEC0008', IMEI_A, cost='3700.00'))
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['results'][0]['cost'], '3700.00')
        self.assertEqual(StockUnit.objects.get(serial_number='SERIEC0008').cost, D('3700.00'))


# ---------------------------------------------------------------------------
# 2 · Cargas de Excel
# ---------------------------------------------------------------------------

class _NoCostShop(_Shop):
    def setUp(self):
        super().setUp()
        # Moves stock and manages the catalogue, but does not hold `costs.view`.
        from store.tests import _p2d_member

        self.stocker, _ = _p2d_member(self.company, 'cwa_almacen', [
            'company.view', 'products.view', 'products.manage', 'inventory.view', 'inventory.adjust'])


class UnitImportCostAuthorityTest(_NoCostShop):
    HEADERS = ['Código', 'Producto', 'Sucursal', 'Número de serie', 'IMEI', 'IMEI 2', 'Condición', 'Costo',
               'Motivo / referencia']

    def setUp(self):
        super().setUp()
        from store.tests import _prod

        self.phone = _prod(self.company, 'iPhone 16', 'iphone-cwa', price='4500.00', inventory=0)
        Product.objects.filter(pk=self.phone.pk).update(is_serialized=True, requires_imei=True)
        ProductBarcode.objects.create(company=self.company, product=self.phone, code='IP16',
                                      symbology=ProductBarcode.INTERNAL)

    def file(self, cost, serial='SERCWA01', imei=IMEI_A):
        return upload(self.HEADERS, [['IP16', '', self.branch.name, serial, imei, '', 'Nuevo', cost, 'Compra']],
                      title='Equipos')

    def preview(self, cost, actor):
        return unit_import_services.preview_units(
            company=self.company, actor=actor, upload=self.file(cost), filename='equipos.xlsx',
            reachable_branch_ids=[self.branch.pk])

    def test_a_filled_cost_without_costs_view_is_a_row_error_and_the_file_does_not_apply(self):
        job = self.preview('3700', self.stocker)
        row = job.rows.get()
        self.assertEqual(row.action, 'error')
        self.assertIn(cost_services.COSTS_IN_FILE, row.errors)
        self.assertEqual(row.normalized_data.get('cost', ''), '')     # the value is not even staged
        with self.assertRaises(unit_import_services.UnitImportError):
            unit_import_services.apply_units(job=job, actor=self.stocker)
        self.assertFalse(StockUnit.objects.filter(serial_number='SERCWA01').exists())

    def test_whatever_the_cell_says_the_row_reads_the_same(self):
        shapes = {repr((row.action, row.errors, row.warnings)) for row in (
            self.preview(value, self.stocker).rows.get() for value in ['3700', '0', 'caro', '-1'])}
        self.assertEqual(len(shapes), 1, shapes)

    def test_a_blank_cost_column_loads_as_before(self):
        job = self.preview('', self.stocker)
        self.assertEqual(job.rows_error, 0, list(job.rows.values_list('errors', flat=True)))
        unit_import_services.apply_units(job=job, actor=self.stocker)
        self.assertIsNone(StockUnit.objects.get(serial_number='SERCWA01').cost)

    def test_the_preview_endpoint_says_why(self):
        res = _client(self.stocker).post('/api/admin/inventory/units/import/preview/', {
            'file': self.file('3700'),
        }, format='multipart')
        self.assertEqual(res.status_code, 201, res.content)
        [row] = res.json()['rows']
        self.assertIn(cost_services.COSTS_IN_FILE, row['errors'])
        self.assertNotIn('3700', res.content.decode())

    def test_a_job_previewed_with_costs_is_not_applied_by_somebody_without(self):
        job = self.preview('3700', self.owner)
        self.assertEqual(job.rows_error, 0)
        res = _client(self.stocker).post(f'/api/admin/inventory/units/import/{job.pk}/apply/', {}, format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'costs_forbidden')
        self.assertFalse(StockUnit.objects.filter(serial_number='SERCWA01').exists())
        job.refresh_from_db()
        self.assertEqual(job.status, BulkImportJob.PREVIEWED)
        # Whoever holds the authority applies it, at the file's cost.
        unit_import_services.apply_units(job=job, actor=self.owner)
        self.assertEqual(StockUnit.objects.get(serial_number='SERCWA01').cost, D('3700.00'))


class StockImportCostAuthorityTest(_NoCostShop):
    HEADERS = ['ID', 'CODIGO', 'CODIGO EAN', 'NOMBRE', 'ALMACEN 1 - 11416', 'Costo unitario']

    def preview(self, cost, actor, quantity=10):
        return stock_import_services.preview_stock(
            company=self.company, actor=actor,
            upload=upload(self.HEADERS, [[1, 'FUN-001', '', 'Funda', quantity, cost]], title='Inventario'),
            filename='stock.xlsx', branch_map={4: self.branch.pk}, mode=BulkImportJob.MODE_RECONCILE,
        )

    def enter(self, quantity, cost):
        inventory.create_stock_movement(
            branch=self.branch, product_id=self.case.pk, movement_type=StockMovement.PURCHASE_ENTRY,
            quantity=quantity, reason='Compra', unit_cost=cost,
        )

    def test_a_filled_cost_without_costs_view_is_a_row_error_and_nothing_moves(self):
        self.enter(4, D('10'))
        lines = StockMovement.objects.count()
        job = self.preview('13', self.stocker)
        row = job.rows.get()
        self.assertEqual(row.action, 'error')
        self.assertIn(cost_services.COSTS_IN_FILE, row.errors)
        self.assertNotIn('unit_cost', row.normalized_data)
        with self.assertRaises(stock_import_services.StockImportError):
            stock_import_services.apply_stock(job=job, actor=self.stocker)
        self.assertEqual(StockMovement.objects.count(), lines)

    def test_a_decrease_with_a_cost_is_refused_too(self):
        self.enter(4, D('10'))
        row = self.preview('99', self.stocker, quantity=1).rows.get()
        self.assertIn(cost_services.COSTS_IN_FILE, row.errors)

    def test_whatever_the_cell_says_the_row_reads_the_same(self):
        self.enter(4, D('10'))
        shapes = {repr((row.action, row.errors, row.warnings)) for row in (
            self.preview(value, self.stocker).rows.get() for value in ['13', '10', '0', 'gratis'])}
        self.assertEqual(len(shapes), 1, shapes)

    def test_a_blank_cost_column_loads_as_before(self):
        self.enter(4, D('10'))
        job = self.preview('', self.stocker)
        self.assertEqual(job.rows_error, 0)
        stock_import_services.apply_stock(job=job, actor=self.stocker)
        line = StockMovement.objects.filter(product=self.case).order_by('-pk').first()
        self.assertEqual((line.quantity, line.unit_cost), (6, None))

    def test_a_job_previewed_with_costs_is_not_applied_by_somebody_without(self):
        self.enter(4, D('10'))
        job = self.preview('13', self.owner)
        self.assertEqual(job.rows_error, 0)
        lines = StockMovement.objects.count()
        res = _client(self.stocker).post(f'/api/admin/inventory/import/{job.pk}/apply/', {}, format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'costs_forbidden')
        self.assertEqual(StockMovement.objects.count(), lines)
        job.refresh_from_db()
        self.assertEqual(job.status, BulkImportJob.PREVIEWED)


class ProductImportCostAuthorityTest(_NoCostShop):
    HEADERS = ['Código', 'Nombre', 'Precio de venta', 'Costo de referencia']

    def preview(self, rows, actor):
        return import_services.preview_products(
            company=self.company, actor=actor, upload=upload(self.HEADERS, rows), filename='carga.xlsx',
        )

    def test_a_filled_reference_cost_without_costs_view_is_a_row_error(self):
        Product.objects.filter(pk=self.case.pk).update(reference_cost=D('20.00'))
        job = self.preview([['FUN-001', 'Funda', '59.90', '35.00'], ['NEW-777', 'Cable', '20.00', '9.00']],
                           self.stocker)
        for row in job.rows.all():
            self.assertEqual(row.action, 'error')
            self.assertIn(cost_services.COSTS_IN_FILE, row.errors)
            self.assertNotIn('reference_cost', row.normalized_data)
        with self.assertRaises(import_services.ImportError_):
            import_services.apply_products(job=job, actor=self.stocker)
        self.case.refresh_from_db()
        self.assertEqual(self.case.reference_cost, D('20.00'))
        self.assertFalse(Product.objects.filter(name='Cable').exists())

    def test_a_blank_reference_cost_column_loads_as_before(self):
        job = self.preview([['NEW-777', 'Cable', '20.00', '']], self.stocker)
        self.assertEqual(job.rows_error, 0)
        import_services.apply_products(job=job, actor=self.stocker)
        self.assertIsNone(Product.objects.get(name='Cable').reference_cost)

    def test_a_job_previewed_with_costs_is_not_applied_by_somebody_without(self):
        Product.objects.filter(pk=self.case.pk).update(reference_cost=D('20.00'))
        job = self.preview([['FUN-001', 'Funda', '59.90', '35.00']], self.owner)
        res = _client(self.stocker).post(f'/api/admin/products/import/{job.pk}/apply/', {}, format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'costs_forbidden')
        self.case.refresh_from_db()
        self.assertEqual((self.case.reference_cost, self.case.price), (D('20.00'), D('59.90')))
        job.refresh_from_db()
        self.assertEqual(job.status, BulkImportJob.PREVIEWED)


class ReceiptStaysTheReferenceTest(TestCase):
    """The reception's own refusal is the model this decision extends: its code is the shared one."""

    def test_the_code_is_shared(self):
        from store import receipt_services

        self.assertEqual(cost_services.COSTS_FORBIDDEN, 'costs_forbidden')
        self.assertEqual(receipt_services.COSTS_REQUIRED, cost_services.COSTS_REQUIRED)
