"""
CATALOG-COMMERCIAL-01 · A1b — the normalized name of a model, unique per company (DEC-CATALOG-NAME-01,
D-A1b-1 approved by the owner on 2026-11-12; docs/adr-catalog-commercial.md §5 «Modelos»).

THE RULE: `normalized_name` = `name` in lower case, accents folded (NFKD without combining marks), one space
between words. Nothing else is taken out: a SIM type, a region, a capacity or a colour written inside the
name stays (reading those out needs the aliases of A2). `_fold` below is the rule inlined; the service
(`device_catalog.normalize_model_name`) is the same text, and a test holds the two together.

THREE STEPS, like 0115 (REGISTER-RACE):
  1. the column, NULL-able, so adding it rewrites nothing;
  2. `RunPython` that folds every model's name and STOPS — listing the company id, the model ids and
     their names — if two models of one company fold equal. It merges nothing, renames nothing, deletes
     nothing: which of the two stays is a person's decision (`python manage.py check_model_names` lists
     them before migrating). What it checked is printed;
  3. NOT NULL with `db_default=''` (metadata only in PostgreSQL 11+), an index, and the partial UNIQUE
     (company, normalized_name) WHERE normalized_name <> ''.

WHY `db_default=''` AND A PARTIAL UNIQUE. The rollback rule is «code first»: the previous release (A1,
master `a362a65`, without this column) keeps inserting and updating models against this schema. Its
INSERTs do not name the column, so the database writes '' — and '' is outside the rule, so nothing of the
old code can trip it. Those rows are listed by `check_model_names` and filled by the next `save()` of the
new code (`ProductModel.save` always writes the column).

Production (backup of 2026-11-12 00:56, restored in isolation): 31 models of 1 company, 0 collisions (the ADR
§4 figure of 12 models is from the backup of 2026-11-09; 19 models were created on 2026-11-10). Run `migrate`
with the usual `PGOPTIONS='-c lock_timeout=5s -c statement_timeout=30s'`, as 0164 and 0165. BEFORE it, and
after any window in which the previous release ran on this schema: `python manage.py check_model_names` must
exit 0 (`--fill` writes the keys the previous release left blank; it stops, like this migration, on a collision). Reversible: `migrate
store 0165` drops the constraint, the index and the column (tested forward → back → forward on the copy);
what is lost is the folded key only, never a model, a slug, a variant or a URL.
"""
import unicodedata

from django.db import migrations, models

_STOPPED = (
    'CATALOG-COMMERCIAL-01 · A1b: {how_many} grupo(s) de modelos de una misma empresa tienen el mismo nombre '
    'normalizado. La regla de unicidad no se puede crear y esta migración no fusiona, renombra ni borra nada. '
    'Decide cuál se conserva en cada grupo (desde el panel, Catálogo › Modelos) y vuelve a migrar.\n{groups}'
)


def _fold(text) -> str:
    """The rule: lower case, accents folded, whitespace collapsed, at most 160 characters."""
    text = unicodedata.normalize('NFKD', str(text or ''))
    return ' '.join(''.join(ch for ch in text if not unicodedata.combining(ch)).lower().split())[:160]


def _collisions(rows):
    """{(company_id, folded): [(model id, name)]} of the groups with more than one model."""
    groups = {}
    for pk, company_id, name in rows:
        # A blank name folds to '' — outside the rule (partial UNIQUE), so never a collision.
        if _fold(name):
            groups.setdefault((company_id, _fold(name)), []).append((pk, name))
    return {key: members for key, members in groups.items() if len(members) > 1}


def fill_normalized_names(apps, schema_editor):
    ProductModel = apps.get_model('store', 'ProductModel')
    rows = list(ProductModel.objects.order_by('pk').values_list('pk', 'company_id', 'name'))
    collisions = _collisions(rows)
    if collisions:
        lines = []
        for (company_id, _folded), members in sorted(collisions.items()):
            names = '; '.join(f'modelo {pk} «{name}»' for pk, name in members)
            lines.append(f'  empresa {company_id}: {names}')
        raise RuntimeError(_STOPPED.format(how_many=len(collisions), groups='\n'.join(lines)))
    filled = 0
    for pk, _company_id, name in rows:
        filled += ProductModel.objects.filter(pk=pk).exclude(normalized_name=_fold(name)).update(
            normalized_name=_fold(name))
    companies = len({company_id for _pk, company_id, _name in rows})
    print(f'\n  0166: {len(rows)} modelo(s) de {companies} empresa(s) comprobados, 0 colisiones de nombre '
          f'normalizado, {filled} fila(s) rellenadas.', end='')


def noop(apps, schema_editor):
    """Backwards: the column goes with the next operation; nothing to undo here."""


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0165_catalog_model_palette'),
    ]

    operations = [
        migrations.AddField(
            model_name='productmodel',
            name='normalized_name',
            field=models.CharField(blank=True, default='', max_length=160, null=True),
        ),
        migrations.RunPython(fill_normalized_names, noop),
        migrations.AlterField(
            model_name='productmodel',
            name='normalized_name',
            field=models.CharField(blank=True, db_default='', db_index=True, default='', max_length=160),
        ),
        migrations.AddConstraint(
            model_name='productmodel',
            constraint=models.UniqueConstraint(
                condition=models.Q(('normalized_name', ''), _negated=True), fields=('company', 'normalized_name'),
                name='unique_product_model_normalized_name_per_company'),
        ),
    ]
