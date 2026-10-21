"""
Commercial analytics — Commercial Phase C1.

TWO SOURCES, AND THEY ARE NOT INTERCHANGEABLE
---------------------------------------------
    UNITS    come from `StockMovement.SALE_EXIT` — what physically left a shelf
             in a specific branch, on a specific day. This is what demand is.

    MONEY    comes from PAID `OrderItem` rows, using `OrderItem.price` — the
             price at the moment of sale. Recomputing revenue from
             `Product.price` would rewrite last quarter's takings every time
             somebody edits a price tag.

Mixing them is the mistake this file exists to avoid. A unit that left the shelf
as a warranty replacement is not revenue; a paid order awaiting collection is
revenue that has not yet moved stock.

WHAT IS NOT HERE
----------------
No margin, no profit, no ROI. The platform does not record what anything cost,
so any such number would be invented. Turnover is reported; earnings are not.
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import Avg, Case, Count, DecimalField, F, Q, Sum, When
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import inventory_forecasting as forecasting
from . import inventory_services
from .models import Order, OrderItem, SalesChannel, StockMovement
from .pos_views import _context
from .tenancy import has_capability, visible_branches
from .throttles import AdminSalesAnalyticsThrottle

CAP_ANALYTICS = 'sales.analytics.view'
CAP_INVENTORY_REPORTS = 'inventory.reports'
CAP_COSTS_VIEW = 'costs.view'

_MONEY = DecimalField(max_digits=14, decimal_places=2)
_WINDOWS = (7, 30, 90)


def _selected_branches(request, company):
    """
    The branches this request covers.

    `?branch=` narrows to one; without it, every branch the caller may see. A
    branch they may not see is never in the set, so the parameter can only ever
    narrow.
    """
    allowed = visible_branches(request.user, company)
    raw = request.query_params.get('branch')
    if raw in (None, '', 'all'):
        return list(allowed)
    try:
        branch_id = int(raw)
    except (TypeError, ValueError):
        return list(allowed)
    return list(allowed.filter(pk=branch_id))


def _company_tz(company):
    """
    The shop's own timezone, or the platform's when it has not set one.

    A company that never configured a timezone is not an error: it inherits the
    installation's, which for a single-country deployment is the right answer.
    A STORED timezone that no longer resolves is a configuration problem, and it
    also is not a reason to fail a dashboard — the platform default is used and
    the numbers stay computable.
    """
    tz_name = getattr(getattr(company, 'settings', None), 'timezone', '') or ''
    if tz_name:
        try:
            import zoneinfo

            return zoneinfo.ZoneInfo(tz_name)
        except Exception:
            pass
    return timezone.get_default_timezone()


def _company_today(company):
    """Today, as the shop reckons it."""
    return timezone.localdate(timezone.now(), _company_tz(company))


def _day_bounds(day: date, tz):
    """
    The UTC instants that bracket one LOCAL calendar day.

    WHY NOT `paid_at__date__gte=`
    -----------------------------
    Django renders `__date` using the CONNECTION's timezone — the platform's,
    or the database's. For a tenant in Tokyo asking a server configured for
    Lima, that is a fourteen-hour error: the morning's takings land on
    yesterday, and "today" on the dashboard is a day that has not started.

    Comparing raw timestamps against boundaries built IN THE TENANT'S ZONE has
    no such dependency. It is also faster — a plain range over an indexed
    column, rather than a per-row date conversion the index cannot serve.
    """
    start_local = datetime.combine(day, time.min, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local, end_local


def _paid_orders(company, branches, since=None, *, tz=None, until=None):
    """
    Paid orders of this company, optionally bracketed by LOCAL calendar days.

    The window is expressed as UTC instants derived from the tenant's own
    timezone — never as `paid_at__date`, which silently uses the connection's.
    """
    qs = Order.objects.filter(company=company, status=Order.Status.PAID)
    ids = [b.pk for b in branches]
    if ids:
        qs = qs.filter(fulfillment_branch_id__in=ids)
    else:
        return qs.none()
    if since is not None:
        tz = tz or timezone.get_default_timezone()
        start, _ = _day_bounds(since, tz)
        qs = qs.filter(paid_at__gte=start)
        if until is not None:
            _, end = _day_bounds(until, tz)
            qs = qs.filter(paid_at__lt=end)
    return qs


def _window(qs, field, since, until, tz):
    start, _ = _day_bounds(since, tz)
    _, end = _day_bounds(until, tz)
    return qs.filter(**{f'{field}__gte': start, f'{field}__lt': end})


def _collections(company, branches, since, until, tz):
    """
    COMMERCE-ADMIN-UX-01 — the money, as opposed to the sales.

        live             PAID orders, by `paid_at`: the sales themselves.
        cancelled_paid   orders cancelled AFTER they were paid (web or till),
                         by `paid_at`: money taken that is not a sale any more.
        late_charges     charges filed as duplicates on a CANCELLED order, by
                         when they were confirmed: money taken all the same.
                         Not those made with TEST keys (review P3), exactly as
                         `order_refunds.collected` leaves them out.
        gross            the three together.
        refunds          `OrderRefund`: CONFIRMED by `confirmed_at` (they
                         leave `net`), PENDING by `created_at` (said, not
                         subtracted: the money is still here).
        net              gross − confirmed refunds.
        cancelled_held   what is still held for the cancelled orders above:
                         their money minus every refund of theirs confirmed.

    Each order counted once, on the day it was paid; a refund on the day it
    was confirmed. No sale is counted here twice: `live` is the sales figure.
    """
    from .models import OrderRefund, PaymentTransaction
    from .order_refunds import TEST_ENVIRONMENT

    ids = [b.pk for b in branches]
    orders = Order.objects.filter(company=company, fulfillment_branch_id__in=ids)
    money = Coalesce(Sum('total', output_field=_MONEY), Decimal('0.00'), output_field=_MONEY)
    live = _window(orders.filter(status=Order.Status.PAID), 'paid_at', since, until, tz).aggregate(
        n=Count('pk'), amount=money)
    cancelled_qs = _window(orders.filter(status=Order.Status.CANCELLED, paid=True), 'paid_at', since, until, tz)
    cancelled = cancelled_qs.aggregate(n=Count('pk'), amount=money)
    late_qs = _window(
        PaymentTransaction.objects.filter(
            order__in=orders.filter(status=Order.Status.CANCELLED),
            status=PaymentTransaction.Status.DUPLICATE,
        ).exclude(environment=TEST_ENVIRONMENT),
        'confirmed_at', since, until, tz)
    late = late_qs.aggregate(
        n=Count('pk'), amount=Coalesce(Sum('amount', output_field=_MONEY), Decimal('0.00'), output_field=_MONEY))
    refund_money = Coalesce(Sum('amount', output_field=_MONEY), Decimal('0.00'), output_field=_MONEY)
    company_refunds = OrderRefund.objects.filter(company=company, order__fulfillment_branch_id__in=ids)
    confirmed = _window(company_refunds.filter(status=OrderRefund.STATUS_CONFIRMED), 'confirmed_at',
                        since, until, tz).aggregate(amount=refund_money)['amount']
    pending = _window(company_refunds.filter(status=OrderRefund.STATUS_PENDING), 'created_at',
                      since, until, tz).aggregate(amount=refund_money)['amount']
    # Held for the cancellations of this window: their money (paid + late
    # charges, whenever those came) minus their refunds confirmed, whenever.
    held_orders = list(cancelled_qs.values_list('pk', flat=True)) + list(
        late_qs.values_list('order_id', flat=True))
    held_ids = set(held_orders)
    held_paid = orders.filter(pk__in=held_ids, paid=True).aggregate(amount=money)['amount']
    held_late = PaymentTransaction.objects.filter(
        order_id__in=held_ids, status=PaymentTransaction.Status.DUPLICATE,
    ).exclude(environment=TEST_ENVIRONMENT).aggregate(amount=Coalesce(Sum('amount', output_field=_MONEY), Decimal('0.00'), output_field=_MONEY))['amount']
    held_back = company_refunds.filter(order_id__in=held_ids, status=OrderRefund.STATUS_CONFIRMED).aggregate(
        amount=refund_money)['amount']
    gross = live['amount'] + cancelled['amount'] + late['amount']
    return {
        'live': {'orders': live['n'], 'amount': str(_cents(live['amount']))},
        'cancelled_paid': {'orders': cancelled['n'], 'amount': str(_cents(cancelled['amount']))},
        'late_charges': {'count': late['n'], 'amount': str(_cents(late['amount']))},
        'gross': str(_cents(gross)),
        'refunds_confirmed': str(_cents(confirmed)),
        'refunds_pending': str(_cents(pending)),
        'net': str(_cents(gross - confirmed)),
        'cancelled_held': str(_cents(held_paid + held_late - held_back)),
        # Every order cancelled in the window, paid or not: the history's count.
        'cancelled_orders': _window(
            orders.filter(status=Order.Status.CANCELLED), 'cancellation__created_at', since, until, tz,
        ).count(),
    }


class AdminSalesDashboardView(APIView):
    """
    GET /api/admin/sales/dashboard/ — commercial summary.

    `sales.analytics.view`. The replenishment block additionally requires
    `inventory.reports`, and is omitted rather than refused when it is absent:
    one locked section must not cost the other five.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminSalesAnalyticsThrottle]

    def get(self, request):
        company, error = _context(request, CAP_ANALYTICS)
        if error:
            return error

        branches = _selected_branches(request, company)
        tz = _company_tz(company)
        today = _company_today(company)

        return Response({
            'company': {'id': company.pk, 'name': company.name},
            'branches': [{'id': b.pk, 'name': b.name} for b in branches],
            'today': today.isoformat(),
            'timezone': str(tz),
            'kpis': self._kpis(company, branches, today, tz),
            # COMMERCE-ADMIN-UX-01: money collected, refunded and still held,
            # apart from the sales (`kpis`, which count PAID orders only).
            'collections': {
                ('today' if window == 1 else f'last_{window}d'): _collections(
                    company, branches, today - timedelta(days=window - 1), today, tz)
                for window in (1, *_WINDOWS)
            },
            'channels': self._channels(company, branches, today, tz),
            'trend': self._trend(company, branches, today, tz),
            'top_products': self._top_products(company, branches, today, tz),
            'stock_alerts': self._stock_alerts(request, company, branches),
        })

    # -- money ------------------------------------------------------------

    def _kpis(self, company, branches, today, tz):
        """
        Turnover and volume. PAID only — an abandoned checkout is not a sale.
        """
        out = {}
        for window in (1, *_WINDOWS):
            since = today - timedelta(days=window - 1)
            agg = _paid_orders(company, branches, since, tz=tz, until=today).aggregate(
                revenue=Coalesce(Sum('total', output_field=_MONEY), Decimal('0.00'), output_field=_MONEY),
                orders=Count('pk'),
                ticket=Coalesce(Avg('total', output_field=_MONEY), Decimal('0.00'), output_field=_MONEY),
            )
            units = OrderItem.objects.filter(
                order__in=_paid_orders(company, branches, since, tz=tz, until=today)
            ).aggregate(u=Coalesce(Sum('quantity'), 0))['u']
            key = 'today' if window == 1 else f'last_{window}d'
            out[key] = {
                'revenue': str(Decimal(agg['revenue']).quantize(Decimal('0.01'))),
                'orders': agg['orders'],
                'units': units,
                'average_ticket': str(Decimal(agg['ticket']).quantize(Decimal('0.01'))),
            }
        return out

    def _channels(self, company, branches, today, tz, window=30):
        """
        POS versus online, reported separately for each metric.

        Orders, revenue and units are kept apart on purpose: a single blended
        "sales" figure hides that the counter sells many cheap articles while
        the site sells few expensive ones, which is the comparison a shop
        actually wants.
        """
        since = today - timedelta(days=window - 1)
        base = _paid_orders(company, branches, since, tz=tz, until=today)
        rows = (
            base.values('sales_channel')
            .annotate(
                orders=Count('pk'),
                revenue=Coalesce(Sum('total', output_field=_MONEY), Decimal('0.00'), output_field=_MONEY),
            )
            .order_by()
        )
        units = {
            r['order__sales_channel']: r['u']
            for r in OrderItem.objects.filter(order__in=base)
            .values('order__sales_channel')
            .annotate(u=Coalesce(Sum('quantity'), 0))
            .order_by()
        }
        by_channel = {
            channel: {'orders': 0, 'revenue': '0.00', 'units': 0}
            for channel, _label in SalesChannel.choices
        }
        for row in rows:
            by_channel[row['sales_channel']] = {
                'orders': row['orders'],
                'revenue': str(Decimal(row['revenue']).quantize(Decimal('0.01'))),
                'units': units.get(row['sales_channel'], 0),
            }
        return {'window_days': window, 'by_channel': by_channel}

    def _trend(self, company, branches, today, tz, window=30):
        """Daily revenue, with days of no trade present as zero."""
        since = today - timedelta(days=window - 1)
        rows = (
            _paid_orders(company, branches, since, tz=tz, until=today)
            # TruncDate in the TENANT's zone. Without the tzinfo argument this
            # buckets by the connection's timezone, so a shop in Tokyo would see
            # its evening trade attributed to the following day.
            .annotate(day=TruncDate('paid_at', tzinfo=tz))
            .values('day')
            .annotate(
                revenue=Coalesce(Sum('total', output_field=_MONEY), Decimal('0.00'), output_field=_MONEY),
                orders=Count('pk'),
            )
            .order_by('day')
        )
        by_day = {}
        for r in rows:
            day = r['day']
            if hasattr(day, 'date'):
                day = day.date()
            by_day[day] = r
        series = []
        for offset in range(window):
            day = since + timedelta(days=offset)
            row = by_day.get(day)
            series.append({
                'date': day.isoformat(),
                'revenue': str(Decimal(row['revenue']).quantize(Decimal('0.01'))) if row else '0.00',
                'orders': row['orders'] if row else 0,
            })
        return series

    # -- units ------------------------------------------------------------

    def _top_products(self, company, branches, today, tz, window=30, limit=10):
        """
        Best sellers, ranked by units that PHYSICALLY left the shelf.

        Units from SALE_EXIT, revenue from paid order lines, each from its own
        source — and each row carries the stock context that makes the ranking
        actionable rather than merely interesting.
        """
        since = today - timedelta(days=window - 1)
        branch_ids = [b.pk for b in branches]
        if not branch_ids:
            return {'window_days': window, 'results': []}

        window_start, _ = _day_bounds(since, tz)
        _, window_end = _day_bounds(today, tz)
        units_rows = (
            StockMovement.objects
            .filter(
                branch_id__in=branch_ids,
                movement_type=StockMovement.SALE_EXIT,
                created_at__gte=window_start,
                created_at__lt=window_end,
            )
            # COMMERCE-ADMIN-UX-01: a cancelled sale's goods came back — it
            # is not demand. An exit with no order is kept.
            .exclude(order__status=Order.Status.CANCELLED)
            .values('product_id', 'product__name')
            .annotate(units=Coalesce(Sum('quantity'), 0))
            .order_by('-units', 'product__name')[:limit]
        )
        results = list(units_rows)
        if not results:
            return {'window_days': window, 'results': []}

        product_ids = [r['product_id'] for r in results]

        revenue = {
            r['product_id']: r['revenue']
            for r in OrderItem.objects
            .filter(
                order__in=_paid_orders(company, branches, since, tz=tz, until=today),
                product_id__in=product_ids,
            )
            .values('product_id')
            .annotate(revenue=Coalesce(
                Sum(F('quantity') * F('price'), output_field=_MONEY),
                Decimal('0.00'), output_field=_MONEY,
            ))
            .order_by()
        }

        stock = {}
        for row in (
            inventory_services.branch_stock_queryset(branches)
            .filter(product_id__in=product_ids)
        ):
            stock[row.product_id] = stock.get(row.product_id, 0) + row.quantity

        demand = forecasting.collect_demand(branch_ids, today=today, tz=tz)

        # WHEN EACH ARTICLE STARTED BEING STOCKED, per product, across the
        # branches in view. Without this the aggregate coverage would start at
        # the product's FIRST SALE and delete every genuine zero before it —
        # the same defect `_history_start` was carrying, arriving by a different
        # door. The earliest BranchStock row is when this company began tracking
        # the article, which is the honest lower bound.
        tracked = {}
        for row in (
            inventory_services.branch_stock_queryset(branches)
            .filter(product_id__in=product_ids)
        ):
            if row.created_at is None:
                continue
            day = row.created_at.date()
            current = tracked.get(row.product_id)
            if current is None or day < current:
                tracked[row.product_id] = day

        coverage = {}
        for pid in product_ids:
            merged = forecasting.DemandSeries()
            for (b_id, p_id), series in demand.items():
                if p_id != pid:
                    continue
                for day, units in series.daily.items():
                    merged.daily[day] = merged.daily.get(day, 0) + units
                if merged.first_observed is None or (
                    series.first_observed and series.first_observed < merged.first_observed
                ):
                    merged.first_observed = series.first_observed
            f = forecasting.forecast_for(
                merged, today=today, tracked_since=tracked.get(pid),
            )
            on_hand = stock.get(pid, 0)
            coverage[pid] = (
                round(on_hand / f['daily'], 1)
                if f['sufficient'] and f['daily'] > 0 else None
            )

        return {
            'window_days': window,
            'results': [
                {
                    'product_id': r['product_id'],
                    'product_name': r['product__name'],
                    'units_sold': r['units'],
                    'revenue': str(Decimal(revenue.get(r['product_id'], Decimal('0.00'))).quantize(Decimal('0.01'))),
                    'current_stock': stock.get(r['product_id'], 0),
                    'days_of_cover': coverage.get(r['product_id']),
                }
                for r in results
            ],
        }

    # -- stock ------------------------------------------------------------

    def _stock_alerts(self, request, company, branches):
        """Counts only. The detail lives behind `inventory.reports`."""
        if not branches:
            return None
        rows = inventory_services.branch_stock_queryset(branches)
        return {
            'out_of_stock': rows.filter(quantity=0).count(),
            'low': rows.filter(minimum_stock__gt=0, quantity__gt=0)
                       .filter(quantity__lte=F('minimum_stock')).count(),
        }


class AdminSalesMarginView(APIView):
    """
    GET /api/admin/sales/margin/?branch=&days=7|30|90 — DEVICE-COMMERCE-01 · DC1.

    `sales.analytics.view` AND `costs.view`: seeing turnover is not seeing what
    the goods cost, and the reverse is not either.

    WHAT IS COUNTED. PAID orders by `paid_at` in the shop's own days — the rule
    of the rest of this dashboard. A pending, failed, expired or cancelled
    order is not a sale; a REFUNDED one is reported apart, as a refund. A paid
    order cancelled afterwards is out of every figure and listed apart
    (`cancellations`); the money is in `collected` (COMMERCE-ADMIN-UX-01).

    THE MARGIN IS ONLY OVER WHAT HAS A COST. `margin = net sales of the lines
    whose cost is known − that cost`. Subtracting the known cost from ALL net
    sales would count every sale of unknown cost as 100 % margin; those are
    reported apart instead («costo pendiente»), with their amount.

    Every figure comes from the line snapshots (`OrderItem.net_amount`,
    `unit_cost`) and the order's frozen breakdown — never from the catalogue.

    OWNER'S DECISION OF 2026-10-15. margin % = (net sales without IGV −
    comparable net cost) / net sales × 100, «comparable» = the lines with a
    known cost; amount and %; ESTIMATED (every known cost: average, device,
    reference, agreed with the supplier) vs REALISED (`REALISED`: confirmed
    costs only); unknown costs (`unknown_cost`) and refunds (`refunds`, with
    and without IGV) apart. There is no cost adjustment of goods sold to show:
    the history is never recomputed (DC1). Never «utilidad neta».

    OWNER'S DECISION D2 (SUPPLIER-MARGIN). An on-order line is ESTIMATED at its
    agreed cost (`unit_cost`) and REALISED at the real cost frozen when it was
    received (`received_unit_cost`); one without a real cost is listed apart
    as `real_cost_pending` (lines, orders, net sales) and is never realised.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminSalesAnalyticsThrottle]

    def get(self, request):
        company, error = _context(request, CAP_ANALYTICS)
        if error:
            return error
        from .cost_services import user_may_view_costs

        # THE one answer to «may this person see costs» (DC1 review).
        if not user_may_view_costs(request.user, company):
            return Response(
                {'detail': 'Se requiere permiso para ver costos y márgenes.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            days = int(request.query_params.get('days') or 30)
        except (TypeError, ValueError):
            days = 30
        if days not in _WINDOWS:
            days = 30

        branches = _selected_branches(request, company)
        tz = _company_tz(company)
        today = _company_today(company)
        since = today - timedelta(days=days - 1)
        paid = _paid_orders(company, branches, since, tz=tz, until=today)

        gross = Decimal('0.00')
        net = Decimal('0.00')
        orders = 0
        from .tax_services import breakdown_for_order

        for order in paid.only('pk', 'total', 'taxable_amount', 'tax_rate', 'subtotal_amount',
                               'discount_amount', 'tax_amount', 'currency', 'tax_treatment', 'company'):
            orders += 1
            gross += order.total
            net += breakdown_for_order(order).taxable_amount

        lines = OrderItem.objects.filter(order__in=paid)
        known = lines.filter(unit_cost__isnull=False)
        line_cost = F('unit_cost') * F('quantity')
        totals = known.aggregate(
            net=Coalesce(Sum('net_amount', output_field=_MONEY), Decimal('0.00'), output_field=_MONEY),
            cost=Coalesce(Sum(line_cost, output_field=_COST), Decimal('0'), output_field=_COST),
        )
        pending = lines.filter(unit_cost__isnull=True)
        estimated = self._estimated_nets(lines)

        refunded = Order.objects.filter(
            company=company, status=Order.Status.REFUNDED,
            fulfillment_branch__in=[b.pk for b in branches],
            paid_at__gte=_day_bounds(since, tz)[0], paid_at__lt=_day_bounds(today, tz)[1],
        )
        refunds = refunded.aggregate(
            orders=Count('pk'),
            amount=Coalesce(Sum('total', output_field=_MONEY), Decimal('0.00'), output_field=_MONEY),
        )
        refunds_net = sum(
            (breakdown_for_order(order).taxable_amount for order in refunded.only(
                'pk', 'total', 'taxable_amount', 'tax_rate', 'subtotal_amount', 'discount_amount',
                'tax_amount', 'currency', 'tax_treatment', 'company')),
            Decimal('0.00'),
        )

        known_net = _cents(totals['net'])
        cost = _cents(totals['cost'])
        # SUPPLIER-MARGIN (owner's decision D2): the realised margin of an
        # on-order line is at the REAL cost frozen when it was received; the
        # estimate keeps its agreed cost. Without a real cost the line is
        # «costo real pendiente», listed apart and never realised.
        realised = self._figures(known.filter(self.REALISED), cost=self.REALISED_COST)
        collections = _collections(company, branches, since, today, tz)
        estimated_only = known.exclude(self.REALISED)
        # A FAILED line will never be received: it is not «pending», it goes to
        # the refund flow (its order is cancelled or refunded in time). A line
        # «por confirmar» may still be received: pending, counted apart.
        real_pending = lines.filter(supply_origin=OrderItem.SUPPLY_SUPPLIER, received_unit_cost__isnull=True).exclude(
            supply_status=OrderItem.SUPPLY_FAILED)
        real_pending_net = real_pending.aggregate(
            net=Coalesce(Sum('net_amount', output_field=_MONEY), Decimal('0.00'), output_field=_MONEY))['net']
        return Response({
            'window_days': days,
            'label': 'Margen comercial estimado',
            'sales': {'orders': orders, 'gross': str(_cents(gross)), 'net': str(_cents(net))},
            # COMMERCE-ADMIN-UX-01: the money, apart from the sales —
            # `amount` is the NET collected (live sales + money still held for
            # cancellations − refunds confirmed); `orders` the live ones.
            'collected': {**collections, 'orders': orders, 'amount': collections['net']},
            # Cancelled after being paid: out of every figure above, listed
            # here from their own snapshots (never rewritten).
            'cancellations': self._cancellations(company, branches, since, today, tz),
            'net_sales_with_cost': str(known_net),
            'cost_of_goods_sold': str(cost),
            'margin': str(known_net - cost),
            'margin_percent': _percent(known_net - cost, known_net),
            'pending_cost': {
                'lines': pending.count(),
                'orders': pending.values('order_id').distinct().count(),
                'amount': str(_cents(net) - known_net),
            },
            'reference_cost_lines': known.filter(cost_source=OrderItem.COST_REFERENCE).count(),
            # OWNER'S DECISION OF 2026-10-15 — estimated vs realised, unknown
            # costs and refunds apart. Both margins are (net sales without IGV
            # − comparable net cost) / net sales × 100 over the lines whose
            # cost is known; «estimated» counts every known cost, «realised»
            # only a confirmed one (own stock sold, or on order and received).
            'estimated': {'lines': known.count(), 'net_sales': str(known_net), 'cost': str(cost),
                          'margin': str(known_net - cost), 'margin_percent': _percent(known_net - cost, known_net)},
            'realised': realised,
            # D2: on-order lines without a real cost yet — not received, or
            # received before the real cost was recorded (never backfilled).
            'real_cost_pending': {
                'lines': real_pending.count(),
                'orders': real_pending.values('order_id').distinct().count(),
                'net_sales': str(_cents(real_pending_net)),
                'not_received_lines': real_pending.filter(supply_status=OrderItem.SUPPLY_PENDING).count(),
                'to_confirm_lines': real_pending.filter(supply_status=OrderItem.SUPPLY_TO_CONFIRM).count(),
                'received_without_real_cost_lines': real_pending.filter(
                    supply_status=OrderItem.SUPPLY_RECEIVED).count(),
            },
            'estimated_only': {
                'lines': estimated_only.count(),
                'net_sales': str(known_net - Decimal(realised['net_sales'])),
                'reference_cost_lines': estimated_only.filter(cost_source=OrderItem.COST_REFERENCE).count(),
                'on_order_not_received_lines': estimated_only.filter(
                    supply_origin=OrderItem.SUPPLY_SUPPLIER).exclude(supply_status=OrderItem.SUPPLY_RECEIVED).count(),
                # D2: received, but no real cost recorded yet.
                'on_order_without_real_cost_lines': estimated_only.filter(
                    supply_origin=OrderItem.SUPPLY_SUPPLIER, supply_status=OrderItem.SUPPLY_RECEIVED).count(),
            },
            'unknown_cost': {
                'lines': pending.count(),
                'orders': pending.values('order_id').distinct().count(),
                'net_sales': str(_cents(net) - known_net),
            },
            'refunds': {'orders': refunds['orders'], 'amount': str(_cents(refunds['amount'])),
                        'net': str(_cents(refunds_net))},
            'by_product': self._grouped(lines, 'product_id', 'product__name', estimated),
            'by_branch': self._grouped(lines, 'order__fulfillment_branch_id', 'order__fulfillment_branch__name',
                                       estimated),
            'by_channel': self._grouped(lines, 'order__sales_channel', None, estimated),
        })

    @staticmethod
    def _cancellations(company, branches, since, today, tz) -> dict:
        """
        Orders cancelled after being paid, by `paid_at` like the sales: what
        they sold (gross), their lines' frozen net and cost (the snapshots stay
        as they were) and the refunds confirmed for them. Their stock came back
        with its own Kardex lines; their margin is not a margin of anything.
        """
        from .models import OrderRefund

        cancelled = _window(
            Order.objects.filter(company=company, fulfillment_branch_id__in=[b.pk for b in branches],
                                 status=Order.Status.CANCELLED, paid=True),
            'paid_at', since, today, tz)
        lines = OrderItem.objects.filter(order__in=cancelled)
        figures = lines.aggregate(
            units=Coalesce(Sum('quantity'), 0),
            net=Coalesce(Sum('net_amount', output_field=_MONEY), Decimal('0.00'), output_field=_MONEY),
            cost=Coalesce(Sum(F('unit_cost') * F('quantity'), filter=Q(unit_cost__isnull=False), output_field=_COST),
                          Decimal('0'), output_field=_COST),
        )
        gross = cancelled.aggregate(
            amount=Coalesce(Sum('total', output_field=_MONEY), Decimal('0.00'), output_field=_MONEY))['amount']
        refund_money = Coalesce(Sum('amount', output_field=_MONEY), Decimal('0.00'), output_field=_MONEY)
        refunded = OrderRefund.objects.filter(order__in=cancelled, status=OrderRefund.STATUS_CONFIRMED).aggregate(
            amount=refund_money)['amount']
        # FINANCIAL-CLOSURE: owed back but not given back yet — said, never subtracted.
        owed = OrderRefund.objects.filter(order__in=cancelled, status=OrderRefund.STATUS_PENDING).aggregate(
            amount=refund_money)['amount']
        return {
            'orders': cancelled.count(),
            'units': figures['units'],
            'gross': str(_cents(gross)),
            'net': str(_cents(figures['net'])),
            'cost': str(_cents(figures['cost'])),
            'stock_restored_orders': cancelled.filter(cancellation__stock_restored=True).count(),
            'refunds_confirmed': str(_cents(refunded)),
            'refunds_pending': str(_cents(owed)),
        }

    #: A CONFIRMED cost (owner, 2026-10-15): what left our own shelf at its
    #: average or its device's own cost, or an on-order line whose goods were
    #: received WITH a real cost recorded (owner's decision D2). A reference
    #: cost, an agreed cost of goods not received yet, or a line received
    #: before its real cost was recorded, is only an estimate.
    REALISED = (
        Q(supply_origin=OrderItem.SUPPLY_OWN, cost_source__in=(OrderItem.COST_AVERAGE, OrderItem.COST_UNIT))
        | Q(supply_origin=OrderItem.SUPPLY_SUPPLIER, supply_status=OrderItem.SUPPLY_RECEIVED,
            received_unit_cost__isnull=False)
    )
    #: D2: the realised cost of a line — an own line's snapshot, an on-order
    #: line's real cost at reception (never its agreed `unit_cost`).
    REALISED_COST = Case(
        When(supply_origin=OrderItem.SUPPLY_SUPPLIER, then=F('received_unit_cost')),
        default=F('unit_cost'), output_field=DecimalField(max_digits=14, decimal_places=4),
    )

    @staticmethod
    def _figures(lines, cost=None) -> dict:
        """Lines, net sales, cost, margin and margin % of lines that all have a cost."""
        unit = cost if cost is not None else F('unit_cost')
        totals = lines.aggregate(
            net=Coalesce(Sum('net_amount', output_field=_MONEY), Decimal('0.00'), output_field=_MONEY),
            cost=Coalesce(Sum(unit * F('quantity'), output_field=_COST), Decimal('0'),
                          output_field=_COST),
        )
        net, cost = _cents(totals['net']), _cents(totals['cost'])
        return {'lines': lines.count(), 'net_sales': str(net), 'cost': str(cost),
                'margin': str(net - cost), 'margin_percent': _percent(net - cost, net)}

    @staticmethod
    def _estimated_nets(lines) -> list[dict]:
        """
        DC1 review — lines sold before DC1 have no frozen net. Their share of
        their order's base is computed here, the same way the snapshot would
        have been (`margin_services.line_nets`), NOT stored, and only for the
        breakdowns: they stay «costo pendiente» and never count in the margin.
        """
        from . import margin_services

        orders = {}
        for item in lines.filter(net_amount__isnull=True).select_related('order'):
            orders.setdefault(item.order_id, item.order)
        out = []
        for order in orders.values():
            items = list(OrderItem.objects.filter(order=order).order_by('pk').values_list('pk', flat=True))
            rows = list(OrderItem.objects.filter(pk__in=items).select_related('product', 'order__fulfillment_branch'))
            nets = margin_services.line_nets(order, rows)
            for item in rows:
                if item.net_amount is not None:
                    continue
                out.append({
                    'product_id': item.product_id, 'product__name': item.product.name,
                    'order__fulfillment_branch_id': order.fulfillment_branch_id,
                    'order__fulfillment_branch__name': (
                        order.fulfillment_branch.name if order.fulfillment_branch_id else None
                    ),
                    'order__sales_channel': order.sales_channel,
                    'net': nets[item.pk][2],
                })
        return out

    @staticmethod
    def _grouped(lines, key, name, estimated=()):
        fields = [key] + ([name] if name else [])
        known = Q(unit_cost__isnull=False)
        rows = (
            lines.values(*fields)
            .annotate(
                units=Coalesce(Sum('quantity'), 0),
                net_all=Coalesce(Sum('net_amount', output_field=_MONEY), Decimal('0.00'), output_field=_MONEY),
                net_known=Coalesce(Sum('net_amount', filter=known, output_field=_MONEY),
                                   Decimal('0.00'), output_field=_MONEY),
                cost=Coalesce(Sum(F('unit_cost') * F('quantity'), filter=known, output_field=_COST),
                              Decimal('0'), output_field=_COST),
                pending=Count('pk', filter=Q(unit_cost__isnull=True)),
            )
            .order_by()
        )
        labels = dict(SalesChannel.choices)
        guessed: dict = {}
        for line in estimated:
            guessed[line[key]] = guessed.get(line[key], Decimal('0.00')) + line['net']
        out = []
        for row in rows:
            net_known = _cents(row['net_known'])
            cost = _cents(row['cost'])
            extra = guessed.get(row[key], Decimal('0.00'))
            out.append({
                'key': row[key],
                'name': row[name] if name else str(labels.get(row[key], row[key])),
                'units': row['units'],
                'net_sales': str(_cents(row['net_all']) + extra),
                'cost': str(cost),
                'margin': str(net_known - cost),
                'margin_percent': _percent(net_known - cost, net_known),
                'pending_lines': row['pending'],
                # Part of `net_sales` came from lines sold before DC1, computed
                # from their order: those lines are «costo pendiente».
                'net_estimated': row[key] in guessed,
            })
        out.sort(key=lambda r: (-Decimal(r['net_sales']), str(r['name'])))
        return out


_COST = DecimalField(max_digits=20, decimal_places=4)


def _cents(value) -> Decimal:
    from .tax_services import money

    return money(Decimal(value))


def _percent(part: Decimal, whole: Decimal):
    """Two decimals, or None when there is nothing to divide by — never a made-up 0 %."""
    if not whole:
        return None
    from .tax_services import money

    return str(money(part * Decimal('100') / whole))


class AdminSalesReplenishmentView(APIView):
    """
    GET /api/admin/sales/replenishment/ — the forecast table.

    NAMED FOR ITS MODULE, not for what it returns. Phase 2D already has an
    `AdminReplenishmentView` at `/api/admin/inventory/replenishment/`, and the
    first version of this class reused that name: both were imported into
    `urls.py`, this one shadowed the older one, and the INVENTORY endpoint
    quietly started demanding a sales capability it had never needed.

    A test from Phase 2D caught it. The lesson is in the name.

    Requires BOTH `sales.analytics.view` and `inventory.reports`: it exposes
    demand, coverage and reorder arithmetic, which is inventory information
    rather than commercial performance.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminSalesAnalyticsThrottle]

    def get(self, request):
        company, error = _context(request, CAP_ANALYTICS)
        if error:
            return error
        if not has_capability(request.user, company, CAP_INVENTORY_REPORTS):
            return Response(
                {'detail': 'Se requiere permiso de reportes de inventario.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        branches = _selected_branches(request, company)
        tz = _company_tz(company)
        today = _company_today(company)
        rows = forecasting.build_replenishment_report(branches, today=today, tz=tz)

        risk = request.query_params.get('risk')
        if risk:
            rows = [r for r in rows if r['risk'] == risk]

        # Transfer opportunities, computed only for rows that need units and
        # only across branches this caller may see.
        needing = [r for r in rows if r['suggested_quantity'] > 0][:40]
        if needing:
            # Collected once for every source branch, rather than per row.
            source_demand = forecasting.collect_demand(branches, today=today, tz=tz)
            for row in needing:
                row['transfer_options'] = self._transfer_options(
                    branches, row['product_id'], row['branch_id'],
                    demand=source_demand, today=today,
                )

        return Response({
            'today': today.isoformat(),
            'branches': [{'id': b.pk, 'name': b.name} for b in branches],
            'method': {
                'formula': 'forecast = 0.50·avg7 + 0.30·avg30 + 0.20·avg90',
                'demand_source': 'StockMovement.SALE_EXIT',
                'note': (
                    'Los días sin ventas cuentan como cero. El pronóstico es una '
                    'estimación explicable, no una predicción garantizada.'
                ),
            },
            'results': rows,
        })

    def _transfer_options(self, branches, product_id, exclude_branch_id, *,
                          demand=None, today=None):
        """
        Branches that could spare units — a suggestion, never an action.

        The SOURCE branch is measured with the same arithmetic as the
        destination: its own demand, its own lead time, its own reorder point.
        Anything less would solve one shortage by opening another in a shop
        nobody was looking at.
        """
        options = []
        rows = (
            inventory_services.branch_stock_queryset(branches)
            .filter(product_id=product_id, quantity__gt=0)
            .exclude(branch_id=exclude_branch_id)
            .select_related('branch')
        )
        for row in rows:
            source_forecast = None
            if demand is not None and today is not None:
                series = demand.get(
                    (row.branch_id, row.product_id), forecasting.DemandSeries(),
                )
                source_forecast = forecasting.forecast_for(
                    series, today=today,
                    tracked_since=row.created_at.date() if row.created_at else None,
                )
            surplus = forecasting.surplus_for_transfer(
                row, forecast=source_forecast, today=today,
            )
            if surplus > 0:
                options.append({
                    'branch_id': row.branch_id,
                    'branch_name': row.branch.name,
                    'quantity': row.quantity,
                    'can_transfer': surplus,
                })
        return sorted(options, key=lambda o: -o['can_transfer'])[:3]


class AdminCommissionsView(APIView):
    """
    GET /api/admin/sales/commissions/ — what has been earned, by whom.

    HISTORICAL COMMISSIONS COME FROM THE LEDGER, never from today's rate times
    yesterday's sales. That distinction is the whole reason `SalesCommission`
    exists: a seller moved from 3% to 5% is owed 3% on everything sold before
    the change, and computing the total from the current rate would silently
    rewrite what the company agreed to pay.

    FINANCIAL-CLOSURE (owner, 2026-10-16). Per seller, by the day the row was
    written (so a past window recomputes the same, except an accrued row that
    a later annulment voids — it was never owed):
        accrued      owed, not yet paid out;
        settled      paid out (`commission_services.settle`); kept even when
                     the sale is annulled later — that sale then counts in
                     `annulled_sales` / `annulled_net_amount`, not in `sales`;
        voided       annulled before being paid out — shown, never counted;
        recoverable  the compensating adjustments of settled commissions whose
                     sale was annulled, by the day of the annulment (negative:
                     «ajuste por anulación / saldo a recuperar»);
        commission   accrued + settled + recoverable — what the seller keeps.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminSalesAnalyticsThrottle]

    def get(self, request):
        from .models import CommissionAdjustment, Membership, SalesCommission
        from .pos_views import CAP_COMMISSIONS_VIEW

        company, error = _context(request, CAP_COMMISSIONS_VIEW)
        if error:
            return error

        branches = _selected_branches(request, company)
        tz = _company_tz(company)
        today = _company_today(company)

        try:
            window = max(1, min(int(request.query_params.get('days', 30)), 365))
        except (TypeError, ValueError):
            window = 30
        since = today - timedelta(days=window - 1)
        start, _ = _day_bounds(since, tz)
        _, end = _day_bounds(today, tz)

        branch_ids = {b.pk for b in branches}
        rows = (
            SalesCommission.objects
            .filter(company=company, created_at__gte=start, created_at__lt=end,
                    order__fulfillment_branch_id__in=branch_ids)
            .select_related('order')
        )
        adjustments = (
            CommissionAdjustment.objects
            .filter(company=company, created_at__gte=start, created_at__lt=end,
                    commission__order__fulfillment_branch_id__in=branch_ids)
            .select_related('commission')
        )

        zero = Decimal('0.00')
        by_seller = {}

        def entry_for(seller_id, seller_name):
            # Keyed by the SNAPSHOT, so a seller whose account was later removed
            # still appears with what they earned.
            return by_seller.setdefault((seller_id, seller_name), {
                'seller_id': seller_id,
                'seller_name': seller_name or '—',
                'sales': 0,
                'net_amount': zero,
                'accrued': zero,
                'settled': zero,
                'voided': zero,
                'voided_sales': 0,
                'annulled_sales': 0,
                'annulled_net_amount': zero,
                'recoverable': zero,
            })

        for row in rows:
            entry = entry_for(row.seller_id, row.seller_name_snapshot)
            if row.status == SalesCommission.STATUS_VOIDED:
                entry['voided'] += row.amount
                entry['voided_sales'] += 1
                continue
            if row.order.status == Order.Status.CANCELLED:
                # Review P2: settled, then its sale was annulled. The commission
                # stays settled (paid out); the sale is not a current sale.
                entry['annulled_sales'] += 1
                entry['annulled_net_amount'] += row.base_amount
            else:
                entry['sales'] += 1
                entry['net_amount'] += row.base_amount
            entry[row.status if row.status == SalesCommission.STATUS_SETTLED else 'accrued'] += row.amount
        for adjustment in adjustments:
            entry_for(adjustment.seller_id, adjustment.seller_name_snapshot)['recoverable'] += adjustment.amount

        # The CURRENT rate, shown beside the historical total rather than used
        # to compute it.
        current_rates = {
            m.user_id: str(m.commission_rate_percent)
            for m in Membership.objects.filter(company=company, is_active=True)
        }

        def cents(value):
            return str(value.quantize(Decimal('0.01')))

        results = sorted(
            (
                {
                    **entry,
                    'net_amount': cents(entry['net_amount']),
                    'annulled_net_amount': cents(entry['annulled_net_amount']),
                    'accrued': cents(entry['accrued']),
                    'settled': cents(entry['settled']),
                    'voided': cents(entry['voided']),
                    'recoverable': cents(entry['recoverable']),
                    'commission': cents(entry['accrued'] + entry['settled'] + entry['recoverable']),
                    'current_rate_percent': current_rates.get(entry['seller_id']),
                }
                for entry in by_seller.values()
            ),
            key=lambda r: Decimal(r['commission']),
            reverse=True,
        )

        def total(key):
            return cents(sum((Decimal(r[key]) for r in results), zero))

        return Response({
            'window_days': window,
            'today': today.isoformat(),
            'branches': [{'id': b.pk, 'name': b.name} for b in branches],
            'total_commission': total('commission'),
            'totals': {key: total(key) for key in ('accrued', 'settled', 'voided', 'recoverable')},
            'note': (
                'Las comisiones históricas salen del registro de cada venta, no '
                'de la tasa actual. Cambiar la tasa no reescribe lo ya devengado. '
                'Una comisión liquidada de una venta anulada no se borra: queda su '
                'ajuste por anulación (saldo a recuperar).'
            ),
            'results': results,
            'adjustments': [
                {
                    'id': adjustment.pk,
                    'order_id': adjustment.commission.order_id,
                    'commission_id': adjustment.commission_id,
                    'seller_name': adjustment.seller_name_snapshot or '—',
                    'amount': cents(adjustment.amount),
                    'kind': adjustment.kind,
                    'label': adjustment.get_kind_display(),
                    'created_at': adjustment.created_at.isoformat(),
                }
                for adjustment in adjustments.order_by('-created_at', '-pk')
            ],
        })
