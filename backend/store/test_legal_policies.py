"""
LEGAL-POLICIES-01 — the three legal documents of each company: who may edit them,
what the public reads, and that a draft is never published by accident.
"""
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from store.models import AdminAuditLog, CompanyLegalPolicy
from store.tests import _p2d_member, _p3_company, _storefront_of

PRIVACY = 'Política de privacidad\n\n# Responsable\nEmpresa A S.A.C., RUC 20111111111.\n\n- Contacto: privacidad@a.invalid'


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class LegalPolicyBase(TestCase):
    def setUp(self):
        cache.clear()
        self.a = _p3_company('legal-a', 'Tienda A', legal_name='A S.A.C.', tax_id='20111111111')
        self.b = _p3_company('legal-b', 'Tienda B', legal_name='B S.A.C.', tax_id='20222222222')
        self.manager, _ = _p2d_member(self.a, 'legal_manager', ['company.view', 'company.manage'])
        self.viewer, _ = _p2d_member(self.a, 'legal_viewer', ['company.view'])
        self.manager_b, _ = _p2d_member(self.b, 'legal_manager_b', ['company.view', 'company.manage'])

    def put(self, user, kind, text, query=''):
        return _client(user).put(f'/api/admin/legal-policies/{kind}/{query}', {'draft_text': text}, format='json')

    def publish(self, user, kind, query=''):
        return _client(user).post(f'/api/admin/legal-policies/{kind}/publish/{query}', format='json')

    def public(self, company, kind):
        with _storefront_of(company):
            return APIClient().get(f'/api/storefront/legal/{kind}/')


class EditorTest(LegalPolicyBase):
    def test_a_manager_saves_a_draft_and_reads_it_back(self):
        res = self.put(self.manager, 'privacy', PRIVACY)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data['draft_text'], PRIVACY)
        self.assertFalse(res.data['is_published'])
        listed = _client(self.manager).get('/api/admin/legal-policies/').data
        self.assertTrue(listed['can_manage'])
        self.assertEqual([p['kind'] for p in listed['policies']], ['privacy', 'cookies', 'whatsapp'])
        self.assertEqual(listed['policies'][0]['draft_text'], PRIVACY)
        self.assertEqual(listed['policies'][1]['draft_text'], '')

    def test_a_viewer_reads_but_never_writes(self):
        self.put(self.manager, 'privacy', PRIVACY)
        listed = _client(self.viewer).get('/api/admin/legal-policies/')
        self.assertEqual(listed.status_code, 200)
        self.assertFalse(listed.data['can_manage'])
        self.assertEqual(self.put(self.viewer, 'privacy', 'Otro texto').status_code, 403)
        self.assertEqual(self.publish(self.viewer, 'privacy').status_code, 403)
        self.assertEqual(CompanyLegalPolicy.objects.get(company=self.a, kind='privacy').draft_text, PRIVACY)
        self.assertEqual(_client(self.viewer).post('/api/admin/legal-policies/privacy/unpublish/').status_code, 403)

    def test_anonymous_cannot_read_or_write(self):
        self.assertIn(APIClient().get('/api/admin/legal-policies/').status_code, (401, 403))
        self.assertIn(APIClient().put('/api/admin/legal-policies/privacy/', {'draft_text': 'x'},
                                      format='json').status_code, (401, 403))
        self.assertFalse(CompanyLegalPolicy.objects.exists())

    def test_another_company_is_out_of_reach(self):
        self.assertEqual(self.put(self.manager, 'privacy', 'Intruso', f'?company={self.b.pk}').status_code, 404)
        self.assertEqual(self.publish(self.manager, 'privacy', f'?company={self.b.pk}').status_code, 404)
        self.put(self.manager_b, 'privacy', 'Texto de B')
        listed = _client(self.manager).get('/api/admin/legal-policies/').data
        self.assertEqual(listed['policies'][0]['draft_text'], '')
        self.assertFalse(CompanyLegalPolicy.objects.filter(company=self.b, draft_text='Intruso').exists())

    def test_an_unknown_document_is_refused(self):
        self.assertEqual(self.put(self.manager, 'terms', 'x').status_code, 404)
        self.assertEqual(self.publish(self.manager, 'terms').status_code, 404)

    def test_length_and_control_characters_are_validated(self):
        too_long = self.put(self.manager, 'cookies', 'a' * (CompanyLegalPolicy.MAX_LENGTH + 1))
        self.assertEqual(too_long.status_code, 400)
        self.assertIn('text', too_long.data['errors'])
        self.assertEqual(self.put(self.manager, 'cookies', 'hola\x00').status_code, 400)
        self.assertEqual(self.put(self.manager, 'cookies', {'html': 1}).status_code, 400)
        self.assertEqual(_client(self.manager).put('/api/admin/legal-policies/cookies/', {},
                                                   format='json').status_code, 400)
        self.assertFalse(CompanyLegalPolicy.objects.filter(company=self.a, kind='cookies').exclude(
            draft_text='').exists())

    def test_saving_and_publishing_are_audited_without_the_text(self):
        self.put(self.manager, 'privacy', PRIVACY)
        self.publish(self.manager, 'privacy')
        logs = AdminAuditLog.objects.filter(company=self.a, target_type='company_legal_policy').order_by('pk')
        self.assertEqual([log.action for log in logs], ['legal_policy_draft_saved', 'legal_policy_published'])
        for log in logs:
            self.assertEqual(log.actor, self.manager)
            self.assertEqual(log.metadata['kind'], 'privacy')
            self.assertNotIn('20111111111', str(log.metadata))

    def test_a_branch_scoped_manager_cannot_write_company_wide_text(self):
        branch = self.a.default_inventory_branch
        scoped, _ = _p2d_member(self.a, 'legal_scoped', ['company.view', 'company.manage'], branches=[branch])
        self.assertEqual(self.put(scoped, 'privacy', PRIVACY).status_code, 403)


class PublicationTest(LegalPolicyBase):
    def test_a_draft_is_not_public_until_published(self):
        self.put(self.manager, 'privacy', PRIVACY)
        self.assertEqual(self.public(self.a, 'privacy').status_code, 404)
        res = self.publish(self.manager, 'privacy')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.data['is_published'])
        public = self.public(self.a, 'privacy')
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.data['text'], PRIVACY)
        self.assertIn('Host', public['Vary'])

    def test_editing_after_publishing_keeps_the_published_text(self):
        self.put(self.manager, 'privacy', PRIVACY)
        self.publish(self.manager, 'privacy')
        res = self.put(self.manager, 'privacy', PRIVACY + '\n\nBorrador nuevo.')
        self.assertTrue(res.data['has_unpublished_changes'])
        cache.clear()
        self.assertEqual(self.public(self.a, 'privacy').data['text'], PRIVACY)

    def test_each_storefront_reads_its_own_company(self):
        self.put(self.manager, 'whatsapp', 'WhatsApp de A')
        self.publish(self.manager, 'whatsapp')
        self.assertEqual(self.public(self.a, 'whatsapp').data['text'], 'WhatsApp de A')
        cache.clear()
        self.assertEqual(self.public(self.b, 'whatsapp').status_code, 404)

    def test_the_public_answer_carries_nothing_internal(self):
        self.put(self.manager, 'cookies', 'Cookies de A')
        self.publish(self.manager, 'cookies')
        public = self.public(self.a, 'cookies')
        self.assertEqual(set(public.data), {'kind', 'text', 'published_at'})

    def test_empty_or_placeholder_drafts_are_never_published(self):
        self.assertEqual(self.publish(self.manager, 'privacy').status_code, 400)
        self.put(self.manager, 'privacy', 'Responsable: [RAZÓN SOCIAL], RUC [RUC]. Correo: [POR CONFIRMAR]')
        res = self.publish(self.manager, 'privacy')
        self.assertEqual(res.status_code, 400)
        self.assertIn('[RUC]', str(res.data))
        self.assertEqual(self.public(self.a, 'privacy').status_code, 404)

    def test_html_is_stored_as_text_not_as_markup(self):
        hostile = '<script>alert(1)</script>\n<img src=x onerror=alert(2)>'
        self.put(self.manager, 'privacy', hostile)
        self.publish(self.manager, 'privacy')
        # Stored and served verbatim: the storefront renders it as escaped text, never as HTML.
        self.assertEqual(self.public(self.a, 'privacy').data['text'], hostile)

    def test_unpublishing_withdraws_the_page_and_keeps_the_draft(self):
        self.put(self.manager, 'privacy', PRIVACY)
        self.publish(self.manager, 'privacy')
        res = _client(self.manager).post('/api/admin/legal-policies/privacy/unpublish/')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data['is_published'])
        self.assertEqual(res.data['draft_text'], PRIVACY)
        cache.clear()
        self.assertEqual(self.public(self.a, 'privacy').status_code, 404)

    def test_unknown_kind_and_inactive_company_answer_404(self):
        self.assertEqual(self.public(self.a, 'terms').status_code, 404)
        self.put(self.manager, 'privacy', PRIVACY)
        self.publish(self.manager, 'privacy')
        self.a.is_active = False
        self.a.save(update_fields=['is_active'])
        cache.clear()
        self.assertEqual(self.public(self.a, 'privacy').status_code, 404)
