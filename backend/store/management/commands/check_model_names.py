"""
CATALOG-COMMERCIAL-01 · A1b — the models whose names fold to one key (DEC-CATALOG-NAME-01), for a person
to decide BEFORE migration 0166 demands the rule; afterwards, the rows an older release left without the
key. Without `--fill` it is read-only: it merges, renames and deletes nothing.

EXIT CODE is the gate: 0 when no two models of a company fold equal and (after 0166) every model has its
key; 2 when there is a collision or a blank or STALE key. Before 0166 only collisions count (the column
does not exist yet). The runbook runs it before migrating, after migrating, and after any window in which
the previous release wrote on this schema, and expects 0 each time.

A STALE key is what the previous release leaves when it RENAMES a model: its full UPDATE never names the
column, so the key still says the old name. Readers never follow a stored key alone (`find_model` folds the
name), the database refuses the old key to a new model, and this command lists and repairs it.

`--fill` writes the key of the rows the previous release left blank or stale — and ONLY that: it stops with
the list, writing nothing, when two models of a company fold equal (the same text as migration 0166). The
rows are locked while it looks and writes; a key committed meanwhile makes it stop the same way.

It prints company ids, model ids and model names only — a model name is catalogue text, never a person's
data — so its output may go into a deployment log.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, connection, transaction

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

    def _scan(self, migrated, *, lock=False):
        """(total, groups, blank, stale) over every model; `lock` takes the rows (inside a transaction)."""
        groups, blank, stale, total = {}, [], [], 0
        rows = ProductModel.objects.order_by('company_id', 'pk')
        if lock:
            rows = rows.select_for_update()
        fields = ('pk', 'company_id', 'name') + (('normalized_name',) if migrated else ())
        for row in rows.values_list(*fields):
            pk, company_id, name = row[:3]
            total += 1
            key = normalize_model_name(name)
            if key:
                groups.setdefault((company_id, key), []).append((pk, name))
            if migrated and not row[3]:
                blank.append((company_id, pk, name, row[3]))
            elif migrated and row[3] != key:
                stale.append((company_id, pk, name, row[3]))
        return total, groups, blank, stale

    def handle(self, *args, **options):
        migrated = _column_exists()
        with transaction.atomic():
            total, groups, blank, stale = self._scan(migrated, lock=options['fill'] and migrated)
            collisions = {key: members for key, members in groups.items() if len(members) > 1}
            companies = len({company_id for company_id, _folded in groups})
            self.stdout.write(f'{total} modelo(s) de {companies} empresa(s) comprobados; '
                              f'{len(collisions)} grupo(s) con el mismo nombre normalizado.')
            for (company_id, folded), members in sorted(collisions.items()):
                self.stdout.write(f'--- empresa {company_id}: «{folded}»')
                for pk, name in members:
                    self.stdout.write(f'  modelo {pk} «{name}»')
            if not migrated:
                self.stdout.write('Columna «normalized_name» aún no migrada (0166 pendiente): las claves se '
                                  'comprueban después de migrar.')
            else:
                self.stdout.write(f'{len(blank)} modelo(s) sin nombre normalizado y {len(stale)} modelo(s) con la '
                                  'clave desfasada (filas escritas por una versión anterior).')
                for company_id, pk, name, _stored in blank + stale:
                    self.stdout.write(f'  empresa {company_id}: modelo {pk} «{name}»')
            if collisions:
                raise CommandError(_STOPPED.format(how_many=len(collisions)), returncode=2)
            pending = blank + stale
            if options['fill'] and pending:
                filled = 0
                try:
                    for _company_id, pk, name, stored in pending:
                        filled += ProductModel.objects.filter(pk=pk, normalized_name=stored).update(
                            normalized_name=normalize_model_name(name))
                except IntegrityError:
                    raise CommandError(_STOPPED.format(how_many='uno o más'), returncode=2) from None
                self.stdout.write(f'{filled} fila(s) rellenadas.')
                pending = []
        if pending:
            raise CommandError('Rellena las claves con «python manage.py check_model_names --fill» '
                               '(o guarda cada modelo desde el panel).', returncode=2)
