"""
CATALOG-RICH-DESC-01 — descriptions of models and products are Markdown text.

The server keeps what the editor sends, character for character (marks, line breaks, emojis, even HTML, which
the storefront shows as text), refuses a description that is too long instead of cutting it, and keeps every
existing rule: who may write, and which company a model belongs to.
"""
from django.test import TestCase, override_settings

from store.description_text import DESCRIPTION_MAX_LENGTH
from store.models import Product, ProductModel
from store.test_device_commerce import DeviceShop, _client
from store.tests import _p2d_member, _p3_company

SAMPLE = """**Apple iPhone 13 Pro Max**

El iPhone 13 Pro Max combina potencia, elegancia y fotografía profesional.

## Características principales

- Pantalla Super Retina XDR OLED.
- Tecnología ProMotion de 120 Hz.
- Face ID 😎 · ñandú «comillas» — 100 %

## Capacidades

1. 128 GB
2. 256 GB

[Más información](https://example.com/iphone)"""

HOSTILE = ('<script>alert(1)</script><img src=x onerror="alert(2)">'
           '[clic](javascript:alert(3)) <iframe src="https://evil.example"></iframe>')


@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class ModelDescriptionTest(DeviceShop, TestCase):
    def setUp(self):
        self.build()
        self.receive(self.new128, 1, cost='2500.00')
        self.api = _client(self.staff)

    def patch(self, description, api=None):
        return (api or self.api).patch(f'/api/admin/catalog/models/{self.model.pk}/', {'description': description},
                                       format='json')

    def test_markdown_is_kept_as_written_and_read_back_by_admin_and_store(self):
        res = self.patch(SAMPLE)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['description'], SAMPLE)
        self.model.refresh_from_db()
        self.assertEqual(self.model.description, SAMPLE)
        self.assertEqual(self.api.get(f'/api/admin/catalog/models/{self.model.pk}/').json()['description'], SAMPLE)
        public = _client(host=self.host).get(f'/api/catalog/models/{self.model.slug}/')
        self.assertEqual(public.status_code, 200, public.content)
        self.assertEqual(public.json()['description'], SAMPLE)

    def test_html_is_stored_as_text_not_interpreted_nor_dropped(self):
        # The storefront renders it as text (React escapes it); the server neither runs nor silently edits it.
        self.assertEqual(self.patch(HOSTILE).status_code, 200)
        self.model.refresh_from_db()
        self.assertEqual(self.model.description, HOSTILE)

    def test_a_long_description_is_no_longer_cut_at_5000(self):
        text = ('Párrafo con **negrita**. ' * 400).strip()
        self.assertGreater(len(text), 5000)
        self.assertEqual(self.patch(text).status_code, 200)
        self.model.refresh_from_db()
        self.assertEqual(self.model.description, text)

    def test_too_long_is_refused_with_its_field_and_nothing_changes(self):
        self.patch('Antes')
        res = self.patch('x' * (DESCRIPTION_MAX_LENGTH + 1))
        self.assertEqual(res.status_code, 400, res.content)
        body = res.json()
        self.assertEqual(body.get('field'), 'description')
        self.assertIn(str(DESCRIPTION_MAX_LENGTH), str(body))
        self.model.refresh_from_db()
        self.assertEqual(self.model.description, 'Antes')
        self.assertEqual(self.patch('x' * DESCRIPTION_MAX_LENGTH).status_code, 200)

    def test_creation_follows_the_same_rule(self):
        res = self.api.post('/api/admin/catalog/models/', {'name': 'Tableta Z', 'description': SAMPLE}, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(ProductModel.objects.get(pk=res.json()['id']).description, SAMPLE)
        res = self.api.post('/api/admin/catalog/models/', {'name': 'Tableta W', 'description': 'y' * (DESCRIPTION_MAX_LENGTH + 1)},
                            format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertFalse(ProductModel.objects.filter(company=self.company, name='Tableta W').exists())

    def test_line_endings_are_unified_and_nul_is_removed(self):
        self.assertEqual(self.patch('Uno\r\n\r\n- dos\rtres\x00').status_code, 200)
        self.model.refresh_from_db()
        self.assertEqual(self.model.description, 'Uno\n\n- dos\ntres')

    def test_without_products_manage_nothing_is_written(self):
        viewer, _ = _p2d_member(self.company, 'rd_lector', ['company.view', 'products.view'])
        self.assertEqual(self.patch(SAMPLE, api=_client(viewer)).status_code, 403)
        self.model.refresh_from_db()
        self.assertNotEqual(self.model.description, SAMPLE)

    def test_another_company_cannot_read_or_write_it(self):
        other = _p3_company('rd-ajena', 'Ajena')
        stranger, _ = _p2d_member(other, 'rd_ajeno', ['company.view', 'products.view', 'products.manage'])
        api = _client(stranger)
        self.assertEqual(api.get(f'/api/admin/catalog/models/{self.model.pk}/').status_code, 404)
        self.assertEqual(self.patch(SAMPLE, api=api).status_code, 404)
        self.model.refresh_from_db()
        self.assertNotEqual(self.model.description, SAMPLE)


class ProductDescriptionTest(DeviceShop, TestCase):
    def setUp(self):
        self.build()
        self.api = _client(self.staff)
        self.url = f'/api/admin/products/{self.new128.pk}/'

    def test_markdown_is_kept_and_the_limit_is_the_same(self):
        res = self.api.patch(self.url, {'description': SAMPLE}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(Product.objects.get(pk=self.new128.pk).description, SAMPLE)
        res = self.api.patch(self.url, {'description': 'z' * (DESCRIPTION_MAX_LENGTH + 1)}, format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn('description', res.json())
        self.assertEqual(Product.objects.get(pk=self.new128.pk).description, SAMPLE)

    def test_another_company_cannot_write_it(self):
        other = _p3_company('rd-ajena-p', 'Ajena P')
        stranger, _ = _p2d_member(other, 'rd_ajeno_p', ['company.view', 'products.view', 'products.manage'])
        res = _client(stranger).patch(self.url, {'description': HOSTILE}, format='json')
        self.assertEqual(res.status_code, 404, res.content)
        self.assertNotEqual(Product.objects.get(pk=self.new128.pk).description, HOSTILE)
