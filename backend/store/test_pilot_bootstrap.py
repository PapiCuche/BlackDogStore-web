"""
FRESH-PRODUCTION-DATA-01 · how the pilot store starts from a new database.

A new database, after `migrate`, already carries three sample products with
stock nobody owns: migration 0002 wrote them when the shop was a prototype, and
a migration that has run is not edited. `bootstrap_pilot_store` is the explicit
step that retires them before the real catalogue is loaded, and leaves the
pilot's approved categories in place.

What these tests hold it to:

  * it touches a sample product only while it is EXACTLY what the migration
    wrote and nothing points at it; anything else stops the whole run;
  * it is the pilot's and nobody else's: another company inherits nothing and
    loses nothing, even one that sells a product with the same slug;
  * a new database has all three sample products or none of them: one or two
    is a database somebody has been in, and it stops there too;
  * it never writes to a shop that is already working, not even to put back a
    category: it prepares a launch, it does not keep a configuration in line;
  * it does not run where DEBUG is on. It deletes rows, and a development
    database is the one place where nobody meant it to;
  * it creates no user and no membership. The first account of a new shop is
    made by hand, with `createsuperuser`.

The test database is a migrated one, so the sample catalogue is really there.
"""
from decimal import Decimal
from io import StringIO

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from store.company_provisioning import provision_company_access_defaults
from store.models import (
    Branch, BranchStock, CartItem, Category, Company, Customer, Membership, Product,
    StockMovement, StorefrontCampaign,
)

PILOT = 'black-dog-store'
SAMPLES = ('iphone-15-pro', 'apple-watch-series-9', 'airpods-pro-2')
APPROVED = ['iPhone', 'Mac', 'iPad', 'Apple Watch', 'Accesorios']


def run(*args):
    out = StringIO()
    call_command('bootstrap_pilot_store', *args, stdout=out)
    return out.getvalue()


class _Base(TestCase):
    def setUp(self):
        self.pilot = Company.objects.get(slug=PILOT)

    def products(self):
        return Product.objects.filter(company=self.pilot)

    def stock(self):
        return BranchStock.objects.filter(branch__company=self.pilot)

    def categories(self):
        return list(
            Category.objects.filter(company=self.pilot)
            .order_by('home_order', 'name').values_list('name', flat=True)
        )

    def assert_untouched(self):
        self.assertEqual(set(self.products().values_list('slug', flat=True)), set(SAMPLES))
        self.assertEqual(sorted(self.stock().values_list('quantity', flat=True)), [10, 15, 20])
        self.assertEqual(sorted(self.categories()), ['Accesorios', 'iPhone'])


class NewDatabaseTest(_Base):
    def test_a_migrated_database_carries_the_sample_catalogue(self):
        """The reason the command exists, stated as a fact that can stop being true."""
        self.assert_untouched()
        self.assertEqual(StockMovement.objects.count(), 0)
        self.assertEqual(get_user_model().objects.count(), 0)


class DryRunTest(_Base):
    def test_without_apply_it_says_what_it_would_do_and_does_nothing(self):
        said = run()
        self.assert_untouched()
        self.assertIn('SIMULACIÓN', said)
        for slug in SAMPLES:
            self.assertIn(slug, said)
        for name in ('Mac', 'iPad', 'Apple Watch'):
            self.assertIn(name, said)


class ApplyTest(_Base):
    def test_it_retires_the_sample_catalogue_and_its_stock(self):
        run('--apply')
        self.assertEqual(self.products().count(), 0)
        self.assertEqual(self.stock().count(), 0)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_the_pilot_has_exactly_the_approved_categories_in_their_order(self):
        run('--apply')
        self.assertEqual(self.categories(), APPROVED)

    def test_the_published_campaign_stays(self):
        before = list(StorefrontCampaign.objects.filter(company=self.pilot).values('title', 'status', 'slot'))
        run('--apply')
        self.assertEqual(list(StorefrontCampaign.objects.filter(company=self.pilot).values('title', 'status', 'slot')), before)
        self.assertEqual(
            StorefrontCampaign.objects.filter(company=self.pilot, title='iPhone 18 Pro Max', status='published').count(), 1,
        )

    def test_it_runs_as_production_does_and_creates_no_account(self):
        self.assertFalse(settings.DEBUG)
        run('--apply')
        self.assertEqual(get_user_model().objects.count(), 0)
        self.assertEqual(Membership.objects.count(), 0)

    def test_a_second_run_changes_nothing(self):
        run('--apply')
        categories = list(Category.objects.order_by('pk').values('pk', 'name', 'slug', 'home_order', 'company_id'))
        said = run('--apply')
        self.assertIn('Nada que hacer', said)
        self.assertEqual(list(Category.objects.order_by('pk').values('pk', 'name', 'slug', 'home_order', 'company_id')), categories)
        self.assertEqual(self.products().count(), 0)

    def test_once_done_it_stays_a_no_op_in_a_shop_that_is_already_working(self):
        run('--apply')
        Customer.objects.create(company=self.pilot, customer_type=Customer.TYPE_PERSON, first_name='Ana', last_name='Real')
        real = Product.objects.create(company=self.pilot, name='iPhone 17', slug='iphone-17', price=Decimal('4999.00'))
        self.assertIn('Nada que hacer', run('--apply'))
        self.assertTrue(Product.objects.filter(pk=real.pk).exists())

    def test_an_order_a_shop_gave_its_categories_is_kept(self):
        Category.objects.filter(company=self.pilot, slug='accesorios').update(home_order=1)
        run('--apply')
        self.assertEqual(Category.objects.get(company=self.pilot, slug='accesorios').home_order, 1)


class AbortTest(_Base):
    """Anything unexpected stops the whole run: no product, stock or category changes."""

    def assert_aborts(self, expected):
        with self.assertRaises(CommandError) as caught:
            run('--apply')
        self.assertIn(expected, str(caught.exception))
        self.assert_untouched()

    def test_a_sample_product_somebody_edited(self):
        Product.objects.filter(company=self.pilot, slug='iphone-15-pro').update(price=Decimal('5499.00'))
        self.assert_aborts('iphone-15-pro')

    def test_a_sample_product_whose_stock_moved(self):
        BranchStock.objects.filter(product__slug='airpods-pro-2', branch__company=self.pilot).update(quantity=19)
        with self.assertRaises(CommandError) as caught:
            run('--apply')
        self.assertIn('airpods-pro-2', str(caught.exception))
        self.assertEqual(self.products().count(), 3)
        self.assertEqual(sorted(self.categories()), ['Accesorios', 'iPhone'])

    def test_a_sample_product_something_points_at(self):
        CartItem.objects.create(session_key='guest-0001', product=Product.objects.get(company=self.pilot, slug='apple-watch-series-9'))
        self.assert_aborts('apple-watch-series-9')

    def test_a_database_that_has_already_been_used(self):
        Customer.objects.create(company=self.pilot, customer_type=Customer.TYPE_PERSON, first_name='Ana', last_name='Real')
        self.assert_aborts('actividad')

    def test_a_sample_product_somebody_deleted(self):
        """Two of three is not a new database, and not one this has already prepared."""
        gone = Product.objects.get(company=self.pilot, slug='airpods-pro-2')
        BranchStock.objects.filter(product=gone).delete()
        gone.delete()
        with self.assertRaises(CommandError) as caught:
            run('--apply')
        self.assertIn('2 de 3', str(caught.exception))
        self.assertEqual(set(self.products().values_list('slug', flat=True)), {'iphone-15-pro', 'apple-watch-series-9'})
        self.assertEqual(sorted(self.stock().values_list('quantity', flat=True)), [10, 15])
        self.assertEqual(sorted(self.categories()), ['Accesorios', 'iPhone'])

    def test_only_one_sample_product_left(self):
        for slug in ('airpods-pro-2', 'apple-watch-series-9'):
            BranchStock.objects.filter(product__slug=slug, branch__company=self.pilot).delete()
            Product.objects.filter(company=self.pilot, slug=slug).delete()
        with self.assertRaises(CommandError) as caught:
            run('--apply')
        self.assertIn('1 de 3', str(caught.exception))
        self.assertEqual(list(self.products().values_list('slug', flat=True)), ['iphone-15-pro'])
        self.assertEqual(list(self.stock().values_list('quantity', flat=True)), [10])
        self.assertEqual(sorted(self.categories()), ['Accesorios', 'iPhone'])

    def test_the_dry_run_says_it_would_stop_too(self):
        Product.objects.filter(company=self.pilot, slug='iphone-15-pro').update(name='iPhone 15 Pro Max')
        with self.assertRaises(CommandError) as caught:
            run()
        self.assertIn('iphone-15-pro', str(caught.exception))
        self.assertEqual(self.products().count(), 3)
        self.assertEqual(sorted(self.categories()), ['Accesorios', 'iPhone'])

    def test_without_the_pilot_company_there_is_nothing_to_prepare(self):
        Company.objects.filter(slug=PILOT).update(slug='otra-cosa')
        with self.assertRaises(CommandError) as caught:
            run('--apply')
        self.assertIn(PILOT, str(caught.exception))


class DevelopmentTest(_Base):
    """It deletes rows: it does not run where nobody meant it to."""

    @override_settings(DEBUG=True)
    def test_it_refuses_to_apply_with_debug_on(self):
        with self.assertRaises(CommandError) as caught:
            run('--apply')
        self.assertIn('DEBUG', str(caught.exception))
        self.assert_untouched()

    @override_settings(DEBUG=True)
    def test_it_refuses_even_the_dry_run_with_debug_on(self):
        with self.assertRaises(CommandError) as caught:
            run()
        self.assertIn('DEBUG', str(caught.exception))
        self.assert_untouched()


class WorkingShopTest(_Base):
    """Once the shop works, this command has nothing left to decide for it."""

    def setUp(self):
        super().setUp()
        run('--apply')
        Customer.objects.create(company=self.pilot, customer_type=Customer.TYPE_PERSON, first_name='Ana', last_name='Real')

    def snapshot(self):
        return list(Category.objects.order_by('pk').values('pk', 'name', 'slug', 'home_order', 'company_id'))

    def test_with_its_categories_in_place_there_is_nothing_to_do(self):
        before = self.snapshot()
        self.assertIn('Nada que hacer', run('--apply'))
        self.assertEqual(self.snapshot(), before)

    def test_a_category_the_shop_removed_is_not_put_back(self):
        Category.objects.filter(company=self.pilot, slug='mac').delete()
        before = self.snapshot()
        with self.assertRaises(CommandError) as caught:
            run('--apply')
        self.assertIn('actividad', str(caught.exception))
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(Category.objects.filter(company=self.pilot, slug='mac').exists())

    def test_an_order_the_shop_reset_is_not_rewritten(self):
        Category.objects.filter(company=self.pilot, slug='ipad').update(home_order=0)
        before = self.snapshot()
        with self.assertRaises(CommandError) as caught:
            run('--apply')
        self.assertIn('actividad', str(caught.exception))
        self.assertEqual(self.snapshot(), before)

    def test_the_dry_run_stops_there_too(self):
        Category.objects.filter(company=self.pilot, slug='mac').delete()
        with self.assertRaises(CommandError) as caught:
            run()
        self.assertIn('actividad', str(caught.exception))


class OtherCompanyTest(_Base):
    """What is the pilot's is not a default of the platform."""

    def setUp(self):
        super().setUp()
        self.other = Company.objects.create(name='Otra Tienda', legal_name='Otra Tienda S.A.C.', tax_id='20000000001', slug='otra-tienda')
        provision_company_access_defaults(self.other)

    def inherited(self):
        return (
            Category.objects.filter(company=self.other).count(),
            StorefrontCampaign.objects.filter(company=self.other).count(),
            Product.objects.filter(company=self.other).count(),
        )

    def test_a_new_company_inherits_no_category_campaign_or_product(self):
        self.assertEqual(self.inherited(), (0, 0, 0))
        run('--apply')
        self.assertEqual(self.inherited(), (0, 0, 0))

    def test_another_company_selling_the_same_thing_keeps_it(self):
        branch = Branch.objects.filter(company=self.other).first()
        category = Category.objects.create(company=self.other, name='iPhone', slug='iphone')
        product = Product.objects.create(
            company=self.other, name='iPhone 15 Pro', slug='iphone-15-pro', price=Decimal('5599.00'),
            inventory=10, category=category, description='El smartphone más avanzado de Apple.',
        )
        BranchStock.objects.create(branch=branch, product=product, quantity=10)
        run('--apply')
        self.assertTrue(Product.objects.filter(pk=product.pk).exists())
        self.assertEqual(BranchStock.objects.get(product=product).quantity, 10)
        self.assertEqual(list(Category.objects.filter(company=self.other).values_list('slug', flat=True)), ['iphone'])
        self.assertEqual(self.products().count(), 0)
