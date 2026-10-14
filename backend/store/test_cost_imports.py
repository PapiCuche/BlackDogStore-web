"""
DEVICE-COMMERCE-01 · DC1 — los costos en las cargas y descargas de Excel.

  * Productos: «Costo de referencia» (opcional) → `Product.reference_cost`.
    La plantilla POS de 18 columnas lleva «Precio costo», que antes se
    ignoraba con una nota; ahora se lee como costo de referencia.
  * Stock: «Costo unitario» (opcional). Una fila que AUMENTA el stock con
    costo entra a ese costo y mueve el promedio de la sucursal; una que lo
    disminuye lo ignora (y lo dice).
  * Equipos: «Costo» es el costo específico de cada equipo; un equipo ya
    vendido no se vuelve a cargar, y la fila dice que su costo no cambia.
  * Un archivo sin esas columnas se carga como siempre.
  * Lo que el sistema YA sabía de costos (el costo anterior, el promedio) sólo
    se enseña a quien tiene `costs.view`.
"""
import io
from decimal import Decimal

import openpyxl
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from store import import_exports, import_formats, import_services, stock_import_services
from store import inventory_services as inventory
from store import stock_unit_services as units
from store.models import (
    BranchStock, BulkImportJob, Order, OrderItem, Product, ProductBarcode, StockMovement,
)
from store.test_stock_units import IMEI_A, IMEI_B
from store.tests import _p2d_member, _p3_company, _prod

D = Decimal
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def upload(headers, rows, title='Productos'):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = title
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return SimpleUploadedFile('carga.xlsx', out.getvalue(), content_type=XLSX)


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class _Shop(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('dc1-excel', 'Tienda Excel')
        self.branch = self.company.default_inventory_branch
        self.case = _prod(self.company, 'Funda', 'funda-excel', price='59.90', inventory=0)
        ProductBarcode.objects.create(company=self.company, product=self.case, code='FUN-001',
                                      symbology=ProductBarcode.INTERNAL)
        self.owner, _ = _p2d_member(self.company, 'dc1_excel_duena', [
            'company.view', 'products.view', 'products.manage', 'inventory.view', 'inventory.adjust',
            'costs.view'])


class ProductReferenceCostTest(_Shop):
    HEADERS = ['Código', 'Nombre', 'Precio de venta', 'Costo de referencia']

    def preview(self, rows, headers=None, actor=None):
        return import_services.preview_products(
            company=self.company, actor=actor or self.owner,
            upload=upload(headers or self.HEADERS, rows), filename='carga.xlsx',
        )

    def test_the_template_offers_the_column(self):
        sheet = openpyxl.load_workbook(io.BytesIO(import_exports.product_template_bytes(self.company))).active
        self.assertIn('Costo de referencia', [cell.value for cell in sheet[1]])

    def test_a_row_sets_the_reference_cost_and_the_preview_says_what_changes(self):
        Product.objects.filter(pk=self.case.pk).update(reference_cost=D('20.00'))
        job = self.preview([['FUN-001', 'Funda', '59.90', '22.50']])
        row = job.rows.get()
        self.assertEqual(row.errors, [])
        self.assertEqual(row.normalized_data['reference_cost'], '22.50')
        self.assertEqual(row.normalized_data['reference_cost_before'], '20.00')
        self.assertTrue(any('Costo de referencia' in w for w in row.warnings), row.warnings)

        _job, changed = import_services.apply_products(job=job, actor=self.owner)
        self.assertTrue(changed)
        self.case.refresh_from_db()
        self.assertEqual(self.case.reference_cost, D('22.50'))
        # The second click is not a second import.
        self.assertEqual(import_services.apply_products(job=job, actor=self.owner)[1], False)

    def test_a_new_product_is_born_with_it(self):
        job = self.preview([['NEW-001', 'Cargador', '99.00', '40']])
        import_services.apply_products(job=job, actor=self.owner)
        self.assertEqual(Product.objects.get(name='Cargador').reference_cost, D('40.00'))

    def test_blank_changes_nothing_and_nonsense_is_a_row_error(self):
        Product.objects.filter(pk=self.case.pk).update(reference_cost=D('20.00'))
        job = self.preview([['FUN-001', 'Funda', '59.90', '']])
        import_services.apply_products(job=job, actor=self.owner)
        self.case.refresh_from_db()
        self.assertEqual(self.case.reference_cost, D('20.00'))

        bad = self.preview([['FUN-001', 'Funda', '59.90', 'caro']]).rows.get()
        self.assertTrue(any('Costo de referencia' in e for e in bad.errors), bad.errors)
        negative = self.preview([['FUN-001', 'Funda', '59.90', '-1']]).rows.get()
        self.assertTrue(negative.errors)

    def test_an_old_file_without_the_column_loads_unchanged(self):
        Product.objects.filter(pk=self.case.pk).update(reference_cost=D('20.00'))
        job = self.preview([['FUN-001', 'Funda', '61.00']], headers=['Código', 'Nombre', 'Precio de venta'])
        import_services.apply_products(job=job, actor=self.owner)
        self.case.refresh_from_db()
        self.assertEqual((self.case.price, self.case.reference_cost), (D('61.00'), D('20.00')))

    def test_the_pos_template_reads_precio_costo_as_the_reference_cost(self):
        headers = [
            'Código de barras', 'Código', 'Unidad de medida', 'Nombre', 'Peso',
            'Código SUNAT', 'Descripción', 'Precio costo', 'Afectación',
        ]
        detected = import_formats.detect(BulkImportJob.PRODUCTS, 'Productos', headers)
        self.assertEqual(detected['mapping']['reference_cost'], headers.index('Precio costo'))
        notes = import_formats.unmapped_notes(headers, detected['mapping'])
        self.assertNotIn('Precio costo', [note['column'] for note in notes])

    def catalogue_user(self):
        user, _ = _p2d_member(self.company, 'dc1_excel_catalogo',
                              ['company.view', 'products.view', 'products.manage'])
        return user

    def test_without_costs_view_the_previous_cost_is_not_shown(self):
        Product.objects.filter(pk=self.case.pk).update(reference_cost=D('20.00'))
        res = _client(self.catalogue_user()).post('/api/admin/products/import/preview/', {
            'file': upload(self.HEADERS, [['FUN-001', 'Funda', '59.90', '22.50']]),
        }, format='multipart')
        self.assertEqual(res.status_code, 201, res.content)
        [row] = res.json()['rows']
        self.assertNotIn('reference_cost_before', row['data'])
        self.assertNotIn('20.00', res.content.decode())
        # DC1 review: the column is not read at all for this caller.
        self.assertNotIn('reference_cost', row['data'])
        self.assertIn(import_services.COST_COLUMN_IGNORED, row['warnings'])

    def test_without_costs_view_no_value_of_the_column_tells_the_stored_cost(self):
        """The equality probe: whatever the cell says, the row reads the same."""
        Product.objects.filter(pk=self.case.pk).update(reference_cost=D('20.00'))
        client = _client(self.catalogue_user())
        shapes = set()
        for value in ['20.00', '20', '21.00', '19.99', '0', 'caro', '-1']:
            res = client.post('/api/admin/products/import/preview/', {
                'file': upload(self.HEADERS, [['FUN-001', 'Funda', '59.90', value]]),
            }, format='multipart')
            self.assertEqual(res.status_code, 201, res.content)
            [row] = res.json()['rows']
            shapes.add(repr((row['action'], row['errors'], row['warnings'], sorted(row['data'].items()))))
        self.assertEqual(len(shapes), 1, shapes)

    def test_without_costs_view_apply_leaves_the_reference_cost_alone(self):
        Product.objects.filter(pk=self.case.pk).update(reference_cost=D('20.00'))
        catalogue = self.catalogue_user()
        job = self.preview([['FUN-001', 'Funda', '59.90', '35.00']], actor=catalogue)
        import_services.apply_products(job=job, actor=catalogue)
        self.case.refresh_from_db()
        self.assertEqual(self.case.reference_cost, D('20.00'))
        new = self.preview([['NEW-777', 'Cable', '20.00', '9.00']], actor=catalogue)
        import_services.apply_products(job=new, actor=catalogue)
        self.assertIsNone(Product.objects.get(name='Cable').reference_cost)

    def test_a_job_previewed_with_costs_is_not_applied_with_them_by_somebody_without(self):
        Product.objects.filter(pk=self.case.pk).update(reference_cost=D('20.00'))
        job = self.preview([['FUN-001', 'Funda', '59.90', '35.00']])
        import_services.apply_products(job=job, actor=self.catalogue_user())
        self.case.refresh_from_db()
        self.assertEqual(self.case.reference_cost, D('20.00'))


class StockUnitCostTest(_Shop):
    HEADERS = ['ID', 'CODIGO', 'CODIGO EAN', 'NOMBRE', 'ALMACEN 1 - 11416', 'Costo unitario']

    def preview(self, rows, headers=None):
        return stock_import_services.preview_stock(
            company=self.company, actor=self.owner,
            upload=upload(headers or self.HEADERS, rows, title='Inventario'), filename='stock.xlsx',
            branch_map={4: self.branch.pk}, mode=BulkImportJob.MODE_RECONCILE,
        )

    def enter(self, quantity, cost):
        inventory.create_stock_movement(
            branch=self.branch, product_id=self.case.pk, movement_type=StockMovement.PURCHASE_ENTRY,
            quantity=quantity, reason='Compra', unit_cost=cost,
        )

    def average(self):
        return BranchStock.objects.get(branch=self.branch, product=self.case).average_cost

    def test_an_increase_with_cost_enters_at_that_cost_and_moves_the_average(self):
        self.enter(4, D('10'))
        job = self.preview([[1, 'FUN-001', '', 'Funda', 10, '13']])
        row = job.rows.get()
        self.assertEqual(row.errors, [])
        self.assertEqual(row.normalized_data['unit_cost'], '13.00')
        # (4 × 10 + 6 × 13) / 10 = 11.8
        self.assertEqual(row.normalized_data['cost_average_before'], '10.0000')
        self.assertEqual(row.normalized_data['cost_average_after'], '11.8000')

        stock_import_services.apply_stock(job=job, actor=self.owner)
        self.assertEqual(self.average(), D('11.8000'))
        line = StockMovement.objects.filter(product=self.case).order_by('-pk').first()
        self.assertEqual((line.quantity, line.unit_cost, line.cost_source), (6, D('13.0000'), 'entry'))
        # Applying again is not a second entry.
        self.assertFalse(stock_import_services.apply_stock(job=job, actor=self.owner)[1])

    def test_a_decrease_ignores_the_cost_and_says_so(self):
        self.enter(4, D('10'))
        job = self.preview([[1, 'FUN-001', '', 'Funda', 1, '99']])
        row = job.rows.get()
        self.assertNotIn('unit_cost', row.normalized_data)
        self.assertTrue(any('costo' in w.lower() for w in row.warnings), row.warnings)
        stock_import_services.apply_stock(job=job, actor=self.owner)
        self.assertEqual(self.average(), D('10.0000'))

    def test_a_bad_cost_is_a_row_error(self):
        row = self.preview([[1, 'FUN-001', '', 'Funda', 10, 'gratis']]).rows.get()
        self.assertTrue(any('Costo unitario' in e for e in row.errors), row.errors)

    def test_an_old_file_without_the_column_loads_as_before(self):
        self.enter(4, D('10'))
        job = self.preview([[1, 'FUN-001', '', 'Funda', 6]], headers=self.HEADERS[:5])
        stock_import_services.apply_stock(job=job, actor=self.owner)
        line = StockMovement.objects.filter(product=self.case).order_by('-pk').first()
        self.assertEqual((line.quantity, line.unit_cost, line.cost_source), (2, None, 'unknown'))
        self.assertEqual(self.average(), D('10.0000'))

    def test_the_stock_sheet_to_reload_does_not_restate_costs(self):
        # It answers as it always did; the cost column is one somebody ADDS.
        headers, _rows = import_exports.inventory_export_table(
            company=self.company, branches=[self.branch], include_quantities=True)
        self.assertNotIn('Costo unitario', headers)
        self.assertEqual(
            import_formats.find_column(['CODIGO', 'ALMACEN Centro', 'Costo unitario'],
                                       import_formats.UNIT_COST_HEADERS), 2)

    def test_without_costs_view_the_average_is_not_shown(self):
        self.enter(4, D('10'))
        stocker, _ = _p2d_member(self.company, 'dc1_excel_almacen',
                                 ['company.view', 'inventory.view', 'inventory.adjust'])
        res = _client(stocker).post('/api/admin/inventory/import/preview/', {
            'file': upload(self.HEADERS, [[1, 'FUN-001', '', 'Funda', 10, '13']], title='Inventario'),
            'branch_map': '{"4": %d}' % self.branch.pk,
        }, format='multipart')
        self.assertEqual(res.status_code, 201, res.content)
        text = res.content.decode()
        self.assertNotIn('cost_average_before', text)
        self.assertNotIn('10.0000', text)


class DeviceCostTest(_Shop):
    HEADERS = ['Código', 'Producto', 'Sucursal', 'Número de serie', 'IMEI', 'IMEI 2', 'Condición', 'Costo',
               'Motivo / referencia']

    def setUp(self):
        super().setUp()
        self.phone = _prod(self.company, 'iPhone 16', 'iphone-excel', price='4500.00', inventory=0)
        Product.objects.filter(pk=self.phone.pk).update(is_serialized=True, requires_imei=True)
        self.phone.refresh_from_db()
        ProductBarcode.objects.create(company=self.company, product=self.phone, code='IP16',
                                      symbology=ProductBarcode.INTERNAL)
        self.sold, = units.receive_units(branch=self.branch, product=self.phone, reason='Compra',
                                         units=[{'serial_number': 'SER-0001', 'imei': IMEI_A, 'cost': '3800'}])
        order = Order.objects.create(
            company=self.company, fulfillment_branch=self.branch, customer_email='x@example.com',
            total=self.phone.price, status=Order.Status.PAID, paid=True, paid_at=timezone.now())
        OrderItem.objects.create(order=order, product=self.phone, quantity=1, price=self.phone.price)
        inventory.record_sale_stock_movements(order)

    def preview(self, rows):
        from store import unit_import_services

        return unit_import_services.preview_units(
            company=self.company, actor=self.owner, upload=upload(self.HEADERS, rows, title='Equipos'),
            filename='equipos.xlsx', reachable_branch_ids=[self.branch.pk])

    def test_the_cost_of_a_new_device_is_its_specific_cost(self):
        from store import unit_import_services

        job = self.preview([['IP16', '', self.branch.name, 'SER-0002', IMEI_B, '', 'Nuevo', '3700', 'Compra']])
        unit_import_services.apply_units(job=job, actor=self.owner)
        line = StockMovement.objects.filter(product=self.phone, movement_type='purchase_entry').order_by('-pk')[0]
        self.assertEqual((line.unit_cost, line.cost_source), (D('3700.0000'), 'unit'))

    def test_a_sold_device_cannot_come_back_to_change_its_cost(self):
        row = self.preview([['IP16', '', self.branch.name, 'SER-0001', IMEI_A, '', 'Nuevo', '1', 'Compra']]).rows.get()
        self.assertTrue(any('vendido' in e and 'costo' in e for e in row.errors), row.errors)
        self.sold.refresh_from_db()
        self.assertEqual(self.sold.cost, D('3800.00'))
