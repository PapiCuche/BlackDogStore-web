"""
UNIT-PHOTO-02 — the real photos of each used device: which side they show, which one is the primary, and who confirmed
that each one is of THIS device. Additive, ONE node (0192, after 0191_trade_in_quote).

SCHEMA (on `UnitPhoto`; every column nullable or with a constant `db_default`: no table rewrite, and the previous
release keeps inserting photos while this migrates — what it inserts is `other`, not primary, not attested):

- `view` (main · front · back · left · right · top · bottom · defect · other), `defect_note`;
- `is_primary`, at most one per device: a partial unique index (`unit_photo_one_primary_per_unit`);
- `attested_by` / `attested_at` — «Confirmo que esta foto es de este equipo exacto».

DATA: the first photo of each device (by `sort_order`, then id) becomes its primary, so nothing that read «the first
photo» changes what it shows. NOTHING is attested here: nobody confirmed those photos.

DEPLOY EFFECT (owner's rule: «una foto de un seminuevo corresponde al equipo exacto; nunca una foto genérica presentada
como el equipo»). From this release a photo counts — for the web and for publishing — only when it is attested AND its
file is an own photo of the same company with its licence verified (the attestation does both). Every used device that
the web offers today with unattested photos LEAVES the web at once (it stays published; it returns as soon as one of
its photos is attested). No stock moves. The release rehearsal counts them on the copy of production:
`manage.py unit_photo_report` (read-only).

VUELTA ATRÁS. The reverse drops the constraint and the columns (views, primaries and attestations decided since are
lost); the previous code counts any photo again. In production, run with
`PGOPTIONS='-c lock_timeout=5s -c statement_timeout=30s'`.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def first_photo_is_primary(apps, schema_editor):
    UnitPhoto = apps.get_model('store', 'UnitPhoto')
    seen = set()
    primaries = []
    for pk, unit_id in UnitPhoto.objects.order_by('unit_id', 'sort_order', 'pk').values_list('pk', 'unit_id'):
        if unit_id not in seen:
            seen.add(unit_id)
            primaries.append(pk)
    for start in range(0, len(primaries), 1000):
        UnitPhoto.objects.filter(pk__in=primaries[start:start + 1000]).update(is_primary=True)



def refuse_reverse_with_quote_tickets(apps, schema_editor):
    """Reverse guard: the pre-0189 print-job check refuses any `sales_quote_ticket` row, so going back with one
    would stop halfway (rehearsal R13). Refuse BEFORE anything is undone and name the fix."""
    PrintJob = apps.get_model('store', 'PrintJob')
    tickets = PrintJob.objects.filter(kind='sales_quote_ticket').count()
    if tickets:
        raise RuntimeError(
            f'0192: hay {tickets} trabajo(s) de impresión de cotizaciones (incluidos los cancelados). '
            f"Antes de volver atrás: DELETE FROM store_printjob WHERE kind='sales_quote_ticket'; "
            f'(docs/despliegue-produccion.md §7).')


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0191_trade_in_quote'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='unitphoto',
            name='attested_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='unitphoto',
            name='attested_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='unitphoto',
            name='defect_note',
            field=models.CharField(blank=True, db_default='', default='', max_length=160),
        ),
        migrations.AddField(
            model_name='unitphoto',
            name='is_primary',
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.AddField(
            model_name='unitphoto',
            name='view',
            field=models.CharField(choices=[('main', 'Principal'), ('front', 'Frontal'), ('back', 'Posterior'), ('left', 'Lateral izquierdo'), ('right', 'Lateral derecho'), ('top', 'Borde superior'), ('bottom', 'Borde inferior'), ('defect', 'Defecto'), ('other', 'Otra')], db_default='other', default='other', max_length=8),
        ),
        migrations.RunPython(first_photo_is_primary, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='unitphoto',
            constraint=models.UniqueConstraint(condition=models.Q(('is_primary', True)), fields=('unit',), name='unit_photo_one_primary_per_unit'),
        ),
        # Last: on reverse it runs FIRST, before any data is undone.
        migrations.RunPython(migrations.RunPython.noop, refuse_reverse_with_quote_tickets),
    ]
