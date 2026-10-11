"""
PRODUCT-DESTINATION-01 · «sólo stock interno» no es «inactivo».

Un producto puede estar en uso —se vende en caja, se cuenta en inventario, se
usa en el taller— y no venderse por la web. Eso lo dice
`Product.is_published_online`, que es otro dato que `is_active`.

    LO QUE NO SE PUBLICA NO EXISTE PARA QUIEN MIRA LA TIENDA.

Ni en el catálogo, ni en la búsqueda, ni por su dirección, ni en un carrito, ni
en una compra, ni en una campaña, ni para dejarle una reseña. Y lo contrario:
dentro de la empresa sigue siendo un producto como cualquier otro, para quien
tenga permiso de verlo.

La carga masiva de este dato está en `test_product_destination_import.py`.
"""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from store import tenancy
from store.models import AdminAuditLog, BranchStock, CartItem, Category, Product, ProductBarcode, Review
from store.tests import (
    FAKE_SESSION_TOKEN, IZIPAY_TEST_SETTINGS, SESSION_TOKEN_PATH,
    _make_product, _p2d_member, _p3_company, _pilot_company, _seeded,
)

User = get_user_model()

CHECKOUT = {
    'customer_name': 'Ana Torres', 'customer_email': 'ana@correo.test', 'customer_phone': '936449536',
    'document_type': 'dni', 'document_number': '12345678', 'delivery_method': 'pickup_store',
    'receipt_type': 'boleta', 'accepted_terms': True, 'accepted_warranty_policy': True,
}


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _pilot_company()
        self.category = Category.objects.create(company=self.company, name='Repuestos Vis', slug='repuestos-vis')
        self.online = _make_product('Funda Vis', 'funda-vis', price='59.00', category=self.category)
        self.internal = _make_product('Flex de carga Vis', 'flex-de-carga-vis', price='35.00', category=self.category)
        Product.objects.filter(pk=self.internal.pk).update(is_published_online=False)
        self.internal.refresh_from_db()
        self.client = APIClient()

    def member(self, name, *capabilities):
        user, _ = _p2d_member(self.company, name, ['company.view', *capabilities])
        client = APIClient()
        client.force_authenticate(user=user)
        return client


class TheFieldTest(_Base):
    def test_a_product_is_published_unless_somebody_says_otherwise(self):
        fresh = Product.objects.create(company=self.company, name='Nuevo Vis', slug='nuevo-vis', price=Decimal('10.00'))
        self.assertTrue(fresh.is_published_online)

    def test_it_is_another_fact_than_being_active(self):
        self.assertTrue(self.internal.is_active)
        self.assertFalse(self.internal.is_published_online)
        self.assertFalse(self.internal.is_sold_online)
        self.assertTrue(self.online.is_sold_online)
        Product.objects.filter(pk=self.online.pk).update(is_active=False)
        self.online.refresh_from_db()
        self.assertTrue(self.online.is_published_online)                 # inactive, and still «for the web» when it returns
        self.assertFalse(self.online.is_sold_online)


class PublicCatalogueTest(_Base):
    def slugs(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        return [row['slug'] for row in (body['results'] if isinstance(body, dict) else body)]

    def test_it_is_not_in_the_catalogue_nor_in_its_category_nor_in_a_search(self):
        for url in ('/api/products/', f'/api/products/?category={self.category.slug}', '/api/products/?search=Vis',
                    '/api/products/?search=Flex', '/api/products/?ordering=newest', '/api/products/?in_stock=true'):
            with self.subTest(url):
                found = self.slugs(url)
                self.assertNotIn('flex-de-carga-vis', found)
                if 'Flex' not in url:
                    self.assertIn('funda-vis', found)

    def test_it_has_no_address_by_id_nor_by_slug(self):
        self.assertEqual(self.client.get(f'/api/products/{self.internal.pk}/').status_code, 404)
        self.assertEqual(self.slugs('/api/products/?slug=flex-de-carga-vis'), [])
        self.assertEqual(self.client.get(f'/api/products/{self.online.pk}/').status_code, 200)

    def test_the_same_through_the_api_the_app_uses(self):
        base = f'/api/v1/storefront/{self.company.slug}/products/'
        found = self.slugs(base)
        self.assertIn('funda-vis', found)
        self.assertNotIn('flex-de-carga-vis', found)
        self.assertEqual(self.slugs(f'{base}?search=Flex'), [])
        self.assertEqual(self.client.get(f'{base}flex-de-carga-vis/').status_code, 404)
        self.assertEqual(self.client.get(f'{base}funda-vis/').status_code, 200)

    def test_the_one_definition_of_what_the_shop_sells_leaves_it_out(self):
        ids = set(tenancy.company_storefront_products(self.company).values_list('pk', flat=True))
        self.assertIn(self.online.pk, ids)
        self.assertNotIn(self.internal.pk, ids)

    def test_the_public_product_does_not_say_where_it_goes(self):
        body = self.client.get(f'/api/products/{self.online.pk}/').json()
        self.assertNotIn('is_published_online', body)
        self.assertNotIn('is_active', body)

    def test_a_campaign_does_not_advertise_it(self):
        from store.models import StorefrontCampaign
        from store.storefront_content_services import _public_payload

        for product, shown in ((self.online, True), (self.internal, False)):
            campaign = StorefrontCampaign(company=self.company, product=product)
            self.assertEqual(_public_payload(campaign)['product'] is not None, shown, product.name)

    def test_nobody_reads_or_leaves_a_review_for_it(self):
        buyer = User.objects.create_user('compradora_vis', 'compradora@correo.test', 'Pass123!')
        Review.objects.create(product=self.internal, user=buyer, rating=5, comment='De antes')
        self.assertEqual(self.client.get(f'/api/reviews/?product={self.internal.pk}').json(), [])

        client = APIClient()
        client.force_authenticate(user=buyer)
        response = client.post('/api/reviews/', {'product': self.internal.pk, 'rating': 4, 'comment': 'Hola'}, format='json')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(Review.objects.filter(product=self.internal).count(), 1)


class OnlinePurchaseTest(_Base):
    def test_it_cannot_be_put_in_a_cart(self):
        response = self.client.post('/api/cart/add/', {'session_key': 'vis_sess', 'product': self.internal.pk, 'quantity': 1}, format='json')
        self.assertEqual(response.status_code, 404, response.content)
        self.assertFalse(CartItem.objects.filter(product=self.internal).exists())
        ok = self.client.post('/api/cart/add/', {'session_key': 'vis_sess', 'product': self.online.pk, 'quantity': 1}, format='json')
        self.assertIn(ok.status_code, (200, 201), ok.content)

    def test_one_that_was_in_a_cart_when_it_left_the_web_is_not_quoted(self):
        CartItem.objects.create(session_key='vis_quote', product=self.internal, quantity=1)
        response = self.client.post('/api/checkout/quote/', {'session_key': 'vis_quote', 'delivery_method': 'pickup_store'}, format='json')
        self.assertEqual(response.status_code, 400, response.content)

        # The same request with something the web does sell is quoted.
        CartItem.objects.create(session_key='vis_quote_ok', product=self.online, quantity=1)
        fine = self.client.post('/api/checkout/quote/', {'session_key': 'vis_quote_ok', 'delivery_method': 'pickup_store'}, format='json')
        self.assertEqual(fine.status_code, 200, fine.content)

    def test_nor_paid_for(self):
        CartItem.objects.create(session_key='vis_pay', product=self.internal, quantity=1)
        with patch(SESSION_TOKEN_PATH, return_value=FAKE_SESSION_TOKEN), override_settings(**IZIPAY_TEST_SETTINGS):
            response = self.client.post('/api/payments/create-checkout-session/', {'session_key': 'vis_pay', **CHECKOUT}, format='json')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('ya no está disponible', response.content.decode())
        self.assertFalse(self.company.orders.filter(items__product=self.internal).exists())

    def test_the_cart_that_holds_it_still_opens(self):
        """Como con un producto desactivado: el carrito carga y la compra es lo que se niega."""
        CartItem.objects.create(session_key='vis_open', product=self.internal, quantity=1)
        self.assertEqual(self.client.get('/api/cart/?session_key=vis_open').status_code, 200)

    def test_the_line_check_every_checkout_shares_refuses_it(self):
        from store import checkout_services

        branch = tenancy.company_fulfillment_branch(self.company)
        BranchStock.objects.update_or_create(branch=branch, product=self.internal, defaults={'quantity': 5})
        # A line that got in some other way — a cart from before — is refused here.
        with self.assertRaises(checkout_services.CheckoutError) as refused:
            checkout_services.validate_lines_and_subtotal(
                branch, [checkout_services.CheckoutLine(product=self.internal, quantity=1)])
        self.assertIn('ya no está disponible', str(refused.exception.errors))

    def test_asking_for_it_by_name_says_no_more_than_asking_for_nothing(self):
        """
        The app's checkout takes slugs. One of an internal product must answer
        exactly like one that does not exist: not «ya no está disponible», and
        never its name — that would tell anybody who guesses a slug what the
        shop keeps in the back.
        """
        from store import checkout_services

        answers = {}
        for slug in ('flex-de-carga-vis', 'no-existe-vis'):
            with self.assertRaises(checkout_services.CheckoutError) as refused:
                checkout_services.resolve_lines_from_intents(self.company, [{'product_slug': slug, 'quantity': 1}])
            answers[slug] = str(getattr(refused.exception, 'errors', '') or refused.exception)
        self.assertNotIn('Flex de carga Vis', answers['flex-de-carga-vis'])
        self.assertEqual(answers['flex-de-carga-vis'].replace('flex-de-carga-vis', 'X'), answers['no-existe-vis'].replace('no-existe-vis', 'X'))
        # What the web does sell still resolves.
        lines = checkout_services.resolve_lines_from_intents(self.company, [{'product_slug': 'funda-vis', 'quantity': 1}])
        self.assertEqual([line.product.pk for line in lines], [self.online.pk])


class InsideTheCompanyTest(_Base):
    """Sigue siendo un producto como cualquier otro, para quien puede verlo."""

    def test_the_panel_lists_it_and_says_where_it_goes(self):
        client = self.member('vis_gestor', 'products.view')
        rows = {row['slug']: row for row in client.get(f'/api/admin/products/?company={self.company.pk}').json()['results']}
        self.assertFalse(rows['flex-de-carga-vis']['is_published_online'])
        self.assertTrue(rows['funda-vis']['is_published_online'])

        detail = client.get(f'/api/admin/products/{self.internal.pk}/?company={self.company.pk}').json()
        self.assertFalse(detail['is_published_online'])

    def test_the_panel_can_list_one_kind_or_the_other(self):
        client = self.member('vis_gestor', 'products.view')
        base = f'/api/admin/products/?company={self.company.pk}&search=Vis'
        only = lambda value: {row['slug'] for row in client.get(f'{base}&is_published_online={value}').json()['results']}
        self.assertEqual(only('false'), {'flex-de-carga-vis'})
        self.assertEqual(only('true'), {'funda-vis'})
        self.assertEqual(only('otra-cosa'), {'flex-de-carga-vis', 'funda-vis'})

    def test_the_till_finds_it_and_reads_its_label(self):
        ProductBarcode.objects.create(company=self.company, product=self.internal, code='7750000000017', is_primary=True)
        client = self.member('vis_caja', 'sales.pos.use')
        branch = tenancy.company_fulfillment_branch(self.company)

        found = client.get(f'/api/admin/pos/products/search/?company={self.company.pk}&branch={branch.pk}&q=Flex')
        self.assertEqual(found.status_code, 200, found.content)
        self.assertIn('Flex de carga Vis', [row['name'] for row in found.json()['results']])

        scanned = client.get(f'/api/admin/pos/products/lookup/?company={self.company.pk}&branch={branch.pk}&code=7750000000017')
        self.assertEqual(scanned.status_code, 200, scanned.content)
        self.assertEqual(scanned.json()['name'], 'Flex de carga Vis')

    def test_the_till_can_sell_it(self):
        from store import pos_services

        products = pos_services.resolve_pos_products(self.company, [{'product': self.internal.pk, 'quantity': 1}])
        self.assertEqual(set(products), {self.internal.pk})

    def test_inventory_counts_it(self):
        client = self.member('vis_almacen', 'inventory.view')
        branch = tenancy.company_fulfillment_branch(self.company)
        BranchStock.objects.update_or_create(branch=branch, product=self.internal, defaults={'quantity': 7})
        response = client.get(f'/api/admin/inventory/stock/?company={self.company.pk}&branch={branch.pk}&search=Flex')
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        rows = body['results'] if isinstance(body, dict) else body
        self.assertIn('Flex de carga Vis', [row.get('product_name') or row.get('name') or row.get('product', {}).get('name') for row in rows])


class WhoDecidesTest(_Base):
    def test_whoever_manages_products_sends_it_to_the_web_or_takes_it_off(self):
        client = self.member('vis_gestor', 'products.view', 'products.manage')
        url = f'/api/admin/products/{self.online.pk}/?company={self.company.pk}'

        response = client.patch(url, {'is_published_online': False}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.json()['is_published_online'])
        self.online.refresh_from_db()
        self.assertFalse(self.online.is_published_online)
        self.assertTrue(self.online.is_active)                              # it did not also switch it off
        self.assertEqual(self.client.get(f'/api/products/{self.online.pk}/').status_code, 404)

        client.patch(url, {'is_published_online': True}, format='json')
        self.assertEqual(self.client.get(f'/api/products/{self.online.pk}/').status_code, 200)

    def test_the_change_is_written_in_the_audit_log(self):
        client = self.member('vis_gestor', 'products.view', 'products.manage')
        client.patch(f'/api/admin/products/{self.online.pk}/?company={self.company.pk}', {'is_published_online': False}, format='json')
        entry = AdminAuditLog.objects.filter(target_type='product', target_id=str(self.online.pk)).order_by('-pk').first()
        self.assertIsNotNone(entry)
        self.assertIn('is_published_online', str(entry.metadata))

    def test_a_new_product_can_be_born_for_internal_stock_only(self):
        client = self.member('vis_gestor', 'products.view', 'products.manage')
        response = client.post(f'/api/admin/products/?company={self.company.pk}', {
            'name': 'Pegamento B-7000 Vis', 'price': '12.00', 'inventory': 0, 'is_published_online': False}, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertFalse(Product.objects.get(name='Pegamento B-7000 Vis').is_published_online)

        default = client.post(f'/api/admin/products/?company={self.company.pk}', {
            'name': 'Cable Vis', 'price': '12.00', 'inventory': 0}, format='json')
        self.assertEqual(default.status_code, 201, default.content)
        self.assertTrue(Product.objects.get(name='Cable Vis').is_published_online)

    def test_editing_something_else_does_not_move_it(self):
        client = self.member('vis_gestor', 'products.view', 'products.manage')
        client.patch(f'/api/admin/products/{self.internal.pk}/?company={self.company.pk}', {'price': '40.00'}, format='json')
        self.internal.refresh_from_db()
        self.assertEqual(self.internal.price, Decimal('40.00'))
        self.assertFalse(self.internal.is_published_online)

    def test_who_only_looks_cannot_change_it(self):
        client = self.member('vis_lector', 'products.view')
        response = client.patch(f'/api/admin/products/{self.internal.pk}/?company={self.company.pk}', {'is_published_online': True}, format='json')
        self.assertEqual(response.status_code, 403)
        self.internal.refresh_from_db()
        self.assertFalse(self.internal.is_published_online)

    def test_nobody_outside_the_company_changes_or_sees_it(self):
        other = _p3_company('vis-otra', 'Otra Vis')
        outsider, _ = _p2d_member(other, 'vis_ajeno', ['company.view', 'products.view', 'products.manage'])
        client = APIClient()
        client.force_authenticate(user=outsider)

        for company in (other, self.company):
            url = f'/api/admin/products/{self.internal.pk}/?company={company.pk}'
            self.assertIn(client.get(url).status_code, (403, 404))
            self.assertIn(client.patch(url, {'is_published_online': True}, format='json').status_code, (403, 404))
        self.internal.refresh_from_db()
        self.assertFalse(self.internal.is_published_online)

    def test_a_form_that_does_not_mention_it_creates_a_published_product(self):
        """An HTML form leaves out what it does not have; that is not «no»."""
        client = self.member('vis_gestor', 'products.view', 'products.manage')
        response = client.post(f'/api/admin/products/?company={self.company.pk}',
                               {'name': 'Mica Vis', 'price': '9.00', 'inventory': '0'}, format='multipart')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(Product.objects.get(name='Mica Vis').is_published_online)

    def test_a_product_born_for_internal_stock_says_so_in_the_audit_log(self):
        client = self.member('vis_gestor', 'products.view', 'products.manage')
        created = client.post(f'/api/admin/products/?company={self.company.pk}', {
            'name': 'Tornillos Vis', 'price': '3.00', 'inventory': 0, 'is_published_online': False}, format='json').json()
        entry = AdminAuditLog.objects.filter(action='product_created', target_id=str(created['id'])).get()
        self.assertEqual(entry.metadata['is_published_online'], False)


class TwoCompaniesTest(TestCase):
    def test_each_company_hides_its_own_and_only_its_own(self):
        cache.clear()
        a, b = _p3_company('vis-a', 'Tienda A'), _p3_company('vis-b', 'Tienda B')
        for company, published in ((a, False), (b, True)):
            _seeded(Product.objects.create(company=company, name='Batería', slug='bateria', price=Decimal('90.00'),
                                           is_published_online=published))
        client = APIClient()

        in_a = client.get('/api/v1/storefront/vis-a/products/').json()
        in_b = client.get('/api/v1/storefront/vis-b/products/').json()
        slugs = lambda body: [row['slug'] for row in (body['results'] if isinstance(body, dict) else body)]
        self.assertEqual(slugs(in_a), [])
        self.assertEqual(slugs(in_b), ['bateria'])
        self.assertEqual(client.get('/api/v1/storefront/vis-a/products/bateria/').status_code, 404)
        self.assertEqual(client.get('/api/v1/storefront/vis-b/products/bateria/').status_code, 200)
