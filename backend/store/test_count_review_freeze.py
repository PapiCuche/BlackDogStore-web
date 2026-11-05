"""
COUNT-REVIEW-FREEZE (audit finding C-M1, owner decision B).

«Bloquear modificaciones cuando el recuento está enviado a revisión. La regla debe imponerse en backend, no
únicamente en frontend. Conservar trazabilidad de las transiciones. Cualquier retorno a edición debe ser una
acción explícita, autorizada y auditada.»

The exploit these close: the approver reads 10 vs 10, a counter changes the line to 2 while the count is «en
revisión», and the approval writes −8 under the approver's name.

What holds, for EVERY kind of count (regular and migration):

* in `review` nobody enters quantities — not by PUT, not by scan (lookup or increment), not by recount: 409
  `count_in_review`. A counter cannot exclude either. The approver keeps ONE line decision in review — exclude
  with a reason (audited, previous values kept) — because it is a review decision, not a counting entry;
* the way back is `POST …/reopen/ {reason}`: `inventory.counts.approve`, review → counting, reason required,
  audited with old/new status;
* the approval compares what it applies with what was submitted (the version stamped at submit) and with what
  the approver saw (`version` in the request): a difference is 409 (`count_changed` / `count_stale`), nothing
  is written;
* in `review` only the approver cancels (B2);
* every transition (submit, reopen, approve, cancel) is audited with its old and new status, and a counted line
  whose value is replaced while counting is audited with old and new values;
* a PUT carries at most 500 lines (B4).
"""

import threading
import time
from unittest import mock

from django.db import connection
from django.test import TestCase, TransactionTestCase

from store import count_services, inventory_services
from store.inventory_services import (
    InventoryCountError,
    approve_inventory_count,
    branch_quantity,
    create_inventory_count,
    set_count_item,
)
from store.models import AdminAuditLog, InventoryCount, InventoryCountItem, StockMovement
from store.test_count_migration import COUNTS, _client, _CountFixture
from store.tests import _p2d_member


class _ReviewHelpers(_CountFixture):
    def setUp(self):
        self.build('crf')
        self.api = _client(self.counter)
        self.boss = _client(self.approver)

    def open(self, kind='regular'):
        res = self.api.post(COUNTS, {'branch': self.branch.pk, 'kind': kind}, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        return res.json()['id']

    def put(self, count_id, lines, client=None):
        return (client or self.api).put(f'{COUNTS}{count_id}/items/', lines, format='json')

    def submit(self, count_id, client=None):
        res = (client or self.api).post(f'{COUNTS}{count_id}/submit/')
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def detail(self, count_id, client=None):
        return (client or self.boss).get(f'{COUNTS}{count_id}/').json()

    def approve(self, count_id, client=None, **body):
        return (client or self.boss).post(f'{COUNTS}{count_id}/approve/', body, format='json')

    def reopen(self, count_id, client=None, reason='Faltó la vitrina 2'):
        return (client or self.boss).post(f'{COUNTS}{count_id}/reopen/', {'reason': reason}, format='json')

    def physical(self, count_id, product):
        return InventoryCountItem.objects.get(count_id=count_id, product=product).physical_quantity

    def in_review(self, kind='regular'):
        count_id = self.open(kind)
        lines = [{'product': self.cable.pk, 'physical_quantity': 10}]
        if kind == 'migration':
            lines.append({'product': self.case.pk, 'physical_quantity': 4})
        self.assertEqual(self.put(count_id, lines).status_code, 200)
        self.submit(count_id)
        return count_id


class CountReviewFreezeTest(_ReviewHelpers, TestCase):
    """In review nobody enters quantities: 409 `count_in_review`, nothing written."""

    def assert_in_review_refusal(self, res):
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'count_in_review')
        self.assertTrue(res.json()['detail'])

    def test_the_exploit_is_closed_the_approval_applies_what_the_approver_saw(self):
        count_id = self.in_review()
        seen = self.detail(count_id)
        line = next(i for i in seen['items'] if i['product'] == self.cable.pk)
        self.assertEqual((line['physical_quantity'], line['theoretical_at_start']), (10, 10))

        self.assert_in_review_refusal(self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 2}]))
        self.assertEqual(self.physical(count_id, self.cable), 10)

        res = self.approve(count_id, version=seen['version'])
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(StockMovement.objects.filter(inventory_count_id=count_id).exists())
        self.assertEqual(branch_quantity(self.branch, self.cable), 10)

    def test_every_kind_refuses_a_PUT_in_review(self):
        for kind in ('regular', 'migration'):
            with self.subTest(kind=kind):
                count_id = self.in_review(kind)
                self.assert_in_review_refusal(
                    self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 2}]))
                self.assertEqual(self.physical(count_id, self.cable), 10)
                self.api.post(f'{COUNTS}{count_id}/cancel/')  # the counter cannot (B2) …
                self.boss.post(f'{COUNTS}{count_id}/cancel/')  # … the approver can

    def test_the_approver_cannot_enter_quantities_in_review_either(self):
        count_id = self.in_review()
        self.assert_in_review_refusal(
            self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 2}], client=self.boss))
        self.assertEqual(self.physical(count_id, self.cable), 10)

    def test_a_scan_in_review_is_refused_and_lists_nothing(self):
        count_id = self.in_review()
        self.assert_in_review_refusal(self.api.post(
            f'{COUNTS}{count_id}/scan/', {'code': '7750001', 'mode': 'increment', 'expected': 10}, format='json'))
        self.assertEqual(self.physical(count_id, self.cable), 10)
        self.assert_in_review_refusal(self.api.post(
            f'{COUNTS}{count_id}/scan/', {'code': '7750002', 'mode': 'lookup'}, format='json'))
        self.assertFalse(InventoryCountItem.objects.filter(count_id=count_id, product=self.case).exists())

    def test_a_recount_in_review_is_refused_for_everybody(self):
        count_id = self.in_review()
        for client in (self.api, self.boss):
            self.assert_in_review_refusal(client.post(
                f'{COUNTS}{count_id}/recount/', {'products': [self.cable.pk]}, format='json'))
        self.assertEqual(self.physical(count_id, self.cable), 10)
        self.assertEqual(InventoryCount.objects.get(pk=count_id).status, InventoryCount.STATUS_REVIEW)

    def test_a_counter_cannot_exclude_in_review(self):
        count_id = self.in_review()
        self.assert_in_review_refusal(self.api.post(
            f'{COUNTS}{count_id}/exclude/', {'products': [self.cable.pk], 'reason': 'No se vende'}, format='json'))
        self.assertFalse(InventoryCountItem.objects.get(count_id=count_id, product=self.cable).excluded)

    def test_the_approver_may_exclude_in_review_audited_with_the_previous_values(self):
        count_id = self.in_review()
        res = self.boss.post(f'{COUNTS}{count_id}/exclude/',
                             {'products': [self.cable.pk], 'reason': 'Se cuenta en otra fase'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['status'], 'review')
        log = AdminAuditLog.objects.get(action='inventory_count_lines_excluded', target_id=str(count_id))
        self.assertEqual(log.actor, self.approver)
        self.assertEqual(log.metadata['status'], 'review')
        self.assertEqual(log.metadata['lines'], [
            {'product': self.cable.pk, 'previous_physical_quantity': 10, 'previous_excluded': False},
        ])
        # The approver's own decision re-stamps what was submitted: approving what the screen shows works.
        res = self.approve(count_id, version=res.json()['version'])
        self.assertEqual(res.status_code, 200, res.content)

    def test_the_counting_panel_learns_the_count_is_in_review(self):
        count_id = self.open()
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 10}])
        before = self.detail(count_id)
        self.assertIsNone(before['review_version'])
        submitted = self.submit(count_id)
        self.assertEqual(submitted['status'], 'review')
        self.assertTrue(submitted['version'])
        self.assertEqual(submitted['review_version'], submitted['version'])
        self.assertNotEqual(submitted['version'], before['version'], 'a submission is a new version')


class CountReopenTest(_ReviewHelpers, TestCase):
    """The way back to editing: explicit, authorized, audited."""

    def test_only_an_approver_reopens(self):
        count_id = self.in_review()
        res = self.reopen(count_id, client=self.api)
        self.assertEqual(res.status_code, 403, res.content)
        self.assertEqual(InventoryCount.objects.get(pk=count_id).status, InventoryCount.STATUS_REVIEW)
        self.assertFalse(AdminAuditLog.objects.filter(action='inventory_count_reopened').exists())

    def test_an_approver_without_inventory_adjust_may_reopen(self):
        count_id = self.in_review()
        approver_only, _ = _p2d_member(self.company, 'crf-approver-only',
                                       ['company.view', 'inventory.view', 'inventory.counts.approve'])
        res = self.reopen(count_id, client=_client(approver_only))
        self.assertEqual(res.status_code, 200, res.content)

    def test_a_reason_is_required(self):
        count_id = self.in_review()
        for reason in ('', '   '):
            res = self.reopen(count_id, reason=reason)
            self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(InventoryCount.objects.get(pk=count_id).status, InventoryCount.STATUS_REVIEW)

    def test_only_a_count_in_review_is_reopened(self):
        count_id = self.open()
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 10}])
        self.assertEqual(self.reopen(count_id).status_code, 400)
        self.approve(count_id)
        self.assertEqual(self.reopen(count_id).status_code, 400)
        self.assertEqual(InventoryCount.objects.get(pk=count_id).status, InventoryCount.STATUS_APPROVED)

    def test_the_whole_round_trip_is_audited_and_nothing_is_silent(self):
        count_id = self.in_review()
        stale = self.detail(count_id)['version']

        res = self.reopen(count_id, reason='Faltó la vitrina 2')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['status'], 'counting')
        self.assertEqual(res.json()['last_reopen']['reason'], 'Faltó la vitrina 2')
        self.assertEqual(res.json()['last_reopen']['by'], self.approver.username)
        log = AdminAuditLog.objects.get(action='inventory_count_reopened', target_id=str(count_id))
        self.assertEqual(log.actor, self.approver)
        self.assertEqual(log.company_id, self.company.pk)
        self.assertEqual((log.metadata['from_status'], log.metadata['to_status']), ('review', 'counting'))
        self.assertEqual(log.metadata['reason'], 'Faltó la vitrina 2')

        # The counter edits — a replaced counted value is audited with old and new values — and resubmits.
        self.assertEqual(self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 9}]).status_code, 200)
        changed = AdminAuditLog.objects.get(action='inventory_count_line_changed', target_id=str(count_id))
        self.assertEqual((changed.metadata['previous'], changed.metadata['physical_quantity']), (10, 9))
        self.assertEqual(changed.actor, self.counter)
        self.submit(count_id)

        # The approver's old screen is refused: it showed 10, the count now says 9.
        res = self.approve(count_id, version=stale)
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'count_stale')
        self.assertFalse(StockMovement.objects.filter(inventory_count_id=count_id).exists())

        fresh = self.detail(count_id)['version']
        res = self.approve(count_id, version=fresh)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(branch_quantity(self.branch, self.cable), 9)

        actions = list(AdminAuditLog.objects.filter(target_id=str(count_id), target_type='inventory_count')
                       .order_by('id').values_list('action', 'metadata'))
        transitions = [(a, m.get('from_status'), m.get('to_status')) for a, m in actions
                       if a in ('inventory_count_submitted', 'inventory_count_reopened', 'inventory_count_approved')]
        self.assertEqual(transitions, [
            ('inventory_count_submitted', 'counting', 'review'),
            ('inventory_count_reopened', 'review', 'counting'),
            ('inventory_count_submitted', 'counting', 'review'),
            ('inventory_count_approved', 'review', 'approved'),
        ])

    def test_a_reopen_changes_the_version_even_with_the_same_lines(self):
        count_id = self.in_review()
        before = self.detail(count_id)['version']
        self.reopen(count_id)
        self.submit(count_id)
        res = self.approve(count_id, version=before)
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'count_stale')

    def test_a_migration_count_also_goes_back(self):
        count_id = self.in_review('migration')
        self.assertEqual(self.reopen(count_id).status_code, 200)
        res = self.put(count_id, [{'product': self.case.pk, 'physical_quantity': 3}])
        self.assertEqual(res.status_code, 200, res.content)


class CountApprovalVersionTest(_ReviewHelpers, TestCase):
    """The approval writes what was submitted and what the approver saw — or nothing."""

    def test_a_line_changed_behind_the_review_blocks_the_approval(self):
        count_id = self.in_review()
        # Defence in depth: no endpoint writes a line in review any more; a write by any other path is still caught.
        InventoryCountItem.objects.filter(count_id=count_id, product=self.cable).update(physical_quantity=2)
        res = self.approve(count_id)
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'count_changed')
        self.assertFalse(StockMovement.objects.filter(inventory_count_id=count_id).exists())
        self.assertEqual(branch_quantity(self.branch, self.cable), 10)
        self.assertEqual(InventoryCount.objects.get(pk=count_id).status, InventoryCount.STATUS_REVIEW)

    def test_a_stale_version_is_refused_while_counting_too(self):
        count_id = self.open()
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 10}])
        seen = self.detail(count_id)['version']
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 2}])
        res = self.approve(count_id, version=seen)
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'count_stale')
        self.assertEqual(branch_quantity(self.branch, self.cable), 10)

    def test_the_approval_audit_says_where_it_came_from(self):
        count_id = self.in_review()
        version = self.detail(count_id)['version']
        self.assertEqual(self.approve(count_id, version=version).status_code, 200)
        log = AdminAuditLog.objects.get(action='inventory_count_approved', target_id=str(count_id))
        self.assertEqual((log.metadata['from_status'], log.metadata['to_status']), ('review', 'approved'))
        self.assertEqual(log.metadata['version'], version)

    def test_an_approved_count_answers_its_movements_again_whatever_the_version(self):
        count_id = self.open()
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 7}])
        first = self.approve(count_id)
        self.assertEqual(first.status_code, 200)
        again = self.approve(count_id, version='old')
        self.assertEqual(again.status_code, 200, again.content)
        self.assertEqual(StockMovement.objects.filter(inventory_count_id=count_id).count(), 1)

    def test_the_version_is_a_string_or_absent(self):
        count_id = self.in_review()
        res = self.approve(count_id, version=['x'])
        self.assertEqual(res.status_code, 400, res.content)


class CountCancelInReviewTest(_ReviewHelpers, TestCase):
    """B2 — in review the approver acts: a counter cannot cancel it."""

    def test_a_counter_cannot_cancel_a_count_in_review(self):
        for kind in ('regular', 'migration'):
            with self.subTest(kind=kind):
                count_id = self.in_review(kind)
                res = self.api.post(f'{COUNTS}{count_id}/cancel/')
                self.assertEqual(res.status_code, 409, res.content)
                self.assertEqual(res.json()['code'], 'count_in_review')
                self.assertEqual(InventoryCount.objects.get(pk=count_id).status, InventoryCount.STATUS_REVIEW)
                res = self.boss.post(f'{COUNTS}{count_id}/cancel/')
                self.assertEqual(res.status_code, 200, res.content)
                log = AdminAuditLog.objects.get(action='inventory_count_cancelled', target_id=str(count_id))
                self.assertEqual((log.metadata['from_status'], log.metadata['to_status']), ('review', 'cancelled'))

    def test_a_counter_still_cancels_while_counting(self):
        count_id = self.open()
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 10}])
        self.assertEqual(self.api.post(f'{COUNTS}{count_id}/cancel/').status_code, 200)
        log = AdminAuditLog.objects.get(action='inventory_count_cancelled', target_id=str(count_id))
        self.assertEqual(log.metadata['from_status'], 'counting')


class CountItemsLimitTest(_ReviewHelpers, TestCase):
    """B4 — one PUT carries at most 500 lines, like recount and exclude."""

    def test_more_than_500_lines_are_refused(self):
        count_id = self.open()
        lines = [{'product': self.cable.pk, 'physical_quantity': 1}] * 501
        res = self.put(count_id, lines)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertFalse(InventoryCountItem.objects.filter(count_id=count_id).exists())

    def test_500_lines_are_read(self):
        count_id = self.open()
        lines = [{'product': self.cable.pk, 'physical_quantity': 3}] * 500
        self.assertEqual(self.put(count_id, lines).status_code, 200)


class CountLineChangeAuditTest(_ReviewHelpers, TestCase):
    """Where changes remain allowed (draft, counting), replacing a counted value leaves old and new values."""

    def test_a_first_entry_and_the_same_value_resent_are_not_changes(self):
        count_id = self.open()
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 8}])
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 8}])
        self.assertFalse(AdminAuditLog.objects.filter(action='inventory_count_line_changed').exists())

    def test_clearing_a_counted_line_is_a_change(self):
        count_id = self.open()
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 8}])
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': None}])
        log = AdminAuditLog.objects.get(action='inventory_count_line_changed')
        self.assertEqual((log.metadata['previous'], log.metadata['physical_quantity']), (8, None))


class CountReviewRaceTest(TransactionTestCase):
    """A line write racing the submission; an approval racing a reopen (PostgreSQL row locks)."""

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
        fixture.build('crf-race')
        self.f = fixture

    def counting(self, physical=10):
        f = self.f
        count = create_inventory_count(company=f.company, branch=f.branch, actor=f.counter)
        set_count_item(count, product=f.cable, physical_quantity=physical, actor=f.counter)
        return count

    def assert_submitted_is_what_is_there(self, count):
        count.refresh_from_db()
        self.assertEqual(count.status, InventoryCount.STATUS_REVIEW)
        self.assertEqual(count.metadata['review']['version'], inventory_services.count_version(count))

    def test_a_write_holding_the_lock_lands_before_the_submission(self):
        f = self.f
        count = self.counting()
        real = inventory_services._locked_branch_stocks
        started = threading.Event()

        def slow(*args, **kwargs):
            started.set()
            time.sleep(0.4)  # the write holds the count's row lock meanwhile
            return real(*args, **kwargs)

        def write():
            return set_count_item(InventoryCount.objects.get(pk=count.pk), product=f.case,
                                  physical_quantity=3, actor=f.counter)

        def submit():
            started.wait(timeout=10)
            return count_services.submit_for_review(InventoryCount.objects.get(pk=count.pk), actor=f.counter)

        with mock.patch.object(inventory_services, '_locked_branch_stocks', side_effect=slow):
            results = self.together([write, submit])
        for result in results:
            self.assertNotIsInstance(result, Exception, repr(result))
        self.assert_submitted_is_what_is_there(count)
        self.assertEqual(count.items.get(product=f.case).physical_quantity, 3, 'the write was submitted')

    def test_a_write_after_the_submission_took_the_lock_is_refused(self):
        f = self.f
        count = self.counting()
        real = inventory_services.count_version
        started = threading.Event()

        def slow(*args, **kwargs):
            started.set()
            time.sleep(0.4)  # the submission holds the count's row lock meanwhile
            return real(*args, **kwargs)

        def submit():
            return count_services.submit_for_review(InventoryCount.objects.get(pk=count.pk), actor=f.counter)

        def write():
            started.wait(timeout=10)
            return set_count_item(InventoryCount.objects.get(pk=count.pk), product=f.cable,
                                  physical_quantity=2, actor=f.counter)

        with mock.patch.object(inventory_services, 'count_version', side_effect=slow):
            results = self.together([submit, write])
        self.assertNotIsInstance(results[0], Exception, repr(results[0]))
        self.assertIsInstance(results[1], inventory_services.CountInReview, repr(results[1]))
        self.assert_submitted_is_what_is_there(count)
        self.assertEqual(count.items.get(product=f.cable).physical_quantity, 10, 'nothing slipped in')

    def test_an_approval_racing_a_reopen_never_applies_a_reopened_count(self):
        f = self.f
        count = self.counting(physical=7)  # 10 on the system: the approval writes −3
        count_services.submit_for_review(count, actor=f.counter)
        version = inventory_services.count_version(InventoryCount.objects.get(pk=count.pk))
        real = inventory_services.create_stock_movement

        def slow(**kwargs):
            time.sleep(0.3)
            return real(**kwargs)

        with mock.patch.object(inventory_services, 'create_stock_movement', side_effect=slow):
            results = self.together([
                lambda: approve_inventory_count(InventoryCount.objects.get(pk=count.pk), actor=f.approver,
                                                version=version),
                lambda: count_services.reopen_for_editing(InventoryCount.objects.get(pk=count.pk),
                                                          reason='Recontar vitrina', actor=f.approver),
            ])
        count.refresh_from_db()
        approved, reopened = results
        if isinstance(approved, Exception):
            self.assertIsInstance(approved, inventory_services.CountStale, repr(approved))
            self.assertNotIsInstance(reopened, Exception, repr(reopened))
            self.assertEqual(count.status, InventoryCount.STATUS_COUNTING)
            self.assertFalse(StockMovement.objects.filter(inventory_count=count).exists())
        else:
            self.assertIsInstance(reopened, InventoryCountError, repr(reopened))
            self.assertEqual(count.status, InventoryCount.STATUS_APPROVED)
            self.assertEqual(StockMovement.objects.filter(inventory_count=count).count(), 1)
            self.assertFalse(AdminAuditLog.objects.filter(action='inventory_count_reopened').exists())

    def test_a_reopen_racing_an_approval_that_holds_the_lock_is_refused(self):
        """The deterministic order: the approval holds the lock, the reopen waits and finds it approved."""
        f = self.f
        count = self.counting(physical=7)
        count_services.submit_for_review(InventoryCount.objects.get(pk=count.pk), actor=f.counter)
        version = inventory_services.count_version(InventoryCount.objects.get(pk=count.pk))
        real = inventory_services.create_stock_movement
        started = threading.Event()

        def slow(**kwargs):
            started.set()
            time.sleep(0.4)
            return real(**kwargs)

        def approve():
            return approve_inventory_count(InventoryCount.objects.get(pk=count.pk), actor=f.approver,
                                           version=version)

        def reopen():
            started.wait(timeout=10)
            return count_services.reopen_for_editing(InventoryCount.objects.get(pk=count.pk),
                                                     reason='Recontar', actor=f.approver)

        with mock.patch.object(inventory_services, 'create_stock_movement', side_effect=slow):
            results = self.together([approve, reopen])
        self.assertNotIsInstance(results[0], Exception, repr(results[0]))
        self.assertIsInstance(results[1], InventoryCountError, repr(results[1]))
        count.refresh_from_db()
        self.assertEqual(count.status, InventoryCount.STATUS_APPROVED)
        self.assertEqual(branch_quantity(f.branch, f.cable), 7)

