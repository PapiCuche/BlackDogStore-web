"""
LEGAL-POLICIES-01 — load a legal document's text from a file, through the same
service as the panel (validation, row lock, audit log).

    python manage.py load_legal_policy --company <slug> --kind privacy --file /path/privacy.txt [--publish]

Without --publish it only saves the DRAFT. The audit entries carry no actor
(it is an operator command) and `source: command`, so the change stays traceable.
"""
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from store import legal_policy_services as legal
from store.models import AdminAuditLog, Company


class Command(BaseCommand):
    help = 'Guarda (y opcionalmente publica) el texto de una política legal de una empresa.'

    def add_arguments(self, parser):
        parser.add_argument('--company', required=True, help='Slug de la empresa.')
        parser.add_argument('--kind', required=True, choices=legal.KINDS)
        parser.add_argument('--file', required=True, help='Archivo de texto plano en UTF-8.')
        parser.add_argument('--publish', action='store_true', help='Publica el borrador guardado.')

    def handle(self, *args, **options):
        company = Company.objects.filter(slug=options['company']).first()
        if company is None:
            raise CommandError('Empresa no encontrada.')
        path = Path(options['file'])
        if not path.is_file():
            raise CommandError('Archivo no encontrado.')
        text = path.read_text(encoding='utf-8')
        kind = options['kind']
        try:
            row = legal.save_draft(company, kind, text, actor=None)
            if options['publish']:
                row = legal.publish(company, kind, actor=None)
        except ValidationError as exc:
            raise CommandError('; '.join(exc.messages)) from exc
        AdminAuditLog.objects.filter(
            company=company, target_type='company_legal_policy', target_id=str(row.pk), actor__isnull=True,
        ).exclude(metadata__has_key='source').update(metadata={
            'company_id': company.pk, 'kind': kind, 'length': len(row.draft_text), 'source': 'command',
        })
        state = 'publicada' if row.published_text else 'borrador guardado'
        self.stdout.write(f'{company.slug} · {kind}: {state} ({len(row.draft_text)} caracteres).')
