"""
CATALOG FASE 1.2 · Y — what each finish picture shows, replacing a picture in place, and approximate swatches per model.

What is fixed here:

  * a finish picture says WHICH VIEW it is (`ModelFinishImage.view`: main, front, back, side, other; '' = not said).
    It is chosen on upload and edited later (audited old → new); the panel and the public gallery read it (the only
    new public field of a picture);
  * a finish picture can be REPLACED keeping its place: same row (id, order, primary, alternative text, view), a new
    file that must say where it comes from (validated like an upload), its licence check starts over (a new file is
    not the verified one), and the old file goes only if nothing else shows it. Because the proxy admits big bodies
    only on the upload route, a replacement is a multipart POST to that same route with `replace=<picture id>`; a
    picture of another model or another company answers like a missing one;
  * a colour's swatch can be flagged APPROXIMATE (pending commercial validation) — the company's, and a model's own
    swatch for that colour (`ModelOption.swatch_hex`, blank = the company's). Every place that draws the colour
    resolves model → company → nothing (name only); only the admin payloads say «approximate», never the shop;
  * all of it per company: products.manage writes, products.view reads, every write leaves an AdminAuditLog with
    its company and the previous values.
"""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from store import device_catalog, model_palette, request_limits, storefront_media
from store.models import AdminAuditLog, ModelFinishImage, ModelOption, StorefrontImage, VariantOption
from store.test_device_commerce import _client
from store.test_finish_images import OWN, SUPPLIER, FinishApiBase
from store.test_storefront_media import jpeg, png_with_transparency
from store.tests import _p2d_member, _p3_company


def _public_id(address):
    return storefront_media.managed_public_id(address)


# ---------------------------------------------------------------------------
# 1. The view of a picture
# ---------------------------------------------------------------------------

class FinishImageViewTest(FinishApiBase):
    def patch(self, image_id, body, api=None):
        with self.captureOnCommitCallbacks(execute=True):
            return (api or self.api).patch(self.url(f'{image_id}/'), body, format='json')

    def test_the_closed_vocabulary_and_the_unspecified_default(self):
        self.assertEqual([value for value, _label in ModelFinishImage.VIEW_CHOICES],
                         ['main', 'front', 'back', 'side', 'other'])
        image = self.place(self.blue)
        self.assertEqual(image.view, '')

    def test_the_view_is_chosen_on_upload_and_listed(self):
        front = self.uploaded(self.blue, {**OWN, 'view': 'front'})
        unsaid = self.uploaded(self.blue)
        self.assertEqual((front['view'], unsaid['view']), ('front', ''))
        rows = {image['id']: image for image in self.listing()[self.blue.pk]['images']}
        self.assertEqual((rows[front['id']]['view'], rows[unsaid['id']]['view']), ('front', ''))
        self.assertEqual(ModelFinishImage.objects.get(pk=front['id']).view, 'front')
        logs = {log.metadata['image_id']: log.metadata['view']
                for log in AdminAuditLog.objects.filter(action='finish_image_added')}
        self.assertEqual((logs[front['id']], logs[unsaid['id']]), ('front', ''))

    def test_a_view_outside_the_vocabulary_is_refused_before_storing(self):
        for value in ('top', 'FRONT ', 'principal'):
            with self.subTest(value):
                res = self.upload(self.blue, {**OWN, 'view': value})
                self.assertEqual(res.status_code, 400, res.content)
                self.assertEqual(res.json()['field'], 'view')
        self.assertFalse(StorefrontImage.objects.filter(company=self.company).exists())

    def test_the_view_is_edited_and_audited_with_old_and_new(self):
        image = self.uploaded(self.blue, {**OWN, 'view': 'front'})
        res = self.patch(image['id'], {'view': 'back'})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['view'], 'back')
        log = AdminAuditLog.objects.filter(action='finish_image_updated').last()
        self.assertEqual(log.company_id, self.company.pk)
        self.assertEqual(log.metadata['view'], {'old': 'front', 'new': 'back'})
        # Back to «not said».
        res = self.patch(image['id'], {'view': ''})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['view'], '')
        # The same value writes nothing.
        before = AdminAuditLog.objects.filter(action='finish_image_updated').count()
        self.assertEqual(self.patch(image['id'], {'view': ''}).status_code, 200)
        self.assertEqual(AdminAuditLog.objects.filter(action='finish_image_updated').count(), before)

    def test_an_invalid_view_is_refused_on_edit(self):
        image = self.uploaded(self.blue)
        for value in ('top', 3, ['front']):
            with self.subTest(value):
                res = self.patch(image['id'], {'view': value})
                self.assertEqual(res.status_code, 400, res.content)
                self.assertEqual(res.json()['field'], 'view')
        self.assertEqual(ModelFinishImage.objects.get(pk=image['id']).view, '')

    def test_products_view_cannot_edit_the_view(self):
        viewer, _ = _p2d_member(self.company, 'fm_viewer', ['company.view', 'products.view'])
        image = self.uploaded(self.blue)
        self.assertEqual(self.patch(image['id'], {'view': 'side'}, api=_client(viewer)).status_code, 403)

    def test_the_public_gallery_says_the_view(self):
        self.uploaded(self.black, {**OWN, 'view': 'main'})
        body = _client(host=self.host).get(f'/api/catalog/models/{self.model.slug}/').json()
        picture = body['finish_images'][str(self.black.pk)][0]
        self.assertEqual(picture['view'], 'main')
        self.assertEqual(body['variants'][0]['gallery'][0]['view'], 'main')


# ---------------------------------------------------------------------------
# 2. Replacing a picture in place
# ---------------------------------------------------------------------------

class FinishImageReplaceTest(FinishApiBase):
    def replace(self, image_id, provenance=None, *, api=None, model=None, raw=None, extra=None):
        body = {'file': SimpleUploadedFile('nueva.jpg', raw or jpeg(), content_type='image/jpeg'),
                'replace': str(image_id), **(OWN if provenance is None else provenance), **(extra or {})}
        with self.captureOnCommitCallbacks(execute=True):
            return (api or self.api).post(self.url(model=model), body, format='multipart')

    def test_the_route_is_the_upload_route_the_proxy_admits(self):
        # Caddy's image matcher cannot change: a replacement travels on the upload route.
        path = reverse('admin-catalog-model-finish-images', args=[7])
        self.assertEqual(request_limits.limit_for(path),
                         storefront_media.max_upload_bytes() + request_limits.MULTIPART_SLACK)

    def test_the_row_keeps_its_place_and_takes_the_new_file_and_provenance(self):
        first = self.uploaded(self.blue, {**SUPPLIER, 'view': 'front'}, alt_text='Azul de frente')
        second = self.uploaded(self.blue, OWN, raw=jpeg(), name='b.jpg')
        # The old licence was verified: a new file is not the verified one.
        self.assertEqual(self.api.patch(self.url(f'{first["id"]}/'), {'license_verified': True},
                                        format='json').status_code, 200)
        old_public_id = _public_id(first['url'])
        res = self.replace(first['id'], {'source_kind': 'licensed_stock', 'license': 'Banco Z, licencia comercial',
                                         'source_url': 'https://banco.example/item/1', 'attribution': 'Banco Z'})
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body['id'], first['id'])
        self.assertNotEqual(body['url'], first['url'])
        self.assertEqual((body['sort_order'], body['is_primary'], body['alt_text'], body['view']),
                         (first['sort_order'], True, 'Azul de frente', 'front'))
        self.assertEqual(body['source']['kind'], 'licensed_stock')
        self.assertEqual(body['source']['license'], 'Banco Z, licencia comercial')
        self.assertEqual(body['source']['url'], 'https://banco.example/item/1')
        self.assertFalse(body['source']['license_verified'])
        self.assertIsNone(body['source']['license_verified_by'])
        stored = StorefrontImage.objects.get(public_id=_public_id(body['url']))
        self.assertIsNone(stored.license_verified_at)
        self.assertIsNone(stored.license_verified_by)
        # The old file nobody else shows is gone; the gallery still has two pictures, in the same order.
        self.assertFalse(StorefrontImage.objects.filter(public_id=old_public_id).exists())
        self.assertEqual([image['id'] for image in self.listing()[self.blue.pk]['images']],
                         [first['id'], second['id']])
        self.assertEqual(ModelFinishImage.objects.filter(model=self.model, color_option=self.blue).count(), 2)
        log = AdminAuditLog.objects.filter(action='finish_image_replaced').last()
        self.assertEqual(log.company_id, self.company.pk)
        self.assertEqual(log.metadata['image_id'], first['id'])
        self.assertEqual(log.metadata['previous_address'], first['url'])
        self.assertEqual(log.metadata['address'], body['url'])
        self.assertEqual(log.metadata['previous_source']['source_kind'], 'supplier_authorized')
        self.assertEqual(log.metadata['source_kind'], 'licensed_stock')

    def test_the_alt_text_and_view_of_the_request_do_not_change_the_slot(self):
        image = self.uploaded(self.blue, {**OWN, 'view': 'side'}, alt_text='De lado')
        res = self.replace(image['id'], extra={'alt_text': 'otro', 'view': 'back', 'color_option_id': self.black.pk})
        self.assertEqual(res.status_code, 200, res.content)
        row = ModelFinishImage.objects.get(pk=image['id'])
        self.assertEqual((row.alt_text, row.view, row.color_option_id), ('De lado', 'side', self.blue.pk))

    def test_the_old_file_stays_while_something_else_shows_it(self):
        image = self.uploaded(self.blue)
        old = StorefrontImage.objects.get(public_id=_public_id(image['url']))
        # The same file is also the black finish's picture.
        self.place(self.black, stored=old)
        res = self.replace(image['id'])
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(StorefrontImage.objects.filter(pk=old.pk).exists())

    def test_the_new_file_must_say_where_it_comes_from(self):
        image = self.uploaded(self.blue)
        files_before = StorefrontImage.objects.filter(company=self.company).count()
        res = self.replace(image['id'], {})
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['field'], 'source_kind')
        res = self.replace(image['id'], {'source_kind': 'supplier_authorized', 'license': ' '})
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['field'], 'license')
        self.assertEqual(StorefrontImage.objects.filter(company=self.company).count(), files_before)
        self.assertEqual(ModelFinishImage.objects.get(pk=image['id']).image_url, image['url'])

    def test_a_replacement_needs_its_file(self):
        image = self.uploaded(self.blue)
        res = self.api.post(self.url(), {'replace': str(image['id']), **OWN}, format='multipart')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['field'], 'file')

    def test_a_full_finish_can_still_replace(self):
        with self.settings(FINISH_IMAGE_MAX_PER_COLOUR=1):
            image = self.uploaded(self.blue)
            self.assertEqual(self.upload(self.blue).status_code, 400)
            self.assertEqual(self.replace(image['id']).status_code, 200)

    def test_a_picture_of_another_model_or_company_answers_like_a_missing_one(self):
        image = self.uploaded(self.blue)
        second = device_catalog.create_model(company=self.company, name='Teléfono Y')
        self.assertEqual(self.replace(image['id'], model=second).status_code, 404)
        self.assertEqual(self.replace(999999).status_code, 404)
        other = _p3_company('fm-otra', 'Otra')
        colour = device_catalog.create_attribute(company=other, name='Color', kind='color', role='color')
        green = device_catalog.create_option(company=other, attribute_id=colour.pk, label='Verde',
                                             swatch_hex='#00AA00')
        foreign_model = device_catalog.create_model(company=other, name='Ajeno')
        foreign_file = storefront_media.store(company=other, actor=self.staff, raw=png_with_transparency())
        foreign = ModelFinishImage.objects.create(company=other, model=foreign_model, color_option=green,
                                                  image_url=foreign_file.url, width=1, height=1, is_primary=True)
        files_before = StorefrontImage.objects.count()
        self.assertEqual(self.replace(foreign.pk).status_code, 404)
        self.assertEqual(self.replace(foreign.pk, model=foreign_model).status_code, 404)
        self.assertEqual(StorefrontImage.objects.count(), files_before)
        self.assertEqual(ModelFinishImage.objects.get(pk=foreign.pk).image_url, foreign_file.url)
        self.assertEqual(ModelFinishImage.objects.get(pk=image['id']).image_url, image['url'])

    def test_an_id_that_is_not_a_number_is_refused(self):
        self.uploaded(self.blue)
        for value in ('x', '-1', '0', '1.5'):
            with self.subTest(value):
                res = self.replace(value)
                self.assertEqual(res.status_code, 400, res.content)
                self.assertEqual(res.json()['field'], 'replace')

    def test_products_view_cannot_replace(self):
        viewer, _ = _p2d_member(self.company, 'fm_viewer_r', ['company.view', 'products.view'])
        image = self.uploaded(self.blue)
        self.assertEqual(self.replace(image['id'], api=_client(viewer)).status_code, 403)
        self.assertEqual(ModelFinishImage.objects.get(pk=image['id']).image_url, image['url'])


# ---------------------------------------------------------------------------
# 3. Approximate swatches, and a model's own swatch for a colour
# ---------------------------------------------------------------------------

class SwatchBase(FinishApiBase):
    def setUp(self):
        super().setUp()
        self.second = device_catalog.create_model(company=self.company, name='Teléfono Y')
        self.till, _ = _p2d_member(self.company, 'fm-caja', ['company.view', 'sales.pos.use'])
        self.viewer, _ = _p2d_member(self.company, 'fm-viewer-s', ['company.view', 'products.view'])

    def option_patch(self, option, body, api=None):
        return (api or self.api).patch(f'/api/admin/catalog/options/{option.pk}/{self.q}', body, format='json')

    def palette_url(self, model=None):
        return f'/api/admin/catalog/models/{(model or self.model).pk}/palette/{self.q}'

    def model_swatch(self, body, *, model=None, api=None):
        return (api or self.api).patch(self.palette_url(model), body, format='json')

    def with_palette(self, model=None):
        model_palette.set_palette(model or self.model,
                                  option_ids=[self.gb128.pk, self.gb256.pk, self.black.pk, self.blue.pk],
                                  actor=self.staff)

    def public_colours(self, model=None):
        body = _client(host=self.host).get(f'/api/catalog/models/{(model or self.model).slug}/').json()
        colour = next(a for a in body['attributes'] if a['role'] == 'color')
        return {o['label']: o for o in colour['options']}

    def till_colours(self, model=None):
        url = (f'/api/admin/pos/models/{(model or self.model).pk}/configurations/'
               f'?company={self.company.pk}&branch={self.branch.pk}')
        res = _client(self.till).get(url)
        self.assertEqual(res.status_code, 200, res.content)
        colour = next(a for a in res.json()['attributes'] if a['role'] == 'color')
        return {o['label']: o for o in colour['options']}

    def palette_colours(self, model=None):
        rows = model_palette.payload(model or self.model)['attributes']
        colour = next(a for a in rows if a['role'] == 'color')
        return {o['label']: o for o in colour['options']}

    def listed(self):
        return {row['option']['label']: row['option'] for row in self.api.get(self.url()).json()['finishes']}


class CompanySwatchApproximationTest(SwatchBase):
    def test_the_company_flags_a_swatch_as_approximate_and_it_is_audited(self):
        self.assertFalse(self.blue.swatch_is_approximate)
        res = self.option_patch(self.blue, {'swatch_hex': '#1E40AF', 'swatch_is_approximate': True})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((res.json()['swatch_hex'], res.json()['swatch_is_approximate']), ('#1E40AF', True))
        self.blue.refresh_from_db()
        self.assertTrue(self.blue.swatch_is_approximate)
        log = AdminAuditLog.objects.filter(action='variant_option_updated').last()
        self.assertEqual(log.company_id, self.company.pk)
        self.assertEqual(log.metadata['previous']['swatch_is_approximate'], False)
        self.assertEqual(log.metadata['previous']['swatch_hex'], '#1D4ED8')
        self.assertIs(log.metadata['swatch_is_approximate'], True)

    def test_an_owner_approved_value_needs_no_flag(self):
        res = self.option_patch(self.blue, {'swatch_hex': '#1E40AF'})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIs(res.json()['swatch_is_approximate'], False)
        # The flag only changes when it is sent.
        self.option_patch(self.blue, {'swatch_is_approximate': True})
        res = self.option_patch(self.blue, {'label': 'Azul marino'})
        self.assertIs(res.json()['swatch_is_approximate'], True)
        res = self.option_patch(self.blue, {'swatch_is_approximate': False})
        self.assertIs(res.json()['swatch_is_approximate'], False)

    def test_the_flag_is_a_boolean_on_a_colour_only(self):
        for value in ('si', 1):
            with self.subTest(value):
                res = self.option_patch(self.blue, {'swatch_is_approximate': value})
                self.assertEqual(res.status_code, 400, res.content)
                self.assertEqual(res.json()['field'], 'swatch_is_approximate')
        res = self.option_patch(self.gb128, {'swatch_is_approximate': True})
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['field'], 'swatch_is_approximate')

    def test_the_admin_payloads_say_it_the_shop_never_does(self):
        self.with_palette()
        self.option_patch(self.blue, {'swatch_is_approximate': True})
        self.assertIs(self.palette_colours()['Azul']['swatch_is_approximate'], True)
        self.assertIs(self.palette_colours()['Negro']['swatch_is_approximate'], False)
        self.assertIs(self.listed()['Azul']['shown_swatch_is_approximate'], True)
        self.assertIs(self.till_colours()['Azul']['swatch_is_approximate'], True)
        attributes = self.api.get(f'/api/admin/catalog/attributes/{self.q}').json()
        attributes = attributes['results'] if isinstance(attributes, dict) else attributes
        colour = next(a for a in attributes if a['id'] == self.colour.pk)
        self.assertIs(next(o for o in colour['options'] if o['id'] == self.blue.pk)['swatch_is_approximate'], True)
        # The shop draws the colour and says nothing else of it.
        self.variant('Teléfono X 128 GB Azul', 'tx-128-azul', '3100.00', [self.gb128, self.blue])
        public = self.public_colours()['Azul']
        self.assertEqual(sorted(public), ['id', 'label', 'sort_order', 'swatch_hex'])
        self.assertEqual(public['swatch_hex'], '#1D4ED8')
        body = _client(host=self.host).get(f'/api/catalog/models/{self.model.slug}/').content.decode()
        self.assertNotIn('approximate', body)


class ModelSwatchTest(SwatchBase):
    def test_a_model_gives_a_colour_its_own_swatch_and_it_is_audited(self):
        self.with_palette()
        res = self.model_swatch({'option_id': self.blue.pk, 'swatch_hex': '#2563eb', 'swatch_is_approximate': True})
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual((body['option_id'], body['swatch_hex'], body['swatch_is_approximate']),
                         (self.blue.pk, '#2563EB', True))
        self.assertEqual((body['shown_swatch_hex'], body['shown_swatch_is_approximate']), ('#2563EB', True))
        row = ModelOption.objects.get(model=self.model, option=self.blue)
        self.assertEqual((row.swatch_hex, row.swatch_is_approximate), ('#2563EB', True))
        # The company's option is untouched: every other model keeps its swatch.
        self.blue.refresh_from_db()
        self.assertEqual((self.blue.swatch_hex, self.blue.swatch_is_approximate), ('#1D4ED8', False))
        log = AdminAuditLog.objects.filter(action='product_model_swatch_set').last()
        self.assertEqual(log.company_id, self.company.pk)
        self.assertEqual(str(log.target_id), str(self.model.pk))
        self.assertEqual(log.metadata['option_id'], self.blue.pk)
        self.assertEqual(log.metadata['previous'], {'swatch_hex': '', 'swatch_is_approximate': False})
        self.assertEqual(log.metadata['swatch_hex'], '#2563EB')
        self.assertIs(log.metadata['swatch_is_approximate'], True)

    def test_without_the_flag_the_value_is_the_owners(self):
        self.with_palette()
        res = self.model_swatch({'option_id': self.blue.pk, 'swatch_hex': '#2563EB'})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIs(res.json()['swatch_is_approximate'], False)

    def test_blank_inherits_the_company_swatch(self):
        self.with_palette()
        self.model_swatch({'option_id': self.blue.pk, 'swatch_hex': '#2563EB', 'swatch_is_approximate': True})
        res = self.model_swatch({'option_id': self.blue.pk, 'swatch_hex': ''})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((res.json()['swatch_hex'], res.json()['swatch_is_approximate']), ('', False))
        self.assertEqual(res.json()['shown_swatch_hex'], '#1D4ED8')
        # Approximate means nothing without the model's own swatch.
        res = self.model_swatch({'option_id': self.blue.pk, 'swatch_is_approximate': True})
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['field'], 'swatch_is_approximate')
        res = self.model_swatch({'option_id': self.blue.pk, 'swatch_hex': '', 'swatch_is_approximate': True})
        self.assertEqual(res.status_code, 400, res.content)

    def test_the_value_is_validated(self):
        self.with_palette()
        for body, field in (
            ({'option_id': self.blue.pk, 'swatch_hex': 'azul'}, 'swatch_hex'),
            ({'option_id': self.blue.pk, 'swatch_hex': '#12345'}, 'swatch_hex'),
            ({'option_id': self.blue.pk, 'swatch_hex': 12}, 'swatch_hex'),
            ({'option_id': self.blue.pk, 'swatch_hex': '#123456', 'swatch_is_approximate': 'si'},
             'swatch_is_approximate'),
            ({'option_id': 'x', 'swatch_hex': '#123456'}, 'option_id'),
            ({'option_id': True, 'swatch_hex': '#123456'}, 'option_id'),
            ({'option_id': self.blue.pk}, 'swatch_hex'),
        ):
            with self.subTest(body):
                res = self.model_swatch(body)
                self.assertEqual(res.status_code, 400, res.content)
                self.assertEqual(res.json()['field'], field)
        self.assertEqual(ModelOption.objects.get(model=self.model, option=self.blue).swatch_hex, '')

    def test_only_a_colour_of_the_palette(self):
        res = self.model_swatch({'option_id': self.blue.pk, 'swatch_hex': '#123456'})
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'no_palette')
        model_palette.set_palette(self.model, option_ids=[self.gb128.pk, self.black.pk], actor=self.staff)
        res = self.model_swatch({'option_id': self.blue.pk, 'swatch_hex': '#123456'})
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'not_in_palette')
        res = self.model_swatch({'option_id': self.gb128.pk, 'swatch_hex': '#123456'})
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'not_a_colour')

    def test_another_companys_model_or_option_answers_404(self):
        self.with_palette()
        other = _p3_company('fm-otra-muestra', 'Otra')
        colour = device_catalog.create_attribute(company=other, name='Color', kind='color', role='color')
        green = device_catalog.create_option(company=other, attribute_id=colour.pk, label='Verde',
                                             swatch_hex='#00AA00')
        foreign_model = device_catalog.create_model(company=other, name='Ajeno')
        model_palette.set_palette(foreign_model, option_ids=[green.pk], actor=None)
        self.assertEqual(self.model_swatch({'option_id': green.pk, 'swatch_hex': '#123456'}).status_code, 404)
        self.assertEqual(self.model_swatch({'option_id': green.pk, 'swatch_hex': '#123456'},
                                           model=foreign_model).status_code, 404)
        self.assertEqual(ModelOption.objects.get(model=foreign_model, option=green).swatch_hex, '')

    def test_products_view_cannot_set_it(self):
        self.with_palette()
        res = self.model_swatch({'option_id': self.blue.pk, 'swatch_hex': '#123456'}, api=_client(self.viewer))
        self.assertEqual(res.status_code, 403, res.content)

    def test_every_place_resolves_model_then_company_then_nothing(self):
        self.with_palette()
        self.with_palette(self.second)
        self.variant('Teléfono X 128 GB Azul', 'tx-128-azul', '3100.00', [self.gb128, self.blue])
        # An imported colour whose company swatch is still the neutral placeholder.
        VariantOption.objects.filter(pk=self.black.pk).update(swatch_hex=device_catalog.NEUTRAL_SWATCH)
        self.model_swatch({'option_id': self.blue.pk, 'swatch_hex': '#2563EB', 'swatch_is_approximate': True})

        # Public configurator: the model's own, else the company's, else no dot.
        public = self.public_colours()
        self.assertEqual((public['Azul']['swatch_hex'], public['Negro']['swatch_hex']), ('#2563EB', ''))
        # The till.
        till = self.till_colours()
        self.assertEqual((till['Azul']['swatch_hex'], till['Negro']['swatch_hex']), ('#2563EB', ''))
        self.assertIs(till['Azul']['swatch_is_approximate'], True)
        # The panel: the palette screen and the finish listing.
        palette = self.palette_colours()
        self.assertEqual((palette['Azul']['shown_swatch_hex'], palette['Azul']['model_swatch_hex'],
                          palette['Azul']['swatch_hex']), ('#2563EB', '#2563EB', '#1D4ED8'))
        self.assertIs(palette['Azul']['shown_swatch_is_approximate'], True)
        self.assertEqual(palette['Negro']['shown_swatch_hex'], '')
        listed = self.listed()
        self.assertEqual((listed['Azul']['shown_swatch_hex'], listed['Azul']['model_swatch_hex'],
                          listed['Azul']['model_swatch_is_approximate'], listed['Azul']['model_swatch_editable']),
                         ('#2563EB', '#2563EB', True, True))
        self.assertEqual(listed['Negro']['shown_swatch_hex'], '')
        order = model_palette.option_order(self.model)
        colours = next(a for a in order['attributes'] if a['id'] == self.colour.pk)['options']
        self.assertEqual({o['label']: o['swatch_hex'] for o in colours}, {'Azul': '#2563EB', 'Negro': ''})

        # Another model with the same company colour keeps the company's swatch.
        till = self.till_colours(self.second)
        self.assertEqual(till['Azul']['swatch_hex'], '#1D4ED8')
        self.assertIs(till['Azul']['swatch_is_approximate'], False)
        self.assertEqual(self.palette_colours(self.second)['Azul']['shown_swatch_hex'], '#1D4ED8')

        # A model's own swatch on a colour the company never configured is drawn.
        self.model_swatch({'option_id': self.black.pk, 'swatch_hex': '#0A0A0A'})
        self.assertEqual(self.public_colours()['Negro']['swatch_hex'], '#0A0A0A')
        self.assertEqual(self.listed()['Negro']['shown_swatch_hex'], '#0A0A0A')

    def test_a_colour_outside_the_palette_is_not_editable_per_model(self):
        listed = self.listed()
        self.assertIs(listed['Azul']['model_swatch_editable'], False)
        self.assertEqual(listed['Azul']['model_swatch_hex'], '')
        self.assertEqual(listed['Azul']['shown_swatch_hex'], '#1D4ED8')

    def test_the_public_page_costs_no_query_more(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.with_palette()
        with CaptureQueriesContext(connection) as before:
            self.public_colours()
        self.model_swatch({'option_id': self.blue.pk, 'swatch_hex': '#2563EB'})
        self.model_swatch({'option_id': self.black.pk, 'swatch_hex': '#0A0A0A'})
        with CaptureQueriesContext(connection) as after:
            self.public_colours()
        self.assertEqual(len(after.captured_queries), len(before.captured_queries))
