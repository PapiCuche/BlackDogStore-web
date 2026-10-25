"""
#184 review P2-1 — a sale and a «no se cobró» decision for the SAME key, at the
same instant. Exactly one of them wins: either the sale exists and the decision
is refused, or the decision is recorded and the sale is refused. Never both.

Real threads against real PostgreSQL; each case holds one side right after it
took the key's lock, so the other side has to wait for it.
"""
import threading
from unittest import mock

from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from store import pos_services
from store.models import AdminAuditLog, Order

HOLD = 1.5
KEY = 'race-key-000001'


class SaleAgainstNotChargedTest(TransactionTestCase):
    def setUp(self):
        from store.tests import Ip1PosBase

        # Ip1PosBase is a TestCase; borrow its fixture builder on this instance.
        Ip1PosBase.setUp(self)  # type: ignore[arg-type]
        cache.clear()

    def _client(self):
        client = APIClient()
        res = client.post('/api/auth/login/', {'username': 'ip1_caja', 'password': 'Pass123!'}, format='json')
        assert res.status_code == 200, res.content
        return client

    def _body(self):
        return {
            'branch': self.branch_a.pk, 'items': [{'product': self.cable.pk, 'quantity': 1}],
            'payment_method': 'cash', 'amount_received': '100.00', 'idempotency_key': KEY, 'terms_confirmed': True,
        }

    def _run(self, first, second, patch_target, patch_name):
        results = {}
        inside = threading.Event()
        real = getattr(patch_target, patch_name)

        def held(*args, **kwargs):
            inside.set()
            threading.Event().wait(HOLD)
            return real(*args, **kwargs)

        def go(name, fn):
            try:
                results[name] = fn()
            finally:
                connection.close()

        with mock.patch.object(patch_target, patch_name, held):
            a = threading.Thread(target=go, args=('first', first))
            a.start()
            inside.wait(HOLD * 2)
        b = threading.Thread(target=go, args=('second', second))
        b.start()
        a.join()
        b.join()
        return results

    def sell(self):
        return self._client().post('/api/admin/pos/sales/', self._body(), format='json').status_code

    def decide(self):
        return self._client().post(
            '/api/admin/pos/sales/by-key/decision/', {'key': KEY, 'decision': 'not_charged'}, format='json',
        ).status_code

    def _exactly_one(self):
        sold = Order.objects.filter(pos_idempotency_key=KEY).exists()
        decided = AdminAuditLog.objects.filter(action='pos_sale_reconciled_not_charged').exists()
        self.assertTrue(sold != decided, f'sold={sold} decided={decided}')

    def test_a_sale_holding_the_key_makes_the_decision_lose(self):
        results = self._run(self.sell, self.decide, pos_services, 'build_pos_sale')
        self.assertEqual((results['first'], results['second']), (201, 409), results)
        self._exactly_one()

    def test_a_decision_holding_the_key_makes_the_sale_lose(self):
        results = self._run(self.decide, self.sell, AdminAuditLog, 'log')
        self.assertEqual((results['first'], results['second']), (200, 409), results)
        self._exactly_one()
