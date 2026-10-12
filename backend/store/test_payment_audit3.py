"""
PAGOS · lo que encontró la tercera auditoría independiente.

  1. El checkout de la web también tiene un límite para quien tiene sesión.
  2. «SDK web / Checkout»: el entorno escrito con mayúsculas o espacios es el
     mismo entorno, también para decidir si un cambio de pasarela es seguro.
  3. Una pausa no vuelve sola con una fila que se apagó al cambiar de producto.
  5. La comprobación de estado dice que los pagos de TEST están abiertos a
     cualquiera cuando lo están en una instalación que no es de desarrollo.
  6. El rechazo de un cambio dice lo que de verdad se puede hacer.
"""
import io
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from store.integrations import payments as payment_adapters
from store.models import IntegrationConfig, PaymentTransaction
from store.payments.fake_izipay import FakeIzipay
from store.test_integrations_payments import CHECKOUT_PUBLIC, CHECKOUT_SECRETS, MCW_PUBLIC, MCW_SECRETS, fake_checkout
from store.test_micuentaweb import CHECKOUT_URL, McwBase
from store.test_ops_status import run as health_check
from store.test_payment_change_guard import ChargingFromTheEnvironmentTest as EnvironmentBase
from store.test_payment_change_guard import GuardBase
from store.tests import IZIPAY_TEST_SETTINGS


# -- 1 -------------------------------------------------------------------------------------

class TheWebCheckoutHasALimitForAnAccountTooTest(McwBase):
    def test_a_signed_in_account_cannot_open_orders_without_limit(self):
        from store.throttles import CheckoutThrottle, CheckoutUserThrottle
        from store.views import CreateCheckoutSessionView

        self.assertEqual(CreateCheckoutSessionView.throttle_classes, [CheckoutThrottle, CheckoutUserThrottle])
        self.client.force_authenticate(get_user_model().objects.create_user('clienta', 'clienta@example.com', 'x'))
        with self.fake.online():
            statuses = [self.client.post(CHECKOUT_URL, {}, format='json').status_code for _ in range(12)]

        self.assertEqual(statuses[-1], 429)
        self.assertLessEqual(len([code for code in statuses if code != 429]), 10)
        self.assertEqual(settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['checkout_user'], '10/min')

    def test_one_account_does_not_spend_the_budget_of_another(self):
        User = get_user_model()
        first, second = APIClient(), APIClient()
        first.force_authenticate(User.objects.create_user('una', 'una@example.com', 'x'))
        second.force_authenticate(User.objects.create_user('otra', 'otra@example.com', 'x'))
        for _ in range(12):
            first.post(CHECKOUT_URL, {}, format='json')

        self.assertNotEqual(second.post(CHECKOUT_URL, {}, format='json').status_code, 429)


# -- 2 -------------------------------------------------------------------------------------

class TheEnvironmentWordOfTheOtherProductTest(EnvironmentBase):
    """
    La aplicación lee `IZIPAY_ENV` sin distinguir mayúsculas ni espacios. El
    guardia leía la palabra tal cual: con «Production» buscaba cobros de TEST,
    no veía el de producción que estaba abierto y dejaba cambiar de pasarela.
    """

    def test_a_production_form_open_is_seen_however_the_word_is_written(self):
        for word in ('Production', ' production ', 'PRODUCTION'):
            with self.subTest(word):
                PaymentTransaction.objects.all().delete()
                IntegrationConfig.objects.all().delete()
                production = {**IZIPAY_TEST_SETTINGS, 'IZIPAY_ENV': word}
                with override_settings(**production):
                    self.fake = FakeIzipay.from_settings({**production, 'IZIPAY_ENV': 'production'})
                    self.client = APIClient()
                    self.assertEqual(self.checkout().status_code, 200)
                    self.assertEqual(PaymentTransaction.objects.get().environment, 'production')
                    version = self.console_draft('izipay_micuentaweb', MCW_PUBLIC, MCW_SECRETS)

                    refused = self.activate('izipay_micuentaweb', version)

                    self.assertEqual(refused.status_code, 400, refused.content)
                    self.assertIn('acknowledge', refused.json()['errors'])
                    self.assertEqual(payment_adapters.active_code(), 'izipay')

    def test_the_console_shows_such_keys_as_what_they_are(self):
        from store.integrations import registry

        provider = registry.get('izipay_checkout')
        for word, mode in (('Production', 'production'), (' production ', 'production'), ('SANDBOX', 'test'),
                           ('sandbox', 'test'), ('', ''), ('otra', '')):
            with self.subTest(word):
                self.assertEqual(provider.mode({'environment': word}, {}), mode)


# -- 3 -------------------------------------------------------------------------------------

class APauseDoesNotComeBackByItselfTest(GuardBase):
    def pause(self):
        return self.console.post(self.url('izipay_micuentaweb', 'pause'), {}, format='json')

    def test_a_row_switched_off_by_a_change_of_product_starts_clean_when_it_charges_again(self):
        self.assertEqual(self.pause().status_code, 200)
        self.prepare('otro producto')
        self.assertEqual(self.change('otro producto').status_code, 200)          # «Mi Cuenta Web» queda apagada
        row = IntegrationConfig.objects.get(provider='izipay_micuentaweb', slot='active')
        self.assertEqual((row.enabled, row.paused), (False, False))

        # Semanas después se vuelve a «Mi Cuenta Web» con un borrador nuevo.
        self.assertEqual(self.save('izipay_micuentaweb', MCW_PUBLIC, MCW_SECRETS).status_code, 200)
        self.validate('izipay_micuentaweb', self.gateway)
        self.assertEqual(self.activate('izipay_micuentaweb').status_code, 200)

        self.assertFalse(self.console.get(self.url('izipay_micuentaweb')).json()['active']['paused'])
        self.assertEqual(self.buy(self.gateway).status_code, 200)

    def test_activating_onto_a_row_that_was_off_and_still_said_paused_clears_it(self):
        self.pause()
        self.assertEqual(self.change('apagar').status_code, 200)
        IntegrationConfig.objects.filter(provider='izipay_micuentaweb', slot='active').update(paused=True)
        self.assertEqual(self.save('izipay_micuentaweb', MCW_PUBLIC, MCW_SECRETS).status_code, 200)
        self.validate('izipay_micuentaweb', self.gateway)

        self.assertEqual(self.activate('izipay_micuentaweb').status_code, 200)

        row = IntegrationConfig.objects.get(provider='izipay_micuentaweb', slot='active')
        self.assertEqual((row.enabled, row.paused), (True, False))

    def test_a_pause_on_a_row_that_is_off_pauses_nobody(self):
        """Lo que cuenta es la fila que cobra. Una apagada que aún dice «en pausa» no detiene a la otra pasarela."""
        self.prepare('otro producto')
        self.assertEqual(self.change('otro producto').status_code, 200)
        IntegrationConfig.objects.filter(provider='izipay_micuentaweb', slot='active').update(paused=True)

        self.assertFalse(payment_adapters.paused())
        self.assertEqual(self.buy(fake_checkout()).status_code, 200)
        self.assertNotIn('en pausa', health_check()[1])


# -- 5 -------------------------------------------------------------------------------------

class TheHealthCheckSaysTestPaymentsAreOpenTest(McwBase):
    NEEDLE = 'los pagos de TEST están abiertos a cualquier comprador'

    def report(self):
        out = io.StringIO()
        try:
            call_command('ops_status', stdout=out)
        except SystemExit:
            pass
        return out.getvalue()

    def test_it_is_an_alarm_on_an_installation_that_is_not_a_developers(self):
        with override_settings(PAYMENTS_PUBLIC_TEST=True, DEBUG=False):
            text = self.report()

        self.assertIn('ATENCIÓN pagos: ' + self.NEEDLE, text)
        self.assertIn('PAYMENTS_PUBLIC_TEST', text)

    def test_not_on_a_developers_machine_and_not_with_the_switch_off(self):
        with override_settings(PAYMENTS_PUBLIC_TEST=True, DEBUG=True):
            self.assertNotIn(self.NEEDLE, self.report())
        with override_settings(PAYMENTS_PUBLIC_TEST=False, DEBUG=False):
            self.assertNotIn(self.NEEDLE, self.report())

    def test_the_other_health_check_tests_look_at_a_published_shop(self):
        """El corredor de pruebas enciende el interruptor; el ayudante de esas pruebas mira una tienda con él apagado."""
        self.assertTrue(settings.PAYMENTS_PUBLIC_TEST)
        self.assertNotIn(self.NEEDLE, health_check()[1])


# -- 6 -------------------------------------------------------------------------------------

class TheRefusalSaysWhatCanBeDoneTest(GuardBase):
    def test_with_the_gateway_in_the_console_it_says_to_pause(self):
        self.open_attempt(minutes=10)

        detail = self.change('apagar').json()['detail']

        self.assertIn('Pausa los pagos nuevos', detail)
        self.assertNotIn('variables del servidor', detail)


class TheRefusalWithAGatewayFromTheEnvironmentTest(EnvironmentBase):
    def test_it_does_not_offer_a_pause_that_does_not_exist_there(self):
        attempt = self.pending()
        PaymentTransaction.objects.filter(pk=attempt.pk).update(created_at=timezone.now() - timedelta(minutes=10))
        version = self.console_draft('izipay_checkout', CHECKOUT_PUBLIC, CHECKOUT_SECRETS)

        response = self.activate('izipay_checkout', version)

        self.assertEqual(response.status_code, 409, response.content)
        detail = response.json()['detail']
        self.assertNotIn('Pausa los pagos nuevos', detail)
        self.assertIn('variables del servidor', detail)
        self.assertIn('Vuelve a intentarlo a partir de las', detail)
        self.assertIn('para poder pausar', detail)
        # Y pausar, ahí, de verdad no existe.
        self.assertEqual(self.console.post('/api/admin/integrations/izipay_micuentaweb/pause/', {}, format='json').status_code, 404)
