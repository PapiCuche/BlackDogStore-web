"""
INV-DRAFT-01 — the server-side draft of «Recibir mercadería»: `ReceiptDraft`.

ONE NEW TABLE, nothing else: no column is added to an existing table, no row of another table is read or
written. The previous release never reads the table, so a rollback is code-only and keeps the schema.
`migrate store 0169` drops the table: what is lost is the unfinished receptions people were typing (each one
expires 24 h after its last save anyway), never stock, a unit, a Kardex line or a label.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0169_merge_legal_policies'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ReceiptDraft',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('payload', models.JSONField(default=dict)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('expires_at', models.DateTimeField(db_index=True)),
                ('branch', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='store.branch')),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='store.company')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('company', 'user'), name='unique_receipt_draft_per_company_user')],
            },
        ),
    ]
