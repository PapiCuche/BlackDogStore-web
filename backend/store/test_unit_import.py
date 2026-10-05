"""
UNIT-IMPORT — «Equipos serializados.xlsx»: muchos equipos de una vez.

    UNA FILA = UNA UNIDAD FÍSICA.

La carga masiva de inventario escribe cantidades, y un producto con serie no
tiene una cantidad que alguien escribe: tiene equipos. Por eso un archivo de
stock rechaza esos productos, y hasta ahora la única forma de registrarlos era
uno por uno en pantalla.

Esta plantilla no es otro camino al stock. Cada fila termina en
`stock_unit_services.receive_units`, el mismo escritor que usa «Registrar
equipo»: mismas reglas de serie e IMEI, misma línea de Kardex, misma invariante
(cantidad == equipos disponibles). Lo que añade es el ensayo: se ve qué va a
pasar con cada fila antes de que pase, y entra todo o no entra nada.
"""
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook, load_workbook
from rest_framework.test import APIClient

from store import inventory_services as inventory
from store import unit_import_services as unit_import
from store.models import (
    AdminAuditLog, BulkImportJob, BulkImportRow, Membership, Product, ProductBarcode,
    StockMovement, StockUnit,
)
from store.test_stock_units import IMEI_A, IMEI_B, IMEI_C, IMEI_D, UnitsBase
from store.tests import _m7_login, _m7_user

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
TEMPLATE_URL = '/api/admin/inventory/units/import/template/'
PREVIEW_URL = '/api/admin/inventory/units/import/preview/'
HEADERS = ['Código', 'Producto', 'Sucursal', 'Número de serie', 'IMEI', 'IMEI 2', 'Condición', 'Costo',
           'Motivo / referencia']


def apply_url(job_id):
    return f'/api/admin/inventory/units/import/{job_id}/apply/'


def workbook(rows, headers=HEADERS, sheet='Equipos', name='equipos.xlsx'):
    book = Workbook()
    page = book.active
    page.title = sheet
    page.append(headers)
    for row in rows:
        page.append(row)
    out = io.BytesIO()
    book.save(out)
    return SimpleUploadedFile(name, out.getvalue(), content_type=XLSX)


class ImportBase(UnitsBase):
    def setUp(self):
        super().setUp()
        ProductBarcode.objects.create(company=self.company, product=self.phone, code='IP16-256-N')
        ProductBarcode.objects.create(company=self.company, product=self.laptop, code='MBA-13-M3')
        self.api = APIClient()
        self.api.force_authenticate(user=self.staff)

    def phone_row(self, serial, imei, **over):
        row = {'Código': 'IP16-256-N', 'Producto': '', 'Sucursal': self.branch_a.name, 'Número de serie': serial,
               'IMEI': imei, 'IMEI 2': '', 'Condición': 'Nuevo', 'Costo': '3800.00',
               'Motivo / referencia': 'Compra F001-204'}
        row.update(over)
        return [row[h] for h in HEADERS]

    def preview(self, rows, client=None, **form):
        return (client or self.api).post(PREVIEW_URL, {'file': workbook(rows), **form}, format='multipart')

    def staged(self, rows, **form):
        response = self.preview(rows, **form)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def errors(self, payload):
        return {row['row']: ' '.join(row['errors']) for row in payload['rows'] if row['errors']}


class TemplateTest(ImportBase):
    def test_the_template_says_one_row_is_one_device(self):
        response = self.api.get(TEMPLATE_URL)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Equipos serializados.xlsx', response['Content-Disposition'])
        book = load_workbook(io.BytesIO(response.content))
        self.assertEqual(book.sheetnames, ['Equipos', 'Instrucciones'])
        self.assertEqual([c.value for c in book['Equipos'][1]], HEADERS)
        help_text = ' '.join(str(c.value) for row in book['Instrucciones'].iter_rows() for c in row if c.value)
        self.assertIn('UNA FILA = UNA UNIDAD FÍSICA', help_text)
        self.assertIn('IMEI', help_text)
        # Un IMEI de 15 dígitos guardado como número lo estropea Excel: las
        # columnas de identificadores van como texto.
        self.assertEqual(book['Equipos']['D2'].number_format, '@')
        self.assertEqual(book['Equipos']['E2'].number_format, '@')

    def test_uploading_the_template_as_downloaded_registers_nothing(self):
        template = SimpleUploadedFile(
            'Equipos serializados.xlsx', unit_import.template_bytes(), content_type=XLSX)

        response = self.api.post(PREVIEW_URL, {'file': template}, format='multipart')

        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()
        self.assertEqual(payload['counts']['create'], 0)
        self.assertEqual(payload['counts']['error'], 0)
        self.assertFalse(StockUnit.objects.exists())


class PreviewTest(ImportBase):
    def test_a_preview_stages_every_row_and_registers_nothing(self):
        payload = self.staged([
            self.phone_row('F2LXK1ABC1', IMEI_A),
            self.phone_row('F2LXK1ABC2', IMEI_B, **{'IMEI 2': IMEI_C, 'Condición': 'Usado'}),
            ['MBA-13-M3', '', 'Sucursal Norte', 'C02ZK0ABMD6R', '', '', '', '', 'Compra F001-204'],
        ])

        self.assertEqual(payload['import_type'], 'units')
        self.assertEqual(payload['status'], 'previewed')
        self.assertEqual(payload['counts'], {**payload['counts'], 'total': 3, 'create': 3, 'error': 0})
        self.assertTrue(payload['is_applicable'])
        first = next(r for r in payload['rows'] if r['row'] == 2)['data']
        self.assertEqual(first['product_id'], self.phone.pk)
        self.assertEqual(first['branch_id'], self.branch_a.pk)
        self.assertEqual(first['serial_number'], 'F2LXK1ABC1')
        self.assertEqual(first['imei'], IMEI_A)
        self.assertEqual(next(r for r in payload['rows'] if r['row'] == 3)['data']['condition'], 'used')
        self.assertEqual(next(r for r in payload['rows'] if r['row'] == 4)['data']['branch_id'], self.branch_b.pk)
        # Ensayar no escribe.
        self.assertFalse(StockUnit.objects.exists())
        self.assertFalse(StockMovement.objects.filter(product__in=[self.phone, self.laptop]).exists())
        self.assertEqual(self.quantity(), 0)

    def test_an_identifier_excel_stored_as_a_number_is_read_whole(self):
        payload = self.staged([self.phone_row('F2LXK1ABC1', int(IMEI_A))])

        self.assertEqual(payload['counts']['error'], 0, payload['rows'])
        self.assertEqual(payload['rows'][0]['data']['imei'], IMEI_A)

    def test_each_bad_row_says_what_is_wrong_with_it(self):
        self.receive(self.row('YAESTABA01', IMEI_D))
        Product.objects.filter(pk=self.product.pk).update(name='Cargador sin serie')
        payload = self.staged([
            self.phone_row('', IMEI_A),                                          # 2 sin serie
            self.phone_row('F2LXK1ABC2', ''),                                    # 3 teléfono sin IMEI
            self.phone_row('F2LXK1ABC3', IMEI_A[:-1] + ('0' if IMEI_A[-1] != '0' else '1')),   # 4 control
            self.phone_row('F2LXK1ABC4', 'N/A'),                                 # 5 marcador
            self.phone_row('F2LXK1ABC5', IMEI_B, **{'Código': 'NO-EXISTE'}),     # 6 producto
            self.phone_row('F2LXK1ABC6', IMEI_B, **{'Código': '', 'Producto': 'Cargador sin serie'}),  # 7 sin serie
            self.phone_row('F2LXK1ABC7', IMEI_B, **{'Sucursal': 'Lima Sur'}),    # 8 sucursal
            self.phone_row('F2LXK1ABC8', IMEI_B, **{'Condición': 'Roto'}),       # 9 condición
            self.phone_row('F2LXK1ABC9', IMEI_B, **{'Costo': 'gratis'}),         # 10 costo
            self.phone_row('YAESTABA01', IMEI_B),                                # 11 serie ya registrada
            self.phone_row('F2LXK1ABD0', IMEI_D),                                # 12 IMEI ya registrado
            self.phone_row('F2LXK1ABD1', IMEI_B, **{'Motivo / referencia': ''}), # 13 sin motivo
            self.phone_row('F2LXK1ABD2', IMEI_C, **{'IMEI 2': IMEI_C}),          # 14 IMEI2 igual
        ])

        errors = self.errors(payload)
        self.assertEqual(sorted(errors), list(range(2, 15)), errors)
        self.assertIn('número de serie', errors[2])
        self.assertIn('lleva IMEI', errors[3])
        self.assertIn('control', errors[4])
        self.assertIn('IMEI', errors[5])
        self.assertIn('No existe un producto', errors[6])
        self.assertIn('no se controla por número de serie', errors[7])
        self.assertIn('Lima Sur', errors[8])
        self.assertIn('Condición', errors[9])
        self.assertIn('costo', errors[10].lower())
        self.assertIn('ya está registrado', errors[11])
        self.assertIn('ya está registrado', errors[12])
        self.assertIn('motivo', errors[13].lower())
        self.assertIn('distinto', errors[14])
        self.assertFalse(payload['is_applicable'])
        self.assertEqual(StockUnit.objects.count(), 1)

    def test_the_same_device_twice_in_one_file_is_an_error_on_the_second_row(self):
        payload = self.staged([
            self.phone_row('F2LXK1ABC1', IMEI_A),
            self.phone_row('f2lxk1abc1', IMEI_B),                                # misma serie
            self.phone_row('F2LXK1ABC3', IMEI_C),
            self.phone_row('F2LXK1ABC4', IMEI_D, **{'IMEI 2': IMEI_C}),          # IMEI de la fila 4
        ])

        errors = self.errors(payload)
        self.assertEqual(sorted(errors), [3, 5], errors)
        self.assertIn('repetido', errors[3])
        self.assertIn('repetido', errors[5])

    def test_a_quantity_is_not_a_device(self):
        """Dos teléfonos son dos filas. Una columna «Cantidad» no los sustituye."""
        headers = HEADERS + ['Cantidad']
        response = self.api.post(PREVIEW_URL, {
            'file': workbook([self.phone_row('F2LXK1ABC1', IMEI_A) + [2]], headers=headers),
        }, format='multipart')

        self.assertEqual(response.status_code, 400)
        self.assertIn('Cantidad', response.json()['detail'])
        self.assertFalse(BulkImportJob.objects.exists())

    def test_a_default_branch_and_reason_fill_the_blanks(self):
        payload = self.staged(
            [self.phone_row('F2LXK1ABC1', IMEI_A, **{'Sucursal': '', 'Motivo / referencia': ''})],
            branch=self.branch_b.pk, reason='Inventario inicial de equipos',
        )

        self.assertEqual(payload['counts']['error'], 0, payload['rows'])
        data = payload['rows'][0]['data']
        self.assertEqual(data['branch_id'], self.branch_b.pk)
        self.assertEqual(data['reason'], 'Inventario inicial de equipos')

    def test_a_file_that_is_not_the_template_is_refused_whole(self):
        response = self.api.post(PREVIEW_URL, {
            'file': workbook([['x', 1]], headers=['Nombre', 'Stock']),
        }, format='multipart')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Número de serie', response.json()['detail'])

        self.assertEqual(self.api.post(PREVIEW_URL, {}, format='multipart').status_code, 400)
        self.assertFalse(BulkImportJob.objects.exists())


class ApplyTest(ImportBase):
    def test_applying_registers_each_row_as_one_unit_through_the_kardex(self):
        job = self.staged([
            self.phone_row('F2LXK1ABC1', IMEI_A),
            self.phone_row('F2LXK1ABC2', IMEI_B, **{'IMEI 2': IMEI_C, 'Condición': 'Usado', 'Costo': ''}),
            ['MBA-13-M3', '', 'Sucursal Norte', 'C02ZK0ABMD6R', '', '', 'Reacondicionado', '4100', 'Compra F001-204'],
        ])

        response = self.api.post(apply_url(job['id']))

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['status'], 'applied')
        self.assertEqual(response.json()['summary']['applied']['units'], 3)
        self.assertEqual(StockUnit.objects.count(), 3)
        self.assertEqual(self.quantity(), 2)
        self.assert_invariant()
        self.assertEqual(self.quantity(self.laptop, self.branch_b), 1)
        self.assert_invariant(self.laptop, self.branch_b)
        used = StockUnit.objects.get(serial_number='F2LXK1ABC2')
        self.assertEqual((used.imei, used.imei2, used.condition, used.cost), (IMEI_B, IMEI_C, 'used', None))
        self.assertEqual(used.status, 'available')
        self.assertEqual(used.company, self.company)
        self.assertEqual(str(StockUnit.objects.get(serial_number='C02ZK0ABMD6R').cost), '4100.00')
        movements = StockMovement.objects.filter(movement_type=StockMovement.PURCHASE_ENTRY,
                                                 product__in=[self.phone, self.laptop])
        self.assertEqual(sorted(m.quantity for m in movements), [1, 2])
        self.assertEqual({m.reason for m in movements}, {'Compra F001-204'})
        log = AdminAuditLog.objects.get(action='stock_units_import_applied')
        self.assertEqual(log.company, self.company)
        self.assertEqual(log.metadata['units'], 3)
        self.assertNotIn(IMEI_A, str(log.metadata))

    def test_applying_twice_registers_once(self):
        job = self.staged([self.phone_row('F2LXK1ABC1', IMEI_A)])

        self.assertEqual(self.api.post(apply_url(job['id'])).status_code, 200)
        self.assertEqual(self.api.post(apply_url(job['id'])).status_code, 200)

        self.assertEqual(StockUnit.objects.count(), 1)
        self.assertEqual(self.quantity(), 1)

    def test_a_preview_with_errors_cannot_be_applied(self):
        job = self.staged([self.phone_row('F2LXK1ABC1', IMEI_A), self.phone_row('', IMEI_B)])

        response = self.api.post(apply_url(job['id']))

        self.assertEqual(response.status_code, 400)
        self.assertFalse(StockUnit.objects.exists())

    def test_what_changed_since_the_preview_refuses_the_whole_file(self):
        """Todo o nada: si una fila ya no puede entrar, no entra ninguna."""
        job = self.staged([
            self.phone_row('F2LXK1ABC1', IMEI_A),
            self.phone_row('F2LXK1ABC2', IMEI_B),
            ['MBA-13-M3', '', self.branch_a.name, 'C02ZK0ABMD6R', '', '', '', '', 'Compra F001-204'],
        ])
        # Alguien registra a mano el segundo teléfono entre el ensayo y la carga.
        self.receive(self.row('OTRASERIE1', IMEI_B))

        response = self.api.post(apply_url(job['id']))

        self.assertEqual(response.status_code, 400)
        self.assertIn('fila 3', response.json()['detail'])
        self.assertEqual(StockUnit.objects.count(), 1)
        self.assertEqual(self.quantity(self.laptop), 0)
        self.assertEqual(BulkImportJob.objects.get(pk=job['id']).status, 'previewed')

    def test_a_product_that_stopped_being_serialized_refuses_the_file(self):
        job = self.staged([self.phone_row('F2LXK1ABC1', IMEI_A)])
        Product.objects.filter(pk=self.phone.pk).update(is_serialized=False)

        self.assertEqual(self.api.post(apply_url(job['id'])).status_code, 400)
        self.assertFalse(StockUnit.objects.exists())


class ScopeTest(ImportBase):
    def test_another_companys_product_and_branch_do_not_exist_here(self):
        ProductBarcode.objects.create(company=self.other, product=self.foreign_phone, code='AJENO-1')
        payload = self.staged([
            self.phone_row('F2LXK1ABC1', IMEI_A, **{'Código': 'AJENO-1'}),
            self.phone_row('F2LXK1ABC2', IMEI_B, **{'Sucursal': self.foreign_branch.name}),
        ])

        errors = self.errors(payload)
        self.assertIn('No existe un producto', errors[2])
        self.assertIn('No existe la sucursal', errors[3])

    def test_a_branch_the_caller_cannot_reach_is_an_error_at_preview_and_at_apply(self):
        job = self.staged([self.phone_row('F2LXK1ABC1', IMEI_A, **{'Sucursal': 'Sucursal Norte'})])
        self.restrict_to_branch_a()

        again = self.preview([self.phone_row('F2LXK1ABC1', IMEI_A, **{'Sucursal': 'Sucursal Norte'})])
        self.assertIn('No tienes acceso', self.errors(again.json())[2])
        # El ensayo hecho ANTES de perder el acceso tampoco se puede cargar ni leer.
        self.assertIn(self.api.post(apply_url(job['id'])).status_code, (403, 404))
        self.assertEqual(self.api.get(f'/api/admin/imports/{job["id"]}/').status_code, 404)
        self.assertFalse(StockUnit.objects.exists())

    def test_it_needs_the_capability_to_adjust_inventory(self):
        viewer = _m7_user('solo-mira')
        membership = Membership.objects.create(user=viewer, company=self.company, role='inventory')
        from store.tests import _assign, _role
        _assign(membership, _role(self.company, 'Sólo ver', capabilities=['inventory.view'], slug='solo-ver'))
        client = APIClient()
        client.force_authenticate(user=viewer)

        self.assertEqual(client.get(TEMPLATE_URL).status_code, 403)
        self.assertEqual(self.preview([self.phone_row('F2LXK1ABC1', IMEI_A)], client=client).status_code, 403)
        self.assertEqual(APIClient().post(PREVIEW_URL, {}, format='multipart').status_code, 401)

    def test_a_job_of_another_company_is_not_found(self):
        job = self.staged([self.phone_row('F2LXK1ABC1', IMEI_A)])
        outsider = _m7_user('ajena')
        Membership.objects.create(user=outsider, company=self.other, role='admin')
        client = APIClient()
        client.force_authenticate(user=outsider)

        self.assertEqual(client.post(apply_url(job['id'])).status_code, 404)
        self.assertFalse(StockUnit.objects.exists())

    def test_the_history_lists_the_job_as_equipment(self):
        job = self.staged([self.phone_row('F2LXK1ABC1', IMEI_A)])

        listed = self.api.get('/api/admin/imports/?type=units').json()['results']

        self.assertEqual([j['id'] for j in listed], [job['id']])
        self.assertEqual(BulkImportRow.objects.filter(job_id=job['id']).count(), 1)
