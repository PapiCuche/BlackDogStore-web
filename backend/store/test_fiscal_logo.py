"""
El logotipo de la tienda en el comprobante — PAYMENT-FISCAL-PRINT-01.

  · LO ELIGE LA TIENDA. Se sube desde el panel, como las demás imágenes; el
    papel de una tienda sin logotipo sale como hasta ahora.
  · SE CONGELA CON EL COMPROBANTE. Un comprobante reimpreso dentro de dos años
    lleva el logotipo con el que se emitió, aunque la tienda lo haya cambiado
    o borrado. Es la misma disciplina que ya se aplica al nombre y a la
    dirección del emisor.
  · SIN ADORNO. En la tienda, un recorte subido recibe una sombra suave. En un
    documento tributario no: el logotipo se dibuja tal cual, una vez, sin
    sombra, ni en el papel ni en la vista previa del panel.
"""
import hashlib
import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image
from rest_framework.test import APIClient

from store import evidence_storage as storage
from store import fiscal_logo, storefront_media
from store.fiscal_pdf_services import generate_fiscal_pdf, generate_fiscal_ticket_pdf
from store.models import CompanySettings, StorefrontImage
from store.test_fiscal_print import FiscalPrintBase
from store.tests import _p2d_member, _p3_company

SETTINGS = '/api/admin/company-settings/'
UPLOAD = '/api/admin/storefront/images/'

_ROOT = tempfile.mkdtemp(prefix='fiscal-logo-')


def tearDownModule():
    shutil.rmtree(_ROOT, ignore_errors=True)


def logo_png(colour=(20, 20, 20, 255), size=(240, 80)):
    image = Image.new('RGBA', size, (0, 0, 0, 0))
    for x in range(20, size[0] - 20):
        for y in range(20, size[1] - 20):
            image.putpixel((x, y), colour)
    out = io.BytesIO()
    image.save(out, format='PNG')
    return out.getvalue()


def embedded_images(pdf_bytes: bytes) -> int:
    """Cuántas imágenes lleva el PDF, sin contar máscaras de transparencia."""
    return pdf_bytes.count(b'/Subtype /Image') - pdf_bytes.count(b'/SMask ')


@override_settings(EVIDENCE_STORAGE_BACKEND='filesystem', EVIDENCE_STORAGE_ROOT=_ROOT)
class FiscalLogoBase(FiscalPrintBase):
    def setUp(self):
        super().setUp()
        self.manager, _ = _p2d_member(
            self.company, 'logo_gestor', ['company.view', 'company.manage'])
        self.client = APIClient()
        self.client.force_authenticate(user=self.manager)

    def upload(self, content=None, company=None):
        company = company or self.company
        res = self.client.post(
            f'{UPLOAD}?company={company.pk}',
            {'file': SimpleUploadedFile('logo.png', content or logo_png(), 'image/png')},
            format='multipart')
        self.assertEqual(res.status_code, 201, res.data)
        return res.data['url']

    def set_logo(self, url, company=None):
        company = company or self.company
        return self.client.patch(
            f'{SETTINGS}?company={company.pk}', {'document_logo_url': url}, format='json')


class DocumentLogoSettingTest(FiscalLogoBase):
    def test_the_shop_chooses_the_logo_of_its_documents(self):
        url = self.upload()

        res = self.set_logo(url)

        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['settings']['document_logo_url'], url)
        self.assertEqual(
            CompanySettings.objects.get(company=self.company).document_logo_url, url)

    def test_only_an_image_uploaded_by_this_shop_can_be_the_logo(self):
        other = _p3_company('logo-ajena', 'Tienda Ajena')
        _p2d_member(other, 'logo_ajeno', ['company.view', 'company.manage'])
        foreign = storefront_media.upload(
            company=other, actor=None,
            uploaded=SimpleUploadedFile('logo.png', logo_png(), 'image/png'))

        for value in (storefront_media.payload(foreign)['url'],
                      'https://cdn.example/logo.png', '/assets/branding/logo.png',
                      '/api/storefront/images/' + '0' * 32):
            with self.subTest(value=value):
                res = self.set_logo(value)
                self.assertEqual(res.status_code, 400, res.data)
                self.assertIn('document_logo_url', res.data)
        self.assertEqual(
            CompanySettings.objects.get(company=self.company).document_logo_url, '')

    def test_the_logo_can_be_removed(self):
        self.set_logo(self.upload())

        res = self.set_logo('')

        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['settings']['document_logo_url'], '')

    def test_a_logo_in_use_is_not_swept_as_an_unplaced_upload(self):
        url = self.upload()
        self.set_logo(url)

        public_id = storefront_media.managed_public_id(url)
        self.assertEqual(storefront_media.reference_count(public_id), 1)


class FiscalDocumentLogoSnapshotTest(FiscalLogoBase):
    def test_a_shop_without_a_logo_prints_as_before(self):
        document = self.issue()

        self.assertEqual(document.logo_storage_key, '')
        self.assertEqual(embedded_images(generate_fiscal_pdf(document)), 1)  # sólo el QR
        self.assertEqual(embedded_images(generate_fiscal_ticket_pdf(document)), 1)

    def test_the_document_freezes_the_logo_it_was_issued_with(self):
        self.set_logo(self.upload())

        document = self.issue()

        self.assertTrue(document.logo_storage_key)
        self.assertRegex(
            document.logo_storage_key,
            rf'^companies/{self.company.pk}/fiscal/logos/[0-9a-f]{{64}}\.png$')
        stored = storage.open_stream(document.logo_storage_key).read()
        self.assertEqual(hashlib.sha256(stored).hexdigest(), document.logo_sha256)
        # El QR y el logotipo, cada uno una vez: sin copia desplazada debajo,
        # que es como se fabrica una sombra en un PDF.
        self.assertEqual(embedded_images(generate_fiscal_pdf(document)), 2)
        self.assertEqual(embedded_images(generate_fiscal_ticket_pdf(document)), 2)

    def test_a_reprint_keeps_the_old_logo_after_the_shop_changes_it(self):
        self.set_logo(self.upload(logo_png(colour=(200, 30, 30, 255))))
        old = self.issue()
        old_pdf = generate_fiscal_pdf(old)

        self.set_logo(self.upload(logo_png(colour=(30, 30, 200, 255))))
        new = self.issue()

        self.assertNotEqual(old.logo_sha256, new.logo_sha256)
        old.refresh_from_db()
        red = Image.open(io.BytesIO(fiscal_logo.load(old))).convert('RGBA').getpixel((120, 40))
        blue = Image.open(io.BytesIO(fiscal_logo.load(new))).convert('RGBA').getpixel((120, 40))
        self.assertEqual((red[:3], blue[:3]), ((200, 30, 30), (30, 30, 200)))
        self.assertEqual(embedded_images(generate_fiscal_pdf(old)), embedded_images(old_pdf))

    def test_a_reprint_keeps_the_logo_after_the_shop_removes_it(self):
        url = self.upload()
        self.set_logo(url)
        document = self.issue()

        # La tienda quita su logotipo y la imagen original desaparece del almacén.
        self.set_logo('')
        with self.captureOnCommitCallbacks(execute=True):
            for image in StorefrontImage.objects.filter(company=self.company):
                storefront_media.delete_unplaced(image)
        self.assertFalse(StorefrontImage.objects.filter(company=self.company).exists())

        document.refresh_from_db()
        self.assertIsNotNone(fiscal_logo.load(document))
        self.assertEqual(embedded_images(generate_fiscal_pdf(document)), 2)
        self.assertEqual(self.issue().logo_storage_key, '')

    def test_the_same_logo_is_stored_once_for_many_documents(self):
        self.set_logo(self.upload())

        first, second = self.issue(), self.issue()

        self.assertEqual(first.logo_storage_key, second.logo_storage_key)

    def test_a_missing_snapshot_does_not_stop_the_reprint(self):
        self.set_logo(self.upload())
        document = self.issue()
        storage.delete_quietly(document.logo_storage_key)

        # Un archivo perdido no puede dejar a nadie sin su comprobante.
        self.assertIsNone(fiscal_logo.load(document))
        self.assertEqual(embedded_images(generate_fiscal_pdf(document)), 1)
