"""
SUNAT-PREFLIGHT: GO / AVISO / NO-GO for one company, read-only. See `store/fiscal_preflight.py`.

    python manage.py fiscal_preflight --company <slug>
    python manage.py fiscal_preflight --company <slug> --target production --json

Exit status 1 when a line says NO-GO. It writes nothing, never talks to SUNAT and never
prints a secret.
"""
import json

from django.core.management.base import BaseCommand, CommandError

from store import fiscal_preflight
from store.models import Company, FiscalEnvironment


class Command(BaseCommand):
    help = ('Diagnóstico fiscal de sólo lectura de una empresa: interruptor, emisor, credenciales, certificado, '
            'series, cola pendiente y plazos. Sin red y sin secretos. Sale con 1 ante un NO-GO.')

    def add_arguments(self, parser):
        parser.add_argument('--company', required=True, help='El slug de la empresa.')
        parser.add_argument('--target', choices=FiscalEnvironment.values, default=FiscalEnvironment.BETA,
                            help='El ambiente para el que se evalúa (por defecto beta).')
        parser.add_argument('--json', action='store_true', help='Salida en JSON.')

    def handle(self, *args, **options):
        company = Company.objects.filter(slug=options['company']).first()
        if company is None:
            raise CommandError(f'No existe la empresa «{options["company"]}».')
        checks = fiscal_preflight.run(company, target=options['target'])
        verdict = fiscal_preflight.verdict(checks)
        if options['json']:
            self.stdout.write(json.dumps(fiscal_preflight.as_dict(company, options['target'], checks),
                                         ensure_ascii=False, indent=2))
        else:
            self.stdout.write(f'Empresa {company.slug} · objetivo {options["target"]}')
            for check in checks:
                self.stdout.write(f'{check.level:<6}{check.area}: {check.detail}')
            self.stdout.write(f'Veredicto: {verdict}')
        if verdict == fiscal_preflight.NO_GO:
            raise CommandError('Hay comprobaciones en NO-GO.', returncode=1)
