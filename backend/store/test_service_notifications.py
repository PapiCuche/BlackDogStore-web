"""
NOTIFY-REAL-PATHS — los avisos salen por donde de verdad pasa el trabajo.

El aviso de «tu equipo está listo» estaba escrito y probado… llamando a la
función que lo emite. Los caminos reales —pasar el control de calidad, pausar
por repuesto, entregar— mueven la orden con `_apply_transition`, que no
avisaba a nadie. El cliente no se enteraba de nada de lo que estas pruebas
recorren; ahora se recorre el dominio, no el emisor.
"""
from unittest import mock

from store import notification_events as ev
from store.models import Notification, NotificationEvent
from store.tests import M12DeliveryBase as _Base, _m8_service


class RealPathNotificationTest(_Base):
    def notes(self, event_type, *, audience=Notification.Audience.CUSTOMER):
        return Notification.objects.filter(
            event__event_type=event_type, audience=audience,
            target_type='repair_order', target_id=self.order.pk,
        )

    def test_receiving_a_device_tells_its_customer(self):
        order = self.make_order()

        note = Notification.objects.get(
            event__event_type=ev.SERVICE_ORDER_CREATED, target_id=order.pk)

        self.assertEqual(note.customer, order.customer)
        self.assertEqual(note.audience, Notification.Audience.CUSTOMER)
        self.assertIn(order.number, note.body)
        self.assertNotIn('Rayones', f'{note.title} {note.body}')

    def test_starting_the_repair_tells_the_customer_once(self):
        self.started()

        self.assertEqual(self.notes(ev.SERVICE_STATUS_CHANGED).count(), 1)
        self.assertIn('reparación', self.notes(ev.SERVICE_STATUS_CHANGED).get().title.lower())

    def test_pausing_for_a_part_tells_the_customer_and_resuming_does_not_repeat_anything(self):
        self.started()
        before = self.notes(ev.SERVICE_STATUS_CHANGED).count()

        _m8_service.pause_for_parts(repair_order=self.order, actor=self.staff)
        _m8_service.resume_repair(repair_order=self.order, actor=self.staff)
        _m8_service.pause_for_parts(repair_order=self.order, actor=self.staff)

        titles = [n.title for n in self.notes(ev.SERVICE_STATUS_CHANGED)]
        self.assertEqual(len(titles), before + 1)
        self.assertTrue(any('repuesto' in title for title in titles))

    def test_passing_quality_control_tells_the_customer_and_whoever_hands_it_over(self):
        self.ready_for_pickup()

        self.assertEqual(self.order.status, 'ready_for_pickup')
        self.assertEqual(self.notes(ev.SERVICE_READY_FOR_PICKUP).count(), 1)
        self.assertTrue(self.notes(
            ev.SERVICE_READY_FOR_PICKUP, audience=Notification.Audience.INTERNAL).exists())
        self.assertEqual(
            NotificationEvent.objects.filter(event_type=ev.SERVICE_READY_FOR_PICKUP).count(), 1)

    def test_handing_the_device_over_tells_the_customer(self):
        self.ready_for_pickup()

        _m8_service.deliver_repair(
            repair_order=self.order, recipient_name='Ana Cliente', actor=self.staff)

        self.assertEqual(self.notes(ev.SERVICE_DELIVERED).count(), 1)

    def test_a_notice_that_cannot_be_written_never_undoes_the_work(self):
        self.started()

        with mock.patch('store.notification_services.emit', side_effect=RuntimeError('caído')):
            _m8_service.pause_for_parts(repair_order=self.order, actor=self.staff)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'waiting_parts')
