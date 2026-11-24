"""
POS-ORDER-ARRIVAL-01 — «Llegó el equipo» en caja cuando ese equipo ya está vendido bajo pedido.

Lo que estas pruebas fijan:

  * el selector de la caja dice, por combinación, qué líneas pagadas esperan un equipo del proveedor
    (`pending_order_lines`): sólo de la empresa, de pedidos que quien consulta puede ver, comprometidas y aún sin
    recibir ni cancelar; el cliente va enmascarado; las consultas no crecen con las líneas;
  * recibir como stock libre mientras esa variante tiene líneas esperando en ESA sucursal es 409
    `pending_order_line` (con las líneas), salvo `confirm_stock` con un motivo, que queda auditado;
  * recibir con `order_item` entrega el equipo a su pedido (`OrderUnit`) y la línea deja de esperar;
  * la respuesta dice la sucursal donde el equipo ENTRÓ (la del pedido) y cómo se leyó (lector o a mano);
  * la idempotencia no cambia: la misma clave y el mismo cuerpo responden lo mismo sin escribir otra vez.

Los nombres de prueba son inventados (AGENTS §2). IMEI sintéticos con dígito de control válido.
"""
import time
from datetime import timedelta
from decimal import Decimal as D
from threading import Event, Thread

from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from store import inventory_services as inventory
from store import supply_services as supply
from store.models import (
    AdminAuditLog, Order, OrderItem, OrderReturn, OrderUnit, Product, StockUnit,
)
from store.test_pos_quick_device import MANAGER_CAPS, QuickShop, _api
from store.tests import _p2d_member, _p3_company, _p3_order, _prod

PENDING_AUDIT = 'pos_device_received_over_pending_order'


class ArrivalShop(QuickShop):
    """`QuickShop` whose existing variant also sells on order, with a valid offer."""

    def build(self, slug='poa-tienda'):
        super().build(slug)
        Product.objects.filter(pk=self.existing.pk).update(sale_mode=Product.SALE_MODE_STOCK_OR_ORDER)
        self.existing.refresh_from_db()
        today = timezone.localdate()
        self.offer = supply.create_offer(
            company=self.company, supplier_id=self.supplier.pk, product_id=self.existing.pk, agreed_cost='2500.00',
            commercial_price='3100.00', confirmed_quantity=20, valid_from=today, valid_until=today + timedelta(days=5),
            lead_time_days=3, may_view_costs=True)
        self.existing_key = self.key_of(self.existing)

    def key_of(self, product):
        from store.models import ProductVariantOption
        from store import device_catalog

        return device_catalog.variant_key(
            ProductVariantOption.objects.filter(product=product).values_list('option_id', flat=True))

    def pending(self, *, branch=None, customer='Ana María Quispe', status=OrderItem.SUPPLY_PENDING,
                order_status=Order.Status.PAID, product=None, company=None, origin=OrderItem.SUPPLY_SUPPLIER,
                quantity=1, **order_extra):
        """A paid order with ONE on-order line of the existing variant waiting for its device."""
        company = company or self.company
        order = _p3_order(company, branch=branch or (self.branch if company == self.company else None),
                          customer_name=customer, status=order_status, **order_extra)
        order.items.all().delete()           # `_p3_order` adds a plain line; this order is only the device
        return OrderItem.objects.create(
            order=order, product=product or self.existing, quantity=quantity, price=D('3100.00'),
            unit_cost=D('2500.00'), supply_origin=origin, supply_status=status,
            supplier_offer=self.offer if company == self.company else None, lead_time_days=3)

    def combination(self, body, key=None):
        return next(c for c in body['combinations'] if c['key'] == (key or self.existing_key))

    def existing_body(self, **extra):
        body = {'model': self.model.pk, 'option_ids': [self.gb128.pk, self.black.pk, self.esim.pk, self.lla.pk],
                'imei': next(self._imei)}
        body.update(extra)
        return body


# ---------------------------------------------------------------------------
# What the picker reads
# ---------------------------------------------------------------------------

class PendingLinesListedTest(ArrivalShop, TestCase):
    def setUp(self):
        self.build('poa-lista')

    def test_a_waiting_line_is_listed_on_its_combination_with_the_customer_masked(self):
        line = self.pending(customer='Ana María Quispe')
        res = _api(self.manager).get(self.config_url())
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        rows = self.combination(body)['pending_order_lines']
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row['order_item'], row['order_id'], row['order_number'], row['quantity']),
                         (line.pk, line.order_id, line.order_id, 1))
        self.assertEqual(row['customer'], 'Ana Q.')
        self.assertEqual(row['branch'], {'id': self.branch.pk, 'name': self.branch.name})
        self.assertTrue(row['here'])
        self.assertTrue(row['created_at'])
        self.assertNotIn('Quispe', res.content.decode())
        self.assertNotIn('cliente-p3@example.invalid', res.content.decode())
        # Every other combination has an empty list, never a missing key.
        others = [c for c in body['combinations'] if c['key'] != self.existing_key]
        self.assertTrue(others)
        self.assertTrue(all(c['pending_order_lines'] == [] for c in others))
        self.assertTrue(body['can']['receive_on_order'])

    def test_lines_that_do_not_wait_for_a_device_are_not_listed(self):
        waiting = self.pending(customer='Bruno Vela')
        self.pending(customer='Recibido', status=OrderItem.SUPPLY_RECEIVED)
        self.pending(customer='Por confirmar', status=OrderItem.SUPPLY_TO_CONFIRM)
        self.pending(customer='Fallido', status=OrderItem.SUPPLY_FAILED)
        self.pending(customer='Cancelado', order_status=Order.Status.CANCELLED)
        self.pending(customer='Sin pagar', order_status=Order.Status.PENDING_PAYMENT, paid=False)
        self.pending(customer='Del estante', origin=OrderItem.SUPPLY_OWN, status='')
        returned = self.pending(customer='Devuelto')
        OrderReturn.objects.create(company=self.company, order=returned.order, reason='Devuelve el equipo',
                                   kind=OrderReturn.KIND_OTHER, status=OrderReturn.STATUS_REQUESTED)
        other = _p3_company('poa-lista-b', 'Otra tienda')
        foreign = _prod(other, 'Teléfono ajeno', 'poa-ajeno', inventory=0)
        self.pending(company=other, product=foreign, customer='Ajeno')
        rows = self.combination(_api(self.manager).get(self.config_url()).json())['pending_order_lines']
        self.assertEqual([r['order_item'] for r in rows], [waiting.pk])

    def test_a_line_of_another_branch_is_listed_only_to_who_may_see_that_branch(self):
        north = self.pending(branch=self.north, customer='Carla Norte')
        rows = self.combination(_api(self.manager).get(self.config_url()).json())['pending_order_lines']
        self.assertEqual([(r['order_item'], r['here'], r['branch']['id']) for r in rows],
                         [(north.pk, False, self.north.pk)])
        only_here, _ = _p2d_member(self.company, 'poa-lista-solo-centro', MANAGER_CAPS, branches=[self.branch])
        rows = self.combination(_api(only_here).get(self.config_url()).json())['pending_order_lines']
        self.assertEqual(rows, [])

    def test_a_seller_who_cannot_receive_reads_no_order_or_customer(self):
        self.pending(customer='Diego Reyes')
        res = _api(self.seller).get(self.config_url())
        self.assertEqual(self.combination(res.json())['pending_order_lines'], [])
        self.assertNotIn('Diego', res.content.decode())
        self.assertFalse(res.json()['can']['receive_on_order'])

    def test_a_receiver_without_orders_capability_still_sees_what_waits(self):
        """He must know why plain stock is refused; delivering to the order is someone else's (`can`)."""
        self.pending(customer='Elena Paz')
        body = _api(self.receiver).get(self.config_url()).json()
        self.assertEqual(len(self.combination(body)['pending_order_lines']), 1)
        self.assertFalse(body['can']['receive_on_order'])

    def test_the_queries_do_not_grow_with_the_lines(self):
        self.pending(customer='Uno')
        with CaptureQueriesContext(connection) as one:
            self.assertEqual(_api(self.manager).get(self.config_url()).status_code, 200)
        for n in range(5):
            self.pending(customer=f'Cliente {n}', branch=self.north if n % 2 else self.branch)
        with CaptureQueriesContext(connection) as many:
            body = _api(self.manager).get(self.config_url()).json()
        self.assertEqual(len(self.combination(body)['pending_order_lines']), 6)
        self.assertEqual(len(many), len(one), [q['sql'] for q in many.captured_queries])


# ---------------------------------------------------------------------------
# The guard: plain stock while a paid order waits
# ---------------------------------------------------------------------------

class PlainStockGuardTest(ArrivalShop, TestCase):
    def setUp(self):
        self.build('poa-guarda')

    def test_plain_stock_while_a_line_waits_here_is_409_with_the_lines_and_writes_nothing(self):
        line = self.pending(customer='Ana María Quispe')
        before = self.counts()
        res = self.post(self.manager, self.existing_body(), key='g-1')
        self.assertEqual(res.status_code, 409, res.content)
        body = res.json()
        self.assertEqual(body['code'], 'pending_order_line')
        self.assertEqual([r['order_item'] for r in body['lines']], [line.pk])
        self.assertEqual(body['lines'][0]['customer'], 'Ana Q.')
        self.assertNotIn('Quispe', res.content.decode())
        self.assertEqual(self.counts(), before)
        self.assertFalse(AdminAuditLog.objects.filter(action__in=('pos_device_received', PENDING_AUDIT)).exists())
        # Refused: the key stays free for the right answer.
        again = self.post(self.manager, self.existing_body(order_item=line.pk), key='g-1')
        self.assertEqual(again.status_code, 201, again.content)

    def test_a_line_waiting_in_another_branch_does_not_stop_this_shelf(self):
        self.pending(branch=self.north)
        res = self.post(self.manager, self.existing_body(), key='g-2')
        self.assertEqual(res.status_code, 201, res.content)

    def test_a_line_of_another_variant_does_not_stop_this_one(self):
        self.pending()
        res = self.post(self.manager, self.body(), key='g-3')        # 256 GB · Azul: another combination
        self.assertEqual(res.status_code, 201, res.content)

    def test_confirming_plain_stock_needs_a_reason(self):
        self.pending()
        before = self.counts()
        for reason in (None, '', '   ', 'abc'):
            body = self.existing_body(confirm_stock=True)
            if reason is not None:
                body['reason'] = reason
            res = self.post(self.manager, body, key=f'g-4-{reason!r}')
            self.assertEqual((res.status_code, res.json().get('field')), (400, 'reason'), res.content)
        res = self.post(self.manager, self.existing_body(confirm_stock=True, reason='x' * 501), key='g-4-long')
        self.assertEqual((res.status_code, res.json().get('field')), (400, 'reason'), res.content)
        self.assertEqual(self.counts(), before)

    def test_confirmed_plain_stock_enters_the_shelf_and_is_audited_with_its_reason(self):
        line = self.pending()
        shelf = inventory.branch_quantity(self.branch, self.existing)
        body = self.existing_body(confirm_stock=True, reason='  Es otro equipo,  para vitrina ')
        res = self.post(self.receiver, body, key='g-5')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['mode'], 'stock')
        self.assertEqual(inventory.branch_quantity(self.branch, self.existing), shelf + 1)
        line.refresh_from_db()
        self.assertEqual(line.supply_status, OrderItem.SUPPLY_PENDING)
        audit = AdminAuditLog.objects.get(action=PENDING_AUDIT)
        self.assertEqual((audit.company, audit.actor, audit.target_type, audit.target_id),
                         (self.company, self.receiver, 'stock_unit', str(res.json()['unit']['unit_id'])))
        self.assertEqual(audit.metadata['reason'], 'Es otro equipo, para vitrina')
        self.assertEqual(audit.metadata['order_item_ids'], [line.pk])
        self.assertEqual((audit.metadata['product_id'], audit.metadata['branch_id']),
                         (self.existing.pk, self.branch.pk))
        self.assertNotIn(body['imei'], str(audit.metadata))
        # The same key and body: the same device, nothing written twice, one audit line.
        after = self.counts()
        again = self.post(self.receiver, body, key='g-5')
        self.assertEqual((again.status_code, again.json()['replayed']), (200, True))
        self.assertEqual(again.json()['unit']['unit_id'], res.json()['unit']['unit_id'])
        self.assertEqual(self.counts(), after)
        self.assertEqual(AdminAuditLog.objects.filter(action=PENDING_AUDIT).count(), 1)
        self.assertEqual(AdminAuditLog.objects.filter(action='pos_device_received').count(), 1)

    def test_without_waiting_lines_a_confirmation_is_not_needed_and_not_audited(self):
        res = self.post(self.manager, self.existing_body(confirm_stock=True, reason='Por si acaso'), key='g-6')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertFalse(AdminAuditLog.objects.filter(action=PENDING_AUDIT).exists())


# ---------------------------------------------------------------------------
# Delivering the device to its order
# ---------------------------------------------------------------------------

class DeliverToOrderTest(ArrivalShop, TestCase):
    def setUp(self):
        self.build('poa-pedido')

    def test_the_device_goes_to_its_order_and_the_line_stops_waiting(self):
        line = self.pending(customer='Ana María Quispe')
        shelf = inventory.branch_quantity(self.branch, self.existing)
        value = next(self._imei)
        res = self.post(self.manager, {'order_item': line.pk, 'imei': value, 'method': 'scanner'}, key='o-1')
        self.assertEqual(res.status_code, 201, res.content)
        answer = res.json()
        self.assertEqual((answer['mode'], answer['order']), ('order', {'id': line.order_id, 'item_id': line.pk}))
        self.assertEqual(answer['method'], 'scanner')
        line.refresh_from_db()
        self.assertEqual(line.supply_status, OrderItem.SUPPLY_RECEIVED)
        unit = StockUnit.objects.get(imei=value)
        self.assertEqual((unit.status, unit.order_id), ('sold', line.order_id))
        record = OrderUnit.objects.get(order=line.order, unit=unit)
        self.assertEqual(record.method, 'scanner')
        self.assertEqual(inventory.branch_quantity(self.branch, self.existing), shelf)
        rows = self.combination(_api(self.manager).get(self.config_url()).json())['pending_order_lines']
        self.assertEqual(rows, [])
        # Nothing waits any more: plain stock of the same variant is no longer refused.
        self.assertEqual(self.post(self.manager, self.existing_body(), key='o-1b').status_code, 201)
        # The same key and body replay the same answer without a second device.
        again = self.post(self.manager, {'order_item': line.pk, 'imei': value, 'method': 'scanner'}, key='o-1')
        self.assertEqual((again.status_code, again.json()['replayed']), (200, True))
        self.assertEqual(again.json()['unit']['unit_id'], unit.pk)
        self.assertEqual(StockUnit.objects.filter(imei=value).count(), 1)
        self.assert_invariant()

    def test_the_answer_names_the_branch_where_the_device_entered(self):
        line = self.pending(branch=self.north)
        res = self.post(self.manager, {'order_item': line.pk, 'imei': next(self._imei)}, key='o-2')
        self.assertEqual(res.status_code, 201, res.content)
        answer = res.json()
        unit = StockUnit.objects.get(pk=answer['unit']['unit_id'])
        self.assertEqual(unit.branch, self.north)
        self.assertEqual(answer['branch'], {'id': self.north.pk, 'name': self.north.name})
        self.assertEqual(answer['product']['available'], inventory.branch_quantity(self.north, self.existing))
        replay = self.post(self.manager, {'order_item': line.pk, 'imei': unit.imei}, key='o-2').json()
        self.assertEqual(replay['branch'], answer['branch'])

    def test_plain_stock_answers_the_tills_branch(self):
        res = self.post(self.manager, self.body(), key='o-3')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['branch'], {'id': self.branch.pk, 'name': self.branch.name})

    def test_a_receiver_without_orders_capability_cannot_deliver_to_the_order(self):
        line = self.pending()
        res = self.post(self.receiver, {'order_item': line.pk, 'imei': next(self._imei)}, key='o-4')
        self.assertEqual((res.status_code, res.json()['code']), (403, 'receive_forbidden'))


class EntryMethodTest(ArrivalShop, TestCase):
    def setUp(self):
        self.build('poa-metodo')

    def test_the_method_is_manual_unless_the_scanner_read_it(self):
        manual = self.post(self.manager, self.body(), key='m-1')
        self.assertEqual((manual.status_code, manual.json()['method']), (201, 'manual'))
        scanned = self.post(self.manager, self.body(method='scanner'), key='m-2')
        self.assertEqual((scanned.status_code, scanned.json()['method']), (201, 'scanner'))
        record = AdminAuditLog.objects.filter(action='pos_device_received').order_by('-pk').first()
        self.assertEqual(record.metadata['record']['method'], 'scanner')

    def test_a_caller_who_may_not_receive_is_403_before_any_400_about_the_method(self):
        res = self.post(self.seller, self.body(method='telepathy'), key='m-4')
        self.assertEqual((res.status_code, res.json()['code']), (403, 'receive_forbidden'), res.content)

    def test_an_unknown_method_is_400_and_writes_nothing(self):
        before = self.counts()
        res = self.post(self.manager, self.body(method='telepathy'), key='m-3')
        self.assertEqual((res.status_code, res.json()['field']), (400, 'method'), res.content)
        self.assertEqual(self.counts(), before)


# ---------------------------------------------------------------------------
# The guard and a delivery of the same line at the same instant
# ---------------------------------------------------------------------------

class GuardRaceTest(ArrivalShop, TransactionTestCase):
    """
    The plain-stock guard LOCKS the waiting lines it reads (`select_for_update`): a delivery of that line that is
    mid-way waits for it, or it waits for the delivery and reads the line as it is once that commits.
    """

    HOLD = 0.6

    def setUp(self):
        self.build('poa-carrera')

    def test_plain_stock_waits_for_a_delivery_in_flight_and_reads_the_line_after_it(self):
        if connection.vendor != 'postgresql':
            self.skipTest('needs PostgreSQL row locks')
        line = self.pending()
        locked, done = Event(), Event()

        def deliver():
            # What `receive_line` does under the order's lock: the line is taken and marked received; the commit
            # comes a moment later.
            try:
                with transaction.atomic():
                    OrderItem.objects.select_for_update().get(pk=line.pk)
                    OrderItem.objects.filter(pk=line.pk).update(supply_status=OrderItem.SUPPLY_RECEIVED)
                    locked.set()
                    time.sleep(self.HOLD)
                done.set()
            finally:
                connection.close()

        thread = Thread(target=deliver)
        thread.start()
        self.assertTrue(locked.wait(10))
        started = time.monotonic()
        res = self.post(self.manager, self.existing_body(), key='race-1')
        waited = time.monotonic() - started
        thread.join()
        # Without the lock the guard read the line as still pending (the delivery had not committed) and refused.
        self.assertTrue(done.is_set())
        self.assertEqual(res.status_code, 201, res.content)
        self.assertGreaterEqual(waited, self.HOLD * 0.8)
        self.assert_invariant()
