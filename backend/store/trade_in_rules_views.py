"""
TRADE-IN VALUATION PRO · Phase 2 — `/api/admin/trade-in/rules/…`: the company's valuation rules for the panel.

AUTHORITY (docs/adr-trade-in-valuation.md §4): the tariff's capabilities.
    sales.tradein.tariff.view         groups, factors, rule-set versions with their rules, «Probar un caso»
    sales.tradein.tariff.manage       write groups and factors; open, edit, propose and withdraw a draft
    sales.tradein.tariff.approve      approve or reject a proposed set (and withdraw one)
    sales.tradein.tariff.approve_own  approve a set one proposed (with a reason; flagged in the audit)

The company comes from the session; anything of another company answers 404. Every write is audited with the company.
"""
from __future__ import annotations

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import trade_in_rules as rules
from .models import TradeInFactor, TradeInFactorGroup, TradeInRule, TradeInRuleSetVersion
from .tenancy import has_capability
from .throttles import AdminSalesQuotesThrottle, AdminSalesQuoteWriteThrottle
from .trade_in_tariff_views import _body, _company, _error, _not_found, _person


def group_payload(group) -> dict:
    return {'id': group.pk, 'kind': group.kind, 'kind_label': group.get_kind_display(), 'name': group.name,
            'combine': group.combine,
            'group_cap_percent': None if group.group_cap_percent is None else str(group.group_cap_percent),
            'sort_order': group.sort_order, 'is_active': group.is_active}


def factor_payload(factor) -> dict:
    return {'id': factor.pk, 'code': factor.code, 'group': factor.group_id, 'label': factor.label,
            'input': factor.input, 'options': list(factor.options or []), 'component': factor.component,
            'applies_brand': factor.applies_brand, 'applies_model_name': factor.applies_model_name,
            'applies_product_model': factor.applies_product_model_id, 'requires_photo': factor.requires_photo,
            'sort_order': factor.sort_order, 'is_active': factor.is_active}


def rule_payload(rule) -> dict:
    def text(value):
        return None if value is None else str(value)

    return {'id': rule.pk, 'factor': rule.factor_id, 'factor_code': rule.factor.code,
            'when_outcome': rule.when_outcome, 'when_percent_gte': text(rule.when_percent_gte),
            'when_percent_lt': text(rule.when_percent_lt), 'effect': rule.effect, 'amount': text(rule.amount),
            'percent': text(rule.percent), 'priority': rule.priority, 'owner': rule.owner_id,
            'owner_name': _person(rule.owner), 'notes': rule.notes}


def _actions(user, company, version) -> dict:
    manage = has_capability(user, company, rules.CAP_MANAGE)
    approve = has_capability(user, company, rules.CAP_APPROVE)
    draft = version.status == TradeInRuleSetVersion.STATUS_DRAFT
    pending = version.status == TradeInRuleSetVersion.STATUS_PENDING
    own = version.proposed_by_id is not None and version.proposed_by_id == user.pk
    author = (version.proposed_by_id or version.created_by_id) == user.pk
    return {
        'edit': manage and draft,
        'propose': manage and draft,
        'approve': approve and pending and (not own or has_capability(user, company, rules.CAP_APPROVE_OWN)),
        'reject': approve and pending,
        'withdraw': manage and (draft or pending) and (author or approve),
    }


def version_payload(user, company, version, *, with_rules=True) -> dict:
    body = {
        'id': version.pk, 'number': version.number, 'status': version.status,
        'status_label': version.get_status_display(),
        'valid_from': version.valid_from.isoformat() if version.valid_from else None,
        'valid_until': version.valid_until.isoformat() if version.valid_until else None,
        'reason': version.reason, 'source_version': version.source_version_id,
        'created_by': _person(version.created_by), 'created_at': version.created_at,
        'proposed_by': _person(version.proposed_by), 'proposed_at': version.proposed_at,
        'approval_kind': version.approval_kind, 'decided_by': _person(version.decided_by),
        'decided_at': version.decided_at, 'decision_note': version.decision_note,
        'actions': _actions(user, company, version),
    }
    if with_rules:
        body['rules'] = [rule_payload(rule) for rule in version.rules.select_related('factor', 'owner')]
        body['snapshot'] = version.snapshot or None
    return body


class _Base(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        if self.request.method in ('POST', 'PATCH', 'PUT', 'DELETE'):
            return [AdminSalesQuoteWriteThrottle()]
        return [AdminSalesQuotesThrottle()]


class AdminTradeInFactorGroupListView(_Base):
    def get(self, request):
        company, error = _company(request, rules.CAP_VIEW)
        if error:
            return error
        rows = TradeInFactorGroup.objects.filter(company=company)
        return Response({'results': [group_payload(g) for g in rows],
                         'kinds': [{'value': v, 'label': l} for v, l in TradeInFactorGroup.KIND_CHOICES]})

    def post(self, request):
        company, error = _company(request, rules.CAP_MANAGE)
        if error:
            return error
        try:
            group = rules.create_group(actor=request.user, company=company, data=_body(request), request=request)
        except rules.TariffError as exc:
            return _error(exc)
        return Response(group_payload(group), status=status.HTTP_201_CREATED)


class AdminTradeInFactorGroupDetailView(_Base):
    def patch(self, request, pk):
        company, error = _company(request, rules.CAP_MANAGE)
        if error:
            return error
        group = TradeInFactorGroup.objects.filter(company=company, pk=pk).first()
        if group is None:
            return _not_found()
        try:
            group = rules.update_group(actor=request.user, company=company, group=group, data=_body(request),
                                       request=request)
        except rules.TariffError as exc:
            return _error(exc)
        return Response(group_payload(group))


class AdminTradeInFactorListView(_Base):
    def get(self, request):
        company, error = _company(request, rules.CAP_VIEW)
        if error:
            return error
        rows = TradeInFactor.objects.filter(company=company)
        if (request.query_params.get('active') or '').lower() in ('true', '1'):
            rows = rows.filter(is_active=True)
        return Response({'results': [factor_payload(f) for f in rows],
                         'check_outcomes': list(TradeInFactor.CHECK_OUTCOMES)})

    def post(self, request):
        company, error = _company(request, rules.CAP_MANAGE)
        if error:
            return error
        try:
            factor = rules.create_factor(actor=request.user, company=company, data=_body(request), request=request)
        except rules.TariffError as exc:
            return _error(exc)
        return Response(factor_payload(factor), status=status.HTTP_201_CREATED)


class AdminTradeInFactorDetailView(_Base):
    def patch(self, request, pk):
        company, error = _company(request, rules.CAP_MANAGE)
        if error:
            return error
        factor = TradeInFactor.objects.filter(company=company, pk=pk).first()
        if factor is None:
            return _not_found()
        try:
            factor = rules.update_factor(actor=request.user, company=company, factor=factor, data=_body(request),
                                         request=request)
        except rules.TariffError as exc:
            return _error(exc)
        return Response(factor_payload(factor))


def _version(company, pk):
    return (TradeInRuleSetVersion.objects.filter(company=company, pk=pk)
            .select_related('created_by', 'proposed_by', 'decided_by').first())


class AdminTradeInRuleSetListView(_Base):
    def get(self, request):
        company, error = _company(request, rules.CAP_VIEW)
        if error:
            return error
        rows = (TradeInRuleSetVersion.objects.filter(company=company)
                .select_related('created_by', 'proposed_by', 'decided_by').order_by('-number'))
        current = rules.effective_rule_set(company)
        return Response({'results': [version_payload(request.user, company, v, with_rules=False) for v in rows],
                         'in_force': current.pk if current else None,
                         'can': {'manage': has_capability(request.user, company, rules.CAP_MANAGE),
                                 'approve': has_capability(request.user, company, rules.CAP_APPROVE)}})

    def post(self, request):
        company, error = _company(request, rules.CAP_MANAGE)
        if error:
            return error
        try:
            draft = rules.create_draft(actor=request.user, company=company, data=_body(request), request=request)
        except rules.TariffError as exc:
            return _error(exc)
        return Response(version_payload(request.user, company, _version(company, draft.pk)),
                        status=status.HTTP_201_CREATED)


class AdminTradeInRuleSetDetailView(_Base):
    def get(self, request, pk):
        company, error = _company(request, rules.CAP_VIEW)
        if error:
            return error
        version = _version(company, pk)
        if version is None:
            return _not_found()
        return Response(version_payload(request.user, company, version))


class AdminTradeInRuleAddView(_Base):
    def post(self, request, pk):
        company, error = _company(request, rules.CAP_MANAGE)
        if error:
            return error
        version = _version(company, pk)
        if version is None:
            return _not_found()
        try:
            rule = rules.add_rule(actor=request.user, company=company, version=version, data=_body(request),
                                  request=request)
        except rules.TariffError as exc:
            return _error(exc)
        return Response(rule_payload(rule), status=status.HTTP_201_CREATED)


class AdminTradeInRuleDetailView(_Base):
    def _rule(self, company, pk):
        return TradeInRule.objects.filter(company=company, pk=pk).select_related('version', 'factor', 'owner').first()

    def patch(self, request, pk):
        company, error = _company(request, rules.CAP_MANAGE)
        if error:
            return error
        rule = self._rule(company, pk)
        if rule is None:
            return _not_found()
        try:
            rule = rules.update_rule(actor=request.user, company=company, rule=rule, data=_body(request),
                                     request=request)
        except rules.TariffError as exc:
            return _error(exc)
        return Response(rule_payload(rule))

    def delete(self, request, pk):
        company, error = _company(request, rules.CAP_MANAGE)
        if error:
            return error
        rule = self._rule(company, pk)
        if rule is None:
            return _not_found()
        try:
            rules.delete_rule(actor=request.user, company=company, rule=rule, request=request)
        except rules.TariffError as exc:
            return _error(exc)
        return Response(status=status.HTTP_204_NO_CONTENT)


class _VersionAction(_Base):
    capability = rules.CAP_MANAGE

    def act(self, request, company, version):  # pragma: no cover - abstract
        raise NotImplementedError

    def post(self, request, pk):
        company, error = _company(request, self.capability)
        if error:
            return error
        version = _version(company, pk)
        if version is None:
            return _not_found()
        try:
            self.act(request, company, version)
        except rules.TariffError as exc:
            return _error(exc)
        return Response(version_payload(request.user, company, _version(company, pk),
                                         with_rules=has_capability(request.user, company, rules.CAP_VIEW)))


class AdminTradeInRuleSetProposeView(_VersionAction):
    def act(self, request, company, version):
        rules.propose(actor=request.user, company=company, version=version, data=_body(request), request=request)


class AdminTradeInRuleSetApproveView(_VersionAction):
    capability = rules.CAP_APPROVE

    def act(self, request, company, version):
        rules.approve(actor=request.user, company=company, version=version, note=_body(request).get('note') or '',
                      request=request)


class AdminTradeInRuleSetRejectView(_VersionAction):
    capability = rules.CAP_APPROVE

    def act(self, request, company, version):
        rules.reject(actor=request.user, company=company, version=version, note=_body(request).get('note') or '',
                     request=request)


class AdminTradeInRuleSetWithdrawView(_VersionAction):
    def act(self, request, company, version):
        rules.withdraw(actor=request.user, company=company, version=version, note=_body(request).get('note') or '',
                       request=request)


class AdminTradeInRulesPreviewView(_Base):
    """POST «Probar un caso»: computes, writes nothing (a POST only because the answers are a body)."""

    def get_throttles(self):
        return [AdminSalesQuotesThrottle()]

    def post(self, request):
        company, error = _company(request, rules.CAP_VIEW)
        if error:
            return error
        try:
            result = rules.preview(actor=request.user, company=company, data=_body(request))
        except rules.TariffError as exc:
            return _error(exc)
        return Response(result)
