"""
CATALOG-REFERENCE-01 — joins PR E and PR F, which both numbered their migration 0183.

`0183_catalog_presentation` (PR E: `ProductModel.is_featured`, `ModelOption.sort_order`) and
`0183_stock_unit_supplier` (PR F: `StockUnit.supplier`) touch disjoint tables, so this node changes no schema:
it only tells Django the order, exactly like `0136_merge_cancellations_device_commerce` and
`0169_merge_legal_policies`.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0183_catalog_presentation'),
        ('store', '0183_stock_unit_supplier'),
    ]

    operations = []
