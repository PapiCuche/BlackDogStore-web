"""
WHATSAPP-NOTIFY — avisos por WhatsApp, por la API oficial y sin credenciales aquí.

    WHATSAPP AVISA; EL PORTAL CONSERVA EL SEGUIMIENTO COMPLETO.

Un mensaje es un resumen y un enlace seguro. No lleva IMEI, ni serie, ni notas,
ni importes: lleva el nombre, el número de orden y el enlace.

Lo que estas pruebas fijan:

  * sólo se escribe a quien dijo que sí, y dejar de querer gana;
  * un aviso es UN mensaje, aunque se reintente o se repita el evento;
  * un fallo del proveedor queda anotado y NO deshace el trabajo;
  * el webhook sólo cree lo que viene firmado, y sólo toca lo de su empresa;
  * ninguna credencial vive en la base de datos ni sale por la API.

No hay llamadas reales: el proveedor es `FakeProvider`, y el cliente de la
Cloud API se prueba contra un `urlopen` simulado.
"""
import hashlib
import hmac
import io
import json
import os
import urllib.error
from datetime import timedelta
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from store import notification_events as ev
from store import tracking_services as tracking
from store import whatsapp_services as wa
from store.messaging import phones
from store.messaging.base import ProviderError
from store.messaging.fake import FakeProvider
from store.messaging.whatsapp_cloud import CloudApiProvider
from store.models import (
    AdminAuditLog, CompanyMessagingSettings, Customer, Device, Notification,
    NotificationDelivery,
)
from store.tests import M10ServiceBase as _Base, _m8_service, _m8_url

IMEI_A = '490154203237518'
ENV = {
    'WHATSAPP_TOKEN_TALLER': 'token-secreto-de-prueba',
    'WHATSAPP_SECRET_TALLER': 'app-secret-de-prueba',
    'WHATSAPP_VERIFY_TALLER': 'verify-de-prueba',
}
TEMPLATES = {
    ev.SERVICE_ORDER_CREATED: 'orden_recibida',
    ev.SERVICE_QUOTE_AVAILABLE: 'cotizacion_lista',
    ev.SERVICE_STATUS_CHANGED: 'orden_actualizada',
    ev.SERVICE_READY_FOR_PICKUP: 'equipo_listo',
    ev.SERVICE_DELIVERED: 'equipo_entregado',
}


class PhoneTest(SimpleTestCase):
    def test_a_number_becomes_what_the_api_expects_or_nothing(self):
        cases = {
            ('+51 987 654 321', ''): '51987654321',
            ('987654321', '51'): '51987654321',
            ('987-654-321', '51'): '51987654321',
            ('0051987654321', ''): '51987654321',
            ('51987654321', ''): '51987654321',
            # Sin código de país y sin uno por defecto: no se adivina.
            ('987654321', ''): None,
            ('12345', '51'): None,
            ('', '51'): None,
            ('no tiene', '51'): None,
        }
        for (raw, code), expected in cases.items():
            with self.subTest(raw=raw, code=code):
                self.assertEqual(phones.to_wa_id(raw, code), expected)

    def test_a_number_is_shown_by_its_last_digits_only(self):
        self.assertEqual(phones.mask('51987654321'), '•••• 4321')
        self.assertEqual(phones.mask(''), '')


@override_settings(WHATSAPP_PROVIDER='fake', FRONTEND_URL='https://tienda.example')
class WhatsAppBase(_Base):
    def setUp(self):
        super().setUp()
        FakeProvider.reset()
        patcher = mock.patch.dict(os.environ, ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        Device.objects.filter(pk=self.device.pk).update(imei=IMEI_A, serial_number='F2LXK1ABC9')
        Customer.objects.filter(pk=self.customer.pk).update(
            phone='987 654 321', whatsapp_opt_in_at=timezone.now(), whatsapp_opt_in_source='counter')
        self.customer.refresh_from_db()
        self.config = CompanyMessagingSettings.objects.create(
            company=self.company, whatsapp_enabled=True, whatsapp_phone_number_id='1055500000',
            whatsapp_access_token_env='WHATSAPP_TOKEN_TALLER',
            whatsapp_app_secret_env='WHATSAPP_SECRET_TALLER',
            whatsapp_verify_token_env='WHATSAPP_VERIFY_TALLER',
            default_calling_code='51', whatsapp_templates=dict(TEMPLATES),
        )

    def new_order(self):
        """Recibe un equipo, y deja correr lo que ocurre al confirmar la transacción."""
        with self.captureOnCommitCallbacks(execute=True):
            order = self.make_order()
        return order

    def delivery(self, order=None, event_type=ev.SERVICE_ORDER_CREATED):
        return NotificationDelivery.objects.filter(
            channel='whatsapp', notification__target_id=(order or self.order).pk,
            notification__event__event_type=event_type,
        ).first()


class SendingTest(WhatsAppBase):
    def test_receiving_a_device_sends_one_message_with_a_summary_and_the_link(self):
        order = self.new_order()

        [message] = FakeProvider.sent
        self.assertEqual(message['to'], '51987654321')
        self.assertEqual(message['template'], 'orden_recibida')
        self.assertEqual(message['language'], 'es')
        self.assertEqual(message['phone_number_id'], '1055500000')
        self.assertEqual(message['parameters'], [
            self.customer.first_name, order.number,
            f'https://tienda.example/seguimiento/{tracking.token_for(order)}',
        ])
        row = self.delivery(order)
        self.assertEqual(row.status, 'sent')
        self.assertTrue(row.provider_message_id)
        self.assertEqual(row.recipient_masked, '•••• 4321')
        self.assertEqual(row.attempt_count, 1)
        self.assertIsNotNone(row.sent_at)

    def test_the_message_never_carries_identifiers_notes_or_money(self):
        self.order.internal_notes = 'CLIENTE CONFLICTIVO'
        self.order.save(update_fields=['internal_notes'])
        with self.captureOnCommitCallbacks(execute=True):
            self.approved_order()
            _m8_service.start_repair(repair_order=self.order, actor=self.staff)

        self.assertGreaterEqual(len(FakeProvider.sent), 2)
        blob = json.dumps(FakeProvider.sent)
        for leaked in (IMEI_A, 'F2LXK1ABC9', 'CONFLICTIVO', '185.00', 'Batería', ENV['WHATSAPP_TOKEN_TALLER']):
            self.assertNotIn(leaked, blob, leaked)

    def test_a_company_that_did_not_turn_it_on_sends_nothing_and_records_nothing(self):
        self.config.whatsapp_enabled = False
        self.config.save(update_fields=['whatsapp_enabled'])

        order = self.new_order()

        self.assertEqual(FakeProvider.sent, [])
        self.assertIsNone(self.delivery(order))

    def test_nobody_is_written_to_without_having_said_yes(self):
        Customer.objects.filter(pk=self.customer.pk).update(whatsapp_opt_in_at=None)

        order = self.new_order()

        self.assertEqual(FakeProvider.sent, [])
        row = self.delivery(order)
        self.assertEqual(row.status, 'skipped')
        self.assertIn('consentimiento', row.failure_reason)

    def test_saying_no_afterwards_wins(self):
        Customer.objects.filter(pk=self.customer.pk).update(whatsapp_opt_out_at=timezone.now())
        order = self.new_order()
        self.assertEqual(FakeProvider.sent, [])
        self.assertEqual(self.delivery(order).status, 'skipped')

    def test_a_number_that_cannot_be_dialled_is_skipped_not_guessed(self):
        Customer.objects.filter(pk=self.customer.pk).update(phone='12345')
        order = self.new_order()
        self.assertEqual(FakeProvider.sent, [])
        row = self.delivery(order)
        self.assertEqual(row.status, 'skipped')
        self.assertIn('teléfono', row.failure_reason)

    def test_an_event_without_a_template_is_not_sent(self):
        self.config.whatsapp_templates = {}
        self.config.save(update_fields=['whatsapp_templates'])
        order = self.new_order()
        self.assertEqual(FakeProvider.sent, [])
        self.assertIn('plantilla', self.delivery(order).failure_reason)

    def test_one_notice_is_one_message_however_often_it_is_attempted(self):
        order = self.new_order()
        row = self.delivery(order)

        wa.deliver(row.pk)
        wa.deliver(row.pk)
        call_command('send_pending_notifications')

        self.assertEqual(len(FakeProvider.sent), 1)

    def test_internal_notices_never_go_out_by_whatsapp(self):
        with self.captureOnCommitCallbacks(execute=True):
            _m8_service.assign_technician(repair_order=self.order, technician=self.staff)
        self.assertFalse(NotificationDelivery.objects.filter(
            channel='whatsapp', notification__audience=Notification.Audience.INTERNAL).exists())

    def test_the_customer_of_another_company_is_never_reached_through_this_one(self):
        self.new_order()
        self.assertEqual({m['phone_number_id'] for m in FakeProvider.sent}, {'1055500000'})
        self.assertFalse(NotificationDelivery.objects.filter(
            channel='whatsapp', notification__company=self.other).exists())


class FailureTest(WhatsAppBase):
    def test_a_provider_failure_is_recorded_and_the_work_stands(self):
        FakeProvider.fail_next(ProviderError('Límite alcanzado', retryable=True, code='130429'))

        order = self.new_order()

        order.refresh_from_db()
        self.assertEqual(order.status, 'received')
        row = self.delivery(order)
        self.assertEqual(row.status, 'failed')
        self.assertIn('130429', row.failure_reason)
        self.assertIsNotNone(row.next_attempt_at)
        self.assertEqual(row.attempt_count, 1)

    def test_the_retry_pass_sends_what_failed_once_its_time_comes(self):
        FakeProvider.fail_next(ProviderError('Caído', retryable=True))
        order = self.new_order()
        row = self.delivery(order)

        call_command('send_pending_notifications')          # todavía no le toca
        self.assertEqual(FakeProvider.sent, [])

        NotificationDelivery.objects.filter(pk=row.pk).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1))
        call_command('send_pending_notifications')

        row.refresh_from_db()
        self.assertEqual(row.status, 'sent')
        self.assertEqual(row.attempt_count, 2)
        self.assertEqual(len(FakeProvider.sent), 1)

    def test_a_final_refusal_is_not_retried_by_itself(self):
        FakeProvider.fail_next(ProviderError('Plantilla inexistente', retryable=False, code='132001'))
        order = self.new_order()

        row = self.delivery(order)
        self.assertEqual(row.status, 'failed')
        self.assertIsNone(row.next_attempt_at)
        call_command('send_pending_notifications')
        self.assertEqual(FakeProvider.sent, [])

    def test_attempts_run_out(self):
        order = self.new_order()
        row = self.delivery(order)
        NotificationDelivery.objects.filter(pk=row.pk).update(
            status='failed', attempt_count=wa.MAX_ATTEMPTS - 1,
            next_attempt_at=timezone.now() - timedelta(seconds=1), provider_message_id='')
        FakeProvider.reset()
        FakeProvider.fail_next(ProviderError('Caído', retryable=True))

        call_command('send_pending_notifications')

        row.refresh_from_db()
        self.assertEqual(row.attempt_count, wa.MAX_ATTEMPTS)
        self.assertIsNone(row.next_attempt_at)

    def test_missing_credentials_fail_the_message_without_breaking_anything(self):
        with mock.patch.dict(os.environ, {'WHATSAPP_TOKEN_TALLER': ''}):
            order = self.new_order()

        row = self.delivery(order)
        self.assertEqual(row.status, 'failed')
        self.assertIn('credenciales', row.failure_reason)
        self.assertEqual(FakeProvider.sent, [])

    def test_a_message_left_pending_by_a_crash_is_picked_up_later(self):
        with mock.patch('store.whatsapp_services.deliver', side_effect=RuntimeError('proceso muerto')):
            order = self.new_order()
        row = self.delivery(order)
        self.assertEqual(row.status, 'pending')

        NotificationDelivery.objects.filter(pk=row.pk).update(
            created_at=timezone.now() - timedelta(minutes=5))
        call_command('send_pending_notifications')

        row.refresh_from_db()
        self.assertEqual(row.status, 'sent')

    def test_staff_can_retry_a_failed_message_from_the_order(self):
        FakeProvider.fail_next(ProviderError('Plantilla inexistente', retryable=False))
        order = self.new_order()
        row = self.delivery(order)
        client = self.with_capabilities('service.orders.view', 'service.orders.manage', slug='reintenta')
        url = _m8_url('m8-taller', f'orders/{order.pk}/notifications/{row.notification_id}/whatsapp/retry/')

        res = client.post(url)

        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['whatsapp_status'], 'sent')
        self.assertEqual(len(FakeProvider.sent), 1)
        self.assertTrue(AdminAuditLog.objects.filter(action='whatsapp_delivery_retried').exists())

        viewer = self.only_capabilities('service.orders.view', slug='solo-ve-wa')
        self.assertEqual(viewer.post(url).status_code, 403)
        self.assertIn(self.client.post(_m8_url(
            'm8-otra', f'orders/{order.pk}/notifications/{row.notification_id}/whatsapp/retry/',
        )).status_code, (403, 404))

    def test_the_order_shows_what_happened_to_each_message(self):
        from store.v1_service_serializers import V1ServiceOrderDetailSerializer

        order = self.new_order()
        [notice] = V1ServiceOrderDetailSerializer(order).data['customer_notifications']

        self.assertEqual(notice['whatsapp_status'], 'sent')
        self.assertEqual(notice['whatsapp_recipient'], '•••• 4321')
        self.assertNotIn('987654321', json.dumps(notice, default=str))


class WebhookTest(WhatsAppBase):
    URL = '/api/v1/webhooks/whatsapp/m8-taller/'

    def setUp(self):
        super().setUp()
        self.order2 = self.new_order()
        self.row = self.delivery(self.order2)
        self.anon = APIClient()

    def signed(self, payload, secret=ENV['WHATSAPP_SECRET_TALLER'], url=None):
        body = json.dumps(payload).encode()
        signature = 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return self.anon.post(url or self.URL, body, content_type='application/json',
                              HTTP_X_HUB_SIGNATURE_256=signature)

    def statuses(self, *rows):
        return {'object': 'whatsapp_business_account', 'entry': [{'id': '1', 'changes': [
            {'field': 'messages', 'value': {'statuses': list(rows)}}]}]}

    def status(self, state, message_id=None, **extra):
        return {'id': message_id or self.row.provider_message_id, 'status': state,
                'timestamp': '1790000000', **extra}

    def test_the_subscription_handshake_needs_the_shared_token(self):
        ok = self.anon.get(self.URL, {
            'hub.mode': 'subscribe', 'hub.verify_token': 'verify-de-prueba', 'hub.challenge': '8675309'})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.content, b'8675309')

        for token in ('otro', ''):
            bad = self.anon.get(self.URL, {
                'hub.mode': 'subscribe', 'hub.verify_token': token, 'hub.challenge': '1'})
            self.assertEqual(bad.status_code, 403)

    def test_odd_bytes_in_the_token_or_the_signature_are_refused_like_anything_else(self):
        """Un carácter no ASCII hacía fallar la comparación con un 500: un oráculo."""
        res = self.anon.get(self.URL, {
            'hub.mode': 'subscribe', 'hub.verify_token': 'ñandú', 'hub.challenge': '1'})
        self.assertEqual(res.status_code, 403)

        body = json.dumps(self.statuses(self.status('delivered')))
        res = self.anon.post(self.URL, body, content_type='application/json',
                             HTTP_X_HUB_SIGNATURE_256='sha256=ñandú')
        self.assertEqual(res.status_code, 403)

    def test_an_unsigned_or_wrongly_signed_report_changes_nothing(self):
        payload = self.statuses(self.status('delivered'))

        self.assertEqual(self.signed(payload, secret='otro-secreto').status_code, 403)
        self.assertEqual(
            self.anon.post(self.URL, json.dumps(payload), content_type='application/json').status_code, 403)

        self.row.refresh_from_db()
        self.assertEqual(self.row.status, 'sent')

    def test_delivered_and_read_move_the_message_forward(self):
        self.assertEqual(self.signed(self.statuses(self.status('delivered'))).status_code, 200)
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, 'delivered')
        self.assertIsNotNone(self.row.delivered_at)

        self.signed(self.statuses(self.status('read')))
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, 'read')
        self.assertIsNotNone(self.row.read_at)

    def test_a_late_report_never_moves_it_backwards(self):
        self.signed(self.statuses(self.status('read')))
        self.signed(self.statuses(self.status('delivered'), self.status('sent')))

        self.row.refresh_from_db()
        self.assertEqual(self.row.status, 'read')
        self.assertIsNotNone(self.row.delivered_at)

    def test_a_failure_reported_later_is_recorded_with_its_reason(self):
        self.signed(self.statuses(self.status(
            'failed', errors=[{'code': 131026, 'title': 'Message undeliverable'}])))

        self.row.refresh_from_db()
        self.assertEqual(self.row.status, 'failed')
        self.assertIn('131026', self.row.failure_reason)
        self.assertIsNone(self.row.next_attempt_at)

    def test_an_unknown_message_is_acknowledged_and_ignored(self):
        res = self.signed(self.statuses(self.status('delivered', message_id='wamid.DESCONOCIDO')))
        self.assertEqual(res.status_code, 200)

    def test_one_company_cannot_report_on_the_messages_of_another(self):
        CompanyMessagingSettings.objects.create(
            company=self.other, whatsapp_enabled=True, whatsapp_phone_number_id='2',
            whatsapp_access_token_env='WHATSAPP_TOKEN_TALLER',
            whatsapp_app_secret_env='WHATSAPP_SECRET_TALLER',
            whatsapp_verify_token_env='WHATSAPP_VERIFY_TALLER',
        )
        res = self.signed(self.statuses(self.status('read')), url='/api/v1/webhooks/whatsapp/m8-otra/')

        self.assertEqual(res.status_code, 200)
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, 'sent')

    def test_a_company_without_whatsapp_has_no_webhook(self):
        self.assertEqual(self.anon.get('/api/v1/webhooks/whatsapp/no-existe/', {
            'hub.mode': 'subscribe', 'hub.verify_token': 'x', 'hub.challenge': '1'}).status_code, 403)
        self.assertEqual(self.signed(
            self.statuses(), url='/api/v1/webhooks/whatsapp/no-existe/').status_code, 403)

    def test_a_customer_who_writes_stop_is_not_messaged_again(self):
        payload = {'object': 'whatsapp_business_account', 'entry': [{'id': '1', 'changes': [
            {'field': 'messages', 'value': {'messages': [
                {'from': '51987654321', 'type': 'text', 'text': {'body': ' Baja '}}]}}]}]}

        self.assertEqual(self.signed(payload).status_code, 200)

        self.customer.refresh_from_db()
        self.assertIsNotNone(self.customer.whatsapp_opt_out_at)
        FakeProvider.reset()
        self.new_order()
        self.assertEqual(FakeProvider.sent, [])


class SettingsApiTest(WhatsAppBase):
    def url(self, slug='m8-taller'):
        return f'/api/v1/internal/{slug}/messaging/whatsapp/'

    def admin(self):
        return self.with_capabilities('settings.view', 'settings.manage', slug='ajustes-wa')

    def test_the_configuration_says_what_is_ready_and_never_shows_a_credential(self):
        res = self.admin().get(self.url())

        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertTrue(body['enabled'])
        self.assertTrue(body['ready'])
        self.assertEqual(body['credentials'], {'access_token': True, 'app_secret': True, 'verify_token': True})
        self.assertEqual(body['templates'][ev.SERVICE_READY_FOR_PICKUP], 'equipo_listo')
        self.assertEqual(body['webhook_path'], '/api/v1/webhooks/whatsapp/m8-taller/')
        text = res.content.decode()
        for secret in list(ENV.values()) + list(ENV.keys()):
            self.assertNotIn(secret, text, secret)

    def test_a_missing_credential_is_named_as_missing_and_blocks_turning_it_on(self):
        self.config.whatsapp_enabled = False
        self.config.save(update_fields=['whatsapp_enabled'])
        with mock.patch.dict(os.environ, {'WHATSAPP_SECRET_TALLER': ''}):
            client = self.admin()
            body = client.get(self.url()).json()
            self.assertFalse(body['ready'])
            self.assertIn('app_secret', body['missing'])

            res = client.patch(self.url(), {'enabled': True}, format='json')
            self.assertEqual(res.status_code, 400)
        self.config.refresh_from_db()
        self.assertFalse(self.config.whatsapp_enabled)

    def test_an_administrator_chooses_templates_and_the_calling_code(self):
        res = self.admin().patch(self.url(), {
            'templates': {ev.SERVICE_READY_FOR_PICKUP: 'listo_v2'}, 'default_calling_code': '+52',
            'template_language': 'es_MX',
        }, format='json')

        self.assertEqual(res.status_code, 200, res.content)
        self.config.refresh_from_db()
        self.assertEqual(self.config.whatsapp_templates[ev.SERVICE_READY_FOR_PICKUP], 'listo_v2')
        self.assertEqual(self.config.whatsapp_templates[ev.SERVICE_DELIVERED], 'equipo_entregado')
        self.assertEqual(self.config.default_calling_code, '52')
        self.assertEqual(self.config.template_language, 'es_MX')
        row = AdminAuditLog.objects.filter(action='whatsapp_settings_updated').get()
        self.assertEqual(row.company, self.company)

    def test_the_api_cannot_point_the_company_at_a_credential(self):
        res = self.admin().patch(self.url(), {
            'whatsapp_access_token_env': 'SECRET_KEY', 'access_token_env': 'SECRET_KEY',
            'phone_number_id': '999', 'access_token': 'robado',
        }, format='json')

        self.assertIn(res.status_code, (200, 400))
        self.config.refresh_from_db()
        self.assertEqual(self.config.whatsapp_access_token_env, 'WHATSAPP_TOKEN_TALLER')
        self.assertEqual(self.config.whatsapp_phone_number_id, '1055500000')

    def test_unknown_events_and_odd_template_names_are_refused(self):
        client = self.admin()
        for templates in ({'no.existe': 'x'}, {ev.SERVICE_DELIVERED: 'Con Espacios!'},
                          {ev.COMMERCE_PAYMENT_CONFIRMED: 'pago'}):
            with self.subTest(templates):
                self.assertEqual(
                    client.patch(self.url(), {'templates': templates}, format='json').status_code, 400)

    def test_reading_and_changing_need_their_capabilities_and_the_right_company(self):
        viewer = self.only_capabilities('settings.view', slug='ve-ajustes-wa')
        self.assertEqual(viewer.get(self.url()).status_code, 200)
        self.assertEqual(viewer.patch(self.url(), {'enabled': False}, format='json').status_code, 403)

        nobody = self.only_capabilities('service.orders.view', slug='sin-ajustes-wa')
        self.assertEqual(nobody.get(self.url()).status_code, 403)
        self.assertIn(nobody.get(self.url('m8-otra')).status_code, (403, 404))

    def test_only_the_operator_sets_credential_references_and_only_whatsapp_ones(self):
        call_command('configure_whatsapp', 'm8-otra', phone_number_id='777',
                     access_token_env='WHATSAPP_TOKEN_TALLER', app_secret_env='WHATSAPP_SECRET_TALLER',
                     verify_token_env='WHATSAPP_VERIFY_TALLER')
        other = CompanyMessagingSettings.objects.get(company=self.other)
        self.assertEqual(other.whatsapp_phone_number_id, '777')
        self.assertFalse(other.whatsapp_enabled)

        from django.core.management.base import CommandError
        for bad in ('SECRET_KEY', 'DATABASE_URL', 'whatsapp_token', 'WHATSAPP_'):
            with self.subTest(bad), self.assertRaises(CommandError):
                call_command('configure_whatsapp', 'm8-otra', access_token_env=bad)


class ConsentApiTest(WhatsAppBase):
    def url(self):
        return _m8_url('m8-taller', f'customers/{self.customer.pk}/whatsapp-consent/')

    def test_staff_record_that_the_customer_agreed_and_that_they_no_longer_do(self):
        Customer.objects.filter(pk=self.customer.pk).update(whatsapp_opt_in_at=None)
        client = self.with_capabilities('service.customers.view', 'service.customers.manage', slug='consiente')

        res = client.post(self.url(), {'opt_in': True}, format='json')

        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.json()['whatsapp_opt_in'])
        self.customer.refresh_from_db()
        self.assertIsNotNone(self.customer.whatsapp_opt_in_at)
        self.assertEqual(self.customer.whatsapp_opt_in_source, 'counter')
        self.assertTrue(AdminAuditLog.objects.filter(action='customer_whatsapp_consent_changed').exists())

        res = client.post(self.url(), {'opt_in': False}, format='json')
        self.assertFalse(res.json()['whatsapp_opt_in'])
        self.customer.refresh_from_db()
        self.assertIsNotNone(self.customer.whatsapp_opt_out_at)

    def test_the_order_says_whether_its_customer_agreed_and_whether_the_channel_is_on(self):
        from store.v1_service_serializers import V1ServiceOrderDetailSerializer

        data = V1ServiceOrderDetailSerializer(self.order).data
        self.assertEqual(data['whatsapp'], {'enabled': True, 'customer_opt_in': True})

        Customer.objects.filter(pk=self.customer.pk).update(whatsapp_opt_out_at=timezone.now())
        self.config.whatsapp_enabled = False
        self.config.save(update_fields=['whatsapp_enabled'])
        self.order.refresh_from_db()
        data = V1ServiceOrderDetailSerializer(self.order).data
        self.assertEqual(data['whatsapp'], {'enabled': False, 'customer_opt_in': False})

    def test_it_needs_the_capability_and_the_customer_of_this_company(self):
        viewer = self.only_capabilities('service.customers.view', slug='no-consiente')
        self.assertEqual(viewer.post(self.url(), {'opt_in': True}, format='json').status_code, 403)

        client = self.with_capabilities('service.customers.manage', slug='consiente-2')
        foreign = _m8_url('m8-taller', f'customers/{self.foreign_customer.pk}/whatsapp-consent/')
        self.assertEqual(client.post(foreign, {'opt_in': True}, format='json').status_code, 404)


class CloudApiProviderTest(SimpleTestCase):
    """El cliente de la API oficial, contra un `urlopen` simulado. Sin red."""

    def provider(self):
        return CloudApiProvider(phone_number_id='1055500000', access_token='TOKEN-REAL', api_version='v21.0')

    def send(self):
        return self.provider().send_template(
            to='51987654321', template='equipo_listo', language='es',
            parameters=['Ana', 'SRV-000001', 'https://tienda.example/seguimiento/abc'])

    def test_it_posts_a_template_message_to_the_graph_api(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {'messages': [{'id': 'wamid.ABC'}]}).encode()
        with mock.patch('urllib.request.urlopen', return_value=response) as urlopen:
            result = self.send()

        self.assertEqual(result.provider_message_id, 'wamid.ABC')
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, 'https://graph.facebook.com/v21.0/1055500000/messages')
        self.assertEqual(request.get_header('Authorization'), 'Bearer TOKEN-REAL')
        self.assertLessEqual(urlopen.call_args.kwargs['timeout'], 15)
        body = json.loads(request.data)
        self.assertEqual(body['messaging_product'], 'whatsapp')
        self.assertEqual(body['to'], '51987654321')
        self.assertEqual(body['type'], 'template')
        self.assertEqual(body['template']['name'], 'equipo_listo')
        self.assertEqual(body['template']['language'], {'code': 'es'})
        self.assertEqual(
            [p['text'] for p in body['template']['components'][0]['parameters']],
            ['Ana', 'SRV-000001', 'https://tienda.example/seguimiento/abc'])

    def error(self, status, payload):
        return urllib.error.HTTPError(
            'https://graph.facebook.com', status, 'x', {}, io.BytesIO(json.dumps(payload).encode()))

    def test_a_server_side_failure_can_be_retried_and_a_refusal_cannot(self):
        cases = (
            (500, {'error': {'message': 'Internal', 'code': 1}}, True),
            (429, {'error': {'message': 'Rate limit', 'code': 130429}}, True),
            (400, {'error': {'message': 'Template does not exist', 'code': 132001}}, False),
            (401, {'error': {'message': 'Invalid OAuth access token TOKEN-REAL', 'code': 190}}, False),
        )
        for status, payload, retryable in cases:
            with self.subTest(status), mock.patch('urllib.request.urlopen', side_effect=self.error(status, payload)):
                with self.assertRaises(ProviderError) as raised:
                    self.send()
                self.assertEqual(raised.exception.retryable, retryable)
                self.assertEqual(raised.exception.code, str(payload['error']['code']))
                # Lo que diga el proveedor no puede devolver el token a un registro.
                self.assertNotIn('TOKEN-REAL', str(raised.exception))

    def test_a_request_that_never_left_can_be_retried(self):
        refused = urllib.error.URLError(ConnectionRefusedError(61, 'Connection refused'))
        with mock.patch('urllib.request.urlopen', side_effect=refused):
            with self.assertRaises(ProviderError) as raised:
                self.send()
        self.assertTrue(raised.exception.retryable)

    def test_a_request_that_got_no_answer_is_not_sent_again_by_itself(self):
        """
        Sin respuesta no se sabe si el proveedor lo aceptó. Reenviarlo solo es
        como el cliente recibe el mismo mensaje dos veces: lo decide una persona.
        """
        for silence in (TimeoutError('timed out'), urllib.error.URLError(TimeoutError('timed out'))):
            with self.subTest(silence), mock.patch('urllib.request.urlopen', side_effect=silence):
                with self.assertRaises(ProviderError) as raised:
                    self.send()
            self.assertFalse(raised.exception.retryable)

    def test_an_answer_without_a_message_id_is_a_failure_not_a_success(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{}'
        with mock.patch('urllib.request.urlopen', return_value=response):
            with self.assertRaises(ProviderError):
                self.send()
