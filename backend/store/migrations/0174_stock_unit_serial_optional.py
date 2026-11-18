# SERIAL-OPTIONAL-01 — a device that carries a valid IMEI may enter without a serial number (NULL, never a
# placeholder). DROP NOT NULL is a catalogue change: no rewrite, no scan. The CHECK `stock_unit_identifiers_null_not_blank`
# already allows NULL and `unique_stock_unit_serial_per_product` treats NULLs as distinct.
#
# Reversal: SET NOT NULL fails while a device without serial exists. Before `migrate store 0173`, such devices must
# get their serial (or the rollback keeps this schema: the previous code never writes NULL and reads every row).
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0173_printer_local_queue'),
    ]

    operations = [
        migrations.AlterField(
            model_name='stockunit',
            name='serial_number',
            field=models.CharField(blank=True, max_length=40, null=True),
        ),
    ]
