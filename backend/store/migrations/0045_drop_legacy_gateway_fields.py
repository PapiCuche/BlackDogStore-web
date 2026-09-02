"""
P0-F — drop the previous gateway's columns.

Safe only because 0044 already copied what they held into PaymentTransaction.
Runs last for that reason.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0044_carry_legacy_payments'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='order',
            name='stripe_payment_intent_id',
        ),
        migrations.RemoveField(
            model_name='order',
            name='stripe_session_id',
        ),
    ]
