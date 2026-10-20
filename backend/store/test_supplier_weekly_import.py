"""
SUPPLIER-DEVICE-FLOW-02 · A — the supplier's weekly Excel, after the owner's decisions.

What these tests pin:

  * the file is a PARTIAL update: only the rows it carries change something; an
    offer it does not mention keeps its quantity, its window and its state;
  * a row updates THE offer of that supplier and variant in place — a new window
    never creates a second offer, and two offers of one supplier and variant are
    never active over the same days (by file or by hand);
  * an explicit quantity 0 is «agotada»: nothing more sells, what was committed
    stays committed;
  * the preview shows what each row changes (before → after) and counts it;
  * a new commercial price is never written in silence: apply asks for an
    explicit decision (accept or keep), and the audit trail keeps before/after;
  * a preview whose offers changed since is not applied (STALE, nothing written);
  * a supplier's own code is learnt and reused; an ambiguous row links nothing;
  * nothing crosses companies, suppliers are independent, sold lines keep their
    snapshot.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from store import supply_services as supply
from store.models import (
    AdminAuditLog, BulkImportJob, OrderItem, Product, SupplierOffer, SupplierProductRef,
)
from store.test_supplier_offers import OFFER_HEADERS, STALE, OfferFile, today
from store.tests import _p2d_member, _p3_company, _prod

D = Decimal
CODE = 'Código del proveedor'
IDENTIFIER = 'Identificador'


class WeeklyFile(OfferFile):
    def build_week(self, slug='sdf2-tienda'):
        self.build_excel(slug)
        self.cable = _prod(self.company, 'Cable USB-C', f'{slug}-cable', price='40.00', inventory=0)
        Product.objects.filter(pk=self.cable.pk).update(sale_mode=Product.SALE_MODE_STOCK_OR_ORDER)
        self.cable.refresh_from_db()

    def same(self, **change):
        """A row that says exactly what `self.offer` says now (window, price, quantity, lead time, cost)."""
        values = {'Vigente desde': self.offer.valid_from.isoformat(),
                  'Vigente hasta': self.offer.valid_until.isoformat(),
                  'Precio comercial': '110', 'Cantidad confirmada': 5, 'Plazo (días)': 4, 'Costo pactado': '70'}
        values.update(change)
        return self.row(**values)

    def as_(self, perms, name):
        user, _ = _p2d_member(self.company, name, perms)
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def offer_now(self):
        return SupplierOffer.objects.get(pk=self.offer.pk)


class PartialUpdateTest(WeeklyFile, TestCase):
    def setUp(self):
        self.build_week()

    def test_a_window_that_starts_today_or_before_updates_the_running_offer_in_place(self):
        # Review P1-b: only a row that starts on or before today may move the offer running today.
        start, end = today(), today() + timedelta(days=13)
        body = self.preview([self.same(**{'Vigente desde': start.isoformat(), 'Vigente hasta': end.isoformat(),
                                          'Cantidad confirmada': 9})]).json()
        row = body['rows'][0]
        self.assertEqual((row['action'], row['data']['offer_id']), ('update', self.offer.pk), row)
        self.assertEqual(body['rows_create'], 0)
        self.assertEqual(self.apply(body['id']).status_code, 200)
        self.assertEqual(SupplierOffer.objects.filter(supplier=self.supplier, product=self.case).count(), 1)
        offer = self.offer_now()
        self.assertEqual((offer.valid_from, offer.valid_until, offer.confirmed_quantity), (start, end, 9))

    def test_a_future_window_that_overlaps_the_running_offer_is_an_error(self):
        # Review P1-b: never a silent stop nor an in-place move of what sells today.
        body = self.preview([self.same(**{'Vigente desde': (today() + timedelta(days=3)).isoformat(),
                                          'Vigente hasta': (today() + timedelta(days=10)).isoformat()})]).json()
        row = body['rows'][0]
        self.assertEqual((row['action'], row['data'].get('error_code')), ('error', 'overlaps_running'), row)
        self.assertTrue(any('se está vendiendo' in e for e in row['errors']), row)
        self.assertEqual(self.offer_now().valid_until, self.offer.valid_until)

    def test_offers_the_file_does_not_mention_are_untouched(self):
        other = self.make_offer(product=self.cable, confirmed_quantity=3, commercial_price='35.00')
        before = SupplierOffer.objects.get(pk=other.pk)
        body = self.preview([self.same(**{'Cantidad confirmada': 7})]).json()
        self.assertEqual(self.apply(body['id']).status_code, 200)
        after = SupplierOffer.objects.get(pk=other.pk)
        self.assertEqual(
            (after.confirmed_quantity, after.valid_from, after.valid_until, after.is_active, after.updated_at,
             after.commercial_price),
            (before.confirmed_quantity, before.valid_from, before.valid_until, before.is_active, before.updated_at,
             before.commercial_price))
        self.assertEqual(self.offer_now().confirmed_quantity, 7)

    def test_the_identical_file_twice_duplicates_nothing(self):
        rows = [self.same(**{'Cantidad confirmada': 6}), self.row(**{'Código/SKU del producto': self.cable.slug,
                                                                    'Precio comercial': '39'})]
        first = self.preview(rows).json()
        self.assertEqual((first['rows_update'], first['rows_create']), (1, 1), first['rows'])
        self.assertEqual(self.apply(first['id']).status_code, 200)
        again = self.preview(rows).json()
        self.assertEqual((again['rows_no_change'], again['rows_create'], again['rows_update']), (2, 0, 0),
                         again['rows'])
        self.assertEqual(self.apply(again['id']).status_code, 200)
        self.assertEqual(SupplierOffer.objects.filter(company=self.company).count(), 2)

    def test_two_rows_for_the_same_supplier_and_variant_are_an_error(self):
        body = self.preview([self.same(), self.same(**{'Vigente desde': (today() + timedelta(days=7)).isoformat(),
                                                       'Vigente hasta': (today() + timedelta(days=9)).isoformat()})]
                            ).json()
        self.assertEqual([r['action'] for r in body['rows']], ['no_change', 'error'], body['rows'])

    def test_the_most_recent_active_offer_is_the_one_updated(self):
        # Production may hold two active offers of one supplier and variant over the same days
        # (before this change): the row updates the one that ends last, deterministically.
        later = self.make_offer(valid_from=today(), valid_until=today() + timedelta(days=9))
        body = self.preview([self.same(**{'Cantidad confirmada': 7, 'Vigente desde': later.valid_from.isoformat(),
                                          'Vigente hasta': later.valid_until.isoformat()})]).json()
        row = body['rows'][0]
        self.assertEqual((row['action'], row['data']['offer_id']), ('update', later.pk), row)
        self.assertTrue(any('superpone' in w for w in row['warnings']), row)
        self.assertEqual(self.apply(body['id']).status_code, 200)
        self.assertEqual(self.offer_now().confirmed_quantity, 5)
        self.assertEqual(SupplierOffer.objects.get(pk=later.pk).confirmed_quantity, 7)

    def test_a_future_window_after_the_running_offer_is_a_new_offer(self):
        # Coordinator rule: a window strictly after a still-running offer (a gap of a day or more) never
        # stops it — it is next week's offer, and the overlap guard keeps the two disjoint.
        start, end = today() + timedelta(days=8), today() + timedelta(days=14)
        rows = [self.same(**{'Vigente desde': start.isoformat(), 'Vigente hasta': end.isoformat(),
                             'Cantidad confirmada': 9})]
        body = self.preview(rows).json()
        self.assertEqual(body['rows'][0]['action'], 'create', body['rows'])
        self.assertEqual(self.apply(body['id']).status_code, 200)
        running = self.offer_now()
        self.assertEqual((running.valid_from, running.valid_until, running.confirmed_quantity),
                         (self.offer.valid_from, self.offer.valid_until, 5))
        self.assertEqual(supply.best_offer(self.case, 1), running)          # still sells today
        future = SupplierOffer.objects.get(supplier=self.supplier, product=self.case, valid_from=start)
        self.assertEqual((future.valid_until, future.confirmed_quantity), (end, 9))
        # The identical file again: the future offer is found by its own window, nothing new.
        again = self.preview(rows).json()
        self.assertEqual(again['rows'][0]['action'], 'no_change', again['rows'])
        self.assertEqual(again['rows'][0]['data']['offer_id'], future.pk)
        self.assertEqual(SupplierOffer.objects.filter(supplier=self.supplier, product=self.case).count(), 2)

    def test_the_offer_with_exactly_the_rows_window_comes_first(self):
        # Re-importing a file finds what it made, even when another offer of the pair also reaches those days.
        twin = self.make_offer(valid_from=today() + timedelta(days=2), valid_until=self.offer.valid_until)
        supply.deactivate_offer(twin, actor=self.staff, reason='Pausada')
        twin.refresh_from_db()
        body = self.preview([self.same(**{'Vigente desde': twin.valid_from.isoformat()})]).json()
        row = body['rows'][0]
        self.assertEqual((row['action'], row['data']['offer_id']), ('no_change', twin.pk), row)

    def test_next_weeks_window_right_after_the_running_offer_is_a_new_offer(self):
        # Review P1-b (replaces «a touching window updates in place»): next week's file uploaded early
        # must not move this week's offer — it is a new offer, and this week's keeps selling.
        start = self.offer.valid_until + timedelta(days=1)
        rows = [self.same(**{'Vigente desde': start.isoformat(),
                             'Vigente hasta': (start + timedelta(days=6)).isoformat()})]
        body = self.preview(rows).json()
        self.assertEqual(body['rows'][0]['action'], 'create', body['rows'])
        self.assertEqual(self.apply(body['id']).status_code, 200)
        self.assertEqual(supply.best_offer(self.case, 1), self.offer_now())
        self.assertEqual(self.preview(rows).json()['rows'][0]['action'], 'no_change')
        self.assertEqual(SupplierOffer.objects.filter(supplier=self.supplier, product=self.case).count(), 2)

    def test_a_scheduled_offer_the_row_reaches_is_updated_in_place(self):
        SupplierOffer.objects.filter(pk=self.offer.pk).update(valid_from=today() + timedelta(days=3),
                                                              valid_until=today() + timedelta(days=9))
        body = self.preview([self.same(**{'Vigente desde': (today() + timedelta(days=5)).isoformat(),
                                          'Vigente hasta': (today() + timedelta(days=12)).isoformat()})]).json()
        self.assertEqual((body['rows'][0]['action'], body['rows'][0]['data']['offer_id']),
                         ('update', self.offer.pk), body['rows'])

    def test_an_offer_that_already_ended_is_updated_in_place(self):
        SupplierOffer.objects.filter(pk=self.offer.pk).update(
            valid_from=today() - timedelta(days=9), valid_until=today() - timedelta(days=2), committed_quantity=2)
        body = self.preview([self.row()]).json()
        self.assertEqual((body['rows'][0]['action'], body['rows'][0]['data']['offer_id']),
                         ('update', self.offer.pk), body['rows'])
        self.assertEqual(self.apply(body['id'], accept_price_changes=True).status_code, 200)
        offer = self.offer_now()
        # What was committed stays committed (owner question: does it count against the new quantity?).
        self.assertEqual((offer.committed_quantity, offer.confirmed_quantity, offer.valid_until), (2, 8, self.end))
        self.assertEqual(SupplierOffer.objects.filter(supplier=self.supplier, product=self.case).count(), 1)

    def test_an_offer_switched_off_only_because_it_expired_comes_back_on(self):
        SupplierOffer.objects.filter(pk=self.offer.pk).update(
            valid_from=today() - timedelta(days=9), valid_until=today() - timedelta(days=2))
        self.assertEqual(supply.expire_offers(company=self.company), 1)
        body = self.preview([self.row()]).json()
        row = body['rows'][0]
        self.assertEqual(row['action'], 'update', row)
        self.assertIn('is_active', row['data']['changes'])
        self.apply(body['id'], accept_price_changes=True)
        self.assertTrue(self.offer_now().is_active)

    def test_a_person_typing_the_expiry_reason_does_not_make_it_expired(self):
        # Review P3: «off only by expiry» is what the expiry job audited, never a reason anybody can type.
        SupplierOffer.objects.filter(pk=self.offer.pk).update(
            valid_from=today() - timedelta(days=9), valid_until=today() - timedelta(days=2))
        supply.deactivate_offer(self.offer, actor=self.staff, reason='Vigencia vencida')
        body = self.preview([self.row()]).json()
        self.assertNotIn('is_active', body['rows'][0]['data']['changes'], body['rows'])
        self.apply(body['id'], accept_price_changes=True)
        self.assertFalse(self.offer_now().is_active)

    def test_an_offer_switched_off_by_hand_stays_matched_and_off(self):
        supply.deactivate_offer(self.offer, actor=self.staff, reason='Proveedor sin stock')
        body = self.preview([self.same(**{'Cantidad confirmada': 7, 'Vigente hasta': (
            today() + timedelta(days=9)).isoformat()})]).json()
        row = body['rows'][0]
        self.assertEqual((row['action'], row['data']['offer_id']), ('update', self.offer.pk), row)
        self.apply(body['id'])
        self.assertEqual((self.offer_now().is_active, self.offer_now().confirmed_quantity), (False, 7))
        self.assertEqual(SupplierOffer.objects.filter(supplier=self.supplier, product=self.case).count(), 1)

    def test_an_import_never_creates_an_offer_over_the_days_of_another_active_one(self):
        # An offer that ended but is still switched on (the hourly command did not run yet), and next
        # month's offer: a row between them is a new offer, and it would reach the ended one's days.
        SupplierOffer.objects.filter(pk=self.offer.pk).update(
            valid_from=today() - timedelta(days=9), valid_until=today() - timedelta(days=1))
        self.make_offer(valid_from=today() + timedelta(days=20), valid_until=today() + timedelta(days=25))
        body = self.preview([self.row(**{'Vigente desde': (today() - timedelta(days=3)).isoformat()})]).json()
        self.assertEqual(body['rows'][0]['action'], 'error', body['rows'])
        self.assertTrue(any('superpone' in e for e in body['rows'][0]['errors']))

    def test_several_suppliers_of_one_variant_are_independent(self):
        other = supply.create_supplier(company=self.company, name='Importadora Norte', actor=self.staff)
        body = self.preview([self.row(**{'Proveedor': 'Importadora Norte', 'Precio comercial': '108'})]).json()
        self.assertEqual(body['rows'][0]['action'], 'create', body['rows'])
        self.assertEqual(self.apply(body['id']).status_code, 200)
        self.assertTrue(SupplierOffer.objects.filter(supplier=other, product=self.case).exists())
        mine = self.offer_now()
        self.assertEqual((mine.commercial_price, mine.confirmed_quantity), (D('110.00'), 5))


class ByHandOverlapTest(WeeklyFile, TestCase):
    def setUp(self):
        self.build_week('sdf2-mano')

    def test_creating_an_offer_over_the_days_of_another_active_one_is_refused(self):
        payload = {'supplier_id': self.supplier.pk, 'product_id': self.case.pk, 'commercial_price': '100.00',
                   'confirmed_quantity': 2, 'valid_from': str(today() + timedelta(days=3)),
                   'valid_until': str(today() + timedelta(days=9)), 'lead_time_days': 3}
        res = self.client_.post('/api/admin/supplier-offers/', payload, format='json')
        self.assertEqual((res.status_code, res.json().get('code')), (400, 'overlap'), res.content)
        after = {**payload, 'valid_from': str(today() + timedelta(days=7)),
                 'valid_until': str(today() + timedelta(days=9))}
        self.assertEqual(self.client_.post('/api/admin/supplier-offers/', after, format='json').status_code, 201)
        # Another supplier, the same days: independent.
        other = supply.create_supplier(company=self.company, name='Importadora Norte', actor=self.staff)
        self.assertEqual(self.client_.post('/api/admin/supplier-offers/', {**payload, 'supplier_id': other.pk},
                                           format='json').status_code, 201)

    def test_moving_or_switching_on_an_offer_over_another_active_one_is_refused(self):
        later = self.make_offer(valid_from=today() + timedelta(days=7), valid_until=today() + timedelta(days=9))
        url = f'/api/admin/supplier-offers/{later.pk}/'
        res = self.client_.patch(url, {'valid_from': str(today() + timedelta(days=5))}, format='json')
        self.assertEqual((res.status_code, res.json().get('code')), (400, 'overlap'), res.content)
        supply.deactivate_offer(self.offer, actor=self.staff, reason='Sin stock')
        self.assertEqual(self.client_.patch(url, {'valid_from': str(today() + timedelta(days=5))},
                                            format='json').status_code, 200)
        res = self.client_.patch(f'/api/admin/supplier-offers/{self.offer.pk}/', {'is_active': True}, format='json')
        self.assertEqual((res.status_code, res.json().get('code')), (400, 'overlap'), res.content)
        # A change that adds no overlap (the price) is never refused.
        self.assertEqual(self.client_.patch(url, {'commercial_price': '101.00'}, format='json').status_code, 200)


class SoldOutTest(WeeklyFile, TestCase):
    def setUp(self):
        self.build_week('sdf2-agotada')

    def test_an_explicit_zero_is_sold_out_and_keeps_what_was_committed(self):
        SupplierOffer.objects.filter(pk=self.offer.pk).update(committed_quantity=2)
        body = self.preview([self.same(**{'Cantidad confirmada': 0})]).json()
        row = body['rows'][0]
        self.assertEqual(row['action'], 'update', row)
        self.assertTrue(row['data']['sold_out'])
        self.assertTrue(any('agotada' in w.lower() for w in row['warnings']), row)
        self.assertEqual(body['summary']['sold_out'], 1)
        self.assertEqual(body['summary']['updated'], 0)
        res = self.apply(body['id'])
        self.assertEqual((res.status_code, res.json()['sold_out']), (200, 1), res.content)
        offer = self.offer_now()
        self.assertEqual((offer.confirmed_quantity, offer.committed_quantity, offer.remaining), (2, 2, 0))
        self.assertIsNone(supply.best_offer(self.case, 1))

    def test_zero_with_nothing_committed_confirms_zero(self):
        body = self.preview([self.same(**{'Cantidad confirmada': 0})]).json()
        self.assertEqual(self.apply(body['id']).status_code, 200)
        self.assertEqual(self.offer_now().confirmed_quantity, 0)

    def test_zero_takes_what_was_committed_when_it_is_applied(self):
        """A payment between preview and apply makes the preview stale: sold out never undercuts it."""
        SupplierOffer.objects.filter(pk=self.offer.pk).update(committed_quantity=1)
        body = self.preview([self.same(**{'Cantidad confirmada': 0})]).json()
        SupplierOffer.objects.filter(pk=self.offer.pk).update(committed_quantity=3)
        res = self.apply(body['id'])
        self.assertEqual(res.status_code, 200, res.content)   # the stamp did not move (a raw update)
        offer = self.offer_now()
        self.assertEqual((offer.confirmed_quantity, offer.committed_quantity), (3, 3))

    def test_a_non_zero_quantity_below_what_was_sold_is_still_an_error(self):
        SupplierOffer.objects.filter(pk=self.offer.pk).update(committed_quantity=4)
        body = self.preview([self.same(**{'Cantidad confirmada': 3})]).json()
        self.assertEqual(body['rows'][0]['action'], 'error')


class PreviewDiffTest(WeeklyFile, TestCase):
    def setUp(self):
        self.build_week('sdf2-diff')

    def test_an_update_row_says_what_it_was_and_the_summary_counts(self):
        SupplierOffer.objects.filter(pk=self.offer.pk).update(committed_quantity=1)
        body = self.preview([
            self.same(**{'Cantidad confirmada': 9, 'Plazo (días)': 6, 'Costo pactado': '65'}),
            self.row(**{'Código/SKU del producto': self.cable.slug}),
            self.row(**{'Proveedor': 'Nadie'}),
        ]).json()
        before = body['rows'][0]['data']['before']
        self.assertEqual(before, {
            'commercial_price': '110.00', 'confirmed_quantity': 5, 'committed_quantity': 1,
            'valid_from': self.offer.valid_from.isoformat(), 'valid_until': self.offer.valid_until.isoformat(),
            'lead_time_days': 4, 'is_active': True, 'agreed_cost': '70.00'})
        self.assertEqual(sorted(body['rows'][0]['data']['changes']),
                         ['agreed_cost', 'confirmed_quantity', 'lead_time_days'])
        self.assertEqual(body['summary'], {'new': 1, 'updated': 1, 'unchanged': 0, 'sold_out': 0, 'errors': 1,
                                           'skipped': 0, 'price_changes': 0, 'mappings': 0})

    def test_the_before_block_never_carries_the_cost_without_costs_view(self):
        client = self.as_(['company.view', 'products.view', 'products.manage'], 'sdf2_sin_costo')
        body = self.preview([self.same(**{'Cantidad confirmada': 9})], client=client).json()
        self.assertNotIn('agreed_cost', body['rows'][0]['data']['before'])
        # Not even kept: the staged row never held it.
        from store.models import BulkImportRow
        self.assertNotIn('agreed_cost', BulkImportRow.objects.get(job_id=body['id']).normalized_data['before'])
        # Previewed by costs.view, read back by somebody without it: still absent.
        job = self.preview([self.same(**{'Cantidad confirmada': 9, 'Costo pactado': '61'})]).json()
        self.assertIn('agreed_cost', job['rows'][0]['data']['changes'])
        from store import supplier_offer_import
        payload = supplier_offer_import.job_payload(BulkImportJob.objects.get(pk=job['id']), include_cost=False)
        self.assertNotIn('agreed_cost', payload['rows'][0]['data']['before'])
        self.assertEqual(payload['rows'][0]['data']['changes'], ['confirmed_quantity'])


class PriceDecisionTest(WeeklyFile, TestCase):
    def setUp(self):
        self.build_week('sdf2-precio')

    def test_a_new_commercial_price_is_never_written_without_a_decision(self):
        body = self.preview([self.same(**{'Precio comercial': '120', 'Cantidad confirmada': 8})]).json()
        row = body['rows'][0]
        self.assertEqual(row['data']['price_change'], {'before': '110.00', 'after': '120.00'})
        self.assertEqual(body['summary']['price_changes'], 1)
        refused = self.apply(body['id'])
        self.assertEqual(refused.status_code, 409, refused.content)
        self.assertEqual(refused.json()['code'], 'price_confirmation_required')
        self.assertEqual([r['row_number'] for r in refused.json()['price_changes']], [row['row_number']])
        offer = self.offer_now()
        self.assertEqual((offer.commercial_price, offer.confirmed_quantity), (D('110.00'), 5))   # nothing written
        self.assertEqual(BulkImportJob.objects.get(pk=body['id']).status, BulkImportJob.PREVIEWED)
        both = self.apply(body['id'], accept_price_changes=True, keep_prices=True)
        self.assertEqual(both.status_code, 400)

        kept = self.apply(body['id'], keep_prices=True)
        self.assertEqual((kept.status_code, kept.json()['prices_kept']), (200, 1), kept.content)
        offer = self.offer_now()
        self.assertEqual((offer.commercial_price, offer.confirmed_quantity), (D('110.00'), 8))
        log = AdminAuditLog.objects.get(action='supplier_offer_price_kept', target_id=str(self.offer.pk))
        self.assertEqual((log.metadata['price'], log.metadata['proposed']), ('110.00', '120.00'))

        body = self.preview([self.same(**{'Precio comercial': '120', 'Cantidad confirmada': 8})]).json()
        accepted = self.apply(body['id'], accept_price_changes=True)
        self.assertEqual((accepted.status_code, accepted.json()['prices_changed']), (200, 1), accepted.content)
        self.assertEqual(self.offer_now().commercial_price, D('120.00'))
        log = AdminAuditLog.objects.get(action='supplier_offer_price_changed', target_id=str(self.offer.pk))
        self.assertEqual((log.metadata['before'], log.metadata['after'], log.metadata['job_id']),
                         ('110.00', '120.00', body['id']))

    def test_a_kept_price_with_nothing_else_is_unchanged(self):
        body = self.preview([self.same(**{'Precio comercial': '120'})]).json()
        res = self.apply(body['id'], keep_prices=True)
        self.assertEqual((res.json()['updated'], res.json()['unchanged']), (0, 1), res.content)

    def test_a_file_without_price_changes_needs_no_decision(self):
        body = self.preview([self.same(**{'Cantidad confirmada': 8}),
                             self.row(**{'Código/SKU del producto': self.cable.slug})]).json()
        self.assertEqual(self.apply(body['id']).status_code, 200)


class StalePreviewTest(WeeklyFile, TestCase):
    def setUp(self):
        self.build_week('sdf2-viejo')

    def test_an_offer_changed_since_the_preview_refuses_the_whole_job(self):
        body = self.preview([self.same(**{'Cantidad confirmada': 8}),
                             self.row(**{'Código/SKU del producto': self.cable.slug})]).json()
        self.assertEqual(body['rows'][0]['data']['offer_updated_at'], self.offer_now().updated_at.isoformat())
        supply.update_offer(self.offer, actor=self.staff, lead_time_days=9)
        res = self.apply(body['id'])
        self.assertEqual((res.status_code, res.json().get('code')), (400, 'stale'), res.content)
        self.assertIn(STALE, res.json()['detail'])
        self.assertEqual(self.offer_now().confirmed_quantity, 5)
        self.assertFalse(SupplierOffer.objects.filter(product=self.cable).exists())
        self.assertEqual(BulkImportJob.objects.get(pk=body['id']).status, BulkImportJob.PREVIEWED)

    def test_an_offer_created_by_hand_between_preview_and_apply_makes_it_stale(self):
        # Review P2: the row's new days are checked again, under the lock, against every active offer.
        body = self.preview([self.same(**{'Vigente hasta': (today() + timedelta(days=12)).isoformat()})]).json()
        self.assertEqual(body['rows'][0]['action'], 'update', body['rows'])
        self.make_offer(valid_from=today() + timedelta(days=9), valid_until=today() + timedelta(days=11))
        res = self.apply(body['id'])
        self.assertEqual(res.json().get('code'), 'stale', res.content)
        self.assertEqual(self.offer_now().valid_until, self.offer.valid_until)

    def test_an_unchanged_row_whose_offer_changed_is_stale_too(self):
        body = self.preview([self.same()]).json()
        self.assertEqual(body['rows'][0]['action'], 'no_change')
        supply.update_offer(self.offer, actor=self.staff, confirmed_quantity=2)
        self.assertEqual(self.apply(body['id']).json().get('code'), 'stale')


class SupplierCodeTest(WeeklyFile, TestCase):
    def setUp(self):
        self.build_week('sdf2-codigo')
        self.headers = OFFER_HEADERS + [CODE]

    def coded(self, code, **change):
        return self.row(**change) + [code]

    def test_template_offers_the_supplier_code_column(self):
        import io

        import openpyxl
        res = self.client_.get('/api/admin/supplier-offers/import/template/')
        headers = [c.value for c in openpyxl.load_workbook(io.BytesIO(res.content)).active[1]]
        self.assertIn(CODE, headers)

    def test_a_code_is_learnt_when_another_key_matches_and_reused_next_time(self):
        body = self.preview([self.coded('MAY-001', **{'Vigente desde': self.offer.valid_from.isoformat(),
                                                      'Vigente hasta': self.offer.valid_until.isoformat(),
                                                      'Precio comercial': '110', 'Cantidad confirmada': 5,
                                                      'Plazo (días)': 4, 'Costo pactado': '70'})],
                            headers=self.headers).json()
        row = body['rows'][0]
        self.assertEqual(row['data']['map_supplier_code'], 'MAY-001', row)
        self.assertEqual(body['summary']['mappings'], 1)
        res = self.apply(body['id'])
        self.assertEqual((res.status_code, res.json()['mappings']), (200, 1), res.content)
        ref = SupplierProductRef.objects.get(company=self.company, supplier=self.supplier, supplier_code='MAY-001')
        self.assertEqual(ref.product_id, self.case.pk)
        self.assertTrue(AdminAuditLog.objects.filter(action='supplier_product_ref_created',
                                                     target_id=str(ref.pk)).exists())
        # Next week: only the supplier's code.
        body = self.preview([self.coded('MAY-001', **{'Código/SKU del producto': '', 'Cantidad confirmada': 6,
                                                      'Vigente desde': self.offer.valid_from.isoformat(),
                                                      'Vigente hasta': self.offer.valid_until.isoformat(),
                                                      'Precio comercial': '110', 'Plazo (días)': 4})],
                            headers=self.headers).json()
        row = body['rows'][0]
        self.assertEqual((row['action'], row['data']['product_id'], row['data'].get('matched_by')),
                         ('update', self.case.pk, 'supplier_code'), row)
        self.assertNotIn('map_supplier_code', row['data'])

    def test_a_code_mapped_to_another_variant_is_an_error_not_a_guess(self):
        SupplierProductRef.objects.create(company=self.company, supplier=self.supplier, supplier_code='MAY-001',
                                          product=self.cable)
        body = self.preview([self.coded('MAY-001')], headers=self.headers).json()
        self.assertEqual(body['rows'][0]['action'], 'error', body['rows'])
        self.assertTrue(any('MAY-001' in e for e in body['rows'][0]['errors']))

    def test_the_system_identifier_comes_first(self):
        from store.import_formats import product_identifier
        headers = OFFER_HEADERS + [IDENTIFIER]
        body = self.preview([self.row(**{'Código/SKU del producto': ''}) + [product_identifier(self.company.pk,
                                                                                              self.cable.pk)]],
                            headers=headers).json()
        self.assertEqual((body['rows'][0]['data']['product_id'], body['rows'][0]['data']['matched_by']),
                         (self.cable.pk, 'identifier'), body['rows'])
        # An identifier and a code that name two variants: nothing is guessed.
        body = self.preview([self.row() + [product_identifier(self.company.pk, self.cable.pk)]],
                            headers=headers).json()
        self.assertEqual(body['rows'][0]['action'], 'error', body['rows'])
        # Another company's identifier names nothing here.
        other = _p3_company('sdf2-ajena', 'Ajena')
        alien = _prod(other, 'Ajeno', 'sdf2-ajeno', inventory=0)
        body = self.preview([self.row(**{'Código/SKU del producto': ''}) + [product_identifier(other.pk, alien.pk)]],
                            headers=headers).json()
        self.assertEqual(body['rows'][0]['action'], 'error', body['rows'])

    def test_an_unmatched_row_is_mapped_by_hand_and_then_matches(self):
        body = self.preview([self.row(**{'Código/SKU del producto': 'XYZ-9'})]).json()
        row = body['rows'][0]
        self.assertEqual(row['action'], 'error')
        self.assertEqual(row['data']['mappable'], {'supplier_id': self.supplier.pk, 'supplier_code': 'XYZ-9'})
        url = f'/api/admin/suppliers/{self.supplier.pk}/product-refs/'
        viewer = self.as_(['company.view', 'products.view'], 'sdf2_viewer')
        self.assertEqual(viewer.post(url, {'supplier_code': 'XYZ-9', 'product_id': self.cable.pk},
                                     format='json').status_code, 403)
        res = self.client_.post(url, {'supplier_code': 'XYZ-9', 'product_id': self.cable.pk}, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual((res.json()['supplier_code'], res.json()['product_id']), ('XYZ-9', self.cable.pk))
        self.assertTrue(AdminAuditLog.objects.filter(action='supplier_product_ref_created').exists())
        # Saved again for the same variant: nothing new.
        self.assertEqual(self.client_.post(url, {'supplier_code': 'XYZ-9', 'product_id': self.cable.pk},
                                           format='json').status_code, 200)
        # For another variant: a conflict, unless asked to replace it.
        res = self.client_.post(url, {'supplier_code': 'XYZ-9', 'product_id': self.case.pk}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'ref_conflict'))
        body = self.preview([self.row(**{'Código/SKU del producto': 'XYZ-9'})]).json()
        self.assertEqual((body['rows'][0]['action'], body['rows'][0]['data']['product_id']), ('create', self.cable.pk),
                         body['rows'])

    def test_mapping_is_scoped_by_company(self):
        other = _p3_company('sdf2-otra', 'Otra')
        foreign_supplier = supply.create_supplier(company=other, name='Mayorista Lima')
        foreign_product = _prod(other, 'Ajeno', 'sdf2-otro', inventory=0)
        res = self.client_.post(f'/api/admin/suppliers/{foreign_supplier.pk}/product-refs/',
                                {'supplier_code': 'A', 'product_id': self.case.pk}, format='json')
        self.assertEqual(res.status_code, 404)
        res = self.client_.post(f'/api/admin/suppliers/{self.supplier.pk}/product-refs/',
                                {'supplier_code': 'A', 'product_id': foreign_product.pk}, format='json')
        self.assertEqual(res.status_code, 404)
        # Another company's mapping of the same code never matches here.
        SupplierProductRef.objects.create(company=other, supplier=foreign_supplier, supplier_code='XYZ-9',
                                          product=foreign_product)
        body = self.preview([self.row(**{'Código/SKU del producto': 'XYZ-9'})]).json()
        self.assertEqual(body['rows'][0]['action'], 'error')

    def test_an_ambiguous_model_row_links_nothing(self):
        from store import device_catalog
        capacity = device_catalog.create_attribute(company=self.company, name='Capacidad')
        colour = device_catalog.create_attribute(company=self.company, name='Color')
        gb = device_catalog.create_option(company=self.company, attribute_id=capacity.pk, label='512 GB')
        white = device_catalog.create_option(company=self.company, attribute_id=colour.pk, label='Blanco')
        black = device_catalog.create_option(company=self.company, attribute_id=colour.pk, label='Negro')
        model = device_catalog.create_model(company=self.company, name='Tableta Z')
        for name, option in (('Blanco', white), ('Negro', black)):
            variant = _prod(self.company, f'Tableta Z 512 {name}', f'sdf2-tz-{name.lower()}', price='5000', inventory=0)
            device_catalog.set_variant(variant, model_id=model.pk, option_ids=[gb.pk, option.pk])
        body = self.preview([self.coded('TZ-512', **{'Código/SKU del producto': '', 'Modelo': 'Tableta Z',
                                                     'Capacidad': '512 GB', 'Línea': 'Nuevo'})],
                            headers=self.headers).json()
        row = body['rows'][0]
        self.assertEqual(row['action'], 'error', row)
        self.assertTrue(any('varias variantes' in e for e in row['errors']), row)
        self.assertEqual(row['data']['mappable'], {'supplier_id': self.supplier.pk, 'supplier_code': 'TZ-512'})
        self.assertFalse(SupplierProductRef.objects.exists())


class SnapshotTest(WeeklyFile, TestCase):
    def setUp(self):
        self.build_week('sdf2-foto')

    def test_a_sold_line_keeps_its_price_and_lead_time_when_the_offer_moves(self):
        self.add_on_order(quantity=2)
        order = self.checkout()
        self.pay(order)
        line = OrderItem.objects.get(order=order)
        frozen = (line.price, line.lead_time_days, line.unit_cost, line.supplier_offer_id)
        self.assertEqual(self.offer_now().committed_quantity, 2)
        body = self.preview([self.same(**{'Precio comercial': '130', 'Plazo (días)': 9,
                                          'Vigente hasta': (today() + timedelta(days=20)).isoformat()})]).json()
        self.assertEqual(self.apply(body['id'], accept_price_changes=True).status_code, 200)
        line.refresh_from_db()
        self.assertEqual((line.price, line.lead_time_days, line.unit_cost, line.supplier_offer_id), frozen)
        self.assertEqual(self.offer_now().committed_quantity, 2)


class ConcurrentCreateTest(TransactionTestCase):
    """Two files that create the same supplier's offer for one variant at once: one wins, one is told."""

    def test_one_applies_and_the_other_is_refused(self):
        from threading import Barrier, Thread

        from django.db import connection

        from store import supplier_offer_import as offer_import

        shop = WeeklyFile()
        shop.build_week('sdf2-carrera')
        SupplierOffer.objects.filter(pk=shop.offer.pk).delete()
        first = shop.preview([shop.row()]).json()['id']
        second = shop.preview([shop.row(**{'Vigente desde': (today() + timedelta(days=2)).isoformat(),
                                           'Vigente hasta': (today() + timedelta(days=9)).isoformat()})]).json()['id']
        barrier, outcomes = Barrier(2), []

        def run(job_id):
            try:
                barrier.wait(timeout=5)
                try:
                    offer_import.apply(job=BulkImportJob.objects.get(pk=job_id), actor=shop.staff)
                    outcomes.append('applied')
                except offer_import.OfferImportError as exc:
                    outcomes.append(str(exc))
            finally:
                connection.close()

        threads = [Thread(target=run, args=(job,)) for job in (first, second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertEqual(sorted(o if o == 'applied' else 'stale' for o in outcomes), ['applied', 'stale'], outcomes)
        self.assertTrue(any(STALE in o for o in outcomes), outcomes)
        self.assertEqual(SupplierOffer.objects.filter(supplier=shop.supplier, product=shop.case).count(), 1)
