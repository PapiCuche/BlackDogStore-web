"""
ACCOUNTING-02 · vistas B y C, permisos, aislamiento y la concesión inicial.

    GET /api/admin/accounting/fiscal-documents/   vista B (`docs/adr-accounting.md` §4.2, §5)
    GET /api/admin/accounting/pending/            vista C (§4.3)

Vista B: el estado se DERIVA (documento + resumen + baja) y nunca dice
«declarado». Vista C: lo que falta, con un código estable; describe, no ejecuta.
"""
import hashlib

from django.test import TestCase

from store.accounting import fiscal_states
from store.models import (
    FiscalDocumentStatus, FiscalSummaryStatus, FiscalVoidCommunication, FiscalVoidCommunicationDocument,
    FiscalVoidStatus, Order, SalesChannel,
)
from store.test_accounting_operations import (
    FISCAL, OPS, PENDING, SUMMARY, VIEW, AccountingFixture, at,
)
from store.tests import _p2d_member


class FiscalViewTest(AccountingFixture):
    def state(self, document):
        rows = [r for r in self.rows(FISCAL, environment=document.environment)
                if r['document_id'] == document.document_id and r['type'] == document.document_type]
        self.assertEqual(len(rows), 1, rows)
        return rows[0]['state']

    def test_a_signed_invoice_is_not_sent(self):
        doc = self.fiscal(self.sale(receipt_type='factura'), '01')
        self.assertEqual(self.state(doc)['code'], 'signed_not_sent')

    def test_an_accepted_invoice_says_the_cdr_is_unverified(self):
        doc = self.fiscal(self.sale(receipt_type='factura'), '01',
                          status=FiscalDocumentStatus.ACCEPTED, cdr='<ApplicationResponse/>')
        row = [r for r in self.rows(FISCAL) if r['document_id'] == doc.document_id][0]
        self.assertEqual(row['state']['code'], 'accepted')
        self.assertEqual(row['cdr']['authenticity'], 'unverified')
        self.assertTrue(row['cdr']['present'])
        self.assertTrue(row['cdr']['hash_ok'])

    def test_a_boleta_walks_through_its_summary(self):
        order = self.sale()
        boleta = self.fiscal(order, '03')
        self.assertEqual(self.state(boleta)['code'], 'pending_summary')
        summary = self.summary(boleta, FiscalSummaryStatus.SUBMITTED)
        self.assertEqual(self.state(boleta)['code'], 'summary_sent')
        summary.status = FiscalSummaryStatus.SUBMISSION_UNKNOWN
        summary.save()
        self.assertEqual(self.state(boleta)['code'], 'summary_uncertain')
        summary.status = FiscalSummaryStatus.ACCEPTED
        summary.save()
        self.assertEqual(self.state(boleta)['code'], 'reported_by_summary')

    def test_a_rejected_summary_puts_the_boleta_back_to_pending(self):
        boleta = self.fiscal(self.sale(), '03')
        self.summary(boleta, FiscalSummaryStatus.REJECTED, superseded=True)
        self.assertEqual(self.state(boleta)['code'], 'pending_summary_after_rejection')

    def test_a_voided_invoice(self):
        invoice = self.fiscal(self.sale(receipt_type='factura'), '01', status=FiscalDocumentStatus.ACCEPTED,
                              cdr='<ApplicationResponse/>')
        ra = FiscalVoidCommunication.objects.create(
            company=self.company, identifier='RA-20260916-1', correlativo=1,
            reference_date=at(15).date(), issue_date=at(16).date(), environment='production',
            status=FiscalVoidStatus.SUBMITTED)
        FiscalVoidCommunicationDocument.objects.create(
            void_communication=ra, document=invoice, line_id=1, void_reason='Error')
        self.assertEqual(self.state(invoice)['code'], 'void_in_progress')
        ra.status = FiscalVoidStatus.ACCEPTED
        ra.save()
        self.assertEqual(self.state(invoice)['code'], 'voided')

    def test_a_credit_note_carries_its_sign_and_its_original(self):
        order = self.sale('118.00', receipt_type='factura')
        invoice = self.fiscal(order, '01', status=FiscalDocumentStatus.ACCEPTED, cdr='<R/>')
        note = self.fiscal(order, '07', original=invoice, status=FiscalDocumentStatus.ACCEPTED,
                           total='118.00', cdr='<R/>')
        body = self.body(FISCAL)
        row = [r for r in body['results'] if r['type'] == '07'][0]
        self.assertEqual(row['total'], '-118.00')
        self.assertEqual(row['original'], invoice.document_id)
        self.assertEqual(row['key'], f'sale:{order.pk}')
        self.assertEqual(body['totals']['07'], {'count': 1, 'total': '-118.00'})
        self.assertEqual(note.order_id, order.pk)

    def test_beta_is_out_by_default_and_marked_when_asked(self):
        beta = self.fiscal(self.sale(), '03', environment='beta')
        self.assertEqual(self.rows(FISCAL), [])
        rows = self.rows(FISCAL, environment='beta')
        self.assertEqual([r['document_id'] for r in rows], [beta.document_id])
        self.assertTrue(rows[0]['test_environment'])

    def test_a_cdr_whose_text_no_longer_gives_its_hash(self):
        doc = self.fiscal(self.sale(receipt_type='factura'), '01', status=FiscalDocumentStatus.ACCEPTED,
                          cdr='<R/>')
        type(doc).objects.filter(pk=doc.pk).update(cdr_sha256=hashlib.sha256(b'otro').hexdigest())
        row = self.rows(FISCAL)[0]
        self.assertFalse(row['cdr']['hash_ok'])
        self.assertIn(('CDR_HASH_MISMATCH', f'sale:{doc.order_id}'),
                      [(r['code'], r['key']) for r in self.rows(PENDING)])

    def test_the_period_is_the_issue_date(self):
        order = self.sale(paid_at=at(30, 23))
        self.fiscal(order, '03', issued_at=at(1, 9, month=10))
        self.assertEqual(self.rows(FISCAL), [])
        self.assertEqual(len(self.rows(FISCAL, tax_period='202610')), 1)

    def test_the_customer_document_is_masked_without_export(self):
        self.fiscal(self.sale(), '03')
        self.assertEqual(self.rows(FISCAL)[0]['customer']['document_number'], '****8912')
        self.assertEqual(self.rows(FISCAL, user=self.exporter)[0]['customer']['document_number'], '45678912')

    def test_no_xml_travels_in_the_list(self):
        self.fiscal(self.sale(), '03')
        row = self.rows(FISCAL)[0]
        self.assertNotIn('signed_xml', row)
        self.assertNotIn('cdr_xml', row)


class FiscalStateMappingTest(TestCase):
    """ADR T-09 y T-23: cada estado tiene nombre, y ningún nombre declara nada."""

    def test_every_status_is_mapped(self):
        self.assertEqual(set(fiscal_states.DOCUMENT_STATUS), set(FiscalDocumentStatus.values))
        self.assertEqual(set(fiscal_states.SUMMARY_STATUS), set(FiscalSummaryStatus.values))
        self.assertEqual(set(fiscal_states.VOID_STATUS), set(FiscalVoidStatus.values))

    def test_no_label_claims_a_declaration(self):
        for code, label in fiscal_states.LABELS.items():
            lowered = label.lower()
            for word in ('declarad', 'presentad', 'conciliad'):
                self.assertNotIn(word, lowered, code)


class PendingViewTest(AccountingFixture):
    def codes(self, **query):
        return {(r['key'], r['code']) for r in self.rows(PENDING, **query)}

    def test_no_document_and_internal_only(self):
        bare = self.sale(channel=SalesChannel.POS, receipt_type='')
        noted = self.sale(channel=SalesChannel.POS, receipt_type='')
        self.note(noted)
        codes = self.codes()
        self.assertIn((f'sale:{bare.pk}', 'NO_DOCUMENT'), codes)
        self.assertIn((f'sale:{noted.pk}', 'INTERNAL_ONLY'), codes)
        self.assertNotIn((f'sale:{noted.pk}', 'NO_DOCUMENT'), codes)

    def test_requested_but_not_issued(self):
        order = self.sale(receipt_type='boleta')
        self.assertIn((f'sale:{order.pk}', 'REQUESTED_NOT_ISSUED'), self.codes())

    def test_fiscal_pipeline_codes(self):
        unsent = self.sale(receipt_type='factura')
        self.fiscal(unsent, '01')
        uncertain = self.sale(receipt_type='factura')
        self.fiscal(uncertain, '01', status=FiscalDocumentStatus.SUBMISSION_ERROR)
        rejected = self.sale(receipt_type='factura')
        self.fiscal(rejected, '01', status=FiscalDocumentStatus.REJECTED)
        boleta = self.sale()
        self.fiscal(boleta, '03')
        in_summary = self.sale()
        self.summary(self.fiscal(in_summary, '03'), FiscalSummaryStatus.SUBMITTED)
        codes = self.codes()
        self.assertIn((f'sale:{unsent.pk}', 'FISCAL_NOT_SENT'), codes)
        self.assertIn((f'sale:{uncertain.pk}', 'FISCAL_UNCERTAIN'), codes)
        self.assertIn((f'sale:{rejected.pk}', 'FISCAL_REJECTED'), codes)
        self.assertIn((f'sale:{boleta.pk}', 'BOLETA_NOT_SUMMARISED'), codes)
        self.assertIn((f'sale:{in_summary.pk}', 'SUMMARY_PENDING'), codes)

    def test_an_accepted_and_reported_sale_is_not_pending(self):
        invoice = self.sale(receipt_type='factura')
        self.fiscal(invoice, '01', status=FiscalDocumentStatus.ACCEPTED, cdr='<R/>')
        boleta = self.sale()
        self.summary(self.fiscal(boleta, '03'), FiscalSummaryStatus.ACCEPTED)
        regularise = [r for r in self.rows(PENDING) if r['severity'] == 'regularizar']
        self.assertEqual(regularise, [])

    def test_cancelled_with_a_live_document(self):
        order = self.sale()
        self.fiscal(order, '03')
        self.cancel(order, fiscal_pending=True)
        self.assertIn((f'sale:{order.pk}', 'CANCELLED_WITH_LIVE_DOC'), self.codes())

    def test_refund_and_note_mismatches(self):
        refunded = self.sale(receipt_type='factura')
        self.fiscal(refunded, '01', status=FiscalDocumentStatus.ACCEPTED, cdr='<R/>')
        self.cancel(refunded)
        self.refund(refunded, '118.00', confirmed_at=at(20))
        noted = self.sale(receipt_type='factura')
        invoice = self.fiscal(noted, '01', status=FiscalDocumentStatus.ACCEPTED, cdr='<R/>')
        self.fiscal(noted, '07', original=invoice, status=FiscalDocumentStatus.ACCEPTED, cdr='<R/>')
        pending = self.sale()
        self.cancel(pending)
        self.refund(pending, '10.00', created_at=at(19))
        codes = self.codes()
        self.assertIn((f'sale:{refunded.pk}', 'REFUND_WITHOUT_NOTE'), codes)
        self.assertIn((f'sale:{noted.pk}', 'NOTE_WITHOUT_REFUND'), codes)
        self.assertIn((f'sale:{pending.pk}', 'REFUND_PENDING'), codes)

    def test_informative_codes(self):
        both = self.sale()
        self.note(both)
        self.fiscal(both, '03')
        beta = self.sale()
        self.fiscal(beta, '03', environment='beta')
        mismatch = self.sale('118.00')
        self.fiscal(mismatch, '03', total='100.00')
        unbranched = self.sale(branch=None)
        Order.objects.filter(pk=unbranched.pk).update(fulfillment_branch=None)
        codes = self.codes()
        self.assertIn((f'sale:{both.pk}', 'INTERNAL_AND_FISCAL'), codes)
        self.assertIn((f'sale:{beta.pk}', 'BETA_DOCUMENT'), codes)
        self.assertIn((f'sale:{mismatch.pk}', 'AMOUNT_MISMATCH'), codes)
        self.assertIn((f'sale:{unbranched.pk}', 'NO_BRANCH'), codes)

    def test_unpaid_orders_are_pending_not_income(self):
        """P2 provisional: lo no pagado va a la vista C, nunca a la A."""
        order = self.sale(pay=False)
        Order.objects.filter(pk=order.pk).update(created_at=at(10))
        self.assertIn((f'sale:{order.pk}', 'PENDING_PAYMENT'), self.codes())
        self.assertEqual(self.rows(OPS), [])

    def test_test_payments_and_services(self):
        test = self.sale()
        self.payment(test, environment='test')
        repair = self.repair()
        self.repair_payment(repair, '20.00')
        codes = self.codes()
        self.assertIn((f'sale:{test.pk}', 'TEST_PAYMENT'), codes)
        self.assertIn((f'repair:{repair.pk}', 'SERVICE_NO_DOCUMENT'), codes)

    def test_every_row_has_a_severity_and_a_message(self):
        self.sale(channel=SalesChannel.POS, receipt_type='')
        for row in self.rows(PENDING):
            self.assertIn(row['severity'], ('regularizar', 'revisar', 'info'))
            self.assertTrue(row['message'])

    def test_counts_by_code(self):
        self.sale(channel=SalesChannel.POS, receipt_type='')
        self.sale(channel=SalesChannel.POS, receipt_type='')
        self.assertEqual(self.body(PENDING)['counts']['NO_DOCUMENT'], 2)

    def test_the_view_writes_nothing(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        order = self.sale()
        self.fiscal(order, '03')
        for url in (OPS, FISCAL, PENDING, SUMMARY):
            with CaptureQueriesContext(connection) as ctx:
                self.get(url)
            writes = [q['sql'] for q in ctx.captured_queries
                      if q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))]
            self.assertEqual(writes, [], url)


class AccessTest(AccountingFixture):
    """403 sin la capacidad; 404 la empresa ajena; la sucursal manda."""

    URLS = (OPS, FISCAL, PENDING, SUMMARY)

    def test_without_the_capability_it_is_refused(self):
        clerk, _ = _p2d_member(self.company, 'acct_sin_permiso', ['company.view', 'sales.orders.view'])
        for url in self.URLS:
            self.assertEqual(self.get(url, clerk).status_code, 403, url)

    def test_anonymous_is_refused(self):
        from rest_framework.test import APIClient

        for url in self.URLS:
            self.assertIn(APIClient().get(url).status_code, (401, 403), url)

    def test_another_company_answers_404(self):
        for url in self.URLS:
            self.assertEqual(self.get(url, company=self.other.pk).status_code, 404, url)

    def test_nothing_of_another_company_leaks(self):
        foreign = self.sale('999.00', company=self.other)
        self.fiscal(foreign, '03')
        self.note(foreign)
        foreign_repair = self.repair(company=self.other)
        self.repair_payment(foreign_repair, '77.00')
        mine = self.sale('118.00')
        for url in self.URLS:
            text = self.get(url).content.decode()
            self.assertNotIn('999.00', text, url)
            self.assertNotIn('77.00', text, url)
            self.assertNotIn(f'sale:{foreign.pk}"', text, url)
        self.assertEqual(self.keys(), [f'sale:{mine.pk}'])

    def test_a_selected_member_sees_only_their_branches(self):
        here = self.sale('118.00', branch=self.norte)
        self.sale('236.00', branch=self.centro)
        repair_here = self.repair(branch=self.norte)
        self.repair_payment(repair_here, '10.00')
        self.repair_payment(self.repair(branch=self.centro), '20.00')
        unbranched = self.sale('5.00')
        Order.objects.filter(pk=unbranched.pk).update(fulfillment_branch=None)
        local, _ = _p2d_member(self.company, 'acct_norte', VIEW, branches=[self.norte])
        self.assertEqual(sorted(self.keys(user=local)), sorted([f'sale:{here.pk}', f'repair:{repair_here.pk}']))
        self.assertEqual(self.keys(user=local, branch=self.centro.pk), [])
        self.assertFalse(self.body(user=local)['scope']['company_wide'])

    def test_a_company_wide_viewer_sees_the_unbranched_history(self):
        unbranched = self.sale('5.00')
        Order.objects.filter(pk=unbranched.pk).update(fulfillment_branch=None)
        body = self.body()
        self.assertEqual([r['key'] for r in body['results']], [f'sale:{unbranched.pk}'])
        self.assertTrue(body['scope']['company_wide'])

    def test_the_scope_notice_is_always_there(self):
        for url in self.URLS:
            notice = self.body(url)['notice']
            self.assertIn('No incluye compras, gastos, bancos', notice, url)

    def test_the_platform_master_holds_both(self):
        from django.contrib.auth import get_user_model

        from store.tenancy import has_capability

        master = get_user_model().objects.create_superuser('acct_master', 'm@example.com', 'Pass123!')
        self.assertTrue(has_capability(master, self.company, 'accounting.view'))
        self.assertTrue(has_capability(master, self.company, 'accounting.export'))


class CatalogueTest(TestCase):
    def test_both_capabilities_are_active_and_assignable(self):
        from store.capabilities import CAPABILITIES, STATUS_ACTIVE

        for code in ('accounting.view', 'accounting.export'):
            self.assertEqual(CAPABILITIES[code].status, STATUS_ACTIVE, code)
            self.assertTrue(CAPABILITIES[code].is_assignable, code)

    def test_a_legacy_admin_gets_them_and_a_legacy_seller_does_not(self):
        from store.models import UserProfile
        from store.tenancy import LEGACY_ROLE_CAPABILITIES

        self.assertIn('accounting.export', LEGACY_ROLE_CAPABILITIES[UserProfile.ROLE_ADMIN])
        self.assertNotIn('accounting.view', LEGACY_ROLE_CAPABILITIES[UserProfile.ROLE_SALES])

    def test_a_new_company_administrator_gets_them(self):
        from store.tests import _p3_company

        company = _p3_company('acct-nueva', 'Nueva')
        admin = company.roles.get(slug='administrador')
        self.assertIn('accounting.view', admin.capabilities)
        self.assertIn('accounting.export', admin.capabilities)
        ventas = company.roles.get(slug='ventas')
        self.assertNotIn('accounting.view', ventas.capabilities)


class GrantMigrationTest(TestCase):
    """P3 provisional: los roles que hoy tienen `company.manage` reciben las dos."""

    def migration(self):
        import importlib
        import pkgutil

        import store.migrations as package

        names = [m.name for m in pkgutil.iter_modules(package.__path__) if m.name.endswith('_accounting_capabilities')]
        self.assertEqual(len(names), 1, names)
        return importlib.import_module(f'store.migrations.{names[0]}')

    def test_holders_of_company_manage_receive_both_and_nobody_else(self):
        from django.apps import apps

        from store.tests import _p3_company, _role

        company = _p3_company('acct-mig', 'Migrada')
        manager = _role(company, 'Gerencia', ['company.view', 'company.manage'], slug='gerencia')
        seller = _role(company, 'Caja', ['company.view', 'sales.pos.use'], slug='caja')
        type(manager).objects.filter(slug='administrador', company=company).update(
            capabilities=['company.manage', 'company.view'])

        self.migration().grant(apps, None)

        manager.refresh_from_db()
        seller.refresh_from_db()
        admin = company.roles.get(slug='administrador')
        for role in (manager, admin):
            self.assertIn('accounting.view', role.capabilities, role.slug)
            self.assertIn('accounting.export', role.capabilities, role.slug)
        self.assertNotIn('accounting.view', seller.capabilities)

    def test_it_is_idempotent_and_frozen(self):
        import inspect

        from django.apps import apps

        from store.accounting.defaults import GRANT_TO_HOLDERS_OF
        from store.tests import _p3_company, _role

        module = self.migration()
        source = inspect.getsource(module)
        self.assertNotIn('from store', source)
        self.assertEqual(module.HOLDERS_OF, GRANT_TO_HOLDERS_OF)
        company = _p3_company('acct-mig2', 'Migrada 2')
        role = _role(company, 'Gerencia', ['company.manage'], slug='gerencia2')
        module.grant(apps, None)
        role.refresh_from_db()
        first = list(role.capabilities)
        module.grant(apps, None)
        role.refresh_from_db()
        self.assertEqual(role.capabilities, first)
        self.assertEqual(len(first), len(set(first)))
