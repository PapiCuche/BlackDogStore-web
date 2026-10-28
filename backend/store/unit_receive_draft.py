"""
INV-FAST-B2 — the reviewed list, registered through the OFFICIAL device importer.

    NO NEW RULES. THE ROWS BECOME THE OFFICIAL TEMPLATE, IN MEMORY.

`POST …/text-preview/units/preview/` takes the draft rows as JSON and writes
them as the «Equipos» workbook (`unit_import_services.HEADERS`) that
`preview_units` reads: the same reader, the same row validators (serial, IMEI,
uniqueness against the company's stock, branch reach, reason), the same
`BulkImportJob`. What comes back is the official preview, each row with the
`key` the browser gave it. Applying is the OFFICIAL `…/units/import/<id>/apply/`,
untouched: `receive_units` is the only writer of units, the Kardex and the
LABEL-A batches, all or nothing, and a second apply of the job changes nothing.

A preview stages a job and moves NO unit, NO movement and NO label — exactly
what uploading the file does. The IMEIs and serials travel only here, to the
official device path of the company that owns them.
"""
from __future__ import annotations

import io

from django.core.files.uploadedfile import SimpleUploadedFile

from . import unit_import_services, xlsx_reader
from .import_exports import _autosize, _header_row, _workbook
from .models import Product, ProductBarcode, StockUnit

FILENAME = 'equipos-de-la-lista.xlsx'
ALLOWED_FIELDS = {'key', 'product_id', 'serial_number', 'imei', 'imei2', 'condition', 'cost', 'battery_health'}
_TEXT_LIMIT = 64


class BadDraft(ValueError):
    """The request is not what this endpoint reads (400)."""


class NotFound(LookupError):
    """A product of another company, or none (404)."""


def _text(value, label):
    if value is None:
        return ''
    if isinstance(value, bool) or not isinstance(value, (str, int)) or len(str(value)) > _TEXT_LIMIT:
        raise BadDraft(f'{label}: texto inválido.')
    return ' '.join(str(value).split())


def clean(company, body, *, with_cost: bool) -> dict:
    if not isinstance(body, dict):
        raise BadDraft('Cuerpo inválido.')
    rows = body.get('rows')
    if not isinstance(rows, list) or not rows or len(rows) > unit_import_services.MAX_UNITS:
        raise BadDraft(f'Envía entre 1 y {unit_import_services.MAX_UNITS} filas.')
    reason = ' '.join(str(body.get('reason') or '').split())[:200]
    if not reason:
        raise BadDraft('Indica el motivo o el documento de ingreso.')
    conditions = {value for value, _label in StockUnit.Condition.choices}
    cleaned, keys = [], set()
    for raw in rows:
        if not isinstance(raw, dict):
            raise BadDraft('Cada fila es un objeto.')
        extra = set(raw) - ALLOWED_FIELDS
        if extra:
            raise BadDraft(f'Campos no admitidos: {", ".join(sorted(extra))[:80]}.')
        key = _text(raw.get('key'), 'key')
        if not key or len(key) > 40:
            raise BadDraft('Falta «key».')
        if key in keys:
            raise BadDraft('Hay claves repetidas.')
        keys.add(key)
        product_id = raw.get('product_id')
        if isinstance(product_id, bool) or not isinstance(product_id, int) or product_id <= 0:
            raise BadDraft('Falta el producto de cada fila.')
        condition = raw.get('condition') or StockUnit.Condition.NEW
        if condition not in conditions:
            raise BadDraft('Condición inválida.')
        cleaned.append({
            'key': key, 'product_id': product_id, 'condition': condition,
            'serial_number': _text(raw.get('serial_number'), 'Número de serie'),
            'imei': _text(raw.get('imei'), 'IMEI'), 'imei2': _text(raw.get('imei2'), 'IMEI 2'),
            'cost': _text(raw.get('cost'), 'Costo') if with_cost else '',
            'battery_health': _text(raw.get('battery_health'), 'Batería'),
        })
    products = {p.pk: p for p in Product.objects.filter(company=company, pk__in={r['product_id'] for r in cleaned})}
    if any(r['product_id'] not in products for r in cleaned):
        raise NotFound()
    codes: dict[int, str] = {}
    for product_id, code in (ProductBarcode.objects.filter(company=company, product_id__in=products)
                             .order_by('product_id', '-is_active', '-is_primary', 'pk').values_list('product_id', 'code')):
        codes.setdefault(product_id, code)
    return {'rows': cleaned, 'products': products, 'codes': codes, 'reason': reason, 'with_cost': with_cost,
            'cost_asked': any(isinstance(r, dict) and r.get('cost') not in (None, '') for r in rows)}


def workbook_bytes(branch, draft) -> bytes:
    """The rows in the official «Equipos» template: what `preview_units` reads, exactly."""
    labels = dict(StockUnit.Condition.choices)
    workbook = _workbook()
    sheet = workbook.active
    sheet.title = unit_import_services.SHEET
    _header_row(sheet, unit_import_services.HEADERS)
    _autosize(sheet, [20] * len(unit_import_services.HEADERS))
    for item in draft['rows']:
        product = draft['products'][item['product_id']]
        code = draft['codes'].get(product.pk, '')
        values = {
            # The product by its code when it has one; by name otherwise (the official match refuses an ambiguous name).
            'Código': code, 'Producto': '' if code else product.name, 'Sucursal': branch.name,
            'Número de serie': item['serial_number'], 'IMEI': item['imei'], 'IMEI 2': item['imei2'],
            'Condición': labels[item['condition']], 'Costo': item['cost'], 'Motivo / referencia': draft['reason'],
            'Batería %': item['battery_health'],
        }
        line = sheet.max_row + 1
        for column, header in enumerate(unit_import_services.HEADERS, start=1):
            value = values.get(header)
            if value:
                # «=1+1», a long digit run or a leading zero is a text the importer judges, never a formula.
                xlsx_reader.mark_as_text(sheet.cell(row=line, column=column, value=value))
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()


def stage(*, company, actor, branch, reachable_branch_ids, draft):
    """The official preview of the draft. Returns the staged job; the key of each row is its row number - 2."""
    upload = SimpleUploadedFile(FILENAME, workbook_bytes(branch, draft))
    return unit_import_services.preview_units(
        company=company, actor=actor, upload=upload, filename=FILENAME, reachable_branch_ids=reachable_branch_ids,
        default_branch=branch.pk, default_reason=draft['reason'],
    )
