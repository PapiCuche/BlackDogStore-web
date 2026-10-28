"""
INV-FAST-B2 — the pasted list registered through the OFFICIAL device importer, without an Excel in between.

    POST …/text-preview/units/preview/   the draft rows as JSON -> `unit_import_services.preview_units`
    POST …/units/import/<id>/apply/      the OFFICIAL apply, unchanged (labels, audit, idempotence)

Nothing is re-implemented: the rows become the official template in memory and
go through the same reader, validators, `BulkImportJob` and `receive_units`. A
preview stages a job and moves no stock, no movement and no label.
"""
from decimal import Decimal

from rest_framework.test import APIClient

from store.models import (
    AdminAuditLog, BulkImportJob, Product, ProductBarcode, StockMovement, StockUnit,
)
from store.test_catalog_proposal import APPLY, PROPOSE, _Base, row, twelve_phones
from store.test_device_commerce import IMEIS
from store.tests import _p2d_member, _p3_company
from django.test import TestCase

PREVIEW = '/api/admin/inventory/text-preview/units/preview/'


def official_apply(job_id):
    return f'/api/admin/inventory/units/import/{job_id}/apply/'


class _Receive(_Base):
    def start_shop(self, slug):
        self.start(slug)
        plan = self.propose(twelve_phones()).json()['plan']
        self.assertEqual(self.apply(twelve_phones(), self.everything(plan)).status_code, 200)

    def product_of(self, name):
        return Product.objects.get(company=self.company, name=name)

    def devices(self):
        """12 pasted devices -> the products the catalogue now has for them."""
        names = [
            'Teléfono X 256 GB Negro', 'Teléfono X 256 GB Azul Seminuevo', 'Teléfono Y 128 GB Negro Seminuevo',
            'Teléfono Y 128 GB Negro Seminuevo', 'Teléfono Y 256 GB Azul Seminuevo', 'Teléfono Z 128 GB Azul Seminuevo',
            'Teléfono W 256 GB Negro Seminuevo', 'Teléfono V 128 GB Negro Seminuevo', 'Tableta T 128 GB Negro Seminuevo',
            'Teléfono U 512 GB Rojo Seminuevo', 'Teléfono U 512 GB Rojo Seminuevo', 'Teléfono X 128 GB Azul usado',
        ]
        rows = []
        for n, name in enumerate(names):
            rows.append({'key': str(n + 1), 'product_id': self.product_of(name).pk, 'serial_number': f'SERIE{n:03d}',
                         'imei': IMEIS[n], 'condition': 'used' if 'Seminuevo' in name or 'usado' in name else 'new'})
        return rows

    def preview(self, rows, client=None, **extra):
        body = {'branch': self.branch.pk, 'reason': 'Compra WhatsApp 28/10', 'rows': rows, **extra}
        return (client or self.api).post(PREVIEW, body, format='json')

    def counts(self):
        return (StockUnit.objects.filter(company=self.company).count(),
                StockMovement.objects.filter(company=self.company).count(),
                ProductBarcode.objects.filter(company=self.company).count(),
                Product.objects.filter(company=self.company).count())


class PreviewTest(_Receive, TestCase):
    def setUp(self):
        self.start_shop('rc-prev')

    def test_twelve_rows_are_staged_by_the_official_importer_and_no_stock_moves(self):
        before = self.counts()
        response = self.preview(self.devices())
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body['import_type'], BulkImportJob.UNITS)
        self.assertEqual((body['counts']['create'], body['counts']['error']), (12, 0))
        self.assertEqual(sorted(r['key'] for r in body['rows']), sorted(str(n) for n in range(1, 13)))
        self.assertTrue(all(r['action'] == 'create' for r in body['rows']))
        self.assertEqual(self.counts(), before, 'a preview moves no unit, no movement, no code')
        job = BulkImportJob.objects.get(pk=body['id'])
        self.assertEqual((job.company_id, job.import_type), (self.company.pk, BulkImportJob.UNITS))

    def test_each_row_error_is_the_official_one_and_names_its_key(self):
        rows = self.devices()
        rows[2]['serial_number'] = ''
        rows[3]['imei'] = '123'
        body = self.preview(rows).json()
        errors = {r['key']: r['errors'] for r in body['rows'] if r['action'] == 'error'}
        self.assertEqual(sorted(errors), ['3', '4'])
        self.assertIn('número de serie', ' '.join(errors['3']))
        self.assertEqual(body['counts']['error'], 2)

    def test_the_same_serial_twice_is_a_row_error_not_a_second_unit(self):
        rows = self.devices()
        rows[3].update(product_id=rows[2]['product_id'], serial_number=rows[2]['serial_number'], imei=IMEIS[30])
        body = self.preview(rows).json()
        self.assertEqual([r['key'] for r in body['rows'] if r['action'] == 'error'], ['4'])

    def test_a_product_of_another_company_is_not_found(self):
        other = _p3_company('rc-other', 'Otra')
        theirs = Product.objects.create(company=other, name='Ajeno', slug='ajeno', price='10.00', is_serialized=True)
        rows = self.devices()
        rows[0]['product_id'] = theirs.pk
        before = BulkImportJob.objects.count()
        self.assertEqual(self.preview(rows).status_code, 404)
        self.assertEqual(BulkImportJob.objects.count(), before)

    def test_a_branch_out_of_reach_is_not_found(self):
        other = _p3_company('rc-branch', 'Otra')
        self.assertEqual(self.preview(self.devices(), branch=other.default_inventory_branch.pk).status_code, 404)

    def test_a_created_variant_requires_the_imei_in_the_official_preview(self):
        rows = self.devices()
        rows[2]['imei'] = ''
        body = self.preview(rows).json()
        errors = {r['key']: ' '.join(r['errors']) for r in body['rows'] if r['action'] == 'error'}
        self.assertIn('3', errors)
        self.assertIn('IMEI', errors['3'])

    def test_a_row_whose_variant_has_no_price_is_warned_not_blocked(self):
        body = self.preview(self.devices()).json()
        self.assertEqual(body['counts']['error'], 0)
        unpriced = [r for r in body['rows'] if any('sin precio' in w.lower() for w in r['warnings'])]
        # The variants this suite creates are at 0.00 (10 rows); the two that existed before are priced.
        self.assertEqual(len(unpriced), 10)
        priced = [r for r in body['rows'] if r['key'] in ('1', '12')]
        self.assertFalse(any('sin precio' in w.lower() for r in priced for w in r['warnings']))

    def test_the_request_shape_is_closed(self):
        for extra in ({'notes': 'x'},):
            rows = self.devices()
            rows[0].update(extra)
            self.assertEqual(self.preview(rows).status_code, 400)
        self.assertEqual(self.preview([]).status_code, 400)
        rows = self.devices()
        rows[1]['key'] = rows[0]['key']
        self.assertEqual(self.preview(rows).status_code, 400)
        self.assertEqual(self.preview(self.devices(), reason='').status_code, 400)

    def test_a_cost_only_travels_for_whoever_may_see_costs(self):
        rows = self.devices()
        rows[0]['cost'] = '1234.50'
        # staff holds no costs.view: the cost is dropped, and the answer says so.
        body = self.preview(rows).json()
        self.assertTrue(body['cost_column_ignored'])
        self.assertNotIn('1234', str(body))
        self.assertEqual(body['counts']['create'], 12)
        boss, _ = _p2d_member(self.company, 'rc-boss', [
            'company.view', 'products.view', 'inventory.view', 'inventory.adjust', 'costs.view'])
        client = APIClient()
        client.force_authenticate(boss)
        kept = self.preview(rows, client=client).json()
        self.assertFalse(kept['cost_column_ignored'])
        self.assertIn('1234.50', str(kept))

    def test_previews_are_capped_and_throttled_like_the_official_upload(self):
        from store import unit_import_services
        from store.text_preview_views import AdminUnitReceiveDraftView
        from store.throttles import AdminImportThrottle
        self.assertEqual(AdminUnitReceiveDraftView.throttle_classes, [AdminImportThrottle])
        rows = [dict(self.devices()[0], key=str(n)) for n in range(unit_import_services.MAX_UNITS + 1)]
        before = BulkImportJob.objects.count()
        self.assertEqual(self.preview(rows).status_code, 400)
        self.assertEqual(BulkImportJob.objects.count(), before)

    def test_it_needs_inventory_adjust(self):
        who, _ = _p2d_member(self.company, 'rc-viewer', ['company.view', 'products.view', 'inventory.view'])
        client = APIClient()
        client.force_authenticate(who)
        self.assertEqual(self.preview(self.devices(), client=client).status_code, 403)
        self.assertEqual(APIClient().post(PREVIEW, {}, format='json').status_code, 401)


class RegisterTest(_Receive, TestCase):
    def setUp(self):
        self.start_shop('rc-reg')

    def test_the_whole_way_from_the_paste_to_twelve_units(self):
        """Proposal -> approval (setUp) -> rows match -> official preview -> official apply."""
        before_units = StockUnit.objects.filter(company=self.company).count()
        job = self.preview(self.devices()).json()
        applied = self.api.post(official_apply(job['id']), {}, format='json')
        self.assertEqual(applied.status_code, 200, applied.content)
        body = applied.json()
        self.assertEqual(body['status'], 'applied')
        self.assertEqual(StockUnit.objects.filter(company=self.company).count(), before_units + 12)
        # 12 individual units under 10 variants: never one product per IMEI.
        made = StockUnit.objects.filter(company=self.company).order_by('-pk')[:12]
        self.assertEqual(len({u.product_id for u in made}), 10)
        self.assertEqual(len({u.imei for u in made}), 12)
        self.assertEqual(Product.objects.filter(company=self.company, product_model__isnull=False).count(), 11)
        # LABEL-A's keys ride along on the official answer.
        self.assertIn('label_batches', body)
        self.assertIn('label_error', body)
        self.assertFalse(body['label_error'])

    def test_the_same_request_again_adds_no_stock_no_codes_and_no_labels(self):
        job = self.preview(self.devices()).json()
        self.api.post(official_apply(job['id']), {}, format='json')
        settled = self.counts()
        batches = body_batches = self.api.post(official_apply(job['id']), {}, format='json').json()['label_batches']
        # The same job applied again answers what it did.
        self.assertEqual(self.counts(), settled)
        # The same paste once more: every row is now a row error, so nothing can be applied.
        again = self.preview(self.devices())
        self.assertEqual(again.status_code, 201)
        self.assertEqual((again.json()['counts']['create'], again.json()['counts']['error']), (0, 12))
        refused = self.api.post(official_apply(again.json()['id']), {}, format='json')
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(self.counts(), settled)
        self.assertEqual(body_batches, batches)

    def test_a_staged_job_applied_after_another_took_the_serials_registers_nothing_twice(self):
        first = self.preview(self.devices()).json()
        second = self.preview(self.devices()).json()
        self.assertEqual(self.api.post(official_apply(first['id']), {}, format='json').status_code, 200)
        before = self.counts()
        late = self.api.post(official_apply(second['id']), {}, format='json')
        self.assertEqual(late.status_code, 400, late.content)
        self.assertEqual(self.counts(), before)

    def test_the_audit_line_is_the_official_one_with_its_company(self):
        job = self.preview(self.devices()).json()
        self.api.post(official_apply(job['id']), {}, format='json')
        line = AdminAuditLog.objects.get(company=self.company, action='stock_units_import_applied')
        self.assertEqual((line.metadata['units'], line.metadata['job_id']), (12, job['id']))
        self.assertNotIn(IMEIS[0], str(line.metadata))

    def test_another_company_cannot_apply_my_job(self):
        job = self.preview(self.devices()).json()
        other = _p3_company('rc-thief', 'Otra')
        thief, _ = _p2d_member(other, 'rc-thief-user', ['company.view', 'inventory.adjust'])
        client = APIClient()
        client.force_authenticate(thief)
        self.assertEqual(client.post(official_apply(job['id']), {}, format='json').status_code, 404)
        self.assertEqual(StockUnit.objects.filter(company=self.company, serial_number='SERIE000').count(), 0)

    def test_unit_cost_lands_on_the_unit_for_whoever_may_set_it(self):
        boss, _ = _p2d_member(self.company, 'rc-boss2', [
            'company.view', 'products.view', 'inventory.view', 'inventory.adjust', 'costs.view'])
        client = APIClient()
        client.force_authenticate(boss)
        rows = self.devices()[:2]
        rows[0]['cost'] = '900.00'
        job = self.preview(rows, client=client).json()
        self.assertEqual(client.post(official_apply(job['id']), {}, format='json').status_code, 200)
        self.assertEqual(StockUnit.objects.get(company=self.company, serial_number='SERIE000').cost, Decimal('900.00'))
