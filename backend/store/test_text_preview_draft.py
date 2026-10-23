"""
INV-TEXT-03 — an import draft from a reviewed WhatsApp list, as a DRY RUN of
the official supplier-offer preview: the same importer, its same rules and
answer, and nothing left behind — no offer, no mapping, no job, no stock.
"""
import io
from datetime import timedelta
from unittest import mock

import openpyxl
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from store import supplier_offer_import
from store import supply_services as supply
from store.models import (
    AdminAuditLog, BulkImportJob, BulkImportRow, Product, StockMovement, StockUnit, SupplierOffer,
    SupplierProductRef,
)
from store.test_supplier_offers import today
from store.test_supplier_weekly_import import WeeklyFile
from store.tests import _p2d_member, _p3_company, _prod

URL = '/api/admin/inventory/text-preview/offer-draft/'
FILE_URL = '/api/admin/inventory/text-preview/offer-draft/file/'


class _Draft(WeeklyFile, TestCase):
    def setUp(self):
        self.build_week('txt3-tienda')

    def body(self, *rows, **extra):
        values = {'supplier_id': self.supplier.pk, 'valid_from': self.offer.valid_from.isoformat(),
                  'valid_until': self.offer.valid_until.isoformat(), 'lead_time_days': 4, 'rows': list(rows)}
        values.update(extra)
        return values

    def same_row(self, key='1', **change):
        """A row that says what `self.offer` says now."""
        row = {'key': key, 'product_id': self.case.pk, 'commercial_price': '110', 'confirmed_quantity': 5,
               'agreed_cost': '70'}
        row.update(change)
        return row

    def post(self, body, client=None, url=URL):
        return (client or self.client_).post(url, body, format='json')

    def ok(self, body, client=None):
        response = self.post(body, client)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def counts(self):
        return tuple(model.objects.count() for model in (
            SupplierOffer, SupplierProductRef, StockUnit, StockMovement, BulkImportJob, BulkImportRow,
            AdminAuditLog))


class DryRunTest(_Draft):
    def test_it_answers_what_the_official_preview_would_say(self):
        body = self.ok(self.body(
            self.same_row('1'),
            {'key': '2', 'product_id': self.cable.pk, 'commercial_price': '55', 'confirmed_quantity': 3}))
        self.assertTrue(body['dry_run'])
        self.assertEqual((body['summary']['unchanged'], body['summary']['new']), (1, 1))
        self.assertEqual([row['key'] for row in body['rows']], ['1', '2'])
        self.assertEqual([row['action'] for row in body['rows']], ['no_change', 'create'])

    def test_a_price_change_is_reported_never_applied(self):
        body = self.ok(self.body(self.same_row(commercial_price='120')))
        self.assertEqual(body['summary']['price_changes'], 1)
        self.assertEqual(body['rows'][0]['data']['price_change'], {'before': '110.00', 'after': '120.00'})
        self.assertEqual(str(self.offer_now().commercial_price), '110.00')

    def test_nothing_is_written_not_even_the_preview_job(self):
        before = self.counts()
        state = supplier_offer_import._fingerprint(self.offer_now())
        self.ok(self.body(self.same_row(commercial_price='120', confirmed_quantity=0),
                          {'key': '2', 'product_id': self.cable.pk, 'commercial_price': '55'}))
        self.ok(self.body(self.same_row(), complete=True))
        self.assertEqual(self.counts(), before)
        self.assertEqual(supplier_offer_import._fingerprint(self.offer_now()), state)

    def test_unknown_known_and_zero_quantities_stay_apart(self):
        SupplierOffer.objects.filter(pk=self.offer.pk).update(committed_quantity=0)
        body = self.ok(self.body(
            self.same_row('1', confirmed_quantity=0),
            {'key': '2', 'product_id': self.cable.pk, 'commercial_price': '55', 'confirmed_quantity': None}))
        self.assertEqual(body['summary']['sold_out'], 1)
        created = body['rows'][1]
        self.assertEqual((created['action'], created['data']['confirmed_quantity']), ('create', None))

    def test_the_importers_own_errors_come_back_on_their_row(self):
        used = _prod(self.company, 'Teléfono usado', 'txt3-usado', price='900.00', inventory=0)
        Product.objects.filter(pk=used.pk).update(condition_line=Product.CONDITION_USED)
        body = self.ok(self.body(
            self.same_row('1'), self.same_row('2'),                       # the same variant twice
            {'key': '3', 'product_id': used.pk, 'commercial_price': '900'},
            {'key': '4', 'product_id': self.cable.pk, 'commercial_price': ''}))
        by_key = {row['key']: row for row in body['rows']}
        self.assertEqual(by_key['1']['action'], 'no_change')
        self.assertIn('Repite el proveedor y el producto', by_key['2']['errors'][0])
        self.assertIn('seminuevo', by_key['3']['errors'][0].lower())
        self.assertIn('precio comercial', by_key['4']['errors'][0].lower())
        self.assertEqual(body['summary']['errors'], 3)

    def test_partial_by_default_and_the_complete_list_names_what_it_would_switch_off(self):
        Product.objects.filter(pk=self.cable.pk).update(sale_mode=Product.SALE_MODE_STOCK_OR_ORDER)
        absent = self.make_offer(product=self.cable)
        partial = self.ok(self.body(self.same_row()))
        self.assertNotIn('snapshot', partial)
        complete = self.ok(self.body(self.same_row(), complete=True))
        self.assertEqual([row['offer_id'] for row in complete['snapshot']['absent']], [absent.pk])
        self.assertEqual(complete['snapshot']['deactivate_count'], 1)
        self.assertTrue(SupplierOffer.objects.get(pk=absent.pk).is_active)

    def test_no_ai_is_asked(self):
        with mock.patch('store.inventory_ai.decide', side_effect=AssertionError('AI')) as decide:
            self.ok(self.body(self.same_row()))
        decide.assert_not_called()

    def test_the_file_is_the_official_template_and_previews_the_same_officially(self):
        response = self.post(self.body(self.same_row(commercial_price='120')), url=FILE_URL)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn('attachment', response['Content-Disposition'])
        data = b''.join(response.streaming_content) if response.streaming else response.content
        sheet = openpyxl.load_workbook(io.BytesIO(data)).active
        headers = [cell.value for cell in sheet[1]]
        self.assertEqual(sheet.title, supplier_offer_import.SHEET)
        self.assertTrue(set(headers) <= set(supplier_offer_import.HEADERS) | {supplier_offer_import.IDENTIFIER_HEADER})
        before = self.counts()
        official = self.client_.post('/api/admin/supplier-offers/import/preview/',
                                     {'file': SimpleUploadedFile('borrador.xlsx', data)}, format='multipart').json()
        self.assertEqual((official['summary']['price_changes'], official['rows'][0]['action']), (1, 'update'))
        # The download wrote nothing; only the official preview, uploaded on purpose, staged its job.
        self.assertEqual(self.counts()[4], before[4] + 1)

    def test_a_text_that_looks_like_a_formula_stays_text(self):
        body = self.ok(self.body(self.same_row(commercial_price='=1+1')))
        self.assertEqual(body['rows'][0]['action'], 'error')
        self.assertNotIn('fórmulas', body['rows'][0]['errors'][0])


class CostTest(_Draft):
    def test_without_costs_view_the_cost_neither_travels_nor_comes_back(self):
        client = self.as_(['company.view', 'products.view', 'products.manage'], 'txt3-nocost')
        body = self.ok(self.body(self.same_row(agreed_cost='99')), client)
        self.assertNotIn('agreed_cost', str(body))
        self.assertFalse(body['cost_column_ignored'])
        response = self.post(self.body(self.same_row(agreed_cost='99')), client, url=FILE_URL)
        sheet = openpyxl.load_workbook(io.BytesIO(response.content)).active
        self.assertNotIn('Costo pactado', [cell.value for cell in sheet[1]])

    def test_the_cleaned_draft_drops_the_cost_without_costs_view(self):
        from store import text_preview_draft

        draft = text_preview_draft.clean(self.company, self.body(self.same_row(agreed_cost='99')), with_cost=False)
        self.assertEqual(draft['rows'][0]['agreed_cost'], '')

    def test_with_costs_view_a_cost_change_is_seen(self):
        body = self.ok(self.body(self.same_row(agreed_cost='75')))
        self.assertIn('agreed_cost', body['rows'][0]['data']['changes'])


class GuardTest(_Draft):
    def test_reading_the_catalogue_is_not_enough(self):
        client = self.as_(['company.view', 'products.view', 'costs.view'], 'txt3-viewer')
        self.assertEqual(self.post(self.body(self.same_row()), client).status_code, 403)
        self.assertEqual(self.post(self.body(self.same_row()), client, url=FILE_URL).status_code, 403)

    def test_anonymous_is_refused(self):
        self.assertIn(APIClient().post(URL, self.body(self.same_row()), format='json').status_code, (401, 403))

    def test_another_companys_product_or_supplier_is_not_found(self):
        other = _p3_company('txt3-otra', 'Otra')
        foreign_product = _prod(other, 'Ajeno', 'txt3-ajeno', price='10.00', inventory=0)
        foreign_supplier = supply.create_supplier(company=other, name='Mayorista Lima', actor=None)
        self.assertEqual(self.post(self.body(self.same_row(product_id=foreign_product.pk))).status_code, 404)
        self.assertEqual(self.post(self.body(self.same_row(product_id=999999))).status_code, 404)
        self.assertEqual(self.post(self.body(self.same_row(), supplier_id=foreign_supplier.pk)).status_code, 404)
        self.assertEqual(self.post(self.body(self.same_row(product_id=foreign_product.pk)), url=FILE_URL).status_code,
                         404)

    def test_identifiers_and_the_pasted_text_never_travel(self):
        for extra in ({'imei': '490154203237518'}, {'serial': 'ABC12345'}, {'source': 'iPhone 13 128'},
                      {'quantity': 2}):
            response = self.post(self.body(self.same_row(**extra)))
            self.assertEqual(response.status_code, 400, extra)
        response = self.post({**self.body(self.same_row()), 'text': 'iPhone'})
        self.assertEqual(response.status_code, 400)

    def test_the_shape_is_checked(self):
        for bad in ({'rows': []}, {'rows': 'x'}, {'rows': [self.same_row()] * 301},
                    {'rows': [self.same_row('1'), self.same_row('1')]},
                    {'rows': [self.same_row(product_id='x')]}, {'rows': [self.same_row(confirmed_quantity=True)]},
                    {'rows': [self.same_row(commercial_price='9' * 30)]}, {'complete': 'yes'},
                    {'lead_time_days': 'x' * 30}):
            response = self.post(self.body(**{**{'rows': [self.same_row()]}, **bad}))
            self.assertEqual(response.status_code, 400, bad)

    def test_a_branch_limited_buyer_cannot_draft_a_complete_list(self):
        limited, _ = _p2d_member(self.company, 'txt3-limited', ['company.view', 'products.view', 'products.manage'],
                                 branches=[self.branch])
        client = APIClient()
        client.force_authenticate(limited)
        self.assertEqual(self.post(self.body(self.same_row(), complete=True), client).status_code, 403)
        self.assertEqual(self.post(self.body(self.same_row()), client).status_code, 200)

    def test_an_empty_complete_list_is_refused_and_leaves_nothing(self):
        before = self.counts()
        response = self.post(self.body({'key': '1', 'product_id': self.case.pk, 'commercial_price': '110'},
                                       complete=True, valid_from='', valid_until=''))
        # Every row in error is still a row: the importer stages it; nothing is switched off and nothing stays.
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.counts(), before)

    def test_dates_outside_the_window_are_the_importers_to_judge(self):
        body = self.ok(self.body(self.same_row(), valid_from=(today() + timedelta(days=50)).isoformat(),
                                 valid_until=(today() + timedelta(days=40)).isoformat()))
        self.assertIn('termina antes de empezar', body['rows'][0]['errors'][0])
