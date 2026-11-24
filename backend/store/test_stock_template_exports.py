"""
STOCK-TEMPLATE-EXPORT — lo que hay en estante, con el formato de «Stock de equipos nuevos» y «Stock de seminuevos».

Dos sabores por plantilla (decisión del propietario, 2026-11-24):

  A. `masked` — revisión: los IMEI y las series ocultos salvo sus últimos 4. Basta `inventory.view`. La hoja se
     llama «Revisión · <hoja de la plantilla>» y ninguna carga la acepta.
  B. `full` — operativa: identificadores completos, exactamente la hoja y las columnas de la plantilla. Exige
     `inventory.adjust` (el permiso que carga el stock). Queda en la auditoría con quién, empresa, sucursales y filas.

Y la regla nueva para las descargas que ya existían: el IMEI y la serie completos sólo salen hacia quien tiene
`inventory.adjust`. Con sólo `inventory.view`, «Equipos con serie» (reporte y para volver a cargar), el ZIP y el
reporte integral los llevan ocultos, y la carga de equipos rechaza un archivo con un identificador oculto.
"""
import io
import zipfile
from decimal import Decimal

from django.utils import timezone
from openpyxl import Workbook, load_workbook

from store import (
    device_catalog, identifier_masking, import_assistant, import_exports, inventory_exports, unit_import_services,
)
from store.models import (
    AdminAuditLog, BulkImportJob, CosmeticGrade, Order, Product, StockMovement, StockUnit, Supplier,
)
from store.test_inventory_exports import _Base, upload, url
from store.test_stock_units import IMEI_A, IMEI_B, IMEI_C, IMEI_D, imei

IMEI_U = imei('35693803564390')
IMEI_FOREIGN = imei('35209900176148')
MASK = identifier_masking.MASK
NEW, USED = unit_import_services.LINE_NEW, unit_import_services.LINE_USED


def _headers(line):
    return [header for header, _field in unit_import_services.STOCK_TEMPLATES[line]['columns']]


def _cells(content):
    """Every cell of every sheet of a workbook, as text."""
    book = load_workbook(io.BytesIO(content))
    return {str(cell) for sheet in book.worksheets for row in sheet.iter_rows(values_only=True)
            for cell in row if cell is not None}


class _Stock(_Base):
    """
    The export fixture plus a NEW variant («Serie 16» 128 GB Negro eSIM LL/A: SER-0001…SER-0003, the third in Norte)
    and a USED one («Serie 16» 128 GB Azul: SER-U001 in Centro, from a supplier). Another shop holds a device too.
    """

    def setUp(self):
        super().setUp()
        device_catalog.seed_cosmetic_grades(self.company)
        self.grade = CosmeticGrade.objects.filter(company=self.company, is_active=True).order_by('sort_order').first()
        capacity = device_catalog.create_attribute(company=self.company, name='Capacidad', role='capacity')
        colour = device_catalog.create_attribute(company=self.company, name='Color', kind='color', role='color')
        sim = device_catalog.create_attribute(company=self.company, name='Tipo de SIM', role='sim')
        region = device_catalog.create_attribute(company=self.company, name='Región', role='region')
        self.gb128 = gb128 = device_catalog.create_option(company=self.company, attribute_id=capacity.pk, label='128 GB')
        self.black = black = device_catalog.create_option(company=self.company, attribute_id=colour.pk, label='Negro',
                                             swatch_hex='#111111')
        self.blue = blue = device_catalog.create_option(company=self.company, attribute_id=colour.pk, label='Azul',
                                            swatch_hex='#1d4ed8')
        self.esim = esim = device_catalog.create_option(company=self.company, attribute_id=sim.pk, label='eSIM')
        self.lla = lla = device_catalog.create_option(company=self.company, attribute_id=region.pk, label='LL/A')
        self.model = device_catalog.create_model(company=self.company, name='Serie 16')
        device_catalog.set_variant(self.phone, model_id=self.model.pk, option_ids=[gb128.pk, black.pk, esim.pk, lla.pk])
        self.used = self.product('Teléfono X usado', 'telefono-x-usado', '1.00', self.phones,
                                 is_serialized=True, requires_imei=True)
        self.used = device_catalog.set_variant(self.used, model_id=self.model.pk,
                                               condition_line=Product.CONDITION_USED, option_ids=[gb128.pk, blue.pk])
        self.supplier = Supplier.objects.create(company=self.company, name='Mayorista Andino')
        from store import stock_unit_services

        stock_unit_services.receive_units(
            branch=self.centro, product=self.used, actor=self.admin, reason='Compra de prueba', supplier=self.supplier,
            units=[{'serial_number': 'SER-U001', 'imei': IMEI_U, 'condition': 'used', 'cost': '1200.00',
                    'cosmetic_grade': self.grade.pk, 'battery_health': 88, 'sale_price': '1899.90',
                    'notes': 'Vitrina 2', 'public_notes': 'Sin rayones', 'warranty_days': 90,
                    'functional_state': 'with_faults', 'defects': 'Botón lateral duro', 'accessories': 'Caja'}])
        foreign = Product.objects.create(company=self.other, name='Teléfono ajeno', slug='tel-ajeno',
                                         price=Decimal('10.00'), is_serialized=True, requires_imei=True)
        stock_unit_services.receive_units(branch=self.foreign_branch, product=foreign, reason='Ajeno', units=[
            {'serial_number': 'SER-AJENA', 'imei': IMEI_FOREIGN}])

    def book(self, response):
        self.assertEqual(response.status_code, 200, response.content[:300])
        return load_workbook(io.BytesIO(response.content))

    def data(self, response):
        """(title, headers, [{header: value}]) of the FIRST sheet: the data."""
        page = self.book(response).worksheets[0]
        rows = [list(row) for row in page.iter_rows(values_only=True)]
        return page.title, rows[0], [dict(zip(rows[0], row)) for row in rows[1:]]


# =============================================================================
# The shape: the template's sheet and columns, one row per device on the shelf
# =============================================================================

class TemplateShapeTest(_Stock):

    def test_the_full_file_is_the_template_sheet_and_columns_one_row_per_device_of_its_line(self):
        for kind, line, serials in (('units_new', NEW, ['SER-0001', 'SER-0002', 'SER-0003']),
                                    ('units_used', USED, ['SER-U001'])):
            with self.subTest(kind):
                title, headers, rows = self.data(self.get(kind, mode='full'))
                self.assertEqual(title, unit_import_services.STOCK_TEMPLATES[line]['sheet'])
                self.assertEqual(headers, _headers(line))
                self.assertEqual(sorted(row['Serie'] for row in rows), serials)
                self.assertEqual(self.book(self.get(kind, mode='full')).sheetnames[1:],
                                 [unit_import_services.HELP_SHEET])

    def test_the_cells_say_what_the_load_reads_back(self):
        _title, _headers, rows = self.data(self.get('units_used', mode='full'))
        self.assertEqual(rows, [{
            'Sucursal': 'Centro', 'Modelo': 'Serie 16', 'Capacidad': '128 GB', 'Color': 'Azul', 'SIM': None,
            'Versión (región / país de venta)': None, 'IMEI 1': IMEI_U, 'IMEI 2': None, 'Serie': 'SER-U001',
            'Proveedor': 'Mayorista Andino', 'Costo': 1200, 'Precio de venta': 1899.9,
            'Estado estético': self.grade.label, 'Salud de batería (%)': 88, 'Estado funcional': 'Con fallas',
            'Defectos': 'Botón lateral duro', 'Accesorios': 'Caja', 'Garantía (días)': 90,
            'Detalles para el comprador': 'Sin rayones',
            'Fecha de ingreso': timezone.localdate().isoformat(), 'Observaciones (notas internas)': 'Vitrina 2',
        }])
        _title, _headers, rows = self.data(self.get('units_new', mode='full'))
        first = next(row for row in rows if row['Serie'] == 'SER-0003')
        self.assertEqual(
            [first[h] for h in ('Sucursal', 'Modelo', 'Capacidad', 'Color', 'SIM', 'Versión (región / país de venta)',
                                'IMEI 1', 'IMEI 2', 'Costo')],
            ['Norte', 'Serie 16', '128 GB', 'Negro', 'eSIM', 'LL/A', IMEI_C, IMEI_D, 3650.5])

    def test_every_template_column_has_a_source_in_the_exporter(self):
        """The drift guard: a column a stock template gains is written, or the exporter is told why not."""
        for line, spec in unit_import_services.STOCK_TEMPLATES.items():
            with self.subTest(line):
                fields = {field for _header, field in spec['columns']}
                self.assertLessEqual(fields, set(inventory_exports.TEMPLATE_FIELDS), fields)

    def test_a_fully_filled_device_fills_every_column_of_both_templates(self):
        """
        Review of #428: no template column may come out empty by omission. A device with everything stated, read by
        somebody who may see costs, suppliers and whole identifiers, fills every column of its template.
        """
        from store import stock_unit_services

        full_new = self.product('Teléfono X lleno', 'telefono-x-lleno', '4999.00', self.phones,
                                is_serialized=True, requires_imei=True)
        full_new = device_catalog.set_variant(full_new, model_id=self.model.pk,
                                   option_ids=[self.gb128.pk, self.blue.pk, self.esim.pk, self.lla.pk])
        full_used = self.product('Teléfono X usado lleno', 'telefono-x-usado-lleno', '1.00', self.phones,
                                 is_serialized=True, requires_imei=True)
        full_used = device_catalog.set_variant(full_used, model_id=self.model.pk, condition_line=Product.CONDITION_USED,
                                   option_ids=[self.gb128.pk, self.black.pk, self.esim.pk, self.lla.pk])
        stock_unit_services.receive_units(
            branch=self.norte, product=full_new, actor=self.admin, reason='Compra', supplier=self.supplier,
            units=[{'serial_number': 'SER-LLENO-N', 'imei': imei('35693803564391'), 'imei2': imei('35693803564392'),
                    'cost': '4000.00', 'notes': 'Vitrina 1'}])
        stock_unit_services.receive_units(
            branch=self.norte, product=full_used, actor=self.admin, reason='Compra', supplier=self.supplier,
            units=[{'serial_number': 'SER-LLENO-U', 'imei': imei('35693803564393'), 'imei2': imei('35693803564394'),
                    'condition': 'used', 'cost': '900.00', 'cosmetic_grade': self.grade.pk, 'battery_health': 91,
                    'sale_price': '1500.00', 'notes': 'Vitrina 3', 'public_notes': 'Como nuevo', 'warranty_days': 30,
                    'functional_state': 'full', 'defects': 'Ninguno visible', 'accessories': 'Caja y cable'}])
        for kind, line, serial in (('units_new', NEW, 'SER-LLENO-N'), ('units_used', USED, 'SER-LLENO-U')):
            with self.subTest(kind):
                _title, headers, rows = self.data(self.get(kind, mode='full'))
                row = next(row for row in rows if row['Serie'] == serial)
                self.assertEqual(headers, _headers(line))
                self.assertEqual([header for header in headers if row[header] in (None, '')], [])

    def test_the_writer_maps_every_field_and_a_new_device_carries_its_variant_price(self):
        """
        Every field of `TEMPLATE_FIELDS` has a value for every device (the writer reads them by key, never with a
        default). «Precio de venta» of a NEW device is its VARIANT's price (#426: the new template reads it as the
        expected price and compares it with the variant's, so a reload proposes nothing); a USED one carries its own.
        Checked on the mapping, not on the header, so it holds whether or not the new template has the column yet.
        """
        new_unit = StockUnit.objects.get(serial_number='SER-0001')
        used_unit = StockUnit.objects.get(serial_number='SER-U001')
        filters = inventory_exports.ExportFilters(branches=[self.centro, self.norte], full_identifiers=True)
        for unit, line in ((new_unit, NEW), (used_unit, USED)):
            values = inventory_exports.template_values(unit, line=line, filters=filters, company=self.company)
            self.assertEqual(set(values), set(inventory_exports.TEMPLATE_FIELDS))
        self.assertEqual(inventory_exports.template_values(new_unit, line=NEW, filters=filters,
                                                           company=self.company)['sale_price'], Decimal('4500.00'))
        self.assertEqual(inventory_exports.template_values(used_unit, line=USED, filters=filters,
                                                           company=self.company)['sale_price'], Decimal('1899.90'))

    def test_identifiers_are_text_cells(self):
        page = self.book(self.get('units_new', mode='full')).worksheets[0]
        headers = [cell.value for cell in page[1]]
        for header in ('IMEI 1', 'IMEI 2', 'Serie'):
            cell = page.cell(row=2, column=headers.index(header) + 1)
            if cell.value is not None:
                self.assertEqual((cell.data_type, cell.number_format), ('s', '@'), header)

    def test_only_what_is_on_the_shelf(self):
        sold = StockUnit.objects.get(serial_number='SER-0002')
        order = Order.objects.create(company=self.company, fulfillment_branch=self.centro,
                                     customer_email='comprador@correo.test', total=Decimal('4500.00'),
                                     status=Order.Status.PAID, paid=True, paid_at=timezone.now())
        StockUnit.objects.filter(pk=sold.pk).update(status=StockUnit.Status.SOLD, sold_at=timezone.now(), order=order)
        for mode in ('masked', 'full'):
            with self.subTest(mode):
                response = self.get('units_new', mode=mode)
                _title, _headers, rows = self.data(response)
                self.assertEqual(len(rows), 2)
                self.assertFalse([cell for cell in _cells(response.content) if 'comprador' in cell or '@' in cell])

    def test_filters_and_refusals(self):
        _t, _h, rows = self.data(self.get('units_new', mode='full', branches=self.norte.pk))
        self.assertEqual([row['Serie'] for row in rows], ['SER-0003'])
        _t, _h, rows = self.data(self.get('units_new', mode='full', condition='open_box'))
        self.assertEqual([row['Serie'] for row in rows], ['SER-0003'])
        for query in ({'mode': 'report'}, {'mode': 'reimport'}, {'mode': 'nada'}, {'stock': 'out_of_stock'},
                      {'line': 'used'}, {'line': 'new,used'}):
            with self.subTest(query):
                self.assertEqual(self.get('units_new', **query).status_code, 400)
        # The line the kind fixes, written again, is harmless.
        self.assertEqual(self.get('units_new', line='new').status_code, 200)

    def test_the_default_is_the_masked_review_and_the_names_are_fixed(self):
        response = self.get('units_used')
        self.assertEqual(self.data(response)[0], identifier_masking.masked_sheet_title('Equipos seminuevos'))
        self.assertIn('stock-seminuevos-revision.xlsx', response['Content-Disposition'])
        self.assertIn('stock-seminuevos-completo.xlsx', self.get('units_used', mode='full')['Content-Disposition'])
        self.assertIn('stock-equipos-nuevos-revision.xlsx', self.get('units_new')['Content-Disposition'])
        self.assertEqual(response['Cache-Control'], 'no-store')


# =============================================================================
# Masked: the review copy
# =============================================================================

class MaskedReviewTest(_Stock):

    def test_mask_keeps_the_last_four_and_never_more_than_half(self):
        self.assertEqual(identifier_masking.mask(IMEI_A), MASK * 11 + IMEI_A[-4:])
        self.assertEqual(identifier_masking.mask('ABC123'), MASK * 3 + '123')
        self.assertEqual(identifier_masking.mask('AB'), MASK + 'B')
        self.assertEqual(identifier_masking.mask(None), '')

    def test_the_masked_sheet_names_fit_in_excel(self):
        for spec in unit_import_services.STOCK_TEMPLATES.values():
            self.assertLessEqual(len(identifier_masking.masked_sheet_title(spec['sheet'])), 31)

    def test_no_full_identifier_leaves_in_the_review_copy_and_it_says_so(self):
        for kind, line in (('units_new', NEW), ('units_used', USED)):
            with self.subTest(kind):
                response = self.get(kind, mode='masked')
                title, headers, rows = self.data(response)
                self.assertEqual(title, identifier_masking.masked_sheet_title(
                    unit_import_services.STOCK_TEMPLATES[line]['sheet']))
                self.assertEqual(headers, _headers(line))
                cells = _cells(response.content)
                for full in (IMEI_A, IMEI_B, IMEI_C, IMEI_D, IMEI_U, 'SER-0001', 'SER-0002', 'SER-0003', 'SER-U001'):
                    self.assertFalse([cell for cell in cells if full in cell], full)
                for row in rows:
                    for header in ('IMEI 1', 'Serie'):
                        self.assertIn(MASK, row[header])
                notice = ' '.join(str(cell) for row in self.book(response)[unit_import_services.HELP_SHEET]
                                  .iter_rows(values_only=True) for cell in row if cell)
                self.assertIn('ocultos', notice)
        _t, _h, rows = self.data(self.get('units_new', mode='masked', branches=self.norte.pk))
        self.assertEqual((rows[0]['IMEI 1'], rows[0]['IMEI 2'], rows[0]['Serie']),
                         (MASK * 11 + IMEI_C[-4:], MASK * 11 + IMEI_D[-4:], MASK * 4 + '0003'))

    def test_the_review_copy_keeps_the_model_of_a_device_whose_variant_does_not_read_back(self):
        # A second model with the same name (only an older release could write one): the full file leaves «Modelo»
        # empty, because a reload would guess; the review copy names the product instead.
        from store.models import ProductModel

        ProductModel.objects.bulk_create([ProductModel(company=self.company, name='SERIE 16', slug='serie-16-bis')])
        _t, _h, full = self.data(self.get('units_used', mode='full'))
        self.assertIsNone(full[0]['Modelo'])
        _t, _h, masked = self.data(self.get('units_used', mode='masked'))
        self.assertEqual(masked[0]['Modelo'], 'Teléfono X usado')


# =============================================================================
# Who: permission, company and branch
# =============================================================================

class TemplateAccessTest(_Stock):

    def test_masked_needs_inventory_view_and_full_needs_inventory_adjust(self):
        viewer = self.member('stx_lectora', 'inventory.view')
        adjuster = self.member('stx_ajuste', 'inventory.view', 'inventory.adjust')
        writer_only = self.member('stx_solo_ajuste', 'inventory.adjust')
        nobody = self.member('stx_nadie', 'products.view', 'costs.view')
        for kind in ('units_new', 'units_used'):
            with self.subTest(kind):
                self.assertEqual(self.get(kind, viewer, mode='masked').status_code, 200)
                refused = self.get(kind, viewer, mode='full')
                self.assertEqual(refused.status_code, 403)
                self.assertEqual(refused.json()['code'], 'full_identifiers_forbidden')
                self.assertEqual(self.get(kind, adjuster, mode='full').status_code, 200)
                # Reading the inventory is still what opens a download.
                self.assertEqual(self.get(kind, writer_only, mode='full').status_code, 403)
                self.assertEqual(self.get(kind, nobody, mode='masked').status_code, 403)
        self.assertEqual(self.client.get(url('units_new')).status_code, 401)

    def test_a_branch_limited_person_gets_only_their_branches(self):
        limited = self.member('stx_norte', 'inventory.view', 'inventory.adjust', branches=[self.norte])
        _t, _h, rows = self.data(self.get('units_new', limited, mode='full'))
        self.assertEqual([row['Serie'] for row in rows], ['SER-0003'])
        self.assertEqual(self.get('units_new', limited, mode='full', branches=self.centro.pk).status_code, 404)

    def test_another_company_never_sees_this_one(self):
        stranger = self.member('stx_ajena', 'inventory.view', 'inventory.adjust', 'costs.view', company=self.other)
        self.assertIn(self.get('units_new', stranger, mode='full', company=self.company.pk).status_code, (403, 404))
        # Their own shop's device has no variant: «Modelo» stays empty in the full file, but it is theirs.
        _t, _h, rows = self.data(self.get('units_new', stranger, mode='full'))
        self.assertEqual([row['Serie'] for row in rows], ['SER-AJENA'])
        self.assertEqual(self.get('units_new', stranger, mode='full', branches=self.centro.pk).status_code, 404)
        mine = _cells(self.get('units_new', mode='full').content)
        self.assertNotIn('SER-AJENA', mine)
        self.assertNotIn(IMEI_FOREIGN, mine)

    def test_cost_only_with_costs_view_and_supplier_only_with_the_template_rule(self):
        adjuster = self.member('stx_ajuste', 'inventory.view', 'inventory.adjust')
        _t, _h, rows = self.data(self.get('units_used', adjuster, mode='full'))
        self.assertEqual((rows[0]['Costo'], rows[0]['Proveedor']), (None, None))
        self.assertFalse([cell for cell in _cells(self.get('units_used', adjuster, mode='full').content)
                          if 'Mayorista' in cell or cell in ('1200', '1200.0')])
        catalogue = self.member('stx_catalogo', 'inventory.view', 'inventory.adjust', 'products.view')
        _t, _h, rows = self.data(self.get('units_used', catalogue, mode='full'))
        self.assertEqual((rows[0]['Costo'], rows[0]['Proveedor']), (None, 'Mayorista Andino'))
        costs = self.member('stx_costos', 'inventory.view', 'costs.view')
        _t, _h, rows = self.data(self.get('units_used', costs, mode='masked'))
        self.assertEqual((rows[0]['Costo'], rows[0]['Proveedor']), (1200, 'Mayorista Andino'))

    def test_each_download_leaves_one_audit_line_with_who_where_and_how_many_never_a_cell(self):
        AdminAuditLog.objects.all().delete()
        limited = self.member('stx_norte', 'inventory.view', 'inventory.adjust', branches=[self.norte])
        self.get('units_new', limited, mode='full')
        self.get('units_new', limited, mode='masked')
        self.get('units_new', limited, mode='full', branches=self.centro.pk)    # refused: no line
        full, masked = AdminAuditLog.objects.filter(action='inventory_exported').order_by('pk')
        self.assertEqual((full.actor.username, full.company_id), ('stx_norte', self.company.pk))
        self.assertEqual((full.metadata['kind'], full.metadata['mode'], full.metadata['identifiers']),
                         ('units_new', 'full', 'full'))
        self.assertEqual((full.metadata['filters']['branches'], full.metadata['rows']),
                         ([self.norte.pk], {'units_new': 1}))
        self.assertEqual(masked.metadata['identifiers'], 'masked')
        for line in (full, masked):
            text = str(line.metadata)
            for value in (IMEI_C, IMEI_D, 'SER-0003'):
                self.assertNotIn(value, text)


# =============================================================================
# The loads: a masked file never goes in
# =============================================================================

class MaskedNeverLoadsTest(_Stock):

    def preview(self, content, **extra):
        return self.api.post('/api/admin/inventory/units/import/preview/',
                             {'file': upload(content), 'reason': 'Recarga', 'entry_kind': 'opening', **extra},
                             format='multipart')

    def test_the_review_copy_is_refused_before_anything_is_staged(self):
        jobs = BulkImportJob.objects.count()
        for kind, line in (('units_new', 'new'), ('units_used', 'used')):
            with self.subTest(kind):
                staged = self.preview(self.get(kind, mode='masked').content, line=line)
                self.assertEqual(staged.status_code, 400, staged.content)
                self.assertIn('copia de revisión', staged.json()['detail'])
        self.assertEqual(BulkImportJob.objects.count(), jobs)

    def test_masked_rows_pasted_into_a_real_template_are_refused_whole(self):
        title, headers, rows = self.data(self.get('units_new', mode='masked'))
        book = Workbook()
        sheet = book.active
        sheet.title = unit_import_services.STOCK_TEMPLATES[NEW]['sheet']
        sheet.append(headers)
        for row in rows:
            sheet.append([row[header] for header in headers])
        out = io.BytesIO()
        book.save(out)
        jobs, movements = BulkImportJob.objects.count(), StockMovement.objects.count()
        staged = self.preview(out.getvalue(), line='new')
        self.assertEqual(staged.status_code, 400, staged.content)
        self.assertIn('oculta', staged.json()['detail'])
        self.assertEqual((BulkImportJob.objects.count(), StockMovement.objects.count()), (jobs, movements))

    def test_the_full_file_reloaded_into_the_same_shop_registers_nothing(self):
        units = StockUnit.objects.count()
        staged = self.preview(self.get('units_used', mode='full').content, line='used')
        self.assertEqual(staged.status_code, 201, staged.content)
        body = staged.json()
        self.assertEqual(body['counts']['create'], 0)
        self.assertTrue(all(any('ya está registrado' in error for error in row['errors'])
                            for row in body['rows']), body['rows'])
        self.assertEqual(StockUnit.objects.count(), units)

    def test_the_assistant_calls_the_review_copy_a_report(self):
        book = load_workbook(io.BytesIO(self.get('units_used').content))
        verdict = import_assistant.classify([(name, [[cell.value for cell in book[name][1]]])
                                             for name in book.sheetnames])
        self.assertEqual(verdict['type'], 'report')
        self.assertIn('copia de revisión', verdict['detail'])
        self.assertTrue(import_exports.report_sheet_refusal('REVISIÓN · equipos nuevos'))


# =============================================================================
# The downloads that already existed: full identifiers only with `inventory.adjust`
# =============================================================================

class ExistingDownloadsMaskTest(_Stock):

    def test_the_device_report_hides_identifiers_from_who_cannot_load_stock(self):
        viewer = self.member('stx_lectora', 'inventory.view')
        for mode in ('report', 'reimport'):
            with self.subTest(mode):
                response = self.get('units', viewer, mode=mode)
                cells = _cells(response.content)
                for full in (IMEI_A, IMEI_C, IMEI_D, IMEI_U, 'SER-0001', 'SER-U001'):
                    self.assertFalse([cell for cell in cells if full in cell], full)
                self.assertIn(MASK * 11 + IMEI_A[-4:], cells)
                adjuster = _cells(self.get('units', self.member(f'stx_ajuste_{mode}', 'inventory.view',
                                                                'inventory.adjust'), mode=mode).content)
                self.assertIn(IMEI_A, adjuster)
                self.assertIn('SER-U001', adjuster)
        line = AdminAuditLog.objects.filter(action='inventory_exported', actor__username='stx_lectora').first()
        self.assertEqual(line.metadata['identifiers'], 'masked')

    def test_the_zip_and_the_integral_report_hide_them_too(self):
        viewer = self.member('stx_dos', 'inventory.view', 'products.view')
        archive = zipfile.ZipFile(io.BytesIO(self.get('full', viewer).content))
        cells = _cells(archive.read('Equipos.xlsx'))
        self.assertFalse([cell for cell in cells if IMEI_A in cell or 'SER-0001' in cell])
        self.assertIn('ocultos', archive.read('LEEME.txt').decode())
        integral = _cells(self.get('integral', viewer).content)
        self.assertFalse([cell for cell in integral if IMEI_A in cell or 'SER-0001' in cell])
        self.assertIn(MASK * 11 + IMEI_A[-4:], integral)
        mine = zipfile.ZipFile(io.BytesIO(self.get('full').content))
        self.assertIn(IMEI_A, _cells(mine.read('Equipos.xlsx')))

    def test_a_masked_file_to_reload_is_refused_by_the_device_load(self):
        viewer = self.member('stx_lectora', 'inventory.view')
        staged = self.api.post('/api/admin/inventory/units/import/preview/',
                               {'file': upload(self.get('units', viewer).content), 'reason': 'Recarga'},
                               format='multipart')
        self.assertEqual(staged.status_code, 400, staged.content)
        self.assertIn('oculta', staged.json()['detail'])
