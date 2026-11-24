"""
IMG-GATE-01 — a device variant shows on the shop only pictures whose licence was verified.

Owner, 2026-11-23: «Las nuevas fotografías requieren procedencia y autorización comprobables antes de publicarse.»
Finish pictures were already gated (`finish_media.is_public`). A variant's OWN pictures (its gallery, `ProductImage`,
and its single address, `Product.image_url`) were not, and they took priority over the finish: an unverified picture,
or one of another colour, passed. What is fixed here:

  * every public surface of a DEVICE VARIANT (a product with a model) — the model page, `/api/products/`, the cart
    and the checkout that reads it, the buyer's orders, the mobile API — shows its own picture only when it is a
    managed upload of the same company whose licence was verified; an external (hotlinked) address is unverified;
  * otherwise the verified finish of the SAME colour, else nothing and `image_pending` («Imagen pendiente»). Never
    another colour's photo;
  * a product WITHOUT a model (an accessory, a cable) is unchanged: it has no finish to fall back on, and hiding all
    of them at deploy could empty the shop. They are listed by IMG-HEALTH for the owner to decide;
  * the panel shows each gallery picture's licence state and verifies it with the finish images' flow.
"""
import csv
import io
from decimal import Decimal
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings

from store import device_catalog, product_media, storefront_media
from store.models import (
    AdminAuditLog, ModelFinishImage, Order, OrderItem, Product, ProductImage, ProductModel, StorefrontImage,
)
from store.serializers import OrderSerializer, closed_product_payload
from store.test_device_commerce import _client
from store.test_finish_images import _ROOT, OWN, SUPPLIER, FinishApiBase
from store.test_storefront_media import jpeg, png_with_transparency
from store.tests import _p2d_member, _p3_company, _prod

EXTERNAL = 'https://cdn.example.com/fotos/azul.png'


@override_settings(EVIDENCE_STORAGE_BACKEND='filesystem', EVIDENCE_STORAGE_ROOT=_ROOT,
                   ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class GateBase(FinishApiBase):
    def setUp(self):
        super().setUp()
        self.new128blue = self.variant('Teléfono X 128 GB Azul', 'tx-128-azul', '3100.00', [self.gb128, self.blue])
        self.public = _client(host=self.host)

    # --- the variant's own pictures -------------------------------------------------------------------------------

    def own(self, product, raw=None):
        """A picture uploaded to the variant's own gallery from the panel: no provenance, licence unchecked."""
        return product_media.add_image(product=product, actor=self.staff, uploaded=SimpleUploadedFile(
            'propia.png', raw or png_with_transparency(), content_type='image/png'))

    def image_url(self, image, suffix=''):
        return f'/api/admin/products/{image.product_id}/images/{image.pk}/{suffix}{self.q}'

    def patch_own(self, image, body, api=None):
        with self.captureOnCommitCallbacks(execute=True):
            return (api or self.api).patch(self.image_url(image), body, format='json')

    def verify_own(self, image, provenance=None):
        res = self.patch_own(image, {**(OWN if provenance is None else provenance), 'license_verified': True})
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    # --- what the shop answers ------------------------------------------------------------------------------------

    def page_row(self, product):
        res = self.public.get(f'/api/catalog/models/{self.model.slug}/')
        self.assertEqual(res.status_code, 200, res.content)
        return {row['product_id']: row for row in res.json()['variants']}[product.pk]

    def product_api(self, product):
        res = self.public.get(f'/api/products/?slug={product.slug}')
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        rows = body['results'] if isinstance(body, dict) else body
        self.assertEqual(len(rows), 1, rows)
        return rows[0]

    def v1_product(self, product):
        res = self.public.get(f'/api/v1/storefront/{self.company.slug}/products/{product.slug}/')
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def cart_line(self, product, session='carrito-gate'):
        self.receive(product)
        res = self.public.post('/api/cart/add/', {'session_key': session, 'product': product.pk}, format='json')
        self.assertIn(res.status_code, (200, 201), res.content)
        res = self.public.get(f'/api/cart/?session_key={session}')
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        rows = body['results'] if isinstance(body, dict) else body
        return {row['product']['id']: row['product'] for row in rows}[product.pk]

    def everywhere(self, product):
        """(model page gallery, product API, v1 API, cart line, `variant_picture`) — the shop's surfaces."""
        product.refresh_from_db()
        return {
            'page': self.page_row(product),
            'product': self.product_api(product),
            'v1': self.v1_product(product),
            'cart': self.cart_line(product),
            'picture': device_catalog.variant_picture(product),
        }

    def assert_shows(self, product, url, *, absent=()):
        seen = self.everywhere(product)
        self.assertEqual([image['url'] for image in seen['page']['gallery']][:1], [url])
        self.assertFalse(seen['page']['image_pending'])
        # The page's `image_url`/`images` are the variant's OWN pictures (verified), empty when the finish is shown.
        self.assertIn(seen['page']['image_url'], (url, ''))
        self.assertEqual((seen['product']['image_url'], seen['product']['image_pending']), (url, False))
        self.assertEqual(seen['product']['images'][0]['url'], url)
        self.assertEqual(seen['v1']['image_url'], url)
        self.assertEqual((seen['cart']['image_url'], seen['cart']['image_pending']), (url, False))
        self.assertEqual(seen['picture'][:2], (url, False))
        for hidden in absent:
            self.assertNotIn(hidden, str(seen))

    def assert_pending(self, product, *, absent=()):
        seen = self.everywhere(product)
        self.assertEqual((seen['page']['gallery'], seen['page']['image_pending']), ([], True))
        self.assertEqual((seen['page']['image_url'], seen['page']['images']), ('', []))
        self.assertEqual((seen['product']['image_url'], seen['product']['images'], seen['product']['image_pending']),
                         ('', [], True))
        self.assertEqual((seen['v1']['image_url'], seen['v1']['images']), ('', []))
        self.assertEqual((seen['cart']['image_url'], seen['cart']['image_pending']), ('', True))
        self.assertEqual(seen['picture'], ('', True, False))
        for hidden in absent:
            self.assertNotIn(hidden, str(seen))


class VariantOwnPictureGateTest(GateBase):
    def test_an_unverified_own_picture_is_hidden_and_the_verified_finish_of_its_colour_shown(self):
        own = self.own(self.new128blue)
        finish = self.published(self.blue)
        self.assert_shows(self.new128blue, finish['url'], absent=[own.image_url])

    def test_an_unverified_own_picture_without_a_finish_is_pending(self):
        own = self.own(self.new128blue)
        self.uploaded(self.blue, SUPPLIER)          # a finish nobody verified: not public either
        self.assert_pending(self.new128blue, absent=[own.image_url])

    def test_a_verified_own_picture_is_shown_before_the_finish(self):
        own = self.own(self.new128blue)
        self.published(self.blue)
        self.verify_own(own, SUPPLIER)
        self.assert_shows(self.new128blue, own.image_url)

    def test_only_the_verified_pictures_of_its_own_gallery_are_shown(self):
        first = self.own(self.new128blue)
        second = self.own(self.new128blue, raw=jpeg())
        self.verify_own(second)
        seen = self.everywhere(self.new128blue)
        self.assertEqual([image['url'] for image in seen['page']['gallery']], [second.image_url])
        self.assertEqual([image['url'] for image in seen['product']['images']], [second.image_url])
        # The primary (the first) waits for its check: the first verified one opens the page.
        self.assertEqual(seen['product']['image_url'], second.image_url)
        self.assertNotIn(first.image_url, str(seen))

    def test_an_external_own_address_is_never_shown_for_a_variant(self):
        Product.objects.filter(pk=self.new128blue.pk).update(image_url=EXTERNAL)
        self.assert_pending(self.new128blue, absent=[EXTERNAL])
        finish = self.published(self.blue)
        self.assert_shows(self.new128blue, finish['url'], absent=[EXTERNAL])

    def test_an_external_address_in_its_gallery_is_never_shown(self):
        ProductImage.objects.create(company=self.company, product=self.new128blue, image_url=EXTERNAL,
                                    is_primary=True, sort_order=0)
        Product.objects.filter(pk=self.new128blue.pk).update(image_url=EXTERNAL)
        self.assert_pending(self.new128blue, absent=[EXTERNAL])

    def test_a_verified_own_single_address_is_shown(self):
        stored = self.stored()
        StorefrontImage.objects.filter(pk=stored.pk).update(source_kind=StorefrontImage.SOURCE_OWN_PHOTO)
        Product.objects.filter(pk=self.new128blue.pk).update(image_url=stored.url)
        self.assert_pending(self.new128blue, absent=[stored.url])
        from django.utils import timezone
        StorefrontImage.objects.filter(pk=stored.pk).update(license_verified_at=timezone.now())
        self.assert_shows(self.new128blue, stored.url)

    def test_another_colours_photo_is_never_used(self):
        black = self.published(self.black)
        own = self.own(self.new128blue)             # unverified: hidden
        self.assert_pending(self.new128blue, absent=[black['url'], own.image_url])

    def test_another_companys_verified_file_is_never_a_variants_picture(self):
        from django.utils import timezone

        other = _p3_company('gate-otra', 'Otra')
        foreign = self.stored(company=other)
        StorefrontImage.objects.filter(pk=foreign.pk).update(source_kind=StorefrontImage.SOURCE_OWN_PHOTO,
                                                             license_verified_at=timezone.now())
        Product.objects.filter(pk=self.new128blue.pk).update(image_url=foreign.url)
        self.assert_pending(self.new128blue, absent=[foreign.url])

    def test_a_verified_demo_own_picture_is_shown_and_said(self):
        own = self.own(self.new128blue)
        self.verify_own(own, {'source_kind': 'demo', 'license': 'Recurso de demostración interno'})
        seen = self.everywhere(self.new128blue)
        self.assertTrue(seen['page']['gallery'][0]['is_demo'])
        self.assertTrue(seen['product']['image_is_demo'])
        self.assertTrue(seen['cart']['image_is_demo'])
        self.assertEqual(seen['picture'], (own.image_url, False, True))
        self.assertNotIn('Recurso de demostración interno', str(seen))

    def test_unverifying_hides_it_again(self):
        own = self.own(self.new128blue)
        self.verify_own(own)
        self.assertEqual(device_catalog.variant_picture(self.new128blue)[0], own.image_url)
        self.assertEqual(self.patch_own(own, {'license_verified': False}).status_code, 200)
        self.assert_pending(self.new128blue, absent=[own.image_url])

    def test_a_used_variant_is_gated_too(self):
        Product.objects.filter(pk=self.used128.pk).update(image_url=EXTERNAL, is_published_online=True)
        product = Product.objects.get(pk=self.used128.pk)
        self.assertEqual(device_catalog.variant_picture(product), ('', True, False))
        res = self.public.get(f'/api/products/?slug={product.slug}')
        rows = res.json()['results'] if isinstance(res.json(), dict) else res.json()
        for row in rows:
            self.assertNotIn(EXTERNAL, str(row))

    def test_the_product_list_reads_the_pictures_once_for_every_variant(self):
        from django.core.cache import cache
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.published(self.black)
        for product in (self.new128, self.new256, self.new128blue):
            self.own(product)
        for index in range(3):
            size = device_catalog.create_option(company=self.company, attribute_id=self.capacity.pk,
                                                label=f'{index + 1} TB')
            extra = self.variant(f'Teléfono X {index + 1} TB Azul', f'tx-extra-{index}', '3000.00', [size, self.blue])
            self.own(extra)
        url = '/api/products/'

        def queries():
            cache.clear()
            with CaptureQueriesContext(connection) as captured:
                body = self.public.get(url).json()
            self.assertGreaterEqual(len(body['results'] if isinstance(body, dict) else body), 6)
            return len(captured.captured_queries)

        cache.clear()
        with mock.patch('store.device_catalog.variant_galleries', wraps=device_catalog.variant_galleries) as batch:
            self.public.get(url)
        # Every variant of the list in ONE batch …
        self.assertEqual(batch.call_count, 1)
        self.assertEqual(len(batch.call_args.args[0]), 6)
        # … whose cost does not depend on how many pictures they have.
        with_pictures = queries()
        ProductImage.objects.all().delete()
        self.assertEqual(queries(), with_pictures)


class OrdersAndClosedLinesTest(GateBase):
    def order_with(self, product):
        order = Order.objects.create(company=self.company, fulfillment_branch=self.branch,
                                     customer_email='x@example.com', total=Decimal('3100.00'))
        OrderItem.objects.create(order=order, product=product, quantity=1, price=product.price)
        return order

    def test_the_buyers_order_shows_the_gated_picture(self):
        own = self.own(self.new128blue)
        finish = self.published(self.blue)
        data = OrderSerializer(self.order_with(self.new128blue)).data
        line = data['items'][0]['product']
        self.assertEqual((line['image_url'], line['image_pending']), (finish['url'], False))
        self.assertNotIn(own.image_url, str(data))

    def test_a_closed_variant_line_shows_the_gated_picture(self):
        own = self.own(self.new128blue)
        Product.objects.filter(pk=self.new128blue.pk).update(is_published_online=False)
        product = Product.objects.get(pk=self.new128blue.pk)
        self.assertEqual(closed_product_payload(product)['image_url'], '')
        self.verify_own(own)
        self.assertEqual(closed_product_payload(product)['image_url'], own.image_url)

    def test_the_v1_order_line_shows_the_gated_picture(self):
        from store.v1_customer_serializers import V1CustomerOrderItemSerializer

        own = self.own(self.new128blue)
        order = self.order_with(self.new128blue)
        line = V1CustomerOrderItemSerializer(order.items.get()).data
        self.assertEqual(line['image_url'], '')
        self.assertNotIn(own.image_url, str(line))


class NonVariantProductsUnchangedTest(GateBase):
    """Decision to confirm (owner): a product without a model keeps its pictures as before — reported, not hidden."""

    def test_an_accessory_keeps_its_unverified_and_external_pictures(self):
        accessory = _prod(self.company, 'Funda', 'funda-gate', price='50.00', inventory=5)
        own = self.own(accessory)
        res = self.public.get(f'/api/products/?slug={accessory.slug}')
        body = res.json()
        row = (body['results'] if isinstance(body, dict) else body)[0]
        self.assertEqual(row['image_url'], own.image_url)
        self.assertEqual([image['url'] for image in row['images']], [own.image_url])
        self.assertNotIn('image_pending', row)
        self.assertNotIn('image_is_demo', row)

        Product.objects.filter(pk=accessory.pk).update(image_url=EXTERNAL)
        ProductImage.objects.filter(product=accessory).delete()
        body = self.public.get(f'/api/products/?slug={accessory.slug}').json()
        self.assertEqual((body['results'] if isinstance(body, dict) else body)[0]['image_url'], EXTERNAL)


class PosUnaffectedTest(GateBase):
    def test_the_till_chips_stay_finish_only_and_verified(self):
        """#396: the till shows only verified finish pictures; a variant's own picture never reaches a chip."""
        till, _ = _p2d_member(self.company, 'gate-caja', ['company.view', 'sales.pos.use'])
        own = self.own(self.new128blue)
        self.verify_own(own)
        url = (f'/api/admin/pos/models/{self.model.pk}/configurations/'
               f'?company={self.company.pk}&branch={self.branch.pk}')
        res = _client(till).get(url)
        self.assertEqual(res.status_code, 200, res.content)
        colour = next(a for a in res.json()['attributes'] if a['role'] == 'color')
        self.assertEqual({o['label']: o['thumbnail'] for o in colour['options']}, {'Negro': None, 'Azul': None})


class ProductImageLicenceApiTest(GateBase):
    """The panel: each gallery picture says its licence state and is verified with the finish images' flow."""

    def listing(self, product, api=None):
        res = (api or self.api).get(f'/api/admin/products/{product.pk}/images/{self.q}')
        self.assertEqual(res.status_code, 200, res.content)
        return {row['id']: row for row in res.json()['results']}

    def test_the_listing_says_the_source_and_whether_the_shop_shows_it(self):
        own = self.own(self.new128blue)
        row = self.listing(self.new128blue)[own.pk]
        self.assertEqual(row['source']['kind'], 'unknown')
        self.assertFalse(row['source']['license_verified'])
        self.assertIs(row['requires_license'], True)
        self.assertIs(row['public'], False)
        accessory = _prod(self.company, 'Funda', 'funda-panel', price='50.00')
        loose = self.own(accessory)
        row = self.listing(accessory)[loose.pk]
        self.assertIs(row['requires_license'], False)
        self.assertIs(row['public'], True)
        # The product's detail carries the same.
        detail = self.api.get(f'/api/admin/products/{self.new128blue.pk}/{self.q}').json()
        self.assertIs(detail['images'][0]['public'], False)

    def test_an_unknown_source_must_be_registered_before_it_is_verified(self):
        own = self.own(self.new128blue)
        res = self.patch_own(own, {'license_verified': True})
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['field'], 'source_kind')
        self.assertIsNone(StorefrontImage.objects.get(public_id=own.image_url.rsplit('/', 1)[-1]).license_verified_at)

    def test_registering_the_source_and_verifying_records_who_and_when(self):
        own = self.own(self.new128blue)
        res = self.patch_own(own, SUPPLIER)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['source']['kind'], 'supplier_authorized')
        self.assertIs(res.json()['public'], False)
        body = self.verify_own(own, {})
        self.assertIs(body['public'], True)
        self.assertTrue(body['source']['license_verified'])
        self.assertTrue(body['source']['license_verified_by'])
        logs = AdminAuditLog.objects.filter(action='storefront_image_license_verified')
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs.get().company_id, self.company.pk)
        self.assertEqual(logs.get().target_type, 'product')
        self.assertEqual(AdminAuditLog.objects.filter(action='storefront_image_provenance_changed').count(), 1)

    def test_changing_the_provenance_clears_the_check(self):
        own = self.own(self.new128blue)
        self.verify_own(own, SUPPLIER)
        res = self.patch_own(own, {'license': 'Otra autorización'})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(res.json()['source']['license_verified'])
        self.assertIs(res.json()['public'], False)

    def test_a_bad_provenance_or_flag_is_refused(self):
        own = self.own(self.new128blue)
        for body in ({'source_kind': 'apple'}, {'source_kind': 'supplier_authorized', 'license': ''},
                     {'license_verified': 'yes'}, {'source_url': 'javascript:alert(1)', **OWN}, {'license': 5}):
            with self.subTest(body):
                self.assertEqual(self.patch_own(own, body).status_code, 400)

    def test_reading_is_not_enough_to_verify_and_another_company_is_not_found(self):
        own = self.own(self.new128blue)
        reader, _ = _p2d_member(self.company, 'gate-lector', ['company.view', 'products.view'])
        res = self.patch_own(own, {**OWN, 'license_verified': True}, api=_client(reader))
        self.assertIn(res.status_code, (403, 404))
        other = _p3_company('gate-panel-otra', 'Otra')
        stranger, _ = _p2d_member(other, 'gate-ajeno', ['company.view', 'products.view', 'products.manage'])
        res = _client(stranger).patch(f'/api/admin/products/{own.product_id}/images/{own.pk}/?company={other.pk}',
                                      {**OWN, 'license_verified': True}, format='json')
        self.assertEqual(res.status_code, 404)
        self.assertIsNone(StorefrontImage.objects.get(public_id=own.image_url.rsplit('/', 1)[-1]).license_verified_at)

    def test_an_external_gallery_address_cannot_be_verified(self):
        image = ProductImage.objects.create(company=self.company, product=self.new128blue, image_url=EXTERNAL,
                                            is_primary=True, sort_order=0)
        row = self.listing(self.new128blue)[image.pk]
        self.assertIs(row['public'], False)
        self.assertEqual(self.patch_own(image, {**OWN, 'license_verified': True}).status_code, 404)

    def test_the_licensing_report_counts_a_variants_own_photo_until_it_is_verified(self):
        own = self.own(self.new128blue)
        self.patch_own(own, OWN)
        report = lambda: self.api.get(f'/api/admin/catalog/image-sources/{self.q}').json()['licensing']['uploads']
        self.assertEqual(report()['unverified_license'], 1)
        self.verify_own(own)
        self.assertEqual(report()['unverified_license'], 0)
        # An accessory's own photo has no licence to check (it is not gated): not counted.
        loose = self.own(_prod(self.company, 'Funda', 'funda-licencias', price='50.00'))
        self.patch_own(loose, OWN)
        self.assertEqual(report()['unverified_license'], 0)

    def test_the_finish_flow_still_verifies_its_pictures(self):
        image = self.uploaded(self.blue, SUPPLIER)
        self.assertIs(self.verify(image)['public'], True)
        self.assertEqual(AdminAuditLog.objects.filter(action='storefront_image_license_verified').get().target_type,
                         'product_model')


class ImageHealthTest(GateBase):
    """IMG-HEALTH (read-only): each class of problem is found, nothing is deleted, one company only."""

    def report(self, **kwargs):
        from store import image_health
        return image_health.report(self.company, **kwargs)

    def issues(self, data, issue):
        return [row for row in data['rows'] if row['issue'] == issue]

    def test_a_managed_file_missing_from_storage_is_broken(self):
        own = self.own(self.new128blue)
        stored = StorefrontImage.objects.get(public_id=own.image_url.rsplit('/', 1)[-1])
        from store import evidence_storage
        evidence_storage.get_storage().delete(stored.storage_key)
        rows = self.issues(self.report(), 'broken_file')
        self.assertEqual([(row['slot'], row['object_id'], row['public_id']) for row in rows],
                         [('product_gallery', self.new128blue.pk, stored.public_id)])
        self.assertTrue(StorefrontImage.objects.filter(pk=stored.pk).exists())

    def test_a_managed_address_without_its_file_row_is_broken(self):
        ProductModel.objects.filter(pk=self.model.pk).update(image_url='/api/storefront/images/' + 'e' * 32)
        rows = self.issues(self.report(), 'broken_file')
        self.assertEqual([(row['slot'], row['object_id']) for row in rows], [('model_cover', self.model.pk)])

    def test_external_hotlinks_by_host(self):
        Product.objects.filter(pk=self.new128.pk).update(image_url='https://cdn.example.com/a.png')
        ProductModel.objects.filter(pk=self.model.pk).update(image_url='https://img.example.org/b.png')
        data = self.report()
        rows = self.issues(data, 'external_hotlink')
        self.assertEqual(sorted(row['host'] for row in rows), ['cdn.example.com', 'img.example.org'])
        self.assertEqual(data['summary']['external_hosts'],
                         [{'host': 'cdn.example.com', 'images': 1}, {'host': 'img.example.org', 'images': 1}])
        self.assertNotIn('https://cdn.example.com/a.png', str(rows))     # the host, never the address

    def test_unverified_public_pictures_are_the_ones_the_shop_still_shows(self):
        accessory = _prod(self.company, 'Funda', 'funda-salud', price='50.00')
        loose = self.own(accessory)
        self.own(self.new128blue)                  # a variant's: hidden by the gate, not «public»
        self.uploaded(self.blue, SUPPLIER)         # a finish nobody verified: not public either
        rows = self.issues(self.report(), 'unverified_public')
        self.assertEqual([(row['slot'], row['object_id'], row['public_id']) for row in rows],
                         [('product_gallery', accessory.pk, loose.image_url.rsplit('/', 1)[-1])])

    def test_the_same_file_on_two_colours_of_one_model(self):
        stored = self.stored()
        self.place(self.black, stored=stored, primary=True)
        self.place(self.blue, stored=stored, primary=True)
        rows = self.issues(self.report(), 'shared_across_colours')
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['model_id'], rows[0]['public_id']), (self.model.pk, stored.public_id))
        self.assertIn('Azul', rows[0]['colour'])
        self.assertIn('Negro', rows[0]['colour'])

    def test_a_variant_picture_and_another_colours_finish_sharing_a_file(self):
        own = self.own(self.new128blue)
        stored = StorefrontImage.objects.get(public_id=own.image_url.rsplit('/', 1)[-1])
        self.place(self.black, stored=stored, primary=True)
        rows = self.issues(self.report(), 'shared_across_colours')
        self.assertEqual([row['public_id'] for row in rows], [stored.public_id])

    def test_published_variants_without_a_public_photo(self):
        self.published(self.black)
        own = self.own(self.new128blue)
        data = self.report()
        rows = self.issues(data, 'variant_without_photo')
        self.assertEqual([row['object_id'] for row in rows], [self.new128blue.pk])
        self.assertIn('own_hidden', rows[0]['detail'])
        self.assertEqual(data['summary']['variant_without_photo'], 1)
        self.assertEqual(data['summary']['variants_hidden_by_gate'], 1)
        self.verify_own(own)
        self.assertEqual(self.issues(self.report(), 'variant_without_photo'), [])

    def test_another_companys_catalogue_is_never_read(self):
        other = _p3_company('salud-otra', 'Otra')
        foreign = _prod(other, 'Ajeno', 'ajeno-salud')
        Product.objects.filter(pk=foreign.pk).update(image_url='https://ajeno.example/x.png')
        self.assertNotIn('ajeno.example', str(self.report()))

    def test_the_command_writes_a_csv_and_deletes_nothing(self):
        own = self.own(self.new128blue)
        Product.objects.filter(pk=self.new128.pk).update(image_url='https://cdn.example.com/a.png')
        counts = (StorefrontImage.objects.count(), ProductImage.objects.count(), ModelFinishImage.objects.count())
        out, err = io.StringIO(), io.StringIO()
        call_command('report_image_health', '--company', self.company.slug, stdout=out, stderr=err)
        rows = list(csv.DictReader(io.StringIO(out.getvalue())))
        self.assertEqual(set(rows[0]), set(__import__('store.image_health', fromlist=['COLUMNS']).COLUMNS))
        self.assertIn('external_hotlink', {row['issue'] for row in rows})
        self.assertIn('variant_without_photo', {row['issue'] for row in rows})
        self.assertIn(self.company.slug, err.getvalue())
        self.assertEqual(counts, (StorefrontImage.objects.count(), ProductImage.objects.count(),
                                  ModelFinishImage.objects.count()))
        self.assertTrue(StorefrontImage.objects.filter(public_id=own.image_url.rsplit('/', 1)[-1]).exists())

    def test_the_command_refuses_an_unknown_company(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            call_command('report_image_health', '--company', 'no-existe', stdout=io.StringIO(),
                         stderr=io.StringIO())

    def test_the_admin_report_needs_products_view_and_is_one_company(self):
        Product.objects.filter(pk=self.new128.pk).update(image_url='https://cdn.example.com/a.png')
        res = self.api.get(f'/api/admin/catalog/image-health/{self.q}')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['summary']['external_hotlink'], 1)
        self.assertIn('rows', res.json())
        nobody, _ = _p2d_member(self.company, 'salud-nadie', ['company.view'])
        self.assertIn(_client(nobody).get(f'/api/admin/catalog/image-health/{self.q}').status_code, (403, 404))


# ---------------------------------------------------------------------------
# Review of #408
# ---------------------------------------------------------------------------

PICTURE_TABLES = ('store_productimage', 'store_modelfinishimage', 'store_storefrontimage', 'store_productmodel',
                  'store_productvariantoption')


class OrderListBatchTest(GateBase):
    """Review #1: a buyer's order list reads every line's picture in ONE batch (no query per line)."""

    def orders(self, count, lines):
        products = [self.new128, self.new256, self.new128blue]
        made = []
        for _ in range(count):
            order = Order.objects.create(company=self.company, fulfillment_branch=self.branch,
                                         customer_email='x@example.com', total=Decimal('1.00'))
            for product in products[:lines]:
                OrderItem.objects.create(order=order, product=product, quantity=1, price=product.price)
            made.append(order.pk)
        # What the order views prefetch (`OrderViewSet`, `V1CustomerOrderViewSet`).
        return Order.objects.filter(pk__in=made).order_by('pk').prefetch_related(
            'items__product', 'items__product__images', 'items__product__product_model')

    def serialise(self, serializer_class, queryset):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as captured:
            data = serializer_class(queryset, many=True).data
        # The picture's tables only: a line's other costs (its rating, its offer) are the product payload's own.
        pictures = [q for q in captured.captured_queries
                    if any(f'"{table}"' in q['sql'] for table in PICTURE_TABLES)]
        return data, len(pictures)

    def test_web_and_v1_order_lists_read_the_pictures_once(self):
        from store.v1_customer_serializers import V1CustomerOrderSerializer

        self.published(self.black)
        self.own(self.new128blue)
        Product.objects.filter(pk=self.new256.pk).update(is_published_online=False)     # a closed line
        for serializer_class in (OrderSerializer, V1CustomerOrderSerializer):
            with self.subTest(serializer_class.__name__):
                with mock.patch('store.device_catalog.variant_galleries',
                                wraps=device_catalog.variant_galleries) as batch, \
                        mock.patch('store.device_catalog.variant_picture') as single:
                    data, _ = self.serialise(serializer_class, self.orders(3, 3))
                self.assertEqual(batch.call_count, 1)
                single.assert_not_called()
                text = str(data)
                self.assertNotIn(ProductImage.objects.get(product=self.new128blue).image_url, text)

    def test_more_orders_and_lines_cost_no_more_picture_queries(self):
        from store.v1_customer_serializers import V1CustomerOrderSerializer

        self.published(self.black)
        Product.objects.filter(pk=self.new256.pk).update(is_published_online=False)
        for serializer_class in (OrderSerializer, V1CustomerOrderSerializer):
            with self.subTest(serializer_class.__name__):
                _, one_line = self.serialise(serializer_class, self.orders(2, 1))
                _, three_lines = self.serialise(serializer_class, self.orders(4, 3))
                self.assertEqual(three_lines, one_line)
                self.assertGreater(one_line, 0)

    def test_one_order_alone_reads_its_lines_once(self):
        with mock.patch('store.device_catalog.variant_galleries', wraps=device_catalog.variant_galleries) as batch:
            OrderSerializer(self.orders(1, 3).get()).data
        self.assertEqual(batch.call_count, 1)


class ColourlessCoverGateTest(GateBase):
    """Review #2: the cover a colourless model's variant falls back on passes the same licence gate."""

    def setUp(self):
        super().setUp()
        self.plain = device_catalog.create_model(company=self.company, name='Cargador Z')
        product = self.variant('Cargador Z 20 W', 'cz-20', '90.00', [self.gb128])
        Product.objects.filter(pk=product.pk).update(product_model=self.plain)
        self.charger = Product.objects.get(pk=product.pk)

    def cover(self, address):
        ProductModel.objects.filter(pk=self.plain.pk).update(image_url=address)

    def test_an_external_or_unverified_cover_is_pending(self):
        for address in (EXTERNAL, self.stored().url):
            with self.subTest(address):
                self.cover(address)
                self.assertEqual(device_catalog.variant_picture(self.charger), ('', True, False))

    def test_a_verified_cover_is_shown_with_its_real_demo_flag(self):
        from django.utils import timezone

        stored = self.stored()
        StorefrontImage.objects.filter(pk=stored.pk).update(source_kind=StorefrontImage.SOURCE_DEMO,
                                                            license='Demostración', license_verified_at=timezone.now())
        self.cover(stored.url)
        self.assertEqual(device_catalog.variant_picture(self.charger), (stored.url, False, True))

    def test_another_companys_verified_cover_is_pending(self):
        from django.utils import timezone

        foreign = self.stored(company=_p3_company('portada-otra', 'Otra'))
        StorefrontImage.objects.filter(pk=foreign.pk).update(license_verified_at=timezone.now())
        self.cover(foreign.url)
        self.assertEqual(device_catalog.variant_picture(self.charger), ('', True, False))


class DemoFlagInProductImagesTest(GateBase):
    """Review #3: each picture of a variant on `/api/products/` says whether it is a demo resource."""

    def test_the_images_carry_is_demo(self):
        own = self.own(self.new128blue)
        self.verify_own(own, {'source_kind': 'demo', 'license': 'Recurso de demostración interno'})
        row = self.product_api(self.new128blue)
        self.assertEqual([(image['url'], image['is_demo']) for image in row['images']], [(own.image_url, True)])
        finish = self.published(self.black)
        row = self.product_api(self.new128)
        self.assertEqual([(image['url'], image['is_demo']) for image in row['images']], [(finish['url'], False)])


class ImageHealthStorageTest(GateBase):
    """Review #4: one storage client per report, and no storage checks above the configured number of files."""

    def test_the_storage_is_built_once(self):
        from store import evidence_storage, image_health

        self.own(self.new128blue)
        self.own(self.new128, raw=jpeg())
        with mock.patch('store.evidence_storage.get_storage', wraps=evidence_storage.get_storage) as built:
            data = image_health.report(self.company)
        self.assertEqual(built.call_count, 1)
        self.assertTrue(data['summary']['storage_checked'])

    def test_above_the_cap_the_storage_is_not_asked(self):
        from store import image_health

        self.own(self.new128blue)
        with self.settings(IMAGE_HEALTH_MAX_STORAGE_CHECKS=0), \
                mock.patch('store.evidence_storage.get_storage') as built:
            data = image_health.report(self.company)
        built.assert_not_called()
        self.assertFalse(data['summary']['storage_checked'])
        self.assertEqual(self.issues_of(data, 'broken_file'), [])

    @staticmethod
    def issues_of(data, issue):
        return [row for row in data['rows'] if row['issue'] == issue]

    def test_the_endpoint_is_throttled(self):
        from store.device_catalog_views import AdminCatalogImageHealthView
        from store.throttles import AdminProductsThrottle

        self.assertIn(AdminProductsThrottle, AdminCatalogImageHealthView.throttle_classes)


class PublicationImageRuleTest(GateBase):
    """Review #6: with «require a picture to publish», a variant has one only if the shop may show it."""

    def setUp(self):
        super().setUp()
        from store.models import CompanySettings

        CompanySettings.objects.update_or_create(company=self.company, defaults={'publication_requires_image': True})

    def test_an_unverified_own_picture_is_not_a_picture_to_publish(self):
        from store import product_publication

        own = self.own(self.new128blue)
        product = Product.objects.get(pk=self.new128blue.pk)
        self.assertIn('no_image', product_publication.publication_problems(product))
        self.verify_own(own)
        self.assertNotIn('no_image', product_publication.publication_problems(product))

    def test_ready_to_publish_leaves_out_a_variant_without_a_public_picture(self):
        from store import product_publication

        from store.models import Category

        category = Category.objects.create(company=self.company, name='Teléfonos', slug='telefonos-gate')
        Product.objects.filter(pk__in=[self.new128.pk, self.new128blue.pk]).update(is_published_online=False,
                                                                                    category=category)
        self.published(self.black)                  # new128 (black) has a public picture
        self.own(self.new128blue)                   # unverified: none
        ready = set(product_publication.ready_to_publish(Product.objects.filter(company=self.company), self.company)
                    .values_list('pk', flat=True))
        self.assertIn(self.new128.pk, ready)
        self.assertNotIn(self.new128blue.pk, ready)

    def test_the_admin_list_reads_the_pictures_once(self):
        with mock.patch('store.device_catalog.variant_galleries', wraps=device_catalog.variant_galleries) as batch:
            res = self.api.get(f'/api/admin/products/{self.q}')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(batch.call_count, 1)
        body = res.json()
        rows = body['results'] if isinstance(body, dict) else body
        problems = {row['id']: row['publication_problems'] for row in rows}
        self.assertIn('no_image', problems[self.new128blue.pk])
