"""
NEW-TEMPLATE-PRICE — «Precio de venta» opcional en la plantilla «Stock de equipos nuevos».

Decisión del propietario (2026-11-24):

    1. La plantilla de nuevos ofrece la columna «Precio de venta», opcional.
    2. Ese precio NUNCA se aplica solo cuando difiere del precio comercial de la variante: la vista previa muestra la
       diferencia, su alcance (equipos del archivo, equipos ya en stock, si la variante se vende) y una propuesta.
    3. Cambiar el precio de la variante exige `products.manage` Y una confirmación explícita al registrar, atada a la
       vista previa: el precio propuesto es el del archivo y el precio actual sigue siendo el que la vista previa vio.
    4. El costo nunca es un precio implícito.

Los equipos se crean exactamente como antes: el precio del archivo nunca se guarda en el equipo nuevo.
"""
from decimal import Decimal

from django.test import TestCase

from store import device_catalog
from store import unit_import_services as unit_import
from store.models import AdminAuditLog, BulkImportJob, Product, StockUnit
from store.test_stock_load import PREVIEW_URL, LoadShop, apply_url, client_for, filled
from store.tests import _p2d_member


class NewPriceShop(LoadShop):
    def build(self, slug='newprice'):
        super().build(slug)
        # Moves stock and sees costs, but does NOT set what things sell for.
        self.counter, _ = _p2d_member(self.company, f'{slug}_contador', [
            'company.view', 'inventory.view', 'inventory.adjust', 'costs.view', 'products.view'])
        self.counter_api = client_for(self.counter)

    def apply_with(self, payload, updates, client=None, expect=200):
        response = (client or self.loader_api).post(apply_url(payload['id']), {'price_updates': updates},
                                                    format='json')
        self.assertEqual(response.status_code, expect, response.content)
        return response.json()

    def review(self, payload):
        return {entry['variant_id']: entry for entry in payload['summary']['price_review']}

    def warnings(self, payload):
        return {row['row']: ' '.join(row['warnings']) for row in payload['rows'] if row['warnings']}

    def price_of(self, product):
        return Product.objects.get(pk=product.pk).price


# ---------------------------------------------------------------------------
# The template
# ---------------------------------------------------------------------------

class NewTemplatePriceColumnTest(NewPriceShop, TestCase):
    def setUp(self):
        self.build('newprice-tpl')

    def test_the_new_template_carries_an_optional_sale_price_after_the_cost(self):
        headers = [header for header, _field in unit_import.STOCK_TEMPLATES['new']['columns']]
        self.assertIn('Precio de venta', headers)
        self.assertEqual(headers.index('Precio de venta'), headers.index('Costo') + 1)
        self.assertNotIn('sale_price', unit_import.USED_TEMPLATE_FIELDS)

    def test_the_used_template_keeps_its_columns_in_the_same_order(self):
        self.assertEqual([header for header, _field in unit_import.STOCK_TEMPLATES['used']['columns']], [
            'Sucursal', 'Modelo', 'Capacidad', 'Color', 'SIM', 'Versión (región / país de venta)', 'IMEI 1', 'IMEI 2',
            'Serie', 'Proveedor', 'Costo', 'Precio de venta', 'Estado estético', 'Salud de batería (%)',
            'Estado funcional', 'Defectos', 'Accesorios', 'Garantía (días)', 'Detalles para el comprador',
            'Fecha de ingreso', 'Observaciones (notas internas)',
        ])

    def test_the_header_note_and_the_instructions_say_it_is_never_applied_alone(self):
        from io import BytesIO

        from openpyxl import load_workbook

        book = load_workbook(BytesIO(self.template('new').content))
        sheet = book.worksheets[0]
        headers = [cell.value for cell in sheet[1]]
        note = sheet.cell(row=1, column=headers.index('Precio de venta') + 1).comment.text
        self.assertIn('Opcional', note)
        self.assertIn('variante', note)
        self.assertIn('costo', note.lower())
        help_text = ' '.join(str(cell.value) for row in book['Instrucciones'].iter_rows() for cell in row
                             if cell.value)
        self.assertIn('no cambia solo', help_text)
        self.assertIn('confirm', help_text)

    def test_the_new_template_still_refuses_the_other_used_only_columns(self):
        for header in ('Garantía (días)', 'Estado estético', 'Salud de batería (%)'):
            with self.subTest(header):
                upload = filled(self.template('new').content, [self.new_row('NP-0001')], extra_headers=[header])
                response = self.loader_api.post(PREVIEW_URL, {'file': upload, 'reason': 'X'}, format='multipart')
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn(header, response.json()['detail'])


# ---------------------------------------------------------------------------
# The preview: the difference, its reach and the proposal — never a write
# ---------------------------------------------------------------------------

class NewTemplatePricePreviewTest(NewPriceShop, TestCase):
    def setUp(self):
        self.build('newprice-prev')

    def test_an_equal_price_proposes_nothing(self):
        payload = self.preview('new', [self.new_row('NP-0101', **{'Precio de venta': 3000})])
        self.assertEqual(payload['counts']['error'], 0, payload['rows'])
        self.assertEqual(payload['summary']['price_review'], [])
        self.assertNotIn(3, self.warnings(payload))

    def test_a_different_price_is_a_row_warning_and_a_proposal_and_writes_nothing(self):
        self.receive(self.new128, 2)                        # two already on the shelf
        payload = self.preview('new', [
            self.new_row('NP-0201', **{'Precio de venta': 3199.9}),
            self.new_row('NP-0202', **{'Precio de venta': '3199.90'}),
            self.new_row('NP-0203'),                         # no price: same variant, counted in the file
        ])
        self.assertEqual(payload['counts'], {**payload['counts'], 'create': 3, 'error': 0}, payload['rows'])
        warning = self.warnings(payload)[3]
        for said in ('Teléfono X 128 GB Negro', '3000.00', '3199.90', '+199.90', 'No se aplica solo'):
            self.assertIn(said, warning)
        self.assertNotIn(5, self.warnings(payload))
        entry = self.review(payload)[self.new128.pk]
        self.assertEqual(entry, {
            'variant_id': self.new128.pk, 'name': 'Teléfono X 128 GB Negro', 'current_price': '3000.00',
            'has_price': True, 'file_prices': ['3199.90'], 'conflict': False, 'proposed_price': '3199.90',
            'difference': '199.90', 'units_in_file': 3, 'rows_with_price': 2, 'units_in_stock': 2,
            'is_published_online': True, 'is_sold_online': True, 'pending_draft': False,
        })
        self.assertEqual(self.price_of(self.new128), Decimal('3000.00'))

    def test_two_prices_for_one_variant_are_a_conflict_without_a_proposal(self):
        payload = self.preview('new', [
            self.new_row('NP-0301', **{'Precio de venta': 3100}),
            self.new_row('NP-0302', **{'Precio de venta': 3000}),
        ])
        self.assertEqual(payload['counts']['error'], 0, payload['rows'])
        entry = self.review(payload)[self.new128.pk]
        self.assertTrue(entry['conflict'])
        self.assertIsNone(entry['proposed_price'])
        self.assertIsNone(entry['difference'])
        self.assertEqual(entry['file_prices'], ['3000.00', '3100.00'])

    def test_a_zero_or_unreadable_price_is_a_row_error(self):
        payload = self.preview('new', [
            self.new_row('NP-0401', **{'Precio de venta': 0}),
            self.new_row('NP-0402', **{'Precio de venta': 'tres mil'}),
            self.new_row('NP-0403', **{'Precio de venta': -5}),
            self.new_row('NP-0404', **{'Precio de venta': '12.345'}),
        ])
        errors = self.errors(payload)
        self.assertIn('mayor que cero', errors[3])
        for row in (4, 5, 6):
            self.assertIn('precio de venta', errors[row].lower())

    def test_a_variant_without_a_price_says_so_and_is_never_enabled_for_sale(self):
        Product.objects.filter(pk=self.new256.pk).update(price=Decimal('0.00'), draft_origin='palette',
                                                         is_published_online=False)
        payload = self.preview('new', [self.new_row('NP-0501', capacity='256 GB', **{'Precio de venta': 3600})])
        self.assertEqual(payload['counts']['error'], 0, payload['rows'])
        self.assertIn('no tiene precio', self.warnings(payload)[3])
        entry = self.review(payload)[self.new256.pk]
        self.assertEqual((entry['has_price'], entry['pending_draft'], entry['is_sold_online'], entry['proposed_price']),
                         (False, True, False, '3600.00'))
        self.apply_with(payload, [{'variant_id': self.new256.pk, 'price': '3600.00', 'confirm': True}])
        variant = Product.objects.get(pk=self.new256.pk)
        self.assertEqual(variant.price, Decimal('3600.00'))
        self.assertIsNone(variant.sale_enabled_at)
        self.assertFalse(variant.is_published_online)

    def test_the_cost_is_never_an_implicit_price(self):
        payload = self.preview('new', [self.new_row('NP-0601', Costo=3999)])
        self.assertEqual(payload['counts']['error'], 0, payload['rows'])
        self.assertEqual(payload['summary']['price_review'], [])
        self.assertNotIn('file_price', payload['rows'][-1]['data'])
        self.apply(payload)
        self.assertEqual(self.price_of(self.new128), Decimal('3000.00'))
        unit = StockUnit.objects.get(serial_number='NP-0601')
        self.assertEqual(unit.cost, Decimal('3999.00'))
        self.assertIsNone(unit.sale_price)

    def test_a_caller_without_products_manage_sees_the_difference_read_only(self):
        payload = self.preview('new', [self.new_row('NP-0701', **{'Precio de venta': 3333})], client=self.counter_api)
        self.assertEqual(payload['counts']['error'], 0, payload['rows'])
        self.assertIn('3333.00', self.warnings(payload)[3])
        self.assertEqual(self.review(payload)[self.new128.pk]['proposed_price'], '3333.00')
        self.assertFalse(payload['can_update_prices'])
        self.assertTrue(self.preview('new', [self.new_row('NP-0702')])['can_update_prices'])

    def test_the_combined_template_still_ignores_a_new_devices_price(self):
        self.assertIn('Precio de venta', unit_import.HEADERS)
        upload = filled(unit_import.template_bytes([self.branch.name]), [{
            'Modelo': 'Teléfono X', 'Capacidad': '128 GB', 'Color': 'Negro', 'Sucursal': self.branch.name,
            'IMEI': next(self._imeis), 'Número de serie': 'NP-0801', 'Línea': 'Nuevo', 'Precio de venta': 3500,
        }], keep_example=False)
        response = self.loader_api.post(PREVIEW_URL, {'file': upload, 'reason': 'X', 'line': 'new'}, format='multipart')
        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()
        self.assertIn('Precio de venta ignorado', self.warnings(payload)[2])
        self.assertNotIn('price_review', payload['summary'])


# ---------------------------------------------------------------------------
# The register: units exactly as before; the price only with authority and confirmation
# ---------------------------------------------------------------------------

class NewTemplatePriceApplyTest(NewPriceShop, TestCase):
    def setUp(self):
        self.build('newprice-apply')

    def staged(self, client=None, price=3199.9, serial='NP-1001'):
        return self.preview('new', [self.new_row(serial, **{'Precio de venta': price})], client=client)

    def test_without_a_confirmed_update_the_variant_price_is_untouched(self):
        payload = self.staged()
        applied = self.apply(payload)
        self.assertEqual(applied['summary']['applied']['units'], 1)
        self.assertEqual(self.price_of(self.new128), Decimal('3000.00'))
        unit = StockUnit.objects.get(serial_number='NP-1001')
        self.assertEqual(unit.product_id, self.new128.pk)
        self.assertIsNone(unit.sale_price)
        self.assertFalse(AdminAuditLog.objects.filter(action='product_updated', target_id=str(self.new128.pk)).exists())

    def test_a_confirmed_update_changes_the_price_with_the_units_and_is_audited(self):
        payload = self.staged()
        applied = self.apply_with(payload, [{'variant_id': self.new128.pk, 'price': '3199.90', 'confirm': True}])
        self.assertEqual(applied['summary']['applied']['units'], 1)
        self.assertEqual(applied['summary']['applied']['price_updates'], [
            {'variant_id': self.new128.pk, 'old_price': '3000.00', 'new_price': '3199.90'}])
        self.assertEqual(self.price_of(self.new128), Decimal('3199.90'))
        self.assertIsNone(StockUnit.objects.get(serial_number='NP-1001').sale_price)
        log = AdminAuditLog.objects.get(action='product_updated', target_id=str(self.new128.pk))
        self.assertEqual(log.company_id, self.company.pk)
        self.assertEqual(log.metadata['changed_fields'], {'price': {'old': '3000.00', 'new': '3199.90'}})
        self.assertEqual((log.metadata['via'], log.metadata['job_id']), ('stock_import', payload['id']))
        load = AdminAuditLog.objects.get(action='stock_units_import_applied', target_id=str(payload['id']))
        self.assertEqual(load.metadata['price_updates'], 1)

    def test_without_products_manage_an_update_is_refused_403_and_nothing_is_registered(self):
        payload = self.staged(client=self.counter_api)
        refused = self.apply_with(payload, [{'variant_id': self.new128.pk, 'price': '3199.90', 'confirm': True}],
                                  client=self.counter_api, expect=403)
        self.assertEqual(refused['code'], unit_import.PRICES_FORBIDDEN)
        self.assertFalse(StockUnit.objects.filter(serial_number='NP-1001').exists())
        self.assertEqual(self.price_of(self.new128), Decimal('3000.00'))
        # The same job, registered without the update, enters as always.
        self.apply(payload, client=self.counter_api)
        self.assertTrue(StockUnit.objects.filter(serial_number='NP-1001').exists())
        self.assertEqual(self.price_of(self.new128), Decimal('3000.00'))

    def test_an_update_that_is_not_the_proposal_is_refused_and_writes_nothing(self):
        payload = self.staged()
        bad = [
            [{'variant_id': self.new128.pk, 'price': '3500.00', 'confirm': True}],          # not the file's price
            [{'variant_id': self.new128.pk, 'price': '3199.90'}],                            # no confirmation
            [{'variant_id': self.new128.pk, 'price': '3199.90', 'confirm': 'true'}],         # not an explicit True
            [{'variant_id': self.new256.pk, 'price': '3199.90', 'confirm': True}],           # not in the review
            [{'variant_id': self.new128.pk, 'price': '3199.90', 'confirm': True}] * 2,       # twice
            {'variant_id': self.new128.pk},                                                  # not a list
            [{'variant_id': 'x', 'price': '3199.90', 'confirm': True}],
        ]
        for updates in bad:
            with self.subTest(updates):
                refused = self.apply_with(payload, updates, expect=400)
                self.assertEqual(refused['code'], unit_import.PRICE_UPDATE_INVALID)
        self.assertFalse(StockUnit.objects.filter(serial_number='NP-1001').exists())
        self.assertEqual(self.price_of(self.new128), Decimal('3000.00'))

    def test_a_conflicting_variant_cannot_be_updated(self):
        payload = self.preview('new', [
            self.new_row('NP-1101', **{'Precio de venta': 3100}),
            self.new_row('NP-1102', **{'Precio de venta': 3200}),
        ])
        refused = self.apply_with(payload, [{'variant_id': self.new128.pk, 'price': '3100.00', 'confirm': True}],
                                  expect=400)
        self.assertEqual(refused['code'], unit_import.PRICE_UPDATE_INVALID)

    def test_a_price_changed_since_the_preview_is_409_and_nothing_is_written(self):
        payload = self.staged()
        Product.objects.filter(pk=self.new128.pk).update(price=Decimal('3050.00'))
        refused = self.apply_with(payload, [{'variant_id': self.new128.pk, 'price': '3199.90', 'confirm': True}],
                                  expect=409)
        self.assertEqual(refused['code'], 'preview_stale')
        self.assertIn('3050.00', refused['detail'])
        self.assertFalse(StockUnit.objects.filter(serial_number='NP-1001').exists())
        self.assertEqual(self.price_of(self.new128), Decimal('3050.00'))

    def test_an_applied_job_cannot_replay_its_proposal(self):
        payload = self.staged()
        self.apply(payload)
        self.apply_with(payload, [{'variant_id': self.new128.pk, 'price': '3199.90', 'confirm': True}])
        self.assertEqual(self.price_of(self.new128), Decimal('3000.00'))
        self.assertEqual(StockUnit.objects.filter(serial_number='NP-1001').count(), 1)

    def test_an_expired_preview_refuses_the_update_too(self):
        from datetime import timedelta

        from django.utils import timezone

        payload = self.staged()
        BulkImportJob.objects.filter(pk=payload['id']).update(created_at=timezone.now() - timedelta(hours=3))
        refused = self.apply_with(payload, [{'variant_id': self.new128.pk, 'price': '3199.90', 'confirm': True}],
                                  expect=409)
        self.assertEqual(refused['code'], 'preview_expired')
        self.assertEqual(self.price_of(self.new128), Decimal('3000.00'))


# ---------------------------------------------------------------------------
# Companies and branches
# ---------------------------------------------------------------------------

class NewTemplatePriceIsolationTest(NewPriceShop, TestCase):
    def setUp(self):
        self.build('newprice-iso')
        self.other = NewPriceShop()
        self.other.assertEqual = self.assertEqual
        self.other.build('newprice-otra')

    def test_a_variant_of_another_company_never_resolves_nor_changes(self):
        # A model that exists only in the other company: the row is an error, nothing is proposed.
        device_catalog.create_model(company=self.other.company, name='Teléfono Solo Otra')
        payload = self.preview('new', [self.new_row('NP-2001', model='Teléfono Solo Otra',
                                                    **{'Precio de venta': 999})])
        self.assertEqual(payload['counts']['error'], 1, payload['rows'])
        self.assertEqual(payload['summary']['price_review'], [])
        # The same model name in both companies resolves to THIS company's variant only.
        payload = self.preview('new', [self.new_row('NP-2002', **{'Precio de venta': 3199.9})])
        self.assertEqual(set(self.review(payload)), {self.new128.pk})
        # An id of the other company's variant is not in the review: refused, nothing written anywhere.
        refused = self.apply_with(payload, [{'variant_id': self.other.new128.pk, 'price': '3199.90',
                                             'confirm': True}], expect=400)
        self.assertEqual(refused['code'], unit_import.PRICE_UPDATE_INVALID)
        self.assertEqual(self.price_of(self.other.new128), Decimal('3000.00'))
        self.apply_with(payload, [{'variant_id': self.new128.pk, 'price': '3199.90', 'confirm': True}])
        self.assertEqual(self.price_of(self.new128), Decimal('3199.90'))
        self.assertEqual(self.price_of(self.other.new128), Decimal('3000.00'))

    def test_a_job_of_another_company_is_not_found(self):
        payload = self.staged_other()
        response = self.loader_api.post(apply_url(payload['id']), {'price_updates': [
            {'variant_id': self.other.new128.pk, 'price': '3199.90', 'confirm': True}]}, format='json')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.price_of(self.other.new128), Decimal('3000.00'))

    def staged_other(self):
        return self.other.preview('new', [self.other.new_row('NP-2101', **{'Precio de venta': 3199.9})])

    def test_units_in_stock_count_only_the_callers_branches(self):
        self.receive(self.new128, 1)                                   # main branch
        from store.stock_unit_services import receive_units

        receive_units(branch=self.north, product=self.new128, actor=self.staff, reason='Compra',
                      units=[{'serial_number': 'NORTE-1', 'imei': next(self._imeis)}])
        scoped, _ = _p2d_member(self.company, 'newprice_iso_norte', [
            'company.view', 'inventory.view', 'inventory.adjust'], branches=[self.north])
        payload = self.preview('new', [self.new_row('NP-2201', branch=self.north, **{'Precio de venta': 3199.9})],
                               client=client_for(scoped))
        self.assertEqual(self.review(payload)[self.new128.pk]['units_in_stock'], 1)
        payload = self.preview('new', [self.new_row('NP-2202', **{'Precio de venta': 3199.9})])
        self.assertEqual(self.review(payload)[self.new128.pk]['units_in_stock'], 2)
