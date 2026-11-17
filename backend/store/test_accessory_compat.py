"""
P2 · F3 — accesorios compatibles de un modelo (`AccessoryCompatibility`).

Lo que estas pruebas fijan:

  * un vínculo es de UNA empresa: el modelo, el accesorio y el vínculo; la base (vía `save()`/`clean()`) y la API
    rechazan otra empresa;
  * un accesorio es un producto SIN modelo: una variante (un equipo) no se vincula como accesorio;
  * `set_for_model` reemplaza la lista ordenada entera, rechaza duplicados y audita sólo cuando algo cambió;
  * la API de administración: `products.view` lee, `products.manage` escribe, 404 para el modelo de otra empresa,
    400 para un cuerpo malo; la búsqueda de candidatos sólo ofrece accesorios de la empresa;
  * la tienda pública (`public_model`) añade `accessories`: sólo activos y publicados, con precio y
    disponibilidad, lista vacía sin datos, nunca un costo, y un número de consultas que no crece con la lista.

Nombres y valores inventados: nada aquí es de Apple ni de la tienda piloto.
"""
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from store import accessory_compat, device_catalog
from store.models import AccessoryCompatibility, AdminAuditLog, BranchStock, CompanySettings, Product
from store.test_device_commerce import DeviceShop, _client, assert_no_amount
from store.test_supplier_offers import SupplyShop
from store.tests import _p2d_member, _p3_company, _prod


class AccessoryShop(DeviceShop):
    """`DeviceShop` plus three accessories of the same company."""

    def build(self, slug='f3-accesorios'):
        super().build(slug)
        self.case = _prod(self.company, 'Funda clara', f'{slug}-funda', price='59.90', inventory=4)
        self.glass = _prod(self.company, 'Lámina de vidrio', f'{slug}-lamina', price='29.90', inventory=0)
        self.charger = _prod(self.company, 'Cargador 20 W', f'{slug}-cargador', price='89.00', inventory=2)
        Product.objects.filter(pk=self.case.pk).update(reference_cost=D('17.31'))
        self.q = f'?company={self.company.pk}'
        self.api = _client(self.staff)
        self.url = f'/api/admin/catalog/models/{self.model.pk}/accessories/{self.q}'

    def link(self, *products, model=None):
        return accessory_compat.set_for_model(model or self.model, accessory_ids=[p.pk for p in products],
                                              actor=self.staff)

    def linked(self, model=None):
        return list(AccessoryCompatibility.objects.filter(model=model or self.model)
                    .order_by('sort_order', 'pk').values_list('accessory_id', flat=True))


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------

class AccessoryCompatibilityModelTest(AccessoryShop, TestCase):
    def setUp(self):
        self.build('f3-modelo')

    def test_a_link_of_one_company_is_saved(self):
        row = AccessoryCompatibility.objects.create(company=self.company, model=self.model, accessory=self.case)
        self.assertEqual((row.sort_order, row.accessory_id), (0, self.case.pk))
        self.assertIsNotNone(row.created_at)

    def test_another_companys_accessory_or_model_is_refused(self):
        other = _p3_company('f3-modelo-b', 'Otra B')
        foreign = _prod(other, 'Funda ajena', 'f3-funda-ajena')
        with self.assertRaises(ValidationError):
            AccessoryCompatibility.objects.create(company=self.company, model=self.model, accessory=foreign)
        foreign_model = device_catalog.create_model(company=other, name='Equipo ajeno')
        with self.assertRaises(ValidationError):
            AccessoryCompatibility.objects.create(company=self.company, model=foreign_model, accessory=self.case)
        with self.assertRaises(ValidationError):
            AccessoryCompatibility.objects.create(company=other, model=self.model, accessory=self.case)
        self.assertFalse(AccessoryCompatibility.objects.exists())

    def test_a_device_is_not_an_accessory(self):
        with self.assertRaises(ValidationError):
            AccessoryCompatibility.objects.create(company=self.company, model=self.model, accessory=self.new128)
        with self.assertRaises(ValidationError):
            AccessoryCompatibility(company=self.company, model=self.model, accessory=self.new128).clean()

    def test_one_accessory_once_per_model(self):
        AccessoryCompatibility.objects.create(company=self.company, model=self.model, accessory=self.case)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AccessoryCompatibility.objects.create(company=self.company, model=self.model, accessory=self.case,
                                                  sort_order=1)

    def test_deleting_the_model_or_the_accessory_deletes_the_link(self):
        # A product with no stock history (the shop never deletes one that has it).
        loose = Product.objects.create(company=self.company, name='Correa', slug='f3-correa', price=D('9.90'))
        second = device_catalog.create_model(company=self.company, name='Teléfono Y')
        self.link(self.case, loose)
        self.link(loose, model=second)
        loose.delete()
        self.assertEqual(self.linked(), [self.case.pk])
        second.delete()
        self.assertEqual(AccessoryCompatibility.objects.count(), 1)


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------

class SetForModelTest(AccessoryShop, TestCase):
    def setUp(self):
        self.build('f3-servicio')

    def test_the_list_is_replaced_in_order(self):
        result = self.link(self.glass, self.case)
        self.assertTrue(result['changed'])
        self.assertEqual(self.linked(), [self.glass.pk, self.case.pk])
        self.link(self.charger, self.glass)
        self.assertEqual(self.linked(), [self.charger.pk, self.glass.pk])
        self.assertEqual(list(AccessoryCompatibility.objects.filter(model=self.model)
                              .order_by('sort_order').values_list('sort_order', flat=True)), [0, 1])
        self.link()
        self.assertEqual(self.linked(), [])

    def test_audit_only_when_something_changed(self):
        self.link(self.case, self.glass)
        logs = AdminAuditLog.objects.filter(action='product_model_accessories_set', company=self.company)
        self.assertEqual(logs.count(), 1)
        entry = logs.get()
        self.assertEqual((entry.target_type, entry.target_id), ('product_model', str(self.model.pk)))
        self.assertEqual(entry.metadata['accessory_ids'], [self.case.pk, self.glass.pk])
        # The same list again: nothing changes, nothing is written.
        self.assertFalse(self.link(self.case, self.glass)['changed'])
        self.assertEqual(logs.count(), 1)
        # Only the order changes: that is a change.
        self.assertTrue(self.link(self.glass, self.case)['changed'])
        self.assertEqual(logs.count(), 2)
        self.assertEqual(logs.order_by('-pk').first().metadata['previous_ids'], [self.case.pk, self.glass.pk])

    def test_duplicates_are_refused(self):
        with self.assertRaises(device_catalog.CatalogError) as caught:
            self.link(self.case, self.glass, self.case)
        self.assertEqual(caught.exception.code, 'duplicate')
        self.assertEqual(self.linked(), [])

    def test_another_companys_or_unknown_product_refuses_the_whole_call(self):
        self.link(self.case)
        other = _p3_company('f3-servicio-b', 'Otra B')
        foreign = _prod(other, 'Funda ajena', 'f3-servicio-ajena')
        for ids in ([self.glass.pk, foreign.pk], [self.glass.pk, 999_999]):
            with self.subTest(ids), self.assertRaises(device_catalog.CatalogError) as caught:
                accessory_compat.set_for_model(self.model, accessory_ids=ids, actor=self.staff)
            self.assertEqual(caught.exception.code, 'invalid_accessory')
        self.assertEqual(self.linked(), [self.case.pk])

    def test_a_device_is_refused_as_an_accessory(self):
        with self.assertRaises(device_catalog.CatalogError) as caught:
            self.link(self.case, self.new256)
        self.assertEqual(caught.exception.code, 'not_an_accessory')
        self.assertEqual(self.linked(), [])

    def test_bad_ids_are_refused(self):
        for ids in ('1', [True], [1.5], ['x'], [0], [-3], None, {'a': 1}):
            with self.subTest(ids), self.assertRaises(device_catalog.CatalogError) as caught:
                accessory_compat.set_for_model(self.model, accessory_ids=ids, actor=self.staff)
            self.assertEqual(caught.exception.field, 'accessory_ids')
        # Ids written as digits are ids.
        accessory_compat.set_for_model(self.model, accessory_ids=[str(self.case.pk)], actor=self.staff)
        self.assertEqual(self.linked(), [self.case.pk])

    def test_non_ascii_digits_are_refused_not_a_crash(self):
        # '²'.isdigit() is True but int('²') raises: an id is ASCII digits or nothing (review of #311).
        for ids in (['²'], ['١٢'], ['٣']):
            with self.subTest(ids), self.assertRaises(device_catalog.CatalogError) as caught:
                accessory_compat.set_for_model(self.model, accessory_ids=ids, actor=self.staff)
            self.assertEqual(caught.exception.field, 'accessory_ids')

    def test_expected_ids_refuse_a_stale_list(self):
        self.link(self.case, self.glass)
        # Somebody else saved in between: what the panel loaded is no longer the list.
        self.link(self.glass)
        with self.assertRaises(device_catalog.CatalogError) as caught:
            accessory_compat.set_for_model(self.model, accessory_ids=[self.charger.pk], actor=self.staff,
                                           expected_ids=[self.case.pk, self.glass.pk])
        self.assertEqual((caught.exception.code, caught.exception.field), ('accessories_changed', 'accessory_ids'))
        self.assertEqual(self.linked(), [self.glass.pk])
        # The order counts too.
        self.link(self.case, self.glass)
        with self.assertRaises(device_catalog.CatalogError):
            accessory_compat.set_for_model(self.model, accessory_ids=[], actor=self.staff,
                                           expected_ids=[self.glass.pk, self.case.pk])
        # The list it loaded is the list: it saves.
        accessory_compat.set_for_model(self.model, accessory_ids=[self.charger.pk], actor=self.staff,
                                       expected_ids=[self.case.pk, self.glass.pk])
        self.assertEqual(self.linked(), [self.charger.pk])
        # A bad expected list is a bad body.
        with self.assertRaises(device_catalog.CatalogError) as caught:
            accessory_compat.set_for_model(self.model, accessory_ids=[], actor=self.staff, expected_ids='x')
        self.assertEqual(caught.exception.field, 'expected_ids')

    def test_too_many_are_refused(self):
        with self.assertRaises(device_catalog.CatalogError):
            accessory_compat.set_for_model(
                self.model, accessory_ids=list(range(1, accessory_compat.MAX_ACCESSORIES + 2)), actor=self.staff)

    def test_two_models_keep_their_own_lists(self):
        second = device_catalog.create_model(company=self.company, name='Teléfono Y')
        self.link(self.case)
        self.link(self.glass, self.case, model=second)
        self.assertEqual(self.linked(), [self.case.pk])
        self.assertEqual(self.linked(second), [self.glass.pk, self.case.pk])

    def test_the_service_is_atomic_and_locks_the_model(self):
        import inspect

        source = inspect.getsource(accessory_compat)
        self.assertIn('@transaction.atomic\ndef set_for_model(', source)
        self.assertIn('select_for_update()', inspect.getsource(accessory_compat.set_for_model))


# ---------------------------------------------------------------------------
# The admin API
# ---------------------------------------------------------------------------

class AdminAccessoriesApiTest(AccessoryShop, TestCase):
    def setUp(self):
        self.build('f3-admin')

    def test_get_is_empty_without_links_and_lists_them_in_order(self):
        res = self.api.get(self.url)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json(), {'model_id': self.model.pk, 'accessories': []})
        self.link(self.glass, self.case)
        Product.objects.filter(pk=self.glass.pk).update(is_published_online=False)
        rows = self.api.get(self.url).json()['accessories']
        self.assertEqual([r['id'] for r in rows], [self.glass.pk, self.case.pk])
        self.assertEqual(rows[0]['name'], 'Lámina de vidrio')
        self.assertFalse(rows[0]['shown_online'])
        self.assertTrue(rows[1]['shown_online'])
        self.assertEqual(rows[1]['price'], '59.90')
        assert_no_amount(self, rows, '17.31')

    def test_put_replaces_the_list(self):
        res = self.api.put(self.url, {'accessory_ids': [self.charger.pk, self.case.pk]}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual([r['id'] for r in body['accessories']], [self.charger.pk, self.case.pk])
        self.assertTrue(body['changed'])
        self.assertEqual(self.linked(), [self.charger.pk, self.case.pk])
        res = self.api.put(self.url, {'accessory_ids': [self.charger.pk, self.case.pk]}, format='json')
        self.assertFalse(res.json()['changed'])
        self.assertEqual(AdminAuditLog.objects.filter(action='product_model_accessories_set').count(), 1)

    def test_bad_bodies_are_400(self):
        for body in ({}, {'accessory_ids': 'x'}, {'accessory_ids': [True]}, {'accessory_ids': [0]},
                     {'accessory_ids': [self.case.pk, self.case.pk]}, {'accessory_ids': [self.new128.pk]},
                     {'accessory_ids': [999_999]}):
            with self.subTest(body):
                res = self.api.put(self.url, body, format='json')
                self.assertEqual(res.status_code, 400, res.content)
                self.assertEqual(res.json()['field'], 'accessory_ids')
        self.assertEqual(self.api.put(self.url, ['x'], format='json').status_code, 400)
        self.assertEqual(self.linked(), [])

    def test_another_companys_accessory_is_400_and_its_model_404(self):
        other = _p3_company('f3-admin-b', 'Otra B')
        foreign = _prod(other, 'Funda ajena', 'f3-admin-ajena')
        res = self.api.put(self.url, {'accessory_ids': [self.case.pk, foreign.pk]}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (400, 'invalid_accessory'))
        self.assertEqual(self.linked(), [])
        foreign_model = device_catalog.create_model(company=other, name='Equipo ajeno')
        url = f'/api/admin/catalog/models/{foreign_model.pk}/accessories/{self.q}'
        self.assertEqual(self.api.get(url).status_code, 404)
        self.assertEqual(self.api.put(url, {'accessory_ids': []}, format='json').status_code, 404)
        # Nor through the other company's own id in the query: the caller is not a member there.
        url = f'/api/admin/catalog/models/{foreign_model.pk}/accessories/?company={other.pk}'
        self.assertIn(self.api.get(url).status_code, (403, 404))

    def test_non_ascii_digits_are_400(self):
        res = self.api.put(self.url, {'accessory_ids': ['²']}, format='json')
        self.assertEqual((res.status_code, res.json()['field']), (400, 'accessory_ids'))

    def test_a_stale_expected_list_is_409_and_without_it_the_put_still_works(self):
        self.link(self.case)
        res = self.api.put(self.url, {'accessory_ids': [self.glass.pk], 'expected_ids': []}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'accessories_changed'), res.content)
        self.assertEqual(self.linked(), [self.case.pk])
        res = self.api.put(self.url, {'accessory_ids': [self.glass.pk], 'expected_ids': [self.case.pk]}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(self.linked(), [self.glass.pk])
        res = self.api.put(self.url, {'accessory_ids': [self.charger.pk]}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(self.linked(), [self.charger.pk])
        res = self.api.put(self.url, {'accessory_ids': [], 'expected_ids': 'x'}, format='json')
        self.assertEqual((res.status_code, res.json()['field']), (400, 'expected_ids'))

    def test_permissions(self):
        viewer, _ = _p2d_member(self.company, 'f3_viewer', ['company.view', 'products.view'])
        self.assertEqual(_client(viewer).get(self.url).status_code, 200)
        res = _client(viewer).put(self.url, {'accessory_ids': [self.case.pk]}, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.linked(), [])
        nobody, _ = _p2d_member(self.company, 'f3_nobody', ['company.view'])
        self.assertEqual(_client(nobody).get(self.url).status_code, 403)
        self.assertIn(_client().get(self.url).status_code, (401, 403))

    def test_candidates_are_the_companys_accessories_only(self):
        other = _p3_company('f3-admin-c', 'Otra C')
        _prod(other, 'Funda ajena', 'f3-admin-c-funda')
        url = f'/api/admin/catalog/accessories/{self.q}'
        res = self.api.get(url)
        self.assertEqual(res.status_code, 200, res.content)
        names = [r['name'] for r in res.json()['results']]
        self.assertIn('Funda clara', names)
        self.assertNotIn('Funda ajena', names)
        self.assertNotIn(self.new128.name, names)
        found = self.api.get(f'{url}&search=funda').json()['results']
        self.assertEqual([r['name'] for r in found], ['Funda clara'])
        assert_no_amount(self, found, '17.31')
        viewer, _ = _p2d_member(self.company, 'f3_cand_viewer', ['company.view', 'products.view'])
        self.assertEqual(_client(viewer).get(url).status_code, 200)
        nobody, _ = _p2d_member(self.company, 'f3_cand_nobody', ['company.view'])
        self.assertEqual(_client(nobody).get(url).status_code, 403)


# ---------------------------------------------------------------------------
# The public storefront
# ---------------------------------------------------------------------------

@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class PublicAccessoriesTest(AccessoryShop, TestCase):
    def setUp(self):
        self.build('f3-publico')
        self.public = _client(host=self.host)

    def detail(self):
        res = self.public.get(f'/api/catalog/models/{self.model.slug}/')
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def test_no_links_is_an_empty_list(self):
        self.assertEqual(self.detail()['accessories'], [])

    def test_active_and_published_accessories_in_order_with_price_and_availability(self):
        self.link(self.charger, self.case, self.glass)
        rows = self.detail()['accessories']
        self.assertEqual([r['name'] for r in rows], ['Cargador 20 W', 'Funda clara', 'Lámina de vidrio'])
        case = rows[1]
        self.assertEqual((case['id'], case['slug'], case['price']), (self.case.pk, self.case.slug, '59.90'))
        self.assertEqual((case['available'], case['availability']), (True, 'own_stock'))
        glass = rows[2]
        self.assertEqual((glass['available'], glass['availability'], glass['on_order']), (False, 'none', None))
        assert_no_amount(self, rows, '17.31')
        self.assertNotIn('reference_cost', str(rows))

    def test_inactive_unpublished_or_device_accessories_are_hidden(self):
        self.link(self.charger, self.case, self.glass)
        Product.objects.filter(pk=self.charger.pk).update(is_active=False)
        Product.objects.filter(pk=self.glass.pk).update(is_published_online=False)
        self.assertEqual([r['name'] for r in self.detail()['accessories']], ['Funda clara'])
        # An accessory turned into a variant later is no longer an accessory on the page.
        Product.objects.filter(pk=self.case.pk).update(product_model=self.model)
        self.assertEqual(self.detail()['accessories'], [])

    def test_the_query_count_does_not_grow_with_the_list(self):
        self.link(self.case)
        branch = self.branch
        with self.assertNumQueries(2):
            rows = accessory_compat.for_model_public(self.model, branch)
        self.assertEqual(len(rows), 1)
        with CaptureQueriesContext(connection) as one:
            self.detail()
        self.link(self.case, self.glass, self.charger)
        with self.assertNumQueries(2):
            rows = accessory_compat.for_model_public(self.model, branch)
        self.assertEqual(len(rows), 3)
        with CaptureQueriesContext(connection) as three:
            self.detail()
        self.assertEqual(len(three.captured_queries), len(one.captured_queries))
        # Without a branch, the stock is zero and nothing more is read.
        with self.assertNumQueries(2):
            rows = accessory_compat.for_model_public(self.model, None)
        self.assertEqual({r['availability'] for r in rows}, {'none'})

    def test_stock_is_the_fulfilment_branchs(self):
        self.link(self.glass)
        BranchStock.objects.update_or_create(branch=self.branch, product=self.glass, defaults={'quantity': 3})
        self.assertEqual(self.detail()['accessories'][0]['availability'], 'own_stock')

    def test_another_storefront_does_not_see_them(self):
        self.link(self.case)
        _p3_company('f3-vecina', 'Vecina')
        res = _client(host='f3-vecina.tienda.pe').get(f'/api/catalog/models/{self.model.slug}/')
        self.assertEqual(res.status_code, 404)


@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class OnOrderAccessoryTest(SupplyShop, TestCase):
    """An accessory sold on order carries `on_order` exactly as the public product does (review of #311)."""

    def setUp(self):
        self.build('f3-pedido')
        self.model = device_catalog.create_model(company=self.company, name='Equipo W')
        accessory_compat.set_for_model(self.model, accessory_ids=[self.case.pk], actor=self.staff)
        self.public = _client(host=self.host)

    def accessory(self):
        res = self.public.get(f'/api/catalog/models/{self.model.slug}/')
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()['accessories'][0]

    def listed(self):
        body = self.public.get(f'/api/products/?slug={self.case.slug}').json()
        return (body['results'] if isinstance(body, dict) else body)[0]

    def test_with_an_empty_shelf_it_is_sold_on_order(self):
        row = self.accessory()
        self.assertEqual((row['availability'], row['on_order']), ('supplier', {'lead_time_days': 4, 'price': '110.00'}))
        self.assertEqual(row['on_order'], self.listed()['on_order'])
        assert_no_amount(self, row, '70')

    def test_with_stock_and_over_stock_allowed_it_still_carries_the_offer(self):
        self.stock(self.case, 3)
        CompanySettings.objects.filter(company=self.company).update(web_on_order_over_stock=True)
        row = self.accessory()
        self.assertEqual(row['availability'], 'own_stock')
        self.assertEqual(row['on_order'], {'lead_time_days': 4, 'price': '110.00', 'over_stock': True})
        self.assertEqual(row['on_order'], self.listed()['on_order'])
