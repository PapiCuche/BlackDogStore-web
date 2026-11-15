"""Privacy policy configuration is tenant-owned, public only as explicitly selected text."""
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from .models import CompanySettings
from .serializers import CompanySettingsSerializer
from .settings_views import build_storefront_config_payload


class PrivacyPolicySettingsTest(SimpleTestCase):
    def test_editable_text_accepts_plain_content_and_rejects_oversize(self):
        settings = CompanySettings()
        serializer = CompanySettingsSerializer(settings, data={
            'privacy_policy_text': 'Datos de contacto y mensajes de WhatsApp.'
        }, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['privacy_policy_text'],
                         'Datos de contacto y mensajes de WhatsApp.')

        oversized = CompanySettingsSerializer(settings, data={
            'privacy_policy_text': 'a' * 12001,
        }, partial=True)
        self.assertFalse(oversized.is_valid())
        self.assertIn('privacy_policy_text', oversized.errors)

    def test_public_config_uses_only_current_tenants_policy(self):
        company = SimpleNamespace(slug='example-tenant')
        identity = SimpleNamespace(
            name='Ejemplo', legal_name='', tax_id='', contact_email='',
            phone='', whatsapp_number='', whatsapp_link='', website_url='',
            legal_address='', city='', warranty_policy_text='',
            warranty_policy_url='', logo_url='',
        )
        branding = SimpleNamespace(
            logo_url='', logos={}, colors={}, light_colors={},
            css_variables=lambda: {},
        )
        settings = SimpleNamespace(
            privacy_policy_text='Contenido propio de esta empresa',
            privacy_url='', terms_url='', facebook_url='', instagram_url='',
        )
        with (
            patch('store.settings_views.company_identity', return_value=identity),
            patch('store.settings_views.company_branding', return_value=branding),
            patch('store.settings_views.get_company_settings', return_value=settings),
            patch('store.settings_views.public_page_settings', return_value={}),
            patch('store.settings_views.public_campaigns', return_value={}),
            patch('store.settings_views.public_list_content', return_value={}),
        ):
            payload = build_storefront_config_payload(company)
        self.assertEqual(payload['policies']['privacy_text'],
                         'Contenido propio de esta empresa')
        self.assertNotIn('order_notification_email', str(payload))
