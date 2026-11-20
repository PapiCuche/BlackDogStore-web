"""
CATALOG-REFERENCE-01 · PR C — the gallery of a real finish (model + colour), and where each picture comes from.

What is fixed here:

  * `ModelFinishImage` belongs to one company, names a colour option (role `color`) of that company and, when the
    model has a palette, a colour of that palette; one primary per (model, colour);
  * the provenance of a picture lives on its `StorefrontImage` (source kind, licence, attribution, verification),
    and a finish image cannot be uploaded without saying it (licence text unless it is the shop's own photo);
  * the reference sweep counts a finish image like any other slot: a picture only a finish shows is never deleted;
  * the admin API is the product gallery's: `products.view` reads, `products.manage` writes, another company 404;
  * the public model page shows each variant its own pictures, else its colour's, else nothing and
    `image_pending` — never another colour's photo and never the model's cover;
  * the F4 report counts finishes without pictures and uploads whose licence is unknown or unverified.
"""
import shutil
import tempfile

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from store import device_catalog, model_palette, storefront_media
from store.models import ModelFinishImage, ProductModel, StorefrontImage, VariantAttribute
from store.test_device_commerce import DeviceShop, _client
from store.test_storefront_media import jpeg, png_with_transparency
from store.tests import _p2d_member, _p3_company

_ROOT = tempfile.mkdtemp(prefix='finish-images-')


def tearDownModule():
    shutil.rmtree(_ROOT, ignore_errors=True)


@override_settings(EVIDENCE_STORAGE_BACKEND='filesystem', EVIDENCE_STORAGE_ROOT=_ROOT,
                   ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class FinishShop(DeviceShop, TestCase):
    """`DeviceShop` with its colour attribute carrying the `color` role (what a finish is made of)."""

    def setUp(self):
        self.build('pc-acabados')
        VariantAttribute.objects.filter(pk=self.colour.pk).update(role='color')
        VariantAttribute.objects.filter(pk=self.capacity.pk).update(role='capacity')
        self.colour.refresh_from_db()
        self.api = _client(self.staff)
        self.q = f'?company={self.company.pk}'

    def stored(self, company=None, raw=None):
        return storefront_media.store(company=company or self.company, actor=self.staff,
                                      raw=raw or png_with_transparency())

    def place(self, colour, *, model=None, primary=False, order=0, stored=None):
        stored = stored or self.stored()
        return ModelFinishImage.objects.create(
            company=self.company, model=model or self.model, color_option=colour, image_url=stored.url,
            width=stored.width, height=stored.height, sort_order=order, is_primary=primary,
        )


class FinishImageModelTest(FinishShop):
    def test_a_finish_image_names_a_model_and_one_of_its_colours(self):
        image = self.place(self.blue, primary=True)
        self.assertEqual(image.color_option, self.blue)
        self.assertEqual(list(self.model.finish_images.all()), [image])

    def test_the_option_must_be_a_colour(self):
        with self.assertRaises(ValidationError):
            self.place(self.gb128)

    def test_another_companys_colour_is_refused(self):
        other = _p3_company('pc-otra', 'Otra')
        colour = device_catalog.create_attribute(company=other, name='Color', kind='color', role='color')
        foreign = device_catalog.create_option(company=other, attribute_id=colour.pk, label='Verde',
                                               swatch_hex='#00AA00')
        with self.assertRaises(ValidationError):
            self.place(foreign)

    def test_another_companys_model_is_refused(self):
        other = _p3_company('pc-otra-modelo', 'Otra')
        foreign_model = device_catalog.create_model(company=other, name='Ajeno')
        with self.assertRaises(ValidationError):
            self.place(self.blue, model=foreign_model)

    def test_with_a_palette_the_colour_must_be_in_it(self):
        model_palette.set_palette(self.model, option_ids=[self.gb128.pk, self.black.pk], actor=self.staff)
        with self.assertRaises(ValidationError):
            self.place(self.blue)
        self.place(self.black)

    def test_a_colour_of_another_models_palette_is_refused(self):
        second = device_catalog.create_model(company=self.company, name='Teléfono Y')
        model_palette.set_palette(second, option_ids=[self.blue.pk], actor=self.staff)
        model_palette.set_palette(self.model, option_ids=[self.black.pk], actor=self.staff)
        with self.assertRaises(ValidationError):
            self.place(self.blue)

    def test_one_primary_per_model_and_colour(self):
        self.place(self.blue, primary=True)
        self.place(self.black, primary=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.place(self.blue, primary=True)


class ProvenanceFieldsTest(FinishShop):
    def test_an_upload_without_provenance_is_unknown(self):
        image = self.stored()
        self.assertEqual(image.source_kind, StorefrontImage.SOURCE_UNKNOWN)
        self.assertEqual((image.source_url, image.license, image.attribution), ('', '', ''))
        self.assertIsNone(image.license_verified_by)
        self.assertIsNone(image.license_verified_at)

    def test_the_closed_vocabulary(self):
        self.assertEqual(
            [kind for kind, _label in StorefrontImage.SOURCE_KIND_CHOICES],
            ['own_photo', 'supplier_authorized', 'licensed_stock', 'demo', 'unknown'],
        )


class FinishImageSweepTest(FinishShop):
    """A picture only a finish shows is in use: the sweep never deletes it."""

    def test_the_sweep_knows_the_field(self):
        self.assertIn((ModelFinishImage, 'image_url'), storefront_media.reference_fields())

    def test_an_image_only_a_finish_shows_is_kept(self):
        stored = self.stored()
        self.assertIn(stored.pk, [image.pk for image in storefront_media.unplaced(older_than_hours=0)])
        self.place(self.blue, stored=stored)
        self.assertEqual(storefront_media.reference_count(stored.public_id), 1)
        self.assertNotIn(stored.pk, [image.pk for image in storefront_media.unplaced(older_than_hours=0)])
        with self.captureOnCommitCallbacks(execute=True):
            deleted = storefront_media.release([stored.url], company=self.company, actor=self.staff)
        self.assertEqual(deleted, 0)
        self.assertTrue(StorefrontImage.objects.filter(pk=stored.pk).exists())


OWN = {'source_kind': 'own_photo'}
SUPPLIER = {'source_kind': 'supplier_authorized', 'license': 'Autorización escrita del distribuidor, 2026-11',
            'source_url': 'https://distribuidor.example/kit-prensa', 'attribution': 'Distribuidor S.A.'}


class FinishApiBase(FinishShop):
    def url(self, suffix='', model=None):
        return f'/api/admin/catalog/models/{(model or self.model).pk}/finish-images/{suffix}{self.q}'

    def upload(self, colour, provenance=None, api=None, raw=None, alt_text='', model=None, name='foto.png'):
        body = {'file': SimpleUploadedFile(name, raw or png_with_transparency(), content_type='image/png'),
                'color_option_id': colour.pk, 'alt_text': alt_text, **(OWN if provenance is None else provenance)}
        with self.captureOnCommitCallbacks(execute=True):
            return (api or self.api).post(self.url(model=model), body, format='multipart')

    def uploaded(self, colour, provenance=None, **kwargs):
        res = self.upload(colour, provenance, **kwargs)
        self.assertEqual(res.status_code, 201, res.content)
        return res.json()

    def listing(self, api=None):
        res = (api or self.api).get(self.url())
        self.assertEqual(res.status_code, 200, res.content)
        return {row['option']['id']: row for row in res.json()['finishes']}


class FinishUploadTest(FinishApiBase):
    def test_the_first_picture_of_a_finish_is_its_primary_and_carries_its_provenance(self):
        first = self.uploaded(self.blue, SUPPLIER, alt_text='Teléfono X azul, de frente')
        second = self.uploaded(self.blue, OWN, raw=jpeg(), name='b.jpg')
        self.assertTrue(first['is_primary'])
        self.assertFalse(second['is_primary'])
        self.assertEqual(first['alt_text'], 'Teléfono X azul, de frente')
        self.assertEqual(first['color_option_id'], self.blue.pk)
        self.assertEqual(first['source']['kind'], 'supplier_authorized')
        self.assertEqual(first['source']['license'], SUPPLIER['license'])
        self.assertEqual(first['source']['url'], SUPPLIER['source_url'])
        self.assertEqual(first['source']['attribution'], SUPPLIER['attribution'])
        self.assertFalse(first['source']['license_verified'])
        stored = StorefrontImage.objects.get(public_id=first['url'].rsplit('/', 1)[-1])
        self.assertEqual((stored.source_kind, stored.license), ('supplier_authorized', SUPPLIER['license']))
        self.assertEqual(ModelFinishImage.objects.filter(model=self.model, color_option=self.blue).count(), 2)
        from store.models import AdminAuditLog
        log = AdminAuditLog.objects.filter(action='finish_image_added').last()
        self.assertEqual(log.company_id, self.company.pk)
        self.assertEqual(log.metadata['source_kind'], 'supplier_authorized')

    def test_the_source_kind_is_required(self):
        res = self.upload(self.blue, {})
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['field'], 'source_kind')
        for kind in ('unknown', 'apple', ''):
            with self.subTest(kind):
                self.assertEqual(self.upload(self.blue, {'source_kind': kind}).status_code, 400)
        self.assertFalse(ModelFinishImage.objects.exists())
        # Nothing was left behind: a refused upload never stores the file.
        self.assertFalse(StorefrontImage.objects.filter(company=self.company).exists())

    def test_a_licence_is_required_unless_it_is_an_own_photo(self):
        for kind in ('supplier_authorized', 'licensed_stock', 'demo'):
            with self.subTest(kind):
                res = self.upload(self.blue, {'source_kind': kind, 'license': '   '})
                self.assertEqual(res.status_code, 400, res.content)
                self.assertEqual(res.json()['field'], 'license')
        self.uploaded(self.blue, OWN)

    def test_the_source_address_is_a_web_page_never_one_of_ours(self):
        for url in ('javascript:alert(1)', 'ftp://x.example/a.png', '/api/storefront/images/' + 'a' * 32,
                    'https://tienda.example/api/storefront/images/' + 'b' * 32):
            with self.subTest(url):
                res = self.upload(self.blue, {**SUPPLIER, 'source_url': url})
                self.assertEqual(res.status_code, 400, res.content)
                self.assertEqual(res.json()['field'], 'source_url')

    def test_a_demo_resource_is_flagged(self):
        demo = self.uploaded(self.black, {'source_kind': 'demo', 'license': 'Recurso de demostración'})
        self.assertTrue(demo['source']['is_demo'])
        self.assertFalse(self.uploaded(self.black, OWN)['source']['is_demo'])

    def test_the_option_must_be_a_colour_of_the_model(self):
        res = self.upload(self.gb128)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'not_a_colour')
        model_palette.set_palette(self.model, option_ids=[self.black.pk], actor=self.staff)
        res = self.upload(self.blue)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'not_in_palette')
        self.assertFalse(StorefrontImage.objects.filter(company=self.company).exists())
        self.uploaded(self.black)

    def test_a_colour_from_another_company_answers_like_a_missing_one(self):
        other = _p3_company('pc-api-otra', 'Otra')
        colour = device_catalog.create_attribute(company=other, name='Color', kind='color', role='color')
        foreign = device_catalog.create_option(company=other, attribute_id=colour.pk, label='Verde',
                                               swatch_hex='#00AA00')
        res = self.upload(foreign)
        self.assertEqual(res.status_code, 404, res.content)
        res = self.api.post(self.url(), {'file': SimpleUploadedFile('f.png', png_with_transparency()),
                                         'color_option_id': 'x', **OWN}, format='multipart')
        self.assertEqual(res.status_code, 400, res.content)

    def test_a_finish_admits_a_bounded_number_of_pictures(self):
        with self.settings(FINISH_IMAGE_MAX_PER_COLOUR=2):
            self.uploaded(self.blue)
            self.uploaded(self.blue)
            self.assertEqual(self.upload(self.blue).status_code, 400)
            self.uploaded(self.black)


class FinishListingTest(FinishApiBase):
    def test_every_colour_of_the_model_is_listed_with_its_pictures_or_pending(self):
        self.uploaded(self.blue)
        rows = self.listing()
        # Colours of its variants (no palette yet): black (new) and blue (used).
        self.assertEqual(set(rows), {self.black.pk, self.blue.pk})
        self.assertFalse(rows[self.blue.pk]['image_pending'])
        self.assertEqual(len(rows[self.blue.pk]['images']), 1)
        self.assertTrue(rows[self.black.pk]['image_pending'])
        self.assertEqual(rows[self.black.pk]['images'], [])

    def test_with_a_palette_its_colours_are_listed_too(self):
        red = device_catalog.create_option(company=self.company, attribute_id=self.colour.pk, label='Rojo',
                                           swatch_hex='#B91C1C')
        model_palette.set_palette(self.model, option_ids=[self.gb128.pk, self.black.pk, self.blue.pk, red.pk],
                                  actor=self.staff)
        self.assertEqual(set(self.listing()), {self.black.pk, self.blue.pk, red.pk})


class FinishEditTest(FinishApiBase):
    def patch(self, image_id, body):
        with self.captureOnCommitCallbacks(execute=True):
            return self.api.patch(self.url(f'{image_id}/'), body, format='json')

    def test_alt_text_and_primary(self):
        first = self.uploaded(self.blue)
        second = self.uploaded(self.blue)
        res = self.patch(second['id'], {'is_primary': True, 'alt_text': 'De lado'})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.json()['is_primary'])
        self.assertEqual(res.json()['alt_text'], 'De lado')
        self.assertFalse(ModelFinishImage.objects.get(pk=first['id']).is_primary)
        self.assertEqual(self.patch(second['id'], {'is_primary': False}).status_code, 400)
        self.assertEqual(self.patch(second['id'], {'is_primary': 'si'}).status_code, 400)

    def test_verifying_a_licence_names_who_and_when(self):
        image = self.uploaded(self.blue, SUPPLIER)
        res = self.patch(image['id'], {'license_verified': True})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.json()['source']['license_verified'])
        stored = StorefrontImage.objects.get(public_id=image['url'].rsplit('/', 1)[-1])
        self.assertEqual(stored.license_verified_by, self.staff)
        self.assertIsNotNone(stored.license_verified_at)
        # Changing what the licence says clears the check: it has to be verified again.
        res = self.patch(image['id'], {'license': 'Otra licencia'})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(res.json()['source']['license_verified'])
        res = self.patch(image['id'], {'license': ''})
        self.assertEqual(res.status_code, 400, res.content)

    def test_reorder_and_remove(self):
        first = self.uploaded(self.blue)
        second = self.uploaded(self.blue)
        third = self.uploaded(self.blue)
        with self.captureOnCommitCallbacks(execute=True):
            res = self.api.post(self.url('order/'), {'color_option_id': self.blue.pk,
                                                     'order': [third['id'], first['id'], second['id']]},
                                format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual([image['id'] for image in self.listing()[self.blue.pk]['images']],
                         [third['id'], first['id'], second['id']])
        bad = self.api.post(self.url('order/'), {'color_option_id': self.blue.pk, 'order': [first['id']]},
                            format='json')
        self.assertEqual(bad.status_code, 400)

        public_id = first['url'].rsplit('/', 1)[-1]
        with self.captureOnCommitCallbacks(execute=True):
            res = self.api.delete(self.url(f'{first["id"]}/'))
        self.assertEqual(res.status_code, 204, res.content)
        # The primary went: the next one in order takes its place, and the file nobody shows is deleted.
        rows = self.listing()[self.blue.pk]['images']
        self.assertEqual([image['id'] for image in rows], [third['id'], second['id']])
        self.assertTrue(rows[0]['is_primary'])
        self.assertFalse(StorefrontImage.objects.filter(public_id=public_id).exists())
        self.assertEqual(self.api.delete(self.url(f'{first["id"]}/')).status_code, 404)


class FinishPermissionsTest(FinishApiBase):
    def test_view_reads_and_manage_writes(self):
        viewer, _ = _p2d_member(self.company, 'pc_viewer', ['company.view', 'products.view'])
        nobody, _ = _p2d_member(self.company, 'pc_nobody', ['company.view'])
        image = self.uploaded(self.blue)
        self.listing(api=_client(viewer))
        self.assertEqual(self.upload(self.blue, api=_client(viewer)).status_code, 403)
        self.assertEqual(_client(viewer).delete(self.url(f'{image["id"]}/')).status_code, 403)
        self.assertEqual(_client(nobody).get(self.url()).status_code, 403)
        self.assertIn(_client().get(self.url()).status_code, (401, 403))

    def test_another_companys_model_answers_404(self):
        other = _p3_company('pc-perm-otra', 'Otra')
        foreign_model = device_catalog.create_model(company=other, name='Ajeno')
        self.assertEqual(self.api.get(self.url(model=foreign_model)).status_code, 404)
        self.assertEqual(self.upload(self.blue, model=foreign_model).status_code, 404)

    def test_an_image_of_another_model_answers_404(self):
        image = self.uploaded(self.blue)
        second = device_catalog.create_model(company=self.company, name='Teléfono Y')
        self.assertEqual(self.api.delete(self.url(f'{image["id"]}/', model=second)).status_code, 404)
        self.assertEqual(self.api.patch(self.url(f'{image["id"]}/', model=second), {'alt_text': 'x'},
                                        format='json').status_code, 404)
        self.assertTrue(ModelFinishImage.objects.filter(pk=image['id']).exists())


class PublicFinishGalleryTest(FinishApiBase):
    """`GET /api/catalog/models/<slug>/`: variant → its own pictures → its colour's → nothing and `image_pending`."""

    def setUp(self):
        super().setUp()
        self.new128blue = self.variant('Teléfono X 128 GB Azul', 'tx-128-azul', '3100.00', [self.gb128, self.blue])
        self.public = _client(host=self.host)

    def detail(self):
        res = self.public.get(f'/api/catalog/models/{self.model.slug}/')
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def variants(self):
        return {row['product_id']: row for row in self.detail()['variants']}

    def test_each_colour_shows_its_own_finish(self):
        black = self.uploaded(self.black, alt_text='Negro de frente')
        blue = self.uploaded(self.blue, raw=jpeg(), name='azul.jpg')
        rows = self.variants()
        for product in (self.new128, self.new256):
            self.assertEqual([image['url'] for image in rows[product.pk]['gallery']], [black['url']])
            self.assertFalse(rows[product.pk]['image_pending'])
        self.assertEqual([image['url'] for image in rows[self.new128blue.pk]['gallery']], [blue['url']])
        self.assertEqual(rows[self.new128.pk]['gallery'][0]['alt_text'], 'Negro de frente')
        # Without its own text, the picture is described by the model and the colour.
        self.assertEqual(rows[self.new128blue.pk]['gallery'][0]['alt_text'], 'Teléfono X Azul')

    def test_a_finish_without_pictures_is_pending_never_another_colours_photo(self):
        black = self.uploaded(self.black)
        row = self.variants()[self.new128blue.pk]
        self.assertEqual(row['gallery'], [])
        self.assertTrue(row['image_pending'])
        self.assertNotIn(black['url'], str(row))

    def test_the_models_cover_is_never_a_variants_gallery(self):
        cover = self.stored()
        ProductModel.objects.filter(pk=self.model.pk).update(image_url=cover.url)
        body = self.detail()
        self.assertEqual(body['image_url'], cover.url)   # the cover stays, for lists
        for row in body['variants']:
            self.assertEqual(row['gallery'], [])
            self.assertTrue(row['image_pending'])

    def test_a_variants_own_pictures_come_first(self):
        from store import product_media
        self.uploaded(self.black)
        own = product_media.add_image(product=self.new128, actor=self.staff, uploaded=SimpleUploadedFile(
            'propia.png', png_with_transparency(), content_type='image/png'))
        rows = self.variants()
        self.assertEqual([image['url'] for image in rows[self.new128.pk]['gallery']], [own.image_url])
        self.assertNotEqual(rows[self.new256.pk]['gallery'][0]['url'], own.image_url)

    def test_a_variants_own_single_address_is_its_own_picture(self):
        # A variant whose own address was written before galleries (no ProductImage) keeps showing it.
        from store.models import Product
        Product.objects.filter(pk=self.new256.pk).update(image_url='https://cdn.example.com/propia-256.png')
        self.uploaded(self.black)
        row = self.variants()[self.new256.pk]
        self.assertEqual([image['url'] for image in row['gallery']], ['https://cdn.example.com/propia-256.png'])

    def test_the_finish_map_is_an_allowlist_with_only_the_demo_flag(self):
        self.uploaded(self.black, SUPPLIER)
        self.uploaded(self.blue, {'source_kind': 'demo', 'license': 'Recurso de demostración interno'})
        body = self.detail()
        finishes = body['finish_images']
        self.assertEqual(set(finishes), {str(self.black.pk), str(self.blue.pk)})
        self.assertEqual(sorted(finishes[str(self.black.pk)][0]),
                         ['alt_text', 'height', 'is_demo', 'is_primary', 'url', 'width'])
        self.assertFalse(finishes[str(self.black.pk)][0]['is_demo'])
        self.assertTrue(finishes[str(self.blue.pk)][0]['is_demo'])
        self.assertTrue(self.variants()[self.new128blue.pk]['gallery'][0]['is_demo'])
        text = str(body)
        for leak in (SUPPLIER['license'], SUPPLIER['source_url'], SUPPLIER['attribution'], 'supplier_authorized',
                     'Recurso de demostración interno', 'source_kind', 'license'):
            self.assertNotIn(leak, text)

    def test_a_colour_no_published_variant_uses_is_not_public(self):
        red = device_catalog.create_option(company=self.company, attribute_id=self.colour.pk, label='Rojo',
                                           swatch_hex='#B91C1C')
        self.uploaded(red)
        self.assertNotIn(str(red.pk), self.detail()['finish_images'])

    def test_the_count_of_queries_does_not_grow_with_the_pictures(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.uploaded(self.black)
        with CaptureQueriesContext(connection) as before:
            self.detail()
        for _ in range(3):
            self.uploaded(self.black)
            self.uploaded(self.blue)
        with CaptureQueriesContext(connection) as after:
            self.detail()
        self.assertEqual(len(after.captured_queries), len(before.captured_queries))


class LicensingReportTest(FinishApiBase):
    """F4 («Imágenes por reemplazar»), read-only: finishes without pictures and uploads without a checked licence."""

    def report(self, api=None):
        res = (api or self.api).get(f'/api/admin/catalog/image-sources/{self.q}')
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()['licensing']

    def test_finishes_without_pictures_are_counted_and_named(self):
        self.uploaded(self.black)
        data = self.report()
        self.assertEqual(data['finishes_without_images'], 1)
        self.assertEqual([(row['model_id'], row['option_id'], row['option_label']) for row in data['pending_finishes']],
                         [(self.model.pk, self.blue.pk, 'Azul')])
        red = device_catalog.create_option(company=self.company, attribute_id=self.colour.pk, label='Rojo',
                                           swatch_hex='#B91C1C')
        model_palette.set_palette(self.model, option_ids=[self.gb128.pk, self.black.pk, self.blue.pk, red.pk],
                                  actor=self.staff)
        self.assertEqual(self.report()['finishes_without_images'], 2)

    def test_uploads_with_unknown_or_unverified_licence(self):
        from store import product_media
        product_media.add_image(product=self.new128, actor=self.staff, uploaded=SimpleUploadedFile(
            'sin-origen.png', png_with_transparency(), content_type='image/png'))
        supplier = self.uploaded(self.black, SUPPLIER)
        self.uploaded(self.blue, {'source_kind': 'demo', 'license': 'Recurso de demostración'})
        self.uploaded(self.blue, OWN)
        self.stored()   # uploaded, shown nowhere in the catalogue: not counted
        data = self.report()['uploads']
        self.assertEqual(data, {'total': 4, 'unknown_source': 1, 'unverified_license': 2, 'demo': 1})
        with self.captureOnCommitCallbacks(execute=True):
            self.api.patch(self.url(f'{supplier["id"]}/'), {'license_verified': True}, format='json')
        self.assertEqual(self.report()['uploads']['unverified_license'], 1)

    def test_another_companys_uploads_are_never_counted(self):
        other = _p3_company('pc-lic-otra', 'Otra')
        stranger, _ = _p2d_member(other, 'pc_lic_ajeno', ['company.view', 'products.view', 'products.manage'])
        foreign_model = device_catalog.create_model(company=other, name='Ajeno')
        stored = storefront_media.store(company=other, actor=stranger, raw=png_with_transparency())
        ProductModel.objects.filter(pk=foreign_model.pk).update(image_url=stored.url)
        self.assertEqual(self.report()['uploads']['total'], 0)

    def test_the_count_of_queries_does_not_grow(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.uploaded(self.black, SUPPLIER)
        with CaptureQueriesContext(connection) as before:
            self.report()
        for _ in range(3):
            self.uploaded(self.blue, SUPPLIER)
        device_catalog.create_model(company=self.company, name='Teléfono Z')
        with CaptureQueriesContext(connection) as after:
            self.report()
        self.assertEqual(len(after.captured_queries), len(before.captured_queries))


class NoVocabularyInFinishMediaTest(TestCase):
    """AGENTS §2 / ADR §2: PR C's engine names no maker, model or colour (the same guard as the importer's)."""

    def test_the_service_views_and_model_name_nothing(self):
        import inspect
        import re
        from pathlib import Path

        from store import finish_media, finish_media_views
        from store import test_catalog_reference_import as guard

        words = guard.NoVocabularyInTheEngineTest.WORDS
        strip = guard.NoVocabularyInTheEngineTest.code
        sources = {module.__name__: Path(module.__file__).read_text(encoding='utf-8')
                   for module in (finish_media, finish_media_views)}
        sources['ModelFinishImage'] = inspect.getsource(ModelFinishImage)
        for name, source in sources.items():
            found = re.search(words, strip(None, source), re.I)
            self.assertIsNone(found, (name, found))
