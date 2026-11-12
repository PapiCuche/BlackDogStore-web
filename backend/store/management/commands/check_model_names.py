"""
CATALOG-COMMERCIAL-01 · A1b — the models whose names fold to one key (DEC-CATALOG-NAME-01), for a person
to decide BEFORE migration 0166 demands the rule; afterwards, the rows an older release left without the
key. Without `--fill` it is read-only: it merges, renames and deletes nothing.

EXIT CODE is the post-control: 0 when every model has its key and no two fold equal; 2 when there is a
collision or a blank key (the runbook runs it after migrating 0166 and after any window in which the
previous release wrote on this schema, and expects 0).

`--fill` writes the key of the rows the previous release left blank — and ONLY that: it stops with the
list, writing nothing, when two models of a company fold equal (the same text as migration 0166).

It prints company ids, model ids and model names only — a model name is catalogue text, never a person's
data — so its output may go into a deployment log.
"""
import sys

from django.core.management.base import BaseCommand
from django.db import connection, transaction

from store.device_catalog import normalize_model_name
from store.models import ProductModel

_STOPPED = (
    'CATALOG-COMMERCIAL-01 · A1b: {how_many} grupo(s) de modelos de una misma empresa tienen el mismo nombre '
    'normalizado. Esta orden no fusiona, renombra ni borra nada. Decide cuál se conserva en cada grupo (desde el '
    'panel, Catálogo › Modelos) y vuelve a ejecutarla.'
)


def _column_exists() -> bool:
    """Whether 0166 has run: before it, the collisions are still checked and the blank rows cannot be."""
    with connection.cursor() as cursor:
        names = {c.name for c in connection.introspection.get_table_description(cursor, 'store_productmodel')}
    return 'normalized_name' in names


class Command(BaseCommand):
    help = ('Lista los modelos de una misma empresa cuyo nombre normalizado coincide (antes o después de la '
            'migración 0166) y, después de ella, las filas sin clave. Sólo lectura; nombra modelos de todas '
            'las empresas (herramienta de operación, no de un usuario).')

    def add_arguments(self, parser):
        parser.add_argument('--fill', action='store_true',
                            help='Rellena la clave de las filas sin ella (sólo si no hay colisiones).')

    def handle(self, *args, **options):
        groups = {}
        blank = []
        migrated = _column_exists()
        if options['fill'] and not migrated:
            self.stdout.write('Columna «normalized_name» aún no migrada (0166 pendiente): nada que rellenar.')
            sys.exit(2)
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
            self.stdout.write(_STOPPED.format(how_many=len(collisions)))
            sys.exit(2)
        if options['fill'] and blank:
            with transaction.atomic():
                filled = 0
                for _company_id, pk, name in blank:
                    filled += ProductModel.objects.filter(pk=pk, normalized_name='').update(
                        normalized_name=normalize_model_name(name))
            self.stdout.write(f'{filled} fila(s) rellenadas.')
            blank = []
        if blank:
            self.stdout.write('Rellena las claves con «python manage.py check_model_names --fill» '
                              '(o guarda cada modelo desde el panel).')
            sys.exit(2)
        if not migrated:
            sys.exit(2)
