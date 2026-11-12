"""
CATALOG-COMMERCIAL-01 · A1b — the models whose names fold to one key (DEC-CATALOG-NAME-01), for a person
to decide BEFORE migration 0166 demands the rule; afterwards, the rows an older release left without the
key. Read-only: it merges, renames and deletes nothing.

It prints company ids, model ids and model names only — a model name is catalogue text, never a person's
data — so its output may go into a deployment log.
"""
from django.core.management.base import BaseCommand
from django.db import connection

from store.device_catalog import normalize_model_name
from store.models import ProductModel


def _column_exists() -> bool:
    """Whether 0166 has run: before it, the collisions are still checked and the blank rows cannot be."""
    with connection.cursor() as cursor:
        names = {c.name for c in connection.introspection.get_table_description(cursor, 'store_productmodel')}
    return 'normalized_name' in names


class Command(BaseCommand):
    help = ('Lista los modelos de una misma empresa cuyo nombre normalizado coincide (antes o después de la '
            'migración 0166) y, después de ella, las filas sin clave. Sólo lectura; nombra modelos de todas '
            'las empresas (herramienta de operación, no de un usuario).')

    def handle(self, *args, **options):
        groups = {}
        blank = []
        migrated = _column_exists()
        fields = ('pk', 'company_id', 'name') + (('normalized_name',) if migrated else ())
        total = 0
        for row in ProductModel.objects.order_by('company_id', 'pk').values_list(*fields):
            pk, company_id, name = row[:3]
            total += 1
            key = normalize_model_name(name)
            if key:
                groups.setdefault((company_id, key), []).append((pk, name))
            if migrated and not row[3]:
                blank.append((company_id, pk, name))
        collisions = {key: members for key, members in groups.items() if len(members) > 1}
        companies = len({company_id for company_id, _folded in groups})
        self.stdout.write(f'{total} modelo(s) de {companies} empresa(s) comprobados; '
                          f'{len(collisions)} grupo(s) con el mismo nombre normalizado.')
        for (company_id, folded), members in sorted(collisions.items()):
            self.stdout.write(f'--- empresa {company_id}: «{folded}»')
            for pk, name in members:
                self.stdout.write(f'  modelo {pk} «{name}»')
        if not migrated:
            self.stdout.write('Columna «normalized_name» aún no migrada (0166 pendiente): las filas sin clave se '
                              'listan después de migrar.')
        else:
            self.stdout.write(f'{len(blank)} modelo(s) sin nombre normalizado (filas escritas por una versión '
                              'anterior; se rellenan al guardarlos).')
        for company_id, pk, name in blank:
            self.stdout.write(f'  empresa {company_id}: modelo {pk} «{name}»')
        if collisions:
            self.stdout.write('No se cambió nada. Decide qué modelo se conserva en cada grupo antes de migrar.')
