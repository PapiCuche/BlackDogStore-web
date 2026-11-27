"""
SUNAT-SERIES-PANEL (C3) — Configuración › Series fiscales (`store/fiscal_series_views.py`).

The owner creates the PRODUCTION series himself, from the panel, before switching SUNAT to
production. Pinned here: only a platform master reaches it; SUNAT's name rules; BETA and
production are separate rows; never two active series for the same slot; `next_number`
only while unused; a used series is never deleted; every change is audited; and the
production preflight sees what the panel created.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from store import fiscal_preflight
from store.models import (
    AdminAuditLog, Branch, FiscalDocument, FiscalDocumentStatus, FiscalDocumentType, FiscalEnvironment, FiscalSeries,
    Order,
)
from store.tests import _p2d_member, _p3_company

URL = '/api/admin/fiscal-series/'
RUC = '20100066603'


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('series-a', 'Empresa Series', tax_id=RUC, legal_name='EMPRESA SERIES SAC')
        self.other = _p3_company('series-b', 'Otra Empresa', tax_id='20999999991')
        self.branch = self.company.default_inventory_branch
        self.master = get_user_model().objects.create_superuser('master', 'master@example.com', 'x')
        self.client = APIClient()
        self.client.force_authenticate(self.master)

    def create(self, **body):
        payload = {'company': self.company.pk, 'document_type': FiscalDocumentType.INVOICE, 'series': 'F001',
                   'environment': FiscalEnvironment.PRODUCTION, **body}
        return self.client.post(URL, payload, format='json')

    def issue_with(self, series):
        order = Order.objects.create(company=series.company, customer_name='C', total=Decimal('118.00'),
                                     status=Order.Status.PAID, paid=True, paid_at=timezone.now())
        return FiscalDocument.objects.create(
            order=order, company=series.company, series_ref=series, document_type=series.document_type,
            series=series.series, number=series.next_number, issued_at=timezone.now(),
            environment=series.environment, issuer_tax_id=RUC, issuer_legal_name='X', customer_doc_type='6',
            customer_doc_number='20000000001', customer_legal_name='C', currency='PEN',
            taxable_amount=Decimal('100.00'), tax_amount=Decimal('18.00'), total=Decimal('118.00'),
            tax_rate=Decimal('0.18'), status=FiscalDocumentStatus.SIGNED, signed_xml='<Invoice/>')


class OnlyAMasterReachesItTest(_Base):
    def test_a_company_owner_is_refused_every_route(self):
        owner, _ = _p2d_member(self.company, 'dueno', ['company.view', 'company.manage', 'sales.fiscal.issue'])
        client = APIClient()
        client.force_authenticate(owner)
        series = FiscalSeries.objects.create(company=self.company, document_type=FiscalDocumentType.INVOICE,
                                             series='F001')
        for response in (client.get(URL, {'company': self.company.pk}), client.post(URL, {}, format='json'),
                         client.patch(f'{URL}{series.pk}/', {'is_active': False}, format='json'),
                         client.delete(f'{URL}{series.pk}/')):
            self.assertEqual(response.status_code, 403)
        self.assertTrue(FiscalSeries.objects.filter(pk=series.pk, is_active=True).exists())
        self.assertFalse(AdminAuditLog.objects.filter(target_type='fiscal_series').exists())


class CreatingASeriesTest(_Base):
    def test_a_production_series_is_created_audited_and_seen_by_the_preflight(self):
        response = self.create(next_number=1)
        self.assertEqual(response.status_code, 201, response.content)
        series = FiscalSeries.objects.get(pk=response.data['id'])
        self.assertEqual((series.environment, series.next_number, series.is_active, series.branch),
                         (FiscalEnvironment.PRODUCTION, 1, True, None))
        entry = AdminAuditLog.objects.get(action='fiscal_series_created')
        self.assertEqual((entry.actor, entry.company, entry.metadata['environment']),
                         (self.master, self.company, FiscalEnvironment.PRODUCTION))
        checks = fiscal_preflight.run(self.company, target=FiscalEnvironment.PRODUCTION)
        self.assertIn('factura: F001 (production)', [c.detail for c in checks if c.area == 'series'])

    def test_the_same_name_in_beta_and_production_are_two_rows(self):
        self.assertEqual(self.create(environment=FiscalEnvironment.BETA).status_code, 201)
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(FiscalSeries.objects.filter(company=self.company, series='F001').count(), 2)
        again = self.create()
        self.assertEqual(again.status_code, 400)
        self.assertIn('series', again.data['errors'])

    def test_sunat_name_rules(self):
        cases = [
            ({'series': 'B001'}, 'empieza por F'),
            ({'document_type': FiscalDocumentType.RECEIPT, 'series': 'F001'}, 'empieza por B'),
            ({'series': 'F01'}, 'Cuatro caracteres'),
            ({'series': 'X001'}, 'Cuatro caracteres'),
            ({'series': 'F0-1'}, 'Cuatro caracteres'),
        ]
        for body, fragment in cases:
            with self.subTest(body=body):
                response = self.create(**body)
                self.assertEqual(response.status_code, 400)
                self.assertIn(fragment, response.data['errors']['series'])
        # Notes take the letter of what they modify; lower case is normalised.
        for kind, name in ((FiscalDocumentType.CREDIT_NOTE, 'fc01'), (FiscalDocumentType.CREDIT_NOTE, 'BC01'),
                           (FiscalDocumentType.DEBIT_NOTE, 'FD01')):
            with self.subTest(kind=kind, name=name):
                self.assertEqual(self.create(document_type=kind, series=name).status_code, 201)
        self.assertTrue(FiscalSeries.objects.filter(series='FC01').exists())

    def test_bad_numbers_environments_and_foreign_branches(self):
        foreign = self.other.default_inventory_branch
        for body, field in (({'next_number': 0}, 'next_number'), ({'next_number': 100_000_000}, 'next_number'),
                            ({'next_number': 'uno'}, 'next_number'), ({'environment': 'staging'}, 'environment'),
                            ({'branch': foreign.pk}, 'branch'), ({'company': 999999}, 'company')):
            with self.subTest(body=body):
                response = self.create(**body)
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data['errors'])
        self.assertFalse(FiscalSeries.objects.exists())

    def test_two_active_series_for_the_same_slot_are_refused(self):
        self.assertEqual(self.create().status_code, 201)
        response = self.create(series='F002')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Desactívala primero', response.data['errors']['is_active'])
        # Inactive, or for a branch, or for BETA, it is another slot.
        self.assertEqual(self.create(series='F002', is_active=False).status_code, 201)
        self.assertEqual(self.create(series='F003', branch=self.branch.pk).status_code, 201)
        self.assertEqual(self.create(series='F004', environment=FiscalEnvironment.BETA).status_code, 201)
        # A note F and a note B do not compete.
        self.assertEqual(self.create(document_type=FiscalDocumentType.CREDIT_NOTE, series='FC01').status_code, 201)
        self.assertEqual(self.create(document_type=FiscalDocumentType.CREDIT_NOTE, series='BC01').status_code, 201)


class ChangingASeriesTest(_Base):
    def setUp(self):
        super().setUp()
        self.series = FiscalSeries.objects.get(pk=self.create(next_number=1).data['id'])

    def patch(self, **body):
        return self.client.patch(f'{URL}{self.series.pk}/', body, format='json')

    def test_next_number_is_editable_only_until_the_first_document(self):
        self.assertEqual(self.patch(next_number=41).status_code, 200)
        self.series.refresh_from_db()
        self.assertEqual(self.series.next_number, 41)
        entry = AdminAuditLog.objects.get(action='fiscal_series_updated')
        self.assertEqual(entry.metadata['changes'], {'next_number': {'from': 1, 'to': 41}})

        self.issue_with(self.series)
        response = self.patch(next_number=1)
        self.assertEqual(response.status_code, 400)
        self.assertIn('no se edita', response.data['errors']['next_number'])
        self.series.refresh_from_db()
        self.assertEqual(self.series.next_number, 41)

    def test_nothing_else_is_changed_here(self):
        response = self.patch(series='F999', environment=FiscalEnvironment.BETA, company=self.other.pk)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(set(response.data['errors']), {'series', 'environment', 'company'})

    def test_deactivating_and_reactivating_respects_the_slot(self):
        self.assertEqual(self.patch(is_active=False).status_code, 200)
        self.assertEqual(self.create(series='F002').status_code, 201)
        response = self.patch(is_active=True)
        self.assertEqual(response.status_code, 400)
        self.assertIn('F002', response.data['errors']['is_active'])

    def test_a_used_series_is_never_deleted_an_unused_one_may_be(self):
        self.issue_with(self.series)
        response = self.client.delete(f'{URL}{self.series.pk}/')
        self.assertEqual(response.status_code, 409)
        self.assertTrue(FiscalSeries.objects.filter(pk=self.series.pk).exists())
        spare = FiscalSeries.objects.get(pk=self.create(series='F009', is_active=False).data['id'])
        self.assertEqual(self.client.delete(f'{URL}{spare.pk}/').status_code, 204)
        self.assertTrue(AdminAuditLog.objects.filter(action='fiscal_series_deleted', target_id=str(spare.pk))
                        .exists())

    def test_the_list_says_what_can_still_be_changed(self):
        self.issue_with(self.series)
        FiscalSeries.objects.create(company=self.other, document_type=FiscalDocumentType.INVOICE, series='F777')
        Branch.objects.create(company=self.other, name='Ajena')
        data = self.client.get(URL, {'company': self.company.pk}).json()
        self.assertEqual([s['series'] for s in data['series']], ['F001'])
        self.assertEqual((data['series'][0]['used'], data['series'][0]['next_number_editable']), (True, False))
        self.assertNotIn('Ajena', [b['name'] for b in data['branches']])
        self.assertIn(self.company.pk, [c['id'] for c in data['companies']])
