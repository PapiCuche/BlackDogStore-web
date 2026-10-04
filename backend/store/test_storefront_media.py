import io
import shutil
import tempfile

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from store.models import AdminAuditLog, Category, StorefrontImage
from store.tests import _p2d_member, _p3_company

UPLOAD = '/api/admin/storefront/images/'
PAGE = '/api/admin/storefront/page/'

_ROOT = tempfile.mkdtemp(prefix='storefront-media-')


def tearDownModule():
    shutil.rmtree(_ROOT, ignore_errors=True)


def png_with_transparency(size=(120, 80)):
    """Un recorte: opaco en el centro, transparente en las esquinas."""
    image = Image.new('RGBA', size, (0, 0, 0, 0))
    for x in range(size[0] // 4, 3 * size[0] // 4):
        for y in range(size[1] // 4, 3 * size[1] // 4):
            image.putpixel((x, y), (200, 30, 30, 255))
    out = io.BytesIO()
    image.save(out, format='PNG')
    return out.getvalue()


def jpeg(size=(120, 80), exif=None):
    image = Image.new('RGB', size, (10, 120, 200))
    out = io.BytesIO()
    kwargs = {'exif': exif} if exif is not None else {}
    image.save(out, format='JPEG', quality=90, **kwargs)
    return out.getvalue()


def webp(size=(120, 80)):
    image = Image.new('RGBA', size, (0, 0, 0, 0))
    image.putpixel((size[0] // 2, size[1] // 2), (5, 5, 5, 255))
    out = io.BytesIO()
    image.save(out, format='WEBP', lossless=True)
    return out.getvalue()


def upload_file(content, name='foto.png', content_type='image/png'):
    return SimpleUploadedFile(name, content, content_type=content_type)


@override_settings(EVIDENCE_STORAGE_BACKEND='filesystem', EVIDENCE_STORAGE_ROOT=_ROOT)
class StorefrontMediaBase(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('media-tienda', 'Tienda Media')
        self.other = _p3_company('media-otra', 'Otra Tienda')
        self.manager, _ = _p2d_member(
            self.company, 'media_gestor',
            ['company.view', 'company.manage', 'products.view', 'products.manage'])
        self.viewer, _ = _p2d_member(self.company, 'media_lector', ['company.view', 'products.view'])
        self.outsider, _ = _p2d_member(
            self.other, 'media_ajeno', ['company.view', 'company.manage', 'products.manage'])

    @staticmethod
    def _as(user):
        client = APIClient()
        if user is not None:
            client.force_authenticate(user=user)
        return client

    def _upload(self, user, content, name='foto.png', content_type='image/png', company=None):
        return self._as(user).post(
            f'{UPLOAD}?company={(company or self.company).pk}',
            {'file': upload_file(content, name, content_type)}, format='multipart',
        )

    def _fetch(self, url):
        # La dirección pública no lleva barra final; quien reenvía a Django la
        # añade (Caddy en producción, el proxy de Next en desarrollo).
        res = APIClient().get(url if url.endswith('/') else url + '/')
        body = b''.join(res.streaming_content) if getattr(res, 'streaming', False) else res.content
        return res, body


class StorefrontImageUploadTest(StorefrontMediaBase):
    """
    Una tienda sube sus propias imágenes desde el panel.

    Hasta ahora una imagen de la tienda era una dirección escrita a mano. Para
    que el dueño pueda poner la foto del hero o de una categoría sin depender
    de dónde esté alojada, el panel sube el archivo y recibe la dirección.
    """

    def test_a_transparent_png_stays_transparent(self):
        """
        LO QUE PIDIÓ EL PROPIETARIO. Un recorte de producto se sube en PNG para
        que no lleve fondo. Si el servidor lo convirtiera a JPEG, o lo aplanara
        sobre blanco como hace con las evidencias, el recorte llegaría a la
        tienda con un rectángulo detrás.
        """
        res = self._upload(self.manager, png_with_transparency())
        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(res.data['has_alpha'])
        self.assertEqual(res.data['mime_type'], 'image/png')
        self.assertRegex(res.data['url'], r'^/api/storefront/images/[0-9a-f]{32}$')

        served, body = self._fetch(res.data['url'])
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served['Content-Type'], 'image/png')
        image = Image.open(io.BytesIO(body)).convert('RGBA')
        self.assertEqual(image.size, (120, 80))
        self.assertEqual(image.getpixel((0, 0))[3], 0, 'la esquina dejó de ser transparente')
        self.assertEqual(image.getpixel((60, 40)), (200, 30, 30, 255))

    def test_a_jpeg_is_served_as_jpeg_without_its_metadata(self):
        exif = Image.Exif()
        exif[0x010F] = 'Cámara del dueño'   # Make
        exif[0x8825] = {1: 'S', 2: (16.0, 24.0, 0.0)}  # GPS
        res = self._upload(self.manager, jpeg(exif=exif), 'foto.jpg', 'image/jpeg')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertFalse(res.data['has_alpha'])

        served, body = self._fetch(res.data['url'])
        self.assertEqual(served['Content-Type'], 'image/jpeg')
        self.assertEqual(len(Image.open(io.BytesIO(body)).getexif()), 0)

    def test_a_webp_with_transparency_is_accepted(self):
        res = self._upload(self.manager, webp(), 'foto.webp', 'image/webp')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(res.data['has_alpha'])
        served, body = self._fetch(res.data['url'])
        self.assertEqual(served['Content-Type'], 'image/webp')
        self.assertEqual(Image.open(io.BytesIO(body)).convert('RGBA').getpixel((0, 0))[3], 0)

    def test_a_very_large_image_is_scaled_down_and_keeps_its_shape(self):
        res = self._upload(self.manager, png_with_transparency((4000, 1000)))
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual((res.data['width'], res.data['height']), (2400, 600))
        self.assertTrue(res.data['has_alpha'])

    def test_what_is_not_an_image_is_refused_whatever_it_is_called(self):
        cases = [
            ('texto.png', b'esto no es una imagen', 'image/png'),
            ('dibujo.svg', b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', 'image/svg+xml'),
            ('pagina.png', b'<html><script>alert(1)</script></html>', 'image/png'),
        ]
        for name, content, content_type in cases:
            with self.subTest(name=name):
                res = self._upload(self.manager, content, name, content_type)
                self.assertEqual(res.status_code, 400, getattr(res, 'data', None))
        self.assertEqual(StorefrontImage.objects.count(), 0)

    def test_a_gif_is_refused(self):
        out = io.BytesIO()
        Image.new('P', (10, 10)).save(out, format='GIF')
        res = self._upload(self.manager, out.getvalue(), 'animado.gif', 'image/gif')
        self.assertEqual(res.status_code, 400)

    @override_settings(STOREFRONT_IMAGE_MAX_UPLOAD_BYTES=2000)
    def test_a_file_over_the_limit_is_refused(self):
        res = self._upload(self.manager, png_with_transparency((600, 600)))
        self.assertEqual(res.status_code, 400)
        self.assertIn('pesa', str(res.data).lower())
        self.assertEqual(StorefrontImage.objects.count(), 0)

    @override_settings(STOREFRONT_IMAGE_MAX_PIXELS=5000)
    def test_disproportionate_dimensions_are_refused(self):
        res = self._upload(self.manager, png_with_transparency((200, 200)))
        self.assertEqual(res.status_code, 400)

    def test_a_request_without_a_file_is_refused(self):
        res = self._as(self.manager).post(f'{UPLOAD}?company={self.company.pk}', {}, format='multipart')
        self.assertEqual(res.status_code, 400)

    def test_the_stored_object_carries_nothing_from_the_original_name(self):
        res = self._upload(self.manager, png_with_transparency(), 'cliente-juan-perez-dni-12345678.png')
        row = StorefrontImage.objects.get(public_id=res.data['id'])
        self.assertTrue(row.storage_key.startswith(f'companies/{self.company.pk}/storefront/'))
        self.assertNotIn('juan', row.storage_key)
        self.assertNotIn('12345678', row.storage_key)
        self.assertEqual(row.company_id, self.company.pk)
        self.assertEqual(row.uploaded_by, self.manager)

    def test_the_upload_is_audited_under_the_company(self):
        res = self._upload(self.manager, png_with_transparency())
        log = AdminAuditLog.objects.get(action='storefront_image_uploaded')
        self.assertEqual(log.company_id, self.company.pk)
        self.assertEqual(log.actor, self.manager)
        self.assertEqual(log.metadata['public_id'], res.data['id'])


class StorefrontImageAuthorityTest(StorefrontMediaBase):
    def test_reading_the_shop_configuration_is_not_enough_to_upload(self):
        res = self._upload(self.viewer, png_with_transparency())
        self.assertEqual(res.status_code, 403)
        self.assertEqual(StorefrontImage.objects.count(), 0)

    def test_an_anonymous_caller_cannot_upload(self):
        res = self._upload(None, png_with_transparency())
        self.assertEqual(res.status_code, 401)

    def test_a_manager_of_another_company_cannot_upload_into_this_one(self):
        res = self._upload(self.outsider, png_with_transparency(), company=self.company)
        self.assertIn(res.status_code, (403, 404))
        self.assertEqual(StorefrontImage.objects.filter(company=self.company).count(), 0)

    def test_an_image_lands_in_the_company_of_the_caller(self):
        res = self._upload(self.outsider, png_with_transparency(), company=self.other)
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(StorefrontImage.objects.get().company_id, self.other.pk)


class StorefrontImageServingTest(StorefrontMediaBase):
    def test_anyone_can_see_a_shop_image_and_the_browser_may_keep_it(self):
        url = self._upload(self.manager, png_with_transparency()).data['url']
        res, _body = self._fetch(url)
        self.assertEqual(res.status_code, 200)
        self.assertIn('public', res['Cache-Control'])
        self.assertIn('immutable', res['Cache-Control'])
        self.assertEqual(res['X-Content-Type-Options'], 'nosniff')

    def test_an_unknown_image_is_not_found(self):
        self.assertEqual(APIClient().get('/api/storefront/images/' + '0' * 32 + '/').status_code, 404)
        self.assertEqual(APIClient().get('/api/storefront/images/no-es-un-id/').status_code, 404)


class StorefrontImageSlotsTest(StorefrontMediaBase):
    """Los huecos donde la tienda coloca lo que subió."""

    def _page(self, user, **data):
        return self._as(user).patch(f'{PAGE}?company={self.company.pk}', data, format='json')

    def _public_page(self):
        res = APIClient().get('/api/storefront/config/', HTTP_HOST='localhost')
        return res

    def test_the_hero_takes_an_uploaded_image_and_a_variant(self):
        url = self._upload(self.manager, png_with_transparency()).data['url']
        res = self._page(self.manager, hero_image_url=url, hero_variant='light')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['page']['hero_image_url'], url)
        self.assertEqual(res.data['page']['hero_variant'], 'light')

    def test_a_shop_that_never_chose_keeps_the_dark_hero_and_no_image(self):
        res = self._as(self.manager).get(f'{PAGE}?company={self.company.pk}')
        self.assertEqual(res.data['page']['hero_variant'], 'dark')
        self.assertEqual(res.data['page']['hero_image_url'], '')

    def test_an_unknown_variant_is_refused(self):
        res = self._page(self.manager, hero_variant='neon')
        self.assertEqual(res.status_code, 400)

    def test_an_address_the_browser_could_run_is_refused(self):
        for value in ('javascript:alert(1)', 'data:image/png;base64,AAAA', '//evil.example/x.png'):
            with self.subTest(value=value):
                self.assertEqual(self._page(self.manager, hero_image_url=value).status_code, 400)

    def _category(self):
        return Category.objects.create(company=self.company, name='Accesorios', slug='accesorios-media')

    def _patch_category(self, user, category, **data):
        return self._as(user).patch(
            f'/api/admin/categories/{category.pk}/?company={self.company.pk}', data, format='json')

    def test_a_category_takes_an_uploaded_image(self):
        category = self._category()
        url = self._upload(self.manager, png_with_transparency()).data['url']
        res = self._patch_category(self.manager, category, image_url=url)
        self.assertEqual(res.status_code, 200, res.data)
        category.refresh_from_db()
        self.assertEqual(category.image_url, url)

        log = AdminAuditLog.objects.get(action='category_updated')
        self.assertEqual(log.company_id, self.company.pk)

    def test_the_category_image_can_be_cleared(self):
        category = self._category()
        category.image_url = '/api/storefront/images/' + 'a' * 32 + '/'
        category.save()
        res = self._patch_category(self.manager, category, image_url='')
        self.assertEqual(res.status_code, 200, res.data)
        category.refresh_from_db()
        self.assertEqual(category.image_url, '')

    def test_a_category_refuses_an_address_the_browser_could_run(self):
        category = self._category()
        res = self._patch_category(self.manager, category, image_url='javascript:alert(1)')
        self.assertEqual(res.status_code, 400)

    def test_without_permission_over_the_catalogue_the_category_does_not_change(self):
        category = self._category()
        res = self._patch_category(self.viewer, category, image_url='/x.png')
        self.assertEqual(res.status_code, 403)
        category.refresh_from_db()
        self.assertEqual(category.image_url, '')

    def test_a_category_of_another_company_does_not_exist_for_this_one(self):
        foreign = Category.objects.create(company=self.other, name='Ajena', slug='ajena-media')
        res = self._patch_category(self.manager, foreign, image_url='/x.png')
        self.assertEqual(res.status_code, 404)
        foreign.refresh_from_db()
        self.assertEqual(foreign.image_url, '')
