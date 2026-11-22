"""
CATALOG-F11 — give the capacity and colour roles and write capacities the catalogue way, for ONE company.

    python manage.py normalize_device_catalog --company <slug|id>                     # simulation (default)
    python manage.py normalize_device_catalog --company <slug|id> --apply --actor <user id> --expect <fingerprint>

The simulation writes nothing and prints the plan with its fingerprint. `--apply` needs an active user who may manage
products in that company (`products.manage`); it is audited under that user and that company, exactly as the panel
would be. `--expect` refuses to apply a plan that changed since the simulation. A second apply changes nothing.
See `store/catalog_normalize.py`.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from store import catalog_normalize as normalize
from store.models import Company
from store.tenancy import has_capability

STATUS = {'assign': 'se asigna', 'held': 'ya asignado', 'ambiguous': 'ambiguo, no se toca', 'missing': 'sin candidato',
          'change': 'cambia', 'unchanged': 'sin cambios', 'conflict': 'CONFLICTO, no se toca',
          'unreadable': 'no es una capacidad, no se toca'}


class Command(BaseCommand):
    help = 'Asigna los roles de capacidad y color y normaliza las capacidades de una empresa (simulación por defecto).'

    def add_arguments(self, parser):
        parser.add_argument('--company', required=True, help='Slug o id de la empresa.')
        parser.add_argument('--apply', action='store_true', help='Escribe los cambios (sin esto, sólo simula).')
        parser.add_argument('--actor', help='Id del usuario que firma la auditoría (con products.manage).')
        parser.add_argument('--expect', help='Huella del plan simulado (obligatoria con --apply): si cambió, no se aplica nada.')

    def handle(self, *args, **options):
        ref = str(options['company'])
        company = Company.objects.filter(slug=ref).first() or (
            Company.objects.filter(pk=int(ref)).first() if ref.isdigit() else None)
        if company is None:
            raise CommandError('Empresa no encontrada.')
        if not options['apply']:
            self.stdout.write(self._summary(company, normalize.plan(company), dry_run=True))
            return
        actor = None
        if options['actor'] and str(options['actor']).isdigit():
            actor = get_user_model().objects.filter(pk=int(options['actor']), is_active=True).first()
        if actor is None or not has_capability(actor, company, 'products.manage'):
            raise CommandError('--apply necesita --actor: un usuario activo con products.manage en esa empresa.')
        if not options['expect']:
            raise CommandError('--apply necesita --expect con la huella que mostró la simulación.')
        try:
            body = normalize.apply(company, actor=actor, expected=options['expect'])
        except normalize.StalePlan as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self._summary(company, body, dry_run=False))

    @staticmethod
    def _summary(company, body, *, dry_run) -> str:
        lines = [f'{company.name} · ' + ('simulación: no se escribió nada' if dry_run else 'aplicado')]
        for row in body['roles']:
            lines.append(f'rol {row["role"]}: {row["name"] or "-"} ({STATUS[row["status"]]})')
        for row in body['capacities']:
            text = f'capacidad {row["current"]} → {row["label"]} (orden {row["current_sort_order"]} → {row["sort_order"]})'
            if row['status'] != 'change':
                text = f'capacidad {row["current"]} (orden {row["current_sort_order"]})'
            lines.append(f'{text}: {STATUS[row["status"]]}' + (f' — {row["reason"]}' if row['reason'] else ''))
        lines.append(f'{body["changes"]} cambios, {body["skipped"]} sin tocar · huella {body["fingerprint"]}')
        return '\n'.join(lines)
