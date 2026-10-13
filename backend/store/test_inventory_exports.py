"""
INVENTORY-UNIFIED-01 · parte 2 — descargas del inventario.

    UN ARCHIVO PARA VOLVER A CARGAR NO ES UN REPORTE, Y EL NOMBRE LO DICE.

Tres libros, una hoja cada uno: «Productos» (el catálogo), «Inventario» (las
cantidades por sucursal) y «Equipos» (una fila por unidad física). En modo
`reimport` son exactamente lo que los importadores piden, y cargarlos sobre la
MISMA tienda no cambia nada: ni un producto, ni una cantidad, ni un equipo dos
veces. En modo `report` llevan columnas de lectura y los importadores los
rechazan.

Lo que estas pruebas fijan:

  * empresa, permisos y sucursal en cada descarga y en cada filtro;
  * el contenido de cada libro y sus filtros;
  * la ida y vuelta por cada importador sin cambios y sin stock doble;
  * que ninguna celda pueda ejecutarse como fórmula, y que al volver se lea igual;
  * el costo sólo para quien puede ajustar inventario;
  * que la descarga de siempre (`/api/admin/inventory/export/`) responde igual.
"""
import io
import json
import zipfile
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import F
from django.test import TestCase
from django.utils import timezone
from openpyxl import load_workbook
from rest_framework.test import APIClient

from store import import_exports, inventory_exports, stock_unit_services, unit_import_services, xlsx_reader
from store.models import (
    AdminAuditLog, BranchStock, BulkImportJob, Category, Order, Product, ProductBarcode,
    StockMovement, StockUnit,
)
from store.test_stock_units import IMEI_A, IMEI_B, IMEI_C, IMEI_D
from store.tests import _p2d_branch, _p2d_member, _p2d_stock, _p3_company

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
PICTURE = 'https://imagenes.example.test/producto.png'

ALL = ['company.view', 'products.view', 'products.manage', 'inventory.view', 'inventory.adjust']


def url(kind, **query):
    text = '&'.join(f'{key}={value}' for key, value in query.items())
    return f'/api/admin/inventory/exports/{kind}/' + (f'?{text}' if text else '')


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def upload(content, name='archivo.xlsx'):
    return SimpleUploadedFile(name, content, content_type=XLSX)


class _Base(TestCase):
    """
    Two branches, four products and four devices in one shop; another shop next door.

      Funda          — by quantity, on the web, EAN and internal code
      Cable          — by quantity, internal stock only, a code with leading zeros
      Batería vieja  — inactive, no code at all
      Teléfono X     — by serial number, with IMEI
    """

    def setUp(self):
        cache.clear()
        self.company = _p3_company('exp-a', 'Tienda A')
        self.other = _p3_company('exp-b', 'Tienda B')
        self.centro = self.branch(self.company, 'Centro')
        self.norte = _p2d_branch(self.company, 'Norte')
        self.foreign_branch = self.branch(self.other, 'Ajena')
        self.accessories = Category.objects.create(company=self.company, name='Accesorios', slug='accesorios')
        self.phones = Category.objects.create(company=self.company, name='Teléfonos', slug='telefonos')
        self.foreign_category = Category.objects.create(company=self.other, name='Ajena', slug='ajena')

        self.case = self.product('Funda', 'funda', '59.90', self.accessories, image_url=PICTURE,
                                 description='Funda de silicona.')
        self.cable = self.product('Cable', 'cable', '25.00', self.accessories, is_published_online=False)
        self.old = self.product('Batería vieja', 'bateria-vieja', '80.00', None, is_active=False)
        self.phone = self.product('Teléfono X', 'telefono-x', '4500.00', self.phones, image_url=PICTURE,
                                  is_serialized=True, requires_imei=True)
        self.foreign = Product.objects.create(
            company=self.other, name='Producto ajeno', slug='ajeno', price=Decimal('10.00'))
        self.code(self.case, 'FUN-001')
        self.code(self.case, '7751234567892', ProductBarcode.EAN13)
        self.code(self.cable, '00123')
        self.code(self.phone, 'TEL-X')
        ProductBarcode.objects.create(company=self.other, product=self.foreign, code='AJENO-1',
                                      symbology=ProductBarcode.INTERNAL)

        _p2d_stock(self.centro, self.case, 4)
        _p2d_stock(self.norte, self.case, 1)
        _p2d_stock(self.norte, self.cable, 7)
        _p2d_stock(self.centro, self.old, 2)
        _p2d_stock(self.foreign_branch, self.foreign, 99)

        self.admin, _ = _p2d_member(self.company, 'exp_admin', ALL)
        self.api = _client(self.admin)
        self.receive(self.centro, {'serial_number': 'SER-0001', 'imei': IMEI_A, 'cost': '3800.00'},
                     {'serial_number': 'SER-0002', 'imei': IMEI_B, 'condition': 'used'})
        self.receive(self.norte, {'serial_number': 'SER-0003', 'imei': IMEI_C, 'imei2': IMEI_D,
                                  'condition': 'open_box', 'cost': '3650.50'})

    @staticmethod
    def branch(company, name):
        """The branch a new company is born with, under the name this test uses."""
        branch = company.branches.order_by('pk').first()
        if branch is None:
            return _p2d_branch(company, name)
        branch.name = name
        branch.save(update_fields=['name'])
        return branch

    def product(self, name, slug, price, category, **fields):
        return Product.objects.create(
            company=self.company, name=name, slug=slug, price=Decimal(price), category=category, **fields)

    def code(self, product, code, symbology=None):
        return ProductBarcode.objects.create(
            company=product.company, product=product, code=code,
            symbology=symbology or ProductBarcode.INTERNAL)

    def receive(self, branch, *rows, product=None):
        return stock_unit_services.receive_units(
            branch=branch, product=product or self.phone, units=list(rows), actor=self.admin,
            reason='Compra de prueba')

    def member(self, name, *capabilities, branches=None, company=None):
        user, _ = _p2d_member(company or self.company, name, ['company.view', *capabilities], branches=branches)
        return _client(user)

    def get(self, kind, client=None, **query):
        return (client or self.api).get(url(kind, **query))

    def sheet(self, response):
        """The ONE sheet of a workbook response: (title, headers, rows as lists)."""
        self.assertEqual(response.status_code, 200, response.content[:300])
        book = load_workbook(io.BytesIO(response.content))
        self.assertEqual(len(book.sheetnames), 1, book.sheetnames)
        page = book[book.sheetnames[0]]
        rows = [list(row) for row in page.iter_rows(values_only=True)]
        return page.title, rows[0], rows[1:]

    def records(self, response):
        _title, headers, rows = self.sheet(response)
        return [dict(zip(headers, row)) for row in rows]

    def exported(self):
        return AdminAuditLog.objects.filter(action='inventory_exported')


# =============================================================================
# Who may download what
# =============================================================================

class ExportAccessTest(_Base):
    def test_each_download_needs_its_own_capability(self):
        catalogue = self.member('exp_catalogo', 'products.view')
        warehouse = self.member('exp_almacen', 'inventory.view')
        both = self.member('exp_ambos', 'products.view', 'inventory.view')
        nobody = self.member('exp_nadie')
        expected = {
            #            catalogue  warehouse  both  nobody
            'products': (200, 403, 200, 403),
            'stock':    (403, 200, 200, 403),
            'units':    (403, 200, 200, 403),
            'full':     (403, 403, 200, 403),
        }
        for kind, codes in expected.items():
            for client, code in zip((catalogue, warehouse, both, nobody), codes):
                with self.subTest(kind=kind, code=code):
                    self.assertEqual(self.get(kind, client).status_code, code)

    def test_without_a_session_nothing_downloads(self):
        for kind in inventory_exports.KINDS:
            self.assertIn(self.get(kind, APIClient()).status_code, (401, 403))

    def test_managing_or_adjusting_is_not_what_opens_a_download(self):
        """`products.manage` no lee el catálogo; `inventory.adjust` no lee el stock."""
        writer = self.member('exp_escritora', 'products.manage', 'inventory.adjust')
        for kind in inventory_exports.KINDS:
            self.assertEqual(self.get(kind, writer).status_code, 403)

    def test_another_company_downloads_its_own_and_never_this_one(self):
        stranger = self.member('exp_ajena', *ALL[1:], company=self.other)
        names = [row['Nombre'] for row in self.records(self.get('products', stranger))]
        self.assertEqual(names, ['Producto ajeno'])
        _title, headers, rows = self.sheet(self.get('stock', stranger))
        self.assertEqual(headers[3:], ['ALMACEN Ajena'])
        self.assertEqual([row[2] for row in rows], ['Producto ajeno'])
        self.assertEqual(self.records(self.get('units', stranger)), [])

    def test_the_company_never_comes_from_the_request(self):
        stranger = self.member('exp_ajena', *ALL[1:], company=self.other)
        for kind in inventory_exports.KINDS:
            with self.subTest(kind):
                self.assertIn(stranger.get(url(kind, company=self.company.pk)).status_code, (403, 404))

    def test_a_branch_limited_person_downloads_only_their_branches(self):
        limited = self.member('exp_centro', 'inventory.view', 'products.view', branches=[self.centro])

        _title, headers, _rows = self.sheet(self.get('stock', limited))
        self.assertEqual(headers[3:], ['ALMACEN Centro'])
        self.assertEqual({row['Sucursal'] for row in self.records(self.get('stock', limited, mode='report'))}, {'Centro'})
        self.assertEqual(
            [row['Número de serie'] for row in self.records(self.get('units', limited))], ['SER-0001', 'SER-0002'])

        book = zipfile.ZipFile(io.BytesIO(self.get('full', limited).content))
        devices = load_workbook(io.BytesIO(book.read('Equipos.xlsx'))).active
        self.assertEqual({row[2] for row in devices.iter_rows(min_row=2, values_only=True)}, {'Centro'})

    def test_a_branch_out_of_reach_answers_like_one_that_does_not_exist(self):
        limited = self.member('exp_centro', 'inventory.view', 'products.view', branches=[self.centro])
        for kind in ('stock', 'units', 'full'):
            with self.subTest(kind):
                out_of_reach = self.get(kind, limited, branches=self.norte.pk)
                foreign = self.get(kind, limited, branches=self.foreign_branch.pk)
                unknown = self.get(kind, limited, branches=987654)
                mixed = self.get(kind, limited, branches=f'{self.centro.pk},{self.norte.pk}')
                self.assertEqual([r.status_code for r in (out_of_reach, foreign, unknown, mixed)], [404] * 4)
                self.assertEqual(out_of_reach.content, foreign.content)
                self.assertEqual(out_of_reach.content, unknown.content)
                self.assertEqual(self.get(kind, limited, branches='centro').status_code, 400)
                self.assertEqual(self.get(kind, limited, branches=self.centro.pk).status_code, 200)

    def test_another_company_s_category_answers_like_one_that_does_not_exist(self):
        for kind in inventory_exports.KINDS:
            with self.subTest(kind):
                foreign = self.get(kind, category=self.foreign_category.pk)
                unknown = self.get(kind, category=987654)
                self.assertEqual((foreign.status_code, unknown.status_code), (404, 404))
                self.assertEqual(foreign.content, unknown.content)
                self.assertEqual(self.get(kind, category='accesorios').status_code, 400)

    def test_a_filter_value_that_means_nothing_is_refused(self):
        for query in ({'mode': 'todo'}, {'type': 'telefono'}, {'stock': 'low_stock'}, {'condition': 'roto'},
                      {'visibility': 'web'}, {'is_active': 'si'}):
            with self.subTest(query=query):
                self.assertEqual(self.get('products', **query).status_code, 400)
                self.assertEqual(self.get('units', **query).status_code, 400)

    def test_the_full_download_is_only_for_reloading(self):
        self.assertEqual(self.get('full', mode='report').status_code, 400)
        self.assertEqual(self.get('full', mode='reimport').status_code, 200)

    def test_an_unknown_kind_is_not_found_and_only_get_is_allowed(self):
        self.assertEqual(self.api.get('/api/admin/inventory/exports/kardex/').status_code, 404)
        for method in ('post', 'put', 'patch', 'delete'):
            self.assertEqual(getattr(self.api, method)(url('products')).status_code, 405)

    def test_the_file_names_are_fixed_and_nothing_is_cached(self):
        names = {
            ('products', 'reimport'): 'productos-para-cargar.xlsx', ('products', 'report'): 'reporte-productos.xlsx',
            ('stock', 'reimport'): 'stock-para-cargar.xlsx', ('stock', 'report'): 'reporte-stock.xlsx',
            ('units', 'reimport'): 'equipos-para-cargar.xlsx', ('units', 'report'): 'reporte-equipos.xlsx',
            ('full', 'reimport'): 'inventario-completo.zip',
        }
        for (kind, mode), name in names.items():
            with self.subTest(kind=kind, mode=mode):
                response = self.get(kind, mode=mode)
                self.assertEqual(response['Content-Disposition'], f'attachment; filename="{name}"')
                self.assertEqual(response['Cache-Control'], 'no-store')
                self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
                self.assertEqual(response['Content-Type'], 'application/zip' if kind == 'full' else XLSX)

    def test_downloads_are_throttled(self):
        from store.inventory_export_views import AdminInventoryExportsView
        from store.throttles import AdminImportThrottle

        self.assertEqual(AdminInventoryExportsView.throttle_classes, [AdminImportThrottle])

    def test_each_download_leaves_one_audit_line_without_any_cell(self):
        self.get('units', mode='report', branches=self.centro.pk, condition='used', category=self.phones.pk)

        entry = self.exported().get()
        self.assertEqual(entry.company, self.company)
        self.assertEqual(entry.actor, self.admin)
        self.assertEqual(entry.target_type, 'inventory_export')
        self.assertEqual(entry.metadata, {
            'kind': 'units', 'mode': 'report',
            'filters': {
                'branches': [self.centro.pk], 'category': self.phones.pk, 'type': '', 'stock': '',
                'condition': 'used', 'visibility': '', 'is_active': '',
            },
            'rows': {'units': 1}, 'cost_included': True,
        })
        text = json.dumps(entry.metadata)
        for value in ('SER-0002', IMEI_B, 'Teléfono X'):
            self.assertNotIn(value, text)

    def test_the_full_download_counts_its_three_files_in_one_line(self):
        self.get('full')
        self.assertEqual(self.exported().get().metadata['rows'], {'products': 3, 'stock': 2, 'units': 3})

    def test_a_refused_download_leaves_no_audit_line(self):
        self.get('stock', branches=self.foreign_branch.pk)
        self.get('products', self.member('exp_nadie'))
        self.assertEqual(self.exported().count(), 0)


# =============================================================================
# The catalogue
# =============================================================================

class ProductsExportTest(_Base):
    def test_to_reload_it_is_the_product_template_one_row_per_product(self):
        title, headers, rows = self.sheet(self.get('products'))

        self.assertEqual(title, 'Productos')
        self.assertEqual(headers, import_exports.PRODUCT_TEMPLATE_HEADERS)
        self.assertEqual(rows, [
            [None, None, 'Batería vieja', None, 80, None, None, None, 'Publicar en e-commerce'],
            [None, '00123', 'Cable', None, 25, 'Accesorios', None, None, 'Solo stock interno'],
            ['7751234567892', 'FUN-001', 'Funda', 'Funda de silicona.', 59.9, 'Accesorios', None, None,
             'Publicar en e-commerce'],
            [None, 'TEL-X', 'Teléfono X', None, 4500, 'Teléfonos', None, None, 'Publicar en e-commerce'],
        ])

    def test_codes_are_text_so_no_zero_is_lost(self):
        page = load_workbook(io.BytesIO(self.get('products').content)).active
        cable = next(row for row in page.iter_rows(min_row=2) if row[2].value == 'Cable')
        funda = next(row for row in page.iter_rows(min_row=2) if row[2].value == 'Funda')
        self.assertEqual((cable[1].value, cable[1].data_type, cable[1].number_format), ('00123', 's', '@'))
        self.assertEqual((funda[0].value, funda[0].data_type, funda[0].number_format), ('7751234567892', 's', '@'))

    def test_the_filters(self):
        def names(**query):
            return [row['Nombre'] for row in self.records(self.get('products', **query))]

        self.assertEqual(names(category=self.accessories.pk), ['Cable', 'Funda'])
        self.assertEqual(names(type='serialized'), ['Teléfono X'])
        self.assertEqual(names(type='quantity'), ['Batería vieja', 'Cable', 'Funda'])
        self.assertEqual(names(visibility='internal'), ['Cable'])
        self.assertEqual(names(visibility='published'), ['Batería vieja', 'Funda', 'Teléfono X'])
        self.assertEqual(names(is_active='false'), ['Batería vieja'])
        self.assertEqual(names(is_active='true'), ['Cable', 'Funda', 'Teléfono X'])
        # Existence, in the catalogue, is the company's total.
        Product.objects.filter(pk=self.cable.pk).update(inventory=0)
        self.assertEqual(names(stock='in_stock'), ['Batería vieja', 'Funda', 'Teléfono X'])
        self.assertEqual(names(stock='out_of_stock'), ['Cable'])
        self.assertEqual(names(stock='in_stock', type='quantity', is_active='true'), ['Funda'])

    def test_the_report_says_it_is_one_and_adds_what_is_only_read(self):
        response = self.get('products', mode='report')
        title, headers, _rows = self.sheet(response)

        self.assertEqual(title, 'Reporte de productos')
        self.assertEqual(headers, import_exports.PRODUCT_TEMPLATE_HEADERS + [
            'Activo', 'Tipo', 'Stock total', 'Publicado', 'Problemas para publicar'])
        rows = {row['Nombre']: row for row in self.records(response)}
        self.assertEqual(
            [rows['Funda'][key] for key in headers[9:]], ['Sí', 'Por cantidad', 5, 'Sí', None])
        self.assertEqual(
            [rows['Teléfono X'][key] for key in headers[9:]], ['Sí', 'Por serie', 3, 'Sí', None])
        self.assertEqual(
            [rows['Batería vieja'][key] for key in headers[9:]],
            ['No', 'Por cantidad', 2, 'Sí', 'El producto está inactivo. No tiene imagen. No tiene categoría.'])
        self.assertEqual(rows['Cable']['Publicado'], 'No')

    def test_reloading_it_into_the_same_shop_changes_nothing(self):
        before = list(Product.objects.filter(company=self.company).order_by('pk').values())
        codes = list(ProductBarcode.objects.order_by('pk').values())

        staged = self.api.post(
            '/api/admin/products/import/preview/', {'file': upload(self.get('products').content)}, format='multipart')

        self.assertEqual(staged.status_code, 201, staged.content)
        counts = staged.json()['counts']
        self.assertEqual((counts['error'], counts['create'], counts['total']), (0, 0, 4))
        applied = self.api.post(f'/api/admin/products/import/{staged.json()["id"]}/apply/')
        self.assertEqual(applied.status_code, 200, applied.content)
        self.assertEqual(
            applied.json()['summary']['applied'],
            {'created': 0, 'updated': 0, 'images_added': 0, 'destination_changed': 0})
        self.assertEqual(list(Product.objects.filter(company=self.company).order_by('pk').values()), before)
        self.assertEqual(list(ProductBarcode.objects.order_by('pk').values()), codes)
        self.assertFalse(AdminAuditLog.objects.filter(action='product_destination_changed').exists())

    def test_the_product_importer_refuses_the_report(self):
        """Elegido y fijado: un reporte no se importa, ni en parte."""
        report = self.get('products', mode='report').content
        response = self.api.post('/api/admin/products/import/preview/', {'file': upload(report)}, format='multipart')

        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('es un reporte', response.json()['detail'])
        self.assertIn('para volver a cargar', response.json()['detail'])
        self.assertFalse(BulkImportJob.objects.exists())


# =============================================================================
# Quantities by branch
# =============================================================================

class StockExportTest(_Base):
    MAPPING = json.dumps({'code': 0, 'barcode': 1, 'name': 2})

    def reload(self, content, columns=None):
        columns = columns or {3: self.centro.pk, 4: self.norte.pk}
        return self.api.post('/api/admin/inventory/import/preview/', {
            'file': upload(content), 'mapping': self.MAPPING, 'header_row': 1,
            'branch_map': json.dumps({str(index): branch for index, branch in columns.items()}),
        }, format='multipart')

    def test_to_reload_it_has_the_inventory_shape_without_the_serialized_products(self):
        title, headers, rows = self.sheet(self.get('stock'))

        self.assertEqual(title, 'Inventario')
        self.assertEqual(headers, ['CODIGO', 'CODIGO EAN', 'NOMBRE', 'ALMACEN Centro', 'ALMACEN Norte'])
        self.assertEqual(rows, [
            [None, None, 'Batería vieja', 2, 0],
            ['00123', None, 'Cable', 0, 7],
            ['FUN-001', '7751234567892', 'Funda', 4, 1],
        ])

    def test_reloading_it_has_no_error_row_and_moves_nothing(self):
        def held():
            # Absence and zero are the same shelf: the importer may create the
            # row of a pair that had none, with nothing in it.
            return {
                (branch, product): quantity
                for branch, product, quantity in BranchStock.objects.values_list('branch_id', 'product_id', 'quantity')
                if quantity
            }

        movements = StockMovement.objects.count()
        stock, totals = held(), dict(Product.objects.values_list('pk', 'inventory'))

        staged = self.reload(self.get('stock').content)

        self.assertEqual(staged.status_code, 201, staged.content)
        counts = staged.json()['counts']
        self.assertEqual((counts['error'], counts['update'], counts['create']), (0, 0, 0))
        self.assertEqual(counts['no_change'], 6)                      # three products, two branches
        applied = self.api.post(f'/api/admin/inventory/import/{staged.json()["id"]}/apply/')
        self.assertEqual(applied.status_code, 200, applied.content)
        self.assertEqual(StockMovement.objects.count(), movements)
        self.assertEqual(held(), stock)
        self.assertEqual(dict(Product.objects.values_list('pk', 'inventory')), totals)

    def test_the_mismatch_this_fixes(self):
        """
        La descarga de siempre incluye los productos con serie, con su número, y
        el importador de stock rechaza cualquier celda con valor de uno de ellos:
        el archivo que la plataforma entregaba no volvía a entrar.
        """
        old = self.api.get('/api/admin/inventory/export/')
        staged = self.reload(old.content)
        self.assertEqual(staged.json()['counts']['error'], 2)          # Teléfono X, en dos sucursales

    def test_the_filters(self):
        def names(**query):
            return [row[2] for row in self.sheet(self.get('stock', **query))[2]]

        self.assertEqual(self.sheet(self.get('stock', branches=self.norte.pk))[1][3:], ['ALMACEN Norte'])
        self.assertEqual(names(category=self.accessories.pk), ['Cable', 'Funda'])
        self.assertEqual(names(is_active='true'), ['Cable', 'Funda'])
        self.assertEqual(names(visibility='internal'), ['Cable'])
        self.assertEqual(names(type='serialized'), [])
        # Existence is counted in the branches being downloaded.
        self.assertEqual(names(stock='in_stock', branches=self.centro.pk), ['Batería vieja', 'Funda'])
        self.assertEqual(names(stock='out_of_stock', branches=self.centro.pk), ['Cable'])

    def test_the_report_is_one_row_per_product_and_branch_with_the_devices_counted(self):
        response = self.get('stock', mode='report')
        title, headers, _rows = self.sheet(response)

        self.assertEqual(title, 'Reporte de stock')
        self.assertEqual(headers, ['Código', 'Código de barras', 'Producto', 'Categoría', 'Tipo', 'Sucursal', 'Cantidad'])
        found = {(row['Producto'], row['Sucursal']): (row['Tipo'], row['Cantidad']) for row in self.records(response)}
        self.assertEqual(found, {
            ('Batería vieja', 'Centro'): ('Por cantidad', 2), ('Batería vieja', 'Norte'): ('Por cantidad', 0),
            ('Cable', 'Centro'): ('Por cantidad', 0), ('Cable', 'Norte'): ('Por cantidad', 7),
            ('Funda', 'Centro'): ('Por cantidad', 4), ('Funda', 'Norte'): ('Por cantidad', 1),
            ('Teléfono X', 'Centro'): ('Por serie', 2), ('Teléfono X', 'Norte'): ('Por serie', 1),
        })
        in_stock = self.records(self.get('stock', mode='report', stock='in_stock', type='quantity'))
        self.assertEqual(
            [(row['Producto'], row['Sucursal']) for row in in_stock],
            [('Batería vieja', 'Centro'), ('Cable', 'Norte'), ('Funda', 'Centro'), ('Funda', 'Norte')])

    def test_the_stock_importer_refuses_the_report(self):
        report = self.get('stock', mode='report').content
        response = self.api.post('/api/admin/inventory/import/preview/', {
            'file': upload(report), 'mapping': json.dumps({'code': 0, 'barcode': 1, 'name': 2}),
            'branch_map': json.dumps({'6': self.centro.pk}),
        }, format='multipart')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('es un reporte', response.json()['detail'])
        self.assertFalse(BulkImportJob.objects.exists())


# =============================================================================
# Devices, one row each
# =============================================================================

class UnitsExportTest(_Base):
    def test_to_reload_it_is_the_device_template_one_row_per_unit_on_the_shelf(self):
        title, headers, rows = self.sheet(self.get('units'))

        self.assertEqual(title, unit_import_services.SHEET)
        self.assertEqual(headers, unit_import_services.HEADERS)
        self.assertEqual(rows, [
            ['TEL-X', 'Teléfono X', 'Centro', 'SER-0001', IMEI_A, None, 'Nuevo', 3800, None],
            ['TEL-X', 'Teléfono X', 'Centro', 'SER-0002', IMEI_B, None, 'Usado', None, None],
            ['TEL-X', 'Teléfono X', 'Norte', 'SER-0003', IMEI_C, IMEI_D, 'Caja abierta', 3650.5, None],
        ])

    def test_serials_and_imeis_are_text(self):
        page = load_workbook(io.BytesIO(self.get('units').content)).active
        first = next(page.iter_rows(min_row=2))
        for index in (0, 3, 4):
            self.assertEqual((first[index].data_type, first[index].number_format), ('s', '@'))
        self.assertEqual(first[4].value, IMEI_A)

    def test_the_cost_is_only_for_who_may_adjust_inventory(self):
        reader = self.member('exp_lectora', 'inventory.view')
        for mode in ('reimport', 'report'):
            with self.subTest(mode):
                self.assertEqual({row['Costo'] for row in self.records(self.get('units', reader, mode=mode))}, {None})
                self.assertIn(3800, {row['Costo'] for row in self.records(self.get('units', mode=mode))})
        both = self.get('full', self.member('exp_dos', 'inventory.view', 'products.view'))
        devices = load_workbook(io.BytesIO(zipfile.ZipFile(io.BytesIO(both.content)).read('Equipos.xlsx'))).active
        self.assertEqual({row[7] for row in devices.iter_rows(min_row=2, values_only=True)}, {None})
        mine = load_workbook(io.BytesIO(zipfile.ZipFile(io.BytesIO(self.get('full').content)).read('Equipos.xlsx'))).active
        self.assertIn(3800, {row[7] for row in mine.iter_rows(min_row=2, values_only=True)})
        self.assertIs(self.exported().filter(actor__username='exp_lectora').first().metadata['cost_included'], False)

    def out_of_the_shelf(self):
        """One device written off and one sold, the second with its order."""
        gone, sold = self.receive(self.centro, {'serial_number': 'SER-0004'}, {'serial_number': 'SER-0005'},
                                  product=self.laptop())
        stock_unit_services.write_off(gone, reason='Pantalla rota', actor=self.admin)
        order = Order.objects.create(
            company=self.company, fulfillment_branch=self.centro, customer_email='compra@correo.test',
            total=Decimal('5200.00'), status=Order.Status.PAID, paid=True, paid_at=timezone.now())
        StockUnit.objects.filter(pk=sold.pk).update(
            status=StockUnit.Status.SOLD, sold_at=timezone.now(), order=order, notes='Entregado en tienda')
        BranchStock.objects.filter(branch=self.centro, product=sold.product).update(quantity=F('quantity') - 1)
        return order

    def laptop(self):
        laptop = self.product('Portátil', 'portatil', '5200.00', self.phones, is_serialized=True)
        self.code(laptop, 'PORT-1')
        return laptop

    def test_what_left_the_shelf_is_not_in_the_file_to_reload(self):
        self.out_of_the_shelf()
        serials = [row['Número de serie'] for row in self.records(self.get('units'))]
        self.assertEqual(serials, ['SER-0001', 'SER-0002', 'SER-0003'])
        # Asking for it does not bring it back: reloading it would put it on sale.
        self.assertEqual(self.records(self.get('units', stock='out_of_stock')), [])

    def test_the_report_has_every_device_with_its_state_date_order_and_notes(self):
        order = self.out_of_the_shelf()
        response = self.get('units', mode='report')
        title, headers, _rows = self.sheet(response)

        self.assertEqual(title, 'Reporte de equipos')
        self.assertEqual(headers, unit_import_services.HEADERS[:-1] + ['Estado', 'Fecha de ingreso', 'Pedido', 'Notas'])
        rows = {row['Número de serie']: row for row in self.records(response)}
        self.assertEqual(set(rows), {'SER-0001', 'SER-0002', 'SER-0003', 'SER-0004', 'SER-0005'})
        self.assertEqual((rows['SER-0001']['Estado'], rows['SER-0001']['Pedido']), ('Disponible', None))
        self.assertEqual(rows['SER-0004']['Estado'], 'Dado de baja')
        self.assertEqual(
            (rows['SER-0005']['Estado'], rows['SER-0005']['Pedido'], rows['SER-0005']['Notas']),
            ('Vendido', order.pk, 'Entregado en tienda'))
        self.assertRegex(rows['SER-0001']['Fecha de ingreso'], r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$')

        gone = self.records(self.get('units', mode='report', stock='out_of_stock'))
        self.assertEqual([row['Número de serie'] for row in gone], ['SER-0004', 'SER-0005'])
        there = self.records(self.get('units', mode='report', stock='in_stock'))
        self.assertEqual(len(there), 3)

    def test_the_filters(self):
        def serials(**query):
            return [row['Número de serie'] for row in self.records(self.get('units', **query))]

        self.receive(self.norte, {'serial_number': 'SER-0009'}, product=self.laptop())
        self.assertEqual(serials(branches=self.norte.pk), ['SER-0009', 'SER-0003'])
        self.assertEqual(serials(condition='used'), ['SER-0002'])
        self.assertEqual(serials(condition='open_box'), ['SER-0003'])
        self.assertEqual(serials(category=self.accessories.pk), [])
        self.assertEqual(serials(type='quantity'), [])
        Product.objects.filter(pk=self.phone.pk).update(is_published_online=False, is_active=False)
        self.assertEqual(serials(visibility='published'), ['SER-0009'])
        self.assertEqual(serials(is_active='false'), ['SER-0001', 'SER-0002', 'SER-0003'])

    def test_reloading_it_registers_no_device_twice(self):
        """Cada fila se rechaza porque ese equipo ya está: un rechazo limpio, no stock doble."""
        units = list(StockUnit.objects.order_by('pk').values())
        movements = StockMovement.objects.count()
        stock = list(BranchStock.objects.order_by('pk').values())

        staged = self.api.post(
            '/api/admin/inventory/units/import/preview/',
            {'file': upload(self.get('units').content), 'reason': 'Recarga de prueba'}, format='multipart')

        self.assertEqual(staged.status_code, 201, staged.content)
        body = staged.json()
        self.assertEqual((body['counts']['create'], body['counts']['error'], body['counts']['total']), (0, 3, 3))
        for row in body['rows']:
            self.assertIn('ya está registrado', ' '.join(row['errors']))
            self.assertNotIn('No existe', ' '.join(row['errors']))
        applied = self.api.post(f'/api/admin/inventory/units/import/{body["id"]}/apply/')
        self.assertEqual(applied.status_code, 400, applied.content)
        self.assertEqual(list(StockUnit.objects.order_by('pk').values()), units)
        self.assertEqual(StockMovement.objects.count(), movements)
        self.assertEqual(list(BranchStock.objects.order_by('pk').values()), stock)

    def test_the_device_importer_refuses_the_report(self):
        report = self.get('units', mode='report').content
        response = self.api.post(
            '/api/admin/inventory/units/import/preview/', {'file': upload(report), 'reason': 'x'}, format='multipart')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('es un reporte', response.json()['detail'])
        self.assertFalse(BulkImportJob.objects.exists())


# =============================================================================
# Everything, in one ZIP
# =============================================================================

class FullExportTest(_Base):
    def archive(self, **query):
        response = self.get('full', **query)
        self.assertEqual(response.status_code, 200, response.content[:300])
        return zipfile.ZipFile(io.BytesIO(response.content))

    def page(self, archive, name):
        book = load_workbook(io.BytesIO(archive.read(name)))
        self.assertEqual(len(book.sheetnames), 1, name)
        return book.active

    def test_three_workbooks_of_one_sheet_and_a_note(self):
        archive = self.archive()
        self.assertEqual(archive.namelist(), ['LEEME.txt', 'Productos.xlsx', 'Stock.xlsx', 'Equipos.xlsx'])
        self.assertIsNone(archive.testzip())
        self.assertEqual(self.page(archive, 'Productos.xlsx').title, 'Productos')
        self.assertEqual(self.page(archive, 'Stock.xlsx').title, 'Inventario')
        self.assertEqual(self.page(archive, 'Equipos.xlsx').title, 'Equipos')

    def test_they_are_the_files_to_reload_of_the_active_products(self):
        archive = self.archive()
        products = self.page(archive, 'Productos.xlsx')
        stock = self.page(archive, 'Stock.xlsx')
        devices = self.page(archive, 'Equipos.xlsx')

        self.assertEqual([cell.value for cell in products[1]], import_exports.PRODUCT_TEMPLATE_HEADERS)
        self.assertEqual([row[2] for row in products.iter_rows(min_row=2, values_only=True)],
                         ['Cable', 'Funda', 'Teléfono X'])
        self.assertEqual([row[2] for row in stock.iter_rows(min_row=2, values_only=True)], ['Cable', 'Funda'])
        self.assertEqual([cell.value for cell in devices[1]], unit_import_services.HEADERS)
        self.assertEqual([row[3] for row in devices.iter_rows(min_row=2, values_only=True)],
                         ['SER-0001', 'SER-0002', 'SER-0003'])
        # An inactive product is in none of the three, whatever is asked for.
        asked = self.archive(is_active='false')
        self.assertEqual([row[2] for row in self.page(asked, 'Productos.xlsx').iter_rows(min_row=2, values_only=True)],
                         ['Cable', 'Funda', 'Teléfono X'])

    def test_the_note_says_what_each_file_is_the_order_and_that_the_same_shop_does_not_change(self):
        note = self.archive().read('LEEME.txt').decode('utf-8')
        for name in ('Productos.xlsx', 'Stock.xlsx', 'Equipos.xlsx'):
            self.assertIn(name, note)
        self.assertLess(note.index('1. Productos.xlsx'), note.index('2. Stock.xlsx'))
        self.assertLess(note.index('2. Stock.xlsx'), note.index('3. Equipos.xlsx'))
        self.assertIn('tienda vacía', note)
        self.assertIn('MISMA tienda no cambia nada', note)
        self.assertIn('con número de serie', note)
        # It names no shop: the note is the platform's, the data is the tenant's.
        self.assertNotIn('Tienda A', note)

    def test_an_archive_over_the_size_cap_is_refused(self):
        with patch.object(inventory_exports, 'MAX_ZIP_BYTES', 200):
            response = self.get('full')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()['code'], 'too_large')
        self.assertEqual(self.exported().count(), 0)


# =============================================================================
# The files themselves
# =============================================================================

class FileSafetyTest(_Base):
    DANGEROUS = ['=HYPERLINK("http://malo.example","clic")', '+SUM(1,2)', '-2+3', '@SUM(1)', "'=YA-CON-COMILLA"]

    def setUp(self):
        super().setUp()
        self.risky = [
            self.product(name, f'riesgo-{index}', '10.00', self.accessories, description=f'={index}+1')
            for index, name in enumerate(self.DANGEROUS)
        ]
        for index, product in enumerate(self.risky):
            self.code(product, f'RIESGO-{index}')

    def cells(self, response):
        page = load_workbook(io.BytesIO(response.content)).active
        return [cell for row in page.iter_rows(min_row=2) for cell in row if cell.value is not None]

    def test_no_text_cell_can_run_as_a_formula(self):
        for kind, mode in (('products', 'reimport'), ('products', 'report'), ('stock', 'reimport'), ('stock', 'report')):
            with self.subTest(kind=kind, mode=mode):
                cells = self.cells(self.get(kind, mode=mode))
                self.assertFalse([cell.value for cell in cells if cell.data_type == 'f'])
                texts = [cell.value for cell in cells if isinstance(cell.value, str)]
                self.assertFalse([text for text in texts if text[:1] in ('=', '+', '-', '@', '\t', '\r')])
                self.assertIn('\'=HYPERLINK("http://malo.example","clic")', texts)
                self.assertIn("'+SUM(1,2)", texts)
                self.assertIn("''=YA-CON-COMILLA", texts)

    def test_a_branch_a_category_and_a_note_are_guarded_too(self):
        self.centro.name = '+Centro'
        self.centro.save(update_fields=['name'])
        Category.objects.filter(pk=self.phones.pk).update(name='=Teléfonos')
        StockUnit.objects.filter(serial_number='SER-0001').update(notes='@nota')

        report = [cell.value for cell in self.cells(self.get('units', mode='report'))]
        self.assertIn("'+Centro", report)
        self.assertIn("'@nota", report)
        catalogue = [cell.value for cell in self.cells(self.get('products'))]
        self.assertIn("'=Teléfonos", catalogue)

    def test_the_guard_comes_off_exactly_when_the_file_is_read_back(self):
        for value in [*self.DANGEROUS, '\tcon tabulador', 'normal', "'comilla suelta", '', "''dos"]:
            with self.subTest(value=value):
                self.assertEqual(xlsx_reader.unguard_text(xlsx_reader.guard_text(value)), value)
        self.assertEqual(xlsx_reader.guard_text('normal'), 'normal')
        self.assertEqual(xlsx_reader.guard_text("'comilla suelta"), "'comilla suelta")
        self.assertEqual(xlsx_reader.unguard_text("'comilla suelta"), "'comilla suelta")

    def test_reloading_the_guarded_catalogue_reads_the_same_names_and_changes_nothing(self):
        before = list(Product.objects.filter(company=self.company).order_by('pk').values())

        staged = self.api.post(
            '/api/admin/products/import/preview/', {'file': upload(self.get('products').content)}, format='multipart')

        self.assertEqual(staged.status_code, 201, staged.content)
        body = staged.json()
        self.assertEqual((body['counts']['error'], body['counts']['create']), (0, 0))
        read_back = {row['data']['name'] for row in body['rows']}
        for name in self.DANGEROUS:
            self.assertIn(name, read_back)
        applied = self.api.post(f'/api/admin/products/import/{body["id"]}/apply/')
        self.assertEqual(applied.json()['summary']['applied']['updated'], 0, applied.content)
        self.assertEqual(list(Product.objects.filter(company=self.company).order_by('pk').values()), before)

    def test_reloading_the_guarded_stock_finds_every_product(self):
        staged = self.api.post('/api/admin/inventory/import/preview/', {
            'file': upload(self.get('stock').content), 'mapping': StockExportTest.MAPPING, 'header_row': 1,
            'branch_map': json.dumps({'3': self.centro.pk, '4': self.norte.pk}),
        }, format='multipart')
        self.assertEqual(staged.status_code, 201, staged.content)
        self.assertEqual((staged.json()['counts']['error'], staged.json()['counts']['update']), (0, 0))

    def test_a_real_formula_in_an_upload_is_still_refused(self):
        """Quitar la guarda no abre la puerta: una celda que ES una fórmula sigue siendo un error de la fila."""
        from openpyxl import Workbook

        book = Workbook()
        page = book.active
        page.title = 'Productos'
        page.append(import_exports.PRODUCT_TEMPLATE_HEADERS)
        page.append(['', 'NUEVO-1', '=1+1', '', 10, '', '', '', ''])
        out = io.BytesIO()
        book.save(out)
        staged = self.api.post('/api/admin/products/import/preview/', {'file': upload(out.getvalue())}, format='multipart')
        self.assertEqual(staged.json()['counts']['error'], 1, staged.content)

    def test_the_error_report_of_a_load_cannot_run_as_a_formula_either(self):
        """Review O1: errors.csv is opened in a spreadsheet, and it wrote what the upload carried."""
        import csv

        from store.models import BulkImportRow

        job = BulkImportJob.objects.create(company=self.company, import_type=BulkImportJob.PRODUCTS, rows_error=3)
        BulkImportRow.objects.create(
            job=job, sheet_name='=Hoja', row_number=2, action=BulkImportRow.ERROR, match_key='=HYPERLINK("http://malo.example","x")',
            normalized_data={'name': '+SUM(1,2)'}, errors=['@SUM(A1) no es un precio.'], warnings=['-2+3'])
        BulkImportRow.objects.create(
            job=job, sheet_name='Productos', row_number=3, action=BulkImportRow.ERROR, match_key='COD-1',
            normalized_data={'name': 'Funda'}, errors=['El precio no es un número.'], warnings=[])
        BulkImportRow.objects.create(
            job=job, sheet_name='Productos', row_number=4, action=BulkImportRow.ERROR, match_key='\tTAB',
            normalized_data={'name': "'=YA"}, errors=['x'], warnings=[])

        response = self.api.get(f'/api/admin/imports/{job.pk}/errors.csv/')

        self.assertEqual(response.status_code, 200)
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual(rows[0], ['Hoja', 'Fila', 'Identificador', 'Producto', 'Error', 'Advertencia'])
        self.assertEqual(rows[1], [
            "'=Hoja", '2', '\'=HYPERLINK("http://malo.example","x")', "'+SUM(1,2)", "'@SUM(A1) no es un precio.", "'-2+3"])
        # What is honest stays exactly as it was.
        self.assertEqual(rows[2], ['Productos', '3', 'COD-1', 'Funda', 'El precio no es un número.', ''])
        self.assertEqual(rows[3][2:4], ["'\tTAB", "''=YA"])
        for row in rows[1:]:
            self.assertEqual([cell for cell in row if cell[:1] in ('=', '+', '-', '@', '\t', '\r')], [])

    def test_a_character_a_workbook_cannot_hold_does_not_break_the_download(self):
        Product.objects.filter(pk=self.case.pk).update(name='Funda\x07 rota', description='línea\x0b dos')
        names = [row['Nombre'] for row in self.records(self.get('products'))]
        self.assertIn('Funda rota', names)

    def test_more_rows_than_the_cap_are_refused_with_the_count(self):
        with patch.object(inventory_exports, 'MAX_EXPORT_ROWS', 2):
            answers = {
                ('products', 'reimport'): self.get('products'),
                ('stock', 'report'): self.get('stock', mode='report'),
                ('units', 'reimport'): self.get('units'),
                ('full', 'reimport'): self.get('full'),
            }
        for key, response in answers.items():
            with self.subTest(key=key):
                self.assertEqual(response.status_code, 400, response.content[:200])
                self.assertEqual((response.json()['code'], response.json()['limit']), ('too_many_rows', 2))
                self.assertGreater(response.json()['count'], 2)
        self.assertEqual(self.exported().count(), 0)

    def test_the_workbooks_are_written_in_write_only_mode(self):
        import openpyxl

        seen = []
        real = openpyxl.Workbook

        def spy(*args, **kwargs):
            seen.append(kwargs.get('write_only', False))
            return real(*args, **kwargs)

        with patch.object(openpyxl, 'Workbook', side_effect=spy):
            for kind, mode in (('products', 'reimport'), ('products', 'report'), ('stock', 'reimport'),
                               ('stock', 'report'), ('units', 'reimport'), ('units', 'report'), ('full', 'reimport')):
                self.assertEqual(self.get(kind, mode=mode).status_code, 200)
        self.assertEqual(seen, [True] * 9)


# =============================================================================
# The download that was already there
# =============================================================================

class LegacyInventoryExportTest(_Base):
    """`GET /api/admin/inventory/export/` answers exactly as it did."""

    URL = '/api/admin/inventory/export/'

    def test_same_sheet_same_columns_same_rows(self):
        response = self.api.get(self.URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Disposition'], 'attachment; filename="inventario-actual.xlsx"')
        book = load_workbook(io.BytesIO(response.content))
        self.assertEqual(book.sheetnames, ['Inventario'])
        rows = [list(row) for row in book.active.iter_rows(values_only=True)]
        self.assertEqual(rows[0], ['CODIGO', 'CODIGO EAN', 'NOMBRE', 'ALMACEN Centro', 'ALMACEN Norte'])
        # Active products only, the serialized one INCLUDED with its count — as before.
        self.assertEqual(rows[1:], [
            ['00123', None, 'Cable', 0, 7],
            ['FUN-001', '7751234567892', 'Funda', 4, 1],
            ['TEL-X', None, 'Teléfono X', 2, 1],
        ])
        page = book.active
        self.assertEqual((page['A2'].number_format, page['B2'].number_format), ('@', '@'))
        self.assertTrue(page['A1'].font.bold)
        self.assertEqual(page.freeze_panes, 'A2')

    def test_blank_for_counting_and_one_branch(self):
        response = self.api.get(f'{self.URL}?branches={self.norte.pk}&quantities=blank')
        self.assertEqual(response['Content-Disposition'], 'attachment; filename="inventario-para-conteo.xlsx"')
        rows = [list(row) for row in load_workbook(io.BytesIO(response.content)).active.iter_rows(values_only=True)]
        self.assertEqual(rows[0], ['CODIGO', 'CODIGO EAN', 'NOMBRE', 'ALMACEN Norte'])
        self.assertEqual(rows[1], ['00123', None, 'Cable', None])

    def test_it_still_asks_for_the_capability_to_adjust_and_stays_inside_the_caller_s_branches(self):
        self.assertEqual(self.member('exp_lectora', 'inventory.view').get(self.URL).status_code, 403)
        limited = self.member('exp_centro', 'inventory.adjust', branches=[self.centro])
        self.assertEqual(limited.get(f'{self.URL}?branches={self.norte.pk}').status_code, 404)
        self.assertEqual(limited.get(f'{self.URL}?branches={self.foreign_branch.pk}').status_code, 404)

    def test_a_name_or_a_code_that_starts_like_a_formula_is_written_as_text(self):
        """Review N1: the old download wrote them as live formula cells."""
        evil = self.product('=HYPERLINK("http://malo.example","clic")', 'formula-1', '10.00', self.accessories)
        self.code(evil, '=COD-1')
        self.code(evil, '+775000000001', ProductBarcode.EAN13)
        plus = self.product('+SUM(1,2)', 'formula-2', '10.00', self.accessories)
        _p2d_stock(self.centro, evil, 3)

        response = self.api.get(self.URL)

        page = load_workbook(io.BytesIO(response.content)).active
        cells = [cell for row in page.iter_rows(min_row=2) for cell in row if cell.value is not None]
        self.assertEqual([cell.coordinate for cell in cells if cell.data_type == 'f'], [])
        texts = [cell.value for cell in cells if isinstance(cell.value, str)]
        self.assertEqual([text for text in texts if text[:1] in ('=', '+', '-', '@')], [])
        for guarded in ('\'=HYPERLINK("http://malo.example","clic")', "'=COD-1", "'+775000000001", "'+SUM(1,2)"):
            self.assertIn(guarded, texts)
        self.assertEqual(plus.name, '+SUM(1,2)')                       # the catalogue itself is untouched

    def test_the_guarded_download_still_reloads_and_finds_every_product(self):
        evil = self.product('=HYPERLINK("http://malo.example","clic")', 'formula-1', '10.00', self.accessories)
        self.code(evil, '=COD-1')
        _p2d_stock(self.centro, evil, 3)
        Product.objects.filter(pk=self.phone.pk).update(is_active=False)   # a serialized one would be refused: not this test

        staged = self.api.post('/api/admin/inventory/import/preview/', {
            'file': upload(self.api.get(self.URL).content), 'mapping': StockExportTest.MAPPING, 'header_row': 1,
            'branch_map': json.dumps({'3': self.centro.pk, '4': self.norte.pk}),
        }, format='multipart')

        self.assertEqual(staged.status_code, 201, staged.content)
        body = staged.json()
        self.assertEqual((body['counts']['error'], body['counts']['update']), (0, 0), body['rows'])
        self.assertIn(evil.pk, {row['data']['product_id'] for row in body['rows']})

    def test_it_leaves_no_export_audit_line_as_before(self):
        self.api.get(self.URL)
        self.assertEqual(self.exported().count(), 0)
