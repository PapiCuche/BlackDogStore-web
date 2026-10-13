"""
INVENTORY-UNIFIED-01 · parte 1 — publicar y ocultar productos en bloque.

    UN SOLO DATO CAMBIA: `Product.is_published_online`.

Ni `is_active`, ni el precio, ni una cantidad, ni un equipo, ni una línea del
Kardex. Ocultar siempre se puede; publicar en bloque exige que el producto
tenga con qué mostrarse (`store.product_publication`), y lo que no lo tiene se
devuelve con el motivo en vez de publicarse a medias en silencio.

Lo que estas pruebas fijan:

  * la regla de «puede publicarse», una sola;
  * la acción por ids y por «todo lo que coincide con el filtro», con el mismo
    filtro que usa la lista y con el conteo que el operador vio;
  * empresa, permisos, topes, concurrencia y auditoría.
"""
import threading
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection as db_connection
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from store import admin_views, product_publication, stock_unit_services
from store.models import (
    AdminAuditLog, BranchStock, Category, Product, ProductImage, StockMovement, StockUnit,
)
from store.tests import _p2d_branch, _p2d_member, _p2d_stock, _p3_company, _saas_company

User = get_user_model()

URL = '/api/admin/products/bulk-visibility/'
LIST_URL = '/api/admin/products/'
PICTURE = 'https://imagenes.example.test/producto.png'


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('bulk-vis-a', 'Tienda A')
        self.other = _p3_company('bulk-vis-b', 'Tienda B')
        self.category = Category.objects.create(company=self.company, name='Accesorios', slug='accesorios')
        self.manager, _ = _p2d_member(
            self.company, 'bv_gestora', ['company.view', 'products.view', 'products.manage'])
        self.api = _client(self.manager)
        self.serial = 0

    def product(self, name, *, company=None, published=True, **over):
        """A product that MAY be published: active, priced, pictured and filed."""
        company = company or self.company
        self.serial += 1
        fields = {
            'price': Decimal('59.00'), 'image_url': PICTURE,
            'category': self.category if company == self.company else None,
            'is_published_online': published,
        }
        fields.update(over)
        return Product.objects.create(company=company, name=name, slug=f'bv-{self.serial}', **fields)

    def published(self, *products):
        return [Product.objects.get(pk=p.pk).is_published_online for p in products]

    def post(self, body, client=None):
        return (client or self.api).post(URL, body, format='json')

    def audit(self):
        return AdminAuditLog.objects.filter(action='products_visibility_changed')


# =============================================================================
# The rule
# =============================================================================

class MayBePublishedTest(_Base):
    def test_a_product_with_everything_has_no_problem(self):
        self.assertEqual(product_publication.publication_problems(self.product('Funda')), [])

    def test_each_thing_missing_is_its_own_code(self):
        cases = {
            'inactive': {'is_active': False},
            'no_price': {'price': Decimal('0.00')},
            'no_image': {'image_url': ''},
            'no_category': {'category': None},
        }
        for code, missing in cases.items():
            with self.subTest(code):
                self.assertEqual(product_publication.publication_problems(self.product(code, **missing)), [code])

    def test_several_at_once_come_in_a_fixed_order(self):
        bare = self.product('Sin nada', is_active=False, price=Decimal('0.00'), image_url='', category=None)
        self.assertEqual(
            product_publication.publication_problems(bare), ['inactive', 'no_price', 'no_image', 'no_category'])

    def test_a_gallery_picture_is_a_picture(self):
        product = self.product('Con galería', image_url='')
        ProductImage.objects.create(
            company=self.company, product=product, image_url='/api/storefront/images/abc', is_primary=True)
        self.assertEqual(product_publication.publication_problems(product), [])

    def test_every_code_is_explained_in_spanish(self):
        self.assertEqual(
            set(product_publication.PROBLEM_LABELS), {'inactive', 'no_price', 'no_image', 'no_category'})
        for label in product_publication.PROBLEM_LABELS.values():
            self.assertTrue(label.strip())


# =============================================================================
# By ids
# =============================================================================

class BulkByIdsTest(_Base):
    def test_hiding_takes_them_off_the_web_and_says_how_many(self):
        first, second = self.product('Funda'), self.product('Cable')
        already = self.product('Repuesto', published=False)

        response = self.post({'action': 'hide', 'ids': [first.pk, second.pk, already.pk]})

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {'action': 'hide', 'changed': 2, 'unchanged': 1, 'refused': [], 'total': 3})
        self.assertEqual(self.published(first, second, already), [False, False, False])

    def test_hiding_is_always_allowed(self):
        """Lo que no podría publicarse sí puede ocultarse: ocultar no enseña nada."""
        bare = self.product('Sin nada', is_active=False, price=Decimal('0.00'), image_url='', category=None)
        response = self.post({'action': 'hide', 'ids': [bare.pk]})
        self.assertEqual(response.json()['changed'], 1)
        self.assertEqual(self.published(bare), [False])

    def test_publishing_leaves_out_what_is_not_ready_and_says_why(self):
        ready = self.product('Funda', published=False)
        no_picture = self.product('Sin foto', published=False, image_url='')
        off = self.product('Inactivo sin precio', published=False, is_active=False, price=Decimal('0.00'))
        there = self.product('Ya publicado')

        response = self.post({'action': 'publish', 'ids': [ready.pk, no_picture.pk, off.pk, there.pk]})

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body['changed'], body['unchanged'], body['total']), (1, 1, 4))
        self.assertEqual(body['refused'], [
            {'id': no_picture.pk, 'name': 'Sin foto', 'problems': ['no_image']},
            {'id': off.pk, 'name': 'Inactivo sin precio', 'problems': ['inactive', 'no_price']},
        ])
        self.assertEqual(self.published(ready, no_picture, off, there), [True, False, False, True])

    def test_a_gallery_picture_lets_it_be_published(self):
        product = self.product('Con galería', published=False, image_url='')
        ProductImage.objects.create(
            company=self.company, product=product, image_url='/api/storefront/images/abc', is_primary=True)
        self.assertEqual(self.post({'action': 'publish', 'ids': [product.pk]}).json()['changed'], 1)

    def test_the_same_request_twice_changes_nothing_the_second_time(self):
        first, second = self.product('Funda'), self.product('Cable')
        body = {'action': 'hide', 'ids': [first.pk, second.pk]}
        self.assertEqual(self.post(body).json()['changed'], 2)
        stamps = list(Product.objects.filter(pk__in=[first.pk, second.pk]).order_by('pk').values_list('updated_at'))

        again = self.post(body)

        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json(), {'action': 'hide', 'changed': 0, 'unchanged': 2, 'refused': [], 'total': 2})
        self.assertEqual(
            list(Product.objects.filter(pk__in=[first.pk, second.pk]).order_by('pk').values_list('updated_at')), stamps)
        self.assertEqual(self.audit().count(), 1)

    def test_an_id_given_twice_is_one_product(self):
        product = self.product('Funda')
        body = self.post({'action': 'hide', 'ids': [product.pk, product.pk]}).json()
        self.assertEqual((body['changed'], body['total']), (1, 1))

    def test_only_the_visibility_and_its_date_are_written(self):
        branch = _p2d_branch(self.company, 'Centro')
        plain = self.product('Funda')
        _p2d_stock(branch, plain, 4)
        device = self.product('Teléfono', is_serialized=True)
        stock_unit_services.receive_units(
            branch=branch, product=device, units=[{'serial_number': 'BV-SERIE-1'}],
            actor=self.manager, reason='Compra de prueba',
        )

        def snapshot():
            rows = Product.objects.filter(pk__in=[plain.pk, device.pk]).order_by('pk').values()
            return (
                [{k: v for k, v in row.items() if k not in ('is_published_online', 'updated_at')} for row in rows],
                list(BranchStock.objects.order_by('pk').values()),
                list(StockMovement.objects.order_by('pk').values()),
                list(StockUnit.objects.order_by('pk').values()),
            )

        before = snapshot()
        stamps = dict(Product.objects.filter(pk__in=[plain.pk, device.pk]).values_list('pk', 'updated_at'))

        for action in ('hide', 'publish'):
            response = self.post({'action': action, 'ids': [plain.pk, device.pk]})
            self.assertEqual(response.json()['changed'], 2, response.content)
            self.assertEqual(snapshot(), before)

        for pk, stamp in Product.objects.filter(pk__in=[plain.pk, device.pk]).values_list('pk', 'updated_at'):
            self.assertGreater(stamp, stamps[pk])

    def test_one_audit_line_per_batch_with_what_changed_and_nobody_s_data(self):
        ready = self.product('Funda', published=False)
        no_picture = self.product('Sin foto', published=False, image_url='')

        self.post({'action': 'publish', 'ids': [ready.pk, no_picture.pk]})

        entry = self.audit().get()
        self.assertEqual(entry.company, self.company)
        self.assertEqual(entry.actor, self.manager)
        self.assertEqual(entry.target_type, 'product')
        self.assertEqual(entry.metadata, {
            'action': 'publish', 'changed': 1, 'unchanged': 0, 'refused': 1, 'total': 2,
            'changed_ids': [ready.pk], 'changed_ids_truncated': False, 'filter': None,
        })

    def test_the_audit_line_lists_a_capped_number_of_ids_and_the_full_count(self):
        products = [self.product(f'Funda {n}') for n in range(5)]
        with patch.object(product_publication, 'AUDIT_IDS_LIMIT', 3):
            self.post({'action': 'hide', 'ids': [p.pk for p in products]})
        metadata = self.audit().get().metadata
        self.assertEqual(metadata['changed'], 5)
        self.assertEqual(metadata['changed_ids'], sorted(p.pk for p in products)[:3])
        self.assertTrue(metadata['changed_ids_truncated'])

    def test_a_batch_that_changes_nothing_leaves_no_audit_line(self):
        product = self.product('Sin foto', published=False, image_url='')
        self.assertEqual(self.post({'action': 'publish', 'ids': [product.pk]}).json()['changed'], 0)
        self.assertEqual(self.audit().count(), 0)

    def test_more_ids_than_the_cap_are_refused_with_the_count(self):
        products = [self.product(f'Funda {n}') for n in range(4)]
        with patch.object(product_publication, 'MAX_BULK_PRODUCTS', 3):
            response = self.post({'action': 'hide', 'ids': [p.pk for p in products]})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()['code'], 'too_many')
        self.assertEqual((response.json()['count'], response.json()['limit']), (4, 3))
        self.assertEqual(self.published(*products), [True] * 4)

    def test_a_request_that_does_not_say_what_to_do_is_refused(self):
        product = self.product('Funda')
        bad = [
            {},
            {'ids': [product.pk]},
            {'action': 'delete', 'ids': [product.pk]},
            {'action': 'hide'},
            {'action': 'hide', 'ids': []},
            {'action': 'hide', 'ids': 'todos'},
            {'action': 'hide', 'ids': [product.pk, 'x']},
            {'action': 'hide', 'ids': [True]},
            {'action': 'hide', 'ids': [0]},
            {'action': 'hide', 'ids': [product.pk], 'filter': {}, 'expected_count': 1},
        ]
        for body in bad:
            with self.subTest(body=body):
                self.assertEqual(self.post(body).status_code, 400)
        self.assertEqual(self.published(product), [True])
        self.assertEqual(self.audit().count(), 0)


class BulkTenancyTest(_Base):
    def test_another_company_s_id_answers_like_one_that_does_not_exist(self):
        mine = self.product('Funda')
        foreign = self.product('Ajeno', company=self.other)

        with_foreign = self.post({'action': 'hide', 'ids': [mine.pk, foreign.pk]})
        with_unknown = self.post({'action': 'hide', 'ids': [mine.pk, 987654321]})

        self.assertEqual(with_foreign.status_code, 404)
        self.assertEqual(with_unknown.status_code, 404)
        self.assertEqual(with_foreign.json(), with_unknown.json())
        self.assertNotIn('Ajeno', with_foreign.content.decode())
        # Refused WHOLE: the caller's own product did not move either.
        self.assertEqual(self.published(mine, foreign), [True, True])
        self.assertEqual(self.audit().count(), 0)

    def test_a_company_named_in_the_body_selects_nothing(self):
        foreign = self.product('Ajeno', company=self.other)
        response = self.post({'action': 'hide', 'ids': [foreign.pk], 'company': self.other.pk})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.published(foreign), [True])

    def test_a_company_named_in_the_address_must_be_the_caller_s(self):
        foreign = self.product('Ajeno', company=self.other)
        response = self.api.post(f'{URL}?company={self.other.pk}', {'action': 'hide', 'ids': [foreign.pk]}, format='json')
        self.assertIn(response.status_code, (403, 404))
        self.assertEqual(self.published(foreign), [True])

    def test_a_filter_never_reaches_another_company(self):
        mine = self.product('Funda compartida')
        foreign = self.product('Funda compartida', company=self.other)
        response = self.post({'action': 'hide', 'filter': {'search': 'compartida'}, 'expected_count': 1})
        self.assertEqual(response.json()['changed'], 1, response.content)
        self.assertEqual(self.published(mine, foreign), [False, True])

    def test_another_company_s_category_in_a_filter_matches_nothing(self):
        theirs = Category.objects.create(company=self.other, name='Ajena', slug='ajena')
        foreign = self.product('Ajeno', company=self.other, category=theirs)
        mine = self.product('Funda')
        response = self.post({'action': 'hide', 'filter': {'category': theirs.pk}, 'expected_count': 1})
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()['current_count'], 0)
        self.assertEqual(self.published(mine, foreign), [True, True])

    def test_it_needs_the_capability_to_manage_products(self):
        product = self.product('Funda')
        viewer, _ = _p2d_member(self.company, 'bv_lectora', ['company.view', 'products.view'])
        stock, _ = _p2d_member(self.company, 'bv_almacen', ['company.view', 'inventory.view', 'inventory.adjust'])
        nobody, _ = _p2d_member(self.company, 'bv_nadie', ['company.view'])
        stranger, _ = _p2d_member(self.other, 'bv_ajena', ['company.view', 'products.view', 'products.manage'])
        outsider = User.objects.create_user('bv_sin_empresa', password='Pass123!')

        for user in (viewer, stock, nobody, outsider):
            with self.subTest(user.username):
                self.assertEqual(self.post({'action': 'hide', 'ids': [product.pk]}, _client(user)).status_code, 403)
        # A manager of ANOTHER company manages their own: this id is not there.
        self.assertEqual(self.post({'action': 'hide', 'ids': [product.pk]}, _client(stranger)).status_code, 404)
        self.assertIn(self.post({'action': 'hide', 'ids': [product.pk]}, APIClient()).status_code, (401, 403))
        self.assertEqual(self.published(product), [True])
        self.assertEqual(self.audit().count(), 0)

    def test_only_post(self):
        for method in ('get', 'put', 'patch', 'delete'):
            self.assertEqual(getattr(self.api, method)(URL).status_code, 405)

    def test_the_write_is_throttled_like_any_other_product_write(self):
        from store.throttles import AdminProductWriteThrottle

        self.assertEqual(
            [type(t) for t in admin_views.AdminProductBulkVisibilityView().get_throttles()], [AdminProductWriteThrottle])


# =============================================================================
# By filter: «todo lo que coincide»
# =============================================================================

class BulkByFilterTest(_Base):
    def setUp(self):
        super().setUp()
        self.spares = Category.objects.create(company=self.company, name='Repuestos', slug='repuestos')
        self.case = self.product('Funda azul')
        self.cable = self.product('Cable azul', category=self.spares)
        self.flex = self.product('Flex', category=self.spares, published=False)
        self.phone = self.product('Teléfono azul', is_serialized=True)
        self.old = self.product('Funda vieja', is_active=False)

    def listed(self, query):
        response = self.api.get(f'{LIST_URL}?{query}&page_size=100')
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_it_changes_exactly_what_the_list_shows_for_that_filter(self):
        cases = [
            ({'search': 'azul'}, 'search=azul'),
            ({'category': self.spares.pk}, f'category={self.spares.pk}'),
            ({'is_active': 'false'}, 'is_active=false'),
            ({'is_serialized': 'true'}, 'is_serialized=true'),
            ({'is_serialized': 'false', 'is_active': 'true'}, 'is_serialized=false&is_active=true'),
            ({'stock': 'out_of_stock', 'search': 'Funda'}, 'stock=out_of_stock&search=Funda'),
        ]
        everything = [self.case, self.cable, self.flex, self.phone, self.old]
        for filters, query in cases:
            with self.subTest(query):
                Product.objects.filter(company=self.company).update(is_published_online=True)
                shown = self.listed(query)
                wanted = {row['id'] for row in shown['results']}
                self.assertTrue(wanted)
                self.assertLess(len(wanted), len(everything))

                response = self.post({'action': 'hide', 'filter': filters, 'expected_count': shown['count']})

                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(response.json()['total'], len(wanted))
                hidden = set(Product.objects.filter(company=self.company, is_published_online=False).values_list('pk', flat=True))
                self.assertEqual(hidden, wanted)

    def test_the_list_and_the_bulk_action_share_one_filter(self):
        """No two copies: both go through `filter_admin_products`."""
        with patch.object(admin_views, 'filter_admin_products', wraps=admin_views.filter_admin_products) as shared:
            self.listed('is_active=true')
            self.post({'action': 'hide', 'filter': {'is_active': 'true'}, 'expected_count': 4})
        self.assertGreaterEqual(shared.call_count, 2)

    def test_the_visibility_filter_selects_the_internal_ones(self):
        response = self.post({'action': 'publish', 'filter': {'is_published_online': 'false'}, 'expected_count': 1})
        self.assertEqual(response.json()['changed'], 1, response.content)
        self.assertEqual(self.published(self.flex), [True])

    def test_true_and_false_may_arrive_as_json_booleans(self):
        response = self.post({'action': 'hide', 'filter': {'is_serialized': True}, 'expected_count': 1})
        self.assertEqual(response.json()['changed'], 1, response.content)
        self.assertEqual(self.published(self.phone, self.case), [False, True])

    def test_an_empty_filter_is_the_whole_catalogue_and_still_needs_the_count(self):
        response = self.post({'action': 'hide', 'filter': {}, 'expected_count': 5})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {'action': 'hide', 'changed': 4, 'unchanged': 1, 'refused': [], 'total': 5})

    def test_a_selection_that_changed_since_it_was_counted_is_not_applied(self):
        self.product('Funda azul nueva')                       # arrived after the operator counted three
        response = self.post({'action': 'hide', 'filter': {'search': 'azul'}, 'expected_count': 3})

        self.assertEqual(response.status_code, 409, response.content)
        body = response.json()
        self.assertEqual(body['code'], 'selection_changed')
        self.assertEqual(body['current_count'], 4)
        self.assertEqual(self.published(self.case, self.cable, self.phone), [True, True, True])
        self.assertEqual(self.audit().count(), 0)

    def test_the_count_is_required_and_must_be_a_number(self):
        for body in (
            {'action': 'hide', 'filter': {'search': 'azul'}},
            {'action': 'hide', 'filter': {'search': 'azul'}, 'expected_count': 'tres'},
            {'action': 'hide', 'filter': {'search': 'azul'}, 'expected_count': -1},
            {'action': 'hide', 'filter': {'search': 'azul'}, 'expected_count': True},
            {'action': 'hide', 'filter': 'azul', 'expected_count': 3},
        ):
            with self.subTest(body=body):
                self.assertEqual(self.post(body).status_code, 400)
        self.assertEqual(self.published(self.case), [True])

    def test_a_filter_it_does_not_know_is_refused_instead_of_ignored(self):
        """A misspelt key must not become «the whole catalogue»."""
        response = self.post({'action': 'hide', 'filter': {'categoria': self.spares.pk}, 'expected_count': 5})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(self.published(self.case, self.cable), [True, True])

    def test_a_filter_value_that_means_nothing_is_refused_instead_of_ignored(self):
        for filters in ({'category': 'repuestos'}, {'is_active': 'quizá'}, {'stock': 'mucho'},
                        {'is_serialized': 'x'}, {'is_published_online': 2}, {'search': ['azul']}):
            with self.subTest(filters=filters):
                response = self.post({'action': 'hide', 'filter': filters, 'expected_count': 5})
                self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(self.published(self.case, self.cable), [True, True])

    def test_more_results_than_the_cap_are_refused_with_the_count(self):
        with patch.object(product_publication, 'MAX_BULK_PRODUCTS', 4):
            response = self.post({'action': 'hide', 'filter': {}, 'expected_count': 5})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual((response.json()['code'], response.json()['count'], response.json()['limit']), ('too_many', 5, 4))
        self.assertEqual(self.published(self.case, self.cable), [True, True])

    def test_the_audit_line_keeps_the_filter_that_was_used(self):
        self.post({'action': 'hide', 'filter': {'search': 'azul', 'is_active': 'true'}, 'expected_count': 3})
        metadata = self.audit().get().metadata
        self.assertEqual(metadata['filter'], {'search': 'azul', 'is_active': 'true'})
        self.assertEqual((metadata['changed'], metadata['total']), (3, 3))

    def test_publishing_by_filter_reports_what_it_left_out(self):
        Product.objects.filter(company=self.company).update(is_published_online=False)
        response = self.post({'action': 'publish', 'filter': {'search': 'Funda'}, 'expected_count': 2})
        body = response.json()
        self.assertEqual((body['changed'], body['unchanged'], body['total']), (1, 0, 2))
        self.assertEqual(body['refused'], [{'id': self.old.pk, 'name': 'Funda vieja', 'problems': ['inactive']}])


# =============================================================================
# The list the screen reads
# =============================================================================

class ProductListTest(_Base):
    def rows(self, query=''):
        response = self.api.get(f'{LIST_URL}?{query}')
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_it_filters_by_how_the_product_is_counted(self):
        phone = self.product('Teléfono', is_serialized=True)
        case = self.product('Funda')
        self.assertEqual([row['id'] for row in self.rows('is_serialized=true')['results']], [phone.pk])
        self.assertEqual([row['id'] for row in self.rows('is_serialized=false')['results']], [case.pk])
        self.assertEqual(self.rows('is_serialized=quizá')['count'], 2)

    def test_each_row_says_why_it_cannot_be_published(self):
        ready = self.product('Funda')
        bare = self.product('Sin nada', is_active=False, price=Decimal('0.00'), image_url='', category=None)
        gallery = self.product('Con galería', image_url='')
        ProductImage.objects.create(
            company=self.company, product=gallery, image_url='/api/storefront/images/abc', is_primary=True)

        problems = {row['id']: row['publication_problems'] for row in self.rows()['results']}

        self.assertEqual(problems[ready.pk], [])
        self.assertEqual(problems[bare.pk], ['inactive', 'no_price', 'no_image', 'no_category'])
        self.assertEqual(problems[gallery.pk], [])

    def test_the_problems_cost_no_query_per_row(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def queries():
            cache.clear()
            with CaptureQueriesContext(connection) as captured:
                rows = self.rows()['results']
            return len(captured), len(rows)

        for n in range(2):
            self.product(f'Sin foto {n}', image_url='')
        with_two, shown = queries()
        self.assertEqual(shown, 2)

        for n in range(6):
            self.product(f'Otra sin foto {n}', image_url='')
        with_eight, shown = queries()

        self.assertEqual(shown, 8)
        self.assertEqual(with_eight, with_two)

    def test_the_detail_carries_them_too(self):
        product = self.product('Sin foto', image_url='')
        self.assertEqual(self.api.get(f'{LIST_URL}{product.pk}/').json()['publication_problems'], ['no_image'])

    def test_the_total_is_exact_whatever_the_page(self):
        for n in range(7):
            self.product(f'Funda {n}')
        self.product('Cable')
        first = self.rows('search=Funda&page_size=3')
        last = self.rows('search=Funda&page_size=3&page=3')
        self.assertEqual((first['count'], len(first['results'])), (7, 3))
        self.assertEqual((last['count'], len(last['results'])), (7, 1))

    def test_the_individual_edit_still_publishes_what_the_bulk_action_would_refuse(self):
        """La regla es del bloque. La ficha del producto guarda como siempre."""
        product = self.product('Sin foto', published=False, image_url='')
        response = self.api.patch(f'{LIST_URL}{product.pk}/', {'is_published_online': True}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.published(product), [True])
        self.assertEqual(response.json()['publication_problems'], ['no_image'])


# =============================================================================
# Two at once
# =============================================================================

class TwoBulkRequestsAtOnceTest(TransactionTestCase):
    """Real commits and real row locks, with two connections."""

    def setUp(self):
        cache.clear()
        self.company = _saas_company('Tienda A', 'bulk-vis-dos', tax_id='20999999991')
        from store.company_provisioning import provision_company_access_defaults
        provision_company_access_defaults(self.company)
        self.first, _ = _p2d_member(self.company, 'bv_una', ['company.view', 'products.manage'])
        self.second, _ = _p2d_member(self.company, 'bv_otra', ['company.view', 'products.manage'])
        category = Category.objects.create(company=self.company, name='Accesorios', slug='accesorios')
        self.ids = [
            Product.objects.create(
                company=self.company, name=f'Funda {n}', slug=f'bv2-{n}', price=Decimal('59.00'),
                image_url=PICTURE, category=category,
            ).pk for n in range(40)
        ]

    def together(self, jobs):
        barrier, results = threading.Barrier(len(jobs)), [None] * len(jobs)

        def run(index, job):
            try:
                barrier.wait(timeout=10)
                results[index] = job()
            except Exception as failed:  # noqa: BLE001 — the outcome under test
                results[index] = failed
            finally:
                db_connection.close()

        threads = [threading.Thread(target=run, args=(i, job)) for i, job in enumerate(jobs)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        return results

    def test_two_people_hiding_the_same_products_change_each_one_once(self):
        for round_ in range(3):
            Product.objects.filter(pk__in=self.ids).update(is_published_online=True)
            AdminAuditLog.objects.filter(action='products_visibility_changed').delete()
            # Opposite orders on purpose: the rows are locked in id order
            # whatever order they were asked for, so neither waits on the other
            # while holding what the other needs.
            results = self.together([
                lambda: _client(self.first).post(URL, {'action': 'hide', 'ids': self.ids}, format='json'),
                lambda: _client(self.second).post(URL, {'action': 'hide', 'ids': self.ids[::-1]}, format='json'),
            ])
            for response in results:
                self.assertEqual(getattr(response, 'status_code', response), 200, results)
            bodies = [response.json() for response in results]
            self.assertEqual(sum(body['changed'] for body in bodies), len(self.ids), bodies)
            self.assertEqual(sorted(body['changed'] for body in bodies), [0, len(self.ids)], bodies)
            self.assertFalse(Product.objects.filter(pk__in=self.ids, is_published_online=True).exists())
            self.assertEqual(AdminAuditLog.objects.filter(action='products_visibility_changed').count(), 1, round_)

    def test_publishing_and_hiding_at_once_leave_every_product_in_one_of_the_two_states(self):
        results = self.together([
            lambda: _client(self.first).post(URL, {'action': 'hide', 'ids': self.ids}, format='json'),
            lambda: _client(self.second).post(URL, {'action': 'publish', 'ids': self.ids[::-1]}, format='json'),
        ])
        for response in results:
            self.assertEqual(getattr(response, 'status_code', response), 200, results)
        # One batch ran after the other, whole: the catalogue is not half and half.
        states = set(Product.objects.filter(pk__in=self.ids).values_list('is_published_online', flat=True))
        self.assertEqual(len(states), 1, states)
