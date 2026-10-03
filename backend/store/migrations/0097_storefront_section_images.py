# Generated for Storefront V4 follow-up: editorial image slots.

import store.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0096_storefront_images'),
    ]

    operations = [
        migrations.AddField(
            model_name='storefrontpagesettings',
            name='services_image_url',
            field=models.CharField(
                blank=True,
                default='',
                max_length=500,
                validators=[store.models.validate_asset_url],
            ),
        ),
        migrations.AddField(
            model_name='storefrontpagesettings',
            name='location_image_url',
            field=models.CharField(
                blank=True,
                default='',
                max_length=500,
                validators=[store.models.validate_asset_url],
            ),
        ),
    ]
