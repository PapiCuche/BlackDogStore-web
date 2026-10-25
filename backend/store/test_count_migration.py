"""
MIGRATION-C — the accessory count engine (docs/adr-migration-count.md §3, §5, §6, §7, §8).

What these prove, owner case by owner case:

* the snapshot is taken when a line is FIRST COUNTED, not when it is listed, with a
  Kardex watermark; a movement of the pair inside the observation window makes the
  line ambiguous, and a migration count is not approved until it is recounted;
* increment mode adds one under the line's lock and refuses a stale `expected`
  (409) without adding; a total replaces;
* blank is «not counted» and is never written off; an explicit 0 is;
* the old system's quantity is a reference that is never applied;
* a correction from somewhere else after the snapshot blocks approval (every kind);
* approval of a migration count runs only from `review`, needs
  `inventory.counts.approve`, is all-or-nothing and idempotent, and writes only
  through `create_stock_movement`;
* nothing of another company or another branch is reachable.
"""

import threading
import time
from decimal import Decimal
from unittest import mock

from django.apps import apps as django_apps
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from store import count_services, inventory_services
from store.capabilities import is_assignable_capability
from store.inventory_services import (
    InventoryCountError,
    approve_inventory_count,
    branch_quantity,
    create_inventory_count,
    create_stock_movement,
    set_count_item,
)
from store.models import (
    AdminAuditLog,
    BranchStock,
    BulkImportJob,
    BulkImportRow,
    CompanyRole,
    InventoryCount,
    ProductBarcode,
    StockMovement,
    StockTransfer,
    StockTransferItem,
)
from store.tests import _p2d_branch, _p2d_company, _p2d_member, _p2d_product, _p2d_stock

COUNTS = '/api/admin/inventory/counts/'
_COUNTERS = ['company.view', 'inventory.view', 'inventory.adjust']
_APPROVERS = _COUNTERS + ['inventory.counts.approve']


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _sale(branch, product, quantity):
    return create_stock_movement(
        branch=branch, product_id=product.pk, movement_type=StockMovement.SALE_EXIT,
        quantity=quantity, reason='Venta durante el conteo',
    )


def _counts_approve_migration():
    """MIGRATION-C's capability migration, found by name: its number is taken at merge time."""
    import pkgutil
    from importlib import import_module

    from store import migrations

    (name,) = [m.name for m in pkgutil.iter_modules(migrations.__path__)
               if m.name.endswith('_inventory_counts_approve_capability')]
    return import_module(f'store.migrations.{name}')


class _CountFixture:
    def build(self, slug):
        cache.clear()
        self.company = _p2d_company(slug)
        self.branch = _p2d_branch(self.company, 'Centro')
        self.other_branch = _p2d_branch(self.company, 'Norte')
        self.cable = _p2d_product(self.company, 'Cable USB-C', f'{slug}-cable')
        ProductBarcode.objects.create(
            company=self.company, product=self.cable, code='7750001', is_primary=True,
        )
        _p2d_stock(self.branch, self.cable, 10)
        self.case = _p2d_product(self.company, 'Funda MagSafe', f'{slug}-funda')
        ProductBarcode.objects.create(
            company=self.company, product=self.case, code='7750002', is_primary=True,
        )
        _p2d_stock(self.branch, self.case, 4)
        self.empty = _p2d_product(self.company, 'Mica sin stock', f'{slug}-mica')
        self.phone = _p2d_product(self.company, 'Teléfono', f'{slug}-phone')
        self.phone.is_serialized = True
        self.phone.save(update_fields=['is_serialized'])
        ProductBarcode.objects.create(company=self.company, product=self.phone, code='7759999')
        self.counter, _ = _p2d_member(self.company, f'{slug}-counter', _COUNTERS)
        self.approver, _ = _p2d_member(self.company, f'{slug}-approver', _APPROVERS)


class CountContractTest(_CountFixture, TestCase):
    def setUp(self):
        self.build('migc')
        self.api = _client(self.counter)
        self.boss = _client(self.approver)

    # --- helpers ---------------------------------------------------------

    def open(self, kind='migration', branch=None, client=None):
        res = (client or self.api).post(
            COUNTS, {'branch': (branch or self.branch).pk, 'kind': kind}, format='json',
        )
        self.assertEqual(res.status_code, 201, res.content)
        return res.json()

    def line(self, data, product):
        return next(i for i in data['items'] if i['product'] == product.pk)

    def scan(self, count_id, client=None, **body):
        return (client or self.api).post(f'{COUNTS}{count_id}/scan/', body, format='json')

    def put(self, count_id, lines, client=None):
        return (client or self.api).put(f'{COUNTS}{count_id}/items/', lines, format='json')

    def submit(self, count_id):
        res = self.api.post(f'{COUNTS}{count_id}/submit/')
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def approve(self, count_id, client=None):
        return (client or self.boss).post(f'{COUNTS}{count_id}/approve/')

    # --- kind and migration counts --------------------------------------------

    def test_every_count_says_its_kind(self):
        self.assertEqual(self.open('regular')['kind'], 'regular')
        self.assertEqual(self.open('migration')['kind'], 'migration')
        listed = self.api.get(COUNTS).json()['results']
        self.assertTrue(all('kind' in c for c in listed))

    def test_a_migration_count_lists_its_pending_lines_without_a_snapshot(self):
        data = self.open()
        products = {i['product'] for i in data['items']}
        self.assertEqual(products, {self.cable.pk, self.case.pk}, 'stock > 0, not serialized')
        cable = self.line(data, self.cable)
        self.assertIsNone(cable['physical_quantity'])
        self.assertIsNone(cable['theoretical_at_start'], 'no snapshot until counted')
        self.assertEqual(cable['review_state'], 'pending')
        self.assertEqual(cable['current_theoretical'], 10)
        self.assertEqual(cable['product_code'], '7750001')
        self.assertEqual(cable['product_codes'], ['7750001'])

    def test_one_open_migration_count_per_branch(self):
        first = self.open()
        res = self.api.post(COUNTS, {'branch': self.branch.pk, 'kind': 'migration'}, format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.open(branch=self.other_branch)  # another branch is not blocked
        self.open('regular')  # an ordinary count is not blocked
        self.api.post(f'{COUNTS}{first["id"]}/cancel/')
        self.open()

    def test_a_migration_count_takes_its_reference_from_an_applied_import(self):
        job = BulkImportJob.objects.create(
            company=self.company, import_type=BulkImportJob.PRODUCTS, status=BulkImportJob.APPLIED,
        )
        BulkImportRow.objects.create(job=job, row_number=2, action='update', normalized_data={
            'product_id': self.cable.pk,
            'reference_quantities': [
                {'branch_id': self.branch.pk, 'quantity': 7},
                {'branch_id': self.other_branch.pk, 'quantity': 99},
            ],
        })
        BulkImportRow.objects.create(job=job, row_number=3, action='create', normalized_data={
            'product_id': self.empty.pk,
            'reference_quantities': [{'branch_id': self.branch.pk, 'quantity': 3}],
        })
        res = self.api.post(COUNTS, {
            'branch': self.branch.pk, 'kind': 'migration', 'source_job': job.pk,
        }, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        data = res.json()
        self.assertEqual(data['source_job'], job.pk)
        self.assertEqual(self.line(data, self.cable)['reference_quantity'], 7)
        self.assertEqual(self.line(data, self.empty)['reference_quantity'], 3, 'reference > 0 lists it')
        self.assertIsNone(self.line(data, self.case)['reference_quantity'])
        self.assertIsNone(self.line(data, self.cable)['physical_quantity'], 'never prefilled')

    def test_a_count_never_runs_against_an_unapplied_import(self):
        job = BulkImportJob.objects.create(
            company=self.company, import_type=BulkImportJob.PRODUCTS, status=BulkImportJob.PREVIEWED,
        )
        res = self.api.post(COUNTS, {
            'branch': self.branch.pk, 'kind': 'migration', 'source_job': job.pk,
        }, format='json')
        self.assertEqual(res.status_code, 400)

    def test_another_companys_import_is_not_found(self):
        other = _p2d_company('migc-other-job')
        job = BulkImportJob.objects.create(
            company=other, import_type=BulkImportJob.PRODUCTS, status=BulkImportJob.APPLIED,
        )
        res = self.api.post(COUNTS, {
            'branch': self.branch.pk, 'kind': 'migration', 'source_job': job.pk,
        }, format='json')
        self.assertEqual(res.status_code, 404)

    def test_the_historical_quantity_is_never_applied(self):
        job = BulkImportJob.objects.create(
            company=self.company, import_type=BulkImportJob.PRODUCTS, status=BulkImportJob.APPLIED,
        )
        BulkImportRow.objects.create(job=job, row_number=2, action='update', normalized_data={
            'product_id': self.cable.pk, 'reference_quantities': [{'branch_id': self.branch.pk, 'quantity': 7}],
        })
        data = self.api.post(COUNTS, {
            'branch': self.branch.pk, 'kind': 'migration', 'source_job': job.pk,
        }, format='json').json()
        self.put(data['id'], [{'product': self.cable.pk, 'physical_quantity': 10},
                              {'product': self.case.pk, 'physical_quantity': 4}])
        self.submit(data['id'])
        res = self.approve(data['id'])
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['movements'], [], 'counted = stock: nothing to correct')
        self.assertEqual(branch_quantity(self.branch, self.cable), 10, 'never the reference 7')

    # --- scanning ------------------------------------------------------------

    def test_a_lookup_finds_the_line_and_writes_no_quantity(self):
        data = self.open()
        res = self.scan(data['id'], code='7750001', mode='lookup')
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body['result'], 'found')
        self.assertEqual(body['line']['product'], self.cable.pk)
        self.assertIsNone(body['line']['physical_quantity'])
        self.assertIsNone(body['line']['theoretical_at_start'])

    def test_each_scan_adds_one_and_the_first_takes_the_snapshot(self):
        data = self.open()
        first = self.scan(data['id'], code='7750001', mode='increment', expected=None)
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json()['result'], 'incremented')
        self.assertEqual(first.json()['line']['physical_quantity'], 1)
        self.assertEqual(first.json()['line']['theoretical_at_start'], 10)
        second = self.scan(data['id'], code='7750001', mode='increment', expected=1)
        self.assertEqual(second.json()['line']['physical_quantity'], 2)
        item = InventoryCount.objects.get(pk=data['id']).items.get(product=self.cable)
        self.assertEqual(item.counted_by, self.counter)
        self.assertIsNotNone(item.snapshot_movement_id)
        self.assertEqual(BranchStock.objects.get(branch=self.branch, product=self.cable).quantity, 10,
                         'no stock moves before approval')

    def test_a_stale_expected_is_a_409_and_adds_nothing(self):
        data = self.open()
        self.scan(data['id'], code='7750001', mode='increment', expected=None)
        self.scan(data['id'], code='7750001', mode='increment', expected=1)
        res = self.scan(data['id'], code='7750001', mode='increment', expected=1)  # a retried request
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'expected_mismatch')
        self.assertEqual(res.json()['line']['physical_quantity'], 2)
        item = InventoryCount.objects.get(pk=data['id']).items.get(product=self.cable)
        self.assertEqual(item.physical_quantity, 2)

    def test_increment_by_product_without_a_code(self):
        data = self.open()
        res = self.scan(data['id'], product=self.case.pk, mode='increment', expected=None)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['line']['physical_quantity'], 1)

    def test_an_unknown_code_is_not_found(self):
        data = self.open()
        res = self.scan(data['id'], code='0000', mode='lookup')
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()['code'], 'not_found')

    def test_a_serialized_product_is_refused_and_named(self):
        data = self.open()
        res = self.scan(data['id'], code='7759999', mode='increment', expected=None)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'serialized_product')
        self.assertEqual(res.json()['product'], {'id': self.phone.pk, 'name': 'Teléfono'})
        res = self.put(data['id'], [{'product': self.phone.pk, 'physical_quantity': 1}])
        self.assertEqual(res.status_code, 400)

    def test_another_companys_code_is_not_found(self):
        other = _p2d_company('migc-other-code')
        foreign = _p2d_product(other, 'Ajeno', 'migc-ajeno')
        ProductBarcode.objects.create(company=other, product=foreign, code='8880001')
        data = self.open()
        res = self.scan(data['id'], code='8880001', mode='lookup')
        self.assertEqual(res.status_code, 404)
        res = self.scan(data['id'], product=foreign.pk, mode='increment', expected=None)
        self.assertEqual(res.status_code, 404)

    def test_the_count_search_matches_codes_and_names_of_this_company_only(self):
        other = _p2d_company('migc-other-search')
        _p2d_product(other, 'Cable ajeno', 'migc-cable-ajeno')
        data = self.open()
        by_code = self.api.get(f'{COUNTS}{data["id"]}/products/', {'search': '775000'}).json()['results']
        self.assertEqual({r['id'] for r in by_code}, {self.cable.pk, self.case.pk})
        by_name = self.api.get(f'{COUNTS}{data["id"]}/products/', {'search': 'cable'}).json()['results']
        self.assertEqual([r['id'] for r in by_name], [self.cable.pk])
        self.assertEqual(by_name[0]['product_code'], '7750001')
        self.assertFalse(by_name[0]['is_serialized'])
        phone = self.api.get(f'{COUNTS}{data["id"]}/products/', {'search': 'teléfono'}).json()['results']
        self.assertTrue(phone[0]['is_serialized'])

    # --- totals, blank and zero --------------------------------------------------

    def test_a_total_replaces_and_does_not_add(self):
        data = self.open()
        self.scan(data['id'], code='7750001', mode='increment', expected=None)
        self.scan(data['id'], code='7750001', mode='increment', expected=1)
        res = self.put(data['id'], [{'product': self.cable.pk, 'physical_quantity': 5}])
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(self.line(res.json(), self.cable)['physical_quantity'], 5)

    def test_blank_is_not_counted_and_zero_is_verified_none(self):
        data = self.open()
        res = self.put(data['id'], [{'product': self.case.pk, 'physical_quantity': 0}])
        self.assertEqual(self.line(res.json(), self.case)['review_state'], 'counted')
        self.assertEqual(self.line(res.json(), self.cable)['review_state'], 'pending')
        self.api.post(f'{COUNTS}{data["id"]}/exclude/', {
            'products': [self.cable.pk], 'reason': 'Contado aparte',
        }, format='json')
        self.submit(data['id'])
        res = self.approve(data['id'])
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(branch_quantity(self.branch, self.case), 0, 'zero is applied')
        self.assertEqual(branch_quantity(self.branch, self.cable), 10, 'blank and excluded are not')

    def test_blank_in_an_ordinary_count_is_skipped(self):
        data = self.open('regular')
        self.put(data['id'], [{'product': self.cable.pk, 'physical_quantity': None},
                              {'product': self.case.pk, 'physical_quantity': 3}])
        res = self.approve(data['id'])
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(branch_quantity(self.branch, self.cable), 10)
        self.assertEqual(branch_quantity(self.branch, self.case), 3)

    # --- the cut-off rule -----------------------------------------------------------

    def test_a_sale_before_the_first_entry_is_not_subtracted_twice(self):
        data = self.open()  # lines listed: no snapshot yet
        _sale(self.branch, self.cable, 2)  # 10 → 8
        res = self.put(data['id'], [{'product': self.cable.pk, 'physical_quantity': 8},
                                    {'product': self.case.pk, 'physical_quantity': 4}])
        self.assertEqual(self.line(res.json(), self.cable)['theoretical_at_start'], 8)
        self.submit(data['id'])
        self.assertEqual(self.approve(data['id']).json()['movements'], [])
        self.assertEqual(branch_quantity(self.branch, self.cable), 8)

    def test_a_sale_inside_the_window_requires_choosing_recount_or_correction(self):
        data = self.open()
        self.put(data['id'], [{'product': self.cable.pk, 'physical_quantity': 8}])  # snapshot 10
        _sale(self.branch, self.cable, 2)  # 8 on the system
        res = self.put(data['id'], [{'product': self.cable.pk, 'physical_quantity': 7}])
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'entry_required')
        self.assertEqual(res.json()['products'], [self.cable.pk])

        # «Recount»: a new observation, a new snapshot.
        res = self.put(data['id'], [{'product': self.cable.pk, 'physical_quantity': 7, 'entry': 'recount'}])
        self.assertEqual(res.status_code, 200, res.content)
        line = self.line(res.json(), self.cable)
        self.assertEqual(line['theoretical_at_start'], 8)
        self.assertEqual(line['review_state'], 'counted')
        self.put(data['id'], [{'product': self.case.pk, 'physical_quantity': 4}])
        self.submit(data['id'])
        self.assertEqual(self.approve(data['id']).status_code, 200)
        self.assertEqual(branch_quantity(self.branch, self.cable), 7)

    def test_a_typing_correction_keeps_the_observation_and_needs_a_reason(self):
        data = self.open()
        self.put(data['id'], [{'product': self.cable.pk, 'physical_quantity': 8}])  # snapshot 10
        _sale(self.branch, self.cable, 2)
        res = self.put(data['id'], [{'product': self.cable.pk, 'physical_quantity': 7, 'entry': 'correction'}])
        self.assertEqual(res.status_code, 400, 'a correction says why')
        res = self.put(data['id'], [{
            'product': self.cable.pk, 'physical_quantity': 7, 'entry': 'correction',
            'entry_note': 'Escribí 8, eran 7',
        }])
        self.assertEqual(res.status_code, 200, res.content)
        line = self.line(res.json(), self.cable)
        self.assertEqual(line['theoretical_at_start'], 10, 'same observation, same snapshot')
        self.assertEqual(line['review_state'], 'counted')
        self.put(data['id'], [{'product': self.case.pk, 'physical_quantity': 4}])
        self.submit(data['id'])
        self.assertEqual(self.approve(data['id']).status_code, 200)
        # 7 seen when the system said 10 → −3; the sale after it stands: 8 − 3 = 5.
        self.assertEqual(branch_quantity(self.branch, self.cable), 5)

    def test_a_sale_during_scanning_asks_for_a_recount_and_blocks_approval(self):
        data = self.open()
        self.scan(data['id'], code='7750001', mode='increment', expected=None)
        _sale(self.branch, self.cable, 1)
        res = self.scan(data['id'], code='7750001', mode='increment', expected=1)
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'recount_required')
        self.assertEqual(res.json()['line']['review_state'], 'ambiguous')
        self.assertEqual(res.json()['line']['physical_quantity'], 1, 'nothing added')
        self.put(data['id'], [{'product': self.case.pk, 'physical_quantity': 4}])
        self.submit(data['id'])
        res = self.approve(data['id'])
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'approval_blocked')
        self.assertEqual(
            [(p['product'], p['reason']) for p in res.json()['problems']],
            [(self.cable.pk, 'ambiguous')],
        )
        self.assertFalse(StockMovement.objects.filter(inventory_count_id=data['id']).exists())

    def test_a_recount_returns_the_line_to_not_counted_and_then_approves(self):
        data = self.open()
        self.put(data['id'], [{'product': self.cable.pk, 'physical_quantity': 8},
                              {'product': self.case.pk, 'physical_quantity': 4}])
        _sale(self.branch, self.cable, 1)  # 9 on the system
        self.scan(data['id'], code='7750001', mode='increment', expected=8)  # refused, ambiguous
        res = self.api.post(f'{COUNTS}{data["id"]}/recount/', {'products': [self.cable.pk]}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        line = self.line(res.json(), self.cable)
        self.assertIsNone(line['physical_quantity'])
        self.assertIsNone(line['theoretical_at_start'])
        self.assertEqual(line['review_state'], 'pending')
        self.put(data['id'], [{'product': self.cable.pk, 'physical_quantity': 9}])
        self.submit(data['id'])
        self.assertEqual(self.approve(data['id']).status_code, 200)
        self.assertEqual(branch_quantity(self.branch, self.cable), 9)

    def test_a_transfer_during_the_count_makes_the_line_ambiguous(self):
        data = self.open()
        self.put(data['id'], [{'product': self.cable.pk, 'physical_quantity': 10}])
        transfer = StockTransfer.objects.create(
            company=self.company, source_branch=self.branch, destination_branch=self.other_branch,
        )
        StockTransferItem.objects.create(transfer=transfer, product=self.cable, quantity=3)
        inventory_services.dispatch_transfer(transfer, actor=self.approver)
        res = self.put(data['id'], [{'product': self.cable.pk, 'physical_quantity': 7}])
        self.assertEqual(res.json()['code'], 'entry_required')

    def test_goods_in_transit_to_the_branch_are_shown_on_the_line(self):
        _p2d_stock(self.other_branch, self.cable, 5)
        transfer = StockTransfer.objects.create(
            company=self.company, source_branch=self.other_branch, destination_branch=self.branch,
        )
        StockTransferItem.objects.create(transfer=transfer, product=self.cable, quantity=2)
        inventory_services.dispatch_transfer(transfer, actor=self.approver)
        data = self.open()
        self.assertEqual(self.line(data, self.cable)['in_transit_quantity'], 2)

    def test_movements_after_the_last_entry_are_respected(self):
        data = self.open()
        self.put(data['id'], [{'product': self.cable.pk, 'physical_quantity': 8},
                              {'product': self.case.pk, 'physical_quantity': 4}])
        _sale(self.branch, self.cable, 3)  # after the last entry: not ambiguous
        self.submit(data['id'])
        res = self.approve(data['id'])
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(branch_quantity(self.branch, self.cable), 5, '10 − 3 sold + (8 − 10) found')

    def test_a_correction_from_elsewhere_after_the_snapshot_blocks_every_kind(self):
        for kind in ('migration', 'regular'):
            with self.subTest(kind=kind):
                data = self.open(kind, branch=self.other_branch if kind == 'regular' else None)
                branch = self.other_branch if kind == 'regular' else self.branch
                _p2d_stock(branch, self.empty, 2)
                self.put(data['id'], [{'product': self.empty.pk, 'physical_quantity': 1}])
                create_stock_movement(
                    branch=branch, product_id=self.empty.pk,
                    movement_type=StockMovement.CORRECTION_NEGATIVE, quantity=1, reason='Ajuste aparte',
                )
                if kind == 'migration':
                    self.api.post(f'{COUNTS}{data["id"]}/exclude/', {
                        'products': [self.cable.pk, self.case.pk], 'reason': 'Fuera de esta prueba',
                    }, format='json')
                    self.submit(data['id'])
                res = self.approve(data['id'])
                self.assertEqual(res.status_code, 400, res.content)
                self.assertEqual(res.json()['problems'][0]['reason'], 'overlapping')

    # --- review and approval ------------------------------------------------------

    def test_a_migration_count_is_approved_only_from_review(self):
        data = self.open()
        self.put(data['id'], [{'product': self.cable.pk, 'physical_quantity': 9},
                              {'product': self.case.pk, 'physical_quantity': 4}])
        self.assertEqual(self.approve(data['id']).status_code, 400)
        review = self.submit(data['id'])
        self.assertEqual(review['status'], 'review')
        self.assertIsNotNone(review['submitted_at'])
        frozen = self.put(data['id'], [{'product': self.cable.pk, 'physical_quantity': 1}])
        self.assertEqual(frozen.status_code, 400, 'counters stop writing in review')
        first = self.approve(data['id'])
        self.assertEqual(first.status_code, 200, first.content)
        again = self.approve(data['id'])
        self.assertEqual(again.status_code, 200)
        self.assertEqual([m['id'] for m in first.json()['movements']],
                         [m['id'] for m in again.json()['movements']])
        self.assertEqual(StockMovement.objects.filter(inventory_count_id=data['id']).count(), 1)
        self.assertEqual(branch_quantity(self.branch, self.cable), 9)

    def test_an_unexplained_pending_line_blocks_a_migration_count(self):
        data = self.open()
        self.put(data['id'], [{'product': self.cable.pk, 'physical_quantity': 10}])
        self.submit(data['id'])
        res = self.approve(data['id'])
        self.assertEqual(res.status_code, 400)
        self.assertEqual([(p['product'], p['reason']) for p in res.json()['problems']],
                         [(self.case.pk, 'pending')])
        # In review only the approver may exclude, and a reason is required.
        self.assertEqual(self.api.post(f'{COUNTS}{data["id"]}/exclude/', {
            'products': [self.case.pk], 'reason': 'Ya no se vende',
        }, format='json').status_code, 403)
        self.assertEqual(self.boss.post(f'{COUNTS}{data["id"]}/exclude/', {
            'products': [self.case.pk], 'reason': '',
        }, format='json').status_code, 400)
        res = self.boss.post(f'{COUNTS}{data["id"]}/exclude/', {
            'products': [self.case.pk], 'reason': 'Ya no se vende',
        }, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(self.line(res.json(), self.case)['review_state'], 'excluded')
        self.assertEqual(self.approve(data['id']).status_code, 200)
        self.assertEqual(branch_quantity(self.branch, self.case), 4)

    def test_approval_needs_its_own_capability(self):
        data = self.open('regular')
        self.put(data['id'], [{'product': self.cable.pk, 'physical_quantity': 9}])
        self.assertEqual(self.approve(data['id'], client=self.api).status_code, 403)
        self.assertEqual(self.approve(data['id']).status_code, 200)

    def test_submit_is_not_approval(self):
        data = self.open('regular')
        self.put(data['id'], [{'product': self.cable.pk, 'physical_quantity': 9}])
        self.submit(data['id'])
        self.assertEqual(branch_quantity(self.branch, self.cable), 10)
        self.assertFalse(StockMovement.objects.filter(inventory_count_id=data['id']).exists())

    def test_a_failed_approval_rolls_everything_back(self):
        data = self.open()
        self.put(data['id'], [{'product': self.cable.pk, 'physical_quantity': 9},
                              {'product': self.case.pk, 'physical_quantity': 1}])
        self.submit(data['id'])
        real = inventory_services.create_stock_movement
        calls = []

        def failing(**kwargs):
            calls.append(kwargs['product_id'])
            if len(calls) == 2:
                raise inventory_services.InventoryError('Fallo simulado')
            return real(**kwargs)

        with mock.patch.object(inventory_services, 'create_stock_movement', side_effect=failing):
            res = self.approve(data['id'])
        self.assertEqual(res.status_code, 400)
        self.assertEqual(len(calls), 2)
        self.assertFalse(StockMovement.objects.filter(inventory_count_id=data['id']).exists())
        self.assertEqual(branch_quantity(self.branch, self.cable), 10)
        self.assertEqual(branch_quantity(self.branch, self.case), 4)
        count = InventoryCount.objects.get(pk=data['id'])
        self.assertEqual(count.status, InventoryCount.STATUS_REVIEW)
        self.assertTrue(all(i.difference is None for i in count.items.all()))

    def test_the_approval_movement_carries_its_evidence(self):
        data = self.open()
        self.put(data['id'], [{'product': self.cable.pk, 'physical_quantity': 9},
                              {'product': self.case.pk, 'physical_quantity': 4}])
        _sale(self.branch, self.cable, 2)
        self.submit(data['id'])
        self.approve(data['id'])
        movement = StockMovement.objects.get(inventory_count_id=data['id'])
        self.assertEqual(movement.movement_type, StockMovement.CORRECTION_NEGATIVE)
        for key in ('theoretical_at_start', 'snapshot_movement_id', 'last_entry_movement_id',
                    'theoretical_at_approval', 'physical_quantity', 'difference',
                    'after_entry_movement_ids'):
            self.assertIn(key, movement.metadata)
        self.assertEqual(len(movement.metadata['after_entry_movement_ids']), 1)

    # --- costs and isolation ------------------------------------------------------

    def test_the_value_of_a_difference_is_only_for_costs_view(self):
        BranchStock.objects.filter(branch=self.branch, product=self.cable).update(average_cost=Decimal('2.5000'))
        data = self.open()
        self.put(data['id'], [{'product': self.cable.pk, 'physical_quantity': 8}])
        plain = self.api.get(f'{COUNTS}{data["id"]}/').json()
        self.assertNotIn('difference_value', self.line(plain, self.cable))
        viewer, _ = _p2d_member(self.company, 'migc-costs', _COUNTERS + ['costs.view'])
        seen = _client(viewer).get(f'{COUNTS}{data["id"]}/').json()
        line = self.line(seen, self.cable)
        self.assertEqual(line['expected_difference'], -2)
        self.assertEqual(line['difference_value'], '-5.00')

    def test_another_branch_is_not_found(self):
        data = self.open()
        limited, _ = _p2d_member(self.company, 'migc-norte', _APPROVERS, branches=[self.other_branch])
        client = _client(limited)
        self.assertEqual(client.get(f'{COUNTS}{data["id"]}/').status_code, 404)
        self.assertEqual(self.scan(data['id'], client=client, code='7750001', mode='lookup').status_code, 404)
        self.assertEqual(client.post(f'{COUNTS}{data["id"]}/submit/').status_code, 404)
        self.assertEqual(client.post(f'{COUNTS}{data["id"]}/recount/', {'products': [1]}, format='json').status_code, 404)

    def test_another_company_is_not_found(self):
        data = self.open()
        other = _p2d_company('migc-other-tenant')
        stranger, _ = _p2d_member(other, 'migc-stranger', _APPROVERS)
        client = _client(stranger)
        self.assertEqual(client.get(f'{COUNTS}{data["id"]}/').status_code, 404)
        self.assertEqual(self.scan(data['id'], client=client, code='7750001', mode='lookup').status_code, 404)
        self.assertEqual(self.approve(data['id'], client=client).status_code, 404)

    # --- ordinary counts keep working ------------------------------------------------

    def test_an_ordinary_partial_count_with_existing_stock(self):
        data = self.open('regular')
        self.assertEqual(data['items'], [], 'an ordinary count lists nothing up front')
        self.put(data['id'], [{'product': self.cable.pk, 'physical_quantity': 12}])
        _sale(self.branch, self.case, 1)
        res = self.approve(data['id'])
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(branch_quantity(self.branch, self.cable), 12)
        self.assertEqual(branch_quantity(self.branch, self.case), 3, 'the uncounted product is untouched')

    def test_an_ordinary_correction_after_a_sale_keeps_working_and_is_flagged(self):
        data = self.open('regular')
        self.put(data['id'], [{'product': self.cable.pk, 'physical_quantity': 8}])
        _sale(self.branch, self.cable, 2)
        res = self.put(data['id'], [{'product': self.cable.pk, 'physical_quantity': 7}])
        self.assertEqual(res.status_code, 200, 'an ordinary count keeps today\'s behaviour')
        line = self.line(res.json(), self.cable)
        self.assertEqual(line['theoretical_at_start'], 10)
        self.assertEqual(line['review_state'], 'ambiguous', 'but the screen is told')

    def test_a_line_annotation_is_refused_on_a_count_approved_meanwhile(self):
        data = self.open('regular')
        self.put(data['id'], [{'product': self.cable.pk, 'physical_quantity': 9}])
        stale = InventoryCount.objects.get(pk=data['id'])  # read before approval
        self.approve(data['id'])
        with self.assertRaises(InventoryCountError):
            set_count_item(stale, product=self.case, physical_quantity=1)
        self.assertFalse(stale.items.filter(product=self.case).exists())


class ReviewFindingsTest(_CountFixture, TestCase):
    """The NO-GO review of 5e07263: each probe, reproduced, then the P2/P3 rules."""

    def setUp(self):
        self.build('migc-review')
        self.api = _client(self.counter)
        self.boss = _client(self.approver)
        self.fresh = _p2d_product(self.company, 'Cargador nuevo', 'migc-review-new')  # no stock, no Kardex

    def open(self, kind='regular'):
        res = self.api.post(COUNTS, {'branch': self.branch.pk, 'kind': kind}, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        return res.json()['id']

    def put(self, count_id, lines, client=None):
        return (client or self.api).put(f'{COUNTS}{count_id}/items/', lines, format='json')

    def scan(self, count_id, **body):
        return self.api.post(f'{COUNTS}{count_id}/scan/', body, format='json')

    def approve(self, count_id):
        return self.boss.post(f'{COUNTS}{count_id}/approve/')

    def blocked(self, count_id, product, reason):
        res = self.approve(count_id)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'approval_blocked')
        self.assertIn((product.pk, reason), [(p['product'], p['reason']) for p in res.json()['problems']])
        self.assertFalse(StockMovement.objects.filter(inventory_count_id=count_id).exists())

    # --- P1: an INITIAL load after counting -------------------------------------

    def test_P1_an_initial_load_after_counting_blocks_approval(self):
        count_id = self.open()
        self.put(count_id, [{'product': self.fresh.pk, 'physical_quantity': 5}])
        # What the INITIAL stock load writes (stock_import_services.apply_stock):
        # it passes its own check because counting wrote no Kardex.
        create_stock_movement(
            branch=self.branch, product_id=self.fresh.pk, movement_type=StockMovement.INITIAL_STOCK,
            quantity=5, reason='Carga masiva de inventario', reference_type='bulk_import', reference_id='1',
        )
        self.blocked(count_id, self.fresh, 'overlapping')
        self.assertEqual(branch_quantity(self.branch, self.fresh), 5, 'never 10')

    def test_an_initial_load_is_refused_while_a_migration_count_lists_the_product(self):
        from store import stock_import_services

        self.api.post(COUNTS, {'branch': self.branch.pk, 'kind': 'migration'}, format='json')
        count = InventoryCount.objects.get(branch=self.branch, kind='migration')
        self.put(count.pk, [{'product': self.fresh.pk, 'physical_quantity': 5}])
        row = mock.Mock(row_number=2, normalized_data={
            'branch_id': self.branch.pk, 'product_id': self.fresh.pk, 'name': 'Cargador nuevo',
        })
        with self.assertRaises(stock_import_services.StockImportError) as caught:
            stock_import_services._assert_initial_still_valid([row], {(self.branch.pk, self.fresh.pk): 0})
        self.assertIn('recuento', str(caught.exception))

    def test_a_reconcile_correction_from_an_import_blocks_approval(self):
        count_id = self.open()
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 8}])
        create_stock_movement(
            branch=self.branch, product_id=self.cable.pk, movement_type=StockMovement.CORRECTION_NEGATIVE,
            quantity=2, reason='Carga masiva de inventario', reference_type='bulk_import', reference_id='9',
        )
        self.blocked(count_id, self.cable, 'overlapping')

    def test_a_manual_adjustment_after_counting_blocks_approval(self):
        for movement_type in (StockMovement.MANUAL_ENTRY, StockMovement.MANUAL_EXIT):
            with self.subTest(movement_type=movement_type):
                count_id = self.open()
                self.put(count_id, [{'product': self.case.pk, 'physical_quantity': 4}])
                inventory_services.apply_manual_stock_movement(
                    branch=self.branch, product_id=self.case.pk, movement_type=movement_type,
                    quantity=1, reason='Ajuste manual', actor=self.approver,
                )
                self.blocked(count_id, self.case, 'overlapping')
                self.api.post(f'{COUNTS}{count_id}/cancel/')

    # --- P1: ambiguous lines on an ordinary count ---------------------------------------

    def test_P1_a_scan_refused_for_recount_is_not_approvable_on_an_ordinary_count(self):
        count_id = self.open()
        self.scan(count_id, code='7750001', mode='increment', expected=None)  # 1 of 10
        _sale(self.branch, self.cable, 1)
        res = self.scan(count_id, code='7750001', mode='increment', expected=1)
        self.assertEqual(res.json()['code'], 'recount_required')
        self.blocked(count_id, self.cable, 'ambiguous')
        self.assertEqual(branch_quantity(self.branch, self.cable), 9, 'never 0')

    def test_an_ambiguous_ordinary_total_is_not_approvable_until_recounted(self):
        count_id = self.open()
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 8}])
        _sale(self.branch, self.cable, 2)
        self.assertEqual(self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 7}]).status_code, 200)
        self.blocked(count_id, self.cable, 'ambiguous')
        self.api.post(f'{COUNTS}{count_id}/recount/', {'products': [self.cable.pk]}, format='json')
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 7}])
        self.assertEqual(self.approve(count_id).status_code, 200)
        self.assertEqual(branch_quantity(self.branch, self.cable), 7)

    def test_a_reception_inside_the_window_is_ambiguous(self):
        count_id = self.open()
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 10}])
        create_stock_movement(
            branch=self.branch, product_id=self.cable.pk, movement_type=StockMovement.PURCHASE_ENTRY,
            quantity=3, reason='Recepción de proveedor',
        )
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 13}])
        self.blocked(count_id, self.cable, 'ambiguous')

    # --- P2: completeness of a migration count ------------------------------------------

    def test_stock_that_appears_during_a_migration_count_cannot_escape(self):
        count_id = self.open('migration')
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 10},
                            {'product': self.case.pk, 'physical_quantity': 4}])
        _p2d_stock(self.branch, self.fresh, 3)  # gained stock after the lines were listed
        res = self.api.post(f'{COUNTS}{count_id}/submit/')
        self.assertEqual(res.status_code, 400, res.content)
        line = InventoryCount.objects.get(pk=count_id).items.get(product=self.fresh)
        self.assertIsNone(line.physical_quantity, 'listed as pending, never prefilled')
        self.put(count_id, [{'product': self.fresh.pk, 'physical_quantity': 3}])
        self.assertEqual(self.api.post(f'{COUNTS}{count_id}/submit/').status_code, 200)
        # …and after submitting too.
        late = _p2d_product(self.company, 'Llegó tarde', 'migc-review-late')
        _p2d_stock(self.branch, late, 2)
        self.blocked(count_id, late, 'pending')
        self.assertTrue(InventoryCount.objects.get(pk=count_id).items.filter(product=late).exists())

    def test_an_all_excluded_migration_count_can_be_submitted(self):
        count_id = self.open('migration')
        res = self.api.post(f'{COUNTS}{count_id}/exclude/', {
            'products': [self.cable.pk, self.case.pk], 'reason': 'Se cuentan en otra fase',
        }, format='json')
        self.assertEqual(res.json()['status'], 'counting')
        self.assertEqual(self.api.post(f'{COUNTS}{count_id}/submit/').status_code, 200)
        self.assertEqual(self.approve(count_id).status_code, 200)
        self.assertEqual(branch_quantity(self.branch, self.cable), 10)

    # --- P2: the trail of every correction and recount ---------------------------------------

    def test_a_correction_and_a_recount_by_PUT_are_audited_and_the_note_is_kept(self):
        count_id = self.open('migration')
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 8}])
        _sale(self.branch, self.cable, 1)
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 7, 'entry': 'correction',
                             'entry_note': 'Escribí 8, eran 7'}])
        corrected = AdminAuditLog.objects.get(action='inventory_count_line_corrected')
        self.assertEqual(corrected.company_id, self.company.pk)
        self.assertEqual(corrected.metadata['entry_note'], 'Escribí 8, eran 7')
        self.assertEqual((corrected.metadata['previous'], corrected.metadata['physical_quantity']), (8, 7))

        _sale(self.branch, self.cable, 1)
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 6, 'entry': 'recount'}])
        recounted = AdminAuditLog.objects.get(action='inventory_count_line_recounted')
        self.assertEqual(recounted.company_id, self.company.pk)
        item = InventoryCount.objects.get(pk=count_id).items.get(product=self.cable)
        self.assertEqual(item.entry_note, 'Escribí 8, eran 7', 'a new snapshot does not erase the reason')

    def test_the_import_reference_type_is_the_one_the_stock_import_writes(self):
        from store import stock_import_services

        self.assertIn(stock_import_services.REFERENCE_TYPE, inventory_services.OVERLAPPING_REFERENCE_TYPES)

    def test_the_review_refusal_is_a_domain_error(self):
        self.assertTrue(issubclass(count_services.CountPermissionDenied, InventoryCountError))
        self.assertFalse(issubclass(count_services.CountPermissionDenied, PermissionError))


class CountsApproveCapabilityTest(TestCase):
    def test_it_is_in_the_catalogue_and_in_the_inventory_preset(self):
        self.assertTrue(is_assignable_capability('inventory.counts.approve'))
        company = _p2d_company('migc-presets')
        for slug in ('inventario', 'administrador'):
            role = CompanyRole.objects.get(company=company, slug=slug)
            self.assertIn('inventory.counts.approve', role.capabilities, slug)

    def test_the_data_migration_gives_it_to_whoever_could_approve(self):
        company = _p2d_company('migc-grant')
        role = CompanyRole.objects.create(
            company=company, name='Editado', slug='editado',
            capabilities=['inventory.view', 'inventory.adjust'],
        )
        viewer = CompanyRole.objects.create(
            company=company, name='Sólo ver', slug='solo-ver', capabilities=['inventory.view'],
        )
        module = _counts_approve_migration()
        module.grant(django_apps, None)
        role.refresh_from_db()
        viewer.refresh_from_db()
        self.assertIn('inventory.counts.approve', role.capabilities)
        self.assertNotIn('inventory.counts.approve', viewer.capabilities)


class CountApprovalRaceTest(TransactionTestCase):
    """Two approvals at once, and an annotation racing an approval (PostgreSQL)."""

    def together(self, jobs):
        barrier = threading.Barrier(len(jobs))
        results = [None] * len(jobs)

        def run(position, job):
            try:
                barrier.wait(timeout=10)
                results[position] = job()
            except Exception as exc:  # noqa: BLE001
                results[position] = exc
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=(p, j)) for p, j in enumerate(jobs)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        return results

    def setUp(self):
        if connection.vendor == 'sqlite':
            self.skipTest('Row locks need PostgreSQL.')
        fixture = _CountFixture()
        fixture.build('migc-race')
        self.f = fixture

    def test_two_approvals_at_once_apply_once(self):
        f = self.f
        count = create_inventory_count(company=f.company, branch=f.branch, actor=f.approver)
        set_count_item(count, product=f.cable, physical_quantity=7, actor=f.approver)
        real = inventory_services.create_stock_movement

        def slow(**kwargs):
            time.sleep(0.2)
            return real(**kwargs)

        with mock.patch.object(inventory_services, 'create_stock_movement', side_effect=slow):
            results = self.together([
                lambda: approve_inventory_count(InventoryCount.objects.get(pk=count.pk), actor=f.approver),
                lambda: approve_inventory_count(InventoryCount.objects.get(pk=count.pk), actor=f.approver),
            ])
        for result in results:
            self.assertNotIsInstance(result, Exception, repr(result))
        self.assertEqual([m.pk for m in results[0]], [m.pk for m in results[1]])
        self.assertEqual(StockMovement.objects.filter(inventory_count=count).count(), 1)
        self.assertEqual(branch_quantity(f.branch, f.cable), 7)

    def test_an_annotation_racing_the_approval_is_never_left_unapplied(self):
        f = self.f
        count = create_inventory_count(company=f.company, branch=f.branch, actor=f.approver)
        set_count_item(count, product=f.cable, physical_quantity=9, actor=f.approver)
        real = inventory_services.create_stock_movement

        def slow(**kwargs):
            time.sleep(0.3)
            return real(**kwargs)

        with mock.patch.object(inventory_services, 'create_stock_movement', side_effect=slow):
            results = self.together([
                lambda: approve_inventory_count(InventoryCount.objects.get(pk=count.pk), actor=f.approver),
                lambda: set_count_item(
                    InventoryCount.objects.get(pk=count.pk), product=f.case, physical_quantity=1,
                    actor=f.counter,
                ),
            ])
        self.assertNotIsInstance(results[0], Exception, repr(results[0]))
        count.refresh_from_db()
        self.assertEqual(count.status, InventoryCount.STATUS_APPROVED)
        line = count.items.filter(product=f.case).first()
        if isinstance(results[1], Exception):
            self.assertIsInstance(results[1], InventoryCountError)
            self.assertIsNone(line, 'a refused annotation leaves nothing behind')
            self.assertEqual(branch_quantity(f.branch, f.case), 4)
        else:
            self.assertIsNotNone(line.difference, 'an accepted annotation was applied')
            self.assertEqual(branch_quantity(f.branch, f.case), 1)
        self.assertEqual(branch_quantity(f.branch, f.cable), 9)

    def test_the_snapshot_waits_for_a_sale_that_is_committing(self):
        f = self.f
        count = create_inventory_count(company=f.company, branch=f.branch, actor=f.approver)
        locked = threading.Event()
        sale_ids = []

        def sale():
            from django.db import transaction

            with transaction.atomic():
                sale_ids.append(_sale(f.branch, f.cable, 2).pk)
                locked.set()
                time.sleep(0.5)  # the stock row stays locked until this commits

        def entry():
            locked.wait(timeout=10)
            return set_count_item(InventoryCount.objects.get(pk=count.pk), product=f.cable,
                                  physical_quantity=8, actor=f.counter)

        results = self.together([sale, entry])
        self.assertNotIsInstance(results[1], Exception, repr(results[1]))
        item = count.items.get(product=f.cable)
        self.assertEqual(item.theoretical_at_start, 8, 'the snapshot saw the committed sale')
        self.assertGreaterEqual(item.snapshot_movement_id, sale_ids[0])
        approve_inventory_count(count, actor=f.approver)
        self.assertEqual(branch_quantity(f.branch, f.cable), 8, 'no correction: 8 seen, 8 on the system')

    def test_two_increments_with_the_same_expected_add_once(self):
        f = self.f
        count = create_inventory_count(company=f.company, branch=f.branch, actor=f.approver)

        def increment():
            return count_services.scan(InventoryCount.objects.get(pk=count.pk), mode='increment',
                                       code='7750001', expected=None, actor=f.counter)

        results = self.together([increment, increment])
        refused = [r for r in results if isinstance(r, count_services.CountExpectedMismatch)]
        added = [r for r in results if isinstance(r, tuple)]
        self.assertEqual((len(added), len(refused)), (1, 1), results)
        self.assertEqual(count.items.get(product=f.cable).physical_quantity, 1)


class CountServicesSurfaceTest(TestCase):
    """The new module exists and owns the new verbs; nothing else writes stock."""

    def test_the_verbs(self):
        for name in ('create_migration_count', 'scan', 'recount', 'exclude', 'submit_for_review',
                     'search_products', 'line_reviews'):
            self.assertTrue(callable(getattr(count_services, name, None)), name)
