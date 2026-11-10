"""
CATALOG-VARIANT-IMAGES — images per variant, and the model's cover, from «Modelos y variantes».

What is fixed here:

  * each variant row of the admin model says which picture it shows (`image_url`, the primary mirror) and how
    many it has (`image_count`), without one query per picture;
  * the model's cover (`ProductModel.image_url`) follows the same rule as every other storefront slot: an
    uploaded image is placed only if it exists and is the company's own (another company's answers like a
    missing one), and a replaced uploaded cover is released — deleted only when nothing else shows it;
  * an external address keeps working as before.
"""
import os
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from store import device_catalog, product_media
from store.models import AdminAuditLog, ProductImage, ProductModel, StorefrontImage
from store.test_device_commerce import DeviceShop, _client
from store.test_storefront_media import jpeg, png_with_transparency
from store.tests import _p2d_member, _p3_company

_ROOT = tempfile.mkdtemp(prefix='variant-images-')

EXTERNAL = 'https://cdn.example.com/modelos/telefono-x.png'


def tearDownModule():
    shutil.rmtree(_ROOT, ignore_errors=True)


@override_settings(EVIDENCE_STORAGE_BACKEND='filesystem', EVIDENCE_STORAGE_ROOT=_ROOT,
                   ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class VariantImagesBase(DeviceShop, TestCase):
    def setUp(self):
        self.build('cvi-tienda')
        self.api = _client(self.staff)

    def add(self, product, content=None, name='foto.png', content_type='image/png'):
        uploaded = SimpleUploadedFile(name, content or png_with_transparency(), content_type=content_type)
        return product_media.add_image(product=product, actor=self.staff, uploaded=uploaded)

    def detail(self, api=None):
        res = (api or self.api).get(f'/api/admin/catalog/models/{self.model.pk}/')
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def rows(self):
        return {row['product_id']: row for row in self.detail()['variants']}

    def patch(self, body, api=None):
        with self.captureOnCommitCallbacks(execute=True):
            return (api or self.api).patch(f'/api/admin/catalog/models/{self.model.pk}/', body, format='json')


class VariantRowsCarryTheirImagesTest(VariantImagesBase):
    def test_each_row_has_its_primary_address_and_its_count(self):
        first = self.add(self.new128)
        self.add(self.new128, jpeg(), 'b.jpg', 'image/jpeg')
        rows = self.rows()
        self.assertEqual(rows[self.new128.pk]['image_count'], 2)
        self.assertEqual(rows[self.new128.pk]['image_url'], first.image_url)
        # The other variants keep what they had: nothing.
        self.assertEqual(rows[self.new256.pk]['image_count'], 0)
        self.assertEqual(rows[self.new256.pk]['image_url'], '')
        self.assertEqual(rows[self.used128.pk]['image_count'], 0)

    def test_the_count_of_queries_does_not_grow_with_the_pictures(self):
        with CaptureQueriesContext(connection) as before:
            self.detail()
        for product in (self.new128, self.new256, self.used128):
            self.add(product)
            self.add(product, jpeg(), 'b.jpg', 'image/jpeg')
        with CaptureQueriesContext(connection) as after:
            self.detail()
        self.assertEqual(len(after.captured_queries), len(before.captured_queries))

    def test_the_list_of_models_is_unchanged(self):
        res = self.api.get('/api/admin/catalog/models/')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertNotIn('variants', res.json()['results'][0])


class ModelCoverTest(VariantImagesBase):
    def test_an_own_uploaded_image_becomes_the_cover(self):
        image = self.add(self.new128)
        res = self.patch({'image_url': image.image_url})
        self.assertEqual(res.status_code, 200, res.content)
        self.model.refresh_from_db()
        self.assertEqual(self.model.image_url, image.image_url)
        self.assertEqual(res.json()['image_url'], image.image_url)
        log = AdminAuditLog.objects.filter(action='product_model_updated', target_id=str(self.model.pk)).last()
        self.assertIsNotNone(log)
        self.assertEqual(log.company_id, self.company.pk)

    def test_a_cover_only_upload_counts_as_placed_for_the_sweep(self):
        from store import storefront_media
        cover = storefront_media.store(company=self.company, actor=self.staff, raw=png_with_transparency())
        self.assertIn(cover.pk, [image.pk for image in storefront_media.unplaced(older_than_hours=0)])
        self.assertEqual(self.patch({'image_url': f'/api/storefront/images/{cover.public_id}'}).status_code, 200)
        self.assertNotIn(cover.pk, [image.pk for image in storefront_media.unplaced(older_than_hours=0)])

    def test_another_companys_image_is_refused_like_a_missing_one(self):
        other = _p3_company('cvi-otra', 'Otra')
        stranger, _ = _p2d_member(other, 'cvi_ajeno', ['products.view', 'products.manage'])
        from store import storefront_media
        foreign = storefront_media.store(company=other, actor=stranger, raw=png_with_transparency())
        address = f'/api/storefront/images/{foreign.public_id}'
        self.patch({'image_url': EXTERNAL})

        res = self.patch({'image_url': address, 'name': 'Cambiado'})

        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json().get('field'), 'image_url')
        missing = self.patch({'image_url': '/api/storefront/images/' + 'f' * 32})
        self.assertEqual(missing.status_code, 400, missing.content)
        self.assertEqual(missing.json().get('field'), 'image_url')
        self.assertEqual(missing.json()['detail'], res.json()['detail'])
        self.model.refresh_from_db()
        self.assertEqual(self.model.image_url, EXTERNAL)
        self.assertEqual(self.model.name, 'Teléfono X')

    def test_creating_a_model_follows_the_same_rule(self):
        other = _p3_company('cvi-otra2', 'Otra 2')
        from store import storefront_media
        foreign = storefront_media.store(company=other, actor=None, raw=png_with_transparency())
        res = self.api.post('/api/admin/catalog/models/', {
            'name': 'Tableta Z', 'image_url': f'/api/storefront/images/{foreign.public_id}'}, format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json().get('field'), 'image_url')
        self.assertFalse(ProductModel.objects.filter(company=self.company, name='Tableta Z').exists())

        own = storefront_media.store(company=self.company, actor=self.staff, raw=png_with_transparency())
        res = self.api.post('/api/admin/catalog/models/', {
            'name': 'Tableta Z', 'image_url': f'/api/storefront/images/{own.public_id}'}, format='json')
        self.assertEqual(res.status_code, 201, res.content)

    def test_an_external_address_still_works(self):
        res = self.patch({'image_url': EXTERNAL})
        self.assertEqual(res.status_code, 200, res.content)
        self.model.refresh_from_db()
        self.assertEqual(self.model.image_url, EXTERNAL)

    def test_a_replaced_cover_is_released_but_kept_while_a_gallery_shows_it(self):
        from store import storefront_media
        gallery = self.add(self.new128)
        cover_only = storefront_media.store(company=self.company, actor=self.staff, raw=png_with_transparency())
        cover_address = f'/api/storefront/images/{cover_only.public_id}'
        key = os.path.join(_ROOT, cover_only.storage_key)
        self.assertEqual(self.patch({'image_url': cover_address}).status_code, 200)

        # Replaced by a gallery image: the old cover, shown nowhere else, goes — row and file.
        self.assertEqual(self.patch({'image_url': gallery.image_url}).status_code, 200)
        self.assertFalse(StorefrontImage.objects.filter(pk=cover_only.pk).exists())
        self.assertFalse(os.path.exists(key))
        self.assertTrue(AdminAuditLog.objects.filter(
            action='storefront_image_deleted', company=self.company, target_id=str(cover_only.pk)).exists())

        # Replaced by an external address: the gallery image stays, the variant still shows it.
        self.assertEqual(self.patch({'image_url': EXTERNAL}).status_code, 200)
        self.assertTrue(StorefrontImage.objects.filter(public_id=gallery.image_url.rsplit('/', 1)[-1]).exists())
        self.assertTrue(ProductImage.objects.filter(pk=gallery.pk).exists())

    def test_without_products_manage_the_cover_does_not_change(self):
        image = self.add(self.new128)
        viewer, _ = _p2d_member(self.company, 'cvi_lector', ['company.view', 'products.view'])
        self.assertEqual(self.patch({'image_url': image.image_url}, api=_client(viewer)).status_code, 403)
        self.model.refresh_from_db()
        self.assertEqual(self.model.image_url, '')

    def test_service_refuses_too(self):
        with self.assertRaises(device_catalog.CatalogError) as caught:
            device_catalog.update_model(self.model, image_url='/api/storefront/images/' + 'a' * 32)
        self.assertEqual(caught.exception.field, 'image_url')
