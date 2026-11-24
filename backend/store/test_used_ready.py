"""
USED-READY-01 — a used device is sold as THAT device, and only once somebody checked it.

What these tests hold:

  * the web offers a used device only when it is AVAILABLE, has its own price > 0, an APPROVED inspection, is itself
    PUBLISHED and has at least ONE real photo — each condition alone hides it, on the model page, the model list, the
    cart's hold and the panel's «En línea»;
  * the till sells a used device only once its inspection is approved (pending and rejected are refused, each with its
    own sentence); publication is not asked at the till;
  * publishing is refused with 409 and every reason; it needs `products.manage`; inspecting needs
    `inventory.units.inspect` (not `inventory.adjust`); both are audited with the company; another company's device
    is a 404;
  * warranty, functional state, defects and accessories: edited on the ficha (the warranty with the price's
    authority), only for used devices, and shown to the buyer;
  * a reception or an import NEVER inspects or publishes; a returned device is inspected again;
  * nothing of this moves stock: `BranchStock.quantity == count(AVAILABLE)` and the Kardex are untouched;
  * migration 0187 grandfathers what the web offered before it (and the photo rule is read at query time).
"""
from decimal import Decimal

from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings

from store import device_catalog, unit_hold_services as holds
from store import stock_unit_services as units
from store import used_unit_services as used_units
from store.models import (
    AdminAuditLog, BranchStock, Product, StockMovement, StockUnit, UnitPhoto,
)
from store.pos_services import PosValidationError
from store import inventory_services
from store.test_device_commerce import DeviceShop, SaleShop, _client
from store.test_pos_unit_price import UsedBase
from store.test_stock_units import IMEI_A, IMEI_B, IMEI_C
from rest_framework.test import APIClient

from store.test_pos_reconcile import _web_login
from store.tests import Ip1PosBase, _p2d_member, _p3_company
from store.used_unit_test_support import real_photo

UNITS = '/api/admin/inventory/units/'


def photo(unit):
    """UNIT-PHOTO-02: a REAL photo (attested, own and verified) — what the web counts."""
    return real_photo(unit)


def make_ready(unit, actor=None, price='1999.00'):
    """Price, approve, photograph and publish one used device: what the web needs."""
    units.update_unit(unit, sale_price=price)
    used_units.set_inspection(unit, decision='approved', actor=actor)
    photo(unit)
    used_units.set_publication(unit, published=True, actor=actor)
    unit.refresh_from_db()
    return unit


class UsedShop(DeviceShop):
    def build(self, slug='used-ready'):
        super().build(slug)
        self.inspector, _ = _p2d_member(self.company, f'{slug}_inspector', [
            'company.view', 'inventory.view', 'inventory.units.inspect'])
        self.stocker, _ = _p2d_member(self.company, f'{slug}_stocker', [
            'company.view', 'inventory.view', 'inventory.adjust'])

    def offered(self):
        return set(device_catalog.offered_units([self.used128], self.branch).values_list('pk', flat=True))


# ---------------------------------------------------------------------------
# The web rule
# ---------------------------------------------------------------------------

@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class WebRuleTest(UsedShop, TestCase):
    def setUp(self):
        self.build()
        [self.unit] = self.receive(self.used128)
        make_ready(self.unit, self.staff)

    def test_a_ready_device_is_offered_everywhere(self):
        self.assertEqual(self.offered(), {self.unit.pk})
        public = _client(host=self.host)
        cards = public.get(f'/api/catalog/models/{self.model.slug}/').json()['used_units']
        self.assertEqual([card['id'] for card in cards], [str(self.unit.public_id)])
        listed = public.get('/api/catalog/models/', {'line': 'used'}).json()['results']
        self.assertEqual([(m['slug'], m['sold_out']) for m in listed], [(self.model.slug, False)])

    def test_each_condition_alone_hides_it(self):
        cases = {
            'no_price': dict(sale_price=None),
            'zero_price': dict(sale_price=Decimal('0.00')),
            'pending': dict(inspection_status='pending', published_online=False),
            'rejected': dict(inspection_status='rejected', published_online=False),
            'unpublished': dict(published_online=False),
            'reserved': dict(status='reserved'),
        }
        for label, change in cases.items():
            with self.subTest(label), transaction.atomic():
                StockUnit.objects.filter(pk=self.unit.pk).update(**change)
                self.assertEqual(self.offered(), set(), label)
                transaction.set_rollback(True)
        with self.subTest('no_photo'), transaction.atomic():
            UnitPhoto.objects.filter(unit=self.unit).delete()
            self.assertEqual(self.offered(), set())
            transaction.set_rollback(True)
        self.assertEqual(self.offered(), {self.unit.pk})

    def test_deleting_the_last_photo_takes_it_off_the_web_without_unpublishing(self):
        UnitPhoto.objects.filter(unit=self.unit).delete()
        self.unit.refresh_from_db()
        self.assertTrue(self.unit.published_online)
        body = _client(host=self.host).get(f'/api/catalog/models/{self.model.slug}/').json()
        self.assertEqual(body['used_units'], [])
        self.assertTrue(body['sold_out'])

    def test_new_devices_are_unaffected(self):
        [new] = self.receive(self.new128)
        self.assertEqual(new.inspection_status, 'pending')
        offered = device_catalog.offered_units([self.new128], self.branch).values_list('pk', flat=True)
        self.assertEqual(list(offered), [new.pk])

    def test_the_public_card_carries_warranty_functional_state_defects_and_accessories(self):
        units.update_unit(self.unit, warranty_days=90, functional_state='with_faults',
                          defects='Face ID no funciona', accessories='Caja y cable')
        [card] = _client(host=self.host).get(f'/api/catalog/models/{self.model.slug}/').json()['used_units']
        self.assertEqual(
            {k: card[k] for k in ('warranty_days', 'functional_state', 'functional_state_label', 'defects',
                                  'accessories')},
            {'warranty_days': 90, 'functional_state': 'with_faults', 'functional_state_label': 'Con fallas declaradas',
             'defects': 'Face ID no funciona', 'accessories': 'Caja y cable'})
        # Staff-only data never leaves.
        for key in ('inspection_status', 'inspection_note', 'published_online', 'cost', 'serial_number'):
            self.assertNotIn(key, card)

    def test_without_its_own_warranty_the_card_says_null_and_the_shop_shows_its_policy(self):
        [card] = _client(host=self.host).get(f'/api/catalog/models/{self.model.slug}/').json()['used_units']
        self.assertIsNone(card['warranty_days'])
        self.assertIsNone(card['functional_state'])

    def test_the_cart_holds_only_a_ready_device(self):
        [other] = self.receive(self.used128)
        units.update_unit(other, sale_price='1500.00')             # priced, but neither inspected nor published
        public = _client(host=self.host)
        refused = public.post('/api/cart/add/', {'session_key': 'carro-x', 'product': self.used128.pk,
                                                 'unit': str(other.public_id)}, format='json')
        self.assertEqual(refused.status_code, 404, refused.content)
        other.refresh_from_db()
        self.assertEqual(other.status, 'available')
        held = public.post('/api/cart/add/', {'session_key': 'carro-x', 'product': self.used128.pk,
                                              'unit': str(self.unit.public_id)}, format='json')
        self.assertEqual(held.status_code, 200, held.content)

    def test_hold_refuses_a_device_whose_photo_went_away(self):
        UnitPhoto.objects.filter(unit=self.unit).delete()
        with self.assertRaises(holds.HoldError):
            holds.hold(product=self.used128, branch=self.branch, session_key='carro-y',
                       unit_public_id=str(self.unit.public_id))

    def test_the_panel_says_every_reason_and_agrees_with_the_model_page(self):
        [fresh] = self.receive(self.used128)
        rows = {r['id']: r for r in _client(self.staff).get(UNITS, {'line': 'used'}).json()['results']}
        self.assertEqual(rows[self.unit.pk]['web'], {'offered': True, 'reasons': []})
        self.assertEqual(rows[fresh.pk]['web']['reasons'], ['no_price', 'not_inspected', 'unit_not_published', 'no_photo'])
        said = {pk for pk, row in rows.items() if row['web']['offered']}
        self.assertEqual(said, self.offered())


# ---------------------------------------------------------------------------
# Inspection and publication, through the API
# ---------------------------------------------------------------------------

class InspectionApiTest(UsedShop, TestCase):
    def setUp(self):
        self.build()
        [self.unit] = self.receive(self.used128)

    def inspect(self, user, body, unit=None):
        return _client(user).post(f'{UNITS}{(unit or self.unit).pk}/inspection/', body, format='json')

    def test_a_received_device_is_pending_and_unpublished(self):
        self.assertEqual((self.unit.inspection_status, self.unit.published_online), ('pending', False))

    def test_inventory_adjust_does_not_approve_an_inspection(self):
        res = self.inspect(self.stocker, {'decision': 'approved'})
        self.assertEqual(res.status_code, 403, res.content)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.inspection_status, 'pending')

    def test_the_inspector_approves_and_the_audit_names_the_company(self):
        res = self.inspect(self.inspector, {'decision': 'approved', 'note': 'Revisado: todo bien'})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((res.json()['inspection_status'], res.json()['inspection_note']),
                         ('approved', 'Revisado: todo bien'))
        self.unit.refresh_from_db()
        self.assertEqual((self.unit.inspected_by_id, self.unit.inspected_at is not None), (self.inspector.pk, True))
        log = AdminAuditLog.objects.get(action='stock_unit_inspection', target_id=str(self.unit.pk))
        self.assertEqual((log.company_id, log.metadata['decision'], log.metadata['previous']),
                         (self.company.pk, 'approved', 'pending'))

    def test_a_rejection_needs_its_reason_and_takes_the_device_off_the_web(self):
        make_ready(self.unit, self.staff)
        res = self.inspect(self.inspector, {'decision': 'rejected'})
        self.assertEqual((res.status_code, res.json().get('field')), (400, 'note'))
        res = self.inspect(self.inspector, {'decision': 'rejected', 'note': 'Pantalla con manchas'})
        self.assertEqual(res.status_code, 200, res.content)
        self.unit.refresh_from_db()
        self.assertEqual((self.unit.inspection_status, self.unit.published_online), ('rejected', False))
        self.assertEqual(self.offered(), set())
        log = AdminAuditLog.objects.filter(action='stock_unit_inspection').latest('pk')
        self.assertTrue(log.metadata['unpublished'])

    def test_an_unknown_decision_a_new_device_and_a_sold_one_are_refused(self):
        self.assertEqual(self.inspect(self.inspector, {'decision': 'quizas'}).status_code, 400)
        [new] = self.receive(self.new128)
        res = self.inspect(self.inspector, {'decision': 'approved'}, unit=new)
        self.assertEqual((res.status_code, res.json().get('code')), (400, 'not_used_line'))
        StockUnit.objects.filter(pk=self.unit.pk).update(status='sold')
        res = self.inspect(self.inspector, {'decision': 'approved'})
        self.assertEqual((res.status_code, res.json().get('code')), (409, 'not_in_stock'))

    def test_another_companys_device_is_a_404(self):
        other = _p3_company('otra-used-ready', 'Otra')
        foreign, _ = _p2d_member(other, 'otra_inspector', ['company.view', 'inventory.view', 'inventory.units.inspect',
                                                            'inventory.adjust', 'products.manage'])
        self.assertEqual(self.inspect(foreign, {'decision': 'approved'}).status_code, 404)
        res = _client(foreign).post(f'{UNITS}{self.unit.pk}/publication/', {'published': True}, format='json')
        self.assertEqual(res.status_code, 404)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.inspection_status, 'pending')

    def test_the_list_filters_by_inspection_and_says_who_may_inspect(self):
        used_units.set_inspection(self.unit, decision='approved', actor=self.inspector)
        [pending] = self.receive(self.used128)
        body = _client(self.staff).get(UNITS, {'line': 'used', 'inspection': 'pending'}).json()
        self.assertEqual([r['id'] for r in body['results']], [pending.pk])
        self.assertFalse(body['can_inspect'])
        self.assertTrue(_client(self.inspector).get(UNITS, {'line': 'used'}).json()['can_inspect'])
        self.assertEqual(_client(self.staff).get(UNITS, {'inspection': 'x'}).status_code, 400)


class PublicationApiTest(UsedShop, TestCase):
    def setUp(self):
        self.build()
        [self.unit] = self.receive(self.used128)

    def publish(self, user, published=True):
        return _client(user).post(f'{UNITS}{self.unit.pk}/publication/', {'published': published}, format='json')

    def test_publish_is_refused_with_every_reason(self):
        res = self.publish(self.staff)
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'not_ready')
        self.assertEqual(res.json()['reasons'], ['no_price', 'not_inspected', 'no_photo'])
        units.update_unit(self.unit, sale_price='1800.00')
        self.assertEqual(self.publish(self.staff).json()['reasons'], ['not_inspected', 'no_photo'])
        used_units.set_inspection(self.unit, decision='rejected', note='Golpe', actor=self.inspector)
        self.assertEqual(self.publish(self.staff).json()['reasons'], ['inspection_rejected', 'no_photo'])
        used_units.set_inspection(self.unit, decision='approved', actor=self.inspector)
        self.assertEqual(self.publish(self.staff).json()['reasons'], ['no_photo'])
        photo(self.unit)
        res = self.publish(self.staff)
        self.assertEqual((res.status_code, res.json()['published_online']), (200, True))
        log = AdminAuditLog.objects.get(action='stock_unit_published', target_id=str(self.unit.pk))
        self.assertEqual(log.company_id, self.company.pk)
        res = self.publish(self.staff, False)
        self.assertEqual((res.status_code, res.json()['published_online']), (200, False))
        self.assertTrue(AdminAuditLog.objects.filter(action='stock_unit_unpublished').exists())

    def test_publishing_needs_products_manage(self):
        make_ready(self.unit, self.staff)
        res = self.publish(self.stocker, False)
        self.assertEqual(res.status_code, 403, res.content)
        self.unit.refresh_from_db()
        self.assertTrue(self.unit.published_online)

    def test_a_sold_device_is_not_published(self):
        units.update_unit(self.unit, sale_price='1800.00')
        used_units.set_inspection(self.unit, decision='approved')
        photo(self.unit)
        StockUnit.objects.filter(pk=self.unit.pk).update(status='sold')
        self.assertEqual(self.publish(self.staff).json()['reasons'], ['not_in_stock'])

    def test_the_database_refuses_a_published_device_that_was_not_approved(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            StockUnit.objects.filter(pk=self.unit.pk).update(published_online=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            StockUnit.objects.filter(pk=self.unit.pk).update(inspection_status='approved', inspected_at=None)


# ---------------------------------------------------------------------------
# The ficha: warranty, functional state, defects, accessories
# ---------------------------------------------------------------------------

class UsedDetailsTest(UsedShop, TestCase):
    def setUp(self):
        self.build()
        [self.unit] = self.receive(self.used128)

    def patch(self, user, body, unit=None):
        return _client(user).patch(f'{UNITS}{(unit or self.unit).pk}/', body, format='json')

    def test_the_stockroom_writes_functional_state_defects_and_accessories(self):
        res = self.patch(self.stocker, {'functional_state': 'with_faults', 'defects': 'Cámara trasera borrosa',
                                        'accessories': 'Caja original'})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((res.json()['functional_state'], res.json()['defects'], res.json()['accessories']),
                         ('with_faults', 'Cámara trasera borrosa', 'Caja original'))
        log = AdminAuditLog.objects.filter(action='stock_unit_updated').latest('pk')
        self.assertEqual(sorted(log.metadata['changed']), ['accessories', 'defects', 'functional_state'])

    def test_the_warranty_needs_the_price_authority(self):
        res = self.patch(self.stocker, {'warranty_days': 90})
        self.assertEqual((res.status_code, res.json().get('field')), (403, 'warranty_days'))
        res = self.patch(self.staff, {'warranty_days': 90})
        self.assertEqual((res.status_code, res.json()['warranty_days']), (200, 90))
        res = self.patch(self.staff, {'warranty_days': None})
        self.assertIsNone(res.json()['warranty_days'])

    def test_invalid_values_are_refused(self):
        for body, field in (({'warranty_days': -1}, 'warranty_days'), ({'warranty_days': '3651'}, 'warranty_days'),
                            ({'warranty_days': 1.5}, 'warranty_days'), ({'warranty_days': True}, 'warranty_days'),
                            ({'functional_state': 'roto'}, 'functional_state'),
                            ({'functional_state': 'with_faults'}, 'defects'),
                            ({'defects': 'x' * 501}, 'defects'), ({'accessories': 'x' * 301}, 'accessories')):
            res = self.patch(self.staff, body)
            self.assertEqual((res.status_code, res.json().get('field')), (400, field), body)

    def test_a_new_device_carries_none_of_them(self):
        [new] = self.receive(self.new128)
        res = self.patch(self.staff, {'accessories': 'Caja'}, unit=new)
        self.assertEqual((res.status_code, res.json().get('code')), (400, 'not_used_line'))

    def test_the_inspection_and_publication_are_never_written_by_patch(self):
        res = self.patch(self.staff, {'inspection_status': 'approved', 'published_online': True})
        self.assertEqual(res.status_code, 200)
        self.unit.refresh_from_db()
        self.assertEqual((self.unit.inspection_status, self.unit.published_online), ('pending', False))

    def test_a_reception_stores_them_and_never_inspects_or_publishes(self):
        res = _client(self.staff).post(UNITS, {
            'branch': self.branch.pk, 'product_id': self.used128.pk, 'reason': 'Compra a cliente',
            'units': [{'serial_number': 'USEDREADY1', 'imei': next(self._imei), 'condition': 'used',
                       'sale_price': '1500.00', 'warranty_days': 30, 'functional_state': 'full',
                       'accessories': 'Cargador', 'inspection_status': 'approved', 'published_online': True}],
        }, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        unit = StockUnit.objects.get(serial_number='USEDREADY1')
        self.assertEqual((unit.warranty_days, unit.functional_state, unit.accessories), (30, 'full', 'Cargador'))
        self.assertEqual((unit.inspection_status, unit.published_online), ('pending', False))


# ---------------------------------------------------------------------------
# The unit import
# ---------------------------------------------------------------------------

USED_COLUMNS = ['Garantía (días)', 'Estado funcional', 'Defectos', 'Accesorios']
UNIT_HEADERS = ['Código', 'Producto', 'Sucursal', 'Número de serie', 'IMEI', 'IMEI 2', 'Condición', 'Costo',
                'Motivo / referencia', 'Modelo', 'Capacidad', 'Color', 'SIM', 'Región', 'Línea',
                'Estado estético', 'Batería %', 'Precio de venta', *USED_COLUMNS]


class UnitImportTest(UsedShop, TestCase):
    def setUp(self):
        self.build()

    def units_file(self, rows):
        from store import unit_import_services
        from store.test_cost_imports import upload
        job = unit_import_services.preview_units(
            company=self.company, actor=self.staff, upload=upload(UNIT_HEADERS, rows, title='Equipos'),
            filename='equipos.xlsx', reachable_branch_ids=[self.branch.pk], default_branch=self.branch.pk,
            default_reason='Compra',
        )
        return job, list(job.rows.order_by('row_number'))

    def test_the_template_offers_the_optional_used_columns_last(self):
        import io
        import openpyxl
        from store import unit_import_services
        sheet = openpyxl.load_workbook(io.BytesIO(unit_import_services.template_bytes())).active
        self.assertEqual([c.value for c in sheet[1]][-4:], USED_COLUMNS)

    def test_an_import_with_a_price_stores_the_details_and_never_inspects_or_publishes(self):
        from store import unit_import_services
        job, [row] = self.units_file([[
            '', '', '', 'IMPUSED01', next(self._imei), '', 'Usado', '', 'Compra', 'Teléfono X', '128 GB', 'Azul', '',
            '', 'Seminuevo', '', '90', '1899.90', '60', 'Con fallas', 'Botón lateral duro', 'Caja']])
        self.assertEqual(row.errors, [])
        unit_import_services.apply_units(job=job, actor=self.staff)
        unit = StockUnit.objects.get(serial_number='IMPUSED01')
        self.assertEqual((unit.sale_price, unit.warranty_days, unit.functional_state, unit.defects, unit.accessories),
                         (Decimal('1899.90'), 60, 'with_faults', 'Botón lateral duro', 'Caja'))
        self.assertEqual((unit.inspection_status, unit.published_online), ('pending', False))
        self.assertEqual(self.offered(), set())

    def test_bad_values_are_row_errors_and_a_new_device_ignores_them_with_a_warning(self):
        job, rows = self.units_file([
            ['', '', '', 'IMPUSED02', next(self._imei), '', '', '', 'Compra', 'Teléfono X', '128 GB', 'Azul', '', '',
             'Seminuevo', '', '', '', '-3', '', '', ''],
            ['', '', '', 'IMPUSED03', next(self._imei), '', '', '', 'Compra', 'Teléfono X', '128 GB', 'Azul', '', '',
             'Seminuevo', '', '', '', '', 'Con fallas', '', ''],
            ['', '', '', 'IMPNEW04', next(self._imei), '', '', '', 'Compra', 'Teléfono X', '128 GB', 'Negro', '', '',
             'Nuevo', '', '', '', '30', '', '', 'Caja'],
        ])
        self.assertIn('garantía', ' '.join(rows[0].errors).lower())
        self.assertIn('Defectos', ' '.join(rows[1].errors))
        self.assertEqual(rows[2].errors, [])
        self.assertTrue(any('ignorado en un equipo nuevo' in w for w in rows[2].warnings))
        self.assertNotIn('warranty_days', rows[2].normalized_data)


# ---------------------------------------------------------------------------
# The till
# ---------------------------------------------------------------------------

class TillTest(UsedBase):
    def setUp(self):
        super().setUp()
        self.unit, = self.receive_used(self.row('TILLUSED01', IMEI_A, condition='used', sale_price='2300.00'),
                                       inspected=False)

    def test_a_pending_device_is_refused_with_its_reason(self):
        with self.assertRaises(PosValidationError) as caught:
            self.sell([self.line(self.unit)])
        self.assertIn('inspección aprobada', str(caught.exception))
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.status, 'available')

    def test_a_rejected_device_is_refused_with_its_reason(self):
        used_units.set_inspection(self.unit, decision='rejected', note='Placa dañada')
        with self.assertRaises(PosValidationError) as caught:
            self.sell([self.line(self.unit)])
        self.assertIn('rechazada', str(caught.exception))

    def test_an_approved_device_sells_without_being_published(self):
        used_units.set_inspection(self.unit, decision='approved')
        order, created = self.sell([self.line(self.unit)])
        self.assertTrue(created)
        self.assertEqual(order.items.get().price, Decimal('2300.00'))
        self.assert_invariant(self.used)

    def test_a_returned_device_is_inspected_again(self):
        used_units.set_inspection(self.unit, decision='approved')
        photo(self.unit)
        used_units.set_publication(self.unit, published=True)
        self.sell([self.line(self.unit)])
        returned = units.return_unit(self.unit, reason='Cliente lo devolvió')
        self.assertEqual((returned.status, returned.inspection_status, returned.published_online),
                         ('available', 'pending', False))
        self.assertTrue(AdminAuditLog.objects.filter(action='stock_unit_inspection_reset',
                                                     target_id=str(self.unit.pk), company=self.company).exists())
        self.assert_invariant(self.used)


# ---------------------------------------------------------------------------
# Every internal door that would sell or hand over a device
# ---------------------------------------------------------------------------

class InternalPathsApiTest(Ip1PosBase):
    """The web till, the mobile till, the cashier's lookup: a used device without an approved inspection is a 409."""

    def setUp(self):
        super().setUp()
        self.client = _web_login('ip1_caja')
        self.v1 = APIClient()
        self.v1.force_authenticate(self.staff)
        self.v1_base = f'/api/v1/internal/{self.company.slug}/sales/pos/'
        self.used = Product.objects.create(
            company=self.company, name='Equipo seminuevo X', slug='equipo-seminuevo-x', price='0.00',
            inventory=0, is_serialized=True, requires_imei=True, condition_line=Product.CONDITION_USED)
        [self.unit] = units.receive_units(
            branch=self.branch_a, product=self.used, actor=self.staff, reason='Compra',
            units=[{'serial_number': 'INTUSED001', 'imei': IMEI_B, 'condition': 'used', 'sale_price': '2100.00'}])

    def items(self):
        return [{'product': self.used.pk, 'quantity': 1, 'units': [{'id': self.unit.pk, 'method': 'scanner'}]}]

    def test_preview_and_sale_on_both_tills_answer_409_unit_not_inspected(self):
        for client, base in ((self.client, '/api/admin/pos/'), (self.v1, self.v1_base)):
            res = client.post(base + 'preview/', {'branch': self.branch_a.pk, 'items': self.items()}, format='json')
            self.assertEqual((res.status_code, res.json().get('code')), (409, 'unit_not_inspected'), res.content)
            res = client.post(base + 'sales/', self.sale_body(items=self.items(), amount_received='10000.00',
                                                   idempotency_key=f'used-ready-{base[-5:]}'), format='json')
            self.assertEqual((res.status_code, res.json().get('code')), (409, 'unit_not_inspected'), res.content)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.status, 'available')

    def test_the_cashiers_lookup_refuses_it_and_an_approved_one_passes(self):
        url = '/api/admin/pos/units/lookup/'
        res = self.client.get(url, {'code': IMEI_B, 'branch': self.branch_a.pk, 'method': 'scanner'})
        self.assertEqual((res.status_code, res.json().get('code')), (409, 'unit_not_inspected'), res.content)
        used_units.set_inspection(self.unit, decision='approved')
        res = self.client.get(url, {'code': IMEI_B, 'branch': self.branch_a.pk, 'method': 'scanner'})
        self.assertEqual(res.status_code, 200, res.content)

    def test_the_single_stock_writer_refuses_to_sell_it_by_name_or_to_set_it_aside(self):
        from store import inventory_services
        from store.models import Order

        order = Order.objects.create(company=self.company, total=Decimal('2100.00'))
        with self.assertRaises(inventory_services.InvalidMovementError):
            inventory_services.create_stock_movement(
                branch=self.branch_a, product_id=self.used.pk, movement_type=StockMovement.SALE_EXIT, quantity=1,
                reason='Venta', actor=self.staff, order=order, units=[self.unit.pk],
                expected_unit_status='available', sell_units=True)
        # A web payment that sets the oldest aside never takes one without an approved inspection.
        with self.assertRaises(inventory_services.InsufficientStockError):
            inventory_services.create_stock_movement(
                branch=self.branch_a, product_id=self.used.pk, movement_type=StockMovement.SALE_EXIT, quantity=1,
                reason='Venta web', actor=self.staff, order=order, assign_units_for_sale=True)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.status, 'available')

    def test_handing_over_an_order_refuses_it(self):
        from store import unit_identification
        from store.models import Order

        order = Order.objects.create(company=self.company, total=Decimal('2100.00'), status=Order.Status.PAID,
                                     paid=True, fulfillment_branch=self.branch_a)
        order.items.create(product=self.used, quantity=1, price=Decimal('2100.00'))
        with self.assertRaises(unit_identification.UnitIdentificationError) as caught:
            unit_identification.candidate_for_order(order=order, code=IMEI_B, method='scanner')
        self.assertEqual((caught.exception.code, caught.exception.http_status), ('unit_not_inspected', 409))

    def test_a_device_already_set_aside_for_a_paid_order_before_the_rule_can_still_be_named(self):
        # Sold (set aside by the payment) before USED-READY-01 and migrated to «pending»: it was already sold, so the
        # hand-over of that paid order is not blocked — only a device taken from the SHELF must be approved.
        from store import unit_identification
        from store.models import Order

        order = Order.objects.create(company=self.company, total=Decimal('2100.00'), status=Order.Status.PAID,
                                     paid=True, fulfillment_branch=self.branch_a)
        order.items.create(product=self.used, quantity=1, price=Decimal('2100.00'))
        StockUnit.objects.filter(pk=self.unit.pk).update(status=StockUnit.Status.SOLD, order=order)
        found = unit_identification.candidate_for_order(order=order, code=IMEI_B, method='scanner')
        self.assertEqual((found.pk, found.inspection_status), (self.unit.pk, 'pending'))


@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class CheckoutOfAWithdrawnDeviceTest(SaleShop, TestCase):
    """
    A buyer holds a ready device; before checkout the shop withdraws it (rejects its inspection, unpublishes it or
    deletes its last photo). The checkout says the cart changed and creates nothing: no order, no payment.
    """

    def setUp(self):
        super().setUp()
        holds.hold(product=self.used128, branch=self.branch, session_key='carrito-a',
                   unit_public_id=str(self.used_a.public_id))
        self.used_a.refresh_from_db()

    def refused_checkout(self):
        from store import checkout_services
        from store.models import CartItem, Order

        orders = Order.objects.count()
        with self.assertRaises(checkout_services.CheckoutError) as caught:
            self.checkout()
        self.assertEqual(caught.exception.code, 'cart_changed')
        self.assertEqual(Order.objects.count(), orders)
        return caught.exception, CartItem

    def assert_back_on_the_shelf(self):
        self.used_a.refresh_from_db()
        self.assertEqual((self.used_a.status, self.used_a.hold_key, self.used_a.hold_until), ('available', '', None))
        self.assertTrue(AdminAuditLog.objects.filter(action='stock_unit_web_hold_released',
                                                     target_id=str(self.used_a.pk), company=self.company).exists())
        self.assertEqual(self.shelf(self.used128), self.available(self.used128))

    def test_a_rejection_releases_the_hold_and_the_checkout_says_the_cart_changed(self):
        used_units.set_inspection(self.used_a, decision='rejected', note='Pantalla con manchas', actor=self.staff)
        self.assert_back_on_the_shelf()
        self.refused_checkout()

    def test_an_unpublication_releases_the_hold_and_the_checkout_says_the_cart_changed(self):
        used_units.set_publication(self.used_a, published=False, actor=self.staff)
        self.assert_back_on_the_shelf()
        self.refused_checkout()

    def test_without_its_last_photo_the_checkout_says_the_cart_changed(self):
        UnitPhoto.objects.filter(unit=self.used_a).delete()
        self.refused_checkout()
        self.used_a.refresh_from_db()
        self.assertEqual(self.used_a.status, 'reserved')            # still the buyer's until it expires

    def test_a_hold_bound_to_a_pending_order_is_left_to_its_payment(self):
        order = self.checkout()
        used_units.set_publication(self.used_a, published=False, actor=self.staff)
        self.used_a.refresh_from_db()
        self.assertEqual((self.used_a.status, self.used_a.hold_order_id), ('reserved', order.pk))


class PaidHoldOfARejectedDeviceTest(SaleShop, TestCase):
    """A buyer held a ready device; its inspection was rejected before the money arrived: never sold, flagged."""

    def test_the_payment_does_not_sell_it_and_says_why(self):
        holds.hold(product=self.used128, branch=self.branch, session_key='carrito-a',
                   unit_public_id=str(self.used_a.public_id))
        order = self.checkout()
        used_units.set_inspection(self.used_a, decision='rejected', note='Pantalla con manchas')
        self.pay(order)
        order.refresh_from_db()
        self.used_a.refresh_from_db()
        self.assertNotEqual(self.used_a.status, 'sold')
        self.assertTrue(StockUnit.objects.filter(pk=self.used_a.pk, order__isnull=True).exists())
        self.assertEqual([r['product_id'] for r in inventory_services.order_stock_shortfall(order)], [self.used128.pk])


# ---------------------------------------------------------------------------
# Warranty: the device's own, else the company's default, else its policy text
# ---------------------------------------------------------------------------

@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class WarrantyTest(UsedShop, TestCase):
    def setUp(self):
        self.build()
        [self.unit] = self.receive(self.used128)
        make_ready(self.unit, self.staff)

    def card(self):
        [card] = _client(host=self.host).get(f'/api/catalog/models/{self.model.slug}/').json()['used_units']
        return card['warranty_days'], card['warranty_source']

    def test_policy_then_company_default_then_the_devices_own(self):
        from store.models import CompanySettings

        CompanySettings.objects.filter(company=self.company).update(used_warranty_days=None)
        self.assertEqual(self.card(), (None, 'policy'))
        CompanySettings.objects.filter(company=self.company).update(used_warranty_days=90)
        self.assertEqual(self.card(), (90, 'company'))
        units.update_unit(self.unit, warranty_days=0)
        self.assertEqual(self.card(), (0, 'unit'))

    def test_another_companys_default_is_not_read(self):
        from store.models import CompanySettings

        other = _p3_company('otra-garantia', 'Otra garantía')
        CompanySettings.objects.filter(company=other).update(used_warranty_days=365)
        CompanySettings.objects.filter(company=self.company).update(used_warranty_days=None)
        self.assertEqual(self.card(), (None, 'policy'))


class CompanyWarrantySettingTest(UsedShop, TestCase):
    """The company's default is a setting of THAT company, edited like its warranty policy."""

    def setUp(self):
        self.build('used-ready-ajustes')
        self.admin, _ = _p2d_member(self.company, 'used_ready_admin', [
            'company.view', 'company.manage', 'settings.view', 'settings.manage'])

    def patch(self, value):
        return _client(self.admin).patch('/api/admin/company-settings/', {'used_warranty_days': value}, format='json')

    def test_days_empty_and_out_of_range(self):
        from store.models import CompanySettings

        res = self.patch('90')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(CompanySettings.objects.get(company=self.company).used_warranty_days, 90)
        self.assertEqual(self.patch('').status_code, 200)
        self.assertIsNone(CompanySettings.objects.get(company=self.company).used_warranty_days)
        for bad in ('-1', '3651', 'noventa'):
            self.assertEqual(self.patch(bad).status_code, 400, bad)


# ---------------------------------------------------------------------------
# Stock is untouched
# ---------------------------------------------------------------------------

class StockInvariantTest(UsedShop, TestCase):
    def setUp(self):
        self.build()
        self.a, self.b = self.receive(self.used128, 2)

    def test_inspection_and_publication_move_no_stock(self):
        before = (StockMovement.objects.count(), list(BranchStock.objects.values_list('product_id', 'quantity')))
        make_ready(self.a, self.staff)
        used_units.set_inspection(self.b, decision='rejected', note='No enciende')
        used_units.set_publication(self.a, published=False)
        self.assertEqual((StockMovement.objects.count(),
                          list(BranchStock.objects.values_list('product_id', 'quantity'))), before)
        self.assertEqual(self.shelf(self.used128), self.available(self.used128))
        self.assertEqual(self.shelf(self.used128), 2)


# ---------------------------------------------------------------------------
# Authority: the new capability
# ---------------------------------------------------------------------------

class CapabilityTest(TestCase):
    def test_the_capability_is_assignable_in_the_admin_preset_and_not_in_inventory_presets(self):
        from store.capabilities import DEFAULT_ADMIN_CAPABILITY_CODES, is_assignable_capability
        from store.company_provisioning import _INVENTORY_CAPS
        from store.tenancy import LEGACY_ROLE_CAPABILITIES

        self.assertTrue(is_assignable_capability('inventory.units.inspect'))
        self.assertIn('inventory.units.inspect', DEFAULT_ADMIN_CAPABILITY_CODES)
        self.assertNotIn('inventory.units.inspect', _INVENTORY_CAPS)
        self.assertNotIn('inventory.units.inspect', LEGACY_ROLE_CAPABILITIES['inventory'])


# ---------------------------------------------------------------------------
# Migration 0187
# ---------------------------------------------------------------------------

MIGRATION = '0187_used_unit_readiness'
PREVIOUS = ('store', '0186_label_printing')


class MigrationTest(TestCase):
    def test_it_only_adds_nullable_or_db_default_columns(self):
        from importlib import import_module

        from django.db import migrations
        from django.db.models import NOT_PROVIDED

        operations = import_module(f'store.migrations.{MIGRATION}').Migration.operations
        added = [op for op in operations if isinstance(op, migrations.AddField)]
        self.assertEqual(len(added), 12)
        for operation in added:
            field = operation.field
            self.assertTrue(field.null or field.db_default is not NOT_PROVIDED, operation.name)
        # No data step touches a device (owner: nothing historical is published automatically).
        runs = [op for op in operations if isinstance(op, migrations.RunPython)]
        self.assertEqual([op.code.__name__ for op in runs], ['grant'])

    def test_existing_used_devices_stay_pending_unpublished_and_no_stock_moves(self):
        from django.db.migrations.executor import MigrationExecutor

        with connection.cursor() as cursor:
            cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
        executor = MigrationExecutor(connection)
        executor.migrate([PREVIOUS])
        apps = executor.loader.project_state([PREVIOUS]).apps
        Company = apps.get_model('store', 'Company')
        Branch = apps.get_model('store', 'Branch')
        HProduct = apps.get_model('store', 'Product')
        HUnit = apps.get_model('store', 'StockUnit')
        HStock = apps.get_model('store', 'BranchStock')
        CompanyRole = apps.get_model('store', 'CompanyRole')

        company = Company.objects.create(name='Migración seminuevos', slug='migracion-seminuevos')
        branch = Branch.objects.create(company=company, name='Centro')
        used = HProduct.objects.create(company=company, name='Usado', slug='usado-mig', price=Decimal('0'),
                                       condition_line='used', is_serialized=True)
        new = HProduct.objects.create(company=company, name='Nuevo', slug='nuevo-mig', price=Decimal('10'),
                                      is_serialized=True)
        rows = [(used, 'MIG01', 'available', Decimal('1500')), (used, 'MIG02', 'available', None),
                (used, 'MIG03', 'reserved', Decimal('1200')), (used, 'MIG04', 'sold', Decimal('900')),
                (new, 'MIG05', 'available', None)]
        for product, serial, state, price in rows:
            HUnit.objects.create(company=company, branch=branch, product=product, serial_number=serial,
                                 status=state, sale_price=price)
        HStock.objects.create(branch=branch, product=used, quantity=2)
        HStock.objects.create(branch=branch, product=new, quantity=1)
        admin = CompanyRole.objects.create(company=company, name='Administrador', slug='administrador-mig',
                                           capabilities=['company.manage', 'inventory.view'])
        clerk = CompanyRole.objects.create(company=company, name='Inventario', slug='inventario-mig',
                                           capabilities=['inventory.view', 'inventory.adjust'])

        def counts():
            return (sorted(HStock.objects.filter(branch=branch).values_list('product_id', 'quantity')),
                    sorted(HUnit.objects.filter(company=company).values_list('serial_number', 'status')))

        before = counts()
        executor = MigrationExecutor(connection)
        executor.migrate([('store', MIGRATION)])

        self.assertEqual(counts(), before)
        self.assertEqual(
            sorted(StockUnit.objects.filter(company_id=company.pk)
                   .values_list('serial_number', 'inspection_status', 'published_online')),
            [(serial, 'pending', False) for _p, serial, _s, _price in rows])
        # Nothing historical is offered: the priced, available one is pending and unpublished.
        from store.models import CompanyRole as Role
        # UNIT-PHOTO-02: the live photo rule reads columns this node's state has not yet (0192), so the web rule is
        # read here without it: no device of the company is both approved and published, hence none is offered.
        self.assertFalse(StockUnit.objects.filter(company_id=company.pk, inspection_status='approved',
                                                  published_online=True).exists())
        self.assertIn('inventory.units.inspect', Role.objects.get(pk=admin.pk).capabilities)
        self.assertNotIn('inventory.units.inspect', Role.objects.get(pk=clerk.pk).capabilities)

        # And back: the columns go, the devices and the shelf are what they were.
        executor = MigrationExecutor(connection)
        executor.migrate([PREVIOUS])
        self.assertEqual(counts(), before)
        executor = MigrationExecutor(connection)
        executor.migrate([('store', MIGRATION)])
