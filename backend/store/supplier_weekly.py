"""
SUPPLY-WEEKLY-01 — the week of a company's supplier lists, one row per supplier × variant. READ ONLY.

    GET /api/admin/supplier-offers/weekly/?supplier=<id>&week=YYYY-Www | YYYY-MM-DD

WHICH ROWS. Every supplier × variant that the week's applied lists mention, or whose offer runs during the
week or the one before. The week is Monday to Sunday (ISO); a day picks the week that holds it; nothing picks
the current one.

WHAT A ROW SAYS
  * The variant as the catalogue has it: its model and the option of each ROLE (capacity, colour, SIM, region),
    never parsed from a name. Nothing is hardcoded about a brand.
  * The offer as it is NOW: commercial price, confirmed and left quantity, lead time, validity and its state
    (`supplier_views.offer_state`). The agreed cost only to `costs.view` — without it the key is absent.
  * The variant's own price (`Product.price`) and the shop's OWN stock (`BranchStock.quantity` in the branches
    the caller reaches) in their own columns: a supplier's quantity is never added to it, and a list never
    creates a unit, a stock row or a movement.
  * What the week's list declared (message day, listed price, declared quantity) and the change against the
    supplier's previous list (`previous`, the latest list before the week, up to `PREVIOUS_LOOKBACK_DAYS`):

      retirado        the offer is switched off, or its validity ended (by the end of the week, or today);
      nuevo           mentioned this week with no earlier list to compare (and not older than the week), or
                      created this week without any list;
      precio_cambio   the listed commercial price differs from the previous list (`changes` may add quantity);
      cantidad_cambio the declared quantity differs;
      sin_cambio      listed again as before (or listed with no earlier list of an older offer);
      no_mencionado   running, and no list of the week mentions it (listed before, or older than the week).

    Deltas are read from the APPLIED lists (`BulkImportJob` + `BulkImportRow`, by message day): an edit by hand
    in the panel is not a list and changes no delta. A past week shows its lists, against the offers as they are
    today.

COST OF A CALL: a fixed number of queries whatever the rows (asserted by the tests).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import Q, Sum
from django.utils import timezone

from . import supply_services
from .models import BranchStock, BulkImportJob, BulkImportRow, ProductVariantOption, SupplierOffer, VariantAttribute

DELTA_NEW = 'nuevo'
DELTA_PRICE = 'precio_cambio'
DELTA_QUANTITY = 'cantidad_cambio'
DELTA_RETIRED = 'retirado'
DELTA_UNMENTIONED = 'no_mencionado'
DELTA_SAME = 'sin_cambio'
DELTAS = (DELTA_NEW, DELTA_PRICE, DELTA_QUANTITY, DELTA_RETIRED, DELTA_UNMENTIONED, DELTA_SAME)

#: How far back «the previous list» of a supplier and variant is looked for.
PREVIOUS_LOOKBACK_DAYS = 28
#: Rows answered at most (the response says when it was cut).
MAX_ROWS = 2000

_WRITING = (BulkImportRow.CREATE, BulkImportRow.UPDATE, BulkImportRow.NO_CHANGE)
_ISO_WEEK = re.compile(r'^(\d{4})-W(\d{2})$')
_ROLES = (VariantAttribute.ROLE_CAPACITY, VariantAttribute.ROLE_COLOR, VariantAttribute.ROLE_SIM,
          VariantAttribute.ROLE_REGION)


class WeekError(ValueError):
    pass


def week_start(value, *, on: date) -> date:
    """The Monday of the week `value` names («2026-W48», or a day «2026-11-24»); `on`'s week when empty."""
    text = str(value or '').strip()
    if not text:
        day = on
    else:
        match = _ISO_WEEK.match(text)
        try:
            if match:
                return date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1)
            day = date.fromisoformat(text)
        except ValueError:
            raise WeekError('Indica la semana como AAAA-Wnn (por ejemplo 2026-W48) o un día AAAA-MM-DD.') from None
    return day - timedelta(days=day.weekday())


def week_label(monday: date) -> str:
    year, week, _ = monday.isocalendar()
    return f'{year}-W{week:02d}'


def _local_day(moment) -> date | None:
    if moment is None:
        return None
    if isinstance(moment, datetime):
        return timezone.localtime(moment).date() if timezone.is_aware(moment) else moment.date()
    return moment


def _declarations(company, *, since: date, until: date, supplier_id=None) -> dict:
    """
    {(supplier id, product id): [declaration, …]} of the APPLIED lists whose message day falls in
    [since, until], newest first. Only the keys a row needs: never the cost. One query.
    """
    rows = (BulkImportRow.objects
            .filter(job__company=company, job__import_type=BulkImportJob.SUPPLIER_OFFERS,
                    job__status=BulkImportJob.APPLIED, job__message_date__gte=since, job__message_date__lte=until,
                    action__in=_WRITING)
            .values_list('job_id', 'job__message_date', 'job__applied_at', 'row_number', 'normalized_data')
            .order_by('-job__message_date', '-job__applied_at', '-job_id', '-row_number'))
    if supplier_id is not None:
        rows = rows.filter(normalized_data__supplier_id=supplier_id)
    found = {}
    for job_id, message_date, _applied_at, row_number, data in rows:
        data = data or {}
        pair = (supply_services._pk(data.get('supplier_id')), supply_services._pk(data.get('product_id')))
        if None in pair:
            continue
        declared = data.get('declared_quantity', data.get('confirmed_quantity'))
        found.setdefault(pair, []).append({
            'job_id': job_id, 'row_number': row_number, 'message_date': message_date,
            'commercial_price': _money(data.get('commercial_price')),
            'declared_quantity': None if declared is None else int(declared),
            'lead_time_days': data.get('lead_time_days'),
        })
    return found


def _money(value) -> str | None:
    """A listed price as the offers write it (two decimals), so «105» and «105.00» compare alike."""
    if value in (None, ''):
        return None
    try:
        return str(Decimal(str(value)).quantize(Decimal('0.01')))
    except (InvalidOperation, ValueError):
        return str(value)


def _options(product_ids) -> dict:
    """{product id: {role: label}} — the option of each role of each variant. One query."""
    out = {}
    rows = (ProductVariantOption.objects.filter(product_id__in=product_ids)
            .values_list('product_id', 'attribute__role', 'attribute__kind', 'option__label')
            .order_by('product_id', 'attribute__sort_order', 'attribute_id'))
    for product_id, role, kind, label in rows:
        if role == VariantAttribute.ROLE_OTHER and kind == VariantAttribute.KIND_COLOR:
            role = VariantAttribute.ROLE_COLOR
        if role in _ROLES:
            out.setdefault(product_id, {}).setdefault(role, label)
    return out


def _own_stock(product_ids, branches) -> dict:
    """{product id: units} on the shelves of `branches` (a queryset, used as a subquery). One query."""
    rows = (BranchStock.objects.filter(product_id__in=product_ids, branch_id__in=branches.values('pk'))
            .values('product_id').annotate(units=Sum('quantity')).order_by())
    return {row['product_id']: int(row['units'] or 0) for row in rows}


def _pick_offer(offers, *, start: date, end: date):
    """The pair's offer for the week: the one running in it that starts last; else the one that ended last."""
    running = [o for o in offers if o.valid_from <= end and o.valid_until >= start]
    if running:
        return max(running, key=lambda o: (o.is_active, o.valid_from, o.pk))
    return max(offers, key=lambda o: (o.valid_until, o.pk))


def _delta(offer, current, previous, *, start: date, end: date, on: date) -> tuple[str, list[str]]:
    reference = min(on, end)
    if not offer.is_active or offer.valid_until < reference:
        return DELTA_RETIRED, []
    created = _local_day(offer.created_at)
    if current is None:
        # Listed before and not this week: it stopped appearing. Never listed and created this week: new.
        if previous is None and created is not None and start <= created <= end:
            return DELTA_NEW, []
        return DELTA_UNMENTIONED, []
    if previous is None:
        if created is not None and created < start:
            return DELTA_SAME, []
        return DELTA_NEW, []
    changes = []
    if current['commercial_price'] != previous['commercial_price']:
        changes.append('price')
    if current['declared_quantity'] != previous['declared_quantity']:
        changes.append('quantity')
    if 'price' in changes:
        return DELTA_PRICE, changes
    if changes:
        return DELTA_QUANTITY, changes
    return DELTA_SAME, []


def _declaration_payload(declaration) -> dict | None:
    if declaration is None:
        return None
    return {'message_date': declaration['message_date'].isoformat(), 'job_id': declaration['job_id'],
            'commercial_price': declaration['commercial_price'],
            'declared_quantity': declaration['declared_quantity']}


def weekly(company, *, start: date, branches, with_cost: bool, supplier_id=None, on: date | None = None) -> dict:
    """The panel of the week that starts on `start` (a Monday). See the module docstring."""
    from .supplier_views import offer_state

    on = on or supply_services.today()
    end = start + timedelta(days=6)
    previous_start = start - timedelta(days=7)
    declarations = _declarations(company, since=start - timedelta(days=PREVIOUS_LOOKBACK_DAYS), until=end,
                                 supplier_id=supplier_id)
    current = {pair: next((d for d in found if d['message_date'] >= start), None)
               for pair, found in declarations.items()}
    previous = {pair: next((d for d in found if d['message_date'] < start), None)
                for pair, found in declarations.items()}
    declared_now = {pair for pair, found in current.items() if found is not None}
    declared_before = {pair for pair, found in previous.items()
                       if found is not None and found['message_date'] >= previous_start}

    offers = SupplierOffer.objects.filter(company=company).select_related('supplier', 'product',
                                                                          'product__product_model')
    if supplier_id is not None:
        offers = offers.filter(supplier_id=supplier_id)
    in_window = Q(valid_from__lte=end, valid_until__gte=previous_start)
    named = declared_now | declared_before
    if named:
        in_window |= Q(product_id__in=sorted({product_id for _supplier_id, product_id in named}))
    by_pair = {}
    for offer in offers.filter(in_window).order_by('pk'):
        pair = (offer.supplier_id, offer.product_id)
        touches = offer.valid_from <= end and offer.valid_until >= previous_start
        if touches or pair in named:
            by_pair.setdefault(pair, []).append(offer)

    chosen = {pair: _pick_offer(found, start=start, end=end) for pair, found in by_pair.items()}
    product_ids = {offer.product_id for offer in chosen.values()}
    options = _options(product_ids)
    stock = _own_stock(product_ids, branches)

    rows = []
    for pair, offer in chosen.items():
        now, before = current.get(pair), previous.get(pair)
        delta, changes = _delta(offer, now, before, start=start, end=end, on=on)
        product = offer.product
        roles = options.get(product.pk, {})
        row = {
            'supplier_id': offer.supplier_id, 'supplier_name': offer.supplier.name,
            'supplier_active': offer.supplier.is_active,
            'product_id': product.pk, 'product_name': product.name, 'offer_id': offer.pk,
            'model': product.product_model.name if product.product_model_id else None,
            'capacity': roles.get(VariantAttribute.ROLE_CAPACITY), 'color': roles.get(VariantAttribute.ROLE_COLOR),
            'sim': roles.get(VariantAttribute.ROLE_SIM), 'region': roles.get(VariantAttribute.ROLE_REGION),
            'condition_line': product.condition_line,
            'commercial_price': str(offer.commercial_price), 'variant_price': str(product.price),
            'confirmed_quantity': offer.confirmed_quantity if offer.availability_confirmed else None,
            'committed_quantity': offer.committed_quantity, 'remaining': offer.remaining_known,
            'lead_time_days': offer.lead_time_days,
            'valid_from': offer.valid_from.isoformat(), 'valid_until': offer.valid_until.isoformat(),
            'state': offer_state(offer, on=on), 'own_stock': stock.get(product.pk, 0),
            'message_date': now['message_date'].isoformat() if now else None,
            'listed_price': now['commercial_price'] if now else None,
            'declared_quantity': now['declared_quantity'] if now else None,
            'previous': _declaration_payload(before),
            'delta': delta, 'changes': changes,
        }
        if with_cost:
            row['agreed_cost'] = str(offer.agreed_cost) if offer.agreed_cost is not None else None
        rows.append(row)
    rows.sort(key=lambda r: (r['supplier_name'].lower(), (r['model'] or '').lower(), r['product_name'].lower(),
                             r['offer_id']))
    counts = {delta: 0 for delta in DELTAS}
    for row in rows:
        counts[row['delta']] += 1
    return {
        'week': week_label(start), 'week_start': start.isoformat(), 'week_end': end.isoformat(),
        'previous_week_start': previous_start.isoformat(), 'today': on.isoformat(), 'with_cost': with_cost,
        'counts': counts, 'total': len(rows), 'truncated': len(rows) > MAX_ROWS, 'results': rows[:MAX_ROWS],
    }
