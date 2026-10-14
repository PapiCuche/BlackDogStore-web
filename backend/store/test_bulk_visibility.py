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
  * empresa, permisos, topes, concurrencia y auditoría;
  * que lo oculto o inactivo no enseña precio ni se deja comprar por ningún
    camino público, también cuando ya estaba en un carrito.
"""
import io
import threading
import time
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection as db_connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from openpyxl import Workbook
from rest_framework.test import APIClient

from store import admin_views, import_exports, import_services, product_publication, stock_unit_services
from store.models import (
    AdminAuditLog, BranchStock, BulkImportJob, CartItem, Category, CompanySettings, Order, OrderItem, Product,
    ProductBarcode,
    ProductImage, Review, StockMovement, StockUnit,
)
from store.tests import (
    FAKE_SESSION_TOKEN, IZIPAY_TEST_SETTINGS, SESSION_TOKEN_PATH,
    _make_product, _p2d_branch, _p2d_member, _p2d_stock, _p3_company, _pilot_company, _saas_company,
    _v1_customer,
)

User = get_user_model()

URL = '/api/admin/products/bulk-visibility/'
LIST_URL = '/api/admin/products/'
PICTURE = 'https://imagenes.example.test/producto.png'

CHECKOUT = {
    'customer_name': 'Ana Torres', 'customer_email': 'ana@correo.test', 'customer_phone': '936449536',
    'document_type': 'dni', 'document_number': '12345678', 'delivery_method': 'pickup_store',
    'receipt_type': 'boleta', 'accepted_terms': True, 'accepted_warranty_policy': True,
}


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

    def require_picture(self, company=None, on=True):
        """The company's own rule: a product needs a picture to be published in bulk. Off as created."""
        CompanySettings.objects.update_or_create(
            company=company or self.company, defaults={'publication_requires_image': on})

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
        self.require_picture()
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
        self.require_picture()
        bare = self.product('Sin nada', is_active=False, price=Decimal('0.00'), image_url='', category=None)
        self.assertEqual(
            product_publication.publication_problems(bare), ['inactive', 'no_price', 'no_image', 'no_category'])

    def test_a_picture_is_asked_only_by_the_company_that_asks_for_it(self):
        """Owner's decision (2026-10-13): no picture is required unless the company configures it."""
        self.assertFalse(CompanySettings._meta.get_field('publication_requires_image').default)
        self.assertFalse(CompanySettings.objects.filter(publication_requires_image=True).exists())
        product = self.product('Sin foto', image_url='')
        bare = self.product('Sin nada', is_active=False, price=Decimal('0.00'), image_url='', category=None)

        self.assertEqual(product_publication.publication_problems(product), [])
        self.assertEqual(product_publication.publication_problems(bare), ['inactive', 'no_price', 'no_category'])

        self.require_picture()
        self.assertEqual(product_publication.publication_problems(product), ['no_image'])
        self.require_picture(on=False)
        self.assertEqual(product_publication.publication_problems(product), [])

    def test_one_companys_rule_is_not_anothers(self):
        self.require_picture(self.other)
        mine = self.product('Sin foto', image_url='')
        theirs = self.product('Sin foto', company=self.other, image_url='', category=Category.objects.create(
            company=self.other, name='Accesorios', slug='accesorios'))
        self.assertEqual(product_publication.publication_problems(mine), [])
        self.assertEqual(product_publication.publication_problems(theirs), ['no_image'])

    def test_a_company_without_a_settings_row_asks_for_no_picture(self):
        CompanySettings.objects.filter(company=self.company).delete()
        self.assertEqual(product_publication.publication_problems(self.product('Sin foto', image_url='')), [])

    def test_a_gallery_picture_is_a_picture(self):
        self.require_picture()
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

    def test_without_the_companys_rule_a_product_with_no_picture_is_published(self):
        no_picture = self.product('Sin foto', published=False, image_url='')
        no_category = self.product('Sin categoría', published=False, image_url='', category=None)

        body = self.post({'action': 'publish', 'ids': [no_picture.pk, no_category.pk]}).json()

        self.assertEqual(body['changed'], 1)
        self.assertEqual(body['refused'], [{'id': no_category.pk, 'name': 'Sin categoría', 'problems': ['no_category']}])
        self.assertEqual(self.published(no_picture, no_category), [True, False])

    def test_publishing_leaves_out_what_is_not_ready_and_says_why(self):
        self.require_picture()
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
        self.require_picture()
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

    def test_an_inactive_product_stays_inactive_whatever_is_done_to_its_visibility(self):
        """Review M06: hiding wrote `is_active` too and nothing noticed, because every fixture was active."""
        off = self.product('Descatalogado', is_active=False, price=Decimal('12.50'))
        on = self.product('Vigente', published=False)

        def facts(product):
            return Product.objects.filter(pk=product.pk).values_list('is_active', 'price', 'name', 'inventory').get()

        before = facts(off), facts(on)
        self.assertEqual(self.post({'action': 'hide', 'ids': [off.pk, on.pk]}).json()['changed'], 1)
        self.assertEqual((facts(off), facts(on)), before)
        self.assertEqual(self.published(off, on), [False, False])

        body = self.post({'action': 'publish', 'ids': [off.pk, on.pk]}).json()
        self.assertEqual((body['changed'], [item['id'] for item in body['refused']]), (1, [off.pk]))
        self.assertEqual((facts(off), facts(on)), before)
        self.assertEqual(self.published(off, on), [False, True])

    def test_one_audit_line_per_batch_with_what_changed_and_nobody_s_data(self):
        ready = self.product('Funda', published=False)
        no_picture = self.product('Sin categoría', published=False, category=None)

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
        product = self.product('Sin categoría', published=False, category=None)
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

    def test_what_starts_matching_after_the_selection_was_read_is_not_changed(self):
        """
        Review N6. The count and the ids were two reads: a product that began to
        match between them was changed without ever having been counted.
        """
        calls = []

        def select(queryset):
            calls.append(1)
            if len(calls) == 2:
                # What a commit by somebody else looks like, right after the selection was read.
                self.late = self.product('Teléfono azul tardío')
            return queryset.filter(name__icontains='azul')

        result = product_publication.set_visibility(company=self.company, publish=False, select=select, expected_count=3)

        self.assertEqual((result['changed'], result['total']), (3, 3))
        self.assertEqual(self.published(self.late), [True])
        self.assertEqual(self.published(self.case, self.cable, self.phone), [False, False, False])

    def test_the_selection_is_read_once_and_that_is_what_is_compared_with_the_count(self):
        late = []

        def select(queryset):
            if not late:
                late.append(self.product('Funda azul recién llegada'))     # arrives before the one and only read
            return queryset.filter(name__icontains='azul')

        with self.assertRaises(product_publication.SelectionChanged) as refused:
            product_publication.set_visibility(company=self.company, publish=False, select=select, expected_count=3)
        self.assertEqual(refused.exception.current_count, 4)
        self.assertEqual(self.published(self.case, self.cable, self.phone), [True] * 3)

    def test_a_product_edited_between_the_selection_and_the_lock_stops_the_batch(self):
        """Asked again WITH the rows held: what was selected may have stopped matching."""
        calls = []

        def select(queryset):
            calls.append(1)
            if len(calls) == 2:
                Product.objects.filter(pk=self.cable.pk).update(name='Cable verde')
            return queryset.filter(name__icontains='azul')

        with self.assertRaises(product_publication.SelectionChanged) as refused:
            product_publication.set_visibility(company=self.company, publish=False, select=select, expected_count=3)
        self.assertEqual(refused.exception.current_count, 2)
        self.assertEqual(self.published(self.case, self.cable, self.phone), [True, True, True])

    def test_a_search_that_no_text_can_hold_is_refused_not_a_crash(self):
        """Review N9: a NUL in «search» reached the database and answered 500."""
        response = self.post({'action': 'hide', 'filter': {'search': 'a\x00b'}, 'expected_count': 0})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()['detail'], 'El filtro no es válido.')
        self.assertEqual(self.published(self.case), [True])

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

    def test_a_search_that_no_text_can_hold_matches_nothing(self):
        """Review N9: the list answered 500 to `?search=a%00b`."""
        self.product('Funda')
        found = self.rows('search=a%00b')
        self.assertEqual((found['count'], found['results']), (0, []))
        self.assertEqual(self.rows('search=Funda')['count'], 1)

    def test_each_row_follows_the_companys_rule_about_pictures(self):
        no_picture = self.product('Sin foto', image_url='')
        self.assertEqual(self.rows()['results'][0]['publication_problems'], [])
        self.assertEqual(self.api.get(f'{LIST_URL}{no_picture.pk}/').json()['publication_problems'], [])
        self.require_picture()
        cache.clear()
        self.assertEqual(self.rows()['results'][0]['publication_problems'], ['no_image'])

    def test_each_row_says_why_it_cannot_be_published(self):
        self.require_picture()
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

        self.require_picture()
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
        self.require_picture()
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
        self.require_picture()
        product = self.product('Sin foto', published=False, image_url='')
        response = self.api.patch(f'{LIST_URL}{product.pk}/', {'is_published_online': True}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.published(product), [True])
        self.assertEqual(response.json()['publication_problems'], ['no_image'])


# =============================================================================
# The company's own rule about pictures
# =============================================================================

class PictureRuleSettingTest(_Base):
    """
    `CompanySettings.publication_requires_image`. Read and written with the
    rest of the company's configuration (`company.view` / `company.manage`),
    off as created, and never told to the public.
    """

    SETTINGS = '/api/admin/company-settings/'

    def setUp(self):
        super().setUp()
        self.admin, _ = _p2d_member(self.company, 'bv_admin', ['company.view', 'company.manage'])
        self.viewer, _ = _p2d_member(self.company, 'bv_lectora', ['company.view'])

    def stored(self, company=None):
        return CompanySettings.objects.get(company=company or self.company).publication_requires_image

    def test_it_is_read_with_the_rest_and_starts_off(self):
        body = _client(self.viewer).get(self.SETTINGS).json()
        self.assertIs(body['settings']['publication_requires_image'], False)

    def test_whoever_administers_the_company_sets_it_and_it_is_written_down(self):
        response = _client(self.admin).patch(self.SETTINGS, {'publication_requires_image': True}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIs(response.json()['settings']['publication_requires_image'], True)
        self.assertIs(self.stored(), True)
        entry = AdminAuditLog.objects.get(action='company_settings_updated')
        self.assertEqual((entry.actor, entry.company), (self.admin, self.company))
        self.assertEqual(entry.metadata['changed_fields'], ['publication_requires_image'])

    def test_reading_is_not_enough_and_a_word_is_not_a_yes(self):
        self.assertEqual(
            _client(self.viewer).patch(self.SETTINGS, {'publication_requires_image': True}, format='json').status_code, 403)
        self.assertEqual(
            _client(self.manager).patch(self.SETTINGS, {'publication_requires_image': True}, format='json').status_code, 403)
        self.assertEqual(
            _client(self.admin).patch(self.SETTINGS, {'publication_requires_image': 'quizás'}, format='json').status_code,
            400)
        self.assertIs(self.stored(), False)

    def test_another_companys_rule_is_out_of_reach(self):
        response = _client(self.admin).patch(
            f'{self.SETTINGS}?company={self.other.pk}', {'publication_requires_image': True}, format='json')
        self.assertEqual(response.status_code, 404)
        _client(self.admin).patch(self.SETTINGS, {'publication_requires_image': True}, format='json')
        self.assertIs(self.stored(self.other), False)

    def test_the_bulk_action_obeys_it_from_the_next_request(self):
        product = self.product('Sin foto', published=False, image_url='')
        _client(self.admin).patch(self.SETTINGS, {'publication_requires_image': True}, format='json')
        body = self.post({'action': 'publish', 'ids': [product.pk]}).json()
        self.assertEqual((body['changed'], [row['problems'] for row in body['refused']]), (0, [['no_image']]))

    @override_settings(ALLOWED_HOSTS=['*'])
    def test_the_public_never_learns_it(self):
        self.require_picture()
        response = APIClient().get(f'/api/v1/storefront/{self.company.slug}/config/')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn('publication_requires_image', response.content.decode())


# =============================================================================
# What the public sees of a product that left the web
# =============================================================================

class PublicExposureMatrixTest(TestCase):
    """
    One product hidden and one deactivated, BOTH AFTER a buyer put them in a cart.

    Every public way in, for each of the two: none shows its price, its stock
    or its description, and none lets it be bought. The cart is the one place
    that still names it — the buyer put it there — and only to say it is gone.
    """

    def setUp(self):
        cache.clear()
        self.company = _pilot_company()
        self.category = Category.objects.create(company=self.company, name='Cables Expo', slug='cables-expo')
        self.on_sale = _make_product('Cable Expo', 'cable-expo', price='41.00', category=self.category)
        hidden = _make_product('Oculto Expo', 'oculto-expo', price='73.19', category=self.category)
        inactive = _make_product('Inactivo Expo', 'inactivo-expo', price='86.27', category=self.category)
        buyer = self.buyer = User.objects.create_user('expo_compradora', 'expo@correo.test', 'Pass123!')
        # An order of that buyer from when all three were on sale, at the price of that day.
        self.order = Order.objects.create(
            company=self.company, user=buyer, customer_name='Ana Torres', customer_email='expo@correo.test',
            total=Decimal('30.00'), status=Order.Status.PAID, paid=True,
        )
        for product in (self.on_sale, hidden, inactive):
            Product.objects.filter(pk=product.pk).update(description=f'Ficha reservada de {product.slug}')
            Review.objects.create(product=product, user=buyer, rating=5, comment=f'Reseña de {product.slug}')
            OrderItem.objects.create(order=self.order, product=product, quantity=3, price=Decimal('10.00'))
        # In a cart while it was still on sale — one cart per case, next to
        # something the shop does sell.
        self.gone = {'hidden': hidden, 'inactive': inactive}
        for kind, product in self.gone.items():
            CartItem.objects.create(session_key=f'expo_{kind}', product=self.on_sale, quantity=1)
            CartItem.objects.create(session_key=f'expo_{kind}', product=product, quantity=2)
        Product.objects.filter(pk=hidden.pk).update(is_published_online=False)
        Product.objects.filter(pk=inactive.pk).update(is_active=False)
        for product in self.gone.values():
            product.refresh_from_db()
        self.client = APIClient()

    def assert_says_nothing(self, response, product, *, name_allowed=False):
        """Not its price, not its description, not its address — and its name only where allowed."""
        body = response.content.decode()
        self.assertNotIn(str(product.price), body)
        self.assertNotIn('Ficha reservada de ' + product.slug, body)
        self.assertNotIn(product.slug, body)
        if not name_allowed:
            self.assertNotIn(product.name, body)

    def slugs(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        return response, [row['slug'] for row in (body['results'] if isinstance(body, dict) else body)]

    def line(self, kind, product):
        return CartItem.objects.get(session_key=f'expo_{kind}', product=product)

    # -- catalogue ------------------------------------------------------------

    def test_web_list(self):
        response, found = self.slugs('/api/products/')
        self.assertIn('cable-expo', found)
        for kind, product in self.gone.items():
            with self.subTest(kind):
                self.assertNotIn(product.slug, found)
                self.assert_says_nothing(response, product)

    def test_web_detail(self):
        self.assertEqual(self.client.get(f'/api/products/{self.on_sale.pk}/').status_code, 200)
        for kind, product in self.gone.items():
            with self.subTest(kind):
                response = self.client.get(f'/api/products/{product.pk}/')
                self.assertEqual(response.status_code, 404)
                self.assert_says_nothing(response, product)
                response, found = self.slugs(f'/api/products/?slug={product.slug}')
                self.assertEqual(found, [])

    def test_web_search(self):
        response, found = self.slugs('/api/products/?search=Expo')
        self.assertEqual(found, ['cable-expo'])
        for kind, product in self.gone.items():
            with self.subTest(kind):
                self.assert_says_nothing(response, product)
                _response, by_name = self.slugs(f'/api/products/?search={product.name.split()[0]}')
                self.assertEqual(by_name, [])

    def test_category_listing(self):
        response, found = self.slugs('/api/products/?category=cables-expo')
        self.assertEqual(found, ['cable-expo'])
        for kind, product in self.gone.items():
            with self.subTest(kind):
                self.assert_says_nothing(response, product)

    def test_reviews_read(self):
        self.assertEqual(len(self.client.get(f'/api/reviews/?product={self.on_sale.pk}').json()), 1)
        for kind, product in self.gone.items():
            with self.subTest(kind):
                response = self.client.get(f'/api/reviews/?product={product.pk}')
                self.assertEqual(response.json(), [])

    # -- cart -----------------------------------------------------------------

    def test_cart_add(self):
        for kind, product in self.gone.items():
            with self.subTest(kind):
                response = self.client.post(
                    '/api/cart/add/', {'session_key': f'expo_{kind}', 'product': product.pk, 'quantity': 1}, format='json')
                self.assertEqual(response.status_code, 404, response.content)
                self.assert_says_nothing(response, product)
                self.assertEqual(self.line(kind, product).quantity, 2)

    def test_cart_read_opens_and_marks_the_line_unavailable(self):
        for kind, product in self.gone.items():
            with self.subTest(kind):
                response = self.client.get(f'/api/cart/?session_key=expo_{kind}')
                self.assertEqual(response.status_code, 200, response.content)
                lines = {row['product']['id']: row for row in response.json()}

                sold = lines[self.on_sale.pk]
                self.assertIs(sold['available'], True)
                self.assertEqual(sold['product']['price'], '41.00')
                self.assertEqual(sold['product']['slug'], 'cable-expo')

                gone = lines[product.pk]
                self.assertIs(gone['available'], False)
                # Name and picture, to say what it was and let it be removed. Nothing else.
                self.assertEqual(gone['product'], {'id': product.pk, 'name': product.name, 'image_url': product.image_url})
                self.assertEqual(gone['quantity'], 2)
                self.assert_says_nothing(response, product, name_allowed=True)

    def test_cart_patch(self):
        for kind, product in self.gone.items():
            with self.subTest(kind):
                line = self.line(kind, product)
                response = self.client.patch(
                    f'/api/cart/{line.pk}/?session_key=expo_{kind}', {'quantity': 1}, format='json')
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn('ya no está disponible', response.json()['detail'])
                self.assert_says_nothing(response, product, name_allowed=True)
                self.assertEqual(self.line(kind, product).quantity, 2)

                # The line next to it is still the buyer's to change.
                other = self.line(kind, self.on_sale)
                fine = self.client.patch(f'/api/cart/{other.pk}/?session_key=expo_{kind}', {'quantity': 2}, format='json')
                self.assertEqual(fine.status_code, 200, fine.content)
                self.assertIs(fine.json()['available'], True)

    def test_cart_remove_still_works(self):
        for kind, product in self.gone.items():
            with self.subTest(kind):
                line = self.line(kind, product)
                response = self.client.delete(f'/api/cart/{line.pk}/?session_key=expo_{kind}')
                self.assertEqual(response.status_code, 204)
                self.assertFalse(CartItem.objects.filter(pk=line.pk).exists())

    # -- buying ---------------------------------------------------------------

    def test_checkout_quote(self):
        for kind, product in self.gone.items():
            with self.subTest(kind):
                body = {'session_key': f'expo_{kind}', 'delivery_method': 'pickup_store'}
                response = self.client.post('/api/checkout/quote/', body, format='json')
                self.assertEqual(response.status_code, 400, response.content)
                self.assertNotIn(str(product.price), response.content.decode())

                # Without that line the same cart is quoted: it was the line, not the cart.
                self.line(kind, product).delete()
                self.assertEqual(self.client.post('/api/checkout/quote/', body, format='json').status_code, 200)

    def test_web_checkout(self):
        for kind, product in self.gone.items():
            with self.subTest(kind):
                with patch(SESSION_TOKEN_PATH, return_value=FAKE_SESSION_TOKEN), override_settings(**IZIPAY_TEST_SETTINGS):
                    response = self.client.post(
                        '/api/payments/create-checkout-session/', {'session_key': f'expo_{kind}', **CHECKOUT}, format='json')
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn('ya no está disponible', response.content.decode())
                self.assertNotIn(str(product.price), response.content.decode())
                self.assertFalse(Order.objects.filter(items__product=product).exclude(pk=self.order.pk).exists())

    # -- what the buyer already bought ---------------------------------------

    def test_my_orders_keep_the_line_and_say_nothing_else_of_the_product(self):
        """
        Review O2 (older than this phase). The buyer's order history nested the
        LIVE product: for one that left the web, today's price, its description,
        its address, the company's stock and its gallery. The line of the order
        — what was bought, how many, at what price — is the buyer's; the
        product as it is today is not on sale to anybody.
        """
        from store.serializers import ProductSerializer

        api = _client(self.buyer)
        listed = api.get('/api/orders/')
        detail = api.get(f'/api/orders/{self.order.pk}/')
        self.assertEqual((listed.status_code, detail.status_code), (200, 200))
        body = listed.json()
        orders = body['results'] if isinstance(body, dict) else body

        for response, order in ((listed, orders[0]), (detail, detail.json())):
            lines = {item['product']['id']: item for item in order['items']}
            # What IS on sale keeps its whole payload, field for field.
            sold = lines[self.on_sale.pk]
            self.assertEqual(list(sold['product']), ProductSerializer.Meta.fields)
            self.assertEqual((sold['product']['slug'], sold['product']['price']), ('cable-expo', '41.00'))
            for kind, product in self.gone.items():
                with self.subTest(kind):
                    line = lines[product.pk]
                    self.assertEqual(line['product'], {'id': product.pk, 'name': product.name, 'image_url': product.image_url})
                    # The line itself is intact: it is the buyer's record of the purchase.
                    self.assertEqual((line['quantity'], line['price'], list(line)), (3, '10.00', ['id', 'product', 'quantity', 'price']))
                    self.assert_says_nothing(response, product, name_allowed=True)
                    self.assertNotIn('"inventory":10', response.content.decode().split(f'"id":{product.pk}', 1)[1][:200])

    def test_a_line_of_an_order_and_a_line_of_a_cart_close_a_product_the_same_way(self):
        from store.serializers import closed_product_payload

        for _kind, product in self.gone.items():
            self.assertEqual(
                closed_product_payload(product), {'id': product.pk, 'name': product.name, 'image_url': product.image_url})

    # -- the app's API --------------------------------------------------------

    def test_v1_list_and_detail(self):
        base = f'/api/v1/storefront/{self.company.slug}/products/'
        response, found = self.slugs(base)
        self.assertIn('cable-expo', found)
        self.assertEqual(self.client.get(f'{base}cable-expo/').status_code, 200)
        for kind, product in self.gone.items():
            with self.subTest(kind):
                self.assertNotIn(product.slug, found)
                self.assert_says_nothing(response, product)
                detail = self.client.get(f'{base}{product.slug}/')
                self.assertEqual(detail.status_code, 404)
                self.assertNotIn(str(product.price), detail.content.decode())
                _response, searched = self.slugs(f'{base}?search={product.name.split()[0]}')
                self.assertEqual(searched, [])

    @override_settings(**IZIPAY_TEST_SETTINGS)
    def test_v1_checkout(self):
        user = User.objects.create_user('expo_app', 'expo_app@example.com', 'Pass123!')
        _v1_customer(self.company, user)
        app = APIClient()
        token = app.post(
            '/api/v1/auth/login/', {'email': 'expo_app@example.com', 'password': 'Pass123!'}, format='json',
        ).json()['access']
        app.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        url = f'/api/v1/customer/{self.company.slug}/checkout/'

        def body(slug, key):
            return {
                'items': [{'product_slug': slug, 'quantity': 1}], 'customer_name': 'Ana Torres',
                'customer_phone': '987654321', 'document_type': 'dni', 'document_number': '12345678',
                'delivery_method': 'pickup_store', 'receipt_type': 'boleta', 'accepted_terms': True,
                'accepted_warranty_policy': True, 'idempotency_key': key,
            }

        with patch(SESSION_TOKEN_PATH, return_value=FAKE_SESSION_TOKEN):
            unknown = app.post(url, body('no-existe-expo', 'expo-req-0000001'), format='json')
            for kind, product in self.gone.items():
                with self.subTest(kind):
                    response = app.post(url, body(product.slug, f'expo-req-{kind:0>8}'), format='json')
                    # Exactly what a slug nobody has answers: it is not «gone», it is not there.
                    self.assertEqual(response.status_code, unknown.status_code, response.content)
                    self.assertGreaterEqual(response.status_code, 400)
                    self.assertNotIn(product.name, response.content.decode())
                    self.assertNotIn(str(product.price), response.content.decode())
                    self.assertFalse(Order.objects.filter(items__product=product).exclude(pk=self.order.pk).exists())
            # The same buyer buys what the shop does sell.
            fine = app.post(url, body('cable-expo', 'expo-req-0000002'), format='json')
            self.assertLess(fine.status_code, 300, fine.content)


# =============================================================================
# One lock order for everything that writes products in bulk
# =============================================================================

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def product_locks(queries):
    """The statements that LOCK product rows, in the order they ran."""
    return [q['sql'] for q in queries if 'FOR UPDATE' in q['sql'] and 'FROM "store_product"' in q['sql']]


def product_workbook(rows):
    book = Workbook()
    page = book.active
    page.title = 'Productos'
    page.append(import_exports.PRODUCT_TEMPLATE_HEADERS)
    for row in rows:
        page.append(row)
    out = io.BytesIO()
    book.save(out)
    return SimpleUploadedFile('productos.xlsx', out.getvalue(), content_type=XLSX)


class LockOrderTest(_Base):
    """
    Review N7, M04. The bulk change, the picture load and the product load all
    hold several product rows at once. If each takes them in its own order, two
    of them wait on each other and the database kills one (a 500). So all three
    take them LOWEST ID FIRST — pinned here on the locking statement itself.
    """

    def test_the_bulk_change_locks_its_products_lowest_id_first(self):
        products = [self.product(f'Funda {n}') for n in range(3)]
        with CaptureQueriesContext(db_connection) as queries:
            self.post({'action': 'hide', 'ids': [p.pk for p in reversed(products)]})
        locks = product_locks(queries)
        self.assertEqual(len(locks), 1, locks)
        self.assertIn('ORDER BY "store_product"."id" ASC', locks[0])

    def test_the_product_load_locks_every_product_it_edits_lowest_id_first_before_writing_any(self):
        low, high = self.product('Funda baja'), self.product('Funda alta')
        for product, code in ((low, 'BAJA-1'), (high, 'ALTA-1')):
            ProductBarcode.objects.create(company=self.company, product=product, code=code)
        staged = self.api.post('/api/admin/products/import/preview/', {'file': product_workbook([
            ['', 'ALTA-1', 'Funda alta renombrada', '', 11, 'Accesorios', '', '', ''],     # the HIGHER id first
            ['', 'BAJA-1', 'Funda baja renombrada', '', 11, 'Accesorios', '', '', ''],
        ])}, format='multipart')
        self.assertEqual((staged.status_code, staged.json()['counts']['update']), (201, 2), staged.content)
        job = BulkImportJob.objects.get(pk=staged.json()['id'])

        with CaptureQueriesContext(db_connection) as queries:
            import_services.apply_products(job=job, actor=self.manager)

        locks = product_locks(queries)
        # ONE statement takes both, in id order, and it runs before any row is written.
        self.assertIn(f'IN ({low.pk}, {high.pk})', locks[0])
        self.assertIn('ORDER BY "store_product"."id" ASC', locks[0])
        self.assertIn(f'"store_product"."company_id" = {self.company.pk}', locks[0])
        first_write = next(i for i, q in enumerate(queries) if q['sql'].startswith('UPDATE "store_product"'))
        first_lock = next(i for i, q in enumerate(queries) if q['sql'] == locks[0])
        self.assertLess(first_lock, first_write)
        self.assertEqual(
            list(Product.objects.filter(pk__in=[low.pk, high.pk]).order_by('pk').values_list('name', 'price')),
            [('Funda baja renombrada', Decimal('11.00')), ('Funda alta renombrada', Decimal('11.00'))])


class ProductLoadAgainstBulkChangeTest(TransactionTestCase):
    """Real locks, two connections: the two writers over the same products, in opposite orders."""

    def setUp(self):
        cache.clear()
        self.company = _saas_company('Tienda A', 'bulk-vis-orden', tax_id='20999999993')
        from store.company_provisioning import provision_company_access_defaults
        provision_company_access_defaults(self.company)
        self.user, _ = _p2d_member(self.company, 'bv_orden', ['company.view', 'products.view', 'products.manage'])
        category = Category.objects.create(company=self.company, name='Accesorios', slug='accesorios')
        self.low, self.high = (
            Product.objects.create(company=self.company, name=name, slug=slug, price=Decimal('10.00'),
                                   category=category, image_url=PICTURE)
            for name, slug in (('Baja', 'bv-orden-baja'), ('Alta', 'bv-orden-alta'))
        )
        ProductBarcode.objects.create(company=self.company, product=self.low, code='BAJA-1')
        ProductBarcode.objects.create(company=self.company, product=self.high, code='ALTA-1')

    def test_a_load_that_names_the_higher_id_first_and_a_bulk_change_do_not_deadlock(self):
        staged = _client(self.user).post('/api/admin/products/import/preview/', {'file': product_workbook([
            ['', 'ALTA-1', 'Alta nueva', '', 11, 'Accesorios', '', '', ''],
            ['', 'BAJA-1', 'Baja nueva', '', 11, 'Accesorios', '', '', ''],
        ])}, format='multipart')
        self.assertEqual(staged.status_code, 201, staged.content)
        job_id = staged.json()['id']

        first_row_done, results = threading.Event(), {}
        original = import_services._place_row_images

        def slow(row, product, images, actor):
            # The load has written its FIRST row and holds whatever it locked for it.
            if not first_row_done.is_set():
                first_row_done.set()
                time.sleep(1.5)        # long enough for the bulk change to take what is free and wait
            return original(row, product, images, actor)

        def run(name, call, wait=False):
            try:
                if wait:
                    first_row_done.wait(10)
                results[name] = call().status_code
            except Exception as failed:  # noqa: BLE001 — a deadlock arrives as an exception
                results[name] = failed
            finally:
                db_connection.close()

        ids = [self.low.pk, self.high.pk]
        with patch.object(import_services, '_place_row_images', side_effect=slow):
            threads = [
                threading.Thread(target=run, args=(
                    'load', lambda: _client(self.user).post(f'/api/admin/products/import/{job_id}/apply/'))),
                threading.Thread(target=run, args=(
                    'bulk', lambda: _client(self.user).post(URL, {'action': 'hide', 'ids': ids}, format='json'), True)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)

        self.assertEqual(results, {'load': 200, 'bulk': 200})
        self.assertEqual(
            list(Product.objects.filter(pk__in=ids).order_by('pk').values_list('name', 'is_published_online')),
            [('Baja nueva', False), ('Alta nueva', False)])


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
