"""
CATALOG-COMMERCIAL-01 · A1b — the models whose names fold to one key (DEC-CATALOG-NAME-01), for a person
to decide BEFORE migration 0166 demands the rule; afterwards, the rows an older release left without the
key. Read-only: it merges, renames and deletes nothing.

It prints company ids, model ids and model names only — a model name is catalogue text, never a person's
data — so its output may go into a deployment log.
"""
from django.core.management.base import BaseCommand

from store.device_catalog import normalize_model_name
from store.models import ProductModel


class Command(BaseCommand):
    help = 'Lista los modelos de una misma empresa cuyo nombre normalizado coincide. Sólo lectura.'

    def handle(self, *args, **options):
        groups = {}
        blank = []
        for pk, company_id, name, stored in (ProductModel.objects.order_by('company_id', 'pk')
                                             .values_list('pk', 'company_id', 'name', 'normalized_name')):
            groups.setdefault((company_id, normalize_model_name(name)), []).append((pk, name))
            if not stored:
                blank.append((company_id, pk, name))
        collisions = {key: members for key, members in groups.items() if len(members) > 1}
        total = len(ProductModel.objects.all())
        companies = len({company_id for company_id, _folded in groups})
        self.stdout.write(f'{total} modelo(s) de {companies} empresa(s) comprobados; '
                          f'{len(collisions)} grupo(s) con el mismo nombre normalizado.')
        for (company_id, folded), members in sorted(collisions.items()):
            self.stdout.write(f'--- empresa {company_id}: «{folded}»')
            for pk, name in members:
                self.stdout.write(f'  modelo {pk} «{name}»')
        self.stdout.write(f'{len(blank)} modelo(s) sin nombre normalizado (filas escritas por una versión anterior; '
                          'se rellenan al guardarlos).')
        for company_id, pk, name in blank:
            self.stdout.write(f'  empresa {company_id}: modelo {pk} «{name}»')
        if collisions:
            self.stdout.write('No se cambió nada. Decide qué modelo se conserva en cada grupo antes de migrar.')
