"""
DC2 — offer the default «estado estético» scale to ONE company that asks for it.

Idempotent: a code the company already has is left as the company wrote it.
Never runs over every company: `--company <slug>` is required.
"""
from django.core.management.base import BaseCommand, CommandError

from store.device_catalog import seed_cosmetic_grades
from store.models import Company


class Command(BaseCommand):
    help = 'Crea la escala de estado estético sugerida en UNA empresa (idempotente).'

    def add_arguments(self, parser):
        parser.add_argument('--company', required=True, help='slug de la empresa')

    def handle(self, *args, **options):
        company = Company.objects.filter(slug=options['company']).first()
        if company is None:
            raise CommandError('Empresa no encontrada.')
        created = seed_cosmetic_grades(company)
        self.stdout.write(f'Estados estéticos creados: {created}.')
