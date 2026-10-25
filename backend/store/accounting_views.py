"""
ACCOUNTING-02 — the read-only accounting centre (`docs/adr-accounting.md`).

    GET /api/admin/accounting/operations/       view A: every operation, once
    GET /api/admin/accounting/fiscal-documents/ view B: fiscal documents and their circuit
    GET /api/admin/accounting/pending/          view C: internal, pending, inconsistent
    GET /api/admin/accounting/summary/          the period's totals and view C counts

`accounting.view` for all four; `accounting.export` additionally shows the
buyers' documents unmasked. The company is resolved on the server (`?company=`
selects, never authorises; another company answers 404); every set starts
from `visible_orders` and the caller's branches. Nothing here writes.

Each answer repeats the provisional rules (`policy`, P1–P4 pending the owner)
and the scope notice: not complete accounting — no purchases, expenses, banks
or journal entries — and nothing here is a filed declaration.
"""
from __future__ import annotations

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .accounting import defaults, fiscal, operations, pending
from .accounting.operations import TooManyRows
from .accounting.periods import LEDGER_MAX_DAYS, MAX_DAYS, PeriodError, resolve as resolve_period
from .accounting.scope import FilterError, page, resolve_filters, resolve_scope
from .pos_views import _context
from .sales_analytics_views import _company_tz
from .tenancy import has_capability
from .throttles import AdminSalesAnalyticsThrottle

CAP_VIEW = 'accounting.view'
CAP_EXPORT = 'accounting.export'


class _AccountingView(APIView):
    #: The widest period this view answers. Views assembled in memory (A, C and
    #: the summary) take `LEDGER_MAX_DAYS` until they aggregate in the database.
    max_days = LEDGER_MAX_DAYS
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminSalesAnalyticsThrottle]
    http_method_names = ['get', 'options']

    def get(self, request):
        company, error = _context(request, CAP_VIEW)
        if error:
            return error
        try:
            period = resolve_period(request.query_params, company, self.max_days)
            scope = resolve_scope(request.user, company, request.query_params)
            filters = resolve_filters(request.query_params, company)
        except (PeriodError, FilterError) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        unmask = has_capability(request.user, company, CAP_EXPORT)
        body = {
            'company': {'id': company.pk, 'name': company.name},
            'period': period.payload(), 'scope': scope.payload(), 'filters': filters.payload(),
            'policy': defaults.policy(), 'notice': defaults.SCOPE_NOTICE,
            'unmasked': unmask,
        }
        try:
            body.update(self.answer(scope, period, filters, unmask))
        except TooManyRows as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(body)

    def answer(self, scope, period, filters, unmask) -> dict:  # pragma: no cover - abstract
        raise NotImplementedError

    @staticmethod
    def ledger(scope, period, filters, unmask):
        ledger = operations.build(scope, period, unmask)
        return ledger, operations.filtered(ledger, filters)


class AccountingOperationsView(_AccountingView):
    def answer(self, scope, period, filters, unmask):
        _ledger, rows = self.ledger(scope, period, filters, unmask)
        chunk, cursor = page(rows, filters)
        return {
            'totals': operations.totals(rows), 'count': len(rows),
            'results': [operations.public(r) for r in chunk], 'next_cursor': cursor,
        }


class AccountingFiscalDocumentsView(_AccountingView):
    max_days = MAX_DAYS

    def answer(self, scope, period, filters, unmask):
        rows, totals, excluded = fiscal.rows(scope, period, filters, unmask)
        chunk, cursor = page(rows, filters)
        return {'environment': filters.environment, 'totals': totals, 'excluded_totals': excluded,
                'count': len(rows), 'results': chunk, 'next_cursor': cursor}


class AccountingPendingView(_AccountingView):
    def answer(self, scope, period, filters, unmask):
        ledger, rows = self.ledger(scope, period, filters, unmask)
        entries = pending.entries(rows, ledger.unpaid, filters, _company_tz(scope.company))
        chunk, cursor = page(entries, filters)
        return {'counts': pending.counts(entries), 'count': len(entries),
                'results': chunk, 'next_cursor': cursor}


class AccountingSummaryView(_AccountingView):
    def answer(self, scope, period, filters, unmask):
        ledger, rows = self.ledger(scope, period, filters, unmask)
        entries = pending.entries(rows, ledger.unpaid, filters, _company_tz(scope.company))
        return {'totals': operations.totals(rows), 'pending': pending.counts(entries)}
