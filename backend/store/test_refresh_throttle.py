from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

WEB = '/api/auth/refresh/'
NATIVE = '/api/v1/auth/refresh/'
LIMIT = 30


class RefreshThrottleTest(TestCase):
    """
    SEC-SET-04-A — renovar la sesión tiene un límite por dirección.

    Las dos rutas de renovación (cookie para la web, cuerpo para la app) no
    tenían limitador. Cada renovación válida rota el refresh y deja una fila
    nueva en `OutstandingToken`, así que quien tuviera un refresh podía
    escribir filas sin tope, y cualquiera podía martillear la ruta sin sesión.

    El límite se cuenta por dirección, no por usuario: la ruta admite a
    cualquiera y un limitador anónimo no cuenta las peticiones que llegan con
    sesión, que aquí son precisamente las normales. Sólo cuentan las peticiones
    que traen un refresh, sea bueno o inventado: las que no traen ninguno se
    rechazan sin trabajo y no gastan el cupo de nadie.
    """

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username='refresh_owner', email='refresh@example.invalid',
            password='RefreshPass123!',
        )

    def _web_client(self):
        client = APIClient()
        res = client.post(
            '/api/auth/login/',
            {'username': 'refresh_owner', 'password': 'RefreshPass123!'},
            format='json',
        )
        self.assertEqual(res.status_code, 200, res.data)
        return client

    def test_web_refresh_is_limited_and_stops_writing_token_rows(self):
        client = self._web_client()
        statuses = [client.post(WEB).status_code for _ in range(LIMIT)]
        self.assertEqual(set(statuses), {200}, statuses)

        rows = OutstandingToken.objects.filter(user=self.user).count()
        blocked = [client.post(WEB).status_code for _ in range(5)]
        self.assertEqual(set(blocked), {429}, blocked)
        self.assertEqual(
            OutstandingToken.objects.filter(user=self.user).count(), rows,
            'una renovación rechazada por el límite no puede emitir un token')

    def test_native_refresh_is_limited(self):
        refresh = str(RefreshToken.for_user(self.user))
        client = APIClient()
        statuses = []
        for _ in range(LIMIT + 3):
            res = client.post(NATIVE, {'refresh': refresh}, format='json')
            statuses.append(res.status_code)
            if res.status_code == 200:
                refresh = res.data['refresh']
        self.assertEqual(set(statuses[:LIMIT]), {200}, statuses)
        self.assertEqual(set(statuses[LIMIT:]), {429}, statuses)

    def _garbage_web(self, client, **extra):
        from django.conf import settings
        client.cookies[settings.JWT_COOKIE_REFRESH_NAME] = 'no-es-un-token'
        return client.post(WEB, **extra)

    def test_a_forged_credential_is_limited(self):
        client = APIClient()
        web = [self._garbage_web(client).status_code for _ in range(LIMIT + 2)]
        self.assertEqual(set(web[:LIMIT]), {401}, web)
        self.assertEqual(set(web[LIMIT:]), {429}, web)

    def test_visitors_without_a_credential_do_not_spend_the_budget(self):
        """
        La web intenta renovar cada vez que una petición responde 401, también
        para quien nunca inició sesión. Esas peticiones no traen refresh, se
        responden sin tocar la base y no pueden gastar el cupo de quien sí
        tiene sesión detrás de la misma dirección.
        """
        visitor = APIClient()
        statuses = {visitor.post(WEB).status_code for _ in range(LIMIT * 3)}
        self.assertEqual(statuses, {401})
        native = {
            visitor.post(NATIVE, {}, format='json').status_code
            for _ in range(LIMIT * 3)
        }
        self.assertEqual(native, {400})
        # Un cuerpo que no es un objeto, o que no se puede leer, tampoco cuenta
        # ni rompe el limitador.
        self.assertEqual(visitor.post(NATIVE, [1, 2], format='json').status_code, 400)
        self.assertEqual(
            visitor.post(NATIVE, '{roto', content_type='application/json').status_code, 400)

        member = self._web_client()
        self.assertEqual(member.post(WEB).status_code, 200)

    def test_a_signed_in_caller_is_not_exempt(self):
        client = APIClient()
        client.force_authenticate(user=self.user)
        statuses = [
            client.post(NATIVE, {'refresh': 'no-es-un-token'}, format='json').status_code
            for _ in range(LIMIT + 2)
        ]
        self.assertIn(429, statuses, statuses)

    def test_web_and_native_share_one_budget_per_address(self):
        client = APIClient()
        for _ in range(LIMIT):
            self._garbage_web(client)
        res = APIClient().post(NATIVE, {'refresh': 'no-es-un-token'}, format='json')
        self.assertEqual(res.status_code, 429)

    def test_another_address_has_its_own_budget(self):
        client = APIClient()
        for _ in range(LIMIT + 1):
            self._garbage_web(client, REMOTE_ADDR='203.0.113.10')
        self.assertEqual(self._garbage_web(client, REMOTE_ADDR='203.0.113.10').status_code, 429)
        self.assertEqual(self._garbage_web(client, REMOTE_ADDR='203.0.113.20').status_code, 401)

    def test_exhausting_refresh_does_not_block_login(self):
        client = APIClient()
        for _ in range(LIMIT + 1):
            self._garbage_web(client)
        res = APIClient().post(
            '/api/auth/login/',
            {'username': 'refresh_owner', 'password': 'RefreshPass123!'},
            format='json',
        )
        self.assertEqual(res.status_code, 200, res.data)
