"""
ORDER-FULFILLMENT-SAFETY-01 · dispatch moves one step along the order's path,
and an accidental «sent» can be corrected without rewriting the record.

What production showed (order #4, 2026-10-12): a PICKUP order, paid, marked
«Enviado» twenty minutes after its payment, then «Cancelado operativo», then
pending, ready and «Cancelado operativo» again. `ever_left` read the old
«Enviado» entry, correctly, and refused the cancellation for good.

The rules now:
  * the path is the delivery mode's — pickup: pending, preparing, ready,
    delivered; shipping: pending, preparing, sent, delivered;
  * exactly the next step; one step back with a reason, only while the goods
    never left; «Cancelado operativo» only from a commercial cancellation;
  * «Enviado» by carrier needs the carrier and its waybill;
  * a superior may declare ONE leaving entry accidental: a new audit entry
    names it, `ever_left` stops counting it, nothing else changes.
"""
from decimal import Decimal
from threading import Barrier, Thread

from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from store import left_marking
from store import order_cancellation as cancelling
from store import unit_identification as picking
from store.fulfillment_test_support import legacy_move, place_at
from store.models import (
    AdminAuditLog, Order, OrderDelivery, OrderRefund, OrderUnit, PaymentTransaction, SalesCommission,
    StockMovement,
)
from store.test_order_cancel import _Base
from store.test_stock_units import IMEI_A
from store.tests import _p2d_member

FS = Order.FulfillmentStatus
CORRECT_REASON = 'Marcado por error durante pruebas: el equipo nunca salió'


class _Flow(_Base):
    def setUp(self):
        super().setUp()
        self.superior, _ = _p2d_member(
            self.company, 'despacho_superior',
            ['company.view', 'company.manage', 'sales.orders.view', 'sales.orders.manage'])
        self.boss = APIClient()
        self.boss.force_authenticate(self.superior)
        self.reader_user, _ = _p2d_member(self.company, 'despacho_lector', ['company.view', 'sales.orders.view'])
        self.reader = APIClient()
        self.reader.force_authenticate(self.reader_user)

    def pickup(self, **kw):
        return self.web_order(delivery_method=Order.DeliveryMethod.PICKUP_STORE, **kw)

    def shipping(self, mode=OrderDelivery.Mode.LOCAL_DELIVERY, **kw):
        order = self.web_order(delivery_method=Order.DeliveryMethod.DELIVERY_AREQUIPA, **kw)
        OrderDelivery.objects.create(company=self.company, order=order, mode=mode)
        return order

    def move(self, order, status, note='', client=None):
        return (client or self.desk).patch(
            f'/api/admin/orders/{order.pk}/fulfillment-status/?{self.q}',
            {'fulfillment_status': status, 'note': note}, format='json')

    def correct(self, order, audit_id, reason=CORRECT_REASON, never_left=True, client=None):
        return (client or self.boss).post(
            f'/api/admin/orders/{order.pk}/left-markings/{audit_id}/correct/?{self.q}',
            {'reason': reason, 'never_left': never_left}, format='json')

    def status_of(self, order):
        order.refresh_from_db()
        return order.fulfillment_status

    def refused(self, response, code='transition_not_allowed', http=409):
        self.assertEqual(response.status_code, http, response.content)
        self.assertEqual(response.json()['code'], code)


class PathByDeliveryModeTest(_Flow):
    def test_a_pickup_order_walks_its_four_steps(self):
        order = self.pickup()
        for status in (FS.PREPARING, FS.READY_FOR_PICKUP, FS.DELIVERED):
            self.assertEqual(self.move(order, status).status_code, 200)
            self.assertEqual(self.status_of(order), status)

    def test_a_pickup_order_is_never_sent(self):
        order = self.pickup()
        for at in (FS.PENDING, FS.PREPARING, FS.READY_FOR_PICKUP):
            with self.subTest(at):
                place_at(order, at)
                response = self.move(order, FS.SHIPPED)
                self.refused(response)
                self.assertIn('recojo en tienda', response.json()['detail'])
                self.assertEqual(self.status_of(order), at)
        self.assertFalse(cancelling.leaving_events(order).exists())

    def test_a_shipped_order_never_waits_for_pickup(self):
        order = self.shipping()
        place_at(order, FS.PREPARING)
        self.refused(self.move(order, FS.READY_FOR_PICKUP))
        self.assertEqual(self.move(order, FS.SHIPPED).status_code, 200)   # own delivery: nothing invented
        self.assertEqual(self.move(order, FS.DELIVERED).status_code, 200)

    def test_no_step_is_skipped(self):
        order = self.pickup()
        for target in (FS.READY_FOR_PICKUP, FS.DELIVERED):
            with self.subTest(target):
                response = self.move(order, target)
                self.refused(response)
                self.assertIn('«En preparación»', response.json()['detail'])
        self.assertEqual(self.status_of(order), FS.PENDING)

    def test_confirmed_stands_where_pending_stands(self):
        order = self.pickup(fulfillment_status='confirmed')
        self.assertEqual(self.move(order, FS.PREPARING).status_code, 200)

    def test_delivered_is_the_end(self):
        order = self.pickup(fulfillment_status='ready_for_pickup')
        self.assertEqual(self.move(order, FS.DELIVERED).status_code, 200)
        for target in (FS.DELIVERED, FS.READY_FOR_PICKUP, FS.PENDING):
            with self.subTest(target):
                self.refused(self.move(order, target, note='quiero volver atrás'))
        self.assertEqual(cancelling.leaving_events(order).count(), 1)     # handed over once

    def test_cancelado_operativo_is_not_a_step_anyone_takes(self):
        order = self.pickup()
        for at in (FS.PENDING, FS.PREPARING, FS.READY_FOR_PICKUP):
            with self.subTest(at):
                place_at(order, at)
                response = self.move(order, FS.CANCELLED, note='cliente ya no quiere')
                self.refused(response)
                self.assertIn('Cancelar pedido', response.json()['detail'])
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)

    def test_a_carrier_needs_its_name_and_its_waybill(self):
        order = self.shipping(mode=OrderDelivery.Mode.NATIONAL_ADDRESS)
        place_at(order, FS.PREPARING)
        response = self.move(order, FS.SHIPPED)
        self.refused(response, 'logistics_missing')
        self.assertIn('transportista', response.json()['detail'])
        self.assertIn('guía', response.json()['detail'])
        OrderDelivery.objects.filter(order=order).update(carrier_name='Olva Courier', waybill_number='G-1001')
        self.assertEqual(self.move(order, FS.SHIPPED).status_code, 200)

    def test_an_agency_pickup_also_names_the_agency(self):
        order = self.shipping(mode=OrderDelivery.Mode.AGENCY_PICKUP)
        place_at(order, FS.PREPARING)
        OrderDelivery.objects.filter(order=order).update(carrier_name='Shalom', tracking_code='T-77')
        response = self.move(order, FS.SHIPPED)
        self.refused(response, 'logistics_missing')
        self.assertIn('agencia', response.json()['detail'])
        OrderDelivery.objects.filter(order=order).update(agency_name='Shalom Cusco')
        self.assertEqual(self.move(order, FS.SHIPPED).status_code, 200)

    def test_the_app_has_the_same_rules(self):
        order = self.pickup()
        member, _ = _p2d_member(self.company, 'despacho_app', ['company.view', 'sales.orders.view', 'sales.orders.manage'])
        app = APIClient()
        app.force_authenticate(member)
        response = app.patch(
            f'/api/v1/internal/{self.company.slug}/orders/{order.pk}/fulfillment/',
            {'fulfillment_status': FS.SHIPPED}, format='json')
        self.refused(response)


class BackOneStepTest(_Flow):
    def test_back_needs_a_reason(self):
        order = self.pickup(fulfillment_status='preparing')
        self.refused(self.move(order, FS.PENDING), 'reason_required', 400)
        self.refused(self.move(order, FS.PENDING, note='abc'), 'reason_required', 400)
        self.assertEqual(self.move(order, FS.PENDING, note='Se preparó el pedido equivocado').status_code, 200)
        entry = AdminAuditLog.objects.filter(action='order_fulfillment_status_changed').latest('pk')
        self.assertEqual(entry.metadata['direction'], 'back')
        self.assertEqual(entry.metadata['note'], 'Se preparó el pedido equivocado')

    def test_only_one_step_back(self):
        order = self.pickup(fulfillment_status='ready_for_pickup')
        self.refused(self.move(order, FS.PENDING, note='volver al inicio'))
        self.assertEqual(self.move(order, FS.PREPARING, note='faltaba el cargador').status_code, 200)

    def test_nothing_goes_back_once_it_left(self):
        order = self.shipping(fulfillment_status='preparing')
        self.assertEqual(self.move(order, FS.SHIPPED).status_code, 200)
        response = self.move(order, FS.PREPARING, note='lo marqué antes de tiempo')
        self.refused(response)
        self.assertIn('ya salió', response.json()['detail'])

    def test_an_order_that_once_left_does_not_step_back_either(self):
        """A pre-phase past: sent, then relabelled pending. The goods are still gone."""
        order = self.pickup()
        legacy_move(order, FS.SHIPPED, actor=self.manager)
        legacy_move(order, FS.PENDING, actor=self.manager)
        self.assertEqual(self.move(order, FS.PREPARING).status_code, 200)   # forward is harmless
        self.refused(self.move(order, FS.PENDING, note='volver a pendiente'))

    def test_an_operational_cancel_left_by_the_old_selector_goes_back_to_pending(self):
        order = self.pickup(fulfillment_status='cancelled')
        self.assertEqual(self.detail(order)['available_fulfillment_transitions'], [FS.PENDING])
        self.refused(self.move(order, FS.CANCELLED, note='otra vez cancelado'))
        self.refused(self.move(order, FS.PENDING), 'reason_required', 400)
        self.assertEqual(self.move(order, FS.PENDING, note='Se detuvo por error').status_code, 200)

    def test_but_not_when_it_once_left(self):
        order = self.pickup()
        legacy_move(order, FS.SHIPPED, actor=self.manager)
        legacy_move(order, FS.CANCELLED, actor=self.manager)
        self.refused(self.move(order, FS.PENDING, note='Se detuvo por error'))


class DispatchViewTest(_Flow):
    def test_the_detail_offers_only_the_next_step_and_the_one_back(self):
        order = self.pickup(fulfillment_status='preparing')
        detail = self.detail(order)
        self.assertEqual(detail['available_fulfillment_transitions'], [FS.PENDING, FS.READY_FOR_PICKUP])
        dispatch = detail['dispatch']
        self.assertEqual([s['status'] for s in dispatch['steps']],
                         [FS.PENDING, FS.PREPARING, FS.READY_FOR_PICKUP, FS.DELIVERED])
        self.assertEqual(dispatch['kind'], 'pickup')
        self.assertEqual((dispatch['forward'], dispatch['back']), (FS.READY_FOR_PICKUP, FS.PENDING))
        self.assertEqual([s['state'] for s in dispatch['steps']], ['done', 'current', 'todo', 'todo'])

    def test_a_shipping_path_has_no_pickup_step(self):
        dispatch = self.detail(self.shipping())['dispatch']
        self.assertEqual([s['status'] for s in dispatch['steps']],
                         [FS.PENDING, FS.PREPARING, FS.SHIPPED, FS.DELIVERED])
        self.assertEqual(dispatch['kind'], 'shipping')

    def test_a_reader_sees_the_path_and_the_history_but_no_step(self):
        order = self.pickup()
        self.move(order, FS.PREPARING, note='armando')
        detail = self.detail(order, client=self.reader)
        self.assertEqual(detail['available_fulfillment_transitions'], [])
        dispatch = detail['dispatch']
        self.assertFalse(dispatch['may_manage'])
        self.assertIsNone(dispatch['forward'])
        self.assertEqual([h['new_status'] for h in dispatch['history']], [FS.PREPARING])
        self.assertEqual(dispatch['steps'][1]['actor'], 'pedidos_cancel')

    def test_a_cancelled_order_shows_its_record_and_offers_nothing(self):
        order = self.pickup()
        self.move(order, FS.PREPARING)
        self.assertEqual(self.post_cancel(order).status_code, 200)
        detail = self.detail(order)
        self.assertEqual(detail['available_fulfillment_transitions'], [])
        self.assertIn('cancelado', detail['dispatch']['notice'])
        self.assertEqual([h['action'] for h in detail['dispatch']['history']][-1], 'order_cancelled')

    def test_what_still_stops_the_next_step_is_said_before_trying(self):
        order = self.shipping(mode=OrderDelivery.Mode.NATIONAL_ADDRESS, fulfillment_status='preparing')
        codes = [b['code'] for b in self.detail(order)['dispatch']['blockers']]
        self.assertEqual(codes, ['logistics_missing'])

    def test_the_history_is_faithful_and_carries_no_buyer_email(self):
        order = self.pickup()
        legacy_move(order, FS.SHIPPED, actor=self.manager)          # incoherent for pickup: shown, not tidied
        history = self.detail(order)['dispatch']['history']
        self.assertEqual((history[0]['old_status'], history[0]['new_status']), (FS.PENDING, FS.SHIPPED))
        self.assertNotIn('rosa@example.com', str(history))
        self.assertIn('no corresponde a esta modalidad', self.detail(order)['dispatch']['notice'])

    def test_another_company_sees_nothing_of_it(self):
        order = self.pickup()
        stranger, _ = _p2d_member(
            self.other, 'despacho_ajeno',
            ['company.view', 'company.manage', 'sales.orders.view', 'sales.orders.manage'])
        client = APIClient()
        client.force_authenticate(stranger)
        q = f'company={self.other.pk}'
        self.assertEqual(client.get(f'/api/admin/orders/{order.pk}/?{q}').status_code, 404)
        self.assertEqual(client.patch(f'/api/admin/orders/{order.pk}/fulfillment-status/?{q}',
                                      {'fulfillment_status': FS.PREPARING}, format='json').status_code, 404)
        event = legacy_move(order, FS.SHIPPED, actor=self.manager)
        self.assertEqual(client.post(f'/api/admin/orders/{order.pk}/left-markings/{event.pk}/correct/?{q}',
                                     {'reason': CORRECT_REASON, 'never_left': True}, format='json').status_code, 404)
        self.assertEqual(client.post(f'/api/admin/orders/{order.pk}/left-markings/{event.pk}/correct/?{self.q}',
                                     {'reason': CORRECT_REASON, 'never_left': True}, format='json').status_code, 403)
        self.assertTrue(cancelling.ever_left(order))


class OrderFourTest(_Flow):
    """The production history of order #4, rebuilt with synthetic data."""

    def setUp(self):
        super().setUp()
        self.order = self.pickup()
        platform = self.manager
        self.sent = legacy_move(self.order, FS.SHIPPED, actor=platform)
        legacy_move(self.order, FS.CANCELLED, actor=platform)
        legacy_move(self.order, FS.PENDING, actor=self.superior)
        legacy_move(self.order, FS.READY_FOR_PICKUP, actor=self.superior)
        legacy_move(self.order, FS.CANCELLED, actor=self.superior)
        self.snapshot = (StockMovement.objects.count(), PaymentTransaction.objects.count(),
                         OrderRefund.objects.count(), SalesCommission.objects.count())

    def test_the_old_sent_entry_blocks_the_cancellation(self):
        self.assertTrue(cancelling.ever_left(self.order))
        self.refused(self.post_cancel(self.order), 'goods_left')

    def test_only_a_superior_corrects_it(self):
        self.refused(self.correct(self.order, self.sent.pk, client=self.desk), 'superior_required', 403)
        markings = self.detail(self.order)['dispatch']['left_markings']
        self.assertEqual([(m['audit_id'], m['can_correct']) for m in markings], [(self.sent.pk, False)])
        mine = self.detail(self.order, client=self.boss)['dispatch']['left_markings']
        self.assertEqual([(m['audit_id'], m['can_correct']) for m in mine], [(self.sent.pk, True)])

    def test_the_correction_asks_for_the_declaration_and_a_reason(self):
        self.refused(self.correct(self.order, self.sent.pk, never_left=False), 'declaration_required', 400)
        self.refused(self.correct(self.order, self.sent.pk, never_left='true'), 'declaration_required', 400)
        self.refused(self.correct(self.order, self.sent.pk, reason='ok'), 'reason_required', 400)
        self.refused(self.correct(self.order, self.sent.pk, reason='motivo\x00oculto'), 'reason_invalid', 400)
        self.assertTrue(cancelling.ever_left(self.order))

    def test_corrected_it_unlocks_only_that_and_rewrites_nothing(self):
        before = AdminAuditLog.objects.filter(target_type='order', target_id=str(self.order.pk))
        kept = list(before.order_by('pk').values_list('pk', 'action', 'metadata', 'created_at'))
        response = self.correct(self.order, self.sent.pk)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(response.json()['correction_created'])
        after = AdminAuditLog.objects.filter(target_type='order', target_id=str(self.order.pk)).order_by('pk')
        self.assertEqual(list(after.values_list('pk', 'action', 'metadata', 'created_at'))[:len(kept)], kept)
        correction = after.last()
        self.assertEqual(correction.action, 'order_left_marking_corrected')
        self.assertEqual(correction.actor, self.superior)
        self.assertEqual(correction.metadata['corrected_audit_id'], self.sent.pk)
        self.assertEqual(correction.metadata['reason'], CORRECT_REASON)
        self.assertEqual(correction.metadata['status_restored_to'], '')
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.fulfillment_status), (Order.Status.PAID, FS.CANCELLED))
        self.assertFalse(cancelling.ever_left(self.order))
        self.assertEqual(self.snapshot, (StockMovement.objects.count(), PaymentTransaction.objects.count(),
                                         OrderRefund.objects.count(), SalesCommission.objects.count()))
        history = self.detail(self.order)['dispatch']['history']
        self.assertTrue([h for h in history if h['id'] == self.sent.pk][0]['corrected'])

    def test_twice_is_once(self):
        self.assertEqual(self.correct(self.order, self.sent.pk).status_code, 201)
        again = self.correct(self.order, self.sent.pk)
        self.assertEqual(again.status_code, 200)
        self.assertFalse(again.json()['correction_created'])
        self.assertEqual(AdminAuditLog.objects.filter(action='order_left_marking_corrected').count(), 1)

    def test_then_the_cancellation_follows_its_own_rules_once(self):
        self.correct(self.order, self.sent.pk)
        stock_before = self.cable_stock()
        response = self.post_cancel(self.order)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.cable_stock(), stock_before + 3)
        self.assertEqual(self.returns(self.order).count(), 1)
        self.assertEqual(OrderRefund.objects.filter(order=self.order).count(), 0)   # no refund unless asked
        self.assertEqual(self.post_cancel(self.order).status_code, 409)
        self.assertEqual(self.returns(self.order).count(), 1)
        self.order.refresh_from_db()
        self.assertTrue(self.order.paid)

    def test_a_correction_of_a_cancelled_order_is_refused(self):
        self.correct(self.order, self.sent.pk)
        self.post_cancel(self.order)
        other = legacy_move(self.order, FS.SHIPPED, actor=self.manager)
        response = self.correct(self.order, other.pk)
        self.refused(response, 'order_cancelled')
        self.assertIn('ya está cancelado', response.json()['detail'])

    def test_an_entry_that_is_not_a_leaving_one_of_this_order_is_not_found(self):
        stranger_order = self.pickup()
        foreign = legacy_move(stranger_order, FS.SHIPPED, actor=self.manager)
        not_leaving = AdminAuditLog.objects.filter(
            target_id=str(self.order.pk), metadata__new_fulfillment_status=FS.PENDING).first()
        for audit_id in (foreign.pk, not_leaving.pk, 999999):
            with self.subTest(audit_id):
                self.refused(self.correct(self.order, audit_id), 'event_not_found', 404)

    def test_a_correction_naming_another_orders_entry_unlocks_nothing(self):
        stranger_order = self.pickup()
        foreign = legacy_move(stranger_order, FS.SHIPPED, actor=self.manager)
        AdminAuditLog.log(actor=self.superior, action='order_left_marking_corrected', target_type='order',
                          target_id=self.order.pk, company=self.company,
                          metadata={'corrected_audit_id': foreign.pk})
        self.assertTrue(cancelling.ever_left(self.order))


class WhatElseSaysItLeftTest(_Flow):
    def test_a_device_stamped_gone_refuses_the_correction(self):
        self.receive(self.row('F2LXKFSONE1', IMEI_A))
        order = self.web_order((self.phone, 1), delivery_method=Order.DeliveryMethod.PICKUP_STORE)
        picking.identify_for_order(order=order, code=IMEI_A, method='scanner', actor=self.manager)
        event = legacy_move(order, FS.SHIPPED, actor=self.manager)     # stamps the device as gone
        self.assertTrue(OrderUnit.objects.filter(order=order, left_at__isnull=False).exists())
        response = self.correct(order, event.pk)
        self.refused(response, 'contradicting_evidence')
        self.assertIn('equipo', response.json()['detail'])
        self.assertTrue(cancelling.ever_left(order))

    def test_a_shipping_date_or_a_waybill_refuses_it(self):
        for field, value in (('shipped_at', None), ('waybill_number', 'G-77'), ('tracking_code', 'T-9')):
            with self.subTest(field):
                order = self.shipping(mode=OrderDelivery.Mode.NATIONAL_ADDRESS)
                event = legacy_move(order, FS.SHIPPED, actor=self.manager)
                update = {field: value if value is not None else event.created_at}
                OrderDelivery.objects.filter(order=order).update(**update)
                response = self.correct(order, event.pk)
                self.refused(response, 'contradicting_evidence')
                self.assertIn('devolución', response.json()['detail'])
                self.assertTrue(cancelling.ever_left(order))

    def test_two_leaving_entries_are_corrected_newest_first_and_each_counts_alone(self):
        order = self.shipping(fulfillment_status='preparing')
        sent = legacy_move(order, FS.SHIPPED, actor=self.manager)
        delivered = legacy_move(order, FS.DELIVERED, actor=self.manager)
        response = self.correct(order, sent.pk)
        self.refused(response, 'contradicting_evidence')
        self.assertIn('más reciente', response.json()['detail'])
        self.assertEqual(self.correct(order, delivered.pk).status_code, 201)
        self.assertEqual(self.status_of(order), FS.DELIVERED)       # sent still says it left
        self.assertTrue(cancelling.ever_left(order))
        self.assertEqual(self.correct(order, sent.pk).status_code, 201)
        self.assertEqual(self.status_of(order), FS.PREPARING)       # back where the mistake started
        self.assertFalse(cancelling.ever_left(order))
        self.assertEqual(AdminAuditLog.objects.filter(
            action='order_left_marking_corrected', target_id=str(order.pk)).count(), 2)

    def test_an_accidental_sent_through_todays_path_goes_back_to_preparing(self):
        order = self.shipping(fulfillment_status='preparing')
        self.assertEqual(self.move(order, FS.SHIPPED).status_code, 200)
        event = cancelling.leaving_events(order).get()
        response = self.correct(order, event.pk)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(self.status_of(order), FS.PREPARING)
        self.assertEqual(response.json()['dispatch']['history'][-1]['new_status'], FS.PREPARING)
        self.assertEqual(self.post_cancel(order).status_code, 200)

    def test_a_status_moved_on_after_the_mistake_stays_and_is_not_rewritten(self):
        order = self.shipping(fulfillment_status='preparing')
        sent = legacy_move(order, FS.SHIPPED, actor=self.manager)
        legacy_move(order, FS.PREPARING, actor=self.manager)
        self.assertEqual(self.correct(order, sent.pk).status_code, 201)
        self.assertEqual(self.status_of(order), FS.PREPARING)
        self.assertFalse(cancelling.ever_left(order))


class ConcurrentCorrectionTest(TransactionTestCase):
    """Two superiors correcting the same entry at once: one correction, the other is told «already»."""

    def setUp(self):
        from store.models import Branch, Company, OrderItem, Product

        self.company = Company.objects.create(
            name='Despacho concurrente', legal_name='Despacho Concurrente SAC', tax_id='20911111129',
            slug='despacho-conc')
        branch = self.company.branches.first() or Branch.objects.create(company=self.company, name='Centro')
        self.superior, _ = _p2d_member(
            self.company, 'despacho_conc', ['company.view', 'company.manage', 'sales.orders.view', 'sales.orders.manage'])
        cable = Product.objects.create(company=self.company, name='Cable', slug='cable-desp-conc', price=Decimal('10.00'))
        self.order = Order.objects.create(
            company=self.company, fulfillment_branch=branch, customer_email='x@example.com',
            total=Decimal('10.00'), status=Order.Status.PAID, paid=True, paid_at=timezone.now(),
            delivery_method=Order.DeliveryMethod.PICKUP_STORE)
        OrderItem.objects.create(order=self.order, product=cable, quantity=1, price=Decimal('10.00'))
        self.event = legacy_move(self.order, FS.SHIPPED, actor=self.superior)

    def test_one_correction_is_written(self):
        barrier, results = Barrier(2), []

        def run():
            try:
                barrier.wait()
                _, created = left_marking.correct(
                    order=self.order, audit_id=self.event.pk, actor=self.superior, company=self.company,
                    reason=CORRECT_REASON, never_left=True)
                results.append(created)
            finally:
                connection.close()

        threads = [Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(AdminAuditLog.objects.filter(action='order_left_marking_corrected').count(), 1)
        self.assertFalse(cancelling.ever_left(self.order))
