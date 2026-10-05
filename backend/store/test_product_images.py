"""
PRODUCT-MEDIA — la galería de un producto, subida desde el panel.

Hasta esta fase la imagen de un producto era una dirección absoluta escrita a
mano en `Product.image_url`. Ahora el panel sube archivos: varias imágenes por
producto, una principal, con orden y texto alternativo.

Lo que se fija aquí:

  * la galería usa la MISMA tubería que las imágenes de la tienda (recodifica
    desde los píxeles, aísla por empresa, limpia lo que nadie muestra);
  * `Product.image_url` sigue siendo lo que lee la tienda, el carrito y los
    pedidos: es la dirección de la imagen principal, o la URL heredada si el
    producto todavía no tiene galería;
  * otra empresa no ve, no toca y no puede citar estas imágenes.
"""
import shutil
import tempfile

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from store import storefront_media
from store.models import AdminAuditLog, Category, Product, ProductImage, StorefrontImage
from store.test_storefront_media import jpeg, png_with_transparency, upload_file, webp
from store.tests import _p2d_member, _p3_company

_ROOT = tempfile.mkdtemp(prefix='product-media-')

LEGACY = 'https://cdn.example.com/fotos/telefono.jpg'


def tearDownModule():
    shutil.rmtree(_ROOT, ignore_errors=True)


@override_settings(EVIDENCE_STORAGE_BACKEND='filesystem', EVIDENCE_STORAGE_ROOT=_ROOT)
class ProductMediaBase(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('galeria-tienda', 'Tienda Galería')
        self.other = _p3_company('galeria-otra', 'Otra Tienda')
        self.manager, _ = _p2d_member(
            self.company, 'galeria_gestor', ['products.view', 'products.manage'])
        self.viewer, _ = _p2d_member(self.company, 'galeria_lector', ['products.view'])
        self.outsider, _ = _p2d_member(
            self.other, 'galeria_ajeno', ['products.view', 'products.manage', 'company.manage'])
        self.product = Product.objects.create(
            company=self.company, name='Teléfono', slug='telefono', price='100.00',
        )
        self.foreign = Product.objects.create(
            company=self.other, name='Ajeno', slug='ajeno', price='50.00',
        )

    @staticmethod
    def _as(user):
        client = APIClient()
        if user is not None:
            client.force_authenticate(user=user)
        return client

    def _base(self, product=None, company=None):
        product = product or self.product
        return f'/api/admin/products/{product.pk}/images/', f'?company={(company or self.company).pk}'

    def _list(self, user, product=None, company=None):
        path, query = self._base(product, company)
        return self._as(user).get(path + query)

    def _add(self, user, content=None, *, name='foto.png', content_type='image/png',
             product=None, company=None, **fields):
        path, query = self._base(product, company)
        data = {'file': upload_file(content or png_with_transparency(), name, content_type)}
        data.update(fields)
        return self._as(user).post(path + query, data, format='multipart')

    def _item(self, image_id, product=None, company=None):
        path, query = self._base(product, company)
        return f'{path}{image_id}/{query}'

    def _patch(self, user, image_id, body, **kw):
        return self._as(user).patch(self._item(image_id, **kw), body, format='json')

    def _delete(self, user, image_id, **kw):
        return self._as(user).delete(self._item(image_id, **kw))

    def _reorder(self, user, ids, product=None, company=None):
        path, query = self._base(product, company)
        return self._as(user).post(f'{path}order/{query}', {'order': ids}, format='json')


class ProductGalleryTest(ProductMediaBase):
    def test_the_first_image_becomes_the_primary_and_the_storefront_shows_it(self):
        res = self._add(self.manager, alt_text='Vista frontal')

        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertTrue(body['is_primary'])
        self.assertEqual(body['alt_text'], 'Vista frontal')
        self.assertRegex(body['url'], r'^/api/storefront/images/[0-9a-f]{32}$')
        self.assertEqual((body['width'], body['height']), (120, 80))
        self.assertNotIn('storage_key', body)

        self.product.refresh_from_db()
        self.assertEqual(self.product.image_url, body['url'])

        # La imagen se sirve al público, recodificada aquí.
        served = APIClient().get(body['url'] + '/')
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served['Content-Type'], 'image/png')

    def test_a_second_image_joins_the_gallery_without_taking_the_primary(self):
        first = self._add(self.manager).json()
        second = self._add(self.manager, jpeg(), name='b.jpg', content_type='image/jpeg').json()

        self.assertFalse(second['is_primary'])
        listed = self._list(self.manager).json()['results']
        self.assertEqual([row['id'] for row in listed], [first['id'], second['id']])
        self.assertEqual([row['is_primary'] for row in listed], [True, False])
        self.product.refresh_from_db()
        self.assertEqual(self.product.image_url, first['url'])

    def test_choosing_another_primary_moves_what_the_storefront_shows(self):
        first = self._add(self.manager).json()
        second = self._add(self.manager, webp(), name='b.webp', content_type='image/webp').json()

        res = self._patch(self.manager, second['id'], {'is_primary': True})

        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.json()['is_primary'])
        self.assertEqual(
            list(ProductImage.objects.filter(product=self.product, is_primary=True)
                 .values_list('pk', flat=True)),
            [second['id']],
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.image_url, second['url'])
        self.assertNotEqual(first['url'], second['url'])

    def test_the_primary_cannot_be_switched_off_without_choosing_another(self):
        first = self._add(self.manager).json()
        res = self._patch(self.manager, first['id'], {'is_primary': False})
        self.assertEqual(res.status_code, 400)
        self.assertTrue(ProductImage.objects.get(pk=first['id']).is_primary)

    def test_the_alternative_text_can_be_edited(self):
        first = self._add(self.manager).json()
        res = self._patch(self.manager, first['id'], {'alt_text': '  Parte trasera  '})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['alt_text'], 'Parte trasera')

    def test_removing_the_primary_promotes_the_next_and_deletes_the_file(self):
        first = self._add(self.manager).json()
        second = self._add(self.manager, jpeg(), name='b.jpg', content_type='image/jpeg').json()
        public_id = storefront_media.managed_public_id(first['url'])

        with self.captureOnCommitCallbacks(execute=True):
            res = self._delete(self.manager, first['id'])

        self.assertEqual(res.status_code, 204)
        self.assertFalse(StorefrontImage.objects.filter(public_id=public_id).exists())
        self.assertEqual(APIClient().get(first['url'] + '/').status_code, 404)
        self.product.refresh_from_db()
        self.assertEqual(self.product.image_url, second['url'])
        self.assertTrue(ProductImage.objects.get(pk=second['id']).is_primary)

    def test_removing_the_last_image_leaves_the_product_without_one(self):
        only = self._add(self.manager).json()
        self.assertEqual(self._delete(self.manager, only['id']).status_code, 204)
        self.product.refresh_from_db()
        self.assertEqual(self.product.image_url, '')
        self.assertEqual(self._list(self.manager).json()['results'], [])

    def test_the_gallery_can_be_reordered(self):
        ids = [self._add(self.manager).json()['id'] for _ in range(3)]

        res = self._reorder(self.manager, [ids[2], ids[0], ids[1]])

        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual([row['id'] for row in res.json()['results']], [ids[2], ids[0], ids[1]])
        # Reordenar no cambia cuál es la principal.
        self.assertTrue(ProductImage.objects.get(pk=ids[0]).is_primary)

    def test_a_reorder_must_name_exactly_the_gallery(self):
        ids = [self._add(self.manager).json()['id'] for _ in range(2)]
        foreign = ProductImage.objects.create(
            company=self.other, product=self.foreign, image_url='/assets/x.png', is_primary=True,
        )
        for order in ([ids[0]], [ids[0], ids[0]], [ids[0], ids[1], foreign.pk], 'no', [ids[0], 'x']):
            with self.subTest(order=order):
                self.assertEqual(self._reorder(self.manager, order).status_code, 400)

    @override_settings(PRODUCT_IMAGE_MAX_PER_PRODUCT=2)
    def test_a_product_has_a_limit_of_images(self):
        self._add(self.manager)
        self._add(self.manager)
        res = self._add(self.manager)
        self.assertEqual(res.status_code, 400)
        self.assertIn('2', res.json()['detail'])
        self.assertEqual(ProductImage.objects.filter(product=self.product).count(), 2)
        # La que no entró no queda guardada.
        self.assertEqual(StorefrontImage.objects.filter(company=self.company).count(), 2)

    def test_every_change_leaves_an_audit_row_in_the_company(self):
        first = self._add(self.manager).json()
        second = self._add(self.manager).json()
        self._patch(self.manager, second['id'], {'is_primary': True})
        self._delete(self.manager, first['id'])

        actions = list(
            AdminAuditLog.objects.filter(company=self.company, target_type='product')
            .order_by('pk').values_list('action', flat=True)
        )
        self.assertEqual(actions, [
            'product_image_added', 'product_image_added',
            'product_image_primary_changed', 'product_image_removed',
        ])
        row = AdminAuditLog.objects.filter(action='product_image_removed').get()
        self.assertEqual(row.actor, self.manager)
        self.assertEqual(row.target_id, str(self.product.pk))
        self.assertNotIn('storage_key', str(row.metadata))


class ProductImageValidationTest(ProductMediaBase):
    def test_what_is_not_an_image_is_refused_and_nothing_is_stored(self):
        cases = {
            'texto con extensión de imagen': (b'<?php echo 1; ?>', 'foto.png', 'image/png'),
            'ejecutable renombrado': (b'MZ\x90\x00' + b'\x00' * 200, 'foto.jpg', 'image/jpeg'),
            'imagen truncada': (png_with_transparency()[:60], 'foto.png', 'image/png'),
            'svg': (b'<svg xmlns="http://www.w3.org/2000/svg"><script>1</script></svg>',
                    'foto.svg', 'image/svg+xml'),
            'vacío': (b'', 'foto.png', 'image/png'),
        }
        for label, (content, name, content_type) in cases.items():
            with self.subTest(label):
                res = self._as(self.manager).post(
                    ''.join(self._base()), {'file': upload_file(content, name, content_type)},
                    format='multipart',
                )
                self.assertEqual(res.status_code, 400, res.content)
                self.assertIn('detail', res.json())
        self.assertEqual(ProductImage.objects.count(), 0)
        self.assertEqual(StorefrontImage.objects.count(), 0)

    @override_settings(STOREFRONT_IMAGE_MAX_UPLOAD_BYTES=2000)
    def test_a_file_over_the_limit_is_refused_as_too_large(self):
        res = self._add(self.manager, jpeg((900, 900)), name='grande.jpg', content_type='image/jpeg')
        self.assertEqual(res.status_code, 413, res.content)
        self.assertEqual(ProductImage.objects.count(), 0)

    def test_the_request_must_carry_a_file(self):
        res = self._as(self.manager).post(''.join(self._base()), {}, format='multipart')
        self.assertEqual(res.status_code, 400)

    def test_a_dangerous_filename_never_reaches_the_storage_key(self):
        res = self._add(self.manager, name='../../etc/passwd.png')
        self.assertEqual(res.status_code, 201)
        stored = StorefrontImage.objects.get()
        self.assertRegex(stored.storage_key, rf'^companies/{self.company.pk}/storefront/[0-9a-f]{{32}}\.png$')

    def test_the_alternative_text_has_a_length_limit(self):
        first = self._add(self.manager).json()
        res = self._patch(self.manager, first['id'], {'alt_text': 'x' * 400})
        self.assertEqual(res.status_code, 400)


class ProductImageAuthorityTest(ProductMediaBase):
    def test_who_can_only_view_products_sees_the_gallery_and_cannot_change_it(self):
        image = self._add(self.manager).json()

        self.assertEqual(self._list(self.viewer).status_code, 200)
        self.assertEqual(self._add(self.viewer).status_code, 403)
        self.assertEqual(self._patch(self.viewer, image['id'], {'alt_text': 'x'}).status_code, 403)
        self.assertEqual(self._delete(self.viewer, image['id']).status_code, 403)
        self.assertEqual(self._reorder(self.viewer, [image['id']]).status_code, 403)

    def test_without_a_session_nothing_answers(self):
        image = self._add(self.manager).json()
        for res in (
            self._list(None), self._add(None), self._delete(None, image['id']),
            self._patch(None, image['id'], {'alt_text': 'x'}),
        ):
            self.assertIn(res.status_code, (401, 403))

    def test_another_company_cannot_see_or_touch_the_gallery(self):
        image = self._add(self.manager).json()

        # Con su propia empresa como contexto: el producto no existe para ella.
        for res in (
            self._list(self.outsider, company=self.other),
            self._add(self.outsider, company=self.other),
            self._patch(self.outsider, image['id'], {'is_primary': True}, company=self.other),
            self._delete(self.outsider, image['id'], company=self.other),
            self._reorder(self.outsider, [image['id']], company=self.other),
        ):
            self.assertEqual(res.status_code, 404, res.content)
        # Pidiendo la empresa ajena como contexto: no tiene autoridad en ella.
        for res in (self._list(self.outsider), self._add(self.outsider)):
            self.assertIn(res.status_code, (403, 404))

        self.assertEqual(ProductImage.objects.filter(product=self.product).count(), 1)
        self.assertEqual(ProductImage.objects.filter(product=self.foreign).count(), 0)

    def test_an_image_of_another_product_is_not_found_under_this_one(self):
        second = Product.objects.create(
            company=self.company, name='Funda', slug='funda', price='10.00',
        )
        image = self._add(self.manager, product=second).json()

        self.assertEqual(self._patch(self.manager, image['id'], {'alt_text': 'x'}).status_code, 404)
        self.assertEqual(self._delete(self.manager, image['id']).status_code, 404)
        self.assertTrue(ProductImage.objects.filter(pk=image['id']).exists())


class LegacyImageAddressTest(ProductMediaBase):
    """`Product.image_url` no desaparece: es lo que todo lo demás ya lee."""

    def _patch_product(self, user, body, product=None):
        product = product or self.product
        return self._as(user).patch(
            f'/api/admin/products/{product.pk}/?company={self.company.pk}', body, format='json',
        )

    def test_a_product_with_an_external_address_keeps_it_until_it_gets_a_gallery(self):
        Product.objects.filter(pk=self.product.pk).update(image_url=LEGACY)

        public = self._as(None).get(f'/api/v1/storefront/{self.company.slug}/products/telefono/')
        self.assertEqual(public.status_code, 200, public.content)
        self.assertEqual(public.json()['image_url'], LEGACY)
        self.assertEqual(public.json()['images'], [])

        added = self._add(self.manager, alt_text='Frontal').json()
        public = self._as(None).get(f'/api/v1/storefront/{self.company.slug}/products/telefono/')
        self.assertEqual(public.json()['image_url'], added['url'])
        self.assertEqual(public.json()['images'], [
            {'url': added['url'], 'alt_text': 'Frontal', 'is_primary': True,
             'width': 120, 'height': 80},
        ])
        row = AdminAuditLog.objects.filter(action='product_image_added').get()
        self.assertEqual(row.metadata['replaced_address'], LEGACY)

    def test_the_product_list_does_not_run_one_query_per_gallery(self):
        for index in range(4):
            product = Product.objects.create(
                company=self.company, name=f'P{index}', slug=f'p{index}', price='10.00',
            )
            self._add(self.manager, product=product)
        url = f'/api/v1/storefront/{self.company.slug}/products/'
        client = self._as(None)
        client.get(url)  # calienta cachés de configuración
        with self.assertNumQueries(self._queries_for(client, url, products=2)):
            client.get(url)

    def _queries_for(self, client, url, *, products):
        """Las consultas de la misma lista con sólo `products` productos visibles."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        hidden = list(Product.objects.filter(company=self.company).order_by('pk')[products:])
        Product.objects.filter(pk__in=[p.pk for p in hidden]).update(is_active=False)
        with CaptureQueriesContext(connection) as captured:
            client.get(url)
        Product.objects.filter(pk__in=[p.pk for p in hidden]).update(is_active=True)
        return len(captured)

    def test_a_site_path_is_a_valid_address_for_a_product_without_gallery(self):
        res = self._patch_product(self.manager, {'image_url': '/assets/products/telefono.png'})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['image_url'], '/assets/products/telefono.png')

    def test_code_is_never_an_address(self):
        for value in ('javascript:alert(1)', 'data:image/png;base64,AAAA', '//evil.example/x.png'):
            with self.subTest(value):
                self.assertEqual(self._patch_product(self.manager, {'image_url': value}).status_code, 400)

    def test_an_uploaded_image_is_placed_through_the_gallery_not_typed(self):
        foreign = storefront_media.upload(
            company=self.other, actor=self.outsider, uploaded=upload_file(png_with_transparency()),
        )
        own = storefront_media.upload(
            company=self.company, actor=self.manager, uploaded=upload_file(png_with_transparency()),
        )
        for address in (foreign.url, own.url, f'https://tienda.example{foreign.url}'):
            with self.subTest(address):
                res = self._patch_product(self.manager, {'image_url': address})
                self.assertEqual(res.status_code, 400, res.content)
                self.assertIn('image_url', res.json())

    def test_with_a_gallery_the_address_is_not_edited_by_hand(self):
        added = self._add(self.manager).json()

        same = self._patch_product(self.manager, {'name': 'Teléfono 2', 'image_url': added['url']})
        self.assertEqual(same.status_code, 200, same.content)

        other = self._patch_product(self.manager, {'image_url': LEGACY})
        self.assertEqual(other.status_code, 400)
        self.assertIn('image_url', other.json())
        self.product.refresh_from_db()
        self.assertEqual(self.product.image_url, added['url'])

    def test_the_admin_product_carries_its_gallery(self):
        added = self._add(self.manager, alt_text='Frontal').json()
        res = self._as(self.manager).get(
            f'/api/admin/products/{self.product.pk}/?company={self.company.pk}')
        self.assertEqual(res.status_code, 200)
        self.assertEqual([row['id'] for row in res.json()['images']], [added['id']])


class ProductImageCleanupTest(ProductMediaBase):
    def test_an_image_in_a_gallery_is_never_swept_as_unused(self):
        self._add(self.manager)
        loose = storefront_media.upload(
            company=self.company, actor=self.manager, uploaded=upload_file(png_with_transparency()),
        )
        StorefrontImage.objects.update(created_at='2020-01-01T00:00:00Z')

        self.assertEqual([image.pk for image in storefront_media.unplaced(1)], [loose.pk])

    def test_a_failed_placement_does_not_leave_the_file_behind(self):
        from unittest import mock

        with mock.patch('store.product_media._audit', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                self._add(self.manager)

        self.assertEqual(ProductImage.objects.count(), 0)
        self.assertEqual(StorefrontImage.objects.count(), 0)
        self.product.refresh_from_db()
        self.assertEqual(self.product.image_url, '')

    def test_a_category_cannot_point_at_a_gallery_image_of_another_company(self):
        added = self._add(self.manager).json()
        category = Category.objects.create(company=self.other, name='Ajena', slug='ajena')
        res = self._as(self.outsider).patch(
            f'/api/admin/categories/{category.pk}/?company={self.other.pk}',
            {'image_url': added['url']}, format='json',
        )
        self.assertEqual(res.status_code, 400, res.content)
