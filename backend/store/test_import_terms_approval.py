"""
IMPORT-TERMS-APPROVAL — the owner's decision (2026-11-25) on a device load whose file brings a used device's own sale
price or warranty (the catalogue's authority, `products.manage`):

* the devices may always come in OPERATIONALLY without that authority — registered without the file's prices and
  warranties (`without_commercial_terms`), never with them by implication;
* with them, only after the EXPLICIT approval of somebody who holds `products.manage` in the job's company, recorded
  with who prepared, who approved and who registered the load;
* the approval is bound to what it approved (a digest of the terms) and to its author still holding the authority:
  reusing a preview is never a way to escalate.
"""
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from store.models import AdminAuditLog, BulkImportJob, CompanyRole, StockUnit
from store.test_device_commerce import DeviceShop
from store.test_stock_units import imei
from store.tests import _p2d_member, _p3_company

IMEIS = [imei(f'35693803599{n:03d}') for n in range(10)]
H = ['Código', 'Producto', 'Sucursal', 'Número de serie', 'IMEI', 'IMEI 2', 'Condición', 'Costo',
     'Motivo / referencia', 'Modelo', 'Capacidad', 'Color', 'Línea', 'Estado estético', 'Batería %',
     'Garantía (días)', 'Precio de venta']
MANAGER_CAPS = ['company.view', 'inventory.view', 'inventory.adjust', 'products.view', 'products.manage']
STOCKER_CAPS = ['company.view', 'inventory.view', 'inventory.adjust']


def approve_url(job_id):
    return f'/api/admin/inventory/units/import/{job_id}/approve-terms/'


def apply_url(job_id):
    return f'/api/admin/inventory/units/import/{job_id}/apply/'


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class TermsApprovalBase(DeviceShop):
    def setUp(self):
        self.build('aprobacion-terminos')
        self.stocker, _ = _p2d_member(self.company, 'terms_stocker', STOCKER_CAPS)
        self.manager, _ = _p2d_member(self.company, 'terms_manager', MANAGER_CAPS)
        self.second_manager, _ = _p2d_member(self.company, 'terms_manager_two', MANAGER_CAPS)

    def row(self, n, warranty='', price=''):
        return ['', '', '', f'SERAPR{n:02d}', IMEIS[n], '', 'Usado', '', 'Compra', 'Teléfono X', '128 GB', 'Azul',
                'Seminuevo', '', '', warranty, price]

    def preview(self, actor, rows):
        from store import unit_import_services
        from store.test_cost_imports import upload
        return unit_import_services.preview_units(
            company=self.company, actor=actor, upload=upload(H, rows, title='Equipos'), filename='equipos.xlsx',
            reachable_branch_ids=[self.branch.pk], default_branch=self.branch.pk, default_reason='Compra')

    def staged_with_terms(self, actor=None):
        """Two devices: one with a warranty and a price, one with nothing of its own."""
        return self.preview(actor or self.manager, [self.row(0, '90', '1500.00'), self.row(1)])

    def approve(self, job, user):
        return client_for(user).post(approve_url(job.pk), {}, format='json')

    def apply(self, job, user, **body):
        return client_for(user).post(apply_url(job.pk), body, format='json')

    def revoke_products_manage(self, username):
        role = CompanyRole.objects.get(company=self.company, slug=f'rol-{username}')
        role.capabilities = [cap for cap in role.capabilities if cap != 'products.manage']
        role.save(update_fields=['capabilities'])
        cache.clear()

    def assert_nothing_registered(self, job):
        self.assertFalse(StockUnit.objects.filter(company=self.company).exists(), 'nothing is written')
        job.refresh_from_db()
        self.assertEqual(job.status, BulkImportJob.PREVIEWED)


class ApproveTermsEndpointTest(TermsApprovalBase, TestCase):

    def test_a_stocker_cannot_approve(self):
        job = self.staged_with_terms()
        res = self.approve(job, self.stocker)
        self.assertEqual(res.status_code, 403, res.content)
        job.refresh_from_db()
        self.assertIsNone(job.terms_approved_by_id)
        self.assertFalse(AdminAuditLog.objects.filter(action='unit_import_terms_approved').exists())

    def test_a_manager_approves_and_it_is_recorded(self):
        job = self.staged_with_terms(self.manager)
        res = self.approve(job, self.second_manager)
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertTrue(body['carries_commercial_terms'])
        self.assertEqual(body['terms_approval']['by_name'], 'terms_manager_two')
        self.assertTrue(body['terms_approval']['valid'])
        job.refresh_from_db()
        self.assertEqual(job.terms_approved_by, self.second_manager)
        self.assertIsNotNone(job.terms_approved_at)
        self.assertEqual(len(job.terms_approved_digest), 64)
        log = AdminAuditLog.objects.get(action='unit_import_terms_approved')
        self.assertEqual((log.actor, log.company, log.target_id), (self.second_manager, self.company, str(job.pk)))
        self.assertEqual(log.metadata['job_id'], job.pk)
        self.assertEqual(log.metadata['rows'], 1)
        self.assertEqual(log.metadata['digest'], job.terms_approved_digest[:12])
        self.assertEqual(log.metadata['prepared_by_id'], self.manager.pk)
        self.assertFalse(log.metadata['self_approval'])

    def test_the_preparer_may_approve_and_it_is_said(self):
        job = self.staged_with_terms(self.manager)
        self.assertEqual(self.approve(job, self.manager).status_code, 200)
        log = AdminAuditLog.objects.get(action='unit_import_terms_approved')
        self.assertTrue(log.metadata['self_approval'])

    def test_approving_twice_is_one_approval(self):
        job = self.staged_with_terms()
        self.assertEqual(self.approve(job, self.manager).status_code, 200)
        self.assertEqual(self.approve(job, self.manager).status_code, 200)
        self.assertEqual(AdminAuditLog.objects.filter(action='unit_import_terms_approved').count(), 1)

    def test_a_manager_of_another_company_does_not_find_the_job(self):
        job = self.staged_with_terms()
        other = _p3_company('aprobacion-otra', 'Otra tienda')
        outsider, _ = _p2d_member(other, 'terms_outsider', MANAGER_CAPS)
        res = self.approve(job, outsider)
        self.assertEqual(res.status_code, 404, res.content)
        job.refresh_from_db()
        self.assertIsNone(job.terms_approved_by_id)

    def test_a_manager_who_does_not_reach_the_branch_does_not_find_the_job(self):
        from store.models import Branch
        elsewhere = Branch.objects.create(company=self.company, name='Otra sucursal', is_active=True)
        job = self.staged_with_terms()
        narrow, _ = _p2d_member(self.company, 'terms_narrow', MANAGER_CAPS, branches=[elsewhere])
        self.assertEqual(self.approve(job, narrow).status_code, 404)

    def test_a_job_without_commercial_terms_has_nothing_to_approve(self):
        job = self.preview(self.manager, [self.row(0)])
        res = self.approve(job, self.manager)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'no_commercial_terms')

    def test_an_applied_job_is_not_approved(self):
        job = self.staged_with_terms()
        self.assertEqual(self.apply(job, self.manager).status_code, 200)
        res = self.approve(job, self.manager)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertFalse(AdminAuditLog.objects.filter(action='unit_import_terms_approved').exists())

    def test_the_job_detail_tells_a_stocker_what_is_pending(self):
        job = self.staged_with_terms()
        body = client_for(self.stocker).get(f'/api/admin/imports/{job.pk}/').json()
        self.assertTrue(body['carries_commercial_terms'])
        self.assertIsNone(body['terms_approval'])
        self.assertFalse(body['can_update_prices'])
        self.approve(job, self.manager)
        body = client_for(self.stocker).get(f'/api/admin/imports/{job.pk}/').json()
        self.assertEqual(body['terms_approval']['by_name'], 'terms_manager')
        self.assertTrue(body['terms_approval']['valid'])


class ApplyWithApprovalTest(TermsApprovalBase, TestCase):

    def test_without_an_approval_a_stocker_is_still_refused(self):
        job = self.staged_with_terms()
        res = self.apply(job, self.stocker)
        self.assertEqual(res.status_code, 403, res.content)
        self.assertEqual(res.json()['code'], 'prices_forbidden')
        self.assert_nothing_registered(job)

    def test_with_a_valid_approval_a_stocker_registers_the_terms(self):
        job = self.staged_with_terms(self.manager)
        self.assertEqual(self.approve(job, self.second_manager).status_code, 200)
        res = self.apply(job, self.stocker)
        self.assertEqual(res.status_code, 200, res.content)
        unit = StockUnit.objects.get(serial_number='SERAPR00')
        self.assertEqual((unit.warranty_days, str(unit.sale_price)), (90, '1500.00'))
        self.assertIsNone(StockUnit.objects.get(serial_number='SERAPR01').warranty_days)
        log = AdminAuditLog.objects.get(action='stock_units_import_applied')
        self.assertEqual(log.actor, self.stocker)
        self.assertEqual(log.metadata['prepared_by_id'], self.manager.pk)
        self.assertEqual(log.metadata['terms_approved_by_id'], self.second_manager.pk)
        self.assertEqual(log.metadata['executed_by_id'], self.stocker.pk)
        self.assertEqual(log.metadata['commercial_terms'], 'applied')
        self.assertEqual(log.metadata['terms_authority'], 'approval')

    def test_terms_changed_after_the_approval_make_it_stale(self):
        job = self.staged_with_terms()
        self.approve(job, self.manager)
        row = job.rows.get(normalized_data__serial_number='SERAPR00')
        row.normalized_data = {**row.normalized_data, 'sale_price': '1.00'}
        row.save(update_fields=['normalized_data'])
        res = self.apply(job, self.stocker)
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'terms_approval_stale')
        self.assert_nothing_registered(job)

    def test_a_warranty_added_after_the_approval_makes_it_stale(self):
        job = self.staged_with_terms()
        self.approve(job, self.manager)
        row = job.rows.get(normalized_data__serial_number='SERAPR01')
        row.normalized_data = {**row.normalized_data, 'warranty_days': 365}
        row.save(update_fields=['normalized_data'])
        res = self.apply(job, self.stocker)
        self.assertEqual(res.status_code, 409, res.content)
        self.assert_nothing_registered(job)

    def test_an_approver_who_lost_products_manage_no_longer_covers_the_load(self):
        job = self.staged_with_terms(self.manager)
        self.approve(job, self.second_manager)
        self.revoke_products_manage('terms_manager_two')
        res = self.apply(job, self.stocker)
        self.assertEqual(res.status_code, 403, res.content)
        self.assertEqual(res.json()['code'], 'prices_forbidden')
        self.assert_nothing_registered(job)
        body = client_for(self.stocker).get(f'/api/admin/imports/{job.pk}/').json()
        self.assertFalse(body['terms_approval']['valid'])

    def test_the_approval_does_not_cover_a_variant_price_change(self):
        job = self.staged_with_terms()
        self.approve(job, self.manager)
        res = self.apply(job, self.stocker,
                         price_updates=[{'variant_id': self.used128.pk, 'price': '1500.00', 'confirm': True}])
        self.assertEqual(res.status_code, 403, res.content)
        self.assert_nothing_registered(job)

    def test_the_service_refuses_too(self):
        from store import unit_import_services
        job = self.staged_with_terms()
        self.approve(job, self.manager)
        self.revoke_products_manage('terms_manager')
        with self.assertRaises(unit_import_services.UnitImportError) as caught:
            unit_import_services.apply_units(job=job, actor=self.stocker)
        self.assertEqual(caught.exception.code, unit_import_services.PRICES_FORBIDDEN)


class OperationalIngressTest(TermsApprovalBase, TestCase):

    def test_a_stocker_registers_the_devices_without_the_terms(self):
        job = self.staged_with_terms()
        res = self.apply(job, self.stocker, without_commercial_terms=True)
        self.assertEqual(res.status_code, 200, res.content)
        unit = StockUnit.objects.get(serial_number='SERAPR00')
        self.assertEqual((unit.warranty_days, unit.sale_price), (None, None))
        self.assertEqual(StockUnit.objects.filter(company=self.company).count(), 2)
        self.assertEqual(res.json()['summary']['applied']['commercial_terms'], 'dropped')
        log = AdminAuditLog.objects.get(action='stock_units_import_applied')
        self.assertEqual(log.metadata['commercial_terms'], 'dropped')
        self.assertEqual(log.metadata['terms_rows_dropped'], 1)
        self.assertIsNone(log.metadata['terms_approved_by_id'])
        self.assertEqual(log.metadata['prepared_by_id'], self.manager.pk)
        self.assertEqual(log.metadata['executed_by_id'], self.stocker.pk)

    def test_dropping_is_an_explicit_choice_even_with_an_approval(self):
        job = self.staged_with_terms()
        self.approve(job, self.manager)
        res = self.apply(job, self.stocker, without_commercial_terms=True)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIsNone(StockUnit.objects.get(serial_number='SERAPR00').warranty_days)

    def test_only_a_true_flag_drops(self):
        job = self.staged_with_terms()
        res = self.apply(job, self.stocker, without_commercial_terms='true')
        self.assertEqual(res.status_code, 403, res.content)
        self.assert_nothing_registered(job)


class ManagerApplyUnchangedTest(TermsApprovalBase, TestCase):

    def test_a_manager_applies_their_terms_without_an_approval(self):
        job = self.staged_with_terms()
        res = self.apply(job, self.manager)
        self.assertEqual(res.status_code, 200, res.content)
        unit = StockUnit.objects.get(serial_number='SERAPR00')
        self.assertEqual((unit.warranty_days, str(unit.sale_price)), (90, '1500.00'))
        log = AdminAuditLog.objects.get(action='stock_units_import_applied')
        self.assertEqual(log.metadata['commercial_terms'], 'applied')
        self.assertEqual(log.metadata['terms_authority'], 'applier')
        self.assertIsNone(log.metadata['terms_approved_by_id'])

    def test_a_load_without_terms_says_none(self):
        job = self.preview(self.stocker, [self.row(0)])
        self.assertEqual(self.apply(job, self.stocker).status_code, 200)
        log = AdminAuditLog.objects.get(action='stock_units_import_applied')
        self.assertEqual(log.metadata['commercial_terms'], 'none')
        self.assertEqual(log.metadata['prepared_by_id'], self.stocker.pk)

    def test_the_preview_answer_says_whether_the_job_carries_terms(self):
        from store.test_cost_imports import upload
        res = client_for(self.manager).post('/api/admin/inventory/units/import/preview/', {
            'file': upload(H, [self.row(0, '90', '1500.00')], title='Equipos'), 'branch': self.branch.pk,
            'reason': 'Compra', 'line': 'used'}, format='multipart')
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertTrue(body['carries_commercial_terms'])
        self.assertIsNone(body['terms_approval'])
        self.assertTrue(body['can_update_prices'])


class ReviewFollowUpTest(TermsApprovalBase, TestCase):
    """Review of #434: the reviewer is always recorded, the reopened job hides the terms, the approver's reach."""

    def test_a_manager_applying_an_approved_job_records_the_reviewer_too(self):
        from store import unit_import_services
        job = self.staged_with_terms(self.manager)
        self.assertEqual(self.approve(job, self.second_manager).status_code, 200)
        digest = unit_import_services.terms_digest(list(job.rows.filter(action='create')))
        self.assertEqual(self.apply(job, self.manager).status_code, 200)
        log = AdminAuditLog.objects.get(action='stock_units_import_applied')
        self.assertEqual(log.metadata['terms_authority'], 'applier')
        self.assertEqual(log.metadata['terms_approved_by_id'], self.second_manager.pk)
        self.assertEqual(log.metadata['terms_digest'], digest[:12])
        self.assertEqual(log.metadata['executed_by_id'], self.manager.pk)

    def test_a_stale_approval_is_not_recorded_as_the_reviewer(self):
        job = self.staged_with_terms(self.manager)
        self.approve(job, self.second_manager)
        row = job.rows.get(normalized_data__serial_number='SERAPR00')
        row.normalized_data = {**row.normalized_data, 'sale_price': '1.00'}
        row.save(update_fields=['normalized_data'])
        self.assertEqual(self.apply(job, self.manager).status_code, 200)
        log = AdminAuditLog.objects.get(action='stock_units_import_applied')
        self.assertIsNone(log.metadata['terms_approved_by_id'])
        self.assertIsNone(log.metadata['terms_digest'])

    def staged_with_review(self):
        job = self.staged_with_terms()
        job.summary = {**job.summary, 'price_review': [{'variant_id': self.used128.pk, 'proposed_price': '1500.00'}]}
        job.save(update_fields=['summary'])
        return job

    def test_the_reopened_job_hides_the_terms_from_a_stocker(self):
        job = self.staged_with_review()
        row_number = job.rows.get(normalized_data__serial_number='SERAPR00').row_number
        body = client_for(self.stocker).get(f'/api/admin/imports/{job.pk}/').json()
        self.assertTrue(body['carries_commercial_terms'])
        self.assertEqual(body['commercial_terms_rows'], [row_number])
        self.assertNotIn('price_review', body['summary'])
        for row in body['rows']:
            self.assertNotIn('sale_price', row['data'])
            self.assertNotIn('warranty_days', row['data'])
        self.assertIn('serial_number', body['rows'][0]['data'])

    def test_the_reopened_job_shows_the_terms_to_a_manager(self):
        job = self.staged_with_review()
        body = client_for(self.second_manager).get(f'/api/admin/imports/{job.pk}/').json()
        self.assertIn('price_review', body['summary'])
        priced = [row for row in body['rows'] if row['data'].get('serial_number') == 'SERAPR00']
        self.assertEqual(priced[0]['data']['sale_price'], '1500.00')
        self.assertEqual(int(priced[0]['data']['warranty_days']), 90)

    def test_an_approver_who_no_longer_reaches_the_branch_no_longer_covers_the_load(self):
        from store.models import Branch, MembershipBranchAccess
        elsewhere = Branch.objects.create(company=self.company, name='Otra sucursal', is_active=True)
        approver, membership = _p2d_member(self.company, 'terms_branch_manager', MANAGER_CAPS,
                                           branches=[self.branch, elsewhere])
        job = self.staged_with_terms()
        self.assertEqual(self.approve(job, approver).status_code, 200)
        MembershipBranchAccess.objects.filter(membership=membership, branch=self.branch).delete()
        cache.clear()
        res = self.apply(job, self.stocker)
        self.assertEqual(res.status_code, 403, res.content)
        self.assertEqual(res.json()['code'], 'prices_forbidden')
        self.assertIn('sucursales', res.json()['detail'])
        self.assert_nothing_registered(job)


PRICE_NOTE = ('Precio del archivo S/ 3199.90 distinto del de la variante «Teléfono X 128 GB Azul usado» (S/ 3000.00; '
              'diferencia +199.90). No se aplica solo: revisa la propuesta antes de registrar.')
NO_PRICE_NOTE = ('«Teléfono X 128 GB Azul usado» aún no tiene precio de venta; el archivo propone S/ 3199.90. No se aplica '
                 'solo: revisa la propuesta antes de registrar.')


class EveryPayloadHidesTheTermsTest(TermsApprovalBase, TestCase):
    """Second review of #434: every answer about a device load hides its terms from whoever may not set them."""

    def staged_with_review(self):
        job = self.staged_with_terms()
        job.summary = {**job.summary, 'price_review': [{'variant_id': self.used128.pk, 'proposed_price': '3199.90'}]}
        job.save(update_fields=['summary'])
        return job

    def history(self, user):
        body = client_for(user).get('/api/admin/imports/?type=units').json()
        return {entry['id']: entry for entry in body['results']}

    def test_the_history_hides_the_price_review_from_a_stocker(self):
        job = self.staged_with_review()
        self.assertNotIn('price_review', self.history(self.stocker)[job.pk]['summary'])
        self.assertIn('price_review', self.history(self.manager)[job.pk]['summary'])

    def test_the_apply_answer_hides_the_price_review_from_a_stocker(self):
        job = self.staged_with_review()
        res = self.apply(job, self.stocker, without_commercial_terms=True)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertNotIn('price_review', res.json()['summary'])

    def test_the_apply_answer_keeps_the_price_review_for_a_manager(self):
        job = self.staged_with_review()
        res = self.apply(job, self.manager)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIn('price_review', res.json()['summary'])

    def test_the_void_answer_hides_the_price_review_from_a_stocker(self):
        job = self.staged_with_review()
        self.assertEqual(self.apply(job, self.stocker, without_commercial_terms=True).status_code, 200)
        res = client_for(self.stocker).post(f'/api/admin/inventory/units/import/{job.pk}/void/',
                                            {'reason': 'Archivo equivocado', 'confirm_used': True}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertNotIn('price_review', res.json()['summary'])

    def with_price_notes(self, job, *, errors=()):
        rows = list(job.rows.order_by('row_number'))
        rows[0].warnings = [PRICE_NOTE, 'Otro aviso.']
        rows[1].warnings = [NO_PRICE_NOTE]
        rows[0].errors = list(errors)
        for row in rows:
            row.save(update_fields=['warnings', 'errors'])
        return job

    def test_the_reopened_job_redacts_the_price_notes_for_a_stocker(self):
        job = self.with_price_notes(self.staged_with_terms())
        body = client_for(self.stocker).get(f'/api/admin/imports/{job.pk}/').json()
        said = ' '.join(w for row in body['rows'] for w in row['warnings'])
        self.assertNotIn('3199.90', said)
        self.assertNotIn('3000.00', said)
        self.assertIn('Otro aviso.', said)
        self.assertIn('quien administra productos', said)
        body = client_for(self.manager).get(f'/api/admin/imports/{job.pk}/').json()
        self.assertIn(PRICE_NOTE, [w for row in body['rows'] for w in row['warnings']])

    def test_the_error_report_redacts_the_price_notes_for_a_stocker(self):
        job = self.with_price_notes(self.staged_with_terms(), errors=['Fila con error.'])
        stocker_csv = client_for(self.stocker).get(f'/api/admin/imports/{job.pk}/errors.csv/')
        self.assertEqual(stocker_csv.status_code, 200)
        text = stocker_csv.content.decode('utf-8')
        self.assertIn('Fila con error.', text)
        self.assertNotIn('3199.90', text)
        manager_csv = client_for(self.manager).get(f'/api/admin/imports/{job.pk}/errors.csv/').content.decode('utf-8')
        self.assertIn('3199.90', manager_csv)

    def test_terms_free_messages(self):
        from store import import_exports
        redacted = import_exports.terms_free_messages([PRICE_NOTE, NO_PRICE_NOTE, 'Otro aviso.'])
        self.assertEqual(len(redacted), 3)
        self.assertEqual(redacted[2], 'Otro aviso.')
        self.assertFalse(any('S/' in message for message in redacted))

    def test_dropping_with_a_valid_approval_records_no_reviewer(self):
        job = self.staged_with_terms()
        self.approve(job, self.manager)
        self.assertEqual(self.apply(job, self.stocker, without_commercial_terms=True).status_code, 200)
        log = AdminAuditLog.objects.get(action='stock_units_import_applied')
        self.assertEqual(log.metadata['commercial_terms'], 'dropped')
        self.assertIsNone(log.metadata['terms_approved_by_id'])
        self.assertIsNone(log.metadata['terms_digest'])
