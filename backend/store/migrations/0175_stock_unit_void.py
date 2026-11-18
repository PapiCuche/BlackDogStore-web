# INV-UNIT-CORRECT-01 · «Anular ingreso» (docs/adr-unit-correction.md §6).
#
# Forward: a new status `voided` (choices only, no SQL), three nullable / db_default columns, and the three unique
# constraints re-created PARTIAL (excluding voided rows). The new constraints are strictly weaker than the old ones:
# every row that satisfied the old ones satisfies these, so creating them cannot fail on existing data.
# LOCKS: dropping a constraint takes ACCESS EXCLUSIVE on store_stockunit, held for the whole (atomic) migration —
# three unique index builds and a validating CHECK — so it blocks READS and writes of the table meanwhile. Run
# `migrate` with `lock_timeout` and size the table in the rehearsal; if it is large, build the indexes CONCURRENTLY
# in a non-atomic migration instead.
#
# Backward: before the FULL constraints come back, `refuse_shared_identifiers` STOPS if two rows share a serial per
# product or an IMEI / IMEI 2 per company (a voided row and the device that re-entered with its identifiers). It lists
# the company and the row ids, never an identifier. Without it the reversal would fail with a bare IntegrityError.

from django.conf import settings
from django.db import migrations, models
from django.db.models import Count
import django.db.models.deletion


def noop(apps, schema_editor):
    pass


def refuse_shared_identifiers(apps, schema_editor):
    StockUnit = apps.get_model('store', 'StockUnit')
    found = []
    for fields in (('company_id', 'product_id', 'serial_number'), ('company_id', 'imei'), ('company_id', 'imei2')):
        key = fields[-1]
        groups = (StockUnit.objects.filter(**{f'{key}__isnull': False}).values(*fields)
                  .annotate(n=Count('pk')).filter(n__gt=1))
        for group in groups:
            filters = {name: group[name] for name in fields}
            ids = sorted(StockUnit.objects.filter(**filters).values_list('pk', flat=True))
            found.append(f'empresa {group["company_id"]}, {key}: equipos {ids}')
    if found:
        raise RuntimeError(
            'No se puede deshacer 0175: estos equipos comparten un identificador (uno anulado y el que volvió a '
            'entrar). Resuélvelo antes de volver atrás: ' + '; '.join(found)
        )


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0174_stock_unit_serial_optional'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='stockunit',
            name='unique_stock_unit_serial_per_product',
        ),
        migrations.RemoveConstraint(
            model_name='stockunit',
            name='unique_stock_unit_imei_per_company',
        ),
        migrations.RemoveConstraint(
            model_name='stockunit',
            name='unique_stock_unit_imei2_per_company',
        ),
        # Backward only: runs after the partial constraints are gone and before the full ones come back.
        migrations.RunPython(noop, refuse_shared_identifiers),
        migrations.AddField(
            model_name='stockunit',
            name='void_reason',
            field=models.CharField(blank=True, db_default='', default='', max_length=300),
        ),
        migrations.AddField(
            model_name='stockunit',
            name='voided_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='stockunit',
            name='voided_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='stockunit',
            name='status',
            field=models.CharField(choices=[('available', 'Disponible'), ('reserved', 'Apartado'), ('sold', 'Vendido'), ('written_off', 'Dado de baja'), ('voided', 'Anulado')], db_index=True, default='available', max_length=16),
        ),
        migrations.AddConstraint(
            model_name='stockunit',
            constraint=models.UniqueConstraint(condition=models.Q(('status', 'voided'), _negated=True), fields=('company', 'product', 'serial_number'), name='unique_stock_unit_serial_per_product'),
        ),
        migrations.AddConstraint(
            model_name='stockunit',
            constraint=models.UniqueConstraint(condition=models.Q(('imei__isnull', False), models.Q(('status', 'voided'), _negated=True)), fields=('company', 'imei'), name='unique_stock_unit_imei_per_company'),
        ),
        migrations.AddConstraint(
            model_name='stockunit',
            constraint=models.UniqueConstraint(condition=models.Q(('imei2__isnull', False), models.Q(('status', 'voided'), _negated=True)), fields=('company', 'imei2'), name='unique_stock_unit_imei2_per_company'),
        ),
        migrations.AddConstraint(
            model_name='stockunit',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('status', 'voided'), _negated=True), ('voided_at__isnull', False), _connector='OR'), name='stock_unit_voided_has_date'),
        ),
    ]
