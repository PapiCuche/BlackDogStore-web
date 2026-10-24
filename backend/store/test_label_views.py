"""
LABEL-03 — «Inventario › Etiquetas y códigos de barras», the read-only surface.

    GET /api/admin/inventory/labels/preview/?branch=&products=<id>:<copies>,…&units=<id>,…
    GET /api/admin/inventory/labels/pdf/?…same…

What is pinned here:
  * company and branch come from the server; a product, a unit or a branch of
    another company — or of a branch the caller does not reach — is a 404;
  * `inventory.view` opens it (until LABEL-02 adds `inventory.labels.print`);
    without it, 403;
  * no IMEI or serial leaves this surface: the `inventory.labels.sensitive`
    permission and the `imei1` profile do not exist yet, so a device label
    without its LABEL-02 internal code goes to review;
  * downloading the PDF writes NOTHING: no batch, no print job, no audit row,
    no stock movement — and not a single INSERT, UPDATE or DELETE.
"""
import io
import re
from unittest import mock

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from store import stock_unit_services as units
from store.models import (
    AdminAuditLog, BranchStock, PrintJob, Product, ProductBarcode, StockMovement, StockUnit,
)
from store.tests import M7InventoryBase, _m7_stock, _prod

PREVIEW = '/api/admin/inventory/labels/preview/'
PDF = '/api/admin/inventory/labels/pdf/'
MM = 72 / 25.4


def imei(body14: str) -> str:
    from store.device_identity import luhn_check_digit
    return body14 + luhn_check_digit(body14)


IMEI_A, IMEI_B, IMEI_F = (imei(f'3569380356438{n}') for n in ('5', '6', '7'))


def png():
    from PIL import Image
    out = io.BytesIO()
    Image.new('RGB', (80, 40), (0, 0, 0)).save(out, 'PNG')
    return out.getvalue()


class LabelViewsBase(M7InventoryBase):
    def setUp(self):
        super().setUp()
        self.cable = _prod(self.company, 'Cable ficticio USB-C', 'cable-ficticio', price='39.90', inventory=0)
        _m7_stock(self.branch_a, self.cable, 12)
        ProductBarcode.objects.create(
            company=self.company, product=self.cable, code='C000701', symbology='internal', is_primary=True)
        self.long_code = _prod(self.company, 'Funda ficticia', 'funda-ficticia', price='25.00', inventory=0)
        _m7_stock(self.branch_a, self.long_code, 3)
        ProductBarcode.objects.create(
            company=self.company, product=self.long_code, code='123456789012', symbology='internal',
            is_primary=True)
        self.phone = _prod(self.company, 'Teléfono ficticio', 'telefono-ficticio', price='2999.00', inventory=0)
        Product.objects.filter(pk=self.phone.pk).update(is_serialized=True, requires_imei=True)
        self.phone.refresh_from_db()
        self.unit_a, self.unit_b = units.receive_units(
            branch=self.branch_a, product=self.phone, actor=self.staff, reason='Compra',
            units=[{'serial_number': 'FICTSERIE01', 'imei': IMEI_A},
                   {'serial_number': 'FICTSERIE02', 'imei': IMEI_B}])
        self.unit_in_b, = units.receive_units(
            branch=self.branch_b, product=self.phone, actor=self.staff, reason='Compra',
            units=[{'serial_number': 'FICTSERIE03', 'imei': imei('35693803564388')}])
        foreign_phone = _prod(self.other, 'Ajeno', 'ajeno-tel', inventory=0)
        Product.objects.filter(pk=foreign_phone.pk).update(is_serialized=True)
        foreign_phone.refresh_from_db()
        self.foreign_unit, = units.receive_units(
            branch=self.foreign_branch, product=foreign_phone, actor=self.staff, reason='Compra',
            units=[{'serial_number': 'AJENA01', 'imei': IMEI_F}])
        self.api = APIClient()
        self.api.force_authenticate(user=self.staff)

    def query(self, products='', units_='', branch=None):
        branch = self.branch_a.pk if branch is None else branch
        return f'?branch={branch}&products={products}&units={units_}'


class PreviewTest(LabelViewsBase):
    def test_accessories_are_copies_and_devices_wait_for_their_internal_code(self):
        res = self.api.get(PREVIEW + self.query(
            f'{self.cable.pk}:10,{self.long_code.pk}:2', f'{self.unit_a.pk},{self.unit_b.pk}'))

        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        by_ref = {(row['kind'], row['ref']): row for row in body['labels']}
        cable = by_ref[('accessory', self.cable.pk)]
        self.assertEqual((cable['status'], cable['copies'], cable['pages']), ('ready', 10, 10))
        self.assertEqual(cable['code']['value'], 'C000701')
        self.assertEqual(cable['layout']['width_mm'], 30)
        self.assertIn('S/ 39.90', cable['lines'])
        too_long = by_ref[('accessory', self.long_code.pk)]
        self.assertEqual(too_long['status'], 'review')
        self.assertTrue(any('No cabe en 30×20' in r for r in too_long['reasons']))
        for unit in (self.unit_a, self.unit_b):
            row = by_ref[('device', unit.pk)]
            self.assertEqual(row['status'], 'review')
            self.assertIsNone(row['code'])
            self.assertTrue(any('LABEL-02' in r for r in row['reasons']))
        self.assertEqual(body['summary'], {'ready': 1, 'review': 3, 'pages': 10})
        self.assertEqual(body['permissions'], {'sensitive': False, 'device_profile': 'internal'})
        content = res.content.decode()
        for secret in (IMEI_A, IMEI_B, 'FICTSERIE01', 'FICTSERIE02'):
            self.assertNotIn(secret, content)
        self.assertNotIn('cost', content)

    def test_a_serialized_product_or_one_without_stock_here_goes_to_review(self):
        empty = _prod(self.company, 'Sin stock ficticio', 'sin-stock-ficticio', price='5.00', inventory=0)
        ProductBarcode.objects.create(company=self.company, product=empty, code='C000702', is_primary=True)
        res = self.api.get(PREVIEW + self.query(f'{self.phone.pk}:1,{empty.pk}:1'))
        self.assertEqual(res.status_code, 200, res.content)
        rows = {row['ref']: row for row in res.json()['labels']}
        self.assertEqual(rows[self.phone.pk]['status'], 'review')
        self.assertEqual(rows[empty.pk]['status'], 'review')
        self.assertTrue(any('existencias' in r for r in rows[empty.pk]['reasons']))

    def test_bad_requests(self):
        for products, units_ in (('', ''), ('x', ''), (f'{self.cable.pk}:0', ''), (f'{self.cable.pk}:501', ''),
                                 (f'{self.cable.pk}:1,{self.cable.pk}:2', ''), ('', 'abc'),
                                 ('', f'{self.unit_a.pk},{self.unit_a.pk}')):
            res = self.api.get(PREVIEW + self.query(products, units_))
            self.assertEqual(res.status_code, 400, (products, units_, res.content))

    def test_the_logo_is_the_company_one_and_a_missing_or_broken_one_never_fails(self):
        with mock.patch('store.label_views.fiscal_logo.current_png', return_value=png()) as current:
            res = self.api.get(PREVIEW + self.query(f'{self.cable.pk}:1'))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(current.call_args.args[0], self.company)
        self.assertTrue(res.json()['labels'][0]['has_logo'])
        for value in (None, b'broken'):
            with mock.patch('store.label_views.fiscal_logo.current_png', return_value=value):
                with self.assertLogs('store.labels.compose', 'WARNING') if value else _nothing():
                    res = self.api.get(PDF + self.query(f'{self.cable.pk}:1'))
            self.assertEqual(res.status_code, 200)


class _nothing:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class PdfTest(LabelViewsBase):
    def test_the_download_is_a_real_scale_pdf_of_the_ready_labels(self):
        res = self.api.get(PDF + self.query(f'{self.cable.pk}:10,{self.long_code.pk}:2', f'{self.unit_a.pk}'))

        self.assertEqual(res.status_code, 200, res.content[:200])
        self.assertEqual(res['Content-Type'], 'application/pdf')
        self.assertIn('no-store', res['Cache-Control'])
        self.assertIn('attachment', res['Content-Disposition'])
        self.assertEqual(res['X-Labels-Pages'], '10')
        self.assertEqual(res['X-Labels-Review'], '2')
        pdf = res.content
        self.assertEqual(len(re.findall(rb'/Type /Page(?![s\w])', pdf)), 10)
        box = re.search(rb'/MediaBox \[\s*0 0 ([\d.]+) ([\d.]+)', pdf)
        self.assertAlmostEqual(float(box.group(1)), 30 * MM, places=2)
        self.assertAlmostEqual(float(box.group(2)), 20 * MM, places=2)

    def test_nothing_ready_is_a_409_with_the_reasons_and_never_an_imei(self):
        res = self.api.get(PDF + self.query('', f'{self.unit_a.pk}'))
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['labels'][0]['status'], 'review')
        self.assertNotIn(IMEI_A, res.content.decode())

    def test_the_download_writes_nothing(self):
        def counts():
            return [model.objects.count() for model in (
                AdminAuditLog, PrintJob, StockMovement, StockUnit, ProductBarcode, BranchStock, Product)]
        before = counts()
        stamp = Product.objects.get(pk=self.cable.pk).updated_at
        with CaptureQueriesContext(connection) as captured:
            res = self.api.get(PDF + self.query(f'{self.cable.pk}:5', f'{self.unit_a.pk}'))
            self.api.get(PREVIEW + self.query(f'{self.cable.pk}:5', f'{self.unit_a.pk}'))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(counts(), before)
        self.assertEqual(Product.objects.get(pk=self.cable.pk).updated_at, stamp)
        writes = [q['sql'] for q in captured.captured_queries
                  if q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))]
        self.assertEqual(writes, [])


class ScopeTest(LabelViewsBase):
    def test_another_company_is_a_404(self):
        foreign_product = self.foreign_product
        for url in (
            PREVIEW + self.query(f'{foreign_product.pk}:1'),
            PDF + self.query(f'{foreign_product.pk}:1'),
            PREVIEW + self.query('', f'{self.foreign_unit.pk}'),
            PDF + self.query('', f'{self.foreign_unit.pk}'),
            PREVIEW + self.query(f'{self.cable.pk}:1', branch=self.foreign_branch.pk),
        ):
            res = self.api.get(url)
            self.assertEqual(res.status_code, 404, url)
            self.assertNotIn(IMEI_F, res.content.decode())

    def test_a_unit_of_another_branch_or_a_branch_out_of_reach_is_a_404(self):
        res = self.api.get(PREVIEW + self.query('', f'{self.unit_in_b.pk}'))
        self.assertEqual(res.status_code, 404)
        self.api.force_authenticate(user=None)
        self.restrict_to_branch_a()
        self.api.force_authenticate(user=self.staff)
        res = self.api.get(PREVIEW + self.query(f'{self.cable.pk}:1', branch=self.branch_b.pk))
        self.assertEqual(res.status_code, 404)

    def test_without_inventory_view_it_is_a_403(self):
        self.with_capabilities('products.view')
        for url in (PREVIEW, PDF):
            res = self.api.get(url + self.query(f'{self.cable.pk}:1'))
            self.assertEqual(res.status_code, 403, url)

    def test_anonymous_is_401(self):
        self.assertEqual(APIClient().get(PDF + self.query(f'{self.cable.pk}:1')).status_code, 401)


class SensitiveGateTest(LabelViewsBase):
    """
    D6 — an IMEI or a serial on paper needs its own permission AND leaves
    `AdminAuditLog('label_sensitive_printed')`. Until that audit ships, the
    sensitive path stays OFF, even if someone registers the capability.
    """

    def test_the_sensitive_path_stays_off_even_if_the_capability_is_registered(self):
        with mock.patch('store.label_views.ALL_CAPABILITY_CODES',
                        frozenset({'inventory.labels.sensitive', 'inventory.view'}), create=True), \
                mock.patch('store.label_views.has_capability', return_value=True, create=True):
            preview = self.api.get(PREVIEW + self.query('', f'{self.unit_a.pk}'))
            pdf = self.api.get(PDF + self.query('', f'{self.unit_a.pk}'))
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()['permissions'], {'sensitive': False, 'device_profile': 'internal'})
        for res in (preview, pdf):
            for secret in (IMEI_A, 'FICTSERIE01'):
                self.assertNotIn(secret, res.content.decode('latin-1'))
        self.assertEqual(AdminAuditLog.objects.filter(action='label_sensitive_printed').count(), 0)

    def test_the_capability_never_ships_without_its_audit(self):
        import inspect

        from store import label_views
        from store.capabilities import ALL_CAPABILITY_CODES

        if 'inventory.labels.sensitive' in ALL_CAPABILITY_CODES:
            self.assertIn(
                'label_sensitive_printed', inspect.getsource(label_views),
                'inventory.labels.sensitive existe pero las etiquetas sensibles no dejan AdminAuditLog (D6).')
        else:
            self.assertFalse(label_views._device_options(None, self.company).sensitive_allowed)


class NotLabellableTest(LabelViewsBase):
    def test_a_sold_or_written_off_unit_goes_to_review(self):
        StockUnit.objects.filter(pk=self.unit_a.pk).update(status=StockUnit.Status.SOLD)
        StockUnit.objects.filter(pk=self.unit_b.pk).update(status=StockUnit.Status.WRITTEN_OFF)
        res = self.api.get(PREVIEW + self.query('', f'{self.unit_a.pk},{self.unit_b.pk}'))
        self.assertEqual(res.status_code, 200)
        for row in res.json()['labels']:
            self.assertEqual(row['status'], 'review')
            self.assertIn('El equipo ya no está en el stock de esta sucursal.', row['reasons'])

    def test_an_inactive_product_goes_to_review(self):
        Product.objects.filter(pk=self.cable.pk).update(is_active=False)
        res = self.api.get(PREVIEW + self.query(f'{self.cable.pk}:2'))
        self.assertEqual(res.status_code, 200)
        row = res.json()['labels'][0]
        self.assertEqual((row['status'], row['pages']), ('review', 0))
        self.assertIn('El producto está desactivado.', row['reasons'])
        self.assertEqual(self.api.get(PDF + self.query(f'{self.cable.pk}:2')).status_code, 409)
