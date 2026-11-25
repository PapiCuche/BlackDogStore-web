"""
INV-LOAD-02 — cargar el stock REAL de la empresa con dos plantillas, comprobarlo y poder deshacer una carga.

    1. Dos plantillas («Stock de equipos nuevos» y «Stock de seminuevos») nacen del registro de columnas del
       importador: cada encabezado lo lee el importador, y cada columna que el importador lee para esa línea está en
       la plantilla o tiene su motivo escrito (`NOT_IN_STOCK_TEMPLATES`). La línea la fija la plantilla.
    2. La fila de ejemplo no se carga. La plantilla de nuevos rechaza las columnas de seminuevos; una fila de la otra
       línea es un error de la fila, en las dos plantillas.
    3. Un seminuevo cargado queda pendiente de inspección y sin publicar: la carga nunca inspecciona, publica ni
       adjunta fotos (USED-READY-01).
    4. `check_unit_stock` (sólo lectura) compara `BranchStock.quantity` con los equipos disponibles: 0 si cuadra, 2 si
       no. La «Conciliación» del panel dice lo mismo dentro de las sucursales de quien la pide.
    5. Una carga aplicada se anula entera (todo o nada, motivo obligatorio, auditada, idempotente) mientras cada
       equipo siga limpio; el Kardex la corrige con sus líneas de corrección, nunca borrando.
    6. El informe antes/después de una carga, en CSV y XLSX.
    7. La entrada de stock por cantidad responde 409 `busy` ante un bloqueo mutuo.
    8. Ensayo completo con datos sintéticos: dos plantillas, vista previa, registro, comprobación, anulación por lote y
       una venta de un nuevo y de un seminuevo.

Los IMEI son sintéticos (dígito de control Luhn), nunca de un equipo real.
"""
import csv
import io
from decimal import Decimal
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError
from django.db.models import F
from django.test import TestCase
from openpyxl import load_workbook
from rest_framework.test import APIClient

from store import device_catalog
from store import inventory_services as inventory
from store import stock_unit_services as units
from store import unit_import_services as unit_import
from store import used_unit_services as used_units
from store.models import (
    AdminAuditLog, BranchStock, BulkImportJob, CosmeticGrade, Product, StockMovement, StockUnit, Supplier, UnitPhoto,
)
from store.test_device_commerce import SaleShop
from store.test_stock_units import imei
from store.test_used_ready import UsedShop
from store.tests import _c1_sale, _p2d_branch, _p2d_member, _p3_company

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
TEMPLATE_URL = '/api/admin/inventory/units/import/template/'
PREVIEW_URL = '/api/admin/inventory/units/import/preview/'
RECONCILIATION_URL = '/api/admin/inventory/units/reconciliation/'

#: Luhn-valid synthetic IMEIs, none of them used by the older suites.
IMEIS = [imei(f'35281907{n:06d}') for n in range(0, 300)]


def apply_url(job_id):
    return f'/api/admin/inventory/units/import/{job_id}/apply/'


def void_url(job_id):
    return f'/api/admin/inventory/units/import/{job_id}/void/'


def report_url(job_id):
    return f'/api/admin/inventory/units/import/{job_id}/report/'


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def filled(template: bytes, rows, *, keep_example=True, name='stock.xlsx', extra_headers=()):
    """The downloaded template with `rows` ({header: value}) written under it, as a person fills it."""
    book = load_workbook(io.BytesIO(template))
    sheet = book.worksheets[0]
    headers = [cell.value for cell in sheet[1]]
    for header in extra_headers:
        headers.append(header)
        sheet.cell(row=1, column=len(headers), value=header)
    if not keep_example:
        sheet.delete_rows(2)
    first = 3 if keep_example else 2
    for offset, row in enumerate(rows):
        for header, value in row.items():
            sheet.cell(row=first + offset, column=headers.index(header) + 1, value=value)
    out = io.BytesIO()
    book.save(out)
    return SimpleUploadedFile(name, out.getvalue(), content_type=XLSX)


class LoadShop(UsedShop):
    """«Teléfono X» (DeviceShop) plus «Teléfono Z», whose variants carry a SIM and a «Versión» (región)."""

    def build(self, slug='invload2'):
        super().build(slug)
        self.north = _p2d_branch(self.company, 'Sucursal Norte')
        self.sim = device_catalog.create_attribute(company=self.company, name='SIM')
        self.version = device_catalog.create_attribute(company=self.company, name='Versión')
        self.esim = device_catalog.create_option(company=self.company, attribute_id=self.sim.pk, label='eSIM')
        self.usa = device_catalog.create_option(company=self.company, attribute_id=self.version.pk, label='USA')
        self.model_z = device_catalog.create_model(company=self.company, name='Teléfono Z')
        self.znew = self.z_variant('Teléfono Z 128 GB Negro eSIM USA', 'tz-128-negro', '4200.00',
                                   [self.gb128, self.black, self.esim, self.usa])
        self.zused = self.z_variant('Teléfono Z 128 GB Azul eSIM USA usado', 'tz-128-azul-usado', '1.00',
                                    [self.gb128, self.blue, self.esim, self.usa], line='used')
        self.supplier = Supplier.objects.create(company=self.company, name='Importadora Andina',
                                                document_number='20123456789')
        # Who loads the real stock: moves stock, sees costs and suppliers, and sets a used device's price.
        self.loader, _ = _p2d_member(self.company, f'{slug}_cargador', [
            'company.view', 'inventory.view', 'inventory.adjust', 'costs.view', 'products.view', 'products.manage'])
        self.loader_api = client_for(self.loader)
        self.stocker_api = client_for(self.stocker)
        self._imeis = iter(IMEIS)

    def z_variant(self, name, slug, price, options, line='new'):
        from store.tests import _prod

        product = _prod(self.company, name, slug, price=price, inventory=0)
        Product.objects.filter(pk=product.pk).update(is_serialized=True, requires_imei=True)
        return device_catalog.set_variant(product, model_id=self.model_z.pk, condition_line=line,
                                          option_ids=[o.pk for o in options])

    def template(self, line, client=None):
        response = (client or self.loader_api).get(TEMPLATE_URL, {'line': line})
        self.assertEqual(response.status_code, 200, response.content)
        return response

    def new_row(self, serial, *, branch=None, model='Teléfono X', capacity='128 GB', colour='Negro', **over):
        row = {'Sucursal': (branch or self.branch).name, 'Modelo': model, 'Capacidad': capacity, 'Color': colour,
               'IMEI 1': next(self._imeis), 'Serie': serial}
        row.update(over)
        return row

    def used_row(self, serial, **over):
        row = self.new_row(serial, **{'colour': 'Azul', **over})
        row.setdefault('Precio de venta', 1999)
        return row

    def preview(self, line, rows, client=None, expect=201, **form):
        upload = filled(self.template(line).content, rows)
        form.setdefault('reason', 'Inventario inicial')
        response = (client or self.loader_api).post(PREVIEW_URL, {'file': upload, **form}, format='multipart')
        self.assertEqual(response.status_code, expect, response.content)
        return response.json()

    def apply(self, payload, client=None, expect=200):
        response = (client or self.loader_api).post(apply_url(payload['id']))
        self.assertEqual(response.status_code, expect, response.content)
        return response.json()

    def errors(self, payload):
        return {row['row']: ' '.join(row['errors']) for row in payload['rows'] if row['errors']}


# ---------------------------------------------------------------------------
# 1. The two templates come from the importer's registry
# ---------------------------------------------------------------------------

class StockTemplateRegistryTest(LoadShop, TestCase):
    def setUp(self):
        self.build('invload2-tpl')

    def headers(self, line):
        book = load_workbook(io.BytesIO(self.template(line).content))
        return book, [cell.value for cell in book.worksheets[0][1]]

    def test_each_template_is_its_registry_in_order_with_its_own_file_and_sheet(self):
        for line in ('new', 'used'):
            with self.subTest(line):
                response = self.template(line)
                spec = unit_import.STOCK_TEMPLATES[line]
                self.assertIn(spec['filename'], response['Content-Disposition'])
                book, headers = self.headers(line)
                self.assertEqual(book.sheetnames[0], spec['sheet'])
                self.assertEqual(headers, [header for header, _field in spec['columns']])

    def test_every_header_is_read_by_the_importer_as_its_registered_field(self):
        for line in ('new', 'used'):
            with self.subTest(line):
                _book, headers = self.headers(line)
                columns = unit_import._columns(headers, template_line=line)
                read = {field: headers[index] for field, index in columns.items()}
                self.assertEqual(read, {field: header for header, field in unit_import.STOCK_TEMPLATES[line]['columns']})

    def test_every_field_the_importer_reads_for_the_line_is_in_the_template_or_says_why_not(self):
        everything = set(unit_import._ALIASES)
        for line in ('new', 'used'):
            with self.subTest(line):
                fields = {field for _header, field in unit_import.STOCK_TEMPLATES[line]['columns']}
                left_out = set(unit_import.NOT_IN_STOCK_TEMPLATES)
                if line == 'new':
                    left_out |= unit_import.USED_TEMPLATE_FIELDS
                self.assertEqual(fields | left_out, everything)
                self.assertFalse(fields & left_out)
        # The used template is the new one plus the used-only columns.
        new = [f for _h, f in unit_import.STOCK_TEMPLATES['new']['columns']]
        used = [f for _h, f in unit_import.STOCK_TEMPLATES['used']['columns']]
        self.assertEqual(set(used) - set(new), unit_import.USED_TEMPLATE_FIELDS)

    def test_no_column_inspects_publishes_or_attaches_photos_and_the_sheet_says_when_that_happens(self):
        book, headers = self.headers('used')
        folded = ' '.join(headers).lower()
        for word in ('inspecci', 'publicaci', 'foto'):
            self.assertNotIn(word, folded)
        help_text = ' '.join(str(cell.value) for row in book['Instrucciones'].iter_rows() for cell in row
                             if cell.value)
        for said in ('Estado de inspección', 'Estado de publicación', 'Fotografías'):
            self.assertIn(said, help_text)
        self.assertTrue(book['Instrucciones'].protection.sheet)

    def test_the_new_template_explains_that_its_sale_price_never_changes_the_variant_alone(self):
        # NEW-TEMPLATE-PRICE — optional, a proposal for the variant (`test_unit_import_new_price`).
        book, headers = self.headers('new')
        self.assertIn('Precio de venta', headers)
        help_text = ' '.join(str(cell.value) for row in book['Instrucciones'].iter_rows() for cell in row
                             if cell.value)
        self.assertIn('precio de su variante', help_text)
        self.assertIn('no cambia solo', help_text)

    def test_notes_example_row_lists_and_widths(self):
        book, headers = self.headers('used')
        sheet = book.worksheets[0]
        for cell in sheet[1]:
            self.assertIsNotNone(cell.comment, cell.value)
        example = {headers[i]: cell.value for i, cell in enumerate(sheet[2])}
        self.assertTrue(str(example['Serie']).startswith(unit_import.EXAMPLE_SERIAL_PREFIX))
        lists = {str(rule.sqref).split(':')[0][:1]: rule.formula1 for rule in sheet.data_validations.dataValidation}
        branch_letter = sheet.cell(row=1, column=headers.index('Sucursal') + 1).column_letter
        self.assertIn(self.branch.name, lists[branch_letter])
        self.assertIn('Sucursal Norte', lists[branch_letter])
        self.assertGreater(sheet.column_dimensions['A'].width, 10)

    def test_a_branch_named_like_a_formula_never_becomes_one_in_the_example_row(self):
        from store.models import Branch

        Branch.objects.filter(pk=self.branch.pk).update(name='=Centro')
        Branch.objects.filter(pk=self.north.pk).update(is_active=False)
        book = load_workbook(io.BytesIO(self.template('new').content))
        example = book.worksheets[0]['A2']
        self.assertNotEqual(example.data_type, 'f')
        self.assertFalse(str(example.value).startswith('='))

    def test_the_branch_list_names_only_the_callers_branches(self):
        scoped, _ = _p2d_member(self.company, 'invload2_tpl_norte', [
            'company.view', 'inventory.view', 'inventory.adjust'], branches=[self.north])
        book = load_workbook(io.BytesIO(self.template('new', client_for(scoped)).content))
        formulas = ' '.join(rule.formula1 for rule in book.worksheets[0].data_validations.dataValidation)
        self.assertIn('Sucursal Norte', formulas)
        self.assertNotIn(self.branch.name, formulas)

    def test_the_combined_template_still_downloads_as_before(self):
        response = self.loader_api.get(TEMPLATE_URL)
        self.assertEqual(response.status_code, 200)
        self.assertIn(unit_import.TEMPLATE_FILENAME, response['Content-Disposition'])
        book = load_workbook(io.BytesIO(response.content))
        self.assertEqual(book.sheetnames, ['Equipos'])
        self.assertEqual([c.value for c in book['Equipos'][1]], unit_import.HEADERS)

    def test_the_import_assistant_sends_both_templates_to_the_device_load(self):
        from store import import_assistant

        for line in ('new', 'used'):
            with self.subTest(line):
                book, headers = self.headers(line)
                found = import_assistant.classify([(name, [[c.value for c in book[name][1]], []])
                                                   for name in book.sheetnames])
                self.assertEqual((found['type'], found['reason'], found['sheet']),
                                 ('units', 'stock_template', unit_import.STOCK_TEMPLATES[line]['sheet']))

    def test_an_unknown_line_is_a_400(self):
        self.assertEqual(self.loader_api.get(TEMPLATE_URL, {'line': 'otra'}).status_code, 400)

    def test_without_inventory_adjust_there_is_no_template(self):
        viewer, _ = _p2d_member(self.company, 'invload2_tpl_viewer', ['company.view', 'inventory.view'])
        self.assertEqual(client_for(viewer).get(TEMPLATE_URL, {'line': 'new'}).status_code, 403)


# ---------------------------------------------------------------------------
# 2. Loading the templates: the line, the example row and the columns of the other line
# ---------------------------------------------------------------------------

class StockTemplateLoadTest(LoadShop, TestCase):
    def setUp(self):
        self.build('invload2-load')

    def test_the_example_row_is_skipped_and_the_rows_resolve_by_model_and_options(self):
        payload = self.preview('new', [
            self.new_row('NEW-0001'),
            self.new_row('NEW-0002', branch=self.north, model='Teléfono Z', SIM='eSIM',
                         **{'Versión (región / país de venta)': 'USA'}),
        ])
        self.assertEqual(payload['counts'], {**payload['counts'], 'create': 2, 'skip': 1, 'error': 0}, payload['rows'])
        skipped = next(row for row in payload['rows'] if row['action'] == 'skip')
        self.assertEqual(skipped['row'], 2)
        self.assertIn('ejemplo', ' '.join(skipped['warnings']).lower())
        self.assertEqual(payload['options']['line'], 'new')
        self.apply(payload)
        self.assertEqual(StockUnit.objects.get(serial_number='NEW-0001').product, self.new128)
        self.assertEqual(StockUnit.objects.get(serial_number='NEW-0002').product, self.znew)

    def test_a_row_of_the_other_line_is_a_row_error_in_both_templates(self):
        # «128 GB Azul» exists only as a USED variant: the new template does not find it…
        payload = self.preview('new', [self.new_row('NEW-0101', colour='Azul')])
        self.assertIn('no tiene esa variante', self.errors(payload)[3])
        # …and the used template does not find a NEW one.
        payload = self.preview('used', [self.used_row('USD-0101', colour='Negro')])
        self.assertIn('no tiene esa variante', self.errors(payload)[3])
        # A «Línea» column that contradicts the template: refused on the row.
        upload = filled(self.template('used').content, [{**self.used_row('USD-0102'), 'Línea': 'Nuevo'}],
                        extra_headers=['Línea'])
        response = self.loader_api.post(PREVIEW_URL, {'file': upload, 'reason': 'X'}, format='multipart')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertIn('seminuevos', self.errors(response.json())[3])

    def test_a_product_code_of_the_other_line_is_refused_on_the_row(self):
        from store.models import ProductBarcode

        ProductBarcode.objects.create(company=self.company, product=self.new128, code='TX-128-N')
        upload = filled(self.template('used').content, [{**self.used_row('USD-0201'), 'Código': 'TX-128-N'}],
                        extra_headers=['Código'])
        response = self.loader_api.post(PREVIEW_URL, {'file': upload, 'reason': 'Inventario'}, format='multipart')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertIn('nuevo', self.errors(response.json())[3])

    def test_the_new_template_refuses_a_used_only_column(self):
        # «Precio de venta» is in both templates since NEW-TEMPLATE-PRICE.
        for header in ('Garantía (días)', 'Estado estético', 'Salud de batería (%)'):
            with self.subTest(header):
                upload = filled(self.template('new').content, [self.new_row('NEW-0301')], extra_headers=[header])
                response = self.loader_api.post(PREVIEW_URL, {'file': upload, 'reason': 'X'}, format='multipart')
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn(header, response.json()['detail'])
                self.assertIn('seminuevos', response.json()['detail'])
        self.assertFalse(BulkImportJob.objects.filter(company=self.company).exists())

    def test_a_line_chosen_on_screen_that_contradicts_the_template_is_refused(self):
        upload = filled(self.template('used').content, [self.used_row('USD-0401')])
        response = self.loader_api.post(PREVIEW_URL, {'file': upload, 'reason': 'X', 'line': 'new'},
                                        format='multipart')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('seminuevos', response.json()['detail'])

    def test_a_line_chosen_on_screen_holds_a_combined_file_to_it(self):
        from store.test_unit_import import HEADERS as LEGACY
        from store.test_cost_imports import upload as legacy_upload

        headers = [*LEGACY, 'Modelo', 'Capacidad', 'Color', 'Línea']
        file = legacy_upload(headers, [['', '', self.branch.name, 'CMB-0001', next(self._imeis), '', '', '',
                                        'Inventario', 'Teléfono X', '128 GB', 'Negro', '']], title='Equipos')
        response = self.loader_api.post(PREVIEW_URL, {'file': file, 'line': 'used'}, format='multipart')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertIn('no tiene esa variante', self.errors(response.json())[2])

    def test_the_used_template_stores_the_used_details_and_never_inspects_publishes_or_photographs(self):
        grade = CosmeticGrade.objects.get(company=self.company, code='excelente')
        payload = self.preview('used', [self.used_row('USD-0501', **{
            'Estado estético': grade.label, 'Salud de batería (%)': 88, 'Estado funcional': 'Con fallas',
            'Defectos': 'Botón lateral duro', 'Accesorios': 'Caja y cable', 'Garantía (días)': 90,
            'Precio de venta': 1899.9, 'Detalles para el comprador': 'Rayón leve en el marco',
            'Proveedor': '20123456789', 'Costo': 1100, 'Fecha de ingreso': '2026-10-01',
            'Observaciones (notas internas)': 'Vitrina 2'})])
        self.assertEqual(payload['counts']['error'], 0, payload['rows'])
        self.assertEqual(payload['options']['line'], 'used')
        self.apply(payload)
        unit = StockUnit.objects.get(serial_number='USD-0501')
        self.assertEqual(
            (unit.product_id, unit.cosmetic_grade_id, unit.battery_health, unit.functional_state, unit.defects,
             unit.accessories, unit.warranty_days, unit.sale_price, unit.public_notes, unit.supplier_id, unit.cost,
             unit.notes, unit.received_at.date().isoformat()),
            (self.used128.pk, grade.pk, 88, 'with_faults', 'Botón lateral duro', 'Caja y cable', 90,
             Decimal('1899.90'), 'Rayón leve en el marco', self.supplier.pk, Decimal('1100.00'), 'Vitrina 2',
             '2026-10-01'))
        self.assertEqual((unit.inspection_status, unit.published_online), ('pending', False))
        self.assertFalse(UnitPhoto.objects.filter(unit=unit).exists())
        self.assertEqual(self.offered(), set())

    def test_costs_and_suppliers_follow_the_callers_authority(self):
        # `stocker` moves stock but sees neither costs nor suppliers (D-F-3).
        payload = self.preview('new', [self.new_row('NEW-0601', Costo=900),
                                       self.new_row('NEW-0602', Proveedor='Importadora Andina')],
                               client=self.stocker_api)
        errors = self.errors(payload)
        self.assertIn('costo', errors[3].lower())
        self.assertEqual(errors[4], unit_import.SUPPLIERS_FORBIDDEN)
        self.assertNotIn('900', str(payload['rows']))

    def test_a_branch_of_another_company_is_not_found(self):
        other = _p3_company('invload2-otra', 'Otra tienda')
        _p2d_branch(other, 'Sucursal Ajena')
        payload = self.preview('new', [self.new_row('NEW-0701', Sucursal='Sucursal Ajena')])
        self.assertIn('No existe la sucursal', self.errors(payload)[3])


# ---------------------------------------------------------------------------
# 3. check_unit_stock and «Conciliación»
# ---------------------------------------------------------------------------

class CheckUnitStockTest(LoadShop, TestCase):
    def setUp(self):
        self.build('invload2-chk')
        self.receive(self.new128, 2)
        self.receive(self.used128, 1)

    def run_check(self, *args):
        out = io.StringIO()
        try:
            call_command('check_unit_stock', *args, stdout=out)
        except CommandError as exc:
            return exc.returncode, out.getvalue() + str(exc)
        return 0, out.getvalue()

    def force_mismatch(self, product=None):
        BranchStock.objects.filter(branch=self.branch, product=product or self.new128).update(quantity=F('quantity') + 1)

    def test_a_clean_shop_exits_0(self):
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertIn('0 diferencia', out)

    def test_a_forced_mismatch_exits_2_and_names_it(self):
        self.force_mismatch()
        code, out = self.run_check('--company', self.company.slug)
        self.assertEqual(code, 2, out)
        self.assertIn(self.new128.name, out)
        self.assertIn('stock 3', out)
        self.assertIn('disponibles 2', out)

    def test_available_units_without_a_shelf_row_are_a_mismatch(self):
        BranchStock.objects.filter(branch=self.branch, product=self.used128).delete()
        code, out = self.run_check()
        self.assertEqual(code, 2, out)
        self.assertIn(self.used128.name, out)

    def test_it_reads_only_the_company_asked_for(self):
        other = _p3_company('invload2-chk-otra', 'Otra')
        code, out = self.run_check('--company', other.slug)
        self.force_mismatch()
        code, out = self.run_check('--company', other.slug)
        self.assertEqual(code, 0, out)
        with self.assertRaises(CommandError):
            call_command('check_unit_stock', '--company', 'no-existe', stdout=io.StringIO())

    def test_it_writes_nothing(self):
        self.force_mismatch()
        before = list(BranchStock.objects.order_by('pk').values_list('pk', 'quantity'))
        movements = StockMovement.objects.count()
        self.run_check()
        self.assertEqual(list(BranchStock.objects.order_by('pk').values_list('pk', 'quantity')), before)
        self.assertEqual(StockMovement.objects.count(), movements)

    def test_the_panel_report_says_the_same_inside_the_callers_branches(self):
        self.force_mismatch()
        viewer, _ = _p2d_member(self.company, 'invload2_chk_viewer', ['company.view', 'inventory.view'])
        body = client_for(viewer).get(RECONCILIATION_URL).json()
        self.assertEqual([(m['product_id'], m['quantity'], m['available'], m['difference'])
                          for m in body['mismatches']], [(self.new128.pk, 3, 2, 1)])
        self.assertGreaterEqual(body['checked'], 2)
        self.assertNotIn('cost', str(body))
        north_only, _ = _p2d_member(self.company, 'invload2_chk_norte', ['company.view', 'inventory.view'],
                                    branches=[self.north])
        self.assertEqual(client_for(north_only).get(RECONCILIATION_URL).json()['mismatches'], [])

    def test_the_panel_report_needs_inventory_view_and_never_crosses_companies(self):
        nobody, _ = _p2d_member(self.company, 'invload2_chk_nadie', ['company.view'])
        self.assertEqual(client_for(nobody).get(RECONCILIATION_URL).status_code, 403)
        other = _p3_company('invload2-chk-ajena', 'Ajena')
        stranger, _ = _p2d_member(other, 'invload2_chk_ajeno', ['company.view', 'inventory.view'])
        self.force_mismatch()
        self.assertEqual(client_for(stranger).get(RECONCILIATION_URL).json()['mismatches'], [])


# ---------------------------------------------------------------------------
# 4. Annulling an applied load, whole
# ---------------------------------------------------------------------------

class BatchVoidTest(LoadShop, TestCase):
    def setUp(self):
        self.build('invload2-void')
        self.job = self.apply(self.preview('new', [self.new_row('VOID-0001'), self.new_row('VOID-0002'),
                                                   self.new_row('VOID-0003', branch=self.north)],
                                           entry_kind='purchase', reason='Compra F001-9'))
        self.created = list(StockUnit.objects.filter(serial_number__startswith='VOID-').order_by('pk'))

    def void(self, client=None, expect=200, **body):
        body.setdefault('reason', 'Se cargó el archivo equivocado')
        response = (client or self.loader_api).post(void_url(self.job['id']), body, format='json')
        self.assertEqual(response.status_code, expect, response.content)
        return response.json()

    def test_every_unit_is_voided_by_a_correction_line_and_the_stock_goes_back(self):
        entries = StockMovement.objects.filter(movement_type=StockMovement.PURCHASE_ENTRY).count()
        body = self.void()
        self.assertEqual(body['voided']['units'], 3)
        for unit in self.created:
            unit.refresh_from_db()
            self.assertEqual(unit.status, StockUnit.Status.VOIDED)
        self.assertEqual(StockMovement.objects.filter(movement_type=StockMovement.CORRECTION_NEGATIVE).count(), 3)
        self.assertEqual(StockMovement.objects.filter(movement_type=StockMovement.PURCHASE_ENTRY).count(), entries)
        self.assertEqual((self.shelf(self.new128), inventory.branch_quantity(self.north, self.new128)), (0, 0))
        log = AdminAuditLog.objects.get(action='stock_units_import_voided')
        self.assertEqual((log.company_id, log.metadata['job_id'], log.metadata['units']),
                         (self.company.pk, self.job['id'], 3))
        self.assertNotIn('VOID-', str(log.metadata))
        job = BulkImportJob.objects.get(pk=self.job['id'])
        self.assertEqual(job.summary['voided']['units'], 3)
        # The identifiers are free again: the right file loads.
        payload = self.preview('new', [self.new_row('VOID-0001', **{'IMEI 1': self.created[0].imei})],
                               entry_kind='purchase')
        self.assertEqual(payload['counts']['error'], 0, payload['rows'])

    def test_the_answers_never_carry_the_list_of_devices(self):
        # The list backs the annulment; the screen needs counts only (a history of fifty loads would carry thousands).
        self.assertNotIn('unit_ids', self.job['summary']['applied'])
        listed = self.loader_api.get('/api/admin/imports/', {'type': 'units'}).json()['results']
        self.assertNotIn('unit_ids', str(listed))
        self.assertIn('unit_ids', BulkImportJob.objects.get(pk=self.job['id']).summary['applied'])

    def test_an_annulled_opening_load_can_be_loaded_again_as_opening(self):
        opening = self.apply(self.preview('new', [self.new_row('VOID-OP-01', branch=self.north, capacity='256 GB')],
                                          entry_kind='opening'))
        self.void_job(opening['id'])
        again = self.preview('new', [self.new_row('VOID-OP-02', branch=self.north, capacity='256 GB')],
                             entry_kind='opening')
        self.assertEqual(again['counts']['error'], 0, again['rows'])
        self.apply(again)
        # A sale still closes the opening, as before.
        from store.tests import _c1_sale

        cashier, _ = _p2d_member(self.company, 'invload2_void_caja', ['company.view', 'sales.pos.use'])
        unit = StockUnit.objects.get(serial_number='VOID-OP-02')
        _c1_sale(company=self.company, branch=self.north, actor=cashier, items=[
            {'product': self.new256.pk, 'quantity': 1, 'units': [{'id': unit.pk, 'method': 'scanner'}]}])
        closed = self.preview('new', [self.new_row('VOID-OP-03', branch=self.north, capacity='256 GB')],
                              entry_kind='opening')
        self.assertIn('inventario inicial', self.errors(closed)[3])

    def void_job(self, job_id, reason='Error'):
        response = self.loader_api.post(void_url(job_id), {'reason': reason}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_a_reason_too_long_once_prefixed_is_a_400_on_the_reason(self):
        body = self.void(expect=400, reason='x' * 299)
        self.assertEqual(body.get('field'), 'reason')
        self.assertEqual(StockUnit.objects.filter(status=StockUnit.Status.VOIDED).count(), 0)

    def test_a_second_call_changes_nothing(self):
        self.void()
        movements = StockMovement.objects.count()
        body = self.void(reason='otra vez')
        self.assertFalse(body['changed'])
        self.assertEqual(StockMovement.objects.count(), movements)
        self.assertEqual(AdminAuditLog.objects.filter(action='stock_units_import_voided').count(), 1)

    def test_one_unit_that_moved_blocks_the_whole_batch_and_is_named(self):
        sold = self.created[0]
        units.reserve(sold, reason='Apartado para un cliente', actor=self.staff)
        movements = StockMovement.objects.count()
        body = self.void(expect=409)
        self.assertEqual(body['code'], 'batch_not_voidable')
        self.assertEqual([b['unit_id'] for b in body['blocking']], [sold.pk])
        self.assertIn('label_code', body['blocking'][0])
        self.assertEqual(StockMovement.objects.count(), movements)
        self.assertEqual(StockUnit.objects.filter(status=StockUnit.Status.VOIDED).count(), 0)

    def test_a_unit_with_history_after_its_entry_blocks_too(self):
        unit = self.created[1]
        units.reserve(unit, reason='Apartado', actor=self.staff)
        units.release(unit, actor=self.staff)          # AVAILABLE again, but it has a history now
        body = self.void(expect=409)
        self.assertEqual([b['unit_id'] for b in body['blocking']], [unit.pk])

    def test_a_reason_is_mandatory(self):
        body = self.void(expect=400, reason='  ')
        self.assertEqual(body.get('field'), 'reason')

    def test_only_an_applied_units_job_and_only_with_its_records(self):
        staged = self.preview('new', [self.new_row('VOID-0101')], entry_kind='purchase')
        response = self.loader_api.post(void_url(staged['id']), {'reason': 'x'}, format='json')
        self.assertEqual(response.status_code, 409, response.content)
        # A job applied before INV-LOAD-02 has no list of its devices: never guessed.
        job = BulkImportJob.objects.get(pk=self.job['id'])
        job.summary['applied'].pop('unit_ids')
        job.save(update_fields=['summary'])
        body = self.void(expect=409)
        self.assertEqual(body['code'], 'batch_unknown_units')

    def test_another_companys_or_an_unreached_job_is_a_404(self):
        other = _p3_company('invload2-void-otra', 'Otra')
        stranger, _ = _p2d_member(other, 'invload2_void_ajeno', ['company.view', 'inventory.view', 'inventory.adjust'])
        self.void(client=client_for(stranger), expect=404)
        north_only, _ = _p2d_member(self.company, 'invload2_void_norte', [
            'company.view', 'inventory.view', 'inventory.adjust'], branches=[self.north])
        self.void(client=client_for(north_only), expect=404)
        viewer, _ = _p2d_member(self.company, 'invload2_void_viewer', ['company.view', 'inventory.view'])
        self.void(client=client_for(viewer), expect=403)
        self.assertEqual(StockUnit.objects.filter(status=StockUnit.Status.VOIDED).count(), 0)


# ---------------------------------------------------------------------------
# 5. Before / after
# ---------------------------------------------------------------------------

class BeforeAfterReportTest(LoadShop, TestCase):
    def setUp(self):
        self.build('invload2-rep')
        self.receive(self.new128, 1)            # one already on the shelf
        self.job = self.apply(self.preview('new', [self.new_row('REP-0001'), self.new_row('REP-0002'),
                                                   self.new_row('REP-0003', branch=self.north)]))

    def test_the_csv_says_before_and_after_per_branch_and_product(self):
        response = self.loader_api.get(report_url(self.job['id']), {'as': 'csv'})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn('text/csv', response['Content-Type'])
        self.assertIn(f'carga-{self.job["id"]}-antes-despues.csv', response['Content-Disposition'])
        rows = list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
        main = next(r for r in rows if r['Sucursal'] == self.branch.name)
        self.assertEqual((main['Producto'], main['Stock antes'], main['Entran'], main['Stock después (previsto)'],
                          main['Stock después (al registrar)'], main['Cuadra']),
                         (self.new128.name, '1', '2', '3', '3', 'Sí'))
        north = next(r for r in rows if r['Sucursal'] == 'Sucursal Norte')
        self.assertEqual((north['Stock antes'], north['Stock después (al registrar)']), ('0', '1'))
        self.assertNotIn('Costo', response.content.decode('utf-8-sig'))

    def test_the_xlsx_carries_the_same_rows(self):
        response = self.loader_api.get(report_url(self.job['id']), {'as': 'xlsx'})
        self.assertEqual(response.status_code, 200)
        sheet = load_workbook(io.BytesIO(response.content)).active
        headers = [c.value for c in sheet[1]]
        self.assertIn('Stock después (al registrar)', headers)
        self.assertEqual(sheet.max_row, 3)

    def test_a_product_name_that_looks_like_a_formula_is_text(self):
        Product.objects.filter(pk=self.new128.pk).update(name='=HYPERLINK("x")')
        text = self.loader_api.get(report_url(self.job['id']), {'as': 'csv'}).content.decode('utf-8-sig')
        self.assertNotIn(',=HYPERLINK', text)

    def test_only_an_applied_job_of_the_callers_company(self):
        staged = self.preview('new', [self.new_row('REP-0101')])
        self.assertEqual(self.loader_api.get(report_url(staged['id'])).status_code, 409)
        other = _p3_company('invload2-rep-otra', 'Otra')
        stranger, _ = _p2d_member(other, 'invload2_rep_ajeno', ['company.view', 'inventory.view', 'inventory.adjust'])
        self.assertEqual(client_for(stranger).get(report_url(self.job['id'])).status_code, 404)


# ---------------------------------------------------------------------------
# 6. «busy» on the quantity entry
# ---------------------------------------------------------------------------

class _Driver(Exception):
    def __init__(self, pgcode):
        super().__init__(pgcode)
        self.pgcode = pgcode


def _db_error(code):
    error = OperationalError('deadlock detected' if code.startswith('40') else 'connection lost')
    error.__cause__ = _Driver(code)
    return error


class QuantityEntryBusyTest(LoadShop, TestCase):
    def setUp(self):
        self.build('invload2-busy')
        from store.tests import _prod

        self.cable = _prod(self.company, 'Cable', 'cable-invload2', price='30.00', inventory=0)
        self.url = f'/api/admin/products/{self.cable.pk}/inventory-adjust/'

    def adjust(self):
        return self.loader_api.post(self.url, {'delta': 3, 'reason': 'Ingreso', 'branch': self.branch.pk},
                                    format='json')

    def test_a_deadlock_or_serialization_failure_is_a_409_busy_and_nothing_moved(self):
        for code in ('40P01', '40001'):
            with self.subTest(code), mock.patch('store.admin_views.apply_manual_stock_movement',
                                                side_effect=_db_error(code)):
                response = self.adjust()
                self.assertEqual(response.status_code, 409, response.content)
                self.assertEqual(response.json()['code'], 'busy')
        self.assertEqual(self.shelf(self.cable), 0)
        self.assertEqual(self.adjust().status_code, 200)            # the retry goes through
        self.assertEqual(self.shelf(self.cable), 3)

    def test_any_other_database_error_still_raises(self):
        with mock.patch('store.admin_views.apply_manual_stock_movement', side_effect=_db_error('08006')):
            with self.assertRaises(OperationalError):
                self.adjust()

    def test_the_stock_file_apply_answers_busy_too(self):
        from store import stock_import_services

        job = BulkImportJob.objects.create(company=self.company, import_type=BulkImportJob.STOCK,
                                           mapping_snapshot={'branch_map': {'Almacén': self.branch.pk}},
                                           created_by=self.loader)
        with mock.patch.object(stock_import_services, 'apply_stock', side_effect=_db_error('40P01')):
            response = self.loader_api.post(f'/api/admin/inventory/import/{job.pk}/apply/')
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()['code'], 'busy')

    def test_a_retry_with_the_same_idempotency_key_after_busy_goes_through(self):
        from store import registration_services
        from store.test_registration import URL as REGISTER_URL

        from store.models import Category

        cases = Category.objects.create(company=self.company, name='Fundas', slug='fundas-invload2')
        body = {'kind': 'accessory',
                'product': {'name': 'Funda INVLOAD2', 'category': cases.pk, 'price': '40.00', 'sku': 'FUNDA-IL2',
                            'is_active': True, 'show_online': False},
                'stock': [{'branch': self.branch.pk, 'quantity': 2}]}
        real = registration_services.register
        with mock.patch.object(registration_services, 'register', side_effect=_db_error('40P01')):
            busy = self.loader_api.post(REGISTER_URL, body, format='json', HTTP_IDEMPOTENCY_KEY='invload2-reg-1')
        self.assertEqual(busy.status_code, 409, busy.content)
        self.assertFalse(Product.objects.filter(name='Funda INVLOAD2').exists())
        self.assertIs(registration_services.register, real)
        again = self.loader_api.post(REGISTER_URL, body, format='json', HTTP_IDEMPOTENCY_KEY='invload2-reg-1')
        self.assertEqual(again.status_code, 201, again.content)
        self.assertTrue(Product.objects.filter(name='Funda INVLOAD2').exists())


# ---------------------------------------------------------------------------
# 7. The whole rehearsal, with synthetic data
# ---------------------------------------------------------------------------

class SyntheticStockLoadTest(LoadShop, SaleShop, TestCase):
    """
    What `docs/carga-stock-real.md` asks the owner to rehearse on an isolated copy, end to end and with synthetic data:
    the two templates filled, previewed, applied as opening stock, checked, a mistaken purchase annulled whole, then a
    new device and a used one sold (the used one only once it is inspected, and on the web only once it is also
    photographed and published).
    """

    def setUp(self):
        self.build('invload2-sim')
        self.cashier, _ = _p2d_member(self.company, 'invload2_sim_caja', ['company.view', 'sales.pos.use'])

    def assert_stock_checks(self):
        out = io.StringIO()
        call_command('check_unit_stock', '--company', self.company.slug, stdout=out)
        self.assertIn('0 diferencia', out.getvalue())

    def test_two_templates_preview_apply_check_annul_and_sell(self):
        grade = CosmeticGrade.objects.get(company=self.company, code='excelente')
        common = {'Proveedor': 'Importadora Andina', 'Costo': 2500, 'Fecha de ingreso': '2026-09-15'}
        new_rows = [
            self.new_row('SIM-N-001', **common),
            self.new_row('SIM-N-002', **common),
            self.new_row('SIM-N-003', capacity='256 GB', **common),
            self.new_row('SIM-N-004', branch=self.north, model='Teléfono Z', SIM='eSIM',
                         **{'Versión (región / país de venta)': 'USA', 'Observaciones (notas internas)': 'Vitrina'},
                         **common),
        ]
        used_rows = [
            self.used_row('SIM-U-001', **{'Estado estético': grade.label, 'Salud de batería (%)': 91,
                                          'Estado funcional': 'Sin fallas', 'Accesorios': 'Caja',
                                          'Garantía (días)': 90, 'Precio de venta': 1999, 'Costo': 1200}),
            self.used_row('SIM-U-002', **{'Precio de venta': 1500, 'Costo': 900}),
            self.used_row('SIM-U-003', branch=self.north, model='Teléfono Z', SIM='eSIM',
                          **{'Versión (región / país de venta)': 'USA', 'Precio de venta': 2100}),
        ]

        # Preview: nothing is written, the example rows are skipped, the simulation says what will happen.
        new_job = self.preview('new', new_rows, entry_kind='opening')
        used_job = self.preview('used', used_rows, entry_kind='opening')
        for job, count in ((new_job, 4), (used_job, 3)):
            self.assertEqual((job['counts']['create'], job['counts']['skip'], job['counts']['error']), (count, 1, 0),
                             job['rows'])
            self.assertEqual(job['summary']['simulation']['units']['initial_stock'], count)
        self.assertFalse(StockUnit.objects.filter(serial_number__startswith='SIM-').exists())

        # Apply, then the check: the shelves are their available devices.
        self.apply(new_job)
        self.apply(used_job)
        self.assertEqual(StockUnit.objects.filter(serial_number__startswith='SIM-').count(), 7)
        self.assertEqual(set(StockMovement.objects.values_list('movement_type', flat=True)),
                         {StockMovement.INITIAL_STOCK})
        self.assert_stock_checks()
        for unit in StockUnit.objects.filter(product__condition_line='used'):
            self.assertEqual((unit.inspection_status, unit.published_online), ('pending', False))
        report = self.loader_api.get(report_url(new_job['id']), {'as': 'csv'}).content.decode('utf-8-sig')
        self.assertNotIn(',No', report)

        # A mistaken purchase, annulled whole: the check still holds and the Kardex keeps both lines.
        wrong = self.apply(self.preview('new', [self.new_row('SIM-X-001'), self.new_row('SIM-X-002')],
                                        entry_kind='purchase', reason='Compra duplicada'))
        voided = self.loader_api.post(void_url(wrong['id']), {'reason': 'Archivo duplicado'}, format='json')
        self.assertEqual(voided.status_code, 200, voided.content)
        self.assertEqual(StockMovement.objects.filter(movement_type=StockMovement.PURCHASE_ENTRY).count(), 1)
        self.assertEqual(StockMovement.objects.filter(movement_type=StockMovement.CORRECTION_NEGATIVE).count(), 2)
        self.assert_stock_checks()

        # A new device sells at the till at its variant's price.
        new_unit = StockUnit.objects.get(serial_number='SIM-N-001')
        order, created = _c1_sale(company=self.company, branch=self.branch, actor=self.cashier, items=[
            {'product': self.new128.pk, 'quantity': 1, 'units': [{'id': new_unit.pk, 'method': 'scanner'}]}])
        self.assertTrue(created)
        self.assertEqual(order.items.get().price, Decimal('3000.00'))

        # A used device: refused at the till while pending; sold once inspected.
        till_used = StockUnit.objects.get(serial_number='SIM-U-002')
        line = {'product': self.used128.pk, 'quantity': 1, 'units': [{'id': till_used.pk, 'method': 'scanner'}]}
        from store.pos_services import PosValidationError

        with self.assertRaises(PosValidationError):
            _c1_sale(company=self.company, branch=self.branch, actor=self.cashier, items=[line])
        inspected = self.client_for_inspector().post(
            f'/api/admin/inventory/units/{till_used.pk}/inspection/', {'decision': 'approved'}, format='json')
        self.assertEqual(inspected.status_code, 200, inspected.content)
        order, created = _c1_sale(company=self.company, branch=self.branch, actor=self.cashier, items=[line])
        self.assertEqual(order.items.get().price, Decimal('1500.00'))

        # On the web: only once inspected, photographed AND published.
        web_used = StockUnit.objects.get(serial_number='SIM-U-001')
        from store import unit_hold_services as holds

        with self.assertRaises(holds.HoldError):
            holds.hold(product=self.used128, branch=self.branch, session_key='carrito-sim',
                       unit_public_id=str(web_used.public_id))
        used_units.set_inspection(web_used, decision='approved', actor=self.inspector)
        # UNIT-PHOTO-02: a REAL photo — attested as this device, its file an own verified photo.
        from store.used_unit_test_support import real_photo

        real_photo(web_used, self.inspector)
        published = self.loader_api.post(f'/api/admin/inventory/units/{web_used.pk}/publication/',
                                          {'published': True}, format='json')
        self.assertEqual(published.status_code, 200, published.content)
        holds.hold(product=self.used128, branch=self.branch, session_key='carrito-sim',
                   unit_public_id=str(web_used.public_id))
        web_order = self.checkout('carrito-sim')
        self.pay(web_order)
        web_used.refresh_from_db()
        self.assertEqual((web_used.status, web_order.items.get().price), ('sold', Decimal('1999.00')))

        # After the sales the shelves still are their devices.
        self.assert_stock_checks()
        for product, branch in ((self.new128, self.branch), (self.used128, self.branch), (self.znew, self.north)):
            self.assertEqual(inventory.branch_quantity(branch, product), StockUnit.objects.filter(
                product=product, branch=branch, status='available').count())

    def client_for_inspector(self):
        return client_for(self.inspector)


# ---------------------------------------------------------------------------
# 8. Review of #409
# ---------------------------------------------------------------------------

class LineNeverGuessedTest(LoadShop, TestCase):
    """A used device never enters as new because the sheet was renamed or pasted into another workbook."""

    def setUp(self):
        self.build('invload2-guess')
        # The dangerous case: the same options exist in BOTH lines, so a default of «new» would find a variant.
        self.new_blue = self.variant('Teléfono X 128 GB Azul', 'tx-128-azul', '3100.00', [self.gb128, self.blue])

    def renamed(self, rows, sheet='Hoja1'):
        book = load_workbook(io.BytesIO(self.template('used').content))
        book.worksheets[0].title = sheet
        headers = [c.value for c in book.worksheets[0][1]]
        for offset, row in enumerate(rows):
            for header, value in row.items():
                book.worksheets[0].cell(row=3 + offset, column=headers.index(header) + 1, value=value)
        out = io.BytesIO()
        book.save(out)
        return SimpleUploadedFile('copia.xlsx', out.getvalue(), content_type=XLSX)

    def test_used_details_without_a_line_are_a_row_error_not_a_new_device(self):
        upload = self.renamed([self.used_row('GUESS-0001', **{'Garantía (días)': 90})])
        response = self.loader_api.post(PREVIEW_URL, {'file': upload, 'reason': 'X'}, format='multipart')
        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()
        self.assertEqual(payload['counts']['create'], 0, payload['rows'])
        self.assertIn('nuevo o seminuevo', self.errors(payload)[3])

    def test_a_sale_price_alone_without_a_line_is_a_row_error_not_a_new_device(self):
        # NEW-TEMPLATE-PRICE review: «Precio de venta» left the used-only columns (it is in both templates now), but
        # without a line it still says «seminuevo»: never a new device with its price dropped.
        upload = self.renamed([self.used_row('GUESS-P001')])
        response = self.loader_api.post(PREVIEW_URL, {'file': upload, 'reason': 'X'}, format='multipart')
        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()
        self.assertEqual(payload['counts']['create'], 0, payload['rows'])
        self.assertIn('nuevo o seminuevo', self.errors(payload)[3])

    def test_the_same_file_with_the_line_chosen_on_screen_loads_used(self):
        upload = self.renamed([self.used_row('GUESS-0002', **{'Garantía (días)': 90})])
        response = self.loader_api.post(PREVIEW_URL, {'file': upload, 'reason': 'X', 'line': 'used'},
                                        format='multipart')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['counts']['error'], 0, response.json()['rows'])
        self.apply(response.json())
        self.assertEqual(StockUnit.objects.get(serial_number='GUESS-0002').product, self.used128)

    def test_a_row_that_says_its_line_keeps_the_old_behaviour(self):
        from store.test_cost_imports import upload as legacy_upload
        from store.test_unit_import import HEADERS as LEGACY

        headers = [*LEGACY, 'Modelo', 'Capacidad', 'Color', 'Línea', 'Garantía (días)']
        file = legacy_upload(headers, [['', '', self.branch.name, 'GUESS-0003', next(self._imeis), '', '', '',
                                        'Inventario', 'Teléfono X', '128 GB', 'Negro', 'Nuevo', '30']], title='Equipos')
        payload = self.loader_api.post(PREVIEW_URL, {'file': file}, format='multipart').json()
        self.assertEqual(payload['counts']['error'], 0, payload['rows'])
        self.assertTrue(any('ignorado en un equipo nuevo' in w for r in payload['rows'] for w in r['warnings']))


class VoidReviewTest(LoadShop, TestCase):
    def setUp(self):
        self.build('invload2-vrev')
        self.job = self.apply(self.preview('used', [self.used_row('VREV-0001'), self.used_row('VREV-0002')],
                                           entry_kind='purchase', reason='Compra F001-1'))
        self.units = list(StockUnit.objects.filter(serial_number__startswith='VREV-').order_by('pk'))

    def preview_void(self):
        response = self.loader_api.get(void_url(self.job['id']))
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_a_device_in_a_draft_transfer_blocks_the_batch_and_the_single_annulment(self):
        from store import transfer_unit_services

        transfer = inventory.create_stock_transfer(company=self.company, source_branch=self.branch,
                                                   destination_branch=self.north, actor=self.staff)
        transfer_unit_services.add_unit(transfer, code=self.units[0].imei, method='scanner', actor=self.staff)
        body = self.loader_api.post(void_url(self.job['id']), {'reason': 'Error'}, format='json')
        self.assertEqual(body.status_code, 409, body.content)
        self.assertEqual([b['unit_id'] for b in body.json()['blocking']], [self.units[0].pk])
        self.assertIn('transferencia', body.json()['blocking'][0]['reason'])
        with self.assertRaises(units.StockUnitError):
            units.void_unit(self.units[0], reason='Error', reason_code='typo', actor=self.staff)
        self.assertEqual(self.preview_void()['blocking'][0]['unit_id'], self.units[0].pk)

    def test_the_preview_counts_what_was_already_prepared_and_the_annulment_asks_to_confirm_it(self):
        clean = self.preview_void()
        self.assertEqual((clean['units'], clean['inspected'], clean['published'], clean['with_photos'],
                          clean['blocking']), (2, 0, 0, 0, []))
        self.assertGreater(clean['reason_max'], 200)
        self.assertLess(clean['reason_max'], 300)
        from store.used_unit_test_support import ready_used

        ready_used(self.units[0], sale_price='1999.00')
        said = self.preview_void()
        self.assertEqual((said['inspected'], said['published'], said['with_photos']), (1, 1, 1))
        refused = self.loader_api.post(void_url(self.job['id']), {'reason': 'Error'}, format='json')
        self.assertEqual(refused.status_code, 409, refused.content)
        self.assertEqual(refused.json()['code'], 'confirm_used_ready')
        self.assertEqual(StockUnit.objects.filter(status=StockUnit.Status.VOIDED).count(), 0)
        done = self.loader_api.post(void_url(self.job['id']), {'reason': 'Error', 'confirm_used': True},
                                    format='json')
        self.assertEqual(done.status_code, 200, done.content)
        self.assertEqual(StockUnit.objects.filter(status=StockUnit.Status.VOIDED).count(), 2)

    def test_the_preview_writes_nothing_and_respects_the_tenant(self):
        movements = StockMovement.objects.count()
        self.preview_void()
        self.assertEqual(StockMovement.objects.count(), movements)
        other = _p3_company('invload2-vrev-otra', 'Otra')
        stranger, _ = _p2d_member(other, 'invload2_vrev_ajeno', ['company.view', 'inventory.view', 'inventory.adjust'])
        self.assertEqual(client_for(stranger).get(void_url(self.job['id'])).status_code, 404)


class AppliedHistoryTest(LoadShop, TestCase):
    def setUp(self):
        self.build('invload2-hist')

    def test_applied_loads_older_than_a_page_of_previews_are_still_listed(self):
        applied = self.apply(self.preview('new', [self.new_row('HIST-0001')], entry_kind='purchase'))
        BulkImportJob.objects.bulk_create([
            BulkImportJob(company=self.company, import_type=BulkImportJob.UNITS, created_by=self.loader,
                          mapping_snapshot={'branch_ids': [self.branch.pk]}) for _ in range(55)])
        listed = self.loader_api.get('/api/admin/imports/', {'type': 'units', 'status': 'applied'}).json()['results']
        self.assertEqual([job['id'] for job in listed], [applied['id']])
        self.assertEqual(self.loader_api.get('/api/admin/imports/', {'status': 'otra'}).status_code, 400)
