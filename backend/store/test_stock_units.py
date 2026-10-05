"""
SERIALIZED-STOCK — equipos con número de serie, dentro del inventario de siempre.

    UN EQUIPO SERIALIZADO ES STOCK, NO UN INVENTARIO PARALELO.

`BranchStock.quantity` sigue siendo la única cifra que decide si algo se puede
vender. Lo que cambia para un producto serializado es que esa cifra deja de ser
un número que alguien escribe: es, siempre, la cantidad de equipos DISPONIBLES
de esa sucursal. Cada cambio pasa por un equipo concreto y deja su línea de
Kardex, por el mismo escritor único.

Lo que estas pruebas fijan:

  * la invariante cantidad == equipos disponibles, por cada camino;
  * un producto serializado no se ajusta "por cantidad" por ningún lado;
  * el mismo IMEI no entra dos veces ni se vende dos veces;
  * vender asigna equipos concretos, en la misma transacción que la salida;
  * todo es por empresa y por sucursal.
"""
from decimal import Decimal
from threading import Barrier, Thread
from unittest import mock

from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from store import inventory_services as inventory
from store import stock_unit_services as units
from store.models import (
    AdminAuditLog, BranchStock, Order, OrderItem, Product, StockMovement, StockUnit,
)
from store.tests import M7InventoryBase as _Base, _m7_stock, _prod


def imei(body14: str) -> str:
    from store.device_identity import luhn_check_digit
    return body14 + luhn_check_digit(body14)


IMEI_A, IMEI_B, IMEI_C, IMEI_D = (imei(f'3569380356438{n}') for n in ('0', '1', '2', '3'))


class UnitsBase(_Base):
    def setUp(self):
        super().setUp()
        self.phone = _prod(self.company, 'iPhone 16', 'iphone-16-serie', price='4500.00', inventory=0)
        Product.objects.filter(pk=self.phone.pk).update(is_serialized=True, requires_imei=True)
        self.phone.refresh_from_db()
        self.laptop = _prod(self.company, 'MacBook Air', 'macbook-air-serie', price='5200.00', inventory=0)
        Product.objects.filter(pk=self.laptop.pk).update(is_serialized=True)
        self.laptop.refresh_from_db()
        self.foreign_phone = _prod(self.other, 'Teléfono ajeno', 'telefono-ajeno-serie', inventory=0)
        Product.objects.filter(pk=self.foreign_phone.pk).update(is_serialized=True, requires_imei=True)
        self.foreign_phone.refresh_from_db()

    def receive(self, *rows, product=None, branch=None, **extra):
        return units.receive_units(
            branch=branch or self.branch_a, product=product or self.phone,
            units=list(rows), actor=self.staff, reason='Compra a proveedor', **extra,
        )

    def row(self, serial, imei_value=None, **extra):
        return {'serial_number': serial, 'imei': imei_value, **extra}

    def quantity(self, product=None, branch=None):
        return inventory.branch_quantity(branch or self.branch_a, product or self.phone)

    def available(self, product=None, branch=None):
        return StockUnit.objects.filter(
            product=product or self.phone, branch=branch or self.branch_a,
            status=StockUnit.Status.AVAILABLE,
        ).count()

    def assert_invariant(self, product=None, branch=None):
        self.assertEqual(self.quantity(product, branch), self.available(product, branch))

    def paid_order(self, product=None, quantity=1, branch=None):
        product = product or self.phone
        order = Order.objects.create(
            company=self.company, fulfillment_branch=branch or self.branch_a,
            customer_email='x@example.com', total=product.price * quantity,
            status=Order.Status.PAID, paid=True, paid_at=timezone.now(),
        )
        OrderItem.objects.create(order=order, product=product, quantity=quantity, price=product.price)
        return order


class ReceivingTest(UnitsBase):
    def test_receiving_units_is_one_kardex_entry_and_the_quantity_is_the_units(self):
        created = self.receive(
            self.row('F2LXK1ABC1', IMEI_A, condition='new', cost='3800.00'),
            self.row('f2lxk1abc2', IMEI_B),
        )

        self.assertEqual(len(created), 2)
        self.assertEqual(self.quantity(), 2)
        self.assert_invariant()
        self.phone.refresh_from_db()
        self.assertEqual(self.phone.inventory, 2)
        movement = StockMovement.objects.get(product=self.phone)
        self.assertEqual(movement.movement_type, StockMovement.PURCHASE_ENTRY)
        self.assertEqual(movement.quantity, 2)
        self.assertEqual(sorted(movement.metadata['unit_ids']), sorted(u.pk for u in created))
        first = StockUnit.objects.get(imei=IMEI_A)
        self.assertEqual(first.serial_number, 'F2LXK1ABC1')
        self.assertEqual(first.company, self.company)
        self.assertEqual(first.branch, self.branch_a)
        self.assertEqual(first.status, 'available')
        self.assertEqual(first.cost, Decimal('3800.00'))
        self.assertIsNotNone(first.received_at)
        self.assertEqual(StockUnit.objects.get(imei=IMEI_B).serial_number, 'F2LXK1ABC2')

    def test_absent_identifiers_are_null_never_a_placeholder(self):
        [unit] = self.receive(self.row('C02XK1LAPTOP'), product=self.laptop)
        self.assertIsNone(unit.imei)
        self.assertIsNone(unit.imei2)

        for placeholder in ('N/A', 'no tiene', '0000'):
            with self.subTest(placeholder), self.assertRaises(units.StockUnitError):
                self.receive(self.row(placeholder), product=self.laptop)

    def test_a_cellular_product_needs_a_valid_imei_and_every_unit_a_serial(self):
        cases = (
            self.row('SERIE00001'),                       # sin IMEI
            self.row('SERIE00002', '356938035643800'),    # dígito de control malo
            self.row('', IMEI_A),                         # sin serie
            self.row('SERIE00003', IMEI_A, imei2=IMEI_A), # el segundo es el mismo
        )
        for bad in cases:
            with self.subTest(bad), self.assertRaises(units.StockUnitError):
                self.receive(bad)
        self.assertEqual(StockUnit.objects.count(), 0)
        self.assertEqual(self.quantity(), 0)

    def test_the_same_imei_or_serial_never_enters_twice(self):
        self.receive(self.row('SERIE00001', IMEI_A, imei2=IMEI_C))

        duplicates = (
            self.row('SERIE00002', IMEI_A),                 # mismo IMEI
            self.row('SERIE00001', IMEI_B),                 # misma serie
            self.row('SERIE00003', IMEI_C),                 # era el IMEI 2 de otro
            self.row('SERIE00004', IMEI_B, imei2=IMEI_A),   # su IMEI 2 es el IMEI de otro
        )
        for bad in duplicates:
            with self.subTest(bad), self.assertRaises(units.StockUnitError):
                self.receive(bad)
        with self.assertRaises(units.StockUnitError):       # repetido dentro del mismo lote
            self.receive(self.row('SERIE00005', IMEI_B), self.row('SERIE00006', IMEI_B))

        self.assertEqual(StockUnit.objects.count(), 1)
        self.assertEqual(self.quantity(), 1)

    def test_a_batch_enters_whole_or_not_at_all(self):
        with self.assertRaises(units.StockUnitError):
            self.receive(self.row('SERIE00001', IMEI_A), self.row('SERIE00002', 'malo'))
        self.assertEqual(StockUnit.objects.count(), 0)
        self.assertEqual(StockMovement.objects.filter(product=self.phone).count(), 0)

    def test_another_company_may_hold_the_same_identifier(self):
        self.receive(self.row('SERIE00001', IMEI_A))
        units.receive_units(
            branch=self.foreign_branch, product=self.foreign_phone,
            units=[self.row('SERIE00001', IMEI_A)], actor=self.staff, reason='Compra',
        )
        self.assertEqual(StockUnit.objects.filter(imei=IMEI_A).count(), 2)

    def test_only_a_serialized_product_of_this_company_and_branch_takes_units(self):
        with self.assertRaises(units.StockUnitError):
            self.receive(self.row('SERIE00001', IMEI_A), product=self.product)          # no serializado
        with self.assertRaises(units.StockUnitError):
            self.receive(self.row('SERIE00001', IMEI_A), product=self.foreign_phone)    # de otra empresa
        with self.assertRaises(units.StockUnitError):
            self.receive()                                                              # sin equipos


class QuantityIsNeverTypedTest(UnitsBase):
    """Ningún camino "por cantidad" toca un producto serializado."""

    def setUp(self):
        super().setUp()
        self.receive(self.row('SERIE00001', IMEI_A), self.row('SERIE00002', IMEI_B))

    def test_the_single_writer_refuses_a_bare_quantity(self):
        for movement_type in (StockMovement.MANUAL_ENTRY, StockMovement.MANUAL_EXIT,
                              StockMovement.CORRECTION_POSITIVE, StockMovement.SALE_EXIT):
            with self.subTest(movement_type), self.assertRaises(inventory.InvalidMovementError):
                inventory.create_stock_movement(
                    branch=self.branch_a, product_id=self.phone.pk,
                    movement_type=movement_type, quantity=1, reason='x', actor=self.staff,
                )
        self.assertEqual(self.quantity(), 2)
        self.assert_invariant()

    def test_the_manual_adjustment_endpoint_answers_400_and_says_why(self):
        client = APIClient()
        client.force_authenticate(user=self.staff)
        res = client.post('/api/admin/inventory/movements/', {
            'product_id': self.phone.pk, 'movement_type': 'manual_entry', 'quantity': 3,
            'reason': 'Ajuste', 'branch': self.branch_a.pk,
        }, format='json')

        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn('serie', res.json()['detail'])
        self.assertEqual(self.quantity(), 2)

    def test_a_transfer_and_a_count_cannot_move_it_by_number(self):
        transfer = inventory.create_stock_transfer(
            company=self.company, source_branch=self.branch_a,
            destination_branch=self.branch_b, actor=self.staff,
        )
        with self.assertRaises(inventory.InventoryError):
            inventory.set_transfer_item(transfer, product=self.phone, quantity=1)
            inventory.dispatch_transfer(transfer, actor=self.staff)

        count = inventory.create_inventory_count(
            company=self.company, branch=self.branch_a, actor=self.staff)
        with self.assertRaises(inventory.InventoryError):
            inventory.set_count_item(count, product=self.phone, physical_quantity=5)
            inventory.approve_inventory_count(count, actor=self.staff)

        self.assertEqual(self.quantity(), 2)
        self.assertEqual(self.quantity(branch=self.branch_b), 0)
        self.assert_invariant()

    def test_initial_stock_cannot_invent_units(self):
        with self.assertRaises(inventory.InventoryError):
            inventory.apply_initial_stock(
                branch=self.branch_b, product=self.phone, quantity=4, actor=self.staff)
        self.assertEqual(self.quantity(branch=self.branch_b), 0)

    def test_a_movement_that_raced_a_change_of_mode_is_undone(self):
        """
        Entre leer el producto y escribir el stock, alguien lo pasa a «con
        serie». El movimiento por cantidad no puede quedar escrito: dejaría un
        producto con serie, cinco unidades y ningún equipo.
        """
        loose = _prod(self.company, 'Reloj', 'reloj-carrera-serie', inventory=0)
        real = inventory._locked_branch_stocks

        def flip_then_lock(branch, products):
            Product.objects.filter(pk=loose.pk).update(is_serialized=True)
            return real(branch, products)

        with mock.patch.object(inventory, '_locked_branch_stocks', side_effect=flip_then_lock):
            with self.assertRaises(inventory.InvalidMovementError):
                inventory.create_stock_movement(
                    branch=self.branch_a, product_id=loose.pk,
                    movement_type=StockMovement.MANUAL_ENTRY, quantity=5, reason='x', actor=self.staff)

        self.assertEqual(self.quantity(product=loose), 0)
        self.assertFalse(StockMovement.objects.filter(product=loose).exists())

    def test_a_product_with_stock_cannot_change_how_it_is_counted(self):
        with self.assertRaises(units.StockUnitError):
            units.set_serialized(self.phone, serialized=False, actor=self.staff)
        with self.assertRaises(units.StockUnitError):
            units.set_serialized(self.product, serialized=True, actor=self.staff)   # tiene 13 sueltas

        empty = _prod(self.company, 'Watch', 'watch-serie', inventory=0)
        units.set_serialized(empty, serialized=True, requires_imei=False, actor=self.staff)
        empty.refresh_from_db()
        self.assertTrue(empty.is_serialized)


class UnitOperationsTest(UnitsBase):
    def setUp(self):
        super().setUp()
        self.first, self.second = self.receive(
            self.row('SERIE00001', IMEI_A), self.row('SERIE00002', IMEI_B))

    def test_writing_off_a_unit_takes_it_out_of_stock_with_its_reason(self):
        units.write_off(self.first, reason='Pantalla rota en vitrina', actor=self.staff)

        self.first.refresh_from_db()
        self.assertEqual(self.first.status, 'written_off')
        self.assertEqual(self.quantity(), 1)
        self.assert_invariant()
        movement = StockMovement.objects.filter(product=self.phone).order_by('-pk').first()
        self.assertEqual(movement.movement_type, StockMovement.DAMAGED_EXIT)
        self.assertEqual(movement.metadata['unit_ids'], [self.first.pk])
        with self.assertRaises(units.StockUnitError):
            units.write_off(self.first, reason='Otra vez', actor=self.staff)
        with self.assertRaises(units.StockUnitError):
            units.write_off(self.second, reason='', actor=self.staff)

    def test_reserving_takes_it_off_sale_and_releasing_brings_it_back(self):
        units.reserve(self.first, reason='Apartado para Ana', actor=self.staff)
        self.assertEqual(self.quantity(), 1)
        self.assert_invariant()
        self.first.refresh_from_db()
        self.assertEqual(self.first.status, 'reserved')

        units.release(self.first, actor=self.staff)
        self.assertEqual(self.quantity(), 2)
        self.assert_invariant()
        with self.assertRaises(units.StockUnitError):
            units.release(self.first, actor=self.staff)

    def test_details_change_without_touching_stock(self):
        before = StockMovement.objects.count()
        units.update_unit(self.first, actor=self.staff, condition='open_box',
                          price_override='4200.00', cost='3700.00', notes='Caja abierta')

        self.first.refresh_from_db()
        self.assertEqual(self.first.condition, 'open_box')
        self.assertEqual(self.first.price_override, Decimal('4200.00'))
        self.assertEqual(StockMovement.objects.count(), before)
        with self.assertRaises(units.StockUnitError):
            units.update_unit(self.first, actor=self.staff, condition='inventada')
        with self.assertRaises(units.StockUnitError):
            units.update_unit(self.first, actor=self.staff, price_override='-1')


class SellingTest(UnitsBase):
    def setUp(self):
        super().setUp()
        self.first, self.second, self.third = self.receive(
            self.row('SERIE00001', IMEI_A), self.row('SERIE00002', IMEI_B), self.row('SERIE00003', IMEI_C))

    def test_a_sale_takes_specific_units_oldest_first(self):
        order = self.paid_order(quantity=2)

        inventory.record_sale_stock_movements(order, strict=True)

        sold = list(StockUnit.objects.filter(order=order).order_by('pk'))
        self.assertEqual([u.pk for u in sold], [self.first.pk, self.second.pk])
        self.assertTrue(all(u.status == 'sold' and u.sold_at for u in sold))
        self.assertEqual(self.quantity(), 1)
        self.assert_invariant()
        movement = StockMovement.objects.get(order=order)
        self.assertEqual(movement.movement_type, StockMovement.SALE_EXIT)
        self.assertEqual(sorted(movement.metadata['unit_ids']), [self.first.pk, self.second.pk])

    def test_a_replayed_confirmation_sells_nothing_twice(self):
        order = self.paid_order()
        inventory.record_sale_stock_movements(order)
        inventory.record_sale_stock_movements(order)

        self.assertEqual(StockUnit.objects.filter(order=order).count(), 1)
        self.assertEqual(self.quantity(), 2)
        self.assert_invariant()

    def test_a_sold_unit_is_never_sold_again(self):
        first = self.paid_order(quantity=3)
        inventory.record_sale_stock_movements(first, strict=True)

        second = self.paid_order()
        with self.assertRaises(inventory.InsufficientStockError):
            inventory.record_sale_stock_movements(second, strict=True)
        self.assertFalse(StockUnit.objects.filter(order=second).exists())
        self.assertEqual(StockUnit.objects.filter(order=first).count(), 3)

    def test_a_reserved_or_written_off_unit_is_not_sold(self):
        units.reserve(self.first, reason='Apartado', actor=self.staff)
        units.write_off(self.second, reason='Dañado', actor=self.staff)
        order = self.paid_order()

        inventory.record_sale_stock_movements(order, strict=True)

        self.assertEqual(StockUnit.objects.get(order=order).pk, self.third.pk)

    def test_units_of_another_branch_never_cover_a_sale(self):
        other_branch_unit, = self.receive(self.row('SERIE00009', IMEI_D), branch=self.branch_b)
        order = self.paid_order(quantity=4)

        with self.assertRaises(inventory.InsufficientStockError):
            inventory.record_sale_stock_movements(order, strict=True)

        other_branch_unit.refresh_from_db()
        self.assertEqual(other_branch_unit.status, 'available')
        self.assertEqual(self.quantity(), 3)

    def test_an_online_shortfall_is_flagged_and_the_units_stay(self):
        order = self.paid_order(quantity=5)

        inventory.record_sale_stock_movements(order)          # no estricto: el dinero ya entró

        self.assertFalse(StockUnit.objects.filter(order=order).exists())
        self.assertEqual(self.quantity(), 3)
        self.assertTrue(inventory.order_stock_shortfall(order))

    def test_a_returned_unit_comes_back_as_the_same_unit(self):
        order = self.paid_order()
        inventory.record_sale_stock_movements(order, strict=True)

        units.return_unit(self.first, reason='Devolución dentro de plazo', actor=self.staff)

        self.first.refresh_from_db()
        self.assertEqual(self.first.status, 'available')
        self.assertIsNone(self.first.sold_at)
        self.assertIsNone(self.first.order)
        self.assertEqual(self.quantity(), 3)
        self.assert_invariant()
        movement = StockMovement.objects.filter(product=self.phone).order_by('-pk').first()
        self.assertEqual(movement.movement_type, StockMovement.RETURN_ENTRY)
        self.assertEqual(movement.metadata['returned_from_order'], order.pk)
        self.assertEqual(StockUnit.objects.filter(imei=IMEI_A).count(), 1)

    def test_a_mixed_basket_takes_units_for_one_line_and_a_number_for_the_other(self):
        order = self.paid_order()
        OrderItem.objects.create(order=order, product=self.product, quantity=2, price=self.product.price)

        inventory.record_sale_stock_movements(order, strict=True)

        self.assertEqual(self.quantity(), 2)
        self.assertEqual(self.quantity(product=self.product), 8)
        self.assert_invariant()


class ConcurrentSaleTest(TransactionTestCase):
    """Dos cajas, un equipo. Uno lo vende; el otro se entera."""

    def test_two_tills_cannot_sell_the_last_unit(self):
        from django.contrib.auth import get_user_model
        from store.models import Branch, Company

        company = Company.objects.create(
            name='Concurrente', legal_name='Concurrente SAC', tax_id='20911111111', slug='unidades-conc')
        branch = company.branches.first() or Branch.objects.create(company=company, name='Centro')
        actor = get_user_model().objects.create_user(username='cajera_conc', password='Pass123!')
        phone = Product.objects.create(
            company=company, name='iPhone', slug='iphone-conc', price=Decimal('100.00'),
            is_serialized=True, requires_imei=True)
        units.receive_units(
            branch=branch, product=phone, units=[{'serial_number': 'SERIE00001', 'imei': IMEI_A}],
            actor=actor, reason='Compra')

        orders = []
        for _ in range(2):
            order = Order.objects.create(
                company=company, fulfillment_branch=branch, customer_email='x@example.com',
                total=phone.price, status=Order.Status.PAID, paid=True, paid_at=timezone.now())
            OrderItem.objects.create(order=order, product=phone, quantity=1, price=phone.price)
            orders.append(order)

        barrier, outcomes = Barrier(2), []

        def sell(order):
            try:
                barrier.wait(timeout=5)
                inventory.record_sale_stock_movements(order, strict=True)
                outcomes.append('sold')
            except inventory.InsufficientStockError:
                outcomes.append('refused')
            finally:
                connection.close()

        threads = [Thread(target=sell, args=(order,)) for order in orders]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertEqual(sorted(outcomes), ['refused', 'sold'])
        self.assertEqual(StockUnit.objects.filter(status='sold').count(), 1)
        self.assertEqual(BranchStock.objects.get(branch=branch, product=phone).quantity, 0)
        self.assertEqual(StockMovement.objects.filter(movement_type='sale_exit').count(), 1)


class UnitsApiTest(UnitsBase):
    LIST = '/api/admin/inventory/units/'

    def setUp(self):
        super().setUp()
        self.first, self.second = self.receive(
            self.row('SERIE00001', IMEI_A, condition='new'), self.row('SERIE00002', IMEI_B, condition='used'))
        self.in_b, = self.receive(self.row('SERIE00003', IMEI_C), branch=self.branch_b)
        units.receive_units(
            branch=self.foreign_branch, product=self.foreign_phone,
            units=[self.row('SERIEAJENA1', IMEI_D)], actor=self.staff, reason='Compra')
        self.api = APIClient()
        self.api.force_authenticate(user=self.staff)

    def test_the_table_lists_the_units_of_this_company_with_filters(self):
        # Como el resto del inventario: sin `branch` es la sucursal por defecto
        # de quien pregunta; `all` son todas las que alcanza.
        self.assertEqual(self.api.get(self.LIST).json()['count'], 2)
        res = self.api.get(self.LIST + '?branch=all')

        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body['count'], 3)
        row = next(r for r in body['results'] if r['id'] == self.first.pk)
        self.assertEqual(row['serial_number'], 'SERIE00001')
        self.assertEqual(row['imei'], IMEI_A)
        self.assertEqual(row['product_name'], 'iPhone 16')
        self.assertEqual(row['branch_name'], self.branch_a.name)
        self.assertEqual(row['status'], 'available')
        self.assertEqual(row['status_label'], 'Disponible')
        self.assertNotIn('SERIEAJENA1', res.content.decode())

        def ids(query):
            if 'branch=' not in query:
                query += ('&' if query else '?') + 'branch=all'
            return sorted(r['id'] for r in self.api.get(self.LIST + query).json()['results'])

        self.assertEqual(ids(f'?branch={self.branch_b.pk}'), [self.in_b.pk])
        self.assertEqual(ids('?condition=used'), [self.second.pk])
        self.assertEqual(ids(f'?search={IMEI_B[-6:]}'), [self.second.pk])
        self.assertEqual(ids('?search=serie00003'), [self.in_b.pk])
        self.assertEqual(ids(f'?product={self.laptop.pk}'), [])
        units.write_off(self.first, reason='Dañado', actor=self.staff)
        self.assertEqual(ids('?status=written_off'), [self.first.pk])
        self.assertEqual(self.api.get(self.LIST + '?status=inventado').status_code, 400)

    def test_a_member_of_one_branch_sees_and_touches_only_that_branch(self):
        self.restrict_to_branch_a()

        rows = self.api.get(self.LIST + '?branch=all').json()['results']
        self.assertEqual(sorted(r['id'] for r in rows), sorted([self.first.pk, self.second.pk]))
        self.assertEqual(self.api.get(self.LIST + f'?branch={self.branch_b.pk}').status_code, 404)
        self.assertEqual(self.api.post(
            f'{self.LIST}{self.in_b.pk}/write-off/', {'reason': 'x'}, format='json').status_code, 404)
        self.assertEqual(self.api.post(self.LIST, {
            'product_id': self.phone.pk, 'branch': self.branch_b.pk, 'reason': 'Compra',
            'units': [self.row('SERIE00010', IMEI_D)],
        }, format='json').status_code, 404)

    def test_receiving_through_the_api_reports_which_line_is_wrong(self):
        ok = self.api.post(self.LIST, {
            'product_id': self.laptop.pk, 'branch': self.branch_a.pk, 'reason': 'Compra',
            'units': [{'serial_number': 'C02LAPTOP01', 'condition': 'new', 'cost': '4000.00'}],
        }, format='json')
        self.assertEqual(ok.status_code, 201, ok.content)
        self.assertEqual(ok.json()['results'][0]['serial_number'], 'C02LAPTOP01')
        self.assertEqual(self.quantity(product=self.laptop), 1)
        self.assertTrue(AdminAuditLog.objects.filter(action='stock_units_received').exists())

        bad = self.api.post(self.LIST, {
            'product_id': self.phone.pk, 'branch': self.branch_a.pk, 'reason': 'Compra',
            'units': [self.row('SERIE00020', IMEI_D), self.row('SERIE00021', IMEI_A)],
        }, format='json')
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.json()['line'], 2)
        self.assertEqual(self.quantity(), 2)

    def test_actions_on_a_unit_go_through_the_api_and_are_audited(self):
        url = f'{self.LIST}{self.first.pk}/'
        self.assertEqual(self.api.post(url + 'reserve/', {'reason': 'Apartado'}, format='json').status_code, 200)
        self.assertEqual(self.api.post(url + 'release/', {}, format='json').json()['status'], 'available')
        self.assertEqual(self.api.patch(url, {'condition': 'open_box'}, format='json').json()['condition'], 'open_box')
        self.assertEqual(self.api.post(url + 'write-off/', {'reason': ''}, format='json').status_code, 400)
        self.assertEqual(self.api.post(url + 'write-off/', {'reason': 'Roto'}, format='json').json()['status'], 'written_off')
        self.assertEqual(self.api.post(url + 'write-off/', {'reason': 'Roto'}, format='json').status_code, 400)
        actions = set(AdminAuditLog.objects.filter(company=self.company).values_list('action', flat=True))
        self.assertTrue({'stock_unit_reserved', 'stock_unit_released', 'stock_unit_updated',
                         'stock_unit_written_off'} <= actions)

    def test_another_company_and_a_reader_cannot_change_anything(self):
        foreign = StockUnit.objects.get(serial_number='SERIEAJENA1')
        self.assertEqual(self.api.post(
            f'{self.LIST}{foreign.pk}/write-off/', {'reason': 'x'}, format='json').status_code, 404)
        self.assertEqual(self.api.post(self.LIST, {
            'product_id': self.foreign_phone.pk, 'branch': self.branch_a.pk, 'reason': 'Compra',
            'units': [self.row('SERIE00030', IMEI_D)],
        }, format='json').status_code, 404)

        from store.models import Membership
        Membership.objects.filter(pk=self.membership.pk).update(role='sales')
        self.assertEqual(self.api.post(
            f'{self.LIST}{self.first.pk}/write-off/', {'reason': 'x'}, format='json').status_code, 403)
        self.assertEqual(APIClient().get(self.LIST).status_code, 401)

    def test_the_summary_counts_available_units_as_part_of_the_stock(self):
        units.reserve(self.second, reason='Apartado', actor=self.staff)

        summary = self.api.get('/api/admin/inventory/summary/?branch=all').json()

        self.assertEqual(summary['equipment_available'], 2)       # uno en A, uno en B
        self.assertEqual(summary['equipment_reserved'], 1)
        # Subconjunto, no suma: las unidades totales ya incluyen a los equipos.
        self.assertEqual(summary['total_units'], 13 + 2)

        only_a = self.api.get(f'/api/admin/inventory/summary/?branch={self.branch_a.pk}').json()
        self.assertEqual(only_a['equipment_available'], 1)

    def test_a_refused_unit_names_the_field_that_is_wrong(self):
        """El formulario pinta el error debajo del campo: el servidor dice cuál es."""
        def refused(**row):
            res = self.api.post(self.LIST, {
                'product_id': self.phone.pk, 'branch': self.branch_a.pk, 'reason': 'Compra',
                'units': [{'serial_number': 'SERIE00090', 'imei': IMEI_D, **row}],
            }, format='json')
            self.assertEqual(res.status_code, 400, res.content)
            return res.json()

        self.assertEqual(refused(serial_number='')['field'], 'serial_number')
        self.assertEqual(refused(serial_number='N/A')['field'], 'serial_number')
        self.assertEqual(refused(serial_number='SERIE00001')['field'], 'serial_number')   # ya existe
        self.assertEqual(refused(imei='')['field'], 'imei')
        self.assertEqual(refused(imei='356938035643800')['field'], 'imei')                # Luhn
        self.assertEqual(refused(imei=IMEI_A)['field'], 'imei')                           # ya existe
        self.assertEqual(refused(imei2=IMEI_D)['field'], 'imei2')                         # igual al primero
        self.assertEqual(refused(imei2='123')['field'], 'imei2')
        self.assertEqual(refused(cost='-1')['field'], 'cost')
        self.assertEqual(refused(price_override='caro')['field'], 'price_override')
        self.assertEqual(refused(condition='inventada')['field'], 'condition')
        missing_reason = self.api.post(self.LIST, {
            'product_id': self.phone.pk, 'branch': self.branch_a.pk, 'reason': '',
            'units': [{'serial_number': 'SERIE00091', 'imei': IMEI_D}],
        }, format='json').json()
        self.assertEqual(missing_reason['field'], 'reason')
        self.assertEqual(self.quantity(), 2)

    def test_the_product_list_counts_only_the_branches_the_caller_reaches(self):
        """Quien sólo opera en una sucursal no aprende el stock de las otras por la lista del formulario."""
        self.receive(self.row('NORTE00001', imei('35693803564377')), branch=self.branch_b)
        everywhere = self.quantity() + self.quantity(branch=self.branch_b)
        self.restrict_to_branch_a()

        rows = {r['id']: r for r in self.api.get(f'{self.LIST}products/?scope=all').json()['results']}

        self.assertGreater(everywhere, self.quantity())
        self.assertEqual(rows[self.phone.pk]['stock'], self.quantity())
        # Lo que NO cambia: con equipos en CUALQUIER sucursal, el modo no se puede cambiar.
        self.assertFalse(rows[self.phone.pk]['can_change_tracking'])

    def test_the_form_can_list_every_product_and_say_how_each_is_counted(self):
        """
        Elegir un producto que no lleva serie no puede acabar en un formulario
        mudo: la pantalla necesita saber que no la lleva, cuánto stock tiene y
        si se le puede activar.
        """
        _m7_stock(self.branch_a, self.laptop, 0)
        loose_empty = _prod(self.company, 'Tablet sin serie', 'tablet-sin-serie', inventory=0)

        rows = {r['name']: r for r in self.api.get(f'{self.LIST}products/?scope=all').json()['results']}

        self.assertEqual(rows['iPhone 16'], {
            'id': self.phone.pk, 'name': 'iPhone 16', 'price': '4500.00',
            'is_serialized': True, 'requires_imei': True, 'stock': 3, 'can_change_tracking': False,
        })
        # Tiene unidades sueltas: no se le puede activar la serie hasta dejarlo en cero.
        self.assertFalse(rows['iPhone 15']['is_serialized'])
        self.assertEqual(rows['iPhone 15']['stock'], 13)
        self.assertFalse(rows['iPhone 15']['can_change_tracking'])
        self.assertTrue(rows[loose_empty.name]['can_change_tracking'])
        self.assertNotIn('Teléfono ajeno', rows)
        self.assertNotIn('Ajeno', rows)
        # Sin el parámetro, lo de siempre: sólo los que llevan serie.
        default = [r['name'] for r in self.api.get(f'{self.LIST}products/').json()['results']]
        self.assertEqual(sorted(default), ['MacBook Air', 'iPhone 16'])

    def test_products_say_whether_they_are_serialized(self):
        res = self.api.get(f'{self.LIST}products/')
        self.assertEqual(res.status_code, 200, res.content)
        names = [row['name'] for row in res.json()['results']]
        self.assertEqual(sorted(names), ['MacBook Air', 'iPhone 16'])
        phone = next(r for r in res.json()['results'] if r['name'] == 'iPhone 16')
        self.assertTrue(phone['requires_imei'])


class SurroundingsTest(UnitsBase):
    """Lo que rodea a los equipos: el tablero, el catálogo y la carga masiva."""

    def setUp(self):
        super().setUp()
        self.receive(self.row('SERIE00001', IMEI_A), self.row('SERIE00002', IMEI_B))
        self.receive(self.row('SERIE00003', IMEI_C), branch=self.branch_b)
        self.api = APIClient()
        self.api.force_authenticate(user=self.staff)

    def test_the_dashboard_says_how_many_devices_are_available_in_what_the_caller_sees(self):
        snapshot = self.api.get('/api/me/internal-dashboard/').json()['inventory']
        self.assertEqual(snapshot['equipment_available'], 3)
        self.assertEqual(snapshot['equipment_reserved'], 0)
        # Los equipos ya están dentro de las unidades: no se suman encima.
        self.assertEqual(snapshot['total_units'], 13 + 3)

        self.restrict_to_branch_a()
        snapshot = self.api.get('/api/me/internal-dashboard/').json()['inventory']
        self.assertEqual(snapshot['equipment_available'], 2)

    def test_the_catalogue_says_which_products_are_counted_by_unit(self):
        from store.serializers import AdminProductSerializer

        data = AdminProductSerializer(self.phone).data
        self.assertTrue(data['is_serialized'])
        self.assertTrue(data['requires_imei'])
        self.assertFalse(AdminProductSerializer(self.product).data['is_serialized'])

    def test_how_a_product_is_counted_changes_through_its_own_endpoint_only(self):
        empty = _prod(self.company, 'Watch', 'watch-api-serie', inventory=0)
        url = f'/api/admin/inventory/units/products/{empty.pk}/serialization/'

        res = self.api.post(url, {'is_serialized': True, 'requires_imei': False}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.json()['is_serialized'])

        busy = f'/api/admin/inventory/units/products/{self.phone.pk}/serialization/'
        self.assertEqual(self.api.post(busy, {'is_serialized': False}, format='json').status_code, 400)
        foreign = f'/api/admin/inventory/units/products/{self.foreign_phone.pk}/serialization/'
        self.assertEqual(self.api.post(foreign, {'is_serialized': False}, format='json').status_code, 404)

        # El formulario del producto no puede cambiarlo de paso.
        patched = self.api.patch(
            f'/api/admin/products/{self.phone.pk}/', {'is_serialized': False}, format='json')
        self.phone.refresh_from_db()
        self.assertTrue(self.phone.is_serialized, patched.status_code)

    def test_a_bulk_stock_file_cannot_type_a_quantity_for_devices(self):
        from store import stock_import_services
        from store.models import BulkImportJob, ProductBarcode
        from store.tests import _c14_stock_workbook, _c14_upload

        ProductBarcode.objects.create(
            company=self.company, product=self.phone, code='SERIE-IPHONE16',
            symbology=ProductBarcode.INTERNAL)
        payload = _c14_stock_workbook([
            {'ID': 1, 'CODIGO': 'SERIE-IPHONE16', 'NOMBRE': 'iPhone 16', 'ALMACEN 1 - 11416': 9},
        ])
        job = stock_import_services.preview_stock(
            company=self.company, actor=self.staff, upload=_c14_upload(payload),
            filename='inventario.xlsx', branch_map={4: self.branch_a.pk},
            mode=BulkImportJob.MODE_RECONCILE,
        )

        row = job.rows.first()
        self.assertTrue(row.errors, row.normalized_data)
        self.assertIn('serie', ' '.join(row.errors))
        self.assertEqual(self.quantity(), 2)
