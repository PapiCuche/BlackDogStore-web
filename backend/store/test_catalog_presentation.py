"""
CATALOG-REFERENCE-01 · PR E — what each company decides about how its catalogue is PRESENTED.

What is fixed here:

  * a model may be FEATURED (`ProductModel.is_featured`); the public list shows featured models first, then by the
    company's `sort_order`. A company that never featured one gets the list it always got, card for card and key
    for key (`is_featured` is on a card only when it is true);
  * the options of one attribute (its colours, say) may be put in an order of the MODEL's own
    (`ModelOption.sort_order`, written only by `model_palette.set_option_order`); the public page lists them so,
    and the options nobody placed follow in the company's order. A model nobody ordered answers byte for byte as
    before;
  * the panel's «Configuración comercial» summary (`GET …/models/<id>/commercial/`) reads what the existing
    editors keep — visibility, palette, variants and prices, finishes, on order, suppliers, promotions — and never
    shows a cost without `costs.view` nor a promotion without `sales.promotions.view`;
  * all of it is per company: a model, an option or an attribute of another company answers 404; every write is
    `products.manage` and leaves an `AdminAuditLog` with its company.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from store import device_catalog, model_palette, supply_services as supply
from store.models import AdminAuditLog, ModelOption, Product, ProductModel, Promotion, PromotionItem
from store.test_device_commerce import _client
from store.test_model_palette import PaletteShop
from store.tests import _p2d_member, _p3_company, _prod


def _cards(body):
    return body['results']


@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class PresentationShop(PaletteShop, TestCase):
    def setUp(self):
        self.build('pe-presenta')
        self.public = _client(host=self.host)
        self.viewer, _ = _p2d_member(self.company, 'pe_viewer', ['company.view', 'products.view'])

    def second_model(self, name, slug, *, sort_order=0):
        model = device_catalog.create_model(company=self.company, name=name, sort_order=sort_order)
        product = _prod(self.company, f'{name} 128 GB Negro', slug, price='900.00', inventory=0)
        device_catalog.set_variant(product, model_id=model.pk, option_ids=[self.gb128.pk, self.black.pk])
        return model

    def listing(self):
        res = self.public.get('/api/catalog/models/')
        self.assertEqual(res.status_code, 200, res.content)
        return _cards(res.json())

    def detail(self, model=None):
        res = self.public.get(f'/api/catalog/models/{(model or self.model).slug}/')
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        body.pop('as_of', None)
        return body

    def colour_labels(self, body):
        attribute = next(a for a in body['attributes'] if a['id'] == self.colour.pk)
        return [option['label'] for option in attribute['options']]


class SchemaDefaultsTest(PresentationShop):
    def test_a_model_is_not_featured_and_an_option_is_not_placed_by_default(self):
        self.assertFalse(ProductModel.objects.get(pk=self.model.pk).is_featured)
        self.palette(self.gb128, self.black)
        self.assertEqual(set(ModelOption.objects.filter(model=self.model).values_list('sort_order', flat=True)), {0})


class FeaturedModelTest(PresentationShop):
    def patch(self, body, *, user=None, model=None):
        return _client(user or self.staff).patch(
            f'/api/admin/catalog/models/{(model or self.model).pk}/{self.q}', body, format='json')

    def test_products_manage_features_a_model_and_it_is_audited_with_its_company(self):
        res = self.patch({'is_featured': True})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.json()['is_featured'])
        self.assertTrue(ProductModel.objects.get(pk=self.model.pk).is_featured)
        log = AdminAuditLog.objects.filter(action='product_model_updated', target_id=str(self.model.pk)).last()
        self.assertEqual(log.company_id, self.company.pk)
        self.assertIn('is_featured', log.metadata['changed'])

    def test_the_admin_payload_says_whether_it_is_featured(self):
        body = self.api.get(f'/api/admin/catalog/models/{self.model.pk}/{self.q}').json()
        self.assertIs(body['is_featured'], False)
        listing = self.api.get(f'/api/admin/catalog/models/{self.q}').json()['results']
        self.assertIs(listing[0]['is_featured'], False)

    def test_only_a_boolean_is_read(self):
        res = self.patch({'is_featured': 'false'})
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['field'], 'is_featured')
        self.assertFalse(ProductModel.objects.get(pk=self.model.pk).is_featured)

    def test_products_view_alone_cannot_feature(self):
        res = self.patch({'is_featured': True}, user=self.viewer)
        self.assertEqual(res.status_code, 403, res.content)
        self.assertFalse(ProductModel.objects.get(pk=self.model.pk).is_featured)

    def test_a_model_of_another_company_is_not_found(self):
        other = _p3_company('pe-otra', 'Otra')
        foreign = device_catalog.create_model(company=other, name='Ajeno')
        res = self.patch({'is_featured': True}, model=foreign)
        self.assertEqual(res.status_code, 404, res.content)
        self.assertFalse(ProductModel.objects.get(pk=foreign.pk).is_featured)


class PublicListOrderTest(PresentationShop):
    def test_featured_first_then_the_company_order(self):
        first = self.second_model('Teléfono A', 'ta-128', sort_order=1)
        second = self.second_model('Teléfono B', 'tb-128', sort_order=2)
        ProductModel.objects.filter(pk=self.model.pk).update(sort_order=3)
        self.assertEqual([c['slug'] for c in self.listing()], [first.slug, second.slug, self.model.slug])
        device_catalog.update_model(ProductModel.objects.get(pk=self.model.pk), is_featured=True)
        cards = self.listing()
        self.assertEqual([c['slug'] for c in cards], [self.model.slug, first.slug, second.slug])
        self.assertIs(cards[0]['is_featured'], True)
        # Only a featured card says so: the others keep their known keys.
        self.assertNotIn('is_featured', cards[1])

    def test_a_company_that_never_features_gets_the_same_cards(self):
        self.second_model('Teléfono A', 'ta-128', sort_order=1)
        before = self.listing()
        self.assertEqual(
            sorted(before[0]), ['category', 'from_price', 'has_new', 'has_used', 'image_url', 'name', 'slug', 'sold_out'])
        # Featuring a model of ANOTHER company changes nothing here.
        other = _p3_company('pe-otra-lista', 'Otra')
        foreign = device_catalog.create_model(company=other, name='Ajeno')
        device_catalog.update_model(foreign, is_featured=True)
        self.assertEqual(self.listing(), before)


class OptionOrderTest(PresentationShop):
    def url(self, model=None):
        return f'/api/admin/catalog/models/{(model or self.model).pk}/palette/order/{self.q}'

    def put(self, attribute, options, *, user=None, model=None):
        return _client(user or self.staff).put(
            self.url(model), {'attribute_id': attribute.pk, 'option_ids': [o.pk for o in options]}, format='json')

    def test_the_model_orders_its_colours_and_it_is_audited(self):
        self.palette(self.gb128, self.gb256, self.black, self.blue, self.red)
        res = self.put(self.colour, [self.red, self.black, self.blue])
        self.assertEqual(res.status_code, 200, res.content)
        colours = next(a for a in res.json()['attributes'] if a['id'] == self.colour.pk)
        self.assertEqual([o['label'] for o in colours['options']], ['Rojo', 'Negro', 'Azul'])
        ranks = dict(ModelOption.objects.filter(model=self.model).values_list('option_id', 'sort_order'))
        self.assertEqual((ranks[self.red.pk], ranks[self.black.pk], ranks[self.blue.pk]), (1, 2, 3))
        self.assertEqual((ranks[self.gb128.pk], ranks[self.gb256.pk]), (0, 0))
        log = AdminAuditLog.objects.filter(action='product_model_option_order_set').last()
        self.assertEqual(log.company_id, self.company.pk)
        self.assertEqual(log.metadata['option_ids'], [self.red.pk, self.black.pk, self.blue.pk])

    def test_get_reads_the_order_with_products_view(self):
        self.palette(self.gb128, self.black, self.blue)
        res = _client(self.viewer).get(self.url())
        self.assertEqual(res.status_code, 200, res.content)
        roles = [a['role'] for a in res.json()['attributes']]
        self.assertEqual(roles, ['capacity', 'color'])

    def test_the_order_names_every_palette_option_of_that_attribute_once(self):
        self.palette(self.gb128, self.black, self.blue, self.red)
        for options in ([self.black, self.blue], [self.black, self.blue, self.red, self.red],
                        [self.black, self.blue, self.red, self.gb128]):
            with self.subTest(options=[o.label for o in options]):
                res = self.put(self.colour, options)
                self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(set(ModelOption.objects.filter(model=self.model).values_list('sort_order', flat=True)), {0})

    def test_a_model_without_palette_has_nothing_to_order(self):
        res = self.put(self.colour, [self.black, self.blue])
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'no_palette')

    def test_products_view_alone_cannot_order(self):
        self.palette(self.black, self.blue)
        res = self.put(self.colour, [self.blue, self.black], user=self.viewer)
        self.assertEqual(res.status_code, 403, res.content)

    def test_another_companys_model_or_attribute_is_not_found(self):
        self.palette(self.black, self.blue)
        other = _p3_company('pe-otra-orden', 'Otra')
        foreign = device_catalog.create_model(company=other, name='Ajeno')
        self.assertEqual(self.put(self.colour, [self.blue, self.black], model=foreign).status_code, 404)
        colour = device_catalog.create_attribute(company=other, name='Color', kind='color', role='color')
        green = device_catalog.create_option(company=other, attribute_id=colour.pk, label='Verde',
                                             swatch_hex='#00AA00')
        res = _client(self.staff).put(self.url(), {'attribute_id': colour.pk, 'option_ids': [green.pk]}, format='json')
        self.assertEqual(res.status_code, 404, res.content)

    def test_the_public_page_lists_the_colours_in_the_models_order(self):
        blue_variant = _prod(self.company, 'Teléfono X 128 GB Azul', 'tx-128-azul', price='3000.00', inventory=0)
        device_catalog.set_variant(blue_variant, model_id=self.model.pk, option_ids=[self.gb128.pk, self.blue.pk])
        before = self.detail()
        self.assertEqual(self.colour_labels(before), ['Azul', 'Negro'])
        # Placing only the capacity changes nothing of the colours.
        self.palette(self.gb128, self.gb256, self.black, self.blue, self.red)
        self.assertEqual(self.detail(), before)
        model_palette.set_option_order(self.model, attribute_id=self.colour.pk,
                                       option_ids=[self.black.pk, self.red.pk, self.blue.pk], actor=self.staff)
        body = self.detail()
        self.assertEqual(self.colour_labels(body), ['Negro', 'Azul'])
        colours = next(a for a in body['attributes'] if a['id'] == self.colour.pk)['options']
        self.assertEqual([o['sort_order'] for o in colours], [0, 1])
        # The capacity, never placed, keeps the company's own values.
        capacity = next(a for a in body['attributes'] if a['id'] == self.capacity.pk)['options']
        self.assertEqual(capacity, before['attributes'][0]['options'])

    def test_an_option_added_after_the_order_follows_the_placed_ones(self):
        self.palette(self.black, self.blue)
        model_palette.set_option_order(self.model, attribute_id=self.colour.pk,
                                       option_ids=[self.blue.pk, self.black.pk], actor=self.staff)
        model_palette.add_to_palette(self.model, option_ids=[self.red.pk], actor=self.staff)
        order = model_palette.option_order(self.model)
        colours = next(a for a in order['attributes'] if a['id'] == self.colour.pk)
        self.assertEqual([o['label'] for o in colours['options']], ['Azul', 'Negro', 'Rojo'])
        self.assertEqual([o['placed'] for o in colours['options']], [True, True, False])

    def test_another_model_of_the_company_keeps_the_company_order(self):
        twin = self.second_model('Teléfono Z', 'tz-128')
        blue = _prod(self.company, 'Teléfono Z 128 GB Azul', 'tz-128-azul', price='900.00', inventory=0)
        device_catalog.set_variant(blue, model_id=twin.pk, option_ids=[self.gb128.pk, self.blue.pk])
        before = self.detail(twin)
        self.palette(self.black, self.blue)
        model_palette.set_option_order(self.model, attribute_id=self.colour.pk,
                                       option_ids=[self.black.pk, self.blue.pk], actor=self.staff)
        self.assertEqual(self.detail(twin), before)


class CommercialSummaryTest(PresentationShop):
    def url(self, model=None):
        return f'/api/admin/catalog/models/{(model or self.model).pk}/commercial/{self.q}'

    def offer(self, product, **extra):
        values = dict(agreed_cost='2800.00', commercial_price='3400.00', confirmed_quantity=2,
                      valid_from=timezone.localdate() - timedelta(days=1),
                      valid_until=timezone.localdate() + timedelta(days=7), lead_time_days=10)
        values.update(extra)
        Product.objects.filter(pk=product.pk).update(sale_mode=Product.SALE_MODE_STOCK_OR_ORDER)
        return supply.create_offer(company=self.company, supplier_id=self.supplier.pk, product_id=product.pk,
                                   actor=self.staff, may_view_costs=True, **values)

    def setUp(self):
        super().setUp()
        self.supplier = supply.create_supplier(company=self.company, name='Mayorista Uno')

    def test_it_reads_what_the_existing_editors_keep(self):
        self.palette(self.gb128, self.gb256, self.black, self.blue, self.esim, self.chip, self.lla)
        model_palette.add_exclusions(self.model, pairs=[(self.chip.pk, self.lla.pk)], actor=self.staff)
        self.offer(self.new128)
        res = _client(self.viewer).get(self.url())
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body['visibility'], {'is_published_online': True, 'is_featured': False, 'sort_order': 0})
        self.assertEqual(body['palette']['has_palette'], True)
        self.assertEqual(body['palette']['options'], {'capacity': 2, 'color': 2, 'sim': 2, 'region': 1})
        self.assertEqual(body['palette']['exclusions'], 1)
        self.assertEqual(body['variants']['total'], 3)
        self.assertEqual(body['variants']['new'], 2)
        self.assertEqual(body['variants']['on_order'], 1)
        self.assertEqual(body['finishes'], {'colours': 2, 'pending': 2})
        self.assertEqual(body['suppliers'], [{'id': self.supplier.pk, 'name': 'Mayorista Uno', 'offers': 1,
                                              'variants': 1}])

    def test_no_cost_without_costs_view(self):
        self.offer(self.new128)
        body = _client(self.viewer).get(self.url()).json()
        self.assertNotIn('2800', str(body))
        self.assertNotIn('lowest_agreed_cost', str(body))
        costs, _ = _p2d_member(self.company, 'pe_costs', ['company.view', 'products.view', 'costs.view'])
        body = _client(costs).get(self.url()).json()
        self.assertEqual(body['suppliers'][0]['lowest_agreed_cost'], '2800.00')

    def test_promotions_only_with_the_promotions_capability(self):
        promo = Promotion.objects.create(company=self.company, name='Combo verano',
                                         promotion_type=Promotion.BUNDLE_FIXED_PRICE,
                                         fixed_price=Decimal('2900.00'))
        PromotionItem.objects.create(promotion=promo, product=self.new128, quantity=1)
        self.assertIsNone(_client(self.viewer).get(self.url()).json()['promotions'])
        seller, _ = _p2d_member(self.company, 'pe_promos', ['company.view', 'products.view', 'sales.promotions.view'])
        body = _client(seller).get(self.url()).json()
        self.assertEqual(body['promotions'], [{'id': promo.pk, 'name': 'Combo verano', 'is_live': True}])

    def test_a_model_of_another_company_is_not_found(self):
        other = _p3_company('pe-otra-resumen', 'Otra')
        foreign = device_catalog.create_model(company=other, name='Ajeno')
        self.assertEqual(_client(self.viewer).get(self.url(foreign)).status_code, 404)

    def test_without_products_view_it_is_refused(self):
        nobody, _ = _p2d_member(self.company, 'pe_nadie', ['company.view'])
        self.assertEqual(_client(nobody).get(self.url()).status_code, 403)


class FinishListingPresentationTest(PresentationShop):
    """The panel's finish manager: colours in the model's order, and who verified a licence."""

    def test_the_finishes_follow_the_models_colour_order(self):
        self.palette(self.black, self.blue, self.red)
        labels = lambda: [row['option']['label'] for row in  # noqa: E731
                          self.api.get(f'/api/admin/catalog/models/{self.model.pk}/finish-images/{self.q}')
                          .json()['finishes']]
        self.assertEqual(labels(), ['Azul', 'Negro', 'Rojo'])
        model_palette.set_option_order(self.model, attribute_id=self.colour.pk,
                                       option_ids=[self.red.pk, self.black.pk, self.blue.pk], actor=self.staff)
        self.assertEqual(labels(), ['Rojo', 'Negro', 'Azul'])

    def test_the_source_says_who_verified_the_licence(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from store.test_storefront_media import png_with_transparency

        self.palette(self.black, self.blue)
        with self.settings(EVIDENCE_STORAGE_BACKEND='filesystem', EVIDENCE_STORAGE_ROOT=self._root()):
            res = self.api.post(
                f'/api/admin/catalog/models/{self.model.pk}/finish-images/{self.q}',
                {'file': SimpleUploadedFile('negro.png', png_with_transparency(), content_type='image/png'),
                 'color_option_id': self.black.pk, 'source_kind': 'licensed_stock', 'license': 'Banco X, comercial'},
                format='multipart')
            self.assertEqual(res.status_code, 201, res.content)
            self.assertIsNone(res.json()['source']['license_verified_by'])
            image_id = res.json()['id']
            res = self.api.patch(f'/api/admin/catalog/models/{self.model.pk}/finish-images/{image_id}/{self.q}',
                                 {'license_verified': True}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['source']['license_verified_by'], self.staff.username)

    def _root(self):
        import shutil
        import tempfile

        root = tempfile.mkdtemp(prefix='presentation-')
        self.addCleanup(shutil.rmtree, root, True)
        return root
