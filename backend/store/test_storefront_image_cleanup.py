import io
import os
import shutil
import tempfile
from datetime import timedelta

from django.apps import apps
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import models
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from store import storefront_media
from store.models import (
    AdminAuditLog, Category, CompanySettings, StorefrontCampaign, StorefrontImage,
    StorefrontPageSettings, validate_asset_url,
)
from store.tests import _p2d_member, _p3_company

UPLOAD = '/api/admin/storefront/images/'
PAGE = '/api/admin/storefront/page/'
CAMPAIGNS = '/api/admin/storefront/campaigns/'

_ROOT = tempfile.mkdtemp(prefix='storefront-cleanup-')


def tearDownModule():
    shutil.rmtree(_ROOT, ignore_errors=True)


def png():
    image = Image.new('RGBA', (24, 16), (0, 0, 0, 0))
    image.putpixel((12, 8), (10, 200, 30, 255))
    out = io.BytesIO()
    image.save(out, format='PNG')
    return out.getvalue()


@override_settings(EVIDENCE_STORAGE_BACKEND='filesystem', EVIDENCE_STORAGE_ROOT=_ROOT)
class CleanupBase(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('limpieza-tienda', 'Tienda Limpieza')
        self.other = _p3_company('limpieza-otra', 'Otra Tienda')
        caps = ['company.view', 'company.manage', 'products.view', 'products.manage']
        self.manager, _ = _p2d_member(self.company, 'limpieza_gestor', caps)
        self.outsider, _ = _p2d_member(self.other, 'limpieza_ajeno', caps)
        self.client = APIClient()
        self.client.force_authenticate(user=self.manager)

    def _upload(self, user=None, company=None):
        client = self.client
        if user is not None:
            client = APIClient()
            client.force_authenticate(user=user)
        res = client.post(
            f'{UPLOAD}?company={(company or self.company).pk}',
            {'file': SimpleUploadedFile('foto.png', png(), content_type='image/png')},
            format='multipart',
        )
        self.assertEqual(res.status_code, 201, res.data)
        return res.data['url']

    # EL ARCHIVO SE BORRA AL CONFIRMARSE LA TRANSACCIÓN, no antes: si el cambio
    # se deshace, el archivo nunca se fue. `TestCase` envuelve cada prueba en
    # una transacción que no se confirma, así que aquí se confirma a mano lo
    # que una petición real confirmaría sola.
    def _patch(self, url, data):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.patch(url, data, format='json')

    def _page(self, **data):
        return self._patch(f'{PAGE}?company={self.company.pk}', data)

    def _category(self, category, **data):
        return self._patch(
            f'/api/admin/categories/{category.pk}/?company={self.company.pk}', data)

    @staticmethod
    def _row(url):
        return StorefrontImage.objects.filter(public_id=url.rstrip('/').rsplit('/', 1)[-1]).first()

    def _stored(self, url):
        """Does the object still exist in storage?"""
        row = self._row(url)
        key = row.storage_key if row else self._keys[url]
        return os.path.exists(os.path.join(_ROOT, key))

    def _remember(self, *urls):
        """Storage keys, kept so a deleted row can still be checked on disk."""
        self._keys = getattr(self, '_keys', {})
        for url in urls:
            self._keys[url] = self._row(url).storage_key

    def assertGone(self, url):
        self.assertIsNone(self._row(url), 'la fila de la imagen sigue existiendo')
        self.assertFalse(self._stored(url), 'el archivo sigue en el almacenamiento')

    def assertKept(self, url):
        self.assertIsNotNone(self._row(url), 'se borró una imagen que no debía borrarse')
        self.assertTrue(self._stored(url), 'el archivo desapareció del almacenamiento')


class ReplacedImageIsReleasedTest(CleanupBase):
    """
    STOREFRONT-IMAGE-CLEANUP — una imagen sustituida no se queda guardada.

    Cada imagen subida ocupa almacenamiento para siempre si nadie la retira. La
    regla: cuando un hueco deja de apuntar a una imagen, se cuentan las
    referencias que le quedan EN TODA LA PLATAFORMA, y sólo si no queda ninguna
    se borran la fila y el archivo. Una imagen que alguien todavía muestra no
    se toca nunca.
    """

    def test_replacing_the_hero_image_deletes_the_old_one(self):
        old, new = self._upload(), self._upload()
        self._remember(old, new)
        self.assertEqual(self._page(hero_image_url=old).status_code, 200)

        res = self._page(hero_image_url=new)

        self.assertEqual(res.status_code, 200, res.data)
        self.assertGone(old)
        self.assertKept(new)

    def test_clearing_a_slot_deletes_its_image(self):
        for field in ('hero_image_url', 'services_image_url', 'location_image_url'):
            with self.subTest(field=field):
                url = self._upload()
                self._remember(url)
                self.assertEqual(self._page(**{field: url}).status_code, 200)

                self.assertEqual(self._page(**{field: ''}).status_code, 200)

                self.assertGone(url)

    def test_an_image_still_shown_in_another_slot_is_kept(self):
        url = self._upload()
        self._remember(url)
        category = Category.objects.create(company=self.company, name='Fundas', slug='fundas-limpieza')
        self._page(hero_image_url=url, services_image_url=url)
        self._category(category, image_url=url)

        self._page(hero_image_url='')
        self.assertKept(url)
        self._page(services_image_url='')
        self.assertKept(url)

        res = self._category(category, image_url='')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertGone(url)

    def test_replacing_a_category_image_deletes_the_old_one(self):
        old, new = self._upload(), self._upload()
        self._remember(old, new)
        category = Category.objects.create(
            company=self.company, name='Cables', slug='cables-limpieza', image_url=old)

        res = self._category(category, image_url=new)

        self.assertEqual(res.status_code, 200, res.data)
        self.assertGone(old)
        self.assertKept(new)

    def test_replacing_a_campaign_image_deletes_the_old_one(self):
        old, new = self._upload(), self._upload()
        self._remember(old, new)
        created = self.client.post(
            f'{CAMPAIGNS}?company={self.company.pk}',
            {'slot': 'home_hero', 'title': 'Semana del estudiante', 'image_url': old},
            format='json',
        )
        self.assertEqual(created.status_code, 201, created.data)

        res = self._patch(
            f'{CAMPAIGNS}{created.data["id"]}/?company={self.company.pk}', {'image_url': new})

        self.assertEqual(res.status_code, 200, res.data)
        self.assertGone(old)
        self.assertKept(new)

    def test_an_archived_campaign_keeps_its_image(self):
        """
        Una campaña archivada es el historial de lo que la tienda anunció. Su
        imagen es parte de ese historial: mientras la campaña exista, se queda.
        """
        url = self._upload()
        self._remember(url)
        campaign = StorefrontCampaign.objects.create(
            company=self.company, slot='home_hero', title='Campaña pasada',
            image_url=url, status=StorefrontCampaign.Status.ARCHIVED,
        )
        self._page(hero_image_url=url)

        self._page(hero_image_url='')

        self.assertKept(url)
        campaign.refresh_from_db()
        self.assertEqual(campaign.image_url, url)

    def test_the_deletion_is_audited_under_the_company(self):
        url = self._upload()
        self._remember(url)
        public_id = self._row(url).public_id
        self._page(hero_image_url=url)

        self._page(hero_image_url='')

        log = AdminAuditLog.objects.get(action='storefront_image_deleted')
        self.assertEqual(log.company_id, self.company.pk)
        self.assertEqual(log.actor, self.manager)
        self.assertEqual(log.metadata['public_id'], public_id)

    def test_a_refused_change_deletes_nothing(self):
        old, new = self._upload(), self._upload()
        self._remember(old, new)
        self._page(hero_image_url=old)

        res = self._page(hero_image_url=new, hero_variant='neon')

        self.assertEqual(res.status_code, 400)
        self.assertKept(old)
        self.assertEqual(
            StorefrontPageSettings.objects.get(company=self.company).hero_image_url, old)


class ReferencesAcrossThePlatformTest(CleanupBase):
    """Las referencias se cuentan en todas las empresas y en todos los campos."""

    def test_an_image_another_company_shows_is_not_deleted(self):
        # Datos anteriores a la regla: otra empresa apunta a esta imagen.
        url = self._upload()
        self._remember(url)
        StorefrontPageSettings.objects.update_or_create(
            company=self.other, defaults={'location_image_url': url})
        self._page(hero_image_url=url)

        self._page(hero_image_url='')

        self.assertKept(url)

    def test_a_logo_pointing_at_the_image_keeps_it(self):
        url = self._upload()
        self._remember(url)
        CompanySettings.objects.filter(company=self.company).update(logo_on_light_url=url)
        self._page(hero_image_url=url)

        self._page(hero_image_url='')

        self.assertKept(url)

    def test_an_absolute_address_counts_as_a_reference(self):
        url = self._upload()
        self._remember(url)
        category = Category.objects.create(
            company=self.company, name='Audio', slug='audio-limpieza',
            image_url=f'https://tienda.example{url}',
        )
        self._page(hero_image_url=url)

        self._page(hero_image_url='')

        self.assertKept(url)
        category.refresh_from_db()

    def test_releasing_another_companys_image_never_deletes_it(self):
        # Datos anteriores a la regla: esta empresa apuntaba a una imagen ajena.
        foreign = self._upload(user=self.outsider, company=self.other)
        self._remember(foreign)
        StorefrontPageSettings.objects.update_or_create(
            company=self.company, defaults={'hero_image_url': foreign})

        res = self._page(hero_image_url='')

        self.assertEqual(res.status_code, 200, res.data)
        self.assertKept(foreign)

    def test_addresses_that_are_not_uploads_are_left_alone(self):
        for value in ('/assets/branding/logo.png', 'https://cdn.example/foto.png'):
            with self.subTest(value=value):
                self.assertEqual(self._page(hero_image_url=value).status_code, 200)
                self.assertEqual(self._page(hero_image_url='').status_code, 200)

    def test_every_field_that_can_hold_an_image_is_counted(self):
        """
        El recuento sólo es seguro si no se le escapa ningún campo. Un campo
        nuevo que pueda llevar la dirección de una imagen tiene que estar en
        la lista, o esta prueba lo dice.
        """
        counted = {(model.__name__, field) for model, field in storefront_media.reference_fields()}
        found = set()
        for model in apps.get_app_config('store').get_models():
            for field in model._meta.get_fields():
                if not isinstance(field, (models.CharField, models.TextField)):
                    continue
                name = field.name.lower()
                if validate_asset_url in field.validators or name.endswith('_url') or 'image' in name or 'logo' in name:
                    found.add((model.__name__, field.name))
        self.assertEqual(found - counted, set())


class PlacingAnImageTest(CleanupBase):
    """
    Lo que se coloca en un hueco tiene que existir y ser de la tienda.

    Sin esto el borrado no sería seguro: un formulario abierto en otra pestaña
    podría guardar la dirección de una imagen que acaba de borrarse, y la
    portada enseñaría una imagen rota.
    """

    def test_an_upload_that_no_longer_exists_cannot_be_placed(self):
        url = self._upload()
        StorefrontImage.objects.all().delete()

        res = self._page(hero_image_url=url)

        self.assertEqual(res.status_code, 400)
        self.assertIn('hero_image_url', res.data['errors'])
        self.assertIn('ya no existe', str(res.data['errors']['hero_image_url']))

    def test_another_companys_upload_cannot_be_placed(self):
        foreign = self._upload(user=self.outsider, company=self.other)

        res = self._page(hero_image_url=foreign)

        self.assertEqual(res.status_code, 400)
        self.assertIn('hero_image_url', res.data['errors'])
        # La misma respuesta que para una que no existe: no confirma que exista.
        self.assertIn('ya no existe', str(res.data['errors']['hero_image_url']))

    def test_the_same_rule_holds_for_categories_and_campaigns(self):
        missing = '/api/storefront/images/' + 'f' * 32
        category = Category.objects.create(company=self.company, name='Fundas', slug='fundas-regla')

        res = self._category(category, image_url=missing)
        self.assertEqual(res.status_code, 400, res.data)

        res = self.client.post(
            f'{CAMPAIGNS}?company={self.company.pk}',
            {'slot': 'home_hero', 'title': 'Campaña', 'image_url': missing}, format='json',
        )
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(StorefrontCampaign.objects.filter(company=self.company).count(), 0)

    def test_saving_a_form_that_did_not_touch_the_image_still_works(self):
        url = self._upload()
        self._page(hero_image_url=url)

        res = self._page(hero_image_url=url, hero_title='Otro titular')

        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['page']['hero_title'], 'Otro titular')


class NeverPlacedUploadsTest(CleanupBase):
    """
    Lo que se subió y nunca se colocó.

    Subir no coloca: quien sube una imagen y cierra el formulario sin guardar
    deja un archivo que ningún hueco soltará nunca. Un comando lo retira, pero
    sólo pasado un plazo: una imagen recién subida todavía puede estar en un
    formulario abierto a punto de guardarse.
    """

    def _age(self, url, hours):
        StorefrontImage.objects.filter(pk=self._row(url).pk).update(
            created_at=timezone.now() - timedelta(hours=hours))

    def _run(self, *args):
        out = io.StringIO()
        with self.captureOnCommitCallbacks(execute=True):
            call_command('cleanup_storefront_images', *args, stdout=out)
        return out.getvalue()

    def test_an_old_upload_nobody_placed_is_deleted(self):
        url = self._upload()
        self._remember(url)
        self._age(url, 48)

        output = self._run()

        self.assertGone(url)
        self.assertIn('1', output)

    def test_a_recent_upload_is_kept_even_if_nobody_placed_it_yet(self):
        url = self._upload()
        self._remember(url)
        self._age(url, 2)

        self._run()

        self.assertKept(url)

    def test_an_old_upload_that_is_shown_is_kept(self):
        url = self._upload()
        self._remember(url)
        self._page(hero_image_url=url)
        self._age(url, 500)

        self._run()

        self.assertKept(url)

    def test_the_grace_period_can_be_changed(self):
        url = self._upload()
        self._remember(url)
        self._age(url, 2)

        self._run('--older-than-hours', '1')

        self.assertGone(url)

    def test_a_dry_run_deletes_nothing_and_says_what_it_would_delete(self):
        url = self._upload()
        self._remember(url)
        self._age(url, 48)

        output = self._run('--dry-run')

        self.assertKept(url)
        self.assertIn(self._row(url).public_id, output)
