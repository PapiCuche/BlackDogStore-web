"""
INV-GRADES-01 — the company edits its cosmetic grade scale from the panel, without rewriting history.

`PATCH /api/admin/catalog/grades/<id>/` (`products.manage`): description, order and «active» always; the NAME only
while no device uses the grade (a device's grade is a foreign key: renaming a used grade would rewrite how sold and
received devices read). An inactive grade is never offered to a new reception and stays on the devices that have it.
"""
from django.test import TestCase

from store import stock_unit_services as units
from store.models import AdminAuditLog, CosmeticGrade
from store.test_device_commerce import DeviceShop, _client
from store.tests import _p2d_member, _p3_company


class GradeEditTest(DeviceShop, TestCase):
    def setUp(self):
        self.build('grades-edit')
        self.api = _client(self.staff)
        self.unused = CosmeticGrade.objects.get(company=self.company, code='muy-bueno') \
            if CosmeticGrade.objects.filter(company=self.company, code='muy-bueno').exists() \
            else CosmeticGrade.objects.filter(company=self.company).exclude(pk=self.grade.pk).first()

    def url(self, grade):
        return f'/api/admin/catalog/grades/{grade.pk}/'

    def test_an_unused_grade_can_be_renamed_described_and_reordered(self):
        res = self.api.patch(self.url(self.unused), {'label': '  Impecable de vitrina ', 'description': 'Sin marcas', 'sort_order': 9},
                             format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.unused.refresh_from_db()
        self.assertEqual((self.unused.label, self.unused.description, self.unused.sort_order), ('Impecable de vitrina', 'Sin marcas', 9))
        self.assertTrue(AdminAuditLog.objects.filter(action='cosmetic_grade_updated', target_id=str(self.unused.pk)).exists())

    def test_a_used_grade_keeps_its_name_but_its_description_and_state_change(self):
        self.receive(self.used128, cosmetic_grade=self.grade.pk)
        res = self.api.patch(self.url(self.grade), {'label': 'Otro nombre'}, format='json')
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'grade_in_use')
        self.grade.refresh_from_db()
        self.assertNotEqual(self.grade.label, 'Otro nombre')
        res = self.api.patch(self.url(self.grade), {'description': 'Detalle nuevo', 'is_active': False}, format='json')
        self.assertEqual(res.status_code, 200, res.content)

    def test_an_inactive_grade_is_refused_for_new_devices_and_kept_on_existing_ones(self):
        [unit] = self.receive(self.used128, cosmetic_grade=self.grade.pk)
        self.api.patch(self.url(self.grade), {'is_active': False}, format='json')
        unit.refresh_from_db()
        self.assertEqual(unit.cosmetic_grade_id, self.grade.pk)
        with self.assertRaises(units.StockUnitError):
            self.receive(self.used128, cosmetic_grade=self.grade.pk)

    def test_a_name_another_grade_has_is_refused(self):
        res = self.api.patch(self.url(self.unused), {'label': self.grade.label.upper()}, format='json')
        self.assertEqual(res.status_code, 400, res.content)

    def test_a_name_that_reads_as_another_grade_is_refused(self):
        # Review: `clean_grade` reads a typed grade by id, code, then name — these would pick ANOTHER grade.
        for label in ('éxcelente', self.grade.code, '3', '  42  '):
            with self.subTest(label=label):
                self.assertEqual(self.api.patch(self.url(self.unused), {'label': label}, format='json').status_code, 400)
                self.assertEqual(self.api.post('/api/admin/catalog/grades/', {'label': label}, format='json').status_code, 400)

    def test_bad_bodies_are_400(self):
        for body in ({'label': ''}, {'sort_order': -1}, {'sort_order': 'x'}, {'is_active': 'si'}, {'code': 'otro'}):
            with self.subTest(body=body):
                self.assertEqual(self.api.patch(self.url(self.unused), body, format='json').status_code, 400)

    def test_another_company_and_users_without_manage_cannot_edit(self):
        other = _p3_company('grades-edit-b', 'Otra')
        stranger, _ = _p2d_member(other, 'grades_stranger', ['company.view', 'products.view', 'products.manage'])
        self.assertEqual(_client(stranger).patch(self.url(self.unused), {'label': 'X'}, format='json').status_code, 404)
        viewer, _ = _p2d_member(self.company, 'grades_viewer', ['company.view', 'products.view'])
        self.assertEqual(_client(viewer).patch(self.url(self.unused), {'label': 'X'}, format='json').status_code, 403)
