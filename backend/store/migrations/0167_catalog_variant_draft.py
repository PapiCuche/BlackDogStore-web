"""
CATALOG-COMMERCIAL-01 · A1b-2 — a variant created from the palette is a PENDING DRAFT until somebody
enables it for sale (DEC-CATALOG-VARIANT-DRAFT-01, owner's resolutions of 2026-11-12, T1–T4).

Three columns on `Product`, ADDITIVE, and one partial index:

  * `draft_origin`      CharField(16), `db_default=''` — PROVENANCE, immutable: 'palette' for a row that
                        `device_catalog.create_variant_product` made; '' for every other row. Never an
                        authorisation, never cleared.
  * `sale_enabled_at`   DateTimeField NULL — the one commercial transition, dated.
  * `sale_enabled_by`   FK user NULL, SET_NULL — who made it (the AdminAuditLog line carries it too).
  * `product_draft_origin_idx` (company, draft_origin) WHERE draft_origin <> '' — the predicate
    «pending» (`draft_origin <> '' AND sale_enabled_at IS NULL`) runs in the shop and the till on every
    request; with the partial condition the index holds only the few rows with a provenance.

WHY `db_default` AND NULL-ABLE. The rollback rule is «code first»: the previous release (R4.5 `e0290c1`
and the A1/A1b-1 code without these columns) keeps inserting products against this schema during the
deployment and after a rollback. Its INSERTs do not name the columns, so the database writes '' / NULL
— and such a row is never pending. A constant `db_default` and NULL-able columns are metadata-only in
PostgreSQL 11+: no table rewrite, no long lock. No data migration: every existing row is '' / NULL by
definition (nothing was ever created from the palette before this release).

Run `migrate` with the usual `PGOPTIONS='-c lock_timeout=5s -c statement_timeout=30s'`, together with
0165 and 0166 in one window (plan A1b §1.2). Reversible: `migrate store 0166` drops the index and the
three columns (tested forward → back → forward on an isolated copy); what is lost is the provenance and
the transition of the drafts created meanwhile, never a product, a variant, a price or stock.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0166_catalog_model_normalized_name'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='draft_origin',
            field=models.CharField(
                blank=True, choices=[('', 'Sin procedencia de borrador'), ('palette', 'Paleta del modelo')],
                db_default='', default='', max_length=16),
        ),
        migrations.AddField(
            model_name='product',
            name='sale_enabled_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='product',
            name='sale_enabled_by',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+',
                to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddIndex(
            model_name='product',
            index=models.Index(
                condition=models.Q(('draft_origin', ''), _negated=True), fields=['company', 'draft_origin'],
                name='product_draft_origin_idx'),
        ),
    ]
