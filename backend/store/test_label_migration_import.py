"""
LABEL-MIGRATION-01 · importar los códigos históricos de un sistema anterior.

ADR `docs/adr-inventory-labels.md` §1.1 (decisión 2 del propietario), §2.10 y D12.
Lo que fijan estas pruebas, con datos FICTICIOS y nunca con el archivo real:

  * es la carga masiva de productos en «modo base histórica», no un importador nuevo;
  * un código es texto: ceros, mayúsculas, longitud y símbolos raros se guardan tal cual,
    la simbología sólo se nombra si su dígito de control cuadra y un código interno
    nunca se presenta como GTIN;
  * una celda numérica pide revisión; con decimales, en notación científica o con más
    de 15 dígitos se rechaza: Excel ya cambió el código y nunca se reconstruye;
  * la vista previa no escribe nada del catálogo y clasifica cada fila;
  * una fila reconocida por código cuyo nombre difiere no renombra el producto;
  * con filas a revisar, aplicar exige escribir cuántas son;
  * volver a subir el mismo archivo no cambia nada;
  * el código de otro producto se rechaza, nunca se reemplaza ni se esquiva;
  * otra empresa responde 404, sin permiso 403, y la caja encuentra lo importado.
"""
import io
import json

import openpyxl
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from store import import_exports, tenancy
from store.models import (
    AdminAuditLog, BulkImportJob, Category, Product, ProductBarcode, StockUnit,
)
from store.tests import _p2d_member, _p3_company

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
PREVIEW = '/api/admin/products/import/preview/'
TEMPLATE = '/api/admin/products/import/template/'
HISTORY = {'history_mode': True}

#: The columns of the historical template, in order.
HEADERS = ['Código de barras original', 'Código interno original', 'SKU antiguo', 'Nombre', 'Categoría',
           'Marca', 'Modelo', 'Variante', 'Precio comercial', 'Id externo']

# Fictitious codes. `EAN_ZERO` has a valid EAN-13 check digit and starts with a zero;
# `TWELVE_NOT_UPC` has twelve digits and a WRONG UPC-A check digit.
EAN_ZERO = '0750000000011'
TWELVE_NOT_UPC = '123456789013'
IMEI = '490154203237518'


def row(barcode=None, code=None, legacy=None, name='Producto ficticio', category=None, brand=None,
        model=None, variant=None, price=50, external=None):
    return [barcode, code, legacy, name, category, brand, model, variant, price, external]


def workbook(rows, headers=HEADERS):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = 'Productos'
    sheet.append(list(headers))
    for line in rows:
        sheet.append(list(line))
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('historica-uno', 'Tienda Histórica Uno')
        self.manager, _ = _p2d_member(
            self.company, 'historica_gestor', ['company.view', 'products.view', 'products.manage'])
        self.client = APIClient()
        self.client.force_authenticate(user=self.manager)
        self.category = Category.objects.create(company=self.company, name='Accesorios', slug='accesorios')

    def preview(self, data, *, options=None, client=None, company=None, filename='historica.xlsx', **fields):
        return (client or self.client).post(
            f'{PREVIEW}?company={(company or self.company).pk}',
            {'file': SimpleUploadedFile(filename, data, content_type=XLSX),
             'options': json.dumps(HISTORY if options is None else options), **fields},
            format='multipart',
        )

    def ok_preview(self, data, **kwargs):
        res = self.preview(data, **kwargs)
        self.assertEqual(res.status_code, 201, res.content)
        return res.json()

    def apply(self, job_id, *, confirm=None, client=None, company=None):
        body = {} if confirm is None else {'confirm_review': confirm}
        return (client or self.client).post(
            f'/api/admin/products/import/{job_id}/apply/?company={(company or self.company).pk}',
            body, format='json')

    def imported(self, rows, *, confirm=None, **kwargs):
        body = self.ok_preview(workbook(rows), **kwargs)
        self.assertEqual(body['counts']['error'], 0, body['rows'])
        res = self.apply(body['id'], confirm=confirm)
        self.assertEqual(res.status_code, 200, res.content)
        return body

    def product(self, name):
        return Product.objects.get(company=self.company, name=name)

    def codes(self, product):
        return {b.code: b.symbology for b in ProductBarcode.objects.filter(product=product)}

    def existing(self, name, *codes, price='50.00'):
        product = Product.objects.create(company=self.company, name=name, slug=name.lower().replace(' ', '-'),
                                         price=price, category=self.category)
        for index, code in enumerate(codes):
            ProductBarcode.objects.create(company=self.company, product=product, code=code,
                                          symbology=ProductBarcode.UNKNOWN, is_primary=index == 0)
        return product

    @staticmethod
    def by_row(body):
        return {line['row']: line for line in body['rows']}

    @staticmethod
    def history(body):
        return body['summary']['history']


class CodesAreKeptAsTextTest(_Base):
    def test_an_imported_code_is_kept_exactly(self):
        self.imported([row(barcode=EAN_ZERO, code='HX-0001', name='Funda ficticia', category='Accesorios')])

        codes = self.codes(self.product('Funda ficticia'))
        self.assertEqual(codes, {EAN_ZERO: ProductBarcode.EAN13, 'HX-0001': ProductBarcode.INTERNAL})

    def test_leading_zeros_survive(self):
        self.imported([row(code='000123', name='Cable ficticio')])
        self.assertEqual(set(self.codes(self.product('Cable ficticio'))), {'000123'})

    def test_case_is_kept(self):
        self.imported([row(code='abC1x', name='Mica ficticia')])
        self.assertEqual(set(self.codes(self.product('Mica ficticia'))), {'abC1x'})

    def test_an_unknown_symbology_is_kept_and_a_length_proves_nothing(self):
        self.imported([
            row(barcode='Q9#77-z/A', name='Soporte ficticio'),
            row(barcode=TWELVE_NOT_UPC, name='Cargador ficticio'),
        ])
        self.assertEqual(self.codes(self.product('Soporte ficticio')), {'Q9#77-z/A': ProductBarcode.UNKNOWN})
        # Twelve digits and a wrong check digit: NOT a UPC-A.
        self.assertEqual(self.codes(self.product('Cargador ficticio')), {TWELVE_NOT_UPC: ProductBarcode.UNKNOWN})

    def test_an_internal_code_is_never_called_a_gtin(self):
        # A valid EAN-13 typed in the INTERNAL column is still the shop's internal code.
        body = self.imported([row(code=EAN_ZERO, name='Adaptador ficticio')])
        self.assertEqual(self.codes(self.product('Adaptador ficticio')), {EAN_ZERO: ProductBarcode.INTERNAL})
        shown = self.by_row(body)[2]['data']['history']['codes']
        self.assertEqual([(c['code'], c['symbology']) for c in shown], [(EAN_ZERO, ProductBarcode.INTERNAL)])


class ExcelDamageTest(_Base):
    def test_a_numeric_cell_asks_for_review_and_apply_needs_the_number(self):
        body = self.ok_preview(workbook([row(barcode=750000000001, name='Batería ficticia')]))
        line = self.by_row(body)[2]
        self.assertEqual(line['action'], 'create')
        self.assertIn('review', line['data']['history']['flags'])
        self.assertEqual(self.history(body)['review'], 1)
        # Nothing reconstructed: the twelve digits Excel left, not a thirteenth.
        self.assertEqual(line['data']['barcode'], '750000000001')

        refused = self.apply(body['id'])
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertFalse(Product.objects.filter(company=self.company, name='Batería ficticia').exists())
        wrong = self.apply(body['id'], confirm='2')
        self.assertEqual(wrong.status_code, 400, wrong.content)

        done = self.apply(body['id'], confirm='1')
        self.assertEqual(done.status_code, 200, done.content)
        self.assertEqual(set(self.codes(self.product('Batería ficticia'))), {'750000000001'})

    def test_a_short_numeric_cell_is_also_reviewed(self):
        # Any length, not only 7, 11 or 12 digits.
        body = self.ok_preview(workbook([row(code=4567, name='Tapa ficticia')]))
        self.assertIn('review', self.by_row(body)[2]['data']['history']['flags'])

    def test_a_float_a_scientific_notation_or_more_than_15_digits_are_rejected(self):
        body = self.ok_preview(workbook([
            row(barcode=12.5, name='Uno ficticio'),
            row(barcode='7.50123E+12', name='Dos ficticio'),
            row(barcode=12345678901234567, name='Tres ficticio'),
        ]))
        lines = self.by_row(body)
        for number in (2, 3, 4):
            with self.subTest(row=number):
                self.assertEqual(lines[number]['action'], 'error')
                self.assertIn('rejected', lines[number]['data']['history']['flags'])
        self.assertEqual(self.history(body)['rejected'], 3)
        self.assertFalse(body['is_applicable'])

    def test_a_text_cell_of_digits_needs_no_review(self):
        body = self.ok_preview(workbook([row(barcode='750000000001', name='Texto ficticio')]))
        self.assertNotIn('review', self.by_row(body)[2]['data']['history']['flags'])


class PreviewTest(_Base):
    def test_the_preview_writes_nothing_of_the_catalogue(self):
        self.existing('Existente ficticio', 'OLD-0001')
        before = (Product.objects.count(), ProductBarcode.objects.count(), AdminAuditLog.objects.count())

        body = self.ok_preview(workbook([
            row(code='OLD-0001', barcode='NEW-0002', name='Existente ficticio'),
            row(code='NEW-0003', name='Nuevo ficticio'),
        ]))

        self.assertEqual(body['counts']['error'], 0, body['rows'])
        self.assertEqual(
            (Product.objects.count(), ProductBarcode.objects.count(), AdminAuditLog.objects.count()), before)

    def test_it_classifies_every_row(self):
        self.existing('Coincide ficticio', 'MATCH-01')
        self.existing('Otro ficticio', 'TAKEN-01')
        self.existing('Tercero ficticio', 'THIRD-01')
        self.existing('Repetido ficticio')
        self.existing('Repetido ficticio ')  # the same name, folded
        Product.objects.filter(name='Repetido ficticio ').update(name='Repetido ficticio')

        body = self.ok_preview(workbook([
            row(code='MATCH-01', barcode='MATCH-02', name='Coincide ficticio'),   # 2 match, 1 kept + 1 existing
            row(code='NEW-01', name='Nuevo ficticio'),                             # 3 new
            row(code='DUP-01', name='Dup uno ficticio'),                           # 4
            row(code='DUP-01', name='Dup dos ficticio'),                           # 5 duplicate in file
            row(code='THIRD-01', barcode='TAKEN-01', name='Choque ficticio'),      # 6 conflict
            row(name='Repetido ficticio'),                                         # 7 ambiguous
            row(code='x', name='Corto ficticio'),                                  # 8 rejected
        ]))
        history = self.history(body)
        self.assertEqual(history['matched'], 1)
        self.assertEqual(history['new'], 2)
        self.assertEqual(history['codes_existing'], 1)
        self.assertEqual(history['codes_kept'], 3)   # MATCH-02, NEW-01, DUP-01 (row 4); none of the errors
        self.assertEqual(history['duplicates'], 1)
        self.assertEqual(history['conflicts'], 1)
        self.assertEqual(history['ambiguous'], 1)
        self.assertEqual(history['rejected'], 1)
        flags = {n: set(line['data']['history']['flags']) for n, line in self.by_row(body).items()}
        self.assertIn('duplicate', flags[5])
        self.assertIn('conflict', flags[6])
        self.assertIn('ambiguous', flags[7])
        self.assertIn('rejected', flags[8])

    def test_a_code_differing_only_in_case_is_reviewed(self):
        self.existing('Mayúsculas ficticio', 'CASE-AB')
        body = self.ok_preview(workbook([row(code='case-ab', name='Minúsculas ficticio')]))
        line = self.by_row(body)[2]
        self.assertEqual(line['action'], 'create')
        self.assertIn('review', line['data']['history']['flags'])

    def test_a_csv_is_refused(self):
        res = self.preview(b'Nombre,Precio\nUno,1\n', filename='historica.csv')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn('.xlsx', res.json()['detail'])
        self.assertFalse(BulkImportJob.objects.filter(company=self.company).exists())

    def test_the_template_columns_are_recognised_and_switch_the_mode_on(self):
        body = self.ok_preview(workbook([row(code='AUTO-01', name='Automático ficticio')]), options={})
        self.assertTrue(body['options']['history_mode'])
        self.assertIn('history', body['summary'])

    def test_only_a_true_boolean_turns_the_mode_on(self):
        headers = ['Nombre', 'Precio de venta']
        for value in ('false', 'true', 1):
            with self.subTest(value=value):
                body = self.ok_preview(workbook([[f'Bool {value}', 10]], headers=headers),
                                       options={'history_mode': value})
                self.assertFalse(body['options']['history_mode'])

    def test_historical_columns_are_not_read_outside_the_mode(self):
        # Mapped by hand onto a file that is not the historical template, without the mode.
        headers = ['Nombre', 'Precio de venta', 'Viejo', 'Otro sistema']
        body = self.ok_preview(
            workbook([['Normal ficticio', 10, 'LEG-1', 'EXT-1']], headers=headers), options={},
            mapping=json.dumps({'name': 0, 'price': 1, 'legacy_code': 2, 'external_id': 3}), header_row=1)
        self.assertFalse(body['options'].get('history_mode'))
        self.assertNotIn('legacy_code', body['mapping']['mapping'])
        self.assertNotIn('external_id', body['mapping']['mapping'])


class RenameNeedsReviewTest(_Base):
    def test_a_row_matched_by_code_does_not_rename_the_product(self):
        product = self.existing('Nombre del catálogo', 'REN-01')
        body = self.ok_preview(workbook([row(code='REN-01', barcode='REN-02', name='Nombre del sistema anterior')]))
        line = self.by_row(body)[2]
        self.assertEqual(line['action'], 'update')
        self.assertIn('review', line['data']['history']['flags'])

        self.assertEqual(self.apply(body['id']).status_code, 400)
        self.assertEqual(self.apply(body['id'], confirm='1').status_code, 200)
        product.refresh_from_db()
        self.assertEqual(product.name, 'Nombre del catálogo')
        self.assertEqual(set(self.codes(product)), {'REN-01', 'REN-02'})


class IdempotencyTest(_Base):
    ROWS = [
        row(barcode=EAN_ZERO, code='IDEM-01', name='Idempotente ficticio', category='Accesorios', price=30),
        row(code='IDEM-02', name='Otro idempotente', price=40),
    ]

    def test_reimporting_the_same_file_changes_nothing(self):
        self.imported(self.ROWS)
        before = (
            list(Product.objects.filter(company=self.company).order_by('pk').values()),
            list(ProductBarcode.objects.filter(company=self.company).order_by('pk').values()),
        )

        again = self.ok_preview(workbook(self.ROWS))
        self.assertEqual(again['counts']['create'], 0, again['rows'])
        self.assertEqual(again['counts']['update'], 0, again['rows'])
        self.assertEqual(again['counts']['no_change'], 2, again['rows'])
        self.assertEqual(self.apply(again['id']).status_code, 200)
        # And the very same job, applied twice, does not run twice.
        self.assertEqual(self.apply(again['id']).status_code, 200)

        after = (
            list(Product.objects.filter(company=self.company).order_by('pk').values()),
            list(ProductBarcode.objects.filter(company=self.company).order_by('pk').values()),
        )
        self.assertEqual(before, after)


class ConflictTest(_Base):
    def test_a_code_of_another_product_is_rejected_not_replaced(self):
        owner = self.existing('Dueño ficticio', 'OWN-01')
        other = self.existing('Ajeno ficticio', 'OTH-01')
        before = ProductBarcode.objects.count()

        body = self.ok_preview(workbook([row(code='OTH-01', barcode='OWN-01', name='Ajeno ficticio')]))
        line = self.by_row(body)[2]
        self.assertEqual(line['action'], 'error')
        self.assertIn('conflict', line['data']['history']['flags'])
        self.assertEqual(self.apply(body['id'], confirm='0').status_code, 400)

        self.assertEqual(ProductBarcode.objects.count(), before)
        self.assertEqual(set(self.codes(owner)), {'OWN-01'})
        self.assertEqual(set(self.codes(other)), {'OTH-01'})

    def test_a_new_product_whose_code_is_held_elsewhere_gets_no_alternative_code(self):
        self.existing('Primero ficticio', 'HELD-01')
        body = self.ok_preview(workbook([row(code='HELD-01', barcode='FREE-01', name='Segundo ficticio')]))
        # Matched by its code, so it is the SAME product (a rename, under review) — never a new one with another code.
        self.assertEqual(self.by_row(body)[2]['action'], 'update')
        self.assertFalse(Product.objects.filter(name='Segundo ficticio').exists())

    def test_a_device_identifier_is_not_a_product_code(self):
        phone = Product.objects.create(company=self.company, name='Teléfono ficticio', slug='telefono-ficticio',
                                       price='900.00', is_serialized=True)
        StockUnit.objects.create(company=self.company, branch=tenancy.company_fulfillment_branch(self.company),
                                 product=phone, serial_number='SERFICT01', imei=IMEI)
        body = self.ok_preview(workbook([
            row(barcode=IMEI, name='Funda con IMEI'),
            row(code='SERFICT01', name='Funda con serie'),
        ]))
        for number in (2, 3):
            with self.subTest(row=number):
                self.assertEqual(self.by_row(body)[number]['action'], 'error')
                self.assertIn('conflict', self.by_row(body)[number]['data']['history']['flags'])


class SeveralCodesTest(_Base):
    def test_one_row_brings_several_codes(self):
        self.imported([row(barcode=EAN_ZERO, code='MULTI-01', legacy='OLD/SKU-9', name='Multi ficticio')],
                      options={**HISTORY, 'legacy_code_as_barcode': True})
        self.assertEqual(self.codes(self.product('Multi ficticio')), {
            EAN_ZERO: ProductBarcode.EAN13, 'MULTI-01': ProductBarcode.INTERNAL, 'OLD/SKU-9': ProductBarcode.UNKNOWN,
        })

    def test_the_old_sku_is_not_a_code_unless_asked(self):
        body = self.imported([row(code='SKU-OFF-1', legacy='LEG-77', name='Sin SKU ficticio')])
        self.assertEqual(set(self.codes(self.product('Sin SKU ficticio'))), {'SKU-OFF-1'})
        self.assertEqual(self.by_row(body)[2]['data']['history']['legacy_code'], 'LEG-77')

    def test_an_old_sku_only_in_the_report_neither_matches_nor_collides(self):
        # Off by default, the old SKU is not a code: equal to another product's code, it changes nothing.
        self.existing('Con ese código', 'SAME-77')
        body = self.ok_preview(workbook([row(code='OWN-77', legacy='SAME-77', name='Informe ficticio')]))
        line = self.by_row(body)[2]
        self.assertEqual(line['action'], 'create', line)
        self.assertEqual([c['code'] for c in line['data']['history']['codes']], ['OWN-77'])

    def test_an_existing_product_keeps_its_codes_and_gains_the_new_one(self):
        product = self.existing('Varios ficticio', 'KEEP-01', 'KEEP-02')
        self.imported([row(code='KEEP-01', barcode='ADD-03', name='Varios ficticio')])
        product.refresh_from_db()
        self.assertEqual(set(self.codes(product)), {'KEEP-01', 'KEEP-02', 'ADD-03'})
        self.assertEqual(ProductBarcode.objects.get(product=product, is_primary=True).code, 'KEEP-01')

    def test_two_new_rows_with_one_name_are_ambiguous(self):
        body = self.ok_preview(workbook([
            row(code='TWIN-01', name='Gemelo ficticio'),
            row(code='TWIN-02', name='Gemelo ficticio'),
        ]))
        self.assertIn('ambiguous', self.by_row(body)[3]['data']['history']['flags'])
        self.assertFalse(body['is_applicable'])


class ReconciliationTest(_Base):
    def test_external_id_and_brand_travel_in_the_job_and_the_report(self):
        body = self.imported([row(code='REC-01', name='Conciliado ficticio', brand='Marca Ficticia',
                                  model='Modelo X', variant='Rojo', external='EXT-9001')])
        data = self.by_row(body)[2]['data']['history']
        self.assertEqual(data['external_id'], 'EXT-9001')
        self.assertEqual(data['brand'], 'Marca Ficticia')

        report = self.client.get(f'/api/admin/imports/{body["id"]}/errors.csv?company={self.company.pk}&kind=history')
        self.assertEqual(report.status_code, 200, report.content)
        text = report.content.decode('utf-8-sig')
        self.assertIn('EXT-9001', text)
        self.assertIn(str(self.product('Conciliado ficticio').pk), text)

    def test_the_template_keeps_codes_as_text(self):
        res = self.client.get(f'{TEMPLATE}?company={self.company.pk}&variant=historical')
        self.assertEqual(res.status_code, 200)
        sheet = openpyxl.load_workbook(io.BytesIO(res.content)).active
        self.assertEqual([c.value for c in sheet[1]], import_exports.HISTORY_TEMPLATE_HEADERS)
        for letter in ('A', 'B', 'C', 'J'):
            self.assertEqual(sheet.column_dimensions[letter].number_format, '@')


class AuthorityTest(_Base):
    def test_another_companys_job_is_not_found(self):
        body = self.ok_preview(workbook([row(code='TEN-01', name='Ajeno ficticio')]))
        other = _p3_company('historica-dos', 'Tienda Histórica Dos')
        intruder, _ = _p2d_member(other, 'historica_intruso', ['company.view', 'products.view', 'products.manage'])
        client = APIClient()
        client.force_authenticate(user=intruder)

        self.assertEqual(self.apply(body['id'], client=client, company=other).status_code, 404)
        report = client.get(f'/api/admin/imports/{body["id"]}/errors.csv?company={other.pk}&kind=history')
        self.assertEqual(report.status_code, 404)
        self.assertFalse(Product.objects.filter(name='Ajeno ficticio').exists())

    def test_without_products_manage_it_is_forbidden(self):
        viewer, _ = _p2d_member(self.company, 'historica_lector', ['company.view', 'products.view'])
        client = APIClient()
        client.force_authenticate(user=viewer)
        self.assertEqual(self.preview(workbook([row(code='PERM-01')]), client=client).status_code, 403)
        self.assertEqual(client.get(f'{TEMPLATE}?company={self.company.pk}&variant=historical').status_code, 403)
        body = self.ok_preview(workbook([row(code='PERM-02', name='Permiso ficticio')]))
        self.assertEqual(self.apply(body['id'], client=client).status_code, 403)


class PosTest(_Base):
    def test_the_till_resolves_an_imported_code(self):
        self.imported([row(code='000777', barcode='zZ-81', name='Escaneable ficticio')])
        cashier, _ = _p2d_member(self.company, 'historica_caja', ['company.view', 'sales.pos.use'])
        client = APIClient()
        client.force_authenticate(user=cashier)
        branch = tenancy.company_fulfillment_branch(self.company)
        for code in ('000777', 'zZ-81'):
            with self.subTest(code=code):
                res = client.get(f'/api/admin/pos/products/lookup/?company={self.company.pk}&branch={branch.pk}&code={code}')
                self.assertEqual(res.status_code, 200, res.content)
                self.assertEqual(res.json()['name'], 'Escaneable ficticio')
        # Exact, like the till always was: another case or without the zeros is not that product.
        for code in ('777', 'ZZ-81'):
            with self.subTest(code=code):
                res = client.get(f'/api/admin/pos/products/lookup/?company={self.company.pk}&branch={branch.pk}&code={code}')
                self.assertEqual(res.status_code, 404)
