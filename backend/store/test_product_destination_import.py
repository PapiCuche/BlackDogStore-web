"""
PRODUCT-DESTINATION-01 · la carga masiva dice a dónde va cada producto.

Un producto puede estar en el catálogo de la empresa y NO venderse por la web:
un repuesto, un equipo que sólo se despacha en tienda. Eso no es «inactivo»
—se vende en caja, se cuenta en inventario, se usa en el taller—, es otro dato:
`Product.is_published_online`.

Lo que estas pruebas fijan del importador y de su plantilla:

  * la columna «Destino» —y «Destino (pendiente web)», como la escribió quien
    preparó la plantilla— se reconoce sola, se valida y se ve en la vista previa;
  * vacía no publica nada: un producto nuevo nace «sólo stock interno» (decisión
    del propietario, 2026-10-13) y uno existente se queda como estaba. Publicar
    es siempre algo que la fila dice;
  * la plantilla trae las categorías DE ESA EMPRESA y sólo las suyas, y sus
    hojas de ayuda nunca se importan;
  * una categoría se valida contra las de la empresa. Ninguna está escrita en
    el código.

Quién ve un producto «sólo stock interno» está en `test_product_visibility.py`.
"""
import io
import json

import openpyxl
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from store import import_exports
from store.models import AdminAuditLog, BulkImportRow, Category, CompanySettings, Product, ProductBarcode
from store.tests import _p2d_member, _p3_company

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
PREVIEW = '/api/admin/products/import/preview/'
TEMPLATE = '/api/admin/products/import/template/'
INSPECT = '/api/admin/imports/inspect/'
ONLINE, INTERNAL = 'Publicar en e-commerce', 'Solo stock interno'
HEADERS = ['Código de barras', 'Código', 'Nombre', 'Descripción', 'Precio de venta', 'Categoría',
           'Imagen principal', 'Imágenes']


def workbook_bytes(rows, *, destination_header='Destino', headers=None, extra_sheets=()):
    """A product file as somebody would fill it: header row, then `rows`."""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = 'Productos'
    sheet.append(list(headers) if headers is not None else [*HEADERS, destination_header])
    for row in rows:
        sheet.append(list(row))
    for name, lines in extra_sheets:
        other = workbook.create_sheet(name)
        for line in lines:
            other.append(list(line))
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()


def product_row(code, name, *, price=100, category=None, destination=None):
    return [None, code, name, None, price, category, None, None, destination]


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('destino-tienda', 'Tienda Destino')
        self.manager, _ = _p2d_member(
            self.company, 'destino_gestor', ['company.view', 'products.view', 'products.manage'])
        self.client = APIClient()
        self.client.force_authenticate(user=self.manager)
        self.phones = Category.objects.create(company=self.company, name='Teléfonos', slug='telefonos')
        self.parts = Category.objects.create(company=self.company, name='Repuestos', slug='repuestos')

    def preview(self, data, *, company=None, client=None, options=None, **fields):
        return (client or self.client).post(
            f'{PREVIEW}?company={(company or self.company).pk}',
            {'file': SimpleUploadedFile('productos.xlsx', data, content_type=XLSX),
             'options': json.dumps(options or {}), **fields},
            format='multipart',
        )

    def apply(self, job_id, *, company=None, client=None):
        return (client or self.client).post(
            f'/api/admin/products/import/{job_id}/apply/?company={(company or self.company).pk}')

    def imported(self, rows, **kwargs):
        body = self.preview(workbook_bytes(rows, **kwargs)).json()
        self.assertEqual(body['counts']['error'], 0, body['rows'])
        res = self.apply(body['id'])
        self.assertEqual(res.status_code, 200, res.content)
        return body

    def product(self, name):
        return Product.objects.get(company=self.company, name=name)


class DestinationColumnTest(_Base):
    def test_each_row_goes_where_its_cell_says(self):
        body = self.imported([
            product_row('WEB-1', 'Funda de silicona', category='Repuestos', destination=ONLINE),
            product_row('INT-1', 'Flex de carga', destination=INTERNAL),
        ])

        shown = {row['data']['name']: row['data']['destination'] for row in body['rows'] if row['action'] == 'create'}
        self.assertEqual(shown, {'Funda de silicona': 'online', 'Flex de carga': 'internal'})
        self.assertTrue(self.product('Funda de silicona').is_published_online)
        self.assertFalse(self.product('Flex de carga').is_published_online)
        # Otro dato que «activo»: lo de sólo stock interno sigue siendo un producto en uso.
        self.assertTrue(self.product('Flex de carga').is_active)

    def test_the_header_is_recognised_as_the_template_wrote_it_and_as_it_is_now(self):
        for header in ('Destino', 'Destino (pendiente web)', 'DESTINO', ' destino  (Pendiente Web) '):
            with self.subTest(header):
                body = self.preview(workbook_bytes(
                    [product_row(f'CODH-{len(header)}', f'Producto {header.strip()}', destination=INTERNAL)],
                    destination_header=header)).json()
                self.assertEqual(body['counts'], {**body['counts'], 'create': 1, 'error': 0}, body['rows'])
                self.assertEqual(body['rows'][0]['data']['destination'], 'internal')

    def test_it_is_read_as_people_write_it(self):
        for position, (written, meaning) in enumerate((
            ('Publicar en e-commerce', 'online'), ('publicar en e-commerce', 'online'), (' PUBLICAR EN E-COMMERCE ', 'online'),
            ('Publicar en ecommerce', 'online'), ('E-commerce', 'online'),
            ('Solo stock interno', 'internal'), ('Sólo stock interno', 'internal'), ('SOLO STOCK INTERNO', 'internal'),
            ('  solo   stock  interno ', 'internal'), ('Stock interno', 'internal'),
        )):
            with self.subTest(written):
                body = self.preview(workbook_bytes(
                    [product_row(f'CODW-{position}', f'Producto {position}', category='Repuestos', destination=written)])).json()
                self.assertEqual(body['counts']['error'], 0, body['rows'])
                self.assertEqual(body['rows'][0]['data']['destination'], meaning)

    def test_a_value_that_is_neither_is_an_error_that_names_both(self):
        for written in ('Quizás', 'Sí', 'No', '1', 'interno o web'):
            with self.subTest(written):
                res = self.preview(workbook_bytes([product_row('CODX-1', 'Producto dudoso', destination=written)]))
                body = res.json()
                self.assertEqual(body['counts']['error'], 1, body['rows'])
                message = ' '.join(body['rows'][0]['errors'])
                self.assertIn(ONLINE, message)
                self.assertIn(INTERNAL, message)
                self.assertIn(written, message)
                self.assertIn(self.apply(body['id']).status_code, (400, 409))        # nothing with an error is applied
        self.assertFalse(Product.objects.filter(company=self.company).exists())

    def test_an_empty_cell_leaves_what_is_new_internal_and_leaves_alone_what_exists(self):
        self.imported([product_row('CODE-1', 'Cargador', destination=INTERNAL)])
        self.assertFalse(self.product('Cargador').is_published_online)

        body = self.imported([
            product_row('CODE-1', 'Cargador', price=120),          # existe, celda vacía
            product_row('CODE-2', 'Cable'),                        # nuevo, celda vacía
        ])

        by_name = {row['data']['name']: row for row in body['rows']}
        self.assertEqual(by_name['Cargador']['action'], 'update')
        self.assertEqual(by_name['Cargador']['data']['destination'], '')          # no dice nada: no cambia nada
        self.assertEqual(by_name['Cable']['data']['destination'], 'internal')     # lo que va a pasar, a la vista
        self.assertFalse(self.product('Cargador').is_published_online)
        self.assertEqual(self.product('Cargador').price, 120)
        self.assertFalse(self.product('Cable').is_published_online)
        self.assertTrue(self.product('Cable').is_active)                          # interno no es inactivo

    def test_an_empty_cell_never_takes_an_existing_product_off_the_web(self):
        self.imported([product_row('CODE-9', 'Parlante', category='Repuestos', destination=ONLINE)])
        self.assertTrue(self.product('Parlante').is_published_online)

        body = self.imported([product_row('CODE-9', 'Parlante', price=130)])

        self.assertEqual(body['rows'][0]['data']['destination'], '')
        self.assertTrue(self.product('Parlante').is_published_online)

    def test_a_new_product_is_published_only_when_its_row_says_so(self):
        body = self.imported([
            product_row('NEW-WEB', 'Nuevo a la web', category='Repuestos', destination=ONLINE),
            product_row('NEW-INT', 'Nuevo interno', category='Repuestos', destination=INTERNAL),
            product_row('NEW-NIL', 'Nuevo sin decir', category='Repuestos'),
        ])
        self.assertEqual(body['counts']['create'], 3)
        self.assertEqual(
            {p.name: p.is_published_online for p in Product.objects.filter(company=self.company)},
            {'Nuevo a la web': True, 'Nuevo interno': False, 'Nuevo sin decir': False})

    def test_an_explicit_cell_changes_an_existing_product_and_says_so(self):
        self.imported([product_row('CODC-1', 'Batería', category='Repuestos', destination=ONLINE)])

        body = self.imported([product_row('CODC-1', 'Batería', destination=INTERNAL)])
        self.assertEqual(body['rows'][0]['action'], 'update')
        self.assertFalse(self.product('Batería').is_published_online)

        body = self.imported([product_row('CODC-1', 'Batería', category='Repuestos', destination=ONLINE)])
        self.assertTrue(self.product('Batería').is_published_online)

    def test_a_file_from_before_the_column_imports_as_it_did(self):
        Product.objects.create(company=self.company, name='Pantalla', slug='pantalla', price=50, is_published_online=False)
        old_file = workbook_bytes(
            [[None, 'CODV-1', 'Pantalla', None, 60, None, None, None], [None, 'CODV-2', 'Mica', None, 10, None, None, None]],
            headers=HEADERS)

        body = self.preview(old_file).json()
        self.assertEqual(body['counts']['error'], 0, body['rows'])
        self.assertEqual(self.apply(body['id']).status_code, 200)

        self.assertFalse(self.product('Pantalla').is_published_online)             # nadie lo pidió: no se toca
        self.assertEqual(self.product('Pantalla').price, 60)
        self.assertFalse(self.product('Mica').is_published_online)                 # nuevo y sin columna: interno

    def test_a_preview_made_before_this_version_still_applies(self):
        # Eligible to publish (it has a category): only the missing key can keep it internal.
        body = self.preview(workbook_bytes([product_row('CODP-1', 'Soporte', category='Repuestos', destination=INTERNAL)])).json()
        for row in BulkImportRow.objects.filter(job_id=body['id']):
            row.normalized_data.pop('destination', None)
            row.save(update_fields=['normalized_data'])

        self.assertEqual(self.apply(body['id']).status_code, 200)
        # Sin la clave nadie pidió publicarlo: nace interno.
        self.assertFalse(self.product('Soporte').is_published_online)

    def test_a_preview_made_by_the_previous_version_applies_as_it_said(self):
        """The release before this one wrote «online» on a new row with an empty cell, and showed it."""
        body = self.preview(workbook_bytes([product_row('CODP-2', 'Trípode', category='Repuestos')])).json()
        for row in BulkImportRow.objects.filter(job_id=body['id']):
            row.normalized_data['destination'] = 'online'
            row.save(update_fields=['normalized_data'])

        self.assertEqual(self.apply(body['id']).status_code, 200)
        self.assertTrue(self.product('Trípode').is_published_online)

    def test_the_column_can_be_assigned_by_hand(self):
        data = workbook_bytes([product_row('CODM-1', 'Lápiz', destination=INTERNAL)], destination_header='A dónde va')
        body = self.preview(data, header_row='1', mapping=json.dumps({'code': 1, 'name': 2, 'price': 4, 'destination': 8})).json()
        self.assertEqual(body['counts']['error'], 0, body)
        self.assertEqual(body['rows'][0]['data']['destination'], 'internal')


class PublishingWhatIsNotEligibleIsRefusedRowByRowTest(_Base):
    """
    INVENTORY-UNIFIED-01, owner's decision D2 (2026-10-13). The load and the
    bulk action obey ONE rule (`product_publication`): a row that asks for the
    web for a product that is inactive, has no price or has no category does
    not publish it — and is NOT an error. The product is created or updated as
    the row says and stays (or is born) «solo stock interno»; the row carries
    the reasons, and the preview and the result count the refusals. A picture
    is asked only by the company that configured it. Loading as internal stock
    is always allowed.
    """

    def hidden(self, name, code, **fields):
        product = Product.objects.create(
            company=self.company, name=name, slug=code.lower(), is_published_online=False,
            **{'price': 59, 'category': self.parts, 'image_url': 'https://imagenes.example.test/a.png', **fields})
        ProductBarcode.objects.create(company=self.company, product=product, code=code)
        return product

    def require_picture(self):
        CompanySettings.objects.update_or_create(company=self.company, defaults={'publication_requires_image': True})

    def warned(self, body):
        return {row['data']['name']: ' '.join(row['warnings']) for row in body['rows']}

    def test_a_row_that_asks_for_the_web_for_what_is_not_eligible_is_not_an_error_and_does_not_publish(self):
        self.hidden('Sin nada', 'BARE-1', price=0, category=None, image_url='')
        self.hidden('Apagado', 'OFFF-1', is_active=False)
        self.hidden('Listo', 'REDY-1')

        body = self.preview(workbook_bytes([
            product_row('BARE-1', 'Sin nada', price=None, destination=ONLINE),
            product_row('OFFF-1', 'Apagado', price=75, category='Repuestos', destination=ONLINE),
            product_row('REDY-1', 'Listo', price=59, category='Repuestos', destination=ONLINE),
        ])).json()

        self.assertEqual(body['counts']['error'], 0, body['rows'])
        warned = self.warned(body)
        self.assertIn('No se publica en e-commerce:', warned['Sin nada'])
        for reason in ('No tiene precio de venta.', 'No tiene categoría.'):
            self.assertIn(reason, warned['Sin nada'])
        self.assertNotIn('No tiene imagen.', warned['Sin nada'])            # nobody configured that rule
        self.assertIn('No se publica en e-commerce: El producto está inactivo.', warned['Apagado'])
        self.assertEqual(warned['Listo'], '')
        self.assertEqual(body['summary']['publication_refused'], 2)
        self.assertEqual(body['summary']['destination_changes'], {'to_web': 1, 'to_internal': 0})
        # The row shows what WILL happen: nothing about its destination changes.
        by_name = {row['data']['name']: row['data']['destination'] for row in body['rows']}
        self.assertEqual(by_name, {'Sin nada': '', 'Apagado': '', 'Listo': 'online'})

        applied = self.apply(body['id']).json()['summary']['applied']
        self.assertEqual((applied['destination_changed'], applied['publication_refused']), (1, 2))
        self.assertFalse(self.product('Sin nada').is_published_online)
        self.assertFalse(self.product('Apagado').is_published_online)
        self.assertTrue(self.product('Listo').is_published_online)
        # The rest of the row was applied as it says.
        self.assertEqual(self.product('Apagado').price, 75)
        self.assertFalse(self.product('Apagado').is_active)

    def test_a_new_product_that_is_not_eligible_is_created_internal_with_the_reason(self):
        body = self.preview(workbook_bytes([
            product_row('NEWW-1', 'Nuevo sin categoría', price=30, destination=ONLINE),
            product_row('NEWW-2', 'Nuevo gratis', price=0, category='Repuestos', destination=ONLINE),
            product_row('NEWW-3', 'Nuevo listo', price=30, category='Repuestos', destination=ONLINE),
        ])).json()

        self.assertEqual((body['counts']['create'], body['counts']['error']), (3, 0), body['rows'])
        warned = self.warned(body)
        self.assertIn('No se publica en e-commerce: No tiene categoría.', warned['Nuevo sin categoría'])
        self.assertIn('No se publica en e-commerce: No tiene precio de venta.', warned['Nuevo gratis'])
        self.assertEqual(warned['Nuevo listo'], '')
        self.assertEqual(
            {row['data']['name']: row['data']['destination'] for row in body['rows']},
            {'Nuevo sin categoría': 'internal', 'Nuevo gratis': 'internal', 'Nuevo listo': 'online'})
        self.assertEqual(body['summary']['publication_refused'], 2)

        self.assertEqual(self.apply(body['id']).json()['summary']['applied']['publication_refused'], 2)
        self.assertEqual(
            {p.name: p.is_published_online for p in Product.objects.filter(company=self.company)},
            {'Nuevo sin categoría': False, 'Nuevo gratis': False, 'Nuevo listo': True})

    def test_a_picture_is_asked_only_when_the_company_configured_it(self):
        self.hidden('Sin foto', 'NOPI-1', image_url='')
        rows = [
            product_row('NOPI-1', 'Sin foto', destination=ONLINE),
            product_row('NOPI-2', 'Nuevo sin foto', price=20, category='Repuestos', destination=ONLINE),
        ]
        free = self.preview(workbook_bytes(rows)).json()
        self.assertEqual((free['summary']['publication_refused'], self.warned(free)['Sin foto']), (0, ''))

        self.require_picture()
        asked = self.preview(workbook_bytes(rows)).json()
        self.assertEqual(asked['counts']['error'], 0, asked['rows'])
        self.assertEqual(asked['summary']['publication_refused'], 2)
        for name in ('Sin foto', 'Nuevo sin foto'):
            self.assertIn('No se publica en e-commerce: No tiene imagen.', self.warned(asked)[name])
        self.apply(asked['id'])
        self.assertFalse(self.product('Sin foto').is_published_online)
        self.assertFalse(self.product('Nuevo sin foto').is_published_online)

    def test_what_the_same_row_brings_counts(self):
        """The state the product will be LEFT in: a price or a category in the row is not missing."""
        self.hidden('Sin nada', 'BARE-1', price=0, category=None, image_url='')
        body = self.imported([product_row('BARE-1', 'Sin nada', price=45, category='Repuestos', destination=ONLINE)])
        self.assertEqual((body['summary']['publication_refused'], body['rows'][0]['warnings']), (0, []))
        self.assertTrue(self.product('Sin nada').is_published_online)

    def test_a_category_the_load_will_create_counts_too(self):
        preview = self.preview(workbook_bytes(
            [product_row('CATN-1', 'Con categoría nueva', price=45, category='Estrenos', destination=ONLINE)]),
            options={'create_missing_categories': True}).json()
        self.assertEqual((preview['counts']['error'], preview['summary']['publication_refused']), (0, 0), preview['rows'])
        self.assertEqual(self.apply(preview['id']).status_code, 200)
        self.assertTrue(self.product('Con categoría nueva').is_published_online)

    def test_loading_as_internal_stock_is_always_allowed(self):
        body = self.imported([
            product_row('INTR-1', 'Repuesto sin categoría', price=0, destination=INTERNAL),
            product_row('INTR-2', 'Repuesto sin decir', price=0),
        ])
        self.assertEqual((body['counts']['create'], body['summary']['publication_refused']), (2, 0))
        self.assertEqual([row['warnings'] for row in body['rows']], [[], []])

    def test_a_product_already_on_the_web_is_not_taken_off_by_this_rule(self):
        published = self.hidden('Ya en la web', 'WEBB-1', category=None)
        Product.objects.filter(pk=published.pk).update(is_published_online=True)

        body = self.imported([product_row('WEBB-1', 'Ya en la web', price=61, destination=ONLINE)])

        self.assertEqual((body['summary']['publication_refused'], body['rows'][0]['warnings']), (0, []))
        self.assertEqual(body['summary']['destination_changes'], {'to_web': 0, 'to_internal': 0})
        self.assertTrue(self.product('Ya en la web').is_published_online)
        self.assertEqual(self.product('Ya en la web').price, 61)

    def test_the_preview_counts_how_many_products_change_destination(self):
        self.hidden('A la web 1', 'TOWB-1')
        self.hidden('A la web 2', 'TOWB-2', image_url='')
        leaving = self.hidden('Sale de la web', 'TOIN-1')
        Product.objects.filter(pk=leaving.pk).update(is_published_online=True)
        self.hidden('Igual', 'SAME-1')

        body = self.preview(workbook_bytes([
            product_row('TOWB-1', 'A la web 1', destination=ONLINE),
            product_row('TOWB-2', 'A la web 2', destination=ONLINE),
            product_row('TOIN-1', 'Sale de la web', destination=INTERNAL),
            product_row('SAME-1', 'Igual', destination=INTERNAL),
            product_row('NUEV-1', 'Nuevo', price=10, destination=INTERNAL),
        ])).json()

        self.assertEqual(body['summary']['destination_changes'], {'to_web': 2, 'to_internal': 1})
        # Nothing has moved yet.
        self.assertFalse(self.product('A la web 1').is_published_online)
        self.assertTrue(self.product('Sale de la web').is_published_online)
        self.assertEqual(self.apply(body['id']).json()['summary']['applied']['destination_changed'], 3)

    def test_a_row_that_will_not_be_applied_counts_nothing_and_warns_of_nothing(self):
        self.hidden('Sin categoría', 'SKIP-1', category=None)
        rows = [product_row('SKIP-1', 'Sin categoría', destination=ONLINE)]
        skipped = self.preview(workbook_bytes(rows), options={'mode': 'create_only'}).json()
        self.assertEqual(skipped['summary']['destination_changes'], {'to_web': 0, 'to_internal': 0})
        self.assertEqual(skipped['summary']['publication_refused'], 0)
        self.assertNotIn('No se publica', ' '.join(skipped['rows'][0]['warnings']))
        wrong = self.preview(workbook_bytes(
            [product_row('SKIP-1', 'Sin categoría', price='gratis', destination=ONLINE)])).json()
        self.assertEqual(
            (wrong['counts']['error'], wrong['summary']['destination_changes']['to_web'],
             wrong['summary']['publication_refused']), (1, 0, 0))

    def test_a_file_without_the_column_changes_no_destination_and_says_zero(self):
        self.hidden('Igual', 'SAME-1')
        body = self.preview(workbook_bytes([['', 'SAME-1', 'Igual', '', 59, 'Repuestos', '', '']], headers=HEADERS)).json()
        self.assertEqual(body['summary']['destination_changes'], {'to_web': 0, 'to_internal': 0})
        self.assertEqual(body['summary']['publication_refused'], 0)

    def test_a_stale_file_does_not_publish_again_what_was_deactivated_since(self):
        """The reviewer's case: downloaded, then hidden and deactivated, then reloaded."""
        product = self.hidden('Funda', 'FUND-1')
        Product.objects.filter(pk=product.pk).update(is_published_online=True)
        stale = workbook_bytes([product_row('FUND-1', 'Funda', price=59, category='Repuestos', destination=ONLINE)])
        Product.objects.filter(pk=product.pk).update(is_published_online=False, is_active=False)

        body = self.preview(stale).json()

        self.assertEqual(body['summary']['destination_changes'], {'to_web': 0, 'to_internal': 0})
        self.assertEqual(body['summary']['publication_refused'], 1)
        self.assertIn('El producto está inactivo.', ' '.join(body['rows'][0]['warnings']))
        self.apply(body['id'])
        self.assertFalse(self.product('Funda').is_published_online)

    def test_what_stopped_being_eligible_after_the_preview_is_not_published_either(self):
        """A preview is a forecast: apply asks the rule again, with the product in hand."""
        product = self.hidden('Funda', 'FUND-2')
        body = self.preview(workbook_bytes([product_row('FUND-2', 'Funda', price=64, destination=ONLINE)])).json()
        self.assertEqual((body['summary']['publication_refused'], body['rows'][0]['data']['destination']), (0, 'online'))
        Product.objects.filter(pk=product.pk).update(is_active=False)

        response = self.apply(body['id'])

        self.assertEqual(response.status_code, 200, response.content)
        applied = response.json()['summary']['applied']
        self.assertEqual((applied['destination_changed'], applied['publication_refused']), (0, 1))
        self.assertFalse(self.product('Funda').is_published_online)
        self.assertEqual(self.product('Funda').price, 64)
        self.assertFalse(AdminAuditLog.objects.filter(action='product_destination_changed').exists())

    def test_a_new_product_that_stopped_being_eligible_after_the_preview_is_created_internal(self):
        """The company asked for a picture between the preview and apply: the rule of apply is the one that counts."""
        body = self.preview(workbook_bytes(
            [product_row('LATE-1', 'Nuevo sin foto', price=20, category='Repuestos', destination=ONLINE)])).json()
        self.assertEqual((body['summary']['publication_refused'], body['rows'][0]['data']['destination']), (0, 'online'))
        self.require_picture()

        applied = self.apply(body['id']).json()['summary']['applied']

        self.assertEqual((applied['created'], applied['publication_refused']), (1, 1))
        self.assertFalse(self.product('Nuevo sin foto').is_published_online)

    def test_a_row_with_this_warning_is_listed_with_the_other_warned_rows(self):
        self.hidden('Sin categoría', 'WARN-1', category=None)
        rows = [product_row(f'FINE-{n}', f'Bien {n}', price=10) for n in range(3)]
        body = self.preview(workbook_bytes([*rows, product_row('WARN-1', 'Sin categoría', destination=ONLINE)])).json()
        self.assertEqual(body['rows'][0]['data']['name'], 'Sin categoría')


class StateColumnTest(_Base):
    """
    INVENTORY-UNIFIED-01, owner's decision D3 (2026-10-13). The file to reload
    carries «Estado» (Activo / Inactivo) so that inactive products can travel.
    The column is optional and a load NEVER activates or deactivates a product
    that exists: it only decides how a NEW product is born.
    """

    HEADERS = [*HEADERS, 'Destino', 'Estado']

    def row(self, code, name, state, *, price=100, category='Repuestos', destination=None):
        return [None, code, name, None, price, category, None, None, destination, state]

    def book(self, rows):
        return workbook_bytes(rows, headers=self.HEADERS)

    def test_a_new_product_marked_inactive_is_born_inactive_and_internal(self):
        body = self.preview(self.book([
            self.row('EST-1', 'Descatalogado', 'Inactivo', destination=ONLINE),
            self.row('EST-2', 'Descatalogado sin destino', 'inactivo'),
            self.row('EST-3', 'Vigente', 'Activo', destination=ONLINE),
            self.row('EST-4', 'Sin decir', None, destination=ONLINE),
        ])).json()

        self.assertEqual((body['counts']['create'], body['counts']['error']), (4, 0), body['rows'])
        warned = {row['data']['name']: ' '.join(row['warnings']) for row in body['rows']}
        self.assertIn('No se publica en e-commerce: El producto está inactivo.', warned['Descatalogado'])
        self.assertEqual(self.apply(body['id']).status_code, 200)
        self.assertEqual(
            {p.name: (p.is_active, p.is_published_online) for p in Product.objects.filter(company=self.company)},
            {'Descatalogado': (False, False), 'Descatalogado sin destino': (False, False),
             'Vigente': (True, True), 'Sin decir': (True, True)})

    def test_it_never_activates_or_deactivates_a_product_that_exists_and_says_so(self):
        off = Product.objects.create(company=self.company, name='Apagado', slug='apagado', price=10, is_active=False)
        on = Product.objects.create(company=self.company, name='Encendido', slug='encendido', price=10)
        same = Product.objects.create(company=self.company, name='Igual', slug='igual', price=10, is_active=False)
        for product, code in ((off, 'ESTX-1'), (on, 'ESTX-2'), (same, 'ESTX-3')):
            ProductBarcode.objects.create(company=self.company, product=product, code=code)

        body = self.preview(self.book([
            self.row('ESTX-1', 'Apagado', 'Activo', price=11),
            self.row('ESTX-2', 'Encendido', 'Inactivo', price=12),
            self.row('ESTX-3', 'Igual', 'Inactivo', price=13),
        ])).json()

        self.assertEqual((body['counts']['update'], body['counts']['error']), (3, 0), body['rows'])
        warned = {row['data']['name']: ' '.join(row['warnings']) for row in body['rows']}
        self.assertIn('El archivo dice «Activo» y el producto está inactivo', warned['Apagado'])
        self.assertIn('El archivo dice «Inactivo» y el producto está activo', warned['Encendido'])
        for name in ('Apagado', 'Encendido'):
            self.assertIn('esta carga no activa ni desactiva productos', warned[name])
        self.assertEqual(warned['Igual'], '')

        self.assertEqual(self.apply(body['id']).status_code, 200)
        self.assertEqual(
            {p.name: (p.is_active, p.price) for p in Product.objects.filter(company=self.company)},
            {'Apagado': (False, 11), 'Encendido': (True, 12), 'Igual': (False, 13)})

    def test_it_is_read_as_people_write_it_and_anything_else_is_an_error_that_names_both(self):
        for position, (written, active) in enumerate((
            ('Activo', True), (' ACTIVO ', True), ('activo', True), ('Inactivo', False), ('  inactivo', False),
        )):
            with self.subTest(written):
                body = self.preview(self.book([self.row(f'ESTW-{position}', f'Producto {position}', written)])).json()
                self.assertEqual(body['counts']['error'], 0, body['rows'])
                self.assertIs(body['rows'][0]['data']['is_active'], active)
        body = self.preview(self.book([self.row('ESTW-9', 'Raro', 'Descontinuado')])).json()
        self.assertEqual(body['counts']['error'], 1)
        self.assertIn('«Descontinuado» no es un estado', body['rows'][0]['errors'][0])
        self.assertIn('«Activo» o «Inactivo»', body['rows'][0]['errors'][0])

    def test_a_file_without_the_column_loads_as_before(self):
        body = self.imported([product_row('ESTO-1', 'De antes', category='Repuestos', destination=ONLINE)])
        self.assertEqual(body['counts']['create'], 1)
        self.assertEqual(
            (self.product('De antes').is_active, self.product('De antes').is_published_online), (True, True))


class IdentifierColumnTest(_Base):
    """
    INVENTORY-UNIFIED-01, owner's decision D5 (2026-10-13). A product without a
    code had only its NAME to be recognised by, so renaming it and reloading
    its file created it again. The file to reload carries «Identificador»
    (`<company id>-<product id>`): when it names a product of THIS company it
    decides the match. It is optional; another company's, or nobody's, is
    ignored with a warning and the row falls back to its codes; when it and a
    code point at two different products the row is an error — nothing guessed.
    """

    HEADERS = [*HEADERS, 'Destino', 'Estado', 'Identificador']

    def setUp(self):
        super().setUp()
        self.other = _p3_company('destino-vecina', 'Tienda Vecina')
        self.loose = Product.objects.create(
            company=self.company, name='Mica suelta', slug='mica-suelta', price=12, category=self.parts)
        self.coded = Product.objects.create(
            company=self.company, name='Cargador', slug='cargador', price=40, category=self.parts)
        ProductBarcode.objects.create(company=self.company, product=self.coded, code='CARG-1')
        self.foreign = Product.objects.create(company=self.other, name='Mica ajena', slug='mica-ajena', price=9)

    def ident(self, product, company=None):
        return f'{(company or self.company).pk}-{product.pk}'

    def row(self, code, name, identifier, *, price=12):
        return [None, code, name, None, price, 'Repuestos', None, None, None, None, identifier]

    def staged(self, rows):
        return self.preview(workbook_bytes(rows, headers=self.HEADERS)).json()

    def test_it_decides_the_match_so_a_new_name_is_an_update(self):
        body = self.staged([self.row(None, 'Mica templada 9H', self.ident(self.loose))])

        self.assertEqual((body['counts']['update'], body['counts']['create'], body['counts']['error']), (1, 0, 0), body['rows'])
        self.assertEqual(
            (body['rows'][0]['data']['product_id'], body['rows'][0]['data']['matched_by']), (self.loose.pk, 'identifier'))
        self.assertEqual(self.apply(body['id']).status_code, 200)
        self.loose.refresh_from_db()
        self.assertEqual(self.loose.name, 'Mica templada 9H')
        self.assertEqual(Product.objects.filter(company=self.company).count(), 2)

    def test_the_company_part_is_checked_even_for_an_id_the_index_holds(self):
        """Review G01: the tenant guard of the identifier, on its own, without the outer company filter."""
        from store.import_services import CatalogueIndex

        index = CatalogueIndex(self.company)
        index.product_ids.add(self.foreign.pk)                  # as if the id set ever held another's product
        self.assertIsNone(index.identified(self.ident(self.foreign, self.other)))
        self.assertIsNone(index.identified(self.ident(self.loose, self.other)))
        self.assertEqual(index.identified(self.ident(self.loose)), self.loose.pk)

    def test_it_is_read_with_spaces_around_and_as_a_number_like_text(self):
        body = self.staged([self.row(None, 'Mica nueva', f'  {self.ident(self.loose)}  ')])
        self.assertEqual(body['rows'][0]['data']['matched_by'], 'identifier')

    def test_another_companys_or_nobodys_is_ignored_with_one_same_warning_and_codes_decide(self):
        cases = {
            'another company\'s': self.ident(self.foreign, self.other),
            # Review G01: the identifier is bound to the company — this product, named with the other's id.
            'this product under another company\'s id': self.ident(self.loose, self.other),
            'this company with another\'s product': self.ident(self.foreign),
            'nobody\'s': f'{self.company.pk}-999999999',
            'not an identifier': 'abc',
            'negative': f'{self.company.pk}--5',
        }
        warnings = set()
        for label, identifier in cases.items():
            with self.subTest(label):
                body = self.staged([self.row('CARG-1', 'Cargador', identifier, price=41)])
                row = body['rows'][0]
                self.assertEqual((row['action'], row['data']['product_id'], row['data']['matched_by']),
                                 ('update', self.coded.pk, 'code'), row)
                said = [w for w in row['warnings'] if 'Identificador' in w or 'identificador' in w]
                self.assertEqual(len(said), 1, row['warnings'])
                self.assertIn('no es de un producto de esta empresa', said[0])
                warnings.add(said[0].replace(identifier, '<id>'))
        # Whether it exists somewhere else is not said: one wording for all of them.
        self.assertEqual(len(warnings), 1, warnings)
        self.foreign.refresh_from_db()
        self.assertEqual((self.foreign.name, self.foreign.price), ('Mica ajena', 9))

    def test_an_ignored_identifier_never_creates_or_touches_anything_of_the_other_company(self):
        body = self.staged([self.row(None, 'Mica ajena', self.ident(self.foreign, self.other))])
        self.assertEqual((body['counts']['create'], body['counts']['error']), (1, 0), body['rows'])
        self.apply(body['id'])
        self.assertEqual(Product.objects.filter(company=self.other).count(), 1)
        self.assertEqual(Product.objects.filter(company=self.company, name='Mica ajena').count(), 1)

    def test_when_it_and_a_code_point_at_two_products_the_row_is_an_error(self):
        body = self.staged([self.row('CARG-1', 'Mica suelta', self.ident(self.loose))])
        self.assertEqual(body['counts']['error'], 1, body['rows'])
        self.assertIn('El identificador y los códigos de esta fila apuntan a productos distintos',
                      ' '.join(body['rows'][0]['errors']))
        self.assertIn(self.apply(body['id']).status_code, (400, 409))
        self.loose.refresh_from_db()
        self.coded.refresh_from_db()
        self.assertEqual((self.loose.name, self.coded.name), ('Mica suelta', 'Cargador'))

    def test_with_a_code_nobody_holds_it_still_decides_and_the_code_becomes_the_products(self):
        body = self.staged([self.row('MICA-1', 'Mica suelta', self.ident(self.loose))])
        self.assertEqual((body['counts']['update'], body['counts']['error']), (1, 0), body['rows'])
        self.apply(body['id'])
        self.assertEqual(ProductBarcode.objects.get(company=self.company, code='MICA-1').product_id, self.loose.pk)

    def test_the_same_identifier_twice_in_a_file_is_an_error_of_the_second_row(self):
        body = self.staged([
            self.row(None, 'Mica A', self.ident(self.loose)), self.row(None, 'Mica B', self.ident(self.loose))])
        self.assertEqual(body['counts']['error'], 1, body['rows'])
        errors = ' '.join(e for row in body['rows'] for e in row['errors'])
        self.assertIn('ya aparece en la fila 2', errors)

    def test_a_product_that_went_away_after_the_preview_stops_the_load(self):
        body = self.staged([self.row(None, 'Mica renombrada', self.ident(self.loose))])
        Product.objects.filter(pk=self.loose.pk).delete()
        response = self.apply(body['id'])
        self.assertIn(response.status_code, (400, 409), response.content)
        self.assertFalse(Product.objects.filter(company=self.company, name='Mica renombrada').exists())

    # ---- no identifier and no code: the name, and only when it is nobody else's ----

    def test_without_identifier_or_code_a_name_two_products_carry_is_an_error_that_asks_for_a_code(self):
        Product.objects.create(company=self.company, name='mica  SUELTA', slug='mica-suelta-2', price=15)
        body = self.staged([self.row(None, 'Mica suelta', None, price=20)])
        self.assertEqual(body['counts']['error'], 1, body['rows'])
        self.assertIn('Hay más de un producto con este nombre', body['rows'][0]['errors'][0])
        self.assertIn('código', body['rows'][0]['errors'][0])
        # With the identifier the same row is not ambiguous at all.
        body = self.staged([self.row(None, 'Mica suelta', self.ident(self.loose), price=20)])
        self.assertEqual((body['counts']['error'], body['rows'][0]['data']['product_id']), (0, self.loose.pk))

    def test_without_identifier_or_code_a_name_only_one_product_carries_still_finds_it(self):
        body = self.staged([self.row(None, ' mica   SUELTA ', None, price=20)])
        self.assertEqual((body['counts']['update'], body['counts']['error']), (1, 0), body['rows'])
        self.assertEqual(
            (body['rows'][0]['data']['product_id'], body['rows'][0]['data']['matched_by']), (self.loose.pk, 'name'))

    def test_a_file_without_the_column_loads_as_before(self):
        body = self.imported([product_row('CARG-1', 'Cargador', price=45)])
        self.assertEqual((body['counts']['update'], body['rows'][0]['data']['matched_by']), (1, 'code'))


class TheColumnIsNeverSilentlyIgnoredTest(_Base):
    """
    A cell that says «Solo stock interno» and is not read publishes the product.

    So the column is found by its header whatever mapping arrives: one the
    screen remembered from before this column existed, one of a format that
    never had it, one somebody assigned by hand and forgot it in.
    """

    def test_a_mapping_remembered_from_before_the_column_still_reads_it(self):
        for header in ('Destino', 'Destino (pendiente web)'):
            with self.subTest(header):
                data = workbook_bytes([product_row(f'CODR-{len(header)}', f'Flex {header}', destination=INTERNAL)],
                                      destination_header=header)
                # What the wizard sends when it prefers its stored profile: no «destination».
                old_profile = {'code': 1, 'name': 2, 'price': 4, 'category': 5}
                body = self.preview(data, header_row='1', mapping=json.dumps(old_profile)).json()

                self.assertEqual(body['counts']['error'], 0, body['rows'])
                self.assertEqual(body['rows'][0]['data']['destination'], 'internal')
                self.assertEqual(self.apply(body['id']).status_code, 200)
                self.assertFalse(self.product(f'Flex {header}').is_published_online)

    def test_a_header_that_starts_like_it_is_read_too(self):
        for position, header in enumerate(('Destino web', 'Destino del producto', 'Destino (e-commerce)', 'destino:')):
            with self.subTest(header):
                body = self.preview(workbook_bytes(
                    [product_row(f'CODS-{position}', f'Repuesto {position}', destination=INTERNAL)],
                    destination_header=header)).json()
                self.assertEqual(body['counts']['error'], 0, body['rows'])
                self.assertEqual(body['rows'][0]['data']['destination'], 'internal', header)

    def test_the_preview_says_which_column_it_took_it_from(self):
        data = workbook_bytes([product_row('CODN-1', 'Repuesto', destination=INTERNAL)], destination_header='Destino web')
        body = self.preview(data, header_row='1', mapping=json.dumps({'code': 1, 'name': 2, 'price': 4})).json()
        self.assertIn('Destino web', ' '.join(body['summary']['format_notes']))

    def test_a_column_somebody_assigned_by_hand_is_the_one_used(self):
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = 'Productos'
        sheet.append([*HEADERS, 'Destino', 'A dónde va de verdad'])
        sheet.append([None, 'CODM-9', 'Lápiz óptico', None, 100, None, None, None, ONLINE, INTERNAL])
        out = io.BytesIO()
        workbook.save(out)
        body = self.preview(out.getvalue(), header_row='1',
                            mapping=json.dumps({'code': 1, 'name': 2, 'price': 4, 'destination': 9})).json()
        self.assertEqual(body['rows'][0]['data']['destination'], 'internal')

    def test_a_help_row_is_only_the_row_under_the_headers(self):
        """A product that really is called «Obligatorio» further down is not swallowed."""
        body = self.preview(workbook_bytes([
            product_row('CODO-1', 'Cable'),
            product_row('CODO-2', 'Obligatorio'),
        ])).json()
        self.assertEqual(body['counts']['create'], 2, body['rows'])


class OwnersWorkbookTest(_Base):
    """El libro tal como lo preparó el propietario: ayuda en mayúsculas, hoja «Categorías», columna I."""

    def book(self, *rows):
        return workbook_bytes(
            [
                ['EAN / UPC; como TEXTO', 'Código interno; como TEXTO', 'OBLIGATORIO', 'Opcional', 'OBLIGATORIO para nuevos',
                 'Selecciona una categoría de la hoja Categorías', 'Opcional: nombre exacto de archivo',
                 'Opcional: archivo1.webp|archivo2.webp', 'CONTROL EN EXCEL: hoy NO se importa'],
                ['195949806575', 'IP16PM-256-W', 'iPhone 16 Pro Max 256GB White', 'Pantalla de 6,9 pulgadas, 256 GB.', 4999,
                 'Teléfonos', None, None, ONLINE],
                *rows,
            ],
            destination_header='Destino (pendiente web)',
            extra_sheets=(
                ('Categorías', [['CATEGORÍAS DE TU EMPRESA', 'INSTRUCCIONES'], ['Nombre exacto de la categoría', 'Copia aquí…'], ['Teléfonos']]),
                ('Instrucciones', [['PLANTILLA DE PRODUCTOS'], ['Estado de funciones', 'Cómo funciona']]),
                ('Ejemplo', [HEADERS, ['195949806575', 'IP16PM-256-W', 'iPhone 16 Pro Max 256GB White', 'x', 4999, 'Teléfonos']]),
            ),
        )

    def test_as_it_is_it_creates_nothing_and_complains_about_nothing(self):
        body = self.preview(self.book()).json()

        self.assertEqual(body['counts']['create'], 0, body['rows'])
        self.assertEqual(body['counts']['error'], 0, body['rows'])
        self.assertFalse(Product.objects.filter(company=self.company).exists())

    def test_its_real_rows_are_imported_with_their_destination(self):
        body = self.imported_book(
            ['', 'FLEX-IP13', 'Flex de carga iPhone 13', None, 35, 'Repuestos', None, None, INTERNAL],
            ['', 'FUNDA-IP16', 'Funda iPhone 16', None, 59, 'Teléfonos', None, None, ONLINE],
        )

        self.assertEqual(body['counts']['create'], 2, body['rows'])
        self.assertFalse(self.product('Flex de carga iPhone 13').is_published_online)
        self.assertEqual(self.product('Flex de carga iPhone 13').category, self.parts)
        self.assertTrue(self.product('Funda iPhone 16').is_published_online)

    def imported_book(self, *rows):
        body = self.preview(self.book(*rows)).json()
        self.assertEqual(body['counts']['error'], 0, body['rows'])
        self.assertEqual(self.apply(body['id']).status_code, 200)
        return body

    def test_its_categories_sheet_is_help_and_never_products(self):
        res = self.client.post(
            f'{INSPECT}?company={self.company.pk}',
            {'file': SimpleUploadedFile('p.xlsx', self.book(), content_type=XLSX)}, format='multipart')
        self.assertEqual(res.status_code, 200, res.content)
        sheets = {sheet['name']: sheet for sheet in res.json()['sheets']}
        self.assertTrue(sheets['Categorías']['help'])
        self.assertFalse(sheets['Productos']['help'])

        chosen = self.preview(self.book(), sheet_name='Categorías')
        self.assertEqual(chosen.status_code, 400, chosen.content)
        self.assertFalse(Product.objects.filter(company=self.company).exists())


class CategoriesTest(_Base):
    def test_a_category_is_checked_against_this_companys_own(self):
        other = _p3_company('destino-otra', 'Otra Tienda')
        Category.objects.create(company=other, name='Sólo de la otra', slug='solo-de-la-otra')

        body = self.preview(workbook_bytes([
            product_row('CODK-1', 'Con la mía', category='Teléfonos'),
            product_row('CODK-2', 'Con la ajena', category='Sólo de la otra'),
        ])).json()

        by_name = {row['data'].get('name'): row for row in body['rows']}
        self.assertEqual(by_name['Con la mía']['action'], 'create')
        self.assertEqual(by_name['Con la mía']['data']['category_id'], self.phones.pk)
        self.assertEqual(by_name['Con la ajena']['action'], 'error')
        self.assertIn('no existe', ' '.join(by_name['Con la ajena']['errors']))

    def test_a_category_can_be_chosen_for_the_rows_that_name_none(self):
        body = self.preview(
            workbook_bytes([product_row('CODD-1', 'Sin categoría'), product_row('CODD-2', 'Con categoría', category='Teléfonos')]),
            options={'default_category_id': self.parts.pk}).json()
        self.assertEqual(body['counts']['error'], 0, body)
        self.assertEqual(self.apply(body['id']).status_code, 200)

        self.assertEqual(self.product('Sin categoría').category, self.parts)
        self.assertEqual(self.product('Con categoría').category, self.phones)       # la de la fila manda

    def test_the_chosen_category_has_to_be_this_companys(self):
        other = _p3_company('destino-otra', 'Otra Tienda')
        foreign = Category.objects.create(company=other, name='Ajena', slug='ajena')

        for bad in (foreign.pk, 999999, 'abc', -1):
            with self.subTest(bad):
                res = self.preview(workbook_bytes([product_row('CODF-1', 'Producto')]), options={'default_category_id': bad})
                self.assertEqual(res.status_code, 400, res.content)
                self.assertNotIn('Ajena', res.content.decode())
        self.assertFalse(Product.objects.filter(company=self.company).exists())

    def test_the_chosen_category_does_not_move_a_product_that_already_has_one(self):
        Product.objects.create(company=self.company, name='Ya ordenado', slug='ya-ordenado', price=10, category=self.phones)
        Product.objects.create(company=self.company, name='Sin ordenar', slug='sin-ordenar', price=10)
        body = self.preview(workbook_bytes([product_row(None, 'Ya ordenado', price=12), product_row(None, 'Sin ordenar', price=12)]),
                            options={'default_category_id': self.parts.pk}).json()
        # The preview says what will happen, not what was asked for.
        by_name = {row['data']['name']: row['data'] for row in body['rows']}
        self.assertEqual(by_name['Ya ordenado']['category_default'], '')
        self.assertEqual(by_name['Sin ordenar']['category_default'], 'Repuestos')

        self.assertEqual(self.apply(body['id']).status_code, 200)
        self.assertEqual(self.product('Ya ordenado').category, self.phones)
        self.assertEqual(self.product('Sin ordenar').category, self.parts)

    def test_a_category_that_was_switched_off_cannot_be_the_chosen_one(self):
        Category.objects.filter(pk=self.parts.pk).update(is_active=False)
        res = self.preview(workbook_bytes([product_row('CODI-1', 'Producto')]), options={'default_category_id': self.parts.pk})
        self.assertEqual(res.status_code, 400, res.content)

    def test_a_number_no_database_holds_is_one_more_refusal(self):
        for bad in (1e999, 10 ** 40, 1.5, True, [1], {'id': 1}):
            with self.subTest(repr(bad)):
                res = self.client.post(
                    f'{PREVIEW}?company={self.company.pk}',
                    {'file': SimpleUploadedFile('p.xlsx', workbook_bytes([product_row('CODB-1', 'Producto')]), content_type=XLSX),
                     'options': json.dumps({'default_category_id': bad}).replace('Infinity', '1e999')},
                    format='multipart')
                self.assertEqual(res.status_code, 400, res.content)


class TemplateTest(_Base):
    """
    IMPORT-SPLIT-01 — ONE sheet, «Productos»: the headers and nothing under them.

    What used to be a help row, an example row and three more sheets is said
    on the screen and in a note on each header. The category list is still
    this company's own, now inside the cell list.
    """

    def book(self, company=None):
        return openpyxl.load_workbook(io.BytesIO(import_exports.product_template_bytes(company or self.company)))

    def rules(self, company=None):
        return {str(rule.sqref): rule for rule in self.book(company)['Productos'].data_validations.dataValidation}

    def test_one_sheet_with_the_headers_and_nothing_else(self):
        workbook = self.book()

        self.assertEqual(workbook.sheetnames, ['Productos'])
        sheet = workbook['Productos']
        self.assertEqual([c.value for c in sheet[1]], [*HEADERS, 'Destino'])
        self.assertEqual(import_exports.PRODUCT_TEMPLATE_HEADERS, [*HEADERS, 'Destino'])
        self.assertEqual(sheet.max_row, 1)                             # no help row, no example row
        self.assertEqual(sheet.freeze_panes, 'A2')

    def test_the_destination_is_chosen_from_a_list(self):
        destination = self.rules()['I2:I501']

        self.assertEqual(destination.type, 'list')
        self.assertEqual(destination.formula1, f'"{ONLINE},{INTERNAL}"')
        # As in the owner's file: the list offers and does not block; a wrong
        # value is a row error when the file is uploaded.
        self.assertFalse(destination.showErrorMessage)

    def test_the_category_list_is_this_companys_and_nobody_elses(self):
        other = _p3_company('destino-otra', 'Otra Tienda')
        Category.objects.create(company=other, name='Sólo de la otra', slug='solo-de-la-otra')

        category = self.rules()['F2:F501']
        self.assertEqual(category.type, 'list')
        self.assertEqual(category.formula1, '"Repuestos,Teléfonos"')
        # Una lista ayuda; no impide escribir una categoría nueva para «crear las que falten».
        self.assertFalse(category.showErrorMessage)
        self.assertEqual(self.rules(other)['F2:F501'].formula1, '"Sólo de la otra"')

    def test_a_category_switched_off_is_not_offered(self):
        Category.objects.filter(company=self.company, name='Repuestos').update(is_active=False)
        self.assertEqual(self.rules()['F2:F501'].formula1, '"Teléfonos"')

    def test_names_a_cell_list_cannot_hold_leave_the_list_out_and_the_template_whole(self):
        """
        A cell list is one text of 255 characters split by commas, and picking
        an item is typing it. What does not fit, would split, would be read as a
        formula or cannot be written at all is left to be typed — and checked on upload.
        """
        for number, (label, names) in enumerate((
            ('a comma in a name', ['Cables, cargadores']),
            ('a quote in a name', ['Pantallas 6"']),
            ('more than fits', [f'Categoría número {n:02d} de la tienda' for n in range(12)]),
            ('a name Excel would run', ['=1+1']),
            ('a name Excel would run', ['+Ofertas']),
            ('a name Excel would run', ['-Liquidación']),
            ('a name Excel would run', ['@todos']),
            ('a control character somebody pasted', ['Fundas\x01']),
            ('a line break in a name', ['Fundas\ny más']),
        )):
            with self.subTest(label=label, names=names[:1]):
                company = _p3_company(f'destino-lista-{number}', f'Tienda de lista {number}')
                for index, name in enumerate(names):
                    Category.objects.create(company=company, name=name, slug=f'cat-{number}-{index}')
                rules = self.rules(company)
                self.assertNotIn('F2:F501', rules)
                self.assertIn('I2:I501', rules)
                self.assertEqual(self.book(company).sheetnames, ['Productos'])

    def test_a_list_is_measured_the_way_excel_measures_it(self):
        """255 characters between the quotes, separators included, counted as Excel stores them (UTF-16)."""
        fits = ['a' * 127, 'b' * 127]                                   # 127 + 1 + 127 = 255
        self.assertEqual(import_exports.cell_list(fits), f'"{fits[0]},{fits[1]}"')
        self.assertIsNone(import_exports.cell_list(['a' * 127, 'b' * 128]))
        self.assertIsNone(import_exports.cell_list(['😀' * 128]))        # 128 signs, 256 units
        self.assertEqual(import_exports.cell_list(['😀' * 127]), '"' + '😀' * 127 + '"')
        self.assertIsNone(import_exports.cell_list([]))
        self.assertIsNone(import_exports.cell_list(['', '   ']))

    def test_the_price_is_a_number_with_two_decimals(self):
        self.assertEqual(self.book()['Productos'].column_dimensions['E'].number_format, '0.00')

    def test_a_company_without_categories_gets_a_template_that_still_opens(self):
        empty = _p3_company('destino-vacia', 'Tienda Vacía')

        self.assertEqual(self.book(empty).sheetnames, ['Productos'])
        self.assertEqual(sorted(self.rules(empty)), ['I2:I501'])

    def test_codes_stay_text(self):
        sheet = self.book()['Productos']
        for column in ('A', 'B'):
            self.assertEqual(sheet.column_dimensions[column].number_format, '@', column)

    def test_each_header_says_what_goes_under_it(self):
        notes = {cell.value: cell.comment.text for cell in self.book()['Productos'][1] if cell.comment}

        self.assertEqual(set(notes), set(import_exports.PRODUCT_TEMPLATE_HEADERS))
        self.assertIn('Obligatorio', notes['Nombre'])
        for optional in ('Imagen principal', 'Imágenes', 'Destino'):
            self.assertTrue(notes[optional].startswith('Opcional'), optional)
        self.assertIn(ONLINE, notes['Destino'])
        self.assertIn(INTERNAL, notes['Destino'])
        self.assertIn('caja', notes['Destino'])                         # sigue vendiéndose en tienda
        # Vacío no publica: lo nuevo queda interno (decisión del propietario).
        self.assertIn('Vacío: lo nuevo queda como «Solo stock interno» y lo existente no cambia.', notes['Destino'])
        self.assertNotIn('lo nuevo se publica', notes['Destino'])
        self.assertNotIn('lo nuevo se publica', ' '.join(import_exports.PRODUCT_TEMPLATE_HELP))
        # Esta carga no mueve stock: lo dice donde alguien buscaría una cantidad.
        self.assertIn('stock', notes['Código'].lower())

    def test_downloaded_and_uploaded_as_it_is_it_creates_nothing(self):
        res = self.client.get(f'{TEMPLATE}?company={self.company.pk}')
        self.assertEqual(res.status_code, 200)
        self.assertIn('carga-masiva-productos.xlsx', res['Content-Disposition'])
        data = b''.join(res.streaming_content) if hasattr(res, 'streaming_content') else res.content

        body = self.preview(data).json()
        self.assertEqual((body['counts']['total'], body['counts']['create'], body['counts']['error']), (0, 0, 0), body['rows'])

    def test_the_row_right_under_the_headers_is_a_product(self):
        workbook = self.book()
        workbook['Productos'].append(product_row('FUNDA-IP16', 'Funda iPhone 16', price=59, category='Teléfonos', destination=INTERNAL))
        out = io.BytesIO()
        workbook.save(out)

        body = self.preview(out.getvalue()).json()

        self.assertEqual((body['counts']['create'], body['counts']['error']), (1, 0), body['rows'])

    def test_nobody_downloads_another_companys_categories(self):
        other = _p3_company('destino-otra', 'Otra Tienda')
        Category.objects.create(company=other, name='Sólo de la otra', slug='solo-de-la-otra')

        res = self.client.get(f'{TEMPLATE}?company={other.pk}')

        self.assertIn(res.status_code, (403, 404))
        self.assertNotIn('spreadsheetml', res.get('Content-Type', ''))                # not a workbook at all


class NoStockThroughTheCatalogueTest(_Base):
    """
    IMPORT-SPLIT-01 — a product file creates and edits the CATALOGUE. Stock has
    its own two uploads (quantities, and devices one by one) with their own
    preview and their own Kardex lines.
    """

    def test_a_stock_column_is_said_to_be_ignored_and_moves_nothing(self):
        from store.models import BranchStock, StockMovement

        for header in ('Stock', 'Cantidad', 'Stock inicial', 'Stock inicial - ALMACEN 1 - 11416', 'Unidades'):
            with self.subTest(header):
                data = workbook_bytes(
                    [[*product_row(f'COD-{len(header)}', f'Cable {header}', category='Repuestos'), 7]],
                    headers=[*HEADERS, 'Destino', header])
                body = self.preview(data).json()

                self.assertEqual(body['counts']['error'], 0, body['rows'])
                said = [note for note in body['summary']['unmapped'] if note['column'] == header]
                self.assertEqual(len(said), 1, body['summary']['unmapped'])
                self.assertIn('no mueve stock', said[0]['reason'])
                # Not one more ignored column: somebody expects stock to go in. The screen shows it open.
                self.assertTrue(said[0]['important'])
                self.assertEqual(self.apply(body['id']).status_code, 200)
        self.assertFalse(StockMovement.objects.filter(company=self.company).exists())
        self.assertFalse(BranchStock.objects.filter(product__company=self.company, quantity__gt=0).exists())

    def test_what_reads_as_stock_however_it_is_headed(self):
        for header in ('Nº de unidades', 'Total unidades', 'Inventario', 'Quantity', 'ALMACEN Centro', 'Depósito 2'):
            with self.subTest(header):
                data = workbook_bytes(
                    [[*product_row('COD-ST', 'Cable de stock', category='Repuestos'), 4]], headers=[*HEADERS, 'Destino', header])
                [note] = self.preview(data).json()['summary']['unmapped']
                self.assertEqual((note['column'], note.get('important')), (header, True))
                self.assertIn('no mueve stock', note['reason'])

    def test_a_column_that_is_not_stock_is_not_called_stock(self):
        """A unit of measure, a pack size, a reorder point, a product line called «Serie»."""
        for header in ('Unidades de medida', 'Cantidad por caja', 'Unidades por caja', 'Stock mínimo - Almacén 1', 'Stock interno', 'Serie'):
            with self.subTest(header):
                data = workbook_bytes(
                    [[*product_row('COD-NS', 'Cable sin stock', category='Repuestos'), 'x']], headers=[*HEADERS, 'Destino', header])
                flagged = [note for note in self.preview(data).json()['summary']['unmapped'] if note.get('important')]
                self.assertEqual(flagged, [])

    def test_a_serial_or_an_imei_column_is_pointed_to_the_device_upload(self):
        data = workbook_bytes(
            [[*product_row('IP16-256', 'iPhone 16 256GB', category='Teléfonos'), 'F2LXK1ABC1', '356938035643809']],
            headers=[*HEADERS, 'Destino', 'Número de serie', 'IMEI'])
        body = self.preview(data).json()

        notes = {note['column']: note['reason'] for note in body['summary']['unmapped']}
        self.assertEqual(set(notes), {'Número de serie', 'IMEI'})
        for header in ('Nº de serie', 'No. de serie', 'Nro. de serie', 'IMEI 2'):
            other = workbook_bytes(
                [[*product_row('IP16-512', 'iPhone 16 512GB', category='Teléfonos'), 'X']], headers=[*HEADERS, 'Destino', header])
            self.assertIn('Carga de equipos', self.preview(other).json()['summary']['unmapped'][0]['reason'], header)
        for reason in notes.values():
            self.assertIn('Carga de equipos', reason)
        self.assertTrue(all(note['important'] for note in body['summary']['unmapped']))

    def test_an_ordinary_ignored_column_is_not_shouted(self):
        data = workbook_bytes(
            [[*product_row('COD-M', 'Cable con marca', category='Repuestos'), 'Anker']], headers=[*HEADERS, 'Destino', 'Marca'])
        [note] = self.preview(data).json()['summary']['unmapped']
        self.assertEqual((note['column'], note.get('important', False)), ('Marca', False))


class AuthorityTest(_Base):
    def test_who_cannot_manage_products_cannot_send_a_destination(self):
        viewer, _ = _p2d_member(self.company, 'destino_lector', ['company.view', 'products.view'])
        client = APIClient()
        client.force_authenticate(user=viewer)

        res = self.preview(workbook_bytes([product_row('CODA-1', 'Producto', destination=INTERNAL)]), client=client)

        self.assertEqual(res.status_code, 403)
        self.assertFalse(Product.objects.filter(company=self.company).exists())

    def test_an_import_writes_only_in_its_own_company(self):
        other = _p3_company('destino-otra', 'Otra Tienda')
        theirs = Product.objects.create(company=other, name='Flex de carga', slug='flex-de-carga', price=30)

        self.imported([product_row('CODT-1', 'Flex de carga', destination=INTERNAL)])

        theirs.refresh_from_db()
        self.assertTrue(theirs.is_published_online)
        self.assertFalse(self.product('Flex de carga').is_published_online)

    def test_an_import_that_moves_a_product_off_the_web_or_onto_it_is_written_down(self):
        from store.models import AdminAuditLog

        self.imported([product_row('CODA-1', 'Batería', category='Repuestos', destination=ONLINE), product_row('CODA-2', 'Tapa')])
        before = AdminAuditLog.objects.filter(action='product_destination_changed').count()
        self.assertEqual(before, 0)                                # being born somewhere is not a change

        body = self.imported([product_row('CODA-1', 'Batería', destination=INTERNAL), product_row('CODA-2', 'Tapa', price=5)])

        entries = AdminAuditLog.objects.filter(action='product_destination_changed')
        self.assertEqual(entries.count(), 1)
        entry = entries.get()
        self.assertEqual(entry.target_id, str(self.product('Batería').pk))
        self.assertEqual(entry.metadata['is_published_online'], False)
        self.assertEqual(entry.metadata['import_job_id'], body['id'])
        self.assertEqual(entry.company_id, self.company.pk)

    def test_a_job_of_another_company_is_not_there_to_apply(self):
        other = _p3_company('destino-otra', 'Otra Tienda')
        outsider, _ = _p2d_member(other, 'destino_ajeno', ['company.view', 'products.view', 'products.manage'])
        client = APIClient()
        client.force_authenticate(user=outsider)
        body = self.preview(workbook_bytes([product_row('CODJ-1', 'Producto', destination=INTERNAL)])).json()

        self.assertIn(self.apply(body['id'], client=client).status_code, (403, 404))
        self.assertIn(self.apply(body['id'], client=client, company=other).status_code, (403, 404))
        self.assertFalse(Product.objects.filter(name='Producto').exists())
