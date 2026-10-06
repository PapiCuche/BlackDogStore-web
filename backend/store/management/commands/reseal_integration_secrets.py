"""
After changing the root key of the secret store.

    1. put the new key in APP_CONFIG_ENCRYPTION_KEY
    2. put the old one in APP_CONFIG_ENCRYPTION_KEYS_PREVIOUS
    3. restart, then:  python manage.py reseal_integration_secrets
    4. remove the old key from the environment and restart again

Opens every stored credential with whichever key opens it and seals it again
with the current one. Prints a count. Never prints a credential.
"""
from django.core.management.base import BaseCommand, CommandError

from store.integrations import secret_store, service


class Command(BaseCommand):
    help = 'Vuelve a cifrar las credenciales de las integraciones con la clave raíz actual.'

    def handle(self, *args, **options):
        try:
            count = service.reseal_all()
        except secret_store.SecretStoreError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(f'Credenciales vueltas a cifrar: {count} integración(es).')
