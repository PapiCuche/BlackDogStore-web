"""
COMMERCE-ADMIN-UX-01 · A1 — «Costos y precios».

INCIDENTE (propietario): «un Administrador no encuentra "Margen comercial" ni
"Valor al costo"». El backend respondía bien a quien tiene `costs.view`; lo que
faltaba era un lugar donde mirar. Esta pantalla reúne lo que ya existía (el
margen, el inventario al costo, el costo de cada equipo) y añade lo único que no
tenía superficie: el costo promedio de cada sucursal por producto.

LA MATRIZ A1, del lado del servidor:

  * rol propio «administrador» (preset)        → ve todos los costos;
  * rol propio «venta-servicio-tecnico-inventario» (sin `costs.view`)
                                                → 200 en el stock, SIN claves de costo;
                                                  403 en el margen;
  * master de plataforma con empresa            → ve;
  * master sin empresa                          → 403 (debe elegir una);
  * puente legacy (admin sin Membership, piloto) → ve en el stock.

Y las correcciones: costo de referencia (con `costs.view` + `products.manage`),
precio comercial (`products.manage`), y «Corregir costo» de un equipo (no
vendido, con motivo obligatorio y auditado sin importes).
"""
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from store import inventory_services as inventory
from store import stock_unit_services as units
from store.company_provisioning import _INVENTORY_CAPS, _SALES_CAPS, _TECHNICIAN_CAPS
from store.models import (
    AdminAuditLog, CompanyRole, Membership, Product, StockMovement, StockUnit, UserProfile,
)
from store.test_device_commerce import assert_no_amount
from store.test_stock_units import IMEI_A, IMEI_B
from store.tests import _assign, _p2d_branch, _p2d_member, _p3_company, _prod, _role

STOCK = '/api/admin/inventory/stock/?branch=all'
MARGIN = '/api/admin/sales/margin/'
SUMMARY = '/api/admin/inventory/summary/?branch=all'
UNITS = '/api/admin/inventory/units/'
COST_KEYS = ('average_cost', 'uncosted_quantity', 'product_reference_cost', 'cost_source')

#: The custom role of the incident: sales + technical service + inventory, no costs.
VSTI_CAPS = sorted(set(_SALES_CAPS) | set(_INVENTORY_CAPS) | set(_TECHNICIAN_CAPS))


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class A1Fixture(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('a1-costos', 'Tienda A1')
        self.other = _p3_company('a1-costos-b', 'Otra A1')
        self.centro = self.company.default_inventory_branch
        self.norte = _p2d_branch(self.company, 'Norte A1')

        self.case = _prod(self.company, 'Funda A1', 'funda-a1', price='50.00', inventory=0)
        self.cable = _prod(self.company, 'Cable A1', 'cable-a1', price='20.00', inventory=0)
        Product.objects.filter(pk=self.cable.pk).update(reference_cost=D('5.00'))
        self.phone = _prod(self.company, 'iPhone A1', 'iphone-a1', price='4000.00', inventory=0)
        Product.objects.filter(pk=self.phone.pk).update(is_serialized=True, requires_imei=True)
        self.phone.refresh_from_db()

        self.entry(self.centro, self.case, 4, D('10'))
        self.entry(self.norte, self.case, 2, D('13'))
        self.entry(self.centro, self.cable, 3, None)
        self.devices = units.receive_units(branch=self.centro, product=self.phone, reason='Compra', units=[
            {'serial_number': 'A1-0001', 'imei': IMEI_A, 'cost': '3000.00'},
            {'serial_number': 'A1-0002', 'imei': IMEI_B},
        ])

        foreign = _prod(self.other, 'Ajeno A1', 'ajeno-a1', price='1.00', inventory=0)
        self.entry(self.other.default_inventory_branch, foreign, 9, D('987'))

        User = get_user_model()
        self.admin = User.objects.create_user('a1_admin', password='Pass123!')
        membership = Membership.objects.create(user=self.admin, company=self.company, role='customer')
        _assign(membership, CompanyRole.objects.get(company=self.company, slug='administrador'))

        self.vsti = User.objects.create_user('a1_vsti', password='Pass123!')
        membership = Membership.objects.create(user=self.vsti, company=self.company, role='customer')
        _assign(membership, _role(self.company, 'Venta, servicio técnico e inventario',
                                  capabilities=VSTI_CAPS, slug='venta-servicio-tecnico-inventario'))

        self.master = User.objects.create_user('a1_master', password='Pass123!', is_superuser=True)

    def entry(self, branch, product, quantity, cost):
        inventory.create_stock_movement(
            branch=branch, product_id=product.pk, movement_type=StockMovement.PURCHASE_ENTRY,
            quantity=quantity, reason='Compra', unit_cost=cost,
        )

    def rows(self, client, query=STOCK):
        res = client.get(query)
        self.assertEqual(res.status_code, 200, res.content)
        return {(row['product_name'], row['branch_name']): row for row in res.json()['results']}


class A1RoleMatrixTest(A1Fixture):
    def test_the_vsti_role_does_not_hold_costs_view(self):
        self.assertNotIn('costs.view', VSTI_CAPS)

    def test_administrador_sees_the_average_cost_of_each_branch(self):
        rows = self.rows(_client(self.admin))
        centro = rows[('Funda A1', self.centro.name)]
        norte = rows[('Funda A1', 'Norte A1')]
        self.assertEqual(D(centro['average_cost']), D('10'))
        self.assertEqual(D(norte['average_cost']), D('13'))
        self.assertEqual(centro['uncosted_quantity'], 0)
        self.assertEqual(centro['cost_source'], 'average')

    def test_without_an_average_the_row_says_where_its_cost_comes_from(self):
        rows = self.rows(_client(self.admin))
        cable = rows[('Cable A1', self.centro.name)]
        self.assertIsNone(cable['average_cost'])
        self.assertEqual(cable['uncosted_quantity'], 3)
        self.assertEqual(cable['product_reference_cost'], '5.00')
        self.assertEqual(cable['cost_source'], 'reference')
        phone = rows[('iPhone A1', self.centro.name)]
        self.assertEqual(phone['cost_source'], 'unit')
        self.assertTrue(phone['product_is_serialized'])

    def test_unknown_when_there_is_nothing_to_go_on(self):
        Product.objects.filter(pk=self.cable.pk).update(reference_cost=None)
        rows = self.rows(_client(self.admin))
        self.assertEqual(rows[('Cable A1', self.centro.name)]['cost_source'], 'unknown')

    def test_vsti_gets_the_stock_without_a_single_cost_key(self):
        res = _client(self.vsti).get(STOCK)
        self.assertEqual(res.status_code, 200, res.content)
        for row in res.json()['results']:
            for key in COST_KEYS:
                self.assertNotIn(key, row)
        assert_no_amount(self, res.json()['results'], '10', '13', '5.00', '3000')

    def test_vsti_is_refused_the_margin_and_the_value_at_cost(self):
        client = _client(self.vsti)
        self.assertEqual(client.get(MARGIN).status_code, 403)
        summary = client.get(SUMMARY).json()
        self.assertNotIn('inventory_cost_value', summary)
        [*rows] = client.get(UNITS + '?branch=all').json()['results']
        self.assertTrue(rows)
        self.assertTrue(all('cost' not in row for row in rows))

    def test_administrador_gets_the_margin_and_the_value_at_cost(self):
        client = _client(self.admin)
        self.assertEqual(client.get(MARGIN).status_code, 200)
        self.assertIn('inventory_cost_value', client.get(SUMMARY).json())

    def test_master_with_a_company_sees_the_costs(self):
        client = _client(self.master)
        rows = self.rows(client, f'{STOCK}&company={self.company.pk}')
        self.assertEqual(D(rows[('Funda A1', self.centro.name)]['average_cost']), D('10'))
        self.assertEqual(client.get(f'{MARGIN}?company={self.company.pk}').status_code, 200)

    def test_master_without_a_company_is_asked_to_choose_one(self):
        client = _client(self.master)
        self.assertEqual(client.get(STOCK).status_code, 403)
        self.assertEqual(client.get(MARGIN).status_code, 403)

    def test_the_legacy_bridge_admin_sees_the_costs_of_the_pilot(self):
        from store.tenancy import legacy_catalog_company

        legacy = get_user_model().objects.create_user('a1_legacy', password='Pass123!')
        UserProfile.objects.update_or_create(user=legacy, defaults={'role': UserProfile.ROLE_ADMIN})
        legacy = get_user_model().objects.get(pk=legacy.pk)
        pilot = legacy_catalog_company(legacy)
        self.assertIsNotNone(pilot)
        product = _prod(pilot, 'Piloto A1', 'piloto-a1', price='9.00', inventory=0)
        self.entry(pilot.default_inventory_branch, product, 2, D('4'))
        res = _client(legacy).get(STOCK)
        self.assertEqual(res.status_code, 200, res.content)
        [row] = [r for r in res.json()['results'] if r['product_name'] == 'Piloto A1']
        self.assertEqual(D(row['average_cost']), D('4'))


class A1IsolationTest(A1Fixture):
    def test_another_company_never_shows_up(self):
        res = _client(self.admin).get(STOCK)
        self.assertNotIn('Ajeno A1', res.content.decode())
        assert_no_amount(self, res.json()['results'], '987')

    def test_holding_costs_view_elsewhere_is_not_holding_it_here(self):
        Membership.objects.create(user=self.vsti, company=self.other, role='admin')
        res = _client(self.vsti).get(f'{STOCK}&company={self.company.pk}')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(all('average_cost' not in row for row in res.json()['results']))

    def test_a_branch_out_of_reach_is_not_listed(self):
        user, _ = _p2d_member(self.company, 'a1_norte', ['inventory.view', 'costs.view'], branches=[self.norte])
        rows = self.rows(_client(user))
        self.assertEqual({branch for _name, branch in rows}, {'Norte A1'})
        self.assertEqual(
            _client(user).get(f'/api/admin/inventory/stock/?branch={self.centro.pk}').status_code, 404,
        )


class ReferenceCostAndPriceTest(A1Fixture):
    def url(self, product):
        return f'/api/admin/products/{product.pk}/'

    def test_administrador_corrects_the_reference_cost_and_it_is_audited_without_the_amount(self):
        res = _client(self.admin).patch(self.url(self.cable), {'reference_cost': '7.50'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['reference_cost'], '7.50')
        self.cable.refresh_from_db()
        self.assertEqual(self.cable.reference_cost, D('7.50'))
        log = AdminAuditLog.objects.filter(target_type='product', target_id=self.cable.pk).latest('pk')
        self.assertIn('reference_cost', log.metadata['changed_fields'])
        assert_no_amount(self, log.metadata, '7.50', '5.00')

    def test_products_manage_without_costs_view_cannot_touch_the_reference_cost(self):
        user, _ = _p2d_member(self.company, 'a1_catalogo', ['products.view', 'products.manage'])
        client = _client(user)
        refused = client.patch(self.url(self.cable), {'reference_cost': '1.00'}, format='json')
        self.assertEqual(refused.status_code, 403, refused.content)
        self.cable.refresh_from_db()
        self.assertEqual(self.cable.reference_cost, D('5.00'))
        # The price is theirs to change, and the answer still carries no cost.
        price = client.patch(self.url(self.cable), {'price': '25.00'}, format='json')
        self.assertEqual(price.status_code, 200, price.content)
        self.assertNotIn('reference_cost', price.json())

    def test_costs_view_without_products_manage_cannot_either(self):
        user, _ = _p2d_member(self.company, 'a1_solo_costos', ['products.view', 'costs.view'])
        res = _client(user).patch(self.url(self.cable), {'reference_cost': '1.00'}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_a_negative_reference_cost_is_refused_and_null_clears_it(self):
        client = _client(self.admin)
        bad = client.patch(self.url(self.cable), {'reference_cost': '-1'}, format='json')
        self.assertEqual(bad.status_code, 400, bad.content)
        cleared = client.patch(self.url(self.cable), {'reference_cost': None}, format='json')
        self.assertEqual(cleared.status_code, 200, cleared.content)
        self.cable.refresh_from_db()
        self.assertIsNone(self.cable.reference_cost)

    def test_a_huge_or_non_finite_reference_cost_is_a_400_with_its_own_message(self):
        client = _client(self.admin)
        for raw, expected in (('1e30', 'demasiado alto'), ('99999999999', 'demasiado alto'),
                              ('Infinity', 'número'), ('-Infinity', 'número'), ('NaN', 'número'),
                              ('sNaN', 'número')):
            res = client.patch(self.url(self.cable), {'reference_cost': raw}, format='json')
            self.assertEqual(res.status_code, 400, (raw, res.content))
            message = res.json()['reference_cost'][0]
            self.assertIn(expected, message, raw)
            self.assertNotIn('negativo', message, raw)
        self.cable.refresh_from_db()
        self.assertEqual(self.cable.reference_cost, D('5.00'))

    def test_the_detail_shows_the_reference_cost_only_to_costs_view(self):
        self.assertEqual(_client(self.admin).get(self.url(self.cable)).json()['reference_cost'], '5.00')
        self.assertNotIn('reference_cost', _client(self.vsti).get(self.url(self.cable)).json())

    def test_a_reference_cost_change_never_rewrites_a_snapshot(self):
        exit_ = inventory.create_stock_movement(
            branch=self.centro, product_id=self.cable.pk, movement_type=StockMovement.MANUAL_EXIT,
            quantity=1, reason='Salida',
        )
        before = (exit_.unit_cost, exit_.cost_source)
        _client(self.admin).patch(self.url(self.cable), {'reference_cost': '9.00'}, format='json')
        exit_.refresh_from_db()
        self.assertEqual((exit_.unit_cost, exit_.cost_source), before)


class CorrectDeviceCostTest(A1Fixture):
    def url(self, unit):
        return f'{UNITS}{unit.pk}/'

    def test_correcting_needs_a_reason(self):
        res = _client(self.admin).patch(self.url(self.devices[1]), {'cost': '2900.00'}, format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['field'], 'cost_reason')
        self.devices[1].refresh_from_db()
        self.assertIsNone(self.devices[1].cost)

    def test_correcting_with_a_reason_is_audited_without_the_amount(self):
        res = _client(self.admin).patch(
            self.url(self.devices[1]), {'cost': '2900.00', 'cost_reason': 'Factura del proveedor'}, format='json',
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['cost'], '2900.00')
        log = AdminAuditLog.objects.filter(action='stock_unit_updated', target_id=self.devices[1].pk).latest('pk')
        self.assertEqual(log.metadata['cost_reason'], 'Factura del proveedor')
        assert_no_amount(self, log.metadata, '2900.00')

    def test_vsti_cannot_correct_a_cost(self):
        res = _client(self.vsti).patch(
            self.url(self.devices[1]), {'cost': '1.00', 'cost_reason': 'x'}, format='json',
        )
        self.assertEqual(res.status_code, 403)

    def test_a_sold_device_keeps_its_cost(self):
        StockUnit.objects.filter(pk=self.devices[0].pk).update(status=StockUnit.Status.SOLD)
        res = _client(self.admin).patch(
            self.url(self.devices[0]), {'cost': '1.00', 'cost_reason': 'Error'}, format='json',
        )
        self.assertEqual(res.status_code, 400, res.content)
        self.devices[0].refresh_from_db()
        self.assertEqual(self.devices[0].cost, D('3000.00'))

    def test_a_written_off_device_keeps_its_cost(self):
        units.write_off(self.devices[0], reason='Pantalla rota', actor=self.admin)
        res = _client(self.admin).patch(
            self.url(self.devices[0]), {'cost': '1.00', 'cost_reason': 'Error'}, format='json',
        )
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['field'], 'cost')
        self.assertIn('dio de baja', res.json()['detail'])
        self.devices[0].refresh_from_db()
        self.assertEqual(self.devices[0].cost, D('3000.00'))

    def test_the_reason_must_be_text(self):
        for reason in (['Factura'], {'motivo': 'Factura'}, 12):
            res = _client(self.admin).patch(
                self.url(self.devices[1]), {'cost': '2900.00', 'cost_reason': reason}, format='json',
            )
            self.assertEqual(res.status_code, 400, (reason, res.content))
            self.assertEqual(res.json()['field'], 'cost_reason')
        self.devices[1].refresh_from_db()
        self.assertIsNone(self.devices[1].cost)
        self.assertFalse(AdminAuditLog.objects.filter(action='stock_unit_updated',
                                                      target_id=self.devices[1].pk).exists())
