"""
DEVICE-COMMERCE-01 · DC1 — el inventario valorizado AL COSTO.

Junto al «valor a precio de venta» de siempre, y sólo para `costs.view`:

  * productos por cantidad: unidades × promedio ponderado de SU sucursal;
  * equipos con serie: la suma del costo específico de cada equipo disponible;
  * lo que no tiene costo no se valora en cero: se cuenta aparte, pendiente.
"""
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from store import inventory_services as inventory
from store import stock_unit_services as units
from store.models import Product, StockMovement
from store.test_stock_units import IMEI_A, IMEI_B, IMEI_C
from store.tests import _p2d_branch, _p2d_member, _p3_company, _prod

D = Decimal
SUMMARY = '/api/admin/inventory/summary/?branch=all'
DASHBOARD = '/api/admin/inventory/dashboard/?branch=all'


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class InventoryAtCostTest(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('dc1-valor', 'Tienda Valor')
        self.other = _p3_company('dc1-valor-b', 'Otra')
        self.centro = self.company.default_inventory_branch
        self.norte = _p2d_branch(self.company, 'Norte')
        case = _prod(self.company, 'Funda', 'funda-valor', price='50.00', inventory=0)
        cable = _prod(self.company, 'Cable', 'cable-valor', price='20.00', inventory=0)
        phone = _prod(self.company, 'iPhone', 'iphone-valor', price='4000.00', inventory=0)
        Product.objects.filter(pk=phone.pk).update(is_serialized=True, requires_imei=True)
        phone.refresh_from_db()

        self.entry(self.centro, case, 4, D('10'))          # 40
        self.entry(self.norte, case, 2, D('13'))           # 26
        self.entry(self.centro, cable, 3, None)            # pending: 3 units
        received = units.receive_units(branch=self.centro, product=phone, reason='Compra', units=[
            {'serial_number': 'SER-0001', 'imei': IMEI_A, 'cost': '3000.00'},
            {'serial_number': 'SER-0002', 'imei': IMEI_B, 'cost': '3100.00'},
            {'serial_number': 'SER-0003', 'imei': IMEI_C},                   # pending: 1 unit
        ])
        units.reserve(received[1], reason='Apartado')       # off the shelf: not valued

        foreign = _prod(self.other, 'Ajeno', 'ajeno-valor', price='1.00', inventory=0)
        self.entry(self.other.default_inventory_branch, foreign, 9, D('999'))

    def entry(self, branch, product, quantity, cost):
        inventory.create_stock_movement(
            branch=branch, product_id=product.pk, movement_type=StockMovement.PURCHASE_ENTRY,
            quantity=quantity, reason='Compra', unit_cost=cost,
        )

    def member(self, name, *caps, branches=None):
        user, _ = _p2d_member(self.company, name, ['company.view', 'inventory.view', *caps], branches=branches)
        return _client(user)

    def test_the_value_at_cost_sits_next_to_the_value_at_sale_price(self):
        body = self.member('dc1_val_duena', 'costs.view').get(SUMMARY).json()
        # 4×50 + 2×50 + 3×20 + 2×4000 (available devices) = 8360
        self.assertEqual(body['inventory_value'], '8360.00')
        self.assertEqual(body['inventory_value_basis'], 'sale_price')
        # 40 + 26 + 3000 = 3066; the cable (3) and the device without cost (1) are pending.
        self.assertEqual(body['inventory_cost_value'], '3066.00')
        self.assertEqual(body['inventory_cost_pending_units'], 4)
        self.assertEqual(body['inventory_cost_basis'], 'average_and_unit_cost')

    def test_one_branch(self):
        client = self.member('dc1_val_norte', 'costs.view')
        body = client.get(f'/api/admin/inventory/summary/?branch={self.norte.pk}').json()
        self.assertEqual(body['inventory_cost_value'], '26.00')
        self.assertEqual(body['inventory_cost_pending_units'], 0)

    def test_without_costs_view_there_is_no_cost_figure(self):
        client = self.member('dc1_val_almacen', 'inventory.adjust')
        for url in (SUMMARY, DASHBOARD):
            with self.subTest(url):
                text = client.get(url).content.decode()
                self.assertNotIn('inventory_cost_value', text)
                self.assertNotIn('inventory_cost_pending_units', text)

    def test_the_dashboard_carries_it_for_costs_view(self):
        body = self.member('dc1_val_tablero', 'costs.view').get(DASHBOARD).json()
        self.assertEqual(body['summary']['inventory_cost_value'], '3066.00')

    def test_a_member_of_one_branch_values_only_that_branch(self):
        client = self.member('dc1_val_sel', 'costs.view', branches=[self.norte])
        body = client.get(SUMMARY).json()
        self.assertEqual(body['inventory_cost_value'], '26.00')
