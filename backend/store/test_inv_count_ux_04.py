"""
INV-COUNT-UX-04 — a regular count can open with its PENDING lines: what the branch holds, still to count.

Without it a regular count starts empty: «Pendientes» says 0 and nothing tells the person what is left. With
`include_stocked`, the count lists every non-serialized product with stock in THAT branch, each line blank
(«not counted»), without a snapshot (the first quantity takes it, as in a migration count). What these hold:

  * only this branch's stock, only accessories (a device is verified by unit, never listed here), nothing of
    another company;
  * listing moves NO stock and writes NO movement;
  * a listed line left blank is skipped at approval — it is never written off to zero; an explicit 0 is;
  * the first quantity on a listed line takes its snapshot then, so a sale before counting is not «lost»;
  * the default stays as it was: an empty regular count.
"""
from django.test import TestCase

from store.models import AdminAuditLog, InventoryCount, StockMovement
from store.test_count_migration import COUNTS, _client, _CountFixture, _sale
from store.tests import _p2d_company, _p2d_member, _p2d_product, _p2d_stock, _p2d_branch


class ListStockedLinesTest(_CountFixture, TestCase):
    def setUp(self):
        self.build('cux04')
        self.api = _client(self.counter)
        self.boss = _client(self.approver)
        _p2d_stock(self.other_branch, self.empty, 3)  # stock in ANOTHER branch: not listed in Centro

    def open(self, **extra):
        res = self.api.post(COUNTS, {'branch': self.branch.pk, 'kind': 'regular', **extra}, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        return res.json()

    def test_the_default_is_still_an_empty_regular_count(self):
        data = self.open()
        self.assertEqual(data['items'], [])

    def test_lists_this_branchs_accessories_blank_and_moves_nothing(self):
        movements = StockMovement.objects.count()
        data = self.open(include_stocked=True)
        listed = {item['product']: item for item in data['items']}
        self.assertEqual(set(listed), {self.cable.pk, self.case.pk})
        for item in listed.values():
            self.assertIsNone(item['physical_quantity'])
            self.assertIsNone(item['theoretical_at_start'])
            self.assertFalse(item['is_counted'])
        self.assertEqual(data['status'], 'draft')
        self.assertEqual(StockMovement.objects.count(), movements)
        log = AdminAuditLog.objects.filter(action='inventory_count_created', target_id=data['id']).get()
        self.assertEqual(log.metadata.get('listed_lines'), 2)

    def test_a_blank_listed_line_is_skipped_at_approval_and_an_explicit_zero_is_not(self):
        data = self.open(include_stocked=True)
        # A sale BEFORE the line is counted: the snapshot is taken by the first quantity, not by the listing.
        _sale(self.branch, self.case, 1)
        res = self.api.put(f'{COUNTS}{data["id"]}/items/', [{'product': self.case.pk, 'physical_quantity': 0}],
                           format='json')
        self.assertLess(res.status_code, 300, res.content)
        detail = self.api.get(f'{COUNTS}{data["id"]}/').json()
        case_line = next(i for i in detail['items'] if i['product'] == self.case.pk)
        self.assertEqual(case_line['theoretical_at_start'], 3)
        approved = self.boss.post(f'{COUNTS}{data["id"]}/approve/', {}, format='json')
        self.assertLess(approved.status_code, 300, approved.content)
        from store.inventory_services import branch_quantity
        self.assertEqual(branch_quantity(self.branch, self.case.pk), 0)   # counted 0: written off
        self.assertEqual(branch_quantity(self.branch, self.cable.pk), 10)  # blank: untouched

    def test_another_companys_branch_is_not_found_and_lists_nothing(self):
        other = _p2d_company('cux04-otra')
        theirs = _p2d_branch(other, 'Ajena')
        _p2d_stock(theirs, _p2d_product(other, 'Cable ajeno', 'cux04-ajeno'), 5)
        res = self.api.post(COUNTS, {'branch': theirs.pk, 'kind': 'regular', 'include_stocked': True}, format='json')
        self.assertEqual(res.status_code, 404, res.content)
        self.assertFalse(InventoryCount.objects.filter(branch=theirs).exists())

    def test_a_viewer_cannot_open_one(self):
        viewer, _ = _p2d_member(self.company, 'cux04-viewer', ['company.view', 'inventory.view'])
        res = _client(viewer).post(COUNTS, {'branch': self.branch.pk, 'include_stocked': True}, format='json')
        self.assertEqual(res.status_code, 403, res.content)
