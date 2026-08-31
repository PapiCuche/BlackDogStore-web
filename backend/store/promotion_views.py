"""
Promotion and coupon administration — Commercial Phase C1.3.

TWO TABS, ONE SCREEN, TWO MODELS THAT STAY SEPARATE
---------------------------------------------------
`Promotion` fires automatically when a basket qualifies. `Coupon` fires when
somebody types a code. They are administered together because to a shopkeeper
they are both "discounts I set up", and they remain distinct models because
merging them would mean either every automatic promotion needs a code nobody
will type, or every coupon fires unasked.

AUTHORITY IS ITS OWN
--------------------
`sales.promotions.manage` is deliberately not implied by `products.manage`.
Editing a price tag and deciding that three articles together cost less than
their parts are different commercial decisions, and a business should be able to
grant one without the other.
"""

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import promotion_services
from .models import (
    AdminAuditLog,
    AppliedPromotion,
    Branch,
    Coupon,
    Product,
    Promotion,
    PromotionBranch,
    PromotionItem,
)
from .pos_views import _NOT_FOUND, _context
from .tenancy import has_capability
from .throttles import AdminSalesAnalyticsThrottle

CAP_VIEW = 'sales.promotions.view'
CAP_MANAGE = 'sales.promotions.manage'


def _promotion_payload(promotion, *, stats=None):
    return {
        'id': promotion.pk,
        'name': promotion.name,
        'promotion_type': promotion.promotion_type,
        'promotion_type_label': promotion.get_promotion_type_display(),
        'priority': promotion.priority,
        'is_active': promotion.is_active,
        'is_live': promotion.is_live(),
        'starts_at': promotion.starts_at,
        'ends_at': promotion.ends_at,
        'branch_scope': promotion.branch_scope,
        'branches': [
            {'id': b.branch_id, 'name': b.branch.name} for b in promotion.branches.all()
        ],
        'fixed_price': str(promotion.fixed_price) if promotion.fixed_price is not None else None,
        'discount_percent': (
            str(promotion.discount_percent) if promotion.discount_percent is not None else None
        ),
        'max_applications_per_order': promotion.max_applications_per_order,
        'items': [
            {
                'product': i.product_id,
                'product_name': i.product.name,
                'price': str(i.product.price),
                'quantity': i.quantity,
            }
            for i in promotion.items.all()
        ],
        'stats': stats or {},
    }


class AdminPromotionListView(APIView):
    """
    GET  /api/admin/sales/promotions/   — `sales.promotions.view`
    POST /api/admin/sales/promotions/   — `sales.promotions.manage`
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminSalesAnalyticsThrottle]

    def get(self, request):
        company, error = _context(request, CAP_VIEW)
        if error:
            return error

        promotions = (
            Promotion.objects
            .filter(company=company)
            .prefetch_related('items__product', 'branches__branch')
        )

        # §65: how often each has fired and what it has given away. Never a
        # "profitability" figure — the platform does not record cost, so any
        # such number would be invented.
        stats = {
            row['promotion']: row
            for row in AppliedPromotion.objects
            .filter(company=company)
            .values('promotion')
            .annotate(
                orders=Count('order', distinct=True),
                applications=Sum('applications'),
                discount=Sum('discount_amount'),
                regular=Sum('regular_amount'),
            )
        }

        results = []
        for promotion in promotions:
            row = stats.get(promotion.pk)
            results.append(_promotion_payload(promotion, stats={
                'orders': row['orders'] if row else 0,
                'applications': row['applications'] if row else 0,
                'discount_given': str(row['discount']) if row else '0.00',
                'regular_value': str(row['regular']) if row else '0.00',
            }))

        return Response({
            'can_manage': has_capability(request.user, company, CAP_MANAGE),
            'branches': [
                {'id': b.pk, 'name': b.name}
                for b in Branch.objects.filter(company=company, is_active=True)
            ],
            'results': results,
        })

    def post(self, request):
        company, error = _context(request, CAP_MANAGE)
        if error:
            return error
        return _write_promotion(request, company, promotion=None)


class AdminPromotionDetailView(APIView):
    """
    GET    /api/admin/sales/promotions/{pk}/ — `sales.promotions.view`
    PATCH  /api/admin/sales/promotions/{pk}/ — `sales.promotions.manage`

    There is no DELETE. A promotion that has been applied is part of the record
    of what customers were charged; `AppliedPromotion.promotion` is PROTECT and
    the database would refuse anyway. Retiring one is `is_active = false`.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminSalesAnalyticsThrottle]

    def _get(self, company, pk):
        return (
            Promotion.objects
            .filter(company=company).filter(pk=pk)
            .prefetch_related('items__product', 'branches__branch')
            .first()
        )

    def get(self, request, pk):
        company, error = _context(request, CAP_VIEW)
        if error:
            return error
        promotion = self._get(company, pk)
        if promotion is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        return Response(_promotion_payload(promotion))

    def patch(self, request, pk):
        company, error = _context(request, CAP_MANAGE)
        if error:
            return error
        promotion = self._get(company, pk)
        if promotion is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        return _write_promotion(request, company, promotion=promotion)


def _write_promotion(request, company, *, promotion):
    """Create or update, validating every tenant invariant on the way."""
    from django.core.exceptions import ValidationError as DjangoValidationError

    data = request.data
    creating = promotion is None
    errors = {}

    name = (data.get('name') or '').strip()
    if creating and not name:
        errors['name'] = ['Indica un nombre.']

    promotion_type = data.get('promotion_type') or (
        promotion.promotion_type if promotion else ''
    )
    if promotion_type not in dict(Promotion.TYPE_CHOICES):
        errors['promotion_type'] = ['Tipo de promoción inválido.']

    def _decimal(field):
        raw = data.get(field)
        if raw in (None, ''):
            return None
        try:
            return Decimal(str(raw)).quantize(Decimal('0.01'))
        except (InvalidOperation, TypeError, ValueError):
            errors[field] = ['Valor numérico inválido.']
            return None

    fixed_price = _decimal('fixed_price')
    discount_percent = _decimal('discount_percent')

    # --- components -------------------------------------------------------
    raw_items = data.get('items')
    components = None
    if raw_items is not None:
        components = []
        seen = set()
        for entry in raw_items if isinstance(raw_items, list) else []:
            try:
                product_id = int(entry.get('product'))
                quantity = int(entry.get('quantity', 1))
            except (TypeError, ValueError, AttributeError):
                errors['items'] = ['Componente inválido.']
                break
            if quantity < 1:
                errors['items'] = ['La cantidad de cada componente debe ser mayor que cero.']
                break
            if product_id in seen:
                errors['items'] = ['Un producto no puede repetirse en la promoción.']
                break
            seen.add(product_id)
            components.append((product_id, quantity))

        if components is not None and 'items' not in errors:
            # §60: at least two. A "combo" of one article is a price change
            # wearing a promotion's clothes, and it would be invisible to
            # anybody reading the price list.
            if len(components) < 2:
                errors['items'] = ['Un combo necesita al menos dos productos.']
            else:
                owned = set(
                    Product.objects
                    .filter(company=company, pk__in=[p for p, _q in components])
                    .values_list('pk', flat=True)
                )
                if owned != seen:
                    # Resolved by walking DOWN from the company: another
                    # tenant's product is simply not in the set.
                    errors['items'] = ['Algún producto no pertenece a esta empresa.']

    # --- branch scope -----------------------------------------------------
    branch_scope = data.get('branch_scope') or (
        promotion.branch_scope if promotion else Promotion.SCOPE_ALL
    )
    if branch_scope not in dict(Promotion.SCOPE_CHOICES):
        errors['branch_scope'] = ['Alcance inválido.']

    branch_ids = None
    if 'branches' in data:
        try:
            branch_ids = [int(b) for b in (data.get('branches') or [])]
        except (TypeError, ValueError):
            errors['branches'] = ['Sucursal inválida.']
        else:
            owned = set(
                Branch.objects.filter(company=company, pk__in=branch_ids)
                .values_list('pk', flat=True)
            )
            if set(branch_ids) != owned:
                errors['branches'] = ['Alguna sucursal no pertenece a esta empresa.']

    if errors:
        return Response(errors, status=status.HTTP_400_BAD_REQUEST)

    try:
        with transaction.atomic():
            if creating:
                promotion = Promotion(company=company, created_by=request.user)
            if name:
                promotion.name = name
            promotion.promotion_type = promotion_type
            if 'priority' in data:
                try:
                    promotion.priority = int(data.get('priority') or 0)
                except (TypeError, ValueError):
                    promotion.priority = 0
            if 'is_active' in data:
                promotion.is_active = bool(data.get('is_active'))
            if 'starts_at' in data:
                promotion.starts_at = data.get('starts_at') or None
            if 'ends_at' in data:
                promotion.ends_at = data.get('ends_at') or None
            if 'max_applications_per_order' in data:
                raw = data.get('max_applications_per_order')
                promotion.max_applications_per_order = int(raw) if raw else None

            promotion.branch_scope = branch_scope
            if promotion_type == Promotion.BUNDLE_FIXED_PRICE:
                promotion.fixed_price = fixed_price
                promotion.discount_percent = None
            else:
                promotion.discount_percent = discount_percent
                promotion.fixed_price = None

            promotion.save()

            if components is not None:
                promotion.items.all().delete()
                PromotionItem.objects.bulk_create([
                    PromotionItem(promotion=promotion, product_id=p, quantity=q)
                    for p, q in components
                ])
            if branch_ids is not None:
                promotion.branches.all().delete()
                PromotionBranch.objects.bulk_create([
                    PromotionBranch(promotion=promotion, branch_id=b)
                    for b in branch_ids
                ])
    except DjangoValidationError as exc:
        return Response(
            getattr(exc, 'message_dict', {'detail': exc.messages}),
            status=status.HTTP_400_BAD_REQUEST,
        )

    AdminAuditLog.log(
        actor=request.user,
        action='promotion_created' if creating else 'promotion_updated',
        target_type='promotion',
        target_id=promotion.pk,
        metadata={
            'company_id': company.pk,
            'promotion_id': promotion.pk,
            'promotion_type': promotion.promotion_type,
            'is_active': promotion.is_active,
        },
        request=request,
        company=company,
    )

    promotion = (
        Promotion.objects.filter(pk=promotion.pk)
        .prefetch_related('items__product', 'branches__branch')
        .first()
    )
    return Response(
        _promotion_payload(promotion),
        status=status.HTTP_201_CREATED if creating else status.HTTP_200_OK,
    )


class AdminCouponView(APIView):
    """
    GET   /api/admin/sales/coupons/      — `sales.promotions.view`
    POST  /api/admin/sales/coupons/      — `sales.promotions.manage`
    PATCH /api/admin/sales/coupons/{pk}/ — `sales.promotions.manage`

    The code-activated half of the same screen. `Coupon` already existed and
    already worked in checkout; this gives it the tenant-aware administration it
    never had, without inventing a second coupon model.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminSalesAnalyticsThrottle]

    def _payload(self, coupon):
        return {
            'id': coupon.pk,
            'code': coupon.code,
            'discount_percent': coupon.discount_percent,
            'is_active': coupon.is_active,
            'expires_at': coupon.expires_at,
            'is_expired': bool(coupon.expires_at and coupon.expires_at < timezone.now()),
        }

    def get(self, request):
        company, error = _context(request, CAP_VIEW)
        if error:
            return error
        return Response({
            'can_manage': has_capability(request.user, company, CAP_MANAGE),
            'results': [
                self._payload(c)
                for c in Coupon.objects.filter(company=company).order_by('code')
            ],
        })

    def post(self, request):
        company, error = _context(request, CAP_MANAGE)
        if error:
            return error

        code = (request.data.get('code') or '').strip().upper()
        if not code:
            return Response({'code': ['Indica un código.']},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            percent = int(request.data.get('discount_percent'))
        except (TypeError, ValueError):
            return Response({'discount_percent': ['Porcentaje inválido.']},
                            status=status.HTTP_400_BAD_REQUEST)
        if not (0 < percent <= 100):
            return Response({'discount_percent': ['Debe estar entre 1 y 100.']},
                            status=status.HTTP_400_BAD_REQUEST)

        if Coupon.objects.filter(company=company, code=code).exists():
            return Response(
                {'code': ['Ya existe un código igual en esta empresa.']},
                status=status.HTTP_409_CONFLICT,
            )

        coupon = Coupon.objects.create(
            company=company, code=code, discount_percent=percent,
            expires_at=request.data.get('expires_at') or None,
        )
        AdminAuditLog.log(
            actor=request.user, action='coupon_created', target_type='coupon',
            target_id=coupon.pk,
            metadata={'company_id': company.pk, 'coupon_id': coupon.pk},
            request=request, company=company,
        )
        return Response(self._payload(coupon), status=status.HTTP_201_CREATED)

    def patch(self, request, pk):
        company, error = _context(request, CAP_MANAGE)
        if error:
            return error
        coupon = Coupon.objects.filter(company=company, pk=pk).first()
        if coupon is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

        changed = []
        if 'is_active' in request.data:
            coupon.is_active = bool(request.data['is_active'])
            changed.append('is_active')
        if 'discount_percent' in request.data:
            try:
                percent = int(request.data['discount_percent'])
            except (TypeError, ValueError):
                return Response({'discount_percent': ['Porcentaje inválido.']},
                                status=status.HTTP_400_BAD_REQUEST)
            if not (0 < percent <= 100):
                return Response({'discount_percent': ['Debe estar entre 1 y 100.']},
                                status=status.HTTP_400_BAD_REQUEST)
            coupon.discount_percent = percent
            changed.append('discount_percent')
        if 'expires_at' in request.data:
            coupon.expires_at = request.data['expires_at'] or None
            changed.append('expires_at')

        if changed:
            coupon.save(update_fields=changed)
            AdminAuditLog.log(
                actor=request.user, action='coupon_updated', target_type='coupon',
                target_id=coupon.pk,
                metadata={
                    'company_id': company.pk, 'coupon_id': coupon.pk,
                    'changed_fields': sorted(changed),
                },
                request=request, company=company,
            )
        return Response(self._payload(coupon))


class AdminPosCombosView(APIView):
    """
    GET /api/admin/pos/combos/?branch= — combos this till could sell right now.

    Each carries how many complete sets the CURRENT stock allows, because a
    one-tap combo that then fails for want of a screen protector is worse than
    one that was never offered. A partial bundle is never sold.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminSalesAnalyticsThrottle]

    def get(self, request):
        from .pos_views import CAP_POS, _branch_or_error

        company, error = _context(request, CAP_POS)
        if error:
            return error
        branch, error = _branch_or_error(request, company, request.query_params.get('branch'))
        if error:
            return error

        return Response({
            'results': promotion_services.combo_availability(company, branch),
        })
