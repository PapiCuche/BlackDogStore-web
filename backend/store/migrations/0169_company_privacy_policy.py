"""Add editable tenant-owned privacy policy text. Empty by default: never publish draft legal claims."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('store', '0168_catalog_aliases')]

    operations = [
        migrations.AddField(
            model_name='companysettings',
            name='privacy_policy_text',
            field=models.TextField(blank=True, default='', max_length=12000),
        ),
    ]
