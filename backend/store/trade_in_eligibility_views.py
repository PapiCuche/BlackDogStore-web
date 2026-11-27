"""
TRADE-IN VALUATION PRO · Phase 3 — `/api/admin/trade-in/{verification-sources,checks}/…`.

AUTHORITY (docs/adr-trade-in-valuation.md §4):
    sales.tradein.view            list and read device checks (identifiers masked) and the verification sources
    sales.tradein.value           register a device, answer its blockers, record an identity check, close a check
    sales.tradein.tariff.manage   configure the company's verification sources

The company comes from the session; a check is reached only in a branch the caller reaches; anything else answers
404. Responses and the audit carry the last four characters at most: never an identifier or its digest.
"""
from __future__ import annotations

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import trade_in_eligibility as eligibility
from . import trade_in_tariff as tariff
from .models import TradeInDeviceCheck, TradeInVerificationSource
from .tenancy import has_capability, visible_branches
from .throttles import AdminSalesQuotesThrottle, AdminSalesQuoteWriteThrottle
from .trade_in_tariff_views import _body, _company, _error, _not_found, _person


def source_payload(source) -> dict:
    return {'id': source.pk, 'name': source.name, 'url': source.url, 'identifier': source.identifier,
            'checks': list(source.checks or []), 'instructions': source.instructions, 'is_active': source.is_active}


def record_payload(record) -> dict:
    return {'id': record.pk, 'slot': record.slot, 'source': record.source_id,
            'source_name': record.source.name if record.source_id else '', 'result': record.result,
            'result_label': record.get_result_display(), 'checked_at': record.checked_at,
            'valid_until': record.valid_until, 'note': record.note, 'recorded_by': _person(record.recorded_by),
            'recorded_at': record.recorded_at}


def check_payload(user, company, check) -> dict:
    verdict = eligibility.evaluate(check)
    can_value = has_capability(user, company, eligibility.CAP_VALUE) and check.status == TradeInDeviceCheck.STATUS_OPEN
    return {
        'id': check.pk, 'branch': check.branch_id, 'branch_name': check.branch.name, 'status': check.status,
        'description': check.description, 'has_imei': check.has_imei,
        'imei': eligibility.masked(check.imei_last4), 'imei2': eligibility.masked(check.imei2_last4),
        'serial': eligibility.masked(check.serial_last4),
        'created_by': _person(check.created_by), 'created_at': check.created_at,
        'closed_at': check.closed_at, 'close_reason': check.close_reason,
        'records': [record_payload(r) for r in check.identity_records.select_related('source', 'recorded_by')],
        'answers': [{'code': a.code, 'answer': a.answer, 'note': a.note, 'answered_by': _person(a.answered_by),
                     'answered_at': a.answered_at} for a in check.blocker_answers.select_related('answered_by')],
        **verdict,
        'actions': {'answer': can_value, 'record': can_value, 'close': can_value},
    }


class _Base(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        if self.request.method in ('POST', 'PATCH'):
            return [AdminSalesQuoteWriteThrottle()]
        return [AdminSalesQuotesThrottle()]


class AdminTradeInSourceListView(_Base):
    def get(self, request):
        company, error = _company(request, eligibility.CAP_VIEW, eligibility.CAP_VALUE, tariff.CAP_VIEW)
        if error:
            return error
        rows = TradeInVerificationSource.objects.filter(company=company)
        return Response({'results': [source_payload(s) for s in rows],
                         'checks': list(TradeInVerificationSource.CHECK_CHOICES)})

    def post(self, request):
        company, error = _company(request, tariff.CAP_MANAGE)
        if error:
            return error
        try:
            source = eligibility.save_source(actor=request.user, company=company, data=_body(request),
                                             request=request)
        except tariff.TariffError as exc:
            return _error(exc)
        return Response(source_payload(source), status=status.HTTP_201_CREATED)


class AdminTradeInSourceDetailView(_Base):
    def patch(self, request, pk):
        company, error = _company(request, tariff.CAP_MANAGE)
        if error:
            return error
        source = TradeInVerificationSource.objects.filter(company=company, pk=pk).first()
        if source is None:
            return _not_found()
        try:
            source = eligibility.save_source(actor=request.user, company=company, data=_body(request), source=source,
                                             request=request)
        except tariff.TariffError as exc:
            return _error(exc)
        return Response(source_payload(source))


def _checks(user, company):
    # Answers and records come with the checks: the verdict of a list costs no query per row.
    return (TradeInDeviceCheck.objects.filter(company=company, branch__in=visible_branches(user, company))
            .select_related('branch', 'created_by', 'company')
            .prefetch_related('blocker_answers', 'identity_records'))


class AdminTradeInCheckListView(_Base):
    def get(self, request):
        company, error = _company(request, eligibility.CAP_VIEW)
        if error:
            return error
        rows = _checks(request.user, company)
        wanted = (request.query_params.get('status') or 'open').strip()
        if wanted in dict(TradeInDeviceCheck.STATUS_CHOICES):
            rows = rows.filter(status=wanted)
        elif wanted != 'all':
            return Response({'detail': 'Estado no reconocido.'}, status=status.HTTP_400_BAD_REQUEST)
        results = [{'id': c.pk, 'branch': c.branch_id, 'branch_name': c.branch.name, 'status': c.status,
                    'description': c.description, 'imei': eligibility.masked(c.imei_last4),
                    'serial': eligibility.masked(c.serial_last4), 'created_at': c.created_at,
                    'eligibility': eligibility.evaluate(c)['eligibility']} for c in rows[:100]]
        return Response({'results': results})

    def post(self, request):
        company, error = _company(request, eligibility.CAP_VALUE)
        if error:
            return error
        try:
            check = eligibility.create_check(actor=request.user, company=company, data=_body(request),
                                             request=request)
        except tariff.TariffError as exc:
            return _error(exc)
        return Response(check_payload(request.user, company, _checks(request.user, company).get(pk=check.pk)),
                        status=status.HTTP_201_CREATED)


class AdminTradeInCheckDetailView(_Base):
    def get(self, request, pk):
        company, error = _company(request, eligibility.CAP_VIEW)
        if error:
            return error
        check = _checks(request.user, company).filter(pk=pk).first()
        if check is None:
            return _not_found()
        return Response(check_payload(request.user, company, check))


class _CheckAction(_Base):
    def act(self, request, company, check):  # pragma: no cover - abstract
        raise NotImplementedError

    def post(self, request, pk):
        company, error = _company(request, eligibility.CAP_VALUE)
        if error:
            return error
        check = _checks(request.user, company).filter(pk=pk).first()
        if check is None:
            return _not_found()
        try:
            self.act(request, company, check)
        except tariff.TariffError as exc:
            return _error(exc)
        body = {'id': check.pk}
        if has_capability(request.user, company, eligibility.CAP_VIEW):
            body = check_payload(request.user, company, _checks(request.user, company).get(pk=pk))
        return Response(body)


class AdminTradeInCheckBlockerView(_CheckAction):
    def act(self, request, company, check):
        eligibility.answer_blocker(actor=request.user, company=company, check=check, data=_body(request),
                                   request=request)


class AdminTradeInCheckRecordView(_CheckAction):
    def act(self, request, company, check):
        eligibility.record_identity(actor=request.user, company=company, check=check, data=_body(request),
                                    request=request)


class AdminTradeInCheckCloseView(_CheckAction):
    def act(self, request, company, check):
        eligibility.close_check(actor=request.user, company=company, check=check,
                                reason=_body(request).get('reason'), request=request)
