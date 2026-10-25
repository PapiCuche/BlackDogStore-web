"""
AUTH-C (owner, 2026-10-25, point 6) — a purchase that began signed in never
becomes a guest's by itself.

The checkout page sends `signed_in: true` when the buyer opened it signed in
and did not choose to buy as a guest. If the request then arrives with no
session (its cookies were cleared by another answer in the meantime), the
server refuses — 401 `session_expired` — instead of placing a stranger's order.
A guest purchase is only ever the buyer's explicit choice (`signed_in` absent).
"""
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from store.models import Order

URL = '/api/payments/create-checkout-session/'


class SignedInPurchaseTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_a_signed_in_purchase_without_a_session_is_refused_not_placed_as_a_guest(self):
        before = Order.objects.count()

        res = APIClient().post(URL, {'signed_in': True, 'session_key': 'x' * 32}, format='json')

        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json().get('code'), 'session_expired')
        self.assertEqual(Order.objects.count(), before)

    def test_a_guest_purchase_is_not_refused_for_the_session(self):
        res = APIClient().post(URL, {'session_key': 'x' * 32}, format='json')

        self.assertNotEqual(res.status_code, 401)

    def test_a_signed_in_purchase_with_its_session_goes_on(self):
        User.objects.create_user('compradora', 'c@example.test', 'ValidPass123!')
        client = APIClient()
        client.post('/api/auth/login/', {'username': 'compradora', 'password': 'ValidPass123!'}, format='json')

        res = client.post(URL, {'signed_in': True, 'session_key': 'x' * 32}, format='json')

        self.assertNotEqual(res.status_code, 401)
