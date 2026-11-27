"""
TRADE-IN VALUATION PRO · Phase 1 — the reception tariff to and from Excel (docs/adr-trade-in-valuation.md §3.1, §7).

ONE LAYOUT, BOTH WAYS. The export is the template: the columns an import reads come first, in this order, and the
informative ones after them (the import ignores those). Re-uploading an unchanged export changes nothing.

PREVIEW, THEN APPLY. The upload is read with the platform's guarded reader (`xlsx_reader`: size, macros, zip bombs,
formulas refused), every row is checked against the company's tariff and the outcome is staged in a
`TradeInTariffImport` (never the file: its SHA-256). Applying writes exactly what was previewed through the same
services as the panel (`trade_in_tariff.create_entry` / `propose_version`), so the approval rules are the same: a
first price or a relevant rise waits for an approver. A file with any row in error is not applied at all; a preview
older than `IMPORT_PREVIEW_TTL_SECONDS`, or one the tariff moved away from, is refused (409) and the file is uploaded
again.
"""
from __future__ import annotations

import hashlib
import io
import re
from datetime import date, datetime
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from . import import_freshness, money_input, xlsx_reader
from . import trade_in_tariff as tariff
from .import_formats import normalize_header
from .models import TradeInTariffEntry, TradeInTariffImport, TradeInTariffVersion

SHEET = 'Tarifario'

#: Column → (header, field). The import reads these, by header, in any order.
IMPORT_COLUMNS = (
    ('Marca', 'brand'),
    ('Modelo', 'model_name'),
    ('Capacidad', 'capacity'),
    ('SIM', 'sim'),
    ('Región', 'region'),
    ('Valor base de recepción', 'base_reception_value'),
    ('Costo de reacondicionamiento', 'refurbishment_cost'),
    ('Valor estimado de reventa', 'resale_value'),
    ('Margen comercial', 'target_margin'),
    ('Valor máximo autorizado', 'max_reception_value'),
    ('Vigente desde', 'valid_from'),
    ('Vigente hasta', 'valid_until'),
    ('Motivo', 'reason'),
)
#: Written by the export, ignored by the import.
INFO_COLUMNS = ('Estado', 'Versión', 'Aprobación', 'Aprobado por', 'Aprobado el', 'Modelo del catálogo')
REQUIRED_HEADERS = ('brand', 'model_name', 'base_reception_value', 'max_reception_value')

ACTION_CREATE = 'create'
ACTION_UPDATE = 'update'
ACTION_UNCHANGED = 'unchanged'
ACTION_ERROR = 'error'

_DMY = re.compile(r'^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$')


class TariffImportError(tariff.TariffError):
    pass


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def _person(user) -> str:
    if user is None:
        return ''
    return (user.get_full_name() or user.username or '').strip()


def _shown_version(entry):
    """What a row of the export says: the pending change if there is one, else the latest approved."""
    return tariff.pending_version(entry) or tariff.latest_approved(entry)


def export_rows(entries) -> list[list]:
    rows = []
    for entry in entries:
        version = _shown_version(entry)
        money = [getattr(version, field) if version else None for field in tariff.MONEY_FIELDS]
        rows.append([
            entry.brand, entry.model_name, entry.capacity, entry.sim, entry.region,
            *money,
            version.valid_from if version else None,
            version.valid_until if version else None,
            '',
            'Activo' if entry.is_active else 'Inactivo',
            version.number if version else None,
            dict(TradeInTariffVersion.STATUS_CHOICES).get(version.status, '') if version else '',
            _person(version.decided_by) if version and version.status == TradeInTariffVersion.STATUS_APPROVED else '',
            (timezone.localtime(version.decided_at).strftime('%Y-%m-%d %H:%M')
             if version and version.decided_at and version.status == TradeInTariffVersion.STATUS_APPROVED else ''),
            entry.product_model.name if entry.product_model_id else '',
        ])
    return rows


def export_bytes(entries) -> bytes:
    """The tariff as an .xlsx whose first columns are the import template."""
    from openpyxl.utils import get_column_letter

    from .import_exports import _autosize, _header_row, _workbook, header_notes, number_columns, text_columns

    workbook = _workbook()
    sheet = workbook.active
    sheet.title = SHEET
    titles = [title for title, _ in IMPORT_COLUMNS] + list(INFO_COLUMNS)
    _header_row(sheet, titles)
    for row_index, values in enumerate(export_rows(entries), start=2):
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=row_index, column=column, value=value)
            if isinstance(value, str) and value:
                xlsx_reader.mark_as_text(cell)
            elif isinstance(value, Decimal):
                cell.number_format = '0.00'
            elif isinstance(value, date):
                cell.number_format = 'yyyy-mm-dd'
    letters = [get_column_letter(index) for index in range(1, len(titles) + 1)]
    text_columns(sheet, letters[:5])
    number_columns(sheet, letters[5:10])
    header_notes(sheet, {
        'Valor base de recepción': 'Lo que la tienda paga por el equipo en buen estado, antes de las reglas.',
        'Valor máximo autorizado': 'Tope: ninguna valorización de este equipo lo supera.',
        'Vigente desde': 'AAAA-MM-DD. Vacío = hoy. No puede ser anterior a hoy en un cambio.',
        'Vigente hasta': 'AAAA-MM-DD, inclusive. Vacío = hasta que otro precio lo reemplace.',
        'Estado': 'Informativa: la carga no la lee. Se desactiva desde el panel.',
    })
    _autosize(sheet, [14, 22, 12, 12, 12, 16, 16, 16, 14, 16, 14, 14, 28, 10, 9, 22, 20, 18, 22])
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Import: reading
# ---------------------------------------------------------------------------

def _columns(headers) -> dict[str, int]:
    """Field → column index, by header. A required header missing refuses the file."""
    by_key = {normalize_header(title): field for title, field in IMPORT_COLUMNS}
    found = {}
    for index, header in enumerate(headers):
        field = by_key.get(normalize_header(header))
        if field is not None and field not in found:
            found[field] = index
    missing = [title for title, field in IMPORT_COLUMNS if field in REQUIRED_HEADERS and field not in found]
    if missing:
        raise TariffImportError('Faltan columnas en la hoja: ' + ', '.join(f'«{m}»' for m in missing)
                                + '. Descarga la plantilla (la exportación del tarifario) y úsala.',
                                code='missing_columns')
    return found


def _money_cell(text, numeric: bool, field: str, *, currency: str):
    if text in (None, ''):
        return ''
    label = tariff.MONEY_LABELS[field]
    try:
        if numeric:
            return str(money_input.number_cell(text, decimals=2, label=label))
        plain = money_input.amount_text(money_input.upload_text(text, currency=currency, label=label), label)
        return plain
    except money_input.AmountTextError as exc:
        raise tariff.TariffError(exc.detail, field=field) from None


def _date_cell(text, field: str) -> str:
    if text in (None, ''):
        return ''
    said = str(text).strip()
    match = _DMY.match(said)
    if match:
        day, month, year = (int(part) for part in match.groups())
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            pass
    else:
        try:
            return datetime.fromisoformat(said).date().isoformat() if 'T' in said else date.fromisoformat(said).isoformat()
        except ValueError:
            pass
    label = 'Vigente desde' if field == 'valid_from' else 'Vigente hasta'
    raise tariff.TariffError(f'«{label}» no es una fecha válida (AAAA-MM-DD o DD/MM/AAAA).', field=field)


def _row_data(values: dict, numeric: set, columns: dict, *, currency: str) -> dict:
    data = {}
    for field, index in columns.items():
        raw = values.get(index, '')
        if raw is xlsx_reader.FORMULA:
            raise tariff.TariffError('La celda tiene una fórmula: conviértela en valor.', field=field)
        if field in tariff.MONEY_FIELDS:
            data[field] = _money_cell(raw, index in numeric, field, currency=currency)
        elif field in ('valid_from', 'valid_until'):
            data[field] = _date_cell(raw, field)
        else:
            data[field] = str(raw or '').strip()
    return data


def _latest_version_id(entry):
    return entry.versions.order_by('-number').values_list('pk', flat=True).first()


def _check_row(company, data: dict, *, today: date, policy, seen: dict, row_number: int) -> dict:
    """What one row would do, with everything apply needs to do it (and to know the tariff did not move)."""
    identity = tariff.clean_identity(data)
    terms = tariff.clean_terms(data, today=today)
    key = tariff.device_key(*(identity[field] for field in tariff.IDENTITY_FIELDS))
    if key in seen:
        raise tariff.TariffError(f'Este equipo ya aparece en la fila {seen[key]}.', code='duplicate_row')
    seen[key] = row_number
    entry = TradeInTariffEntry.objects.filter(company=company, device_key=key).first()
    out = {'device_key': key, 'identity': identity, 'terms': _terms_json(terms), 'reasons': []}
    if entry is None:
        tariff.check_not_retroactive(terms, today=today)
        out.update(action=ACTION_CREATE, entry=None, base_version=None, reasons=[tariff.REASON_NEW_ENTRY])
        return out
    out.update(entry=entry.pk, base_version=_latest_version_id(entry))
    pending = tariff.pending_version(entry)
    previous = tariff.latest_approved(entry)
    if tariff.same_terms(pending or previous, terms):
        out['action'] = ACTION_UNCHANGED
        return out
    if not entry.is_active:
        raise tariff.TariffError('La entrada está desactivada: reactívala en el panel antes de cambiar su precio.',
                                 code='inactive')
    if pending is not None:
        raise tariff.TariffError('Este equipo tiene un cambio pendiente de aprobación: decídelo antes de cargar '
                                 'otro precio.', code='pending_exists')
    tariff.check_not_retroactive(terms, today=today)
    out.update(action=ACTION_UPDATE, reasons=tariff.approval_reasons(previous, terms, policy))
    return out


def _terms_json(terms: dict) -> dict:
    out = {}
    for field, value in terms.items():
        if isinstance(value, (Decimal, date)):
            out[field] = value.isoformat() if isinstance(value, date) else str(value)
        else:
            out[field] = value
    return out


def preview(*, actor, company, upload, filename: str = '', request=None) -> TradeInTariffImport:
    """Read the file, check every row, stage the outcome. Writes nothing of the tariff."""
    tariff._require(actor, company, tariff.CAP_MANAGE, 'No tienes permiso para importar el tarifario.')
    data = xlsx_reader.check_upload(upload, filename=filename)
    workbook, _notes = xlsx_reader.load_workbook(data)
    names = xlsx_reader.sheet_names(workbook)
    sheet = SHEET if SHEET in names else names[0]
    headers, rows, _ = xlsx_reader.read_rows(workbook, sheet)
    columns = _columns(headers)
    today = tariff.company_today(company)
    policy = tariff.policy_of(company)
    currency = getattr(getattr(company, 'settings', None), 'currency', None)
    seen: dict[str, int] = {}
    staged = []
    counts = {ACTION_CREATE: 0, ACTION_UPDATE: 0, ACTION_UNCHANGED: 0, ACTION_ERROR: 0, 'needs_approval': 0}
    for row_number, values, numeric in rows:
        if not values:
            continue
        try:
            outcome = _check_row(company, _row_data(values, numeric, columns, currency=currency), today=today,
                                 policy=policy, seen=seen, row_number=row_number)
        except tariff.TariffError as exc:
            outcome = {'action': ACTION_ERROR, 'error': str(exc), 'field': exc.field, 'code': exc.code}
        outcome['row'] = row_number
        counts[outcome['action']] += 1
        if outcome['action'] in (ACTION_CREATE, ACTION_UPDATE) and outcome.get('reasons'):
            counts['needs_approval'] += 1
        staged.append(outcome)
    if not staged:
        raise TariffImportError('La hoja no tiene filas con datos.', code='empty')
    batch = TradeInTariffImport.objects.create(
        company=company, original_filename=(filename or getattr(upload, 'name', '') or '')[:255],
        file_sha256=hashlib.sha256(data).hexdigest(), rows=staged, summary=counts,
        created_by=actor if getattr(actor, 'is_authenticated', False) else None)
    from .models import AdminAuditLog

    AdminAuditLog.log(actor=actor, action='trade_in_tariff_import_previewed', target_type='trade_in_tariff_import',
                      target_id=batch.pk, metadata={'company_id': company.pk, 'import_id': batch.pk,
                                                    'file_sha256': batch.file_sha256, **counts},
                      request=request, company=company)
    return batch


# ---------------------------------------------------------------------------
# Import: applying
# ---------------------------------------------------------------------------

def _stale(company, row) -> bool:
    if row['action'] == ACTION_CREATE:
        return TradeInTariffEntry.objects.filter(company=company, device_key=row['device_key']).exists()
    entry = TradeInTariffEntry.objects.select_for_update().filter(company=company, pk=row['entry']).first()
    return entry is None or _latest_version_id(entry) != row['base_version']


def apply(*, actor, company, batch, request=None) -> dict:
    """Write what the preview staged, all or nothing."""
    tariff._require(actor, company, tariff.CAP_MANAGE, 'No tienes permiso para importar el tarifario.')
    if batch.company_id != company.pk:
        raise TariffImportError('No encontrado.', code='not_found')
    with transaction.atomic():
        batch = TradeInTariffImport.objects.select_for_update().get(pk=batch.pk)
        if batch.status == TradeInTariffImport.STATUS_APPLIED:
            raise tariff.TariffStateError('Esta carga ya se aplicó.', code='already_applied')
        import_freshness.refuse_if_expired(batch, tariff.TariffConflict)
        rows = batch.rows or []
        errors = [row for row in rows if row.get('action') == ACTION_ERROR]
        if errors:
            raise TariffImportError(f'La carga tiene {len(errors)} fila(s) con errores: corrígelas y súbela otra '
                                    'vez. No se aplicó nada.', code='preview_has_errors')
        stale = [row['row'] for row in rows if row['action'] in (ACTION_CREATE, ACTION_UPDATE) and _stale(company, row)]
        if stale:
            shown = ', '.join(str(number) for number in stale[:import_freshness.NAMED])
            raise tariff.TariffConflict(
                f'El tarifario cambió desde la vista previa (filas {shown}). No se aplicó nada: sube el archivo otra '
                'vez para revisarlo con los datos de ahora.', code=import_freshness.PREVIEW_STALE)
        created = proposed = approved = 0
        for row in rows:
            data = {**row['identity'], **row['terms']}
            if row['action'] == ACTION_CREATE:
                _, version = tariff.create_entry(actor=actor, company=company, data=data, request=request,
                                                 source=TradeInTariffVersion.SOURCE_IMPORT, batch=batch)
                created += 1
            elif row['action'] == ACTION_UPDATE:
                entry = TradeInTariffEntry.objects.get(company=company, pk=row['entry'])
                version = tariff.propose_version(actor=actor, company=company, entry=entry, data=data,
                                                 request=request, source=TradeInTariffVersion.SOURCE_IMPORT,
                                                 batch=batch)
                proposed += 1
            else:
                continue
            if version.status == TradeInTariffVersion.STATUS_APPROVED:
                approved += 1
        batch.status = TradeInTariffImport.STATUS_APPLIED
        batch.applied_by = actor
        batch.applied_at = timezone.now()
        batch.save(update_fields=['status', 'applied_by', 'applied_at'])
        result = {'created': created, 'updated': proposed, 'approved_by_policy': approved,
                  'pending': created + proposed - approved}
        from .models import AdminAuditLog

        AdminAuditLog.log(actor=actor, action='trade_in_tariff_import_applied', target_type='trade_in_tariff_import',
                          target_id=batch.pk, metadata={'company_id': company.pk, 'import_id': batch.pk,
                                                        'file_sha256': batch.file_sha256, **result},
                          request=request, company=company)
    return result
