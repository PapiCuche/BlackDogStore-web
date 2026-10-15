"""
DEVICE-COMMERCE-01 · DC3 — the weekly supplier price list, as a spreadsheet.

    ONE SHEET, «Ofertas de proveedor». ONE ROW = ONE OFFER of one supplier for
    one sellable variant, for one validity window.

THE SAME RULES AS THE SCREEN. Every row ends in `supply_services.create_offer`
or `update_offer`: the price, quantity, dates and lead-time rules, the audit
line and the cost gate are theirs. What this module adds is the rehearsal:
stage every row, say what is wrong with each, write nothing; then apply all of
the file or none of it.

IDEMPOTENT BY ITS KEY. An offer is (supplier, variant, valid_from, valid_until).
A row whose key exists UPDATES that offer (or says «sin cambios»); a row with
a new window CREATES a new offer. Other windows of the same supplier and
variant are left alone — they expire on their own. Re-uploading the same file
changes nothing; next week's file is just another upload.

THE COST COLUMN («Costo pactado») is read only for `costs.view` (DC1): for
anybody else it is ignored — never compared, never written — and the preview
says so once.
"""
from __future__ import annotations

import io
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from . import device_catalog, supply_services, xlsx_reader
from .import_exports import _autosize, _header_row, _workbook, header_notes, number_columns, text_columns
from .import_formats import normalize_header
from .import_services import CatalogueIndex, _safe_filename, _sha256
from .models import AdminAuditLog, BulkImportJob, BulkImportRow, Product, Supplier, SupplierOffer

SHEET = 'Ofertas de proveedor'
TEMPLATE_FILENAME = 'ofertas-de-proveedor.xlsx'
EXPORT_FILENAME = 'ofertas-de-proveedor-vigentes.xlsx'
MAX_ROWS = 2000

HEADERS = ['Proveedor', 'Código/SKU del producto', 'Modelo', 'Capacidad', 'Color', 'Línea', 'Costo pactado',
           'Precio comercial', 'Cantidad confirmada', 'Vigente desde', 'Vigente hasta', 'Plazo (días)']

_ALIASES = {
    'supplier': ('proveedor', 'nombre del proveedor'),
    'code': ('codigo sku del producto', 'codigo', 'sku', 'codigo del producto', 'codigo de barras', 'codigo sku'),
    'name': ('producto', 'nombre del producto'),
    'model': ('modelo',),
    'capacity': ('capacidad', 'almacenamiento'),
    'colour': ('color', 'colour'),
    'line': ('linea', 'linea nuevo seminuevo'),
    'agreed_cost': ('costo pactado', 'costo', 'costo proveedor'),
    'commercial_price': ('precio comercial', 'precio de venta bajo pedido', 'precio'),
    'confirmed_quantity': ('cantidad confirmada', 'cantidad'),
    'valid_from': ('vigente desde', 'desde', 'inicio de vigencia'),
    'valid_until': ('vigente hasta', 'hasta', 'fin de vigencia'),
    'lead_time_days': ('plazo dias', 'plazo', 'plazo de entrega dias', 'dias de entrega'),
}


class OfferImportError(Exception):
    """The file, or the job, cannot be used. The message is for the operator."""


# ---------------------------------------------------------------------------
# Template and export
# ---------------------------------------------------------------------------

def _notes() -> dict:
    return {
        'Proveedor': 'El nombre del proveedor tal como está en Productos › Proveedores. Debe existir.',
        'Código/SKU del producto': (
            'El código de barras o el código (slug) de la variante que se vende. Si lo dejas vacío, '
            '«Modelo», «Capacidad», «Color» y «Línea» dicen qué variante es.'),
        'Modelo': 'Opcional, sin código: el modelo del catálogo de equipos.',
        'Capacidad': 'Opcional, con «Modelo»: una opción que ya existe.',
        'Color': 'Opcional, con «Modelo»: una opción que ya existe.',
        'Línea': 'Opcional: «Nuevo». Un seminuevo nunca se vende bajo pedido.',
        'Costo pactado': (
            'Lo que la tienda paga al proveedor por unidad, sin símbolo de moneda. Sólo se lee si tienes '
            'permiso para ver costos; si no, se ignora.'),
        'Precio comercial': 'Lo que paga el cliente cuando compra bajo pedido.',
        'Cantidad confirmada': 'Cuántas unidades confirmó el proveedor. Vacío = sin confirmar: no se vende.',
        'Vigente desde': 'Primer día de la oferta (AAAA-MM-DD o DD/MM/AAAA).',
        'Vigente hasta': 'Último día de la oferta, incluido.',
        'Plazo (días)': 'Días hasta la entrega al cliente.',
    }


def _sheet(headers):
    workbook = _workbook()
    sheet = workbook.active
    sheet.title = SHEET
    _header_row(sheet, headers)
    sheet.freeze_panes = 'A2'
    return workbook, sheet


def template_bytes() -> bytes:
    workbook, sheet = _sheet(HEADERS)
    header_notes(sheet, _notes())
    _autosize(sheet, [28, 28, 22, 14, 14, 12, 16, 18, 20, 16, 16, 14])
    text_columns(sheet, ('B',))
    number_columns(sheet, ('G', 'H', 'I', 'L'))
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()


def _product_code(product) -> str:
    barcode = (product.barcodes.filter(is_active=True, is_primary=True).first()
               or product.barcodes.filter(is_active=True).first())
    return barcode.code if barcode else product.slug


def export_bytes(*, company, include_cost: bool) -> bytes:
    """The offers that are not over yet, in the template's columns — a file that loads back unchanged."""
    headers = [h for h in HEADERS if include_cost or h != 'Costo pactado']
    headers += ['Comprometido', 'Estado']
    workbook, sheet = _sheet(headers)
    today = supply_services.today()
    offers = (SupplierOffer.objects.filter(company=company, is_active=True, valid_until__gte=today)
              .select_related('supplier', 'product').order_by('supplier__name', 'product__name', 'valid_from'))
    from .supplier_views import offer_state

    for offer in offers:
        row = {
            'Proveedor': offer.supplier.name, 'Código/SKU del producto': _product_code(offer.product),
            'Modelo': '', 'Capacidad': '', 'Color': '', 'Línea': '',
            'Costo pactado': float(offer.agreed_cost) if offer.agreed_cost is not None else None,
            'Precio comercial': float(offer.commercial_price), 'Cantidad confirmada': offer.confirmed_quantity,
            'Vigente desde': offer.valid_from.isoformat(), 'Vigente hasta': offer.valid_until.isoformat(),
            'Plazo (días)': offer.lead_time_days, 'Comprometido': offer.committed_quantity,
            'Estado': offer_state(offer, today),
        }
        sheet.append([xlsx_reader.guard_text(v) if isinstance(v, str) else v for v in (row[h] for h in headers)])
    _autosize(sheet, [28, 28, 22, 14, 14, 12, 16, 18, 20, 16, 16, 14, 14, 14][:len(headers)])
    text_columns(sheet, ('B',))
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

def _columns(headers) -> dict[str, int]:
    folded = [normalize_header(header) for header in headers]
    columns = {}
    for field, aliases in _ALIASES.items():
        for index, key in enumerate(folded):
            if key in aliases and index not in columns.values():
                columns[field] = index
                break
    if 'supplier' not in columns:
        raise OfferImportError('No se encontró la columna «Proveedor». Descarga la plantilla de ofertas y úsala.')
    if 'commercial_price' not in columns:
        raise OfferImportError('No se encontró la columna «Precio comercial».')
    if 'code' not in columns and 'name' not in columns and 'model' not in columns:
        raise OfferImportError('No se encontró la columna «Código/SKU del producto» ni «Modelo».')
    return columns


def _pick_sheet(workbook) -> str:
    names = list(workbook.sheetnames)
    for name in names:
        if normalize_header(name) == normalize_header(SHEET):
            return name
    return names[0]


def _resolve_product(company, index, fields):
    code = fields.get('code') or ''
    if code:
        owner = index.owner_of(code)
        if owner:
            return Product.objects.filter(company=company, pk=owner).first(), None
        product = Product.objects.filter(company=company, slug__iexact=code.strip()).first()
        if product is not None:
            return product, None
        return None, f'No existe un producto con el código «{code[:40]}».'
    if fields.get('model'):
        try:
            spec = device_catalog.resolve_variant_spec(
                company, model_name=fields['model'], line_text=fields.get('line') or '',
                labels=[fields.get('capacity'), fields.get('colour')])
        except device_catalog.CatalogError as exc:
            return None, str(exc)
        product = device_catalog.find_variant(company, spec)
        if product is None:
            return None, 'No existe esa variante del modelo. Créala en Catálogo › Modelos.'
        return product, None
    if fields.get('name'):
        product_id, error, _by, _warnings = index.match(name=fields['name'])
        if product_id and not error:
            return Product.objects.filter(company=company, pk=product_id).first(), None
        return None, error or f'No existe el producto «{fields["name"][:60]}».'
    return None, 'Indica el código del producto o su modelo.'


_COMPARED = ('commercial_price', 'confirmed_quantity', 'lead_time_days')


@transaction.atomic
def preview(*, company, actor, upload, filename) -> BulkImportJob:
    """Stage a price list. Writes NO offer."""
    data = xlsx_reader.check_upload(upload, filename=filename)
    workbook, _notes_ = xlsx_reader.load_workbook(data)
    sheet_name = _pick_sheet(workbook)
    try:
        headers, rows, _truncated = xlsx_reader.read_rows(
            workbook, sheet_name, header_row=1, limit=MAX_ROWS + 1, mode=xlsx_reader.FULL_IMPORT)
    except xlsx_reader.XlsxError:
        raise OfferImportError(f'Se cargan hasta {MAX_ROWS} ofertas por archivo. Divide el archivo.') from None
    columns = _columns(headers)
    from .cost_services import user_may_view_costs

    with_cost = user_may_view_costs(actor, company)
    cost_ignored = 'agreed_cost' in columns and not with_cost

    suppliers = {' '.join(s.name.split()).lower(): s for s in Supplier.objects.filter(company=company)}
    index = CatalogueIndex(company)
    job = BulkImportJob.objects.create(
        company=company, import_type=BulkImportJob.SUPPLIER_OFFERS,
        original_filename=_safe_filename(filename), file_sha256=_sha256(data),
        mapping_snapshot={'sheet_name': sheet_name, 'mapping': columns, 'headers': [str(h) for h in headers]},
        options_snapshot={'with_cost': with_cost}, created_by=actor,
        summary={'cost_column_ignored': cost_ignored},
    )
    staged, counts, seen = [], dict(create=0, update=0, no_change=0, skip=0, error=0), {}

    def stage(row_number, action, *, key='', data=None, errors=None, warnings=None):
        staged.append(BulkImportRow(job=job, sheet_name=sheet_name, row_number=row_number, action=action,
                                    match_key=key[:120], normalized_data=data or {}, errors=errors or [],
                                    warnings=warnings or []))
        counts[action] += 1

    for row_number, values, _numeric in rows:
        fields = {}
        formula = False
        for field in _ALIASES:
            raw = values.get(columns[field]) if field in columns else None
            if raw is xlsx_reader.FORMULA:
                formula = True
                raw = None
            fields[field] = '' if raw is None else str(raw).strip()
        if formula:
            stage(row_number, BulkImportRow.ERROR, errors=['La fila tiene fórmulas: escribe los valores.'])
            continue
        if not any(fields.values()):
            stage(row_number, BulkImportRow.SKIP, warnings=['Fila vacía.'])
            continue

        errors, warnings = [], []
        supplier = suppliers.get(' '.join(fields['supplier'].split()).lower()) if fields['supplier'] else None
        if supplier is None:
            errors.append(f'No existe el proveedor «{fields["supplier"][:60]}». Créalo en Productos › Proveedores.'
                          if fields['supplier'] else 'Indica el proveedor.')
        product, problem = _resolve_product(company, index, fields)
        if problem:
            errors.append(problem)
        elif product is not None and product.condition_line == Product.CONDITION_USED:
            errors.append('Un seminuevo se vende por equipo, nunca bajo pedido.')

        clean = {}
        checks = [
            ('commercial_price', lambda v: supply_services.clean_money(v, 'commercial_price', 'el precio comercial',
                                                                       required=True, positive=True)),
            ('confirmed_quantity', lambda v: supply_services.clean_int(v, 'confirmed_quantity',
                                                                       'la cantidad confirmada', required=False)),
            ('valid_from', lambda v: supply_services.clean_date(v, 'valid_from', 'el inicio de la vigencia')),
            ('valid_until', lambda v: supply_services.clean_date(v, 'valid_until', 'el fin de la vigencia')),
            ('lead_time_days', lambda v: supply_services.clean_int(v, 'lead_time_days', 'el plazo en días',
                                                                   maximum=supply_services.MAX_LEAD_TIME_DAYS)),
        ]
        if with_cost and 'agreed_cost' in columns:
            checks.append(('agreed_cost', lambda v: supply_services.clean_money(v, 'agreed_cost', 'el costo pactado')))
        for field, cleaner in checks:
            try:
                clean[field] = cleaner(fields.get(field))
            except supply_services.SupplyError as exc:
                errors.append(exc.detail)
        if clean.get('valid_from') and clean.get('valid_until') and clean['valid_until'] < clean['valid_from']:
            errors.append('La vigencia termina antes de empezar.')
        if cost_ignored:
            warnings.append('Columna «Costo pactado» ignorada: requiere permiso para ver costos.')

        shown = {
            'supplier': fields['supplier'], 'product_name': product.name if product else (fields['code'] or
                                                                                          fields['model']),
            **{k: (str(v) if not isinstance(v, int) or isinstance(v, bool) else v)
               for k, v in clean.items() if v is not None},
        }
        if errors:
            stage(row_number, BulkImportRow.ERROR, data=shown, errors=errors, warnings=warnings)
            continue
        key = (supplier.pk, product.pk, clean['valid_from'], clean['valid_until'])
        if key in seen:
            stage(row_number, BulkImportRow.ERROR, data=shown, warnings=warnings,
                  errors=[f'Repite la oferta de la fila {seen[key]} (mismo proveedor, producto y vigencia).'])
            continue
        seen[key] = row_number
        existing = SupplierOffer.objects.filter(
            company=company, supplier=supplier, product=product, valid_from=clean['valid_from'],
            valid_until=clean['valid_until']).first()
        payload = {
            **shown, 'supplier_id': supplier.pk, 'product_id': product.pk,
            'valid_from': clean['valid_from'].isoformat(), 'valid_until': clean['valid_until'].isoformat(),
            'commercial_price': str(clean['commercial_price']), 'confirmed_quantity': clean['confirmed_quantity'],
            'lead_time_days': clean['lead_time_days'],
        }
        if 'agreed_cost' in clean:
            payload['agreed_cost'] = str(clean['agreed_cost']) if clean['agreed_cost'] is not None else None
        if existing is None:
            stage(row_number, BulkImportRow.CREATE, key=f'{supplier.pk}:{product.pk}', data=payload,
                  warnings=warnings)
            continue
        payload['offer_id'] = existing.pk
        confirmed = clean['confirmed_quantity']
        if existing.committed_quantity and (confirmed is None or confirmed < existing.committed_quantity):
            stage(row_number, BulkImportRow.ERROR, data=shown, warnings=warnings, errors=[
                f'Ya se vendieron {existing.committed_quantity} contra esta oferta: la cantidad confirmada '
                'no puede ser menor.'])
            continue
        same = (existing.commercial_price == clean['commercial_price']
                and existing.confirmed_quantity == confirmed
                and existing.lead_time_days == clean['lead_time_days']
                and existing.is_active
                and ('agreed_cost' not in clean or existing.agreed_cost == clean['agreed_cost']))
        stage(row_number, BulkImportRow.NO_CHANGE if same else BulkImportRow.UPDATE,
              key=f'{supplier.pk}:{product.pk}', data=payload, warnings=warnings)

    BulkImportRow.objects.bulk_create(staged)
    job.rows_total = len(staged)
    job.rows_create, job.rows_update = counts['create'], counts['update']
    job.rows_no_change, job.rows_skip, job.rows_error = counts['no_change'], counts['skip'], counts['error']
    job.save(update_fields=['rows_total', 'rows_create', 'rows_update', 'rows_no_change', 'rows_skip',
                            'rows_error'])
    return job


def job_payload(job, *, include_cost: bool) -> dict:
    rows = []
    for row in job.rows.order_by('row_number', 'pk')[:MAX_ROWS]:
        data = dict(row.normalized_data or {})
        if not include_cost:
            data.pop('agreed_cost', None)
        data.pop('supplier_id', None)
        rows.append({'row_number': row.row_number, 'action': row.action, 'errors': row.errors,
                     'warnings': row.warnings, 'data': data})
    return {
        'id': job.pk, 'status': job.status, 'rows_total': job.rows_total, 'rows_create': job.rows_create,
        'rows_update': job.rows_update, 'rows_no_change': job.rows_no_change, 'rows_error': job.rows_error,
        'cost_column_ignored': bool((job.summary or {}).get('cost_column_ignored')),
        'rows': rows,
    }


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

@transaction.atomic
def apply(*, job, actor, request=None) -> dict:
    """
    Write every staged offer. ALL OF THE FILE OR NONE OF IT. A job applied
    before answers with what it did then, and does nothing.
    """
    job = BulkImportJob.objects.select_for_update().get(pk=job.pk)
    if job.import_type != BulkImportJob.SUPPLIER_OFFERS:
        raise OfferImportError('Este trabajo no es una carga de ofertas.')
    if job.status == BulkImportJob.APPLIED:
        return {'status': 'applied', **(job.summary or {}).get('applied', {})}
    if job.rows_error:
        raise OfferImportError(f'Hay {job.rows_error} fila(s) con error. Corrige el archivo y vuelve a subirlo.')
    with_cost = bool((job.options_snapshot or {}).get('with_cost'))
    created = updated = unchanged = 0
    for row in job.rows.order_by('row_number', 'pk'):
        data = row.normalized_data or {}
        fields = {key: data.get(key) for key in ('commercial_price', 'confirmed_quantity', 'valid_from',
                                                 'valid_until', 'lead_time_days')}
        if with_cost and 'agreed_cost' in data:
            fields['agreed_cost'] = data['agreed_cost']
        try:
            if row.action == BulkImportRow.CREATE:
                supply_services.create_offer(company=job.company, actor=actor, request=request,
                                             may_view_costs=with_cost, supplier_id=data.get('supplier_id'),
                                             product_id=data.get('product_id'), **fields)
                created += 1
            elif row.action == BulkImportRow.UPDATE:
                offer = SupplierOffer.objects.filter(company=job.company, pk=data.get('offer_id')).first()
                if offer is None:
                    raise OfferImportError(f'La oferta de la fila {row.row_number} ya no existe. Vuelve a subir el '
                                           'archivo.')
                supply_services.update_offer(offer, actor=actor, request=request, may_view_costs=with_cost,
                                             is_active=True, **fields)
                updated += 1
            elif row.action == BulkImportRow.NO_CHANGE:
                unchanged += 1
        except supply_services.SupplyError as exc:
            raise OfferImportError(f'Fila {row.row_number}: {exc.detail}') from None
    result = {'created': created, 'updated': updated, 'unchanged': unchanged}
    job.status = BulkImportJob.APPLIED
    job.applied_by = actor
    job.applied_at = timezone.now()
    job.summary = {**(job.summary or {}), 'applied': result}
    job.save(update_fields=['status', 'applied_by', 'applied_at', 'summary'])
    AdminAuditLog.log(actor=actor, action='supplier_offers_import_applied', target_type='bulk_import_job',
                      target_id=job.pk, metadata={'job_id': job.pk, **result, 'file_sha256': job.file_sha256},
                      request=request, company=job.company)
    return {'status': 'applied', **result}
