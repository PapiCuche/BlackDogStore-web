"""
Phase 3, step 1 of 2 — SCHEMA ONLY.

  0027  CompanySettings + Order.company_snapshot          (this file)
  0028  move the pilot's hardcoded identity into its own settings row, and
        backfill the snapshot of every existing order

Running 0027 alone changes nothing visible: every new column is nullable or
defaulted, and nothing reads them until 0028 has filled them.
"""


import django.db.models.deletion
import store.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0026_multibranch_inventory_required'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='company_snapshot',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.CreateModel(
            name='CompanySettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('contact_email', models.EmailField(blank=True, max_length=254)),
                ('phone', models.CharField(blank=True, max_length=30)),
                ('whatsapp_number', models.CharField(blank=True, help_text='Solo dígitos, con código de país y sin "+". Ej: 51987654321', max_length=20, validators=[store.models.validate_whatsapp_number])),
                ('website_url', models.URLField(blank=True, max_length=300)),
                ('facebook_url', models.URLField(blank=True, max_length=300)),
                ('instagram_url', models.URLField(blank=True, max_length=300)),
                ('legal_address', models.CharField(blank=True, max_length=300)),
                ('city', models.CharField(blank=True, max_length=120)),
                ('country_code', models.CharField(blank=True, default='', max_length=2)),
                ('logo_url', models.URLField(blank=True, max_length=500)),
                ('primary_color', models.CharField(blank=True, max_length=7, validators=[store.models.validate_hex_color])),
                ('accent_color', models.CharField(blank=True, max_length=7, validators=[store.models.validate_hex_color])),
                ('background_color', models.CharField(blank=True, max_length=7, validators=[store.models.validate_hex_color])),
                ('surface_color', models.CharField(blank=True, max_length=7, validators=[store.models.validate_hex_color])),
                ('text_color', models.CharField(blank=True, max_length=7, validators=[store.models.validate_hex_color])),
                ('border_color', models.CharField(blank=True, max_length=7, validators=[store.models.validate_hex_color])),
                ('timezone', models.CharField(blank=True, help_text='Zona horaria IANA. Ej: America/Lima', max_length=64, validators=[store.models.validate_timezone_name])),
                ('currency', models.CharField(blank=True, default='PEN', max_length=3)),
                ('warranty_policy_text', models.TextField(blank=True, max_length=2000)),
                ('warranty_policy_url', models.URLField(blank=True, max_length=300)),
                ('terms_url', models.URLField(blank=True, max_length=300)),
                ('privacy_url', models.URLField(blank=True, max_length=300)),
                ('order_notification_email', models.EmailField(blank=True, max_length=254)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('company', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='settings', to='store.company')),
            ],
            options={
                'verbose_name': 'company settings',
                'verbose_name_plural': 'company settings',
            },
        ),
    ]
