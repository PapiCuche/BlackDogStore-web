"""
What «may be published» means, and the bulk change of that one fact.
INVENTORY-UNIFIED-01.

    ONE CATALOGUE. «On the web» is a FACT about a product, not a second list.

`Product.is_published_online` already says whether a product is offered in the
public shop (PRODUCT-DESTINATION-01). This module adds two things and no data:

  · ONE definition of what a product needs before it is worth publishing
    (`publication_problems`), so the list, the bulk action and the reports say
    the same thing about the same product;
  · the bulk change (`set_visibility`): many products, one transaction, one
    field.

THE RULE GATES WHAT PUBLISHES MANY AT ONCE, NOT THE PRODUCT FORM
---------------------------------------------------------------
Somebody editing ONE product sees what they are doing and may have a reason to
publish it before it is ready. Somebody ticking «all 600 results», or loading
a file of 600 rows, does not see each one: the bulk action and the product
load leave out what is not eligible and say which and why. Hiding is always
allowed: hiding shows nothing to anybody.

WHAT IS ASKED (owner's decision, 2026-10-13): active, a price, a category.
A PICTURE ONLY IF THE COMPANY ASKS FOR ONE
(`CompanySettings.publication_requires_image`, off as created).

Nothing here touches `is_active`, a price, a quantity, a device or the Kardex.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from .models import CompanySettings, Product, ProductImage

#: code -> what the panel says. The ORDER is the order problems are reported in.
PROBLEM_LABELS = {
    'inactive': 'El producto está inactivo.',
    'no_price': 'No tiene precio de venta.',
    'no_image': 'No tiene imagen.',
    'no_category': 'No tiene categoría.',
}

#: Ids in one request, and results of one «all that match». Above it the
#: request is refused with the count: one transaction holds every row locked.
MAX_BULK_PRODUCTS = 2000

#: How many of the changed ids the audit line lists. It always carries the
#: full COUNT; the list is there to find a product, not to be the record.
AUDIT_IDS_LIMIT = 200


class BulkVisibilityError(Exception):
    """The batch was refused WHOLE. Nothing was written."""


class UnknownProducts(BulkVisibilityError):
    """An id that is not a product of this company — another's, or nobody's."""


class TooManyProducts(BulkVisibilityError):
    def __init__(self, count: int):
        super().__init__(count)
        self.count = count
        self.limit = MAX_BULK_PRODUCTS


class SelectionChanged(BulkVisibilityError):
    """«All that match» no longer matches what the operator counted."""

    def __init__(self, current_count: int):
        super().__init__(current_count)
        self.current_count = current_count


def with_gallery_flag(queryset):
    """`has_gallery` on every row, so a page of products asks about pictures once."""
    return queryset.annotate(
        has_gallery=Exists(ProductImage.objects.filter(product_id=OuterRef('pk'))),
    )


def requires_image(company) -> bool:
    """
    Whether `company` (or its id) asks for a picture before publishing in bulk.

    One query. A company with no settings row asks for nothing. A caller with
    many products in hand asks ONCE and passes the answer on.
    """
    company_id = getattr(company, 'pk', company)
    return CompanySettings.objects.filter(company_id=company_id, publication_requires_image=True).exists()


def publication_problems(product, *, has_gallery=None, require_image=None) -> list[str]:
    """
    The codes of what `product` lacks to be published in bulk. Empty = ready.

    `require_image` is the company's own rule (`requires_image`): given by a
    caller that already asked, read here otherwise. Without it a picture is
    not looked for at all.

    `has_gallery` is taken from the argument, then from the `with_gallery_flag`
    annotation, and only then asked of the database — one product in hand costs
    one query, a page of them costs none.
    """
    if require_image is None:
        require_image = requires_image(product.company_id)
    has_image = bool(product.image_url)
    if require_image and not has_image:
        if has_gallery is None:
            has_gallery = getattr(product, 'has_gallery', None)
        if has_gallery is None:
            has_gallery = ProductImage.objects.filter(product_id=product.pk).exists()
        has_image = bool(has_gallery)
    return problems_of(
        is_active=product.is_active, price=product.price, has_image=has_image,
        has_category=product.category_id is not None, require_image=require_image,
    )


def problems_of(*, is_active, price, has_image, has_category, require_image) -> list[str]:
    """
    The same rule over plain facts, for a caller that knows the state a product
    WILL be left in rather than the one it is in (the product load).
    """
    problems = []
    if not is_active:
        problems.append('inactive')
    if price is None or price <= 0:
        problems.append('no_price')
    if require_image and not has_image:
        problems.append('no_image')
    if not has_category:
        problems.append('no_category')
    return problems


def ready_to_publish(queryset, company):
    """
    ECOM-READY-D1 — «Listos para publicar»: hidden products that `publication_problems`
    would find nothing to say about. The same rule as that function, as a query, so the
    list, its count and «Publicar listos» (which re-evaluates it) agree with the row.

    `queryset` is already of `company`; `company` only supplies its picture rule.
    """
    ready = queryset.filter(is_published_online=False, is_active=True, price__gt=0, category__isnull=False)
    if requires_image(company):
        ready = ready.filter(
            ~Q(image_url='') | Exists(ProductImage.objects.filter(product_id=OuterRef('pk')))
        )
    return ready


@transaction.atomic
def set_visibility(*, company, publish: bool, ids=None, select=None, expected_count=None) -> dict:
    """
    Publish or hide many products of `company`. All of the batch or none of it.

    Exactly one of:
      · `ids`    — the products ticked on the screen;
      · `select` — «all that match»: a callable that narrows a queryset of this
                   company's products (the list's own filter), with the
                   `expected_count` the operator was shown.

    Returns `{'changed', 'unchanged', 'refused', 'total', 'changed_ids'}`.
    `refused` is only ever non-empty when publishing: `[{id, name, problems}]`.

    LOCKED IN ID ORDER. Two batches over the same products each take the rows
    lowest id first, so one waits for the other instead of each holding what
    the other needs. The product form locks this same row, so a save and a
    batch take turns too.

    ONLY `is_published_online` AND `updated_at` ARE WRITTEN, with an UPDATE that
    names them — never `save()`, which writes the whole row back.
    """
    base = Product.objects.filter(company=company)

    if select is not None:
        # READ ONCE. The selection is the ids of ONE statement, and it is that
        # set which is compared with what the operator counted. A count and
        # then a second read of the ids let a product that began to match in
        # between be changed without ever having been counted.
        found = list(select(base).order_by().values_list('pk', flat=True)[:MAX_BULK_PRODUCTS + 1])
        if len(found) > MAX_BULK_PRODUCTS:
            # Past the cap nothing is loaded; the full count is only for the message.
            raise TooManyProducts(select(base).count())
        wanted = set(found)
        if len(wanted) != expected_count:
            raise SelectionChanged(len(wanted))
    else:
        wanted = set(ids)
        if len(wanted) > MAX_BULK_PRODUCTS:
            raise TooManyProducts(len(wanted))

    locked = list(base.select_for_update(of=('self',)).filter(pk__in=wanted).order_by('pk'))

    if select is None:
        # Another company's id is not in `base`: it is missing exactly like an
        # id nobody ever used, and the answer does not tell them apart.
        if len(locked) != len(wanted):
            raise UnknownProducts()
    else:
        # Asked again WITH the rows held: what was counted a moment ago may
        # have been edited before the lock was granted.
        still = set(select(base.filter(pk__in=wanted)).order_by().values_list('pk', flat=True))
        if still != wanted or len(locked) != len(wanted):
            raise SelectionChanged(select(base).count())

    pictured, require_image = set(), False
    if publish:
        require_image = requires_image(company)
    if publish and require_image:
        pictured = set(
            ProductImage.objects.filter(product_id__in=[p.pk for p in locked]).values_list('product_id', flat=True)
        )

    to_change, refused, unchanged = [], [], 0
    for product in locked:
        if product.is_published_online == publish:
            unchanged += 1
            continue
        if publish:
            problems = publication_problems(
                product, has_gallery=product.pk in pictured, require_image=require_image)
            if problems:
                refused.append({'id': product.pk, 'name': product.name, 'problems': problems})
                continue
        to_change.append(product.pk)

    if to_change:
        Product.objects.filter(pk__in=to_change).update(is_published_online=publish, updated_at=timezone.now())

    return {
        'changed': len(to_change), 'unchanged': unchanged, 'refused': refused,
        'total': len(locked), 'changed_ids': to_change,
    }
