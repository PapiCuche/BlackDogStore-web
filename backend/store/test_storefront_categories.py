"""
STOREFRONT-CATEGORIES — las categorías de la portada son de cada tienda.

La portada mostraba "todas las categorías, por nombre". Con dos, salían dos; y
no había forma de decir cuáles van primero, cuál todavía no se enseña o cuál se
retiró sin borrarla. Nada de eso se decide en el código del frontend: cada
empresa lo configura, y la API pública devuelve ya lo que su tienda debe ver.

  * `is_active`      la categoría existe para el público o no;
  * `show_on_home`   aparece en la portada (sigue en el menú y en el catálogo);
  * `home_order`     en qué orden.
"""
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from store.models import AdminAuditLog, Category
from store.tests import _p2d_member, _prod, _saas_company


class CategoriesBase(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _saas_company('Tienda', 'cat-home-a', tax_id='20930000001')
        self.other = _saas_company('Otra', 'cat-home-b', tax_id='20930000002')
        self.phones = Category.objects.create(company=self.company, name='Teléfonos', slug='telefonos')
        self.laptops = Category.objects.create(company=self.company, name='Laptops', slug='laptops')
        self.cases = Category.objects.create(company=self.company, name='Accesorios', slug='accesorios')
        self.foreign = Category.objects.create(company=self.other, name='Ajena', slug='ajena')
        self.product = _prod(self.company, 'Funda', 'funda-cat-home', category=self.cases)
        self.manager, _ = _p2d_member(self.company, 'cat_home_admin', ['products.view', 'products.manage'])
        self.viewer, _ = _p2d_member(self.company, 'cat_home_viewer', ['products.view'])
        self.public = APIClient()

    def as_user(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def store(self):
        return override_settings(DEFAULT_STOREFRONT_COMPANY_SLUG=self.company.slug)


class PublicCategoriesTest(CategoriesBase):
    def listed(self):
        with self.store():
            return self.public.get('/api/categories/').json()

    def test_the_shop_decides_the_order_and_ties_go_by_name(self):
        Category.objects.filter(pk=self.phones.pk).update(home_order=1)
        Category.objects.filter(pk=self.cases.pk).update(home_order=2)
        Category.objects.filter(pk=self.laptops.pk).update(home_order=2)

        self.assertEqual([row['slug'] for row in self.listed()], ['telefonos', 'accesorios', 'laptops'])

    def test_a_category_can_stay_off_the_home_and_still_exist(self):
        Category.objects.filter(pk=self.laptops.pk).update(show_on_home=False)

        rows = {row['slug']: row for row in self.listed()}

        self.assertEqual(set(rows), {'telefonos', 'laptops', 'accesorios'})
        self.assertFalse(rows['laptops']['show_on_home'])
        self.assertTrue(rows['telefonos']['show_on_home'])

    def test_a_retired_category_is_not_offered_but_its_products_remain(self):
        Category.objects.filter(pk=self.cases.pk).update(is_active=False)

        self.assertNotIn('accesorios', [row['slug'] for row in self.listed()])
        with self.store():
            body = self.public.get('/api/products/').json()
        names = [p['name'] for p in (body['results'] if isinstance(body, dict) else body)]
        self.assertIn('Funda', names)

    def test_nothing_of_another_shop_and_nothing_internal_travels(self):
        rows = self.listed()
        self.assertNotIn('ajena', [row['slug'] for row in rows])
        self.assertEqual(sorted(rows[0]), ['id', 'image_url', 'name', 'show_on_home', 'slug'])

    def test_the_mobile_catalogue_follows_the_same_rule_without_changing_its_contract(self):
        Category.objects.filter(pk=self.cases.pk).update(is_active=False)
        Category.objects.filter(pk=self.phones.pk).update(home_order=1)
        Category.objects.filter(pk=self.laptops.pk).update(home_order=2)

        res = self.public.get(f'/api/v1/storefront/{self.company.slug}/categories/')

        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        rows = body['results'] if isinstance(body, dict) else body
        self.assertEqual([row['slug'] for row in rows], ['telefonos', 'laptops'])


class AdminCategoriesTest(CategoriesBase):
    LIST = '/api/admin/categories/'

    def url(self, category):
        return f'{self.LIST}{category.pk}/'

    def test_the_panel_lists_every_category_with_how_it_is_shown(self):
        Category.objects.filter(pk=self.cases.pk).update(is_active=False, home_order=3)

        rows = {row['slug']: row for row in self.as_user(self.viewer).get(self.LIST).json()}

        self.assertEqual(set(rows), {'telefonos', 'laptops', 'accesorios'})
        self.assertFalse(rows['accesorios']['is_active'])
        self.assertEqual(rows['accesorios']['home_order'], 3)
        self.assertEqual(rows['accesorios']['product_count'], 1)
        self.assertEqual(rows['telefonos']['product_count'], 0)
        self.assertTrue(rows['telefonos']['show_on_home'])

    def test_a_manager_configures_how_a_category_is_shown(self):
        res = self.as_user(self.manager).patch(
            self.url(self.laptops), {'show_on_home': False, 'home_order': 4, 'is_active': True},
            format='json')

        self.assertEqual(res.status_code, 200, res.content)
        self.laptops.refresh_from_db()
        self.assertFalse(self.laptops.show_on_home)
        self.assertEqual(self.laptops.home_order, 4)
        self.assertEqual(res.json()['home_order'], 4)
        row = AdminAuditLog.objects.filter(action='category_updated').get()
        self.assertEqual(row.company, self.company)
        self.assertEqual(
            set(row.metadata['changed_fields']), {'show_on_home', 'home_order'})

    def test_an_order_that_is_not_a_small_number_is_refused(self):
        client = self.as_user(self.manager)
        for bad in (-1, 100000, 'primero'):
            with self.subTest(bad):
                self.assertEqual(
                    client.patch(self.url(self.laptops), {'home_order': bad}, format='json').status_code, 400)

    def test_a_new_category_is_active_and_goes_last(self):
        Category.objects.filter(pk=self.phones.pk).update(home_order=7)

        res = self.as_user(self.manager).post(self.LIST, {'name': 'Tablets'}, format='json')

        self.assertEqual(res.status_code, 201, res.content)
        created = Category.objects.get(company=self.company, slug='tablets')
        self.assertTrue(created.is_active)
        self.assertTrue(created.show_on_home)
        self.assertEqual(created.home_order, 8)

    def test_a_viewer_and_another_company_change_nothing(self):
        self.assertEqual(self.as_user(self.viewer).patch(
            self.url(self.laptops), {'is_active': False}, format='json').status_code, 403)
        self.assertEqual(self.as_user(self.manager).patch(
            self.url(self.foreign), {'is_active': False}, format='json').status_code, 404)
        self.foreign.refresh_from_db()
        self.assertTrue(self.foreign.is_active)
