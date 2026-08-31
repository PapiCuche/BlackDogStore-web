"""
Phase 2D, step 1 of 3 — SCHEMA ONLY, nothing is populated here.

The three-step shape is the same one Phases 2B and 2C used, for the same
reason: a column cannot be born NOT NULL on a table that already has rows.

  0024  add the tables and the nullable columns          (this file)
  0025  backfill them from the data that already exists
  0026  tighten StockMovement.company / branch to NOT NULL

Running 0024 alone leaves the installation working exactly as before: nothing
reads the new columns until 0025 has filled them.
"""


import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0023_commerce_company_required'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='StockTransferItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.PositiveIntegerField()),
            ],
            options={
                'ordering': ['product__name'],
            },
        ),
        migrations.AddField(
            model_name='company',
            name='default_inventory_branch',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='fulfilling_companies', to='store.branch'),
        ),
        migrations.AddField(
            model_name='membership',
            name='branch_access_mode',
            field=models.CharField(choices=[('all', 'Todas las sucursales'), ('selected', 'Sucursales seleccionadas')], db_index=True, default='all', max_length=20),
        ),
        migrations.AddField(
            model_name='order',
            name='fulfillment_branch',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='fulfilled_orders', to='store.branch'),
        ),
        migrations.AddField(
            model_name='stockmovement',
            name='branch',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='stock_movements', to='store.branch'),
        ),
        migrations.AddField(
            model_name='stockmovement',
            name='company',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='stock_movements', to='store.company'),
        ),
        migrations.AlterField(
            model_name='stockmovement',
            name='movement_type',
            field=models.CharField(choices=[('initial_stock', 'Stock inicial'), ('purchase_entry', 'Entrada por compra'), ('manual_entry', 'Entrada manual'), ('return_entry', 'Entrada por devolución'), ('correction_positive', 'Corrección positiva'), ('transfer_in', 'Entrada por transferencia'), ('manual_exit', 'Salida manual'), ('sale_exit', 'Salida por venta'), ('correction_negative', 'Corrección negativa'), ('damaged_exit', 'Salida por daño / merma'), ('service_exit', 'Salida por servicio técnico'), ('transfer_out', 'Salida por transferencia')], db_index=True, max_length=40),
        ),
        migrations.CreateModel(
            name='BranchStock',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.PositiveIntegerField(default=0)),
                ('minimum_stock', models.PositiveIntegerField(default=0)),
                ('target_stock', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('branch', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='stock_levels', to='store.branch')),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='branch_stocks', to='store.product')),
            ],
            options={
                'ordering': ['branch__name', 'product__name'],
            },
        ),
        migrations.CreateModel(
            name='InventoryCount',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('draft', 'Borrador'), ('counting', 'En conteo'), ('review', 'En revisión'), ('approved', 'Aprobado'), ('cancelled', 'Anulado')], db_index=True, default='draft', max_length=20)),
                ('reason', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
                ('cancelled_at', models.DateTimeField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('approved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='inventory_counts_approved', to=settings.AUTH_USER_MODEL)),
                ('branch', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='inventory_counts', to='store.branch')),
                ('cancelled_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='inventory_counts_cancelled', to=settings.AUTH_USER_MODEL)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='inventory_counts', to='store.company')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='inventory_counts_created', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.AddField(
            model_name='stockmovement',
            name='inventory_count',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='stock_movements', to='store.inventorycount'),
        ),
        migrations.CreateModel(
            name='InventoryCountItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('theoretical_at_start', models.IntegerField(default=0)),
                ('physical_quantity', models.PositiveIntegerField(blank=True, null=True)),
                ('theoretical_at_approval', models.IntegerField(blank=True, null=True)),
                ('difference', models.IntegerField(blank=True, null=True)),
                ('note', models.CharField(blank=True, max_length=250)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('count', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='store.inventorycount')),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='count_items', to='store.product')),
            ],
            options={
                'ordering': ['product__name'],
            },
        ),
        migrations.CreateModel(
            name='MembershipBranchAccess',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('branch', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='membership_access', to='store.branch')),
                ('granted_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='branch_access_granted', to=settings.AUTH_USER_MODEL)),
                ('membership', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='branch_access', to='store.membership')),
            ],
            options={
                'verbose_name_plural': 'membership branch access',
                'ordering': ['membership__company__name', 'branch__name'],
            },
        ),
        migrations.CreateModel(
            name='StockTransfer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('draft', 'Borrador'), ('in_transit', 'En tránsito'), ('received', 'Recibida'), ('cancelled', 'Anulada')], db_index=True, default='draft', max_length=20)),
                ('reason', models.TextField(blank=True)),
                ('reference', models.CharField(blank=True, max_length=120)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('dispatched_at', models.DateTimeField(blank=True, null=True)),
                ('received_at', models.DateTimeField(blank=True, null=True)),
                ('cancelled_at', models.DateTimeField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('cancelled_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='transfers_cancelled', to=settings.AUTH_USER_MODEL)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='stock_transfers', to='store.company')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='transfers_created', to=settings.AUTH_USER_MODEL)),
                ('destination_branch', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='transfers_in', to='store.branch')),
                ('dispatched_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='transfers_dispatched', to=settings.AUTH_USER_MODEL)),
                ('received_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='transfers_received', to=settings.AUTH_USER_MODEL)),
                ('source_branch', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='transfers_out', to='store.branch')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.AddField(
            model_name='stockmovement',
            name='transfer',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='stock_movements', to='store.stocktransfer'),
        ),
        migrations.AddIndex(
            model_name='stockmovement',
            index=models.Index(fields=['company', '-created_at'], name='store_stock_company_722fd0_idx'),
        ),
        migrations.AddIndex(
            model_name='stockmovement',
            index=models.Index(fields=['branch', '-created_at'], name='store_stock_branch__9dc655_idx'),
        ),
        migrations.AddIndex(
            model_name='stockmovement',
            index=models.Index(fields=['branch', 'product', '-created_at'], name='store_stock_branch__61da26_idx'),
        ),
        migrations.AddIndex(
            model_name='stockmovement',
            index=models.Index(fields=['branch', 'movement_type', '-created_at'], name='store_stock_branch__be9291_idx'),
        ),
        migrations.AddField(
            model_name='stocktransferitem',
            name='product',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='transfer_items', to='store.product'),
        ),
        migrations.AddField(
            model_name='stocktransferitem',
            name='transfer',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='store.stocktransfer'),
        ),
        migrations.AddIndex(
            model_name='branchstock',
            index=models.Index(fields=['branch', 'quantity'], name='store_branc_branch__47ec0b_idx'),
        ),
        migrations.AddIndex(
            model_name='branchstock',
            index=models.Index(fields=['product', 'branch'], name='store_branc_product_267847_idx'),
        ),
        migrations.AddConstraint(
            model_name='branchstock',
            constraint=models.UniqueConstraint(fields=('branch', 'product'), name='unique_stock_per_branch_product'),
        ),
        migrations.AddConstraint(
            model_name='branchstock',
            constraint=models.CheckConstraint(condition=models.Q(('quantity__gte', 0)), name='branch_stock_quantity_non_negative'),
        ),
        migrations.AddConstraint(
            model_name='branchstock',
            constraint=models.CheckConstraint(condition=models.Q(('target_stock', 0), ('target_stock__gte', models.F('minimum_stock')), _connector='OR'), name='branch_stock_target_at_least_minimum'),
        ),
        migrations.AddIndex(
            model_name='inventorycount',
            index=models.Index(fields=['company', '-created_at'], name='store_inven_company_31f15a_idx'),
        ),
        migrations.AddIndex(
            model_name='inventorycount',
            index=models.Index(fields=['branch', 'status'], name='store_inven_branch__cf5887_idx'),
        ),
        migrations.AddIndex(
            model_name='inventorycount',
            index=models.Index(fields=['company', 'status'], name='store_inven_company_499dff_idx'),
        ),
        migrations.AddIndex(
            model_name='inventorycountitem',
            index=models.Index(fields=['count', 'product'], name='store_inven_count_i_367e78_idx'),
        ),
        migrations.AddConstraint(
            model_name='inventorycountitem',
            constraint=models.UniqueConstraint(fields=('count', 'product'), name='unique_product_per_count'),
        ),
        migrations.AddIndex(
            model_name='membershipbranchaccess',
            index=models.Index(fields=['membership', 'is_active'], name='store_membe_members_62897f_idx'),
        ),
        migrations.AddIndex(
            model_name='membershipbranchaccess',
            index=models.Index(fields=['branch', 'is_active'], name='store_membe_branch__874a76_idx'),
        ),
        migrations.AddConstraint(
            model_name='membershipbranchaccess',
            constraint=models.UniqueConstraint(fields=('membership', 'branch'), name='unique_branch_access_per_membership'),
        ),
        migrations.AddIndex(
            model_name='stocktransfer',
            index=models.Index(fields=['company', '-created_at'], name='store_stock_company_3ed67f_idx'),
        ),
        migrations.AddIndex(
            model_name='stocktransfer',
            index=models.Index(fields=['source_branch', 'status'], name='store_stock_source__3af01a_idx'),
        ),
        migrations.AddIndex(
            model_name='stocktransfer',
            index=models.Index(fields=['destination_branch', 'status'], name='store_stock_destina_ddff3f_idx'),
        ),
        migrations.AddIndex(
            model_name='stocktransfer',
            index=models.Index(fields=['company', 'status'], name='store_stock_company_15692a_idx'),
        ),
        migrations.AddConstraint(
            model_name='stocktransfer',
            constraint=models.CheckConstraint(condition=models.Q(('source_branch', models.F('destination_branch')), _negated=True), name='transfer_source_differs_from_destination'),
        ),
        migrations.AddConstraint(
            model_name='stocktransferitem',
            constraint=models.UniqueConstraint(fields=('transfer', 'product'), name='unique_product_per_transfer'),
        ),
        migrations.AddConstraint(
            model_name='stocktransferitem',
            constraint=models.CheckConstraint(condition=models.Q(('quantity__gt', 0)), name='transfer_item_quantity_positive'),
        ),
    ]
