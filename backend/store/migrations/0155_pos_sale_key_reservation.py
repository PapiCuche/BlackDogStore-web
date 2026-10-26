"""
AUTH-C (#184 review): the counter-sale keys a cashier recorded as «no se cobró».

Additive and reversible (a new table; nothing reads it before this version).
After 0154_auth_session; the FIN phase starts at 0156.
"""

import django.db.models.deletion
import django.db.models.functions.datetime
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0154_auth_session'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PosSaleKeyReservation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('idempotency_key', models.CharField(max_length=64)),
                ('decision', models.CharField(choices=[('not_charged', 'No se cobró')], default='not_charged', max_length=16)),
                ('decided_at', models.DateTimeField(db_default=django.db.models.functions.datetime.Now())),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pos_key_reservations', to='store.company')),
                ('decided_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='pos_key_reservations', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('company', 'idempotency_key'), name='unique_pos_key_reservation')],
            },
        ),
    ]
