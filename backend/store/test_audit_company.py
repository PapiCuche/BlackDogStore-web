from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from store.models import AdminAuditLog, Category
from store.tests import _p2d_member, _p3_company, _prod


class CatalogAuditCompanyTest(TestCase):
    """
    AUDIT-07 — lo que una empresa hace con su catálogo queda en SU auditoría.

    El registro de auditoría de una empresa sólo enseña las filas que llevan esa
    empresa. Cambiar un producto (precio, nombre, publicación) y crear una
    categoría escribían la fila sin empresa: existía, pero ningún administrador
    del tenant podía verla. Un cambio de precio no dejaba rastro para quien
    tiene que responder de él.
    """

    def setUp(self):
        cache.clear()
        self.company = _p3_company('audit-cat', 'Empresa Auditoría')
        self.other = _p3_company('audit-cat-otra', 'Otra Empresa')
        self.manager, _ = _p2d_member(
            self.company, 'audit_cat_gestor',
            ['products.view', 'products.manage', 'memberships.view'])
        self.outsider, _ = _p2d_member(
            self.other, 'audit_cat_ajeno', ['memberships.view'])
        self.product = _prod(self.company, 'Cable USB-C', 'cable-audit', price='50.00')

    def _as(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def _audit_actions(self, user):
        res = self._as(user).get('/api/admin/audit-logs/')
        self.assertEqual(res.status_code, 200, res.data)
        rows = res.data['results'] if isinstance(res.data, dict) else res.data
        return [row['action'] for row in rows]

    def test_a_price_change_is_recorded_under_the_company(self):
        res = self._as(self.manager).patch(
            f'/api/admin/products/{self.product.pk}/', {'price': '75.00'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)

        row = AdminAuditLog.objects.get(action='product_updated', target_id=str(self.product.pk))
        self.assertEqual(row.company_id, self.company.pk)

    def test_the_company_sees_its_own_catalogue_changes(self):
        self._as(self.manager).patch(
            f'/api/admin/products/{self.product.pk}/', {'price': '75.00'}, format='json')
        self._as(self.manager).patch(
            f'/api/admin/products/{self.product.pk}/', {'is_active': False}, format='json')

        actions = self._audit_actions(self.manager)
        self.assertIn('product_updated', actions)
        self.assertIn('product_deactivated', actions)

    def test_a_new_category_is_recorded_under_the_company(self):
        res = self._as(self.manager).post(
            '/api/admin/categories/', {'name': 'Cables', 'slug': 'cables-audit'}, format='json')
        self.assertEqual(res.status_code, 201, res.data)

        category = Category.objects.get(company=self.company, slug='cables-audit')
        row = AdminAuditLog.objects.get(action='category_created', target_id=str(category.pk))
        self.assertEqual(row.company_id, self.company.pk)
        self.assertIn('category_created', self._audit_actions(self.manager))

    def test_another_company_does_not_see_them(self):
        self._as(self.manager).patch(
            f'/api/admin/products/{self.product.pk}/', {'price': '75.00'}, format='json')
        self._as(self.manager).post(
            '/api/admin/categories/', {'name': 'Cables', 'slug': 'cables-audit'}, format='json')

        actions = self._audit_actions(self.outsider)
        self.assertNotIn('product_updated', actions)
        self.assertNotIn('category_created', actions)
