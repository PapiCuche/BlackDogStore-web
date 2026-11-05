"""
SEC-IMPORT-01 — what a staged import tells the people who READ it.

  * A1. A cost the file stated (`cost` of a device, `unit_cost` of a stock
    entry, `reference_cost` of a product) travels only to `costs.view`: in the
    job detail, the preview answer, the error report and the reconciliation
    report. Not as a key, and not inside a sentence: a warning or an error that
    names the amount reaches anybody else without it. Whoever HAS `costs.view`
    reads exactly what they read before.
  * I-M1. A stock job (and a historical product job that assigns old
    quantities to branches) exists only for whoever may operate EVERY branch it
    touches: 404 on its detail and its report, absent from the history. A
    member with access to the whole company sees every job, as before.

Amounts are distinctive decimals so a substring search cannot hit an id.
"""
import json

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from store import import_services, stock_import_services, unit_import_services
from store.models import BulkImportJob, Product, ProductBarcode
from store.test_cost_imports import upload
from store.test_stock_units import IMEI_A, IMEI_B
from store.tests import _p2d_branch, _p2d_member, _p3_company, _prod

UNIT_HEADERS = ['Código', 'Producto', 'Sucursal', 'Número de serie', 'IMEI', 'IMEI 2', 'Condición', 'Costo',
                'Motivo / referencia']
STOCK_HEADERS = ['ID', 'CODIGO', 'CODIGO EAN', 'NOMBRE', 'ALMACEN 1 - 11416', 'Costo unitario']
PRODUCT_HEADERS = ['Código', 'Nombre', 'Precio de venta', 'Costo de referencia']
STOCKER = ['company.view', 'inventory.view', 'inventory.adjust']
CATALOGUE = ['company.view', 'products.view', 'products.manage']

DEVICE_COST = '3777.31'
UNIT_COST = '13.37'
AMBIGUOUS_COST = '7,391'
REFERENCE_COST = '22.57'


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _csv(response):
    return response.content.decode('utf-8-sig')


class _Shop(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('sec-import', 'Tienda Lectura')
        self.branch = self.company.default_inventory_branch
        self.north = _p2d_branch(self.company, 'Norte lectura')
        self.phone = _prod(self.company, 'iPhone 16', 'sec-imp-ip16', price='4500.00', inventory=0)
        Product.objects.filter(pk=self.phone.pk).update(is_serialized=True, requires_imei=True)
        ProductBarcode.objects.create(company=self.company, product=self.phone, code='IP16',
                                      symbology=ProductBarcode.INTERNAL)
        self.case = _prod(self.company, 'Funda', 'sec-imp-funda', price='59.90', inventory=0)
        ProductBarcode.objects.create(company=self.company, product=self.case, code='FUN-001',
                                      symbology=ProductBarcode.INTERNAL)
        self.owner, _ = _p2d_member(self.company, 'secimp_duena', [
            *CATALOGUE, 'inventory.view', 'inventory.adjust', 'costs.view'])
        self.stocker, _ = _p2d_member(self.company, 'secimp_almacen', STOCKER)
        self.catalogue, _ = _p2d_member(self.company, 'secimp_catalogo', CATALOGUE)

    def stock_job(self, rows, branch=None, headers=STOCK_HEADERS, actor=None):
        return stock_import_services.preview_stock(
            company=self.company, actor=actor or self.owner,
            upload=upload(headers, rows, title='Inventario'), filename='stock.xlsx',
            branch_map={4: (branch or self.branch).pk}, mode=BulkImportJob.MODE_RECONCILE)

    def unit_job(self, cost=DEVICE_COST):
        return unit_import_services.preview_units(
            company=self.company, actor=self.owner,
            upload=upload(UNIT_HEADERS, [['IP16', '', self.branch.name, 'SECIMP0001', IMEI_A, '', 'Nuevo', cost,
                                          'Compra']], title='Equipos'),
            filename='equipos.xlsx', reachable_branch_ids=[self.branch.pk], default_branch=self.branch.pk)

    def product_job(self, rows):
        Product.objects.filter(pk=self.case.pk).update(reference_cost='20.00')
        return import_services.preview_products(
            company=self.company, actor=self.owner, upload=upload(PRODUCT_HEADERS, rows), filename='p.xlsx')

    def detail(self, user, job):
        res = _client(user).get(f'/api/admin/imports/{job.pk}/')
        self.assertEqual(res.status_code, 200, res.content)
        return res

    def errors_csv(self, user, job, **query):
        res = _client(user).get(f'/api/admin/imports/{job.pk}/errors.csv', query)
        self.assertEqual(res.status_code, 200, res.content)
        return _csv(res)


class StagedCostsTest(_Shop):
    """A1 — no cost of a staged row reaches a reader without `costs.view`."""

    # --- devices ----------------------------------------------------------
    def test_a_device_row_read_without_costs_view_carries_no_cost(self):
        job = self.unit_job()
        self.assertEqual(job.rows_error, 0, list(job.rows.values_list('errors', flat=True)))
        res = self.detail(self.stocker, job)
        [row] = res.json()['rows']
        self.assertNotIn('cost', row['data'])
        self.assertNotIn(DEVICE_COST, res.content.decode())
        # What is NOT a cost still travels: the serial is the row.
        self.assertEqual(row['data']['serial_number'], 'SECIMP0001')

    def test_a_device_preview_answers_its_uploader_without_costs_view_with_no_cost(self):
        res = _client(self.stocker).post('/api/admin/inventory/units/import/preview/', {
            'file': upload(UNIT_HEADERS, [['IP16', '', self.branch.name, 'SECIMP0002', IMEI_B, '', 'Nuevo',
                                           DEVICE_COST, 'Compra']], title='Equipos'),
            'branch': self.branch.pk,
        }, format='multipart')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertNotIn('cost', res.json()['rows'][0]['data'])
        self.assertNotIn(DEVICE_COST, res.content.decode())

    def test_a_device_row_read_with_costs_view_keeps_its_cost(self):
        [row] = self.detail(self.owner, self.unit_job()).json()['rows']
        self.assertEqual(row['data']['cost'], DEVICE_COST)

    # --- stock -------------------------------------------------------------
    def test_a_stock_row_read_without_costs_view_carries_no_unit_cost_in_data_or_text(self):
        job = self.stock_job([[1, 'FUN-001', '', 'Funda', 10, UNIT_COST]])
        res = self.detail(self.stocker, job)
        [row] = res.json()['rows']
        self.assertNotIn('unit_cost', row['data'])
        self.assertNotIn(UNIT_COST, res.content.decode())
        # The warning stays — ten units come in at a cost — without the amount.
        self.assertTrue(any(w.startswith('Entran 10 unidad(es) con costo unitario') for w in row['warnings']),
                        row['warnings'])

    def test_a_stock_preview_answers_its_uploader_without_costs_view_with_no_unit_cost(self):
        res = _client(self.stocker).post('/api/admin/inventory/import/preview/', {
            'file': upload(STOCK_HEADERS, [[1, 'FUN-001', '', 'Funda', 10, UNIT_COST]], title='Inventario'),
            'branch_map': json.dumps({'4': self.branch.pk}),
        }, format='multipart')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertNotIn('unit_cost', res.json()['rows'][0]['data'])
        self.assertNotIn(UNIT_COST, res.content.decode())

    def test_a_stock_row_read_with_costs_view_is_unchanged(self):
        job = self.stock_job([[1, 'FUN-001', '', 'Funda', 10, UNIT_COST]])
        [row] = self.detail(self.owner, job).json()['rows']
        self.assertEqual(row['data']['unit_cost'], UNIT_COST)
        self.assertEqual(row['warnings'], list(job.rows.get().warnings))
        self.assertIn(f'Entran 10 unidad(es) con costo unitario {UNIT_COST}.', row['warnings'])

    def test_an_ambiguous_unit_cost_is_not_repeated_to_a_reader_without_costs_view(self):
        job = self.stock_job([[1, 'FUN-001', '', 'Funda', 10, AMBIGUOUS_COST]])
        self.assertEqual(job.rows_error, 1)
        digits = AMBIGUOUS_COST.replace(',', '')
        res = self.detail(self.stocker, job)
        [row] = res.json()['rows']
        self.assertTrue(any(e.startswith('Costo unitario') for e in row['errors']), row['errors'])
        for text in (res.content.decode(), self.errors_csv(self.stocker, job)):
            self.assertNotIn(AMBIGUOUS_COST, text)
            self.assertNotIn(digits, text)
        # Whoever may see costs still reads which number was ambiguous.
        self.assertIn(AMBIGUOUS_COST, self.errors_csv(self.owner, job))

    # --- products ------------------------------------------------------------
    def test_a_product_row_read_without_costs_view_carries_no_reference_cost(self):
        job = self.product_job([['FUN-001', 'Funda', '59.90', REFERENCE_COST]])
        res = self.detail(self.catalogue, job)
        [row] = res.json()['rows']
        self.assertNotIn('reference_cost', row['data'])
        self.assertNotIn(REFERENCE_COST, res.content.decode())
        self.assertTrue(any(w.startswith('Costo de referencia') for w in row['warnings']), row['warnings'])

    def test_a_product_row_read_with_costs_view_is_unchanged(self):
        job = self.product_job([['FUN-001', 'Funda', '59.90', REFERENCE_COST]])
        [row] = self.detail(self.owner, job).json()['rows']
        self.assertEqual(row['data']['reference_cost'], REFERENCE_COST)
        self.assertIn(f'Costo de referencia: pasa a {REFERENCE_COST}.', row['warnings'])

    def test_the_error_report_repeats_no_reference_cost_to_a_reader_without_costs_view(self):
        # A row that FAILS on its price still says what its cost would become.
        job = self.product_job([['FUN-001', 'Funda', 'caro', REFERENCE_COST]])
        self.assertEqual(job.rows_error, 1)
        self.assertIn(REFERENCE_COST, self.errors_csv(self.owner, job))
        self.assertNotIn(REFERENCE_COST, self.errors_csv(self.catalogue, job))


class StockJobBranchReachTest(_Shop):
    """I-M1 — a stock job of a branch out of the caller's reach is not there for them."""

    def setUp(self):
        super().setUp()
        self.limited, _ = _p2d_member(self.company, 'secimp_limitado', STOCKER, branches=[self.branch])
        self.elsewhere = self.stock_job([[1, 'FUN-001', '', 'Funda', 7]], branch=self.north,
                                        headers=STOCK_HEADERS[:5])
        self.here = self.stock_job([[1, 'FUN-001', '', 'Funda', 3]], headers=STOCK_HEADERS[:5])

    def listed(self, user):
        res = _client(user).get('/api/admin/imports/')
        self.assertEqual(res.status_code, 200, res.content)
        return [job['id'] for job in res.json()['results']]

    def test_its_detail_and_its_report_answer_404(self):
        api = _client(self.limited)
        for url in (f'/api/admin/imports/{self.elsewhere.pk}/', f'/api/admin/imports/{self.elsewhere.pk}/errors.csv'):
            res = api.get(url)
            self.assertEqual(res.status_code, 404, (url, res.status_code))
            self.assertNotIn(self.north.name, res.content.decode('utf-8-sig'))

    def test_the_history_leaves_it_out_and_keeps_the_callers_own_branch(self):
        ids = self.listed(self.limited)
        self.assertNotIn(self.elsewhere.pk, ids)
        self.assertIn(self.here.pk, ids)
        self.assertEqual(self.detail(self.limited, self.here).json()['id'], self.here.pk)

    def test_a_job_spanning_a_reachable_and_an_unreachable_branch_is_out_of_reach(self):
        both = stock_import_services.preview_stock(
            company=self.company, actor=self.owner,
            upload=upload(['CODIGO', 'NOMBRE', 'ALMACEN A', 'ALMACEN B'], [['FUN-001', 'Funda', 1, 2]],
                          title='Inventario'),
            filename='dos.xlsx', branch_map={2: self.branch.pk, 3: self.north.pk},
            mode=BulkImportJob.MODE_RECONCILE, mapping={'code': 0, 'name': 1})
        self.assertNotIn(both.pk, self.listed(self.limited))
        self.assertEqual(_client(self.limited).get(f'/api/admin/imports/{both.pk}/').status_code, 404)

    def test_a_member_with_the_whole_company_sees_every_job(self):
        ids = self.listed(self.stocker)
        self.assertIn(self.elsewhere.pk, ids)
        self.assertIn(self.here.pk, ids)
        self.assertEqual(self.detail(self.stocker, self.elsewhere).json()['id'], self.elsewhere.pk)

    def test_a_historical_product_job_with_quantities_of_another_branch_is_out_of_reach(self):
        from store.test_label_migration_import import HEADERS, HISTORY, row, workbook
        from django.core.files.uploadedfile import SimpleUploadedFile

        headers = [*HEADERS, 'Cantidad anterior - Norte']
        data = workbook([[*row(code='SECIMP-H1', name='Histórico ficticio'), '4']], headers=headers)
        job = import_services.preview_products(
            company=self.company, actor=self.owner,
            upload=SimpleUploadedFile('h.xlsx', data), filename='h.xlsx',
            options=dict(HISTORY), quantity_branches={len(headers) - 1: self.north.pk})
        self.assertEqual(job.mapping_snapshot['quantity_branches'], {str(len(headers) - 1): self.north.pk})
        limited_catalogue, _ = _p2d_member(self.company, 'secimp_cat_lim', CATALOGUE, branches=[self.branch])
        api = _client(limited_catalogue)
        self.assertEqual(api.get(f'/api/admin/imports/{job.pk}/').status_code, 404)
        self.assertNotIn(job.pk, [j['id'] for j in api.get('/api/admin/imports/').json()['results']])
        # A product job that names no branch stays where it always was.
        plain = self.product_job([['FUN-001', 'Funda', '59.90', '']])
        self.assertEqual(api.get(f'/api/admin/imports/{plain.pk}/').status_code, 200)
        # And the whole company still reads the historical one.
        self.assertEqual(self.detail(self.catalogue, job).json()['id'], job.pk)
