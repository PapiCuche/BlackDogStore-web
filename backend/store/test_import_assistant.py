"""
INVENTORY-UNIFIED-01 · parte 3 — «¿Qué archivo tengo?».

    EL ASISTENTE DICE A QUÉ CARGA VA UN ARCHIVO. NO LO CARGA.

Se decide SÓLO por los nombres de las hojas y sus cabeceras, con el mismo
vocabulario que usan las tres cargas: una columna de número de serie es de
equipos, una de cantidad por almacén es de stock, nombre y precio son de
productos, una hoja «Reporte de …» no se carga. Lo que mezcla dos cargas o no
se reconoce se dice así — no se elige una por conveniencia.

Lo que estas pruebas fijan:

  * cada plantilla, cada descarga (en sus dos modos) y cada libro histórico;
  * lo ambiguo y lo desconocido;
  * que no deja nada escrito: ni un trabajo, ni una fila, ni un perfil;
  * permisos y empresa: una sugerencia sólo para lo que quien pregunta puede cargar.
"""
import io

import openpyxl
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from store import import_assistant, import_exports, unit_import_services
from store.models import (
    AdminAuditLog, BulkImportJob, BulkImportRow, Category, ImportMappingProfile, Product, ProductBarcode,
)
from store.test_import_template import legacy_template as legacy_product_template
from store.test_unit_import import legacy_template as legacy_unit_template
from store.tests import (
    _C14_STOCK_HEADERS, _c14_products_workbook, _c14_stock_workbook, _p2d_branch, _p2d_member, _p3_company,
)

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
INSPECT = '/api/admin/imports/inspect/'

PRODUCT_HEADERS = import_exports.PRODUCT_TEMPLATE_HEADERS
UNIT_HEADERS = unit_import_services.HEADERS


def book(*sheets) -> bytes:
    """A workbook from `(sheet name, [row, row…])` pairs. The first row of each is its header row."""
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets:
        page = workbook.create_sheet(name)
        for row in rows:
            page.append(list(row))
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()


def saved(workbook) -> bytes:
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()


# =============================================================================
# The rule, from sheet names and headers alone
# =============================================================================

class ClassifyTest(SimpleTestCase):
    def of(self, headers, sheet='Hoja1'):
        found = import_assistant.classify([(sheet, [list(headers)])])
        return found['type'], found['reason']

    def test_a_serial_column_is_a_device_file(self):
        for header in ('Número de serie', 'Nº de serie', 'No. de serie', 'S/N'):
            with self.subTest(header):
                self.assertEqual(self.of(['Código', 'Sucursal', header]), ('units', 'serial_column'))

    def test_a_quantity_per_warehouse_is_a_stock_file(self):
        self.assertEqual(self.of(['CODIGO', 'NOMBRE', 'ALMACEN Centro']), ('stock', 'warehouse_columns'))
        self.assertEqual(self.of(['Código', 'Depósito 2']), ('stock', 'warehouse_columns'))
        self.assertEqual(self.of(_C14_STOCK_HEADERS), ('stock', 'warehouse_columns'))

    def test_a_bare_quantity_column_is_a_stock_file_too(self):
        for header in ('Cantidad', 'Stock', 'Nº de unidades', 'Existencias'):
            with self.subTest(header):
                self.assertEqual(self.of(['Código', 'Nombre', header]), ('stock', 'quantity_column'))

    def test_what_counts_something_else_is_not_a_quantity(self):
        for header in ('Stock mínimo', 'Unidades de medida', 'Costo unitario'):
            with self.subTest(header):
                self.assertEqual(self.of(['Código', 'Nombre', header]), ('unknown', 'not_recognised'))

    def test_name_and_price_are_a_product_file(self):
        self.assertEqual(self.of(PRODUCT_HEADERS), ('products', 'product_columns'))
        self.assertEqual(self.of(['Nombre', 'Precio de venta']), ('products', 'product_columns'))

    def test_a_product_file_with_a_stock_column_is_still_a_product_file_and_says_so(self):
        """As the product importer treats it: it reads the catalogue and says the stock is not loaded there."""
        found = import_assistant.classify([('Productos', [[*PRODUCT_HEADERS, 'Stock inicial']])])
        self.assertEqual((found['type'], found['reason']), ('products', 'product_columns_with_stock'))
        self.assertIn('stock', found['detail'].lower())

    def test_code_and_pictures_without_name_or_price_are_an_image_file(self):
        self.assertEqual(self.of(['Código', 'Imagen principal', 'Imágenes'], 'Imágenes'), ('images', 'image_columns'))
        self.assertEqual(self.of(['Código', 'Imágenes']), ('images', 'image_columns'))
        # A picture goes to a product by its CODE, never by its name.
        self.assertEqual(self.of(['Nombre', 'Imágenes']), ('unknown', 'not_recognised'))

    def test_a_serial_and_a_quantity_together_are_not_guessed(self):
        for extra in ('Cantidad', 'ALMACEN Centro', 'Stock inicial'):
            with self.subTest(extra):
                self.assertEqual(self.of([*UNIT_HEADERS, extra]), ('unknown', 'serial_and_quantity'))

    def test_product_columns_and_a_serial_together_are_not_guessed(self):
        self.assertEqual(self.of([*PRODUCT_HEADERS, 'Número de serie']), ('unknown', 'product_and_serial'))
        self.assertEqual(self.of(['Nombre', 'Precio de venta', 'IMEI', 'Nº de serie']), ('unknown', 'product_and_serial'))

    def test_what_it_does_not_know_it_says(self):
        for headers in (['Código', 'Nombre'], ['Cliente', 'Teléfono'], [], ['IMEI'], ['Serie']):
            with self.subTest(headers=headers):
                self.assertEqual(self.of(headers), ('unknown', 'not_recognised'))

    def test_a_report_sheet_is_a_report_whatever_its_columns(self):
        for title in import_exports.REPORT_SHEETS.values():
            with self.subTest(title):
                found = import_assistant.classify([(title, [UNIT_HEADERS])])
                self.assertEqual((found['type'], found['reason'], found['sheet']), ('report', 'report_sheet', title))
                self.assertIn('para volver a cargar', found['detail'])

    def test_the_headers_may_be_on_the_second_row(self):
        found = import_assistant.classify([('Productos', [['Información del producto'], ['Nombre', 'Precio de venta']])])
        self.assertEqual(found['type'], 'products')

    def test_a_banner_above_the_headers_that_mentions_stock_is_not_the_header_row(self):
        """The owner's product export: «Stock Por Almacén» is a group title, and the headers are under it."""
        found = import_assistant.classify([
            ('Productos', [['Información del producto', 'Stock Por Almacén', 'Stock Tienda Uno - (11834)'],
                           ['Código', 'Nombre', 'Precio de venta']]),
        ])
        self.assertEqual((found['type'], found['reason']), ('products', 'product_columns'))

    def test_a_count_or_a_serial_with_nothing_that_names_the_product_is_not_a_load(self):
        for headers in (['Stock por almacén', 'Cantidad'], ['ALMACEN Centro'], ['Número de serie', 'IMEI'],
                        ['Número de serie', 'Cantidad']):
            with self.subTest(headers=headers):
                self.assertEqual(self.of(headers), ('unknown', 'not_recognised'))

    def test_sheets_that_only_explain_do_not_count(self):
        found = import_assistant.classify([
            ('Instrucciones', [['Número de serie', 'Cantidad']]),
            ('Productos', [PRODUCT_HEADERS]),
            # Whatever an example shows, it is an example: it does not make the file two loads.
            ('Ejemplo', [UNIT_HEADERS]),
            ('Categorías', [['Nombre exacto']]),
        ])
        self.assertEqual((found['type'], found['sheet']), ('products', 'Productos'))

    def test_the_first_row_that_says_something_is_the_header_row(self):
        """The second row is tried only when the first says nothing: it is usually data."""
        found = import_assistant.classify([('Equipos', [UNIT_HEADERS, ['Código', 'Nombre', 'Precio de venta']])])
        self.assertEqual((found['type'], found['reason']), ('units', 'serial_column'))

    def test_two_sheets_of_two_loads_are_not_guessed(self):
        found = import_assistant.classify([('Productos', [PRODUCT_HEADERS]), ('Equipos', [UNIT_HEADERS])])
        self.assertEqual((found['type'], found['reason']), ('unknown', 'several_kinds'))

    def test_two_sheets_of_the_same_load_are_that_load(self):
        found = import_assistant.classify([('Centro', [UNIT_HEADERS]), ('Norte', [UNIT_HEADERS])])
        self.assertEqual((found['type'], found['sheet']), ('units', 'Centro'))

    def test_every_answer_carries_a_sentence(self):
        for reason, detail in import_assistant.REASONS.items():
            with self.subTest(reason):
                self.assertTrue(detail.strip())
        self.assertEqual(set(import_assistant.TYPES), {'products', 'stock', 'units', 'images', 'report', 'unknown'})


# =============================================================================
# Through the endpoint, with real workbooks
# =============================================================================

class _Api(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('asist-a', 'Tienda A')
        self.other = _p3_company('asist-b', 'Tienda B')
        self.branch = _p2d_branch(self.company, 'Centro')
        self.everything = self.member('as_todo', 'products.view', 'products.manage', 'inventory.view', 'inventory.adjust')

    def member(self, name, *capabilities, company=None):
        user, _ = _p2d_member(company or self.company, name, ['company.view', *capabilities])
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def ask(self, data, client=None, name='archivo.xlsx', **fields):
        return (client or self.everything).post(
            INSPECT, {'file': SimpleUploadedFile(name, data, content_type=XLSX), 'import_type': 'auto', **fields},
            format='multipart',
        )

    def suggestion(self, data, client=None):
        response = self.ask(data, client)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        return body['suggested_import_type'], body['suggestion_reason']


class TemplatesAndHistoricWorkbooksTest(_Api):
    def test_the_three_templates_the_platform_hands_out(self):
        self.assertEqual(
            self.suggestion(import_exports.product_template_bytes(self.company)), ('products', 'product_columns'))
        self.assertEqual(
            self.suggestion(unit_import_services.template_bytes(['Centro'])), ('units', 'serial_column'))

    def test_the_templates_from_before_one_sheet(self):
        self.assertEqual(self.suggestion(legacy_product_template()), ('products', 'product_columns'))
        self.assertEqual(self.suggestion(saved(legacy_unit_template())), ('units', 'serial_column'))

    def test_the_owner_s_point_of_sale_exports(self):
        # 18 columns, five sheets, a banner row above the headers, a stock column, and the
        # data validations that stop openpyxl from opening it.
        products = _c14_products_workbook([{'Código': 'C000002', 'Nombre': 'Cable', 'Precio venta - 11834': 19.9}])
        self.assertEqual(self.suggestion(products), ('products', 'product_columns_with_stock'))
        self.assertEqual(self.suggestion(_c14_stock_workbook([])), ('stock', 'warehouse_columns'))

    def test_the_owner_s_planning_workbook(self):
        data = book(
            ('Productos', [[*PRODUCT_HEADERS[:-1], 'Destino (pendiente web)'], ['EAN', 'Código', 'OBLIGATORIO']]),
            ('Categorías', [['CATEGORÍAS DE TU EMPRESA']]),
            ('Instrucciones', [['PLANTILLA DE PRODUCTOS']]),
            ('Ejemplo', [PRODUCT_HEADERS]),
        )
        self.assertEqual(self.suggestion(data), ('products', 'product_columns'))

    def test_the_inventory_download_that_was_already_there(self):
        old = self.member('as_stock', 'inventory.adjust').get('/api/admin/inventory/export/')
        self.assertEqual(old.status_code, 200)
        self.assertEqual(self.suggestion(old.content), ('stock', 'warehouse_columns'))


class ExportsOfPartTwoTest(_Api):
    def setUp(self):
        super().setUp()
        category = Category.objects.create(company=self.company, name='Accesorios', slug='accesorios')
        product = Product.objects.create(company=self.company, name='Funda', slug='funda', price='59.00', category=category)
        ProductBarcode.objects.create(company=self.company, product=product, code='FUN-1')

    def download(self, kind, mode):
        response = self.everything.get(f'/api/admin/inventory/exports/{kind}/?mode={mode}')
        self.assertEqual(response.status_code, 200, response.content[:200])
        return response.content

    def test_each_file_to_reload_goes_to_its_own_load(self):
        self.assertEqual(self.suggestion(self.download('products', 'reimport')), ('products', 'product_columns'))
        self.assertEqual(self.suggestion(self.download('stock', 'reimport')), ('stock', 'warehouse_columns'))
        self.assertEqual(self.suggestion(self.download('units', 'reimport')), ('units', 'serial_column'))

    def test_each_report_is_a_report_and_points_at_the_file_to_reload(self):
        for kind in ('products', 'stock', 'units'):
            with self.subTest(kind):
                body = self.ask(self.download(kind, 'report')).json()
                self.assertEqual((body['suggested_import_type'], body['suggestion_reason']), ('report', 'report_sheet'))
                self.assertIn('es un reporte', body['suggestion_detail'])
                self.assertIn('para volver a cargar', body['suggestion_detail'])
                self.assertEqual(body['suggestion_sheet'], import_exports.REPORT_SHEETS[kind])


class AmbiguousAndUnknownTest(_Api):
    def test_a_quantity_column_and_a_serial_column(self):
        body = self.ask(book(('Equipos', [[*UNIT_HEADERS, 'Cantidad'], ['C1', 'Teléfono', 'Centro', 'S1']]))).json()
        self.assertEqual((body['suggested_import_type'], body['suggestion_reason']), ('unknown', 'serial_and_quantity'))
        self.assertIn('número de serie', body['suggestion_detail'])
        self.assertIn('cantidad', body['suggestion_detail'])

    def test_product_and_device_columns(self):
        self.assertEqual(
            self.suggestion(book(('Hoja1', [[*PRODUCT_HEADERS, 'Número de serie', 'IMEI']]))),
            ('unknown', 'product_and_serial'))

    def test_sheets_of_two_loads(self):
        self.assertEqual(
            self.suggestion(book(('Productos', [PRODUCT_HEADERS]), ('Inventario', [['CODIGO', 'NOMBRE', 'ALMACEN Centro']]))),
            ('unknown', 'several_kinds'))

    def test_columns_nobody_reads(self):
        self.assertEqual(self.suggestion(book(('Clientes', [['Cliente', 'Teléfono']]))), ('unknown', 'not_recognised'))
        self.assertEqual(self.suggestion(book(('Vacía', []))), ('unknown', 'not_recognised'))

    def test_the_image_workbook(self):
        body = self.ask(book(('Imágenes', [['Código', 'Imagen principal', 'Imágenes']]))).json()
        self.assertEqual((body['suggested_import_type'], body['suggestion_reason']), ('images', 'image_columns'))

    def test_something_that_is_not_a_workbook_is_refused_as_before(self):
        response = self.everything.post(
            INSPECT, {'file': SimpleUploadedFile('notas.xlsx', b'esto no es un excel', content_type=XLSX),
                      'import_type': 'auto'}, format='multipart')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.everything.post(INSPECT, {'import_type': 'auto'}, format='multipart').status_code, 400)


class ItOnlyLooksTest(_Api):
    def test_it_stages_nothing_and_writes_nothing(self):
        Product.objects.create(company=self.company, name='Funda', slug='funda', price='59.00')
        rows = [PRODUCT_HEADERS, [None, 'NUEVO-1', 'Producto nuevo', None, 10, None, None, None, None]]
        before = (Product.objects.count(), AdminAuditLog.objects.count())

        body = self.ask(book(('Productos', rows))).json()

        self.assertEqual(body['suggested_import_type'], 'products')
        self.assertEqual(BulkImportJob.objects.count(), 0)
        self.assertEqual(BulkImportRow.objects.count(), 0)
        self.assertEqual(ImportMappingProfile.objects.count(), 0)
        self.assertEqual((Product.objects.count(), AdminAuditLog.objects.count()), before)

    def test_it_answers_with_the_suggestion_and_the_sheet_names_and_no_cell(self):
        rows = [UNIT_HEADERS, ['TEL-X', 'Teléfono secreto', 'Centro', 'SERIE-SECRETA-1', '', '', 'Nuevo', '3800', 'F001']]
        response = self.ask(book(('Equipos', rows), ('Instrucciones', [['Cómo llenar']])))
        body = response.json()

        self.assertEqual(set(body), {
            'import_type', 'suggested_import_type', 'suggestion_reason', 'suggestion_detail', 'suggestion_sheet',
            'sheet_names',
        })
        self.assertEqual(body['import_type'], 'auto')
        self.assertEqual(body['suggestion_sheet'], 'Equipos')
        self.assertEqual(body['sheet_names'], ['Equipos', 'Instrucciones'])
        for value in ('SERIE-SECRETA-1', 'Teléfono secreto', '3800'):
            self.assertNotIn(value, response.content.decode())

    def test_the_reason_is_a_sentence_somebody_can_read(self):
        body = self.ask(unit_import_services.template_bytes(['Centro'])).json()
        self.assertEqual(body['suggestion_detail'], import_assistant.REASONS['serial_column'])
        self.assertIn('número de serie', body['suggestion_detail'])
        self.assertIn('carga de equipos', body['suggestion_detail'])


class WhoMayAskTest(_Api):
    def files(self):
        return {
            'products': import_exports.product_template_bytes(self.company),
            'stock': _c14_stock_workbook([]),
            'units': unit_import_services.template_bytes(['Centro']),
            'images': book(('Imágenes', [['Código', 'Imagen principal', 'Imágenes']])),
        }

    def test_a_suggestion_only_for_what_the_caller_could_load(self):
        catalogue = self.member('as_catalogo', 'products.manage')
        warehouse = self.member('as_almacen', 'inventory.adjust')
        expected = {
            #            catalogue    warehouse
            'products': ('products', 'forbidden'),
            'images':   ('images', 'forbidden'),
            'stock':    ('forbidden', 'stock'),
            'units':    ('forbidden', 'units'),
        }
        for kind, data in self.files().items():
            for client, wanted in zip((catalogue, warehouse), expected[kind]):
                with self.subTest(kind=kind, wanted=wanted):
                    self.assertEqual(self.ask(data, client).json()['suggested_import_type'], wanted)

    def test_without_the_permission_it_says_so_and_nothing_else(self):
        catalogue = self.member('as_catalogo', 'products.manage')
        answers = [self.ask(self.files()[kind], catalogue).json() for kind in ('stock', 'units')]

        # The same answer for both: it does not say WHICH load the file belongs to.
        self.assertEqual(answers[0], answers[1])
        self.assertEqual(answers[0], {
            'import_type': 'auto', 'suggested_import_type': 'forbidden', 'suggestion_reason': 'no_permission',
            'suggestion_detail': import_assistant.NO_PERMISSION, 'suggestion_sheet': '', 'sheet_names': [],
        })

    def test_a_report_or_an_unknown_file_is_said_to_anyone_who_may_load_something(self):
        catalogue = self.member('as_catalogo', 'products.manage')
        report = book((import_exports.REPORT_SHEETS['units'], [UNIT_HEADERS]))
        self.assertEqual(self.ask(report, catalogue).json()['suggested_import_type'], 'report')
        self.assertEqual(self.ask(book(('X', [['Cliente']])), catalogue).json()['suggested_import_type'], 'unknown')

    def test_reading_is_not_loading(self):
        for name, capabilities in (('as_lectora', ('products.view', 'inventory.view')), ('as_nadie', ())):
            with self.subTest(name):
                response = self.ask(self.files()['products'], self.member(name, *capabilities))
                self.assertEqual(response.status_code, 403)
        self.assertIn(self.ask(self.files()['products'], APIClient()).status_code, (401, 403))

    def test_another_company_is_not_reachable(self):
        stranger = self.member('as_ajena', 'products.manage', 'inventory.adjust', company=self.other)
        response = stranger.post(
            f'{INSPECT}?company={self.company.pk}',
            {'file': SimpleUploadedFile('a.xlsx', self.files()['products'], content_type=XLSX), 'import_type': 'auto'},
            format='multipart')
        self.assertIn(response.status_code, (403, 404))
        # In their own company the same file is theirs to ask about, and the answer names nothing of A.
        own = self.ask(self.files()['products'], stranger)
        self.assertEqual(own.json()['suggested_import_type'], 'products')
        self.assertNotIn('Centro', own.content.decode())

    def test_the_inspection_the_wizards_already_use_gains_the_suggestion_and_loses_nothing(self):
        catalogue = self.member('as_catalogo', 'products.manage')
        response = catalogue.post(
            INSPECT, {'file': SimpleUploadedFile('a.xlsx', self.files()['units'], content_type=XLSX),
                      'import_type': 'products'}, format='multipart')
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        for key in ('import_type', 'reader_notes', 'sheets', 'fields', 'branches', 'image_rules'):
            self.assertIn(key, body)
        # They asked as a product load and the file is a device file they may not load.
        self.assertEqual((body['suggested_import_type'], body['suggestion_reason']), ('forbidden', 'no_permission'))

        stock = self.member('as_almacen', 'inventory.adjust').post(
            INSPECT, {'file': SimpleUploadedFile('a.xlsx', self.files()['units'], content_type=XLSX),
                      'import_type': 'stock'}, format='multipart').json()
        self.assertEqual((stock['suggested_import_type'], stock['suggestion_sheet']), ('units', 'Equipos'))
        self.assertIn('sheets', stock)

    def test_it_is_the_same_view_and_the_same_address(self):
        from django.urls import resolve

        from store.import_views import AdminImportInspectView

        self.assertIs(resolve('/api/admin/imports/inspect/').func.view_class, AdminImportInspectView)
