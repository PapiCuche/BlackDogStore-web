"""
INV-DRAFT-01 — the draft of «Recibir mercadería», kept on the SERVER so a reload does not lose it.

    GET    /api/admin/inventory/receipts/draft/    {draft: {...payload, branch, updated_at, expires_at}} | {draft: null}
    PUT    /api/admin/inventory/receipts/draft/    {branch: id|null, payload: {...}}  → upsert, expiry renewed
    DELETE /api/admin/inventory/receipts/draft/    idempotent 204

What these tests hold:

  * one draft per person per company, read back only by its author, in that company;
  * the same authority as the receipt itself (`inventory.adjust`, branch among the caller's grants);
  * a conservative expiry (`RECEIPT_DRAFT_TTL_SECONDS`): an expired draft is deleted when read and by the purge
    command, never given back;
  * costs only from and to whoever holds `costs.view`;
  * a draft writes NOTHING of the inventory: no unit, movement, shelf, label batch or audit line;
  * a confirmed reception deletes its author's draft.
"""
import json
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from store.models import ReceiptDraft
from store.test_inventory_receipts import ReceiptBase, unit
from store.test_registration import _client
from store.tests import _p2d_member

DRAFT = '/api/admin/inventory/receipts/draft/'


def payload(*, lines=None, staged=None, **origin):
    return {
        'origin': {'branch': None, 'supplier': None, 'document_ref': 'F001-9', 'notes': '', **origin},
        'lines': lines if lines is not None else [{
            'key': 'l1', 'product': {'id': 1, 'name': 'iPhone', 'is_serialized': True, 'requires_imei': True},
            'quantity': '', 'unitCost': '',
            'units': [{'key': 'u1', 'serial_number': 'SERDRAFT1', 'imei': '356789012345678', 'imei2': '',
                       'condition': 'new', 'cost': '', 'battery': '', 'grade': '', 'notes': '', 'price': '',
                       'publicNotes': ''}],
        }],
        'staged': staged if staged is not None else [],
    }


class DraftBase(ReceiptBase):
    def put(self, user, body, expect=None, company=None):
        url = DRAFT + (f'?company={company.pk}' if company else '')
        res = _client(user).put(url, body, format='json')
        if expect is not None:
            self.assertEqual(res.status_code, expect, res.content)
        return res

    def get(self, user, expect=200, company=None):
        url = DRAFT + (f'?company={company.pk}' if company else '')
        res = _client(user).get(url)
        self.assertEqual(res.status_code, expect, res.content)
        return res

    def delete(self, user, expect=204):
        res = _client(user).delete(DRAFT)
        self.assertEqual(res.status_code, expect, res.content)
        return res


class RoundTripTest(DraftBase):
    def test_put_then_get_gives_the_payload_back_with_branch_and_dates(self):
        body = {'branch': self.centro.pk, 'payload': payload(branch=self.centro.pk)}
        res = self.put(self.clerk, body, expect=200)
        self.assertIn('expires_at', res.json()['draft'])
        draft = self.get(self.clerk).json()['draft']
        self.assertEqual(draft['branch'], self.centro.pk)
        self.assertEqual(draft['lines'], body['payload']['lines'])
        self.assertEqual(draft['origin']['document_ref'], 'F001-9')
        self.assertIn('updated_at', draft)
        self.assertIn('expires_at', draft)

    def test_no_draft_is_null(self):
        self.assertIsNone(self.get(self.clerk).json()['draft'])

    def test_branch_may_be_null_before_it_is_chosen(self):
        self.put(self.clerk, {'branch': None, 'payload': payload()}, expect=200)
        self.assertIsNone(self.get(self.clerk).json()['draft']['branch'])

    def test_a_second_put_replaces_the_first_one_draft_per_person_per_company(self):
        self.put(self.clerk, {'branch': None, 'payload': payload(document_ref='PRIMERO')}, expect=200)
        self.put(self.clerk, {'branch': self.norte.pk, 'payload': payload(document_ref='SEGUNDO')}, expect=200)
        self.assertEqual(ReceiptDraft.objects.filter(company=self.company, user=self.clerk).count(), 1)
        draft = self.get(self.clerk).json()['draft']
        self.assertEqual((draft['origin']['document_ref'], draft['branch']), ('SEGUNDO', self.norte.pk))

    def test_the_server_fields_cannot_be_spoofed_by_the_payload(self):
        body = payload()
        body.update({'branch': self.norte.pk, 'expires_at': '2999-01-01T00:00:00Z', 'updated_at': 'x'})
        self.put(self.clerk, {'branch': None, 'payload': body}, expect=200)
        draft = self.get(self.clerk).json()['draft']
        self.assertIsNone(draft['branch'])
        self.assertNotEqual(draft['expires_at'], '2999-01-01T00:00:00Z')

    def test_the_draft_is_renewed_on_each_save(self):
        self.put(self.clerk, {'branch': None, 'payload': payload()}, expect=200)
        ReceiptDraft.objects.update(expires_at=timezone.now() + timedelta(minutes=1))
        self.put(self.clerk, {'branch': None, 'payload': payload()}, expect=200)
        row = ReceiptDraft.objects.get()
        self.assertGreater(row.expires_at, timezone.now() + timedelta(hours=23))


class ScopeTest(DraftBase):
    def test_another_person_of_the_same_company_cannot_read_it(self):
        self.put(self.clerk, {'branch': None, 'payload': payload()}, expect=200)
        self.assertIsNone(self.get(self.costed).json()['draft'])
        # …nor delete it.
        self.delete(self.costed)
        self.assertTrue(ReceiptDraft.objects.filter(user=self.clerk).exists())

    def test_another_company_sees_nothing(self):
        self.put(self.clerk, {'branch': None, 'payload': payload()}, expect=200)
        self.assertIsNone(self.get(self.outsider).json()['draft'])

    def test_the_same_person_in_another_company_has_another_draft(self):
        from store.models import Membership
        from store.tests import _assign, _role
        membership = Membership.objects.create(user=self.clerk, company=self.other,
                                               role=Membership.objects.get(user=self.clerk).role)
        _assign(membership, _role(self.other, 'Rol otro', ['inventory.adjust', 'inventory.view'], slug='rol-otro'))
        self.put(self.clerk, {'branch': None, 'payload': payload()}, expect=200, company=self.company)
        self.assertIsNone(self.get(self.clerk, company=self.other).json()['draft'])
        self.assertIsNotNone(self.get(self.clerk, company=self.company).json()['draft'])

    def test_a_platform_master_names_the_company_and_keeps_one_draft_per_company(self):
        from django.contrib.auth import get_user_model
        master = get_user_model().objects.create_user(
            username='draft_master', email='draft_master@example.com', password='Pass123!',
            is_superuser=True, is_staff=True)
        self.put(master, {'branch': self.centro.pk, 'payload': payload()}, expect=200, company=self.company)
        self.assertIsNone(self.get(master, company=self.other).json()['draft'])
        self.assertEqual(self.get(master, company=self.company).json()['draft']['branch'], self.centro.pk)
        self.assertEqual(ReceiptDraft.objects.get(user=master).company, self.company)

    def test_without_inventory_adjust_every_method_is_403(self):
        self.put(self.viewer, {'branch': None, 'payload': payload()}, expect=403)
        self.get(self.viewer, expect=403)
        self.delete(self.viewer, expect=403)
        self.assertFalse(ReceiptDraft.objects.exists())

    def test_anonymous_is_refused(self):
        from rest_framework.test import APIClient
        for method in ('get', 'put', 'delete'):
            self.assertEqual(getattr(APIClient(), method)(DRAFT, {}, format='json').status_code, 401, method)

    def test_a_branch_outside_the_grants_is_refused(self):
        res = self.put(self.centro_only, {'branch': self.norte.pk, 'payload': payload()})
        self.assertEqual(res.status_code, 404, res.content)
        self.assertFalse(ReceiptDraft.objects.exists())

    def test_another_companys_branch_is_refused(self):
        res = self.put(self.clerk, {'branch': self.foreign_branch.pk, 'payload': payload()})
        self.assertEqual(res.status_code, 404, res.content)
        self.assertFalse(ReceiptDraft.objects.exists())

    def test_a_branch_that_is_no_longer_reachable_is_read_back_as_null(self):
        from store.models import Membership, MembershipBranchAccess
        self.put(self.clerk, {'branch': self.norte.pk, 'payload': payload()}, expect=200)
        membership = Membership.objects.get(user=self.clerk, company=self.company)
        Membership.objects.filter(pk=membership.pk).update(branch_access_mode=Membership.ACCESS_MODE_SELECTED)
        MembershipBranchAccess.objects.create(membership=membership, branch=self.centro)
        self.assertIsNone(self.get(self.clerk).json()['draft']['branch'])


class ShapeTest(DraftBase):
    def test_the_payload_must_be_an_object(self):
        for body in ({'branch': None, 'payload': []}, {'branch': None, 'payload': 'x'}, {'branch': None},
                     [], {'branch': 'x', 'payload': payload()}):
            with self.subTest(body=body):
                self.put(self.clerk, body, expect=400)
        self.assertFalse(ReceiptDraft.objects.exists())

    def test_an_oversize_payload_is_400(self):
        big = payload()
        big['origin']['notes'] = 'x' * (256 * 1024)
        res = self.put(self.clerk, {'branch': None, 'payload': big}, expect=400)
        self.assertEqual(res.json()['code'], 'too_large')
        self.assertFalse(ReceiptDraft.objects.exists())

    def test_more_lines_or_devices_than_a_receipt_takes_is_400(self):
        from store import receipt_services as receipts
        line = payload()['lines'][0]
        too_many_lines = payload(lines=[line] * (receipts.MAX_LINES + 1))
        self.put(self.clerk, {'branch': None, 'payload': too_many_lines}, expect=400)
        devices = [dict(line['units'][0]) for _ in range(receipts.MAX_DEVICES + 1)]
        too_many_devices = payload(lines=[{**line, 'units': devices}])
        self.put(self.clerk, {'branch': None, 'payload': too_many_devices}, expect=400)
        self.put(self.clerk, {'branch': None, 'payload': payload(lines='x')}, expect=400)
        self.assertFalse(ReceiptDraft.objects.exists())


class CostTest(DraftBase):
    def test_a_cost_from_someone_without_costs_view_is_refused(self):
        line = payload()['lines'][0]
        with_unit_cost = payload(lines=[{**line, 'units': [{**line['units'][0], 'cost': '1200.00'}]}])
        with_line_cost = payload(lines=[{**line, 'unitCost': '12.50'}])
        with_staged_cost = payload(staged=[{'id': 1, 'serial': 'S', 'cost': '9.00'}])
        for body in (with_unit_cost, with_line_cost, with_staged_cost):
            with self.subTest(body=body):
                res = self.put(self.clerk, {'branch': None, 'payload': body}, expect=403)
                self.assertEqual(res.json()['code'], 'costs_forbidden')
        self.assertFalse(ReceiptDraft.objects.exists())

    def test_costs_are_kept_for_who_may_see_them_and_stripped_for_who_lost_it(self):
        line = payload()['lines'][0]
        body = payload(lines=[{**line, 'unitCost': '12.50', 'units': [{**line['units'][0], 'cost': '1200.00'}]}],
                       staged=[{'id': 1, 'serial': 'S', 'cost': '9.00'}])
        self.put(self.costed, {'branch': None, 'payload': body}, expect=200)
        draft = self.get(self.costed).json()['draft']
        self.assertEqual(draft['lines'][0]['units'][0]['cost'], '1200.00')
        self.assertEqual(draft['lines'][0]['unitCost'], '12.50')
        # The permission is taken away: the stored amounts are not given back.
        from store.models import CompanyRole
        CompanyRole.objects.filter(company=self.company, slug='rol-ops_costed').update(
            capabilities=['inventory.adjust', 'inventory.view'])
        text = json.dumps(self.get(self.costed).json())
        self.assertNotIn('1200.00', text)
        self.assertNotIn('12.50', text)
        self.assertNotIn('9.00', text)


class NothingOfTheInventoryTest(DraftBase):
    def test_a_draft_writes_no_stock_kardex_unit_label_or_audit(self):
        before = self.snapshot()
        body = payload(lines=[{
            'key': 'l1', 'product': {'id': self.phone.pk, 'name': self.phone.name, 'is_serialized': True},
            'quantity': '', 'unitCost': '',
            'units': [{'key': 'u1', 'serial_number': 'DRAFTSER', 'imei': '356789012345678', 'condition': 'new',
                       'cost': ''}],
        }], branch=self.centro.pk)
        self.put(self.clerk, {'branch': self.centro.pk, 'payload': body}, expect=200)
        self.get(self.clerk)
        self.delete(self.clerk)
        self.assertEqual(self.snapshot(), before)


class ExpiryTest(DraftBase):
    def test_an_expired_draft_is_null_and_deleted_on_read(self):
        self.put(self.clerk, {'branch': None, 'payload': payload()}, expect=200)
        ReceiptDraft.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertIsNone(self.get(self.clerk).json()['draft'])
        self.assertFalse(ReceiptDraft.objects.exists())

    @override_settings(RECEIPT_DRAFT_TTL_SECONDS=60)
    def test_the_expiry_follows_the_setting(self):
        self.put(self.clerk, {'branch': None, 'payload': payload()}, expect=200)
        row = ReceiptDraft.objects.get()
        self.assertLessEqual(row.expires_at, timezone.now() + timedelta(seconds=61))

    def test_the_default_is_24_hours(self):
        from django.conf import settings
        self.assertEqual(settings.RECEIPT_DRAFT_TTL_SECONDS, 86400)

    def test_the_purge_command_deletes_only_expired_drafts(self):
        self.put(self.clerk, {'branch': None, 'payload': payload()}, expect=200)
        self.put(self.costed, {'branch': None, 'payload': payload()}, expect=200)
        ReceiptDraft.objects.filter(user=self.clerk).update(expires_at=timezone.now() - timedelta(minutes=1))
        out = StringIO()
        call_command('purge_receipt_drafts', stdout=out)
        self.assertIn('1', out.getvalue())
        self.assertEqual(list(ReceiptDraft.objects.values_list('user_id', flat=True)), [self.costed.pk])
        out = StringIO()
        call_command('purge_receipt_drafts', stdout=out)   # idempotent
        self.assertIn('0', out.getvalue())
        self.assertEqual(ReceiptDraft.objects.count(), 1)


class LifecycleTest(DraftBase):
    def test_delete_is_idempotent(self):
        self.put(self.clerk, {'branch': None, 'payload': payload()}, expect=200)
        self.delete(self.clerk)
        self.delete(self.clerk)
        self.assertFalse(ReceiptDraft.objects.exists())

    def test_a_confirmed_reception_deletes_its_authors_draft_only(self):
        self.put(self.clerk, {'branch': None, 'payload': payload()}, expect=200)
        self.put(self.costed, {'branch': None, 'payload': payload()}, expect=200)
        key = self.key()
        self.confirm(self.clerk, self.body(self.qty(self.cable, 3)), key=key, expect=201)
        self.assertFalse(ReceiptDraft.objects.filter(user=self.clerk).exists())
        self.assertTrue(ReceiptDraft.objects.filter(user=self.costed).exists())
        # A replay of the same reception (the answer was lost, the screen saved again meanwhile) deletes it too.
        self.put(self.clerk, {'branch': None, 'payload': payload()}, expect=200)
        self.confirm(self.clerk, self.body(self.qty(self.cable, 3)), key=key, expect=200)
        self.assertFalse(ReceiptDraft.objects.filter(user=self.clerk).exists())

    def test_a_refused_reception_keeps_the_draft(self):
        self.put(self.clerk, {'branch': None, 'payload': payload()}, expect=200)
        self.confirm(self.clerk, self.body(self.dev(self.phone, unit('X', None))), expect=400)
        self.assertTrue(ReceiptDraft.objects.filter(user=self.clerk).exists())
