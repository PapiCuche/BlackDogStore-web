"""
LEGAL-POLICIES-01 — the three public legal documents of each company.

A NEW TABLE, nothing else: no column is added to an existing table, so the code
running before this release ignores it and a code-only rollback is safe.

Branches from 0164 (the schema in production) so it can ship on its own. Master
joins it to 0165–0168 with an empty merge migration (0169), the same way 0041
joined two branches.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0164_supplier_offer_reimport_controls'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CompanyLegalPolicy',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('privacy', 'Política de privacidad'), ('cookies', 'Política de cookies'), ('whatsapp', 'Política de WhatsApp y CRM')], max_length=20)),
                ('draft_text', models.TextField(blank=True, db_default='', default='', max_length=40000)),
                ('published_text', models.TextField(blank=True, db_default='', default='', max_length=40000)),
                ('published_at', models.DateTimeField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='legal_policies', to='store.company')),
                ('published_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('company', 'kind'), name='unique_legal_policy_per_company_kind')],
            },
        ),
    ]
