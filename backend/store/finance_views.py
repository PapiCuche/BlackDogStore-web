"""
FINANCE-OBLIGATIONS-01 · FIN-B1 — the panel's API for fixed payments and obligations.

    /api/admin/finance/categories/                     GET finance.view · POST finance.manage
    /api/admin/finance/categories/<pk>/                PATCH finance.manage
    /api/admin/finance/rules/                          GET finance.view · POST finance.manage
    /api/admin/finance/rules/<pk>/                     GET finance.view · PATCH finance.manage
    /api/admin/finance/rules/<pk>/<action>/            POST finance.manage (pause, resume, end, generate)
    /api/admin/finance/obligations/                    GET finance.view (?state= &direction= &branch=
                                                       &category= &disputed= &due_from= &due_to=)
                                                       POST finance.manage (a one-off payable) or, with
                                                       direction=receivable, finance.receivables.manual (FIN-E:
                                                       customer + reason; never a link to a repair or a sale)
    /api/admin/finance/obligations/<pk>/               GET finance.view (payments, adjustments, history)
    /api/admin/finance/obligations/<pk>/payments/      POST finance.payments.record
    /api/admin/finance/obligations/<pk>/payments/<id>/reverse/   POST finance.payments.reverse
    /api/admin/finance/obligations/<pk>/adjustments/   POST finance.adjust
    /api/admin/finance/obligations/<pk>/void/          POST finance.void
    /api/admin/finance/obligations/<pk>/dispute/       POST finance.dispute (+ /resolve/)
    /api/admin/finance/summary/                        GET finance.view — open balances PER CURRENCY
    /api/admin/finance/reminder-policy/                GET finance.view · PATCH finance.automation.manage (FIN-REMIND-01)
    /api/admin/finance/obligations/<pk>/snooze/        POST / DELETE finance.view — THIS person's reminders only

The company is resolved on the server; `?company=` selects, never authorises,
and another company's row answers 404 like one that does not exist. A row of a
branch the caller cannot operate in answers 404 too; a GENERAL row (no branch)
needs company-wide scope. Nothing here sends a message to anybody.
"""
from __future__ import annotations

from datetime import date

from django.db.models import Exists, OuterRef
from rest_framework import permissions, status
from rest_framework.exceptions import ParseError
from rest_framework.response import Response
from rest_framework.views import APIView

from .finance import receivables, reminders, services
from .models import (
    AdminAuditLog, Branch, Customer, FinanceCategory, FinanceDirection, FinanceReminderPolicy, Notification,
    NotificationEvent, Obligation, ObligationPayment, ObligationReminderSnooze, RecurringObligationRule, Supplier,
)
from .pos_views import _context
from .tenancy import has_branch_access, has_capability, has_company_wide_scope

CAP_VIEW = 'finance.view'
CAP_MANAGE = 'finance.manage'
CAP_RECORD = 'finance.payments.record'
CAP_REVERSE = 'finance.payments.reverse'
CAP_ADJUST = 'finance.adjust'
CAP_VOID = 'finance.void'
CAP_DISPUTE = 'finance.dispute'
CAP_RECEIVABLE_MANUAL = 'finance.receivables.manual'
CAP_AUTOMATION = 'finance.automation.manage'

_NOT_FOUND = {'detail': 'No encontrado.'}
_PAGE_SIZE = 50
_MAX_PAGE_SIZE = 200
_MAX_PAGE = 1_000_000


def _error(exc: services.FinanceError) -> Response:
    return Response({'detail': exc.message}, status=exc.status)


def _not_found() -> Response:
    return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)


def _date(value, field):
    if value in (None, ''):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise services.FinanceError(f'Fecha inválida en «{field}».') from None


def _int(value, field):
    if value in (None, ''):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        raise services.FinanceError(f'Valor inválido en «{field}».') from None


def _key(request) -> str:
    return services._text(request.headers.get('Idempotency-Key') or request.data.get('idempotency_key'))


def _person(user):
    if user is None:
        return None
    return {'id': user.pk, 'name': user.get_full_name() or user.get_username()}


def _ref(row):
    return None if row is None else {'id': row.pk, 'name': getattr(row, 'name', str(row))}


def _company_row(model, company, pk, field):
    """A related row named by the client: it selects, the company authorises."""
    pk = _int(pk, field)
    if pk is None:
        return None
    row = model.objects.filter(company=company, pk=pk).first()
    if row is None:
        raise services.FinanceNotFound('No encontrado.')
    return row


def _member(company, pk):
    pk = _int(pk, 'owner')
    if pk is None:
        return None
    from django.contrib.auth import get_user_model

    from .tenancy import get_membership

    user = get_user_model().objects.filter(pk=pk).first()
    if user is None or get_membership(user, company) is None:
        raise services.FinanceError('El responsable no pertenece a esta empresa.')
    return user


def _write_branch(request, company, raw):
    """
    The branch a NEW row goes to. None (general) needs company-wide scope; a
    branch the caller cannot operate in answers 404, like one that does not exist.
    """
    try:
        branch_id = _int(raw, 'branch')
    except services.FinanceError as exc:
        return None, _error(exc)
    if branch_id is None:
        if not has_company_wide_scope(request.user, company):
            return None, Response({'detail': 'Una obligación general exige alcance de toda la empresa.'},
                                  status=status.HTTP_403_FORBIDDEN)
        return None, None
    branch = Branch.objects.filter(company=company, pk=branch_id).first()
    if branch is None or not has_branch_access(request.user, branch):
        return None, _not_found()
    return branch, None


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------

def category_payload(category):
    return {'id': category.pk, 'direction': category.direction, 'name': category.name,
            'is_active': category.is_active}


def rule_payload(rule, today):
    open_pause = next((p for p in rule.pauses.all() if p.resumed_on is None), None)
    return {
        'id': rule.pk, 'concept': rule.concept, 'branch': _ref(rule.branch), 'category': _ref(rule.category),
        'payee_supplier': _ref(rule.payee_supplier), 'payee_name': rule.payee_name, 'amount': str(rule.amount),
        'currency': rule.currency, 'frequency': rule.frequency, 'anchor_date': rule.anchor_date.isoformat(),
        'due_day': rule.due_day, 'end_date': rule.end_date.isoformat() if rule.end_date else None,
        'owner': _person(rule.owner), 'planned_method': rule.planned_method, 'reference': rule.reference,
        'notes': rule.notes, 'ended': rule.ended_at is not None, 'end_reason': rule.end_reason,
        'paused': open_pause is not None,
        'paused_from': open_pause.paused_from.isoformat() if open_pause else None,
        'pauses': [{'paused_from': p.paused_from.isoformat(),
                    'resumed_on': p.resumed_on.isoformat() if p.resumed_on else None, 'reason': p.reason}
                   for p in rule.pauses.all()],
        'calendar_locked': rule.has_periods if hasattr(rule, 'has_periods') else rule.periods.exists(),
    }


def obligation_payload(obligation, today, snoozes=None):
    numbers = services.figures(obligation)
    repair = obligation.repair_order if obligation.origin_type == Obligation.ORIGIN_REPAIR else None
    return {
        'id': obligation.pk, 'reference': obligation.reference, 'direction': obligation.direction,
        'origin_type': obligation.origin_type, 'concept': obligation.concept,
        'customer': None if obligation.customer is None else {'id': obligation.customer_id,
                                                              'name': obligation.customer.display_name},
        'repair': None if repair is None else {'id': repair.pk, 'number': repair.number,
                                               'status': numbers['order_status']},
        'creation_reason': obligation.creation_reason, 'credit': str(numbers['credit']),
        'branch': _ref(obligation.branch), 'category': _ref(obligation.category),
        'supplier': _ref(obligation.supplier), 'payee_name': obligation.payee_name,
        'owner': _person(obligation.owner), 'planned_method': obligation.planned_method,
        'rule_id': obligation.rule_id, 'period_index': obligation.period_index,
        'currency': numbers['currency'],
        'amount_original': None if obligation.amount_original is None else str(obligation.amount_original),
        'effective': str(numbers['effective']), 'paid': str(numbers['paid']), 'balance': str(numbers['balance']),
        'partially_paid': numbers['partially_paid'],
        'issue_date': obligation.issue_date.isoformat(),
        'due_date': obligation.due_date.isoformat() if obligation.due_date else None,
        'state': services.state(obligation, today=today, figures_=numbers),
        'lifecycle': obligation.lifecycle, 'void_reason': obligation.void_reason,
        'void_kind': obligation.void_kind, 'disputed': obligation.is_disputed,
        'owed_despite_pause': obligation.owed_despite_pause_at is not None,
        'dispute_reason': obligation.dispute_reason if obligation.is_disputed else '',
        'external_reference': obligation.external_reference, 'notes': obligation.notes,
        # FIN-REMIND-01: until when THE CALLER silenced this obligation's reminders (null: not snoozed).
        'snoozed_until': (snoozes or {}).get(obligation.pk),
    }


def payment_payload(payment):
    return {
        'id': payment.pk, 'amount': str(payment.amount), 'currency': payment.currency, 'method': payment.method,
        'paid_on': payment.paid_on.isoformat(), 'reference': payment.reference, 'notes': payment.notes,
        'recorded_by': _person(payment.recorded_by), 'recorded_at': payment.recorded_at.isoformat(),
        'reversed': payment.is_reversed, 'reversal_reason': payment.reversal_reason,
        'reversed_at': payment.reversed_at.isoformat() if payment.reversed_at else None,
    }


def adjustment_payload(row):
    return {
        'id': row.pk, 'kind': row.kind, 'amount': str(row.amount), 'is_amount_change': row.is_amount_change,
        'previous_effective': str(row.previous_effective), 'new_effective': str(row.new_effective),
        'reason': row.reason, 'actor': _person(row.actor), 'created_at': row.created_at.isoformat(),
    }


def _snoozes(user, ids, today):
    """{obligation id: ISO date} of the caller's own live snoozes among `ids`."""
    rows = ObligationReminderSnooze.objects.filter(user=user, obligation_id__in=ids, until__gte=today)
    return {obligation_id: until.isoformat() for obligation_id, until in rows.values_list('obligation_id', 'until')}


def _reminder_history(obligation, user):
    """How many reminders went out for this obligation, and the caller's own, newest first. Notices only."""
    own = (Notification.objects.filter(company_id=obligation.company_id, user=user, target_type=reminders.TARGET,
                                       target_id=obligation.pk, event__event_type='finance.obligation.due')
           .order_by('-created_at', '-pk'))
    sent = NotificationEvent.objects.filter(company_id=obligation.company_id, event_type='finance.obligation.due',
                                            target_type=reminders.TARGET, target_id=obligation.pk).count()
    return {'sent': sent, 'mine': [{'title': n.title, 'at': n.created_at.isoformat(), 'read': n.read_at is not None}
                                   for n in own[:10]]}


def _history(obligation):
    rows = AdminAuditLog.objects.filter(
        company_id=obligation.company_id, target_type=services.AUDIT_OBLIGATION, target_id=str(obligation.pk),
    ).select_related('actor').order_by('created_at', 'pk')
    return [{'action': row.action, 'actor': _person(row.actor), 'at': row.created_at.isoformat(),
             'metadata': row.metadata} for row in rows]


def _obligations(company):
    return (Obligation.objects.filter(company=company)
            .select_related('branch', 'category', 'supplier', 'owner', 'customer', 'repair_order'))


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

class _FinanceView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.method in ('POST', 'PATCH', 'PUT') and not hasattr(request.data, 'get'):
            raise ParseError('El cuerpo debe ser un objeto JSON.')

    def context(self, request, capability):
        return _context(request, capability)

    def obligation(self, request, company, pk):
        """The caller's obligation, or None (another company, another branch, a general row without scope)."""
        rows = services.visible(_obligations(company), request.user, company)
        return services.with_figures(rows).filter(pk=pk).first()


class FinanceCategoryListView(_FinanceView):
    def get(self, request):
        company, error = self.context(request, CAP_VIEW)
        if error:
            return error
        rows = FinanceCategory.objects.filter(company=company)
        direction = request.query_params.get('direction')
        if direction:
            rows = rows.filter(direction=direction)
        return Response({'results': [category_payload(c) for c in rows]})

    def post(self, request):
        company, error = self.context(request, CAP_MANAGE)
        if error:
            return error
        try:
            category = services.create_category(company, direction=request.data.get('direction', ''),
                                                name=request.data.get('name', ''), actor=request.user,
                                                request=request)
        except services.FinanceError as exc:
            return _error(exc)
        return Response(category_payload(category), status=status.HTTP_201_CREATED)


class FinanceCategoryDetailView(_FinanceView):
    def patch(self, request, pk):
        company, error = self.context(request, CAP_MANAGE)
        if error:
            return error
        category = FinanceCategory.objects.filter(company=company, pk=pk).first()
        if category is None:
            return _not_found()
        try:
            services.update_category(category, actor=request.user, name=request.data.get('name'),
                                     is_active=request.data.get('is_active'), request=request)
        except services.FinanceError as exc:
            return _error(exc)
        return Response(category_payload(category))


def _rules(company):
    return (RecurringObligationRule.objects.filter(company=company)
            .select_related('branch', 'category', 'payee_supplier', 'owner').prefetch_related('pauses')
            .annotate(has_periods=Exists(Obligation.objects.filter(rule=OuterRef('pk')))))


def _rule_fields(company, data, *, partial):
    fields = {}
    for name in ('concept', 'amount', 'payee_name', 'planned_method', 'reference', 'notes', 'frequency',
                 'currency'):
        if name in data:
            fields[name] = data.get(name)
    if 'due_day' in data:
        fields['due_day'] = data.get('due_day')
    if 'anchor_date' in data:
        fields['anchor_date'] = _date(data.get('anchor_date'), 'anchor_date')
    if 'category' in data:
        fields['category'] = _company_row(FinanceCategory, company, data.get('category'), 'category')
    if 'payee_supplier' in data:
        fields['payee_supplier'] = _company_row(Supplier, company, data.get('payee_supplier'), 'payee_supplier')
    if 'owner' in data:
        fields['owner'] = _member(company, data.get('owner'))
    if not partial and 'end_date' in data:
        fields['end_date'] = _date(data.get('end_date'), 'end_date')
    return fields


class FinanceRuleListView(_FinanceView):
    def get(self, request):
        company, error = self.context(request, CAP_VIEW)
        if error:
            return error
        today = services.company_today(company)
        rows = services.visible(_rules(company), request.user, company)
        return Response({'results': [rule_payload(r, today) for r in rows]})

    def post(self, request):
        company, error = self.context(request, CAP_MANAGE)
        if error:
            return error
        branch, error = _write_branch(request, company, request.data.get('branch'))
        if error:
            return error
        try:
            fields = _rule_fields(company, request.data, partial=False)
            for required in ('concept', 'amount', 'frequency', 'anchor_date', 'due_day'):
                if fields.get(required) in (None, ''):
                    raise services.FinanceError(f'Falta «{required}».')
            rule = services.create_rule(company, actor=request.user, branch=branch,
                                        generate_past=request.data.get('generate_past') in (True, 'true', '1', 1),
                                        request=request, **fields)
        except services.FinanceError as exc:
            return _error(exc)
        rule = _rules(company).get(pk=rule.pk)
        return Response(rule_payload(rule, services.company_today(company)), status=status.HTTP_201_CREATED)


class FinanceRuleDetailView(_FinanceView):
    def _rule(self, request, company, pk):
        return services.visible(_rules(company), request.user, company).filter(pk=pk).first()

    def get(self, request, pk):
        company, error = self.context(request, CAP_VIEW)
        if error:
            return error
        rule = self._rule(request, company, pk)
        if rule is None:
            return _not_found()
        today = services.company_today(company)
        body = rule_payload(rule, today)
        periods = services.with_figures(_obligations(company).filter(rule=rule)).order_by('-period_index')[:60]
        body['periods'] = [obligation_payload(p, today) for p in periods]
        return Response(body)

    def patch(self, request, pk):
        company, error = self.context(request, CAP_MANAGE)
        if error:
            return error
        rule = self._rule(request, company, pk)
        if rule is None:
            return _not_found()
        try:
            services.update_rule(rule, actor=request.user, request=request,
                                 **_rule_fields(company, request.data, partial=True))
        except services.FinanceError as exc:
            return _error(exc)
        return Response(rule_payload(_rules(company).get(pk=rule.pk), services.company_today(company)))


class FinanceRuleActionView(_FinanceView):
    ACTIONS = ('pause', 'resume', 'end', 'generate')

    def post(self, request, pk, action):
        if action not in self.ACTIONS:
            return _not_found()
        company, error = self.context(request, CAP_MANAGE)
        if error:
            return error
        rule = services.visible(_rules(company), request.user, company).filter(pk=pk).first()
        if rule is None:
            return _not_found()
        try:
            if action == 'pause':
                services.pause_rule(rule, reason=request.data.get('reason'), actor=request.user, request=request)
            elif action == 'resume':
                services.resume_rule(rule, actor=request.user, request=request)
            elif action == 'end':
                services.end_rule(rule, end_date=_date(request.data.get('end_date'), 'end_date'),
                                  reason=request.data.get('reason'), actor=request.user, request=request)
            else:
                services.generate_period(rule, index=request.data.get('period_index'), actor=request.user,
                                         request=request)
        except services.FinanceError as exc:
            return _error(exc)
        return Response(rule_payload(_rules(company).get(pk=rule.pk), services.company_today(company)))


class FinanceObligationListView(_FinanceView):
    def get(self, request):
        company, error = self.context(request, CAP_VIEW)
        if error:
            return error
        today = services.company_today(company)
        params = request.query_params
        rows = services.with_figures(services.visible(_obligations(company), request.user, company))
        try:
            if params.get('direction'):
                if params['direction'] not in FinanceDirection.values:
                    raise services.FinanceError('Tipo inválido.')
                rows = rows.filter(direction=params['direction'])
            for name in ('branch', 'category', 'rule'):
                value = _int(params.get(name), name)
                if value is not None:
                    rows = rows.filter(**{f'{name}_id': value})
            if params.get('disputed') in ('true', '1'):
                rows = rows.filter(disputed_at__isnull=False, dispute_resolved_at__isnull=True)
            due_from, due_to = _date(params.get('due_from'), 'due_from'), _date(params.get('due_to'), 'due_to')
            if due_from:
                rows = rows.filter(due_date__gte=due_from)
            if due_to:
                rows = rows.filter(due_date__lte=due_to)
            if params.get('state'):
                rows = services.filter_state(rows, params['state'], today=today)
            page = min(_MAX_PAGE, max(1, _int(params.get('page'), 'page') or 1))
            size = min(_MAX_PAGE_SIZE, max(1, _int(params.get('page_size'), 'page_size') or _PAGE_SIZE))
        except services.FinanceError as exc:
            return _error(exc)
        rows = rows.order_by('due_date', 'pk')
        count = rows.count()
        chunk = rows[(page - 1) * size: page * size]
        chunk = list(chunk)
        snoozes = _snoozes(request.user, [o.pk for o in chunk], today)
        return Response({'count': count, 'page': page, 'page_size': size,
                         'results': [obligation_payload(o, today, snoozes) for o in chunk]})

    def post(self, request):
        data = request.data
        receivable = data.get('direction') == FinanceDirection.RECEIVABLE
        company, error = self.context(request, CAP_RECEIVABLE_MANUAL if receivable else CAP_MANAGE)
        if error:
            return error
        if data.get('direction') not in (None, '', *FinanceDirection.values):
            return _error(services.FinanceError('Tipo inválido.'))
        branch, error = _write_branch(request, company, data.get('branch'))
        if error:
            return error
        if receivable:
            return self._create_receivable(request, company, branch)
        try:
            obligation = services.create_obligation(
                company, actor=request.user, concept=data.get('concept'), amount=data.get('amount'),
                due_date=_date(data.get('due_date'), 'due_date'), issue_date=_date(data.get('issue_date'),
                                                                                    'issue_date'),
                branch=branch, currency=data.get('currency'),
                category=_company_row(FinanceCategory, company, data.get('category'), 'category'),
                supplier=_company_row(Supplier, company, data.get('supplier'), 'supplier'),
                payee_name=data.get('payee_name', ''), owner=_member(company, data.get('owner')),
                planned_method=data.get('planned_method', ''), external_reference=data.get('external_reference', ''),
                notes=data.get('notes', ''), idempotency_key=_key(request), request=request,
            )
        except services.FinanceError as exc:
            return _error(exc)
        row = services.with_figures(_obligations(company)).get(pk=obligation.pk)
        return Response(obligation_payload(row, services.company_today(company)), status=status.HTTP_201_CREATED)


    def _create_receivable(self, request, company, branch):
        """A receivable by hand (FIN-E): a customer and a reason, never a link to a repair or a sale."""
        data = request.data
        linked = [name for name in receivables.FORBIDDEN_LINKS if name in data]
        if linked:
            return _error(services.FinanceError(
                'Una cuenta por cobrar manual no se vincula a una reparación ni a una venta: '
                'el saldo de una reparación se cobra desde su orden.'))
        try:
            if data.get('customer') in (None, ''):
                raise services.FinanceError('Elige el cliente.')
            obligation = receivables.create_receivable(
                company, actor=request.user, customer=_company_row(Customer, company, data.get('customer'), 'customer'),
                concept=data.get('concept'), amount=data.get('amount'),
                due_date=_date(data.get('due_date'), 'due_date'), reason=data.get('reason'),
                issue_date=_date(data.get('issue_date'), 'issue_date'), branch=branch, currency=data.get('currency'),
                category=_company_row(FinanceCategory, company, data.get('category'), 'category'),
                owner=_member(company, data.get('owner')), planned_method=data.get('planned_method', ''),
                external_reference=data.get('external_reference', ''), notes=data.get('notes', ''),
                idempotency_key=_key(request), request=request,
            )
        except services.FinanceError as exc:
            return _error(exc)
        row = services.with_figures(_obligations(company)).get(pk=obligation.pk)
        return Response(obligation_payload(row, services.company_today(company)), status=status.HTTP_201_CREATED)


class FinanceObligationDetailView(_FinanceView):
    def get(self, request, pk):
        company, error = self.context(request, CAP_VIEW)
        if error:
            return error
        obligation = self.obligation(request, company, pk)
        if obligation is None:
            return _not_found()
        today = services.company_today(company)
        body = obligation_payload(obligation, today, _snoozes(request.user, [obligation.pk], today))
        body['reminders'] = _reminder_history(obligation, request.user)
        body['payments'] = [payment_payload(p) for p in obligation.payments.select_related('recorded_by')]
        body['adjustments'] = [adjustment_payload(a) for a in obligation.adjustments.select_related('actor')]
        body['history'] = _history(obligation)
        return Response(body)


class _ObligationAction(_FinanceView):
    capability = ''

    def post(self, request, pk, **kwargs):
        company, error = self.context(request, self.capability)
        if error:
            return error
        obligation = self.obligation(request, company, pk)
        if obligation is None:
            return _not_found()
        try:
            result = self.act(request, company, obligation, **kwargs)
        except services.FinanceError as exc:
            return _error(exc)
        fresh = services.with_figures(_obligations(company)).get(pk=obligation.pk)
        body = obligation_payload(fresh, services.company_today(company))
        if isinstance(result, tuple):
            key, value, code = result
            return Response({key: value, 'obligation': body}, status=code)
        return Response(body)

    def act(self, request, company, obligation, **kwargs):  # pragma: no cover - abstract
        raise NotImplementedError


class FinancePaymentView(_ObligationAction):
    capability = CAP_RECORD

    def act(self, request, company, obligation):
        payment = services.record_payment(
            obligation, amount=request.data.get('amount'), method=request.data.get('method'), actor=request.user,
            paid_on=_date(request.data.get('paid_on'), 'paid_on'), reference=request.data.get('reference', ''),
            notes=request.data.get('notes', ''), idempotency_key=_key(request), request=request,
        )
        return 'payment', payment_payload(payment), status.HTTP_201_CREATED


class FinancePaymentReverseView(_ObligationAction):
    capability = CAP_REVERSE

    def act(self, request, company, obligation, payment_pk):
        payment = ObligationPayment.objects.filter(obligation=obligation, pk=payment_pk).first()
        if payment is None:
            raise services.FinanceNotFound('No encontrado.')
        payment = services.reverse_payment(payment, reason=request.data.get('reason'), actor=request.user,
                                           request=request)
        return 'payment', payment_payload(payment), status.HTTP_200_OK


class FinanceAdjustmentView(_ObligationAction):
    capability = CAP_ADJUST

    def act(self, request, company, obligation):
        row = services.adjust(
            obligation, kind=request.data.get('kind'), reason=request.data.get('reason'), actor=request.user,
            amount=request.data.get('amount'), new_amount=request.data.get('new_amount'),
            idempotency_key=_key(request), request=request,
        )
        return 'adjustment', adjustment_payload(row), status.HTTP_201_CREATED


class FinanceVoidView(_ObligationAction):
    capability = CAP_VOID

    def act(self, request, company, obligation):
        services.void(obligation, reason=request.data.get('reason'), actor=request.user, request=request)


class FinanceDisputeView(_ObligationAction):
    capability = CAP_DISPUTE

    def act(self, request, company, obligation, resolve=False):
        if resolve:
            services.resolve_dispute(obligation, resolution=request.data.get('resolution'), actor=request.user,
                                     request=request)
        else:
            services.mark_disputed(obligation, reason=request.data.get('reason'), actor=request.user,
                                   request=request)


class FinanceSummaryView(_FinanceView):
    def get(self, request):
        company, error = self.context(request, CAP_VIEW)
        if error:
            return error
        today = services.company_today(company)
        rows = services.visible(Obligation.objects.filter(company=company), request.user, company)
        direction = request.query_params.get('direction')
        if direction in FinanceDirection.values:
            rows = rows.filter(direction=direction)
        body = services.totals(rows, today=today)
        body['today'] = today.isoformat()
        body['notice'] = ('Control interno de obligaciones. No es contabilidad completa ni sustituye a los '
                          'registros de SUNAT.')
        return Response(body)


def policy_payload(policy):
    return {
        'enabled': policy.enabled, 'offsets': list(policy.offsets), 'overdue_every_days': policy.overdue_every_days,
        'overdue_max': policy.overdue_max, 'email_enabled': policy.email_enabled,
        'email_offsets': list(policy.email_offsets), 'email_overdue': policy.email_overdue,
        'last_run_at': policy.last_run_at.isoformat() if policy.last_run_at else None,
        'last_error': policy.last_error,
    }


class FinanceReminderPolicyView(_FinanceView):
    """When this company is reminded of what it must pay. Reading needs finance.view; changing it, the automation capability."""

    def get(self, request):
        company, error = self.context(request, CAP_VIEW)
        if error:
            return error
        body = policy_payload(reminders.policy_for(company))
        body['can_manage'] = has_capability(request.user, company, CAP_AUTOMATION)
        return Response(body)

    def patch(self, request):
        company, error = self.context(request, CAP_AUTOMATION)
        if error:
            return error
        try:
            policy = reminders.update_policy(company, actor=request.user, request=request, **dict(request.data.items()))
        except services.FinanceError as exc:
            return _error(exc)
        body = policy_payload(policy)
        body['can_manage'] = True
        return Response(body)


class FinanceSnoozeView(_FinanceView):
    """«Posponer aviso»: silences the CALLER's reminders of one obligation. The debt, the state and the others' notices stay."""

    def post(self, request, pk):
        company, error = self.context(request, CAP_VIEW)
        if error:
            return error
        obligation = self.obligation(request, company, pk)
        if obligation is None:
            return _not_found()
        try:
            row = reminders.snooze(obligation, user=request.user, days=request.data.get('days'),
                                   today=services.company_today(company))
        except services.FinanceError as exc:
            return _error(exc)
        return Response({'snoozed_until': row.until.isoformat()})

    def delete(self, request, pk):
        company, error = self.context(request, CAP_VIEW)
        if error:
            return error
        obligation = self.obligation(request, company, pk)
        if obligation is None:
            return _not_found()
        reminders.unsnooze(obligation, user=request.user)
        return Response({'snoozed_until': None})
