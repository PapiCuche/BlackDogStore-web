"""
UNIT-PHOTO-02 — what the photo rule of used devices changes, counted (for the release rehearsal on the copy of
production, before and after migrating 0192).

    python manage.py unit_photo_report [--company <slug>] [--format text|json]

READ-ONLY: it writes nothing and audits nothing. From 0192 a photo of a used device counts only when it is attested
(«esta foto es de este equipo exacto») and its file is an own photo of the company with its licence verified
(`unit_media.public_photos_q`). `published_leaving_the_web`: devices in the shop's hands, published, that have photos
but none public — the web stops offering them until somebody attests one. Counts only: no identifier, no amount.
"""
import json

from django.core.management.base import BaseCommand, CommandError

from store import unit_media
from store.models import Company


class Command(BaseCommand):
    help = 'Cuenta los seminuevos publicados que salen de la web hasta que se confirme una foto real del equipo.'

    def add_arguments(self, parser):
        parser.add_argument('--company', help='Slug de la empresa (sin él, todas).')
        parser.add_argument('--format', choices=('text', 'json'), default='text')

    def handle(self, *args, **options):
        company = None
        if options.get('company'):
            company = Company.objects.filter(slug=options['company']).first()
            if company is None:
                raise CommandError(f'No existe la empresa «{options["company"]}».')
        counts = unit_media.report(company)
        if options['format'] == 'json':
            self.stdout.write(json.dumps({'company': company.slug if company else None, **counts}))
            return
        scope = company.slug if company else 'todas las empresas'
        self.stdout.write(
            f'{scope}: {counts["used_units"]} seminuevo(s); {counts["published_units"]} publicado(s) en la tienda, '
            f'{counts["published_with_public_photo"]} con foto confirmada, '
            f'{counts["published_leaving_the_web"]} salen de la web hasta confirmar una foto, '
            f'{counts["published_without_any_photo"]} sin ninguna foto. Fotos: {counts["photos"]}, '
            f'{counts["photos_public"]} públicas, {counts["photos_waiting_attestation"]} sin confirmar.')
