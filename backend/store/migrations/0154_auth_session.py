"""
AUTH-B (SESSION-IDLE-01): one row per signed-in browser.

Follows 0153 (fiscal, #179). Additive: the code before this one never reads the
table, so it can be applied while the previous version is still serving.
"""

import django.db.models.deletion
import django.db.models.functions.datetime
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0153_fiscal_cdr_evidence'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AuthSession',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sid', models.UUIDField(unique=True)),
                ('channel', models.CharField(choices=[('web', 'Web')], default='web', max_length=8)),
                ('kind', models.CharField(choices=[('customer', 'Cliente'), ('staff', 'Personal')], default='customer', max_length=10)),
                ('login_method', models.CharField(choices=[('password', 'Contraseña'), ('google', 'Google')], max_length=16)),
                ('created_at', models.DateTimeField(db_default=django.db.models.functions.datetime.Now())),
                ('last_activity_at', models.DateTimeField(db_default=django.db.models.functions.datetime.Now())),
                ('absolute_expires_at', models.DateTimeField()),
                ('ended_at', models.DateTimeField(blank=True, null=True)),
                ('end_reason', models.CharField(blank=True, choices=[('logout', 'Cerró sesión'), ('idle', 'Inactividad'), ('absolute', 'Tope absoluto'), ('password', 'Contraseña cambiada o restablecida'), ('deactivated', 'Cuenta desactivada')], max_length=16)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='auth_sessions', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'indexes': [models.Index(fields=['user', 'ended_at'], name='store_authsession_user_ended')],
            },
        ),
    ]
