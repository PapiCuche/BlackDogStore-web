"""
P2 · F4 «Imágenes propias» — el informe de las imágenes del catálogo que NO subió la tienda.

Lo que estas pruebas fijan:

  * externa es lo que no sale de la tubería de la tienda (`/api/storefront/images/<id>`, relativa o absoluta) ni es
    una ruta del propio sitio; vacía no es una imagen;
  * el informe es de UNA empresa: la del contexto del servidor, nunca la de otra;
  * modelos, variantes (productos con modelo) y productos sueltos, con el dominio externo (sólo el dominio), el
    número de imágenes externas y adónde ir a cambiarlas;
  * los dominios se agrupan; las páginas son de 200 como mucho; nunca sale un costo;
  * `products.view` para leerlo, y un número de consultas que no crece con el catálogo.

Nombres y valores inventados: nada aquí es de Apple ni de la tienda piloto.
"""
from decimal import Decimal as D

from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from store import catalog_image_sources as sources
from store import device_catalog
from store.models import Product, ProductImage, ProductModel
from store.test_device_commerce import DeviceShop, _client, assert_no_amount
from store.tests import _p2d_member, _p3_company, _prod

OWN = '/api/storefront/images/' + 'a' * 32
OWN_ABSOLUTE = 'https://tienda-propia.pe/api/storefront/images/' + 'b' * 32 + '/'
CDN = 'https://cdn.fabricante.example/fotos/telefono-x.png'
CDN_2 = 'https://CDN.fabricante.example:443/fotos/telefono-x-azul.png?v=2'
OTHER = 'http://imagenes.proveedor.example/funda.jpg'


def _set(obj, url):
    type(obj).objects.filter(pk=obj.pk).update(image_url=url)


class IsExternalTest(SimpleTestCase):
    def test_own_uploads_and_site_paths_are_not_external(self):
        for value in (OWN, OWN + '/', OWN + '?w=200', OWN_ABSOLUTE,
                      '/assets/telefono.png', '', '   ', None):
            self.assertFalse(sources.is_external(value), value)

    def test_anything_else_is_external(self):
        for value in (CDN, CDN_2, OTHER, '//cdn.fabricante.example/x.png',
                      'https://cdn.fabricante.example/api/storefront/images/corto',
                      'https://cdn.fabricante.example/otra/api/storefront/images/' + 'c' * 32):
            self.assertTrue(sources.is_external(value), value)

    def test_the_host_is_only_the_host(self):
        self.assertEqual(sources.host_of(CDN_2), 'cdn.fabricante.example')
        self.assertEqual(sources.host_of('https://usuario:clave@cdn.fabricante.example/x'), 'cdn.fabricante.example')
        self.assertEqual(sources.host_of('//cdn.fabricante.example/x.png'), 'cdn.fabricante.example')
        self.assertEqual(sources.host_of('no es una dirección'), '')


class ImageShop(DeviceShop):
    """`DeviceShop` (a model and three variants) plus two accessories, with images of every kind."""

    def build(self, slug='f4-imagenes'):
        super().build(slug)
        self.case = _prod(self.company, 'Funda clara', f'{slug}-funda', price='59.90')
        self.glass = _prod(self.company, 'Lámina de vidrio', f'{slug}-lamina', price='29.90')
        Product.objects.filter(pk=self.case.pk).update(reference_cost=D('17.31'))
        self.q = f'?company={self.company.pk}'
        self.api = _client(self.staff)
        self.url = f'/api/admin/catalog/image-sources/{self.q}'

        _set(self.model, CDN)                 # the model's cover: external
        _set(self.new128, CDN)                # a variant: external
        _set(self.new256, OWN)                # a variant: the shop's own upload
        _set(self.used128, OWN_ABSOLUTE)      # absolute address of the shop's own pipeline: own
        _set(self.case, OTHER)                # an accessory: external
        _set(self.glass, '/assets/lamina.png')  # a path of the site: not external
        # The variant's gallery: one external picture (an old row), and the primary one, its own.
        ProductImage.objects.create(company=self.company, product=self.new128, image_url=CDN_2, sort_order=1)
        ProductImage.objects.create(company=self.company, product=self.new256, image_url=OWN, is_primary=True)

    def report(self, **params):
        query = ''.join(f'&{k}={v}' for k, v in params.items())
        res = self.api.get(self.url + query)
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()


class ReportServiceTest(ImageShop, TestCase):
    def setUp(self):
        self.build('f4-servicio')

    def test_only_external_images_are_reported(self):
        data = sources.report(self.company)
        found = {(r['kind'], r['id']) for r in data['results']}
        self.assertEqual(found, {('model', self.model.pk), ('variant', self.new128.pk), ('product', self.case.pk)})
        self.assertEqual(data['summary']['models'], 1)
        self.assertEqual(data['summary']['variants'], 1)
        self.assertEqual(data['summary']['products'], 1)
        self.assertEqual(data['summary']['items'], 3)
        self.assertEqual(data['count'], 3)

    def test_an_entry_says_what_where_and_how_many(self):
        rows = {(r['kind'], r['id']): r for r in sources.report(self.company)['results']}
        variant = rows[('variant', self.new128.pk)]
        self.assertEqual(variant['name'], self.new128.name)
        self.assertEqual(variant['slug'], self.new128.slug)
        self.assertEqual(variant['host'], 'cdn.fabricante.example')
        self.assertEqual(variant['hosts'], ['cdn.fabricante.example'])
        self.assertEqual(variant['image_count'], 2)   # the address the shop shows and the gallery's old row
        self.assertEqual(variant['model_id'], self.model.pk)
        self.assertEqual(variant['admin_url'], f'/admin/products/{self.new128.pk}')
        model = rows[('model', self.model.pk)]
        self.assertEqual((model['image_count'], model['admin_url'], model['slug']),
                         (1, f'/admin/products/models/{self.model.pk}', self.model.slug))
        product = rows[('product', self.case.pk)]
        self.assertEqual((product['host'], product['image_count'], product['model_id']),
                         ('imagenes.proveedor.example', 1, None))
        for row in rows.values():
            self.assertNotIn('image_url', row)  # the host only, never the address

    def test_models_first_then_variants_then_products(self):
        kinds = [r['kind'] for r in sources.report(self.company)['results']]
        self.assertEqual(kinds, ['model', 'variant', 'product'])

    def test_hosts_are_grouped(self):
        hosts = sources.report(self.company)['summary']['hosts']
        self.assertEqual(hosts, [
            {'host': 'cdn.fabricante.example', 'items': 2, 'images': 3},
            {'host': 'imagenes.proveedor.example', 'items': 1, 'images': 1},
        ])
        self.assertEqual(sources.report(self.company)['summary']['images'], 4)

    def test_the_same_address_twice_is_one_image(self):
        ProductImage.objects.create(company=self.company, product=self.case, image_url=OTHER, sort_order=0)
        row = next(r for r in sources.report(self.company)['results'] if r['id'] == self.case.pk)
        self.assertEqual(row['image_count'], 1)

    def test_a_product_whose_only_external_image_is_in_its_gallery_is_reported(self):
        ProductImage.objects.create(company=self.company, product=self.glass, image_url=CDN, sort_order=0)
        row = next(r for r in sources.report(self.company)['results'] if r['id'] == self.glass.pk)
        self.assertEqual((row['kind'], row['host'], row['image_count']), ('product', 'cdn.fabricante.example', 1))

    def test_replacing_the_image_takes_it_off_the_report(self):
        _set(self.model, OWN)
        _set(self.case, '')
        found = {(r['kind'], r['id']) for r in sources.report(self.company)['results']}
        self.assertEqual(found, {('variant', self.new128.pk)})

    def test_an_empty_catalogue_is_an_empty_report(self):
        empty = _p3_company('f4-vacia', 'Vacía')
        data = sources.report(empty)
        self.assertEqual(data['results'], [])
        self.assertEqual(data['summary'], {'models': 0, 'variants': 0, 'products': 0, 'items': 0, 'images': 0,
                                           'hosts': []})

    def test_another_companys_images_never_appear(self):
        other = _p3_company('f4-servicio-b', 'Otra B')
        foreign = _prod(other, 'Funda ajena', 'f4-funda-ajena')
        _set(foreign, CDN)
        foreign_model = device_catalog.create_model(company=other, name='Equipo ajeno')
        _set(foreign_model, CDN)
        ProductImage.objects.create(company=other, product=foreign, image_url=OTHER)
        ids = {(r['kind'], r['id']) for r in sources.report(self.company)['results']}
        self.assertNotIn(('product', foreign.pk), ids)
        self.assertNotIn(('model', foreign_model.pk), ids)
        theirs = sources.report(other)
        self.assertEqual({(r['kind'], r['id']) for r in theirs['results']},
                         {('product', foreign.pk), ('model', foreign_model.pk)})
        self.assertEqual(theirs['summary']['images'], 3)

    def test_publication_state_is_said(self):
        ProductModel.objects.filter(pk=self.model.pk).update(is_published_online=False)
        Product.objects.filter(pk=self.case.pk).update(is_active=False)
        rows = {(r['kind'], r['id']): r for r in sources.report(self.company)['results']}
        self.assertFalse(rows[('model', self.model.pk)]['is_published'])
        self.assertFalse(rows[('product', self.case.pk)]['is_published'])
        self.assertTrue(rows[('variant', self.new128.pk)]['is_published'])

    def test_three_queries_whatever_the_size_of_the_catalogue(self):
        with self.assertNumQueries(3):
            sources.report(self.company)
        for n in range(15):
            product = _prod(self.company, f'Correa {n}', f'f4-correa-{n}')
            _set(product, f'https://cdn{n % 3}.proveedor.example/correa-{n}.png')
            ProductImage.objects.create(company=self.company, product=product, image_url=OTHER)
            model = device_catalog.create_model(company=self.company, name=f'Equipo {n}')
            _set(model, CDN)
        with self.assertNumQueries(3):
            data = sources.report(self.company)
        self.assertEqual(data['count'], 3 + 30)


class ReportPaginationTest(ImageShop, TestCase):
    def setUp(self):
        self.build('f4-paginas')

    def test_pages(self):
        first = sources.report(self.company, page=1, page_size=2)
        second = sources.report(self.company, page=2, page_size=2)
        self.assertEqual((first['count'], first['page'], first['page_size']), (3, 1, 2))
        self.assertEqual([r['kind'] for r in first['results']], ['model', 'variant'])
        self.assertEqual([r['kind'] for r in second['results']], ['product'])
        self.assertEqual(sources.report(self.company, page=3, page_size=2)['results'], [])
        # The summary is of the whole catalogue, not of the page.
        self.assertEqual(second['summary']['items'], 3)

    def test_the_page_size_is_capped_at_200(self):
        self.assertEqual(sources.report(self.company, page_size=5000)['page_size'], 200)
        self.assertEqual(self.report(page_size=5000)['page_size'], 200)

    def test_bad_values_fall_back(self):
        data = self.report(page='x', page_size='-3')
        self.assertEqual((data['page'], data['page_size']), (1, sources.DEFAULT_PAGE_SIZE))
        self.assertEqual(self.report(page='0')['page'], 1)
        self.assertEqual(self.report(page='²')['page'], 1)


class ReportApiTest(ImageShop, TestCase):
    def setUp(self):
        self.build('f4-api')

    def test_the_endpoint_returns_the_report(self):
        data = self.report()
        self.assertEqual(data['summary']['items'], 3)
        self.assertEqual(len(data['results']), 3)
        assert_no_amount(self, data, '17.31')
        for row in data['results']:
            self.assertFalse({'cost', 'reference_cost', 'price', 'unit_cost'} & set(row))

    def test_permissions(self):
        viewer, _ = _p2d_member(self.company, 'f4_viewer', ['company.view', 'products.view'])
        self.assertEqual(_client(viewer).get(self.url).status_code, 200)
        nobody, _ = _p2d_member(self.company, 'f4_nobody', ['company.view'])
        self.assertEqual(_client(nobody).get(self.url).status_code, 403)
        self.assertIn(_client().get(self.url).status_code, (401, 403))

    def test_read_only(self):
        self.assertEqual(self.api.post(self.url, {}, format='json').status_code, 405)

    def test_another_companys_report_cannot_be_asked_for(self):
        other = _p3_company('f4-api-b', 'Otra B')
        foreign = _prod(other, 'Funda ajena', 'f4-api-ajena')
        _set(foreign, CDN)
        # The caller is not a member there: the company in the query is checked, never trusted.
        res = self.api.get(f'/api/admin/catalog/image-sources/?company={other.pk}')
        self.assertEqual(res.status_code, 403)
        ids = {r['id'] for r in self.report()['results']}
        self.assertNotIn(foreign.pk, ids)

    def test_the_query_count_does_not_grow_with_the_catalogue(self):
        with CaptureQueriesContext(connection) as before:
            self.report()
        for n in range(10):
            product = _prod(self.company, f'Cable {n}', f'f4-api-cable-{n}')
            _set(product, OTHER)
        with CaptureQueriesContext(connection) as after:
            data = self.report()
        self.assertEqual(data['count'], 13)
        self.assertEqual(len(after.captured_queries), len(before.captured_queries))
