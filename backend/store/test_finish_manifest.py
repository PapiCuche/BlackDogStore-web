"""
CATALOG FASE 1.2 · Z — `import_finish_images`: licensed finish photos loaded from a manifest, safely and idempotently.

What is fixed here:

  * the whole manifest is validated first and reported row by row (ok / skip-duplicate / error); any error and nothing
    is written (all-or-nothing) and the command exits non-zero; `--dry-run` never writes nor stores a file;
  * every row says where the picture comes from (`finish_media.clean_provenance`); a licence is NEVER marked verified;
  * the model is the company's (by slug or by the reference key it was imported from), never another company's; the
    colour is one of THAT model's palette (by reference key or label), never another colour;
  * a file is a relative path inside `--dir`: no absolute path, no `..`, no symlink leaving it;
  * the same content already in that finish is skipped: re-running a manifest writes nothing new;
  * the panel's limits hold (pictures per colour, upload size, decoder-decided type) and at most one primary per finish;
  * a failed run leaves no stored file behind; every write is audited with the company, plus one summary row.
"""
import atexit
import csv
import hashlib
import io
import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from PIL import Image

from store import device_catalog, finish_media, model_palette, storefront_media
from store.models import AdminAuditLog, ModelFinishImage, StorefrontImage
from store.test_finish_images import FinishShop
from store.tests import _p2d_member, _p3_company

_ROOT = tempfile.mkdtemp(prefix='finish-manifest-')
# Shared by the parallel runner's forked workers: only the creating process removes it, at exit
# (see test_parallel_tempdir_guard).
atexit.register(shutil.rmtree, _ROOT, True)

FIELDS = ['model', 'color', 'file', 'view', 'alt_text', 'is_primary', 'sort', 'source_kind', 'source_url', 'license',
          'attribution']


def picture(seed: int, fmt: str = 'PNG') -> bytes:
    """A tiny picture whose pixels (and so its content) depend on `seed`."""
    image = Image.new('RGBA', (24 + seed, 16), (0, 0, 0, 0))
    for x in range(4, 20):
        image.putpixel((x, 8), ((seed * 37) % 256, (seed * 91) % 256, 120, 255))
    out = io.BytesIO()
    image.save(out, format=fmt, **({'lossless': True} if fmt == 'WEBP' else {}))
    return out.getvalue()


def row(**values) -> dict:
    base = {'model': 'telefono-x', 'color': 'Negro', 'file': 'negro-1.png', 'view': 'front', 'alt_text': 'Frente',
            'is_primary': '', 'sort': '', 'source_kind': 'licensed_stock',
            'source_url': 'https://example.com/foto', 'license': 'Licencia comercial de ejemplo',
            'attribution': 'Ejemplo S.A.'}
    base.update(values)
    return base


@override_settings(EVIDENCE_STORAGE_ROOT=_ROOT)
class ManifestShop(FinishShop):
    def setUp(self):
        super().setUp()
        self.folder = Path(tempfile.mkdtemp(prefix='fotos-', dir=_ROOT))
        self.work = Path(tempfile.mkdtemp(prefix='manifiestos-', dir=_ROOT))
        self.model.reference_key = 'telefono-x-ref'
        self.model.save(update_fields=['reference_key'])
        self.black.reference_key = 'color:negro-medianoche'
        self.black.save(update_fields=['reference_key'])
        model_palette.set_palette(self.model, option_ids=[self.black.pk, self.blue.pk], actor=self.staff)

    def file(self, name, content=None, *, seed=1):
        path = self.folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if content is not None else picture(seed))
        return path

    def manifest(self, rows, *, kind='csv') -> Path:
        path = self.work / f'manifiesto-{len(list(self.work.iterdir()))}.{kind}'
        if kind == 'json':
            path.write_text(json.dumps(rows), encoding='utf-8')
        else:
            with path.open('w', newline='', encoding='utf-8') as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows(rows)
        return path

    def do_import(self, manifest, *, dry_run=False, actor=None, company=None, folder=None):
        out = io.StringIO()
        args = ['import_finish_images', '--company', company or self.company.slug, '--manifest', str(manifest),
                '--dir', str(folder or self.folder), '--actor', str(actor if actor is not None else self.staff.pk)]
        if dry_run:
            args.append('--dry-run')
        with self.captureOnCommitCallbacks(execute=True):
            call_command(*args, stdout=out)
        return out.getvalue()

    def fails(self, manifest, **kwargs) -> str:
        out = io.StringIO()
        with self.assertRaises(CommandError) as caught, self.captureOnCommitCallbacks(execute=True):
            args = ['import_finish_images', '--company', self.company.slug, '--manifest', str(manifest),
                    '--dir', str(kwargs.get('folder', self.folder)), '--actor', str(kwargs.get('actor', self.staff.pk))]
            if kwargs.get('dry_run'):
                args.append('--dry-run')
            call_command(*args, stdout=out)
        return out.getvalue() + '\n' + str(caught.exception)

    def stored_files(self) -> set:
        base = Path(_ROOT) / 'companies' / str(self.company.pk) / 'storefront'
        return {p.name for p in base.iterdir()} if base.exists() else set()

    def snapshot(self):
        return (ModelFinishImage.objects.count(), StorefrontImage.objects.count(),
                AdminAuditLog.objects.count(), self.stored_files())


class HappyPathTest(ManifestShop):
    def test_imports_each_row_with_its_provenance_and_never_verifies_the_licence(self):
        self.file('negro-1.png', seed=1)
        self.file('negro-2.webp', picture(2, 'WEBP'))
        self.file('azul/1.png', seed=3)
        manifest = self.manifest([
            row(file='negro-1.png', view='main', alt_text='Negro de frente'),
            row(file='negro-2.webp', view='back', color='color:negro-medianoche', model='telefono-x-ref',
                source_kind='own_photo', license='', source_url='', attribution=''),
            row(file='azul/1.png', color='azul', view='blank', alt_text=''),
        ])
        output = self.do_import(manifest)

        black = list(finish_media.gallery(self.model, self.black).order_by('sort_order'))
        blue = list(finish_media.gallery(self.model, self.blue))
        self.assertEqual(len(black), 2)
        self.assertEqual(len(blue), 1)
        self.assertEqual([image.view for image in black], ['main', 'back'])
        self.assertEqual(blue[0].view, '')
        self.assertEqual(black[0].alt_text, 'Negro de frente')
        self.assertTrue(black[0].is_primary)
        self.assertFalse(black[1].is_primary)
        stored = finish_media.stored_of(black + blue)
        first = stored[storefront_media.managed_public_id(black[0].image_url)]
        self.assertEqual((first.source_kind, first.license, first.source_url, first.attribution),
                         ('licensed_stock', 'Licencia comercial de ejemplo', 'https://example.com/foto',
                          'Ejemplo S.A.'))
        own = stored[storefront_media.managed_public_id(black[1].image_url)]
        self.assertEqual(own.source_kind, 'own_photo')
        self.assertEqual(own.mime_type, 'image/webp')
        for image in stored.values():
            self.assertIsNone(image.license_verified_at)
            self.assertIsNone(image.license_verified_by)
            self.assertEqual(image.company, self.company)
        self.assertIn('3 nuevas', output)

    def test_json_manifest(self):
        self.file('negro-1.png', seed=1)
        manifest = self.manifest([row(is_primary=True, sort=0)], kind='json')
        self.do_import(manifest)
        self.assertEqual(finish_media.gallery(self.model, self.black).count(), 1)

    def test_is_primary_goes_through_the_service_and_demotes_the_current_primary(self):
        self.file('negro-1.png', seed=1)
        self.file('negro-2.png', seed=2)
        self.do_import(self.manifest([row(file='negro-1.png')]))
        self.do_import(self.manifest([row(file='negro-2.png', is_primary='true')]))
        primary = finish_media.gallery(self.model, self.black).get(is_primary=True)
        self.assertEqual(primary.sort_order, 1)
        self.assertTrue(AdminAuditLog.objects.filter(action='finish_image_primary_changed',
                                                     company=self.company).exists())

    def test_the_sort_hint_orders_new_pictures_of_a_finish(self):
        self.file('a.png', seed=1)
        self.file('b.png', seed=2)
        self.do_import(self.manifest([row(file='a.png', sort='2', alt_text='A'), row(file='b.png', sort='1', alt_text='B')]))
        self.assertEqual([image.alt_text for image in finish_media.gallery(self.model, self.black)
                          .order_by('sort_order')], ['B', 'A'])

    def test_every_write_is_audited_with_the_company_and_one_summary(self):
        self.file('negro-1.png', seed=1)
        manifest = self.manifest([row()])
        self.do_import(manifest)
        actions = {'storefront_image_uploaded', 'finish_image_added', 'finish_images_imported'}
        logs = AdminAuditLog.objects.filter(action__in=actions)
        self.assertEqual({log.action for log in logs}, actions)
        self.assertTrue(all(log.company_id == self.company.pk for log in logs))
        self.assertTrue(all(log.actor_id == self.staff.pk for log in logs))
        summary = AdminAuditLog.objects.get(action='finish_images_imported')
        self.assertEqual(summary.metadata['manifest_sha256'], hashlib.sha256(manifest.read_bytes()).hexdigest())
        self.assertEqual(summary.metadata['imported'], 1)
        self.assertEqual(summary.metadata['skipped'], 0)


class DryRunTest(ManifestShop):
    def test_dry_run_writes_nothing_and_stores_no_file(self):
        self.file('negro-1.png', seed=1)
        before = self.snapshot()
        output = self.do_import(self.manifest([row()]), dry_run=True)
        self.assertEqual(self.snapshot(), before)
        self.assertIn('ok', output)
        self.assertIn('simulación', output)

    def test_dry_run_with_errors_reports_them_and_exits_non_zero(self):
        before = self.snapshot()
        output = self.fails(self.manifest([row(file='no-existe.png')]), dry_run=True)
        self.assertIn('error', output)
        self.assertEqual(self.snapshot(), before)


class IdempotencyTest(ManifestShop):
    def test_rerunning_the_same_manifest_writes_nothing_new(self):
        self.file('negro-1.png', seed=1)
        self.file('azul.png', seed=2)
        manifest = self.manifest([row(), row(file='azul.png', color='Azul')])
        self.do_import(manifest)
        before = self.snapshot()
        output = self.do_import(manifest)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(output.count('skip-duplicate'), 2)

    def test_a_picture_already_uploaded_from_the_panel_is_skipped(self):
        raw = picture(5)
        self.file('negro-1.png', raw)
        finish_media.add_image(model=self.model, color_option_id=self.black.pk, actor=self.staff,
                               uploaded=storefront_media_upload(raw),
                               provenance={'source_kind': 'own_photo'})
        before = ModelFinishImage.objects.count()
        output = self.do_import(self.manifest([row()]))
        self.assertEqual(ModelFinishImage.objects.count(), before)
        self.assertIn('skip-duplicate', output)

    def test_the_recorded_source_hash_survives_a_different_encoding(self):
        # The stored bytes are a re-encoding: if the encoder changed, the summary still knows that source file.
        self.file('negro-1.png', seed=1)
        manifest = self.manifest([row()])
        self.do_import(manifest)
        image = finish_media.gallery(self.model, self.black).get()
        stored = finish_media.stored_of([image])[storefront_media.managed_public_id(image.image_url)]
        (Path(_ROOT) / stored.storage_key).write_bytes(picture(40))
        before = ModelFinishImage.objects.count()
        self.do_import(manifest)
        self.assertEqual(ModelFinishImage.objects.count(), before)

    def test_the_same_file_twice_for_one_finish_is_imported_once(self):
        self.file('negro-1.png', seed=1)
        shutil.copy(self.folder / 'negro-1.png', self.folder / 'copia.png')
        output = self.do_import(self.manifest([row(), row(file='copia.png')]))
        self.assertEqual(finish_media.gallery(self.model, self.black).count(), 1)
        self.assertIn('skip-duplicate', output)

    def test_the_same_photo_for_two_colours_is_refused(self):
        self.file('negro-1.png', seed=1)
        before = self.snapshot()
        output = self.fails(self.manifest([row(), row(color='Azul')]))
        self.assertIn('otro color', output)
        self.assertEqual(self.snapshot(), before)


def storefront_media_upload(raw):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile('foto.png', raw, content_type='image/png')


class AllOrNothingTest(ManifestShop):
    def test_a_missing_licence_is_an_error_and_nothing_is_written(self):
        self.file('negro-1.png', seed=1)
        self.file('azul.png', seed=2)
        before = self.snapshot()
        output = self.fails(self.manifest([row(), row(file='azul.png', color='Azul', license='')]))
        self.assertIn('fila 2', output)
        self.assertIn('licencia', output.lower())
        self.assertEqual(self.snapshot(), before)

    def test_a_missing_source_kind_is_an_error(self):
        self.file('negro-1.png', seed=1)
        output = self.fails(self.manifest([row(source_kind='')]))
        self.assertIn('de dónde sale', output)
        self.assertEqual(ModelFinishImage.objects.count(), 0)

    def test_an_unknown_source_kind_is_an_error(self):
        self.file('negro-1.png', seed=1)
        self.fails(self.manifest([row(source_kind='unknown')]))
        self.assertEqual(ModelFinishImage.objects.count(), 0)

    def test_a_failure_while_placing_releases_every_stored_file(self):
        self.file('negro-1.png', seed=1)
        self.file('negro-2.png', seed=2)
        before = self.snapshot()
        real_place = finish_media.place
        calls = []

        def flaky(**kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError('fallo provocado')
            return real_place(**kwargs)

        with mock.patch('store.finish_manifest.finish_media.place', side_effect=flaky), \
                self.assertRaises(RuntimeError), self.captureOnCommitCallbacks(execute=True):
            call_command('import_finish_images', '--company', self.company.slug,
                         '--manifest', str(self.manifest([row(file='negro-1.png'), row(file='negro-2.png')])),
                         '--dir', str(self.folder), '--actor', str(self.staff.pk), stdout=io.StringIO())
        count, stored, _logs, files = self.snapshot()
        self.assertEqual((count, stored, files), (before[0], before[1], before[3]))
        self.assertFalse(AdminAuditLog.objects.filter(action='finish_images_imported').exists())

    def test_not_a_picture_is_refused_by_the_decoder(self):
        self.file('negro-1.png', b'<svg xmlns="http://www.w3.org/2000/svg"></svg>')
        before = self.snapshot()
        self.fails(self.manifest([row()]))
        self.assertEqual(self.snapshot(), before)

    @override_settings(STOREFRONT_IMAGE_MAX_UPLOAD_BYTES=100)
    def test_the_upload_size_limit_holds(self):
        self.file('negro-1.png', picture(1) + b'\0' * 200)
        output = self.fails(self.manifest([row()]))
        self.assertIn('pesa más', output)
        self.assertEqual(ModelFinishImage.objects.count(), 0)


class PathSafetyTest(ManifestShop):
    def test_traversal_absolute_and_escaping_symlinks_are_refused(self):
        outside = Path(self.work) / 'fuera.png'
        outside.write_bytes(picture(1))
        os.symlink(outside, self.folder / 'enlace.png')
        before = self.snapshot()
        output = self.fails(self.manifest([
            row(file='../' + outside.parent.name + '/fuera.png'),
            row(file=str(outside)),
            row(file='enlace.png'),
        ]))
        self.assertEqual(output.count('· error ·'), 3)
        self.assertEqual(self.snapshot(), before)

    def test_a_symlink_inside_the_folder_is_allowed(self):
        self.file('real.png', seed=1)
        os.symlink(self.folder / 'real.png', self.folder / 'alias.png')
        self.do_import(self.manifest([row(file='alias.png')]))
        self.assertEqual(finish_media.gallery(self.model, self.black).count(), 1)


class ResolutionTest(ManifestShop):
    def test_a_colour_outside_the_models_palette_is_refused(self):
        model_palette.set_palette(self.model, option_ids=[self.black.pk], actor=self.staff)
        self.file('negro-1.png', seed=1)
        output = self.fails(self.manifest([row(color='Azul')]))
        self.assertIn('paleta', output)
        self.assertEqual(ModelFinishImage.objects.count(), 0)

    def test_a_non_colour_option_is_refused(self):
        self.file('negro-1.png', seed=1)
        self.fails(self.manifest([row(color='128 GB')]))
        self.assertEqual(ModelFinishImage.objects.count(), 0)

    def test_another_companys_model_is_not_resolvable(self):
        other = _p3_company('pc-ajena', 'Ajena')
        foreign = device_catalog.create_model(company=other, name='Modelo ajeno')
        foreign.reference_key = 'ajeno-ref'
        foreign.save(update_fields=['reference_key'])
        self.file('negro-1.png', seed=1)
        output = self.fails(self.manifest([row(model=foreign.slug), row(model='ajeno-ref')]))
        self.assertEqual(output.count('Modelo no encontrado'), 2)
        self.assertEqual(ModelFinishImage.objects.count(), 0)

    def test_the_actor_must_manage_products_in_that_company(self):
        other = _p3_company('pc-ajena-2', 'Ajena 2')
        outsider, _ = _p2d_member(other, 'fuera_staff', ['products.view', 'products.manage'])
        viewer, _ = _p2d_member(self.company, 'solo_ver', ['products.view'])
        self.file('negro-1.png', seed=1)
        manifest = self.manifest([row()])
        for actor in (outsider.pk, viewer.pk, 'nadie@example.com', '999999'):
            with self.subTest(actor=actor), self.assertRaises(CommandError):
                call_command('import_finish_images', '--company', self.company.slug, '--manifest', str(manifest),
                             '--dir', str(self.folder), '--actor', str(actor), stdout=io.StringIO())
        self.assertEqual(ModelFinishImage.objects.count(), 0)

    def test_the_actor_may_be_named_by_email(self):
        self.staff.email = 'Equipo@Example.com'
        self.staff.save(update_fields=['email'])
        self.file('negro-1.png', seed=1)
        self.do_import(self.manifest([row()]), actor='equipo@example.com')
        self.assertEqual(ModelFinishImage.objects.count(), 1)

    def test_an_unknown_view_or_field_is_an_error(self):
        self.file('negro-1.png', seed=1)
        self.fails(self.manifest([row(view='arriba')]))
        self.fails(self.manifest([{**row(), 'licence': 'x'}], kind='json'))
        self.assertEqual(ModelFinishImage.objects.count(), 0)


class LimitsTest(ManifestShop):
    def test_at_most_one_primary_per_finish_in_the_manifest(self):
        self.file('negro-1.png', seed=1)
        self.file('negro-2.png', seed=2)
        output = self.fails(self.manifest([row(file='negro-1.png', is_primary='1'),
                                           row(file='negro-2.png', is_primary='sí')]))
        self.assertIn('principal', output)
        self.assertEqual(ModelFinishImage.objects.count(), 0)

    @override_settings(FINISH_IMAGE_MAX_PER_COLOUR=2)
    def test_the_limit_per_colour_counts_existing_and_new_pictures(self):
        self.file('negro-1.png', seed=1)
        self.file('negro-2.png', seed=2)
        self.file('negro-3.png', seed=3)
        self.do_import(self.manifest([row(file='negro-1.png')]))
        before = self.snapshot()
        output = self.fails(self.manifest([row(file='negro-2.png'), row(file='negro-3.png')]))
        self.assertIn('hasta 2', output)
        self.assertEqual(self.snapshot(), before)


class StorageIsTheStoreTest(ManifestShop):
    def test_files_go_through_storefront_media(self):
        self.file('negro-1.png', seed=1)
        with mock.patch('store.finish_manifest.storefront_media.store', wraps=storefront_media.store) as store:
            self.do_import(self.manifest([row()]))
        self.assertEqual(store.call_count, 1)
        self.assertEqual(store.call_args.kwargs['company'], self.company)


class ExampleManifestTest(ManifestShop):
    def test_the_shipped_example_is_a_valid_manifest_with_only_fake_sources(self):
        from django.conf import settings

        from store import finish_manifest

        example = Path(settings.BASE_DIR).parent / 'docs' / 'finish-images-manifest.example.csv'
        if not example.exists():
            self.skipTest('docs/ is not next to the backend in this checkout')
        rows, _digest = finish_manifest.read_manifest(example)
        self.assertEqual(len(rows), 2)
        for item in rows:
            self.assertEqual(set(item), set(finish_manifest.FIELDS))
            self.assertTrue(item['source_url'].startswith('https://example.com/'))
