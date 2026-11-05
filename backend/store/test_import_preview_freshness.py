"""
IMPORT-STALE-01 — a preview is a photograph, and an old photograph is not applied.

Decision A of the owner: «Rechazar previews caducados o incompatibles con el
estado actual del inventario», with the check and the write protected against
concurrency.

    EXPIRY        every import kind refuses a preview older than
                  `IMPORT_PREVIEW_TTL_SECONDS` (`preview_expired`, HTTP 409).
    RELEVANT      stock: the shelf must still hold what the preview showed
    CHANGE        (`current_preview`), read under the row lock; otherwise the
                  whole file is refused (`preview_stale`, 409) and nothing is
                  written. Products: a product the job would edit must not have
                  been saved since the preview (`updated_at`).
    CONCURRENCY   two applies of one job apply once; two previews of one shelf
                  applied at once: the second is stale.
"""
import threading
from datetime import timedelta

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from store import image_import_services, inventory_services, supplier_offer_import, unit_import_services
from store import import_services as products_import
from store import stock_import_services as stock_import
from store.models import BulkImportJob, Product, ProductBarcode, StockMovement
from store.tests import (
    _c1_product, _c14_products_workbook, _c14_stock_workbook, _c14_upload, _p2d_branch, _p2d_member,
    _p3_company,
)

TTL = 3600


def _age(job, seconds):
    """Move the preview `seconds` into the past, as if the operator had gone to lunch."""
    BulkImportJob.objects.filter(pk=job.pk).update(created_at=timezone.now() - timedelta(seconds=seconds))
    job.refresh_from_db()
    return job


class _Shop:
    def build(self, slug):
        cache.clear()
        self.company = _p3_company(slug, f'Tienda {slug}')
        self.branch = _p2d_branch(self.company, 'Principal')
        self.user, _ = _p2d_member(
            self.company, f'{slug}_admin',
            ['company.view', 'inventory.adjust', 'inventory.view', 'products.manage'],
        )
        self.product = _c1_product(self.company, 'Cable Lightning', '49.90')
        ProductBarcode.objects.create(company=self.company, product=self.product, code='C000001')
        self.api = APIClient()
        self.api.force_authenticate(user=self.user)

    def stock_file(self, quantity, *, code='C000001', name='Cable Lightning'):
        return _c14_upload(_c14_stock_workbook([{
            'ID': 1, 'CODIGO': code, 'CODIGO EAN': '', 'NOMBRE': name, 'ALMACEN 1 - 11416': quantity,
        }]), 'Carga_Masiva_(inventarios)-2.xlsx')

    def preview_stock(self, quantity, mode=BulkImportJob.MODE_RECONCILE):
        return stock_import.preview_stock(
            company=self.company, actor=self.user, upload=self.stock_file(quantity),
            filename='inv.xlsx', branch_map={4: self.branch.pk}, mode=mode,
        )

    def preview_products(self, price):
        return products_import.preview_products(
            company=self.company, actor=self.user,
            upload=_c14_upload(_c14_products_workbook([{
                'Código de barras': '', 'Código': 'C000001', 'Nombre': 'Cable Lightning',
                'Precio venta - 11834': price,
            }]), 'Carga_Masiva_(Productos).xlsx'),
            filename='Carga_Masiva_(Productos).xlsx',
        )

    def move(self, movement_type, quantity):
        inventory_services.create_stock_movement(
            branch=self.branch, product_id=self.product.pk, movement_type=movement_type,
            quantity=quantity, reason='movimiento de prueba',
        )

    def quantity(self):
        return inventory_services.branch_quantity(self.branch, self.product)

    def imported_movements(self):
        return StockMovement.objects.filter(reference_type='bulk_import').count()


@override_settings(IMPORT_PREVIEW_TTL_SECONDS=TTL)
class PreviewExpiryTest(_Shop, TestCase):
    """Every kind of import refuses a preview older than the TTL, before writing anything."""

    def setUp(self):
        self.build('fresh-ttl')

    def test_a_stock_preview_inside_the_ttl_still_applies(self):
        self.move(StockMovement.INITIAL_STOCK, 10)
        job = _age(self.preview_stock(15), TTL - 60)
        _job, changed, _movements = stock_import.apply_stock(job=job, actor=self.user)
        self.assertTrue(changed)
        self.assertEqual(self.quantity(), 15)

    def test_an_expired_stock_preview_is_refused_and_writes_nothing(self):
        self.move(StockMovement.INITIAL_STOCK, 10)
        job = _age(self.preview_stock(15), TTL + 1)
        with self.assertRaises(stock_import.StockImportError) as caught:
            stock_import.apply_stock(job=job, actor=self.user)
        self.assertEqual(caught.exception.code, 'preview_expired')
        self.assertIn('caducó', str(caught.exception))
        self.assertEqual(self.quantity(), 10)
        self.assertEqual(self.imported_movements(), 0)
        job.refresh_from_db()
        self.assertEqual(job.status, BulkImportJob.PREVIEWED)

    def test_an_expired_product_preview_is_refused_and_writes_nothing(self):
        job = _age(self.preview_products(59.90), TTL + 1)
        with self.assertRaises(products_import.ImportError_) as caught:
            products_import.apply_products(job=job, actor=self.user)
        self.assertEqual(caught.exception.code, 'preview_expired')
        self.product.refresh_from_db()
        self.assertEqual(str(self.product.price), '49.90')

    def _empty_job(self, import_type, **extra):
        job = BulkImportJob.objects.create(company=self.company, import_type=import_type, created_by=self.user, **extra)
        return _age(job, TTL + 1)

    def test_an_expired_unit_preview_is_refused(self):
        job = self._empty_job(BulkImportJob.UNITS)
        with self.assertRaises(unit_import_services.UnitImportError) as caught:
            unit_import_services.apply_units(job=job, actor=self.user)
        self.assertEqual(caught.exception.code, 'preview_expired')

    def test_an_expired_image_preview_is_refused(self):
        job = self._empty_job(BulkImportJob.PRODUCT_IMAGES)
        with self.assertRaises(image_import_services.ImageImportError) as caught:
            image_import_services.apply_images(job=job, actor=self.user)
        self.assertEqual(caught.exception.code, 'preview_expired')

    def test_an_expired_supplier_offer_preview_is_refused(self):
        job = self._empty_job(BulkImportJob.SUPPLIER_OFFERS)
        with self.assertRaises(supplier_offer_import.OfferImportError) as caught:
            supplier_offer_import.apply(job=job, actor=self.user)
        self.assertEqual(caught.exception.code, 'preview_expired')

    def test_an_applied_job_answers_its_result_again_even_after_the_ttl(self):
        """The second click of an applied job is not a new import: no expiry error, no second write."""
        self.move(StockMovement.INITIAL_STOCK, 10)
        job = self.preview_stock(15)
        stock_import.apply_stock(job=job, actor=self.user)
        job = _age(job, TTL + 1)
        _job, changed, _movements = stock_import.apply_stock(job=job, actor=self.user)
        self.assertFalse(changed)
        self.assertEqual(self.imported_movements(), 1)

    @override_settings(IMPORT_PREVIEW_TTL_SECONDS=120)
    def test_the_ttl_is_the_setting(self):
        self.move(StockMovement.INITIAL_STOCK, 10)
        job = _age(self.preview_stock(15), 121)
        with self.assertRaises(stock_import.StockImportError) as caught:
            stock_import.apply_stock(job=job, actor=self.user)
        self.assertEqual(caught.exception.code, 'preview_expired')

    def test_an_expired_preview_is_no_longer_applicable_in_its_payload(self):
        job = self.preview_stock(15)
        self.assertTrue(job.is_applicable)
        self.assertFalse(_age(job, TTL + 1).is_applicable)

    def test_the_api_answers_409_with_the_code_for_stock_and_products(self):
        stock = _age(self.preview_stock(15), TTL + 1)
        res = self.api.post(f'/api/admin/inventory/import/{stock.pk}/apply/', {}, format='json')
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'preview_expired')
        self.assertIn('caducó', res.json()['detail'])

        products = _age(self.preview_products(59.90), TTL + 1)
        res = self.api.post(f'/api/admin/products/import/{products.pk}/apply/', {}, format='json')
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'preview_expired')


@override_settings(IMPORT_PREVIEW_TTL_SECONDS=TTL)
class StockPreviewStaleTest(_Shop, TestCase):
    """The shelf must still hold what the preview showed, or nothing of the file is written."""

    def setUp(self):
        self.build('fresh-stale')

    def test_a_sale_after_the_preview_refuses_the_whole_file(self):
        self.move(StockMovement.INITIAL_STOCK, 10)
        job = self.preview_stock(15)
        self.move(StockMovement.SALE_EXIT, 2)
        with self.assertRaises(stock_import.StockImportError) as caught:
            stock_import.apply_stock(job=job, actor=self.user)
        self.assertEqual(caught.exception.code, 'preview_stale')
        self.assertIn('Cable Lightning', str(caught.exception))
        self.assertEqual(caught.exception.extra['affected'], 1)
        self.assertEqual(self.quantity(), 8)
        self.assertEqual(self.imported_movements(), 0)
        job.refresh_from_db()
        self.assertEqual(job.status, BulkImportJob.PREVIEWED)

    def test_a_row_that_matched_at_preview_is_stale_too(self):
        """«No change» at 10 is a claim that the shelf holds 10: applying it after a sale would write it back up."""
        self.move(StockMovement.INITIAL_STOCK, 10)
        job = self.preview_stock(10)
        self.assertEqual(job.rows_no_change, 1)
        self.move(StockMovement.SALE_EXIT, 1)
        with self.assertRaises(stock_import.StockImportError) as caught:
            stock_import.apply_stock(job=job, actor=self.user)
        self.assertEqual(caught.exception.code, 'preview_stale')
        self.assertEqual(self.quantity(), 9)

    def test_one_stale_row_stops_the_rows_that_did_not_change(self):
        self.move(StockMovement.INITIAL_STOCK, 10)
        other = _c1_product(self.company, 'Funda', '19.90')
        ProductBarcode.objects.create(company=self.company, product=other, code='C000002')
        job = stock_import.preview_stock(
            company=self.company, actor=self.user,
            upload=_c14_upload(_c14_stock_workbook([
                {'ID': 1, 'CODIGO': 'C000001', 'CODIGO EAN': '', 'NOMBRE': 'Cable Lightning', 'ALMACEN 1 - 11416': 15},
                {'ID': 2, 'CODIGO': 'C000002', 'CODIGO EAN': '', 'NOMBRE': 'Funda', 'ALMACEN 1 - 11416': 4},
            ])),
            filename='inv.xlsx', branch_map={4: self.branch.pk},
        )
        self.move(StockMovement.SALE_EXIT, 1)
        with self.assertRaises(stock_import.StockImportError):
            stock_import.apply_stock(job=job, actor=self.user)
        self.assertEqual(inventory_services.branch_quantity(self.branch, other), 0)
        self.assertEqual(self.imported_movements(), 0)

    def test_a_change_that_nets_back_to_the_preview_still_applies(self):
        """The shelf holds what the operator saw: the count still describes it."""
        self.move(StockMovement.INITIAL_STOCK, 10)
        job = self.preview_stock(15)
        self.move(StockMovement.SALE_EXIT, 2)
        self.move(StockMovement.PURCHASE_ENTRY, 2)
        _job, changed, _movements = stock_import.apply_stock(job=job, actor=self.user)
        self.assertTrue(changed)
        self.assertEqual(self.quantity(), 15)

    def test_the_api_answers_409_with_the_affected_products(self):
        self.move(StockMovement.INITIAL_STOCK, 10)
        job = self.preview_stock(15)
        self.move(StockMovement.SALE_EXIT, 2)
        res = self.api.post(f'/api/admin/inventory/import/{job.pk}/apply/', {}, format='json')
        self.assertEqual(res.status_code, 409, res.content)
        body = res.json()
        self.assertEqual(body['code'], 'preview_stale')
        self.assertEqual(body['affected'], 1)
        self.assertEqual(body['products'], ['Cable Lightning'])
        self.assertEqual(self.quantity(), 8)


@override_settings(IMPORT_PREVIEW_TTL_SECONDS=TTL)
class ProductPreviewStaleTest(_Shop, TestCase):
    """A product edited after the preview is not overwritten by the file the operator reviewed before it."""

    def setUp(self):
        self.build('fresh-prod')

    def test_a_product_edited_after_the_preview_refuses_the_file(self):
        job = self.preview_products(59.90)
        self.assertEqual(job.rows_update, 1)
        product = Product.objects.get(pk=self.product.pk)
        product.price = '45.00'
        product.save()
        with self.assertRaises(products_import.ImportError_) as caught:
            products_import.apply_products(job=job, actor=self.user)
        self.assertEqual(caught.exception.code, 'preview_stale')
        self.assertIn('Cable Lightning', str(caught.exception))
        product.refresh_from_db()
        self.assertEqual(str(product.price), '45.00')

    def test_a_stock_movement_does_not_make_a_product_preview_stale(self):
        """Stock lives on the shelf, not on the product: a sale is not an edit of the catalogue."""
        job = self.preview_products(59.90)
        self.move(StockMovement.INITIAL_STOCK, 3)
        products_import.apply_products(job=job, actor=self.user)
        self.product.refresh_from_db()
        self.assertEqual(str(self.product.price), '59.90')


@override_settings(IMPORT_PREVIEW_TTL_SECONDS=TTL)
class StockApplyRaceTest(_Shop, TransactionTestCase):
    """Real threads on PostgreSQL: the check and the write happen under the same row locks."""

    def setUp(self):
        if connection.vendor == 'sqlite':
            self.skipTest('Row locks: PostgreSQL only.')
        self.build('fresh-race')
        self.move(StockMovement.INITIAL_STOCK, 10)

    def race(self, *steps):
        barrier, outcomes = threading.Barrier(len(steps)), {}

        def run(name, step):
            try:
                barrier.wait(timeout=10)
                outcomes[name] = step()
            except Exception as exc:  # noqa: BLE001 - the outcome is the exception
                outcomes[name] = exc
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=step) for step in steps]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(set(outcomes), {name for name, _ in steps}, outcomes)
        return outcomes

    def apply(self, job_id):
        def step():
            job = BulkImportJob.objects.get(pk=job_id)
            _job, changed, _movements = stock_import.apply_stock(job=job, actor=self.user)
            return changed
        return step

    def test_two_applies_of_the_same_job_apply_once(self):
        job = self.preview_stock(15)
        outcomes = self.race(('a', self.apply(job.pk)), ('b', self.apply(job.pk)))
        self.assertEqual(sorted(outcomes.values()), [False, True], outcomes)
        self.assertEqual(self.quantity(), 15)
        self.assertEqual(self.imported_movements(), 1)

    def test_two_previews_of_one_shelf_applied_at_once_the_second_is_stale(self):
        first, second = self.preview_stock(15), self.preview_stock(12)
        outcomes = self.race(('first', self.apply(first.pk)), ('second', self.apply(second.pk)))
        won = [name for name, value in outcomes.items() if value is True]
        lost = [value for value in outcomes.values() if isinstance(value, stock_import.StockImportError)]
        self.assertEqual(len(won), 1, outcomes)
        self.assertEqual([error.code for error in lost], ['preview_stale'], outcomes)
        self.assertEqual(self.quantity(), {'first': 15, 'second': 12}[won[0]])
        self.assertEqual(self.imported_movements(), 1)

    def test_an_apply_against_a_sale_never_overwrites_the_sale(self):
        """Whichever commits first, the shelf ends at «the count» or at «the count minus the sale» — never resurrected."""
        job = self.preview_stock(15)

        def sell():
            self.move(StockMovement.SALE_EXIT, 2)
            return 'sold'

        outcomes = self.race(('apply', self.apply(job.pk)), ('sale', sell))
        self.assertEqual(outcomes['sale'], 'sold', outcomes)
        if outcomes['apply'] is True:
            self.assertEqual(self.quantity(), 13)       # applied first, then the sale left
        else:
            self.assertEqual(outcomes['apply'].code, 'preview_stale', outcomes)
            self.assertEqual(self.quantity(), 8)        # sold first: the file is refused
            self.assertEqual(self.imported_movements(), 0)

