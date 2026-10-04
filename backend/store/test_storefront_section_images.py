from django.test import TestCase

from store.models import StorefrontPageSettings
from store.storefront_content_services import public_page_settings
from store.tests import _p3_company


class StorefrontSectionImageSettingsTest(TestCase):
    """V4 editorial images are tenant content, not compiled pilot assets."""

    def setUp(self):
        self.company = _p3_company('section-images', 'Tienda Imágenes')

    def test_empty_store_has_no_section_images(self):
        page = public_page_settings(self.company)
        self.assertEqual(page['services_image_url'], '')
        self.assertEqual(page['location_image_url'], '')

    def test_public_page_exposes_only_this_tenants_section_images(self):
        StorefrontPageSettings.objects.create(
            company=self.company,
            services_image_url='/api/storefront/images/' + ('a' * 32),
            location_image_url='/api/storefront/images/' + ('b' * 32),
        )
        page = public_page_settings(self.company)

        self.assertEqual(
            page['services_image_url'],
            '/api/storefront/images/' + ('a' * 32),
        )
        self.assertEqual(
            page['location_image_url'],
            '/api/storefront/images/' + ('b' * 32),
        )
