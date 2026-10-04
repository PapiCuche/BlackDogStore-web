from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from store.models import Membership
from store.tests import _p2d_member, _p3_company


ENDPOINT = '/api/admin/memberships/'


class DirectMembershipConsentBoundaryTest(TestCase):
    """
    F-TENANT-01 — tenant admins invite people; they do not claim global users.

    The staff UI already uses StaffInvitation and acceptance creates the
    membership. Direct numeric-id enrollment is retained only for the platform
    operator as a bootstrap/migration escape hatch.
    """

    def setUp(self):
        cache.clear()
        self.company = _p3_company('consent-company', 'Empresa Consentimiento')
        self.company_admin, _ = _p2d_member(
            self.company,
            'consent_company_admin',
            ['memberships.view', 'memberships.manage'],
        )
        self.target = User.objects.create_user(
            username='global_target',
            email='global-target@example.invalid',
            password='TargetPass123!',
        )
        self.platform_admin = User.objects.create_superuser(
            username='platform_direct_membership',
            email='platform@example.invalid',
            password='PlatformPass123!',
        )

    def _payload(self):
        return {
            'user': self.target.pk,
            'company': self.company.pk,
            'role': 'technician',
            'branch_access_mode': 'all',
            'is_active': True,
        }

    @staticmethod
    def _as(user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_company_admin_cannot_attach_an_arbitrary_platform_user(self):
        response = self._as(self.company_admin).post(
            ENDPOINT, self._payload(), format='json',
        )

        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(
            Membership.objects.filter(user=self.target, company=self.company).exists()
        )
        self.assertNotIn(self.target.username, str(response.data))

    def test_platform_admin_keeps_direct_bootstrap_path(self):
        response = self._as(self.platform_admin).post(
            ENDPOINT, self._payload(), format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        membership = Membership.objects.get(user=self.target, company=self.company)
        self.assertEqual(membership.role, 'technician')
        self.assertEqual(response.data['username'], self.target.username)
