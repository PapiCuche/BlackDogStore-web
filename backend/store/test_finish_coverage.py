"""
CATALOG-REFERENCE-01 · FASE 1.2 — cobertura de fotos por acabado de UNA empresa (`report_finish_coverage`).

    python manage.py report_finish_coverage --company <slug> [--format csv|json] [--only-missing]

Lo que estas pruebas fijan:

  * una fila por modelo × color del catálogo de la empresa (los mismos acabados que el informe F4,
    `catalog_image_sources.catalog_finishes`: paleta y variantes activas, dimensión de color D-PRC-8), sin tope;
  * por acabado: fotos, si tiene principal, licencias verificadas, demostrativas, propias, de proveedor, de banco con
    licencia y sin origen;
  * una foto demostrativa NO cubre un acabado: cubierto = al menos una foto que no es demostrativa;
  * el resumen: acabados, cubiertos, sin fotos y porcentaje de cobertura;
  * otra empresa nunca aparece; una empresa que no existe es un error; el comando no escribe nada;
  * el informe de la API sigue con su tope de 200 nombres (`MAX_PENDING_FINISHES`).
"""
import csv
import io
import json
from unittest import mock

from django.core.management import CommandError, call_command

from store import catalog_image_sources as sources
from store import device_catalog, model_palette, storefront_media
from store.models import AdminAuditLog, ModelFinishImage, StorefrontImage
from store.test_finish_images import OWN, SUPPLIER, FinishApiBase
from store.test_storefront_media import png_with_transparency
from store.tests import _p2d_member, _p3_company

DEMO = {'source_kind': 'demo', 'license': 'Recurso de demostración'}
LICENSED = {'source_kind': 'licensed_stock', 'license': 'Licencia comercial del banco, 2026'}


class _Coverage(FinishApiBase):
    def run_command(self, *args, slug=None):
        out, err = io.StringIO(), io.StringIO()
        call_command('report_finish_coverage', '--company', slug or self.company.slug, *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def rows(self, *args):
        text, _summary = self.run_command('--format', 'csv', *args)
        return {(r['model_name'], r['option_label']): r for r in csv.DictReader(io.StringIO(text))}

    def verify(self, image):
        with self.captureOnCommitCallbacks(execute=True):
            res = self.api.patch(self.url(f'{image["id"]}/'), {'license_verified': True}, format='json')
        self.assertEqual(res.status_code, 200, res.content)


class FinishCoverageServiceTest(_Coverage):
    def test_every_finish_of_the_catalogue_with_its_counts(self):
        first = self.uploaded(self.black, SUPPLIER)
        self.uploaded(self.black, OWN)
        self.uploaded(self.black, LICENSED)
        self.verify(first)
        report = sources.finish_coverage(self.company)
        rows = {row['option_label']: row for row in report['rows']}
        self.assertEqual(set(rows), {'Negro', 'Azul'})
        black = rows['Negro']
        self.assertEqual((black['model_id'], black['model_name'], black['option_id']),
                         (self.model.pk, 'Teléfono X', self.black.pk))
        self.assertEqual((black['photos'], black['has_primary'], black['license_verified'], black['demo']), (3, True, 1, 0))
        self.assertEqual((black['own'], black['supplier'], black['licensed'], black['unknown']), (1, 1, 1, 0))
        self.assertTrue(black['covered'])
        blue = rows['Azul']
        self.assertEqual((blue['photos'], blue['has_primary'], blue['covered']), (0, False, False))
        self.assertTrue(black['public'])
        self.assertFalse(blue['public'])
        self.assertEqual(report['summary'], {'finishes': 2, 'covered': 1, 'without_photos': 1, 'demo_only': 0,
                                             'public': 1, 'coverage_percent': 50.0})

    def test_demo_photos_do_not_cover_a_finish(self):
        self.uploaded(self.blue, DEMO)
        self.uploaded(self.blue, DEMO)
        report = sources.finish_coverage(self.company)
        blue = next(row for row in report['rows'] if row['option_id'] == self.blue.pk)
        self.assertEqual((blue['photos'], blue['demo'], blue['has_primary'], blue['covered']), (2, 2, True, False))
        self.assertEqual(report['summary'], {'finishes': 2, 'covered': 0, 'without_photos': 1, 'demo_only': 1,
                                             'public': 0, 'coverage_percent': 0.0})
        self.uploaded(self.blue, OWN)
        self.assertEqual(sources.finish_coverage(self.company)['summary']['covered'], 1)

    def test_a_covered_finish_is_public_only_with_a_verified_licence(self):
        # Owner, 2026-11-23: a finish picture is on the shop only once its licence is verified, whatever its source.
        own = self.uploaded(self.blue, OWN)
        demo = self.uploaded(self.black, DEMO)
        rows = {row['option_label']: row for row in sources.finish_coverage(self.company)['rows']}
        self.assertEqual((rows['Azul']['covered'], rows['Azul']['public']), (True, False))
        self.assertEqual((rows['Negro']['covered'], rows['Negro']['public']), (False, False))
        self.verify(own)
        self.verify(demo)
        report = sources.finish_coverage(self.company)
        rows = {row['option_label']: row for row in report['rows']}
        self.assertEqual((rows['Azul']['covered'], rows['Azul']['public']), (True, True))
        # A verified demo is on the shop («Imagen referencial») but still covers nothing.
        self.assertEqual((rows['Negro']['covered'], rows['Negro']['public']), (False, True))
        self.assertEqual((report['summary']['covered'], report['summary']['public']), (1, 2))

    def test_a_palette_colour_without_variants_is_a_finish(self):
        red = device_catalog.create_option(company=self.company, attribute_id=self.colour.pk, label='Rojo',
                                           swatch_hex='#B91C1C')
        model_palette.set_palette(self.model, option_ids=[self.gb128.pk, self.black.pk, self.blue.pk, red.pk],
                                  actor=self.staff)
        labels = [row['option_label'] for row in sources.finish_coverage(self.company)['rows']]
        self.assertEqual(sorted(labels), ['Azul', 'Negro', 'Rojo'])

    def test_another_companys_models_and_photos_never_appear(self):
        other = _p3_company('pc-cob-otra', 'Otra')
        stranger, _ = _p2d_member(other, 'pc_cob_ajeno', ['company.view', 'products.view', 'products.manage'])
        colour = device_catalog.create_attribute(company=other, name='Color', kind='color', role='color')
        green = device_catalog.create_option(company=other, attribute_id=colour.pk, label='Verde', swatch_hex='#00AA00')
        foreign = device_catalog.create_model(company=other, name='Ajeno')
        model_palette.set_palette(foreign, option_ids=[green.pk], actor=stranger)
        stored = storefront_media.store(company=other, actor=stranger, raw=png_with_transparency())
        ModelFinishImage.objects.create(company=other, model=foreign, color_option=green, image_url=stored.url,
                                        is_primary=True)
        mine = sources.finish_coverage(self.company)
        self.assertEqual({row['model_name'] for row in mine['rows']}, {'Teléfono X'})
        self.assertEqual(mine['summary']['finishes'], 2)
        theirs = sources.finish_coverage(other)
        self.assertEqual([(row['model_name'], row['option_label'], row['photos'], row['covered'])
                          for row in theirs['rows']], [('Ajeno', 'Verde', 1, True)])

    def test_no_cap_where_the_api_names_at_most_its_maximum(self):
        for n in range(3):
            device_catalog.create_option(company=self.company, attribute_id=self.colour.pk, label=f'Tono {n}',
                                         swatch_hex='#123456')
        model_palette.set_palette(self.model, option_ids=[self.gb128.pk, *self._colour_ids()], actor=self.staff)
        with mock.patch.object(sources, 'MAX_PENDING_FINISHES', 2):
            api = sources.licensing(self.company)
            full = sources.finish_coverage(self.company)
        self.assertEqual(api['finishes_without_images'], 5)
        self.assertEqual(len(api['pending_finishes']), 2)
        self.assertEqual(len(full['rows']), 5)

    def test_the_count_of_queries_does_not_grow(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.uploaded(self.black, SUPPLIER)
        with CaptureQueriesContext(connection) as before:
            sources.finish_coverage(self.company)
        for _ in range(3):
            self.uploaded(self.blue, OWN)
        second = device_catalog.create_model(company=self.company, name='Teléfono Z')
        model_palette.set_palette(second, option_ids=[self.black.pk], actor=self.staff)
        with CaptureQueriesContext(connection) as after:
            report = sources.finish_coverage(self.company)
        self.assertEqual(len(after.captured_queries), len(before.captured_queries))
        self.assertEqual(report['summary']['finishes'], 3)

    def _colour_ids(self):
        from store.models import VariantOption

        return list(VariantOption.objects.filter(company=self.company, attribute=self.colour).values_list('pk', flat=True))


class FinishCoverageCommandTest(_Coverage):
    def test_csv_rows_and_a_summary_line(self):
        self.uploaded(self.black, OWN)
        text, summary = self.run_command()
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(list(rows[0]), list(sources.FINISH_COVERAGE_COLUMNS))
        by_label = {r['option_label']: r for r in rows}
        self.assertEqual((by_label['Negro']['photos'], by_label['Negro']['covered']), ('1', 'yes'))
        self.assertEqual((by_label['Azul']['photos'], by_label['Azul']['covered']), ('0', 'no'))
        self.assertIn('2 acabado(s)', summary)
        self.assertIn('1 con al menos una foto no demostrativa', summary)
        self.assertIn('1 sin fotos', summary)
        self.assertIn('50.0 %', summary)
        # Covered is not visible: nobody verified the licence yet (owner, 2026-11-23).
        self.assertEqual(by_label['Negro']['public'], 'no')
        self.assertIn('0 visible(s) en la tienda', summary)

    def test_json_and_only_missing(self):
        self.uploaded(self.black, OWN)
        self.uploaded(self.blue, DEMO)
        text, _summary = self.run_command('--format', 'json', '--only-missing')
        data = json.loads(text)
        self.assertEqual(data['company'], self.company.slug)
        self.assertEqual([row['option_label'] for row in data['rows']], ['Azul'])
        self.assertEqual(data['summary']['finishes'], 2)    # the summary is always the whole catalogue's

    def test_an_unknown_company_is_an_error_and_nothing_is_written(self):
        with self.assertRaises(CommandError):
            self.run_command(slug='no-existe')
        before = (AdminAuditLog.objects.count(), StorefrontImage.objects.count(), ModelFinishImage.objects.count())
        self.run_command()
        self.run_command('--format', 'json')
        self.assertEqual(before, (AdminAuditLog.objects.count(), StorefrontImage.objects.count(),
                                  ModelFinishImage.objects.count()))
