"""
CATALOG FASE 1.2 · Y — additive only: four columns, each with a database default, so the running code (which does not
know them) keeps inserting rows while this migration is applied, and rolling the code back leaves them unused.

  * `ModelFinishImage.view`: which view of the object a finish picture is ('' = not said);
  * `VariantOption.swatch_is_approximate`: the company's swatch is an approximation pending commercial validation;
  * `ModelOption.swatch_hex` / `swatch_is_approximate`: a model's own swatch for one colour ('' = the company's).

No data is written: every existing row reads as before (no view, no approximation, the company's swatch).
"""

import store.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0184_merge_presentation_stock_unit_supplier'),
    ]

    operations = [
        migrations.AddField(
            model_name='modelfinishimage',
            name='view',
            field=models.CharField(blank=True, choices=[('main', 'Principal'), ('front', 'Frontal'), ('back', 'Posterior'), ('side', 'Lateral'), ('other', 'Otra')], db_default='', default='', max_length=8),
        ),
        migrations.AddField(
            model_name='modeloption',
            name='swatch_hex',
            field=models.CharField(blank=True, db_default='', default='', max_length=7, validators=[store.models.validate_swatch_hex]),
        ),
        migrations.AddField(
            model_name='modeloption',
            name='swatch_is_approximate',
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.AddField(
            model_name='variantoption',
            name='swatch_is_approximate',
            field=models.BooleanField(db_default=False, default=False),
        ),
    ]
