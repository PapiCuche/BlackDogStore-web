"""
LABEL-03 — «Inventario › Etiquetas y códigos de barras», the read-only surface.

    GET /api/admin/inventory/labels/preview/?branch=&products=<id>:<copies>,…&units=<id>,…
    GET /api/admin/inventory/labels/pdf/?…same…

What is pinned here:
  * company and branch come from the server; a product, a unit or a branch of
    another company — or of a branch the caller does not reach — is a 404;
  * `inventory.labels.print` opens it; `inventory.view` alone is a 403;
  * an IMEI or a serial reaches the PDF only with the company's `imei1`
    profile AND `inventory.labels.sensitive`, and then the download leaves
    `AdminAuditLog('label_sensitive_printed')` with unit ids only (D6). The
    preview never shows a whole IMEI or serial;
  * a device without its internal code goes to review, naming the command;
  * otherwise downloading the PDF writes NOTHING: no batch, no print job, no
    audit row, no stock movement — not a single INSERT, UPDATE or DELETE.
"""
import base64
import io
import re
import zlib
from unittest import mock

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from store import labels
from store import stock_unit_services as units
from store.models import (
    AdminAuditLog, BranchStock, CompanyLabelSettings, PrintJob, Product, ProductBarcode, StockMovement,
    StockUnit,
)
from store.models import MembershipRoleAssignment
from store.tests import M7InventoryBase, _assign, _m7_stock, _prod, _role

PREVIEW = '/api/admin/inventory/labels/preview/'
PDF = '/api/admin/inventory/labels/pdf/'
MM = 72 / 25.4


def imei(body14: str) -> str:
    from store.device_identity import luhn_check_digit
    return body14 + luhn_check_digit(body14)


IMEI_A, IMEI_B, IMEI_F = (imei(f'3569380356438{n}') for n in ('5', '6', '7'))


def pdf_text(content: bytes) -> bytes:
    """
    Every page stream of a PDF, decoded. reportlab writes them ASCII85 + Flate;
    a helper that only tried Flate would find nothing and prove nothing.
    """
    out = []
    for match in re.finditer(rb'/Filter \[([^\]]*)\][^>]*>>\s*stream\r?\n(.*?)endstream', content, re.S):
        filters, data = match.group(1), match.group(2).strip()
        if b'ASCII85Decode' in filters:
            data = base64.a85decode(data[:-2] if data.endswith(b'~>') else data)
        if b'FlateDecode' in filters:
            data = zlib.decompress(data)
        out.append(data)
    return b''.join(out)


def masked(value: str) -> str:
    return '•' * (len(value) - 4) + value[-4:]


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

    def only(self, *capabilities):
        """The member holds exactly these capabilities, replacing any role given before."""
        MembershipRoleAssignment.objects.filter(membership=self.membership).update(is_active=False)
        name = 'Rol ' + '+'.join(sorted(capabilities))
        _assign(self.membership, _role(self.company, name, capabilities=list(capabilities), slug=None))

    def without_label_code(self, unit):
        StockUnit.objects.filter(pk=unit.pk).update(label_code=None)

    def query(self, products='', units_='', branch=None):
        branch = self.branch_a.pk if branch is None else branch
        return f'?branch={branch}&products={products}&units={units_}'


class PreviewTest(LabelViewsBase):
    def test_accessories_are_copies_and_a_device_carries_its_internal_code(self):
        self.without_label_code(self.unit_b)
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
        self.unit_a.refresh_from_db()
        self.assertTrue(self.unit_a.label_code)
        device = by_ref[('device', self.unit_a.pk)]
        self.assertEqual((device['status'], device['pages']), ('ready', 1))
        self.assertEqual(device['code']['kind'], 'label_code')
        self.assertEqual(device['code']['value'], self.unit_a.label_code)
        self.assertEqual(device['layout']['width_mm'], 50)
        uncoded = by_ref[('device', self.unit_b.pk)]
        self.assertEqual(uncoded['status'], 'review')
        self.assertIsNone(uncoded['code'])
        self.assertTrue(any('assign_unit_codes --company m7-shop --apply' in r for r in uncoded['reasons']),
                        uncoded['reasons'])
        self.assertEqual(body['summary'], {'ready': 2, 'review': 2, 'pages': 11})
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
        self.assertEqual(res['X-Labels-Pages'], '11')
        self.assertEqual(res['X-Labels-Review'], '1')
        pdf = res.content
        self.assertEqual(len(re.findall(rb'/Type /Page(?![s\w])', pdf)), 11)
        self.unit_a.refresh_from_db()
        self.assertIn(f'({self.unit_a.label_code})'.encode(), pdf_text(pdf))
        box = re.search(rb'/MediaBox \[\s*0 0 ([\d.]+) ([\d.]+)', pdf)
        self.assertAlmostEqual(float(box.group(1)), 30 * MM, places=2)
        self.assertAlmostEqual(float(box.group(2)), 20 * MM, places=2)

    def test_nothing_ready_is_a_409_with_the_reasons_and_never_an_imei(self):
        self.without_label_code(self.unit_a)
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

    def test_without_the_print_capability_it_is_a_403(self):
        self.only('products.view')
        for url in (PREVIEW, PDF):
            res = self.api.get(url + self.query(f'{self.cable.pk}:1'))
            self.assertEqual(res.status_code, 403, url)

    def test_inventory_view_alone_is_a_403_and_the_print_capability_opens_it(self):
        self.only('inventory.view')
        for url in (PREVIEW, PDF):
            self.assertEqual(self.api.get(url + self.query(f'{self.cable.pk}:1')).status_code, 403, url)
        self.only('inventory.labels.print')
        for url in (PREVIEW, PDF):
            self.assertEqual(self.api.get(url + self.query(f'{self.cable.pk}:1')).status_code, 200, url)

    def test_anonymous_is_401(self):
        self.assertEqual(APIClient().get(PDF + self.query(f'{self.cable.pk}:1')).status_code, 401)


class SensitiveGateTest(LabelViewsBase):
    """
    D6 — an IMEI on paper needs BOTH keys: the company's `imei1` profile and
    `inventory.labels.sensitive`. With both, the PDF carries it and the download
    leaves ONE `AdminAuditLog('label_sensitive_printed')` naming unit ids only.
    The preview never shows it whole. With either key missing, no IMEI or serial
    anywhere and no audit line.
    """

    def imei1_profile(self):
        CompanyLabelSettings.objects.update_or_create(
            company=self.company, defaults={'device_symbol': CompanyLabelSettings.DEVICE_SYMBOL_IMEI1})

    def audits(self):
        return AdminAuditLog.objects.filter(company=self.company, action='label_sensitive_printed')

    def assert_no_identifier(self, *responses):
        for res in responses:
            text = res.content + (pdf_text(res.content) if res['Content-Type'] == 'application/pdf' else b'')
            for secret in (IMEI_A, 'FICTSERIE01'):
                self.assertNotIn(secret.encode(), text)

    def test_both_keys_put_the_imei_on_the_audited_pdf_and_only_masked_in_the_preview(self):
        self.imei1_profile()
        self.only('inventory.labels.print', 'inventory.labels.sensitive')

        preview = self.api.get(PREVIEW + self.query('', f'{self.unit_a.pk}'))
        self.assertEqual(preview.status_code, 200, preview.content)
        body = preview.json()
        self.assertEqual(body['permissions'], {'sensitive': True, 'device_profile': 'imei1'})
        row = body['labels'][0]
        self.assertEqual((row['status'], row['code']['kind'], row['sensitive']), ('ready', 'imei', True))
        self.assertEqual(row['code']['value'], masked(IMEI_A))
        self.assertIn(masked(IMEI_A), row['lines'])
        self.assertNotIn(IMEI_A, preview.content.decode())
        self.assertEqual(self.audits().count(), 0, 'la vista previa no se audita: no muestra el IMEI')

        pdf = self.api.get(PDF + self.query(f'{self.cable.pk}:1', f'{self.unit_a.pk}'))
        self.assertEqual(pdf.status_code, 200)
        self.assertIn('no-store', pdf['Cache-Control'])
        self.assertIn(f'({IMEI_A})'.encode(), pdf_text(pdf.content))
        audit = self.audits().get()
        self.assertEqual(audit.metadata, {
            'unit_ids': [self.unit_a.pk], 'count': 1, 'purpose': 'device_commercial', 'channel': 'pdf'})
        self.assertEqual(audit.actor, self.staff)
        recorded = str({f.name: getattr(audit, f.name) for f in audit._meta.fields})
        for secret in (IMEI_A, 'FICTSERIE01'):
            self.assertNotIn(secret, recorded)

    def test_the_profile_without_the_capability_prints_no_imei_and_audits_nothing(self):
        self.imei1_profile()
        self.only('inventory.labels.print')
        preview = self.api.get(PREVIEW + self.query('', f'{self.unit_a.pk}'))
        pdf = self.api.get(PDF + self.query('', f'{self.unit_a.pk}'))
        self.assertEqual(preview.json()['permissions'], {'sensitive': False, 'device_profile': 'imei1'})
        self.assertEqual(preview.json()['labels'][0]['code']['kind'], 'label_code')
        self.assertEqual(pdf.status_code, 200)
        self.assert_no_identifier(preview, pdf)
        self.assertEqual(self.audits().count(), 0)

    def test_the_capability_with_the_internal_profile_prints_no_imei_and_audits_nothing(self):
        self.only('inventory.labels.print', 'inventory.labels.sensitive')
        preview = self.api.get(PREVIEW + self.query('', f'{self.unit_a.pk}'))
        pdf = self.api.get(PDF + self.query('', f'{self.unit_a.pk}'))
        self.assertEqual(preview.json()['permissions'], {'sensitive': True, 'device_profile': 'internal'})
        self.assertEqual(pdf.status_code, 200)
        self.assert_no_identifier(preview, pdf)
        self.assertEqual(self.audits().count(), 0)

    def test_even_the_platform_master_never_gets_an_identifier_without_its_audit(self):
        """Behaviour, for the caller holding every capability, under both profiles."""
        from django.contrib.auth import get_user_model

        imei2 = imei('35693803564397')
        StockUnit.objects.filter(pk=self.unit_a.pk).update(imei2=imei2)
        master = get_user_model().objects.create_superuser(
            username='master-etiquetas', email='master-etiquetas@example.com', password='Pass123!')
        api = APIClient()
        api.force_authenticate(user=master)
        query = self.query(f'{self.cable.pk}:1', f'{self.unit_a.pk}') + f'&company={self.company.pk}'
        for profile in ('internal', 'imei1'):
            CompanyLabelSettings.objects.update_or_create(company=self.company, defaults={'device_symbol': profile})
            before = self.audits().count()
            preview = api.get(PREVIEW + query)
            self.assertEqual(preview.status_code, 200, preview.content)
            pdf = api.get(PDF + query)
            self.assertEqual(pdf.status_code, 200)
            text = pdf_text(pdf.content)
            self.assertIn(b'(Cable ficticio USB-C)', text, 'el texto del PDF no se descomprimió')
            audited = self.audits().count() > before
            for secret in (IMEI_A, imei2, 'FICTSERIE01'):
                self.assertNotIn(secret, preview.content.decode(), profile)
                if not audited:
                    self.assertNotIn(secret.encode(), text, f'{profile}: identificador sin auditoría (D6)')
            self.assertEqual(audited, profile == 'imei1')

    def test_the_technical_fallback_exists_only_with_the_capability_and_is_audited_as_technical(self):
        self.imei1_profile()
        narrow = labels.DEVICE_COMMERCIAL_50X25.with_overrides(width_mm=36)   # nothing fits in 32 mm
        self.only('inventory.labels.print')
        with mock.patch('store.label_views.DEVICE_LAYOUT', narrow):
            refused = self.api.get(PDF + self.query('', f'{self.unit_a.pk}'))
        self.assertEqual(refused.status_code, 409)
        self.assert_no_identifier(refused)
        self.only('inventory.labels.print', 'inventory.labels.sensitive')
        with mock.patch('store.label_views.DEVICE_LAYOUT', narrow):
            preview = self.api.get(PREVIEW + self.query('', f'{self.unit_a.pk}'))
            pdf = self.api.get(PDF + self.query('', f'{self.unit_a.pk}'))
        self.assertEqual(preview.json()['labels'][0]['layout']['width_mm'], 60)
        self.assertNotIn(IMEI_A, preview.content.decode())
        self.assertNotIn('FICTSERIE01', preview.content.decode())
        self.assertEqual(pdf.status_code, 200)
        self.assertIn(b'FICTSERIE01', pdf_text(pdf.content))
        self.assertEqual(self.audits().get().metadata['purpose'], 'device_technical')


class ProfileContractTest(LabelViewsBase):
    def test_the_package_profiles_are_the_company_setting_values(self):
        self.assertEqual(labels.data.PROFILE_INTERNAL, CompanyLabelSettings.DEVICE_SYMBOL_INTERNAL)
        self.assertEqual(labels.data.PROFILE_IMEI1, CompanyLabelSettings.DEVICE_SYMBOL_IMEI1)
        self.assertEqual(set(labels.data.PROFILES), {c for c, _ in CompanyLabelSettings.DEVICE_SYMBOL_CHOICES})


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
