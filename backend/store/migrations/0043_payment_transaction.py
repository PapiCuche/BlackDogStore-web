"""
P0-F — the payments table, added BEFORE anything is removed.

Three migrations do this move, and the order is the whole point:

    0043  create PaymentTransaction, widen PaymentMethod
    0044  copy the previous gateway's identifiers into it
    0045  drop the previous gateway's columns

Collapsing them into one — which is what `makemigrations` proposed — would drop
the columns in the same operation that creates their replacement, and the data
migration in between would have nothing left to read. A migration that destroys
the only copy of something before the copy exists is not reversible in the way
that matters: the rows are gone.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0042_enforce_line_uniqueness'),
    ]

    operations = [
        migrations.AlterField(
            model_name='order',
            name='payment_method',
            field=models.CharField(choices=[('online', 'Pago en línea'), ('cash', 'Efectivo'), ('card', 'Tarjeta'), ('transfer', 'Transferencia'), ('other', 'Otro')], db_index=True, default='online', max_length=16),
        ),
        migrations.CreateModel(
            name='PaymentTransaction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('provider', models.CharField(db_index=True, max_length=32)),
                ('transaction_id', models.CharField(max_length=64)),
                ('order_number', models.CharField(blank=True, default=None, max_length=64, null=True, unique=True)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('currency', models.CharField(max_length=3)),
                ('status', models.CharField(choices=[('pending', 'Pendiente'), ('authorized', 'Autorizada'), ('rejected', 'Rechazada'), ('integrity_failed', 'Falla de integridad')], db_index=True, default='pending', max_length=20)),
                ('payment_method', models.CharField(blank=True, max_length=32)),
                ('authorization_code', models.CharField(blank=True, max_length=32)),
                ('reference_number', models.CharField(blank=True, max_length=64)),
                ('provider_unique_id', models.CharField(blank=True, max_length=64)),
                ('response_code', models.CharField(blank=True, max_length=8)),
                ('signature_verified', models.BooleanField(default=False)),
                ('failure_reason', models.CharField(blank=True, max_length=200)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('confirmed_at', models.DateTimeField(blank=True, null=True)),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='payment_transactions', to='store.order')),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['order', 'created_at'], name='store_payme_order_i_0880a2_idx')],
                'constraints': [models.UniqueConstraint(fields=('provider', 'transaction_id'), name='unique_payment_transaction_per_provider')],
            },
        ),
    ]
