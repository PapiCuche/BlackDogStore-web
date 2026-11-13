"""
CATALOG-COMMERCIAL-01 · A2 — the company's supplier vocabulary (DEC-CATALOG-ALIAS-01): `CatalogAlias`.

ONE NEW TABLE, nothing else: no column is added to an existing table, no row of another table is read or
written. The previous release (R4.5 `e0290c1`, or A1/A1b without A2) never reads the table, so a rollback
is code-only and keeps the schema. `migrate store 0167` drops the table: what is lost is the aliases the
company typed meanwhile, never a model, an option, a variant, a price, an offer or stock.

Constraints held by the database, besides the service (`catalog_aliases`):
  * UNIQUE (company, scope, alias_key)        one meaning per company and scope;
  * CHECK alias_key <> ''                       a blank key never matches anything;
  * CHECK target matches scope                  `model` → model only, `option` → option only,
                                                `model_word` → a replacement and no foreign key.

Deploy with 0165–0167 in one window, before the code, with
`PGOPTIONS='-c lock_timeout=5s -c statement_timeout=30s'`; `check_model_names` must still answer 0.
Tested forward → back → forward on an isolated database.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0167_catalog_variant_draft'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CatalogAlias',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('scope', models.CharField(choices=[('model_word', 'Palabra del nombre del modelo'), ('model', 'Modelo'), ('option', 'Opción')], max_length=12)),
                ('alias', models.CharField(max_length=60)),
                ('alias_key', models.CharField(max_length=60)),
                ('replacement', models.CharField(blank=True, default='', max_length=60)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='store.company')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('model', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='aliases', to='store.productmodel')),
                ('option', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='aliases', to='store.variantoption')),
            ],
            options={
                'ordering': ['scope', 'alias_key', 'pk'],
                'constraints': [models.UniqueConstraint(fields=('company', 'scope', 'alias_key'), name='unique_catalog_alias_per_scope'), models.CheckConstraint(condition=models.Q(('alias_key', ''), _negated=True), name='catalog_alias_key_not_blank'), models.CheckConstraint(condition=models.Q(models.Q(('model__isnull', False), ('option__isnull', True), ('replacement', ''), ('scope', 'model')), models.Q(('model__isnull', True), ('option__isnull', False), ('replacement', ''), ('scope', 'option')), models.Q(('model__isnull', True), ('option__isnull', True), ('scope', 'model_word'), models.Q(('replacement', ''), _negated=True)), _connector='OR'), name='catalog_alias_target_matches_scope')],
            },
        ),
    ]
