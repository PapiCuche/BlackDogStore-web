"""
DEVICE-COMMERCE-01 · DC2 — models, options and variants.

New tables (ProductModel, VariantAttribute, VariantOption,
ProductVariantOption) and three columns on `store_product`: a nullable FK,
`condition_line` (`db_default` 'new') and `variant_key` (`db_default` ''),
plus a partial unique index on (product_model, condition_line, variant_key)
WHERE product_model IS NOT NULL — no existing row has a model, so it builds
over nothing. Two text columns on the storefront page settings (`db_default`
''). Schema only, no data. The previous release keeps inserting products and
settings rows: every new column has a database default or is nullable.
Reversible: the reverse drops what this adds.
"""
import django.contrib.postgres.functions
import django.core.validators
import django.db.models.deletion
import store.models
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0129_dc1_uncosted_stock'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ProductModel',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=160)),
                ('slug', models.SlugField(max_length=120)),
                ('description', models.TextField(blank=True, default='')),
                ('image_url', models.CharField(blank=True, default='', max_length=500, validators=[store.models.validate_asset_url])),
                ('is_published_online', models.BooleanField(default=True)),
                ('sort_order', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['sort_order', 'name', 'pk'],
            },
        ),
        migrations.CreateModel(
            name='ProductVariantOption',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ],
        ),
        migrations.CreateModel(
            name='VariantAttribute',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=60)),
                ('kind', models.CharField(choices=[('text', 'Texto'), ('color', 'Color')], default='text', max_length=8)),
                ('sort_order', models.PositiveIntegerField(default=0)),
            ],
            options={
                'ordering': ['sort_order', 'name', 'pk'],
            },
        ),
        migrations.CreateModel(
            name='VariantOption',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('label', models.CharField(max_length=60)),
                ('sort_order', models.PositiveIntegerField(default=0)),
                ('swatch_hex', models.CharField(blank=True, default='', max_length=7, validators=[store.models.validate_swatch_hex])),
            ],
            options={
                'ordering': ['sort_order', 'label', 'pk'],
            },
        ),
        migrations.AddField(
            model_name='product',
            name='condition_line',
            field=models.CharField(choices=[('new', 'Nuevo'), ('used', 'Seminuevo')], db_default='new', default='new', max_length=8),
        ),
        migrations.AddField(
            model_name='product',
            name='variant_key',
            field=models.CharField(blank=True, db_default='', default='', max_length=120),
        ),
        migrations.AddField(
            model_name='storefrontpagesettings',
            name='new_line_label',
            field=models.CharField(blank=True, db_default='', default='', max_length=40),
        ),
        migrations.AddField(
            model_name='storefrontpagesettings',
            name='used_line_label',
            field=models.CharField(blank=True, db_default='', default='', max_length=40),
        ),
        migrations.AddField(
            model_name='productmodel',
            name='category',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='product_models', to='store.category'),
        ),
        migrations.AddField(
            model_name='productmodel',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='product_models', to='store.company'),
        ),
        migrations.AddField(
            model_name='product',
            name='product_model',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='variants', to='store.productmodel'),
        ),
        migrations.AddConstraint(
            model_name='product',
            constraint=models.UniqueConstraint(condition=models.Q(('product_model__isnull', False)), fields=('product_model', 'condition_line', 'variant_key'), name='unique_variant_per_model_line'),
        ),
        migrations.AddField(
            model_name='productvariantoption',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='store.company'),
        ),
        migrations.AddField(
            model_name='productvariantoption',
            name='product',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='variant_options', to='store.product'),
        ),
        migrations.AddField(
            model_name='variantattribute',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='variant_attributes', to='store.company'),
        ),
        migrations.AddField(
            model_name='productvariantoption',
            name='attribute',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='store.variantattribute'),
        ),
        migrations.AddField(
            model_name='variantoption',
            name='attribute',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='options', to='store.variantattribute'),
        ),
        migrations.AddField(
            model_name='variantoption',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='variant_options', to='store.company'),
        ),
        migrations.AddField(
            model_name='productvariantoption',
            name='option',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='product_links', to='store.variantoption'),
        ),
        migrations.AddConstraint(
            model_name='productmodel',
            constraint=models.UniqueConstraint(fields=('company', 'slug'), name='unique_product_model_slug_per_company'),
        ),
        migrations.AddConstraint(
            model_name='variantattribute',
            constraint=models.UniqueConstraint(fields=('company', 'name'), name='unique_variant_attribute_per_company'),
        ),
        migrations.AddConstraint(
            model_name='variantoption',
            constraint=models.UniqueConstraint(fields=('attribute', 'label'), name='unique_variant_option_per_attribute'),
        ),
        migrations.AddConstraint(
            model_name='productvariantoption',
            constraint=models.UniqueConstraint(fields=('product', 'attribute'), name='unique_variant_attribute_per_product'),
        ),
    ]
