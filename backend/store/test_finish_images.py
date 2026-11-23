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
import atexit
import shutil
import tempfile

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from store import device_catalog, model_palette, storefront_media
from store.finish_media import listing as finish_media_listing
from store.models import ModelFinishImage, Product, ProductModel, StorefrontImage, VariantAttribute, VariantOption
from store.test_device_commerce import DeviceShop, _client
from store.test_storefront_media import jpeg, png_with_transparency
from store.tests import _p2d_member, _p3_company

_ROOT = tempfile.mkdtemp(prefix='finish-images-')


# The parallel runner forks its workers after this module is imported, so they all share _ROOT. A tearDownModule
# would run in each worker and delete files the others still use; only the process that created it removes it,
# at exit (forked workers leave without running atexit). Guarded by test_parallel_tempdir_guard.
atexit.register(shutil.rmtree, _ROOT, True)


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

    def verify(self, image, model=None):
        """Records that someone checked the picture's licence (the panel's checkbox)."""
        with self.captureOnCommitCallbacks(execute=True):
            res = self.api.patch(self.url(f'{image["id"]}/', model=model), {'license_verified': True}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def published(self, colour, provenance=None, **kwargs):
        """An uploaded picture whose licence was verified: the only kind the shop shows (owner, 2026-11-23)."""
        return self.verify(self.uploaded(colour, provenance, **kwargs), model=kwargs.get('model'))

    def make_primary(self, image):
        with self.captureOnCommitCallbacks(execute=True):
            res = self.api.patch(self.url(f'{image["id"]}/'), {'is_primary': True}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
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


class CartPictureTest(FinishApiBase):
    """G (owner's path «photo per colour → cart»): a cart line of a NEW variant shows the picture the model page opens
    on — its own gallery's primary (else its first, else its own address), else its colour's finish's primary among the
    PUBLIC pictures (licence verified; else the first of them), else the pending marker — never another colour's photo
    and never the model's cover on a model with colours; a demo resource is said (`image_is_demo`, «Imagen referencial»
    like the page). One rule, batched for the whole cart: `device_catalog.variant_pictures`. Read through the cart
    endpoint. Owner, 2026-11-23: the cart and the checkout show the PRIMARY photo of the chosen colour."""

    SESSION = 'carrito-g'

    def setUp(self):
        super().setUp()
        self.new128blue = self.variant('Teléfono X 128 GB Azul', 'tx-128-azul', '3100.00', [self.gb128, self.blue])
        ProductModel.objects.filter(pk=self.model.pk).update(image_url='/api/storefront/images/' + 'c' * 32)
        self.model.refresh_from_db()
        self.public = _client(host=self.host)

    def add(self, product, **extra):
        res = self.public.post('/api/cart/add/', {'session_key': self.SESSION, 'product': product.pk, **extra},
                               format='json')
        self.assertIn(res.status_code, (200, 201), res.content)

    def cart(self):
        res = self.public.get(f'/api/cart/?session_key={self.SESSION}')
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        rows = body['results'] if isinstance(body, dict) else body
        return {row['product']['id']: row['product'] for row in rows}

    def line(self, product):
        self.receive(product)
        self.add(product)
        return self.cart()[product.pk]

    def page_gallery(self, product):
        rows = {row['product_id']: row for row in self.public.get(f'/api/catalog/models/{self.model.slug}/').json()['variants']}
        return rows[product.pk]['gallery']

    def gallery_primary(self, product):
        # The photo the page opens on: the configurator's `gallery.findIndex(image => image.is_primary)`, else the first
        # (`frontend/app/lib/model-configurator.ts`).
        gallery = self.page_gallery(product)
        primary = next((image for image in gallery if image['is_primary']), gallery[0] if gallery else None)
        return primary['url'] if primary else ''

    def own_gallery(self, product, count=2):
        from store import product_media

        return [product_media.add_image(product=product, actor=self.staff, uploaded=SimpleUploadedFile(
            f'propia-{n}.png', png_with_transparency(), content_type='image/png')) for n in range(count)]

    def make_own_primary(self, image):
        from store.models import ProductImage

        ProductImage.objects.filter(product_id=image.product_id).update(is_primary=False)
        ProductImage.objects.filter(pk=image.pk).update(is_primary=True)

    def test_a_variant_without_its_own_picture_shows_its_colours_primary_finish(self):
        first = self.published(self.blue, alt_text='Azul de frente')
        self.published(self.blue, alt_text='Azul dorso')
        black = self.published(self.black)
        line = self.line(self.new128blue)
        self.assertEqual((line['image_url'], line['image_pending'], line['image_is_demo']), (first['url'], False, False))
        self.assertEqual(line['image_url'], self.gallery_primary(self.new128blue))
        self.assertNotEqual(line['image_url'], black['url'])

    def test_the_finishs_primary_even_when_it_is_not_the_first(self):
        first = self.published(self.blue, alt_text='Azul de frente')
        second = self.make_primary(self.published(self.blue, alt_text='Azul dorso'))
        self.assertEqual(self.page_gallery(self.new128blue)[0]['url'], first['url'])   # still first in the gallery
        line = self.line(self.new128blue)
        self.assertEqual((line['image_url'], line['image_pending']), (second['url'], False))
        self.assertEqual(line['image_url'], self.gallery_primary(self.new128blue))

    def test_its_own_gallerys_primary_even_when_it_is_not_the_first(self):
        self.published(self.blue)
        first, second = self.own_gallery(self.new128blue)
        self.make_own_primary(second)
        self.assertEqual(self.page_gallery(self.new128blue)[0]['url'], first.image_url)
        line = self.line(self.new128blue)
        self.assertEqual((line['image_url'], line['image_pending']), (second.image_url, False))
        self.assertEqual(line['image_url'], self.gallery_primary(self.new128blue))

    def test_an_unverified_primary_gives_way_to_the_first_public_picture(self):
        public = self.published(self.blue)
        hidden = self.make_primary(self.uploaded(self.blue, SUPPLIER))
        self.assertFalse(hidden['source']['license_verified'])
        line = self.line(self.new128blue)
        self.assertEqual((line['image_url'], line['image_pending']), (public['url'], False))
        self.assertEqual(line['image_url'], self.gallery_primary(self.new128blue))
        self.assertNotIn(hidden['url'], str(self.page_gallery(self.new128blue)))

    def test_a_finish_without_a_verified_licence_is_pending(self):
        self.uploaded(self.blue)
        self.uploaded(self.blue, SUPPLIER)
        line = self.line(self.new128blue)
        self.assertEqual((line['image_url'], line['image_pending'], line['image_is_demo']), ('', True, False))
        self.assertEqual(self.gallery_primary(self.new128blue), '')

    def test_a_colour_without_pictures_is_pending_never_the_cover_nor_another_colour(self):
        self.published(self.black)
        line = self.line(self.new128blue)
        self.assertEqual((line['image_url'], line['image_pending']), ('', True))
        self.assertEqual(self.gallery_primary(self.new128blue), '')

    def test_its_own_picture_comes_first(self):
        self.published(self.blue)
        Product.objects.filter(pk=self.new128blue.pk).update(image_url='/api/storefront/images/' + 'd' * 32)
        line = self.line(Product.objects.get(pk=self.new128blue.pk))
        self.assertEqual((line['image_url'], line['image_pending']), ('/api/storefront/images/' + 'd' * 32, False))

    def test_a_demo_finish_is_shown_and_said_like_on_the_model_page(self):
        demo = self.published(self.blue, {'source_kind': 'demo', 'license': 'Recurso de demostración interno'})
        line = self.line(self.new128blue)
        self.assertEqual((line['image_url'], line['image_pending'], line['image_is_demo']), (demo['url'], False, True))
        page = self.page_gallery(self.new128blue)[0]
        self.assertEqual((page['url'], page['is_demo']), (demo['url'], True))

    def test_another_companys_picture_is_never_public_even_verified(self):
        from django.utils import timezone

        other = _p3_company('pc-carrito-otra', 'Otra')
        foreign = self.stored(company=other)
        StorefrontImage.objects.filter(pk=foreign.pk).update(source_kind=StorefrontImage.SOURCE_OWN_PHOTO,
                                                             license_verified_at=timezone.now())
        self.place(self.blue, primary=True, stored=foreign)
        line = self.line(self.new128blue)
        self.assertEqual((line['image_url'], line['image_pending']), ('', True))
        self.assertEqual(self.gallery_primary(self.new128blue), '')

    def test_a_colourless_model_shows_its_cover(self):
        plain = device_catalog.create_model(company=self.company, name='Cargador Z')
        ProductModel.objects.filter(pk=plain.pk).update(image_url='/api/storefront/images/' + 'f' * 32)
        product = self.variant('Cargador Z 20 W', 'cz-20', '90.00', [self.gb128])
        Product.objects.filter(pk=product.pk).update(product_model=plain)
        product.refresh_from_db()
        line = self.line(product)
        self.assertEqual((line['image_url'], line['image_pending']), ('/api/storefront/images/' + 'f' * 32, False))

    def test_an_on_order_line_shows_its_finish_too(self):
        from store import supply_services as supply
        from datetime import timedelta
        from django.utils import timezone

        first = self.published(self.blue)
        supplier = supply.create_supplier(company=self.company, name='Mayorista G')
        Product.objects.filter(pk=self.new128blue.pk).update(sale_mode=Product.SALE_MODE_STOCK_OR_ORDER)
        supply.create_offer(company=self.company, supplier_id=supplier.pk, product_id=self.new128blue.pk,
                            actor=self.staff, may_view_costs=True, agreed_cost='2000.00', commercial_price='3300.00',
                            confirmed_quantity=3, lead_time_days=4, valid_from=timezone.localdate() - timedelta(days=1),
                            valid_until=timezone.localdate() + timedelta(days=5))
        self.add(Product.objects.get(pk=self.new128blue.pk), on_order=True)
        line = self.cart()[self.new128blue.pk]
        self.assertEqual((line['image_url'], line['image_pending']), (first['url'], False))

    def test_used_accessory_and_plain_lines_are_unchanged(self):
        from store.tests import _prod

        accessory = _prod(self.company, 'Funda', 'funda-g', price='50.00', inventory=5)
        Product.objects.filter(pk=accessory.pk).update(image_url='/api/storefront/images/' + 'a' * 32)
        self.add(accessory)
        line = self.cart()[accessory.pk]
        self.assertEqual(line['image_url'], '/api/storefront/images/' + 'a' * 32)
        self.assertNotIn('image_pending', line)
        self.assertNotIn('image_is_demo', line)

    def test_every_new_variant_agrees_with_the_model_page(self):
        self.published(self.black)
        self.make_primary(self.published(self.black))
        self.uploaded(self.blue)                                 # not public: blue stays pending
        _first, second = self.own_gallery(self.new256)
        self.make_own_primary(second)
        pictures = device_catalog.variant_pictures([self.new128, self.new256, self.new128blue])
        for product in (self.new128, self.new256, self.new128blue):
            self.assertEqual(pictures[product.pk][0], self.gallery_primary(product), product.name)
        self.assertEqual(pictures[self.new256.pk][0], second.image_url)
        self.assertEqual(pictures[self.new128blue.pk], ('', True, False))

    def test_the_cart_reads_the_pictures_once_for_all_its_lines(self):
        from unittest import mock

        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.published(self.blue)
        self.uploaded(self.blue)
        self.published(self.black)
        self.own_gallery(self.new256)
        for product in (self.new128, self.new256, self.new128blue):
            self.receive(product)
            self.add(product)
        with mock.patch('store.device_catalog.variant_pictures', wraps=device_catalog.variant_pictures) as batch:
            self.cart()
        self.assertEqual(batch.call_count, 1)
        self.assertEqual(sorted(p.pk for p in batch.call_args.args[0]),
                         sorted(p.pk for p in (self.new128, self.new256, self.new128blue)))
        # A bounded number of queries, whatever the number of lines.
        products = [Product.objects.get(pk=p.pk) for p in (self.new128, self.new256, self.new128blue)]
        with CaptureQueriesContext(connection) as one:
            device_catalog.variant_pictures(products[:1])
        with CaptureQueriesContext(connection) as three:
            device_catalog.variant_pictures(products)
        self.assertEqual(len(three), len(one))
        self.assertLessEqual(len(three), 6)


class ColourChipTest(FinishApiBase):
    """G (owner's visual order 4): imported colours must not all look the same grey.

    A colour chip (public configurator, till, admin palette) draws, in this order: a thumbnail of its finish's first
    AUTHORIZED picture (never a demo resource) → the company's own swatch → nothing (a name-only chip). The importer's
    neutral default on a colour of the reference catalogue (`reference_key` set) is «not configured» and is never sent
    as a colour; a swatch the company sets is. No migration: the rule is derived (`device_catalog.swatch_is_set`)."""

    def setUp(self):
        super().setUp()
        self.red = device_catalog.create_option(company=self.company, attribute_id=self.colour.pk, label='Rojo',
                                                swatch_hex='#9CA3AF')
        VariantOption.objects.filter(pk=self.red.pk).update(reference_key='color:red')
        self.red.refresh_from_db()
        self.new128blue = self.variant('Teléfono X 128 GB Azul', 'tx-128-azul', '3100.00', [self.gb128, self.blue])
        self.new128red = self.variant('Teléfono X 128 GB Rojo', 'tx-128-rojo', '3100.00', [self.gb128, self.red])
        self.public = _client(host=self.host)
        self.till, _ = _p2d_member(self.company, 'pc-acabados-caja', ['company.view', 'sales.pos.use'])

    def public_swatches(self):
        body = self.public.get(f'/api/catalog/models/{self.model.slug}/').json()
        colour = next(a for a in body['attributes'] if a['role'] == 'color')
        return {o['label']: o['swatch_hex'] for o in colour['options']}

    def till_options(self):
        url = (f'/api/admin/pos/models/{self.model.pk}/configurations/'
               f'?company={self.company.pk}&branch={self.branch.pk}')
        res = _client(self.till).get(url)
        self.assertEqual(res.status_code, 200, res.content)
        colour = next(a for a in res.json()['attributes'] if a['role'] == 'color')
        return {o['label']: o for o in colour['options']}

    def palette_options(self):
        rows = model_palette.payload(self.model)['attributes']
        colour = next(a for a in rows if a['role'] == 'color')
        return {o['label']: o for o in colour['options']}

    def test_the_importers_neutral_default_is_not_a_colour_anywhere(self):
        self.assertFalse(device_catalog.swatch_is_set(self.red))
        self.assertTrue(device_catalog.swatch_is_set(self.black))
        self.assertEqual(self.public_swatches(), {'Negro': '#111111', 'Azul': '#1D4ED8', 'Rojo': ''})
        self.assertEqual(self.till_options()['Rojo']['swatch_hex'], '')
        self.assertEqual(self.till_options()['Azul']['swatch_hex'], '#1D4ED8')
        self.assertEqual((self.palette_options()['Rojo']['swatch_set'], self.palette_options()['Azul']['swatch_set']),
                         (False, True))
        listed = {row['option']['label']: row['option'] for row in finish_media_listing(self.model)['finishes']}
        self.assertEqual((listed['Rojo']['swatch_set'], listed['Azul']['swatch_set']), (False, True))

    def test_a_swatch_the_company_sets_on_an_imported_colour_is_shown(self):
        device_catalog.update_option(self.red, swatch_hex='#B91C1C')
        self.red.refresh_from_db()
        self.assertTrue(device_catalog.swatch_is_set(self.red))
        self.assertEqual(self.public_swatches()['Rojo'], '#B91C1C')
        self.assertEqual(self.till_options()['Rojo']['swatch_hex'], '#B91C1C')
        self.assertTrue(self.palette_options()['Rojo']['swatch_set'])

    def test_the_neutral_grey_is_unset_whatever_made_the_colour(self):
        # Decision (lead, review of #377): the neutral placeholder counts as «not configured» for EVERY colour —
        # one made by hand, one a supplier list created (`catalog_proposal`), one imported.
        grey = device_catalog.create_option(company=self.company, attribute_id=self.colour.pk, label='Gris',
                                            swatch_hex='#9CA3AF')
        self.assertFalse(device_catalog.swatch_is_set(grey))
        from store import catalog_proposal

        self.assertEqual(catalog_proposal.PLACEHOLDER_SWATCH, device_catalog.NEUTRAL_SWATCH)
        listed = device_catalog.create_option(company=self.company, attribute_id=self.colour.pk, label='Verde lista',
                                              swatch_hex=catalog_proposal.PLACEHOLDER_SWATCH)
        self.assertFalse(device_catalog.swatch_is_set(listed))
        self.assertTrue(device_catalog.swatch_is_set(self.black))

    def test_the_chip_picture_is_the_first_authorized_finish_never_a_demo(self):
        self.uploaded(self.blue, {'source_kind': 'demo', 'license': 'Recurso de demostración interno'})
        # Verified: since 2026-11-23 the till shows only those (`test_the_till_shows_only_finish_pictures_…`).
        own_blue = self.published(self.blue, raw=jpeg(), name='azul.jpg')
        own_black = self.published(self.black, raw=jpeg(size=(130, 90)), name='negro.jpg')
        till = self.till_options()
        self.assertEqual((till['Azul']['thumbnail'], till['Negro']['thumbnail'], till['Rojo']['thumbnail']),
                         (own_blue['url'], own_black['url'], None))
        palette = self.palette_options()
        self.assertEqual((palette['Azul']['thumbnail'], palette['Rojo']['thumbnail']), (own_blue['url'], None))
        # Capacities carry no picture.
        self.assertNotIn('thumbnail', next(a for a in model_palette.payload(self.model)['attributes']
                                           if a['role'] == 'capacity')['options'][0])

    def test_the_till_shows_only_finish_pictures_whose_licence_is_verified(self):
        # Owner, 2026-11-23: «Tratar el POS como una pantalla potencialmente visible por clientes». The till's chips
        # follow the shop's licence gate; the admin palette, staff only, still shows every authorized picture.
        # Three different files: a licence is verified per file, and the same bytes are one file.
        waiting_blue = self.uploaded(self.blue)
        verified_black = self.published(self.black, raw=jpeg(), name='negro.jpg')
        till = self.till_options()
        self.assertEqual((till['Azul']['thumbnail'], till['Negro']['thumbnail']), (None, verified_black['url']))
        self.assertEqual(self.palette_options()['Azul']['thumbnail'], waiting_blue['url'])
        # A verified picture of the same finish is the till's chip while the primary still waits for its check.
        verified_blue = self.published(self.blue, raw=jpeg(size=(140, 90)), name='azul.jpg')
        self.assertTrue(waiting_blue['is_primary'])
        self.assertEqual(self.till_options()['Azul']['thumbnail'], verified_blue['url'])
        self.assertEqual(self.palette_options()['Azul']['thumbnail'], waiting_blue['url'])

    def test_the_till_never_shows_a_demo_or_another_companys_file_even_verified(self):
        # Review of #396: a VERIFIED demo is skipped by the demo rule (not by the licence one), and a verified file of
        # another company behind a finish address is not this company's picture.
        from django.utils import timezone

        self.published(self.blue, {'source_kind': 'demo', 'license': 'Recurso de demostración interno'})
        self.assertIsNone(self.till_options()['Azul']['thumbnail'])
        other = _p3_company('pc-chip-otra', 'Otra')
        foreign = self.stored(company=other, raw=jpeg(size=(150, 90)))
        StorefrontImage.objects.filter(pk=foreign.pk).update(license_verified_at=timezone.now())
        self.place(self.black, primary=True, stored=foreign)
        self.assertIsNone(self.till_options()['Negro']['thumbnail'])


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
        black = self.published(self.black, alt_text='Negro de frente')
        blue = self.published(self.blue, raw=jpeg(), name='azul.jpg')
        rows = self.variants()
        for product in (self.new128, self.new256):
            self.assertEqual([image['url'] for image in rows[product.pk]['gallery']], [black['url']])
            self.assertFalse(rows[product.pk]['image_pending'])
        self.assertEqual([image['url'] for image in rows[self.new128blue.pk]['gallery']], [blue['url']])
        self.assertEqual(rows[self.new128.pk]['gallery'][0]['alt_text'], 'Negro de frente')
        # Without its own text, the picture is described by the model and the colour.
        self.assertEqual(rows[self.new128blue.pk]['gallery'][0]['alt_text'], 'Teléfono X Azul')

    def test_a_finish_without_pictures_is_pending_never_another_colours_photo(self):
        black = self.published(self.black)
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
        self.published(self.black)
        own = product_media.add_image(product=self.new128, actor=self.staff, uploaded=SimpleUploadedFile(
            'propia.png', png_with_transparency(), content_type='image/png'))
        rows = self.variants()
        self.assertEqual([image['url'] for image in rows[self.new128.pk]['gallery']], [own.image_url])
        self.assertNotEqual(rows[self.new256.pk]['gallery'][0]['url'], own.image_url)

    def test_a_variants_own_single_address_is_its_own_picture(self):
        # A variant whose own address was written before galleries (no ProductImage) keeps showing it.
        from store.models import Product
        Product.objects.filter(pk=self.new256.pk).update(image_url='https://cdn.example.com/propia-256.png')
        self.published(self.black)
        row = self.variants()[self.new256.pk]
        self.assertEqual([image['url'] for image in row['gallery']], ['https://cdn.example.com/propia-256.png'])

    def test_the_finish_map_is_an_allowlist_with_only_the_demo_flag(self):
        self.published(self.black, SUPPLIER)
        self.published(self.blue, {'source_kind': 'demo', 'license': 'Recurso de demostración interno'})
        body = self.detail()
        finishes = body['finish_images']
        self.assertEqual(set(finishes), {str(self.black.pk), str(self.blue.pk)})
        self.assertEqual(sorted(finishes[str(self.black.pk)][0]),
                         ['alt_text', 'height', 'is_demo', 'is_primary', 'url', 'view', 'width'])
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
        self.published(red)
        self.assertNotIn(str(red.pk), self.detail()['finish_images'])

    def test_the_count_of_queries_does_not_grow_with_the_pictures(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.published(self.black)
        with CaptureQueriesContext(connection) as before:
            self.detail()
        for _ in range(3):
            self.published(self.black)
            self.published(self.blue)
            self.uploaded(self.blue, SUPPLIER)          # not public: read in the same query
        with CaptureQueriesContext(connection) as after:
            self.detail()
        self.assertEqual(len(after.captured_queries), len(before.captured_queries))


class PublicFinishLicenceTest(FinishApiBase):
    """Owner, 2026-11-23: «Las nuevas fotografías requieren procedencia y autorización comprobables antes de
    publicarse.» A finish picture is on the shop only when its `StorefrontImage` — of the SAME company, behind a managed
    address — has its licence verified (`license_verified_at`), whatever its source kind; a verified demo is shown and
    still said. Until then it stays in the panel, marked `public: false`, and the finish is `image_pending` on the shop.
    A variant's OWN pictures are not gated (they are audited, not hidden)."""

    def setUp(self):
        super().setUp()
        self.new128blue = self.variant('Teléfono X 128 GB Azul', 'tx-128-azul', '3100.00', [self.gb128, self.blue])
        self.public = _client(host=self.host)

    def detail(self):
        res = self.public.get(f'/api/catalog/models/{self.model.slug}/')
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def blue_row(self, body=None):
        return {row['product_id']: row for row in (body or self.detail())['variants']}[self.new128blue.pk]

    def assert_hidden(self, image):
        body = self.detail()
        self.assertNotIn(image['url'], str(body))
        self.assertNotIn(str(self.blue.pk), body['finish_images'])
        self.assertEqual((self.blue_row(body)['gallery'], self.blue_row(body)['image_pending']), ([], True))
        self.assertEqual(device_catalog.variant_picture(self.new128blue), ('', True, False))

    def panel_image(self, image):
        return next(row for row in self.listing()[self.blue.pk]['images'] if row['id'] == image['id'])

    def test_an_unverified_picture_of_any_source_is_not_public(self):
        for provenance in (OWN, SUPPLIER, {'source_kind': 'licensed_stock', 'license': 'Licencia de banco, 2026'},
                           {'source_kind': 'demo', 'license': 'Recurso de demostración interno'}):
            with self.subTest(provenance['source_kind']):
                image = self.uploaded(self.blue, provenance)
                self.assert_hidden(image)
                # It stays in the panel, said not to be public.
                self.assertIs(self.panel_image(image)['public'], False)
                self.assertTrue(self.listing()[self.blue.pk]['images'])
                with self.captureOnCommitCallbacks(execute=True):
                    self.api.delete(self.url(f'{image["id"]}/'))

    def test_verifying_publishes_it_and_unverifying_hides_it_again(self):
        image = self.uploaded(self.blue, SUPPLIER)
        self.assert_hidden(image)
        self.assertIs(self.verify(image)['public'], True)
        row = self.blue_row()
        self.assertEqual(([i['url'] for i in row['gallery']], row['image_pending']), ([image['url']], False))
        self.assertEqual(device_catalog.variant_picture(self.new128blue), (image['url'], False, False))
        self.assertIs(self.panel_image(image)['public'], True)
        with self.captureOnCommitCallbacks(execute=True):
            res = self.api.patch(self.url(f'{image["id"]}/'), {'license_verified': False}, format='json')
        self.assertIs(res.json()['public'], False)
        self.assert_hidden(image)

    def test_changing_the_provenance_hides_it_until_it_is_verified_again(self):
        image = self.published(self.blue, SUPPLIER)
        with self.captureOnCommitCallbacks(execute=True):
            self.api.patch(self.url(f'{image["id"]}/'), {'license': 'Otra autorización'}, format='json')
        self.assert_hidden(image)

    def test_a_verified_demo_is_public_and_still_said(self):
        demo = self.published(self.blue, {'source_kind': 'demo', 'license': 'Recurso de demostración interno'})
        body = self.detail()
        self.assertTrue(body['finish_images'][str(self.blue.pk)][0]['is_demo'])
        self.assertTrue(self.blue_row(body)['gallery'][0]['is_demo'])
        self.assertEqual(device_catalog.variant_picture(self.new128blue), (demo['url'], False, True))

    def test_only_the_verified_pictures_of_a_finish_are_shown(self):
        hidden = self.uploaded(self.blue, SUPPLIER)
        shown = self.published(self.blue)
        body = self.detail()
        self.assertEqual([i['url'] for i in body['finish_images'][str(self.blue.pk)]], [shown['url']])
        self.assertEqual([i['url'] for i in self.blue_row(body)['gallery']], [shown['url']])
        self.assertNotIn(hidden['url'], str(body))

    def test_a_picture_of_another_company_is_never_public_even_verified(self):
        from django.utils import timezone

        other = _p3_company('pc-licencia-otra', 'Otra')
        foreign = self.stored(company=other)
        StorefrontImage.objects.filter(pk=foreign.pk).update(source_kind=StorefrontImage.SOURCE_OWN_PHOTO,
                                                             license_verified_at=timezone.now())
        image = self.place(self.blue, primary=True, stored=foreign)
        self.assert_hidden({'url': image.image_url})
        self.assertIs(self.panel_image({'id': image.pk})['public'], False)

    def test_an_address_that_is_not_a_managed_image_is_never_public(self):
        image = ModelFinishImage.objects.create(company=self.company, model=self.model, color_option=self.blue,
                                                image_url='https://cdn.example.com/azul.png', is_primary=True)
        self.assert_hidden({'url': image.image_url})
        self.assertIs(self.panel_image({'id': image.pk})['public'], False)

    def test_a_variants_own_pictures_are_not_gated(self):
        from store import product_media

        own = product_media.add_image(product=self.new128blue, actor=self.staff, uploaded=SimpleUploadedFile(
            'propia.png', png_with_transparency(), content_type='image/png'))
        self.assertIsNone(StorefrontImage.objects.get(public_id=own.image_url.rsplit('/', 1)[-1]).license_verified_at)
        self.assertEqual([i['url'] for i in self.blue_row()['gallery']], [own.image_url])
        self.assertEqual(device_catalog.variant_picture(self.new128blue)[0], own.image_url)


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
        own = self.uploaded(self.blue, OWN)
        self.stored()   # uploaded, shown nowhere in the catalogue: not counted
        # The shop's own photo as the model's cover: not a finish picture, so not gated and nothing to verify.
        cover = self.stored()
        StorefrontImage.objects.filter(pk=cover.pk).update(source_kind=StorefrontImage.SOURCE_OWN_PHOTO)
        ProductModel.objects.filter(pk=self.model.pk).update(image_url=cover.url)
        data = self.report()['uploads']
        # Owner, 2026-11-23: a finish picture is published only once verified, the shop's own photo too — so an own
        # photo a finish shows counts as unverified until somebody checks it.
        self.assertEqual(data, {'total': 5, 'unknown_source': 1, 'unverified_license': 3, 'demo': 1})
        with self.captureOnCommitCallbacks(execute=True):
            self.api.patch(self.url(f'{supplier["id"]}/'), {'license_verified': True}, format='json')
        self.assertEqual(self.report()['uploads']['unverified_license'], 2)
        self.verify(own)
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


# ---------------------------------------------------------------------------
# Review of #372
# ---------------------------------------------------------------------------

class RoleChangeAfterUploadTest(FinishApiBase):
    """An attribute's role (or kind) can change after pictures were placed: its finish galleries stay workable."""

    def _two_pictures(self):
        return self.uploaded(self.blue), self.uploaded(self.blue)

    def _still_workable(self, first, second):
        res = self.api.patch(self.url(f'{second["id"]}/'), {'alt_text': 'De lado'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        with self.captureOnCommitCallbacks(execute=True):
            res = self.api.post(self.url('order/'), {'color_option_id': self.blue.pk,
                                                     'order': [second['id'], first['id']]}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        with self.captureOnCommitCallbacks(execute=True):
            res = self.api.delete(self.url(f'{first["id"]}/'))
        self.assertEqual(res.status_code, 204, res.content)
        self.assertTrue(ModelFinishImage.objects.get(pk=second['id']).is_primary)

    def test_no_longer_a_colour_the_pictures_are_listed_flagged_and_editable(self):
        first, second = self._two_pictures()
        VariantAttribute.objects.filter(pk=self.colour.pk).update(role='other', kind='text')
        rows = self.listing()
        self.assertEqual(len(rows[self.blue.pk]['images']), 2)
        self.assertTrue(rows[self.blue.pk]['not_colour'])
        self.assertNotIn(self.black.pk, rows)       # black has no pictures and is no longer a colour
        self._still_workable(first, second)

    def test_role_other_with_a_colour_kind_still_is_the_colour_dimension(self):
        first, second = self._two_pictures()
        VariantAttribute.objects.filter(pk=self.colour.pk).update(role='other')
        rows = self.listing()
        self.assertFalse(rows[self.blue.pk]['not_colour'])
        self.assertIn(self.black.pk, rows)
        self._still_workable(first, second)


class ColourDimensionTest(FinishApiBase):
    """D-PRC-8: the colour dimension is the `color` role; without one, the attributes of the `color` kind."""

    def setUp(self):
        super().setUp()
        self.public = _client(host=self.host)
        self.new128blue = self.variant('Teléfono X 128 GB Azul', 'tx-128-azul', '3100.00', [self.gb128, self.blue])
        self.cover = self.stored()
        ProductModel.objects.filter(pk=self.model.pk).update(image_url=self.cover.url)

    def variants(self, model=None):
        res = self.public.get(f'/api/catalog/models/{(model or self.model).slug}/')
        self.assertEqual(res.status_code, 200, res.content)
        return {row['product_id']: row for row in res.json()['variants']}

    def test_a_colour_kind_without_the_role_is_the_colour_dimension(self):
        # Production today: the colour attribute kept the `other` role (0165).
        VariantAttribute.objects.filter(pk=self.colour.pk).update(role='other')
        blue = self.published(self.blue)
        rows = self.variants()
        self.assertEqual([image['url'] for image in rows[self.new128blue.pk]['gallery']], [blue['url']])
        self.assertEqual(rows[self.new128.pk]['gallery'], [])
        self.assertTrue(rows[self.new128.pk]['image_pending'])
        self.assertNotIn(self.cover.url, str(rows))
        report = self.api.get(f'/api/admin/catalog/image-sources/{self.q}').json()['licensing']
        self.assertEqual(report['finishes_without_images'], 1)   # black

    def test_with_the_colour_role_present_the_kind_is_ignored(self):
        tone = device_catalog.create_attribute(company=self.company, name='Tono', kind='color')
        warm = device_catalog.create_option(company=self.company, attribute_id=tone.pk, label='Cálido',
                                            swatch_hex='#AA5500')
        res = self.upload(warm)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'not_a_colour')

    def test_a_colourless_model_shows_its_cover(self):
        charger = device_catalog.create_model(company=self.company, name='Cargador X')
        ProductModel.objects.filter(pk=charger.pk).update(image_url=self.cover.url)
        self.model = charger
        plain = self.variant('Cargador X 128 GB', 'cargador-128', '99.00', [self.gb128])
        row = self.variants(charger)[plain.pk]
        self.assertEqual([image['url'] for image in row['gallery']], [self.cover.url])
        self.assertFalse(row['image_pending'])

    def test_a_model_with_colours_never_shows_its_cover(self):
        for row in self.variants().values():
            self.assertNotIn(self.cover.url, str(row['gallery']))


class SweepIgnoresProvenanceTest(FinishShop):
    def test_a_source_address_never_keeps_another_image_alive(self):
        self.assertNotIn((StorefrontImage, 'source_url'), storefront_media.reference_fields())
        victim = self.stored()
        other = self.stored()
        StorefrontImage.objects.filter(pk=other.pk).update(
            source_url=f'https://proveedor.example/catalogo/{victim.public_id}')
        self.assertEqual(storefront_media.reference_count(victim.public_id), 0)
        self.assertIn(victim.pk, [image.pk for image in storefront_media.unplaced(older_than_hours=0)])


class CoverOnlyForAColourlessModelTest(FinishApiBase):
    """Re-review of #372: the cover is allowed only when NO variant of the model has an option of an attribute with
    the `color` role OR the `color` kind — whatever the company's colour dimension for finish pictures."""

    def setUp(self):
        super().setUp()
        self.public = _client(host=self.host)
        self.cover = self.stored()

    def gallery_of(self, model, product):
        res = self.public.get(f'/api/catalog/models/{model.slug}/')
        self.assertEqual(res.status_code, 200, res.content)
        return next(row for row in res.json()['variants'] if row['product_id'] == product.pk)

    def test_a_model_on_a_legacy_colour_kind_attribute_never_shows_the_cover(self):
        # The company's colour dimension is «Tono» (role color); «Color» stayed kind=color, role=other.
        VariantAttribute.objects.filter(pk=self.colour.pk).update(role='other')
        tone = device_catalog.create_attribute(company=self.company, name='Tono', kind='color', role='color')
        device_catalog.create_option(company=self.company, attribute_id=tone.pk, label='Cálido', swatch_hex='#AA5500')
        ProductModel.objects.filter(pk=self.model.pk).update(image_url=self.cover.url)
        self.model.refresh_from_db()
        row = self.gallery_of(self.model, self.new128)
        self.assertEqual(row['gallery'], [])
        self.assertTrue(row['image_pending'])

    def test_a_hidden_variant_with_a_colour_also_forbids_the_cover(self):
        from store.models import Product
        charger = device_catalog.create_model(company=self.company, name='Cargador X')
        ProductModel.objects.filter(pk=charger.pk).update(image_url=self.cover.url)
        charger.refresh_from_db()
        self.model = charger
        plain = self.variant('Cargador X 128 GB', 'cargador-128', '99.00', [self.gb128])
        hidden = self.variant('Cargador X 256 GB Negro', 'cargador-256-negro', '99.00', [self.gb256, self.black])
        Product.objects.filter(pk=hidden.pk).update(is_published_online=False)
        row = self.gallery_of(charger, plain)
        self.assertEqual(row['gallery'], [])
        self.assertTrue(row['image_pending'])

    def test_a_truly_colourless_model_shows_its_cover(self):
        charger = device_catalog.create_model(company=self.company, name='Cargador X')
        ProductModel.objects.filter(pk=charger.pk).update(image_url=self.cover.url)
        charger.refresh_from_db()
        self.model = charger
        plain = self.variant('Cargador X 128 GB', 'cargador-128', '99.00', [self.gb128])
        row = self.gallery_of(charger, plain)
        self.assertEqual([image['url'] for image in row['gallery']], [self.cover.url])
        self.assertFalse(row['image_pending'])
