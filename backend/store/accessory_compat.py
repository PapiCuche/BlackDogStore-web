"""
P2 · F3 — the ACCESSORIES that fit a model («Accesorios compatibles»).

    AN ACCESSORY IS A PRODUCT WITHOUT A MODEL. A MODEL LISTS THE ONES THAT FIT IT, IN ORDER.

`AccessoryCompatibility(model, accessory, sort_order)` rows, one company each. The only writer
is `set_for_model`, which replaces the whole ordered list of one model under the model's lock and
audits only a real change. An id of another company answers like one that does not exist.

The public page (`device_catalog.public_model`) reads `for_model_public`: only accessories the shop
SELLS (active, published, not a pending draft, still without a model), with the price and the
availability every public product gets — the fulfilment branch's shelf first, then an on-order
offer. Never a cost. A sold-out accessory is listed with `availability: 'none'` (published is not
available, owner 2026-10-20); whether the page shows it is the page's decision.
"""
from __future__ import annotations

from django.db import transaction
from django.db.models import IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce

from .model_palette import is_ascii_id
from .models import AccessoryCompatibility, BranchStock, Product

#: Accessories one model may list: far above a real page, below any abuse.
MAX_ACCESSORIES = 60

AUDIT_ACTION = 'product_model_accessories_set'
#: The list changed since the caller read it (HTTP 409).
ACCESSORIES_CHANGED = 'accessories_changed'


def _error(message, code='invalid'):
    from .device_catalog import CatalogError

    return CatalogError(message, field='accessory_ids', code=code)


def clean_accessory_ids(value, *, field='accessory_ids') -> list[int]:
    """
    An ORDERED list of positive ints (ASCII digits as text accepted). A repeated id is refused, never folded.
    """
    from .device_catalog import CatalogError

    if not isinstance(value, (list, tuple)) or len(value) > MAX_ACCESSORIES:
        raise CatalogError(f'{field} debe ser una lista de hasta {MAX_ACCESSORIES} accesorios.', field=field,
                           code='invalid')
    ids: list[int] = []
    for item in value:
        if isinstance(item, str) and is_ascii_id(item):
            item = int(item)
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise CatalogError('Accesorios no válidos.', field=field, code='invalid')
        if item in ids:
            raise CatalogError('Un accesorio aparece dos veces en la lista.', field=field, code='duplicate')
        ids.append(item)
    return ids


def linked_ids(model) -> list[int]:
    return list(AccessoryCompatibility.objects.filter(model=model, company_id=model.company_id)
                .order_by('sort_order', 'pk').values_list('accessory_id', flat=True))


@transaction.atomic
def set_for_model(model, *, accessory_ids, actor, request=None, expected_ids=None) -> dict:
    """
    Make `accessory_ids`, in that order, THE accessories of `model`. Every id must be a product of the
    model's company without a model of its own; one that is not refuses the whole call. The same list
    again writes nothing and audits nothing.

    `expected_ids` (optional) is the ordered list the caller last read. When the list under the lock is
    another one, somebody saved in between: refused (`accessories_changed`, 409) instead of overwritten.
    Without it, the call replaces whatever is there (last writer wins), as before.
    """
    from .device_catalog import CatalogError, _audit

    wanted = clean_accessory_ids(accessory_ids)
    expected = None if expected_ids is None else clean_accessory_ids(expected_ids, field='expected_ids')
    # The model's row is the lock: two saves of one model's list queue, the second reads the first.
    model = type(model).objects.select_for_update().get(pk=model.pk)
    company_id = model.company_id
    if expected is not None and expected != linked_ids(model):
        raise CatalogError('Otra persona cambió los accesorios de este modelo mientras editabas. '
                           'Se volvió a cargar la lista guardada: revisa y vuelve a guardar.',
                           field='accessory_ids', code=ACCESSORIES_CHANGED)
    found = {product.pk: product for product in Product.objects.filter(company_id=company_id, pk__in=wanted)}
    if len(found) != len(wanted):
        raise _error('Algún accesorio no existe.', code='invalid_accessory')
    devices = [pk for pk in wanted if found[pk].product_model_id is not None]
    if devices:
        raise _error('Una variante de un modelo es un equipo, no un accesorio.', code='not_an_accessory')

    current = {row.accessory_id: row for row in AccessoryCompatibility.objects.filter(model=model, company_id=company_id)}
    previous = [row.accessory_id for row in sorted(current.values(), key=lambda r: (r.sort_order, r.pk))]
    if previous == wanted:
        return {'changed': False, 'total': len(wanted)}

    removed = [pk for pk in previous if pk not in wanted]
    if removed:
        AccessoryCompatibility.objects.filter(model=model, company_id=company_id, accessory_id__in=removed).delete()
    added = []
    for position, pk in enumerate(wanted):
        row = current.get(pk)
        if row is None:
            AccessoryCompatibility(company_id=company_id, model=model, accessory=found[pk], sort_order=position).save()
            added.append(pk)
        elif row.sort_order != position:
            AccessoryCompatibility.objects.filter(pk=row.pk).update(sort_order=position)
    _audit(AUDIT_ACTION, company=model.company, actor=actor, request=request,
           target_type='product_model', target_id=model.pk, model_name=model.name,
           accessory_ids=wanted, previous_ids=previous, added=added, removed=removed)
    return {'changed': True, 'total': len(wanted)}


def admin_payload(product, *, sort_order=None) -> dict:
    """One accessory as the panel shows it: its sale price and whether the shop shows it. Never a cost."""
    data = {
        'id': product.pk,
        'name': product.name,
        'slug': product.slug,
        'price': str(product.price),
        'image_url': product.image_url,
        'is_active': product.is_active,
        'is_published_online': product.is_published_online,
        # What `for_model_public` would list (`is_sold_online` plus «still an accessory»).
        'shown_online': bool(product.is_sold_online and product.product_model_id is None),
        'is_accessory': product.product_model_id is None,
    }
    if sort_order is not None:
        data['sort_order'] = sort_order
    return data


def for_model_admin(model) -> list[dict]:
    rows = (AccessoryCompatibility.objects.filter(model=model, company_id=model.company_id)
            .select_related('accessory').order_by('sort_order', 'pk'))
    return [admin_payload(row.accessory, sort_order=row.sort_order) for row in rows]


def candidates(company, *, search='', limit=20) -> list[dict]:
    """The company's accessories (products without a model), by name, for the panel's search."""
    qs = (Product.objects.filter(company=company, product_model__isnull=True)
          .exclude(Product.pending_draft_q()).order_by('-is_active', 'name', 'pk'))
    search = (search or '').strip()[:100]
    if search:
        qs = qs.filter(name__icontains=search)
    return [admin_payload(product) for product in qs[:limit]]


def _sold_online_q() -> Q:
    """`company_storefront_products`' rule, seen from the link: active, published, not a pending draft."""
    return (Q(accessory__is_active=True, accessory__is_published_online=True, accessory__product_model__isnull=True)
            & ~(Q(accessory__sale_enabled_at__isnull=True) & ~Q(accessory__draft_origin='')))


def for_model_public(model, branch=None) -> list[dict]:
    """
    The accessories of `model` the shop sells, in the company's order. Two queries read them all (the links
    with their product and shelf, then their pictures); an accessory that may be sold on order adds 1–2 more
    each, to read its offer and the company's over-stock setting (`on_order_payload`), as a variant does
    in `public_model`. Others add none.

    `on_order` is the same as the public product's (`ProductSerializer.get_on_order`): carried with stock on
    the shelf too, so `over_stock` reaches the page; `availability` still says own stock first.
    """
    from .product_media import public_payload
    from .supply_services import on_order_payload

    if branch is not None and branch.company_id != model.company_id:
        branch = None
    rows = AccessoryCompatibility.objects.filter(model=model, company_id=model.company_id,
                                                 accessory__company_id=model.company_id).filter(_sold_online_q())
    if branch is not None:
        shelf = BranchStock.objects.filter(branch=branch, product_id=OuterRef('accessory_id')).values('quantity')[:1]
        rows = rows.annotate(shelf=Coalesce(Subquery(shelf, output_field=IntegerField()),
                                            Value(0, output_field=IntegerField())))
    else:
        rows = rows.annotate(shelf=Value(0, output_field=IntegerField()))
    rows = rows.select_related('accessory').prefetch_related('accessory__images').order_by('sort_order', 'pk')

    result = []
    for row in rows:
        product = row.accessory
        on_order = on_order_payload(product)
        availability = 'own_stock' if row.shelf > 0 else ('supplier' if on_order else 'none')
        result.append({
            'id': product.pk,
            'slug': product.slug,
            'name': product.name,
            'price': str(product.price),
            'image_url': product.image_url,
            'images': [public_payload(image) for image in product.images.all()],
            'available': availability != 'none',
            'availability': availability,
            'on_order': on_order,
            'sells_by_unit': product.sells_by_unit,
        })
    return result
