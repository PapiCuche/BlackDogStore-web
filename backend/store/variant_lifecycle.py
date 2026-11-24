"""
VARIANT-SAFE-01 — deleting, archiving and unarchiving a variant without losing anything.

A variant is a `Product` with a `product_model` (DC2). Three acts, each under the product's row lock, each audited
with a snapshot of the variant (name, key, line, options, price, codes) in `AdminAuditLog.metadata`:

  delete     `products.manage` AND `products.delete`, a reason and the variant's name typed back. Only when NOTHING
             depends on it (see RELATIONS): its own option rows and codes go with it, explicitly; anything else blocks.
  archive    `products.manage` and a reason. For a variant WITH history the company no longer uses: it keeps its row
             and its combination, leaves the shop, the till, every importer and resolver and the palette's «existing»
             set, and the database keeps it inactive (`archived_product_is_inactive`). Refused while it still has
             stock (units available, set aside or in transit, or a shelf count above zero): move or sell it first.
  unarchive  `products.manage` and a reason. It comes back INACTIVE: reactivating it is a separate, ordinary edit.

THE CLASSIFICATION IS THE GUARD. Every relation that points at `Product` (`Product._meta.related_objects`) is listed in
`RELATIONS` as a blocker, as deleted with the variant, or as ignored with a reason; `test_variant_safe` fails when a
new foreign key appears unclassified, so a future table can never be emptied silently by a deletion.

CONCURRENCY. The product row is locked (`select_for_update(of=('self',))`) and the blockers are counted again under
that lock, inside the transaction that deletes. A child row inserted meanwhile by another transaction cannot commit
before this one (its foreign-key check needs a KEY SHARE lock on the locked row), and once this one has deleted the
product that insert fails; one committed before the lock is counted and blocks. PROTECT on every blocking relation
is the second belt: Django refuses the `delete()` itself if anything slipped through.

Audit rows are never touched: `AdminAuditLog` names its target by type and id (text), with no foreign key.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.db.models import ProtectedError, Sum
from django.utils import timezone

from .models import (
    AdminAuditLog, BranchStock, Product, ProductBarcode, ProductVariantOption, ReceiptDraft, StockUnit,
)

BLOCKER = 'blocker'
DELETED_WITH_VARIANT = 'deleted_with_variant'
IGNORED = 'ignored'

REASON_MIN, REASON_MAX = 5, 500


@dataclass(frozen=True)
class Relation:
    kind: str
    code: str
    label: str
    why: str = ''


#: (model label, field name) → what a deletion does about it. EVERY relation to `Product` must be here.
RELATIONS: dict[tuple[str, str], Relation] = {
    ('store.OrderItem', 'product'): Relation(BLOCKER, 'order_items', 'Líneas de pedidos o ventas'),
    ('store.StockMovement', 'product'): Relation(BLOCKER, 'stock_movements', 'Movimientos de inventario (Kardex)'),
    ('store.BranchStock', 'product'): Relation(BLOCKER, 'branch_stock', 'Filas de stock en sucursales'),
    ('store.StockUnit', 'product'): Relation(BLOCKER, 'stock_units', 'Equipos registrados (incluidos los anulados)'),
    ('store.StockTransferItem', 'product'): Relation(BLOCKER, 'transfer_items', 'Traslados entre sucursales'),
    ('store.InventoryCountItem', 'product'): Relation(BLOCKER, 'count_items', 'Recuentos de inventario'),
    ('store.LabelBatchItem', 'product'): Relation(BLOCKER, 'label_items', 'Etiquetas generadas o impresas'),
    ('store.CartItem', 'product'): Relation(
        BLOCKER, 'cart_items', 'Carritos de clientes que la contienen',
        'A cart is a customer\'s, not the catalogue\'s: it is never emptied by a deletion. It blocks until the '
        'customer removes it or it is archived instead.'),
    ('store.Review', 'product'): Relation(BLOCKER, 'reviews', 'Reseñas de clientes'),
    ('store.PromotionItem', 'product'): Relation(BLOCKER, 'promotion_items', 'Promociones o combos'),
    ('store.StorefrontCampaign', 'product'): Relation(BLOCKER, 'campaigns', 'Campañas de la tienda'),
    ('store.RepairQuoteItem', 'product'): Relation(BLOCKER, 'repair_quote_items', 'Presupuestos de reparación'),
    ('store.PartUsage', 'product'): Relation(BLOCKER, 'part_usages', 'Repuestos usados en reparaciones'),
    ('store.SupplierOffer', 'product'): Relation(BLOCKER, 'supplier_offers', 'Ofertas de proveedores'),
    ('store.SupplierProductRef', 'product'): Relation(BLOCKER, 'supplier_refs', 'Códigos de proveedor vinculados'),
    ('store.ProductImage', 'product'): Relation(
        BLOCKER, 'images', 'Imágenes de su galería',
        'Uploaded work: the gallery removes pictures one by one, a deletion never takes them along.'),
    ('store.AccessoryCompatibility', 'accessory'): Relation(
        BLOCKER, 'accessory_links', 'Compatibilidades configuradas como accesorio'),
    ('store.ProductVariantOption', 'product'): Relation(
        DELETED_WITH_VARIANT, 'variant_options', 'Opciones de la variante',
        'These rows ARE the variant (its capacity, colour…): they have no meaning without it. Deleted explicitly '
        'before the product and recorded in the audit snapshot.'),
    ('store.ProductBarcode', 'product'): Relation(
        DELETED_WITH_VARIANT, 'barcodes', 'Códigos de la variante',
        'Codes only identify their product (a palette draft is born with a generated one). Deleted explicitly '
        'and recorded in the audit snapshot; a printed label is a LabelBatchItem, which blocks.'),
}

#: Not a foreign key, so not in `related_objects`: an unfinished reception of somebody that names the variant.
RECEIPT_DRAFTS = Relation(BLOCKER, 'receipt_drafts', 'Recepciones de mercadería en curso (borradores)')
NOT_A_VARIANT = Relation(BLOCKER, 'not_a_variant', 'No es una variante de un modelo')


class LifecycleError(Exception):
    def __init__(self, message, *, code, status=409, field='', **extra):
        super().__init__(message)
        self.code, self.status, self.field, self.extra = code, status, field, extra

    def body(self) -> dict:
        body = {'detail': str(self), 'code': self.code, **self.extra}
        if self.field:
            body['field'] = self.field
        return body


def relation_key(relation) -> tuple[str, str]:
    return relation.related_model._meta.label, relation.field.name


def unclassified_relations() -> list[tuple[str, str]]:
    """What the guard test checks: every relation to `Product` that `RELATIONS` does not classify."""
    return [relation_key(r) for r in Product._meta.related_objects if relation_key(r) not in RELATIONS]


def _receipt_drafts(product) -> int:
    """Unexpired receptions of the variant's company whose lines name it (`payload.lines[].product.id`)."""
    return ReceiptDraft.objects.filter(
        company_id=product.company_id, expires_at__gt=timezone.now(),
        payload__lines__contains=[{'product': {'id': product.pk}}],
    ).count()


def blockers(product) -> list[dict]:
    """Every reason `product` cannot be deleted, with how many rows, in Spanish. Empty = deletable."""
    found = []
    if not product.product_model_id:
        found.append({'code': NOT_A_VARIANT.code, 'count': 1, 'label': NOT_A_VARIANT.label})
    for relation in Product._meta.related_objects:
        rule = RELATIONS.get(relation_key(relation))
        if rule is None:
            # Unclassified: never deleted blind. The guard test makes this unreachable in a release.
            rule = Relation(BLOCKER, 'unclassified', f'Datos relacionados sin clasificar ({relation_key(relation)[0]})')
        if rule.kind != BLOCKER:
            continue
        count = relation.related_model._base_manager.filter(**{relation.field.name: product.pk}).count()
        if count:
            found.append({'code': rule.code, 'count': count, 'label': rule.label})
    drafts = _receipt_drafts(product)
    if drafts:
        found.append({'code': RECEIPT_DRAFTS.code, 'count': drafts, 'label': RECEIPT_DRAFTS.label})
    return found


def stock_counts(product) -> dict:
    """
    What still exists of the variant: devices available, set aside or in transit, quantities above zero on a shelf,
    and quantities on their way between two branches (a dispatched transfer has left the source shelf and not reached
    the destination yet — `stock_unit_services.holds_stock`'s reading). A device in transit is counted once, as a
    device; `in_transit_quantity` is the stock counted by quantity.
    """
    from .models import StockTransfer, StockTransferItem

    units = StockUnit.objects.filter(product_id=product.pk)
    return {
        'available_units': units.filter(status=StockUnit.Status.AVAILABLE).count(),
        'reserved_units': units.filter(status=StockUnit.Status.RESERVED).count(),
        'in_transit_units': units.filter(status=StockUnit.Status.IN_TRANSIT).count(),
        'stock': int(BranchStock.objects.filter(product_id=product.pk, quantity__gt=0)
                     .aggregate(n=Sum('quantity'))['n'] or 0),
        'in_transit_quantity': 0 if product.is_serialized else int(
            StockTransferItem.objects.filter(product_id=product.pk, transfer__status=StockTransfer.STATUS_IN_TRANSIT)
            .aggregate(n=Sum('quantity'))['n'] or 0),
    }


def _has_stock(counts) -> bool:
    return any(counts.values())


def check(product) -> dict:
    """GET …/deletion-check/: what the dialog explains before anything is done."""
    found = blockers(product)
    counts = stock_counts(product)
    is_variant = bool(product.product_model_id)
    return {
        'product_id': product.pk, 'name': product.name, 'is_variant': is_variant,
        'is_active': product.is_active, 'archived': product.is_archived,
        'archived_at': product.archived_at.isoformat() if product.archived_at else None,
        'archive_reason': product.archive_reason,
        'deletable': not found,
        'archivable': is_variant and not product.is_archived and not _has_stock(counts),
        'blockers': found,
        'stock': counts,
    }


def snapshot(product) -> dict:
    """The variant as it was, for the audit line: enough to know what was deleted or archived."""
    options = [
        {'attribute': link.attribute.name, 'option': link.option.label, 'option_id': link.option_id}
        for link in ProductVariantOption.objects.filter(product_id=product.pk)
        .select_related('attribute', 'option').order_by('attribute__sort_order', 'attribute__name', 'attribute_id')
    ]
    return {
        'product_id': product.pk, 'name': product.name, 'slug': product.slug,
        'model_id': product.product_model_id,
        'model_name': product.product_model.name if product.product_model_id else '',
        'condition_line': product.condition_line, 'variant_key': product.variant_key,
        'options': options, 'price': str(product.price),
        'is_active': product.is_active, 'is_published_online': product.is_published_online,
        'draft_origin': product.draft_origin,
        'codes': sorted(ProductBarcode.objects.filter(product_id=product.pk).values_list('code', flat=True)),
    }


def clean_reason(value) -> str:
    reason = value.strip() if isinstance(value, str) else ''
    if not REASON_MIN <= len(reason) <= REASON_MAX:
        raise LifecycleError(f'Escribe el motivo ({REASON_MIN} a {REASON_MAX} caracteres).',
                             code='reason_required', status=400, field='reason')
    return reason


def _locked(company, pk) -> Product:
    product = (Product.objects.select_for_update(of=('self',))
               .filter(company=company, pk=pk).select_related('product_model').first())
    if product is None:
        raise LifecycleError('No encontrado.', code='not_found', status=404)
    return product


def _variant(product) -> None:
    if not product.product_model_id:
        raise LifecycleError(f'«{product.name}» no es una variante de un modelo.', code='not_a_variant')


def delete_variant(*, company, pk, reason, confirm_name, actor, request=None) -> dict:
    """Delete a variant without history for good. Answers the audit snapshot; raises `LifecycleError`."""
    reason = clean_reason(reason)
    with transaction.atomic():
        product = _locked(company, pk)
        _variant(product)
        typed = confirm_name.strip() if isinstance(confirm_name, str) else ''
        if typed != product.name.strip():
            raise LifecycleError('Escribe el nombre exacto de la variante para confirmar.',
                                 code='confirm_name', status=400, field='confirm_name')
        found = blockers(product)   # again, under the lock
        if found:
            raise LifecycleError(
                f'«{product.name}» tiene historial o datos relacionados: no se puede eliminar. Archívala.',
                code='has_dependencies', blockers=found)
        before = snapshot(product)
        # Its own rows, explicitly and in this transaction (never by a cascade nobody looked at).
        ProductBarcode.objects.filter(product_id=product.pk).delete()
        ProductVariantOption.objects.filter(product_id=product.pk).delete()
        try:
            with transaction.atomic():
                product.delete()
        except ProtectedError:
            raise LifecycleError(
                f'«{product.name}» tiene datos relacionados: no se puede eliminar. Archívala.',
                code='has_dependencies', blockers=blockers(product)) from None
        AdminAuditLog.log(
            actor=actor, action='product_variant_deleted', target_type='product', target_id=before['product_id'],
            metadata={'reason': reason, 'variant': before}, request=request, company=company,
        )
    return before


def archive_variant(*, company, pk, reason, actor, request=None) -> Product:
    reason = clean_reason(reason)
    with transaction.atomic():
        product = _locked(company, pk)
        _variant(product)
        if product.is_archived:
            raise LifecycleError(f'«{product.name}» ya está archivada.', code='already_archived')
        counts = stock_counts(product)
        if _has_stock(counts):
            raise LifecycleError(
                f'«{product.name}» todavía tiene stock: trasládalo, véndelo o dalo de baja antes de archivarla.',
                code='stock_available', stock=counts)
        before = snapshot(product)
        product.archived_at = timezone.now()
        product.archived_by = actor if getattr(actor, 'is_authenticated', False) else None
        product.archive_reason = reason
        product.is_active = False
        product.save(update_fields=['archived_at', 'archived_by', 'archive_reason', 'is_active', 'updated_at'])
        AdminAuditLog.log(
            actor=actor, action='product_variant_archived', target_type='product', target_id=product.pk,
            metadata={'reason': reason, 'variant': before}, request=request, company=company,
        )
    return product


def unarchive_variant(*, company, pk, reason, actor, request=None) -> Product:
    reason = clean_reason(reason)
    with transaction.atomic():
        product = _locked(company, pk)
        if not product.is_archived:
            raise LifecycleError(f'«{product.name}» no está archivada.', code='not_archived')
        before = snapshot(product)
        previous = {'archived_at': product.archived_at.isoformat(), 'archive_reason': product.archive_reason,
                    'archived_by_id': product.archived_by_id}
        product.archived_at, product.archived_by, product.archive_reason = None, None, ''
        # It comes back INACTIVE: reactivating it is a separate, ordinary edit.
        product.save(update_fields=['archived_at', 'archived_by', 'archive_reason', 'updated_at'])
        AdminAuditLog.log(
            actor=actor, action='product_variant_unarchived', target_type='product', target_id=product.pk,
            metadata={'reason': reason, 'variant': before, 'archived': previous}, request=request, company=company,
        )
    return product


ARCHIVED_REFUSAL = ('«{name}» está archivada: no se usa en cargas, caja ni tienda. '
                    'Desarchívala en Catálogo › Modelos si vuelve a usarse.')


def archived_refusal(product) -> str | None:
    """The one sentence every importer, resolver and till says about an archived variant (None when it is not)."""
    if product is not None and product.archived_at is not None:
        return ARCHIVED_REFUSAL.format(name=product.name)
    return None
