"""
Bulk inventory import — Phase C1.4.

WHAT MAKES THIS DIFFERENT FROM THE PRODUCT IMPORT
-------------------------------------------------
A wrong product can be edited. A wrong stock import cannot be edited away: it
becomes Kardex movements, and the Kardex is an append-only record of physical
fact. "Undoing" it means issuing compensating movements that are themselves
permanent history. So everything here is arranged around catching the mistake
before the write.

THE RULE THIS FILE EXISTS TO GET RIGHT (§14, §44)
-------------------------------------------------
    an EMPTY cell  →  DO NOT TOUCH THIS STOCK
    an explicit 0  →  SET THIS STOCK TO ZERO

They are opposite instructions and they look almost identical in a spreadsheet.
The owner's inventory file has SIX HUNDRED AND NINETY-SIX rows and a completely
empty quantity column: it is the catalogue, printed, waiting for somebody to walk
the shelves and write numbers on it. Read blank as zero and that one upload
writes off the entire shop.

TARGET, NOT DELTA (§43, §48)
----------------------------
The number in the file is the stock the operator COUNTED — what should be on the
shelf, not how much to add. The movement is computed as `target - current` under
a row lock at apply time, never from the delta shown in the preview. Between the
two, the till may have sold two of them; applying the preview's delta would
resurrect stock that has already left the building.
"""

from __future__ import annotations

import re

from django.db import transaction
from django.utils import timezone

from . import import_formats, inventory_services, xlsx_reader
from .import_services import CatalogueIndex, _safe_filename, _sha256
from .models import (
    Branch,
    BranchStock,
    BulkImportJob,
    BulkImportRow,
    Product,
    StockMovement,
)

IMPORT_REASON = 'Carga masiva de inventario'
REFERENCE_TYPE = 'bulk_import'


class StockImportError(Exception):
    pass


def parse_quantity(raw):
    """
    Read one stock cell.

    Returns `(kind, value, error)` where `kind` is `'blank'` or `'value'`.

    The distinction between blank and zero is returned as a separate KIND rather
    than as `None` versus `0`, because a `None` that means "skip" and a `0` that
    means "set to zero" are one careless `or` away from becoming the same thing.
    Making them different kinds means that mistake does not typecheck.
    """
    if raw is None:
        return 'blank', None, None
    if raw is xlsx_reader.FORMULA:
        return 'value', None, (
            'La celda contiene una fórmula. Conviértela a valor antes de importar.'
        )
    text = str(raw).strip()
    if text == '':
        return 'blank', None, None
    if not re.fullmatch(r'-?\d+(\.0+)?', text):
        if re.fullmatch(r'-?\d+\.\d+', text):
            return 'value', None, (
                f'«{text}» no es una cantidad entera. El inventario se lleva en '
                f'unidades enteras.'
            )
        return 'value', None, f'«{text}» no es una cantidad válida.'
    value = int(float(text))
    if value < 0:
        return 'value', None, (
            f'«{text}» es negativo. Una existencia física no puede ser negativa.'
        )
    return 'value', value, None


# =============================================================================
# Preview
# =============================================================================

@transaction.atomic
def preview_stock(*, company, actor, upload, filename, branch_map,
                  mode=BulkImportJob.MODE_RECONCILE, sheet_name=None,
                  header_row=None, mapping=None):
    """
    Stage an inventory workbook. Writes NO stock and NO movements (§53).

    `branch_map` is `{column_index: branch_id}` — the operator's explicit answer
    to "which shop is this column". Never inferred from the number inside the
    header: `ALMACEN 1 - 11416` carries an id from the system that exported the
    file, and reading it as a `Branch.pk` here would point at whichever branch
    happened to have that primary key.
    """
    if mode not in dict(BulkImportJob.MODE_CHOICES):
        raise StockImportError('Modo de carga no válido.')

    data = xlsx_reader.check_upload(upload, filename=filename)
    sha256 = _sha256(data)
    workbook, reader_notes = xlsx_reader.load_workbook(data)
    sheet_name = sheet_name or workbook.sheetnames[0]

    probe, _rows, _t = xlsx_reader.read_rows(
        workbook, sheet_name, header_row=header_row or 1,
    )
    detected = import_formats.detect(BulkImportJob.STOCK, sheet_name, probe)
    if detected:
        header_row = header_row or detected['header_row']
        mapping = mapping or detected['mapping']
    header_row = header_row or 1
    mapping = {
        k: int(v) for k, v in (mapping or {}).items()
        if k in import_formats.STOCK_FIELDS
    }
    if not mapping:
        raise StockImportError(
            'No se reconoció el formato del archivo. Asigna las columnas a mano.'
        )

    headers, rows, truncated = xlsx_reader.read_rows(
        workbook, sheet_name, header_row=header_row,
    )

    branch_map = {int(k): int(v) for k, v in (branch_map or {}).items()}
    if not branch_map:
        raise StockImportError(
            'Indica a qué sucursal corresponde cada columna de almacén.'
        )
    branches = {
        b.pk: b for b in Branch.objects.filter(company=company, pk__in=set(branch_map.values()))
    }
    missing = set(branch_map.values()) - set(branches)
    if missing:
        # Walking DOWN from the company: another tenant's branch is simply not
        # in the set, so it cannot be selected by guessing an id.
        raise StockImportError('Alguna sucursal no pertenece a esta empresa.')

    job = BulkImportJob.objects.create(
        company=company, import_type=BulkImportJob.STOCK, stock_mode=mode,
        original_filename=_safe_filename(filename), file_sha256=sha256,
        mapping_snapshot={
            'sheet_name': sheet_name, 'header_row': header_row,
            'mapping': mapping, 'headers': [str(h) for h in headers],
            'branch_map': {str(k): v for k, v in branch_map.items()},
            'preset': (detected or {}).get('preset', ''),
        },
        options_snapshot={'mode': mode},
        created_by=actor,
    )

    index = CatalogueIndex(company)
    current = _current_quantities(company, branches.values())
    staged = []
    counts = dict(create=0, update=0, no_change=0, skip=0, error=0)
    seen_pairs: dict[tuple[int, int], int] = {}

    for row_number, values in rows:
        if not values:
            staged.append(BulkImportRow(
                job=job, sheet_name=sheet_name, row_number=row_number,
                action=BulkImportRow.SKIP, warnings=['Fila vacía.'],
                normalized_data={},
            ))
            counts['skip'] += 1
            continue

        barcode = str(values.get(mapping.get('barcode'), '') or '').strip()
        code = str(values.get(mapping.get('code'), '') or '').strip()
        name = str(values.get(mapping.get('name'), '') or '').strip()
        external_id = str(values.get(mapping.get('external_id'), '') or '').strip()

        product_id, match_error, matched_by = index.match(
            barcode=barcode, code=code, name=name,
        )

        for column_index, branch_id in sorted(branch_map.items()):
            branch = branches[branch_id]
            kind, quantity, quantity_error = parse_quantity(values.get(column_index))

            errors, warnings = [], []
            if quantity_error:
                errors.append(quantity_error)

            if kind == 'blank':
                # THE RULE. An empty cell is not a zero, and the preview says so
                # in words, because the difference is invisible in a spreadsheet
                # and total on a shelf.
                #
                # The CURRENT quantity is carried even though nothing will
                # happen to it: an operator scanning this screen needs to see
                # "19, unchanged" and not a blank cell, because a blank next to
                # a blank is exactly the ambiguity this rule exists to remove.
                held = current.get((branch_id, product_id)) if product_id else None
                staged.append(BulkImportRow(
                    job=job, sheet_name=sheet_name, row_number=row_number,
                    action=BulkImportRow.SKIP,
                    match_key=barcode or code or name[:120],
                    normalized_data={
                        'branch_id': branch_id, 'branch_name': branch.name,
                        'column': str(headers[column_index]) if column_index < len(headers) else '',
                        'product_id': product_id, 'name': name, 'code': code,
                        'barcode': barcode, 'external_id': external_id,
                        'quantity_kind': 'blank',
                        'current_preview': held if held is not None else '',
                    },
                    warnings=[
                        'Celda vacía: no se cambia el stock de esta sucursal.'
                        if product_id is None else
                        f'Celda vacía: el stock se queda en {held or 0}.'
                    ],
                ))
                counts['skip'] += 1
                continue

            if match_error:
                errors.append(match_error)
            elif product_id is None:
                errors.append(
                    'No se encontró el producto. Impórtalo primero desde la '
                    'carga masiva de productos.'
                )

            if product_id:
                pair = (branch_id, product_id)
                previous = seen_pairs.get(pair)
                if previous:
                    # §46 — "last row wins" would silently discard a count.
                    errors.append(
                        f'Este producto y esta sucursal ya aparecen en la fila '
                        f'{previous}. Deja una sola fila por producto y almacén.'
                    )
                else:
                    seen_pairs[pair] = row_number

            now = current.get((branch_id, product_id), 0) if product_id else 0
            delta = (quantity - now) if quantity is not None else 0

            if not errors and mode == BulkImportJob.MODE_INITIAL and product_id:
                if _has_history(branch_id, product_id):
                    errors.append(
                        'Esta sucursal ya tiene historial de Kardex para este '
                        'producto. Usa el modo de ajuste a stock objetivo.'
                    )

            normalized = {
                'branch_id': branch_id, 'branch_name': branch.name,
                'column': str(headers[column_index]) if column_index < len(headers) else '',
                'product_id': product_id, 'name': name, 'code': code,
                'barcode': barcode, 'external_id': external_id,
                'quantity_kind': 'value', 'target': quantity,
                'current_preview': now, 'delta_preview': delta,
                'matched_by': matched_by,
            }

            if errors:
                action = BulkImportRow.ERROR
            elif delta == 0:
                action = BulkImportRow.NO_CHANGE
            else:
                action = BulkImportRow.UPDATE

            counts['no_change' if action == BulkImportRow.NO_CHANGE else action] += 1
            staged.append(BulkImportRow(
                job=job, sheet_name=sheet_name, row_number=row_number,
                action=action, match_key=barcode or code or name[:120],
                normalized_data=normalized, errors=errors, warnings=warnings,
            ))

    BulkImportRow.objects.bulk_create(staged, batch_size=500)
    job.rows_total = len(staged)
    job.rows_create = counts['create']
    job.rows_update = counts['update']
    job.rows_no_change = counts['no_change']
    job.rows_skip = counts['skip']
    job.rows_error = counts['error']
    job.summary = {
        'reader_notes': reader_notes,
        'detected': (detected or {}).get('label', ''),
        'format_notes': (detected or {}).get('notes', []),
        'truncated': truncated,
        'branches': [
            {'column': str(headers[c]) if c < len(headers) else str(c),
             'branch_id': b, 'branch_name': branches[b].name}
            for c, b in sorted(branch_map.items())
        ],
        'blank_is_skip': True,
    }
    job.save(update_fields=[
        'rows_total', 'rows_create', 'rows_update', 'rows_no_change',
        'rows_skip', 'rows_error', 'summary',
    ])
    return job


def _current_quantities(company, branches):
    return {
        (b, p): q
        for b, p, q in BranchStock.objects
        .filter(branch__company=company, branch__in=list(branches))
        .values_list('branch_id', 'product_id', 'quantity')
    }


def _has_history(branch_id, product_id) -> bool:
    return StockMovement.objects.filter(
        branch_id=branch_id, product_id=product_id,
    ).exists()


# =============================================================================
# Apply
# =============================================================================

@transaction.atomic
def apply_stock(*, job, actor):
    """
    Turn the staged targets into Kardex movements.

    Everything about this function is about the gap between preview and apply.
    """
    job = BulkImportJob.objects.select_for_update().get(pk=job.pk)
    if job.status == BulkImportJob.APPLIED:
        return job, False, []           # §52 — the second click is not a second import
    if job.import_type != BulkImportJob.STOCK:
        raise StockImportError('Este trabajo no es de inventario.')
    if job.rows_error:
        raise StockImportError(
            f'Hay {job.rows_error} fila(s) con error. Corrige el archivo y vuelve '
            f'a subirlo.'
        )

    mode = job.stock_mode or BulkImportJob.MODE_RECONCILE
    rows = list(
        job.rows.filter(action__in=[BulkImportRow.UPDATE, BulkImportRow.NO_CHANGE])
        .order_by('pk')
    )

    # §50 — a deterministic lock order, the same one the rest of the inventory
    # code uses. Six hundred rows taking locks in file order, while the POS takes
    # them in its own order, is a deadlock waiting for a busy Saturday.
    ordered = sorted(
        (r for r in rows if (r.normalized_data or {}).get('quantity_kind') == 'value'),
        key=lambda r: (
            (r.normalized_data or {}).get('branch_id') or 0,
            (r.normalized_data or {}).get('product_id') or 0,
        ),
    )

    branches = {
        b.pk: b for b in Branch.objects.filter(
            company=job.company,
            pk__in={(r.normalized_data or {}).get('branch_id') for r in ordered},
        )
    }
    # §74 — resolved once for the whole job. One query per row to find the
    # product would be six hundred queries against a table we already know how
    # to fetch in one. Filtered by company, so a staged row naming another
    # tenant's product resolves to nothing and is skipped rather than written.
    products = {
        p.pk: p for p in Product.objects.filter(
            company=job.company,
            pk__in={(r.normalized_data or {}).get('product_id') for r in ordered},
        )
    }

    movements = []
    applied = skipped = 0
    for row in ordered:
        data = row.normalized_data or {}
        branch = branches.get(data.get('branch_id'))
        product = products.get(data.get('product_id'))
        product_id = data.get('product_id')
        target = data.get('target')
        if branch is None or product is None or target is None:
            continue

        # The delta is recomputed HERE, under the lock create_stock_movement
        # takes, from the CURRENT quantity — never from `delta_preview`. The
        # preview said "+5"; if the till has since sold two, the truth is "+7",
        # and applying +5 would leave the shelf permanently two short.
        stock = inventory_services.get_or_create_branch_stock(branch, product)
        current = BranchStock.objects.select_for_update().get(pk=stock.pk).quantity
        delta = target - current
        if delta == 0:
            skipped += 1
            continue

        if mode == BulkImportJob.MODE_INITIAL and current == 0:
            movement_type = StockMovement.INITIAL_STOCK
        else:
            movement_type = (
                StockMovement.CORRECTION_POSITIVE if delta > 0
                else StockMovement.CORRECTION_NEGATIVE
            )

        movement = inventory_services.create_stock_movement(
            branch=branch, product_id=product_id,
            movement_type=movement_type, quantity=abs(delta),
            reason=IMPORT_REASON, actor=actor,
            reference_type=REFERENCE_TYPE, reference_id=str(job.pk),
            metadata={
                'import_job': job.pk, 'row': row.row_number,
                'target': target, 'previous': current,
            },
        )
        movements.append(movement)
        applied += 1

    job.status = BulkImportJob.APPLIED
    job.applied_by = actor
    job.applied_at = timezone.now()
    job.summary = {**(job.summary or {}), 'applied': {
        'movements': applied,
        'already_matching': skipped,
    }}
    job.save(update_fields=['status', 'applied_by', 'applied_at', 'summary'])
    return job, True, movements
