"""
PALETTE-SUGGEST-01 — proponer la paleta de un modelo desde las variantes que ya tiene.

Producción tiene 0 de 31 modelos con paleta y, sin ella, el asistente de proveedores y la lista pegada no pueden
validar variantes. La propuesta es la UNIÓN de las opciones que usan las variantes del modelo (línea nueva y
seminueva, activas e inactivas). Lo que estas pruebas fijan:

  * la propuesta es exactamente esa unión (`model_palette.payload` la lleva en `suggested_option_ids`);
  * una opción de otra empresa nunca entra, aunque una fila mal escrita la enlace a una variante;
  * un modelo sin variantes recibe una propuesta vacía;
  * todos los roles cuentan (también «Otro»): `set_palette` acepta cualquier opción de la empresa, y producción
    tiene hoy su «Capacidad» con el rol «Otro»;
  * `GET /api/admin/catalog/palette-suggestions/` lista sólo los modelos SIN paleta, con su propuesta agrupada
    por atributo en orden de rol; exige `products.view`; la empresa sale del contexto del servidor;
  * leer nunca escribe ni audita; guardar sigue siendo el PUT de la paleta, con `products.manage`;
  * un número fijo de consultas, crezca o no el catálogo.

Nombres inventados: nada aquí es de Apple ni de la tienda piloto.
"""
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from store import device_catalog, model_palette
from store.models import AdminAuditLog, ModelOption, Product, ProductVariantOption, VariantAttribute
from store.test_device_commerce import DeviceShop, _client
from store.tests import _p2d_member, _p3_company, _prod

URL = '/api/admin/catalog/palette-suggestions/'


class SuggestShop(DeviceShop):
    def build(self, slug='sug-tienda'):
        super().build(slug)
        self.q = f'?company={self.company.pk}'
        self.viewer, _ = _p2d_member(self.company, f'{slug}_viewer', ['products.view'])
        self.api = _client(self.staff)

    def palette_url(self, model=None):
        return f'/api/admin/catalog/models/{(model or self.model).pk}/palette/{self.q}'

    def listing(self, user=None):
        res = _client(user or self.viewer).get(f'{URL}{self.q}')
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def row_of(self, body, model=None):
        rows = [row for row in body['models'] if row['model_id'] == (model or self.model).pk]
        return rows[0] if rows else None


class SuggestionInPayloadTest(SuggestShop, TestCase):
    def setUp(self):
        self.build('sug-payload')

    def test_the_suggestion_is_the_union_of_every_variant_option(self):
        # 128 Negro (nueva), 256 Negro (nueva), 128 Azul (seminueva): todas las opciones usadas.
        body = model_palette.payload(self.model)
        self.assertEqual(body['suggested_option_ids'],
                         sorted([self.gb128.pk, self.gb256.pk, self.black.pk, self.blue.pk]))

    def test_inactive_and_used_variants_count_too(self):
        red = device_catalog.create_option(company=self.company, attribute_id=self.colour.pk, label='Rojo',
                                         swatch_hex='#b91c1c')
        old = self.variant('Teléfono X 256 GB Rojo usado', 'tx-256-rojo-u', '1.00', [self.gb256, red], line='used')
        Product.objects.filter(pk=old.pk).update(is_active=False)
        self.assertIn(red.pk, model_palette.payload(self.model)['suggested_option_ids'])

    def test_an_option_no_variant_uses_is_not_suggested(self):
        unused = device_catalog.create_option(company=self.company, attribute_id=self.capacity.pk, label='1 TB')
        self.assertNotIn(unused.pk, model_palette.payload(self.model)['suggested_option_ids'])

    def test_another_model_variants_do_not_count(self):
        other = device_catalog.create_model(company=self.company, name='Teléfono Y')
        tb = device_catalog.create_option(company=self.company, attribute_id=self.capacity.pk, label='1 TB')
        product = _prod(self.company, 'Teléfono Y 1 TB', 'ty-1tb', inventory=0)
        device_catalog.set_variant(product, model_id=other.pk, condition_line='new', option_ids=[tb.pk])
        self.assertNotIn(tb.pk, model_palette.payload(self.model)['suggested_option_ids'])
        self.assertEqual(model_palette.payload(other)['suggested_option_ids'], [tb.pk])

    def test_an_option_of_another_company_is_never_suggested(self):
        other = _p3_company('sug-payload-b', 'Otra B')
        foreign_attr = device_catalog.create_attribute(company=other, name='Capacidad B')
        foreign = device_catalog.create_option(company=other, attribute_id=foreign_attr.pk, label='9 TB')
        # A row written around `save()` (its guard refuses this): the suggestion must not trust it.
        ProductVariantOption.objects.bulk_create([ProductVariantOption(
            company=self.company, product=self.new128, attribute=foreign_attr, option=foreign)])
        body = model_palette.payload(self.model)
        self.assertNotIn(foreign.pk, body['suggested_option_ids'])
        self.assertEqual(body['suggested_option_ids'],
                         sorted([self.gb128.pk, self.gb256.pk, self.black.pk, self.blue.pk]))

    def test_a_model_without_variants_gets_an_empty_suggestion(self):
        empty = device_catalog.create_model(company=self.company, name='Vacío')
        self.assertEqual(model_palette.payload(empty)['suggested_option_ids'], [])

    def test_every_role_is_suggested_also_other(self):
        # Production today: «Capacidad» and «Color» with role «Otro». `set_palette` takes them: so does the suggestion.
        self.assertEqual({self.capacity.role, self.colour.role}, {VariantAttribute.ROLE_OTHER})
        self.assertIn(self.gb128.pk, model_palette.payload(self.model)['suggested_option_ids'])

    def test_the_suggestion_stays_after_a_palette_exists(self):
        model_palette.set_palette(self.model, option_ids=[self.gb128.pk, self.black.pk])
        body = model_palette.payload(self.model)
        self.assertEqual(body['suggested_option_ids'],
                         sorted([self.gb128.pk, self.gb256.pk, self.black.pk, self.blue.pk]))

    def test_the_palette_get_carries_it_and_writes_nothing(self):
        audits, rows = AdminAuditLog.objects.count(), ModelOption.objects.count()
        res = _client(self.viewer).get(self.palette_url())
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['suggested_option_ids'],
                         sorted([self.gb128.pk, self.gb256.pk, self.black.pk, self.blue.pk]))
        self.assertFalse(res.json()['has_palette'])
        self.assertEqual((AdminAuditLog.objects.count(), ModelOption.objects.count()), (audits, rows))

    def test_the_payload_reads_no_more_than_before(self):
        # The suggestion is computed from rows the payload already loaded: no query of its own.
        with CaptureQueriesContext(connection) as few:
            model_palette.payload(self.model)
        for n in range(5):
            size = device_catalog.create_option(company=self.company, attribute_id=self.capacity.pk, label=f'{n} TB')
            self.variant(f'Teléfono X extra {n}', f'tx-extra-{n}', '1.00', [size, self.blue], line='used')
        with CaptureQueriesContext(connection) as many:
            model_palette.payload(self.model)
        self.assertEqual(len(few), len(many))


class SuggestionListingTest(SuggestShop, TestCase):
    def setUp(self):
        self.build('sug-lista')

    def test_it_lists_models_without_palette_with_their_suggestion_grouped(self):
        empty = device_catalog.create_model(company=self.company, name='Vacío')
        body = self.listing()
        row = self.row_of(body)
        self.assertEqual(row['name'], 'Teléfono X')
        self.assertEqual(row['variant_count'], 3)
        self.assertEqual(row['suggested_option_ids'],
                         sorted([self.gb128.pk, self.gb256.pk, self.black.pk, self.blue.pk]))
        self.assertEqual([g['attribute_id'] for g in row['groups']], [self.capacity.pk, self.colour.pk])
        capacity = row['groups'][0]
        self.assertEqual(capacity['name'], 'Capacidad')
        self.assertEqual(capacity['role'], 'other')
        self.assertEqual([o['label'] for o in capacity['options']], ['128 GB', '256 GB'])
        colour = row['groups'][1]
        self.assertEqual(colour['kind'], 'color')
        self.assertEqual([(o['label'], o['swatch_hex']) for o in colour['options']],
                         [('Azul', '#1D4ED8'), ('Negro', '#111111')])
        none = self.row_of(body, empty)
        self.assertEqual((none['variant_count'], none['suggested_option_ids'], none['groups']), (0, [], []))
        self.assertEqual(body['summary'], {'without_palette': 2, 'with_suggestion': 1})

    def test_groups_follow_the_role_order(self):
        VariantAttribute.objects.filter(pk=self.capacity.pk).update(role='capacity', sort_order=9)
        VariantAttribute.objects.filter(pk=self.colour.pk).update(role='color', sort_order=0)
        sim = device_catalog.create_attribute(company=self.company, name='Tipo de SIM', role='sim', sort_order=0)
        esim = device_catalog.create_option(company=self.company, attribute_id=sim.pk, label='eSIM')
        self.variant('Teléfono X 128 GB Negro eSIM', 'tx-esim', '1.00', [self.gb128, self.black, esim])
        row = self.row_of(self.listing())
        self.assertEqual([g['role'] for g in row['groups']], ['capacity', 'color', 'sim'])

    def test_it_counts_the_variants_that_would_stand_incomplete(self):
        # One variant carries a SIM option, the three others do not: saving the suggestion leaves those incomplete.
        sim = device_catalog.create_attribute(company=self.company, name='Tipo de SIM', role='sim')
        esim = device_catalog.create_option(company=self.company, attribute_id=sim.pk, label='eSIM')
        self.variant('Teléfono X 128 GB Negro eSIM', 'tx-esim', '1.00', [self.gb128, self.black, esim])
        self.assertEqual(self.row_of(self.listing())['incomplete_variants'], 3)
        # Without the SIM option on it, every variant carries every suggested attribute.
        ProductVariantOption.objects.filter(option=esim).delete()
        self.assertEqual(self.row_of(self.listing())['incomplete_variants'], 0)

    def test_a_model_with_a_palette_is_not_listed(self):
        model_palette.set_palette(self.model, option_ids=[self.gb128.pk, self.black.pk])
        body = self.listing()
        self.assertIsNone(self.row_of(body))
        self.assertEqual(body['summary'], {'without_palette': 0, 'with_suggestion': 0})

    def test_saving_the_shown_ids_through_the_put_makes_every_variant_fit(self):
        row = self.row_of(self.listing())
        res = self.api.put(self.palette_url(), {'option_ids': row['suggested_option_ids']}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.json()['has_palette'])
        self.assertEqual({v['status'] for v in res.json()['variants']}, {'variante_existente'})
        self.assertTrue(AdminAuditLog.objects.filter(action='product_model_palette_set').exists())
        self.assertIsNone(self.row_of(self.listing()))


class SuggestionAccessTest(SuggestShop, TestCase):
    def setUp(self):
        self.build('sug-acceso')

    def test_without_products_view_it_is_403(self):
        user, _ = _p2d_member(self.company, 'sug_sin_permiso', ['company.view', 'inventory.view'])
        res = _client(user).get(f'{URL}{self.q}')
        self.assertEqual(res.status_code, 403)
        self.assertNotIn('models', res.json())

    def test_anonymous_is_refused(self):
        self.assertIn(_client().get(URL).status_code, (401, 403))

    def test_products_view_reads_but_cannot_save(self):
        self.listing(self.viewer)
        res = _client(self.viewer).put(self.palette_url(), {'option_ids': [self.gb128.pk]}, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertFalse(ModelOption.objects.filter(model=self.model).exists())

    def test_it_is_read_only(self):
        before = (AdminAuditLog.objects.count(), ModelOption.objects.count())
        for method in ('post', 'put', 'patch', 'delete'):
            res = getattr(self.api, method)(f'{URL}{self.q}', {'option_ids': [self.gb128.pk]}, format='json')
            self.assertEqual(res.status_code, 405, method)
        self.listing(self.staff)
        self.assertEqual((AdminAuditLog.objects.count(), ModelOption.objects.count()), before)

    def test_no_price_nor_cost_is_exposed(self):
        Product.objects.filter(company=self.company).update(reference_cost='4321.98', price='8765.43')
        body = self.api.get(f'{URL}{self.q}').content.decode()
        self.assertNotIn('4321', body)
        self.assertNotIn('8765', body)
        self.assertNotIn('cost', body.lower())


class SuggestionIsolationTest(SuggestShop, TestCase):
    def setUp(self):
        self.build('sug-a')
        self.b = _p3_company('sug-b', 'Otra B')
        attr = device_catalog.create_attribute(company=self.b, name='Almacenamiento B', role='capacity')
        self.foreign = device_catalog.create_option(company=self.b, attribute_id=attr.pk, label='9 TB')
        model = device_catalog.create_model(company=self.b, name='Secreto B')
        product = _prod(self.b, 'Secreto B 9 TB', 'secreto-b', inventory=0)
        device_catalog.set_variant(product, model_id=model.pk, condition_line='new', option_ids=[self.foreign.pk])

    def test_another_company_never_shows(self):
        body = self.api.get(f'{URL}{self.q}').content.decode()
        for leak in ('Secreto B', 'Almacenamiento B', '9 TB'):
            self.assertNotIn(leak, body)
        self.assertEqual([row['name'] for row in self.listing()['models']], ['Teléfono X'])

    def test_asking_for_another_company_is_refused(self):
        res = self.api.get(f'{URL}?company={self.b.pk}')
        self.assertIn(res.status_code, (403, 404))
        self.assertNotIn('Secreto B', res.content.decode())

    def test_the_service_reads_only_the_company_it_is_given(self):
        rows = model_palette.suggestions(self.company)['models']
        self.assertEqual([row['name'] for row in rows], ['Teléfono X'])
        self.assertNotIn(self.foreign.pk, rows[0]['suggested_option_ids'])


class SuggestionQueryCountTest(SuggestShop, TestCase):
    def setUp(self):
        self.build('sug-consultas')

    def test_the_service_uses_four_queries(self):
        with self.assertNumQueries(4):
            model_palette.suggestions(self.company)

    def test_the_endpoint_does_not_grow_with_the_catalogue(self):
        url = f'{URL}{self.q}'
        self.api.get(url)
        with CaptureQueriesContext(connection) as small:
            self.assertEqual(self.api.get(url).status_code, 200)
        for n in range(8):
            model = device_catalog.create_model(company=self.company, name=f'Modelo {n}')
            product = _prod(self.company, f'Modelo {n} 128', f'modelo-{n}-128', inventory=0)
            device_catalog.set_variant(product, model_id=model.pk, condition_line='new',
                                       option_ids=[self.gb128.pk, self.blue.pk])
        device_catalog.create_attribute(company=self.company, name='Acabado')
        with CaptureQueriesContext(connection) as large:
            self.assertEqual(self.api.get(url).status_code, 200)
        self.assertEqual(len(small), len(large))
        with self.assertNumQueries(4):
            self.assertEqual(len(model_palette.suggestions(self.company)['models']), 9)
