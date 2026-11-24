"""
UNIT-PHOTO-02 — the real photos of each used device (seminuevo).

What these tests hold:

  * a photo counts — for the web, for publishing, for a cart or an order line — only when it is ATTESTED («esta foto
    es de este equipo exacto») and its file is an OWN photo of the same company with its licence VERIFIED; anything
    else (unattested, unverified, another source, another company's file, an external address) does not;
  * publishing is refused with a clear reason: `no_photo` without photos, `photo_not_attested` with photos nobody
    confirmed; a published device whose photos are not attested leaves the web without being unpublished;
  * the panel's API: upload with its side (`view`, a defect says what it shows), primary (one per device), reorder,
    replace in place (order and primary kept, attestation cleared, old file released), delete (the next becomes the
    primary), attest / withdraw (`inventory.units.inspect`, sets the file's provenance `own_photo` + verified) —
    every write audited with the company; another company's device 404; a new device 409;
  * the public card shows only public photos, primary first, each with its side's label; the cart and the order line
    of a used device show THAT device's primary photo, never the variant's or its colour's picture, and nothing when
    it has none;
  * migration 0192 is additive and makes the first photo of each device its primary; nothing is attested.
"""
import atexit
import io
import json
import shutil
import tempfile
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from store import storefront_media, unit_media
from store import stock_unit_services as units
from store import used_unit_services as used_units
from store.models import AdminAuditLog, Order, OrderItem, StockUnit, StorefrontImage, UnitPhoto
from store.serializers import OrderSerializer
from store.test_device_commerce import _client
from store.test_storefront_media import png_with_transparency
from store.test_used_ready import UsedShop
from store.tests import _p3_company
from store.used_unit_test_support import real_photo

UNITS = '/api/admin/inventory/units/'
_ROOT = tempfile.mkdtemp(prefix='unit-photos-')
# Forked parallel workers share _ROOT; only the creating process removes it, at exit (test_parallel_tempdir_guard).
atexit.register(shutil.rmtree, _ROOT, True)


def upload(name='equipo.png'):
    return SimpleUploadedFile(name, png_with_transparency(), content_type='image/png')


@override_settings(EVIDENCE_STORAGE_BACKEND='filesystem', EVIDENCE_STORAGE_ROOT=_ROOT,
                   ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class PhotoShop(UsedShop, TestCase):
    def setUp(self):
        self.build('unit-photos')
        [self.unit] = self.receive(self.used128)
        units.update_unit(self.unit, sale_price='1999.00')
        used_units.set_inspection(self.unit, decision='approved', actor=self.inspector)
        self.unit.refresh_from_db()

    def ready(self):
        """Attest one real photo and publish: what the web needs."""
        real_photo(self.unit, self.inspector)
        used_units.set_publication(self.unit, published=True, actor=self.staff)
        self.unit.refresh_from_db()

    def card(self):
        cards = _client(host=self.host).get(f'/api/catalog/models/{self.model.slug}/').json()['used_units']
        return {c['id']: c for c in cards}.get(str(self.unit.public_id))

    def add(self, user=None, **data):
        body = {'file': upload(), **data}
        return _client(user or self.staff).post(f'{UNITS}{self.unit.pk}/photos/', body, format='multipart')

    def attest(self, photo_id, attested=True, user=None):
        return _client(user or self.inspector).post(
            f'{UNITS}{self.unit.pk}/photos/{photo_id}/attestation/', {'attested': attested}, format='json')

    def photos(self):
        return list(UnitPhoto.objects.filter(unit=self.unit).order_by('sort_order', 'pk'))


# ---------------------------------------------------------------------------
# The rule: what counts as a real photo
# ---------------------------------------------------------------------------

class RealPhotoRuleTest(PhotoShop):
    def test_only_an_attested_own_verified_photo_of_the_company_counts(self):
        other = _p3_company('unit-photos-otra', 'Otra')
        cases = {
            'unattested': dict(attested=False),
            'unverified': dict(verified=False),
        }
        for label, kwargs in cases.items():
            with self.subTest(label), transaction.atomic():
                real_photo(self.unit, self.inspector, **kwargs)
                self.assertEqual(used_units.real_photos(self.unit), 0, label)
                self.assertEqual(used_units.used_unit_publish_blockers(self.unit), ['photo_not_attested'])
                transaction.set_rollback(True)
        with self.subTest('another source'), transaction.atomic():
            photo = real_photo(self.unit, self.inspector)
            StorefrontImage.objects.filter(public_id=storefront_media.managed_public_id(photo.image_url)).update(
                source_kind=StorefrontImage.SOURCE_SUPPLIER_AUTHORIZED, license='Autorizado')
            self.assertEqual(used_units.real_photos(self.unit), 0)
            transaction.set_rollback(True)
        with self.subTest('another company'), transaction.atomic():
            photo = real_photo(self.unit, self.inspector)
            StorefrontImage.objects.filter(public_id=storefront_media.managed_public_id(photo.image_url)).update(
                company=other)
            self.assertEqual(used_units.real_photos(self.unit), 0)
            transaction.set_rollback(True)
        with self.subTest('external address'), transaction.atomic():
            UnitPhoto.objects.create(company=self.company, unit=self.unit, image_url='https://cdn.example.com/x.png',
                                     attested_at=timezone.now(), is_primary=True)
            self.assertEqual(used_units.real_photos(self.unit), 0)
            transaction.set_rollback(True)
        real_photo(self.unit, self.inspector)
        self.assertEqual(used_units.real_photos(self.unit), 1)
        self.assertEqual(used_units.used_unit_publish_blockers(self.unit), [])

    def test_the_filter_and_the_count_agree(self):
        real_photo(self.unit, self.inspector, attested=False)
        has = StockUnit.objects.filter(pk=self.unit.pk).filter(used_units.real_photos_q()).exists()
        self.assertEqual((has, used_units.real_photos(self.unit)), (False, 0))
        real_photo(self.unit, self.inspector)
        has = StockUnit.objects.filter(pk=self.unit.pk).filter(used_units.real_photos_q()).exists()
        self.assertEqual((has, used_units.real_photos(self.unit)), (True, 1))

    def test_publishing_says_no_photo_or_photo_not_attested(self):
        publish = lambda: _client(self.staff).post(f'{UNITS}{self.unit.pk}/publication/',  # noqa: E731
                                                   {'published': True}, format='json')
        res = publish()
        self.assertEqual((res.status_code, res.json()['reasons']), (409, ['no_photo']))
        real_photo(self.unit, self.inspector, attested=False)
        res = publish()
        self.assertEqual((res.status_code, res.json()['reasons']), (409, ['photo_not_attested']))
        self.assertIn('confirmada como de este equipo exacto', res.json()['detail'])
        UnitPhoto.objects.filter(unit=self.unit).update(attested_at=timezone.now())
        self.assertEqual(publish().status_code, 200)

    def test_a_published_device_whose_photos_are_not_attested_leaves_the_web_without_being_unpublished(self):
        self.ready()
        self.assertIsNotNone(self.card())
        UnitPhoto.objects.filter(unit=self.unit).update(attested_at=None, attested_by=None)
        self.assertIsNone(self.card())
        self.unit.refresh_from_db()
        self.assertTrue(self.unit.published_online)
        rows = {r['id']: r for r in _client(self.staff).get(UNITS, {'line': 'used'}).json()['results']}
        self.assertEqual(rows[self.unit.pk]['web']['reasons'], ['photo_not_attested'])
        UnitPhoto.objects.filter(unit=self.unit).update(attested_at=timezone.now())
        self.assertIsNotNone(self.card())

    def test_the_rehearsal_report_counts_what_leaves_the_web(self):
        self.ready()
        [second] = self.receive(self.used128)
        units.update_unit(second, sale_price='1500.00')
        used_units.set_inspection(second, decision='approved', actor=self.inspector)
        real_photo(second, self.inspector)
        used_units.set_publication(second, published=True, actor=self.staff)
        UnitPhoto.objects.filter(unit=second).update(attested_at=None)        # what 0192 leaves of a legacy photo
        out = io.StringIO()
        call_command('unit_photo_report', '--company', self.company.slug, '--format', 'json', stdout=out)
        counts = json.loads(out.getvalue())
        self.assertEqual((counts['published_units'], counts['published_with_public_photo'],
                          counts['published_leaving_the_web'], counts['photos_waiting_attestation']), (2, 1, 1, 1))
        self.assertEqual(UnitPhoto.objects.filter(unit=second).count(), 1)          # read-only


# ---------------------------------------------------------------------------
# The panel's API
# ---------------------------------------------------------------------------

class PhotoApiTest(PhotoShop):
    def test_upload_with_its_side_the_first_is_primary_and_nothing_is_attested(self):
        res = self.add(view='front')
        self.assertEqual(res.status_code, 200, res.content)
        [photo] = res.json()['photos']
        self.assertEqual((photo['view'], photo['view_label'], photo['is_primary'], photo['attested'], photo['public']),
                         ('front', 'Frontal', True, False, False))
        stored = StorefrontImage.objects.get(public_id=storefront_media.managed_public_id(photo['url']))
        self.assertEqual((stored.source_kind, stored.license_verified_at), (StorefrontImage.SOURCE_OWN_PHOTO, None))
        second = self.add(view='back').json()['photos'][1]
        self.assertFalse(second['is_primary'])
        log = AdminAuditLog.objects.filter(action='unit_photo_added', target_id=str(self.unit.pk)).order_by('pk').first()
        self.assertEqual((log.company_id, log.metadata['view']), (self.company.pk, 'front'))

    def test_a_defect_photo_says_what_it_shows_and_a_bad_side_is_refused(self):
        res = self.add(view='defect')
        self.assertEqual((res.status_code, res.json()['field']), (400, 'defect_note'))
        res = self.add(view='lado')
        self.assertEqual((res.status_code, res.json()['field']), (400, 'view'))
        self.assertEqual(StorefrontImage.objects.filter(company=self.company).count(), 0)   # refused before storing
        res = self.add(view='defect', defect_note='  Rayón   en la esquina ')
        [photo] = res.json()['photos']
        self.assertEqual((photo['defect_note'], photo['view_label']), ('Rayón en la esquina', 'Defecto: Rayón en la esquina'))
        res = _client(self.staff).patch(f'{UNITS}{self.unit.pk}/photos/{photo["id"]}/', {'view': 'back'},
                                        format='json')
        self.assertEqual((res.json()['photos'][0]['view'], res.json()['photos'][0]['defect_note']), ('back', ''))

    def test_a_new_device_and_another_companys_device(self):
        [new] = self.receive(self.new128)
        res = _client(self.staff).post(f'{UNITS}{new.pk}/photos/', {'file': upload()}, format='multipart')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'not_used_line'))
        stranger = UsedShop()
        stranger.build('unit-photos-ajena2')
        [foreign] = stranger.receive(stranger.used128)
        self.assertEqual(_client(self.staff).post(f'{UNITS}{foreign.pk}/photos/', {'file': upload()},
                                                  format='multipart').status_code, 404)
        photo = real_photo(foreign)
        for method, path, body in (
            ('post', f'{UNITS}{foreign.pk}/photos/{photo.pk}/attestation/', {'attested': True}),
            ('patch', f'{UNITS}{foreign.pk}/photos/{photo.pk}/', {'is_primary': True}),
            ('delete', f'{UNITS}{foreign.pk}/photos/{photo.pk}/', None),
            ('post', f'{UNITS}{foreign.pk}/photos/order/', {'order': [photo.pk]}),
        ):
            with self.subTest(method=method, path=path):
                user = self.inspector if 'attestation' in path else self.staff
                res = getattr(_client(user), method)(path, body, format='json')
                self.assertEqual(res.status_code, 404, res.content)
        # A photo of ANOTHER device of the same company is a 404 on this one.
        self.assertEqual(self.attest(photo.pk).status_code, 404)

    def test_one_primary_per_device_in_the_database_and_through_the_api(self):
        first = real_photo(self.unit)
        second = real_photo(self.unit)
        self.assertEqual((first.is_primary, second.is_primary), (True, False))
        with self.assertRaises(IntegrityError), transaction.atomic():
            UnitPhoto.objects.filter(pk=second.pk).update(is_primary=True)
        res = _client(self.staff).patch(f'{UNITS}{self.unit.pk}/photos/{second.pk}/', {'is_primary': True},
                                        format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual([p['is_primary'] for p in res.json()['photos']], [False, True])
        refused = _client(self.staff).patch(f'{UNITS}{self.unit.pk}/photos/{second.pk}/', {'is_primary': False},
                                            format='json')
        self.assertEqual((refused.status_code, refused.json()['field']), (400, 'is_primary'))
        self.assertTrue(AdminAuditLog.objects.filter(action='unit_photo_primary_changed', company=self.company,
                                                     target_id=str(self.unit.pk)).exists())

    def test_reorder_names_every_photo_once(self):
        a, b, c = (real_photo(self.unit) for _ in range(3))
        api = _client(self.staff)
        for bad in ([c.pk, b.pk], [c.pk, b.pk, b.pk, a.pk], [c.pk, b.pk, 999999], 'x', [True, b.pk, c.pk]):
            with self.subTest(bad=bad):
                res = api.post(f'{UNITS}{self.unit.pk}/photos/order/', {'order': bad}, format='json')
                self.assertEqual((res.status_code, res.json().get('field')), (400, 'order'))
        res = api.post(f'{UNITS}{self.unit.pk}/photos/order/', {'order': [c.pk, a.pk, b.pk]}, format='json')
        self.assertEqual([p['id'] for p in res.json()['photos']], [c.pk, a.pk, b.pk])
        self.assertTrue(UnitPhoto.objects.get(pk=a.pk).is_primary)      # the primary stays the primary
        self.assertTrue(AdminAuditLog.objects.filter(action='unit_photos_reordered', company=self.company).exists())

    def test_replace_keeps_the_place_clears_the_attestation_and_releases_the_old_file(self):
        self.add(view='front')
        self.add(view='back')
        first, second = self.photos()
        self.assertEqual(self.attest(second.pk).status_code, 200)
        old_public_id = storefront_media.managed_public_id(second.image_url)
        _client(self.staff).patch(f'{UNITS}{self.unit.pk}/photos/{second.pk}/', {'is_primary': True}, format='json')
        res = _client(self.staff).post(f'{UNITS}{self.unit.pk}/photos/{second.pk}/replace/', {'file': upload()},
                                       format='multipart')
        self.assertEqual(res.status_code, 200, res.content)
        replaced = UnitPhoto.objects.get(pk=second.pk)
        self.assertEqual((replaced.sort_order, replaced.is_primary, replaced.view), (second.sort_order, True, 'back'))
        self.assertNotEqual(replaced.image_url, second.image_url)
        self.assertIsNone(replaced.attested_at)
        self.assertFalse(StorefrontImage.objects.filter(public_id=old_public_id).exists())
        log = AdminAuditLog.objects.get(action='unit_photo_replaced', company=self.company)
        self.assertTrue(log.metadata['was_attested'])
        missing = _client(self.staff).post(f'{UNITS}{self.unit.pk}/photos/999999/replace/', {'file': upload()},
                                           format='multipart')
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(first.pk, self.photos()[0].pk)

    def test_deleting_the_primary_promotes_the_next_and_releases_its_file(self):
        self.add(view='front')
        self.add(view='back')
        first, second = self.photos()
        public_id = storefront_media.managed_public_id(first.image_url)
        res = _client(self.staff).delete(f'{UNITS}{self.unit.pk}/photos/{first.pk}/')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual([(p['id'], p['is_primary']) for p in res.json()['photos']], [(second.pk, True)])
        self.assertFalse(StorefrontImage.objects.filter(public_id=public_id).exists())
        log = AdminAuditLog.objects.get(action='unit_photo_removed', company=self.company)
        self.assertEqual((log.metadata['was_primary'], log.metadata['new_primary_id']), (True, second.pk))

    def test_attesting_needs_the_inspection_capability_and_verifies_the_file_as_an_own_photo(self):
        self.add(view='front')
        [photo] = self.photos()
        self.assertEqual(self.attest(photo.pk, user=self.stocker).status_code, 403)
        self.assertEqual(self.attest(photo.pk, user=self.staff).status_code, 403)
        res = self.attest(photo.pk)
        self.assertEqual(res.status_code, 200, res.content)
        [row] = res.json()['photos']
        self.assertEqual((row['attested'], row['public']), (True, True))
        stored = StorefrontImage.objects.get(public_id=storefront_media.managed_public_id(photo.image_url))
        self.assertEqual((stored.source_kind, stored.license_verified_by_id), ('own_photo', self.inspector.pk))
        actions = set(AdminAuditLog.objects.filter(company=self.company, target_id=str(self.unit.pk))
                      .values_list('action', flat=True))
        self.assertTrue({'unit_photo_attested', 'storefront_image_license_verified'} <= actions)
        again = self.attest(photo.pk)          # saying what already is changes nothing
        self.assertEqual(again.status_code, 200)
        self.assertEqual(AdminAuditLog.objects.filter(action='unit_photo_attested').count(), 1)
        self.assertEqual(self.attest(photo.pk, attested='sí').status_code, 400)
        withdrawn = self.attest(photo.pk, attested=False)
        self.assertEqual([(p['attested'], p['public']) for p in withdrawn.json()['photos']], [(False, False)])
        stored.refresh_from_db()
        self.assertIsNone(stored.license_verified_at)
        self.assertTrue(AdminAuditLog.objects.filter(action='unit_photo_attestation_withdrawn',
                                                     company=self.company).exists())

    def test_a_legacy_photo_without_a_file_of_the_shop_cannot_be_attested(self):
        photo = UnitPhoto.objects.create(company=self.company, unit=self.unit, image_url='/api/storefront/images/9001',
                                         is_primary=True)
        res = self.attest(photo.pk)
        self.assertEqual((res.status_code, res.json()['code']), (409, 'no_file'))

    def test_the_panel_payload_says_which_are_public(self):
        real_photo(self.unit, self.inspector, view='front')
        real_photo(self.unit, self.inspector, view='back', attested=False)
        rows = {r['id']: r for r in _client(self.staff).get(UNITS, {'line': 'used'}).json()['results']}
        photos = rows[self.unit.pk]['photos']
        self.assertEqual([(p['view'], p['public'], p['attested']) for p in photos],
                         [('front', True, True), ('back', False, False)])
        self.assertNotIn('storage_key', str(photos))


# ---------------------------------------------------------------------------
# The shop
# ---------------------------------------------------------------------------

class PublicPhotosTest(PhotoShop):
    def test_the_card_shows_only_public_photos_primary_first_with_their_labels(self):
        front = real_photo(self.unit, self.inspector, view='front')
        defect = real_photo(self.unit, self.inspector, view='defect', defect_note='Rayón leve en el marco')
        real_photo(self.unit, self.inspector, view='back', attested=False)
        unit_media.update_photo(unit=self.unit, photo_id=defect.pk, actor=self.staff, is_primary=True)
        used_units.set_publication(self.unit, published=True, actor=self.staff)
        photos = self.card()['photos']
        self.assertEqual([(p['url'], p['view_label'], p['is_primary']) for p in photos],
                         [(defect.image_url, 'Defecto: Rayón leve en el marco', True),
                          (front.image_url, 'Frontal', False)])
        self.assertEqual(set(photos[0]), {'url', 'alt_text', 'width', 'height', 'view', 'view_label',
                                          'defect_note', 'is_primary'})

    @staticmethod
    def cart(public, session='carro-foto'):
        body = public.get(f'/api/cart/?session_key={session}').json()
        return body['results'] if isinstance(body, dict) else body

    def test_the_cart_line_shows_the_devices_primary_photo_never_a_generic_picture(self):
        self.ready()
        generic = storefront_media.store(company=self.company, actor=self.staff, raw=png_with_transparency(),
                                         provenance={'source_kind': 'own_photo'})
        StorefrontImage.objects.filter(pk=generic.pk).update(license_verified_at=timezone.now())
        type(self.used128).objects.filter(pk=self.used128.pk).update(image_url=generic.url)
        public = _client(host=self.host)
        held = public.post('/api/cart/add/', {'session_key': 'carro-foto', 'product': self.used128.pk,
                                              'unit': str(self.unit.public_id)}, format='json')
        self.assertEqual(held.status_code, 200, held.content)
        [line] = self.cart(public)
        primary = UnitPhoto.objects.get(unit=self.unit, is_primary=True)
        self.assertEqual((line['product']['image_url'], line['product']['image_scope']),
                         (primary.image_url, 'unit'))
        self.assertEqual(line['units'][0]['photo']['url'], primary.image_url)
        self.assertNotIn(generic.url, json.dumps(line))
        # Its photo withdrawn while held: nothing — never the generic picture.
        unit_media.attest(unit=self.unit, photo_id=primary.pk, attested=False, actor=self.inspector)
        [line] = self.cart(public)
        self.assertEqual((line['product']['image_url'], line['product']['image_scope']), ('', 'unit'))
        self.assertIsNone(line['units'][0]['photo'])
        self.assertNotIn(generic.url, json.dumps(line))

    def test_the_order_line_shows_the_sold_devices_photo(self):
        self.ready()
        order = Order.objects.create(company=self.company, fulfillment_branch=self.branch,
                                     customer_email='x@example.com', total=Decimal('1999.00'))
        OrderItem.objects.create(order=order, product=self.used128, quantity=1, price=Decimal('1999.00'))
        StockUnit.objects.filter(pk=self.unit.pk).update(order=order)
        primary = UnitPhoto.objects.get(unit=self.unit, is_primary=True)
        line = OrderSerializer(order).data['items'][0]['product']
        self.assertEqual((line['image_url'], line['image_scope']), (primary.image_url, 'unit'))
        UnitPhoto.objects.filter(unit=self.unit).update(attested_at=None)
        line = OrderSerializer(order).data['items'][0]['product']
        self.assertEqual((line['image_url'], line['image_pending']), ('', False))

    def test_a_used_variant_never_shows_its_catalogue_picture_on_the_product_api(self):
        generic = storefront_media.store(company=self.company, actor=self.staff, raw=png_with_transparency(),
                                         provenance={'source_kind': 'own_photo'})
        StorefrontImage.objects.filter(pk=generic.pk).update(license_verified_at=timezone.now())
        type(self.used128).objects.filter(pk=self.used128.pk).update(image_url=generic.url)
        from store.serializers import ProductSerializer

        data = ProductSerializer(type(self.used128).objects.get(pk=self.used128.pk)).data
        self.assertEqual((data['image_url'], data['images']), ('', []))


# ---------------------------------------------------------------------------
# Migration 0192
# ---------------------------------------------------------------------------

class MigrationTest(TestCase):
    def test_it_only_adds_nullable_or_db_default_columns_and_one_partial_unique_index(self):
        from importlib import import_module

        from django.db import migrations
        from django.db.models import NOT_PROVIDED

        operations = import_module('store.migrations.0192_unit_photo_provenance').Migration.operations
        added = [op for op in operations if isinstance(op, migrations.AddField)]
        self.assertEqual({op.name for op in added}, {'view', 'defect_note', 'is_primary', 'attested_by',
                                                     'attested_at'})
        for operation in added:
            self.assertTrue(operation.field.null or operation.field.db_default is not NOT_PROVIDED, operation.name)
        [constraint] = [op for op in operations if isinstance(op, migrations.AddConstraint)]
        self.assertEqual(constraint.constraint.name, 'unit_photo_one_primary_per_unit')
        runs = [op.code.__name__ for op in operations if isinstance(op, migrations.RunPython)]
        self.assertEqual(runs, ['first_photo_is_primary'])

    def test_the_first_photo_of_each_device_becomes_its_primary_and_nothing_is_attested(self):
        from importlib import import_module

        from django.apps import apps

        shop = UsedShop()
        shop.build('unit-photos-mig')
        a, b = shop.receive(shop.used128, 2)
        rows = [UnitPhoto.objects.create(company=shop.company, unit=unit, image_url=f'/api/storefront/images/{n}',
                                         sort_order=order)
                for n, (unit, order) in enumerate([(a, 3), (a, 1), (b, 0)])]
        import_module('store.migrations.0192_unit_photo_provenance').first_photo_is_primary(apps, None)
        self.assertEqual(list(UnitPhoto.objects.filter(is_primary=True).order_by('unit_id')
                              .values_list('pk', flat=True)), [rows[1].pk, rows[2].pk])
        self.assertFalse(UnitPhoto.objects.filter(attested_at__isnull=False).exists())
