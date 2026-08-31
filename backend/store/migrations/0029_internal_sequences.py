"""
Phase 2E, step 1 of 2 — SCHEMA.

  0029  InternalSequence, SalesNote.sequence/sequence_value, and the removal of
        the GLOBAL unique on SalesNote.number                        (this file)
  0030  create each company's series and attach every existing note to it

THE GLOBAL UNIQUE GOES AWAY HERE, AND THAT IS THE POINT
-------------------------------------------------------
`SalesNote.number` was `unique=True` across the whole installation, which made
one tenant's numbering depend on another's: company A issued NV-000001, company
B took NV-000002. Two companies must both be able to show NV-000001.

Uniqueness moves to `unique_value_per_sequence` — one ordinal per series — which
is the constraint that was actually meant all along.

THIS MIGRATION IS NOT REVERSIBLE, ON PURPOSE
--------------------------------------------
Going backwards would restore the global unique. Once two companies each hold an
NV-000001 — which is the whole purpose of this phase — that constraint cannot be
satisfied without renumbering somebody's already-issued documents.

Renumbering history to satisfy a schema is not a rollback, it is data loss with a
migration in front of it. So the reverse raises and explains, rather than failing
on a database error nobody can act on, or "succeeding" by rewriting documents
people are holding. See docs/saas-multiempresa.md.
"""


import django.db.models.deletion


def _forward_noop(apps, schema_editor):
    """Nothing to do going forward; this operation exists for its reverse."""


def _refuse_reverse(apps, schema_editor):
    """
    Stop a rollback before it undoes anything.

    Placed LAST in `operations` so it runs FIRST in reverse — Django applies
    reverse operations in reverse order, so a guard at the end is the first thing
    a rollback hits.
    """
    raise RuntimeError(
        'La migración 0029 no es reversible.\n\n'
        'Revertirla restauraría el unique global de SalesNote.number, que es '
        'justamente lo que esta fase eliminó para que dos empresas puedan emitir '
        'NV-000001 cada una. Con documentos ya emitidos en más de una empresa, '
        'ese constraint no puede satisfacerse sin renumerar historia ajena.\n\n'
        'Renumerar documentos ya emitidos no es un rollback: es pérdida de datos. '
        'Si de verdad necesita volver atrás, restaure una copia de seguridad '
        'anterior a esta migración.'
    )
import store.models
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0028_backfill_company_settings'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='companysettings',
            name='sales_note_sequence_scope',
            field=models.CharField(choices=[('company', 'Una numeración para toda la empresa'), ('branch', 'Una numeración por sucursal')], default='company', max_length=16),
        ),
        migrations.AddField(
            model_name='salesnote',
            name='sequence_value',
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='salesnote',
            name='number',
            field=models.CharField(max_length=40),
        ),
        migrations.CreateModel(
            name='InternalSequence',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('document_type', models.CharField(choices=[('sales_note', 'Nota de venta interna')], db_index=True, max_length=32)),
                ('prefix', models.CharField(blank=True, max_length=12, validators=[store.models.validate_sequence_prefix])),
                ('padding', models.PositiveSmallIntegerField(default=6)),
                ('next_value', models.PositiveBigIntegerField(default=1)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('branch', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='sequences', to='store.branch')),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='sequences', to='store.company')),
            ],
            options={
                'ordering': ['company__name', 'document_type', 'branch__name'],
            },
        ),
        migrations.AddField(
            model_name='salesnote',
            name='sequence',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='sales_notes', to='store.internalsequence'),
        ),
        migrations.AddIndex(
            model_name='salesnote',
            index=models.Index(fields=['sequence', 'sequence_value'], name='store_sales_sequenc_bc7442_idx'),
        ),
        migrations.AddIndex(
            model_name='salesnote',
            index=models.Index(fields=['number'], name='store_sales_number_5eed92_idx'),
        ),
        migrations.AddConstraint(
            model_name='salesnote',
            constraint=models.UniqueConstraint(condition=models.Q(('sequence__isnull', False), ('sequence_value__isnull', False)), fields=('sequence', 'sequence_value'), name='unique_value_per_sequence'),
        ),
        migrations.AddIndex(
            model_name='internalsequence',
            index=models.Index(fields=['company', 'document_type'], name='store_inter_company_542345_idx'),
        ),
        migrations.AddIndex(
            model_name='internalsequence',
            index=models.Index(fields=['branch', 'document_type'], name='store_inter_branch__92362a_idx'),
        ),
        migrations.AddConstraint(
            model_name='internalsequence',
            constraint=models.UniqueConstraint(condition=models.Q(('branch__isnull', True)), fields=('company', 'document_type'), name='unique_company_sequence_per_document'),
        ),
        migrations.AddConstraint(
            model_name='internalsequence',
            constraint=models.UniqueConstraint(condition=models.Q(('branch__isnull', False)), fields=('company', 'branch', 'document_type'), name='unique_branch_sequence_per_document'),
        ),
        migrations.AddConstraint(
            model_name='internalsequence',
            constraint=models.CheckConstraint(condition=models.Q(('padding__gte', 1), ('padding__lte', 12)), name='sequence_padding_within_range'),
        ),
        migrations.AddConstraint(
            model_name='internalsequence',
            constraint=models.CheckConstraint(condition=models.Q(('next_value__gte', 1)), name='sequence_next_value_positive'),
        ),
        # LAST here so it runs FIRST in reverse — see the module docstring.
        migrations.RunPython(_forward_noop, _refuse_reverse),
    ]
