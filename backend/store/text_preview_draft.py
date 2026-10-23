"""
INV-TEXT-03 — the import draft of a reviewed WhatsApp list, for SUPPLIER OFFERS.

THIS IS NOT ANOTHER IMPORTER. The draft is the official template
(`supplier_offer_import.HEADERS` + «Identificador»), written in memory, and its
answer is `supplier_offer_import.preview` itself — run inside a transaction that
is ALWAYS rolled back. Same rules, same messages, and nothing stays: no offer,
no supplier code, no job, no stock (docs/adr-inv-text-03.md).

What travels is only what the template has: the variant the person chose (its
id, resolved in THIS company), the commercial price the person typed, the
quantity (null = unknown, 0 = sold out) and, with `costs.view`, the agreed cost.
Never the pasted text, an IMEI or a serial: other keys are refused.
"""
from __future__ import annotations

import io
import re

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction

from . import import_formats, supplier_offer_import, xlsx_reader
from .models import Product, Supplier

MAX_ROWS = 300            # what the text preview recognizes at most
FILENAME = 'borrador-ofertas-whatsapp.xlsx'
BODY_FIELDS = {'supplier_id', 'valid_from', 'valid_until', 'lead_time_days', 'complete', 'rows'}
ROW_FIELDS = {'key', 'product_id', 'commercial_price', 'confirmed_quantity', 'agreed_cost'}
_SHORT = 20


class BadDraft(ValueError):
    """Not the shape this endpoint reads (400)."""


class NotFound(LookupError):
    """A supplier or product that is not this company's (404, like one that does not exist)."""


class _RolledBack(Exception):
    def __init__(self, payload):
        super().__init__('dry run')
        self.payload = payload


def _id(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    text = str(value).strip()
    return int(text) if re.fullmatch(r'\d{1,12}', text) else None


def _short(value, name):
    """A cell as typed (the importer judges it): a short text or a number, nothing else."""
    if value is None:
        return ''
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise BadDraft(f'«{name}» no es válido.')
    text = str(value).strip()
    if len(text) > _SHORT:
        raise BadDraft(f'«{name}» es demasiado largo.')
    return text


def clean(company, body, *, with_cost: bool) -> dict:
    if not isinstance(body, dict):
        raise BadDraft('El borrador es un objeto.')
    extra = set(body) - BODY_FIELDS
    if extra:
        raise BadDraft(f'Campos no admitidos: {", ".join(sorted(extra))[:80]}.')
    complete = body.get('complete', False)
    if not isinstance(complete, bool):
        raise BadDraft('«complete» es verdadero o falso.')
    rows = body.get('rows')
    if not isinstance(rows, list) or not rows or len(rows) > MAX_ROWS:
        raise BadDraft(f'Envía entre 1 y {MAX_ROWS} filas.')
    cleaned, keys = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise BadDraft('Cada fila es un objeto.')
        extra = set(row) - ROW_FIELDS
        if extra:
            # The pasted text, quantities read from repeated lines, IMEIs and serials never travel here.
            raise BadDraft(f'Campos no admitidos: {", ".join(sorted(extra))[:80]}.')
        key = row.get('key')
        if not isinstance(key, str) or not key.strip() or len(key) > 40 or key in keys:
            raise BadDraft('Cada fila lleva una «key» propia.')
        keys.add(key)
        product_id = _id(row.get('product_id'))
        if product_id is None:
            raise BadDraft('Cada fila lleva la variante elegida («product_id»).')
        quantity = row.get('confirmed_quantity')
        if quantity is not None and (isinstance(quantity, bool) or not isinstance(quantity, int)):
            raise BadDraft('«confirmed_quantity» es un entero o vacío.')
        cleaned.append({
            'key': key, 'product_id': product_id, 'confirmed_quantity': quantity,
            'commercial_price': _short(row.get('commercial_price'), 'commercial_price'),
            # Without `costs.view` the cost is not written at all (DC1): not even an ignored column.
            'agreed_cost': _short(row.get('agreed_cost'), 'agreed_cost') if with_cost else '',
        })
    header = {name: _short(body.get(name), name) for name in ('valid_from', 'valid_until', 'lead_time_days')}

    supplier_id = _id(body.get('supplier_id'))
    supplier = Supplier.objects.filter(company=company, pk=supplier_id).first() if supplier_id else None
    if supplier is None:
        raise NotFound('supplier')
    wanted = {row['product_id'] for row in cleaned}
    found = set(Product.objects.filter(company=company, pk__in=wanted).values_list('pk', flat=True))
    if found != wanted:
        raise NotFound('product')
    return {'supplier': supplier, 'complete': complete, 'rows': cleaned, 'with_cost': with_cost, **header}


def workbook_bytes(company, draft) -> bytes:
    """The draft in the official template: what «Ofertas de proveedores» reads, exactly."""
    headers = [h for h in supplier_offer_import.HEADERS if draft['with_cost'] or h != 'Costo pactado']
    headers.append(supplier_offer_import.IDENTIFIER_HEADER)
    workbook, sheet = supplier_offer_import._sheet(headers)
    for row in draft['rows']:
        values = {
            'Proveedor': draft['supplier'].name, 'Costo pactado': row['agreed_cost'],
            'Precio comercial': row['commercial_price'], 'Cantidad confirmada': row['confirmed_quantity'],
            'Vigente desde': draft['valid_from'], 'Vigente hasta': draft['valid_until'],
            'Plazo (días)': draft['lead_time_days'],
            supplier_offer_import.IDENTIFIER_HEADER: import_formats.product_identifier(company.pk, row['product_id']),
        }
        line = sheet.max_row + 1
        for column, header in enumerate(headers, start=1):
            value = values.get(header)
            cell = sheet.cell(row=line, column=column)
            if isinstance(value, int):
                cell.value = value
            elif value:
                cell.value = value
                xlsx_reader.mark_as_text(cell)      # «=1+1» is a text the importer judges, never a formula
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()


def dry_run(company, actor, draft) -> dict:
    """The official preview's answer for the draft. Everything it staged is rolled back."""
    upload = SimpleUploadedFile(FILENAME, workbook_bytes(company, draft))
    try:
        with transaction.atomic():
            job = supplier_offer_import.preview(
                company=company, actor=actor, upload=upload, filename=FILENAME,
                complete_supplier=draft['supplier'] if draft['complete'] else None)
            raise _RolledBack(supplier_offer_import.job_payload(job, include_cost=draft['with_cost']))
    except _RolledBack as done:
        payload = done.payload
    # The job is gone: no id to apply, no status, no «already applied» of a file nobody uploaded.
    for gone in ('id', 'status', 'already_applied'):
        payload.pop(gone, None)
    keys = [row['key'] for row in draft['rows']]
    for row in payload['rows']:
        index = row['row_number'] - 2          # row 1 holds the headers
        row['key'] = keys[index] if 0 <= index < len(keys) else None
        for internal in ('offer_state', 'offer_updated_at'):
            row['data'].pop(internal, None)
    payload['dry_run'] = True
    return payload
