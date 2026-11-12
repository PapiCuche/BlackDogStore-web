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
        # Past `save()` (which refuses first): a keyed row written any other way is refused by the database.
        with self.assertRaises(IntegrityError):
            ProductModel.objects.bulk_create([ProductModel(company=self.company, name='TELÉFONO X', slug='otro',
                                                           normalized_name='telefono x')])

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
        from django.core.management import CommandError

        out = io.StringIO()
        with self.assertRaises(CommandError) as stopped:
            call_command('check_model_names', stdout=out)
        self.assertEqual(stopped.exception.returncode, 2)
        text = out.getvalue() + str(stopped.exception)
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
        self.assertIn('lleva «Capacidad» (Capacidad), «Color» (Color), «Tipo de SIM» (SIM), «Región» (Región)',
                      row.errors[0])
        self.assertIn('le faltan «Tipo de SIM» (SIM), «Región» (Región)', row.errors[0])
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


class ReviewFindingsTest(RolesShop, TestCase):
    """What the independent review of A1b-1 found, held by a test each."""

    def setUp(self):
        self.build('a1b-revision')

    def test_a_new_row_whose_options_a_historical_variant_holds_is_still_held_to_the_palette(self):
        # Review 1: Azul leaves the palette; `full` (256 GB Azul eSIM LL/A) is history. A NEW product naming
        # «256 GB / Azul / Chip / LL/A» is not that variant: the preview refuses it (never the apply).
        self.palette(self.gb128, self.gb256, self.black, self.esim, self.chip, self.lla, self.ja)
        job = self.preview(NEW_HEADERS, [
            ['TX-NUEVO', 'Teléfono X 256 GB Azul Chip LL/A', '3100', 'Teléfono X', '256 GB', 'Azul', 'Chip', 'LL/A',
             'Nuevo'],
            ['', self.full.name, '3700', 'Teléfono X', '256 GB', 'Azul', 'eSIM', 'LL/A', 'Nuevo'],
        ])
        rows = {r.row_number: r for r in job.rows.all()}
        self.assertEqual(rows[2].action, 'error')
        self.assertTrue(any('«Azul» (Color)' in e for e in rows[2].errors), rows[2].errors)
        self.assertEqual((rows[3].errors, rows[3].normalized_data['product_id']), ([], self.full.pk))
        # The readers keep offering the historical variant for the same partial description.
        spec = device_catalog.resolve_variant_spec(self.company, model_name='Teléfono X', labels=['Azul'])
        self.assertEqual(spec['option_ids'], [self.blue.pk])
        with self.assertRaises(device_catalog.CatalogError):
            device_catalog.resolve_variant_spec(self.company, model_name='Teléfono X', labels=['Azul'], strict=True)

    def test_a_row_the_previous_release_wrote_without_a_key_still_counts_for_the_rule(self):
        # Review 6: a blank-key row (older code) is folded by the service; the new name is refused and the old
        # row can still be saved (its key is filled) without tripping the database.
        [old] = ProductModel.objects.bulk_create([ProductModel(company=self.company, name='Tableta Z', slug='tz')])
        with self.assertRaises(device_catalog.CatalogError) as caught:
            device_catalog.create_model(company=self.company, name='tableta  z')
        self.assertEqual(caught.exception.code, 'duplicate')
        device_catalog.update_model(old, sort_order=3, actor=self.staff)
        old.refresh_from_db()
        self.assertEqual((old.normalized_name, old.sort_order), ('tableta z', 3))
        # And the readers find it by its name while the key is blank.
        [again] = ProductModel.objects.bulk_create([ProductModel(company=self.company, name='Tableta W', slug='tw')])
        self.assertEqual(device_catalog.find_model(self.company, 'tableta w').pk, again.pk)

    def test_the_readers_find_a_model_by_the_same_rule_the_writer_uses(self):
        # Review 7: «Telefono X» (no accent) names «Teléfono X» in a file, in the registration flow and in the
        # export's own count of names.
        spec = device_catalog.resolve_variant_spec(self.company, model_name='TELEFONO   x', labels=['128 GB', 'Negro'])
        self.assertEqual(device_catalog.find_variant(self.company, spec).pk, self.new128.pk)
        from store import registration_services

        model, created = registration_services._model(
            self.company, {'name': 'telefono x'}, category_id=None, show_online=True,
            first_variant_problems=lambda: [], actor=self.staff, request=None)
        self.assertEqual((model.pk, created), (self.model.pk, False))
        self.assertEqual(ProductModel.objects.filter(company=self.company).count(), 1)

    def test_a_name_that_folds_past_160_characters_is_refused_before_the_database(self):
        # Review 9: NFKD may lengthen a name; the folded key must fit the column.
        with self.assertRaises(device_catalog.CatalogError) as caught:
            device_catalog.create_model(company=self.company, name='ﬁ' * 100)      # 100 ligatures → 200 letters
        self.assertEqual(caught.exception.field, 'name')

    def test_a_pre_a1b_file_with_a_free_text_sim_column_still_loads_in_a_company_without_that_role(self):
        # Review 4: the company has no attribute with the SIM role: «SIM» is a note, not an option.
        VariantAttribute.objects.filter(pk__in=[self.sim.pk, self.region.pk]).update(role='other')
        headers = ['Código', 'Nombre', 'Precio de venta', 'Modelo', 'Capacidad', 'Color', 'SIM', 'Línea']
        job = self.preview(headers, [['', 'Teléfono X 128 GB Negro', '3000', 'Teléfono X', '128 GB', 'Negro',
                                      'bandeja doble', 'Nuevo']])
        row = job.rows.get()
        self.assertEqual((row.errors, row.normalized_data['product_id']), ([], self.new128.pk))
        self.assertTrue(any('«SIM»' in n and 'no se leen' in n for n in job.summary['format_notes']), job.summary)

    def test_the_devices_template_keeps_its_drop_down_and_number_formats_under_the_right_headers(self):
        # Review 3: the template's letters follow the headers, not the other way round.
        from store import unit_import_services

        sheet = openpyxl.load_workbook(io.BytesIO(unit_import_services.template_bytes(['Centro']))).active
        headers = [c.value for c in sheet[1]]
        from openpyxl.utils import get_column_letter

        line = get_column_letter(headers.index('Línea') + 1)
        refs = ' '.join(str(v.sqref) for v in sheet.data_validations.dataValidation)
        self.assertIn(line, refs)
        price = get_column_letter(headers.index('Precio de venta') + 1)
        self.assertEqual(sheet.column_dimensions[price].number_format, '0.00')
        self.assertEqual(sheet.column_dimensions[line].number_format, 'General')

    def test_an_unassigned_text_attribute_is_not_exported_as_the_capacity_once_the_company_named_one(self):
        # Review 12.
        extra = device_catalog.create_attribute(company=self.company, name='Conectividad')
        fiveg = device_catalog.create_option(company=self.company, attribute_id=extra.pk, label='5G')
        self.palette(self.gb128, self.gb256, self.black, self.blue, self.esim, self.chip, self.lla, self.ja, fiveg)
        product = _prod(self.company, 'Teléfono X 5G', 'tx-5g', price='10.00', inventory=0)
        device_catalog.set_variant(product, model_id=self.model.pk,
                                   option_ids=[self.gb128.pk, self.black.pk, self.esim.pk, self.lla.pk, fiveg.pk])
        product = Product.objects.select_related('product_model').get(pk=product.pk)
        self.assertEqual(inventory_exports._VariantCells(self.company)(product), {})


# ---------------------------------------------------------------------------
# Ventana «código anterior sobre 0166»: claves vacías y colisiones tardías
# ---------------------------------------------------------------------------

def _old_code_insert(company, name, slug):
    """What the previous release does: an INSERT that does not name `normalized_name` (the database writes '')."""
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            'INSERT INTO store_productmodel (company_id, name, slug, description, image_url, is_published_online, '
            'sort_order, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, now(), now()) RETURNING id',
            [company.pk, name, slug, '', '', True, 0])
        return ProductModel.objects.get(pk=cursor.fetchone()[0])


def _old_code_rename(model, name):
    """What the previous release's `update_model` does: a full UPDATE that never names `normalized_name`."""
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute('UPDATE store_productmodel SET name = %s WHERE id = %s', [name, model.pk])
    return ProductModel.objects.get(pk=model.pk)


def _check(*args):
    """`check_model_names`: (exit code, output + the error text)."""
    from django.core.management import CommandError, call_command

    out = io.StringIO()
    try:
        call_command('check_model_names', *args, stdout=out)
    except CommandError as stopped:
        return stopped.returncode, out.getvalue() + str(stopped)
    return 0, out.getvalue()


class OldCodeWindowTest(RolesShop, TestCase):
    def setUp(self):
        self.build('a1b-ventana')

    def test_a_model_the_old_code_wrote_is_read_and_written_by_the_new_code(self):
        old = _old_code_insert(self.company, 'Tableta Vieja', 'tableta-vieja')
        self.assertEqual(old.normalized_name, '')
        self.assertEqual(device_catalog.find_model(self.company, 'TABLETA VIEJA').pk, old.pk)
        spec = device_catalog.resolve_variant_spec(self.company, model_name='tableta vieja')
        self.assertEqual(spec['model_id'], old.pk)
        res = self.api.get(f'/api/admin/catalog/models/{old.pk}/{self.q}')
        self.assertEqual(res.status_code, 200)
        device_catalog.update_model(old, description='Sin cambios de nombre', actor=self.staff)
        old.refresh_from_db()
        self.assertEqual((old.name, old.normalized_name, old.slug), ('Tableta Vieja', 'tableta vieja', 'tableta-vieja'))

    def test_two_old_rows_that_fold_equal_are_detected_and_neither_is_saved_silently(self):
        a = _old_code_insert(self.company, 'Tableta Z', 'tz-a')
        b = _old_code_insert(self.company, 'TABLETA  Z', 'tz-b')
        code, text = _check()
        self.assertEqual(code, 2, text)
        self.assertIn(f'empresa {self.company.pk}', text)
        self.assertIn(f'modelo {a.pk} «Tableta Z»', text)
        self.assertIn(f'modelo {b.pk} «TABLETA  Z»', text)
        # The first save of EITHER row is refused, naming the other; nothing is renamed, merged or deleted.
        for row, other in ((a, b), (b, a)):
            with self.subTest(row.name), self.assertRaises(device_catalog.CatalogError) as caught:
                row.sort_order = 5
                row.save()
            self.assertEqual((caught.exception.code, caught.exception.field), ('duplicate', 'name'))
            self.assertIn(f'«{other.name}»', str(caught.exception))
            with self.assertRaises(device_catalog.CatalogError):
                device_catalog.update_model(row, sort_order=5, actor=self.staff)
        self.assertEqual(sorted(ProductModel.objects.filter(pk__in=[a.pk, b.pk]).values_list('name', 'normalized_name',
                                                                                           'sort_order')),
                         [('TABLETA  Z', '', 0), ('Tableta Z', '', 0)])
        # The API maps it to 400 `duplicate` too (a PATCH that does not touch the name).
        res = self.api.patch(f'/api/admin/catalog/models/{a.pk}/{self.q}', {'sort_order': 9}, format='json')
        self.assertEqual((res.status_code, res.json()['code'], res.json()['field']), (400, 'duplicate', 'name'))
        # A person resolves it by renaming one; then both save.
        device_catalog.update_model(b, name='Tableta Z Plus', actor=self.staff)
        device_catalog.update_model(a, sort_order=5, actor=self.staff)
        self.assertEqual(_check()[0], 0)

    def test_the_check_exits_two_with_blank_keys_and_zero_when_clean(self):
        self.assertEqual(_check()[0], 0)
        old = _old_code_insert(self.company, 'Tableta Vieja', 'tableta-vieja')
        code, text = _check()
        self.assertEqual(code, 2)
        self.assertIn('1 modelo(s) sin nombre normalizado', text)
        self.assertIn(f'modelo {old.pk} «Tableta Vieja»', text)

    def test_fill_writes_the_blank_keys_and_stops_on_a_collision_writing_nothing(self):
        old = _old_code_insert(self.company, 'Tableta Vieja', 'tableta-vieja')
        code, text = _check('--fill')
        self.assertEqual(code, 0, text)
        self.assertIn('1 fila(s) rellenadas', text)
        self.assertEqual(ProductModel.objects.get(pk=old.pk).normalized_name, 'tableta vieja')
        a = _old_code_insert(self.company, 'Tableta Z', 'tz-a')
        b = _old_code_insert(self.company, 'tableta z', 'tz-b')
        c = _old_code_insert(self.company, 'Tableta Y', 'ty')
        code, text = _check('--fill')
        self.assertEqual(code, 2)
        self.assertIn('no fusiona, renombra ni borra nada', text)
        self.assertIn(f'modelo {a.pk} «Tableta Z»', text)
        self.assertIn(f'modelo {b.pk} «tableta z»', text)
        # Nothing written: not even the row that does not collide.
        self.assertEqual(set(ProductModel.objects.filter(pk__in=[a.pk, b.pk, c.pk]).values_list('normalized_name',
                                                                                                flat=True)), {''})
        self.assertEqual(ProductModel.objects.get(pk=c.pk).name, 'Tableta Y')

    def test_a_rename_by_the_old_code_leaves_a_stale_key_that_is_detected_repaired_and_never_followed(self):
        # Review 2/1: the previous release renames «Tableta A» → «Tableta B» with a full UPDATE that keeps the
        # key «tableta a». No reader may follow the stale key, the gate must see it, `--fill` must repair it,
        # and a NEW «Tableta A» must be refused naming the row that still holds the key, not the slug.
        row = device_catalog.create_model(company=self.company, name='Tableta A', actor=self.staff)
        row = _old_code_rename(row, 'Tableta B')
        self.assertEqual(row.normalized_name, 'tableta a')
        self.assertIsNone(device_catalog.find_model(self.company, 'Tableta A'))
        self.assertEqual(device_catalog.find_model(self.company, 'tableta  b').pk, row.pk)
        code, text = _check()
        self.assertEqual(code, 2)
        self.assertIn('1 modelo(s) con la clave desfasada', text)
        self.assertIn(f'modelo {row.pk} «Tableta B»', text)
        with self.assertRaises(device_catalog.CatalogError) as caught:
            device_catalog.create_model(company=self.company, name='Tableta A')
        self.assertEqual((caught.exception.code, caught.exception.field), ('duplicate', 'name'))
        self.assertIn('«Tableta B»', str(caught.exception))
        self.assertIn('check_model_names', str(caught.exception))
        # Saving the stale row itself repairs it (and refuses nothing: «Tableta B» collides with nobody).
        device_catalog.update_model(row, sort_order=1, actor=self.staff)
        self.assertEqual(ProductModel.objects.get(pk=row.pk).normalized_name, 'tableta b')
        other = _old_code_rename(device_catalog.create_model(company=self.company, name='Tableta C'), 'Tableta D')
        self.assertEqual(_check('--fill')[0], 0)
        self.assertEqual(ProductModel.objects.get(pk=other.pk).normalized_name, 'tableta d')
        self.assertEqual(_check()[0], 0)

    def test_before_the_migration_the_gate_passes_with_zero_collisions(self):
        # Review 3: the runbook runs the check BEFORE 0166 and expects 0 when nothing collides.
        from store.management.commands import check_model_names as command

        with mock.patch.object(command, '_column_exists', return_value=False):
            code, text = _check()
            self.assertEqual((code, 'aún no migrada' in text), (0, True))
            _old_code_insert(self.company, 'Tableta Z', 'tz-a')
            _old_code_insert(self.company, 'tableta z', 'tz-b')
            self.assertEqual(_check()[0], 2)
            self.assertEqual(_check('--fill')[0], 2)

    def test_the_catalogue_proposal_plans_against_the_same_key_as_the_writer(self):
        # Review 4: «telefono x» (no accent) is the existing «Teléfono X» for the proposal too, so apply never
        # promises a model that `create_model` then refuses.
        from store.catalog_proposal import _Plan

        plan = _Plan(self.company, [])
        self.assertEqual(plan._models_by_name.get(device_catalog.normalize_model_name('TELEFONO  x')).pk, self.model.pk)

    def test_save_with_update_fields_still_writes_the_key_and_refuses_an_overlong_fold(self):
        # Review 7 and 12.
        old = _old_code_insert(self.company, 'Tableta Vieja', 'tableta-vieja')
        old.sort_order = 2
        old.save(update_fields=['sort_order'])
        self.assertEqual(ProductModel.objects.get(pk=old.pk).normalized_name, 'tableta vieja')
        with self.assertRaises(device_catalog.CatalogError) as caught:
            ProductModel(company=self.company, name='ﬁ' * 100, slug='largo').save()
        self.assertEqual(caught.exception.field, 'name')

    def test_an_edit_of_a_colliding_old_row_is_refused_before_any_storefront_work(self):
        # Review 9: the message names the name field even when the form did not send it; nothing moves.
        a = _old_code_insert(self.company, 'Tableta Z', 'tz-a')
        _old_code_insert(self.company, 'tableta z', 'tz-b')
        with mock.patch.object(device_catalog, '_claim_cover') as claim, \
                self.assertRaises(device_catalog.CatalogError):
            device_catalog.update_model(a, image_url='/api/storefront/images/1', actor=self.staff)
        claim.assert_not_called()

    def test_the_check_is_about_every_company_but_names_only_models(self):
        other = _p3_company('a1b-ventana-2', 'Otra')
        _old_code_insert(other, 'Teléfono X', 'tx-otra')          # same name as this company's model: no collision
        code, text = _check()
        self.assertEqual(code, 2)                                   # a blank key, in the other company
        self.assertIn(f'empresa {other.pk}', text)
        self.assertNotIn('Otra', text)
        self.assertNotIn('@', text)
        self.assertEqual(_check('--fill')[0], 0)
        self.assertEqual(ProductModel.objects.filter(normalized_name='telefono x').count(), 2)


# ---------------------------------------------------------------------------
# Archivos exportados por R4.5 (código a362a65), tal cual los escribió
# ---------------------------------------------------------------------------

FIXTURES = __import__('pathlib').Path(__file__).parent / 'fixtures' / 'r45'


def _fixture_upload(name):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, (FIXTURES / name).read_bytes(),
                              content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


class R45WorkbooksTest(TestCase):
    """
    The three workbooks under `fixtures/r45` were written by the R4.5 exporters (master `a362a65`) for a
    synthetic shop: columns in the positions of that release, no «SIM», no «Región». They must read exactly
    as the same shop reads back its own new export.
    """

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.company = _p3_company('fixture-r45', 'Tienda fixture R4.5')
        self.branch = self.company.default_inventory_branch
        self.staff, _ = _p2d_member(self.company, 'fixture_r45_staff', [
            'company.view', 'products.view', 'products.manage', 'inventory.view', 'inventory.adjust', 'costs.view'])
        capacity = device_catalog.create_attribute(company=self.company, name='Capacidad', role='capacity')
        colour = device_catalog.create_attribute(company=self.company, name='Color', kind='color', role='color')
        self.gb128 = device_catalog.create_option(company=self.company, attribute_id=capacity.pk, label='128 GB')
        self.gb256 = device_catalog.create_option(company=self.company, attribute_id=capacity.pk, label='256 GB')
        self.black = device_catalog.create_option(company=self.company, attribute_id=colour.pk, label='Negro',
                                                  swatch_hex='#111111')
        self.blue = device_catalog.create_option(company=self.company, attribute_id=colour.pk, label='Azul',
                                                 swatch_hex='#1d4ed8')
        self.model = device_catalog.create_model(company=self.company, name='Serie F')
        from store.models import ProductBarcode, Supplier

        def code(product, text):
            ProductBarcode.objects.create(company=self.company, product=product, code=text,
                                          symbology=ProductBarcode.INTERNAL)
        self.phone = _prod(self.company, 'Serie F 128 GB Negro', 'serie-f-128-negro', price='2500.00', inventory=0)
        Product.objects.filter(pk=self.phone.pk).update(is_serialized=True, requires_imei=True)
        code(self.phone, 'SF-128-NE')
        device_catalog.set_variant(self.phone, model_id=self.model.pk, option_ids=[self.gb128.pk, self.black.pk])
        self.used = _prod(self.company, 'Serie F 256 GB Azul usado', 'serie-f-256-azul-usado', price='1.00', inventory=0)
        Product.objects.filter(pk=self.used.pk).update(is_serialized=True, requires_imei=True)
        code(self.used, 'SF-256-AZ-U')
        device_catalog.set_variant(self.used, model_id=self.model.pk, condition_line=Product.CONDITION_USED,
                                   option_ids=[self.gb256.pk, self.blue.pk])
        self.funda = _prod(self.company, 'Funda F', 'funda-f', price='49.90', inventory=0)
        code(self.funda, 'FUN-F')
        self.supplier = Supplier.objects.create(company=self.company, name='Mayorista F')

    def test_the_r45_product_workbook_reads_as_the_same_products_with_their_variants(self):
        from store import import_services

        job = import_services.preview_products(company=self.company, actor=self.staff, filename='productos-r45.xlsx',
                                               upload=_fixture_upload('productos-r45.xlsx'))
        rows = {r.normalized_data['name']: r for r in job.rows.all()}
        self.assertEqual({name: (r.action, list(r.errors)) for name, r in rows.items()},
                         {'Funda F': ('update', []), 'Serie F 128 GB Negro': ('update', []),
                          'Serie F 256 GB Azul usado': ('update', [])})
        self.assertEqual(rows['Serie F 128 GB Negro'].normalized_data['variant'],
                         {'model_id': self.model.pk, 'condition_line': 'new',
                          'option_ids': sorted([self.gb128.pk, self.black.pk])})
        self.assertEqual(rows['Serie F 256 GB Azul usado'].normalized_data['variant']['condition_line'], 'used')
        self.assertNotIn('variant', rows['Funda F'].normalized_data)
        # Identical to how the shop reads back its own export made by THIS release (two more columns).
        content, _n = inventory_exports.products_workbook(self.company, inventory_exports.ExportFilters(),
                                                          inventory_exports.MODE_REIMPORT)
        from django.core.files.uploadedfile import SimpleUploadedFile

        fresh = import_services.preview_products(
            company=self.company, actor=self.staff, filename='productos.xlsx',
            upload=SimpleUploadedFile('productos.xlsx', content, content_type='application/octet-stream'))
        read = lambda j: sorted((r.normalized_data['name'], r.action, r.normalized_data.get('variant'),      # noqa: E731
                                 r.normalized_data.get('product_id')) for r in j.rows.all())
        self.assertEqual(read(job), read(fresh))
        before = sorted(Product.objects.filter(company=self.company)
                        .values_list('pk', 'product_model_id', 'condition_line', 'variant_key'))
        import_services.apply_products(job=job, actor=self.staff)
        self.assertEqual(sorted(Product.objects.filter(company=self.company)
                                .values_list('pk', 'product_model_id', 'condition_line', 'variant_key')), before)

    def test_the_r45_devices_workbook_reads_as_the_same_products_and_variants(self):
        from store import unit_import_services

        job = unit_import_services.preview_units(
            company=self.company, actor=self.staff, filename='equipos-r45.xlsx', upload=_fixture_upload('equipos-r45.xlsx'),
            reachable_branch_ids=[self.branch.pk], default_reason='Compra')
        rows = {r.normalized_data['serial_number']: r for r in job.rows.all()}
        self.assertEqual({serial: (r.action, list(r.errors)) for serial, r in rows.items()},
                         {'FX-0001': ('create', []), 'FX-0002': ('create', []), 'FX-U001': ('create', [])})
        self.assertEqual({serial: r.normalized_data['product_id'] for serial, r in rows.items()},
                         {'FX-0001': self.phone.pk, 'FX-0002': self.phone.pk, 'FX-U001': self.used.pk})
        self.assertEqual(rows['FX-U001'].normalized_data.get('battery_health'), 90)

    def test_the_r45_offer_workbook_reads_as_an_offer_on_the_same_product(self):
        from datetime import date

        from django.core.files.uploadedfile import SimpleUploadedFile

        # An offer export names its product by «Identificador» (company id + product id), which is checked
        # against THIS company (another shop's identifier is a row error, by design). Those ids are the
        # fixture shop's, so the cell is rewritten to this test shop's ids; every other cell, header and
        # position is exactly what R4.5 wrote.
        book = openpyxl.load_workbook(io.BytesIO((FIXTURES / 'ofertas-r45.xlsx').read_bytes()))
        sheet = book.active
        self.assertEqual(sheet['A1'].value, 'Identificador')
        sheet['A2'] = f'{self.company.pk}-{self.funda.pk}'
        out = io.BytesIO()
        book.save(out)
        job = supplier_offer_import.preview(
            company=self.company, actor=self.staff, filename='ofertas-r45.xlsx', message_date=date.today(),
            upload=SimpleUploadedFile('ofertas-r45.xlsx', out.getvalue(), content_type='application/octet-stream'))
        row = job.rows.get()
        self.assertEqual((row.action, list(row.errors), row.normalized_data.get('product_id')),
                         ('create', [], self.funda.pk))


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
