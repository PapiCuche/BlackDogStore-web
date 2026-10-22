"""
INV-SNAPSHOT-01 — a supplier's COMPLETE list (ADR docs/adr-inv-snapshot-external-used.md, owner D1–D2).

Partial files (the default) behave exactly as before. In complete mode, for ONE
supplier: the running offers of that supplier missing from the file stop
selling once a person confirms how many; scheduled (future) ones are left
alone; commitments, orders and payments never change. A quantity not given is
«not confirmed» — never zero, never one — and never sells.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from store import supply_services as supply
from store.models import AdminAuditLog, BulkImportJob, Product, SupplierOffer
from store.test_supplier_offers import today
from store.test_supplier_weekly_import import WeeklyFile
from store.tests import _p2d_member, _prod

PREVIEW = '/api/admin/supplier-offers/import/preview/'


class _Snapshot(WeeklyFile, TestCase):
    def setUp(self):
        self.build_week('snap-tienda')
        Product.objects.filter(pk=self.cable.pk).update(sale_mode=Product.SALE_MODE_STOCK_OR_ORDER)
        self.absent = self.make_offer(product=self.cable)                      # running, not in the file
        self.future_product = _prod(self.company, 'Funda', 'snap-funda', price='30.00', inventory=0)
        Product.objects.filter(pk=self.future_product.pk).update(sale_mode=Product.SALE_MODE_STOCK_OR_ORDER)
        self.future = self.make_offer(product=self.future_product, valid_from=today() + timedelta(days=10),
                                      valid_until=today() + timedelta(days=20))   # scheduled: D1, untouched
        self.other_supplier = supply.create_supplier(company=self.company, name='Otro Mayorista', actor=self.staff)
        self.other_offer = supply.create_offer(
            company=self.company, supplier_id=self.other_supplier.pk, product_id=self.cable.pk, actor=self.staff,
            may_view_costs=True, commercial_price='100.00', confirmed_quantity=3,
            valid_from=today() + timedelta(days=30), valid_until=today() + timedelta(days=40), lead_time_days=2)

    def complete(self, rows, *, supplier=None, client=None):
        return (client or self.client_).post(PREVIEW, {
            'file': self.file(rows), 'complete_snapshot': 'true',
            'supplier_id': str((supplier or self.supplier).pk)}, format='multipart')

    def apply_job(self, job_id, client=None, **body):
        return (client or self.client_).post(f'/api/admin/supplier-offers/import/{job_id}/apply/', body,
                                             format='json')

    def fresh(self, offer):
        return SupplierOffer.objects.get(pk=offer.pk)


class AvailabilityConfirmedTest(_Snapshot):
    def test_a_quantity_confirms_and_no_quantity_does_not(self):
        self.assertTrue(self.fresh(self.offer_now()).availability_confirmed)
        unknown = self.make_offer(product=self.future_product, confirmed_quantity=None)
        self.assertFalse(unknown.availability_confirmed)
        self.assertIsNone(unknown.confirmed_quantity)

    def test_an_unconfirmed_offer_never_sells_even_with_room(self):
        SupplierOffer.objects.filter(pk=self.absent.pk).update(availability_confirmed=False)
        self.assertNotIn(self.absent.pk, set(supply.valid_offers(self.cable).values_list('pk', flat=True)))
        self.assertIsNone(supply.best_offer(self.cable, 1))

    def test_the_database_refuses_a_confirmed_offer_without_a_quantity(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            SupplierOffer.objects.filter(pk=self.absent.pk).update(confirmed_quantity=None, availability_confirmed=True)

    def test_the_panel_never_shows_an_unknown_quantity_as_zero(self):
        from store.supplier_views import offer_payload

        SupplierOffer.objects.filter(pk=self.absent.pk).update(availability_confirmed=False,
                                                                confirmed_quantity=self.absent.committed_quantity)
        payload = offer_payload(self.fresh(self.absent), with_cost=False)
        self.assertEqual((payload['remaining'], payload['availability_confirmed'], payload['state']),
                         (None, False, 'unconfirmed'))


class PartialStaysTheSameTest(_Snapshot):
    def test_a_partial_file_leaves_absent_offers_alone(self):
        body = self.preview([self.same()]).json()
        self.assertNotIn('snapshot', body)
        self.assertEqual(self.apply(body['id']).status_code, 200)
        self.assertTrue(self.fresh(self.absent).is_active)

    def test_a_partial_file_still_refuses_no_quantity_with_commitments(self):
        SupplierOffer.objects.filter(pk=self.offer.pk).update(committed_quantity=2)
        body = self.preview([self.same(**{'Cantidad confirmada': ''})]).json()
        self.assertEqual(body['rows_error'], 1)


class CompleteSnapshotTest(_Snapshot):
    def test_the_preview_lists_running_absent_offers_and_never_future_ones(self):
        body = self.complete([self.same()]).json()
        absent = body['snapshot']['absent']
        self.assertEqual([row['offer_id'] for row in absent], [self.absent.pk])
        self.assertEqual(body['snapshot']['deactivate_count'], 1)

    def test_nothing_is_switched_off_without_the_exact_confirmation(self):
        job = self.complete([self.same()]).json()['id']
        refused = self.apply_job(job, all_messages_included=True)
        self.assertEqual((refused.status_code, refused.json()['code']), (409, 'deactivation_confirmation_required'))
        refused = self.apply_job(job, all_messages_included=True, confirm_deactivations=2)
        self.assertEqual(refused.json()['code'], 'deactivation_confirmation_required')
        refused = self.apply_job(job, confirm_deactivations=1)
        self.assertEqual(refused.json()['code'], 'snapshot_confirmation_required')
        self.assertTrue(self.fresh(self.absent).is_active)

    def test_confirmed_it_switches_off_only_the_absent_running_offer_and_audits_it(self):
        job = self.complete([self.same()]).json()['id']
        response = self.apply_job(job, all_messages_included=True, confirm_deactivations=1)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['deactivated'], 1)
        self.assertFalse(self.fresh(self.absent).is_active)
        self.assertTrue(self.fresh(self.future).is_active)          # D1
        self.assertTrue(self.fresh(self.other_offer).is_active)     # another supplier
        self.assertTrue(self.fresh(self.offer_now()).is_active)
        log = AdminAuditLog.objects.get(action='supplier_snapshot_applied')
        self.assertEqual(log.metadata['deactivated'], [self.absent.pk])
        self.assertNotIn('agreed_cost', str(log.metadata))

    def test_an_absent_offer_with_sales_stops_selling_and_keeps_its_commitments(self):
        SupplierOffer.objects.filter(pk=self.absent.pk).update(committed_quantity=2)
        body = self.complete([self.same()]).json()
        self.assertEqual(body['snapshot']['with_commitments'], 1)
        self.assertEqual(body['snapshot']['absent'][0]['committed'], 2)
        self.apply_job(body['id'], all_messages_included=True, confirm_deactivations=1)
        absent = self.fresh(self.absent)
        self.assertEqual((absent.is_active, absent.committed_quantity, absent.confirmed_quantity), (False, 2, 5))

    def test_no_quantity_with_commitments_keeps_them_and_sells_nothing_more(self):
        SupplierOffer.objects.filter(pk=self.offer.pk).update(committed_quantity=2)
        body = self.complete([self.same(**{'Cantidad confirmada': ''})]).json()
        self.assertEqual(body['rows_error'], 0, body['rows'])
        self.apply_job(body['id'], all_messages_included=True, confirm_deactivations=1)
        offer = self.offer_now()
        self.assertEqual((offer.availability_confirmed, offer.confirmed_quantity, offer.committed_quantity),
                         (False, 2, 2))
        self.assertIsNone(supply.best_offer(self.case, 1))

    def test_a_row_of_another_supplier_is_an_error(self):
        body = self.complete([self.same(), self.same(**{'Proveedor': 'Otro Mayorista'})]).json()
        self.assertEqual(body['rows_error'], 1)

    def test_an_empty_complete_list_is_refused(self):
        response = self.complete([])
        self.assertEqual(response.status_code, 400)

    def test_a_list_with_errors_switches_off_nothing(self):
        body = self.complete([self.same(), self.row(**{'Código/SKU del producto': 'no-existe'})]).json()
        self.assertGreater(body['rows_error'], 0)
        self.assertEqual(self.apply_job(body['id'], all_messages_included=True, confirm_deactivations=1).status_code,
                         400)
        self.assertTrue(self.fresh(self.absent).is_active)

    def test_an_offer_added_by_hand_after_the_preview_makes_it_stale(self):
        job = self.complete([self.same()]).json()['id']
        extra = _prod(self.company, 'Cable 2', 'snap-cable-2', price='30.00', inventory=0)
        self.make_offer(product=extra)
        refused = self.apply_job(job, all_messages_included=True, confirm_deactivations=1)
        self.assertEqual((refused.status_code, refused.json().get('code')), (400, 'stale'))
        self.assertTrue(self.fresh(self.absent).is_active)

    def test_the_same_complete_list_twice_switches_off_nothing_more(self):
        job = self.complete([self.same()]).json()['id']
        self.apply_job(job, all_messages_included=True, confirm_deactivations=1)
        again = self.complete([self.same()]).json()
        self.assertEqual(again['snapshot']['deactivate_count'], 0)

    def test_a_branch_limited_buyer_cannot_run_a_complete_list(self):
        limited, _ = _p2d_member(self.company, 'snap-limited', ['company.view', 'products.view', 'products.manage'],
                                 branches=[self.branch])
        client = APIClient()
        client.force_authenticate(limited)
        self.assertEqual(self.complete([self.same()], client=client).status_code, 403)

    def test_another_companys_supplier_is_not_found(self):
        from store.tests import _p3_company

        other = _p3_company('snap-otra', 'Otra')
        foreign = supply.create_supplier(company=other, name='Ajeno', actor=None)
        self.assertEqual(self.complete([self.same()], supplier=foreign).status_code, 404)

    def test_the_offer_list_and_the_integral_report_say_unconfirmed_not_zero(self):
        SupplierOffer.objects.filter(pk=self.offer.pk).update(committed_quantity=2)
        body = self.complete([self.same(**{'Cantidad confirmada': ''})]).json()
        self.apply_job(body['id'], all_messages_included=True, confirm_deactivations=1)
        listed = self.client_.get('/api/admin/supplier-offers/').json()['results']
        row = next(item for item in listed if item['id'] == self.offer.pk)
        self.assertEqual((row['remaining'], row['state']), (None, 'unconfirmed'))
        from store import inventory_exports

        filters = inventory_exports.ExportFilters(branches=[self.branch])
        sheet = inventory_exports._offers_sheet(self.company, filters)
        line = next(r for r in sheet.rows if r[0] == self.case.name)
        self.assertEqual((line[4], line[5]), (inventory_exports.UNKNOWN_QUANTITY, 'No'))
        self.assertEqual(Decimal(str(line[3])), Decimal('110'))


class ReviewFixesTest(_Snapshot):
    def test_a_list_that_drops_most_running_offers_looks_truncated_and_is_refused(self):
        for i in range(4):
            extra = _prod(self.company, f'Extra {i}', f'snap-extra-{i}', price='30.00', inventory=0)
            Product.objects.filter(pk=extra.pk).update(sale_mode=Product.SALE_MODE_STOCK_OR_ORDER)
            self.make_offer(product=extra)
        body = self.complete([self.same()]).json()   # 1 listed, 5 running absent
        self.assertTrue(body['snapshot']['looks_truncated'])
        refused = self.apply_job(body['id'], all_messages_included=True,
                                 confirm_deactivations=body['snapshot']['deactivate_count'])
        self.assertEqual((refused.status_code, refused.json()['code']), (400, 'snapshot_looks_truncated'))
        self.assertTrue(self.fresh(self.absent).is_active)

    def test_saving_an_unconfirmed_offer_without_touching_the_quantity_keeps_it_unconfirmed(self):
        SupplierOffer.objects.filter(pk=self.absent.pk).update(committed_quantity=2, confirmed_quantity=2,
                                                                availability_confirmed=False)
        offer = supply.update_offer(self.fresh(self.absent), actor=self.staff, may_view_costs=True,
                                    confirmed_quantity=2, lead_time_days=7)
        self.assertFalse(offer.availability_confirmed)
        offer = supply.update_offer(offer, actor=self.staff, may_view_costs=True, confirmed_quantity=6)
        self.assertTrue(offer.availability_confirmed)
