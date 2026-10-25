"""
AUTH-C (review P2-6) — «was the last sale made?», asked after signing in again.

The till sends a sale, the answer never arrives, and the session closes before
the cashier can retry. After signing in again the basket is gone from memory,
and rebuilding it under a NEW key would sell twice. So the till keeps only the
key of a sale in flight (sessionStorage, nothing private) and, back in, ASKS
the server with it. This endpoint only answers; it never sells, never retries,
never repeats anything. The cashier reads the answer and decides.

    GET /api/admin/pos/sales/by-key/?key=<idempotency key>

Same authority as the till (`sales.pos.use`), same company resolution, and a
sale of another company — or of a branch the caller cannot operate — answers
exactly like a key nobody used: `{"found": false}`.
"""
from django.core.cache import cache
from rest_framework.test import APIClient

from store.models import Order
from store.tests import Ip1PosBase, _m7_user, _saas_company  # noqa: F401

URL = '/api/admin/pos/sales/by-key/'


def _web_login(username):
    client = APIClient()
    cache.clear()
    res = client.post('/api/auth/login/', {'username': username, 'password': 'Pass123!'}, format='json')
    assert res.status_code == 200, res.content
    return client


class PosSaleByKeyTest(Ip1PosBase):
    def setUp(self):
        super().setUp()
        self.web = _web_login('ip1_caja')

    def sell(self, key='ip1-reconcile-0001', **over):
        res = self.web.post('/api/admin/pos/sales/', self.sale_body(idempotency_key=key, **over), format='json')
        self.assertIn(res.status_code, (200, 201), res.content)
        return res.json()

    def test_a_sale_that_was_made_is_found_by_its_key(self):
        sale = self.sell()

        res = self.web.get(URL, {'key': 'ip1-reconcile-0001'})

        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertTrue(body['found'])
        self.assertEqual(body['order_id'], sale['order_id'])
        self.assertEqual(body['total'], sale['total'])
        self.assertIn('paid_at', body)

    def test_a_key_nobody_used_is_not_found(self):
        res = self.web.get(URL, {'key': 'ip1-nunca-usada-01'})

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {'found': False})

    def test_asking_sells_nothing(self):
        before = Order.objects.count()

        self.web.get(URL, {'key': 'ip1-reconcile-0002'})

        self.assertEqual(Order.objects.count(), before)

    def test_a_sale_of_another_company_answers_like_nothing(self):
        self.sell(key='ip1-compartida-001')
        Order.objects.filter(pos_idempotency_key='ip1-compartida-001').update(company=self.other)

        res = self.web.get(URL, {'key': 'ip1-compartida-001'})

        self.assertEqual(res.json(), {'found': False})

    def test_a_sale_of_a_branch_the_caller_cannot_operate_answers_like_nothing(self):
        from store.models import MembershipBranchAccess

        self.sell(key='ip1-otra-sucursal1', branch=self.branch_b.pk)
        self.membership.branch_access_mode = 'selected'
        self.membership.save()
        MembershipBranchAccess.objects.create(membership=self.membership, branch=self.branch_a)
        cache.clear()

        res = self.web.get(URL, {'key': 'ip1-otra-sucursal1'})

        self.assertEqual(res.json(), {'found': False})

    def test_without_the_till_capability_it_is_refused(self):
        self.sell()
        self.only_caps('inventory.view')
        client = _web_login('ip1_caja')

        res = client.get(URL, {'key': 'ip1-reconcile-0001'})

        self.assertEqual(res.status_code, 403)

    def test_a_key_is_required(self):
        self.assertEqual(self.web.get(URL).status_code, 400)
        self.assertEqual(self.web.get(URL, {'key': 'x' * 65}).status_code, 400)

    def test_it_needs_a_session(self):
        self.assertEqual(APIClient().get(URL, {'key': 'ip1-reconcile-0001'}).status_code, 401)


DECISION = '/api/admin/pos/sales/by-key/decision/'


class PosNotChargedDecisionTest(Ip1PosBase):
    """
    Owner, 2026-10-25 (5): when the server cannot say, the cashier decides —
    and the decision is RECORDED. It is refused if a sale under that key exists.
    """

    def setUp(self):
        super().setUp()
        self.web = _web_login('ip1_caja')

    def test_the_cashiers_not_charged_decision_is_recorded_with_the_company(self):
        from store.models import AdminAuditLog

        res = self.web.post(DECISION, {'key': 'ip1-decision-0001', 'decision': 'not_charged'}, format='json')

        self.assertEqual(res.status_code, 200, res.content)
        entry = AdminAuditLog.objects.get(action='pos_sale_reconciled_not_charged')
        self.assertEqual(entry.company, self.company)
        self.assertEqual(entry.actor, self.staff)
        self.assertEqual(entry.metadata['idempotency_key'], 'ip1-decision-0001')

    def test_it_is_refused_when_the_sale_was_made(self):
        from store.models import AdminAuditLog

        made = self.web.post('/api/admin/pos/sales/', self.sale_body(idempotency_key='ip1-decision-0002'), format='json')
        self.assertIn(made.status_code, (200, 201), made.content)

        res = self.web.post(DECISION, {'key': 'ip1-decision-0002', 'decision': 'not_charged'}, format='json')

        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['order_id'], made.json()['order_id'])
        self.assertFalse(AdminAuditLog.objects.filter(action='pos_sale_reconciled_not_charged').exists())

    def test_it_sells_nothing(self):
        before = Order.objects.count()
        self.web.post(DECISION, {'key': 'ip1-decision-0003', 'decision': 'not_charged'}, format='json')
        self.assertEqual(Order.objects.count(), before)

    def test_only_that_decision_and_a_real_key(self):
        self.assertEqual(self.web.post(DECISION, {'key': 'ip1-decision-0004', 'decision': 'charged'}, format='json').status_code, 400)
        self.assertEqual(self.web.post(DECISION, {'decision': 'not_charged'}, format='json').status_code, 400)

    def test_without_the_till_capability_it_is_refused(self):
        self.only_caps('inventory.view')
        client = _web_login('ip1_caja')
        res = client.post(DECISION, {'key': 'ip1-decision-0005', 'decision': 'not_charged'}, format='json')
        self.assertEqual(res.status_code, 403)


class NotChargedReservesTheKeyTest(Ip1PosBase):
    """
    #184 review P2-1: «no se cobró» RESERVES the key. A sale arriving later with
    it — the original request stuck for minutes, or a retry — answers 409 and
    sells nothing. Without this the decision opened a door to a double sale.
    """

    def setUp(self):
        super().setUp()
        self.web = _web_login('ip1_caja')

    def test_after_the_decision_a_sale_with_that_key_is_refused_and_sells_nothing(self):
        self.assertEqual(
            self.web.post(DECISION, {'key': 'ip1-reserve-0001', 'decision': 'not_charged'}, format='json').status_code,
            200,
        )
        stock = self.stock()

        res = self.web.post('/api/admin/pos/sales/', self.sale_body(idempotency_key='ip1-reserve-0001'), format='json')

        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json().get('code'), 'key_reserved')
        self.assertFalse(Order.objects.filter(pos_idempotency_key='ip1-reserve-0001').exists())
        self.assertEqual(self.stock(), stock)

    def test_the_reservation_is_per_company(self):
        from store.models import AdminAuditLog

        AdminAuditLog.objects.create(
            actor=self.staff, action='pos_sale_reconciled_not_charged', target_type='pos_sale_key',
            target_id='ip1-reserve-0002', metadata={'idempotency_key': 'ip1-reserve-0002'}, company=self.other,
        )

        res = self.web.post('/api/admin/pos/sales/', self.sale_body(idempotency_key='ip1-reserve-0002'), format='json')

        self.assertIn(res.status_code, (200, 201), res.content)

    def test_a_sale_in_a_branch_the_cashier_cannot_see_refuses_the_decision_without_details(self):
        from store.models import AdminAuditLog, MembershipBranchAccess

        self.web.post('/api/admin/pos/sales/', self.sale_body(idempotency_key='ip1-reserve-0003', branch=self.branch_b.pk),
                      format='json')
        self.membership.branch_access_mode = 'selected'
        self.membership.save()
        MembershipBranchAccess.objects.create(membership=self.membership, branch=self.branch_a)
        cache.clear()

        res = self.web.post(DECISION, {'key': 'ip1-reserve-0003', 'decision': 'not_charged'}, format='json')

        self.assertEqual(res.status_code, 409)
        self.assertNotIn('order_id', res.json())
        self.assertNotIn('total', res.json())
        self.assertFalse(AdminAuditLog.objects.filter(action='pos_sale_reconciled_not_charged').exists())
