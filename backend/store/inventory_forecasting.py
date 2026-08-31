"""
Demand forecasting and replenishment arithmetic — Commercial Phase C1.

WHAT THIS IS
------------
A small, explainable estimate of how fast each article leaves each shop, and
what that implies about restocking it. Every number it produces can be derived
by hand from data an operator can see.

WHAT THIS IS NOT
----------------
It is not machine learning and it is not called "AI" anywhere in the product.
Nothing here is seasonal, nothing is trained, and nothing is opaque. A shopkeeper
who disagrees with a suggestion must be able to find out why in one screen, and
a weighted average of three windows is a thing one can argue with. A model that
merely asserts a number is not.

DEMAND IS SALES, NOT SHRINKAGE
------------------------------
Demand comes exclusively from `StockMovement.SALE_EXIT`. Stock also leaves via
manual exits, damage, corrections, service and transfers — all of them reduce
what is on the shelf, and none of them is a customer wanting the article.
Counting a breakage as demand would order a replacement for something nobody
bought.

DAYS WITH NO SALES ARE PART OF THE DATA
---------------------------------------
The single most common way to get this wrong is to average only the days that
had sales. Four days with 2, 0, 0, 2 is one unit a day, not two. Omitting the
zeros inflates every downstream number — coverage shrinks, reorder points grow,
and the shop is told to buy stock it does not need.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.db.models import Count, Sum
from django.db.models.functions import TruncDate

from .models import BranchStock, StockMovement

# --- windows ---------------------------------------------------------------
SHORT_WINDOW = 7
MEDIUM_WINDOW = 30
LONG_WINDOW = 90

# Weighted towards the recent past: the short window reacts to a change in what
# people are buying, the long one keeps a single busy afternoon from dominating.
WEIGHT_SHORT = 0.50
WEIGHT_MEDIUM = 0.30
WEIGHT_LONG = 0.20

# --- data sufficiency ------------------------------------------------------
MIN_HISTORY_DAYS = 14
MIN_SELLING_DAYS = 3

CONFIDENCE_HIGH_DAYS, CONFIDENCE_HIGH_SELLING = 60, 12
CONFIDENCE_MEDIUM_DAYS, CONFIDENCE_MEDIUM_SELLING = 30, 6

# --- statuses --------------------------------------------------------------
CONFIDENCE_HIGH = 'high'
CONFIDENCE_MEDIUM = 'medium'
CONFIDENCE_LOW = 'low'
INSUFFICIENT_DATA = 'insufficient_data'

TREND_UP = 'up'
TREND_STABLE = 'stable'
TREND_DOWN = 'down'
TREND_UNKNOWN = 'unknown'
TREND_TOLERANCE = 0.20

RISK_OUT_OF_STOCK = 'out_of_stock'
RISK_CRITICAL = 'critical'
RISK_REORDER = 'reorder'
RISK_LOW = 'low'
RISK_OK = 'ok'
RISK_INSUFFICIENT_DATA = 'insufficient_data'

# Most severe first — the order the replenishment table sorts by.
RISK_ORDER = [
    RISK_OUT_OF_STOCK,
    RISK_CRITICAL,
    RISK_REORDER,
    RISK_LOW,
    RISK_INSUFFICIENT_DATA,
    RISK_OK,
]

CONFIGURATION_REQUIRED = 'configuration_required'


@dataclass
class DemandSeries:
    """Daily SALE_EXIT units for one (branch, product), zeros included."""

    daily: dict[date, int] = field(default_factory=dict)
    first_observed: date | None = None

    def units_in(self, since: date, until: date) -> int:
        return sum(
            units for day, units in self.daily.items() if since <= day <= until
        )

    def selling_days_in(self, since: date, until: date) -> int:
        return sum(
            1 for day, units in self.daily.items()
            if since <= day <= until and units > 0
        )


def collect_demand(branches, *, today: date, days: int = LONG_WINDOW):
    """
    Daily sale-exit units per (branch, product) over the window, in ONE query.

    Aggregated in the database and grouped in memory. The shape that must be
    avoided is a query per product per day: for a few hundred articles across a
    handful of branches that is tens of thousands of round trips to render one
    table.

    Returns `{(branch_id, product_id): DemandSeries}`.
    """
    branch_ids = [getattr(b, 'pk', b) for b in branches]
    if not branch_ids:
        return {}

    since = today - timedelta(days=days - 1)

    rows = (
        StockMovement.objects
        .filter(
            branch_id__in=branch_ids,
            movement_type=StockMovement.SALE_EXIT,
            created_at__date__gte=since,
            created_at__date__lte=today,
        )
        .annotate(day=TruncDate('created_at'))
        .values('branch_id', 'product_id', 'day')
        .annotate(units=Sum('quantity'))
        .order_by()
    )

    series: dict[tuple[int, int], DemandSeries] = {}
    for row in rows:
        key = (row['branch_id'], row['product_id'])
        day = row['day']
        if hasattr(day, 'date'):
            day = day.date()
        entry = series.setdefault(key, DemandSeries())
        entry.daily[day] = entry.daily.get(day, 0) + int(row['units'] or 0)
        if entry.first_observed is None or day < entry.first_observed:
            entry.first_observed = day
    return series


def _history_start(series: DemandSeries, *, today: date, tracked_since: date | None):
    """
    The first day this article could plausibly have sold here.

    A product stocked three days ago has three days of history, not ninety.
    Padding the earlier eighty-seven with zeros would divide its real sales by
    thirty and report a brand-new bestseller as barely moving.
    """
    window_start = today - timedelta(days=LONG_WINDOW - 1)
    candidates = [window_start]
    if tracked_since is not None:
        candidates.append(max(tracked_since, window_start))
    if series.first_observed is not None:
        candidates.append(max(series.first_observed, window_start))
    return max(candidates)


def _window_average(series: DemandSeries, *, today: date, window: int, start: date):
    """
    Mean daily units over `window`, counting every calendar day INCLUDING zeros.

    Returns `(average, days_counted)`. Days before `start` are not counted at
    all rather than counted as zero — see `_history_start`.
    """
    since = max(today - timedelta(days=window - 1), start)
    days = (today - since).days + 1
    if days <= 0:
        return 0.0, 0
    return series.units_in(since, today) / days, days


def forecast_for(series: DemandSeries, *, today: date, tracked_since: date | None = None):
    """
    The demand estimate for one (branch, product).

        forecast = 0.50·avg7 + 0.30·avg30 + 0.20·avg90

    Each average counts real calendar days, zeros included, and never reaches
    back before the article existed here.
    """
    start = _history_start(series, today=today, tracked_since=tracked_since)
    history_days = (today - start).days + 1
    selling_days = series.selling_days_in(start, today)

    avg7, _ = _window_average(series, today=today, window=SHORT_WINDOW, start=start)
    avg30, _ = _window_average(series, today=today, window=MEDIUM_WINDOW, start=start)
    avg90, _ = _window_average(series, today=today, window=LONG_WINDOW, start=start)

    sufficient = history_days >= MIN_HISTORY_DAYS and selling_days >= MIN_SELLING_DAYS

    if not sufficient:
        confidence = INSUFFICIENT_DATA
    elif history_days >= CONFIDENCE_HIGH_DAYS and selling_days >= CONFIDENCE_HIGH_SELLING:
        confidence = CONFIDENCE_HIGH
    elif history_days >= CONFIDENCE_MEDIUM_DAYS and selling_days >= CONFIDENCE_MEDIUM_SELLING:
        confidence = CONFIDENCE_MEDIUM
    else:
        confidence = CONFIDENCE_LOW

    daily = (
        WEIGHT_SHORT * avg7 + WEIGHT_MEDIUM * avg30 + WEIGHT_LONG * avg90
    ) if sufficient else 0.0

    # A ratio against zero says nothing, so it is not computed.
    if not sufficient or avg30 <= 0:
        trend = TREND_UNKNOWN
        trend_ratio = None
    else:
        trend_ratio = avg7 / avg30
        if trend_ratio > 1 + TREND_TOLERANCE:
            trend = TREND_UP
        elif trend_ratio < 1 - TREND_TOLERANCE:
            trend = TREND_DOWN
        else:
            trend = TREND_STABLE

    return {
        'daily': round(daily, 4),
        'avg_7': round(avg7, 4),
        'avg_30': round(avg30, 4),
        'avg_90': round(avg90, 4),
        'history_days': history_days,
        'selling_days': selling_days,
        'sufficient': sufficient,
        'confidence': confidence,
        'trend': trend,
        'trend_ratio': round(trend_ratio, 4) if trend_ratio is not None else None,
    }


def replenishment_for(row: BranchStock, forecast: dict, *, today: date):
    """
    Turn stock plus a forecast into coverage, a reorder point and a suggestion.

    Every field is either a number an operator can reproduce, or an explicit
    statement that a setting is missing. Nothing is guessed.
    """
    quantity = row.quantity
    daily = forecast['daily'] if forecast['sufficient'] else 0.0
    lead_time = row.lead_time_days or 0

    # --- coverage ---------------------------------------------------------
    if forecast['sufficient'] and daily > 0:
        days_of_cover = quantity / daily
        stockout_date = today + timedelta(days=int(days_of_cover))
    else:
        # Not infinity. A shelf nobody buys from is not "covered forever", it is
        # a shelf with no recent consumption, and the screen says exactly that.
        days_of_cover = None
        stockout_date = None

    # --- reorder point ----------------------------------------------------
    if not forecast['sufficient']:
        reorder_point = None
        reorder_state = INSUFFICIENT_DATA
    elif lead_time <= 0:
        reorder_point = None
        reorder_state = CONFIGURATION_REQUIRED
    else:
        reorder_point = math.ceil(daily * lead_time + row.safety_stock)
        reorder_state = 'ok'

    # --- suggestion -------------------------------------------------------
    candidates = [row.target_stock - quantity]
    if reorder_point is not None:
        candidates.append(reorder_point - quantity)
    suggested = max(max(candidates), 0)

    # --- risk -------------------------------------------------------------
    if quantity == 0:
        risk = RISK_OUT_OF_STOCK
    elif (
        forecast['sufficient']
        and lead_time > 0
        and days_of_cover is not None
        and days_of_cover <= lead_time
    ):
        # It will run out before a resupply could arrive.
        risk = RISK_CRITICAL
    elif reorder_point is not None and quantity <= reorder_point:
        risk = RISK_REORDER
    elif row.minimum_stock > 0 and quantity <= row.minimum_stock:
        risk = RISK_LOW
    elif not forecast['sufficient']:
        # Physical alerts above still fire without a forecast — only the
        # forecast-dependent verdicts fall back to saying so.
        risk = RISK_INSUFFICIENT_DATA
    else:
        risk = RISK_OK

    return {
        'quantity': quantity,
        'minimum_stock': row.minimum_stock,
        'target_stock': row.target_stock,
        'safety_stock': row.safety_stock,
        'lead_time_days': lead_time,
        'days_of_cover': round(days_of_cover, 1) if days_of_cover is not None else None,
        'estimated_stockout_date': stockout_date.isoformat() if stockout_date else None,
        'reorder_point': reorder_point,
        'reorder_state': reorder_state,
        'suggested_quantity': suggested,
        'risk': risk,
    }


def surplus_for_transfer(row: BranchStock) -> int:
    """
    How much this branch could give up without creating a problem of its own.

    Conservative on purpose: a branch keeps whichever of its own thresholds is
    highest. Emptying one shop to fill another is not a solution, it is the same
    shortage in a different postcode.
    """
    reserve = max(row.target_stock, row.minimum_stock, row.safety_stock)
    return max(row.quantity - reserve, 0)


def build_replenishment_report(branches, *, today: date | None = None, limit: int = 200):
    """
    The replenishment table: one row per (branch, product) worth acting on.

    Restricted to active products that this company actually stocks or has
    configured. Listing every article that ever existed at zero would bury the
    handful that need a decision.
    """
    today = today or timezone_today()
    branch_ids = [getattr(b, 'pk', b) for b in branches]
    if not branch_ids:
        return []

    demand = collect_demand(branch_ids, today=today)

    rows = (
        BranchStock.objects
        .filter(branch_id__in=branch_ids, product__is_active=True)
        .select_related('branch', 'product')
        .order_by('branch__name', 'product__name')
    )

    report = []
    for row in rows:
        key = (row.branch_id, row.product_id)
        series = demand.get(key, DemandSeries())
        has_config = (
            row.minimum_stock > 0 or row.target_stock > 0
            or row.safety_stock > 0 or row.lead_time_days > 0
        )
        if not series.daily and row.quantity == 0 and not has_config:
            # Never stocked, never sold, never configured: not a decision.
            continue

        forecast = forecast_for(
            series, today=today,
            tracked_since=row.created_at.date() if row.created_at else None,
        )
        plan = replenishment_for(row, forecast, today=today)
        report.append({
            'branch_id': row.branch_id,
            'branch_name': row.branch.name,
            'product_id': row.product_id,
            'product_name': row.product.name,
            'forecast': forecast,
            **plan,
        })

    report.sort(key=lambda r: (RISK_ORDER.index(r['risk']), -r['suggested_quantity']))
    return report[:limit]


def timezone_today() -> date:
    from django.utils import timezone as _tz

    return _tz.localdate()
