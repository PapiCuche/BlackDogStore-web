"""
CATALOG-READINESS-01 — «Preparación del catálogo»: qué le falta a una empresa antes de registrar su inventario
(equipos nuevos, seminuevos y accesorios) con el asistente de listas de proveedor y «Recibir mercadería».

Lo que estas pruebas fijan:

  * `GET /api/admin/catalog/readiness/` exige `products.view`; la empresa sale del contexto del servidor;
  * cada comprobación pasa de `missing`/`warning` a `ok` cuando se corrige lo que nombra;
  * los roles dicen lo que hacen hoy las herramientas: las dos leen capacidad por rol o, sin rol Capacidad, por
    el ÚNICO nombre de capacidad (A1b; aviso, no falta); el asistente de proveedores lee color por rol o, sin rol
    Color, por el tipo color; la lista pegada lee color por el ÚNICO atributo de tipo color; un rol sin opciones
    no sirve;
  * lo de otra empresa nunca cuenta ni aparece;
  * un número fijo de consultas, crezca o no el catálogo;
  * nada se escribe y ningún costo sale en la respuesta.

Nombres inventados: nada aquí es de Apple ni de la tienda piloto.
"""
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from store import catalog_readiness, device_catalog, model_palette
from store.models import (
    AdminAuditLog, Branch, CatalogAlias, Category, CosmeticGrade, Product, ProductModel, VariantAttribute,
)
from store.test_device_commerce import DeviceShop, _client
from store.tests import _p2d_member, _p3_company, _prod

URL = '/api/admin/catalog/readiness/'


def _by_key(payload):
    return {check['key']: check for check in payload['checks']}


class ReadinessShop(DeviceShop):
    def build(self, slug='ready-tienda'):
        super().build(slug)
        self.viewer, _ = _p2d_member(self.company, f'{slug}_viewer', ['products.view'])
        self.api = _client(self.viewer)

    def checks(self):
        res = self.api.get(f'{URL}?company={self.company.pk}')
        self.assertEqual(res.status_code, 200, res.content)
        return _by_key(res.json())

    def check(self, key):
        return self.checks()[key]


class AccessTest(ReadinessShop, TestCase):
    def setUp(self):
        self.build('ready-acceso')

    def test_without_products_view_it_is_403(self):
        user, _ = _p2d_member(self.company, 'ready_sin_permiso', ['company.view', 'inventory.view'])
        res = _client(user).get(f'{URL}?company={self.company.pk}')
        self.assertEqual(res.status_code, 403)
        self.assertNotIn('checks', res.json())

    def test_anonymous_is_refused(self):
        self.assertIn(_client().get(URL).status_code, (401, 403))

    def test_it_is_read_only(self):
        before = (AdminAuditLog.objects.count(), VariantAttribute.objects.count(), ProductModel.objects.count())
        for method in ('post', 'put', 'patch', 'delete'):
            res = getattr(self.api, method)(f'{URL}?company={self.company.pk}', {}, format='json')
            self.assertEqual(res.status_code, 405, method)
        self.checks()
        after = (AdminAuditLog.objects.count(), VariantAttribute.objects.count(), ProductModel.objects.count())
        self.assertEqual(before, after)

    def test_no_cost_is_exposed(self):
        Product.objects.filter(company=self.company).update(reference_cost=Decimal('4321.98'))
        res = self.api.get(f'{URL}?company={self.company.pk}')
        self.assertNotIn('4321', res.content.decode())
        self.assertNotIn('cost', res.content.decode().lower())


class ShapeTest(ReadinessShop, TestCase):
    def setUp(self):
        self.build('ready-forma')

    def test_every_check_has_the_contract(self):
        res = self.api.get(f'{URL}?company={self.company.pk}').json()
        keys = [check['key'] for check in res['checks']]
        self.assertEqual(keys, [
            'role_capacity', 'role_color', 'palette', 'published_without_variant', 'grades', 'branches',
            'categories', 'role_sim', 'role_region', 'accessories', 'aliases',
        ])
        for check in res['checks']:
            self.assertIn(check['status'], ('ok', 'warning', 'missing'), check)
            self.assertIn(check['kind'], ('required', 'recommended', 'optional', 'info'), check)
            self.assertTrue(check['title'] and check['detail'], check)
            self.assertTrue(check['action'].startswith('/admin/'), check)
            self.assertTrue(check['action_label'], check)
            self.assertIsInstance(check['counts'], dict, check)
            self.assertIsInstance(check['samples'], list, check)
        summary = res['summary']
        self.assertEqual(summary['ok'] + summary['warning'] + summary['missing'], len(res['checks']))
        self.assertEqual(res['ready'], summary['missing'] == 0)


class ProductionShapeTest(TestCase):
    """Production today: «Capacidad» (text, role other, 4 options) and «Color» (colour, role other, 2 options)."""

    def setUp(self):
        self.company = _p3_company('ready-prod', 'Como producción')
        capacity = device_catalog.create_attribute(company=self.company, name='Capacidad')
        colour = device_catalog.create_attribute(company=self.company, name='Color', kind='color')
        for label in ('128 GB', '256 GB', '512 GB', '1 TB'):
            device_catalog.create_option(company=self.company, attribute_id=capacity.pk, label=label)
        for label, swatch in (('Negro', '#111111'), ('Azul', '#1d4ed8')):
            device_catalog.create_option(company=self.company, attribute_id=colour.pk, label=label, swatch_hex=swatch)
        user, _ = _p2d_member(self.company, 'ready_prod_viewer', ['products.view'])
        self.checks = _by_key(_client(user).get(f'{URL}?company={self.company.pk}').json())

    def test_capacity_works_through_its_name_and_is_a_warning(self):
        # RESOLVER-CAPACITY-FALLBACK: the pasted list AND the supplier assistant read it by its name (A1b).
        check = self.checks['role_capacity']
        self.assertEqual(check['status'], 'warning')
        self.assertEqual(check['kind'], 'required')
        self.assertIn('"Capacidad" se reconoce por su nombre; asígnale el rol Capacidad para no depender del '
                      'nombre', check['detail'])
        self.assertEqual(check['counts'], {'with_role': 0, 'options': 4, 'named_like': 1})
        self.assertEqual(check['action'], '/admin/products/models#atributos')

    def test_it_is_ready_with_two_warnings(self):
        self.assertEqual((self.checks['role_capacity']['status'], self.checks['role_color']['status']),
                         ('warning', 'warning'))

    def test_colour_works_through_its_kind_and_is_a_warning(self):
        check = self.checks['role_color']
        self.assertEqual(check['status'], 'warning')
        self.assertIn('"Color"', check['detail'])
        self.assertIn('Asígnale el rol Color', check['detail'])
        self.assertEqual(check['counts'], {'kind_color': 1, 'with_role': 0, 'options': 2})


class CapacityRoleTest(ReadinessShop, TestCase):
    def setUp(self):
        self.build('ready-capacidad')

    def test_the_role_with_options_is_ok(self):
        VariantAttribute.objects.filter(pk=self.capacity.pk).update(role='capacity')
        check = self.check('role_capacity')
        self.assertEqual(check['status'], 'ok')
        self.assertIn('"Capacidad"', check['detail'])
        self.assertEqual(check['counts'], {'with_role': 1, 'options': 2, 'named_like': 0})

    def test_the_role_without_options_is_missing(self):
        empty = device_catalog.create_attribute(company=self.company, name='Almacenaje', role='capacity')
        check = self.check('role_capacity')
        self.assertEqual((check['status'], check['counts']['options']), ('missing', 0))
        self.assertIn('"Almacenaje" tiene el rol Capacidad pero no tiene opciones', check['detail'])
        device_catalog.create_option(company=self.company, attribute_id=empty.pk, label='64 GB')
        self.assertEqual(self.check('role_capacity')['status'], 'ok')

    def test_a_name_the_paste_list_does_not_know_gets_no_hint(self):
        VariantAttribute.objects.filter(pk=self.capacity.pk).update(name='Memoria')
        check = self.check('role_capacity')
        self.assertEqual((check['status'], check['counts']['named_like']), ('missing', 0))
        self.assertNotIn('Memoria', check['detail'])
        self.assertIn('Ningún atributo tiene el rol Capacidad', check['detail'])

    def test_a_name_without_options_is_missing(self):
        empty = device_catalog.create_attribute(company=self.company, name='Almacenamiento')
        VariantAttribute.objects.filter(pk=self.capacity.pk).update(name='Memoria')
        check = self.check('role_capacity')
        self.assertEqual((check['status'], check['counts']), ('missing', {'with_role': 0, 'options': 0, 'named_like': 1}))
        self.assertIn('"Almacenamiento" se reconoce por su nombre pero no tiene opciones', check['detail'])
        device_catalog.create_option(company=self.company, attribute_id=empty.pk, label='64 GB')
        self.assertEqual(self.check('role_capacity')['status'], 'warning')

    def test_a_name_with_another_role_is_missing(self):
        VariantAttribute.objects.filter(pk=self.capacity.pk).update(role='sim')
        check = self.check('role_capacity')
        self.assertEqual(check['status'], 'missing')
        self.assertIn('"Capacidad" tiene el rol SIM', check['detail'])

    def test_a_colour_attribute_named_like_a_capacity_is_missing(self):
        VariantAttribute.objects.filter(pk=self.capacity.pk).update(name='Memoria')
        VariantAttribute.objects.filter(pk=self.colour.pk).update(name='Storage')
        check = self.check('role_capacity')
        self.assertEqual(check['status'], 'missing')
        self.assertIn('"Storage" es de tipo color', check['detail'])

    def test_two_names_matching_are_named_and_the_paste_list_does_not_read_them_either(self):
        device_catalog.create_attribute(company=self.company, name='Storage')
        check = self.check('role_capacity')
        self.assertEqual((check['status'], check['counts']['named_like']), ('missing', 2))
        self.assertIn('"Capacidad"', check['detail'])
        self.assertIn('"Storage"', check['detail'])
        self.assertIn('ni la lista pegada ni el asistente de proveedores', check['detail'])


class ColourRoleTest(ReadinessShop, TestCase):
    def setUp(self):
        self.build('ready-color')

    def test_the_role_on_the_only_colour_attribute_is_ok(self):
        VariantAttribute.objects.filter(pk=self.colour.pk).update(role='color')
        check = self.check('role_color')
        self.assertEqual(check['status'], 'ok')
        self.assertEqual(check['counts'], {'kind_color': 1, 'with_role': 1, 'options': 2})

    def test_the_kind_fallback_is_a_warning(self):
        check = self.check('role_color')
        self.assertEqual(check['status'], 'warning')
        self.assertIn('Asígnale el rol Color', check['detail'])

    def test_the_colour_role_on_a_text_attribute_is_missing(self):
        VariantAttribute.objects.filter(pk=self.colour.pk).update(kind='text', role='color')
        check = self.check('role_color')
        self.assertEqual(check['status'], 'missing')
        self.assertIn('"Color" tiene el rol Color pero es de tipo texto', check['detail'])
        # Still missing with another colour-kind attribute: the two tools would read different attributes.
        device_catalog.create_attribute(company=self.company, name='Tono', kind='color')
        check = self.check('role_color')
        self.assertEqual(check['status'], 'missing')
        self.assertIn('es de tipo texto', check['detail'])

    def test_no_colour_kind_attribute_is_missing(self):
        VariantAttribute.objects.filter(pk=self.colour.pk).update(kind='text')
        check = self.check('role_color')
        self.assertEqual((check['status'], check['counts']['kind_color']), ('missing', 0))
        self.assertIn('Ningún atributo es de tipo color', check['detail'])

    def test_two_colour_kind_attributes_are_missing(self):
        VariantAttribute.objects.filter(pk=self.colour.pk).update(role='color')
        device_catalog.create_attribute(company=self.company, name='Acabado', kind='color')
        check = self.check('role_color')
        self.assertEqual((check['status'], check['counts']['kind_color']), ('missing', 2))
        self.assertIn('Hay 2 atributos de tipo color', check['detail'])
        self.assertIn('"Acabado"', check['detail'])
        self.assertIn('no tiene un único atributo de color', check['detail'])

    def test_a_colour_attribute_without_options_is_missing(self):
        # The colour in use turns into text; a new colour attribute has no option yet.
        VariantAttribute.objects.filter(pk=self.colour.pk).update(kind='text')
        tono = device_catalog.create_attribute(company=self.company, name='Tono', kind='color')
        for role in ('other', 'color'):
            VariantAttribute.objects.filter(pk=tono.pk).update(role=role)
            check = self.check('role_color')
            self.assertEqual((check['status'], check['counts']['options']), ('missing', 0), role)
            self.assertIn('"Tono" no tiene opciones', check['detail'])

    def test_the_colour_kind_under_another_role_is_missing(self):
        VariantAttribute.objects.filter(pk=self.colour.pk).update(role='region')
        check = self.check('role_color')
        self.assertEqual(check['status'], 'missing')
        self.assertIn('"Color" es de tipo color pero tiene el rol Región', check['detail'])


class SimRegionRoleTest(ReadinessShop, TestCase):
    def setUp(self):
        self.build('ready-simreg')

    def test_they_are_warnings_only_and_need_an_option(self):
        checks = self.checks()
        for key in ('role_sim', 'role_region'):
            self.assertEqual((checks[key]['status'], checks[key]['kind']), ('warning', 'optional'), key)
            self.assertIn('si vendes variantes por SIM/región', checks[key]['detail'])
        sim = device_catalog.create_attribute(company=self.company, name='Tipo de SIM', role='sim')
        region = device_catalog.create_attribute(company=self.company, name='Región', role='region')
        checks = self.checks()
        for key, name, label in (('role_sim', 'Tipo de SIM', 'SIM'), ('role_region', 'Región', 'Región')):
            self.assertEqual((checks[key]['status'], checks[key]['counts']['options']), ('warning', 0), key)
            self.assertIn(f'"{name}" tiene el rol {label} pero no tiene opciones', checks[key]['detail'])
        device_catalog.create_option(company=self.company, attribute_id=sim.pk, label='eSIM')
        device_catalog.create_option(company=self.company, attribute_id=region.pk, label='LL/A')
        checks = self.checks()
        self.assertEqual((checks['role_sim']['status'], checks['role_region']['status']), ('ok', 'ok'))
        self.assertEqual(checks['role_sim']['counts'], {'with_role': 1, 'options': 1})

    def test_a_name_alone_never_counts(self):
        device_catalog.create_attribute(company=self.company, name='SIM')
        check = self.check('role_sim')
        self.assertEqual((check['status'], check['counts']['with_role']), ('warning', 0))


class PaletteTest(ReadinessShop, TestCase):
    def setUp(self):
        self.build('ready-paleta')
        VariantAttribute.objects.filter(pk=self.capacity.pk).update(role='capacity')
        VariantAttribute.objects.filter(pk=self.colour.pk).update(role='color')

    def test_from_missing_to_warning_to_ok(self):
        check = self.check('palette')
        self.assertEqual(check['status'], 'missing')
        self.assertEqual(check['counts'], {'without_palette': 1, 'models': 1})
        self.assertEqual(check['samples'], [{'id': self.model.pk, 'name': 'Teléfono X',
                                             'path': f'/admin/products/models/{self.model.pk}#paleta'}])
        self.assertEqual(check['action'], f'/admin/products/models/{self.model.pk}#paleta')

        other = device_catalog.create_model(company=self.company, name='Tableta Y')
        model_palette.set_palette(self.model, option_ids=[self.gb128.pk, self.black.pk], actor=self.staff)
        check = self.check('palette')
        self.assertEqual(check['status'], 'warning')
        self.assertEqual(check['counts'], {'without_palette': 1, 'models': 2})
        self.assertEqual([s['name'] for s in check['samples']], ['Tableta Y'])

        model_palette.set_palette(other, option_ids=[self.gb256.pk], actor=self.staff)
        check = self.check('palette')
        self.assertEqual((check['status'], check['samples']), ('ok', []))
        self.assertEqual(check['action'], '/admin/products/models')

    def test_samples_are_at_most_five(self):
        for n in range(7):
            device_catalog.create_model(company=self.company, name=f'Equipo {n}')
        check = self.check('palette')
        self.assertEqual(check['counts']['without_palette'], 8)
        self.assertEqual(len(check['samples']), 5)

    def test_no_models_at_all_is_missing(self):
        company = _p3_company('ready-sin-modelos', 'Sin modelos')
        user, _ = _p2d_member(company, 'ready_sin_modelos', ['products.view'])
        checks = _by_key(_client(user).get(f'{URL}?company={company.pk}').json())
        self.assertEqual(checks['palette']['status'], 'missing')
        self.assertEqual(checks['palette']['counts'], {'without_palette': 0, 'models': 0})
        self.assertIn('Todavía no hay modelos', checks['palette']['detail'])


class PublishedWithoutVariantTest(ReadinessShop, TestCase):
    def setUp(self):
        self.build('ready-publicado')

    def test_a_published_model_without_a_published_variant_is_a_warning(self):
        self.assertEqual(self.check('published_without_variant')['status'], 'ok')
        empty = device_catalog.create_model(company=self.company, name='Reloj Z')
        check = self.check('published_without_variant')
        self.assertEqual((check['status'], check['kind']), ('warning', 'recommended'))
        self.assertEqual(check['counts'], {'published_without_variant': 1, 'published_models': 2})
        self.assertEqual(check['samples'][0]['path'], f'/admin/products/models/{empty.pk}#variantes')
        # A hidden model is not offered: nothing to warn about.
        ProductModel.objects.filter(pk=empty.pk).update(is_published_online=False)
        self.assertEqual(self.check('published_without_variant')['status'], 'ok')

    def test_hidden_or_inactive_variants_do_not_count(self):
        Product.objects.filter(product_model=self.model).update(is_published_online=False)
        self.assertEqual(self.check('published_without_variant')['status'], 'warning')
        Product.objects.filter(pk=self.new128.pk).update(is_published_online=True, is_active=False)
        self.assertEqual(self.check('published_without_variant')['status'], 'warning')
        Product.objects.filter(pk=self.new128.pk).update(is_active=True)
        self.assertEqual(self.check('published_without_variant')['status'], 'ok')

    def test_a_pending_draft_is_not_a_published_variant(self):
        Product.objects.filter(product_model=self.model).exclude(pk=self.new128.pk).update(is_published_online=False)
        Product.objects.filter(pk=self.new128.pk).update(draft_origin='palette', sale_enabled_at=None)
        self.assertEqual(self.check('published_without_variant')['status'], 'warning')


class CompanyBasicsTest(ReadinessShop, TestCase):
    def setUp(self):
        self.build('ready-basicos')

    def test_grades(self):
        check = self.check('grades')
        self.assertEqual((check['status'], check['kind']), ('ok', 'required'))
        self.assertGreater(check['counts']['active'], 0)
        CosmeticGrade.objects.filter(company=self.company).update(is_active=False)
        check = self.check('grades')
        self.assertEqual((check['status'], check['counts']['active']), ('missing', 0))
        self.assertEqual(check['action'], '/admin/products/models#escala')

    def test_branches(self):
        self.assertEqual(self.check('branches')['status'], 'ok')
        Branch.objects.filter(company=self.company).update(is_active=False)
        check = self.check('branches')
        self.assertEqual((check['status'], check['counts']['active'], check['action']),
                         ('missing', 0, '/admin/branches'))

    def test_categories(self):
        Category.objects.filter(company=self.company).delete()
        check = self.check('categories')
        self.assertEqual((check['status'], check['action']), ('missing', '/admin/products/categories'))
        Category.objects.create(company=self.company, name='Retirada', slug='retirada', is_active=False)
        self.assertEqual(self.check('categories')['status'], 'missing')
        Category.objects.create(company=self.company, name='Teléfonos', slug='telefonos')
        check = self.check('categories')
        self.assertEqual((check['status'], check['counts']['active']), ('ok', 1))

    def test_accessories(self):
        Product.objects.filter(company=self.company, product_model__isnull=True).delete()
        check = self.check('accessories')
        self.assertEqual((check['status'], check['kind'], check['counts']['accessories']), ('warning', 'optional', 0))
        _prod(self.company, 'Funda azul', 'funda-azul')
        check = self.check('accessories')
        self.assertEqual((check['status'], check['counts']['accessories']), ('ok', 1))

    def test_aliases_are_informational(self):
        check = self.check('aliases')
        self.assertEqual((check['status'], check['kind'], check['counts']['aliases']), ('ok', 'info', 0))
        self.assertEqual(check['action'], '/admin/products/models#vocabulario')
        CatalogAlias.objects.create(company=self.company, scope='model_word', alias='Tlf', alias_key='tlf',
                                    replacement='telefono')
        self.assertEqual(self.check('aliases')['counts']['aliases'], 1)

    def test_ready_once_nothing_required_is_missing(self):
        self.assertFalse(self.api.get(f'{URL}?company={self.company.pk}').json()['ready'])
        VariantAttribute.objects.filter(pk=self.capacity.pk).update(role='capacity')
        VariantAttribute.objects.filter(pk=self.colour.pk).update(role='color')
        model_palette.set_palette(self.model, option_ids=[self.gb128.pk, self.black.pk], actor=self.staff)
        Category.objects.get_or_create(company=self.company, slug='telefonos', defaults={'name': 'Teléfonos'})
        res = self.api.get(f'{URL}?company={self.company.pk}').json()
        self.assertTrue(res['ready'], [c for c in res['checks'] if c['status'] == 'missing'])
        self.assertEqual(res['summary']['missing'], 0)


class IsolationTest(ReadinessShop, TestCase):
    def setUp(self):
        self.build('ready-a')
        # Company B has everything A lacks.
        self.b = _p3_company('ready-b', 'Otra B')
        cap = device_catalog.create_attribute(company=self.b, name='Almacenamiento B', role='capacity')
        device_catalog.create_attribute(company=self.b, name='Tono B', role='color')
        device_catalog.create_attribute(company=self.b, name='SIM B', role='sim')
        device_catalog.create_attribute(company=self.b, name='Región B', role='region')
        option = device_catalog.create_option(company=self.b, attribute_id=cap.pk, label='1 TB')
        model = device_catalog.create_model(company=self.b, name='Secreto B')
        model_palette.set_palette(model, option_ids=[option.pk])
        device_catalog.create_model(company=self.b, name='Oculto B sin paleta')
        Category.objects.create(company=self.b, name='Cat B', slug='cat-b')
        CatalogAlias.objects.create(company=self.b, scope='model_word', alias='Sb', alias_key='sb',
                                    replacement='secreto')
        _prod(self.b, 'Cable B', 'cable-b')
        Category.objects.filter(company=self.company).delete()
        Product.objects.filter(company=self.company, product_model__isnull=True).delete()

    def test_another_company_never_counts_nor_shows(self):
        res = self.api.get(f'{URL}?company={self.company.pk}')
        body = res.content.decode()
        for leak in ('Secreto B', 'Oculto B', 'Almacenamiento B', 'Tono B', 'Región B', 'Cat B', 'Cable B'):
            self.assertNotIn(leak, body)
        checks = _by_key(res.json())
        # B's capacity ROLE never counts for A: A's «Capacidad» works only through its name (a warning).
        self.assertEqual((checks['role_capacity']['status'], checks['role_capacity']['counts']['with_role']),
                         ('warning', 0))
        self.assertEqual(checks['role_sim']['status'], 'warning')
        self.assertEqual(checks['palette']['counts'], {'without_palette': 1, 'models': 1})
        self.assertEqual(checks['categories']['counts']['active'], 0)
        self.assertEqual(checks['accessories']['counts']['accessories'], 0)
        self.assertEqual(checks['aliases']['counts']['aliases'], 0)

    def test_asking_for_another_company_is_refused(self):
        res = self.api.get(f'{URL}?company={self.b.pk}')
        self.assertIn(res.status_code, (403, 404))
        self.assertNotIn('Secreto B', res.content.decode())


class QueryCountTest(ReadinessShop, TestCase):
    def setUp(self):
        self.build('ready-consultas')

    def test_the_service_uses_three_queries(self):
        with self.assertNumQueries(3):
            catalog_readiness.readiness(self.company)

    def test_the_endpoint_does_not_grow_with_the_catalogue(self):
        url = f'{URL}?company={self.company.pk}'
        self.api.get(url)
        with CaptureQueriesContext(connection) as small:
            self.assertEqual(self.api.get(url).status_code, 200)
        for n in range(12):
            model = device_catalog.create_model(company=self.company, name=f'Modelo {n}')
            if n % 2:
                model_palette.set_palette(model, option_ids=[self.gb128.pk])
            _prod(self.company, f'Accesorio {n}', f'accesorio-{n}')
        Category.objects.create(company=self.company, name='Más', slug='mas')
        device_catalog.create_attribute(company=self.company, name='Acabado')
        with CaptureQueriesContext(connection) as large:
            self.assertEqual(self.api.get(url).status_code, 200)
        self.assertEqual(len(small), len(large))
