"""
PAY-SWITCH-PENDING — cambiar de pasarela con cobros abiertos.

Un comprador con el formulario de la tarjeta delante paga contra la pasarela que
había cuando lo abrió. Si entretanto se activa otro producto, se apaga el que
cobra, se cambia de tienda o de entorno (TEST ↔ PRODUCCIÓN), su notificación ya
no se puede creer ni su pago consultar: cobrado y sin pedido.

Lo que se decidió: BLOQUEAR lo que no se puede comprobar. Con «Mi Cuenta Web»:

  * antes de un cambio así, el servidor pregunta a la pasarela por los cobros
    abiertos, con las claves que TODAVÍA están (la misma consulta de
    PAY-RECONCILE, no otra);
  * si alguno aún puede cobrarse (su formulario vive), o la pasarela lo tiene
    sin resolver, el cambio se rechaza (409) y no cambia nada. Escribir
    INTERRUMPIR ya no lo salta;
  * renovar la contraseña o las claves de la MISMA tienda y entorno no es un
    cambio así: sigue permitido;
  * INTERRUMPIR queda para lo que no se puede comprobar: el otro producto, que
    no tiene consulta, y una pasarela que no responde. Entonces queda un
    registro de auditoría con los intentos interrumpidos, bajo su empresa.

La pasarela es de la instalación, no de cada empresa (PAY-TENANT-SCOPE): no hay
configuración por empresa que aislar. Lo que sí es de cada empresa es el
registro de sus intentos interrumpidos.
"""
import threading
from datetime import datetime, timedelta
from decimal import Decimal
from unittest import mock, skipUnless
from urllib.parse import urlencode

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from store import checkout_services as checkout
from store.integrations import payments as payment_adapters
from store.integrations import registry, service
from store.models import (
    AdminAuditLog, CartItem, Company, IntegrationConfig, Order, OrderDelivery, OrderRiskAssessment, PaymentTransaction,
)
from store.payments.fake_micuentaweb import FakeMiCuentaWeb
from store.test_integrations_payments import (
    CHECKOUT_PUBLIC, CHECKOUT_SECRETS, EMPTY_ENV, MCW_PUBLIC, MCW_SECRETS, _Base, fake_checkout, fake_mcw,
)
from store.test_micuentaweb import IPN_URL, MCW_SETTINGS, McwBase
from store.tests import IZIPAY_TEST_SETTINGS, _pilot_company, _seeded

OTHER_SHOP = {**MCW_PUBLIC, 'shop_id': '90000002', 'public_key': '90000002:testpublickey_OtraTiendaNoEsRealNoEsReal'}
PRODUCTION = {**MCW_PUBLIC, 'public_key': '90000001:publickey_DelPanelNoEsRealNoEsReal'}
PRODUCTION_SECRETS = {'password': 'prodpassword_DelPanelNoEsRealNoEsRealNoEsReal'}
RENEWED_SECRETS = {'password': 'testpassword_RenovadaNoEsRealNoEsRealNoEsReal'}

#: Every change after which an open attempt of «Mi Cuenta Web» could no longer
#: be confirmed or asked about.
UNSAFE = ('otro producto', 'apagar', 'revocar', 'otra tienda', 'otro entorno')


class GuardBase(_Base):
    def setUp(self):
        super().setUp()
        self.gateway = self.go_live_with_mcw()
        self.gateway.requests.clear()

    def reset_console(self):
        """Volver a «Mi Cuenta Web» cobrando, sin nada más guardado."""
        IntegrationConfig.objects.all().delete()
        self.gateway = self.go_live_with_mcw()
        self.gateway.requests.clear()

    # -- an attempt of the product that charges ---------------------------------

    def open_attempt(self, **age):
        # Un pago confirmado vacía el carrito: quien vuelve a comprar lo llena otra vez.
        CartItem.objects.get_or_create(session_key=self.session_key, product=self.product, defaults={'quantity': 2})
        self.assertEqual(self.buy(self.gateway).status_code, 200)
        attempt = PaymentTransaction.objects.select_related('order').latest('pk')
        if age:
            PaymentTransaction.objects.filter(pk=attempt.pk).update(created_at=timezone.now() - timedelta(**age))
        self.gateway.requests.clear()
        return attempt

    def held(self, attempt, **overrides):
        values = {'order_id': attempt.transaction_id, 'amount_cents': 29800}
        values.update(overrides)
        return self.gateway.transaction(**values)

    # -- the changes ----------------------------------------------------------------

    def prepare(self, name):
        """Lo que haya que dejar guardado y probado para poder pedir el cambio."""
        if name == 'otro producto':
            self.assertEqual(self.save('izipay_checkout', CHECKOUT_PUBLIC, CHECKOUT_SECRETS).status_code, 200)
            self.assertEqual(self.validate('izipay_checkout', fake_checkout())['status'], 'ok')
        elif name == 'otra tienda':
            self.assertEqual(self.save('izipay_micuentaweb', OTHER_SHOP, MCW_SECRETS).status_code, 200)
            self.assertEqual(self.validate('izipay_micuentaweb', fake_mcw(OTHER_SHOP))['status'], 'ok')
        elif name == 'otro entorno':
            self.assertEqual(self.save('izipay_micuentaweb', PRODUCTION, PRODUCTION_SECRETS).status_code, 200)
            self.assertEqual(self.validate('izipay_micuentaweb', fake_mcw())['status'], 'unverified')
        elif name == 'renovar':
            self.assertEqual(self.save('izipay_micuentaweb', MCW_PUBLIC, RENEWED_SECRETS).status_code, 200)
            self.assertEqual(self.validate('izipay_micuentaweb', fake_mcw(MCW_PUBLIC, RENEWED_SECRETS))['status'], 'ok')
        self.gateway.requests.clear()

    def change(self, name, **words):
        """Pedir el cambio. La pasarela que TODAVÍA cobra es la que contesta a lo que se le pregunte."""
        with self.gateway.online():
            if name == 'apagar':
                return self.console.post(self.url('izipay_micuentaweb', 'disable'), words, format='json')
            if name == 'revocar':
                return self.console.post(self.url('izipay_micuentaweb', 'revoke'), {'confirm': 'REVOCAR', **words},
                                         format='json')
            if name == 'otro producto':
                return self.activate('izipay_checkout', **words)
            if name == 'otro entorno':
                return self.activate('izipay_micuentaweb', confirm='PRODUCCION', **words)
            return self.activate('izipay_micuentaweb', **words)

    def snapshot(self):
        return (
            checkout.active_payment_provider(),
            list(IntegrationConfig.objects.order_by('pk').values_list('provider', 'slot', 'enabled', 'version', 'public')),
            AdminAuditLog.objects.filter(action__startswith='integration_').exclude(action='integration_tested').count(),
        )

    def asked(self):
        return [r for r in self.gateway.requests if r['url'].endswith('/Order/Get')]

    def assert_blocked(self, response, before, **counts):
        self.assertEqual(response.status_code, 409, response.content)
        body = response.json()
        self.assertEqual(body['code'], 'open_payments')
        for name, value in counts.items():
            self.assertEqual(body['open_payments'][name], value, name)
        self.assertTrue(body['open_payments']['retry_at'])
        for secret in (MCW_SECRETS['password'], CHECKOUT_SECRETS['api_key'], CHECKOUT_SECRETS['hash_key']):
            self.assertNotIn(secret, response.content.decode())
        # Rechazado: no cambia nada de lo configurado.
        self.assertEqual(self.snapshot(), before)


class UnsafeChangeTest(GuardBase):
    def test_with_nothing_open_every_change_goes_through_and_nobody_is_asked(self):
        for name in UNSAFE:
            with self.subTest(name):
                self.reset_console()
                self.prepare(name)
                self.assertEqual(self.change(name).status_code, 200)
                self.assertEqual(self.asked(), [])

    def test_an_attempt_whose_form_can_still_charge_blocks_every_one_of_them(self):
        attempt = self.open_attempt(minutes=10)
        for name in UNSAFE:
            with self.subTest(name):
                self.prepare(name)
                before = self.snapshot()
                self.assert_blocked(self.change(name), before, open=1, can_still_charge=1)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, PaymentTransaction.Status.PENDING)

    def test_typing_the_word_is_no_longer_a_way_round_an_attempt_that_can_still_charge(self):
        self.open_attempt(minutes=10)
        for name in UNSAFE:
            with self.subTest(name):
                self.prepare(name)
                before = self.snapshot()
                self.assert_blocked(self.change(name, acknowledge='INTERRUMPIR'), before, can_still_charge=1)
        self.assertFalse(AdminAuditLog.objects.filter(action='payments_interrupted').exists())

    def test_the_refusal_says_how_many_and_when_to_come_back(self):
        attempt = self.open_attempt(minutes=10)
        self.open_attempt(minutes=4)
        response = self.change('apagar')

        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertIn('2 cobros abiertos', body['detail'])
        self.assertEqual(body['open_payments']['can_still_charge'], 2)
        # Cuando el formulario del más reciente ya no pueda cobrar: 30 minutos después de abrirse.
        retry = datetime.fromisoformat(body['open_payments']['retry_at'])
        self.assertAlmostEqual(
            (retry - timezone.now()).total_seconds(), 26 * 60, delta=90)
        self.assertNotIn(attempt.transaction_id, response.content.decode())

    def test_an_attempt_just_opened_blocks_without_the_gateway_being_asked(self):
        self.open_attempt()
        before = self.snapshot()
        self.assert_blocked(self.change('apagar'), before, can_still_charge=1)
        # Demasiado reciente para preguntar por él: su notificación aún no tuvo su oportunidad.
        self.assertEqual(self.asked(), [])

    def test_an_old_attempt_the_gateway_knows_nothing_of_holds_nothing_up(self):
        for name in UNSAFE:
            with self.subTest(name):
                self.reset_console()
                PaymentTransaction.objects.all().delete()
                OrderRiskAssessment.objects.all().delete()
                OrderDelivery.objects.all().delete()
                Order.objects.all().delete()
                attempt = self.open_attempt(minutes=45)
                self.prepare(name)
                self.assertEqual(self.change(name).status_code, 200)
                self.assertEqual([r['body']['orderId'] for r in self.asked()], [attempt.transaction_id])

    def test_a_payment_the_gateway_already_took_is_paid_first_with_the_keys_still_there(self):
        """La consulta se hace ANTES del cambio y con las credenciales actuales: el pedido queda pagado y el cambio sigue."""
        _seeded(self.product, 50)               # cada vuelta vende dos unidades de verdad
        for name in UNSAFE:
            with self.subTest(name):
                self.reset_console()
                attempt = self.open_attempt(minutes=20)
                self.gateway.hold(attempt.transaction_id, self.held(attempt))
                self.prepare(name)

                self.assertEqual(self.change(name).status_code, 200)

                self.assertEqual(self.gateway.violations, [])
                attempt.refresh_from_db()
                attempt.order.refresh_from_db()
                self.assertTrue(attempt.order.paid)
                self.assertEqual(attempt.status, PaymentTransaction.Status.AUTHORIZED)

    def test_what_the_gateway_has_not_resolved_blocks_the_change(self):
        cases = {
            'en curso': lambda a: [self.held(a, status='RUNNING')],
            'un reembolso': lambda a: [self.held(a), self.held(a, operation='CREDIT', uuid='c' * 32)],
            'dos cobros': lambda a: [self.held(a, uuid='a' * 32), self.held(a, uuid='b' * 32)],
        }
        for case, transactions in cases.items():
            for name in ('otro producto', 'apagar'):
                with self.subTest(f'{case} · {name}'):
                    self.reset_console()
                    PaymentTransaction.objects.all().delete()
                    OrderRiskAssessment.objects.all().delete()
                    OrderDelivery.objects.all().delete()
                    Order.objects.all().delete()
                    attempt = self.open_attempt(minutes=45)
                    self.gateway.hold(attempt.transaction_id, *transactions(attempt))
                    self.prepare(name)
                    before = self.snapshot()
                    response = self.change(name, acknowledge='INTERRUMPIR')
                    self.assert_blocked(response, before, open=1, can_still_charge=0, unresolved=1)
                    self.assertIn('sin resolver', response.json()['detail'])

    def test_the_other_product_is_another_gateway_even_with_the_same_account_number(self):
        self.open_attempt(minutes=10)
        same_number = {**CHECKOUT_PUBLIC, 'merchant_code': MCW_PUBLIC['shop_id']}
        self.assertEqual(self.save('izipay_checkout', same_number, CHECKOUT_SECRETS).status_code, 200)
        self.assertEqual(self.validate('izipay_checkout', fake_checkout(same_number))['status'], 'ok')
        before = self.snapshot()

        self.assert_blocked(self.change('otro producto'), before, can_still_charge=1)

    def test_more_open_attempts_than_one_change_asks_about_is_not_a_verified_change(self):
        from store.integrations.providers import payments as payment_providers

        self.open_attempt(minutes=50)
        self.open_attempt(minutes=45)
        before = self.snapshot()
        with mock.patch.object(payment_providers, 'GUARD_LIMIT', 1):
            response = self.change('apagar', acknowledge='INTERRUMPIR')

        # Se preguntó por uno (la pasarela no tenía nada de él); el otro sigue sin comprobar.
        self.assert_blocked(response, before, open=2, can_still_charge=0)
        self.assertEqual(len(self.asked()), 1)

    def test_a_change_found_unsafe_only_at_the_last_moment_was_not_verified_and_is_not_made(self):
        """
        Lo que se pregunta a la pasarela se pregunta antes de tomar el bloqueo.
        Si al decidir, ya con él, resulta que nadie preguntó, no se decide a ciegas.
        """
        self.open_attempt(minutes=45)
        provider = registry.get('izipay_micuentaweb')
        for word in (False, True):
            with self.assertRaises(registry.ChangeBlocked):
                provider.guard_change(None, change='disable', prepared=None, acknowledged=word)

    def test_an_attempt_older_than_the_window_is_nobodys_business_any_more(self):
        self.open_attempt(hours=3)
        self.assertEqual(self.change('apagar').status_code, 200)
        self.assertEqual(self.asked(), [])

    def test_an_attempt_that_already_has_its_answer_holds_nothing_up(self):
        attempt = self.open_attempt(minutes=5)
        form = self.gateway.ipn(self.gateway.answer(order_id=attempt.transaction_id, amount_cents=29800))
        self.assertEqual(APIClient().post(
            IPN_URL, data=urlencode(form), content_type='application/x-www-form-urlencoded').status_code, 200)

        self.assertEqual(self.change('apagar').status_code, 200)
        self.assertEqual(self.asked(), [])


class GatewayThatDoesNotAnswerTest(GuardBase):
    """Lo único de «Mi Cuenta Web» que sigue pidiendo la palabra: no poder preguntar."""

    def setUp(self):
        super().setUp()
        self.attempt = self.open_attempt(minutes=45)
        self.gateway.mode = 'unreachable'

    def test_it_has_to_be_meant(self):
        for name in ('otro producto', 'apagar', 'revocar'):
            with self.subTest(name):
                self.prepare(name)
                before = self.snapshot()
                with self.assertLogs('store', level='ERROR'):
                    refused = self.change(name)
                self.assertEqual(refused.status_code, 400, refused.content)
                self.assertIn('INTERRUMPIR', refused.json()['errors']['acknowledge'])
                self.assertIn('1 cobro abierto', refused.json()['errors']['acknowledge'])
                self.assertEqual(self.snapshot(), before)

    def test_meant_it_goes_through_and_leaves_the_interrupted_attempts_on_record(self):
        with self.assertLogs('store', level='ERROR'):
            response = self.change('apagar', acknowledge='INTERRUMPIR')

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(checkout.active_payment_provider(), '')
        log = AdminAuditLog.objects.get(action='payments_interrupted')
        self.assertEqual(log.company_id, self.attempt.order.company_id)
        self.assertEqual(log.actor, self.master)
        self.assertEqual(log.metadata, {
            'provider': 'izipay_micuentaweb', 'scope': 'platform', 'change': 'disable',
            'interrupted': 'izipay_micuentaweb', 'reason': 'gateway_unreachable',
            'attempts': [self.attempt.pk], 'orders': [self.attempt.order_id],
        })

    def test_the_word_does_not_cover_an_attempt_that_can_still_charge(self):
        self.gateway.mode = 'ok'
        self.open_attempt(minutes=10)
        self.gateway.mode = 'unreachable'
        before = self.snapshot()
        with self.assertLogs('store', level='ERROR'):
            response = self.change('apagar', acknowledge='INTERRUMPIR')
        self.assert_blocked(response, before, open=2, can_still_charge=1)


class SafeRenewalTest(GuardBase):
    def test_a_password_renewed_in_the_same_shop_and_environment_is_allowed_with_a_buyer_at_the_form(self):
        attempt = self.open_attempt(minutes=2)
        self.prepare('renovar')

        response = self.change('renovar')

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.asked(), [])
        # Y el comprador que estaba pagando paga: la pasarela firma ya con la contraseña nueva.
        renewed = fake_mcw(MCW_PUBLIC, RENEWED_SECRETS)
        form = renewed.ipn(renewed.answer(order_id=attempt.transaction_id, amount_cents=29800))
        self.assertEqual(APIClient().post(
            IPN_URL, data=urlencode(form), content_type='application/x-www-form-urlencoded').status_code, 200)
        attempt.order.refresh_from_db()
        self.assertTrue(attempt.order.paid)

    def test_a_new_notification_address_or_hmac_key_is_not_a_change_of_gateway_either(self):
        self.open_attempt(minutes=2)
        public = {**MCW_PUBLIC, 'ipn_url': 'https://otra.invalid/api/payments/micuentaweb/notification/'}
        self.assertEqual(self.save('izipay_micuentaweb', public, {'hmac_key': 'otra-hmac-NoEsReal'}).status_code, 200)
        self.assertEqual(self.validate('izipay_micuentaweb', fake_mcw())['status'], 'ok')

        self.assertEqual(self.change('renovar').status_code, 200)

    def test_switching_back_on_what_was_off_asks_nothing(self):
        self.assertEqual(self.change('apagar').status_code, 200)
        response = self.console.post(self.url('izipay_micuentaweb', 'enable'), {}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(checkout.active_payment_provider(), 'micuentaweb')


class WhatTheConsoleSaysTest(GuardBase):
    def test_open_charges_of_mi_cuenta_web_are_said_without_asking_for_a_word(self):
        self.open_attempt(minutes=10)
        for provider in ('izipay_micuentaweb', 'izipay_checkout'):
            with self.subTest(provider):
                detail = self.console.get(self.url(provider)).json()
                self.assertIsNone(detail['interruption'])
                self.assertEqual(detail['change_notice']['count'], 1)
                self.assertIn('1 cobro abierto', detail['change_notice']['message'])
        self.assertEqual(self.asked(), [])          # leer la consola no pregunta nada a nadie

    def test_with_nothing_open_it_says_nothing(self):
        self.assertIsNone(self.console.get(self.url('izipay_micuentaweb')).json()['change_notice'])


class TheOtherProductTest(_Base):
    """«SDK web / Checkout» no tiene consulta: ahí la palabra sigue, y ahora deja rastro."""

    def setUp(self):
        super().setUp()
        self.sdk = self.go_live_with_checkout()
        self.assertEqual(self.buy(self.sdk).status_code, 200)
        self.attempt = PaymentTransaction.objects.select_related('order').get()

    def test_the_word_still_goes_through_and_now_names_what_it_interrupted(self):
        refused = self.console.post(self.url('izipay_checkout', 'disable'), {}, format='json')
        self.assertEqual(refused.status_code, 400)
        self.assertIn('acknowledge', refused.json()['errors'])
        self.assertFalse(AdminAuditLog.objects.filter(action='payments_interrupted').exists())

        allowed = self.console.post(self.url('izipay_checkout', 'disable'), {'acknowledge': 'INTERRUMPIR'}, format='json')

        self.assertEqual(allowed.status_code, 200)
        log = AdminAuditLog.objects.get(action='payments_interrupted')
        self.assertEqual(log.company_id, self.attempt.order.company_id)
        self.assertEqual(log.metadata['reason'], 'no_reconciliation')
        self.assertEqual(log.metadata['attempts'], [self.attempt.pk])
        self.assertEqual(log.metadata['orders'], [self.attempt.order_id])
        self.assertNotIn(self.attempt.transaction_id, str(log.metadata))

    def test_each_company_finds_its_own_interrupted_attempts_and_only_those(self):
        other = Company.objects.create(name='Otra Tienda', slug='otra-tienda-guard')
        order = Order.objects.create(company=other, total=Decimal('10.00'), customer_email='x@example.com')
        theirs = PaymentTransaction.objects.create(
            order=order, provider='izipay', transaction_id='40000000000000000001',
            order_number='40000000000000000001', amount=Decimal('10.00'), currency='PEN')

        self.console.post(self.url('izipay_checkout', 'disable'), {'acknowledge': 'INTERRUMPIR'}, format='json')

        logs = {log.company_id: log.metadata['attempts']
                for log in AdminAuditLog.objects.filter(action='payments_interrupted')}
        self.assertEqual(logs, {self.attempt.order.company_id: [self.attempt.pk], other.pk: [theirs.pk]})


@override_settings(APP_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode())
class ChargingFromTheEnvironmentTest(McwBase):
    """
    Una instalación que todavía cobra con las variables del servidor: el primer
    cambio hecho desde la consola es un cambio de pasarela como cualquier otro.
    Antes no avisaba de nada.
    """

    def setUp(self):
        super().setUp()
        cache.clear()
        self.master = get_user_model().objects.create_superuser('master', 'master@example.com', 'x')
        self.console = APIClient()
        self.console.force_authenticate(self.master)

    def console_draft(self, provider_id, public, secrets):
        provider = registry.get(provider_id)
        draft = service.save_draft(provider, None, actor=self.master, public=public, secrets=secrets)
        IntegrationConfig.objects.filter(pk=draft.pk).update(
            validated=True, last_test_status='ok', last_tested_at=timezone.now())
        return draft.version

    def activate(self, provider_id, version, **words):
        with self.fake.online():
            return self.console.post(f'/api/admin/integrations/{provider_id}/activate/',
                                     {'version': version, **words}, format='json')

    def test_mi_cuenta_web_from_the_environment_is_asked_and_can_block_too(self):
        attempt = self.pending()
        PaymentTransaction.objects.filter(pk=attempt.pk).update(created_at=timezone.now() - timedelta(minutes=10))
        version = self.console_draft('izipay_checkout', CHECKOUT_PUBLIC, CHECKOUT_SECRETS)

        response = self.activate('izipay_checkout', version, acknowledge='INTERRUMPIR')

        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(checkout.active_payment_provider(), 'micuentaweb')
        self.assertFalse(IntegrationConfig.objects.filter(slot='active').exists())

    def test_moving_the_same_shop_into_the_console_is_not_a_change_of_gateway(self):
        self.pending()
        public = {'shop_id': MCW_SETTINGS['MICUENTAWEB_SHOP_ID'], 'public_key': MCW_SETTINGS['MICUENTAWEB_PUBLIC_KEY'],
                  'api_url': 'https://api.micuentaweb.pe', 'currency': 'PEN', 'ipn_url': MCW_SETTINGS['MICUENTAWEB_IPN_URL']}
        version = self.console_draft('izipay_micuentaweb', public, {'password': MCW_SETTINGS['MICUENTAWEB_PASSWORD']})

        self.assertEqual(self.activate('izipay_micuentaweb', version).status_code, 200)

    def test_throwing_away_a_draft_changes_nothing_of_what_the_environment_charges_with(self):
        self.pending()
        self.console_draft('izipay_micuentaweb', MCW_PUBLIC, MCW_SECRETS)

        with self.fake.online():
            response = self.console.post(
                '/api/admin/integrations/izipay_micuentaweb/revoke/', {'confirm': 'REVOCAR'}, format='json')

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(checkout.active_payment_provider(), 'micuentaweb')
        self.assertEqual([r for r in self.fake.requests if r['url'].endswith('/Order/Get')], [])

    def test_the_other_product_from_the_environment_now_asks_for_the_word(self):
        from store.payments.fake_izipay import FakeIzipay

        with override_settings(**IZIPAY_TEST_SETTINGS):
            self.fake = FakeIzipay.from_settings(IZIPAY_TEST_SETTINGS)
            self.assertEqual(self.checkout().status_code, 200)
            self.fake = FakeMiCuentaWeb.from_settings(MCW_SETTINGS)
            version = self.console_draft('izipay_micuentaweb', MCW_PUBLIC, MCW_SECRETS)

            refused = self.activate('izipay_micuentaweb', version)
            self.assertEqual(refused.status_code, 400, refused.content)
            self.assertIn('acknowledge', refused.json()['errors'])
            self.assertEqual(self.activate('izipay_micuentaweb', version, acknowledge='INTERRUMPIR').status_code, 200)
        self.assertEqual(AdminAuditLog.objects.filter(action='payments_interrupted').count(), 1)


class ACheckoutThatReadTheOldGatewayTest(GuardBase):
    def test_credentials_loaded_before_a_change_cannot_open_an_attempt_after_it(self):
        """
        El checkout lee la pasarela al empezar y escribe su intento después. Si
        entre una cosa y otra la pasarela cambió, no escribe nada: el comprador
        reintenta y paga contra lo que hay.
        """
        credentials = checkout.require_payment_provider_configured(self.master)
        order = Order.objects.create(company=_pilot_company(), total=Decimal('10.00'), customer_email='x@example.com')
        self.prepare('otro producto')
        self.assertEqual(self.change('otro producto').status_code, 200)

        with self.assertRaises(checkout.CheckoutError) as refused, self.gateway.online():
            checkout.start_payment_attempt(order, credentials=credentials)

        self.assertEqual(refused.exception.status_code, 409)
        self.assertFalse(PaymentTransaction.objects.exists())
        self.assertEqual(self.gateway.requests, [])

    def test_a_renewed_password_is_a_change_for_a_checkout_that_holds_the_old_one(self):
        credentials = checkout.require_payment_provider_configured(self.master)
        order = Order.objects.create(company=_pilot_company(), total=Decimal('10.00'), customer_email='x@example.com')
        self.prepare('renovar')
        self.assertEqual(self.change('renovar').status_code, 200)

        with self.assertRaises(checkout.CheckoutError):
            checkout.start_payment_attempt(order, credentials=credentials)
        self.assertFalse(PaymentTransaction.objects.exists())


@skipUnless(connection.vendor == 'postgresql', 'several writers at once need a database that takes them')
@override_settings(APP_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode(), **{**EMPTY_ENV, **MCW_SETTINGS})
class AtTheSameInstantTest(TransactionTestCase):
    """Lo de verdad: un checkout y un cambio a la vez, con sus propias conexiones y sus commits."""

    def setUp(self):
        cache.clear()
        self.master = get_user_model().objects.create_superuser('master', 'master@example.com', 'x')
        self.order = Order.objects.create(
            company=Company.objects.create(name='Tienda a la vez', slug='tienda-a-la-vez'),
            total=Decimal('10.00'), customer_email='x@example.com')
        provider = registry.get('izipay_checkout')
        draft = service.save_draft(provider, None, actor=self.master, public=CHECKOUT_PUBLIC, secrets=CHECKOUT_SECRETS)
        IntegrationConfig.objects.filter(pk=draft.pk).update(
            validated=True, last_test_status='ok', last_tested_at=timezone.now())
        self.provider, self.version = provider, draft.version

    def test_a_change_waits_for_the_checkout_that_is_writing_its_attempt_and_then_sees_it(self):
        from store.payments import micuentaweb

        writing, release, outcome = threading.Event(), threading.Event(), {}
        credentials = micuentaweb.load_credentials()

        def still_there(_credentials):
            # El checkout ya tiene el bloqueo y está a punto de escribir su intento.
            writing.set()
            release.wait(timeout=20)
            return True

        def checkout_in_the_middle():
            try:
                with mock.patch.object(payment_adapters, 'still_charging', side_effect=still_there):
                    checkout._open_attempt(
                        self.order, credentials, provider='micuentaweb',
                        transaction_id='50000000000000000001', order_number='50000000000000000001')
            except Exception as exc:  # noqa: BLE001
                outcome['buyer'] = exc
            finally:
                connection.close()

        def change_of_gateway():
            try:
                service.activate(self.provider, None, actor=self.master, version=self.version)
                outcome['result'] = 'changed'
            except registry.ChangeBlocked as blocked:
                outcome['result'] = blocked
            except Exception as exc:  # noqa: BLE001
                outcome['result'] = exc
            finally:
                outcome['at'] = timezone.now()
                connection.close()

        buyer = threading.Thread(target=checkout_in_the_middle)
        master = threading.Thread(target=change_of_gateway)
        buyer.start()
        self.assertTrue(writing.wait(timeout=20))
        master.start()
        master.join(timeout=1.5)
        # El intento aún no está confirmado en la base: el cambio no puede decidir sin él, y espera.
        self.assertTrue(master.is_alive(), outcome)
        released_at = timezone.now()
        release.set()
        buyer.join(timeout=20)
        master.join(timeout=20)

        self.assertNotIn('buyer', outcome)
        self.assertEqual(PaymentTransaction.objects.get().environment, 'test')
        self.assertIsInstance(outcome['result'], registry.ChangeBlocked, outcome)
        self.assertGreaterEqual(outcome['at'], released_at)
        self.assertEqual(payment_adapters.active_code(), 'micuentaweb')
        self.assertFalse(IntegrationConfig.objects.filter(slot='active').exists())
