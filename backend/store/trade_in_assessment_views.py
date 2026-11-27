"""
TRADE-IN VALUATION PRO · Phase 4 — `/api/admin/trade-in/{tariff/match,assessments}/…`.

AUTHORITY (docs/adr-trade-in-valuation.md §4):
    sales.tradein.view      list and read assessments (identifiers masked)
    sales.tradein.value     match the tariff, open an assessment, answer, calculate, cancel
    sales.tradein.approve   approve a valuation that needs it (not the one who calculated it)

The company comes from the session; an assessment is reached only in a branch the caller reaches; anything else
answers 404. The tariff's internal estimates (refurbishment, resale, margin) never appear here.
"""
from __future__ import annotations

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import trade_in_assessment as assessments
from . import trade_in_eligibility as eligibility
from . import trade_in_tariff as tariff
from .models import TradeInAssessment
from .tenancy import has_capability, visible_branches
from .throttles import AdminSalesQuotesThrottle, AdminSalesQuoteWriteThrottle
from .trade_in_tariff_views import _body, _company, _error, _not_found, _person


def _money(value):
    return None if value is None else str(value)


def assessment_payload(user, company, a, *, full=True) -> dict:
    check = a.device_check
    body = {
        'id': a.pk, 'number': a.label, 'status': a.status, 'status_label': a.get_status_display(),
        'purpose': a.purpose, 'branch': a.branch_id, 'branch_name': a.branch.name,
        'device_check': check.pk, 'device': ' '.join(p for p in (a.brand, a.model_name, a.capacity, a.sim, a.region)
                                                    if p),
        'imei': eligibility.masked(check.imei_last4), 'serial': eligibility.masked(check.serial_last4),
        'tariff_entry': a.tariff_entry_id, 'customer': a.customer_id,
        'eligibility': a.eligibility, 'eligibility_label': assessments.LABELS.get(a.eligibility, ''),
        'computed_value': _money(a.computed_value), 'offered_value': _money(a.offered_value),
        'needs_approval': a.needs_approval, 'created_at': a.created_at,
    }
    if not full:
        return body
    can_value = has_capability(user, company, assessments.CAP_VALUE)
    calculated = a.status == TradeInAssessment.STATUS_CALCULATED
    # A sales quote's total is that quote's business: only whoever may compose quotes reads it here.
    shows_price = not a.target_sales_quote_id or has_capability(user, company, assessments.CAP_QUOTES_MANAGE)
    body.update({
        'target_product': a.target_product_id, 'target_sales_quote': a.target_sales_quote_id,
        'target_price': _money(a.target_price) if shows_price else None,
        'difference': _money(a.difference) if shows_price else None,
        'adjustment_reason': a.adjustment_reason,
        'answers': {ans.factor.code: ans.value for ans in a.answers.select_related('factor')},
        'calculation': a.calculation or None,
        'calculated_by': _person(a.calculated_by), 'calculated_at': a.calculated_at,
        'approved_by': _person(a.approved_by), 'approved_at': a.approved_at, 'approval_note': a.approval_note,
        'created_by': _person(a.created_by), 'cancel_reason': a.cancel_reason,
        'actions': {
            'edit': can_value and a.status in TradeInAssessment.EDITABLE,
            'calculate': can_value and a.status in TradeInAssessment.EDITABLE,
            'approve': (calculated and a.eligibility == assessments.REQUIRES_APPROVAL
                        and has_capability(user, company, assessments.CAP_APPROVE)
                        and user.pk not in assessments._authors(a)),
            'cancel': can_value and a.status in (*TradeInAssessment.EDITABLE, TradeInAssessment.STATUS_APPROVED),
        },
    })
    return body


class _Base(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        if self.request.method in ('POST', 'PATCH', 'PUT'):
            return [AdminSalesQuoteWriteThrottle()]
        return [AdminSalesQuotesThrottle()]


def _assessments(user, company):
    return (TradeInAssessment.objects.filter(company=company, branch__in=visible_branches(user, company))
            .select_related('branch', 'device_check', 'tariff_entry', 'created_by', 'calculated_by', 'approved_by'))


class AdminTradeInTariffMatchView(_Base):
    """GET `?product_model=&capacity=&sim=&region=`: the tariff entry the device selector's choice is (§8.1)."""

    def get(self, request):
        company, error = _company(request, assessments.CAP_VALUE)
        if error:
            return error
        params = request.query_params
        try:
            entry = assessments.match_tariff(company, product_model=params.get('product_model'),
                                             capacity=params.get('capacity') or '', sim=params.get('sim') or '',
                                             region=params.get('region') or '')
        except tariff.TariffError as exc:
            return _error(exc)
        return Response({'id': entry.pk, 'brand': entry.brand, 'model_name': entry.model_name,
                         'capacity': entry.capacity, 'sim': entry.sim, 'region': entry.region})


class AdminTradeInAssessmentListView(_Base):
    def get(self, request):
        company, error = _company(request, assessments.CAP_VIEW)
        if error:
            return error
        rows = _assessments(request.user, company)
        wanted = (request.query_params.get('status') or '').strip()
        if wanted:
            if wanted not in dict(TradeInAssessment.STATUS_CHOICES):
                return Response({'detail': 'Estado no reconocido.'}, status=status.HTTP_400_BAD_REQUEST)
            rows = rows.filter(status=wanted)
        return Response({'results': [assessment_payload(request.user, company, a, full=False) for a in rows[:100]]})

    def post(self, request):
        company, error = _company(request, assessments.CAP_VALUE)
        if error:
            return error
        try:
            created = assessments.create_assessment(actor=request.user, company=company, data=_body(request),
                                                    request=request)
        except tariff.TariffError as exc:
            return _error(exc)
        return Response(assessment_payload(request.user, company, _assessments(request.user, company).get(
            pk=created.pk)), status=status.HTTP_201_CREATED)


class AdminTradeInAssessmentDetailView(_Base):
    def get(self, request, pk):
        company, error = _company(request, assessments.CAP_VIEW)
        if error:
            return error
        found = _assessments(request.user, company).filter(pk=pk).first()
        if found is None:
            return _not_found()
        return Response(assessment_payload(request.user, company, found))

    def patch(self, request, pk):
        return _act(request, pk, assessments.CAP_VALUE,
                    lambda company, a: assessments.update_details(actor=request.user, company=company, assessment=a,
                                                                  data=_body(request), request=request))


def _act(request, pk, capability, action):
    company, error = _company(request, capability)
    if error:
        return error
    found = _assessments(request.user, company).filter(pk=pk).first()
    if found is None:
        return _not_found()
    try:
        action(company, found)
    except tariff.TariffError as exc:
        return _error(exc)
    if not has_capability(request.user, company, assessments.CAP_VIEW):
        return Response({'id': found.pk})
    return Response(assessment_payload(request.user, company, _assessments(request.user, company).get(pk=pk)))


class AdminTradeInAssessmentQuestionsView(_Base):
    def get(self, request, pk):
        company, error = _company(request, assessments.CAP_VIEW, assessments.CAP_VALUE)
        if error:
            return error
        found = _assessments(request.user, company).filter(pk=pk).first()
        if found is None:
            return _not_found()
        try:
            return Response({'results': assessments.questions(company, found)})
        except tariff.TariffError as exc:
            return _error(exc)


class AdminTradeInAssessmentAnswersView(_Base):
    def put(self, request, pk):
        return _act(request, pk, assessments.CAP_VALUE,
                    lambda company, a: assessments.set_answers(actor=request.user, company=company, assessment=a,
                                                               data=_body(request), request=request))


class AdminTradeInAssessmentCalculateView(_Base):
    def post(self, request, pk):
        return _act(request, pk, assessments.CAP_VALUE,
                    lambda company, a: assessments.calculate(actor=request.user, company=company, assessment=a,
                                                             data=_body(request), request=request))


class AdminTradeInAssessmentApproveView(_Base):
    def post(self, request, pk):
        return _act(request, pk, assessments.CAP_APPROVE,
                    lambda company, a: assessments.approve(actor=request.user, company=company, assessment=a,
                                                           note=_body(request).get('note'), request=request))


class AdminTradeInAssessmentCancelView(_Base):
    def post(self, request, pk):
        return _act(request, pk, assessments.CAP_VALUE,
                    lambda company, a: assessments.cancel(actor=request.user, company=company, assessment=a,
                                                          reason=_body(request).get('reason'), request=request))
