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

THE SAME FILE TWICE. A preview whose SHA-256 is that of a file already APPLIED
in the company says so (`already_applied: {job_id, applied_at}`), and apply
refuses it (409 `already_applied`, nothing written) unless `confirm_reapply:
true`; the confirmation is audited (`supplier_offers_reapply_confirmed`).
Checked again at apply, after the stale checks.

B0 — SAFE RE-IMPORTS (docs/adr-catalog-commercial.md §7). A WhatsApp draft is
rebuilt as a new workbook every time (another SHA), so the bytes prove nothing:

  * THE MESSAGE DATE. Every offer import carries the date of the supplier's
    message (`message_date`, set by the operator, never in the future). The
    screens derive the default validity from it, never from «today».
  * THE CONTENT FINGERPRINT (`BulkImportJob.content_fingerprint`): a keyed hash
    of the company, the list mode, the message date and every staged row
    (supplier, variant, price, quantity incl. none, lead time, validity, the
    explicit «Activa»/«Bajo pedido» and — only as a hash component — the agreed
    cost), sorted. Never sent out. A preview with the content of an APPLIED job
    of the company says so (`already_applied_content`), and apply refuses it
    (409 `already_applied_content`) unless `confirm_reapply` WITH a reason, by
    somebody with the whole company in scope; audited. Checked under the job
    and pair locks, before the stale checks.
  * THE DECLARATION of each offer (`last_declared_quantity`,
    `declared_message_date`, `committed_at_declaration`). Per row, against the
    offer it matches:
      - `new`: no declaration registered, or a later message — or the same day
        with other content (a correction). Owner D1 with the safe cut-off: the
        quantity is what the supplier had when the message was sent, so every
        unit committed against this supplier's offers of the variant SINCE THE
        MESSAGE DAY BEGAN (the message has no hour: the whole day counts) is
        subtracted — never sold twice: what is left = max(quantity − sold
        since, 0), `committed` and those sales read under the locks.
      - `repetition`: the registered declaration again (same quantity, price
        and lead time; message not later). Writes NOTHING: what was sold since
        stays sold (left = declared − (committed now − committed at the
        declaration)), the validity is not renewed, `last_confirmed_at` does
        not move — even with `confirm_reapply`.
      - `older`: a message before the registered one with other content.
        Writes nothing.
    A message not later than the registered one never moves `valid_from` /
    `valid_until`.
  * AN EXPIRED OFFER is never switched back on in silence: the row says
    `reactivation_requires_review` and apply refuses (409
    `reactivation_confirmation_required`) unless `confirm_reactivations` is
    exactly their number; audited per offer.
  * A QUANTITY CLEARED on a confirmed offer with nothing sold is a decision
    (`quantity_cleared`): apply refuses (409 `quantity_decision_required`)
    unless `quantity_decisions` gives each such offer `clear` (unconfirmed: it
    stops selling) or `keep` (the confirmed quantity stays; nothing is declared
    and `last_confirmed_at` does not move).
  * THE SAME ROWS UNDER A LATER MESSAGE DATE (B0 review): a list whose rows —
    validity and message date aside — are those of an applied load is refused
    (409 `same_rows_new_date`, with what it would sell again per offer) unless
    `confirm_new_declaration` with a note; audited. `products.manage` suffices.
  * ONE DECLARATION PER SUPPLIER AND VARIANT (B0 review): rows are classified
    against the latest declaration of the PAIR, whatever offer (window) holds
    it; the same message for another window never opens a second offer.
  * `last_confirmed_at` / `last_confirmed_job` advance only with a `new`
    declaration (to the message day's start: 7 days count from the message,
    not from the paste) or an operator's explicit confirmation in the panel —
    audited `supplier_offer_confirmed` with the company and the job.

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
import hmac
import io
import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from . import device_catalog, import_formats, supply_services, xlsx_reader
from .import_exports import _autosize, _header_row, _workbook, header_notes, number_columns, text_columns
from .cost_services import COSTS_IN_FILE
from .import_formats import normalize_header
from .import_services import CatalogueIndex, _safe_filename, _sha256
from .models import (
    AdminAuditLog, BulkImportJob, BulkImportRow, OrderItem, Product, ProductVariantOption, Supplier, SupplierOffer,
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
# B0 — the message date, the sales since it, the content fingerprint
# ---------------------------------------------------------------------------

#: The row's classification against the offer's registered declaration (see B0 in the docstring).
DECLARATION_NEW, DECLARATION_REPETITION, DECLARATION_OLDER = 'new', 'repetition', 'older'
#: B0 review — never staged: the same day's message with other content for another window is a row error.
DECLARATION_CONFLICT = 'conflict'
MAX_NOTE = 300
MAX_REASON = 300
QUANTITY_DECISIONS = ('clear', 'keep')


def clean_message_date(value, *, on=None) -> date:
    """The supplier's message day, as the operator states it: required, a date, never in the future."""
    on = on or supply_services.today()
    if value in (None, ''):
        raise OfferImportError(
            'Indica la fecha del mensaje del proveedor (el día en que envió la lista). Con ella se calcula la '
            'vigencia y se reconoce una lista que ya se cargó.', code='message_date_required')
    try:
        day = supply_services.clean_date(value, 'message_date', 'la fecha del mensaje')
    except supply_services.SupplyError as exc:
        raise OfferImportError(exc.detail, code='message_date_invalid') from None
    if isinstance(day, datetime):
        day = day.date()
    if day > on:
        raise OfferImportError(
            f'La fecha del mensaje ({day:%d/%m/%Y}) es posterior a hoy: indica el día en que el proveedor envió '
            'la lista.', code='message_date_future')
    return day


def message_cutoff(day: date) -> datetime:
    """
    The start of the message day: midnight in `settings.TIME_ZONE` (America/Lima for the installation) — the same
    calendar as `supply_services.today()`, which also decides each offer's validity days, so the cut-off and the
    validity never disagree on what «that day» is. A message has no hour here: the whole day counts.
    """
    return timezone.make_aware(datetime.combine(day, time.min))


def sold_since(company_id, day: date) -> dict:
    """
    `{(supplier id, product id): {offer id: units}}` — what was COMMITTED against
    each supplier's offers since the message day began: lines still owed to the
    supplier or received from it, whose order was paid that day or later (or has
    no payment stamp), or that a person confirmed against an offer that day or
    later (`order_supply_confirmed`). A failed or released line gave its units
    back and does not count. Read under the locks at apply.
    """
    cutoff = message_cutoff(day)
    confirmed = set()
    for metadata in (AdminAuditLog.objects.filter(company_id=company_id, action='order_supply_confirmed',
                                                  created_at__gte=cutoff).values_list('metadata', flat=True)):
        line_id = (metadata or {}).get('line_id')
        if isinstance(line_id, int):
            confirmed.add(line_id)
    lines = (OrderItem.objects.filter(order__company_id=company_id, supply_origin=OrderItem.SUPPLY_SUPPLIER,
                                      supply_status__in=(OrderItem.SUPPLY_PENDING, OrderItem.SUPPLY_RECEIVED),
                                      supplier_offer__isnull=False)
             .filter(Q(order__paid_at__gte=cutoff) | Q(order__paid_at__isnull=True) | Q(pk__in=confirmed))
             .values('supplier_offer__supplier_id', 'supplier_offer__product_id', 'supplier_offer_id')
             .annotate(units=Sum('quantity')))
    sold = defaultdict(dict)
    for line in lines:
        pair = (line['supplier_offer__supplier_id'], line['supplier_offer__product_id'])
        sold[pair][line['supplier_offer_id']] = int(line['units'] or 0)
    return sold


def _sold(sold, supplier_id, product_id, offer_id=None) -> tuple[int, int]:
    """(units of the pair, units of that offer) sold since the message day began."""
    by_offer = sold.get((_pk(supplier_id), _pk(product_id)), {})
    return sum(by_offer.values()), by_offer.get(offer_id, 0) if offer_id else 0


def _keyed(payload) -> str:
    """A keyed hash (HMAC-SHA256 with the installation's secret): it may carry a cost and is never sent out."""
    text = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str)
    return hmac.new(settings.SECRET_KEY.encode(), text.encode(), hashlib.sha256).hexdigest()


def _content_row(data: dict) -> list:
    listed = data.get('listed') or data
    return [_pk(data.get('supplier_id')), _pk(data.get('product_id')), data.get('commercial_price'),
            data.get('agreed_cost'), data.get('declared_quantity', data.get('confirmed_quantity')),
            data.get('lead_time_days'), listed.get('valid_from'), listed.get('valid_until'), listed.get('is_active'),
            listed.get('sale_mode')]


def content_fingerprints(*, company_id, message_date, complete_supplier_id, rows) -> tuple[str, str]:
    """
    `(content, rows)`: the job's content fingerprint (everything the list says,
    message date and validity included) and the fingerprint of its rows alone
    (without the message date or the validity) — the second only WARNS that the
    same rows were applied before under another message date.
    """
    content_rows = sorted((_content_row(row) for row in rows), key=lambda r: json.dumps(r, default=str))
    content = _keyed({'v': 1, 'company': company_id, 'message_date': message_date.isoformat(),
                      'complete_supplier': complete_supplier_id, 'rows': content_rows})
    bare = sorted((row[:6] + row[8:] for row in content_rows), key=lambda r: json.dumps(r, default=str))
    return content, _keyed({'v': 1, 'company': company_id, 'complete_supplier': complete_supplier_id, 'rows': bare})


def _user_label(user) -> str | None:
    return user.get_username() if user is not None else None


def _job_reference(job) -> dict:
    return {'job_id': job.pk, 'applied_at': job.applied_at.isoformat() if job.applied_at else None,
            'applied_by': _user_label(job.applied_by),
            'message_date': job.message_date.isoformat() if job.message_date else None}


def previously_applied_content(job):
    """The latest APPLIED job of the company with this very content, or None. Never another company's."""
    if not job.content_fingerprint:
        return None
    earlier = (BulkImportJob.objects.filter(company_id=job.company_id, import_type=BulkImportJob.SUPPLIER_OFFERS,
                                            content_fingerprint=job.content_fingerprint,
                                            status=BulkImportJob.APPLIED)
               .exclude(pk=job.pk).select_related('applied_by').order_by('-applied_at', '-pk').first())
    return _job_reference(earlier) if earlier is not None else None


def previously_applied_rows(job):
    """
    The latest APPLIED job with the same rows (validity aside) under an EARLIER message date, or None — B0 review:
    applying them again under this later date needs `confirm_new_declaration`. The same date is the pair's
    repetition (it writes nothing), not this.
    """
    rows_fp = (job.summary or {}).get('rows_fingerprint')
    if not rows_fp or job.message_date is None:
        return None
    earlier = (BulkImportJob.objects.filter(company_id=job.company_id, import_type=BulkImportJob.SUPPLIER_OFFERS,
                                            status=BulkImportJob.APPLIED, summary__rows_fingerprint=rows_fp,
                                            message_date__lt=job.message_date)
               .exclude(pk=job.pk).exclude(content_fingerprint=job.content_fingerprint)
               .select_related('applied_by').order_by('-applied_at', '-pk').first())
    return _job_reference(earlier) if earlier is not None else None


def _when(reference) -> str:
    applied = reference.get('applied_at')
    if not applied:
        return ''
    moment = timezone.localtime(datetime.fromisoformat(applied))
    return f' el {moment:%d/%m/%Y a las %H:%M}'


def already_applied_content_text(reference) -> str:
    who = f' por {reference["applied_by"]}' if reference.get('applied_by') else ''
    message = (f' (mensaje del {date.fromisoformat(reference["message_date"]):%d/%m/%Y})'
               if reference.get('message_date') else '')
    return (f'Esta lista ya se aplicó: es idéntica a la carga n.º {reference["job_id"]}{message}, aplicada'
            f'{_when(reference)}{who}. Aplicarla otra vez no repone lo vendido ni renueva la vigencia. Si de '
            'verdad quieres registrarla otra vez, confírmalo e indica el motivo.')


def same_rows_text(reference, restores) -> str:
    """B0 review — the same rows as an applied load under a later message date: what that would sell again."""
    message = (f' (mensaje del {date.fromisoformat(reference["message_date"]):%d/%m/%Y})'
               if reference.get('message_date') else '')
    who = f' por {reference["applied_by"]}' if reference.get('applied_by') else ''
    units = sum(row['units'] for row in restores)
    if units:
        names = '; '.join(f'«{(row["product_name"] or "")[:40]}»: {row["units"]}' for row in restores[:5])
        effect = (f' Con la fecha de este mensaje volverían a venderse {units} unidad(es) ya vendidas desde esa carga '
                  f'({names}).')
    else:
        effect = ' No vuelve a venderse nada de lo ya vendido.'
    return (f'Estas filas son las mismas de la carga n.º {reference["job_id"]}{message}, aplicada{_when(reference)}'
            f'{who}.{effect} Aplícala sólo si el proveedor volvió a enviar esta misma lista en la fecha indicada: '
            'confírmalo y escribe una nota.')


def _clean_reason(value) -> str:
    return ' '.join(str(value or '').split())[:MAX_REASON]


def pair_declaration(company_id, supplier_id, product_id):
    """
    B0 review — THE supplier's latest declaration for this variant, whatever offer (window) holds it: a message
    is one declaration per supplier and variant, never one per window.
    """
    return (SupplierOffer.objects.filter(company_id=company_id, supplier_id=supplier_id, product_id=product_id,
                                         declared_message_date__isnull=False)
            .order_by('-declared_message_date', '-pk').first())


def _pair_signature(offer):
    if offer is None:
        return None
    return [offer.pk, offer.declared_message_date.isoformat(), offer.last_declared_quantity]


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
        # B0: another list registered since the preview (even one that changed nothing else) makes it stale.
        'declared_message_date': offer.declared_message_date.isoformat() if offer.declared_message_date else None,
        'last_declared_quantity': offer.last_declared_quantity,
    }


def _offer_before(offer, *, with_cost: bool) -> dict:
    before = {
        'commercial_price': str(offer.commercial_price), 'confirmed_quantity': offer.confirmed_quantity,
        'committed_quantity': offer.committed_quantity, 'valid_from': offer.valid_from.isoformat(),
        'valid_until': offer.valid_until.isoformat(), 'lead_time_days': offer.lead_time_days,
        'is_active': offer.is_active, 'available': _available(offer),
        'declared_message_date': offer.declared_message_date.isoformat() if offer.declared_message_date else None,
        'last_declared_quantity': offer.last_declared_quantity,
        'last_confirmed_at': offer.last_confirmed_at.isoformat() if offer.last_confirmed_at else None,
    }
    if with_cost:
        before['agreed_cost'] = str(offer.agreed_cost) if offer.agreed_cost is not None else None
    return before


@transaction.atomic
def preview(*, company, actor, upload, filename, message_date, complete_supplier=None) -> BulkImportJob:
    """
    Stage a price list. Writes NO offer and NO mapping.

    `message_date` (B0): the day of the supplier's message the list comes from
    (`clean_message_date`). Each row is classified against its offer's
    registered declaration (new / repetition / older); see B0 above.

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
    message_date = clean_message_date(message_date, on=today)
    sold = sold_since(company.pk, message_date)

    suppliers = {' '.join(s.name.split()).lower(): s for s in Supplier.objects.filter(company=company)}
    refs = defaultdict(dict)       # supplier id → {code: product id}, plus what this file maps
    for supplier_id, code, product_id in (SupplierProductRef.objects.filter(company=company)
                                          .values_list('supplier_id', 'supplier_code', 'product_id')):
        refs[supplier_id][code] = product_id
    index = CatalogueIndex(company)
    job = BulkImportJob.objects.create(
        company=company, import_type=BulkImportJob.SUPPLIER_OFFERS,
        original_filename=_safe_filename(filename), file_sha256=_sha256(data), message_date=message_date,
        mapping_snapshot={'sheet_name': sheet_name, 'mapping': columns, 'headers': [str(h) for h in headers]},
        options_snapshot={'with_cost': with_cost,
                          **({'complete_supplier_id': complete_supplier.pk} if complete_supplier else {})},
        created_by=actor,
        summary={'cost_column_ignored': cost_ignored},
    )
    staged, counts, seen = [], dict(create=0, update=0, no_change=0, skip=0, error=0), {}
    extra = dict(sold_out=0, price_changes=0, mappings=0, repeated=0, older=0, reactivations=0, quantity_cleared=0)
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
        if cost_ignored and fields.get('agreed_cost'):
            # COST-WRITE-AUTHORITY (owner's decision C): a filled «Costo pactado» from who cannot state costs
            # is an ERROR of the row (it used to be an «ignored» warning) — one sentence whatever the cell says,
            # the value is not read — and a file with errors applies nothing. An empty cell is no cost.
            errors.append(COSTS_IN_FILE)

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
        quantity = clean['confirmed_quantity']       # what the supplier DECLARED (None = unknown)
        payload = {
            **shown, 'supplier_id': supplier.pk, 'product_id': product.pk, 'matched_by': matched_by,
            'valid_from': clean['valid_from'].isoformat(), 'valid_until': clean['valid_until'].isoformat(),
            'commercial_price': str(clean['commercial_price']), 'confirmed_quantity': quantity,
            'declared_quantity': quantity, 'lead_time_days': clean['lead_time_days'],
        }
        if map_code:
            payload['map_supplier_code'] = map_code
        if 'agreed_cost' in clean:
            payload['agreed_cost'] = str(clean['agreed_cost'])
        if active is not None:
            payload['is_active'] = active
        if wanted_mode is not None:
            payload['sale_mode'] = wanted_mode
        # B0: what the LIST says, for the content fingerprint — whatever this row ends up writing.
        payload['listed'] = {key: payload.get(key) for key in ('valid_from', 'valid_until', 'is_active', 'sale_mode')}
        # B0 review: against the PAIR's latest declaration (any window), so one message never opens a second offer.
        reference = pair_declaration(company.pk, supplier.pk, product.pk)
        payload['pair_declaration'] = _pair_signature(reference)
        declaration = _classify(reference, existing, message_date, clean)
        if declaration == DECLARATION_CONFLICT:
            stage(row_number, BulkImportRow.ERROR, data=shown, warnings=warnings, errors=[
                f'El mensaje del {message_date:%d/%m/%Y} ya está registrado para este proveedor y variante en la '
                f'oferta del {reference.valid_from:%d/%m/%Y} al {reference.valid_until:%d/%m/%Y}, con otro '
                'contenido: una misma declaración no abre otra oferta. Si es una corrección, usa la vigencia de esa '
                'oferta; si no, quita la fila.'])
            continue
        payload['declaration'] = declaration
        if reference is not None:
            payload['registered_message_date'] = reference.declared_message_date.isoformat()
        if declaration != DECLARATION_NEW:
            # B0: the registered declaration again, or an older message. NOTHING is written: what was sold
            # since stays sold, the validity is not renewed, the confirmation does not move.
            switches = _stage_unchanged_declaration(
                payload, reference, declaration, message_date, warnings, with_cost=with_cost, existing=existing,
                product=product, active=active, wanted_mode=wanted_mode)
            extra['repeated' if declaration == DECLARATION_REPETITION else 'older'] += 1
            if map_code:
                extra['mappings'] += 1
            # A repetition still applies the person's switches («Activa», «Bajo pedido»), as before B0: an update
            # of those alone. The declaration (quantity, validity, confirmation) stays frozen.
            stage(row_number, BulkImportRow.UPDATE if switches else BulkImportRow.NO_CHANGE,
                  key=f'{supplier.pk}:{product.pk}', data=payload, warnings=warnings)
            continue
        start, end = clean['valid_from'], clean['valid_until']
        if existing is not None and existing.declared_message_date and message_date <= existing.declared_message_date:
            # B0: a correction of the same day's message never moves the validity.
            if (start, end) != (existing.valid_from, existing.valid_until):
                warnings.append(
                    f'La vigencia no cambia (sigue del {existing.valid_from:%d/%m/%Y} al '
                    f'{existing.valid_until:%d/%m/%Y}): el mensaje no es posterior al ya registrado del '
                    f'{existing.declared_message_date:%d/%m/%Y}. Para cambiarla, edita la oferta.')
            start, end = existing.valid_from, existing.valid_until
            payload['valid_from'], payload['valid_until'] = start.isoformat(), end.isoformat()
        if (active is None and existing is not None and not existing.is_active
                and end >= today and expired_only(existing)):
            # Off only because its days ran out (the hourly command), and the row gives it new days. B0: never
            # back on in silence — apply asks for an explicit confirmation of exactly these rows.
            active = True
            payload['is_active'] = True
            payload['reactivation_requires_review'] = True
            extra['reactivations'] += 1
            warnings.append(
                f'La oferta venció el {existing.valid_until:%d/%m/%Y} y está desactivada: esta fila la reactivaría '
                f'hasta el {end:%d/%m/%Y}. Al aplicar tendrás que confirmarlo; si no, quita la fila.')
        will_be_active = (active if active is not None else
                          (existing.is_active if existing is not None else True))
        added, kept = overlap_of(company, supplier.pk, product.pk, start=start,
                                 end=end, active=will_be_active, current=existing)
        if added:
            stage(row_number, BulkImportRow.ERROR, data=shown, warnings=warnings, errors=[_overlap_text(added)])
            continue
        if kept:
            warnings.append(f'{_overlap_text(kept)} Ya se superponían antes de este archivo.')
        sold_pair, _sold_offer = _sold(sold, supplier.pk, product.pk)
        available = None if quantity is None else max(quantity - sold_pair, 0)
        if sold_pair:
            payload['sold_since_message'] = sold_pair
            if quantity:
                warnings.append(
                    f'Desde el {message_date:%d/%m/%Y} (día del mensaje) se vendieron {sold_pair} de este proveedor '
                    f'y variante: se descuentan de las {quantity} que declaró. Quedan {available} para vender.')
        if reference is not None:
            # B0 review: what was sold since the registered declaration and this row would put back on sale.
            since = max(reference.committed_quantity - (reference.committed_at_declaration or 0), 0)
            current = _available(existing) if existing is not None else 0
            grows = max((available or 0) - (current or 0), 0)
            payload['would_restore'] = min(since, grows)
        if existing is None:
            if map_code:
                extra['mappings'] += 1
            payload['available'] = available
            stage(row_number, BulkImportRow.CREATE, key=f'{supplier.pk}:{product.pk}', data=payload,
                  warnings=warnings)
            continue
        twin = SupplierOffer.objects.filter(company=company, supplier=supplier, product=product,
                                            valid_from=start, valid_until=end).exclude(pk=existing.pk).first()
        if twin is not None:
            stage(row_number, BulkImportRow.ERROR, data=shown, warnings=warnings, errors=[
                'Ya hay otra oferta de ese proveedor para ese producto con esa misma vigencia (desactivada o '
                'vencida). Cambia las fechas del archivo.'])
            continue
        payload['offer_id'] = existing.pk
        payload['offer_updated_at'] = existing.updated_at.isoformat()
        payload['offer_state'] = _fingerprint(existing)
        payload['before'] = _offer_before(existing, with_cost=with_cost)
        payload['available'] = available            # NET: what is left to sell after applying
        sold_out = quantity == 0
        if quantity is None and existing.committed_quantity:
            if complete_supplier is None:
                stage(row_number, BulkImportRow.ERROR, data=shown, warnings=warnings, errors=[
                    f'Ya se vendieron {existing.committed_quantity} contra esta oferta: indica cuántas le quedan al '
                    'proveedor (0 = agotada).'])
                continue
            payload['unconfirmed_keeping_commitments'] = True
            warnings.append(f'Sin cantidad: sus {existing.committed_quantity} venta(s) comprometidas siguen; no se '
                            'vende más hasta que el proveedor confirme cuántas le quedan.')
        elif quantity is None and existing.confirmed_quantity is not None and existing.availability_confirmed:
            # B0 (f): never a silent «sin confirmar». Apply asks: clear it or keep the confirmed quantity.
            payload['quantity_cleared'] = True
            extra['quantity_cleared'] += 1
            warnings.append(
                f'El mensaje no dice cuántas quedan y la oferta tenía {existing.confirmed_quantity} confirmada(s): '
                'al aplicar decide si pasa a «sin confirmar» (deja de venderse) o conserva su cantidad.')
        if active is None and not existing.is_active:
            warnings.append('Oferta desactivada a mano: no se reactiva. Escribe «Sí» en «Activa» para reactivarla.')
        changes = [name for name, differs in (
            ('commercial_price', existing.commercial_price != clean['commercial_price']),
            ('available', _available(existing) != available),
            ('valid_from', existing.valid_from != start),
            ('valid_until', existing.valid_until != end),
            ('lead_time_days', existing.lead_time_days != clean['lead_time_days']),
            ('is_active', active is not None and existing.is_active != active),
            ('agreed_cost', 'agreed_cost' in clean and existing.agreed_cost != clean['agreed_cost']),
            ('sale_mode', wanted_mode is not None and product.sale_mode != wanted_mode),
        ) if differs]
        payload['changes'] = changes
        if map_code:
            extra['mappings'] += 1
        if not changes:
            # B0: a NEW declaration that changes nothing visible still registers the supplier's confirmation.
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
        'repeated': extra['repeated'], 'older': extra['older'], 'reactivations': extra['reactivations'],
        'quantity_cleared': extra['quantity_cleared'],
    }}
    update_fields = ['rows_total', 'rows_create', 'rows_update', 'rows_no_change', 'rows_skip', 'rows_error',
                     'summary']
    if not counts['error']:
        # B0: what the list SAYS, whatever the bytes. A list with errors is never applied: no fingerprint.
        content, rows_fp = content_fingerprints(
            company_id=company.pk, message_date=message_date,
            complete_supplier_id=complete_supplier.pk if complete_supplier else None,
            rows=[row.normalized_data for row in staged
                  if row.action in (BulkImportRow.CREATE, BulkImportRow.UPDATE, BulkImportRow.NO_CHANGE)])
        job.content_fingerprint = content
        job.summary = {**job.summary, 'rows_fingerprint': rows_fp}
        update_fields.append('content_fingerprint')
    job.save(update_fields=update_fields)
    return job


def _classify(reference, existing, message_date, clean) -> str:
    """
    B0 — the row against the PAIR's latest registered declaration (`reference`, any window): new, repetition,
    older — or a conflict: the same day's message with other content for ANOTHER window (one message, one offer).
    """
    if reference is None or message_date > reference.declared_message_date:
        return DECLARATION_NEW
    same = (reference.last_declared_quantity == clean['confirmed_quantity']
            and reference.commercial_price == clean['commercial_price']
            and reference.lead_time_days == clean['lead_time_days']
            and ('agreed_cost' not in clean or reference.agreed_cost == clean['agreed_cost']))
    if same:
        return DECLARATION_REPETITION
    if message_date < reference.declared_message_date:
        return DECLARATION_OLDER
    # The same day with other content: a correction of the offer that holds it (it never moves the validity) —
    # never a second offer.
    return DECLARATION_NEW if existing is not None and existing.pk == reference.pk else DECLARATION_CONFLICT


def _stage_unchanged_declaration(payload, reference, declaration, message_date, warnings, *, with_cost, existing,
                                 product, active, wanted_mode) -> list:
    """
    A repetition or an older message: shown against the offer that holds the declaration; its declaration is
    applied as NOTHING (quantity, validity, price, lead time, confirmation). Returns the switch columns that still
    apply — only on a repetition:
      * «Bajo pedido» is the product's sale mode, the person's instruction (SUPPLIER-DEVICE-FLOW-02, owner): applied
        as before B0;
      * «Activa» only on the offer the row names (the one holding the declaration);
      * an OLDER message applies neither: it must not undo what a newer list, or a person since, decided.
    """
    existing_is_reference = existing is not None and existing.pk == reference.pk
    switches = []
    if declaration == DECLARATION_REPETITION:
        if wanted_mode is not None and product.sale_mode != wanted_mode:
            switches.append('sale_mode')
        if active is not None and existing_is_reference and reference.is_active != active:
            switches.append('is_active')
    asked = [name for name, value in (('sale_mode', wanted_mode), ('is_active', active)) if value is not None]
    ignored = [name for name in asked if name not in switches and (
        (name == 'sale_mode' and product.sale_mode != wanted_mode)
        or (name == 'is_active' and (not existing_is_reference or reference.is_active != active)))]
    for switch in ('is_active', 'sale_mode'):
        if switch not in switches:
            payload.pop(switch, None)
    existing = reference
    payload.update({
        'offer_id': existing.pk, 'offer_updated_at': existing.updated_at.isoformat(),
        'offer_state': _fingerprint(existing), 'before': _offer_before(existing, with_cost=with_cost),
        'available': _available(existing), 'changes': list(switches),
        'valid_from': existing.valid_from.isoformat(), 'valid_until': existing.valid_until.isoformat(),
    })
    registered = existing.declared_message_date
    if declaration == DECLARATION_REPETITION:
        left = _available(existing)
        warnings.append(
            f'Repite la declaración ya registrada (mensaje del {registered:%d/%m/%Y}): no cambia nada. Lo vendido '
            'desde entonces sigue descontado' + (f' (quedan {left})' if left is not None else '')
            + ' y la vigencia no se renueva.')
    else:
        warnings.append(
            f'El mensaje ({message_date:%d/%m/%Y}) es anterior a la declaración ya registrada '
            f'({registered:%d/%m/%Y}): esta fila no cambia nada. Para cambiar la oferta, edítala en el panel.')
    if not existing.is_active and expired_only(existing):
        warnings.append('La oferta está vencida: un mensaje que no es posterior al registrado no la reactiva.')
    if ignored:
        why = ('el mensaje es anterior al registrado' if declaration == DECLARATION_OLDER
               else 'la fila es de otra vigencia que la oferta registrada')
        warnings.append(f'«Activa» y «Bajo pedido» no se aplican en esta fila ({why}): cámbialos en la oferta.')
    return switches


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
        # Internal: what apply compares to refuse a stale preview. `offer_state` carries the offer's agreed
        # cost — it never leaves the server, whoever asks (B0 review: it used to reach who lacks `costs.view`).
        for internal in ('listed', 'offer_state'):
            data.pop(internal, None)
        rows.append({'row_number': row.row_number, 'action': row.action, 'errors': row.errors,
                     'warnings': row.warnings, 'data': data})
    summary = (job.summary or {}).get('counts') or {
        'new': job.rows_create, 'updated': job.rows_update, 'unchanged': job.rows_no_change, 'sold_out': 0,
        'errors': job.rows_error, 'skipped': job.rows_skip, 'price_changes': 0, 'mappings': 0,
    }
    pending = job.status != BulkImportJob.APPLIED
    # B0: who applied the same content, and when — never the fingerprint itself (it may hash a cost).
    content = previously_applied_content(job) if pending else None
    return {
        'id': job.pk, 'status': job.status, 'rows_total': job.rows_total, 'rows_create': job.rows_create,
        'rows_update': job.rows_update, 'rows_no_change': job.rows_no_change, 'rows_error': job.rows_error,
        'cost_column_ignored': bool((job.summary or {}).get('cost_column_ignored')),
        'message_date': job.message_date.isoformat() if job.message_date else None,
        'already_applied': previously_applied(job) if pending else None,
        'already_applied_content': ({**content, 'detail': already_applied_content_text(content)}
                                    if content else None),
        'same_rows_applied': _same_rows_payload(job) if pending and not content else None,
        'summary': summary, 'rows': rows,
        **_snapshot_payload(job),
    }


def _restores(rows) -> list[dict]:
    """B0 review — per offer, the units sold since the registered declaration that a row would sell again."""
    return [{'row_number': row.row_number, 'offer_id': (row.normalized_data or {}).get('offer_id'),
             'product_name': (row.normalized_data or {}).get('product_name'),
             'units': int((row.normalized_data or {}).get('would_restore') or 0)}
            for row in rows if (row.normalized_data or {}).get('declaration') == DECLARATION_NEW
            and (row.normalized_data or {}).get('would_restore')]


def _same_rows_payload(job):
    reference = previously_applied_rows(job)
    if reference is None:
        return None
    restores = _restores(job.rows.order_by('row_number', 'pk'))
    return {**reference, 'restores': restores, 'detail': same_rows_text(reference, restores)}


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
    """
    `{job_id, applied_at}` of the latest APPLIED job of the company with the very same file, or None.
    B0: the same bytes under ANOTHER message date are a new message (the supplier resent the same list): the
    content fingerprint and the per-row declarations judge them; this guard keeps the jobs of before B0.
    """
    earlier = (BulkImportJob.objects.filter(company_id=job.company_id, import_type=BulkImportJob.SUPPLIER_OFFERS,
                                            file_sha256=job.file_sha256, status=BulkImportJob.APPLIED)
               .filter(Q(message_date__isnull=True) | Q(message_date=job.message_date))
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
          confirm_deactivations=None, reapply_reason: str = '', may_override_reapply: bool = False,
          confirm_reactivations=None, quantity_decisions=None, confirm_new_declaration: bool = False,
          new_declaration_note: str = '') -> dict:
    """
    Write every staged offer. ALL OF THE FILE OR NONE OF IT. A job applied
    before answers with what it did then, and does nothing.

    B0: `confirm_reapply` + `reapply_reason` (and `may_override_reapply`: the
    whole company in scope) for a content already applied;
    `confirm_reactivations` = the exact number of expired offers it switches
    back on; `quantity_decisions` ({offer id: `clear` | `keep`}, one per row
    that clears the quantity of a confirmed offer; `keep` declares nothing);
    `confirm_new_declaration` + `new_declaration_note` for the same rows as an
    applied load under a later message date (B0 review). Repetitions and older
    messages write nothing whatever is confirmed.
    """
    job = BulkImportJob.objects.select_for_update().get(pk=job.pk)
    if job.import_type != BulkImportJob.SUPPLIER_OFFERS:
        raise OfferImportError('Este trabajo no es una carga de ofertas.')
    if job.status == BulkImportJob.APPLIED:
        return {'status': 'applied', **(job.summary or {}).get('applied', {})}
    if job.rows_error:
        raise OfferImportError(f'Hay {job.rows_error} fila(s) con error. Corrige el archivo y vuelve a subirlo.')
    # IMPORT-STALE-01 — the age; what changed since is the `stale` checks further down.
    from . import import_freshness

    import_freshness.refuse_if_expired(job, OfferImportError)
    if accept_price_changes and keep_prices:
        raise OfferImportError('Elige aceptar los precios nuevos o mantener los actuales, no las dos cosas.')
    if job.message_date is None:
        # Previewed before B0: without the message date its rows cannot be told from a repetition.
        raise OfferImportError('Esta vista previa no tiene la fecha del mensaje del proveedor: vuelve a '
                               'previsualizar la lista indicándola.', code='message_date_required')
    decisions = _clean_decisions(quantity_decisions)
    from .cost_services import COSTS_FORBIDDEN, COSTS_IN_JOB, staged_rows_carry_cost, user_may_view_costs

    # The cost is written only if the preview read it AND whoever applies may see costs.
    applier_sees_costs = user_may_view_costs(actor, job.company)
    with_cost = bool((job.options_snapshot or {}).get('with_cost')) and applier_sees_costs
    rows = list(job.rows.order_by('row_number', 'pk'))
    # COST-WRITE-AUTHORITY — a job previewed WITH costs by somebody who holds `costs.view` is REFUSED to an
    # applier who does not (it used to be applied without the costs). Nothing is written; the job stays previewed.
    if not applier_sees_costs and staged_rows_carry_cost(rows, ('agreed_cost',)):
        raise OfferImportError(COSTS_IN_JOB, code=COSTS_FORBIDDEN)
    priced = _price_rows(rows)
    if priced and not (accept_price_changes or keep_prices):
        raise OfferImportError(
            f'El archivo cambia el precio comercial de {len(priced)} oferta(s). Acepta los precios nuevos o '
            'mantén los actuales.', code='price_confirmation_required', extra={'price_changes': priced})
    decision = ('accepted' if accept_price_changes else 'kept') if priced else None
    _require_review_decisions(rows, confirm_reactivations=confirm_reactivations, decisions=decisions)
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
    # payment of several lines takes them in — before any is written. B0: with every
    # other offer of the same supplier and variant, so no payment commits against
    # them while the units sold since the message are counted.
    wanted = {_pk(r.normalized_data.get('offer_id')) for r in writing} - {None}
    wanted |= _pair_offer_ids(job.company_id, pairs)
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
        if now['looks_truncated']:
            # Second review: recomputed under the lock — offers that stopped running since the preview can turn
            # the same absent ones into «most of what runs».
            raise OfferImportError(
                'Desde la vista previa dejaron de estar vigentes otras ofertas del proveedor y esta lista ahora '
                'desactivaría la mayoría: parece incompleta. Vuelve a previsualizarla.', code='snapshot_looks_truncated')
    # B0: the same content applied before — checked under the job and pair locks, so of two identical
    # lists applied at once the second one waits here and then sees the first one applied.
    reason = _clean_reason(reapply_reason)
    earlier_content = previously_applied_content(job)
    if earlier_content is not None:
        if not confirm_reapply:
            raise OfferImportError(already_applied_content_text(earlier_content), code='already_applied_content',
                                   extra={'already_applied_content': earlier_content})
        if not may_override_reapply:
            raise OfferImportError(
                'Registrar otra vez una lista ya aplicada es una revisión de toda la empresa y tu acceso está '
                'limitado a algunas sucursales. Pide a un responsable con acceso a toda la empresa que la revise.',
                code='reapply_forbidden')
        if not reason:
            raise OfferImportError(
                'Indica el motivo para registrar otra vez esta misma lista (por ejemplo, «el proveedor la reenvió '
                'y lo confirmó por teléfono»). Las filas repetidas no cambiarán nada igualmente.',
                code='reapply_reason_required', extra={'already_applied_content': earlier_content})
    # B0 review: the same rows as an applied load under a LATER message date would put back on sale what sold
    # since — only when the person states that the supplier really re-sent the list (products.manage suffices:
    # it is the weekly flow). Audited with what it sells again.
    note = ' '.join(str(new_declaration_note or '').split())[:MAX_NOTE]
    earlier_rows = previously_applied_rows(job) if earlier_content is None else None
    if earlier_rows is not None:
        restores = _restores(rows)
        if not confirm_new_declaration:
            raise OfferImportError(same_rows_text(earlier_rows, restores), code='same_rows_new_date',
                                   extra={'same_rows_applied': {**earlier_rows, 'restores': restores}})
        if not note:
            raise OfferImportError(
                'Escribe una nota que diga que el proveedor volvió a enviar esta lista en la fecha indicada (por '
                'ejemplo, «mensaje del proveedor de hoy, mismas cantidades»).', code='new_declaration_note_required',
                extra={'same_rows_applied': {**earlier_rows, 'restores': restores}})
    for row in writing:
        data = row.normalized_data or {}
        if 'pair_declaration' in data and _pair_signature(pair_declaration(
                job.company_id, data.get('supplier_id'), data.get('product_id'))) != data['pair_declaration']:
            # Another list registered a declaration for this supplier and variant since the preview.
            raise OfferImportError(STALE, code='stale')
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
    if earlier is not None and earlier_content is None and not confirm_reapply:
        raise OfferImportError(
            'Este archivo ya se aplicó. Aplicarlo otra vez no repone lo vendido ni renueva la vigencia de lo que '
            'repite; confírmalo para aplicarlo de nuevo.', code='already_applied', extra={'already_applied': earlier})
    if earlier_content is not None or earlier is not None:
        previous = earlier_content or earlier
        AdminAuditLog.log(actor=actor, action='supplier_offers_reapply_confirmed', target_type='bulk_import_job',
                          target_id=job.pk, request=request, company=job.company,
                          metadata={'job_id': job.pk, 'previous_job_id': previous['job_id'],
                                    'previous_applied_at': previous['applied_at'], 'file_sha256': job.file_sha256,
                                    'kind': 'content' if earlier_content is not None else 'file',
                                    **({'reason': reason} if reason else {})})
    if earlier_rows is not None:
        AdminAuditLog.log(actor=actor, action='supplier_offers_new_declaration_confirmed',
                          target_type='bulk_import_job', target_id=job.pk, request=request, company=job.company,
                          metadata={'job_id': job.pk, 'previous_job_id': earlier_rows['job_id'],
                                    'previous_message_date': earlier_rows['message_date'],
                                    'message_date': job.message_date.isoformat(), 'note': note,
                                    'restores': _restores(rows)})

    # B0: what was committed since the message day began, read under the locks.
    sold = sold_since(job.company_id, job.message_date)
    created = updated = unchanged = sold_out = prices_changed = prices_kept = mappings = 0
    repeated = older = confirmed_count = reactivated = 0
    for row in writing:
        data = row.normalized_data or {}
        declaration = data.get('declaration', DECLARATION_NEW)
        fields = {key: data.get(key) for key in ('commercial_price', 'confirmed_quantity', 'valid_from',
                                                 'valid_until', 'lead_time_days')}
        declared = data.get('declared_quantity', data.get('confirmed_quantity'))
        sold_pair, _ = _sold(sold, data.get('supplier_id'), data.get('product_id'))
        if with_cost and 'agreed_cost' in data:
            fields['agreed_cost'] = data['agreed_cost']
        if 'is_active' in data:
            fields['is_active'] = data['is_active']
        try:
            if declaration != DECLARATION_NEW:
                # A repetition or an older message: its declaration writes nothing — whatever was confirmed: what
                # was sold since stays sold, the validity and the confirmation stay. A repetition's switch columns
                # («Activa», «Bajo pedido», staged as an update of those alone) still apply, as before B0.
                repeated += declaration == DECLARATION_REPETITION
                older += declaration == DECLARATION_OLDER
                if row.action == BulkImportRow.UPDATE:
                    updated += 1
                    if 'is_active' in data:
                        supply_services.update_offer(offers[_pk(data.get('offer_id'))], actor=actor, request=request,
                                                     may_view_costs=with_cost, is_active=data['is_active'])
                else:
                    unchanged += 1
            elif row.action == BulkImportRow.CREATE:
                # Owner D1 with the safe cut-off: what the supplier declared minus what sold since the message.
                fields['confirmed_quantity'] = None if declared is None else max(int(declared) - sold_pair, 0)
                offer = supply_services.create_offer(company=job.company, actor=actor, request=request,
                                                     may_view_costs=with_cost, supplier_id=data.get('supplier_id'),
                                                     product_id=data.get('product_id'), **fields)
                created += 1
                confirmed_count += _register_declaration(
                    offer, job=job, row=row, declared=declared, committed_at=0, sold_pair=sold_pair,
                    kind='created', actor=actor, request=request)
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
                elif data.get('quantity_cleared') and decisions.get(str(offer.pk)) == 'keep':
                    # B0 (f): the person kept the confirmed quantity of a row that came without one.
                    fields.pop('confirmed_quantity', None)
                    AdminAuditLog.log(actor=actor, action='supplier_offer_quantity_kept', target_type='supplier_offer',
                                      target_id=offer.pk, request=request, company=job.company, metadata={
                                          'job_id': job.pk, 'row_number': row.row_number,
                                          'confirmed': offer.confirmed_quantity})
                elif declared is None:
                    fields['confirmed_quantity'] = None
                else:
                    # NET AVAILABILITY (owner D1) with the SAFE CUT-OFF (B0): `committed` and the units sold
                    # since the message day began, both read under the locks — what is left is the declared
                    # quantity minus what sold after the message, never negative, never sold twice.
                    fields['confirmed_quantity'] = offer.committed_quantity + max(int(declared) - sold_pair, 0)
                if price_change and keep_prices:
                    fields.pop('commercial_price')
                if data.get('reactivation_requires_review'):
                    reactivated += 1
                    AdminAuditLog.log(actor=actor, action='supplier_offer_reactivation_confirmed',
                                      target_type='supplier_offer', target_id=offer.pk, request=request,
                                      company=job.company, metadata={
                                          'job_id': job.pk, 'row_number': row.row_number,
                                          'previous_valid_until': offer.valid_until.isoformat(),
                                          'valid_until': data.get('valid_until')})
                supply_services.update_offer(offer, actor=actor, request=request, may_view_costs=with_cost, **fields)
                quantity_after = {'confirmed': offer.confirmed_quantity, 'committed': offer.committed_quantity,
                                  'available': _available(offer)}
                if quantity_after != quantity_before:
                    AdminAuditLog.log(actor=actor, action='supplier_offer_quantity_set', target_type='supplier_offer',
                                      target_id=offer.pk, request=request, company=job.company, metadata={
                                          'job_id': job.pk, 'row_number': row.row_number,
                                          'file_quantity': declared, 'sold_since_message': sold_pair,
                                          'before': quantity_before, 'after': quantity_after})
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
                _, sold_offer = _sold(sold, data.get('supplier_id'), data.get('product_id'), offer.pk)
                if not (data.get('quantity_cleared') and decisions.get(str(offer.pk)) == 'keep'):
                    # A kept quantity is the person's decision, not the supplier's: nothing is declared.
                    confirmed_count += _register_declaration(
                        offer, job=job, row=row, declared=declared, sold_pair=sold_pair,
                        committed_at=max(offer.committed_quantity - sold_offer, 0), kind='updated', actor=actor,
                        request=request)
            else:
                unchanged += 1
                offer = offers.get(_pk(data.get('offer_id')))
                if offer is not None:
                    # A NEW declaration that changed nothing visible: the supplier still confirmed it. A sale
                    # since the preview against ANOTHER offer of the pair still comes off what is left.
                    if declared is not None and offer.confirmed_quantity is not None and offer.availability_confirmed:
                        target = offer.committed_quantity + max(int(declared) - sold_pair, 0)
                        if target < offer.confirmed_quantity:
                            supply_services.update_offer(offer, actor=actor, request=request,
                                                         may_view_costs=with_cost, confirmed_quantity=target)
                    _, sold_offer = _sold(sold, data.get('supplier_id'), data.get('product_id'), offer.pk)
                    confirmed_count += _register_declaration(
                        offer, job=job, row=row, declared=declared, sold_pair=sold_pair,
                        committed_at=max(offer.committed_quantity - sold_offer, 0), kind='no_change', actor=actor,
                        request=request)
            if data.get('sale_mode') and row.action in (BulkImportRow.CREATE, BulkImportRow.UPDATE):
                product = Product.objects.filter(company=job.company, pk=data.get('product_id')).first()
                if product is not None:
                    supply_services.set_sale_mode(product, data['sale_mode'], actor=actor, request=request)
            mappings += _learn_code(job, data, actor=actor, request=request)
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
              'price_decision': decision, 'repeated': repeated, 'older': older, 'confirmed': confirmed_count,
              'reactivated': reactivated, **({'deactivated': len(deactivated)} if snapshot else {})}
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


def _learn_code(job, data, *, actor, request) -> int:
    """Record the supplier code a row matched without doubt (see WHICH VARIANT). 1 when it was new."""
    if not data.get('map_supplier_code'):
        return 0
    supplier = Supplier.objects.get(company=job.company, pk=data.get('supplier_id'))
    product = Product.objects.get(company=job.company, pk=data.get('product_id'))
    _ref, changed = save_product_ref(company=job.company, supplier=supplier, supplier_code=data['map_supplier_code'],
                                     product=product, actor=actor, request=request, source='import', job_id=job.pk)
    return int(changed)


def _pair_offer_ids(company_id, pairs) -> set:
    """Every offer of the (supplier, variant) pairs of the job."""
    wanted = {(_pk(s), _pk(p)) for s, p in pairs if _pk(s) and _pk(p)}
    if not wanted:
        return set()
    rows = (SupplierOffer.objects.filter(company_id=company_id, supplier_id__in={s for s, _p in wanted},
                                         product_id__in={p for _s, p in wanted})
            .values_list('pk', 'supplier_id', 'product_id'))
    return {pk for pk, supplier_id, product_id in rows if (supplier_id, product_id) in wanted}


def _review_rows(rows, flag) -> list[dict]:
    return [{'row_number': row.row_number, 'offer_id': (row.normalized_data or {}).get('offer_id'),
             'product_name': (row.normalized_data or {}).get('product_name'),
             'valid_from': (row.normalized_data or {}).get('valid_from'),
             'valid_until': (row.normalized_data or {}).get('valid_until')}
            for row in rows if row.action == BulkImportRow.UPDATE and (row.normalized_data or {}).get(flag)]


def _clean_decisions(value) -> dict:
    """`{offer id (text): 'keep' | 'clear'}`; anything else is refused (400)."""
    if value in (None, ''):
        return {}
    if not isinstance(value, dict) or any(v not in QUANTITY_DECISIONS for v in value.values()):
        raise OfferImportError('Cada decisión sobre una cantidad vacía es «keep» (conservar la cantidad confirmada) o '
                               '«clear» (dejarla sin confirmar), por oferta.', code='invalid')
    return {str(k): v for k, v in value.items()}


def _require_review_decisions(rows, *, confirm_reactivations, decisions) -> None:
    """B0 — expired offers switched back on and quantities cleared are never applied without a decision."""
    reactivations = _review_rows(rows, 'reactivation_requires_review')
    if reactivations and (isinstance(confirm_reactivations, bool) or confirm_reactivations != len(reactivations)):
        names = ', '.join(f'fila {r["row_number"]} «{(r["product_name"] or "")[:40]}»' for r in reactivations[:5])
        raise OfferImportError(
            f'La lista reactivaría {len(reactivations)} oferta(s) vencida(s) ({names}). Revísalas y confirma ese '
            'número para aplicarla, o quita esas filas.', code='reactivation_confirmation_required',
            extra={'reactivations': reactivations})
    cleared = [r for r in _review_rows(rows, 'quantity_cleared') if str(r['offer_id']) not in decisions]
    if cleared:
        names = ', '.join(f'fila {r["row_number"]} «{(r["product_name"] or "")[:40]}»' for r in cleared[:5])
        raise OfferImportError(
            f'{len(cleared)} fila(s) llegan sin cantidad sobre una oferta con cantidad confirmada ({names}). Decide '
            'si pasan a «sin confirmar» (dejan de venderse) o conservan la cantidad actual.',
            code='quantity_decision_required', extra={'quantity_cleared': cleared})


def _register_declaration(offer, *, job, row, declared, committed_at, sold_pair, kind, actor, request) -> int:
    """
    B0 — the supplier's declaration of this row, on its offer: quantity, message
    day, what the offer had committed at its cut-off; and the verifiable
    confirmation (`last_confirmed_at` = the message day's start, never earlier
    than one already recorded). Audited with the company and the job.
    """
    cutoff = message_cutoff(job.message_date)
    current = SupplierOffer.objects.filter(pk=offer.pk).values_list('last_confirmed_at', flat=True).first()
    confirmed_at = max(cutoff, current) if current else cutoff
    SupplierOffer.objects.filter(pk=offer.pk).update(
        last_declared_quantity=declared, declared_message_date=job.message_date,
        committed_at_declaration=committed_at, last_confirmed_at=confirmed_at, last_confirmed_job=job)
    AdminAuditLog.log(actor=actor, action='supplier_offer_confirmed', target_type='supplier_offer',
                      target_id=offer.pk, request=request, company=job.company, metadata={
                          'job_id': job.pk, 'row_number': row.row_number, 'source': 'message', 'kind': kind,
                          'message_date': job.message_date.isoformat(), 'declared_quantity': declared,
                          'sold_since_message': sold_pair, 'committed_at_declaration': committed_at})
    return 1


def confirm_by_operator(offer, *, actor, request=None) -> None:
    """B0 — an operator's explicit confirmation of the offer's availability (panel): `last_confirmed_at` = now."""
    now = timezone.now()
    SupplierOffer.objects.filter(pk=offer.pk).update(last_confirmed_at=now, last_confirmed_job=None)
    AdminAuditLog.log(actor=actor, action='supplier_offer_confirmed', target_type='supplier_offer', target_id=offer.pk,
                      request=request, company=offer.company, metadata={
                          'job_id': None, 'source': 'operator', 'confirmed_quantity': offer.confirmed_quantity,
                          'committed_quantity': offer.committed_quantity})
    offer.refresh_from_db()


def _audit_price(job, row, offer, change, *, kept, actor, request):
    if kept:
        action, meta = 'supplier_offer_price_kept', {'price': change['before'], 'proposed': change['after']}
    else:
        action, meta = 'supplier_offer_price_changed', {'before': change['before'], 'after': change['after']}
    AdminAuditLog.log(actor=actor, action=action, target_type='supplier_offer', target_id=offer.pk,
                      metadata={**meta, 'job_id': job.pk, 'row_number': row.row_number}, request=request,
                      company=job.company)
