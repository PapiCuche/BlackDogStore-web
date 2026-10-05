"""
Point a company at its WhatsApp Business credentials.

    python manage.py configure_whatsapp <company-slug> \\
        --phone-number-id 1055500000 \\
        --access-token-env WHATSAPP_TOKEN_ACME \\
        --app-secret-env   WHATSAPP_SECRET_ACME \\
        --verify-token-env WHATSAPP_VERIFY_ACME

THE OPERATOR'S COMMAND, NOT AN API. What it stores is the NAME of the
environment variable that holds each secret; the secrets themselves stay in the
environment of the process. A tenant cannot set these references through the
panel, so a tenant cannot aim their configuration at another company's token —
and only names in the `WHATSAPP_` namespace are accepted, so nobody can aim it
at `SECRET_KEY` either.

It does NOT turn sending on. The tenant's administrator does that from the
panel, once the templates are chosen.
"""
import re

from django.core.management.base import BaseCommand, CommandError

from store import messaging
from store import whatsapp_services as wa
from store.models import Company, CompanyMessagingSettings

REFERENCES = {
    'access_token_env': 'whatsapp_access_token_env',
    'app_secret_env': 'whatsapp_app_secret_env',
    'verify_token_env': 'whatsapp_verify_token_env',
}


class Command(BaseCommand):
    help = 'Referencia las credenciales de WhatsApp Business de una empresa.'

    def add_arguments(self, parser):
        parser.add_argument('company_slug')
        parser.add_argument('--phone-number-id', dest='phone_number_id')
        parser.add_argument('--business-account-id', dest='business_account_id')
        for option in REFERENCES:
            parser.add_argument(f'--{option.replace("_", "-")}', dest=option)

    def handle(self, *args, **options):
        company = Company.objects.filter(slug=options['company_slug'], is_active=True).first()
        if company is None:
            raise CommandError('No hay una empresa activa con ese identificador.')

        changes = {}
        for option, field in REFERENCES.items():
            name = options.get(option)
            if name is None:
                continue
            if not messaging.is_valid_reference(name):
                raise CommandError(
                    f'--{option.replace("_", "-")}: el nombre de la variable debe empezar por '
                    'WHATSAPP_ y llevar sólo mayúsculas, números y guion bajo.'
                )
            changes[field] = name
        for option, field in (('phone_number_id', 'whatsapp_phone_number_id'),
                              ('business_account_id', 'whatsapp_business_account_id')):
            value = options.get(option)
            if value is None:
                continue
            if not re.fullmatch(r'\d{1,40}', value):
                raise CommandError(f'--{option.replace("_", "-")}: sólo dígitos.')
            changes[field] = value

        config, _ = CompanyMessagingSettings.objects.get_or_create(company=company)
        for field, value in changes.items():
            setattr(config, field, value)
        config.save()

        ready, missing = wa.readiness(config)
        # Names of what is missing, never a value.
        self.stdout.write(
            f'{company.slug}: ' + ('listo para activar.' if ready else f'falta {", ".join(missing)}.')
        )
