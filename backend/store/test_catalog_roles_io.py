"""
CATALOG-COMMERCIAL-01 · A1b-1 — columnas por rol en cargas y exportaciones, nombre de modelo
normalizado único y los tres hallazgos bajos de la auditoría de A1 (plan A1b §1, ADR §5 y §13).

Lo que estas pruebas fijan:

  * el nombre de un modelo se compara sin mayúsculas, sin acentos y con los espacios colapsados
    (DEC-CATALOG-NAME-01, D-A1b-1): dos modelos así iguales en una empresa se rechazan con `duplicate`
    nombrando al que ya existe; otra empresa sí puede tener ese nombre; el slug y las variantes no cambian;
  * la plantilla de productos, la de ofertas de proveedor y la exportación llevan «SIM» y «Región» por
    ROL; una plantilla antigua sin esas columnas carga igual que antes; lo exportado se relee sin cambios;
  * una fila que crearía una variante nueva debe estar completa según la paleta (nombra los roles que
    faltan); una variante que ya existe se encuentra diga lo que diga la paleta;
  * `resolve_variant_spec` devuelve el spec cuando una variante histórica del modelo lleva todas las
    opciones nombradas aunque la paleta ya no las admita; nunca acepta una combinación nueva fuera de ella;
  * la vista previa no repite la consulta de paleta por fila;
  * `update_attribute` distingue un choque de nombre (`duplicate`/`name`) de uno de rol (`role_taken`/`role`);
  * el reconocedor de texto y las propuestas de catálogo encuentran la capacidad por rol antes que por nombre;
  * nada de esto crea stock ni toca equipos; el contrato público no cambia.

Nada aquí es de Apple ni de la tienda piloto: nombres inventados, valores de cada empresa de prueba.
"""
import io
from decimal import Decimal as D
from unittest import mock

import openpyxl
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.utils import timezone

from store import device_catalog, import_exports, import_formats, inventory_exports, supplier_offer_import
from store.models import (
    BranchStock, Order, OrderItem, Product, ProductModel, StockMovement, StockUnit, VariantAttribute,
    VariantOption,
)
from store.test_cost_imports import upload
from store.test_device_commerce import _client
from store.test_model_palette import PaletteShop
from store.tests import _p2d_member, _p3_company, _prod


class RolesShop(PaletteShop):
    """`PaletteShop` plus a full palette (capacity, colour, SIM, region) and one variant that carries the four."""

    def build(self, slug='a1b-roles'):
        super().build(slug)
        self.palette(self.gb128, self.gb256, self.black, self.blue, self.esim, self.chip, self.lla, self.ja)
        self.full = _prod(self.company, 'Teléfono X 256 GB Azul eSIM LL/A', 'tx-256-azul-esim-lla',
                          price='3700.00', inventory=0)
        Product.objects.filter(pk=self.full.pk).update(is_serialized=True, requires_imei=True)
        self.full.refresh_from_db()
        device_catalog.set_variant(self.full, model_id=self.model.pk, option_ids=[
            self.gb256.pk, self.blue.pk, self.esim.pk, self.lla.pk])

    def preview(self, headers, rows, **options):
        from store import import_services

        return import_services.preview_products(
            company=self.company, actor=self.staff, filename='p.xlsx', upload=upload(headers, rows),
            options=options or None)

    def counts(self):
        return (BranchStock.objects.filter(product__company=self.company).count(),
                StockUnit.objects.filter(product__company=self.company).count(),
                StockMovement.objects.filter(product__company=self.company).count())


# ---------------------------------------------------------------------------
# Nombre de modelo normalizado (DEC-CATALOG-NAME-01)
# ---------------------------------------------------------------------------

class ModelNameTest(RolesShop, TestCase):
    def setUp(self):
        self.build('a1b-nombre')

    def test_the_normalized_name_is_written_by_every_save(self):
        self.model.refresh_from_db()
        self.assertEqual(self.model.normalized_name, 'telefono x')
        made = ProductModel.objects.create(company=self.company, name='  Tableta   Ñandú  ', slug='tableta')
        self.assertEqual(made.normalized_name, 'tableta nandu')
        self.assertEqual(device_catalog.normalize_model_name('  TELÉFONO   x '), 'telefono x')

    def test_two_models_that_normalize_equal_are_refused_naming_the_existing_one(self):
        for spelling in ('teléfono x', 'TELEFONO X', ' Teléfono   X ', 'Telefono x'):
            with self.subTest(spelling), self.assertRaises(device_catalog.CatalogError) as caught:
                device_catalog.create_model(company=self.company, name=spelling, actor=self.staff)
            self.assertEqual((caught.exception.code, caught.exception.field), ('duplicate', 'name'))
            self.assertIn('«Teléfono X»', str(caught.exception))
        self.assertEqual(ProductModel.objects.filter(company=self.company).count(), 1)

    def test_renaming_into_another_models_name_is_refused_and_nothing_moves(self):
        other = device_catalog.create_model(company=self.company, name='Tableta Z', actor=self.staff)
        before = (self.model.slug, list(self.model.variants.order_by('pk').values_list('pk', 'variant_key')))
        res = self.api.patch(f'/api/admin/catalog/models/{other.pk}/{self.q}', {'name': 'TELÉFONO  X'},
                             format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual((res.json()['code'], res.json()['field']), ('duplicate', 'name'))
        self.assertIn('Teléfono X', res.json()['detail'])
        other.refresh_from_db()
        self.assertEqual((other.name, other.normalized_name), ('Tableta Z', 'tableta z'))
        self.model.refresh_from_db()
        self.assertEqual(before, (self.model.slug, list(self.model.variants.order_by('pk')
                                                        .values_list('pk', 'variant_key'))))
        # Keeping its own name (another spelling) is not a collision with itself.
        res = self.api.patch(f'/api/admin/catalog/models/{self.model.pk}/{self.q}', {'name': 'Teléfono  X'},
                             format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.model.refresh_from_db()
        self.assertEqual(self.model.slug, before[0])

    def test_the_api_refuses_a_duplicate_on_creation_with_the_field_and_the_code(self):
        res = self.api.post(f'/api/admin/catalog/models/{self.q}', {'name': 'telefono x'}, format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual((res.json()['code'], res.json()['field']), ('duplicate', 'name'))

    def test_the_database_holds_the_rule_too(self):
        with self.assertRaises(IntegrityError):
            ProductModel.objects.create(company=self.company, name='TELÉFONO X', slug='otro')

    def test_another_company_may_use_the_same_name(self):
        other = _p3_company('a1b-otra', 'Otra tienda')
        made = device_catalog.create_model(company=other, name='Teléfono X')
        self.assertEqual(made.normalized_name, 'telefono x')
        self.assertEqual(ProductModel.objects.filter(normalized_name='telefono x').count(), 2)

    def test_the_migration_folds_exactly_as_the_service(self):
        from importlib import import_module

        migration = import_module('store.migrations.0166_catalog_model_normalized_name')
        for text in ('  TELÉFONO   X ', 'Ñandú', 'iPhone Pro', 'a' * 200):
            self.assertEqual(migration._fold(text), device_catalog.normalize_model_name(text))

    def test_the_migration_fill_stops_naming_the_colliding_models_and_fills_the_rest(self):
        from importlib import import_module

        from django.apps import apps
        from django.db import connection

        migration = import_module('store.migrations.0166_catalog_model_normalized_name')
        other = _p3_company('a1b-mig', 'Migración')
        # Two models of ONE company that fold equal, written as an OLDER release would (no `save()`, blank key:
        # the rule leaves blank rows out, so the database admits them — exactly the state 0166 meets).
        a, b = ProductModel.objects.bulk_create([
            ProductModel(company=other, name='Tableta Z', slug='tz-1'),
            ProductModel(company=other, name='tableta  z', slug='tz-2')])
        ProductModel.objects.filter(pk=self.model.pk).update(normalized_name='')
        self.assertEqual(set(ProductModel.objects.filter(pk__in=[a.pk, b.pk]).values_list('normalized_name', flat=True)),
                         {''})
        with connection.schema_editor() as editor, self.assertRaises(RuntimeError) as stopped:
            migration.fill_normalized_names(apps, editor)
        text = str(stopped.exception)
        self.assertIn(f'empresa {other.pk}', text)
        self.assertIn(f'{a.pk}', text)
        self.assertIn(f'{b.pk}', text)
        self.assertIn('Tableta Z', text)
        self.assertNotIn(f'empresa {self.company.pk}', text)
        # Nothing merged, renamed or deleted.
        self.assertEqual(ProductModel.objects.filter(pk__in=[a.pk, b.pk]).count(), 2)
        self.assertEqual(ProductModel.objects.get(pk=a.pk).name, 'Tableta Z')
        # Without the collision the fill writes every blank row.
        b.delete()
        with connection.schema_editor() as editor:
            migration.fill_normalized_names(apps, editor)
        self.assertEqual(ProductModel.objects.get(pk=a.pk).normalized_name, 'tableta z')
        self.assertEqual(ProductModel.objects.get(pk=self.model.pk).normalized_name, 'telefono x')

    def test_the_check_command_lists_collisions_and_blank_rows_read_only(self):
        from django.core.management import call_command

        other = _p3_company('a1b-cmd', 'Comando')
        a, b = ProductModel.objects.bulk_create([
            ProductModel(company=other, name='Tableta Z', slug='tz-1'),
            ProductModel(company=other, name='TABLETA Z', slug='tz-2')])
        out = io.StringIO()
        call_command('check_model_names', stdout=out)
        text = out.getvalue()
        self.assertIn(f'empresa {other.pk}', text)
        self.assertIn(f'{a.pk}', text)
        self.assertIn('TABLETA Z', text)
        self.assertIn('2 modelo(s) sin nombre normalizado', text)
        self.assertEqual(ProductModel.objects.filter(pk__in=[a.pk, b.pk], normalized_name='').count(), 2)


# ---------------------------------------------------------------------------
# LOW 3 — un choque de nombre no es un choque de rol
# ---------------------------------------------------------------------------

class AttributeCollisionTest(RolesShop, TestCase):
    def setUp(self):
        self.build('a1b-atrib')

    def test_a_name_collision_the_database_catches_is_duplicate_on_name(self):
        other = device_catalog.create_attribute(company=self.company, name='Acabado')
        # The race: the pre-check saw nothing, the database refused the name.
        with mock.patch.object(device_catalog, '_name_free'), self.assertRaises(device_catalog.CatalogError) as c:
            device_catalog.update_attribute(other, name='Capacidad', actor=self.staff)
        self.assertEqual((c.exception.code, c.exception.field), ('duplicate', 'name'))
        self.assertIn('Capacidad', str(c.exception))

    def test_a_role_collision_the_database_catches_is_role_taken_on_role(self):
        other = device_catalog.create_attribute(company=self.company, name='Acabado')
        with mock.patch.object(device_catalog, '_role_free'), self.assertRaises(device_catalog.CatalogError) as c:
            device_catalog.update_attribute(other, role='sim', actor=self.staff)
        self.assertEqual((c.exception.code, c.exception.field), ('role_taken', 'role'))
        self.assertIn('Tipo de SIM', str(c.exception))

    def test_a_refused_update_leaves_the_attribute_and_the_audit_as_they_were(self):
        from store.models import AdminAuditLog

        other = device_catalog.create_attribute(company=self.company, name='Acabado')
        before = AdminAuditLog.objects.filter(company=self.company, action='variant_attribute_updated').count()
        with self.assertRaises(device_catalog.CatalogError):
            device_catalog.update_attribute(other, name='capacidad', actor=self.staff)
        other.refresh_from_db()
        self.assertEqual((other.name, other.role), ('Acabado', 'other'))
        self.assertEqual(AdminAuditLog.objects.filter(company=self.company, action='variant_attribute_updated')
                         .count(), before)
        device_catalog.update_attribute(other, name='Terminación', actor=self.staff)
        log = AdminAuditLog.objects.filter(company=self.company, action='variant_attribute_updated').latest('pk')
        self.assertEqual(log.company_id, self.company.pk)


# ---------------------------------------------------------------------------
# LOW 1 — lo histórico se encuentra aunque la paleta ya no lo admita
# ---------------------------------------------------------------------------

class ResolveHistoricalTest(RolesShop, TestCase):
    def setUp(self):
        self.build('a1b-hist')
        # Negro leaves the palette: new128 and new256 (both Negro) are history now.
        self.palette(self.gb128, self.gb256, self.blue, self.esim, self.chip, self.lla, self.ja)

    def test_a_partial_description_naming_an_option_a_variant_holds_is_a_spec(self):
        spec = device_catalog.resolve_variant_spec(self.company, model_name='Teléfono X', labels=['Negro'])
        self.assertEqual(spec['option_ids'], [self.black.pk])
        spec = device_catalog.resolve_variant_spec(self.company, model_name='Teléfono X', labels=['256 GB', 'Negro'])
        self.assertEqual(device_catalog.find_variant(self.company, spec).pk, self.new256.pk)

    def test_a_new_combination_outside_the_palette_is_still_refused(self):
        with self.assertRaises(device_catalog.CatalogError) as caught:
            device_catalog.resolve_variant_spec(self.company, model_name='Teléfono X', labels=['Rojo'])
        self.assertEqual(caught.exception.code, 'option_not_in_palette')
        with self.assertRaises(device_catalog.CatalogError) as caught:
            device_catalog.resolve_variant_spec(self.company, model_name='Teléfono X', labels=['128 GB', 'Rojo'])
        self.assertEqual(caught.exception.code, 'option_not_in_palette')
        # Negro exists in the NEW line only: in the used line it is a new, out-of-palette combination.
        with self.assertRaises(device_catalog.CatalogError) as caught:
            device_catalog.resolve_variant_spec(self.company, model_name='Teléfono X', line_text='seminuevo',
                                                labels=['256 GB', 'Negro'])
        self.assertEqual(caught.exception.code, 'option_not_in_palette')

    def test_the_text_recogniser_offers_the_historical_variants(self):
        from store.text_preview_match import match_device

        found = match_device(self.company, {'model': 'Teléfono X', 'capacity': 0, 'colour': 'Negro', 'line': 'new'})
        self.assertEqual(found['status'], 'ambiguous', found)
        self.assertEqual(sorted(o['product_id'] for o in found['options']), sorted([self.new128.pk, self.new256.pk]))

    def test_the_supplier_list_finds_a_historical_variant_by_its_options(self):
        product, problem = supplier_offer_import._by_model(
            self.company, {'model': 'Teléfono X', 'capacity': '128 GB', 'colour': 'Negro', 'line': 'Nuevo'})
        self.assertEqual((product.pk if product else None, problem), (self.new128.pk, None))


# ---------------------------------------------------------------------------
# Cargas de productos: SIM y Región por rol, mensajes por fila, plantilla antigua
# ---------------------------------------------------------------------------

OLD_HEADERS = ['Código', 'Nombre', 'Precio de venta', 'Modelo', 'Capacidad', 'Color', 'Línea']
NEW_HEADERS = ['Código', 'Nombre', 'Precio de venta', 'Modelo', 'Capacidad', 'Color', 'SIM', 'Región', 'Línea']


class ProductImportRolesTest(RolesShop, TestCase):
    def setUp(self):
        self.build('a1b-carga')

    def test_sim_and_region_are_normal_columns_of_the_product_template(self):
        self.assertFalse(import_formats.PRODUCT_FIELDS['sim'].get('history'))
        self.assertFalse(import_formats.PRODUCT_FIELDS['region'].get('history'))
        self.assertEqual(import_formats.VARIANT_HEADERS['sim'], ('sim', 'tipo de sim'))
        self.assertEqual(import_formats.VARIANT_HEADERS['region'], ('region',))
        headers = import_exports.PRODUCT_TEMPLATE_HEADERS
        colour = headers.index('Color')
        self.assertEqual(headers[colour:colour + 4], ['Color', 'SIM', 'Región', 'Línea'])
        sheet = openpyxl.load_workbook(io.BytesIO(import_exports.product_template_bytes(self.company))).active
        self.assertEqual([c.value for c in sheet[1]], headers)
        # The platform preset maps them too.
        preset = next(p for p in import_formats.PRESETS if p['key'] == 'products_platform')
        self.assertEqual((preset['mapping']['sim'], preset['mapping']['region']),
                         (import_formats.VARIANT_HEADERS['sim'], import_formats.VARIANT_HEADERS['region']))

    def test_an_old_template_without_the_columns_loads_as_before(self):
        before = self.counts()
        job = self.preview(OLD_HEADERS, [
            ['', 'Teléfono X 128 GB Negro', '3000', 'Teléfono X', '128 GB', 'Negro', 'Nuevo'],
            ['FUNDA-1', 'Funda', '50', '', '', '', ''],
        ])
        rows = {r.row_number: r for r in job.rows.all()}
        self.assertEqual(rows[2].errors, [])
        self.assertEqual(rows[2].normalized_data['variant']['option_ids'], sorted([self.gb128.pk, self.black.pk]))
        self.assertEqual(rows[3].action, 'create')
        self.assertNotIn('variant', rows[3].normalized_data)
        self.assertEqual(self.counts(), before)

    def test_a_new_complete_variant_is_created_from_the_four_columns(self):
        from store import import_services

        before = self.counts()
        job = self.preview(NEW_HEADERS, [
            ['TX-128-AZ-CH-JA', 'Teléfono X 128 GB Azul Chip J/A', '3100', 'Teléfono X', '128 GB', 'Azul', 'Chip',
             'J/A', 'Nuevo'],
        ])
        row = job.rows.get()
        self.assertEqual(row.errors, [])
        self.assertEqual(row.normalized_data['variant']['option_ids'],
                         sorted([self.gb128.pk, self.blue.pk, self.chip.pk, self.ja.pk]))
        import_services.apply_products(job=job, actor=self.staff)
        made = Product.objects.get(company=self.company, name='Teléfono X 128 GB Azul Chip J/A')
        self.assertEqual(made.product_model_id, self.model.pk)
        self.assertEqual(made.variant_key, device_catalog.variant_key([self.gb128.pk, self.blue.pk, self.chip.pk,
                                                                       self.ja.pk]))
        self.assertEqual(self.counts(), before)

    def test_an_incomplete_new_variant_names_the_missing_roles(self):
        job = self.preview(NEW_HEADERS, [
            ['TX-128-AZ', 'Teléfono X 128 GB Azul', '3100', 'Teléfono X', '128 GB', 'Azul', '', '', 'Nuevo'],
        ])
        row = job.rows.get()
        self.assertEqual(row.action, 'error')
        self.assertEqual(len(row.errors), 1, row.errors)
        self.assertIn('«Tipo de SIM» (SIM)', row.errors[0])
        self.assertIn('«Región» (Región)', row.errors[0])
        self.assertIn('Teléfono X', row.errors[0])

    def test_an_option_outside_the_palette_names_the_model_the_option_and_the_attribute(self):
        job = self.preview(NEW_HEADERS, [
            ['TX-128-RO', 'Teléfono X 128 GB Rojo', '3100', 'Teléfono X', '128 GB', 'Rojo', 'eSIM', 'LL/A', 'Nuevo'],
        ])
        row = job.rows.get()
        self.assertEqual(row.action, 'error')
        self.assertTrue(any('«Rojo» (Color)' in e and '«Teléfono X»' in e for e in row.errors), row.errors)

    def test_a_variant_that_already_exists_is_found_and_a_second_product_naming_it_is_refused(self):
        job = self.preview(NEW_HEADERS, [
            ['', 'Teléfono X 256 GB Azul eSIM LL/A', '3700', 'Teléfono X', '256 GB', 'Azul', 'eSIM',
             'LL/A', 'Nuevo'],
            ['OTRO', 'Otro con las mismas opciones', '3700', 'Teléfono X', '256 GB', 'Azul', 'eSIM', 'LL/A', 'Nuevo'],
        ])
        rows = {r.row_number: r for r in job.rows.all()}
        self.assertEqual(rows[2].normalized_data['product_id'], self.full.pk)
        self.assertFalse(any('Tipo de SIM' in e for e in rows[2].errors), rows[2].errors)
        self.assertTrue(any('ya es esa variante' in e and self.full.name in e for e in rows[3].errors), rows[3].errors)

    def test_an_existing_variant_outside_todays_palette_is_still_found_by_the_old_template(self):
        self.palette(self.gb128, self.gb256, self.blue, self.esim, self.chip, self.lla, self.ja)   # Negro out
        job = self.preview(OLD_HEADERS, [
            ['', 'Teléfono X 128 GB Negro', '3000', 'Teléfono X', '128 GB', 'Negro', 'Nuevo'],
        ])
        row = job.rows.get()
        self.assertEqual(row.errors, [])
        self.assertEqual(row.normalized_data['product_id'], self.new128.pk)

    def test_the_preview_reads_the_palette_once_per_model(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def rows(n):
            return [[f'TX-{i}', f'Teléfono X nuevo {i}', '3100', 'Teléfono X', '128 GB', 'Azul', 'Chip', 'J/A', 'Nuevo']
                    for i in range(n)]

        with CaptureQueriesContext(connection) as one:
            self.preview(NEW_HEADERS, rows(1))
        with CaptureQueriesContext(connection) as fifty:
            self.preview(NEW_HEADERS, rows(50))
        # The palette (`store_modeloption`) is read ONCE for the model, not once per row; and the rows of the
        # fifty-row pass cost no more each than the single row (nothing grows with the file).
        self.assertEqual(sum('store_modeloption' in q['sql'] for q in fifty.captured_queries), 1)
        self.assertEqual(sum('store_modeloption' in q['sql'] for q in one.captured_queries), 1)
        self.assertLessEqual(len(fifty) - len(one), 49 * 9, (len(one), len(fifty)))


# ---------------------------------------------------------------------------
# Exportación por rol y recarga sin cambios
# ---------------------------------------------------------------------------

class ExportRolesTest(RolesShop, TestCase):
    def setUp(self):
        self.build('a1b-export')

    def cells(self, product):
        product = Product.objects.select_related('product_model').get(pk=product.pk)
        return inventory_exports._VariantCells(self.company)(product)

    def test_the_cells_are_written_by_role(self):
        self.assertEqual(self.cells(self.full), {
            'Modelo': 'Teléfono X', 'Capacidad': '256 GB', 'Color': 'Azul', 'SIM': 'eSIM', 'Región': 'LL/A',
            'Línea': 'Nuevo'})
        self.assertEqual(self.cells(self.new128), {
            'Modelo': 'Teléfono X', 'Capacidad': '128 GB', 'Color': 'Negro', 'SIM': None, 'Región': None,
            'Línea': 'Nuevo'})

    def test_without_roles_the_kind_decides_as_before(self):
        VariantAttribute.objects.filter(company=self.company).update(role='other')
        self.assertEqual(self.cells(self.new128), {
            'Modelo': 'Teléfono X', 'Capacidad': '128 GB', 'Color': 'Negro', 'SIM': None, 'Región': None,
            'Línea': 'Nuevo'})
        # Two text attributes without roles cannot be told apart: the row is left empty, as before.
        self.assertEqual(self.cells(self.full), {})

    def test_the_devices_template_reads_the_four_columns_back_too(self):
        from store import unit_import_services

        colour = unit_import_services.HEADERS.index('Color')
        self.assertEqual(unit_import_services.HEADERS[colour:colour + 4], ['Color', 'SIM', 'Región', 'Línea'])
        self.assertEqual(unit_import_services._ALIASES['sim'], ('sim', 'tipo de sim'))

    def test_an_exported_catalogue_with_the_four_roles_reloads_without_a_change(self):
        from store import import_services

        self.receive(self.full, 1, cost='2500.00')
        before = self.counts()
        links = sorted(Product.objects.filter(company=self.company)
                       .values_list('pk', 'product_model_id', 'condition_line', 'variant_key'))
        content, _n = inventory_exports.products_workbook(
            self.company, inventory_exports.ExportFilters(), inventory_exports.MODE_REIMPORT)
        sheet = openpyxl.load_workbook(io.BytesIO(content)).active
        headers = [c.value for c in sheet[1]]
        self.assertEqual(headers[:len(import_exports.PRODUCT_TEMPLATE_HEADERS)], import_exports.PRODUCT_TEMPLATE_HEADERS)
        records = {row[headers.index('Nombre')]: dict(zip(headers, row))
                   for row in sheet.iter_rows(min_row=2, values_only=True)}
        self.assertEqual([records[self.full.name][h] for h in ('Modelo', 'Capacidad', 'Color', 'SIM', 'Región', 'Línea')],
                         ['Teléfono X', '256 GB', 'Azul', 'eSIM', 'LL/A', 'Nuevo'])
        from django.core.files.uploadedfile import SimpleUploadedFile

        job = import_services.preview_products(
            company=self.company, actor=self.staff, filename='recarga.xlsx',
            upload=SimpleUploadedFile('recarga.xlsx', content, content_type=
                                      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'))
        self.assertEqual([list(r.errors) for r in job.rows.all()], [[]] * job.rows.count())
        self.assertEqual({r.normalized_data['product_id'] for r in job.rows.all()},
                         set(Product.objects.filter(company=self.company).values_list('pk', flat=True)))
        import_services.apply_products(job=job, actor=self.staff)
        self.assertEqual(sorted(Product.objects.filter(company=self.company)
                                .values_list('pk', 'product_model_id', 'condition_line', 'variant_key')), links)
        self.assertEqual(self.counts(), before)


# ---------------------------------------------------------------------------
# Ofertas de proveedor: SIM y Región opcionales
# ---------------------------------------------------------------------------

class SupplierOfferRolesTest(RolesShop, TestCase):
    def setUp(self):
        self.build('a1b-ofertas')
        from store.models import Supplier

        self.supplier = Supplier.objects.create(company=self.company, name='Mayorista Norte')

    def test_the_template_and_the_export_carry_sim_and_region_after_colour(self):
        colour = supplier_offer_import.HEADERS.index('Color')
        self.assertEqual(supplier_offer_import.HEADERS[colour:colour + 4], ['Color', 'SIM', 'Región', 'Línea'])
        self.assertEqual(supplier_offer_import._ALIASES['sim'], ('sim', 'tipo de sim'))
        self.assertEqual(supplier_offer_import._ALIASES['region'], ('region',))
        content = supplier_offer_import.export_bytes(company=self.company, include_cost=True)
        headers = [c.value for c in openpyxl.load_workbook(io.BytesIO(content)).active[1]]
        self.assertIn('SIM', headers)
        self.assertIn('Región', headers)

    def test_by_model_passes_the_four_labels(self):
        product, problem = supplier_offer_import._by_model(self.company, {
            'model': 'Teléfono X', 'capacity': '256 GB', 'colour': 'Azul', 'sim': 'eSIM', 'region': 'LL/A',
            'line': 'Nuevo'})
        self.assertEqual((product.pk, problem), (self.full.pk, None))
        # Without SIM and region the description fits several variants of 256 GB Azul? Only one here: found
        # by narrowing, never invented.
        product, problem = supplier_offer_import._by_model(self.company, {
            'model': 'Teléfono X', 'capacity': '256 GB', 'colour': 'Azul', 'line': 'Nuevo'})
        self.assertEqual((product, 'No existe esa variante' in (problem or '')), (None, True))

    def test_a_file_with_the_new_columns_and_one_without_them_both_preview(self):
        from datetime import date

        start, end = date.today(), date.today()
        base = ['Mayorista Norte', '', 'Teléfono X', '', '', '', '', 'Nuevo', '', '3600', 5, start.isoformat(),
                end.isoformat(), 3]
        headers = ['Proveedor', 'Código/SKU del producto', 'Modelo', 'Capacidad', 'Color', 'SIM', 'Región', 'Línea',
                   'Costo pactado', 'Precio comercial', 'Cantidad confirmada', 'Vigente desde', 'Vigente hasta',
                   'Plazo (días)']
        row = list(base)
        row[3:7] = ['256 GB', 'Azul', 'eSIM', 'LL/A']
        job = supplier_offer_import.preview(
            company=self.company, actor=self.staff, filename='o.xlsx', message_date=start,
            upload=upload(headers, [row], title='Ofertas de proveedor'))
        staged = job.rows.get()
        self.assertEqual(staged.errors, [], staged.errors)
        self.assertEqual(staged.normalized_data['product_id'], self.full.pk)
        old_headers = [h for h in headers if h not in ('SIM', 'Región')]
        old_row = ['Mayorista Norte', '', 'Teléfono X', '128 GB', 'Negro', 'Nuevo', '', '3000', 5,
                   start.isoformat(), end.isoformat(), 3]
        job = supplier_offer_import.preview(
            company=self.company, actor=self.staff, filename='o.xlsx', message_date=start,
            upload=upload(old_headers, [old_row], title='Ofertas de proveedor'))
        staged = job.rows.get()
        self.assertEqual(staged.errors, [], staged.errors)
        self.assertEqual(staged.normalized_data['product_id'], self.new128.pk)


# ---------------------------------------------------------------------------
# Capacidad por rol en el reconocedor y en las propuestas
# ---------------------------------------------------------------------------

class CapacityByRoleTest(RolesShop, TestCase):
    def setUp(self):
        self.build('a1b-cap')

    def test_the_capacity_attribute_is_found_by_role_whatever_its_name(self):
        from store.text_preview_match import _capacity_label

        VariantAttribute.objects.filter(pk=self.capacity.pk).update(name='Memoria interna')
        self.assertEqual(_capacity_label(self.company, 128), '128 GB')
        # Without roles, the name decides as before.
        VariantAttribute.objects.filter(pk=self.capacity.pk).update(role='other', name='Almacenamiento')
        self.assertEqual(_capacity_label(self.company, 256), '256 GB')
        VariantAttribute.objects.filter(pk=self.capacity.pk).update(name='Memoria interna')
        self.assertIsNone(_capacity_label(self.company, 256))

    def test_the_catalogue_proposal_reads_the_capacity_by_role_first(self):
        from store.catalog_proposal import _Plan

        VariantAttribute.objects.filter(pk=self.capacity.pk).update(name='Memoria interna')
        self.assertEqual(_Plan(self.company, [])._capacity_attribute.pk, self.capacity.pk)
        VariantAttribute.objects.filter(pk=self.capacity.pk).update(role='other', name='Almacenamiento')
        self.assertEqual(_Plan(self.company, [])._capacity_attribute.pk, self.capacity.pk)


# ---------------------------------------------------------------------------
# Historial, tenencia, contrato público
# ---------------------------------------------------------------------------

class HistoryAndTenancyTest(RolesShop, TestCase):
    def setUp(self):
        self.build('a1b-tenencia')
        order = Order.objects.create(
            company=self.company, fulfillment_branch=self.branch, customer_email='c@example.com',
            total=self.full.price, status=Order.Status.PAID, paid=True, paid_at=timezone.now(),
            fulfillment_status=Order.FulfillmentStatus.DELIVERED)
        OrderItem.objects.create(order=order, product=self.full, quantity=1, price=self.full.price)
        self.receive(self.full, 1, cost='2500.00')

    def test_a_sold_variant_with_sim_and_region_survives_a_palette_change_and_a_reload(self):
        from store import import_services

        before = self.counts()
        self.palette(self.gb128, self.gb256, self.black, self.blue, self.chip, self.ja)   # eSIM and LL/A out
        job = import_services.preview_products(
            company=self.company, actor=self.staff, filename='p.xlsx', upload=upload(NEW_HEADERS, [
                ['', self.full.name, '3700', 'Teléfono X', '256 GB', 'Azul', 'eSIM', 'LL/A', 'Nuevo'],
            ]))
        row = job.rows.get()
        self.assertEqual(row.errors, [])
        self.assertEqual(row.normalized_data['product_id'], self.full.pk)
        import_services.apply_products(job=job, actor=self.staff)
        self.full.refresh_from_db()
        self.assertEqual(self.full.variant_key, device_catalog.variant_key([self.gb256.pk, self.blue.pk, self.esim.pk,
                                                                            self.lla.pk]))
        self.assertEqual(OrderItem.objects.filter(product=self.full).count(), 1)
        self.assertEqual(self.counts(), before)

    def test_a_variant_in_use_cannot_leave_its_model_and_says_so(self):
        res = self.api.put(f'/api/admin/products/{self.full.pk}/variant/{self.q}', {'model_id': None}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'variant_in_use'), res.content)

    def test_another_company_sees_nothing_of_this_one(self):
        other = _p3_company('a1b-ajena', 'Ajena')
        staff, _ = _p2d_member(other, 'a1b_ajena', ['company.view', 'products.view', 'products.manage'])
        api = _client(staff)
        self.assertEqual(api.get(f'/api/admin/catalog/models/{self.model.pk}/?company={other.pk}').status_code, 404)
        res = api.patch(f'/api/admin/catalog/models/{self.model.pk}/?company={other.pk}', {'name': 'X'}, format='json')
        self.assertEqual(res.status_code, 404)
        with self.assertRaises(device_catalog.CatalogError) as caught:
            device_catalog.resolve_variant_spec(other, model_name='Teléfono X', labels=['256 GB'])
        self.assertEqual(caught.exception.field, 'model')


@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class PublicContractWithRolesTest(RolesShop, TestCase):
    """A model whose variants carry SIM and region answers the same public shape, and nothing of A1b leaks."""

    maxDiff = None

    def setUp(self):
        self.build('a1b-publico')
        self.receive(self.full, 1, cost='2500.00')
        self.public = _client(host=self.host)

    def detail(self):
        body = self.public.get(f'/api/catalog/models/{self.model.slug}/').json()
        body.pop('as_of')
        return body

    def test_the_payload_is_the_known_shape_with_the_four_attributes(self):
        body = self.detail()
        self.assertEqual([a['name'] for a in body['attributes']], ['Capacidad', 'Color', 'Tipo de SIM', 'Región'])
        self.assertEqual(sorted(body['attributes'][2]), ['id', 'kind', 'name', 'options', 'sort_order'])
        full = next(v for v in body['variants'] if v['product_id'] == self.full.pk)
        self.assertEqual(sorted(full), ['availability', 'available', 'image_url', 'images', 'name', 'on_order',
                                        'options', 'price', 'product_id', 'sells_by_unit', 'slug'])
        self.assertEqual(full['options'], {str(self.capacity.pk): self.gb256.pk, str(self.colour.pk): self.blue.pk,
                                           str(self.sim.pk): self.esim.pk, str(self.region.pk): self.lla.pk})
        text = str(body)
        for word in ('role', 'palette', 'normalized'):
            self.assertNotIn(word, text)
        # Renaming the model (another spelling) and changing the palette: the same answer, name aside.
        device_catalog.update_model(self.model, name='Teléfono  X', actor=self.staff)
        self.palette(self.gb256, self.blue)
        self.assertEqual(self.detail(), body)
