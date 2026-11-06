"""
DEC-INV-06 — separation between who PERFORMS a count and who APPROVES it (audit finding B1).

«Regla aprobada: separación entre quien realiza el recuento y quien lo aprueba.»

WHO PERFORMED THE COUNT (`inventory_services.count_performers`) — whoever, at any time in the count's life:

* entered, replaced or cleared a quantity (PUT, an increment scan) — even if somebody later overwrote it:
  `InventoryCountItem.counted_by` only keeps the LAST writer, so the count keeps its own list
  (`metadata['performers']`, written under the count's row lock by every counting write);
* sent lines back to «not counted» (recount) or excluded them WHILE COUNTING;
* sent the count to review (the submitter vouches it is complete).

NOT a performer: whoever only created the count (a draft holds no quantity — a manager opens, staff counts, the
manager approves: textbook four eyes), only looked a code up, or took the approver's own review decisions
(exclusion in review, return to editing, cancel). For a count that was open before this release, the trail the
count already had is read too (`submitted_by`, `counted_by`, the audited line changes).

THE RULE. A performer does not approve: 403 `count_self_approval`, nothing written. Decided under the count's row
lock, which every counting write takes first: a write either landed before (and is seen) or is refused after.

THE SMALL-TEAM ALTERNATIVE is opt-in per company and per role: the capability `inventory.counts.approve_own`
(«Aprobar recuentos propios»). No preset holds it, nor the legacy matrix, nor the `Administrador` preset
(`EXPLICIT_GRANT_CODES`); only someone who holds it may grant it. Even with it the approval of one's own count
asks a reason (400 `self_approval_reason_required`), is stored on the count and flagged in the audit.
"""

import threading
import time
from unittest import mock

from django.db import connection
from django.test import TestCase, TransactionTestCase

from store import count_services, inventory_services
from store.capabilities import (
    ASSIGNABLE_CAPABILITY_CODES,
    CAPABILITIES,
    DEFAULT_ADMIN_CAPABILITY_CODES,
    EXPLICIT_GRANT_CODES,
)
from store.company_provisioning import PRESET_ROLES
from store.inventory_services import (
    approve_inventory_count,
    branch_quantity,
    create_inventory_count,
    set_count_item,
)
from store.models import AdminAuditLog, InventoryCount, StockMovement
from store.tenancy import LEGACY_ROLE_CAPABILITIES
from store.test_count_migration import _APPROVERS, COUNTS, _client, _CountFixture
from store.tests import _p2d_member

OWN = 'inventory.counts.approve_own'


class _SegregationHelpers(_CountFixture):
    def setUp(self):
        self.build('seg')
        self.api = _client(self.counter)
        self.boss = _client(self.approver)
        self.owner, _ = _p2d_member(self.company, 'seg-owner', _APPROVERS + [OWN])
        self.own = _client(self.owner)

    def open(self, kind='regular', client=None):
        res = (client or self.api).post(COUNTS, {'branch': self.branch.pk, 'kind': kind}, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        return res.json()['id']

    def put(self, count_id, lines, client=None):
        res = (client or self.api).put(f'{COUNTS}{count_id}/items/', lines, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def submit(self, count_id, client=None):
        res = (client or self.api).post(f'{COUNTS}{count_id}/submit/')
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def detail(self, count_id, client=None):
        return (client or self.boss).get(f'{COUNTS}{count_id}/').json()

    def approve(self, count_id, client=None, **body):
        client = client or self.boss
        body.setdefault('version', self.detail(count_id, client=client)['version'])
        return client.post(f'{COUNTS}{count_id}/approve/', body, format='json')

    def assert_refused(self, res, count_id, status=InventoryCount.STATUS_REVIEW):
        self.assertEqual(res.status_code, 403, res.content)
        self.assertEqual(res.json()['code'], 'count_self_approval')
        self.assertTrue(res.json()['detail'])
        count = InventoryCount.objects.get(pk=count_id)
        self.assertEqual(count.status, status)
        self.assertIsNone(count.approved_by)
        self.assertFalse(StockMovement.objects.filter(inventory_count_id=count_id).exists())
        self.assertFalse(AdminAuditLog.objects.filter(
            target_type='inventory_count', target_id=str(count_id), action='inventory_count_approved').exists())

    def performers(self, count_id):
        return inventory_services.count_performers(InventoryCount.objects.get(pk=count_id))


class WhoPerformedTheCountTest(_SegregationHelpers, TestCase):
    def test_who_enters_a_quantity_is_a_performer(self):
        count_id = self.open()
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 7}], client=self.boss)
        self.assertEqual(self.performers(count_id), {self.approver.pk})

    def test_who_scans_an_increment_is_a_performer_but_a_lookup_is_not(self):
        count_id = self.open()
        res = self.boss.post(f'{COUNTS}{count_id}/scan/', {'code': '7750001', 'mode': 'lookup'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(self.performers(count_id), set())
        res = self.boss.post(f'{COUNTS}{count_id}/scan/',
                             {'code': '7750001', 'mode': 'increment', 'expected': None}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(self.performers(count_id), {self.approver.pk})

    def test_an_overwritten_entry_still_counts(self):
        """`counted_by` keeps only the last writer; the count remembers every one."""
        count_id = self.open()
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 3}], client=self.boss)
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 10}])
        line = InventoryCount.objects.get(pk=count_id).items.get(product=self.cable)
        self.assertEqual(line.counted_by, self.counter)
        self.assertEqual(self.performers(count_id), {self.approver.pk, self.counter.pk})
        self.submit(count_id)
        self.assert_refused(self.approve(count_id), count_id)

    def test_recount_and_exclusion_while_counting_are_counting_work(self):
        count_id = self.open()
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 10},
                            {'product': self.case.pk, 'physical_quantity': 4}])
        res = self.boss.post(f'{COUNTS}{count_id}/recount/', {'products': [self.cable.pk]}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIn(self.approver.pk, self.performers(count_id))

        other = self.open()
        self.put(other, [{'product': self.cable.pk, 'physical_quantity': 10}])
        res = self.boss.post(f'{COUNTS}{other}/exclude/', {'products': [self.cable.pk], 'reason': 'Otra fase'},
                             format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIn(self.approver.pk, self.performers(other))

    def test_the_submitter_is_a_performer(self):
        count_id = self.open()
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 10}])
        self.submit(count_id, client=self.boss)
        self.assertEqual(self.performers(count_id), {self.counter.pk, self.approver.pk})

    def test_the_creator_alone_is_not_a_performer(self):
        count_id = self.open(client=self.boss)
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 9}])
        self.submit(count_id)
        self.assertEqual(self.performers(count_id), {self.counter.pk})
        res = self.approve(count_id)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(branch_quantity(self.branch, self.cable), 9)

    def test_the_approvers_review_decisions_do_not_make_a_performer(self):
        count_id = self.open()
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 10},
                            {'product': self.case.pk, 'physical_quantity': 4}])
        self.submit(count_id)
        res = self.boss.post(f'{COUNTS}{count_id}/exclude/', {'products': [self.case.pk], 'reason': 'Vitrina'},
                             format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(self.performers(count_id), {self.counter.pk})
        res = self.boss.post(f'{COUNTS}{count_id}/reopen/', {'reason': 'Recontar la vitrina'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.submit(count_id)
        self.assertEqual(self.performers(count_id), {self.counter.pk})
        self.assertEqual(self.approve(count_id).status_code, 200)

    def test_a_count_opened_before_this_release_counts_its_creator_too(self):
        """
        Review #247 P3 — before this release a first entry was not audited and `counted_by` keeps only the last
        writer, so who opened an old count may have counted lines nobody can attribute any more. Conservative:
        without `metadata['performers']`, the creator is a performer. A count opened now carries the list.
        """
        count_id = self.open(client=self.boss)
        self.assertEqual(InventoryCount.objects.get(pk=count_id).metadata.get('performers'), [])
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 9}])
        self.submit(count_id)
        self.assertEqual(self.performers(count_id), {self.counter.pk})
        count = InventoryCount.objects.get(pk=count_id)
        count.metadata = {k: v for k, v in (count.metadata or {}).items() if k != 'performers'}
        count.save(update_fields=['metadata'])
        self.assertEqual(self.performers(count_id), {self.counter.pk, self.approver.pk})
        self.assert_refused(self.approve(count_id), count_id)

    def test_a_count_open_at_deploy_keeps_its_creator_after_later_writes(self):
        """
        Review #247 round 2 — the creator of a count opened before the rule stays a performer when, after the
        deploy, somebody else enters a quantity and submits: the first post-deploy write seeds the list with the
        creator instead of starting it empty.
        """
        count_id = self.open(client=self.boss)
        count = InventoryCount.objects.get(pk=count_id)
        count.metadata = {}  # as every count opened before this release
        count.save(update_fields=['metadata'])
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 9}])  # after the deploy
        self.submit(count_id)
        self.assertEqual(InventoryCount.objects.get(pk=count_id).metadata['performers'],
                         sorted([self.approver.pk, self.counter.pk]))
        self.assert_refused(self.approve(count_id), count_id)

    def test_a_count_open_at_deploy_and_only_submitted_keeps_its_creator(self):
        count_id = self.open(client=self.boss)
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 9}])
        count = InventoryCount.objects.get(pk=count_id)
        count.metadata = {}
        count.save(update_fields=['metadata'])
        # nothing else after the deploy: the submission is the first write
        self.submit(count_id)
        self.assertIn(self.approver.pk, InventoryCount.objects.get(pk=count_id).metadata['performers'])
        self.assert_refused(self.approve(count_id), count_id)

    def test_another_companys_trail_is_not_read(self):
        """Review #247 P3 — the trail is read by company (indexed), never across tenants."""
        from store.tests import _p2d_company

        count_id = self.open()
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 9}])
        AdminAuditLog.objects.create(
            actor=self.approver, action='inventory_count_line_changed', target_type='inventory_count',
            target_id=str(count_id), metadata={}, company=_p2d_company('seg-elsewhere'),
        )
        self.assertEqual(self.performers(count_id), {self.counter.pk})

    def test_a_count_opened_before_this_release_is_read_from_its_trail(self):
        """No `metadata['performers']`: the submitter, `counted_by` and the audited line changes still say who."""
        count_id = self.open()
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 3}], client=self.boss)
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 10}])  # audited: the boss's 3 → 10
        self.put(count_id, [{'product': self.case.pk, 'physical_quantity': 4}], client=self.own)
        count = InventoryCount.objects.get(pk=count_id)
        count.metadata = {k: v for k, v in (count.metadata or {}).items() if k != 'performers'}
        count.save(update_fields=['metadata'])
        # The honest limit of an old count: the audit names who CHANGED a line (the counter), not whose entry was
        # overwritten (the boss's 3). Counts opened from this release on keep every performer.
        self.assertEqual(self.performers(count_id), {self.counter.pk, self.owner.pk})
        self.submit(count_id, client=self.boss)
        self.assertIn(self.approver.pk, self.performers(count_id))


class SelfApprovalRefusedTest(_SegregationHelpers, TestCase):
    def test_an_approver_who_counted_cannot_approve_from_review(self):
        count_id = self.open(client=self.boss)
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 7}], client=self.boss)
        self.submit(count_id, client=self.boss)
        self.assert_refused(self.approve(count_id), count_id)
        self.assertEqual(branch_quantity(self.branch, self.cable), 10)

    def test_nor_directly_from_counting(self):
        count_id = self.open()
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 7}], client=self.boss)
        self.assert_refused(self.approve(count_id), count_id, status=InventoryCount.STATUS_COUNTING)

    def test_nor_a_migration_count(self):
        count_id = self.open('migration')
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 10},
                            {'product': self.case.pk, 'physical_quantity': 4}])
        self.submit(count_id, client=self.boss)
        self.assert_refused(self.approve(count_id), count_id)

    def test_a_reason_does_not_open_it_without_the_capability(self):
        count_id = self.open()
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 7}], client=self.boss)
        self.submit(count_id)
        self.assert_refused(self.approve(count_id, self_approval_reason='Estoy solo hoy'), count_id)

    def test_an_administrator_preset_is_not_exempt(self):
        admin, _ = _p2d_member(self.company, 'seg-admin', sorted(DEFAULT_ADMIN_CAPABILITY_CODES))
        client = _client(admin)
        count_id = self.open(client=client)
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 7}], client=client)
        self.submit(count_id, client=client)
        self.assert_refused(self.approve(count_id, client=client), count_id)

    def test_another_approver_approves_it(self):
        count_id = self.open(client=self.boss)
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 7}], client=self.boss)
        self.submit(count_id, client=self.boss)
        other, _ = _p2d_member(self.company, 'seg-other', _APPROVERS)
        res = self.approve(count_id, client=_client(other))
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(branch_quantity(self.branch, self.cable), 7)

    def test_the_screen_is_told_why(self):
        count_id = self.open()
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 7}], client=self.boss)
        self.submit(count_id)
        self.assertEqual(self.detail(count_id)['self_approval'],
                         {'counted_by_me': True, 'may_approve_own': False})
        self.assertEqual(self.detail(count_id, client=self.own)['self_approval'],
                         {'counted_by_me': False, 'may_approve_own': True})
        self.assertEqual(self.detail(count_id, client=self.api)['self_approval'],
                         {'counted_by_me': True, 'may_approve_own': False})

    def test_the_service_refuses_too(self):
        count = create_inventory_count(company=self.company, branch=self.branch, actor=self.counter)
        set_count_item(count, product=self.cable, physical_quantity=7, actor=self.approver)
        with self.assertRaises(inventory_services.CountSelfApproval):
            approve_inventory_count(count, actor=self.approver, version=inventory_services.count_version(count))
        self.assertFalse(StockMovement.objects.filter(inventory_count=count).exists())


class SelfApprovalRequestShapeTest(_SegregationHelpers, TestCase):
    def test_a_reason_that_is_not_text_is_a_400(self):
        count_id = self.open(client=self.own)
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 7}], client=self.own)
        self.submit(count_id, client=self.own)
        for reason in (['Solo'], {'r': 'Solo'}, 7, True):
            res = self.approve(count_id, client=self.own, self_approval_reason=reason)
            self.assertEqual(res.status_code, 400, (reason, res.content))
        self.assertEqual(InventoryCount.objects.get(pk=count_id).status, InventoryCount.STATUS_REVIEW)
        self.assertFalse(StockMovement.objects.filter(inventory_count_id=count_id).exists())


class ApproveOwnDelegationTest(_SegregationHelpers, TestCase):
    """Nobody delegates what they do not hold: a role manager without the capability cannot hand it out."""

    MANAGER = ['company.view', 'roles.manage', 'memberships.manage', 'memberships.view',
               'inventory.view', 'inventory.adjust', 'inventory.counts.approve']

    def setUp(self):
        super().setUp()
        from store.models import CompanyRole

        manager, _ = _p2d_member(self.company, 'seg-manager', self.MANAGER)
        self.manager = _client(manager)
        self.role = CompanyRole.objects.create(company=self.company, name='Contador seg', slug='contador-seg',
                                               capabilities=['company.view', 'inventory.view'])
        self.loaded = CompanyRole.objects.create(company=self.company, name='Con propios', slug='con-propios',
                                                 capabilities=['company.view', OWN])
        self.target, self.target_membership = _p2d_member(self.company, 'seg-target', ['company.view'])

    def test_it_cannot_put_it_in_a_new_role(self):
        from store.models import CompanyRole

        res = self.manager.post('/api/admin/roles/', {
            'company': self.company.pk, 'name': 'Propios', 'slug': 'propios',
            'capabilities': ['company.view', OWN],
        }, format='json')
        self.assertEqual(res.status_code, 403, res.content)
        self.assertFalse(CompanyRole.objects.filter(slug='propios').exists())

    def test_it_cannot_add_it_to_an_existing_role(self):
        res = self.manager.patch(f'/api/admin/roles/{self.role.pk}/', {
            'capabilities': ['company.view', 'inventory.view', OWN],
        }, format='json')
        self.assertEqual(res.status_code, 403, res.content)
        self.role.refresh_from_db()
        self.assertNotIn(OWN, self.role.capabilities)

    def test_it_cannot_assign_a_role_that_holds_it(self):
        from store.models import MembershipRoleAssignment

        res = self.manager.post('/api/admin/membership-role-assignments/', {
            'membership': self.target_membership.pk, 'role': self.loaded.pk,
        }, format='json')
        self.assertEqual(res.status_code, 403, res.content)
        self.assertFalse(MembershipRoleAssignment.objects.filter(
            membership=self.target_membership, role=self.loaded).exists())

    def test_whoever_holds_it_may_grant_it(self):
        from store.models import CompanyRole

        granter, _ = _p2d_member(self.company, 'seg-granter', self.MANAGER + [OWN])
        res = _client(granter).post('/api/admin/roles/', {
            'company': self.company.pk, 'name': 'Propios', 'slug': 'propios',
            'capabilities': ['company.view', OWN],
        }, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertIn(OWN, CompanyRole.objects.get(slug='propios').capabilities)


class CountListQueriesTest(_SegregationHelpers, TestCase):
    """Review #247 P2 — the history names who approved each count without one query per count."""

    def approved_count(self, approver):
        return InventoryCount.objects.create(
            company=self.company, branch=self.branch, status=InventoryCount.STATUS_APPROVED,
            created_by=self.counter, approved_by=approver,
        )

    def list_queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            res = self.boss.get(COUNTS, {'branch': self.branch.pk})
        self.assertEqual(res.status_code, 200, res.content)
        return len(ctx.captured_queries), res.json()['results']

    def test_the_list_does_not_query_per_approver(self):
        self.approved_count(self.approver)
        self.list_queries()  # warm the per-request caches
        first, _ = self.list_queries()
        for n in range(3):
            other, _ = _p2d_member(self.company, f'seg-list-{n}', _APPROVERS)
            self.approved_count(other)
        then, rows = self.list_queries()
        self.assertEqual(then, first)
        self.assertEqual(len(rows), 4)
        self.assertEqual({r['approved_by_username'] for r in rows},
                         {self.approver.username, 'seg-list-0', 'seg-list-1', 'seg-list-2'})


class SelfApprovalOptInTest(_SegregationHelpers, TestCase):
    def own_count(self):
        count_id = self.open(client=self.own)
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 7}], client=self.own)
        self.submit(count_id, client=self.own)
        return count_id

    def test_it_asks_a_reason(self):
        count_id = self.own_count()
        for reason in (None, '', '   '):
            body = {} if reason is None else {'self_approval_reason': reason}
            res = self.approve(count_id, client=self.own, **body)
            self.assertEqual(res.status_code, 400, res.content)
            self.assertEqual(res.json()['code'], 'self_approval_reason_required')
        count = InventoryCount.objects.get(pk=count_id)
        self.assertEqual(count.status, InventoryCount.STATUS_REVIEW)
        self.assertFalse(StockMovement.objects.filter(inventory_count_id=count_id).exists())

    def test_with_a_reason_it_is_approved_recorded_and_flagged(self):
        count_id = self.own_count()
        res = self.approve(count_id, client=self.own, self_approval_reason='Sólo yo en la tienda hoy')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(branch_quantity(self.branch, self.cable), 7)
        body = res.json()
        self.assertEqual(body['approved_by_username'], self.owner.username)
        self.assertEqual(body['self_approved']['by'], self.owner.username)
        self.assertEqual(body['self_approved']['reason'], 'Sólo yo en la tienda hoy')
        self.assertTrue(body['self_approved']['at'])

        approved = AdminAuditLog.objects.get(action='inventory_count_approved', target_id=str(count_id))
        self.assertEqual(approved.actor, self.owner)
        self.assertTrue(approved.metadata['self_approval'])
        self.assertEqual(approved.metadata['self_approval_reason'], 'Sólo yo en la tienda hoy')
        self.assertEqual(approved.metadata['performers'], [self.owner.pk])
        flag = AdminAuditLog.objects.get(action='inventory_count_self_approved', target_id=str(count_id))
        self.assertEqual(flag.actor, self.owner)
        self.assertEqual(flag.company_id, self.company.pk)
        self.assertEqual(flag.metadata['reason'], 'Sólo yo en la tienda hoy')

    def test_an_approval_of_somebody_elses_count_is_not_flagged(self):
        count_id = self.open()
        self.put(count_id, [{'product': self.cable.pk, 'physical_quantity': 7}])
        self.submit(count_id)
        res = self.approve(count_id, client=self.own, self_approval_reason='No hace falta')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIsNone(res.json()['self_approved'])
        approved = AdminAuditLog.objects.get(action='inventory_count_approved', target_id=str(count_id))
        self.assertFalse(approved.metadata['self_approval'])
        self.assertFalse(AdminAuditLog.objects.filter(action='inventory_count_self_approved').exists())

    def test_a_refused_approval_records_nothing(self):
        count_id = self.own_count()
        res = self.approve(count_id, client=self.own, version='stale', self_approval_reason='Solo')
        self.assertEqual(res.status_code, 409, res.content)
        count = InventoryCount.objects.get(pk=count_id)
        self.assertNotIn('self_approval', count.metadata or {})
        self.assertFalse(AdminAuditLog.objects.filter(action='inventory_count_self_approved').exists())


class ApproveOwnCapabilityTest(TestCase):
    def test_it_is_assignable_and_granted_by_nobody_by_default(self):
        self.assertIn(OWN, ASSIGNABLE_CAPABILITY_CODES)
        self.assertEqual(CAPABILITIES[OWN].module, 'inventory')
        self.assertIn(OWN, EXPLICIT_GRANT_CODES)
        self.assertNotIn(OWN, DEFAULT_ADMIN_CAPABILITY_CODES)
        for _name, slug, _desc, caps in PRESET_ROLES:
            self.assertNotIn(OWN, caps, slug)
        for role, caps in LEGACY_ROLE_CAPABILITIES.items():
            self.assertNotIn(OWN, caps, role)

    def run_0163(self):
        from importlib import import_module

        from django.apps import apps

        import_module('store.migrations.0163_counts_approve_own_capability').strip(apps, None)

    def test_the_repair_touches_only_a_fresh_installs_seeded_administrador(self):
        """Review #247 P3 — re-running 0163 on a live database never takes back a grant made on purpose."""
        from store.models import CompanyRole, Membership
        from store.tests import _p2d_company

        company = _p2d_company('seg-0163')
        admin = CompanyRole.objects.get(company=company, slug='administrador')
        admin.capabilities = sorted({*admin.capabilities, OWN})
        admin.save(update_fields=['capabilities'])
        other = CompanyRole.objects.create(company=company, name='Propios', slug='propios-0163',
                                           capabilities=['company.view', OWN])
        self.assertFalse(Membership.objects.exists())  # a fresh install: nobody could have granted it yet
        self.run_0163()
        admin.refresh_from_db()
        other.refresh_from_db()
        self.assertNotIn(OWN, admin.capabilities)
        self.assertIn(OWN, other.capabilities, 'only the seeded Administrador is repaired')

        # A database in use (somebody works in it): the grant is a decision, and a re-run keeps it.
        _p2d_member(company, 'seg-0163-user', ['company.view'])
        admin.capabilities = sorted({*admin.capabilities, OWN})
        admin.save(update_fields=['capabilities'])
        self.run_0163()
        admin.refresh_from_db()
        self.assertIn(OWN, admin.capabilities)

    def test_the_deploy_rehearsal_checks_a_fresh_database(self):
        """Review #247 round 2 — 0163 keys on «no Membership»; the rehearsal proves no role kept the code."""
        from pathlib import Path

        from django.conf import settings

        rehearsal = Path(settings.BASE_DIR).parent / 'deploy' / 'rehearsal.sh'
        if not rehearsal.exists():
            self.skipTest('deploy/ is not part of this checkout')
        script = rehearsal.read_text()
        self.assertIn('FRESH_APPROVE_OWN=', script)
        self.assertIn('"roles con inventory.counts.approve_own: 0"', script)

    def test_no_migrated_role_holds_it(self):
        """A fresh install's seeded `Administrador` is built from the live catalogue (0061): the repair strips it."""
        from store.models import CompanyRole

        self.assertFalse([r.slug for r in CompanyRole.objects.all() if OWN in (r.capabilities or [])])


class SegregationRaceTest(TransactionTestCase):
    """The check is taken under the count's row lock: the approver's own write cannot slip past it."""

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
        fixture.build('seg-race')
        self.f = fixture

    def counting(self):
        f = self.f
        count = create_inventory_count(company=f.company, branch=f.branch, actor=f.counter)
        set_count_item(count, product=f.cable, physical_quantity=10, actor=f.counter)
        return count

    def test_the_approvers_write_holding_the_lock_is_seen_by_the_approval(self):
        f = self.f
        count = self.counting()
        version = inventory_services.count_version(InventoryCount.objects.get(pk=count.pk))
        real = inventory_services._locked_branch_stocks
        started = threading.Event()

        def slow(*args, **kwargs):
            if not started.is_set():
                started.set()
                time.sleep(0.4)  # the write holds the count's row lock meanwhile
            return real(*args, **kwargs)

        def write():
            return set_count_item(InventoryCount.objects.get(pk=count.pk), product=f.case,
                                  physical_quantity=3, actor=f.approver)

        def approve():
            started.wait(timeout=10)
            return approve_inventory_count(InventoryCount.objects.get(pk=count.pk), actor=f.approver,
                                           version=version)

        with mock.patch.object(inventory_services, '_locked_branch_stocks', side_effect=slow):
            results = self.together([write, approve])
        self.assertNotIsInstance(results[0], Exception, repr(results[0]))
        self.assertIsInstance(results[1], inventory_services.CountSelfApproval, repr(results[1]))
        count.refresh_from_db()
        self.assertEqual(count.status, InventoryCount.STATUS_COUNTING)
        self.assertFalse(StockMovement.objects.filter(inventory_count=count).exists())

    def test_the_approvers_write_after_the_approval_took_the_lock_is_refused(self):
        f = self.f
        count = self.counting()
        version = inventory_services.count_version(InventoryCount.objects.get(pk=count.pk))
        real = inventory_services.count_performers
        started = threading.Event()

        def slow(*args, **kwargs):
            started.set()
            time.sleep(0.4)  # the approval holds the count's row lock meanwhile
            return real(*args, **kwargs)

        def approve():
            return approve_inventory_count(InventoryCount.objects.get(pk=count.pk), actor=f.approver,
                                           version=version)

        def write():
            started.wait(timeout=10)
            return set_count_item(InventoryCount.objects.get(pk=count.pk), product=f.case,
                                  physical_quantity=3, actor=f.approver)

        with mock.patch.object(inventory_services, 'count_performers', side_effect=slow):
            results = self.together([approve, write])
        self.assertNotIsInstance(results[0], Exception, repr(results[0]))
        self.assertIsInstance(results[1], inventory_services.InventoryCountError, repr(results[1]))
        count.refresh_from_db()
        self.assertEqual(count.status, InventoryCount.STATUS_APPROVED)
        self.assertNotIn(f.approver.pk, inventory_services.count_performers(count))
        self.assertFalse(count.items.filter(product=f.case).exists())
