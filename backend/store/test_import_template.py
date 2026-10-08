"""
IMPORT-TEMPLATE-HELP — la plantilla de productos explica cómo van las imágenes.

La duda de siempre: «¿pego la foto en el Excel?». No. El Excel guarda NOMBRES
de archivo, y los archivos se adjuntan después. La plantilla lo dice en una
hoja de instrucciones, con un ejemplo completo, y la primera hoja trae una fila
de ejemplo para que se vea la forma.

    NADA DE LA AYUDA SE IMPORTA COMO PRODUCTO.

Ni la fila de ayuda, ni la fila de ejemplo, ni las hojas de instrucciones y de
ejemplo. Subir la plantilla tal cual se descarga no crea nada.
"""
import io
import json

import openpyxl
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from store import import_exports
from store.models import BulkImportJob, Product
from store.tests import _p2d_member, _p3_company

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
PREVIEW = '/api/admin/products/import/preview/'
INSPECT = '/api/admin/imports/inspect/'


def book(data=None):
    return openpyxl.load_workbook(io.BytesIO(data or import_exports.product_template_bytes()))


def text_of(sheet) -> str:
    return '\n'.join(str(c.value) for row in sheet.iter_rows() for c in row if c.value is not None)


class TemplateContentTest(TestCase):
    def test_the_first_sheet_is_the_one_that_gets_imported(self):
        workbook = book()

        # PRODUCT-DESTINATION-01: «Categorías» lists the company's own, for the drop-down.
        self.assertEqual(workbook.sheetnames, ['Productos', 'Categorías', 'Instrucciones', 'Ejemplo'])
        sheet = workbook['Productos']
        self.assertEqual([c.value for c in sheet[1]], import_exports.PRODUCT_TEMPLATE_HEADERS)
        example = dict(zip(import_exports.PRODUCT_TEMPLATE_HEADERS, [c.value for c in sheet[3]]))
        self.assertEqual(example['Código'], 'IP16PM-256-W')
        self.assertEqual(example['Nombre'], 'iPhone 16 Pro Max 256GB White')
        self.assertEqual(example['Imagen principal'], 'iphone16-front.webp')
        self.assertEqual(example['Imágenes'], 'iphone16-front.webp|iphone16-back.webp|iphone16-side.webp')

    def test_the_instructions_say_how_images_travel(self):
        text = text_of(book()['Instrucciones'])

        for expected in (
            'NO se pegan dentro de Excel',            # 1
            'sólo los NOMBRES',                       # 2
            'Imagen principal',                       # 3
            'separados por |',                        # 4
            'ZIP',                                    # 5
            'deben coincidir',                        # 6
            'PNG', 'JPG', 'WEBP',                     # 7
            'iphone16-front.webp|iphone16-back.webp', # 10
        ):
            self.assertIn(expected, text, expected)

    @override_settings(PRODUCT_IMAGE_MAX_PER_PRODUCT=5, STOREFRONT_IMAGE_MAX_UPLOAD_BYTES=3 * 1024 * 1024,
                       IMPORT_IMAGES_MAX_FILES=40)
    def test_the_limits_it_states_are_the_ones_the_server_applies(self):
        text = text_of(book()['Instrucciones'])

        self.assertIn('5 imágenes por producto', text)
        self.assertIn('3 MB', text)
        self.assertIn('40 archivos', text)

    def test_the_example_sheet_is_a_whole_filled_case(self):
        sheet = book()['Ejemplo']
        text = text_of(sheet)

        self.assertEqual(
            [c.value for c in sheet[1]][:len(import_exports.PRODUCT_TEMPLATE_HEADERS)],
            import_exports.PRODUCT_TEMPLATE_HEADERS)
        self.assertGreaterEqual(sheet.max_row, 4)
        self.assertIn('Archivos que se adjuntan', text)
        self.assertIn('iphone16-back.webp', text)


class TemplateIsNeverImportedTest(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('plantilla-tienda', 'Tienda Plantilla')
        self.manager, _ = _p2d_member(
            self.company, 'plantilla_gestor', ['company.view', 'products.view', 'products.manage'])
        self.client = APIClient()
        self.client.force_authenticate(user=self.manager)

    def upload(self, data=None, name='plantilla-productos.xlsx'):
        return SimpleUploadedFile(name, data or import_exports.product_template_bytes(), content_type=XLSX)

    def preview(self, data=None, **fields):
        return self.client.post(
            f'{PREVIEW}?company={self.company.pk}',
            {'file': self.upload(data), 'options': json.dumps({'create_missing_categories': True}), **fields},
            format='multipart',
        )

    def test_inspecting_it_offers_one_sheet_and_marks_the_help_ones(self):
        res = self.client.post(
            f'{INSPECT}?company={self.company.pk}',
            {'file': self.upload(), 'import_type': 'products'}, format='multipart')

        self.assertEqual(res.status_code, 200, res.content)
        sheets = {s['name']: s for s in res.json()['sheets']}
        self.assertEqual(sheets['Productos']['preset'], 'products_platform')
        self.assertFalse(sheets['Productos']['help'])
        for name in ('Instrucciones', 'Ejemplo'):
            self.assertTrue(sheets[name]['help'], name)
            self.assertEqual(sheets[name]['preset'], '', name)
            self.assertEqual(sheets[name]['detected'], '', name)

    def test_inspecting_tells_the_screen_the_image_rules(self):
        rules = self.client.post(
            f'{INSPECT}?company={self.company.pk}',
            {'file': self.upload(), 'import_type': 'products'}, format='multipart').json()['image_rules']

        self.assertEqual(rules['separator'], '|')
        self.assertEqual(rules['formats'], ['PNG', 'JPG', 'JPEG', 'WEBP'])
        self.assertEqual(rules['max_per_product'], 12)
        self.assertEqual(rules['max_file_mb'], 8)
        self.assertEqual(rules['max_files'], 200)

    def test_uploading_the_template_as_downloaded_creates_nothing_and_complains_about_nothing(self):
        res = self.preview()

        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual(body['counts']['create'], 0, body['rows'])
        self.assertEqual(body['counts']['error'], 0, body['rows'])
        warnings = ' '.join(w for row in body['rows'] for w in row['warnings'])
        self.assertIn('Fila de ayuda de la plantilla', warnings)
        self.assertIn('Fila de ejemplo de la plantilla', warnings)
        self.assertEqual(Product.objects.filter(company=self.company).count(), 0)

    def test_a_real_row_under_the_example_is_imported_and_the_example_is_not(self):
        workbook = book()
        sheet = workbook['Productos']
        sheet.append([None, 'COD-REAL-1', 'Funda de silicona', 'Negra', 39.9, 'Accesorios', None, None])
        out = io.BytesIO()
        workbook.save(out)

        body = self.preview(out.getvalue()).json()

        self.assertEqual(body['counts']['create'], 1, body['rows'])
        self.assertEqual(body['counts']['error'], 0, body['rows'])
        created = [row for row in body['rows'] if row['action'] == 'create']
        self.assertEqual(created[0]['data']['name'], 'Funda de silicona')

    def test_a_shop_that_really_sells_that_phone_is_not_silenced(self):
        """Sólo se salta la fila EXACTA de la plantilla, no cualquier iPhone."""
        workbook = book()
        sheet = workbook['Productos']
        sheet.append([None, 'IP16PM-256-N', 'iPhone 16 Pro Max 256GB White', None, 4500, 'Teléfonos', None, None])
        out = io.BytesIO()
        workbook.save(out)

        body = self.preview(out.getvalue()).json()

        self.assertEqual(body['counts']['create'], 1, body['rows'])

    def test_a_help_sheet_cannot_be_chosen_as_the_products(self):
        for name in ('Ejemplo', 'Instrucciones'):
            with self.subTest(name):
                res = self.preview(sheet_name=name)
                self.assertEqual(res.status_code, 400, res.content)
                self.assertIn('hoja de ayuda', res.json()['detail'])
        self.assertFalse(BulkImportJob.objects.filter(company=self.company).exists())
