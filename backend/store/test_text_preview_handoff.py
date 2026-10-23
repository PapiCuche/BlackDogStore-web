"""
INV-TEXT-04 — from the reviewed WhatsApp draft to the OFFICIAL supplier-offer
importer. The draft's workbook goes through the official preview and the
official apply, with their own confirmations; nothing else writes. These are
the owner's cases on that path that no earlier test named.
"""
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from store.models import BulkImportJob, SupplierOffer
from store.test_text_preview_draft import FILE_URL, _Draft
from store.tests import _p2d_member, _p3_company

PREVIEW = '/api/admin/supplier-offers/import/preview/'


class HandoffTest(_Draft):
    def draft_file(self, *rows, client=None, **extra):
        response = self.post(self.body(*rows, **extra), client, url=FILE_URL)
        self.assertEqual(response.status_code, 200, response.content)
        return response.content

    def official_preview(self, data, client=None, **form):
        response = (client or self.client_).post(
            PREVIEW, {'file': SimpleUploadedFile('borrador.xlsx', data), **form}, format='multipart')
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def apply(self, job_id, client=None, **body):
        return (client or self.client_).post(f'/api/admin/supplier-offers/import/{job_id}/apply/', body, format='json')

    def test_the_draft_is_applied_only_by_the_official_apply_with_its_price_confirmation(self):
        data = self.draft_file(
            self.same_row('1', commercial_price='120', confirmed_quantity=7),
            {'key': '2', 'product_id': self.cable.pk, 'commercial_price': '55', 'confirmed_quantity': None})
        self.assertEqual(SupplierOffer.objects.count(), 1)               # the file wrote nothing
        job = self.official_preview(data)
        refused = self.apply(job['id'])
        self.assertEqual((refused.status_code, refused.json()['code']), (409, 'price_confirmation_required'))
        self.assertEqual(str(self.offer_now().commercial_price), '110.00')
        kept = self.apply(job['id'], keep_prices=True)
        self.assertEqual(kept.status_code, 200, kept.content)
        offer = self.offer_now()
        self.assertEqual((str(offer.commercial_price), offer.confirmed_quantity), ('110.00', 7))
        created = SupplierOffer.objects.get(product=self.cable)
        # Unknown quantity stays unknown: it does not sell until the supplier confirms.
        self.assertEqual((created.confirmed_quantity, created.availability_confirmed), (None, False))

    def test_applying_the_same_job_again_writes_nothing_more(self):
        job = self.official_preview(self.draft_file(
            {'key': '1', 'product_id': self.cable.pk, 'commercial_price': '55', 'confirmed_quantity': 3}))
        first = self.apply(job['id'])
        self.assertEqual(first.status_code, 200, first.content)
        again = self.apply(job['id'])
        self.assertEqual((again.status_code, again.json()['created']), (200, 1))
        self.assertEqual(SupplierOffer.objects.filter(product=self.cable).count(), 1)

    def test_who_only_reads_the_catalogue_cannot_apply(self):
        job = self.official_preview(self.draft_file(self.same_row(confirmed_quantity=9)))
        viewer, _ = _p2d_member(self.company, 'txt4-viewer', ['company.view', 'products.view', 'costs.view'])
        client = APIClient()
        client.force_authenticate(viewer)
        self.assertEqual(self.apply(job['id'], client).status_code, 403)
        self.assertEqual(self.offer_now().confirmed_quantity, 5)

    def test_another_companys_job_is_not_found(self):
        job = self.official_preview(self.draft_file(self.same_row(confirmed_quantity=9)))
        other = _p3_company('txt4-otra', 'Otra')
        outsider, _ = _p2d_member(other, 'txt4-outsider', ['company.view', 'products.view', 'products.manage'])
        client = APIClient()
        client.force_authenticate(outsider)
        self.assertEqual(self.apply(job['id'], client).status_code, 404)
        self.assertEqual(self.offer_now().confirmed_quantity, 5)

    def test_an_empty_batch_writes_nothing(self):
        from store.test_cost_imports import upload

        empty = upload(['Proveedor', 'Precio comercial', 'Identificador'], [], title='Ofertas de proveedor').read()
        job = self.official_preview(empty)
        self.assertEqual(job['rows_total'], 0)
        before = SupplierOffer.objects.count()
        self.assertEqual(self.apply(job['id']).status_code, 200)
        self.assertEqual(SupplierOffer.objects.count(), before)
        # The draft never sends an empty batch at all.
        self.assertEqual(self.post(self.body(), url=FILE_URL).status_code, 400)

    def test_cancelling_before_apply_writes_nothing(self):
        before = (SupplierOffer.objects.count(), self.offer_now().confirmed_quantity)
        job = self.official_preview(self.draft_file(self.same_row(confirmed_quantity=0)))
        self.assertEqual(BulkImportJob.objects.get(pk=job['id']).status, BulkImportJob.PREVIEWED)
        self.assertEqual((SupplierOffer.objects.count(), self.offer_now().confirmed_quantity), before)

    def test_a_complete_draft_needs_the_official_confirmations_to_switch_anything_off(self):
        absent = self.make_offer(product=self.cable)
        job = self.official_preview(self.draft_file(self.same_row(), complete=True),
                                    complete_snapshot='true', supplier_id=str(self.supplier.pk))
        self.assertEqual(job['snapshot']['deactivate_count'], 1)
        refused = self.apply(job['id'])
        self.assertEqual(refused.status_code, 409)
        self.assertTrue(SupplierOffer.objects.get(pk=absent.pk).is_active)
        done = self.apply(job['id'], all_messages_included=True, confirm_deactivations=1)
        self.assertEqual(done.status_code, 200, done.content)
        self.assertFalse(SupplierOffer.objects.get(pk=absent.pk).is_active)
