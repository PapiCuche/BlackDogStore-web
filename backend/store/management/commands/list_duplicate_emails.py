"""
REGISTER-RACE-01 · the accounts that share one e-mail address, for a person to decide.

Read-only. It prints what is needed to choose which account of each group
stays — is it active, when was it made, when was it last used, does it have a
password, does it work somewhere, did it buy anything — and an address
recognisable to its owner without being copied whole into a terminal or a log.

It changes nothing. Nothing in this project merges accounts automatically.
"""
from django.core.management.base import BaseCommand

from store import accounts
from store.models import Membership, Order


def _masked(email: str) -> str:
    email = (email or '').strip().lower()
    name, _, domain = email.partition('@')
    # One letter, whatever the length: «al@…» shown as «al***» is shown whole.
    return f'{name[:1]}***@{domain}' if domain else f'{name[:1]}***'


class Command(BaseCommand):
    help = 'Lista las cuentas que comparten un mismo correo. Sólo lectura.'

    def handle(self, *args, **options):
        groups = accounts.duplicate_email_groups()
        total = sum(len(group) for group in groups)
        noun = 'correo' if len(groups) == 1 else 'correos'
        self.stdout.write(f'{len(groups)} {noun} con más de una cuenta ({total} cuentas implicadas).')
        for group in groups:
            self.stdout.write(f'--- {_masked(group[0].email)}')
            for user in group:
                self.stdout.write(
                    f'  id={user.pk} activa={"sí" if user.is_active else "no"} '
                    f'superusuario={"sí" if user.is_superuser else "no"} '
                    f'alta={user.date_joined.date()} '
                    f'último_ingreso={user.last_login.date() if user.last_login else "nunca"} '
                    f'con_contraseña={"sí" if user.has_usable_password() else "no"} '
                    f'membresías={Membership.objects.filter(user=user).count()} '
                    f'pedidos={Order.objects.filter(user=user).count()}'
                )
        if groups:
            self.stdout.write('No se cambió nada. Decide qué cuenta se conserva en cada grupo.')
