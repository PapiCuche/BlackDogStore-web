"""
INV-LOAD-02 — the invariant of serialized stock, checked from outside.

    BranchStock.quantity == the AVAILABLE devices of that branch and product

`stock_unit_services` keeps it on every write; this reads it back, per company × branch × product, for a person (the
panel's «Conciliación») or a runbook (`manage.py check_unit_stock`, exit 2 when anything differs). It READS ONLY: a
difference is reported, never repaired — repairing stock without knowing why it differs would hide the cause.

Every product that sells by device is checked, plus any product that still has available devices (a product whose
serial control was switched off after the fact would otherwise hide them). Both numbers are read in ONE snapshot when
the caller is not already inside a transaction, so a sale committing between the two reads is not a false difference.
"""
from __future__ import annotations

from contextlib import contextmanager

from django.db import connection, transaction
from django.db.models import Count

from .models import BranchStock, StockUnit


@contextmanager
def snapshot():
    """One consistent, read-only view of the database for the block (only when nobody opened a transaction before)."""
    if connection.in_atomic_block:
        yield
        return
    with transaction.atomic():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        yield


def mismatches(*, company=None, branch_ids=None) -> dict:
    """
    `{'checked': shelves looked at, 'mismatches': [...]}` — each mismatch with company, branch and product (ids and
    names), `quantity` (the shelf), `available` (devices) and `difference` (quantity − available). Sorted for reading.
    """
    stock_rows = BranchStock.objects.all()
    unit_rows = StockUnit.objects.filter(status=StockUnit.Status.AVAILABLE)
    if company is not None:
        stock_rows = stock_rows.filter(branch__company=company)
        unit_rows = unit_rows.filter(company=company)
    if branch_ids is not None:
        stock_rows = stock_rows.filter(branch_id__in=branch_ids)
        unit_rows = unit_rows.filter(branch_id__in=branch_ids)

    with snapshot():
        available = {
            (row['branch_id'], row['product_id']): row['n']
            for row in unit_rows.order_by().values('branch_id', 'product_id').annotate(n=Count('pk'))
        }
        quantities = {
            (row['branch_id'], row['product_id']): row['quantity']
            for row in stock_rows.filter(product__is_serialized=True).values('branch_id', 'product_id', 'quantity')
        }
        # Devices of a product that is not (or no longer) serialized: their shelf too.
        missing = {pair for pair in available if pair not in quantities}
        if missing:
            for row in stock_rows.filter(product_id__in={p for _, p in missing}).values(
                    'branch_id', 'product_id', 'quantity'):
                pair = (row['branch_id'], row['product_id'])
                if pair in missing:
                    quantities[pair] = row['quantity']
        pairs = set(available) | set(quantities)
        differing = sorted(pair for pair in pairs if quantities.get(pair, 0) != available.get(pair, 0))
        names = _names(differing)

    found = []
    for branch_id, product_id in differing:
        branch, product = names['branches'][branch_id], names['products'][product_id]
        quantity, units = quantities.get((branch_id, product_id), 0), available.get((branch_id, product_id), 0)
        found.append({
            'company_id': branch['company_id'], 'company': branch['company__slug'],
            'branch_id': branch_id, 'branch': branch['name'], 'product_id': product_id, 'product': product['name'],
            'quantity': quantity, 'available': units, 'difference': quantity - units,
        })
    found.sort(key=lambda m: (m['company'], m['branch'], m['product'], m['branch_id'], m['product_id']))
    return {'checked': len(pairs), 'mismatches': found}


def _names(pairs) -> dict:
    from .models import Branch, Product

    branch_ids = {b for b, _ in pairs}
    product_ids = {p for _, p in pairs}
    return {
        'branches': {row['pk']: row for row in Branch.objects.filter(pk__in=branch_ids).values(
            'pk', 'name', 'company_id', 'company__slug')},
        'products': {row['pk']: row for row in Product.objects.filter(pk__in=product_ids).values('pk', 'name')},
    }
