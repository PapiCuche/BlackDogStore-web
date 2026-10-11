"""
MAIL-TEMPLATE-01, phase 2 · the notices a shop sends by e-mail wear its template.

`notification_services` already decides WHICH events are worth an e-mail, to
whom, and that each goes once. None of that changes here. What these tests hold
the code to:

  * a notice of the template's owner goes dressed, with a button that leads to
    where the detail lives — the customer's own page, or the panel for staff;
  * it says what the notice said and nothing more: no cost, no diagnosis, no
    field of the order it is about;
  * any other company's notice is the one it was;
  * the delivery row tells the truth: SENT, FAILED and one attempt each.
"""
import json
import re
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail as outbox_module
from django.core.cache import cache
from django.core.mail.message import EmailMessage
from django.test import override_settings
from django.utils import timezone

from store import mail
from store import notification_events as ev
from store import notification_services as notif
from store.mail import builders, contract
from store.models import Company, Customer, Notification, NotificationDelivery
from store.test_mail import PILOT_MARKS, SITE, TEMPLATE, _Base, example, other_company, shape, within
from store.tests import M12DeliveryBase

User = get_user_model()

# event → (what it is about, its number, where the customer reads it, the step it is at).
# No page for the repairs here: these are hand-made notices about repairs that do
# not exist, and a repair's page is its tracking link — `RepairTrackingLinkTest`.
CUSTOMER_EVENTS = {
    ev.SERVICE_QUOTE_AVAILABLE: ('repair_order', 41, None, 'Cotización por revisar'),
    ev.SERVICE_READY_FOR_PICKUP: ('repair_order', 42, None, 'Listo para recoger'),
    ev.COMMERCE_FULFILLMENT_READY: ('order', 43, '/orders', 'Listo para recoger'),
    ev.COMMERCE_FULFILLMENT_SHIPPED: ('order', 44, '/orders', 'Enviado'),
}


class NotificationEmailTest(_Base):
    def setUp(self):
        super().setUp()
        self.customer = self.customer_of(self.pilot)
        self.staff = User.objects.create_user('tecnico', 'tecnico@tienda.test', 'x', first_name='Teo')

    def customer_of(self, company, email='ana@correo.test', account=True):
        user = User.objects.create_user(f'cliente{User.objects.count()}', email, 'x') if account else None
        return Customer.objects.create(
            company=company, first_name='Ana', last_name='Torres', document_type='dni',
            document_number=str(10000000 + Customer.objects.count()), email=email, user=user)

    def order_of(self, customer, **extra):
        from store.tests import _p3_order

        return _p3_order(customer.company, customer=customer, user=customer.user, **extra)

    def emit(self, event_type, *, company=None, key='k', title='Tu equipo está listo para recoger',
             body='Puedes pasar a retirarlo cuando quieras. Orden ST-000042.', **who):
        with self.captureOnCommitCallbacks(execute=True):
            return notif.emit(company=company or self.pilot, event_type=event_type, event_key=key,
                              title=title, body=body, **who)

    def one(self):
        self.assertEqual(len(outbox_module.outbox), 1)
        return outbox_module.outbox[0]

    def test_this_file_knows_every_event_that_goes_by_email(self):
        self.assertEqual(set(CUSTOMER_EVENTS), set(ev.EMAIL_WORTHY_EVENTS))

    def test_the_owners_notice_to_a_customer_wears_the_template(self):
        for event_type, (target, number, page, step) in CUSTOMER_EVENTS.items():
            with self.subTest(event_type):
                outbox_module.outbox = []
                if target == 'order':
                    number = self.order_of(self.customer).pk
                self.emit(event_type, key=event_type, customers=[self.customer], target_type=target, target_id=number)
                message = self.one()
                html = message.alternatives[0][0]
                self.assertEqual(message.to, ['ana@correo.test'])
                self.assertEqual(message.subject, 'Tu equipo está listo para recoger · Black Dog Store')
                self.assertIn('logo-horizontal-on-light.png', html)
                self.assertIn('>Tu equipo está listo para recoger</h1>', html)
                self.assertIn('Puedes pasar a retirarlo cuando quieras. Orden ST-000042.', html)
                if page:
                    self.assertIn(f'href="{SITE}{page}"', html)
                    self.assertIn(f'{SITE}{page}', message.body)
                else:
                    self.assertNotIn('class="btn"', html)
                self.assertIn(f'[>] {step} (en curso)', message.body)
                self.assertEqual(message.body.count('[>]'), 1)
                delivery = NotificationDelivery.objects.get(notification__event__event_key=event_type)
                self.assertEqual(delivery.status, NotificationDelivery.Status.SENT)

    def test_an_order_is_named_by_its_number_and_a_repair_by_what_the_notice_says(self):
        order = self.order_of(self.customer)
        self.emit(ev.COMMERCE_FULFILLMENT_SHIPPED, customers=[self.customer], target_type='order', target_id=order.pk,
                  title='Tu pedido fue enviado', body=f'Va en camino. Pedido #{order.pk}.')
        self.assertIn(f'PEDIDO · N.º {order.pk}', self.one().body)

        outbox_module.outbox = []
        self.emit(ev.SERVICE_READY_FOR_PICKUP, key='k2', customers=[self.customer], target_type='repair_order', target_id=5)
        body = self.one().body
        self.assertIn('SERVICIO TÉCNICO', body)
        self.assertNotIn('N.º 5', body)                # a repair's number is its code, and the notice already says it

    def test_the_staff_copy_leads_to_the_panel_and_carries_no_progress(self):
        self.emit(ev.SERVICE_READY_FOR_PICKUP, users=[self.staff], target_type='repair_order', target_id=42)
        message = self.one()
        self.assertEqual(message.to, ['tecnico@tienda.test'])
        self.assertIn(f'{SITE}/admin/service/orders/42', message.body)
        self.assertIn(f'href="{SITE}/admin/service/orders/42"', message.alternatives[0][0])
        self.assertNotIn('[>]', message.body)
        self.assertNotIn('/repairs', message.body)
        self.assertIn('formas parte del equipo', message.body)

    def test_one_event_for_both_audiences_gives_each_its_own(self):
        self.emit(ev.SERVICE_READY_FOR_PICKUP, users=[self.staff], customers=[self.customer],
                  target_type='repair_order', target_id=42)
        by_address = {message.to[0]: message.body for message in outbox_module.outbox}
        self.assertEqual(set(by_address), {'tecnico@tienda.test', 'ana@correo.test'})
        self.assertIn('/admin/service/orders/42', by_address['tecnico@tienda.test'])
        self.assertNotIn('/admin/', by_address['ana@correo.test'])

    def test_it_says_what_the_notice_said_and_nothing_of_the_order(self):
        """A notice is «go and look». The detail stays behind its own authorisation."""
        data = builders.notification(
            company='Black Dog Store', title='Tienes una cotización pendiente de revisión',
            body='Orden ST-000041 · revisa y aprueba o rechaza.', audience=Notification.Audience.CUSTOMER,
            event_type=ev.SERVICE_QUOTE_AVAILABLE, target_type='repair_order', target_id=41, link=f'{SITE}/seguimiento/TOKEN')
        self.assertEqual(within(shape(contract.clean(data)), shape(example('10-aviso'))), [])
        # … and the example it has to stay inside has no block that could carry a detail.
        self.assertEqual(set(example('10-aviso')) & {'detalle', 'datos', 'codigo', 'imagen', 'aviso', 'secundario'}, set())

    def test_a_real_order_gives_the_notice_nothing_but_its_number(self):
        """End to end, through the emitter the shop uses, on an order that has plenty to tell."""
        from decimal import Decimal

        from store import commerce_notifications

        order = self.order_of(self.customer, total=Decimal('4299.00'), customer_phone='+51 987 654 321',
                              document_number='45678912', notes='Dejar con el portero')
        with self.captureOnCommitCallbacks(execute=True):
            commerce_notifications.emit_fulfillment_changed(order, 'shipped')
        message = self.one()
        blob = message.subject + message.body + message.alternatives[0][0]
        self.assertIn(f'Pedido #{order.pk}', blob)
        self.assertIn(f'href="{SITE}/orders"', blob)
        for private in ('4299', '4,299', '987 654 321', '45678912', 'portero', 'Producto '):
            self.assertNotIn(private, blob)

    def test_a_customer_without_an_account_gets_no_button_to_a_page_they_cannot_open(self):
        """
        `/repairs` and `/orders` list what belongs to an ACCOUNT. Somebody who
        left a device at the counter, or bought without registering, has none:
        a button there is a login screen and then an empty list.
        """
        walk_in = self.customer_of(self.pilot, 'luis@correo.test', account=False)
        for event_type, target, number in (
            (ev.SERVICE_QUOTE_AVAILABLE, 'repair_order', 999999),       # a repair that is not there: no tracking link either
            (ev.COMMERCE_FULFILLMENT_SHIPPED, 'order', self.order_of(walk_in).pk),
        ):
            with self.subTest(event_type):
                outbox_module.outbox = []
                # A short body, like the real ones: the line beside the subject is padded, and with what.
                self.emit(event_type, key=event_type, customers=[walk_in], target_type=target, target_id=number, body='Va en camino.')
                message = self.one()
                html = message.alternatives[0][0]
                self.assertIn('logo-horizontal-on-light.png', html)            # dressed all the same
                self.assertIn('Va en camino. Es un aviso sobre tu', html)
                self.assertNotIn('class="btn"', html)
                for dead_end in ('/repairs', '/orders', '/seguimiento', 'con el botón'):
                    self.assertNotIn(dead_end, message.body + html)
                self.assertIn('[>]', message.body)                             # what step it is at is still true

    def test_an_order_of_another_account_gets_no_button_either(self):
        """The customer has an account, but this order was not placed from it: `/orders` would not list it."""
        other_user = User.objects.create_user('otra.cuenta', 'otra@correo.test', 'x')
        from store.tests import _p3_order

        for label, order in (
            ('sold at the counter', _p3_order(self.pilot, customer=self.customer, user=None)),
            ('placed from another account', _p3_order(self.pilot, customer=self.customer, user=other_user)),
        ):
            with self.subTest(label):
                outbox_module.outbox = []
                self.emit(ev.COMMERCE_FULFILLMENT_READY, key=label, customers=[self.customer], target_type='order', target_id=order.pk)
                self.assertNotIn('class="btn"', self.one().alternatives[0][0])

    def test_an_order_of_another_company_is_nobodys_button(self):
        other = other_company()
        from store.tests import _p3_order

        foreign = _p3_order(Company.objects.get(pk=other.pk), user=self.customer.user)
        self.emit(ev.COMMERCE_FULFILLMENT_READY, customers=[self.customer], target_type='order', target_id=foreign.pk)
        self.assertNotIn('class="btn"', self.one().alternatives[0][0])

    def test_a_title_that_cannot_be_a_subject_is_a_failed_delivery_not_a_crash(self):
        self.emit(ev.SERVICE_READY_FOR_PICKUP, customers=[self.customer], target_type='repair_order', target_id=1,
                  title='Listo\nBcc: otro@correo.test')
        self.assertEqual(outbox_module.outbox, [])
        delivery = NotificationDelivery.objects.get()
        self.assertEqual((delivery.status, delivery.attempt_count), (NotificationDelivery.Status.FAILED, 1))

    def test_a_target_nobody_mapped_shows_no_half_message(self):
        data = contract.clean(builders.notification(
            company='Black Dog Store', title='Aviso', body='Algo pasó.', audience=Notification.Audience.CUSTOMER,
            event_type=ev.SERVICE_READY_FOR_PICKUP, target_type='otra_cosa', target_id=3, link=f'{SITE}/x'))
        for block in ('progreso', 'boton', 'etiqueta'):
            self.assertNotIn(block, data)

    def test_what_a_tenant_wrote_is_escaped(self):
        self.emit(ev.SERVICE_READY_FOR_PICKUP, customers=[self.customer], target_type='repair_order', target_id=1,
                  title='<script>alert(1)</script> listo', body='Tom & "Jerry" <b>ya</b>')
        html = self.one().alternatives[0][0]
        self.assertNotIn('<script>alert(1)', html)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt; listo', html)
        self.assertIn('Tom &amp; &quot;Jerry&quot; &lt;b&gt;ya&lt;/b&gt;', html)

    def test_a_notice_without_a_body_or_a_target_still_goes_dressed(self):
        self.emit(ev.SERVICE_READY_FOR_PICKUP, customers=[self.customer], title='Listo', body='')
        message = self.one()
        html = message.alternatives[0][0]
        self.assertIn('>Listo</h1>', html)
        self.assertNotIn('class="btn"', html)
        self.assertNotIn('None', message.body + html)

    def test_another_companys_notice_is_the_one_it_was(self):
        other = other_company()
        # Having no template is an answer, not a failure: nothing is logged as one.
        with self.assertNoLogs('store.notification_services', level='ERROR'):
            self.emit(ev.COMMERCE_FULFILLMENT_READY, company=other, customers=[self.customer_of(other, 'luis@otra.test')],
                      target_type='order', target_id=9, title='Tu pedido está listo para recoger', body='Pedido #9.')
        message = self.one()
        self.assertEqual(message.subject, 'Otra Tienda · Tu pedido está listo para recoger')
        blob = message.subject + message.body + message.alternatives[0][0]
        for mark in ('Black Dog', 'logo-horizontal-on-light', *PILOT_MARKS):
            self.assertNotIn(mark, blob)
        self.assertEqual(message.body, 'Tu pedido está listo para recoger\n\nPedido #9.\n\n— Otra Tienda')

    def test_a_notice_that_cannot_be_dressed_goes_as_it_did(self):
        with mock.patch('store.mail.service.chevron.render', side_effect=RuntimeError('boom')), \
                self.assertLogs('store.notification_services', level='ERROR'):
            self.emit(ev.SERVICE_READY_FOR_PICKUP, customers=[self.customer], target_type='repair_order', target_id=42)
        message = self.one()
        self.assertEqual(message.subject, 'Black Dog Store · Tu equipo está listo para recoger')
        self.assertNotIn('logo-horizontal-on-light', message.alternatives[0][0])
        self.assertEqual(NotificationDelivery.objects.get().status, NotificationDelivery.Status.SENT)

    def test_a_send_that_fails_is_one_attempt_and_is_written_down(self):
        with mock.patch.object(EmailMessage, 'send', autospec=True, side_effect=TimeoutError('sin respuesta')) as attempt:
            self.emit(ev.SERVICE_READY_FOR_PICKUP, customers=[self.customer], target_type='repair_order', target_id=42)
        self.assertEqual(attempt.call_count, 1)
        self.assertIn('logo-horizontal-on-light', attempt.call_args.args[0].alternatives[0][0])     # the dressed one
        delivery = NotificationDelivery.objects.get()
        self.assertEqual((delivery.status, delivery.attempt_count), (NotificationDelivery.Status.FAILED, 1))
        self.assertEqual(delivery.failure_reason, 'TimeoutError: sin respuesta')

        notif.retry_failed_delivery(delivery)
        delivery.refresh_from_db()
        self.assertEqual((delivery.status, delivery.attempt_count), (NotificationDelivery.Status.SENT, 2))
        self.assertIn('logo-horizontal-on-light', self.one().alternatives[0][0])

    def test_a_long_notice_keeps_a_preheader_that_fits(self):
        long_body = 'Comunicado. ' * 30
        for body in ('', 'Corto.', long_body):
            with self.subTest(len(body)):
                data = builders.notification(
                    company='Black Dog Store', title='Aviso', body=body, audience=Notification.Audience.INTERNAL,
                    event_type=ev.COMMUNICATIONS_ANNOUNCEMENT_PUBLISHED, target_type='announcement', target_id=3, link=f'{SITE}/admin/communications/3')
                cleaned = contract.validate(contract.clean(data))
                self.assertTrue(40 <= len(cleaned['preheader']) <= 90, cleaned['preheader'])
                self.assertEqual(cleaned['boton']['url'], f'{SITE}/admin/communications/3')
                self.assertEqual(cleaned['etiqueta'], 'Comunicado')
                if body.strip():
                    self.assertEqual(cleaned['parrafos'], [body.strip()])

    def test_the_kind_is_declared_and_its_example_renders(self):
        self.assertIn('notification', mail.KINDS)
        rendered = mail.render('notification', builders.notification(
            company='Black Dog Store', title='Tu pedido fue enviado', body='Va en camino. Pedido #44.',
            audience=Notification.Audience.CUSTOMER, event_type=ev.COMMERCE_FULFILLMENT_SHIPPED,
            target_type='order', target_id=44, link=f'{SITE}/orders'), company=self.pilot)
        self.assertEqual(rendered.html.count('En curso'), 1)
        self.assertEqual(len(re.findall(r'\[x\] ', rendered.text)), 1)
        self.assertEqual(contract.clean(builders.notification(
            company='X', title='T', body='B', audience=Notification.Audience.CUSTOMER, event_type=ev.SERVICE_DELIVERED,
            target_type='repair_order', target_id=1, link=''))['anio'], str(timezone.localdate().year))


@override_settings(FRONTEND_URL=SITE)
class SmtpTestMessageTest(_Base):
    """The message a MASTER sends to see that the mail server works."""

    def message(self):
        from store.integrations.providers.smtp import test_message

        return test_message(sender='Tienda <tienda@tienda.test>', send_to='prueba@correo.test')

    def test_where_the_owner_is_the_storefront_it_shows_the_real_design(self):
        message = self.message()
        self.assertEqual(message['To'], 'prueba@correo.test')
        self.assertEqual(message['From'], 'Tienda <tienda@tienda.test>')
        self.assertEqual(message['Subject'], 'Prueba de correo · Black Dog Store')
        text = message.get_body(('plain',)).get_content()
        html = message.get_body(('html',)).get_content()
        self.assertIn('logo-horizontal-on-light.png', html)
        self.assertIn('Prueba de correo', html)
        self.assertNotIn('class="btn"', html)                    # nothing to press
        self.assertNotIn('token', text + html)
        self.assertIn('ni requiere ninguna acción', text)
        self.assertEqual(re.findall(r'https?://\S+', text), [])

    def test_anywhere_else_it_is_the_plain_one_with_no_link(self):
        with override_settings(DEFAULT_STOREFRONT_COMPANY_SLUG=''), \
                self.assertNoLogs('store.integrations.providers.smtp', level='ERROR'):
            message = self.message()
        self.assertEqual(message['Subject'], 'Prueba de correo')
        self.assertFalse(message.is_multipart())
        self.assertNotIn('http', message.get_content())

    def test_a_name_that_cannot_be_a_subject_does_not_cost_the_test(self):
        with mock.patch('store.mail.brand', return_value='Tienda\nBcc: otro@correo.test'), \
                self.assertLogs('store.integrations.providers.smtp', level='ERROR'):
            message = self.message()
        self.assertEqual(message['Subject'], 'Prueba de correo')
        self.assertFalse(message.is_multipart())
        self.assertEqual(message['To'], 'prueba@correo.test')

    def test_the_provider_sends_the_dressed_one_through_a_real_conversation(self):
        from cryptography.fernet import Fernet
        from rest_framework.test import APIClient

        from store.test_integrations_smtp import PASSWORD, received, start_sink

        with override_settings(APP_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            port, folder = start_sink(self, ('tienda', PASSWORD))
            client = APIClient()
            client.force_authenticate(User.objects.create_superuser('master', 'master@tienda.test', 'x'))
            saved = client.put('/api/admin/integrations/smtp/draft/', {
                'public': {'host': '127.0.0.1', 'port': port, 'security': 'none', 'username': 'tienda',
                           'from_email': 'tienda@tienda.test', 'from_name': 'Tienda', 'timeout': 3},
                'secrets': {'password': PASSWORD}}, format='json')
            self.assertEqual(saved.status_code, 200, saved.content)
            result = client.post('/api/admin/integrations/smtp/test/', {'send_to': 'prueba@correo.test'}, format='json').json()
        self.assertEqual(result['status'], 'ok', result)
        [raw] = received(folder)
        self.assertIn('multipart/alternative', raw)
        self.assertIn('logo-horizontal-on-light.png', raw.replace('=\n', ''))

    def test_a_template_that_cannot_be_filled_does_not_cost_the_test(self):
        with mock.patch('store.mail.service.chevron.render', side_effect=RuntimeError('boom')), \
                self.assertLogs('store.integrations.providers.smtp', level='ERROR'):
            message = self.message()
        self.assertEqual(message['Subject'], 'Prueba de correo')
        self.assertFalse(message.is_multipart())


class RepairTrackingLinkTest(M12DeliveryBase):
    """
    MAIL-TRACKING-LINK · a repair's notice to its customer carries the tracking link.

    The owner decided it: the page behind that link is where a quote is read and
    answered, and most people who leave a device have no account to look in.
    The link is a way in, so it goes to the order's OWN customer and nobody
    else, a revoked one stays revoked, and it is written to no log.

    The real path of the workshop, not a hand-made notice: the fixture's order
    (`self.order`, received in `setUp`) is quoted, repaired and checked.
    """

    def setUp(self):
        super().setUp()
        cache.clear()
        outbox_module.outbox = []
        company = self.order_company()
        Company.objects.filter(pk=company.pk).update(legal_name='Taller de Prueba S.A.C.', tax_id='20999999991')
        owner = {**json.loads((TEMPLATE.parent / 'marca.json').read_text(encoding='utf-8')), 'empresa': company.slug}
        patcher = mock.patch('store.mail.skin._brand', return_value=owner)
        patcher.start()
        self.addCleanup(patcher.stop)
        for setting in (override_settings(FRONTEND_URL=SITE, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                                          DEFAULT_FROM_EMAIL='no-reply@tienda.test'),):
            setting.enable()
            self.addCleanup(setting.disable)

    def order_company(self):
        return Company.objects.get(slug='m8-taller')

    def ready(self, *, email='ana@correo.test'):
        with self.captureOnCommitCallbacks(execute=True):
            Customer.objects.filter(pk=self.order.customer_id).update(email=email, user=None)
            self.order.customer.refresh_from_db()
            self.ready_for_pickup()
        return {message.to[0]: message for message in outbox_module.outbox}

    def test_the_customers_notice_leads_to_the_tracking_page_account_or_not(self):
        from store import tracking_services

        sent = self.ready()
        message = sent['ana@correo.test']
        link = tracking_services.url_for(self.order)
        self.assertTrue(link.startswith(f'{SITE}/seguimiento/'))
        self.assertIsNone(self.order.customer.user_id)                     # no account, and it does not matter
        self.assertIn(f'href="{link}"', message.alternatives[0][0])
        self.assertIn(f'Ver mi equipo:\n{link}', message.body)
        self.assertNotIn('/repairs', message.body)

    def test_a_quote_to_review_leads_there_too(self):
        from store import tracking_services

        self.ready()
        outbox_module.outbox = []
        with self.captureOnCommitCallbacks(execute=True):
            notif.emit(company=self.order.company, event_type=ev.SERVICE_QUOTE_AVAILABLE, event_key='cotiza',
                       title='Tienes una cotización pendiente de revisión', body='Revisa y aprueba o rechaza.',
                       customers=[self.order.customer], target_type='repair_order', target_id=self.order.pk)
        [message] = outbox_module.outbox
        self.assertIn(f'href="{tracking_services.url_for(self.order)}"', message.alternatives[0][0])
        self.assertIn('[>] Cotización por revisar (en curso)', message.body)

    def test_the_staff_copy_never_carries_it(self):
        sent = self.ready()
        staff = [message for address, message in sent.items() if address != 'ana@correo.test']
        self.assertTrue(staff)
        for message in staff:
            blob = message.body + message.alternatives[0][0]
            self.assertNotIn('/seguimiento/', blob)
            self.assertIn(f'{SITE}/admin/service/orders/{self.order.pk}', blob)

    def test_a_revoked_link_stays_revoked(self):
        from store import tracking_services
        from store.models import RepairTrackingLink

        with self.captureOnCommitCallbacks(execute=True):
            Customer.objects.filter(pk=self.order.customer_id).update(email='ana@correo.test', user=None)
            tracking_services.revoke(self.order, actor=self.staff)
            self.ready_for_pickup()
        message = {m.to[0]: m for m in outbox_module.outbox}['ana@correo.test']
        self.assertNotIn('/seguimiento/', message.body + message.alternatives[0][0])
        self.assertNotIn('class="btn"', message.alternatives[0][0])
        self.assertFalse(RepairTrackingLink.objects.filter(repair_order=self.order, revoked_at__isnull=True).exists())

    def test_somebody_with_an_account_gets_the_tracking_page_as_well(self):
        """One page for everybody: it is where the quote is answered, and it needs no login."""
        from store import tracking_services

        with self.captureOnCommitCallbacks(execute=True):
            account = User.objects.create_user('ana.cuenta', 'ana@correo.test', 'x')
            Customer.objects.filter(pk=self.order.customer_id).update(email='ana@correo.test', user=account)
            self.ready_for_pickup()
        message = {m.to[0]: m for m in outbox_module.outbox}['ana@correo.test']
        self.assertIn(f'href="{tracking_services.url_for(self.order)}"', message.alternatives[0][0])
        self.assertNotIn('/repairs', message.body)

    def test_with_the_link_revoked_an_account_still_has_its_own_page(self):
        from store import tracking_services

        with self.captureOnCommitCallbacks(execute=True):
            account = User.objects.create_user('ana.cuenta', 'ana@correo.test', 'x')
            Customer.objects.filter(pk=self.order.customer_id).update(email='ana@correo.test', user=account)
            tracking_services.revoke(self.order, actor=self.staff)
            self.ready_for_pickup()
        message = {m.to[0]: m for m in outbox_module.outbox}['ana@correo.test']
        self.assertIn(f'href="{SITE}/repairs"', message.alternatives[0][0])

    def test_it_goes_to_the_orders_own_customer_and_to_nobody_else(self):
        """A notice that names a repair it does not belong to gets no way into it."""
        from store import tracking_services

        self.ready()
        token = tracking_services.url_for(self.order).rsplit('/', 1)[1]
        stranger = Customer.objects.create(
            company=self.order.company, first_name='Otra', last_name='Persona', document_type='dni',
            document_number='70000001', email='otra@correo.test')
        outbox_module.outbox = []
        with self.captureOnCommitCallbacks(execute=True):
            notif.emit(company=self.order.company, event_type=ev.SERVICE_READY_FOR_PICKUP, event_key='ajeno',
                       title='Listo', body='Orden ajena.', customers=[stranger],
                       target_type='repair_order', target_id=self.order.pk)
        [message] = outbox_module.outbox
        self.assertNotIn(token, message.body + message.alternatives[0][0])
        self.assertNotIn('/seguimiento/', message.body)

    def test_the_link_is_written_to_no_log_and_to_no_failure_reason(self):
        import logging

        from store import tracking_services

        records = []

        class Keep(logging.Handler):
            def emit(self, record):
                records.append(self.format(record) + repr(record.args))

        handler = Keep(level=logging.DEBUG)
        handler.setFormatter(logging.Formatter('%(message)s\n%(exc_text)s'))
        watched = [logging.getLogger()] + [
            logger for logger in logging.root.manager.loggerDict.values() if isinstance(logger, logging.Logger)]
        for logger in watched:
            logger.addHandler(handler)
        try:
            with mock.patch.object(EmailMessage, 'send', side_effect=TimeoutError('sin respuesta')):
                self.ready()
        finally:
            for logger in watched:
                logger.removeHandler(handler)
        token = tracking_services.url_for(self.order).rsplit('/', 1)[1]
        failed = NotificationDelivery.objects.filter(status=NotificationDelivery.Status.FAILED)
        self.assertTrue(failed.exists())
        for delivery in failed:
            self.assertNotIn(token, delivery.failure_reason)
        self.assertTrue(records)
        self.assertNotIn(token, '\n'.join(records))
        self.assertNotIn('/seguimiento/', '\n'.join(records))
