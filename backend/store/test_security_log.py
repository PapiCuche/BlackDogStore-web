from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

PASSWORD = 'ClaveCorrecta123!'
WRONG = 'clave-equivocada-que-no-debe-salir'


class LoginSecurityLogTest(TestCase):
    """
    AUTH-LOGGING-01 — cada intento de inicio de sesión deja un registro.

    Ningún canal lo hacía. Se comprueba lo que queda escrito: quién lo intentó,
    desde dónde y por qué canal; y lo que no puede quedar escrito nunca, que es
    la contraseña.
    """

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username='registro_ok', email='registro@example.invalid', password=PASSWORD,
        )
        self.client = APIClient()

    def _web(self, username, password, **extra):
        return self.client.post(
            '/api/auth/login/', {'username': username, 'password': password},
            format='json', **extra,
        )

    def _native(self, email, password):
        return self.client.post(
            '/api/v1/auth/login/', {'email': email, 'password': password}, format='json',
        )

    def test_a_failed_web_login_is_recorded_without_the_password(self):
        with self.assertLogs('store.security', level='INFO') as logs:
            res = self._web('registro_ok', WRONG, REMOTE_ADDR='203.0.113.7')
        self.assertEqual(res.status_code, 401)

        self.assertEqual(len(logs.records), 1)
        record = logs.records[0]
        self.assertEqual(record.levelname, 'WARNING')
        line = record.getMessage()
        self.assertIn('login_failed', line)
        self.assertIn('channel=web', line)
        self.assertIn('ip=203.0.113.7', line)
        self.assertIn('registro_ok', line)
        self.assertNotIn(WRONG, line)

    def test_a_successful_web_login_is_recorded_with_the_user_id(self):
        with self.assertLogs('store.security', level='INFO') as logs:
            res = self._web('registro_ok', PASSWORD)
        self.assertEqual(res.status_code, 200)

        line = logs.records[0].getMessage()
        self.assertEqual(logs.records[0].levelname, 'INFO')
        self.assertIn('login_ok', line)
        self.assertIn(f'user_id={self.user.pk}', line)
        self.assertNotIn(PASSWORD, line)

    def test_the_native_channel_records_every_kind_of_failure(self):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        cases = [
            ('nadie@example.invalid', WRONG),       # no existe
            ('registro@example.invalid', WRONG),    # contraseña equivocada
            ('registro@example.invalid', PASSWORD),  # cuenta desactivada
        ]
        for email, password in cases:
            with self.subTest(email=email, password=password == PASSWORD):
                with self.assertLogs('store.security', level='INFO') as logs:
                    res = self._native(email, password)
                self.assertEqual(res.status_code, 401)
                line = logs.records[0].getMessage()
                self.assertIn('login_failed', line)
                self.assertIn('channel=native', line)
                self.assertNotIn(WRONG, line)
                self.assertNotIn(PASSWORD, line)

    def test_a_successful_native_login_is_recorded(self):
        with self.assertLogs('store.security', level='INFO') as logs:
            res = self._native('registro@example.invalid', PASSWORD)
        self.assertEqual(res.status_code, 200)
        self.assertIn('login_ok channel=native', logs.records[0].getMessage())

    def test_a_name_with_line_breaks_cannot_forge_a_second_record(self):
        forged = 'alguien\nlogin_ok channel=web ip=10.0.0.1 user_id=1'
        with self.assertLogs('store.security', level='INFO') as logs:
            self._web(forged, WRONG)

        self.assertEqual(len(logs.records), 1)
        line = logs.records[0].getMessage()
        self.assertNotIn('\n', line)
        self.assertTrue(line.startswith('login_failed'))

    def test_a_very_long_name_is_cut(self):
        with self.assertLogs('store.security', level='INFO') as logs:
            self._web('x' * 5000, WRONG)
        self.assertLess(len(logs.records[0].getMessage()), 400)
