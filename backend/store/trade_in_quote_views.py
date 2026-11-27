"""
TRADE-IN VALUATION PRO · Phase 5a — `/api/admin/trade-in/quotes/…`: the trade-in quotes for the panel.

AUTHORITY (docs/adr-trade-in-valuation.md §4):
    sales.tradein.view         list and read quotes (identifiers masked; internal estimates only with
                               `sales.tradein.tariff.view` or `costs.view`)
    sales.tradein.value        generate from an assessment, issue, record the customer's answer, cancel, revise
    sales.tradein.approve      approve or reject a quote that needs it (`approve_own` for one's own, with a reason)

The company comes from the session; a quote is reached only in a branch the caller reaches; anything else answers
404. A sales quote's price is shown only to whoever may compose quotes.
"""
from __future__ import annotations

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import trade_in_assessment as assessments
from . import trade_in_eligibility as eligibility
from . import trade_in_quote as quotes
from . import trade_in_tariff as tariff
from .models import TradeInAssessment, TradeInQuote
from .tenancy import has_capability, visible_branches
from .throttles import AdminSalesQuotesThrottle, AdminSalesQuoteWriteThrottle
from .trade_in_tariff_views import _body, _company, _error, _not_found, _person


def _money(value):
    return None if value is None else str(value)


def quote_payload(user, company, q, *, full=True) -> dict:
    effective = q.effective_status
    check = q.assessment.device_check
    body = {
        'id': q.pk, 'number': q.label, 'revision': q.revision, 'status': effective, 'stored_status': q.status,
        'status_label': dict(TradeInQuote.STATUS_CHOICES).get(effective, effective),
        'branch': q.branch_id, 'assessment': q.assessment_id,
        'device': ' '.join(p for p in (q.assessment.brand, q.assessment.model_name, q.assessment.capacity) if p),
        'imei': eligibility.masked(check.imei_last4), 'serial': eligibility.masked(check.serial_last4),
        'eligibility': q.eligibility, 'eligibility_label': assessments.LABELS.get(q.eligibility, ''),
        'offered_value': _money(q.offered_value), 'currency': q.currency,
        'issued_at': q.issued_at, 'valid_until': q.valid_until,
        'valid_until_display': quotes.display_valid_until(company, q),
        'created_at': q.created_at,
    }
    if not full:
        return body
    shows_price = (not q.assessment.target_sales_quote_id
                   or has_capability(user, company, assessments.CAP_QUOTES_MANAGE))
    internal = (has_capability(user, company, tariff.CAP_VIEW) or has_capability(user, company, 'costs.view'))
    can_value = has_capability(user, company, quotes.CAP_VALUE)
    approver = has_capability(user, company, quotes.CAP_APPROVE)
    pending = q.status == TradeInQuote.STATUS_PENDING_APPROVAL
    body.update({
        'computed_value': _money(q.computed_value), 'adjustment_reason': q.adjustment_reason,
        'max_reception_value': _money(q.max_reception_value),
        'target_price': _money(q.target_price) if shows_price else None,
        'difference': _money(q.difference) if shows_price else None,
        'conditions': q.conditions, 'calculation': q.calculation,
        'revision_reason': q.revision_reason, 'source_quote': q.source_quote_id,
        'approval_kind': q.approval_kind, 'approved_by': _person(q.approved_by), 'approved_at': q.approved_at,
        'issued_by': _person(q.issued_by), 'decided_by': _person(q.decided_by), 'decided_at': q.decided_at,
        'decision_note': q.decision_note, 'created_by': _person(q.created_by),
        'revisions': list(TradeInQuote.objects.filter(company=company, number=q.number).order_by('revision')
                          .values('id', 'revision', 'status')),
        'actions': {
            'approve': pending and approver and (user.pk not in quotes._authors(q)
                                                 or has_capability(user, company, quotes.CAP_APPROVE_OWN)),
            'reject': pending and approver,
            'issue': can_value and q.status == TradeInQuote.STATUS_APPROVED,
            'decide': can_value and q.status == TradeInQuote.STATUS_ISSUED and not q.is_expired,
            'cancel': can_value and q.status in (TradeInQuote.STATUS_PENDING_APPROVAL, TradeInQuote.STATUS_APPROVED),
            'revise': can_value and (q.status in TradeInQuote.REVISABLE),
        },
    })
    if internal:
        body['internal'] = {'refurbishment_cost': _money(q.refurbishment_cost), 'resale_value': _money(q.resale_value),
                            'target_margin': _money(q.target_margin)}
    return body


class _Base(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        if self.request.method in ('POST', 'PATCH', 'PUT'):
            return [AdminSalesQuoteWriteThrottle()]
        return [AdminSalesQuotesThrottle()]


def _quotes(user, company):
    return (TradeInQuote.objects.filter(company=company, branch__in=visible_branches(user, company))
            .select_related('branch', 'assessment__device_check', 'approved_by', 'issued_by', 'decided_by',
                            'created_by'))


class AdminTradeInQuoteListView(_Base):
    def get(self, request):
        company, error = _company(request, quotes.CAP_VIEW)
        if error:
            return error
        rows = _quotes(request.user, company)
        wanted = (request.query_params.get('status') or 'current').strip()
        if wanted == 'current':
            rows = rows.exclude(status=TradeInQuote.STATUS_SUPERSEDED)
        elif wanted in dict(TradeInQuote.STATUS_CHOICES):
            rows = rows.filter(status=wanted)
        elif wanted != 'all':
            return Response({'detail': 'Estado no reconocido.'}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'results': [quote_payload(request.user, company, q, full=False) for q in rows[:100]]})

    def post(self, request):
        company, error = _company(request, quotes.CAP_VALUE)
        if error:
            return error
        raw = _body(request).get('assessment')
        try:
            pk = int(raw)
        except (TypeError, ValueError):
            return Response({'detail': 'Indica la evaluación.', 'field': 'assessment'},
                            status=status.HTTP_400_BAD_REQUEST)
        found = TradeInAssessment.objects.filter(company=company, pk=pk,
                                                 branch__in=visible_branches(request.user, company)).first()
        if found is None:
            return _not_found()
        try:
            created = quotes.generate(actor=request.user, company=company, assessment=found, request=request)
        except tariff.TariffError as exc:
            return _error(exc)
        return Response(quote_payload(request.user, company, _quotes(request.user, company).get(pk=created.pk)),
                        status=status.HTTP_201_CREATED)


class AdminTradeInQuoteDetailView(_Base):
    def get(self, request, pk):
        company, error = _company(request, quotes.CAP_VIEW)
        if error:
            return error
        found = _quotes(request.user, company).filter(pk=pk).first()
        if found is None:
            return _not_found()
        return Response(quote_payload(request.user, company, found))


def _act(request, pk, capability, action):
    company, error = _company(request, capability)
    if error:
        return error
    found = _quotes(request.user, company).filter(pk=pk).first()
    if found is None:
        return _not_found()
    try:
        result = action(company, found)
    except tariff.TariffError as exc:
        return _error(exc)
    target = result.pk if isinstance(result, TradeInQuote) else pk
    if not has_capability(request.user, company, quotes.CAP_VIEW):
        return Response({'id': target})
    return Response(quote_payload(request.user, company, _quotes(request.user, company).get(pk=target)))


class AdminTradeInQuoteApproveView(_Base):
    def post(self, request, pk):
        return _act(request, pk, quotes.CAP_APPROVE, lambda c, q: quotes.approve(
            actor=request.user, company=c, quote=q, note=_body(request).get('note'), request=request))


class AdminTradeInQuoteRejectView(_Base):
    def post(self, request, pk):
        return _act(request, pk, quotes.CAP_APPROVE, lambda c, q: quotes.reject(
            actor=request.user, company=c, quote=q, note=_body(request).get('note'), request=request))


class AdminTradeInQuoteIssueView(_Base):
    def post(self, request, pk):
        return _act(request, pk, quotes.CAP_VALUE, lambda c, q: quotes.issue(
            actor=request.user, company=c, quote=q, request=request))


class AdminTradeInQuoteAcceptView(_Base):
    def post(self, request, pk):
        return _act(request, pk, quotes.CAP_VALUE, lambda c, q: quotes.decide(
            actor=request.user, company=c, quote=q, accepted=True, note=_body(request).get('note'), request=request))


class AdminTradeInQuoteDeclineView(_Base):
    def post(self, request, pk):
        return _act(request, pk, quotes.CAP_VALUE, lambda c, q: quotes.decide(
            actor=request.user, company=c, quote=q, accepted=False, note=_body(request).get('note'), request=request))


class AdminTradeInQuoteCancelView(_Base):
    def post(self, request, pk):
        return _act(request, pk, quotes.CAP_VALUE, lambda c, q: quotes.cancel(
            actor=request.user, company=c, quote=q, reason=_body(request).get('reason'), request=request))


class AdminTradeInQuoteReviseView(_Base):
    def post(self, request, pk):
        return _act(request, pk, quotes.CAP_VALUE, lambda c, q: quotes.revise(
            actor=request.user, company=c, quote=q, data=_body(request), request=request))
