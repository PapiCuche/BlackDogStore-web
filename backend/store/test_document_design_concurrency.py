"""
DESIGN-VERIFY — two people editing the document design at once.

    GET   /api/admin/document-design/   answers `version` and `ETag: "<version>"`
    PATCH /api/admin/document-design/   `If-Match: "<version>"` → applied, or 409

Before this, the last save won silently: an administrator who had the panel
open saved over what another one had saved meanwhile. Now the panel sends the
version it loaded; if the saved design is another one, the PATCH is a 409
(`design_conflict`, a message in Spanish, and the current design with its
version) that writes and audits nothing, and the panel reloads it keeping its
own changes on top. Without `If-Match` a PATCH is applied as before.
"""
from rest_framework.test import APIClient

from store import document_design
from store.document_design import DEFAULT
from store.models import AdminAuditLog, CompanyDocumentDesign
from store.test_document_design_settings import URL, DesignBase

CONFLICT_CODE = 'design_conflict'


# ---------------------------------------------------------------------------
# Optimistic concurrency
# ---------------------------------------------------------------------------

class ConcurrencyTest(DesignBase):
    def setUp(self):
        super().setUp()
        from store.models import Membership
        from store.tests import _assign, _m7_user, _role

        self.other_admin = _m7_user('segundo-admin-dv')
        membership = Membership.objects.create(user=self.other_admin, company=self.company, role='inventory')
        _assign(membership, _role(self.company, 'Admin DV', capabilities=['company.manage'], slug=None))
        self.second = APIClient()
        self.second.force_authenticate(user=self.other_admin)

    def read(self, client=None):
        response = (client or self.api).get(URL)
        self.assertEqual(response.status_code, 200)
        return response

    def patch_as(self, client, version, **body):
        headers = {} if version is None else {'HTTP_IF_MATCH': f'"{version}"'}
        return client.patch(URL, body, format='json', **headers)

    def test_the_design_carries_its_version_and_an_etag(self):
        response = self.read()
        version = response.json()['version']
        self.assertIsInstance(version, str)
        self.assertTrue(version)
        self.assertEqual(response['ETag'], f'"{version}"')

    def test_a_save_with_the_current_version_is_applied_and_moves_the_version(self):
        version = self.read().json()['version']
        response = self.patch_as(self.api, version, footer_text='Pie con versión.')
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertNotEqual(response.json()['version'], version)
        self.assertEqual(response['ETag'], f'"{response.json()["version"]}"')
        self.assertEqual(self.read().json()['version'], response.json()['version'])
        # And the version moves again with the next save.
        again = self.patch_as(self.api, response.json()['version'], footer_text='Otro pie.')
        self.assertEqual(again.status_code, 200)
        self.assertNotEqual(again.json()['version'], response.json()['version'])

    def test_two_admins_editing_at_once_the_second_save_is_a_409_that_writes_nothing(self):
        mine = self.read().json()['version']
        theirs = self.read(self.second).json()['version']
        self.assertEqual(mine, theirs)

        first = self.patch_as(self.second, theirs, footer_text='Pie del segundo admin.')
        self.assertEqual(first.status_code, 200)
        audits = AdminAuditLog.objects.filter(action='document_design_updated').count()

        late = self.patch_as(self.api, mine, footer_text='Pie del primer admin.', show_phone=False)
        self.assertEqual(late.status_code, 409, late.content[:300])
        body = late.json()
        self.assertEqual(body['code'], CONFLICT_CODE)
        self.assertIn('Otra persona guardó', body['detail'])
        # It answers the design as it is now, with its version: the panel reloads it.
        self.assertEqual(body['current']['design']['footer_text'], 'Pie del segundo admin.')
        self.assertEqual(body['current']['version'], first.json()['version'])
        # Nothing of the late save was written or audited.
        saved = document_design.design_for(self.company)
        self.assertEqual((saved.footer_text, saved.show_phone), ('Pie del segundo admin.', True))
        self.assertEqual(AdminAuditLog.objects.filter(action='document_design_updated').count(), audits)

        # Reloaded, the same change goes through.
        retry = self.patch_as(self.api, body['current']['version'], footer_text='Pie del primer admin.')
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(document_design.design_for(self.company).footer_text, 'Pie del primer admin.')

    def test_the_first_save_of_a_company_without_a_row_is_guarded_too(self):
        version = self.read().json()['version']
        self.assertFalse(CompanyDocumentDesign.objects.filter(company=self.company).exists())
        self.assertEqual(self.patch_as(self.second, version, footer_text='Primero.').status_code, 200)
        late = self.patch_as(self.api, version, footer_text='Segundo.')
        self.assertEqual(late.status_code, 409)
        self.assertEqual(document_design.design_for(self.company).footer_text, 'Primero.')

    def test_a_stale_version_is_refused_even_when_it_changes_a_different_field(self):
        version = self.read().json()['version']
        self.assertEqual(self.patch_as(self.second, version, show_website=True).status_code, 200)
        self.assertEqual(self.patch_as(self.api, version, logo_size='small').status_code, 409)
        self.assertEqual(document_design.design_for(self.company).logo_size, 'medium')

    def test_a_weak_or_unquoted_etag_is_understood_and_a_star_is_no_precondition(self):
        version = self.read().json()['version']
        response = self.api.patch(URL, {'footer_text': 'Débil.'}, format='json', HTTP_IF_MATCH=f'W/"{version}"')
        self.assertEqual(response.status_code, 200)
        version = response.json()['version']
        response = self.api.patch(URL, {'footer_text': 'Sin comillas.'}, format='json', HTTP_IF_MATCH=version)
        self.assertEqual(response.status_code, 200)
        response = self.api.patch(URL, {'footer_text': 'Cualquiera.'}, format='json', HTTP_IF_MATCH='*')
        self.assertEqual(response.status_code, 200)

    def test_without_if_match_the_patch_behaves_as_before(self):
        self.read()
        self.assertEqual(self.patch_as(self.second, None, footer_text='A.').status_code, 200)
        self.assertEqual(self.patch_as(self.api, None, footer_text='B.').status_code, 200)
        self.assertEqual(document_design.design_for(self.company).footer_text, 'B.')

    def test_validation_comes_before_the_version_and_a_conflict_needs_company_manage(self):
        version = self.read().json()['version']
        self.assertEqual(self.patch_as(self.second, version, footer_text='Antes.').status_code, 200)
        # A wrong value is a 400 whatever the version.
        self.assertEqual(self.patch_as(self.api, version, logo_size='enorme').status_code, 400)
        # Without company.manage the precondition does not even reveal the design.
        self.only('company.view')
        refused = self.patch_as(self.api, version, footer_text='Después.')
        self.assertEqual(refused.status_code, 403)
        self.assertNotIn('current', refused.json())

    def test_another_company_never_answers_a_conflict_with_its_design(self):
        other = self.other
        CompanyDocumentDesign.objects.create(company=other, footer_text='Pie de la otra empresa.')
        response = self.api.patch(f'{URL}?company={other.pk}', {'footer_text': 'X.'}, format='json',
                                  HTTP_IF_MATCH='"0"')
        self.assertEqual(response.status_code, 404)
        self.assertNotIn('Pie de la otra empresa', response.content.decode())

    def test_the_version_of_the_pure_function(self):
        self.assertEqual(document_design.saved(self.company), (DEFAULT, document_design.NO_ROW_VERSION))
        with self.assertRaises(document_design.DesignConflict):
            document_design.update(self.company, {'footer_text': 'X.'}, actor=self.staff, expected_version='1')
        design = document_design.update(self.company, {'footer_text': 'X.'}, actor=self.staff,
                                        expected_version=document_design.NO_ROW_VERSION)
        saved, version = document_design.saved(self.company)
        self.assertEqual(saved, design)
        self.assertNotEqual(version, document_design.NO_ROW_VERSION)
