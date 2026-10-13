"""
INVENTORY-UNIFIED-01 · parte 4 — imágenes en lote, sin libro de productos.

    SE AÑADEN IMÁGENES A PRODUCTOS QUE YA EXISTEN. NO SE CREA NI SE EDITA NINGUNO.

Muchos archivos o un ZIP, y cada archivo va a un producto por su NOMBRE:
`<código>.png` es de ese producto; `<código>_2`, `<código>-3`, `<código> (4)`
dan el orden y la de número más bajo es la principal. El código se busca como
lo busca la carga de productos —código interno y códigos de barras, exacto— y
nunca por el nombre del producto. Lo que no se puede decidir no se adivina: se
dice y no se carga.

Lo que estas pruebas fijan:

  * la gramática de los nombres y la alternativa con un libro «Imágenes»;
  * que previsualizar no coloca nada y dice qué se añade, qué es principal,
    qué ya estaba, qué no tiene producto y qué pasa del límite;
  * añadir y reemplazar (éste, sólo con su palabra de confirmación);
  * aplicar todo o nada, una vez, y sólo sobre la galería;
  * empresa, permiso y cada protección del ZIP, también por este camino.
"""
import io
import shutil
import tempfile
import threading
import zipfile

import openpyxl
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection as db_connection
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from store import image_import_services as image_import
from store import storefront_media
from store.models import (
    AdminAuditLog, BranchStock, BulkImportJob, Category, Product, ProductBarcode, ProductImage,
    StockMovement, StorefrontImage,
)
from store.test_import_media import archive, picture
from store.test_storefront_media import jpeg, png_with_transparency, webp
from store.tests import _p2d_branch, _p2d_member, _p2d_stock, _p3_company, _saas_company

_ROOT = tempfile.mkdtemp(prefix='image-import-')

PREVIEW = '/api/admin/products/import/preview/'
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
WORD = 'REEMPLAZAR'

# Four pictures that are not the same picture.
ONE, TWO, THREE, FOUR = png_with_transparency(), jpeg((100, 60)), webp((90, 90)), png_with_transparency((64, 64))


def tearDownModule():
    shutil.rmtree(_ROOT, ignore_errors=True)


def apply_url(job_id):
    return f'/api/admin/products/images/import/{job_id}/apply/'


def image_book(rows, headers=('Código', 'Imagen principal', 'Imágenes'), sheet='Imágenes'):
    book = openpyxl.Workbook()
    page = book.active
    page.title = sheet
    page.append(list(headers))
    for row in rows:
        page.append(list(row))
    out = io.BytesIO()
    book.save(out)
    return SimpleUploadedFile('imagenes.xlsx', out.getvalue(), content_type=XLSX)


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@override_settings(EVIDENCE_STORAGE_BACKEND='filesystem', EVIDENCE_STORAGE_ROOT=_ROOT)
class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('img-a', 'Tienda A')
        self.other = _p3_company('img-b', 'Tienda B')
        self.manager, _ = _p2d_member(self.company, 'img_gestora', ['company.view', 'products.view', 'products.manage'])
        self.viewer, _ = _p2d_member(self.company, 'img_lectora', ['company.view', 'products.view'])
        self.outsider, _ = _p2d_member(self.other, 'img_ajena', ['company.view', 'products.view', 'products.manage'])
        self.api = _client(self.manager)
        self.phone = self.product('Teléfono', 'COD-A1', ean='7751234567892')
        self.cable = self.product('Cable', 'COD-A2')
        # Another shop with the SAME code on its own product, and one of its own.
        self.foreign_twin = self.product('Teléfono ajeno', 'COD-A1', company=self.other)
        self.foreign = self.product('Funda ajena', 'COD-B1', company=self.other)

    def product(self, name, code, *, ean=None, company=None):
        company = company or self.company
        product = Product.objects.create(
            company=company, name=name, slug=f'img-{company.pk}-{code.lower()}', price='100.00')
        ProductBarcode.objects.create(company=company, product=product, code=code, symbology=ProductBarcode.INTERNAL)
        if ean:
            ProductBarcode.objects.create(company=company, product=product, code=ean, symbology=ProductBarcode.EAN13)
        return product

    def preview(self, images=(), *, client=None, images_zip=None, book=None, **fields):
        data = {'kind': 'images', **fields}
        if images:
            data['images'] = list(images)
        if images_zip is not None:
            data['images_zip'] = images_zip
        if book is not None:
            data['file'] = book
        return (client or self.api).post(PREVIEW, data, format='multipart')

    def staged(self, images=(), **kwargs):
        response = self.preview(images, **kwargs)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def apply(self, job_id, *, client=None, **body):
        with self.captureOnCommitCallbacks(execute=True):
            return (client or self.api).post(apply_url(job_id), body, format='json')

    def loaded(self, images=(), **kwargs):
        """Preview and apply; the applied job."""
        confirm = {'confirm': WORD} if kwargs.get('mode') == 'replace' else {}
        body = self.staged(images, **kwargs)
        self.assertEqual(body['counts']['error'], 0, body['rows'])
        response = self.apply(body['id'], **confirm)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    @staticmethod
    def row(body, product):
        return next(row for row in body['rows'] if row['data'].get('product_id') == product.pk)

    @staticmethod
    def unmatched(body):
        return {row['data']['file']: row['data']['reason'] for row in body['rows'] if row['action'] == 'skip'}

    @staticmethod
    def names(row):
        return [image['name'] for image in row['data']['images']]

    def gallery(self, product):
        return list(ProductImage.objects.filter(product=product).order_by('sort_order', 'pk'))


# =============================================================================
# The grammar of a file name
# =============================================================================

class FileNameTest(SimpleTestCase):
    def test_the_whole_name_is_a_code_and_a_trailing_number_is_an_order(self):
        cases = {
            'COD-A1.png': [('COD-A1', 0)],                # a digit glued to the code is the code
            'FUNDA.jpg': [('FUNDA', 0)],
            'FUNDA_2.jpeg': [('FUNDA_2', 0), ('FUNDA', 2)],
            'FUNDA-3.webp': [('FUNDA-3', 0), ('FUNDA', 3)],
            'FUNDA (4).png': [('FUNDA (4)', 0), ('FUNDA', 4)],
            'FUN-001_10.PNG': [('FUN-001_10', 0), ('FUN-001', 10)],
            '7751234567892.png': [('7751234567892', 0)],
            'carpeta/sub/FUNDA_1.png': [('FUNDA_1', 0), ('FUNDA', 1)],
            'FUNDA_.png': [('FUNDA_', 0)],
            'FUNDA_1234.png': [('FUNDA_1234', 0)],        # an order has at most three digits
            'FUNDA.frontal.png': [('FUNDA.frontal', 0)],  # only the last extension comes off
            '_2.png': [('_2', 0)],                        # a number alone names nothing
        }
        for name, expected in cases.items():
            with self.subTest(name):
                self.assertEqual(image_import.interpretations(name), expected)

    def test_the_rule_is_written_where_the_screen_and_the_docs_can_read_it(self):
        rule = image_import.naming_rule()
        self.assertEqual(rule['suffixes'], ['_2', '-2', ' (2)'])
        self.assertEqual(rule['confirm_word'], WORD)
        self.assertEqual(rule['workbook'], {'sheet': 'Imágenes', 'headers': ['Código', 'Imagen principal', 'Imágenes']})
        self.assertEqual(rule['max_per_product'], 12)


# =============================================================================
# Preview, by the names of the files
# =============================================================================

class PreviewByNameTest(_Base):
    def test_each_file_goes_to_the_product_its_name_says_in_the_order_its_name_says(self):
        body = self.staged([
            picture('COD-A1_2.png', THREE), picture('COD-A1.png', ONE), picture('COD-A1-1.jpg', TWO),
            picture('COD-A2 (5).png', FOUR),
        ])

        self.assertEqual(body['import_type'], 'product_images')
        self.assertEqual(body['options'], {'mode': 'add', 'use_main': False, 'source': 'names'})
        phone, cable = self.row(body, self.phone), self.row(body, self.cable)
        self.assertEqual(self.names(phone), ['COD-A1.png', 'COD-A1-1.jpg', 'COD-A1_2.png'])
        self.assertEqual((phone['action'], phone['data']['will_add'], phone['data']['will_remove']), ('update', 3, 0))
        # An empty gallery takes the first as its main picture, ticked or not.
        self.assertEqual(phone['data']['main'], 'COD-A1.png')
        self.assertEqual((phone['data']['name'], phone['data']['code']), ('Teléfono', 'COD-A1'))
        self.assertEqual(self.names(cable), ['COD-A2 (5).png'])
        self.assertEqual(body['counts'], {'total': 2, 'create': 0, 'update': 2, 'no_change': 0, 'skip': 0, 'error': 0})
        self.assertEqual(
            {key: body['summary'][key] for key in ('products', 'images_to_add', 'images_to_remove', 'unmatched')},
            {'products': 2, 'images_to_add': 4, 'images_to_remove': 0, 'unmatched': 0})

    def test_previewing_places_nothing_and_changes_no_product(self):
        before = list(Product.objects.order_by('pk').values())
        body = self.staged([picture('COD-A1.png', ONE)])

        self.assertEqual(ProductImage.objects.count(), 0)
        self.assertEqual(list(Product.objects.order_by('pk').values()), before)
        self.assertFalse(AdminAuditLog.objects.filter(action__startswith='product_image').exists())
        # The picture waits as an unplaced image of THIS company, with an address to show it.
        image = StorefrontImage.objects.get()
        self.assertEqual(image.company, self.company)
        self.assertEqual(storefront_media.reference_count(image.public_id), 0)
        self.assertEqual(self.row(body, self.phone)['data']['images'][0]['address'], image.url)

    def test_a_barcode_names_the_product_too(self):
        body = self.staged([picture('7751234567892.png', ONE), picture('7751234567892_2.png', TWO)])
        self.assertEqual(self.names(self.row(body, self.phone)), ['7751234567892.png', '7751234567892_2.png'])

    def test_a_code_that_ends_in_a_number_is_that_code_first(self):
        gadget = self.product('Adaptador', 'ADP-2')
        body = self.staged([picture('ADP-2.png', ONE), picture('ADP-2_2.png', TWO)])
        self.assertEqual(self.names(self.row(body, gadget)), ['ADP-2.png', 'ADP-2_2.png'])
        self.assertEqual(self.unmatched(body), {})

    def test_a_name_that_fits_two_products_is_not_guessed(self):
        self.product('Adaptador', 'ADPT')
        self.product('Adaptador dos', 'ADPT-2')
        body = self.staged([picture('ADPT-2.png', ONE), picture('COD-A2.png', TWO)])

        self.assertEqual(self.unmatched(body), {'ADPT-2.png': 'ambiguous_code'})
        skipped = next(row for row in body['rows'] if row['action'] == 'skip')
        self.assertIn('ADPT-2', skipped['warnings'][0])
        self.assertIn('dos productos', skipped['warnings'][0])
        self.assertEqual(body['summary']['unmatched'], 1)
        # It was not even stored: nothing will show it.
        self.assertEqual(StorefrontImage.objects.count(), 1)
        # The rest of the load is still good.
        self.assertEqual(body['counts']['error'], 0)
        self.assertTrue(body['is_applicable'])

    def test_a_file_whose_name_is_no_product_s_code_is_said_and_not_loaded(self):
        body = self.staged([
            picture('NO-EXISTE.png', ONE), picture('cod-a1.png', TWO), picture('Teléfono.png', THREE),
            picture('COD-A2.png', FOUR),
        ])
        # Exact, as the product load reads a code: another case is another code. And never by the product's NAME.
        self.assertEqual(self.unmatched(body), {
            'NO-EXISTE.png': 'no_product', 'cod-a1.png': 'no_product', 'Teléfono.png': 'no_product'})
        self.assertEqual(body['summary']['unmatched'], 3)
        self.assertEqual(body['summary']['media']['orphans'], 3)
        self.assertEqual(StorefrontImage.objects.count(), 1)

    def test_what_is_already_in_the_gallery_is_not_added_again(self):
        self.loaded([picture('COD-A1.png', ONE)])
        body = self.staged([picture('COD-A1.png', ONE), picture('COD-A1_2.png', TWO)])

        phone = self.row(body, self.phone)
        self.assertEqual([image['existing'] for image in phone['data']['images']], [True, False])
        self.assertEqual((phone['data']['will_add'], phone['data']['gallery_count']), (1, 1))
        self.assertEqual(body['summary']['media']['already_present'], 1)
        # It already has a main picture and nobody ticked «usar como principal».
        self.assertEqual(phone['data']['main'], '')

        same = self.staged([picture('COD-A1.png', ONE)])
        self.assertEqual((self.row(same, self.phone)['action'], same['counts']['no_change']), ('no_change', 1))

    def test_the_same_picture_twice_for_one_product_goes_in_once(self):
        body = self.staged([picture('COD-A1.png', ONE), picture('COD-A1_2.png', ONE)])
        phone = self.row(body, self.phone)
        self.assertEqual(self.names(phone), ['COD-A1.png'])
        self.assertIn('misma imagen', ' '.join(phone['warnings']))
        self.assertEqual(StorefrontImage.objects.count(), 1)

    @override_settings(PRODUCT_IMAGE_MAX_PER_PRODUCT=2)
    def test_more_than_a_product_admits_is_an_error_of_that_product(self):
        body = self.staged([picture('COD-A1.png', ONE), picture('COD-A1_2.png', TWO), picture('COD-A1_3.png', THREE),
                            picture('COD-A2.png', FOUR)])
        phone = self.row(body, self.phone)
        self.assertEqual(phone['action'], 'error')
        self.assertIn('hasta 2 imágenes', ' '.join(phone['errors']))
        self.assertFalse(body['is_applicable'])
        # A job that cannot be applied keeps no picture waiting.
        self.assertEqual(StorefrontImage.objects.count(), 0)

    @override_settings(PRODUCT_IMAGE_MAX_PER_PRODUCT=2)
    def test_what_the_gallery_already_holds_counts_towards_the_limit(self):
        self.loaded([picture('COD-A1.png', ONE), picture('COD-A1_2.png', TWO)])
        body = self.staged([picture('COD-A1.png', THREE)])
        phone = self.row(body, self.phone)
        self.assertEqual(phone['action'], 'error')
        self.assertIn('quedaría con 3', ' '.join(phone['errors']))
        # Replacing it instead leaves one: that fits.
        self.assertEqual(self.row(self.staged([picture('COD-A1.png', THREE)], mode='replace'), self.phone)['action'], 'update')

    def test_a_file_that_is_not_a_picture_is_an_error_of_its_product(self):
        body = self.staged([picture('COD-A1.png', b'esto no es una imagen'), picture('COD-A2.png', ONE)])
        phone = self.row(body, self.phone)
        self.assertEqual(phone['action'], 'error')
        self.assertIn('COD-A1.png', ' '.join(phone['errors']))
        self.assertEqual(self.row(body, self.cable)['action'], 'update')
        self.assertEqual(body['summary']['media']['invalid'], 1)
        self.assertEqual(StorefrontImage.objects.count(), 0)
        self.assertEqual(self.apply(body['id']).status_code, 400)

    def test_two_different_files_with_one_name_are_an_error_of_that_product(self):
        body = self.staged([picture('COD-A1.png', ONE)], images_zip=archive([('otra/COD-A1.png', TWO)]))
        self.assertEqual(self.row(body, self.phone)['action'], 'error')
        self.assertIn('más de un archivo', ' '.join(self.row(body, self.phone)['errors']))

    def test_what_is_not_an_image_file_is_ignored_and_said(self):
        notes = SimpleUploadedFile('notas.txt', b'hola', content_type='text/plain')
        body = self.staged([picture('COD-A1.png', ONE), notes])
        self.assertEqual(body['summary']['media']['ignored_names'], ['notas.txt'])
        self.assertEqual(body['counts']['error'], 0)

    def test_a_load_with_no_picture_is_refused(self):
        for response in (self.preview(), self.preview([SimpleUploadedFile('notas.txt', b'x')])):
            self.assertEqual(response.status_code, 400)
            self.assertIn('imagen', response.json()['detail'])
        self.assertEqual(BulkImportJob.objects.count(), 0)

    def test_what_it_does_not_understand_is_refused(self):
        mode = self.preview([picture('COD-A1.png', ONE)], mode='borrar')
        self.assertEqual((mode.status_code, mode.json()['detail']), (400, 'Modo de carga de imágenes no válido.'))
        kind = self.api.post(PREVIEW, {'kind': 'fotos', 'images': [picture('COD-A1.png', ONE)]}, format='multipart')
        self.assertEqual((kind.status_code, kind.json()['detail']), (400, 'Tipo de carga no válido.'))
        self.assertEqual(BulkImportJob.objects.count(), 0)
        self.assertEqual(StorefrontImage.objects.count(), 0)

    def test_a_zip_works_like_loose_files_and_both_may_come_together(self):
        body = self.staged(
            [picture('COD-A1.png', ONE)],
            images_zip=archive([('fotos/COD-A1_2.jpg', TWO), ('fotos/COD-A2.webp', THREE), ('__MACOSX/._COD-A2.webp', b'x')]))
        self.assertEqual(self.names(self.row(body, self.phone)), ['COD-A1.png', 'COD-A1_2.jpg'])
        self.assertEqual(self.names(self.row(body, self.cable)), ['COD-A2.webp'])
        self.assertEqual(body['summary']['media']['attached'], 3)


# =============================================================================
# Preview, with the «Imágenes» workbook
# =============================================================================

class PreviewByWorkbookTest(_Base):
    def test_the_workbook_says_which_files_are_whose_and_which_is_the_main_one(self):
        body = self.staged(
            [picture('frontal.png', ONE), picture('trasera.jpg', TWO), picture('cable.webp', THREE)],
            book=image_book([['COD-A1', 'trasera.jpg', 'frontal.png|trasera.jpg'], ['COD-A2', '', 'cable.webp']]),
        )
        self.assertEqual(body['options']['source'], 'workbook')
        phone = self.row(body, self.phone)
        # As the product load reads these two columns: the main one first.
        self.assertEqual(self.names(phone), ['trasera.jpg', 'frontal.png'])
        self.assertEqual(phone['data']['main'], 'trasera.jpg')
        self.assertEqual(self.names(self.row(body, self.cable)), ['cable.webp'])
        self.assertEqual(body['counts']['error'], 0)

    def test_with_a_workbook_the_file_names_decide_nothing(self):
        body = self.staged(
            [picture('frontal.png', ONE), picture('COD-A2.png', TWO)], book=image_book([['COD-A1', '', 'frontal.png']]))
        self.assertEqual([row['data'].get('product_id') for row in body['rows']], [self.phone.pk])
        self.assertEqual(body['summary']['media']['orphan_names'], ['COD-A2.png'])
        self.assertEqual(StorefrontImage.objects.count(), 1)

    def test_what_is_wrong_with_a_row_is_an_error_of_that_row(self):
        body = self.staged([picture('frontal.png', ONE)], book=image_book([
            ['NO-EXISTE', '', 'frontal.png'],          # 2: no such product
            ['COD-A1', '', 'falta.png'],               # 3: the file did not come
            ['COD-A2', '', ''],                        # 4: names no file
            ['', '', 'frontal.png'],                   # 5: names no product
        ]))
        errors = {row['row']: ' '.join(row['errors']) for row in body['rows']}
        self.assertIn('No existe un producto', errors[2])
        self.assertIn('falta.png', errors[3])
        self.assertIn('no está entre los archivos adjuntos', errors[3])
        self.assertIn('no indica ninguna imagen', errors[4])
        self.assertIn('Falta el código', errors[5])
        self.assertEqual(body['counts']['error'], 4)
        self.assertEqual(StorefrontImage.objects.count(), 0)

    def test_a_product_named_in_two_rows_is_an_error(self):
        body = self.staged([picture('a.png', ONE), picture('b.png', TWO)], book=image_book([
            ['COD-A1', '', 'a.png'], ['7751234567892', '', 'b.png']]))
        by_row = {row['row']: row for row in body['rows']}
        self.assertEqual((by_row[2]['action'], by_row[3]['action']), ('update', 'error'))
        self.assertIn('ya aparece en la fila 2', ' '.join(by_row[3]['errors']))

    def test_a_workbook_without_the_code_or_the_picture_columns_is_refused(self):
        for headers in (['Nombre', 'Imágenes'], ['Código', 'Nombre']):
            with self.subTest(headers=headers):
                response = self.preview([picture('a.png', ONE)], book=image_book([['x', 'y']], headers=headers))
                self.assertEqual(response.status_code, 400)
                self.assertIn('«Código»', response.json()['detail'])
                self.assertIn('«Imágenes»', response.json()['detail'])
        report = self.preview([picture('a.png', ONE)], book=image_book([['COD-A1', '', 'a.png']], sheet='Reporte de productos'))
        self.assertEqual(report.status_code, 400)
        self.assertIn('es un reporte', report.json()['detail'])
        self.assertEqual((BulkImportJob.objects.count(), StorefrontImage.objects.count()), (0, 0))

    def test_the_product_workbook_sent_here_adds_pictures_and_creates_nothing(self):
        """«Añadir imágenes después, sin recrear productos»: sus otras columnas no se leen."""
        book = image_book(
            [['COD-A1', 'Nombre cambiado', 9999, 'frontal.png', ''], ['NUEVO-1', 'Producto nuevo', 10, '', 'frontal.png']],
            headers=['Código', 'Nombre', 'Precio de venta', 'Imagen principal', 'Imágenes'], sheet='Productos')
        body = self.staged([picture('frontal.png', ONE)], book=book)
        self.assertEqual(self.row(body, self.phone)['action'], 'update')
        self.assertEqual(body['counts']['error'], 1)           # NUEVO-1 is nobody: it is not created
        self.assertEqual(Product.objects.filter(company=self.company).count(), 2)
        self.phone.refresh_from_db()
        self.assertEqual((self.phone.name, str(self.phone.price)), ('Teléfono', '100.00'))


# =============================================================================
# Apply: add
# =============================================================================

class ApplyAddTest(_Base):
    def test_the_pictures_enter_the_gallery_in_order_and_the_first_is_the_main_one(self):
        job = self.loaded([picture('COD-A1_2.png', TWO), picture('COD-A1.png', ONE), picture('COD-A2.png', THREE)])

        self.assertEqual(job['status'], 'applied')
        self.assertEqual(job['summary']['applied'], {'products': 2, 'images_added': 3, 'images_removed': 0, 'made_main': 0})
        first, second = self.gallery(self.phone)
        self.assertEqual((first.is_primary, second.is_primary), (True, False))
        self.assertLess(first.sort_order, second.sort_order)
        self.phone.refresh_from_db()
        self.assertEqual(self.phone.image_url, first.image_url)
        # Each one is a placed image now: counted, and safe from the daily sweep.
        for image in StorefrontImage.objects.all():
            self.assertGreaterEqual(storefront_media.reference_count(image.public_id), 1)
        StorefrontImage.objects.update(created_at='2020-01-01T00:00:00Z')
        with self.captureOnCommitCallbacks(execute=True):
            call_command('cleanup_storefront_images', '--older-than-hours', '24', verbosity=0)
        self.assertEqual(StorefrontImage.objects.count(), 3)

    def test_added_to_a_gallery_they_go_after_and_the_main_one_stays(self):
        self.loaded([picture('COD-A1.png', ONE)])
        main = self.gallery(self.phone)[0]

        self.loaded([picture('COD-A1.png', TWO), picture('COD-A1_2.png', THREE)])

        gallery = self.gallery(self.phone)
        self.assertEqual([image.pk for image in gallery][0], main.pk)
        self.assertEqual([image.is_primary for image in gallery], [True, False, False])
        self.phone.refresh_from_db()
        self.assertEqual(self.phone.image_url, main.image_url)

    def test_ticking_use_as_main_makes_the_first_uploaded_the_main_one(self):
        self.loaded([picture('COD-A1.png', ONE)])
        body = self.staged([picture('COD-A1.png', TWO), picture('COD-A1_2.png', THREE)], use_main='true')
        self.assertEqual((body['options']['use_main'], self.row(body, self.phone)['data']['main']), (True, 'COD-A1.png'))

        applied = self.apply(body['id']).json()

        self.assertEqual(applied['summary']['applied']['made_main'], 1)
        gallery = self.gallery(self.phone)
        self.assertEqual([image.is_primary for image in gallery], [False, True, False])
        self.phone.refresh_from_db()
        self.assertEqual(self.phone.image_url, gallery[1].image_url)

    def test_use_as_main_also_moves_the_main_one_to_a_picture_that_was_already_there(self):
        self.loaded([picture('COD-A1.png', ONE), picture('COD-A1_2.png', TWO)])
        self.loaded([picture('COD-A1.png', TWO)], use_main='true')
        self.assertEqual([image.is_primary for image in self.gallery(self.phone)], [False, True])
        self.assertEqual(len(self.gallery(self.phone)), 2)

    def test_use_as_main_on_the_picture_that_already_is_the_main_one_changes_nothing(self):
        self.loaded([picture('COD-A1.png', ONE), picture('COD-A1_2.png', TWO)])
        body = self.staged([picture('COD-A1.png', ONE)], use_main='true')
        phone = self.row(body, self.phone)
        self.assertEqual((phone['action'], phone['data']['main']), ('no_change', ''))
        self.assertEqual(self.apply(body['id']).json()['summary']['applied'],
                         {'products': 0, 'images_added': 0, 'images_removed': 0, 'made_main': 0})
        self.assertEqual(AdminAuditLog.objects.filter(action='product_image_primary_changed').count(), 0)

    def test_applying_twice_returns_the_first_result_and_adds_nothing(self):
        body = self.staged([picture('COD-A1.png', ONE)])
        first, second = self.apply(body['id']), self.apply(body['id'])

        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(second.json()['summary']['applied'], first.json()['summary']['applied'])
        self.assertEqual(len(self.gallery(self.phone)), 1)
        self.assertEqual(AdminAuditLog.objects.filter(action='product_images_import_applied').count(), 1)

    def test_loading_the_same_files_again_adds_nothing(self):
        self.loaded([picture('COD-A1.png', ONE), picture('COD-A1_2.png', TWO)])
        again = self.staged([picture('COD-A1.png', ONE), picture('COD-A1_2.png', TWO)])
        self.assertEqual(again['counts'], {'total': 1, 'create': 0, 'update': 0, 'no_change': 1, 'skip': 0, 'error': 0})
        self.assertEqual(self.apply(again['id']).json()['summary']['applied']['images_added'], 0)
        self.assertEqual(len(self.gallery(self.phone)), 2)
        self.assertEqual(AdminAuditLog.objects.filter(action='product_images_import_applied').count(), 1)

    def test_only_the_gallery_and_the_product_s_picture_address_are_written(self):
        branch = _p2d_branch(self.company, 'Centro')
        category = Category.objects.create(company=self.company, name='Teléfonos', slug='telefonos')
        Product.objects.filter(pk=self.phone.pk).update(category=category, is_published_online=False, description='Como estaba')
        _p2d_stock(branch, self.phone, 4)

        def snapshot():
            rows = Product.objects.filter(company=self.company).order_by('pk').values()
            return (
                [{k: v for k, v in row.items() if k not in ('image_url', 'updated_at')} for row in rows],
                list(BranchStock.objects.order_by('pk').values()),
                list(StockMovement.objects.order_by('pk').values()),
                list(ProductBarcode.objects.order_by('pk').values()),
                list(Category.objects.order_by('pk').values()),
            )

        before = snapshot()
        self.loaded([picture('COD-A1.png', ONE), picture('COD-A1_2.png', TWO)])
        self.loaded([picture('COD-A1.png', THREE)], mode='replace')

        self.assertEqual(snapshot(), before)
        self.assertEqual(Product.objects.filter(company=self.company).count(), 2)
        # The other product of the shop was not in either load: not even its date moved.
        self.assertEqual(ProductImage.objects.filter(product=self.cable).count(), 0)

    def test_one_audit_line_for_the_job_with_counts_and_no_file_name(self):
        job = self.loaded([picture('COD-A1.png', ONE), picture('NADIE.png', TWO)])

        entry = AdminAuditLog.objects.get(action='product_images_import_applied')
        self.assertEqual((entry.company, entry.actor, entry.target_type, entry.target_id), (
            self.company, self.manager, 'bulk_import_job', str(job['id'])))
        self.assertEqual(entry.metadata, {
            'job_id': job['id'], 'mode': 'add', 'source': 'names', 'products': 1, 'images_added': 1,
            'images_removed': 0, 'made_main': 0, 'unmatched': 1,
        })
        # The gallery keeps its own line per picture, as when one is uploaded by hand.
        placed = AdminAuditLog.objects.get(action='product_image_added')
        self.assertEqual((placed.company, placed.metadata['source']), (self.company, 'import'))

    def test_a_job_with_errors_is_not_applied(self):
        body = self.staged([picture('COD-A1.png', b'no es una imagen'), picture('COD-A2.png', ONE)])
        response = self.apply(body['id'])
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.json()['detail'])
        self.assertEqual(ProductImage.objects.count(), 0)

    def test_a_picture_that_expired_before_apply_takes_the_whole_load_back(self):
        body = self.staged([picture('COD-A1.png', ONE), picture('COD-A2.png', TWO)])
        StorefrontImage.objects.order_by('-pk').first().delete()

        response = self.apply(body['id'])

        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('Vuelve a previsualizar', response.json()['detail'])
        self.assertEqual(ProductImage.objects.count(), 0)
        self.assertEqual(BulkImportJob.objects.get(pk=body['id']).status, BulkImportJob.PREVIEWED)

    def test_a_catalogue_that_changed_since_the_preview_stops_the_load(self):
        body = self.staged([picture('COD-A1.png', ONE)])
        # The code now belongs to another product.
        ProductBarcode.objects.filter(company=self.company, code='COD-A1').update(product=self.cable)

        response = self.apply(body['id'])

        self.assertEqual(response.status_code, 400)
        self.assertIn('El catálogo cambió', response.json()['detail'])
        self.assertEqual(ProductImage.objects.count(), 0)

    def test_a_gallery_that_changed_since_the_preview_stops_the_load(self):
        body = self.staged([picture('COD-A1.png', ONE)])
        self.loaded([picture('COD-A1.png', TWO)])            # somebody else got there first

        response = self.apply(body['id'])

        self.assertEqual(response.status_code, 400)
        self.assertIn('galería', response.json()['detail'])
        self.assertIn('Teléfono', response.json()['detail'])
        self.assertEqual(len(self.gallery(self.phone)), 1)


# =============================================================================
# Apply: replace
# =============================================================================

class ApplyReplaceTest(_Base):
    def setUp(self):
        super().setUp()
        self.loaded([picture('COD-A1.png', ONE), picture('COD-A1_2.png', TWO), picture('COD-A2.png', FOUR)])
        self.old = [image.image_url for image in self.gallery(self.phone)]

    def test_the_preview_says_how_many_pictures_each_product_loses(self):
        body = self.staged([picture('COD-A1.png', THREE)], mode='replace')
        phone = self.row(body, self.phone)
        self.assertEqual(body['options']['mode'], 'replace')
        self.assertEqual((phone['data']['will_add'], phone['data']['will_remove'], phone['data']['main']), (1, 2, 'COD-A1.png'))
        self.assertEqual(body['summary']['images_to_remove'], 2)
        self.assertEqual(body['summary']['confirm_word'], WORD)
        # Nothing has gone yet.
        self.assertEqual([image.image_url for image in self.gallery(self.phone)], self.old)

    def test_without_the_word_nothing_is_replaced(self):
        body = self.staged([picture('COD-A1.png', THREE)], mode='replace')
        for confirm in ({}, {'confirm': ''}, {'confirm': 'reemplazar'}, {'confirm': 'SI'}, {'confirm': True}):
            with self.subTest(confirm=confirm):
                response = self.apply(body['id'], **confirm)
                self.assertEqual(response.status_code, 400)
                self.assertIn(WORD, response.json()['detail'])
        self.assertEqual([image.image_url for image in self.gallery(self.phone)], self.old)
        self.assertEqual(BulkImportJob.objects.get(pk=body['id']).status, BulkImportJob.PREVIEWED)

    def test_with_the_word_the_gallery_becomes_the_uploaded_set_in_its_order(self):
        body = self.staged([picture('COD-A1_2.png', FOUR), picture('COD-A1.png', THREE)], mode='replace')
        applied = self.apply(body['id'], confirm=WORD)

        self.assertEqual(applied.status_code, 200, applied.content)
        self.assertEqual(applied.json()['summary']['applied'], {'products': 1, 'images_added': 2, 'images_removed': 2, 'made_main': 0})
        gallery = self.gallery(self.phone)
        self.assertEqual(len(gallery), 2)
        self.assertEqual([image.is_primary for image in gallery], [True, False])
        self.assertFalse(set(self.old) & {image.image_url for image in gallery})
        self.phone.refresh_from_db()
        self.assertEqual(self.phone.image_url, gallery[0].image_url)
        # What nobody shows any more is gone from the store, and its removal is on the record.
        self.assertFalse(StorefrontImage.objects.filter(public_id__in=[
            storefront_media.managed_public_id(address) for address in self.old]).exists())
        self.assertEqual(AdminAuditLog.objects.filter(action='product_image_removed').count(), 2)
        # A product that was not in the upload keeps its gallery.
        self.assertEqual(len(self.gallery(self.cable)), 1)

    def test_a_picture_that_was_already_there_is_part_of_the_new_gallery_like_any_other(self):
        """Replacing rebuilds from the upload: «ya estaba» is a thing of adding."""
        body = self.staged([picture('COD-A1.png', THREE), picture('COD-A1_2.png', ONE)], mode='replace')
        phone = self.row(body, self.phone)
        self.assertEqual((phone['data']['will_add'], phone['data']['will_remove']), (2, 2))
        self.assertEqual([image['existing'] for image in phone['data']['images']], [False, False])

        self.assertEqual(self.apply(body['id'], confirm=WORD).status_code, 200)
        self.assertEqual(len(self.gallery(self.phone)), 2)
        self.assertEqual([image.is_primary for image in self.gallery(self.phone)], [True, False])

    def test_adding_never_removes_and_never_asks_for_the_word(self):
        body = self.staged([picture('COD-A1.png', THREE)])
        self.assertEqual(self.row(body, self.phone)['data']['will_remove'], 0)
        self.assertEqual(self.apply(body['id']).status_code, 200)
        self.assertEqual(len(self.gallery(self.phone)), 3)

    def test_a_replacement_keeps_to_the_limit_of_what_it_uploads(self):
        with override_settings(PRODUCT_IMAGE_MAX_PER_PRODUCT=2):
            fine = self.staged([picture('COD-A1.png', THREE), picture('COD-A1_2.png', FOUR)], mode='replace')
            self.assertEqual(fine['counts']['error'], 0)
            many = self.staged([picture('COD-A1.png', ONE), picture('COD-A1_2.png', TWO), picture('COD-A1_3.png', THREE)],
                               mode='replace')
            self.assertEqual(self.row(many, self.phone)['action'], 'error')

    def test_a_gallery_that_changed_since_the_preview_is_not_replaced(self):
        body = self.staged([picture('COD-A1.png', THREE)], mode='replace')
        self.loaded([picture('COD-A1.png', FOUR)])          # one more picture than the preview counted
        response = self.apply(body['id'], confirm=WORD)
        self.assertEqual(response.status_code, 400)
        self.assertIn('galería', response.json()['detail'])
        self.assertEqual(len(self.gallery(self.phone)), 3)


# =============================================================================
# Whose it is, and who may
# =============================================================================

class TenancyAndAuthorityTest(_Base):
    def test_a_code_of_another_company_never_matches(self):
        body = self.staged([picture('COD-B1.png', ONE), picture('COD-A1.png', TWO)])

        self.assertEqual(self.unmatched(body), {'COD-B1.png': 'no_product'})
        self.assertEqual(self.row(body, self.phone)['data']['product_id'], self.phone.pk)
        self.assertNotIn('Funda ajena', str(body))
        self.assertNotIn('Teléfono ajeno', str(body))
        self.apply(body['id'])
        # The twin code in the other shop got nothing: the picture went to THIS shop's product.
        self.assertEqual(ProductImage.objects.filter(company=self.other).count(), 0)
        self.assertEqual(ProductImage.objects.filter(product=self.phone).count(), 1)
        self.assertFalse(StorefrontImage.objects.filter(company=self.other).exists())

    def test_the_company_never_comes_from_the_form(self):
        body = self.staged([picture('COD-B1.png', ONE)], company=str(self.other.pk))
        self.assertEqual(self.unmatched(body), {'COD-B1.png': 'no_product'})
        self.assertEqual(BulkImportJob.objects.get(pk=body['id']).company, self.company)

    def test_it_needs_the_capability_to_manage_products(self):
        response = self.preview([picture('COD-A1.png', ONE)], client=_client(self.viewer))
        self.assertEqual(response.status_code, 403)
        self.assertEqual((BulkImportJob.objects.count(), StorefrontImage.objects.count()), (0, 0))
        self.assertIn(self.preview([picture('COD-A1.png', ONE)], client=APIClient()).status_code, (401, 403))

        body = self.staged([picture('COD-A1.png', ONE)])
        self.assertEqual(self.apply(body['id'], client=_client(self.viewer)).status_code, 403)
        self.assertEqual(ProductImage.objects.count(), 0)

    def test_another_company_cannot_apply_or_read_the_job(self):
        body = self.staged([picture('COD-A1.png', ONE)])
        stranger = _client(self.outsider)
        self.assertEqual(self.apply(body['id'], client=stranger).status_code, 404)
        self.assertEqual(stranger.get(f'/api/admin/imports/{body["id"]}/').status_code, 404)
        self.assertEqual(stranger.get('/api/admin/imports/').json()['results'], [])
        self.assertEqual(ProductImage.objects.count(), 0)

    def test_a_job_of_another_kind_is_not_applied_through_this_address_nor_this_one_through_the_other(self):
        body = self.staged([picture('COD-A1.png', ONE)])
        self.assertEqual(self.api.post(f'/api/admin/products/import/{body["id"]}/apply/').status_code, 404)
        products = BulkImportJob.objects.create(company=self.company, import_type=BulkImportJob.PRODUCTS)
        self.assertEqual(self.apply(products.pk).status_code, 404)

    def test_the_history_shows_it_to_who_manages_products_and_not_to_who_only_moves_stock(self):
        job = self.loaded([picture('COD-A1.png', ONE)])
        stock, _ = _p2d_member(self.company, 'img_almacen', ['company.view', 'inventory.adjust'])

        listed = self.api.get('/api/admin/imports/?type=product_images').json()['results']
        self.assertEqual([(entry['id'], entry['import_type'], entry['status']) for entry in listed],
                         [(job['id'], 'product_images', 'applied')])
        self.assertEqual(self.api.get(f'/api/admin/imports/{job["id"]}/').status_code, 200)
        self.assertEqual(_client(stock).get('/api/admin/imports/').json()['results'], [])
        self.assertEqual(_client(stock).get('/api/admin/imports/?type=product_images').status_code, 403)
        self.assertEqual(_client(stock).get(f'/api/admin/imports/{job["id"]}/').status_code, 403)

    def test_its_errors_download_like_any_other_load_s(self):
        body = self.staged([picture('COD-A1.png', b'no es una imagen')])
        response = self.api.get(f'/api/admin/imports/{body["id"]}/errors.csv/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('COD-A1.png', response.content.decode('utf-8'))
        self.assertIn('Teléfono', response.content.decode('utf-8'))

    def test_the_apply_address_takes_no_file_and_is_rate_limited(self):
        from rest_framework.views import APIView

        from store.import_views import AdminProductImagesImportApplyView as View

        self.assertEqual(list(View.parser_classes), list(APIView.parser_classes))
        self.assertEqual([t.scope for t in View().get_throttles()], ['admin_import'])
        self.assertEqual(self.api.get(apply_url(1)).status_code, 405)

    def test_the_kind_is_a_choice_of_the_model(self):
        self.assertIn(('product_images', 'Imágenes de productos'), BulkImportJob.TYPE_CHOICES)
        self.assertEqual(BulkImportJob.PRODUCT_IMAGES, 'product_images')


# =============================================================================
# Every protection of the picture bundle, on this path too
# =============================================================================

class BundleSafetyTest(_Base):
    def refused(self, response, *codes, says='ZIP'):
        """Refused by the BUNDLE's own rule — its sentence, not any 400 — and nothing kept."""
        self.assertIn(response.status_code, codes or (400,), response.content)
        self.assertIn(says, response.json()['detail'])
        self.assertEqual((BulkImportJob.objects.count(), StorefrontImage.objects.count()), (0, 0))

    def test_paths_that_leave_the_archive(self):
        for member in ('../COD-A1.png', 'fotos/../../COD-A1.png', '/etc/COD-A1.png', 'C:\\Windows\\COD-A1.png'):
            with self.subTest(member):
                self.refused(self.preview(images_zip=archive([('COD-A2.png', ONE), (member, TWO)])))

    def test_a_symbolic_link(self):
        link = zipfile.ZipInfo('COD-A1.png')
        link.external_attr = (0o120777 << 16)
        self.refused(self.preview(images_zip=archive([(link, '/etc/passwd')])))

    def test_an_encrypted_archive(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as bundle:
            bundle.writestr('COD-A1.png', ONE)
        data = bytearray(out.getvalue())
        data[data.find(b'PK\x03\x04') + 6] |= 1
        data[data.find(b'PK\x01\x02') + 8] |= 1
        self.refused(self.preview(images_zip=SimpleUploadedFile('i.zip', bytes(data), content_type='application/zip')))

    def test_what_is_not_an_archive(self):
        self.refused(self.preview(images_zip=SimpleUploadedFile('i.zip', b'esto no es un zip', content_type='application/zip')))

    @override_settings(IMPORT_IMAGES_ZIP_MAX_ENTRIES=3)
    def test_too_many_entries(self):
        self.refused(self.preview(images_zip=archive([(f'COD-A1_{n}.png', ONE) for n in range(4)])))

    def test_an_entry_that_expands_out_of_all_proportion(self):
        self.refused(self.preview(images_zip=archive([('COD-A1.png', b'\x00' * 6_000_000)])), 400, 413)

    @override_settings(IMPORT_IMAGES_MAX_TOTAL_BYTES=4000)
    def test_more_bytes_than_a_load_admits(self):
        self.refused(self.preview(images_zip=archive([('COD-A1.png', b'\x00' * 2_000_000)])), 413)
        self.refused(self.preview([picture('COD-A1.png', b'x' * 3000), picture('COD-A2.png', b'y' * 3000)]), 413,
                     says='pesan más')

    @override_settings(IMPORT_IMAGES_MAX_FILES=2)
    def test_more_files_than_a_load_admits(self):
        self.refused(self.preview([picture(f'COD-A1_{n}.png', ONE) for n in range(3)]), says='demasiadas imágenes')

    @override_settings(STOREFRONT_IMAGE_MAX_PIXELS=5000)
    def test_a_picture_of_too_many_pixels_is_an_error_of_its_product(self):
        body = self.staged([picture('COD-A1.png', png_with_transparency((400, 400)))])
        self.assertEqual(self.row(body, self.phone)['action'], 'error')
        self.assertEqual(StorefrontImage.objects.count(), 0)

    @override_settings(STOREFRONT_IMAGE_MAX_UPLOAD_BYTES=1000)
    def test_a_picture_that_weighs_too_much_is_an_error_of_its_product(self):
        body = self.staged([picture('COD-A1.png', ONE + b'\x00' * 2000)])
        self.assertEqual(self.row(body, self.phone)['action'], 'error')
        self.assertIn('pesa más', ' '.join(self.row(body, self.phone)['errors']))

    def test_an_abandoned_preview_is_swept_by_the_daily_cleanup(self):
        self.staged([picture('COD-A1.png', ONE)])
        self.assertEqual(StorefrontImage.objects.count(), 1)
        StorefrontImage.objects.update(created_at='2020-01-01T00:00:00Z')
        with self.captureOnCommitCallbacks(execute=True):
            call_command('cleanup_storefront_images', '--older-than-hours', '24', verbosity=0)
        self.assertEqual(StorefrontImage.objects.count(), 0)

    def test_a_preview_that_fails_half_way_leaves_no_file_behind(self):
        from unittest import mock

        from store import evidence_storage

        written = []
        real = storefront_media.store

        def spy(**kwargs):
            image = real(**kwargs)
            written.append(image.storage_key)
            return image

        with mock.patch.object(storefront_media, 'store', side_effect=spy), \
                mock.patch.object(image_import, '_finish', side_effect=RuntimeError('falla a propósito')):
            with self.assertRaises(RuntimeError):
                self.preview([picture('COD-A1.png', ONE)])
        self.assertEqual(len(written), 1)
        self.assertFalse(evidence_storage.get_storage().exists(written[0]))
        self.assertEqual((BulkImportJob.objects.count(), StorefrontImage.objects.count()), (0, 0))


# =============================================================================
# Two at once
# =============================================================================

@override_settings(EVIDENCE_STORAGE_BACKEND='filesystem', EVIDENCE_STORAGE_ROOT=_ROOT)
class TwoAppliesAtOnceTest(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.company = _saas_company('Tienda A', 'img-dos', tax_id='20999999992')
        from store.company_provisioning import provision_company_access_defaults
        provision_company_access_defaults(self.company)
        self.manager, _ = _p2d_member(self.company, 'img_una', ['company.view', 'products.manage'])
        self.products = []
        for n in range(4):
            product = Product.objects.create(company=self.company, name=f'Producto {n}', slug=f'img2-{n}', price='10.00')
            ProductBarcode.objects.create(company=self.company, product=product, code=f'DOS-{n}')
            self.products.append(product)

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

    def test_the_same_job_applied_twice_at_once_places_each_picture_once(self):
        api = _client(self.manager)
        staged = api.post(PREVIEW, {
            'kind': 'images', 'images': [picture(f'DOS-{n}.png', png_with_transparency((40 + n, 40))) for n in range(4)],
        }, format='multipart')
        self.assertEqual(staged.status_code, 201, staged.content)
        job_id = staged.json()['id']

        results = self.together([lambda: _client(self.manager).post(apply_url(job_id), {}, format='json')] * 2)

        for response in results:
            self.assertEqual(getattr(response, 'status_code', response), 200, results)
        self.assertEqual(ProductImage.objects.count(), 4)
        self.assertEqual(AdminAuditLog.objects.filter(action='product_images_import_applied').count(), 1)
        self.assertEqual([response.json()['summary']['applied']['images_added'] for response in results], [4, 4])

    def test_two_jobs_over_the_same_products_at_once_do_not_deadlock(self):
        def stage(order, offset):
            api = _client(self.manager)
            files = [picture(f'DOS-{n}.png', png_with_transparency((60 + n + offset, 50))) for n in order]
            return api.post(PREVIEW, {'kind': 'images', 'images': files}, format='multipart').json()['id']

        first, second = stage(range(4), 0), stage(reversed(range(4)), 10)
        results = self.together([
            lambda: _client(self.manager).post(apply_url(first), {}, format='json'),
            lambda: _client(self.manager).post(apply_url(second), {}, format='json'),
        ])
        # One went in; the other found the galleries changed since its preview and said so. Nobody hung.
        codes = sorted(getattr(response, 'status_code', 500) for response in results)
        self.assertEqual(codes, [200, 400], results)
        self.assertEqual(ProductImage.objects.count(), 4)
