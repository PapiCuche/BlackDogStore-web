"""
El estado REAL de las cuentas de desarrollo — sólo en desarrollo.

EL DEFECTO QUE CIERRA
---------------------
La tarjeta de accesos rápidos de `/auth` anunciaba seis cuentas con su
contraseña, incondicionalmente, con su propia lista escrita a mano en el
frontend. No comprobaba nada: si nadie había ejecutado `seed_demo_users`, la
pantalla seguía ofreciendo seis credenciales que el backend rechazaba con «No
active account found with the given credentials».

Prometer una credencial que no existe es peor que no ofrecer ninguna: manda a
depurar el login, que funciona perfectamente.

Y había DOS listas —la del comando y la del componente— que podían separarse en
silencio. Ahora sólo hay una: ésta pregunta al comando y el frontend pregunta
aquí.

POR QUÉ ESTO NO ES UNA PUERTA TRASERA
-------------------------------------
No autentica a nadie ni crea nada. Devuelve, en desarrollo, qué cuentas demo
existen y cuáles pueden usarse, para que la interfaz diga la verdad. Las cuentas
siguen siendo usuarios ordinarios que entran por `/api/auth/login/` con el mismo
JWT, las mismas cookies HttpOnly y el mismo CSRF que cualquiera.

Con `DEBUG = False` responde 404 — no 403: en producción esta ruta simplemente
no existe, y su ausencia no confirma que exista en otra parte.

La contraseña de desarrollo no es un secreto: está en el repositorio, la imprime
el propio comando, y sólo abre cuentas que únicamente pueden crearse en
desarrollo. Lo que sí sería un secreto es la contraseña de una cuenta real, y
esta vista sólo mira las que llevan la firma del comando en su dirección.
"""

from __future__ import annotations

from django.conf import settings
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .management.commands.seed_demo_users import (
    ALL_DEMO_USERNAMES,
    DEMO_CUSTOMER_USERNAME,
    DEMO_INTERNAL_USERS,
    DEMO_MASTER_USERNAME,
    DEMO_PASSWORD,
    demo_email,
)

#: Adónde lleva cada cuenta y qué autoridad tiene. Es lo que la tarjeta muestra,
#: y vive aquí para que no haya una segunda copia en el frontend.
DEMO_DESTINATIONS = {
    DEMO_CUSTOMER_USERNAME: {
        'label': 'Cliente',
        'destination': 'Tienda y pedidos',
        'authority': 'Ninguna autoridad interna.',
    },
    'dev_sales': {
        'label': 'Ventas',
        'destination': 'Control interno · ventas',
        'authority': 'Pedidos, punto de venta y clientes.',
    },
    'dev_inventory': {
        'label': 'Inventario',
        'destination': 'Control interno · inventario',
        'authority': 'Stock, movimientos y transferencias.',
    },
    'dev_technician': {
        'label': 'Técnico',
        'destination': 'Control interno · servicio técnico',
        'authority': 'Órdenes de reparación.',
    },
    'dev_admin': {
        'label': 'Admin de empresa',
        'destination': 'Control interno completo',
        'authority': 'Autoridad completa sobre SU empresa.',
    },
    DEMO_MASTER_USERNAME: {
        'label': 'Master de plataforma',
        'destination': 'Control interno · elige empresa',
        'authority': 'Superusuario. Debe elegir empresa explícitamente.',
    },
}

_ORDER = (
    DEMO_CUSTOMER_USERNAME,
    *(u for u, _r, _rs, _a in DEMO_INTERNAL_USERS),
    DEMO_MASTER_USERNAME,
)


class DevDemoAccountsView(APIView):
    """
    GET /api/dev/demo-accounts/ — sólo con DEBUG=True.

    Devuelve qué cuentas demo existen y cuáles sirven para entrar, para que la
    interfaz de desarrollo no ofrezca credenciales que no funcionan.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request):
        if not settings.DEBUG:
            # 404 y no 403: en producción esta superficie no existe.
            return Response(status=status.HTTP_404_NOT_FOUND)

        from django.contrib.auth import get_user_model

        User = get_user_model()
        # Sólo las cuentas que llevan la FIRMA del comando en su dirección. Un
        # usuario real llamado `dev_admin` no se describe aquí ni se ofrece.
        rows = {
            u.username: u
            for u in User.objects.filter(username__in=ALL_DEMO_USERNAMES)
            if (u.email or '').lower() == demo_email(u.username)
        }

        accounts = []
        for username in _ORDER:
            meta = DEMO_DESTINATIONS[username]
            user = rows.get(username)
            accounts.append({
                'username': username,
                **meta,
                'exists': user is not None,
                # `usable` es lo único que la interfaz necesita para decidir si
                # ofrece el botón. Existir e inactivo NO es usable, y ése fue
                # exactamente el defecto que el comando dejaba sin reparar.
                'usable': bool(user and user.is_active),
            })

        ready = all(a['usable'] for a in accounts)
        return Response({
            'password': DEMO_PASSWORD,
            'accounts': accounts,
            'ready': ready,
            # El comando exacto, con el slug de una empresa REAL de esta base:
            # decir «<slug>» obliga a ir a buscarlo.
            'seed_command': _seed_command(),
        })


def _seed_command() -> str:
    from .models import Company

    company = Company.objects.filter(is_active=True).order_by('id').first()
    slug = company.slug if company else '<slug-de-empresa>'
    return f'python manage.py seed_demo_users --company-slug {slug}'
