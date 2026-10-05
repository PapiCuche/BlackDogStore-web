"""
TRACKING — every repair order that predates tracking links gets its own.

From this phase on an order is born with its link (`create_repair_order`). The
orders that already existed have none, and the staff panel reads the link
without creating it — a GET writes nothing — so without this they would all
show "no active link".

Only orders with NO link row at all. An order whose link was revoked keeps
having none: that was somebody's decision.

Nothing to undo on rollback: 0103 owns the table.
"""
import secrets

from django.db import migrations


def backfill(apps, schema_editor):
    RepairOrder = apps.get_model('store', 'RepairOrder')
    RepairTrackingLink = apps.get_model('store', 'RepairTrackingLink')

    linked = RepairTrackingLink.objects.values_list('repair_order_id', flat=True)
    missing = RepairOrder.objects.exclude(pk__in=linked).values_list('pk', 'company_id')
    batch = [
        RepairTrackingLink(
            company_id=company_id, repair_order_id=order_id, uid=secrets.token_hex(16),
        )
        for order_id, company_id in missing.iterator()
    ]
    RepairTrackingLink.objects.bulk_create(batch, batch_size=500)
    if batch and schema_editor is not None:
        print(f'\n  TRACKING — enlace de seguimiento creado para {len(batch)} orden(es) anterior(es)')


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0108_external_identity'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
