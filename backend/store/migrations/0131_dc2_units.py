"""
DEVICE-COMMERCE-01 · DC2 — what a device says about itself, and web holds.

`store_cosmeticgrade` and `store_unitphoto` (new, empty). On
`store_stockunit`: `public_id` uuid UNIQUE with `db_default gen_random_uuid()`
(volatile, so PostgreSQL rewrites the — small — table and gives every
existing unit its own value), `battery_health` (nullable, CHECK <= 100),
`cosmetic_grade_id` (nullable FK), `public_notes` and `hold_key`
(`db_default` ''), `sale_price`, `hold_until`, `hold_price`, `hold_order_id`
(nullable). No company gets a grade scale written into it. The previous
release keeps receiving units: every column defaults in the database.
Reversible.
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
        ('store', '0130_dc2_variants'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CosmeticGrade',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.SlugField(max_length=40)),
                ('label', models.CharField(max_length=60)),
                ('description', models.CharField(blank=True, default='', max_length=300)),
                ('sort_order', models.PositiveIntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'ordering': ['sort_order', 'label', 'pk'],
            },
        ),
        migrations.CreateModel(
            name='UnitPhoto',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('image_url', models.CharField(max_length=255, validators=[store.models.validate_asset_url])),
                ('alt_text', models.CharField(blank=True, default='', max_length=160)),
                ('width', models.PositiveIntegerField(default=0)),
                ('height', models.PositiveIntegerField(default=0)),
                ('sort_order', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['sort_order', 'pk'],
            },
        ),
        migrations.AddField(
            model_name='stockunit',
            name='battery_health',
            field=models.PositiveSmallIntegerField(blank=True, null=True, validators=[django.core.validators.MaxValueValidator(100)]),
        ),
        migrations.AddField(
            model_name='stockunit',
            name='hold_key',
            field=models.CharField(blank=True, db_default='', default='', max_length=64),
        ),
        migrations.AddField(
            model_name='stockunit',
            name='hold_order',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='store.order'),
        ),
        migrations.AddField(
            model_name='stockunit',
            name='hold_price',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='stockunit',
            name='hold_until',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='stockunit',
            name='public_id',
            field=models.UUIDField(db_default=django.contrib.postgres.functions.RandomUUID(), default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.AddField(
            model_name='stockunit',
            name='public_notes',
            field=models.CharField(blank=True, db_default='', default='', max_length=500),
        ),
        migrations.AddField(
            model_name='stockunit',
            name='sale_price',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='cosmeticgrade',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cosmetic_grades', to='store.company'),
        ),
        migrations.AddField(
            model_name='stockunit',
            name='cosmetic_grade',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='units', to='store.cosmeticgrade'),
        ),
        migrations.AddConstraint(
            model_name='stockunit',
            constraint=models.CheckConstraint(condition=models.Q(('battery_health__isnull', True), ('battery_health__lte', 100), _connector='OR'), name='stock_unit_battery_health_percent'),
        ),
        migrations.AddField(
            model_name='unitphoto',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='store.company'),
        ),
        migrations.AddField(
            model_name='unitphoto',
            name='unit',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='photos', to='store.stockunit'),
        ),
        migrations.AddField(
            model_name='unitphoto',
            name='uploaded_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddConstraint(
            model_name='cosmeticgrade',
            constraint=models.UniqueConstraint(fields=('company', 'code'), name='unique_cosmetic_grade_per_company'),
        ),
        migrations.AddIndex(
            model_name='unitphoto',
            index=models.Index(fields=['company', 'unit'], name='store_unitp_company_f0d62f_idx'),
        ),
    ]
