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
import threading

import openpyxl
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from store import device_catalog, import_exports, import_services, tenancy
from store.models import (
    AdminAuditLog, BranchStock, BulkImportJob, Category, Product, ProductBarcode, StockMovement, StockUnit,
    SupplierOffer,
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

    def imported(self, rows, *, confirm=None, headers=HEADERS, **kwargs):
        body = self.ok_preview(workbook(rows, headers=headers), **kwargs)
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


# =============================================================================
# LABEL-MIGRATION-02 · MIGRATION-B — reconciliation and code preservation
# =============================================================================

FULL = import_exports.HISTORY_TEMPLATE_HEADERS


def full_row(**cells):
    """A row of the full historical template: `cells` by header, the rest blank."""
    return [cells.get(header) for header in FULL]


class ClassificationTest(_Base):
    def test_excel_stripped_zeros_never_make_a_second_product(self):
        self.existing('Con ceros ficticio', EAN_ZERO)
        body = self.ok_preview(workbook([row(barcode=int(EAN_ZERO), name='Sin ceros ficticio')]))
        line = self.by_row(body)[2]
        self.assertEqual(line['action'], 'error')
        self.assertEqual(line['data']['history']['classification'], 'ambiguous')
        self.assertEqual(line['data']['barcode'], EAN_ZERO.lstrip('0'))  # nothing reconstructed
        self.assertFalse(Product.objects.filter(name='Sin ceros ficticio').exists())

    def test_a_product_without_codes_gets_one_from_the_code_policy(self):
        # LABEL-01's policy, at apply: a NEW product with no code receives one generated code.
        body = self.imported([row(name='Sin código ficticio')])
        product = self.product('Sin código ficticio')
        codes = list(ProductBarcode.objects.filter(product=product).values_list('code', 'symbology', 'is_primary'))
        self.assertEqual(len(codes), 1, codes)
        code, symbology, primary = codes[0]
        self.assertRegex(code, r'^[A-Z][A-Z0-9]*\d+$')
        self.assertEqual((symbology, primary), (ProductBarcode.INTERNAL, True))
        self.assertTrue(self.by_row(body)[2]['data']['history']['needs_code'])
        self.assertEqual(self.history(body)['without_code'], 1)

    def test_exact_secondary_name_only_and_inactive(self):
        self.existing('Primario ficticio', 'PRI-01', 'SEC-02')
        self.existing('Por nombre ficticio')
        retired = self.existing('Retirado ficticio', 'RET-01')
        Product.objects.filter(pk=retired.pk).update(is_active=False)
        body = self.ok_preview(workbook([
            row(code='PRI-01', barcode='PRI-NEW', name='Primario ficticio'),
            row(code='SEC-02', barcode='SEC-NEW', name='Primario ficticio dos'),
            row(name='Por nombre ficticio', price=99),
            row(code='RET-01', barcode='RET-NEW', name='Retirado ficticio'),
        ]))
        lines = self.by_row(body)
        self.assertEqual(lines[2]['data']['history']['match'], 'exact')
        self.assertEqual(lines[3]['data']['history']['match'], 'secondary_code')
        # Never merged by a name alone without somebody confirming it.
        self.assertEqual(lines[4]['data']['history']['classification'], 'needs_review')
        self.assertIn('review', lines[4]['data']['history']['flags'])
        self.assertEqual(lines[5]['data']['history']['classification'], 'inactive')
        self.assertIn('review', lines[5]['data']['history']['flags'])
        self.assertEqual(self.history(body)['version'], 1)
        self.assertEqual(set(self.history(body)['classes']), set(import_services.HISTORY_CLASSES))

    def test_two_conflicting_products_are_a_conflict_for_the_operator(self):
        self.existing('Uno en choque', 'CH-01')
        self.existing('Dos en choque', 'CH-02')
        body = self.ok_preview(workbook([row(code='CH-01', barcode='CH-02', name='Uno en choque')]))
        self.assertEqual(self.by_row(body)[2]['data']['history']['classification'], 'conflict')
        self.assertFalse(body['is_applicable'])

    def test_the_same_code_in_another_company_is_not_a_match(self):
        other = _p3_company('historica-tres', 'Tienda Histórica Tres')
        Product.objects.create(company=other, name='Ajeno', slug='ajeno', price='1.00')
        ProductBarcode.objects.create(company=other, product=Product.objects.get(company=other, name='Ajeno'),
                                      code='ISO-01')
        body = self.imported([row(code='ISO-01', name='Propio ficticio')])
        self.assertEqual(self.by_row(body)[2]['data']['history']['match'], 'new')
        self.assertEqual(set(self.codes(self.product('Propio ficticio'))), {'ISO-01'})


class VariantsTest(_Base):
    def setUp(self):
        super().setUp()
        capacity = device_catalog.create_attribute(company=self.company, name='Capacidad')
        colour = device_catalog.create_attribute(company=self.company, name='Color', kind='color')
        self.gb64 = device_catalog.create_option(company=self.company, attribute_id=capacity.pk, label='64 GB')
        self.gb512 = device_catalog.create_option(company=self.company, attribute_id=capacity.pk, label='512 GB')
        self.grey = device_catalog.create_option(company=self.company, attribute_id=colour.pk, label='Gris',
                                                 swatch_hex='#777777')
        self.model = device_catalog.create_model(company=self.company, name='Modelo Ficticio')
        held = Product.objects.create(company=self.company, name='Modelo Ficticio 64 GB Gris', slug='mf-64-gris',
                                      price='800.00', is_serialized=True)
        self.held = device_catalog.set_variant(held, model_id=self.model.pk, option_ids=[self.gb64.pk, self.grey.pk])
        ProductBarcode.objects.create(company=self.company, product=self.held, code='VAR-64', is_primary=True)

    def test_existing_variant_new_variant_and_new_model(self):
        body = self.ok_preview(workbook([
            full_row(**{'Código interno original': 'VAR-64', 'Nombre': 'Modelo Ficticio 64 GB Gris',
                        'Modelo': 'Modelo Ficticio', 'Capacidad': '64 GB', 'Color': 'Gris',
                        'Precio comercial': 800}),
            full_row(**{'Código interno original': 'VAR-512', 'Nombre': 'Modelo Ficticio 512 GB Gris',
                        'Modelo': 'Modelo Ficticio', 'Capacidad': '512 GB', 'Color': 'Gris',
                        'Precio comercial': 1200}),
            full_row(**{'Código interno original': 'VAR-TWIN', 'Nombre': 'Gemelo de variante',
                        'Modelo': 'Modelo Ficticio', 'Capacidad': '64 GB', 'Color': 'Gris',
                        'Precio comercial': 800}),
            full_row(**{'Código interno original': 'NUEVO-M', 'Nombre': 'Equipo de modelo nuevo',
                        'Modelo': 'Modelo Que No Existe', 'Capacidad': '64 GB', 'Precio comercial': 500}),
        ], headers=FULL))
        lines = self.by_row(body)
        self.assertEqual((lines[2]['action'], lines[2]['data']['history']['match']), ('no_change', 'exact'))
        self.assertEqual((lines[3]['action'], lines[3]['data']['history']['classification']),
                         ('create', 'model_new_variant'))
        # The variant already exists: never a second product for it, and nothing merged silently.
        self.assertEqual(lines[4]['action'], 'error')
        self.assertEqual(lines[4]['data']['history']['classification'], 'ambiguous')
        self.assertEqual((lines[5]['action'], lines[5]['data']['history']['classification']), ('create', 'new_model'))

    def test_an_undefined_sim_keeps_the_variant_and_is_reported(self):
        # P3. The company has no «SIM» option: the variant is still found without it.
        body = self.ok_preview(workbook([
            full_row(**{'Código interno original': 'VAR-SIM', 'Nombre': 'Modelo Ficticio 512 GB Gris SIM',
                        'Modelo': 'Modelo Ficticio', 'Capacidad': '512 GB', 'Color': 'Gris', 'SIM': 'Dual SIM',
                        'Precio comercial': 1200}),
        ], headers=FULL))
        line = self.by_row(body)[2]
        self.assertEqual(line['action'], 'create', line)
        self.assertEqual(line['data']['history']['classification'], 'model_new_variant')
        self.assertIn('variant', line['data'])
        self.assertTrue(any('Dual SIM' in w for w in line['warnings']))


class OldQuantitiesTest(_Base):
    HEADERS = [*HEADERS, 'Cantidad anterior - Centro', 'Cantidad anterior - Norte']

    def ledgers(self):
        return (list(BranchStock.objects.filter(product__company=self.company).values_list('product_id', 'quantity')),
                StockMovement.objects.count(), SupplierOffer.objects.count())

    def test_an_old_inventory_without_quantities_records_none(self):
        body = self.imported([[*row(code='QTY-00', name='Sin cantidades ficticio'), None, None]], headers=self.HEADERS)
        self.assertEqual(self.by_row(body)[2]['data']['history']['old_quantities'], [])
        self.assertEqual(self.history(body)['old_quantity_columns'],
                         ['Cantidad anterior - Centro', 'Cantidad anterior - Norte'])

    def test_blank_and_zero_are_kept_apart_and_only_as_reference(self):
        before = self.ledgers()
        body = self.imported([
            [*row(code='QTY-01', name='Cero ficticio'), 0, None],
            [*row(code='QTY-02', name='Con cantidad ficticio'), '7', '3'],
        ], headers=self.HEADERS)
        lines = self.by_row(body)
        self.assertEqual(lines[2]['data']['history']['old_quantities'],
                         [{'column': 'Cantidad anterior - Centro', 'value': '0'}])
        self.assertEqual([q['value'] for q in lines[3]['data']['history']['old_quantities']], ['7', '3'])
        # Never stock, never a counted quantity, never a supplier offer.
        self.assertEqual(self.ledgers(), before)
        self.assertFalse(any('quantity' in key for key in lines[3]['data'] if key != 'history'))
        report = self.client.get(f'/api/admin/imports/{body["id"]}/errors.csv?company={self.company.pk}&kind=history')
        self.assertIn('Cantidad anterior - Centro: 7', report.content.decode('utf-8-sig'))


class ReferenceQuantitiesTest(_Base):
    """The contract with the count (docs/adr-migration-count.md §8): per branch the person chose, integers."""

    HEADERS = [*HEADERS, 'Cantidad anterior - Centro', 'Cantidad anterior - Norte']
    CENTRE, NORTH = len(HEADERS) - 2, len(HEADERS) - 1

    def setUp(self):
        super().setUp()
        self.centre = tenancy.company_fulfillment_branch(self.company)
        from store.tests import _p2d_branch

        self.north = _p2d_branch(self.company, 'Norte ficticio')

    def branch_map(self, **pairs):
        return json.dumps({str(column): branch.pk for column, branch in pairs.items()})

    def test_quantities_go_to_the_chosen_branch_as_integers_and_blank_stays_absent(self):
        before = (StockMovement.objects.count(), list(BranchStock.objects.values_list('pk', 'quantity')))
        data = workbook([
            [*row(code='REF-01', name='Referencia uno'), 0, '2.5'],
            [*row(code='REF-02', name='Referencia dos'), '7', '3'],
            [*row(code='REF-07', name='Referencia tres'), 'muchos', None],
        ], headers=self.HEADERS)
        body = self.ok_preview(data, branch_map=json.dumps({str(self.CENTRE): self.centre.pk,
                                                            str(self.NORTH): self.north.pk}))
        lines = self.by_row(body)
        # Zero is a quantity; blank and a decimal are not.
        self.assertEqual(lines[2]['data']['history']['reference_quantities'],
                         [{'branch_id': self.centre.pk, 'quantity': 0}])
        self.assertTrue(any('2.5' in w for w in lines[2]['warnings']))
        self.assertEqual(lines[3]['data']['history']['reference_quantities'],
                         [{'branch_id': self.centre.pk, 'quantity': 7}, {'branch_id': self.north.pk, 'quantity': 3}])
        self.assertEqual(lines[4]['data']['history']['reference_quantities'], [])
        self.assertTrue(any('muchos' in w for w in lines[4]['warnings']))
        self.assertEqual(body['mapping']['quantity_branches'],
                         {str(self.CENTRE): self.centre.pk, str(self.NORTH): self.north.pk})

        self.assertEqual(self.apply(body['id']).status_code, 200)
        # Reference only: no movement, no stock.
        self.assertEqual((StockMovement.objects.count(), list(BranchStock.objects.values_list('pk', 'quantity'))),
                         before)
        # And every row now says which product it is, the created ones included.
        job = BulkImportJob.objects.get(pk=body['id'])
        for staged in job.rows.all():
            self.assertEqual(staged.normalized_data['history']['product_id'],
                             Product.objects.get(company=self.company, name=staged.normalized_data['name']).pk)

    def test_the_screen_can_carry_the_choice_inside_the_options(self):
        data = workbook([[*row(code='REF-05', name='Por opciones'), '2', None]], headers=self.HEADERS)
        body = self.ok_preview(data, options={**HISTORY, 'quantity_branches': {str(self.CENTRE): self.centre.pk}})
        self.assertEqual(self.by_row(body)[2]['data']['history']['reference_quantities'],
                         [{'branch_id': self.centre.pk, 'quantity': 2}])
        self.assertNotIn('quantity_branches', body['options'])

    def test_the_inspection_lists_the_quantity_columns(self):
        data = workbook([[*row(code='REF-06', name='Inspección'), '2', None]], headers=self.HEADERS)
        res = self.client.post(f'/api/admin/imports/inspect/?company={self.company.pk}',
                               {'file': SimpleUploadedFile('h.xlsx', data, content_type=XLSX),
                                'import_type': 'products'}, format='multipart')
        self.assertEqual(res.status_code, 200, res.content)
        sheet = res.json()['sheets'][0]
        self.assertEqual([c['header'] for c in sheet['quantity_columns']],
                         ['Cantidad anterior - Centro', 'Cantidad anterior - Norte'])

    def test_without_a_chosen_branch_there_is_no_reference_quantity(self):
        body = self.ok_preview(workbook([[*row(code='REF-03', name='Sin sucursal'), '4', None]],
                                        headers=self.HEADERS))
        line = self.by_row(body)[2]['data']['history']
        self.assertEqual(line['reference_quantities'], [])
        self.assertEqual(line['old_quantities'], [{'column': 'Cantidad anterior - Centro', 'value': '4'}])

    def test_a_branch_of_another_company_is_not_found_and_a_column_that_is_not_a_quantity_is_refused(self):
        other = _p3_company('historica-cuatro', 'Tienda Histórica Cuatro')
        foreign = tenancy.company_fulfillment_branch(other)
        data = workbook([[*row(code='REF-04', name='Ajena'), '1', None]], headers=self.HEADERS)
        self.assertEqual(self.preview(data, branch_map=json.dumps({str(self.CENTRE): foreign.pk})).status_code, 404)
        refused = self.preview(data, branch_map=json.dumps({'3': self.centre.pk}))
        self.assertEqual(refused.status_code, 400, refused.content)
        twice = self.preview(data, branch_map=json.dumps({str(self.CENTRE): self.centre.pk,
                                                          str(self.NORTH): self.centre.pk}))
        self.assertEqual(twice.status_code, 400)
        self.assertFalse(BulkImportJob.objects.filter(company=self.company).exists())


class HistoricalCostTest(_Base):
    HEADERS = [*HEADERS, 'Costo histórico']

    def test_the_cost_needs_costs_view_and_never_reaches_a_reader_without_it(self):
        data = workbook([[*row(code='COST-01', name='Con costo ficticio'), '12.50']], headers=self.HEADERS)
        blind = self.ok_preview(data)
        self.assertNotIn('historical_cost', self.by_row(blind)[2]['data'])
        self.assertTrue(any('Costo histórico' in w for w in self.by_row(blind)[2]['warnings']))

        accountant, _ = _p2d_member(self.company, 'historica_costos',
                                    ['company.view', 'products.view', 'products.manage', 'costs.view'])
        client = APIClient()
        client.force_authenticate(user=accountant)
        seen = self.ok_preview(data, client=client)
        self.assertEqual(self.by_row(seen)[2]['data']['historical_cost'], '12.50')
        # The same job read by somebody without the permission.
        again = self.client.get(f'/api/admin/imports/{seen["id"]}/?company={self.company.pk}')
        self.assertNotIn('12.50', again.content.decode())
        report = self.client.get(f'/api/admin/imports/{seen["id"]}/errors.csv?company={self.company.pk}&kind=history')
        self.assertNotIn('12.50', report.content.decode('utf-8-sig'))
        # And it changes no cost of the catalogue.
        self.assertEqual(self.apply(seen['id'], client=client).status_code, 200)
        self.assertIsNone(self.product('Con costo ficticio').reference_cost)


class SimultaneousImportsTest(TransactionTestCase):
    """Two historical loads of the same file, applied at once: one product, never two."""

    def test_two_loads_create_one_product(self):
        cache.clear()
        company = _p3_company('historica-carrera', 'Tienda Carrera')
        user, _ = _p2d_member(company, 'historica_carrera', ['company.view', 'products.view', 'products.manage'])
        data = workbook([row(code='RACE-01', name='Carrera ficticia'), row(name='Carrera sin código')])
        jobs = [
            import_services.preview_products(
                company=company, actor=user, upload=SimpleUploadedFile('h.xlsx', data, content_type=XLSX),
                filename='h.xlsx', options=dict(HISTORY),
            ) for _ in range(2)
        ]
        barrier = threading.Barrier(2)
        results = []

        def run(job):
            try:
                barrier.wait(timeout=10)
                import_services.apply_products(job=job, actor=user)
                results.append('applied')
            except import_services.ImportError_:
                results.append('stale')
            except Exception as exc:  # noqa: BLE001 — the assertion below names it
                results.append(type(exc).__name__)
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=(job,)) for job in jobs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertEqual(sorted(results), ['applied', 'stale'])
        self.assertEqual(Product.objects.filter(company=company, name='Carrera ficticia').count(), 1)
        self.assertEqual(Product.objects.filter(company=company, name='Carrera sin código').count(), 1)
        self.assertEqual(ProductBarcode.objects.filter(company=company, code='RACE-01').count(), 1)


# =============================================================================
# Review of #170 — findings P1 to P3
# =============================================================================

class ReviewFindingsTest(_Base):
    def test_codes_differing_only_in_leading_zeros_reimport_cleanly(self):
        # P1. Both are this product's, exactly: nothing is ambiguous and nothing changes.
        product = self.existing('Ceros dobles', '012345678905', '0012345678905')
        body = self.ok_preview(workbook([row(barcode='012345678905', code='0012345678905', name='Ceros dobles')]))
        line = self.by_row(body)[2]
        self.assertEqual(line['action'], 'no_change', line)
        self.assertTrue(body['is_applicable'])
        self.assertEqual(set(self.codes(product)), {'012345678905', '0012345678905'})

    def test_an_exact_code_is_not_rejected_because_another_product_holds_it_with_zeros(self):
        # P1. A owns 123456, B owns 0123456: A's row is A's, exactly.
        a = self.existing('Producto A', '123456')
        self.existing('Producto B', '0123456')
        body = self.ok_preview(workbook([row(code='123456', barcode='A-EXTRA', name='Producto A')]))
        line = self.by_row(body)[2]
        self.assertEqual(line['action'], 'update', line)
        self.assertEqual(line['data']['product_id'], a.pk)
        self.assertNotIn('ambiguous', line['data']['history']['flags'])

    def test_a_zero_twin_of_the_same_product_is_not_ambiguous(self):
        # P1. The padded code belongs to the product this row already is: no other product is at stake.
        product = self.existing('Gemelo de ceros', 'ZP-01', '007777')
        body = self.ok_preview(workbook([row(code='ZP-01', barcode='7777', name='Gemelo de ceros')]))
        line = self.by_row(body)[2]
        self.assertEqual((line['action'], line['data']['product_id']), ('update', product.pk), line['errors'])
        self.assertNotIn('ambiguous', line['data']['history']['flags'])

    def test_a_rename_after_the_preview_is_not_overwritten(self):
        # P2. Same name at preview; renamed before apply; the code-matched row never writes a name.
        product = self.existing('Nombre al previsualizar', 'NAME-01')
        body = self.ok_preview(workbook([row(code='NAME-01', barcode='NAME-02', name='Nombre al previsualizar')]))
        Product.objects.filter(pk=product.pk).update(name='Renombrado después')
        self.assertEqual(self.apply(body['id']).status_code, 200)
        product.refresh_from_db()
        self.assertEqual(product.name, 'Renombrado después')
        self.assertEqual(set(self.codes(product)), {'NAME-01', 'NAME-02'})

    def test_a_code_cell_excel_turned_into_a_date_is_rejected(self):
        # P2. A date or time in a code column is Excel's damage, not a code.
        import datetime

        body = self.ok_preview(workbook([
            row(barcode=datetime.datetime(2024, 1, 5), name='Fecha ficticia'),
            row(code=datetime.time(10, 30), name='Hora ficticia'),
        ]))
        for number in (2, 3):
            with self.subTest(row=number):
                line = self.by_row(body)[number]
                self.assertEqual(line['action'], 'error')
                self.assertIn('rejected', line['data']['history']['flags'])

    def test_a_new_code_never_reaches_a_product_by_its_name(self):
        # P2. Found only by its name, the row would attach a code: a merge by name. Refused.
        product = self.existing('Sólo por nombre')
        body = self.ok_preview(workbook([row(code='BYNAME-01', name='Sólo por nombre')]))
        line = self.by_row(body)[2]
        self.assertEqual(line['action'], 'error')
        self.assertEqual(line['data']['history']['classification'], 'conflict')
        self.assertFalse(ProductBarcode.objects.filter(product=product).exists())

    def test_an_invalid_old_sku_is_a_report_warning_not_a_row_error(self):
        # P3. With «the old SKU scans» on, an invalid one is dropped and reported; the row applies.
        body = self.imported([row(code='SKUOK-01', legacy='x', name='SKU inválido')],
                             options={**HISTORY, 'legacy_code_as_barcode': True})
        line = self.by_row(body)[2]
        self.assertTrue(any('SKU antiguo' in w for w in line['warnings']))
        self.assertEqual(line['data']['history']['legacy_code'], 'x')
        self.assertEqual(set(self.codes(self.product('SKU inválido'))), {'SKUOK-01'})



# =============================================================================
# LABEL-01 wiring — `product_codes.ensure_code` at apply
# =============================================================================

class CodePolicyWiringTest(_Base):
    def test_a_product_that_brought_codes_gets_no_generated_one(self):
        self.imported([row(code='OWN-CODE-1', name='Con código propio')])
        self.assertEqual(set(self.codes(self.product('Con código propio'))), {'OWN-CODE-1'})

    def test_an_existing_product_without_codes_is_never_given_one(self):
        # generate=False for a product that already exists, even when it has no code.
        product = self.existing('Existente sin código')
        body = self.ok_preview(workbook([row(name='Existente sin código', price=77)]))
        self.assertEqual(self.apply(body['id'], confirm=str(self.history(body)['review'])).status_code, 200)
        product.refresh_from_db()
        self.assertEqual(str(product.price), '77.00')
        self.assertFalse(ProductBarcode.objects.filter(product=product).exists())

    def test_the_same_file_twice_generates_one_code_once(self):
        self.imported([row(name='Generado una vez')])
        first = set(self.codes(self.product('Generado una vez')))
        again = self.ok_preview(workbook([row(name='Generado una vez')]))
        self.assertEqual(again['counts']['create'], 0, again['rows'])
        self.assertEqual(self.apply(again['id'], confirm=str(self.history(again)['review'])).status_code, 200)
        self.assertEqual(Product.objects.filter(company=self.company, name='Generado una vez').count(), 1)
        self.assertEqual(set(self.codes(self.product('Generado una vez'))), first)

    def test_an_inactive_series_is_said_at_preview_not_at_apply(self):
        from store import product_codes
        from store.models import InternalSequence

        sequence = product_codes.product_code_sequence(self.company)
        InternalSequence.objects.filter(pk=sequence.pk).update(is_active=False)
        body = self.ok_preview(workbook([row(code='FINE-01', name='Con código'), row(name='Sin serie activa')]))
        lines = self.by_row(body)
        self.assertEqual(lines[2]['action'], 'create')
        self.assertEqual(lines[3]['action'], 'error')
        self.assertTrue(any('serie' in e for e in lines[3]['errors']), lines[3]['errors'])
        self.assertFalse(body['is_applicable'])

    def test_a_policy_error_at_apply_is_a_row_error_and_nothing_is_written(self):
        # The series switched off AFTER the preview: apply stops on that row, with the policy's code.
        from store import product_codes
        from store.models import InternalSequence

        body = self.ok_preview(workbook([row(code='FINE-02', name='Con código dos'), row(name='Sin serie luego')]))
        self.assertTrue(body['is_applicable'])
        sequence = product_codes.product_code_sequence(self.company)
        InternalSequence.objects.filter(pk=sequence.pk).update(is_active=False)
        res = self.apply(body['id'])
        self.assertEqual(res.status_code, 400, res.content)
        self.assertTrue(res.json()['detail'].startswith('Fila 3:'), res.json())
        self.assertEqual(res.json()['code'], 'sequence_inactive')
        self.assertFalse(Product.objects.filter(company=self.company, name__in=['Con código dos', 'Sin serie luego'])
                         .exists())

    def test_a_generated_code_never_takes_a_code_a_later_row_brings(self):
        # Row 2 has no code; row 3 brings exactly the series' next number. The file applies.
        from store import product_codes

        sequence = product_codes.product_code_sequence(self.company)
        next_code = f'{sequence.prefix}{str(sequence.next_value).zfill(sequence.padding)}'
        body = self.ok_preview(workbook([row(name='Sin código primero'), row(code=next_code, name='Trae ese código')]))
        res = self.apply(body['id'])
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(set(self.codes(self.product('Trae ese código'))), {next_code})
        generated = set(self.codes(self.product('Sin código primero')))
        self.assertEqual(len(generated), 1)
        self.assertNotIn(next_code, generated)

    def test_an_old_sku_symbology_is_the_same_at_preview_and_at_apply(self):
        body = self.imported([row(code='SYM-01', legacy=EAN_ZERO, name='SKU con EAN')],
                             options={**HISTORY, 'legacy_code_as_barcode': True})
        shown = {c['code']: c['symbology'] for c in self.by_row(body)[2]['data']['history']['codes']}
        stored = self.codes(self.product('SKU con EAN'))
        self.assertEqual(shown[EAN_ZERO], stored[EAN_ZERO])
        self.assertEqual(stored[EAN_ZERO], ProductBarcode.EAN13)  # named only because its check digit proves it

    def test_a_device_received_after_the_preview_is_a_unit_identifier_conflict(self):
        phone = Product.objects.create(company=self.company, name='Teléfono de carrera', slug='tel-carrera',
                                       price='900.00', is_serialized=True)
        body = self.ok_preview(workbook([row(code='SERLATE01', name='Funda tardía')]))
        self.assertEqual(body['counts']['error'], 0)
        StockUnit.objects.create(company=self.company, branch=tenancy.company_fulfillment_branch(self.company),
                                 product=phone, serial_number='SERLATE01')
        res = self.apply(body['id'])
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'unit_identifier_conflict')
        self.assertFalse(Product.objects.filter(company=self.company, name='Funda tardía').exists())

    def test_a_serial_in_another_case_is_caught_at_preview(self):
        # Serials are stored upper-cased: the preview compares the way the policy does.
        phone = Product.objects.create(company=self.company, name='Teléfono serie', slug='tel-serie',
                                       price='900.00', is_serialized=True)
        StockUnit.objects.create(company=self.company, branch=tenancy.company_fulfillment_branch(self.company),
                                 product=phone, serial_number='SERCASE01')
        body = self.ok_preview(workbook([row(code='sercase01', name='Funda minúsculas')]))
        line = self.by_row(body)[2]
        self.assertEqual(line['action'], 'error')
        self.assertIn('conflict', line['data']['history']['flags'])
