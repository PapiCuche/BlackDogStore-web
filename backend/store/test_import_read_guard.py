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
    touches: 404 on its detail, its report and its product apply, absent from
    the history. A job whose branches cannot be read is the whole company's. A
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


# =============================================================================
# Review of PR #236
# =============================================================================

def _historical_job(case, headers_extra, cells, *, quantity_branches=None, actor=None):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from store.test_label_migration_import import HEADERS, HISTORY, row, workbook

    headers = [*HEADERS, *headers_extra]
    data = workbook([[*row(code='SECIMP-H2', name='Histórico de revisión'), *cells]], headers=headers)
    return import_services.preview_products(
        company=case.company, actor=actor or case.owner, upload=SimpleUploadedFile('h.xlsx', data),
        filename='h.xlsx', options=dict(HISTORY),
        quantity_branches=quantity_branches)


class ReachFollowUpTest(_Shop):
    """P2 / P3 — every door to a job asks the same question, and fails closed."""

    def setUp(self):
        super().setUp()
        self.limited, _ = _p2d_member(self.company, 'secimp_lim_rev', STOCKER, branches=[self.branch])
        self.limited_catalogue, _ = _p2d_member(self.company, 'secimp_catlim_rev', CATALOGUE,
                                                branches=[self.branch])
        self.here = self.stock_job([[1, 'FUN-001', '', 'Funda', 3]], headers=STOCK_HEADERS[:5])

    def listed(self, user):
        res = _client(user).get('/api/admin/imports/')
        self.assertEqual(res.status_code, 200, res.content)
        return [job['id'] for job in res.json()['results']]

    def north_history_job(self):
        from store.test_label_migration_import import HEADERS

        return _historical_job(self, ['Cantidad anterior - Norte'], ['4'],
                               quantity_branches={len(HEADERS): self.north.pk})

    def test_the_product_apply_of_a_historical_job_of_another_branch_answers_404(self):
        job = self.north_history_job()
        api = _client(self.limited_catalogue)
        res = api.post(f'/api/admin/products/import/{job.pk}/apply/', {}, format='json')
        self.assertEqual(res.status_code, 404, res.content)
        self.assertNotIn(self.north.name, res.content.decode())
        job.refresh_from_db()
        self.assertEqual(job.status, BulkImportJob.PREVIEWED)
        # Nor once applied by somebody who may: a repeated apply is no read oracle.
        self.assertEqual(_client(self.owner).post(f'/api/admin/products/import/{job.pk}/apply/', {},
                                                  format='json').status_code, 200)
        res = api.post(f'/api/admin/products/import/{job.pk}/apply/', {}, format='json')
        self.assertEqual(res.status_code, 404, res.content)
        self.assertNotIn(self.north.name, res.content.decode())

    def test_the_product_apply_of_a_job_naming_no_branch_still_works_for_a_limited_member(self):
        plain = self.product_job([['FUN-001', 'Funda', '61.00', '']])
        res = _client(self.limited_catalogue).post(f'/api/admin/products/import/{plain.pk}/apply/', {},
                                                   format='json')
        self.assertEqual(res.status_code, 200, res.content)

    def test_a_stock_job_whose_branch_map_is_empty_or_unreadable_is_company_wide_only(self):
        for broken in ({}, None, ['x'], 'junk', {'4': 'x'}):
            BulkImportJob.objects.filter(pk=self.here.pk).update(
                mapping_snapshot={**self.here.mapping_snapshot, 'branch_map': broken})
            self.assertEqual(_client(self.limited).get(f'/api/admin/imports/{self.here.pk}/').status_code, 404,
                             broken)
            self.assertEqual(_client(self.limited).get(
                f'/api/admin/imports/{self.here.pk}/errors.csv').status_code, 404, broken)
            self.assertNotIn(self.here.pk, self.listed(self.limited), broken)
            # The whole company still reads it, and nobody gets a 500.
            self.assertEqual(_client(self.stocker).get(f'/api/admin/imports/{self.here.pk}/').status_code, 200,
                             broken)
            self.assertIn(self.here.pk, self.listed(self.stocker), broken)
            res = _client(self.stocker).post(f'/api/admin/inventory/import/{self.here.pk}/apply/', {},
                                             format='json')
            self.assertLess(res.status_code, 500, (broken, res.content))
        BulkImportJob.objects.filter(pk=self.here.pk).update(mapping_snapshot='junk')
        self.assertEqual(_client(self.limited).get(f'/api/admin/imports/{self.here.pk}/').status_code, 404)
        self.assertEqual(_client(self.stocker).get(f'/api/admin/imports/{self.here.pk}/').status_code, 200)

    def _bulk_stock_jobs(self, branch, count):
        BulkImportJob.objects.bulk_create([
            BulkImportJob(company=self.company, import_type=BulkImportJob.STOCK, created_by=self.owner,
                          original_filename=f'otra-{i}.xlsx', mapping_snapshot={'branch_map': {'4': branch.pk}})
            for i in range(count)
        ])

    def test_the_history_fills_its_page_with_jobs_the_caller_may_read(self):
        # Fifty-five newer jobs of another branch do not push out an older one of theirs.
        self._bulk_stock_jobs(self.north, 55)
        ids = self.listed(self.limited)
        self.assertIn(self.here.pk, ids)
        self.assertEqual(len(self.listed(self.stocker)), 50)

    def test_the_history_page_is_capped_at_fifty_reachable_jobs(self):
        self._bulk_stock_jobs(self.branch, 60)
        self.assertEqual(len(self.listed(self.limited)), 50)

    def test_the_history_asks_the_callers_reach_once_not_once_per_job(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def queries():
            with CaptureQueriesContext(connection) as captured:
                self.listed(self.limited)
            return len(captured)

        self._bulk_stock_jobs(self.branch, 2)
        few = queries()
        self._bulk_stock_jobs(self.branch, 10)
        self.assertEqual(queries(), few)


class CostFollowUpTest(_Shop):
    """P3 — the reconciliation report, a limited member's own job, and a guard for every cost cell."""

    def test_the_reconciliation_report_repeats_no_ambiguous_historical_cost(self):
        job = _historical_job(self, ['Costo histórico'], [AMBIGUOUS_COST])
        [staged] = job.rows.all()
        self.assertTrue(any(w.startswith('Costo histórico (sólo informe)') and AMBIGUOUS_COST in w
                            for w in staged.warnings), staged.warnings)
        blind = self.errors_csv(self.catalogue, job, kind='history')
        for form in (AMBIGUOUS_COST, '7391', '7.391'):
            self.assertNotIn(form, blind)
        self.assertIn('Costo histórico (sólo informe)', blind)
        self.assertIn(AMBIGUOUS_COST, self.errors_csv(self.owner, job, kind='history'))

    def test_a_branch_limited_member_reads_their_own_device_job_as_before(self):
        limited, _ = _p2d_member(self.company, 'secimp_lim_units', STOCKER, branches=[self.branch])
        job = self.unit_job()
        res = self.detail(limited, job)
        [row] = res.json()['rows']
        staged = job.rows.get()
        expected = {k: v for k, v in staged.normalized_data.items() if k != 'cost'}
        self.assertEqual(row['data'], expected)
        self.assertEqual((row['errors'], row['warnings']), (staged.errors, staged.warnings))
        self.assertIn(job.pk, [j['id'] for j in _client(limited).get('/api/admin/imports/').json()['results']])

    def test_no_cost_cell_reaches_a_reader_without_costs_view_in_any_text(self):
        """
        A guard for messages yet to be written: every cost cell of every load,
        valid or not, staged by `costs.view` and read by somebody without it.
        `in_text` marks the cases whose stored text DOES name the amount, so the
        guard is known to be looking at something.
        """
        cases = [
            # (label, make job, reader, forms of the amount, stored text names it)
            ('stock valid', lambda: self.stock_job([[1, 'FUN-001', '', 'Funda', 10, '46.13']]),
             self.stocker, ('46.13', '4613'), True),
            ('stock ambiguous', lambda: self.stock_job([[1, 'FUN-001', '', 'Funda', 10, AMBIGUOUS_COST]]),
             self.stocker, (AMBIGUOUS_COST, '7391', '7.391'), True),
            ('stock thousands', lambda: self.stock_job([[1, 'FUN-001', '', 'Funda', 10, '5.613,29']]),
             self.stocker, ('5.613,29', '5613.29', '561329'), True),
            ('product valid', lambda: self.product_job([['FUN-001', 'Funda', '59.90', REFERENCE_COST]]),
             self.catalogue, (REFERENCE_COST, '2257'), True),
            ('product ambiguous', lambda: self.product_job([['FUN-001', 'Funda', '59.90', AMBIGUOUS_COST]]),
             self.catalogue, (AMBIGUOUS_COST, '7391', '7.391'), True),
            ('product failing row', lambda: self.product_job([['FUN-001', 'Funda', 'caro', REFERENCE_COST]]),
             self.catalogue, (REFERENCE_COST, '2257'), True),
            ('history ambiguous', lambda: _historical_job(self, ['Costo histórico'], [AMBIGUOUS_COST]),
             self.catalogue, (AMBIGUOUS_COST, '7391', '7.391'), True),
            ('history valid', lambda: _historical_job(self, ['Costo histórico'], ['31.19']),
             self.catalogue, ('31.19', '3119'), False),
            ('device valid', lambda: self.unit_job(), self.stocker, (DEVICE_COST, '377731'), False),
            ('device invalid', lambda: self.unit_job(cost=AMBIGUOUS_COST), self.stocker,
             (AMBIGUOUS_COST, '7391', '7.391'), False),
        ]
        for label, make, reader, forms, in_text in cases:
            with self.subTest(label):
                job = make()
                stored = ' '.join(t for r in job.rows.all() for t in [*r.errors, *r.warnings])
                if in_text:
                    self.assertTrue(any(form in stored for form in forms), (label, stored))
                payload = self.detail(reader, job).json()
                texts = [' '.join([*r['errors'], *r['warnings']]) for r in payload['rows']]
                texts.append(json.dumps([r['data'] for r in payload['rows']], ensure_ascii=False))
                texts.append(self.errors_csv(reader, job))
                if job.import_type == BulkImportJob.PRODUCTS and (job.options_snapshot or {}).get('history_mode'):
                    texts.append(self.errors_csv(reader, job, kind='history'))
                for text in texts:
                    for form in forms:
                        self.assertNotIn(form, text, (label, form))
