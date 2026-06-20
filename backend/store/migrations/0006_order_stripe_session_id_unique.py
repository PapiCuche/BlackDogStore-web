from django.db import migrations, models


def convert_empty_stripe_session_id_to_null(apps, schema_editor):
    """Convert '' to None so the unique constraint can be added safely."""
    Order = apps.get_model('store', 'Order')
    Order.objects.filter(stripe_session_id='').update(stripe_session_id=None)


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0005_order_status_stripe_fields'),
    ]

    operations = [
        # Step 1: allow null (required before data migration)
        migrations.AlterField(
            model_name='order',
            name='stripe_session_id',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        # Step 2: convert existing '' to None
        migrations.RunPython(
            convert_empty_stripe_session_id_to_null,
            reverse_code=migrations.RunPython.noop,
        ),
        # Step 3: add unique constraint (unique implies index, replaces old db_index)
        migrations.AlterField(
            model_name='order',
            name='stripe_session_id',
            field=models.CharField(blank=True, max_length=255, null=True, unique=True, default=None),
        ),
    ]
