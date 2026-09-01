"""
Point-of-sale and commercial analytics endpoints — Commercial Phase C1.

All INTERNAL CONTROL. There is no public counterpart and there will not be one:
these expose stock levels, turnover and per-branch performance.

AUTHORITY, and why it is split three ways
-----------------------------------------
    sales.pos.use          operate the till: look up an article, take money
    sales.analytics.view   see what the business earns
    inventory.reports      see the replenishment arithmetic behind the numbers

Someone ringing up a cable needs the first and neither of the others. Someone
reviewing the month needs the second. The person deciding what to reorder needs
the third. Collapsing them would mean a temp at the counter can read the
company's turnover, which is not a permission anybody asked to grant.

BRANCH IS VERIFIED, NEVER TRUSTED
---------------------------------
A till reports which branch it is standing in. That value selects among branches
the caller already reaches; it can never widen access. Stock is decremented from
that branch and no other — a shop with an empty shelf does not quietly take
units from the shop across town.
"""

from datetime import timedelta

from django.db.models import Count, DecimalField, F, Q, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from decimal import Decimal

from . import inventory_forecasting as forecasting
from . import inventory_services, pos_services
from .models import (
    AdminAuditLog,
    Order,
    OrderItem,
    PaymentMethod,
    Product,
    ProductBarcode,
    SalesChannel,
    StockMovement,
)
from .tenancy import (
    CrossTenantError,
    NoTenantError,
    has_capability,
    resolve_company_for_user,
    visible_branches,
)
from .throttles import AdminPosThrottle, AdminPosSaleThrottle, AdminSalesAnalyticsThrottle

CAP_POS = 'sales.pos.use'
CAP_ASSIGN_SELLER = 'sales.pos.assign_seller'
CAP_DISCOUNTS = 'sales.discounts.apply'
CAP_COMMISSIONS_VIEW = 'sales.commissions.view'
CAP_COMMISSIONS_MANAGE = 'sales.commissions.manage'
CAP_ANALYTICS = 'sales.analytics.view'
CAP_INVENTORY_REPORTS = 'inventory.reports'
CAP_PRODUCTS_MANAGE = 'products.manage'

_NOT_FOUND = 'No encontrado.'
_MAX_SEARCH = 25


def _context(request, capability):
    """Resolve the company for this request and authorise it."""
    raw = request.query_params.get('company')
    requested_id = None
    if raw not in (None, ''):
        try:
            requested_id = int(raw)
        except (TypeError, ValueError):
            return None, Response(
                {'detail': 'Parámetro "company" inválido.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
    try:
        company = resolve_company_for_user(request.user, requested_id)
    except CrossTenantError:
        return None, Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
    except NoTenantError as exc:
        return None, Response({'detail': str(exc)}, status=status.HTTP_403_FORBIDDEN)

    if not has_capability(request.user, company, capability):
        return None, Response(
            {'detail': 'No tienes permisos para esta operación.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return company, None


def _branch_or_error(request, company, raw_branch):
    try:
        return pos_services.resolve_pos_branch(request.user, company, raw_branch), None
    except pos_services.PosValidationError as exc:
        return None, Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


def _stock_row(branch, product):
    return inventory_services.branch_quantity(branch, product)


def _product_payload(product, branch, *, barcode=None):
    primary = (
        barcode
        or product.barcodes.filter(is_active=True, is_primary=True).first()
        or product.barcodes.filter(is_active=True).first()
    )
    return {
        'id': product.pk,
        'name': product.name,
        'price': str(product.price),
        'available': _stock_row(branch, product),
        'barcode': primary.code if primary else '',
    }


class AdminPosContextView(APIView):
    """
    GET /api/admin/pos/context/ — what this till may do, before it opens.

    Returns the branches the caller can actually sell from, so the UI never
    offers one the backend would then refuse.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminPosThrottle]

    def get(self, request):
        company, error = _context(request, CAP_POS)
        if error:
            return error

        branches = list(visible_branches(request.user, company).filter(is_active=True))
        default = None
        configured = company.default_inventory_branch
        if configured is not None and any(b.pk == configured.pk for b in branches):
            default = configured.pk
        elif len(branches) == 1:
            default = branches[0].pk
        # With several branches and no authorised default, the till asks. It
        # does NOT pick one: selling from the wrong shop moves real units.

        return Response({
            'company': {'id': company.pk, 'name': company.name},
            'branches': [{'id': b.pk, 'name': b.name} for b in branches],
            'default_branch': default,
            'payment_methods': [
                {'value': v, 'label': l}
                for v, l in PaymentMethod.choices
                # Stripe is the online channel's method; a counter cannot pick it.
                if v != PaymentMethod.STRIPE
            ],
            'can_manage_customers': has_capability(
                request.user, company, 'service.customers.manage',
            ),
            # The UI asks once, at open, rather than guessing per action. A
            # control that appears and then 403s is worse than one that is not
            # offered.
            'can_assign_seller': has_capability(request.user, company, CAP_ASSIGN_SELLER),
            'can_apply_discount': has_capability(request.user, company, CAP_DISCOUNTS),
            'can_view_commissions': has_capability(
                request.user, company, CAP_COMMISSIONS_VIEW,
            ),
            'seller': {
                'id': request.user.pk,
                'username': request.user.get_username(),
                'name': pos_services.seller_display_name(request.user),
            },
            'sellers': self._sellers(request, company),
        })

    def _sellers(self, request, company):
        """
        Who this sale may be credited to.

        Empty unless the caller may reassign — a list of colleagues is staffing
        information, and a cashier who cannot use it has no reason to receive it.
        """
        if not has_capability(request.user, company, CAP_ASSIGN_SELLER):
            return []
        # ELIGIBLE sellers only. Offering a stock clerk here would let a
        # supervisor pay commission to somebody who is not allowed to sell.
        return [
            {
                'id': m.user_id,
                'name': pos_services.seller_display_name(m.user) or m.user.get_username(),
            }
            for m in pos_services.eligible_pos_sellers(company)[:200]
        ]


class AdminPosLookupView(APIView):
    """
    GET /api/admin/pos/products/lookup/?code=...&branch=...

    The scanner's endpoint. One indexed query, scoped to the company.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminPosThrottle]

    def get(self, request):
        company, error = _context(request, CAP_POS)
        if error:
            return error
        branch, error = _branch_or_error(request, company, request.query_params.get('branch'))
        if error:
            return error

        from .models import normalize_barcode

        code = normalize_barcode(request.query_params.get('code', ''))
        if not code:
            return Response(
                {'detail': 'Indica un código.'}, status=status.HTTP_400_BAD_REQUEST,
            )

        entry = (
            ProductBarcode.objects
            .filter(company=company, code=code, is_active=True, product__is_active=True)
            .select_related('product')
            .first()
        )
        if entry is None:
            # A code belonging to another company answers exactly like one that
            # does not exist anywhere.
            return Response(
                {'detail': 'Código no encontrado en esta empresa.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(_product_payload(entry.product, branch, barcode=entry))


class AdminPosSearchView(APIView):
    """
    GET /api/admin/pos/products/search/?q=...&branch=...

    The fallback for an article whose label is torn, or a shop with no scanner.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminPosThrottle]

    def get(self, request):
        company, error = _context(request, CAP_POS)
        if error:
            return error
        branch, error = _branch_or_error(request, company, request.query_params.get('branch'))
        if error:
            return error

        term = (request.query_params.get('q') or '').strip()
        if len(term) < 2:
            return Response({'results': []})

        products = (
            Product.objects
            .filter(company=company, is_active=True)
            .filter(Q(name__icontains=term) | Q(barcodes__code__istartswith=term))
            .distinct()
            .prefetch_related('barcodes')[:_MAX_SEARCH]
        )
        return Response({
            'results': [_product_payload(p, branch) for p in products],
        })


class AdminPosSaleView(APIView):
    """
    POST /api/admin/pos/sales/ — complete a counter sale.

    Body: branch, items[{product, quantity}], customer?, payment_method,
    idempotency_key.

    Prices and totals are NOT accepted. The browser is told what to display; it
    is never asked what to charge.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminPosSaleThrottle]

    def post(self, request):
        company, error = _context(request, CAP_POS)
        if error:
            return error
        branch, error = _branch_or_error(request, company, request.data.get('branch'))
        if error:
            return error

        data = request.data
        try:
            order, created = pos_services.create_pos_sale(
                actor=request.user,
                company=company,
                branch=branch,
                items=data.get('items'),
                customer=data.get('customer'),
                seller_id=data.get('seller'),
                payment_method=data.get('payment_method', PaymentMethod.CASH),
                # NOT truncated here. `[:64]` would fold two distinct long keys
                # into one and answer the second sale with the first one's
                # order; the service validates and rejects instead.
                idempotency_key=data.get('idempotency_key'),
                terms_confirmed=data.get('terms_confirmed') is True,
                coupon_code=data.get('coupon_code', ''),
                manual_discount_type=data.get('manual_discount_type', ''),
                manual_discount_value=data.get('manual_discount_value'),
                discount_reason=data.get('discount_reason', ''),
                amount_received=data.get('amount_received'),
                payment_reference=data.get('payment_reference', ''),
                external_reference=data.get('external_reference', ''),
                sale_notes=data.get('sale_notes', ''),
                # Authority is resolved HERE, from the caller's capabilities —
                # never read from the request body.
                may_assign_seller=has_capability(request.user, company, CAP_ASSIGN_SELLER),
                may_apply_manual_discount=has_capability(
                    request.user, company, CAP_DISCOUNTS,
                ),
                request=request,
            )
        except pos_services.PosIdempotencyConflict as exc:
            # 409, not 400: the request is well-formed. The key has simply
            # already been spent on something else, and returning that other
            # sale would tell the caller their basket was sold when it was not.
            return Response(
                {
                    'detail': str(exc),
                    'existing_order': exc.existing_order.pk,
                },
                status=status.HTTP_409_CONFLICT,
            )
        except pos_services.PosPermissionError as exc:
            # 403, not 400: the request is fine, the caller is not allowed to
            # make it. A 400 would send an operator hunting for a typo.
            return Response({'detail': str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except pos_services.PosValidationError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except inventory_services.InsufficientStockError as exc:
            # The whole sale rolled back. Nothing was charged and nothing moved.
            payload = {'detail': str(exc), 'code': 'insufficient_stock'}
            other = self._other_branches_with_stock(request, company, branch)
            if other:
                payload['available_elsewhere'] = other
            return Response(payload, status=status.HTTP_409_CONFLICT)

        commission = getattr(order, 'sales_commission', None)
        return Response(
            {
                'order_id': order.pk,
                'created': created,
                'subtotal': str(order.total + order.discount_amount),
                'discount': str(order.discount_amount),
                'discount_source': order.discount_source,
                'discount_reason': order.discount_reason,
                'total': str(order.total),
                'paid_at': order.paid_at,
                'payment_method': order.payment_method,
                'amount_received': (
                    str(order.amount_received) if order.amount_received is not None else None
                ),
                'change_amount': (
                    str(order.change_amount) if order.change_amount is not None else None
                ),
                'payment_reference': order.payment_reference,
                'branch': {'id': branch.pk, 'name': branch.name},
                'seller': order.seller_name_snapshot,
                'customer': order.customer_name,
                # Only to someone allowed to see earnings. A cashier does not
                # need to know what the sale paid a colleague.
                'commission': (
                    str(commission.amount)
                    if commission and has_capability(
                        request.user, company, CAP_COMMISSIONS_VIEW,
                    )
                    else None
                ),
                'items': [
                    {
                        'product': i.product_id,
                        'name': i.product.name,
                        'quantity': i.quantity,
                        'price': str(i.price),
                    }
                    for i in order.items.select_related('product').all()
                ],
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def _other_branches_with_stock(self, request, company, branch):
        """
        Where the units actually are — shown only to someone allowed to see it.

        This informs; it moves nothing. Taking stock from another shop to cover
        a sale here is a transfer, and a transfer is a decision with paperwork.
        """
        if not has_capability(request.user, company, 'inventory.view'):
            return []
        try:
            product_ids = [
                int(i.get('product'))
                for i in (request.data.get('items') or [])
                if str(i.get('product', '')).isdigit()
            ]
        except (TypeError, ValueError):
            return []
        if not product_ids:
            return []

        others = visible_branches(request.user, company).exclude(pk=branch.pk)
        rows = (
            inventory_services.branch_stock_queryset(others)
            .filter(product_id__in=product_ids, quantity__gt=0)
            .select_related('branch', 'product')[:10]
        )
        return [
            {
                'branch': r.branch.name,
                'product': r.product.name,
                'quantity': r.quantity,
            }
            for r in rows
        ]


class AdminProductBarcodeView(APIView):
    """
    GET    /api/admin/products/{pk}/barcodes/   — `products.view`
    POST   /api/admin/products/{pk}/barcodes/   — `products.manage`
    DELETE /api/admin/products/{pk}/barcodes/?code=... — `products.manage`

    Barcodes are catalogue data, so they take the catalogue capability rather
    than a permission of their own. Inventing `barcodes.manage` would mean a
    role that can rename an article but not label it.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminPosThrottle]

    def _product(self, company, pk):
        return Product.objects.filter(company=company).filter(pk=pk).first()

    def _rows(self, product):
        return [
            {
                'id': b.pk,
                'code': b.code,
                'symbology': b.symbology,
                'symbology_label': b.get_symbology_display(),
                'is_primary': b.is_primary,
                'is_active': b.is_active,
            }
            for b in product.barcodes.all()
        ]

    def get(self, request, pk):
        company, error = _context(request, 'products.view')
        if error:
            return error
        product = self._product(company, pk)
        if product is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            'results': self._rows(product),
            'can_manage': has_capability(request.user, company, CAP_PRODUCTS_MANAGE),
        })

    def post(self, request, pk):
        from django.core.exceptions import ValidationError as DjangoValidationError
        from django.db import IntegrityError, transaction

        from .models import normalize_barcode

        company, error = _context(request, CAP_PRODUCTS_MANAGE)
        if error:
            return error
        product = self._product(company, pk)
        if product is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

        code = normalize_barcode(request.data.get('code', ''))
        symbology = request.data.get('symbology', ProductBarcode.UNKNOWN)
        make_primary = bool(request.data.get('is_primary'))

        if symbology not in dict(ProductBarcode.SYMBOLOGY_CHOICES):
            symbology = ProductBarcode.UNKNOWN

        # A code already used by ANOTHER article of this company is a conflict
        # worth naming: the operator has almost certainly scanned the wrong box.
        clash = (
            ProductBarcode.objects
            .filter(company=company, code=code)
            .exclude(product=product)
            .select_related('product')
            .first()
        )
        if clash is not None:
            return Response(
                {
                    'detail': (
                        f'Ese código ya identifica a "{clash.product.name}" en esta '
                        f'empresa.'
                    ),
                    'existing_product': clash.product_id,
                },
                status=status.HTTP_409_CONFLICT,
            )

        try:
            with transaction.atomic():
                if make_primary:
                    # At most one primary per product — demote the incumbent
                    # inside the same transaction as promoting the successor, so
                    # the constraint never sees two.
                    ProductBarcode.objects.filter(
                        product=product, is_primary=True,
                    ).update(is_primary=False)
                barcode = ProductBarcode(
                    company=company, product=product, code=code,
                    symbology=symbology, is_primary=make_primary,
                )
                barcode.save()
        except DjangoValidationError as exc:
            return Response(
                getattr(exc, 'message_dict', {'code': exc.messages}),
                status=status.HTTP_400_BAD_REQUEST,
            )
        except IntegrityError:
            return Response(
                {'detail': 'Ese código ya existe para este producto.'},
                status=status.HTTP_409_CONFLICT,
            )

        AdminAuditLog.log(
            actor=request.user, action='barcode_created',
            target_type='product_barcode', target_id=barcode.pk,
            metadata={
                'company_id': company.pk,
                'product_id': product.pk,
                'symbology': symbology,
            },
            request=request, company=company,
        )
        return Response(
            {'results': self._rows(product)}, status=status.HTTP_201_CREATED,
        )

    def patch(self, request, pk):
        """Activate, deactivate, or promote a code to primary."""
        from django.db import transaction

        company, error = _context(request, CAP_PRODUCTS_MANAGE)
        if error:
            return error
        product = self._product(company, pk)
        if product is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

        barcode = product.barcodes.filter(pk=request.data.get('id')).first()
        if barcode is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

        changed = []
        with transaction.atomic():
            if 'is_active' in request.data:
                barcode.is_active = bool(request.data['is_active'])
                changed.append('is_active')
            if request.data.get('is_primary'):
                ProductBarcode.objects.filter(
                    product=product, is_primary=True,
                ).exclude(pk=barcode.pk).update(is_primary=False)
                barcode.is_primary = True
                changed.append('is_primary')
            if changed:
                barcode.save()

        if changed:
            AdminAuditLog.log(
                actor=request.user, action='barcode_updated',
                target_type='product_barcode', target_id=barcode.pk,
                metadata={
                    'company_id': company.pk,
                    'product_id': product.pk,
                    'changed_fields': sorted(changed),
                },
                request=request, company=company,
            )
        return Response({'results': self._rows(product)})


class AdminPosPreviewView(APIView):
    """
    POST /api/admin/pos/preview/ — what this basket costs, before charging it.

    The operator has to read a total aloud before taking money, and that number
    must be the one the server will charge. So this runs the SAME resolution and
    the SAME arithmetic as the sale — the discount is validated, the permission
    is checked, the cash is checked — and then writes nothing.

    It moves no stock, creates no order and consumes no idempotency key. A
    preview that spent a key would make the sale that follows it a "retry".
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminPosThrottle]

    def post(self, request):
        company, error = _context(request, CAP_POS)
        if error:
            return error
        branch, error = _branch_or_error(request, company, request.data.get('branch'))
        if error:
            return error

        data = request.data
        try:
            priced = pos_services.build_pos_sale(
                operator=request.user,
                company=company,
                branch=branch,
                items=data.get('items'),
                customer=data.get('customer'),
                seller_id=data.get('seller'),
                payment_method=data.get('payment_method', PaymentMethod.CASH),
                coupon_code=data.get('coupon_code', ''),
                manual_discount_type=data.get('manual_discount_type', ''),
                manual_discount_value=data.get('manual_discount_value'),
                discount_reason=data.get('discount_reason', ''),
                # Cash is NOT validated in a preview: the operator is still
                # counting it out, and refusing to show the total until they
                # finish would defeat the purpose. The sale checks it.
                validate_cash=False,
                may_assign_seller=has_capability(request.user, company, CAP_ASSIGN_SELLER),
                may_apply_manual_discount=has_capability(
                    request.user, company, CAP_DISCOUNTS,
                ),
            )
        except pos_services.PosPermissionError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except pos_services.PosValidationError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        seller = priced['seller']
        may_see_commission = has_capability(request.user, company, CAP_COMMISSIONS_VIEW)
        return Response({
            'subtotal': str(priced['subtotal']),
            'discount': str(priced['discount_amount']),
            'discount_source': priced['discount']['source'],
            'coupon_code': priced['discount']['coupon_code'],
            # What fired on its own, so the till can name it on screen rather
            # than showing an unexplained reduction.
            'promotions': [
                {
                    'id': a['promotion'].pk,
                    'name': a['promotion'].name,
                    'applications': a['applications'],
                    'regular_amount': str(a['regular_amount']),
                    'discount_amount': str(a['discount_amount']),
                }
                for a in priced['promotions']['applied']
            ],
            'total': str(priced['total']),
            'seller': {
                'id': seller.pk if seller else None,
                'name': pos_services.seller_display_name(seller),
            },
            'customer': (
                {'id': priced['customer'].pk, 'name': priced['customer'].display_name}
                if priced['customer'] else None
            ),
            'commission': (
                {
                    'rate_percent': str(priced['commission']['rate']),
                    'base_amount': str(priced['commission']['base']),
                    'amount': str(priced['commission']['amount']),
                }
                if may_see_commission else None
            ),
            'lines': [
                {
                    'product': p.pk, 'name': p.name,
                    'quantity': q, 'price': str(u),
                }
                for p, q, u in priced['lines']
            ],
        })


class AdminCommissionSettingsView(APIView):
    """
    GET   /api/admin/sales/commission-settings/       — `sales.commissions.view`
    PATCH /api/admin/sales/commission-settings/{pk}/  — `sales.commissions.manage`

    The rate lives on the MEMBERSHIP, so this configures an employment rather
    than a person: the same human may sell for two companies on different terms,
    and neither company can see the other's deal.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminSalesAnalyticsThrottle]

    def get(self, request):
        from .models import Membership

        company, error = _context(request, CAP_COMMISSIONS_VIEW)
        if error:
            return error

        rows = (
            Membership.objects
            .filter(company=company, is_active=True)
            .select_related('user')
            .order_by('user__first_name', 'user__username')
        )
        return Response({
            'can_manage': has_capability(request.user, company, CAP_COMMISSIONS_MANAGE),
            'results': [
                {
                    'membership_id': m.pk,
                    'user_id': m.user_id,
                    'name': pos_services.seller_display_name(m.user) or m.user.get_username(),
                    'role': m.role,
                    'commission_rate_percent': str(m.commission_rate_percent),
                }
                for m in rows
            ],
        })

    def patch(self, request, pk):
        from decimal import Decimal, InvalidOperation

        from .models import Membership

        company, error = _context(request, CAP_COMMISSIONS_MANAGE)
        if error:
            return error

        # Scoped by walking DOWN from this company's memberships: another
        # tenant's membership id is not in the set being searched.
        membership = Membership.objects.filter(company=company, pk=pk).first()
        if membership is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

        try:
            rate = Decimal(str(request.data.get('commission_rate_percent'))).quantize(
                Decimal('0.01'),
            )
        except (InvalidOperation, TypeError, ValueError):
            return Response(
                {'commission_rate_percent': ['Porcentaje inválido.']},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not (Decimal('0') <= rate <= Decimal('100')):
            return Response(
                {'commission_rate_percent': ['Debe estar entre 0 y 100.']},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if membership.commission_rate_percent != rate:
            previous = membership.commission_rate_percent
            membership.commission_rate_percent = rate
            membership.save(update_fields=['commission_rate_percent', 'updated_at'])
            AdminAuditLog.log(
                actor=request.user,
                action='commission_rate_changed',
                target_type='membership',
                target_id=membership.pk,
                metadata={
                    'company_id': company.pk,
                    'membership_id': membership.pk,
                    # The rate is the term being agreed, not personal data, and
                    # a change to it is exactly what an auditor comes here for.
                    'from': str(previous),
                    'to': str(rate),
                },
                request=request,
                company=company,
            )
            # PAST SALES ARE NOT TOUCHED. Their commissions were frozen at the
            # rate agreed when they happened; recomputing them from today's rate
            # would rewrite a debt after the fact.

        return Response({
            'membership_id': membership.pk,
            'commission_rate_percent': str(membership.commission_rate_percent),
        })
