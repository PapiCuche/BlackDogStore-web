# SERIAL-TRANSFER-01 (docs/adr-serial-transfer.md §3.3). Additive: a status choice (no SQL) and a new, empty table.
# The previous release keeps working while this runs: it never writes `in_transit` and never reads the table.
#
# Backward STOPS while any transfer names a device or any device is in transit: the history of which device travelled
# on which document is never dropped in silence, and the previous release cannot receive a transfer of devices.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def noop(apps, schema_editor):
    pass


def refuse_devices_in_transfers(apps, schema_editor):
    StockTransferUnit = apps.get_model('store', 'StockTransferUnit')
    StockUnit = apps.get_model('store', 'StockUnit')
    rows = StockTransferUnit.objects.count()
    travelling = StockUnit.objects.filter(status='in_transit').count()
    if rows or travelling:
        raise RuntimeError(
            f'No se puede deshacer 0176: {rows} equipo(s) figuran en transferencias y {travelling} están en tránsito. '
            'Recibe las transferencias en tránsito con la versión nueva y conserva este esquema.'
        )


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0175_stock_unit_void'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='stockunit',
            name='status',
            field=models.CharField(choices=[('available', 'Disponible'), ('reserved', 'Apartado'), ('sold', 'Vendido'), ('written_off', 'Dado de baja'), ('voided', 'Anulado'), ('in_transit', 'En tránsito')], db_index=True, default='available', max_length=16),
        ),
        migrations.CreateModel(
            name='StockTransferUnit',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('method', models.CharField(choices=[('scanner', 'Lector'), ('manual', 'Ingreso manual')], max_length=16)),
                ('added_at', models.DateTimeField(auto_now_add=True)),
                ('received_method', models.CharField(blank=True, choices=[('scanner', 'Lector'), ('manual', 'Ingreso manual')], db_default='', default='', max_length=16)),
                ('received_at', models.DateTimeField(blank=True, null=True)),
                ('added_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='store.company')),
                ('item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='unit_rows', to='store.stocktransferitem')),
                ('received_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('transfer', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='unit_rows', to='store.stocktransfer')),
                ('unit', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='transfer_rows', to='store.stockunit')),
            ],
            options={
                'ordering': ['pk'],
                'indexes': [models.Index(fields=['unit', 'transfer'], name='transfer_unit_history_idx')],
                'constraints': [models.UniqueConstraint(fields=('transfer', 'unit'), name='unique_unit_per_transfer')],
            },
        ),
        # Backward only, and LAST so that it runs FIRST when reversing: before the table is dropped.
        migrations.RunPython(noop, refuse_devices_in_transfers),
    ]
