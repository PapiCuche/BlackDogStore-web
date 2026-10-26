"""
FINANCE-OBLIGATIONS-01 · FIN-F — manual collection / payment messages (docs/adr-finance-obligations.md §7.3).

    /api/admin/finance/obligations/<pk>/messages/          GET   finance.messages.prepare — the templates on offer
    /api/admin/finance/obligations/<pk>/message-preview/   POST  finance.messages.prepare — the server-written text
                                                           (read-only; optional edited `text` is re-validated)
    /api/admin/finance/obligations/<pk>/message-copied/    POST  finance.messages.prepare — «Copiar mensaje» audit
    /api/admin/finance/message-templates/                  GET   finance.templates.manage
    /api/admin/finance/message-templates/<key>/            PUT / DELETE finance.templates.manage (company wording)

Nothing here sends anything (no WhatsApp, Meta, SMS, e-mail, push, no link that carries the text elsewhere) and
nothing economic is written: copying is not sending. The text is filled on the server from the obligation's
current rows; amounts or names in the request are never read. The company is resolved on the server and a row of
another company or of a branch the caller cannot operate in answers 404.
"""
from __future__ import annotations

from django.db import transaction
from rest_framework import status
from rest_framework.response import Response

from .finance import messages, services
from .finance_views import _FinanceView, _error, _not_found
from .models import FinanceMessageTemplate

CAP_PREPARE = 'finance.messages.prepare'
CAP_TEMPLATES = 'finance.templates.manage'
AUDIT_MESSAGE = 'finance_message'
AUDIT_TEMPLATE = 'finance_message_template'


def _edited_text(data):
    """The person's edited text, or None when the template itself is wanted. Never coerced."""
    value = data.get('text')
    if value is None:
        return None
    if not isinstance(value, str):
        raise services.FinanceError('Se esperaba texto.')
    return value


class _MessageView(_FinanceView):
    def prepared(self, request, pk):
        """(company, obligation, plan) or an error Response."""
        company, error = self.context(request, CAP_PREPARE)
        if error:
            return None, None, None, error
        obligation = self.obligation(request, company, pk)
        if obligation is None:
            return None, None, None, _not_found()
        return company, obligation, messages.plan(obligation, company, today=services.company_today(company)), None

    @staticmethod
    def choose(request, plan):
        """The template key the client named, if this obligation is offered it. Else a 400 (FinanceError)."""
        key = services._text(request.data.get('template'))
        if key not in plan['templates']:
            raise services.FinanceError(plan['blocked'] or 'Esa plantilla no está disponible para esta obligación.')
        return key

    @staticmethod
    def build(company, plan, key, text):
        """(final text, company template used, text the template alone would give)."""
        body, customized = messages.body_for(company, key)
        default_text = messages.render(body, plan['facts'])
        if text is None:
            return default_text, customized, default_text
        return messages.render(text, plan['facts']), customized, default_text


class FinanceMessageListView(_MessageView):
    def get(self, request, pk):
        company, obligation, plan, error = self.prepared(request, pk)
        if error:
            return error
        return Response({
            'obligation': obligation.pk, 'templates': [
                {'key': key, 'label': messages.LABELS[key], 'audience': messages.audience(key),
                 'company_template': messages.body_for(company, key)[1]} for key in plan['templates']],
            'suggested': plan['suggested'], 'blocked': plan['blocked'], 'warnings': plan['warnings'],
            'data': plan['data'], 'guidance': messages.GUIDANCE, 'max_length': messages.MAX_LENGTH,
            'variables': list(messages.VARIABLES), 'sends_nothing': True,
        })


class FinanceMessagePreviewView(_MessageView):
    def post(self, request, pk):
        company, obligation, plan, error = self.prepared(request, pk)
        if error:
            return error
        try:
            key = self.choose(request, plan)
            text, customized, _default = self.build(company, plan, key, _edited_text(request.data))
        except services.FinanceError as exc:
            return _error(exc)
        return Response({
            'template': key, 'label': messages.LABELS[key], 'text': text,
            'company_template': customized and _edited_text(request.data) is None,
            'data': plan['data'], 'warnings': plan['warnings'], 'guidance': messages.GUIDANCE,
            'sends_nothing': True,
        })


class FinanceMessageCopiedView(_MessageView):
    """«Copiar mensaje»: the copy itself is the browser's; this only leaves the audit record. It sends nothing."""

    def post(self, request, pk):
        company, obligation, plan, error = self.prepared(request, pk)
        if error:
            return error
        try:
            key = self.choose(request, plan)
            text = _edited_text(request.data)
            final, _customized, default_text = self.build(company, plan, key, text)
        except services.FinanceError as exc:
            return _error(exc)
        # Its own target type: the obligation's history (and its figures) stay exactly as they were.
        services._audit(
            actor=request.user, action='finance_message_copied', target_type=AUDIT_MESSAGE, target_id=obligation.pk,
            company=company, request=request,
            metadata={'template': key, 'edited': final != default_text, 'branch_id': obligation.branch_id,
                      'direction': obligation.direction, 'origin_type': obligation.origin_type})
        return Response({'copied': True, 'sent': False, 'template': key})


# ---------------------------------------------------------------------------
# The company's own wording
# ---------------------------------------------------------------------------

def _template_payload(company, key, row=None):
    row = row if row is not None else FinanceMessageTemplate.objects.filter(company=company, key=key).first()
    return {
        'key': key, 'label': messages.LABELS[key], 'audience': messages.audience(key),
        'default': messages.DEFAULT_BODIES[key], 'body': row.body if row else messages.DEFAULT_BODIES[key],
        'customized': row is not None, 'variables': list(messages.VARIABLES), 'max_length': messages.MAX_LENGTH,
    }


class FinanceMessageTemplateListView(_FinanceView):
    def get(self, request):
        company, error = self.context(request, CAP_TEMPLATES)
        if error:
            return error
        rows = {row.key: row for row in FinanceMessageTemplate.objects.filter(company=company)}
        return Response({'results': [_template_payload(company, key, rows.get(key))
                                     for key in messages.enabled_keys()]})


class FinanceMessageTemplateDetailView(_FinanceView):
    def _key(self, key):
        return key if key in messages.enabled_keys() else None

    def put(self, request, key):
        company, error = self.context(request, CAP_TEMPLATES)
        if error:
            return error
        if self._key(key) is None:
            return _not_found()
        body = request.data.get('body')
        try:
            if not isinstance(body, str):
                raise services.FinanceError('Se esperaba texto.')
            messages.check(body)
        except services.FinanceError as exc:
            return _error(exc)
        body = body.replace('\r\n', '\n').replace('\r', '\n').strip()
        with transaction.atomic():
            row, _created = FinanceMessageTemplate.objects.update_or_create(
                company=company, key=key, defaults={'body': body, 'updated_by': request.user})
            services._audit(actor=request.user, action='finance_message_template_saved',
                            target_type=AUDIT_TEMPLATE, target_id=key, company=company, request=request,
                            metadata={'key': key})
        return Response(_template_payload(company, key, row))

    def delete(self, request, key):
        company, error = self.context(request, CAP_TEMPLATES)
        if error:
            return error
        if self._key(key) is None:
            return _not_found()
        with transaction.atomic():
            removed, _ = FinanceMessageTemplate.objects.filter(company=company, key=key).delete()
            if removed:
                services._audit(actor=request.user, action='finance_message_template_restored',
                                target_type=AUDIT_TEMPLATE, target_id=key, company=company, request=request,
                                metadata={'key': key})
        return Response(_template_payload(company, key, None), status=status.HTTP_200_OK)
