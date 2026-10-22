"""
DEVICE-COMMERCE-01 · DC3 — the weekly supplier price list, as a spreadsheet.
SUPPLIER-DEVICE-FLOW-02 · A — the owner's rules for that weekly file.

    ONE SHEET, «Ofertas de proveedor». ONE ROW = THE OFFER of one supplier for
    one sellable variant.

THE SAME RULES AS THE SCREEN. Every row ends in `supply_services.create_offer`
or `update_offer`: the price, quantity, dates and lead-time rules, the audit
line and the cost gate are theirs. What this module adds is the rehearsal:
stage every row, say what is wrong with each and what it would change, write
nothing; then apply all of the file or none of it.

A PARTIAL UPDATE (owner, SUPPLIER-DEVICE-FLOW-02). Only the rows of the file
change something. An offer the file does not mention is not touched: not
switched off, its quantity and its window stay.

ONE OFFER PER SUPPLIER AND VARIANT AND WINDOW, UPDATED IN PLACE
(`match_offer`). A row updates the offer with exactly its window. Else, when
an active offer is running TODAY: a row starting today or before that overlaps
or touches it (a day apart) updates it in place; a row starting in the future
that touches it or comes after it CREATES next week's offer — today's keeps
selling, the overlap rule keeps the two disjoint; a row starting in the future
that OVERLAPS it is an error (`overlaps_running`): uploading next week's file
early never stops nor moves what sells today. Else the scheduled or switched-
off offer the row reaches is updated in place; else (every offer ended) the
one that ended last — what was committed stays committed (see NET
AVAILABILITY). An offer off only because the
expiry job switched it off (its last audited switch-off is
`supplier_offer_expired`; a typed reason never counts) comes back on with new
days; one switched off by hand stays matched and off. Two rows of one file
for the same supplier and variant are an error. Before this, the key carried
the window: next week's file created a SECOND offer and both sold over the
overlapping days.

NEVER TWO ACTIVE OFFERS OF ONE SUPPLIER AND VARIANT OVER THE SAME DAYS — by
file (row error, and STALE at apply) or by hand (`guard_offer_overlap`, from the
panel's create and edit). No database constraint: production may already hold
overlapping rows from before, and an exclusion constraint would refuse the
migration. A row that keeps an overlap which already existed warns instead.
Each supplier is independent: two suppliers of one variant may overlap.

NET AVAILABILITY (owner D1, 2026-10-21). The file's quantity is what the
supplier still has at the cut-off: what was already sold against the offer is
reflected in it and is never subtracted again. A row that updates an offer
sets `confirmed = committed + file quantity`, `committed` read under the
offer's lock when applied — so what is left to sell after applying is EXACTLY
the file's quantity, whatever payments committed between preview and apply,
and never negative. A new offer confirms the file's quantity. Rows are
compared on what is left (`available`), not on `confirmed`: the identical
file again changes nothing. Audited per offer (`supplier_offer_quantity_set`:
confirmed, committed and available before and after). An empty quantity on an
offer with units sold is an error (it cannot become «sin confirmar»).

AN EXPLICIT 0 IS «AGOTADA». `confirmed = committed`: nothing more sells,
orders already paid keep theirs.

THE COMMERCIAL PRICE IS NEVER CHANGED IN SILENCE. A row that changes an
offer's `commercial_price` (what the customer pays on order) is shown before →
after, and apply refuses the job (`price_confirmation_required`, HTTP 409, the
rows listed) unless the request decides: `accept_price_changes: true` writes
the new prices, `keep_prices: true` writes everything else and keeps every
current price. Both at once is refused. Each decision is audited per offer
(`supplier_offer_price_changed` with before/after, `supplier_offer_price_kept`
with the current and the proposed price) with the job id.

A STALE PREVIEW IS NOT APPLIED. The preview keeps what it read of each
existing offer (`offer_state`: price, confirmed, window, lead time, state,
cost); apply compares it under the lock (an edit, another file) and refuses
the WHOLE job — a payment only moves `committed`, which NET AVAILABILITY
already absorbs, so it never makes a preview stale — «El archivo cambió: vuelve a
previsualizarlo» — before writing anything. A row the preview staged as new
is stale too when, by then, the supplier and variant have an offer that is
not over, or one over those days.

THE SAME FILE TWICE. Under NET AVAILABILITY, re-applying a file after sales
would set what is left once more (applied 4, sold 3, the same file again → 4
left, not 1). A preview whose SHA-256 is that of a file already APPLIED in the
company says so (`already_applied: {job_id, applied_at}`), and apply refuses it
(409 `already_applied`, nothing written) unless `confirm_reapply: true`; the
confirmation is audited (`supplier_offers_reapply_confirmed`). Checked again at
apply, after the stale checks.

WHICH VARIANT. In order: the system code («Identificador», or a code cell that
is one); a barcode or SKU (slug); the supplier's own code already mapped to a
variant (`SupplierProductRef`, «Código del proveedor», or an unmatched code
cell); model and options; the name, only when one product carries it. Two keys
naming different variants are an error — nothing is guessed — and so is a
model whose options fit several variants. A row whose supplier code matched
without doubt by another key records the mapping when applied (audited); an
unmatched or ambiguous row offers `mappable` so a person picks the variant
(`save_product_ref`, `POST /api/admin/suppliers/<pk>/product-refs/`) and the
next preview matches it.

THE COST COLUMN («Costo pactado») is read only for `costs.view` (DC1): for
anybody else it is ignored — never compared, never written — and the preview
says so once. It is asked again of whoever APPLIES the file. An EMPTY cost
cell means «no change». The preview's `before` block carries the cost only to
`costs.view`.

AN OFFER SWITCHED OFF BY HAND STAYS OFF. A row reactivates an offer only when
its optional «Activa (Sí/No)» column says «Sí» (and «No» switches one off);
without it the preview warns and the rest of the row still applies.

LOCK ORDER: the job row; then a transaction advisory lock per (company,
supplier, variant) of the job, sorted — the panel's create and edit take the
same one, so two writers of one supplier and variant take turns; then the
job's existing offers in id order — the order every payment takes them in —
never the file's row order.
"""
from __future__ import annotations

import hashlib
import io
from collections import defaultdict
from datetime import timedelta

from django.db import IntegrityError, connection, transaction
from django.db.models import Count
from django.utils import timezone

from . import device_catalog, import_formats, supply_services, xlsx_reader
from .import_exports import _autosize, _header_row, _workbook, header_notes, number_columns, text_columns
from .import_formats import normalize_header
from .import_services import CatalogueIndex, _safe_filename, _sha256
from .models import (
    AdminAuditLog, BulkImportJob, BulkImportRow, Product, ProductVariantOption, Supplier, SupplierOffer,
    SupplierProductRef,
)

SHEET = 'Ofertas de proveedor'
TEMPLATE_FILENAME = 'ofertas-de-proveedor.xlsx'
EXPORT_FILENAME = 'ofertas-de-proveedor-vigentes.xlsx'
MAX_ROWS = 2000

HEADERS = ['Proveedor', 'Código/SKU del producto', 'Modelo', 'Capacidad', 'Color', 'Línea', 'Costo pactado',
           'Precio comercial', 'Cantidad confirmada', 'Vigente desde', 'Vigente hasta', 'Plazo (días)']
#: Optional, only in the template: an export never writes it (re-loading an old
#: export must not switch back on an offer somebody switched off since).
ACTIVE_HEADER = 'Activa (Sí/No)'
#: COMMERCE-ADMIN-UX-01 — optional, template only, like «Activa»: whether the
#: product may be sold «Bajo pedido» (`supply_services.set_sale_mode`). Empty =
#: no change.
ON_ORDER_HEADER = 'Bajo pedido (Sí/No)'
#: SUPPLIER-DEVICE-FLOW-02 — optional: the supplier's own code for the variant.
SUPPLIER_CODE_HEADER = 'Código del proveedor'
#: The system code (`import_formats.product_identifier`): written by the export.
IDENTIFIER_HEADER = 'Identificador'
STALE = 'El archivo cambió: vuelve a previsualizarlo.'
MAX_REF_CODE = 120
_YES = {'si', 's', 'yes', 'y', '1', 'true', 'verdadero', 'x'}
_NO = {'no', 'n', '0', 'false', 'falso'}

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
    'active': ('activa si no', 'activa', 'activo', 'activa sino'),
    'on_order': ('bajo pedido si no', 'bajo pedido', 'bajo pedido sino', 'venta bajo pedido'),
    'supplier_code': ('codigo del proveedor', 'codigo proveedor', 'codigo de proveedor', 'referencia del proveedor',
                      'sku del proveedor'),
    'identifier': ('identificador', 'codigo del sistema'),
}


class OfferImportError(Exception):
    """The file, or the job, cannot be used. The message is for the operator; `extra` travels in the answer."""

    def __init__(self, message: str, *, code: str = 'invalid', extra: dict | None = None):
        super().__init__(message)
        self.code = code
        self.extra = extra or {}


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
        'Precio comercial': ('Lo que paga el cliente cuando compra bajo pedido. Si cambia el de una oferta, '
                             'al aplicar se te pide aceptarlo o mantener el actual.'),
        'Cantidad confirmada': ('Cuántas unidades le QUEDAN al proveedor a la fecha del archivo (lo ya vendido '
                                'no se resta otra vez). Vacío = sin confirmar: no se vende. 0 = agotada: no se '
                                'vende más; lo ya vendido se mantiene.'),
        'Vigente desde': 'Primer día de la oferta (AAAA-MM-DD o DD/MM/AAAA).',
        'Vigente hasta': 'Último día de la oferta, incluido.',
        'Plazo (días)': 'Días hasta la entrega al cliente.',
        ACTIVE_HEADER: ('Opcional. Vacío = no cambia. «Sí» reactiva una oferta desactivada; «No» la desactiva. '
                        'Sin «Sí», una oferta desactivada a mano sigue desactivada.'),
        ON_ORDER_HEADER: ('Opcional. Vacío = no cambia. «Sí»: el producto se puede vender bajo pedido con sus '
                          'ofertas vigentes; «No»: sólo con stock propio.'),
        SUPPLIER_CODE_HEADER: ('Opcional: el código con el que el proveedor llama a esta variante. Se recuerda: '
                               'la próxima vez basta con él.'),
    }


def _sheet(headers):
    workbook = _workbook()
    sheet = workbook.active
    sheet.title = SHEET
    _header_row(sheet, headers)
    sheet.freeze_panes = 'A2'
    return workbook, sheet


def template_bytes() -> bytes:
    workbook, sheet = _sheet(HEADERS + [ACTIVE_HEADER, ON_ORDER_HEADER, SUPPLIER_CODE_HEADER])
    header_notes(sheet, _notes())
    _autosize(sheet, [28, 28, 22, 14, 14, 12, 16, 18, 20, 16, 16, 14, 16, 18, 22])
    text_columns(sheet, ('B', 'O'))
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
    headers = [IDENTIFIER_HEADER] + [h for h in HEADERS if include_cost or h != 'Costo pactado']
    headers += [SUPPLIER_CODE_HEADER, 'Comprometido', 'Estado']
    workbook, sheet = _sheet(headers)
    today = supply_services.today()
    offers = (SupplierOffer.objects.filter(company=company, is_active=True, valid_until__gte=today)
              .select_related('supplier', 'product').order_by('supplier__name', 'product__name', 'valid_from'))
    refs = {}
    for supplier_id, product_id, code in (SupplierProductRef.objects.filter(company=company)
                                          .order_by('supplier_code', 'pk')
                                          .values_list('supplier_id', 'product_id', 'supplier_code')):
        refs.setdefault((supplier_id, product_id), code)
    from .supplier_views import offer_state

    for offer in offers:
        row = {
            IDENTIFIER_HEADER: import_formats.product_identifier(company.pk, offer.product_id),
            'Proveedor': offer.supplier.name, 'Código/SKU del producto': _product_code(offer.product),
            'Modelo': '', 'Capacidad': '', 'Color': '', 'Línea': '',
            'Costo pactado': float(offer.agreed_cost) if offer.agreed_cost is not None else None,
            'Precio comercial': float(offer.commercial_price), 'Cantidad confirmada': _available(offer),
            'Vigente desde': offer.valid_from.isoformat(), 'Vigente hasta': offer.valid_until.isoformat(),
            'Plazo (días)': offer.lead_time_days,
            SUPPLIER_CODE_HEADER: refs.get((offer.supplier_id, offer.product_id), ''),
            'Comprometido': offer.committed_quantity, 'Estado': offer_state(offer, today),
        }
        sheet.append([xlsx_reader.guard_text(v) if isinstance(v, str) else v for v in (row[h] for h in headers)])
    _autosize(sheet, [14, 28, 28, 22, 14, 14, 12, 16, 18, 20, 16, 16, 14, 22, 14, 14][:len(headers)])
    text_columns(sheet, ('A', 'C'))
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# One offer per supplier and variant
# ---------------------------------------------------------------------------

def lock_pairs(company_id, pairs) -> None:
    """
    Transaction advisory locks on «this supplier's offers of this variant», in
    one sorted order. Taken before any offer row (see LOCK ORDER).
    """
    if connection.vendor != 'postgresql':
        return
    ids = sorted({
        int.from_bytes(hashlib.sha256(f'supplier-offer|{company_id}|{s}|{p}'.encode()).digest()[:8], 'big',
                       signed=True)
        for s, p in pairs if s and p
    })
    with connection.cursor() as cursor:
        for lock_id in ids:
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', [lock_id])


def _as_date(value):
    return supply_services.clean_date(value, 'valid_from', 'la fecha') if value else None


def match_offer(company, supplier_id, product_id, *, start=None, end=None, on=None):
    """
    `(offer, running)` for a row of (supplier, variant) over `start..end`:
    `offer` is THE offer the row updates (None = the row creates one);
    `running` is set instead when the row is refused because it would move,
    in the future, the offer that sells today.

      1. the offer with exactly that window, in any state (re-importing a file
         finds what it made, a future offer included);
      2. an ACTIVE offer running TODAY (`valid_from <= today <= valid_until`;
         the one that ends last, then the latest start, then the highest id):
         - a row starting today or before that overlaps or touches it (a day
           apart) updates it in place;
         - a row starting in the future that touches it or comes after it
           updates the SCHEDULED offer it overlaps or touches (next week's,
           already loaded), or else is next week's offer: created; today's
           keeps selling;
         - a row starting in the future that OVERLAPS it is refused
           (`overlaps_running`): neither a silent stop nor an in-place move;
      3. else the first offer not over yet (scheduled, or switched off) —
         active first, then the one that ends last — that the row overlaps or
         touches, updated in place; None when there are such offers but none
         is reached;
      4. else (every offer already ended) the one that ended last.
    """
    on = on or supply_services.today()
    start, end = _as_date(start), _as_date(end)
    offers = SupplierOffer.objects.filter(company=company, supplier_id=supplier_id, product_id=product_id)
    if start and end:
        exact = offers.filter(valid_from=start, valid_until=end).order_by('-is_active', '-pk').first()
        if exact is not None:
            return exact, None
    day = timedelta(days=1)

    def reaches(offer):
        return start <= offer.valid_until + day and end >= offer.valid_from - day

    if not (start and end):
        return offers.filter(valid_until__gte=on).order_by('-is_active', '-valid_until', '-pk').first(), None
    running = list(offers.filter(is_active=True, valid_from__lte=on, valid_until__gte=on)
                   .order_by('-valid_until', '-valid_from', '-pk'))
    if running:
        for offer in running:
            if start <= on:
                if reaches(offer):
                    return offer, None
            elif start <= offer.valid_until:
                return None, offer
        if start > on:
            # Next week's offer may already exist (last week's file): a future row that reaches a
            # SCHEDULED offer updates that one, never the one selling today.
            scheduled = (offers.filter(valid_from__gt=on).order_by('-is_active', 'valid_from', 'pk'))
            return next((o for o in scheduled if reaches(o)), None), None
        return None, None
    pending = list(offers.filter(valid_until__gte=on).order_by('-is_active', '-valid_until', '-valid_from', '-pk'))
    if pending:
        return next((o for o in pending if reaches(o)), None), None
    return offers.order_by('-valid_until', '-valid_from', '-pk').first(), None


def current_offer(company, supplier_id, product_id, *, start=None, end=None, on=None):
    """THE offer the row updates, or None — see `match_offer`."""
    return match_offer(company, supplier_id, product_id, start=start, end=end, on=on)[0]


def _overlaps_running_text(running) -> str:
    return (f'Esta vigencia empieza en el futuro y se cruza con la oferta que se está vendiendo hoy (del '
            f'{running.valid_from:%d/%m/%Y} al {running.valid_until:%d/%m/%Y}). Para la semana siguiente, '
            f'empieza el {running.valid_until + timedelta(days=1):%d/%m/%Y} o después; para cambiar la oferta de '
            'hoy, haz que la fila empiece hoy o antes.')


def expired_only(offer) -> bool:
    """
    Whether `offer` is off ONLY because the expiry job switched it off: the last
    audited switch-off is `supplier_offer_expired`. A reason anybody can type
    (even «Vigencia vencida») never counts.
    """
    if offer.is_active:
        return False
    last = (AdminAuditLog.objects.filter(company_id=offer.company_id, target_type='supplier_offer',
                                         target_id=str(offer.pk),
                                         action__in=('supplier_offer_expired', 'supplier_offer_deactivated',
                                                     'supplier_offer_updated', 'supplier_offer_created'))
            .order_by('-created_at', '-pk').values_list('action', flat=True).first())
    return last == 'supplier_offer_expired'


def _overlapping(company, supplier_id, product_id, start, end, *, exclude=None) -> dict:
    offers = SupplierOffer.objects.filter(company=company, supplier_id=supplier_id, product_id=product_id,
                                          is_active=True, valid_from__lte=end, valid_until__gte=start)
    if exclude is not None:
        offers = offers.exclude(pk=exclude)
    return {o.pk: o for o in offers.order_by('pk')}


def overlap_of(company, supplier_id, product_id, *, start, end, active, current=None):
    """
    `(added, kept)`: the other ACTIVE offers of this supplier and variant over
    `start..end` that this write would ADD as overlaps, and those that already
    overlapped `current` (rows from before the rule: warned, not refused).
    """
    if not active or not start or not end:
        return {}, {}
    now = _overlapping(company, supplier_id, product_id, start, end, exclude=getattr(current, 'pk', None))
    before = {}
    if current is not None and current.is_active:
        before = _overlapping(company, supplier_id, product_id, current.valid_from, current.valid_until,
                              exclude=current.pk)
    added = {pk: o for pk, o in now.items() if pk not in before}
    kept = {pk: o for pk, o in now.items() if pk in before}
    return added, kept


def _overlap_text(offers) -> str:
    first = sorted(offers.values(), key=lambda o: (o.valid_from, o.pk))[0]
    return (f'Se superpone con otra oferta activa de ese proveedor para ese producto (del '
            f'{first.valid_from:%d/%m/%Y} al {first.valid_until:%d/%m/%Y}). Cambia la vigencia o desactiva esa '
            'oferta.')


def guard_offer_overlap(company, fields: dict, *, current=None) -> None:
    """
    The panel's create (`current` None) and edit: refuse a write that leaves two
    active offers of one supplier and variant over the same days. Takes the
    pair's advisory lock — call it inside the transaction that then writes.
    Values the cleaners would refuse are left to them (this says nothing).
    """
    supplier_id = supply_services._pk(fields.get('supplier_id')) if current is None else current.supplier_id
    product_id = supply_services._pk(fields.get('product_id')) if current is None else current.product_id
    if not supplier_id or not product_id:
        return
    try:
        start = (supply_services.clean_date(fields['valid_from'], 'valid_from', 'el inicio')
                 if 'valid_from' in fields or current is None else current.valid_from)
        end = (supply_services.clean_date(fields['valid_until'], 'valid_until', 'el fin')
               if 'valid_until' in fields or current is None else current.valid_until)
    except supply_services.SupplyError:
        return
    if 'is_active' in fields:
        active = fields['is_active'] not in (False, 'false', '0', 0)
    else:
        active = True if current is None else current.is_active
    lock_pairs(company.pk, [(supplier_id, product_id)])
    if current is not None:
        # Read again under the lock: what was read before it may have changed since.
        current = SupplierOffer.objects.get(pk=current.pk)
        start = start if 'valid_from' in fields else current.valid_from
        end = end if 'valid_until' in fields else current.valid_until
        if 'is_active' not in fields:
            active = current.is_active
    added, _kept = overlap_of(company, supplier_id, product_id, start=start, end=end, active=active, current=current)
    if added:
        raise supply_services.SupplyError(_overlap_text(added), field='valid_from', code='overlap')


# ---------------------------------------------------------------------------
# The supplier's own codes
# ---------------------------------------------------------------------------

def clean_ref_code(value) -> str:
    return ' '.join(str(value or '').split())


def ref_payload(ref) -> dict:
    return {'id': ref.pk, 'supplier_id': ref.supplier_id, 'supplier_code': ref.supplier_code,
            'product_id': ref.product_id, 'product_name': ref.product.name}


def save_product_ref(*, company, supplier, supplier_code, product, actor=None, request=None, replace=False,
                     source='panel', job_id=None):
    """
    Map `supplier_code` of `supplier` to `product` (both of `company`).
    Returns `(ref, created_or_changed)`. The same mapping again changes nothing;
    the code mapped to another variant is `ref_conflict` unless `replace`.
    """
    code = clean_ref_code(supplier_code)
    if not code:
        raise supply_services.SupplyError('Indica el código del proveedor.', field='supplier_code')
    if len(code) > MAX_REF_CODE:
        raise supply_services.SupplyError('El código del proveedor es demasiado largo.', field='supplier_code')
    if supplier.company_id != company.pk or product.company_id != company.pk:
        raise supply_services.SupplyError('No encontrado.', code='not_found')
    if product.condition_line == Product.CONDITION_USED:
        raise supply_services.SupplyError('Un seminuevo se vende por equipo, nunca bajo pedido.', field='product_id')
    meta = {'supplier_id': supplier.pk, 'supplier_code': code, 'product_id': product.pk, 'source': source}
    if job_id is not None:
        meta['job_id'] = job_id
    with transaction.atomic():
        ref = (SupplierProductRef.objects.select_for_update().select_related('product')
               .filter(company=company, supplier=supplier, supplier_code=code).first())
        if ref is not None and ref.product_id == product.pk:
            return ref, False
        if ref is not None and not replace:
            raise supply_services.SupplyError(
                f'El código «{code[:40]}» de este proveedor ya está asociado a «{ref.product.name[:60]}».',
                field='supplier_code', code='ref_conflict')
        if ref is not None:
            previous = ref.product_id
            SupplierProductRef.objects.filter(pk=ref.pk).update(product=product, updated_at=timezone.now())
            ref.refresh_from_db()
            action, meta['previous_product_id'] = 'supplier_product_ref_updated', previous
        else:
            try:
                with transaction.atomic():
                    ref = SupplierProductRef.objects.create(company=company, supplier=supplier, supplier_code=code,
                                                            product=product, created_by=actor)
            except IntegrityError:
                raise supply_services.SupplyError(
                    f'El código «{code[:40]}» de este proveedor acaba de asociarse a otra variante.',
                    field='supplier_code', code='ref_conflict') from None
            action = 'supplier_product_ref_created'
        AdminAuditLog.log(actor=actor, action=action, target_type='supplier_product_ref', target_id=ref.pk,
                          metadata=meta, request=request, company=company)
    return ref, True


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
    if not {'code', 'name', 'model', 'supplier_code', 'identifier'} & set(columns):
        raise OfferImportError('No se encontró la columna «Código/SKU del producto» ni «Modelo».')
    return columns


def _pk(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _pick_sheet(workbook) -> str:
    names = list(workbook.sheetnames)
    for name in names:
        if normalize_header(name) == normalize_header(SHEET):
            return name
    return names[0]


_MAP_HINT = (' Si es un código del proveedor, elige la variante en la vista previa: se recordará para el '
             'próximo archivo.')
_KEY_LABELS = {'identifier': 'el identificador', 'barcode': 'el código del producto',
               'supplier_code': 'el código del proveedor'}


def _by_model(company, fields):
    try:
        spec = device_catalog.resolve_variant_spec(
            company, model_name=fields['model'], line_text=fields.get('line') or '',
            labels=[fields.get('capacity'), fields.get('colour')])
    except device_catalog.CatalogError as exc:
        return None, str(exc)
    product = device_catalog.find_variant(company, spec)
    if product is not None:
        return product, None
    # Fewer options than the variant has: several variants may fit. Never pick one.
    fitting = Product.objects.filter(company=company, product_model_id=spec['model_id'],
                                     condition_line=spec['condition_line'])
    if spec['option_ids']:
        holders = (ProductVariantOption.objects.filter(company_id=company.pk, option_id__in=spec['option_ids'])
                   .values('product_id').annotate(n=Count('option_id', distinct=True))
                   .filter(n=len(spec['option_ids'])).values('product_id'))
        fitting = fitting.filter(pk__in=holders)
    many = fitting.count()
    if many > 1:
        return None, (f'«{spec["model_name"][:60]}» con esas opciones coincide con varias variantes ({many}): '
                      'indica todas las opciones o el código.')
    return None, 'No existe esa variante del modelo. Créala en Catálogo › Modelos.'


def _resolve_product(company, index, fields, *, supplier, refs):
    """
    `(product, problem, matched_by, map_code, mappable_code)` — see WHICH VARIANT.
    `refs` is this supplier's {supplier code: product id}.
    """
    code = fields.get('code') or ''
    identifier = fields.get('identifier') or ''
    supplier_code = clean_ref_code(fields.get('supplier_code'))[:MAX_REF_CODE]
    hits = []
    if identifier:
        if import_formats.parse_product_identifier(identifier) is None:
            return None, f'El identificador «{identifier[:40]}» no es válido.', None, None, None
        product_id = index.identified(identifier)
        if product_id is None:
            return None, f'El identificador «{identifier[:40]}» no es de un producto de esta empresa.', None, None, None
        hits.append(('identifier', product_id))
    code_matched = False
    if code:
        by_identifier = index.identified(code) if import_formats.parse_product_identifier(code) else None
        owner = index.owner_of(code)
        if owner is None:
            by_slug = Product.objects.filter(company=company, slug__iexact=code.strip()).values_list('pk', flat=True)
            owner = by_slug.first()
        if by_identifier:
            hits.append(('identifier', by_identifier))
        if owner:
            hits.append(('barcode', owner))
        code_matched = bool(by_identifier or owner)
    ref_code = supplier_code or (clean_ref_code(code)[:MAX_REF_CODE] if code and not code_matched else '')
    if supplier is not None and ref_code and ref_code in refs:
        hits.append(('supplier_code', refs[ref_code]))

    if hits:
        if len({product_id for _by, product_id in hits}) > 1:
            names = dict(Product.objects.filter(company=company, pk__in={p for _b, p in hits})
                         .values_list('pk', 'name'))
            said = '; '.join(f'{_KEY_LABELS[by]} dice «{names.get(p, "?")[:50]}»' for by, p in hits)
            by_ref = any(by == 'supplier_code' for by, _p in hits)
            ref_text = f' (código del proveedor «{ref_code[:40]}»)' if by_ref else ''
            return None, (f'Los códigos de la fila señalan variantes distintas{ref_text}: {said}. Corrige el '
                          'archivo o la asociación del código.'), None, None, None
        matched_by, product_id = hits[0]
        product = Product.objects.filter(company=company, pk=product_id).first()
        map_code = supplier_code if supplier is not None and supplier_code and supplier_code not in refs else None
        return product, None, matched_by, map_code, None

    mappable = ref_code if supplier is not None else None
    hint = _MAP_HINT if mappable else ''
    if code:
        return None, f'No existe un producto con el código «{code[:40]}».{hint}', None, None, mappable
    if fields.get('model'):
        product, problem = _by_model(company, fields)
        if product is None:
            return None, f'{problem}{hint}', None, None, mappable
        map_code = supplier_code if supplier is not None and supplier_code else None
        return product, None, 'model', map_code, None
    if fields.get('name'):
        product_id, error, _by, _warnings = index.match(name=fields['name'])
        if product_id and not error:
            map_code = supplier_code if supplier is not None and supplier_code else None
            return Product.objects.filter(company=company, pk=product_id).first(), None, 'name', map_code, None
        return None, f'{error or "No existe el producto «" + fields["name"][:60] + "»."}{hint}', None, None, mappable
    if supplier_code:
        return None, f'El código del proveedor «{supplier_code[:40]}» aún no está asociado a una variante.{hint}', \
            None, None, mappable
    return None, 'Indica el código del producto o su modelo.', None, None, None


def _available(offer):
    """What is left to sell: None for an unconfirmed offer."""
    if offer.confirmed_quantity is None:
        return None
    return max(offer.confirmed_quantity - offer.committed_quantity, 0)


def _fingerprint(offer) -> dict:
    """What the preview read of an offer and a person (or another file) may change. Not `committed`."""
    return {
        'commercial_price': str(offer.commercial_price), 'confirmed_quantity': offer.confirmed_quantity,
        'valid_from': offer.valid_from.isoformat(), 'valid_until': offer.valid_until.isoformat(),
        'lead_time_days': offer.lead_time_days, 'is_active': offer.is_active,
        'deactivated_reason': offer.deactivated_reason,
        'agreed_cost': str(offer.agreed_cost) if offer.agreed_cost is not None else None,
    }


def _offer_before(offer, *, with_cost: bool) -> dict:
    before = {
        'commercial_price': str(offer.commercial_price), 'confirmed_quantity': offer.confirmed_quantity,
        'committed_quantity': offer.committed_quantity, 'valid_from': offer.valid_from.isoformat(),
        'valid_until': offer.valid_until.isoformat(), 'lead_time_days': offer.lead_time_days,
        'is_active': offer.is_active, 'available': _available(offer),
    }
    if with_cost:
        before['agreed_cost'] = str(offer.agreed_cost) if offer.agreed_cost is not None else None
    return before


@transaction.atomic
def preview(*, company, actor, upload, filename, complete_supplier=None) -> BulkImportJob:
    """
    Stage a price list. Writes NO offer and NO mapping.

    `complete_supplier` (INV-SNAPSHOT-01): the file is that supplier's COMPLETE
    list. Every row must be of that supplier; its running offers missing from
    the file are listed as absent (to switch off only on confirmation, at
    apply); scheduled ones are never touched (owner D1). A row with no quantity
    for an offer that already sold is staged as «availability not confirmed»
    instead of an error: its sales keep their course and nothing more sells.
    """
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
    today = supply_services.today()

    suppliers = {' '.join(s.name.split()).lower(): s for s in Supplier.objects.filter(company=company)}
    refs = defaultdict(dict)       # supplier id → {code: product id}, plus what this file maps
    for supplier_id, code, product_id in (SupplierProductRef.objects.filter(company=company)
                                          .values_list('supplier_id', 'supplier_code', 'product_id')):
        refs[supplier_id][code] = product_id
    index = CatalogueIndex(company)
    job = BulkImportJob.objects.create(
        company=company, import_type=BulkImportJob.SUPPLIER_OFFERS,
        original_filename=_safe_filename(filename), file_sha256=_sha256(data),
        mapping_snapshot={'sheet_name': sheet_name, 'mapping': columns, 'headers': [str(h) for h in headers]},
        options_snapshot={'with_cost': with_cost,
                          **({'complete_supplier_id': complete_supplier.pk} if complete_supplier else {})},
        created_by=actor,
        summary={'cost_column_ignored': cost_ignored},
    )
    staged, counts, seen = [], dict(create=0, update=0, no_change=0, skip=0, error=0), {}
    extra = dict(sold_out=0, price_changes=0, mappings=0)
    modes = {}      # product id → (row, «Bajo pedido» it asks for)

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
        elif complete_supplier is not None and supplier.pk != complete_supplier.pk:
            errors.append(f'En la lista completa de «{complete_supplier.name[:60]}» todas las filas son de ese '
                          'proveedor.')
        product, problem, matched_by, map_code, mappable = _resolve_product(
            company, index, fields, supplier=supplier, refs=refs[supplier.pk] if supplier else {})
        if problem:
            errors.append(problem)
        elif product is not None and product.condition_line == Product.CONDITION_USED:
            errors.append('Un seminuevo se vende por equipo, nunca bajo pedido.')
            map_code = None

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
        if 'agreed_cost' in clean and clean['agreed_cost'] is None:
            # An empty cost cell is «no change», never «clear the cost».
            del clean['agreed_cost']
        active = _clean_active(fields.get('active'))
        if active == 'invalid':
            errors.append('La columna «Activa» lleva «Sí», «No» o nada.')
            active = None
        on_order = _clean_active(fields.get('on_order'))
        if on_order == 'invalid':
            errors.append('La columna «Bajo pedido» lleva «Sí», «No» o nada.')
            on_order = None
        wanted_mode = (None if on_order is None else
                       Product.SALE_MODE_STOCK_OR_ORDER if on_order else Product.SALE_MODE_STOCK)
        if clean.get('valid_from') and clean.get('valid_until') and clean['valid_until'] < clean['valid_from']:
            errors.append('La vigencia termina antes de empezar.')
        if cost_ignored:
            warnings.append('Columna «Costo pactado» ignorada: requiere permiso para ver costos.')

        shown = {
            'supplier': fields['supplier'],
            'product_name': product.name if product else (fields['code'] or fields['model'] or
                                                           fields['supplier_code'] or fields['identifier']),
            **{k: (str(v) if not isinstance(v, int) or isinstance(v, bool) else v)
               for k, v in clean.items() if v is not None},
        }
        if mappable and supplier is not None:
            shown['mappable'] = {'supplier_id': supplier.pk, 'supplier_code': mappable}
        if errors:
            stage(row_number, BulkImportRow.ERROR, data=shown, errors=errors, warnings=warnings)
            continue
        key = (supplier.pk, product.pk)
        if key in seen:
            stage(row_number, BulkImportRow.ERROR, data=shown, warnings=warnings, errors=[
                f'Repite el proveedor y el producto de la fila {seen[key]}: una fila por oferta.'])
            continue
        seen[key] = row_number
        if map_code:
            refs[supplier.pk][map_code] = product.pk
        if wanted_mode is not None:
            # A product has ONE sale mode: two rows that disagree would let the
            # later one win in silence at apply time.
            earlier = modes.get(product.pk)
            if earlier is not None and earlier[1] != wanted_mode:
                warnings.append(
                    f'La fila {earlier[0]} pide otro «Bajo pedido» para este mismo producto, y un producto tiene '
                    'un solo modo de venta: queda el de la última fila que se aplique. Revisa cuál quieres.')
            modes[product.pk] = (row_number, wanted_mode)
        existing, running = match_offer(company, supplier.pk, product.pk, start=clean['valid_from'],
                                        end=clean['valid_until'], on=today)
        if running is not None:
            stage(row_number, BulkImportRow.ERROR, data={**shown, 'error_code': 'overlaps_running'},
                  warnings=warnings, errors=[_overlaps_running_text(running)])
            continue
        payload = {
            **shown, 'supplier_id': supplier.pk, 'product_id': product.pk, 'matched_by': matched_by,
            'valid_from': clean['valid_from'].isoformat(), 'valid_until': clean['valid_until'].isoformat(),
            'commercial_price': str(clean['commercial_price']), 'confirmed_quantity': clean['confirmed_quantity'],
            'lead_time_days': clean['lead_time_days'],
        }
        if map_code:
            payload['map_supplier_code'] = map_code
        if 'agreed_cost' in clean:
            payload['agreed_cost'] = str(clean['agreed_cost'])
        if active is not None:
            payload['is_active'] = active
        if wanted_mode is not None:
            payload['sale_mode'] = wanted_mode
        if (active is None and existing is not None and not existing.is_active
                and clean['valid_until'] >= today and expired_only(existing)):
            # Off only because its days ran out (the hourly command), and the row gives it new days:
            # back on. An offer switched off BY HAND stays off (below).
            active = True
            payload['is_active'] = True
            warnings.append('La oferta estaba desactivada sólo por vencimiento: con la nueva vigencia se reactiva.')
        will_be_active = (active if active is not None else
                          (existing.is_active if existing is not None else True))
        added, kept = overlap_of(company, supplier.pk, product.pk, start=clean['valid_from'],
                                 end=clean['valid_until'], active=will_be_active, current=existing)
        if added:
            stage(row_number, BulkImportRow.ERROR, data=shown, warnings=warnings, errors=[_overlap_text(added)])
            continue
        if kept:
            warnings.append(f'{_overlap_text(kept)} Ya se superponían antes de este archivo.')
        if existing is None:
            if map_code:
                extra['mappings'] += 1
            stage(row_number, BulkImportRow.CREATE, key=f'{supplier.pk}:{product.pk}', data=payload,
                  warnings=warnings)
            continue
        twin = SupplierOffer.objects.filter(company=company, supplier=supplier, product=product,
                                            valid_from=clean['valid_from'], valid_until=clean['valid_until']
                                            ).exclude(pk=existing.pk).first()
        if twin is not None:
            stage(row_number, BulkImportRow.ERROR, data=shown, warnings=warnings, errors=[
                'Ya hay otra oferta de ese proveedor para ese producto con esa misma vigencia (desactivada o '
                'vencida). Cambia las fechas del archivo.'])
            continue
        payload['offer_id'] = existing.pk
        payload['offer_updated_at'] = existing.updated_at.isoformat()
        payload['offer_state'] = _fingerprint(existing)
        payload['before'] = _offer_before(existing, with_cost=with_cost)
        available = clean['confirmed_quantity']        # NET: what the supplier still has
        payload['available'] = available
        sold_out = available == 0
        if available is None and existing.committed_quantity:
            if complete_supplier is None:
                stage(row_number, BulkImportRow.ERROR, data=shown, warnings=warnings, errors=[
                    f'Ya se vendieron {existing.committed_quantity} contra esta oferta: indica cuántas le quedan al '
                    'proveedor (0 = agotada).'])
                continue
            payload['unconfirmed_keeping_commitments'] = True
            warnings.append(f'Sin cantidad: sus {existing.committed_quantity} venta(s) comprometidas siguen; no se '
                            'vende más hasta que el proveedor confirme cuántas le quedan.')
        if active is None and not existing.is_active:
            warnings.append('Oferta desactivada a mano: no se reactiva. Escribe «Sí» en «Activa» para reactivarla.')
        changes = [name for name, differs in (
            ('commercial_price', existing.commercial_price != clean['commercial_price']),
            ('available', _available(existing) != available),
            ('valid_from', existing.valid_from != clean['valid_from']),
            ('valid_until', existing.valid_until != clean['valid_until']),
            ('lead_time_days', existing.lead_time_days != clean['lead_time_days']),
            ('is_active', active is not None and existing.is_active != active),
            ('agreed_cost', 'agreed_cost' in clean and existing.agreed_cost != clean['agreed_cost']),
            ('sale_mode', wanted_mode is not None and product.sale_mode != wanted_mode),
        ) if differs]
        payload['changes'] = changes
        if map_code:
            extra['mappings'] += 1
        if not changes:
            stage(row_number, BulkImportRow.NO_CHANGE, key=f'{supplier.pk}:{product.pk}', data=payload,
                  warnings=warnings)
            continue
        if sold_out:
            payload['sold_out'] = True
            extra['sold_out'] += 1
            warnings.append(f'Agotada: no se vende más. Lo ya vendido ({existing.committed_quantity}) se mantiene.')
        if 'commercial_price' in changes:
            payload['price_change'] = {'before': str(existing.commercial_price),
                                       'after': str(clean['commercial_price'])}
            extra['price_changes'] += 1
        stage(row_number, BulkImportRow.UPDATE, key=f'{supplier.pk}:{product.pk}', data=payload, warnings=warnings)

    if complete_supplier is not None and not any(r.action != BulkImportRow.SKIP for r in staged):
        # A complete list with nothing in it would switch everything off: never.
        job.delete()
        raise OfferImportError('La lista completa está vacía: no se desactiva nada con un archivo sin filas.')
    BulkImportRow.objects.bulk_create(staged)
    if complete_supplier is not None:
        job.summary = {**job.summary, 'snapshot': snapshot_absent(
            company, complete_supplier, {product_id for supplier_id, product_id in seen
                                         if supplier_id == complete_supplier.pk}, on=today)}
    job.rows_total = len(staged)
    job.rows_create, job.rows_update = counts['create'], counts['update']
    job.rows_no_change, job.rows_skip, job.rows_error = counts['no_change'], counts['skip'], counts['error']
    job.summary = {**job.summary, 'counts': {
        'new': counts['create'], 'updated': counts['update'] - extra['sold_out'], 'unchanged': counts['no_change'],
        'sold_out': extra['sold_out'], 'errors': counts['error'], 'skipped': counts['skip'],
        'price_changes': extra['price_changes'], 'mappings': extra['mappings'],
    }}
    job.save(update_fields=['rows_total', 'rows_create', 'rows_update', 'rows_no_change', 'rows_skip',
                            'rows_error', 'summary'])
    return job


def _running_offers(company, supplier, *, on):
    """The supplier's offers running today — the only ones a complete list may switch off (owner D1)."""
    return (SupplierOffer.objects.filter(company=company, supplier=supplier, is_active=True,
                                         valid_from__lte=on, valid_until__gte=on)
            .select_related('product').order_by('pk'))


def snapshot_absent(company, supplier, listed_product_ids, *, on) -> dict:
    """What a complete list would switch off: running offers of `supplier` whose product is not in the file."""
    running = list(_running_offers(company, supplier, on=on))
    absent = [offer for offer in running if offer.product_id not in listed_product_ids]
    return {
        # Review: a list that would switch off most of what runs looks cut short (a missing message, a
        # partial export): refused, never applied. Use the partial mode, or switch offers off by hand.
        'looks_truncated': len(absent) > max(3, len(running) // 2),
        'supplier_id': supplier.pk, 'supplier_name': supplier.name,
        'listed_product_ids': sorted(listed_product_ids),
        'absent': [{'offer_id': offer.pk, 'product_id': offer.product_id, 'product_name': offer.product.name,
                    'committed': offer.committed_quantity, 'available': offer.remaining_known,
                    'state': _fingerprint(offer)} for offer in absent],
        'deactivate_count': len(absent),
        'with_commitments': sum(1 for offer in absent if offer.committed_quantity),
    }


def _clean_active(value):
    """«Sí» → True, «No» → False, empty → None (no change); anything else → 'invalid'."""
    text = normalize_header(value or '')
    if not text:
        return None
    if text in _YES:
        return True
    if text in _NO:
        return False
    return 'invalid'


def job_payload(job, *, include_cost: bool) -> dict:
    rows = []
    for row in job.rows.order_by('row_number', 'pk')[:MAX_ROWS]:
        data = dict(row.normalized_data or {})
        if not include_cost:
            data.pop('agreed_cost', None)
            if isinstance(data.get('before'), dict):
                data['before'] = {k: v for k, v in data['before'].items() if k != 'agreed_cost'}
            if isinstance(data.get('changes'), list):
                # Not even THAT the cost changes.
                data['changes'] = [k for k in data['changes'] if k != 'agreed_cost']
        data.pop('supplier_id', None)
        rows.append({'row_number': row.row_number, 'action': row.action, 'errors': row.errors,
                     'warnings': row.warnings, 'data': data})
    summary = (job.summary or {}).get('counts') or {
        'new': job.rows_create, 'updated': job.rows_update, 'unchanged': job.rows_no_change, 'sold_out': 0,
        'errors': job.rows_error, 'skipped': job.rows_skip, 'price_changes': 0, 'mappings': 0,
    }
    return {
        'id': job.pk, 'status': job.status, 'rows_total': job.rows_total, 'rows_create': job.rows_create,
        'rows_update': job.rows_update, 'rows_no_change': job.rows_no_change, 'rows_error': job.rows_error,
        'cost_column_ignored': bool((job.summary or {}).get('cost_column_ignored')),
        'already_applied': previously_applied(job) if job.status != BulkImportJob.APPLIED else None,
        'summary': summary, 'rows': rows,
        **_snapshot_payload(job),
    }


def _snapshot_payload(job) -> dict:
    """INV-SNAPSHOT-01: the complete list's absent offers, for the person to confirm. Nothing about costs."""
    snapshot = (job.summary or {}).get('snapshot')
    if not snapshot:
        return {}
    return {'snapshot': {
        'supplier_id': snapshot['supplier_id'], 'supplier_name': snapshot['supplier_name'],
        'absent': [{k: v for k, v in row.items() if k != 'state'} for row in snapshot['absent']],
        'deactivate_count': snapshot['deactivate_count'], 'with_commitments': snapshot['with_commitments'],
        'looks_truncated': bool(snapshot.get('looks_truncated')),
    }}


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def previously_applied(job):
    """`{job_id, applied_at}` of the latest APPLIED job of the company with the very same file, or None."""
    earlier = (BulkImportJob.objects.filter(company_id=job.company_id, import_type=BulkImportJob.SUPPLIER_OFFERS,
                                            file_sha256=job.file_sha256, status=BulkImportJob.APPLIED)
               .exclude(pk=job.pk).order_by('-applied_at', '-pk').first())
    if earlier is None:
        return None
    return {'job_id': earlier.pk, 'applied_at': earlier.applied_at.isoformat() if earlier.applied_at else None}


def _price_rows(rows) -> list[dict]:
    return [{
        'row_number': row.row_number, 'offer_id': row.normalized_data.get('offer_id'),
        'supplier': row.normalized_data.get('supplier'), 'product_name': row.normalized_data.get('product_name'),
        **row.normalized_data['price_change'],
    } for row in rows if row.action == BulkImportRow.UPDATE and (row.normalized_data or {}).get('price_change')]


@transaction.atomic
def apply(*, job, actor, request=None, accept_price_changes: bool = False, keep_prices: bool = False,
          confirm_reapply: bool = False, all_messages_included: bool = False,
          confirm_deactivations=None) -> dict:
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
    if accept_price_changes and keep_prices:
        raise OfferImportError('Elige aceptar los precios nuevos o mantener los actuales, no las dos cosas.')
    from .cost_services import user_may_view_costs

    # The cost is written only if the preview read it AND whoever applies may see costs.
    with_cost = bool((job.options_snapshot or {}).get('with_cost')) and user_may_view_costs(actor, job.company)
    rows = list(job.rows.order_by('row_number', 'pk'))
    priced = _price_rows(rows)
    if priced and not (accept_price_changes or keep_prices):
        raise OfferImportError(
            f'El archivo cambia el precio comercial de {len(priced)} oferta(s). Acepta los precios nuevos o '
            'mantén los actuales.', code='price_confirmation_required', extra={'price_changes': priced})
    decision = ('accepted' if accept_price_changes else 'kept') if priced else None
    snapshot = (job.summary or {}).get('snapshot')
    if snapshot:
        if snapshot.get('looks_truncated'):
            raise OfferImportError(
                f'La lista desactivaría {snapshot["deactivate_count"]} de las ofertas vigentes del proveedor: parece '
                'incompleta (falta un mensaje o una parte del archivo). Revisa que esté completa, o cárgala como '
                'parcial.', code='snapshot_looks_truncated')
        # INV-SNAPSHOT-01: a complete list switches offers off only with BOTH marks, the count exact.
        if not all_messages_included:
            raise OfferImportError(
                'Confirma que la lista incluye todos los mensajes y archivos del proveedor antes de aplicarla '
                'como completa.', code='snapshot_confirmation_required')
        if confirm_deactivations != snapshot['deactivate_count'] or isinstance(confirm_deactivations, bool):
            raise OfferImportError(
                f'La lista completa desactiva {snapshot["deactivate_count"]} oferta(s) ausente(s) '
                f'({snapshot["with_commitments"]} con ventas comprometidas, que siguen su curso). Confirma ese '
                'número para aplicarla.', code='deactivation_confirmation_required',
                extra={'deactivations': snapshot['deactivate_count'],
                       'with_commitments': snapshot['with_commitments']})
    writing = [r for r in rows if r.action in (BulkImportRow.CREATE, BulkImportRow.UPDATE, BulkImportRow.NO_CHANGE)]
    pairs = [(r.normalized_data.get('supplier_id'), r.normalized_data.get('product_id')) for r in writing]
    if snapshot:
        # Review: the absent pairs too, so no import or offer for them commits between the check and the switch-off.
        pairs += [(snapshot['supplier_id'], row['product_id']) for row in snapshot['absent']]
    lock_pairs(job.company_id, pairs)
    # Every offer the job read, locked in ONE statement in id order — the order a
    # payment of several lines takes them in — before any is written.
    wanted = {_pk(r.normalized_data.get('offer_id')) for r in writing} - {None}
    today = supply_services.today()
    supplier = None
    if snapshot:
        supplier = Supplier.objects.get(company=job.company, pk=snapshot['supplier_id'])
        # The supplier's running offers, locked with the rest: none may start or stop under us.
        wanted |= set(_running_offers(job.company, supplier, on=today).values_list('pk', flat=True))
        wanted |= {row['offer_id'] for row in snapshot['absent']}
    offers = {o.pk: o for o in SupplierOffer.objects.select_for_update().filter(company=job.company,
                                                                                pk__in=sorted(wanted))
              .order_by('pk')}
    if snapshot:
        now = snapshot_absent(job.company, supplier, set(snapshot['listed_product_ids']), on=today)
        if [(row['offer_id'], row['state']) for row in now['absent']] != \
                [(row['offer_id'], row['state']) for row in snapshot['absent']]:
            raise OfferImportError(STALE, code='stale')
    for row in writing:
        data = row.normalized_data or {}
        if row.action == BulkImportRow.CREATE:
            start, end = data.get('valid_from'), data.get('valid_until')
            taken, running = match_offer(job.company, data.get('supplier_id'), data.get('product_id'), start=start,
                                         end=end, on=today)
            added, _kept = overlap_of(job.company, data.get('supplier_id'), data.get('product_id'),
                                      start=start, end=end, active=data.get('is_active', True) is not False)
            if taken is not None or running is not None or added:
                raise OfferImportError(STALE, code='stale')
            continue
        offer = offers.get(_pk(data.get('offer_id')))
        if offer is None:
            raise OfferImportError(f'La oferta de la fila {row.row_number} ya no existe. Vuelve a subir el archivo.',
                                   code='stale')
        if data.get('offer_state') is not None:
            if _fingerprint(offer) != data['offer_state']:
                raise OfferImportError(STALE, code='stale')
        elif data.get('offer_updated_at') and offer.updated_at.isoformat() != data['offer_updated_at']:
            raise OfferImportError(STALE, code='stale')     # a job previewed before NET AVAILABILITY
        # Under the lock, the row's days against every OTHER active offer of the pair: one created by
        # hand (or by another file) since the preview must not end up selling over the same days.
        added, _kept = overlap_of(job.company, offer.supplier_id, offer.product_id,
                                  start=_as_date(data.get('valid_from')), end=_as_date(data.get('valid_until')),
                                  active=data['is_active'] if 'is_active' in data else offer.is_active,
                                  current=offer)
        if added:
            raise OfferImportError(STALE, code='stale')

    earlier = previously_applied(job)
    if earlier is not None:
        if not confirm_reapply:
            raise OfferImportError(
                'Este archivo ya se aplicó: aplicarlo otra vez vuelve a fijar lo disponible de cada oferta (lo '
                'vendido desde entonces no se descuenta). Confírmalo para aplicarlo de nuevo.',
                code='already_applied', extra={'already_applied': earlier})
        AdminAuditLog.log(actor=actor, action='supplier_offers_reapply_confirmed', target_type='bulk_import_job',
                          target_id=job.pk, request=request, company=job.company,
                          metadata={'job_id': job.pk, 'previous_job_id': earlier['job_id'],
                                    'previous_applied_at': earlier['applied_at'], 'file_sha256': job.file_sha256})

    created = updated = unchanged = sold_out = prices_changed = prices_kept = mappings = 0
    for row in writing:
        data = row.normalized_data or {}
        fields = {key: data.get(key) for key in ('commercial_price', 'confirmed_quantity', 'valid_from',
                                                 'valid_until', 'lead_time_days')}
        if with_cost and 'agreed_cost' in data:
            fields['agreed_cost'] = data['agreed_cost']
        if 'is_active' in data:
            fields['is_active'] = data['is_active']
        try:
            if row.action == BulkImportRow.CREATE:
                supply_services.create_offer(company=job.company, actor=actor, request=request,
                                             may_view_costs=with_cost, supplier_id=data.get('supplier_id'),
                                             product_id=data.get('product_id'), **fields)
                created += 1
            elif row.action == BulkImportRow.UPDATE:
                offer = offers[_pk(data.get('offer_id'))]
                price_change = data.get('price_change')
                quantity_before = {'confirmed': offer.confirmed_quantity, 'committed': offer.committed_quantity,
                                   'available': _available(offer)}
                if data.get('unconfirmed_keeping_commitments'):
                    # INV-SNAPSHOT-01: no quantity in a complete list, sales already made: they keep their
                    # course (confirmed = committed under the lock), nothing more sells.
                    fields.pop('confirmed_quantity', None)
                    fields['availability_unconfirmed_keeping_commitments'] = True
                elif 'available' in data:
                    # NET AVAILABILITY: `committed` read under the lock, so what is left is the file's quantity.
                    if data['available'] is None:
                        fields['confirmed_quantity'] = None
                    else:
                        fields['confirmed_quantity'] = offer.committed_quantity + int(data['available'])
                elif data.get('sold_out'):
                    fields['confirmed_quantity'] = offer.committed_quantity
                if price_change and keep_prices:
                    fields.pop('commercial_price')
                supply_services.update_offer(offer, actor=actor, request=request, may_view_costs=with_cost, **fields)
                quantity_after = {'confirmed': offer.confirmed_quantity, 'committed': offer.committed_quantity,
                                  'available': _available(offer)}
                if quantity_after != quantity_before:
                    AdminAuditLog.log(actor=actor, action='supplier_offer_quantity_set', target_type='supplier_offer',
                                      target_id=offer.pk, request=request, company=job.company, metadata={
                                          'job_id': job.pk, 'row_number': row.row_number,
                                          'file_quantity': data.get('available'), 'before': quantity_before,
                                          'after': quantity_after})
                if price_change:
                    _audit_price(job, row, offer, price_change, kept=keep_prices, actor=actor, request=request)
                    prices_kept += keep_prices
                    prices_changed += not keep_prices
                if keep_prices and data.get('changes') == ['commercial_price'] and quantity_after == quantity_before:
                    # Only the price changed, and it was kept. A payment since the preview moves `confirmed`
                    # (NET AVAILABILITY): then it is an update.
                    unchanged += 1
                elif data.get('sold_out'):
                    sold_out += 1
                else:
                    updated += 1
            else:
                unchanged += 1
            if data.get('sale_mode') and row.action in (BulkImportRow.CREATE, BulkImportRow.UPDATE):
                product = Product.objects.filter(company=job.company, pk=data.get('product_id')).first()
                if product is not None:
                    supply_services.set_sale_mode(product, data['sale_mode'], actor=actor, request=request)
            if data.get('map_supplier_code'):
                supplier = Supplier.objects.get(company=job.company, pk=data.get('supplier_id'))
                product = Product.objects.get(company=job.company, pk=data.get('product_id'))
                _ref, changed = save_product_ref(company=job.company, supplier=supplier,
                                                 supplier_code=data['map_supplier_code'], product=product,
                                                 actor=actor, request=request, source='import', job_id=job.pk)
                mappings += changed
        except supply_services.SupplyError as exc:
            if exc.code in ('duplicate', 'ref_conflict'):
                raise OfferImportError(STALE, code='stale') from None
            raise OfferImportError(f'Fila {row.row_number}: {exc.detail}') from None
        except IntegrityError:
            # Another apply inserted the same key between our check and our insert.
            raise OfferImportError(STALE, code='stale') from None
    deactivated = []
    if snapshot:
        for row in snapshot['absent']:
            if supply_services.deactivate_offer(
                    offers[row['offer_id']], actor=actor, request=request,
                    reason=f'Ausente en la lista completa del {today.isoformat()}',
                    action='supplier_offer_absent_in_snapshot'):
                deactivated.append(row['offer_id'])
        AdminAuditLog.log(actor=actor, action='supplier_snapshot_applied', target_type='bulk_import_job',
                          target_id=job.pk, request=request, company=job.company, metadata={
                              'job_id': job.pk, 'supplier_id': supplier.pk, 'deactivated': deactivated,
                              'with_commitments': [row['offer_id'] for row in snapshot['absent'] if row['committed']],
                              'all_messages_included': True})
    result = {'created': created, 'updated': updated, 'unchanged': unchanged, 'sold_out': sold_out,
              'prices_changed': prices_changed, 'prices_kept': prices_kept, 'mappings': mappings,
              'price_decision': decision, **({'deactivated': len(deactivated)} if snapshot else {})}
    job.status = BulkImportJob.APPLIED
    job.applied_by = actor
    job.applied_at = timezone.now()
    job.summary = {**(job.summary or {}), 'applied': result}
    job.save(update_fields=['status', 'applied_by', 'applied_at', 'summary'])
    AdminAuditLog.log(actor=actor, action='supplier_offers_import_applied', target_type='bulk_import_job',
                      target_id=job.pk, metadata={'job_id': job.pk, **result, 'file_sha256': job.file_sha256,
                                                  'filename': job.original_filename},
                      request=request, company=job.company)
    return {'status': 'applied', **result}


def _audit_price(job, row, offer, change, *, kept, actor, request):
    if kept:
        action, meta = 'supplier_offer_price_kept', {'price': change['before'], 'proposed': change['after']}
    else:
        action, meta = 'supplier_offer_price_changed', {'before': change['before'], 'after': change['after']}
    AdminAuditLog.log(actor=actor, action=action, target_type='supplier_offer', target_id=offer.pk,
                      metadata={**meta, 'job_id': job.pk, 'row_number': row.row_number}, request=request,
                      company=job.company)
