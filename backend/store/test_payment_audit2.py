"""
PAGOS · lo que encontró la segunda auditoría independiente.

  1. Las claves de TEST no son para compradores, vengan de donde vengan: de la
     consola o de las variables del servidor. Sólo un interruptor explícito,
     apagado por defecto, abre los pagos de TEST a cualquiera — para las
     instalaciones donde la pasarela simulada es justo lo que se prueba.
     Y revocar la configuración de la consola no pone a cobrar, sin decirlo, a
     las claves de las variables del servidor.
  3. El propietario puede dejar de abrir pagos nuevos en el acto, sin palabra y
     sin que un comprador pueda impedirlo, mientras los ya abiertos se siguen
     confirmando. Con los pagos en pausa, apagar, revocar o cambiar de pasarela
     llega a poder hacerse.
  2 y 4. «No se pudo preguntar» —la pasarela no responde, rechaza a quien llama,
     no hay credenciales con que preguntar, o contesta con un error— lleva a la
     palabra y a la auditoría, sean uno o veinte los cobros abiertos.
  6. No se pregunta a una tienda por los intentos que se abrieron con otra.
  7. Lo que nadie intentó pagar se cierra al final de su ventana, no antes.
  8. Una parte de cada pasada es para quien lleva más tiempo sin respuesta.
 13. La consulta previa a un cambio tiene un tiempo máximo.
 15. Lo que se le dice a quien opera dice lo que pasó.
  5 y 12. La app no puede multiplicar las consultas a la pasarela, y se le dice
     por qué no recibe un formulario. Un cambio de pasarela no falla un pedido.
  9, 10 y 11. Un rechazo deja rastro sin cerrar el intento; la tienda se compara
     como se guardó; las credenciales del otro producto tampoco se imprimen.
"""
import io
import os
import subprocess
import sys
import threading
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest import mock, skipUnless
from urllib.parse import urlencode

from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from store import checkout_services as checkout
from store.integrations import payments as payment_adapters
from store.integrations import registry, service
from store.models import AdminAuditLog, Company, IntegrationConfig, Order, PaymentTransaction
from store.payments import micuentaweb, reconcile
from store.payments.fake_izipay import FakeIzipay
from store.payments.fake_micuentaweb import FakeMiCuentaWeb
from store.test_integrations_payments import EMPTY_ENV, MCW_PUBLIC, fake_mcw
from store.test_integrations_payments import _Base as ConsoleBase
from store.test_micuentaweb import IPN_URL, MCW_SETTINGS, McwBase
from store.test_ops_status import run as health_check
from store.test_payment_change_guard import GuardBase
from store.test_payment_hardening import Base as Hardening
from store.test_payment_reconcile import PRODUCTION
from store.test_payment_test_access import NOT_YET
from store.tests import IZIPAY_TEST_SETTINGS, M5CheckoutBase, _m5_checkout_url, _pilot_company

BACKEND = Path(__file__).resolve().parents[1]


# -- 1 · the setting ---------------------------------------------------------------------

class TheSwitchTest(SimpleTestCase):
    def test_it_is_off_unless_the_server_says_otherwise(self):
        """Lo que vale en un servidor: el valor del módulo de ajustes sin la variable, leído en otro proceso."""
        environment = {k: v for k, v in os.environ.items() if k != 'PAYMENTS_PUBLIC_TEST'}
        code = ('import django; django.setup(); from django.conf import settings; '
                'print(settings.PAYMENTS_PUBLIC_TEST)')
        for value, expected in ((None, 'False'), ('1', 'True'), ('', 'False')):
            with self.subTest(value):
                env = dict(environment, DJANGO_SETTINGS_MODULE='backend.settings')
                if value is not None:
                    env.update(PAYMENTS_PUBLIC_TEST=value)
                out = subprocess.run([sys.executable, '-c', code], cwd=BACKEND, env=env, capture_output=True, text=True)
                self.assertEqual(out.stdout.strip().splitlines()[-1:], [expected], out.stderr[-400:])

    def test_the_test_suite_turns_it_on_in_one_place(self):
        """Las pruebas cobran con pasarelas simuladas: ahí, y sólo ahí, está encendido para todos."""
        self.assertIs(settings.PAYMENTS_PUBLIC_TEST, True)
        self.assertEqual(settings.TEST_RUNNER, 'store.suite_runner.SimulatedGatewayRunner')
        turned_on = ('PAYMENTS_PUBLIC_TEST' + ' = True', "PAYMENTS_PUBLIC_TEST'" + '] = ', "'PAYMENTS_PUBLIC_TEST'" + ': True')
        mentions = [path.name for path in (BACKEND / 'store').rglob('*.py')
                    if any(needle in path.read_text(encoding='utf-8') for needle in turned_on)]
        self.assertEqual(mentions, ['suite_runner.py'])


@override_settings(PAYMENTS_PUBLIC_TEST=False)
class TestKeysFromTheEnvironmentAreNotForBuyersEitherTest(McwBase):
    """Una tienda publicada (interruptor apagado) con claves de TEST en las variables del servidor."""

    def assert_refused_before_anything(self, response, fake):
        self.assertEqual(response.status_code, 503, response.data)
        self.assertEqual(response.data, {'detail': NOT_YET, 'code': 'payment_test_only'})
        self.assertFalse(Order.objects.exists())
        self.assertFalse(PaymentTransaction.objects.exists())
        self.assertEqual(fake.requests, [])

    def test_a_buyer_is_refused_before_the_gateway_hears_anything(self):
        self.assert_refused_before_anything(self.checkout(), self.fake)

    def test_with_the_other_product_too(self):
        with override_settings(**IZIPAY_TEST_SETTINGS):
            self.fake = FakeIzipay.from_settings(IZIPAY_TEST_SETTINGS)
            self.assert_refused_before_anything(self.checkout(), self.fake)

    def test_a_master_can_still_try_the_gateway(self):
        self.client.force_authenticate(get_user_model().objects.create_superuser('master', 'm@example.com', 'x'))

        self.assertEqual(self.checkout().status_code, 200)

    def test_production_keys_in_the_environment_charge_anybody(self):
        with override_settings(**PRODUCTION):
            self.fake = type(self.fake).from_settings(PRODUCTION)
            response = self.checkout()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(PaymentTransaction.objects.get().environment, 'production')

    def test_an_environment_word_nobody_knows_is_test(self):
        """Sólo «production» es dinero. Cualquier otra palabra, también una mal escrita, es TEST."""
        for word in ('test', 'sandbox', 'staging', '', 'Production', 'prod'):
            with self.subTest(word):
                credentials = micuentaweb.MiCuentaWebCredentials(
                    environment=word, shop_id='1', password='x', public_key='', api_url='', currency='PEN', ipn_url='')
                self.assertEqual(payment_adapters.environment_of(credentials), 'test')
                self.assertTrue(payment_adapters.test_keys_not_for_buyers(credentials))
        production = micuentaweb.MiCuentaWebCredentials(
            environment='production', shop_id='1', password='x', public_key='', api_url='', currency='PEN', ipn_url='')
        self.assertEqual(payment_adapters.environment_of(production), 'production')
        self.assertFalse(payment_adapters.test_keys_not_for_buyers(production))


@override_settings(PAYMENTS_PUBLIC_TEST=True)
class WhereASimulatedGatewayIsThePointTest(ConsoleBase):
    """Con el interruptor encendido (pruebas, ensayo, desarrollo) cualquiera paga en TEST, venga de donde venga."""

    def test_a_buyer_pays_with_test_keys_of_the_console(self):
        self.fake = self.go_live_with_mcw()
        self.client = APIClient()

        self.assertEqual(self.checkout().status_code, 200)

    def test_and_with_test_keys_of_the_environment(self):
        with override_settings(**MCW_SETTINGS):
            self.fake = type(self.fake).from_settings(MCW_SETTINGS)
            self.assertEqual(self.checkout().status_code, 200)


@override_settings(PAYMENTS_PUBLIC_TEST=False, **MCW_SETTINGS)
class AFreshOrderFromTheAppTest(M5CheckoutBase):
    def test_the_app_is_refused_the_same_way_before_an_order_exists(self):
        response = self.client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')

        self.assertEqual(response.status_code, 503, response.content)
        self.assertEqual(response.json(), {'detail': NOT_YET, 'code': 'payment_test_only'})
        self.assertFalse(Order.objects.exists())


# -- 1 · revoking the console configuration ------------------------------------------------

@override_settings(PAYMENTS_PUBLIC_TEST=False)
class RevokingDoesNotHandTheShopToTheEnvironmentTest(ConsoleBase):
    """
    Con una fila activa en la consola manda la consola. Si se revoca la última,
    volverían a mandar las variables del servidor: otra pasarela cobrando sin
    que nadie la haya activado ni escrito PRODUCCION.
    """

    def revoke(self, provider='izipay_micuentaweb', **words):
        return self.console.post(self.url(provider, 'revoke'), {'confirm': 'REVOCAR', **words}, format='json')

    def test_it_is_refused_with_its_reason_whichever_environment_the_server_keys_are(self):
        self.go_live_with_mcw()
        for name, environment in (('TEST', MCW_SETTINGS), ('PRODUCCIÓN', PRODUCTION)):
            with self.subTest(name), override_settings(**environment):
                before = list(IntegrationConfig.objects.values_list('provider', 'slot', 'enabled', 'version'))
                for words in ({}, {'acknowledge': 'INTERRUMPIR'}):
                    response = self.revoke(**words)
                    self.assertEqual(response.status_code, 409, response.content)
                    body = response.json()
                    self.assertEqual(body['code'], 'environment_fallback')
                    self.assertIn('variables del servidor', body['detail'])
                    self.assertIn(name, body['detail'])
                    self.assertNotIn(environment['MICUENTAWEB_PASSWORD'], response.content.decode())
                self.assertEqual(
                    list(IntegrationConfig.objects.values_list('provider', 'slot', 'enabled', 'version')), before)
                self.assertEqual(checkout.active_payment_provider(), 'micuentaweb')

    def test_a_configuration_that_is_switched_off_is_not_a_way_round(self):
        """Apagada no cobra nadie; revocarla dejaría cobrando a las variables del servidor."""
        self.go_live_with_mcw()
        self.assertEqual(self.console.post(self.url('izipay_micuentaweb', 'disable'), {}, format='json').status_code, 200)
        with override_settings(**IZIPAY_TEST_SETTINGS):
            self.assertEqual(checkout.active_payment_provider(), '')
            response = self.revoke()
            self.assertEqual(response.status_code, 409, response.content)
            self.assertEqual(checkout.active_payment_provider(), '')

    def test_after_it_nobody_pays_in_test_through_the_server_keys(self):
        """La sonda de la auditoría: antes del intento de revocar, 503; después, también."""
        self.fake = self.go_live_with_mcw()
        with override_settings(**MCW_SETTINGS):
            self.revoke()
            IntegrationConfig.objects.all().delete()            # y aunque la consola quedara vacía por otra vía
            self.fake = type(self.fake).from_settings(MCW_SETTINGS)
            self.client = APIClient()
            self.assertEqual(self.checkout().status_code, 503)
        self.assertFalse(Order.objects.exists())

    def test_with_nothing_usable_in_the_environment_revoking_is_what_it_was(self):
        self.go_live_with_mcw()
        self.assertEqual(self.revoke().status_code, 200)
        self.assertFalse(IntegrationConfig.objects.exists())

    def test_while_another_console_configuration_remains_the_console_still_decides(self):
        self.go_live_with_checkout()
        self.go_live_with_mcw()                                   # el otro queda apagado, con su fila
        with override_settings(**IZIPAY_TEST_SETTINGS):
            self.assertEqual(self.revoke('izipay_checkout').status_code, 200)
            self.assertEqual(checkout.active_payment_provider(), 'micuentaweb')

    def test_throwing_away_a_draft_is_not_revoking_what_decides(self):
        with override_settings(**MCW_SETTINGS):
            self.assertEqual(self.save('izipay_checkout', {
                'environment': 'sandbox', 'merchant_code': '9001001', 'public_key': 'x',
                'token_url': 'https://izipay.invalid/token', 'currency': 'PEN'}, {'api_key': 'a', 'hash_key': 'h'}).status_code, 200)
            self.assertEqual(self.revoke('izipay_checkout').status_code, 200)


assert EMPTY_ENV      # the console base starts from an environment with nothing usable


# -- 3 · pausing new payments ---------------------------------------------------------------

Status = PaymentTransaction.Status
PROD_SECRETS = {'password': 'prodpassword_DelPanelNoEsRealNoEsRealNoEsReal'}
PROD_PUBLIC = {**MCW_PUBLIC, 'public_key': '90000001:publickey_DelPanelNoEsRealNoEsReal'}


@override_settings(PAYMENTS_PUBLIC_TEST=False)
class PausingNewPaymentsTest(GuardBase):
    def pause(self):
        with self.gateway.online():
            return self.console.post(self.url('izipay_micuentaweb', 'pause'), {}, format='json')

    def resume(self, **words):
        return self.console.post(self.url('izipay_micuentaweb', 'resume'), words, format='json')

    def as_a_buyer(self):
        self.client = APIClient()
        self.fake = self.gateway
        return self.checkout()

    def test_one_action_no_word_whatever_is_open_and_without_asking_the_gateway(self):
        self.open_attempt(minutes=1)
        self.gateway.mode = 'unreachable'

        response = self.pause()

        self.assertEqual(response.status_code, 200, response.content)
        detail = response.json()
        self.assertTrue(detail['pausable'])
        self.assertTrue(detail['active']['paused'])
        self.assertTrue(detail['active']['enabled'])
        self.assertEqual(self.gateway.requests, [])
        log = AdminAuditLog.objects.get(action='integration_paused')
        self.assertEqual((log.actor, log.company_id, log.target_id), (self.master, None, 'izipay_micuentaweb'))
        self.assertFalse(AdminAuditLog.objects.filter(action='payments_interrupted').exists())

    def test_while_paused_nobody_opens_a_payment_not_even_the_master(self):
        self.pause()
        self.gateway.requests.clear()
        for buyer in (None, self.master):
            with self.subTest(buyer):
                self.client = APIClient()
                if buyer:
                    self.client.force_authenticate(buyer)
                self.fake = self.gateway
                response = self.checkout()
                self.assertEqual(response.status_code, 503, response.data)
                self.assertEqual(response.data, {'detail': NOT_YET, 'code': 'payment_paused'})
        self.assertFalse(Order.objects.exists())
        self.assertEqual(self.gateway.requests, [])

    def test_a_checkout_that_started_before_the_pause_writes_no_attempt_after_it(self):
        credentials = checkout.require_payment_provider_configured(self.master)
        order = Order.objects.create(company=_pilot_company(), total=Decimal('10.00'), customer_email='x@example.com')
        self.pause()

        with self.assertRaises(checkout.CheckoutError) as refused, self.gateway.online():
            checkout.start_payment_attempt(order, credentials=credentials)

        self.assertEqual((refused.exception.status_code, refused.exception.code), (503, 'payment_paused'))
        self.assertFalse(PaymentTransaction.objects.exists())

    def test_what_was_already_open_is_still_confirmed_by_its_notification(self):
        attempt = self.open_attempt(minutes=5)
        self.pause()

        form = self.gateway.ipn(self.gateway.answer(order_id=attempt.transaction_id, amount_cents=29800))
        response = APIClient().post(IPN_URL, data=urlencode(form), content_type='application/x-www-form-urlencoded')

        self.assertEqual(response.status_code, 200, response.content)
        attempt.order.refresh_from_db()
        self.assertTrue(attempt.order.paid)

    def test_and_by_asking_the_gateway(self):
        attempt = self.open_attempt(minutes=5)
        self.pause()
        self.gateway.hold(attempt.transaction_id, self.held(attempt))

        with self.gateway.online():
            summary = reconcile.reconcile_micuentaweb()

        self.assertEqual(summary.paid, 1)
        attempt.order.refresh_from_db()
        self.assertTrue(attempt.order.paid)

    def test_a_buyer_who_keeps_opening_checkouts_cannot_keep_the_owner_from_stopping(self):
        """
        La sonda de la auditoría: un checkout cada 29 minutos renovaba la espera
        para siempre. Con los pagos en pausa ya no entra ninguno, los abiertos
        se agotan, y el cambio se hace.
        """
        self.prepare('otro entorno')
        self.assertEqual(self.change('otro entorno').status_code, 200)             # claves de PRODUCCIÓN: paga cualquiera
        self.gateway = fake_mcw(PROD_PUBLIC, PROD_SECRETS)
        self.assertEqual(self.as_a_buyer().status_code, 200)
        self.assertEqual(self.change('apagar', acknowledge='INTERRUMPIR').status_code, 409)
        PaymentTransaction.objects.update(created_at=timezone.now() - timedelta(minutes=29))
        self.assertEqual(self.as_a_buyer().status_code, 200)                        # otra vez, y otra…
        self.assertEqual(self.change('apagar', acknowledge='INTERRUMPIR').status_code, 409)

        self.assertEqual(self.pause().status_code, 200)

        self.assertEqual(self.as_a_buyer().status_code, 503)
        self.assertEqual(self.change('apagar').status_code, 409)                     # los abiertos aún pueden cobrarse
        PaymentTransaction.objects.update(created_at=timezone.now() - timedelta(minutes=31))
        self.assertEqual(self.as_a_buyer().status_code, 503)
        for name in ('apagar', 'revocar'):
            with self.subTest(name):
                response = self.change(name)
                self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(AdminAuditLog.objects.filter(action='payments_interrupted').exists())

    def test_resuming_opens_payments_again_and_asks_what_activating_asked(self):
        self.pause()
        self.assertEqual(self.resume().status_code, 200)                             # TEST: no hay palabra
        self.assertFalse(self.console.get(self.url('izipay_micuentaweb')).json()['active']['paused'])
        self.assertTrue(AdminAuditLog.objects.filter(action='integration_resumed').exists())

        self.prepare('otro entorno')
        self.assertEqual(self.change('otro entorno').status_code, 200)
        self.pause()
        refused = self.resume()
        self.assertEqual(refused.status_code, 400)
        self.assertIn('confirm', refused.json()['errors'])
        self.assertEqual(self.resume(confirm='PRODUCCION').status_code, 200)
        self.gateway = fake_mcw(PROD_PUBLIC, PROD_SECRETS)
        self.assertEqual(self.as_a_buyer().status_code, 200)

    def test_renewing_the_keys_while_paused_does_not_resume_by_itself(self):
        self.pause()
        self.prepare('renovar')

        self.assertEqual(self.change('renovar').status_code, 200)

        self.assertTrue(self.console.get(self.url('izipay_micuentaweb')).json()['active']['paused'])
        self.assertEqual(self.as_a_buyer().status_code, 503)

    def test_switching_back_on_what_was_off_starts_unpaused(self):
        self.pause()
        self.assertEqual(self.change('apagar').status_code, 200)
        self.console.post(self.url('izipay_micuentaweb', 'enable'), {}, format='json')

        self.assertFalse(self.console.get(self.url('izipay_micuentaweb')).json()['active']['paused'])

    def test_what_is_switched_off_is_not_paused(self):
        self.assertEqual(self.change('apagar').status_code, 200)
        self.assertEqual(self.pause().status_code, 404)

    def test_only_what_charges_from_the_console_can_be_paused(self):
        self.assertEqual(self.console.post(self.url('izipay_checkout', 'pause'), {}, format='json').status_code, 404)
        self.assertEqual(self.console.post(self.url('smtp', 'pause'), {}, format='json').status_code, 404)
        self.assertFalse(self.console.get(self.url('smtp')).json()['pausable'])
        anonymous = APIClient().post(self.url('izipay_micuentaweb', 'pause'), {}, format='json')
        self.assertIn(anonymous.status_code, (401, 403))

    def test_what_does_not_declare_it_cannot_be_paused_even_when_it_runs(self):
        """El correo corre y no «abre operaciones nuevas»: pausarlo no significa nada, y no se hace."""
        row = IntegrationConfig.objects.create(provider='smtp', slot='active', enabled=True, version=1)

        response = self.console.post(self.url('smtp', 'pause'), {}, format='json')

        self.assertEqual(response.status_code, 404)
        row.refresh_from_db()
        self.assertFalse(row.paused)

    def test_the_health_check_says_payments_are_paused(self):
        self.assertNotIn('en pausa', health_check()[1])
        self.pause()

        code, text = health_check()

        self.assertIn('NOTA  pagos: los pagos nuevos están en pausa', text)


# -- 2 and 4 · «could not ask» -----------------------------------------------------------------

class CouldNotAskTest(GuardBase):
    def setUp(self):
        super().setUp()
        self.first = self.open_attempt(minutes=50)
        self.second = self.open_attempt(minutes=45)

    def assert_asks_for_the_word_and_then_records_both(self, reason, **around):
        before = self.snapshot()
        with self.assertLogs('store', level='WARNING'):
            refused = self.change('apagar')
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertIn('INTERRUMPIR', refused.json()['errors']['acknowledge'])
        self.assertIn('2 cobros abiertos', refused.json()['errors']['acknowledge'])
        self.assertEqual(self.snapshot(), before)

        with self.assertLogs('store', level='WARNING'):
            allowed = self.change('apagar', acknowledge='INTERRUMPIR')
        self.assertEqual(allowed.status_code, 200, allowed.content)
        log = AdminAuditLog.objects.get(action='payments_interrupted')
        self.assertEqual(log.metadata['reason'], reason)
        self.assertEqual(log.metadata['attempts'], [self.first.pk, self.second.pk])

    def test_a_gateway_that_is_down_with_more_than_one_attempt_open(self):
        self.gateway.mode = 'unreachable'
        self.assert_asks_for_the_word_and_then_records_both('gateway_unreachable')

    def test_a_gateway_that_refuses_the_caller(self):
        for mode in ('http_401', 'int_error'):
            with self.subTest(mode):
                AdminAuditLog.objects.filter(action='payments_interrupted').delete()
                self.reset_console()
                self.gateway.mode = mode
                self.assert_asks_for_the_word_and_then_records_both('gateway_unreachable')

    def test_credentials_that_cannot_be_loaded(self):
        with mock.patch.object(micuentaweb, 'load_credentials',
                               side_effect=micuentaweb.MiCuentaWebError('almacén de secretos no disponible')):
            with self.assertLogs('store', level='ERROR'):
                refused = self.change('apagar')
            self.assertEqual(refused.status_code, 400, refused.content)
            self.assertIn('acknowledge', refused.json()['errors'])
            with self.assertLogs('store', level='ERROR'):
                self.assertEqual(self.change('apagar', acknowledge='INTERRUMPIR').status_code, 200)
        self.assertEqual(AdminAuditLog.objects.get(action='payments_interrupted').metadata['attempts'],
                         [self.first.pk, self.second.pk])

    def test_an_error_answer_about_references_past_their_form(self):
        """
        Nadie ha visto qué contesta la pasarela real de una referencia sin
        usar. Si es un error, no puede bloquear dos horas cada cambio: es «no se
        pudo preguntar», con palabra y registro.
        """
        self.gateway.order_get_mode = 'error'
        self.gateway.order_get_error = 'PSP_010'
        self.assert_asks_for_the_word_and_then_records_both('gateway_error')

    def test_none_of_it_gets_past_an_attempt_that_can_still_charge(self):
        self.open_attempt(minutes=10)
        for setting in ({'mode': 'unreachable'}, {'mode': 'http_401'}, {'order_get_mode': 'error'}):
            with self.subTest(str(setting)):
                self.gateway.mode, self.gateway.order_get_mode = 'ok', 'ok'
                for name, value in setting.items():
                    setattr(self.gateway, name, value)
                before = self.snapshot()
                with self.assertLogs('store', level='WARNING'):
                    response = self.change('apagar', acknowledge='INTERRUMPIR')
                self.assert_blocked(response, before, can_still_charge=1)

    def test_nor_past_one_the_gateway_holds_unresolved(self):
        self.gateway.hold(self.first.transaction_id, self.held(self.first, status='RUNNING'))
        self.gateway.order_get_mode = 'ok'
        self.gateway.mode = 'ok'
        before = self.snapshot()
        self.assert_blocked(self.change('apagar', acknowledge='INTERRUMPIR'), before, unresolved=1)

    def test_the_window_of_this_product_is_two_hours(self):
        """Un cobro en curso de hace hora y media sigue contando: su notificación aún puede llegar."""
        PaymentTransaction.objects.filter(pk=self.first.pk).update(created_at=timezone.now() - timedelta(minutes=95))
        self.gateway.hold(self.first.transaction_id, self.held(self.first, status='RUNNING'))
        before = self.snapshot()
        self.assert_blocked(self.change('apagar'), before, unresolved=1)


class TheQuestionBeforeAChangeHasATimeLimitTest(GuardBase):
    def test_what_is_not_asked_in_time_is_not_verified_and_the_change_is_not_made(self):
        from store.integrations.providers import payments as payment_providers

        for minutes in (50, 48, 46, 44):
            self.open_attempt(minutes=minutes)
        ticks = iter(range(0, 10_000, 8))                   # cada mirada al reloj, ocho segundos después
        before = self.snapshot()
        with mock.patch.object(reconcile, '_clock', side_effect=lambda: next(ticks)):
            response = self.change('apagar', acknowledge='INTERRUMPIR')

        self.assertEqual(payment_providers.GUARD_BUDGET, timedelta(seconds=20))
        self.assert_blocked(response, before, open=4, can_still_charge=0)
        self.assertLess(len(self.asked()), 4)
        self.assertGreater(len(self.asked()), 0)

    def test_a_scheduled_run_has_no_such_limit(self):
        attempts = [self.open_attempt(minutes=m) for m in (50, 48, 46, 44)]
        ticks = iter(range(0, 10_000, 8))
        with mock.patch.object(reconcile, '_clock', side_effect=lambda: next(ticks)), self.gateway.online():
            summary = reconcile.reconcile_micuentaweb()

        self.assertEqual((summary.asked, summary.left), (len(attempts), 0))


# -- 6, 7 and 8 · who is asked, and when an attempt is closed -------------------------------------

class AttemptsOfAnotherShopOrEnvironmentTest(Hardening):
    def foreign(self, **origin):
        PaymentTransaction.objects.filter(pk=self.attempt.pk).update(**origin)
        self.attempt.refresh_from_db()

    def test_they_are_not_asked_about_and_stay_open_for_somebody_to_review(self):
        for origin in ({'shop': '11111111'}, {'environment': 'production'}):
            with self.subTest(str(origin)):
                PaymentTransaction.objects.filter(pk=self.attempt.pk).update(shop='69876357', environment='test')
                self.foreign(**origin)
                self.aged(minutes=115)                      # ni al final de su ventana se cierra
                self.fake.requests.clear()

                summary = self.ask()

                self.assertEqual((summary.asked, summary.foreign, summary.abandoned), (0, 1, 0))
                self.assertEqual(self.asked(), [])
                self.assertIn('de otra tienda o entorno: 1', summary.line())
                self.assert_still_waiting()

    def test_asked_by_reference_it_says_so_instead_of_asking_the_wrong_account(self):
        self.foreign(shop='11111111')
        out = io.StringIO()
        with self.fake.online():
            call_command('reconcile_payments', '--reference', self.attempt.transaction_id, stdout=out)

        self.assertIn('se abrió con otra tienda o en otro entorno', out.getvalue())
        self.assertEqual(self.asked(), [])

    def test_the_health_check_says_they_are_there(self):
        self.assertNotIn('otra tienda', health_check()[1])
        self.foreign(environment='production')

        self.assertIn('ATENCIÓN pagos: 1 pago abierto con otra tienda o en otro entorno', health_check()[1])

    def test_an_attempt_older_than_the_columns_is_asked_about_as_before(self):
        self.foreign(shop='', environment='')
        self.assertEqual(self.ask().asked, 1)


class ForeignAttemptsDoNotHoldAChangeUpTest(GuardBase):
    def test_an_attempt_of_a_previous_shop_is_not_this_gateways_to_wait_for(self):
        attempt = self.open_attempt(minutes=10)
        PaymentTransaction.objects.filter(pk=attempt.pk).update(shop='11111111')

        self.assertEqual(self.change('apagar').status_code, 200)
        self.assertEqual(self.asked(), [])


class ClosedAtTheEndOfItsWindowTest(Hardening):
    def test_an_empty_answer_after_the_form_is_gone_does_not_close_it_yet(self):
        self.aged(minutes=45)

        summary = self.ask()

        self.assertEqual((summary.abandoned, summary.waiting), (0, 1))
        self.assert_still_waiting()
        # …y se sigue preguntando por él: un medio de pago lento puede aparecer después.
        self.fake.hold(self.attempt.transaction_id, self.paid())
        self.assertEqual(self.ask().paid, 1)

    def test_it_is_closed_in_the_last_runs_of_its_window(self):
        self.assertEqual(reconcile.CLOSING, timedelta(minutes=10))
        for minutes, closed in ((105, False), (112, True)):
            with self.subTest(minutes):
                PaymentTransaction.objects.filter(pk=self.attempt.pk).update(status=Status.PENDING)
                self.aged(minutes=minutes)
                self.assertEqual(self.ask().abandoned, int(closed))
                self.refresh()
                self.assertEqual(self.attempt.status == Status.ABANDONED, closed)

    def test_asked_by_reference_long_after_it_is_closed_too(self):
        self.aged(hours=50)

        summary = self.ask(reference=self.attempt.transaction_id)

        self.assertEqual(summary.abandoned, 1)


class APartOfEachRunForWhoeverWaitedLongestTest(Hardening):
    def test_twenty_five_new_attempts_do_not_keep_out_the_one_asked_longest_ago(self):
        """Los nunca consultados van primero, pero no se llevan la pasada entera."""
        self.aged(minutes=100)
        PaymentTransaction.objects.filter(pk=self.attempt.pk).update(
            gateway_checked_at=timezone.now() - timedelta(minutes=30))
        self.fake.hold(self.attempt.transaction_id, self.paid())
        for n in range(25):
            reference = f'700000000000000000{n:02d}'
            extra = PaymentTransaction.objects.create(
                order=self.order, provider='micuentaweb', transaction_id=reference, order_number=reference,
                amount=Decimal('298.00'), currency='PEN')
            self.aged(extra, minutes=5 + n)

        summary = self.ask()

        self.assertEqual(summary.asked, 20)
        self.assertEqual(reconcile.RESERVED, 5)
        self.refresh()
        self.assertTrue(self.order.paid)

    def test_an_error_answer_also_sends_the_attempt_to_the_back_of_the_queue(self):
        self.fake.order_get_mode = 'error'
        with self.assertLogs('store', level='WARNING'):
            self.ask()

        self.refresh()
        self.assertIsNotNone(self.attempt.gateway_checked_at)


class WhatIsNotAskedAboutAgainTest(Hardening):
    def test_a_duplicate_charge_is_not_asked_about_by_reference(self):
        PaymentTransaction.objects.filter(pk=self.attempt.pk).update(status=Status.DUPLICATE)

        self.assertEqual(self.ask(reference=self.attempt.transaction_id).asked, 0)
        self.assertEqual(self.asked(), [])

    def test_a_second_charge_on_an_order_that_was_paid_and_refunded_is_a_duplicate_too(self):
        """El pedido ya no dice «pagado» (se reembolsó) pero se pagó: otro cobro no lo vuelve a pagar."""
        second = self.another_attempt()
        self.notify()
        Order.objects.filter(pk=self.order.pk).update(status=Order.Status.REFUNDED, paid=True)

        with self.assertLogs('store.views', level='ERROR'):
            self.notify(order_id=second.transaction_id, uuid='c' * 32)

        second.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(second.status, Status.DUPLICATE)
        self.assertEqual(self.order.status, Order.Status.REFUNDED)


# -- 15 · what the operator is told -----------------------------------------------------------------

class WhatTheOperatorIsToldTest(Hardening):
    def by_reference(self):
        out = io.StringIO()
        with self.fake.online():
            call_command('reconcile_payments', '--reference', self.attempt.transaction_id, stdout=out)
        return out.getvalue()

    def test_a_reference_the_gateway_knows_nothing_of(self):
        for status in (Status.PENDING, Status.ABANDONED, Status.REJECTED):
            with self.subTest(status):
                PaymentTransaction.objects.filter(pk=self.attempt.pk).update(status=status)
                self.aged(hours=50)
                text = self.by_reference()
                self.assertIn('La pasarela no tiene ningún intento de pago bajo esa referencia', text)
                self.assertNotIn('siguen esperando', text)

    def test_a_reference_with_the_other_product_charging(self):
        with override_settings(**IZIPAY_TEST_SETTINGS):
            text = self.by_reference()

        self.assertIn('«Mi Cuenta Web» no es la pasarela que cobra ahora', text)
        self.assertNotIn('No hay ningún intento', text)
        self.assertEqual(self.fake.requests, [])

    def test_a_reference_that_was_paid_refused_or_is_still_open_there(self):
        cases = {
            'El pedido quedó pagado': [self.paid()],
            'La pasarela lo tiene como rechazado': [self.paid(status='UNPAID')],
            'La pasarela todavía lo tiene en curso': [self.paid(status='RUNNING')],
            'no se puede decidir': [self.paid(uuid='a' * 32), self.paid(uuid='b' * 32)],
        }
        self.aged(hours=50)
        for sentence, transactions in cases.items():
            with self.subTest(sentence):
                PaymentTransaction.objects.filter(pk=self.attempt.pk).update(status=Status.REJECTED)
                Order.objects.filter(pk=self.order.pk).update(status=Order.Status.PENDING_PAYMENT, paid=False)
                self.fake.hold(self.attempt.transaction_id, *transactions)
                self.assertIn(sentence, self.by_reference())

    def test_an_answer_that_cannot_be_read_is_not_called_two_charges_or_a_refund(self):
        """Otra tienda en la respuesta, un formato inesperado: no es «dos cobros, un reembolso o un importe parcial»."""
        broken = self.paid()
        del broken['orderDetails']
        self.fake.hold(self.attempt.transaction_id, broken)
        with self.assertLogs('store', level='WARNING'):
            summary = self.ask()

        self.assertEqual((summary.undecided, summary.unreadable), (0, 1))
        self.refresh()
        self.assertTrue(self.attempt.failure_reason.startswith('unreadable: '))
        log = AdminAuditLog.objects.get(action='payment_undecided')
        self.assertEqual(log.metadata['kind'], 'unreadable')
        text = health_check()[1]
        self.assertIn('ATENCIÓN pagos: 1 pago con una respuesta de la pasarela que no se pudo leer', text)
        self.assertNotIn('dos cobros', text)

    def test_two_charges_under_one_reference_are_still_called_that(self):
        self.fake.hold(self.attempt.transaction_id, self.paid(uuid='a' * 32), self.paid(uuid='b' * 32))
        with self.assertLogs('store', level='WARNING'):
            summary = self.ask()

        self.assertEqual((summary.undecided, summary.unreadable), (1, 0))
        self.assertEqual(AdminAuditLog.objects.get(action='payment_undecided').metadata['kind'], 'undecided')
        self.assertIn('dos cobros, un reembolso o un importe parcial', health_check()[1])


# -- 5 and 12 · the app ---------------------------------------------------------------------------

@override_settings(**MCW_SETTINGS)
class TheAppCannotFanOutTest(M5CheckoutBase):
    def setUp(self):
        super().setUp()
        self.fake = FakeMiCuentaWeb.from_settings(MCW_SETTINGS)

    def post(self, **overrides):
        with self.fake.online():
            return self.client.post(_m5_checkout_url('m5-shop'), self.body(**overrides), format='json')

    def asked(self):
        return [r for r in self.fake.requests if r['url'].endswith('/Order/Get')]

    def test_a_signed_in_customer_has_a_limit_of_their_own(self):
        from store.throttles import CheckoutThrottle, CheckoutUserThrottle
        from store.v1_checkout_views import V1CustomerCheckoutView

        self.assertEqual(V1CustomerCheckoutView.throttle_classes, [CheckoutThrottle, CheckoutUserThrottle])
        self.assertEqual(settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'][CheckoutUserThrottle.scope], '10/min')
        statuses = [self.post().status_code for _ in range(12)]
        self.assertEqual(statuses[-1], 429)
        self.assertLessEqual(len([code for code in statuses if code != 429]), 10)

    def test_an_order_does_not_collect_more_than_three_open_forms(self):
        for _ in range(3):
            self.assertIsNotNone(self.post().json()['payment'])
        self.assertEqual((PaymentTransaction.objects.count(), len(self.asked())), (3, 3))     # 0 + 1 + 2 consultas
        self.fake.requests.clear()

        refused = self.post()

        self.assertEqual(refused.status_code, 200, refused.content)
        self.assertIsNone(refused.json()['payment'])
        self.assertEqual(refused.json()['code'], 'too_many_payment_attempts')
        self.assertEqual(PaymentTransaction.objects.count(), 3)
        self.assertEqual(self.fake.requests, [])

    def test_a_gateway_that_is_down_is_asked_once_not_once_per_open_form(self):
        self.post()
        self.post()
        self.fake.requests.clear()
        for mode in ('unreachable', 'http_401'):
            with self.subTest(mode):
                self.fake.requests.clear()
                self.fake.mode = mode
                with self.assertLogs('store', level='WARNING'):
                    replay = self.post()
                self.assertEqual(len(self.asked()), 1)
                self.assertEqual(replay.json()['code'], 'payment_in_progress')
                self.assertEqual(PaymentTransaction.objects.count(), 2)


@override_settings(**MCW_SETTINGS)
class TheAppIsToldWhyTest(M5CheckoutBase):
    def setUp(self):
        super().setUp()
        self.fake = FakeMiCuentaWeb.from_settings(MCW_SETTINGS)

    def post(self):
        with self.fake.online():
            return self.client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')

    def test_a_first_answer_has_no_code_and_the_shape_it_always_had(self):
        first = self.post()

        self.assertEqual(first.status_code, 201)
        self.assertNotIn('code', first.json())
        self.assertIsNotNone(first.json()['payment'])

    def test_a_replay_that_gets_no_form_says_why(self):
        self.post()
        attempt = PaymentTransaction.objects.get()
        cases = {
            'payment_in_progress': lambda: setattr(self.fake, 'order_get_mode', 'error'),
            'order_already_paid': lambda: (setattr(self.fake, 'order_get_mode', 'ok'), self.fake.hold(
                attempt.transaction_id, self.fake.transaction(order_id=attempt.transaction_id, amount_cents=400000))),
        }
        for code, arrange in cases.items():
            with self.subTest(code):
                arrange()
                with self.assertLogs('store', level='WARNING'):
                    replay = self.post()
                self.assertEqual(replay.status_code, 200, replay.content)
                body = replay.json()
                self.assertIsNone(body['payment'])
                self.assertEqual(body['code'], code)
                self.assertEqual(sorted(body), ['code', 'order_id', 'payment', 'status'])
        self.assertEqual(body['status'], 'paid')

    def test_a_replay_while_the_gateway_is_only_for_the_master_says_so(self):
        self.post()
        with override_settings(PAYMENTS_PUBLIC_TEST=False), self.assertLogs('store', level='WARNING'):
            replay = self.post()

        self.assertEqual((replay.status_code, replay.json()['code']), (200, 'payment_test_only'))

    def test_a_gateway_that_changed_under_a_fresh_order_does_not_fail_the_order(self):
        """El checkout leyó una pasarela y al ir a escribir su intento ya era otra: no es un fallo del pedido."""
        with mock.patch.object(payment_adapters, 'still_charging', return_value=False):
            response = self.post()

        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()['code'], 'payment_gateway_changed')
        order = Order.objects.get()
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)
        self.assertFalse(PaymentTransaction.objects.exists())
        # Y la misma petición, repetida, abre su pago contra lo que hay.
        self.assertIsNotNone(self.post().json()['payment'])

    def test_a_gateway_that_fails_still_fails_the_order_as_before(self):
        self.fake.mode = 'error'
        with self.assertLogs('store', level='ERROR'):
            response = self.post()

        self.assertEqual(response.status_code, 502)
        self.assertNotIn('code', response.json())
        self.assertEqual(Order.objects.get().status, Order.Status.FAILED)


class TheWebCheckoutAndAChangedGatewayTest(McwBase):
    def test_it_is_told_so_and_the_order_is_not_failed(self):
        with mock.patch.object(payment_adapters, 'still_charging', return_value=False):
            response = self.checkout()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'payment_gateway_changed')
        self.assertEqual(Order.objects.get().status, Order.Status.PENDING_PAYMENT)


class AnErrorAnswerIsNotAClearAnswerTest(Hardening):
    def test_before_a_second_form_an_error_about_the_first_is_nobody_knows(self):
        self.fake.order_get_mode = 'error'
        with self.assertLogs('store', level='WARNING'), self.assertRaises(checkout.CheckoutError) as refused:
            with self.fake.online():
                checkout.start_payment_attempt(self.order, credentials=micuentaweb.load_credentials())

        self.assertEqual(refused.exception.code, 'payment_in_progress')
        self.assertEqual(PaymentTransaction.objects.count(), 1)

    def test_nor_is_a_form_opened_with_another_shop(self):
        PaymentTransaction.objects.filter(pk=self.attempt.pk).update(shop='11111111')
        with self.assertRaises(checkout.CheckoutError) as refused, self.fake.online():
            checkout.start_payment_attempt(self.order, credentials=micuentaweb.load_credentials())

        self.assertEqual(refused.exception.code, 'payment_in_progress')
        self.assertEqual(self.asked(), [])


# -- 9, 10 and 11 -------------------------------------------------------------------------------------

class ADeclineLeavesATraceTest(Hardening):
    def test_a_signed_decline_while_the_form_lives_is_written_on_the_open_attempt(self):
        self.assertEqual(self.notify(status='UNPAID').status_code, 200)

        self.refresh()
        self.assertEqual(self.attempt.status, Status.PENDING)
        self.assertEqual(self.attempt.response_code, 'UNPAID')
        self.assertEqual(self.attempt.failure_reason, 'rechazado: REFUSED')
        self.assertEqual(self.attempt.payment_method, 'CARD')

    def test_it_does_not_hide_an_answer_nobody_could_settle(self):
        self.fake.hold(self.attempt.transaction_id, self.paid(uuid='a' * 32), self.paid(uuid='b' * 32))
        with self.assertLogs('store', level='WARNING'):
            self.ask()
        self.notify(status='UNPAID')

        self.refresh()
        self.assertTrue(self.attempt.failure_reason.startswith('undecided: '))

    def test_and_the_attempt_can_still_be_paid(self):
        self.notify(status='UNPAID', uuid='a' * 32)
        self.assertEqual(self.notify(uuid='b' * 32).status_code, 200)

        self.refresh()
        self.assertEqual((self.attempt.status, self.attempt.failure_reason), (Status.AUTHORIZED, 'rechazado: REFUSED'))
        self.assertTrue(self.order.paid)


LONG_MERCHANT = {**IZIPAY_TEST_SETTINGS, 'IZIPAY_MERCHANT_CODE': '4' * 40}


@override_settings(**LONG_MERCHANT)
class AShopCodeLongerThanTheColumnTest(McwBase):
    def test_a_genuine_notification_is_still_this_shops(self):
        gateway = FakeIzipay.from_settings(LONG_MERCHANT)
        self.fake = gateway
        self.assertEqual(self.checkout().status_code, 200)
        attempt = PaymentTransaction.objects.get()
        self.assertEqual(attempt.shop, '4' * 32)

        payload = gateway.payload(transaction_id=attempt.transaction_id, order_number=attempt.order_number,
                                  amount=attempt.amount)
        response = self.client.post('/api/payments/izipay/notification/', gateway.envelope(payload), format='json')

        self.assertEqual(response.status_code, 200, response.content)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, Status.AUTHORIZED)


class TheOtherProductsCredentialsDoNotPrintEitherTest(SimpleTestCase):
    def test_neither_key_shows(self):
        from store.payments import izipay

        with override_settings(**IZIPAY_TEST_SETTINGS):
            credentials = izipay.credentials_from({
                name: IZIPAY_TEST_SETTINGS.get(name, '') for name in izipay._CONSOLE_FIELDS.values()})
        for text in (repr(credentials), str(credentials), f'{credentials!r}'):
            self.assertNotIn(IZIPAY_TEST_SETTINGS['IZIPAY_API_KEY'], text)
            self.assertNotIn(IZIPAY_TEST_SETTINGS['IZIPAY_HASH_KEY'], text)
        self.assertIn(IZIPAY_TEST_SETTINGS['IZIPAY_MERCHANT_CODE'], repr(credentials))
        self.assertEqual(credentials.api_key, IZIPAY_TEST_SETTINGS['IZIPAY_API_KEY'])


# -- the lock, for every change that takes it -------------------------------------------------------

@skipUnless(connection.vendor == 'postgresql', 'several writers at once need a database that takes them')
@override_settings(APP_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode(), **EMPTY_ENV)
class EveryChangeWaitsForACheckoutThatIsWritingTest(TransactionTestCase):
    """
    Apagar, revocar y pausar toman el mismo bloqueo que activar: ninguno decide
    mientras un checkout está escribiendo su intento, y todos lo ven después.
    """

    def setUp(self):
        cache.clear()
        self.master = get_user_model().objects.create_superuser('master', 'master@example.com', 'x')
        self.order = Order.objects.create(
            company=Company.objects.create(name='Tienda a la vez 2', slug='tienda-a-la-vez-2'),
            total=Decimal('10.00'), customer_email='x@example.com')
        self.provider = registry.get('izipay_micuentaweb')
        draft = service.save_draft(self.provider, None, actor=self.master, public=MCW_PUBLIC,
                                   secrets={'password': 'testpassword_DelPanelNoEsRealNoEsRealNoEsReal'})
        IntegrationConfig.objects.filter(pk=draft.pk).update(
            validated=True, last_test_status='ok', last_tested_at=timezone.now())
        service.activate(self.provider, None, actor=self.master, version=draft.version)

    def race(self, change):
        writing, release, outcome = threading.Event(), threading.Event(), {}
        credentials = micuentaweb.load_credentials()

        def still_there(_credentials):
            writing.set()
            release.wait(timeout=20)
            return True

        def checkout_in_the_middle():
            try:
                with mock.patch.object(payment_adapters, 'still_charging', side_effect=still_there):
                    checkout._open_attempt(
                        self.order, credentials, provider='micuentaweb',
                        transaction_id='80000000000000000001', order_number='80000000000000000001')
            except Exception as exc:  # noqa: BLE001
                outcome['buyer'] = exc
            finally:
                connection.close()

        def the_change():
            try:
                change()
                outcome['result'] = 'done'
            except Exception as exc:  # noqa: BLE001
                outcome['result'] = exc
            finally:
                outcome['at'] = timezone.now()
                connection.close()

        buyer, master = threading.Thread(target=checkout_in_the_middle), threading.Thread(target=the_change)
        buyer.start()
        self.assertTrue(writing.wait(timeout=20))
        master.start()
        master.join(timeout=1.5)
        self.assertTrue(master.is_alive(), outcome)          # espera al checkout
        released_at = timezone.now()
        release.set()
        buyer.join(timeout=20)
        master.join(timeout=20)
        self.assertNotIn('buyer', outcome)
        self.assertGreaterEqual(outcome['at'], released_at)
        return outcome['result']

    def test_switching_off_waits_and_then_sees_the_attempt(self):
        result = self.race(lambda: service.set_enabled(self.provider, None, actor=self.master, enabled=False))

        self.assertIsInstance(result, registry.ChangeBlocked, result)
        self.assertEqual(payment_adapters.active_code(), 'micuentaweb')

    def test_revoking_waits_and_then_sees_the_attempt(self):
        result = self.race(lambda: service.revoke(self.provider, None, actor=self.master))

        self.assertIsInstance(result, registry.ChangeBlocked, result)
        self.assertTrue(IntegrationConfig.objects.filter(slot='active').exists())

    def test_pausing_waits_for_it_and_is_not_refused_because_of_it(self):
        result = self.race(lambda: service.set_paused(self.provider, None, actor=self.master, paused=True))

        self.assertEqual(result, 'done')
        self.assertTrue(payment_adapters.paused())
        self.assertEqual(PaymentTransaction.objects.count(), 1)     # el que ya estaba escribiéndose, entró
