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

from django.db.models import Avg, Count, DecimalField, F, Q, Sum
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
