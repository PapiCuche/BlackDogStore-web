"""
TRADE-IN VALUATION PRO · Phase 1 — `/api/admin/trade-in/…`: the per-company reception tariff for the panel.

AUTHORITY (docs/adr-trade-in-valuation.md §4)
---------------------------------------------
    sales.tradein.tariff.view         list, search, detail with history, export, read the policy
    sales.tradein.tariff.manage       add a device, propose a price, link a catalogue model, deactivate,
                                      import (preview and apply), withdraw a pending change
    sales.tradein.tariff.approve      approve or reject a pending change, reactivate, change the policy
    sales.tradein.tariff.approve_own  approve a change one proposed (with a reason; flagged in the audit)

TENANT. The company is resolved on the server (`?company=` only selects among the caller's own); an entry, a
version or an import of another company answers like one that does not exist (404). The tariff is the company's,
not a branch's. Writes are throttled like the quotes'; every write leaves an `AdminAuditLog` with the company.
"""
from __future__ import annotations

from django.db.models import Q
from django.http import HttpResponse
from rest_framework import permissions, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from . import trade_in_tariff as tariff
from . import trade_in_tariff_io as tariff_io
from . import xlsx_reader
from .models import TradeInTariffEntry, TradeInTariffImport, TradeInTariffVersion
from .tenancy import CrossTenantError, NoTenantError, has_capability, resolve_company_for_user
from .throttles import (
    AdminExportThrottle,
    AdminImportThrottle,
    AdminSalesQuotesThrottle,
    AdminSalesQuoteWriteThrottle,
)

_NOT_FOUND = 'No encontrado.'
PAGE_SIZE = 50
EXPORT_FILENAME = 'tarifario-parte-de-pago.xlsx'
#: The tariff workbook travels on the GENERAL request tier (1 MiB, `request_limits`; no proxy change). Its own cap
#: leaves room for the multipart envelope; a real tariff is tens of KB.
MAX_TARIFF_UPLOAD_BYTES = 960 * 1024
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def _company(request, *capabilities):
    """(company, error): the caller's company, if they hold ANY of `capabilities` in it."""
    raw = request.query_params.get('company')
    requested = None
    if raw not in (None, ''):
        try:
            requested = int(raw)
        except (TypeError, ValueError):
            return None, Response({'detail': 'Parámetro "company" inválido.'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        company = resolve_company_for_user(request.user, requested)
    except CrossTenantError:
        return None, Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
    except NoTenantError as exc:
        return None, Response({'detail': str(exc)}, status=status.HTTP_403_FORBIDDEN)
    if not any(has_capability(request.user, company, code) for code in capabilities):
        return None, Response({'detail': 'No tienes permisos para esta operación.'}, status=status.HTTP_403_FORBIDDEN)
    return company, None


def _error(exc: tariff.TariffError) -> Response:
    if exc.code == 'not_found':
        return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
    body = {'detail': str(exc), 'code': exc.code, **exc.extra}
    if exc.field:
        body['field'] = exc.field
    return Response(body, status=exc.status)


def _not_found() -> Response:
    return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)


def _body(request) -> dict:
    return request.data if isinstance(request.data, dict) else {}


def _person(user) -> str | None:
    if user is None:
        return None
    return (user.get_full_name() or user.username or '').strip()


def _money(value):
    return None if value is None else str(value)


def version_payload(version) -> dict:
    return {
        'id': version.pk, 'number': version.number, 'status': version.status,
        'status_label': dict(TradeInTariffVersion.STATUS_CHOICES).get(version.status, version.status),
        'currency': version.currency,
        **{field: _money(getattr(version, field)) for field in tariff.MONEY_FIELDS},
        'valid_from': version.valid_from.isoformat(),
        'valid_until': version.valid_until.isoformat() if version.valid_until else None,
        'reason': version.reason, 'source': version.source, 'import_id': version.import_batch_id,
        'previous_number': version.previous.number if version.previous_id else None,
        'approval_reasons': [{'code': code, 'label': tariff.REASON_LABELS.get(code, code)}
                             for code in version.approval_reasons or []],
        'approval_kind': version.approval_kind,
        'proposed_by': _person(version.proposed_by), 'proposed_at': version.proposed_at,
        'decided_by': _person(version.decided_by), 'decided_at': version.decided_at,
        'decision_note': version.decision_note,
        'warnings': tariff.margin_warnings(version),
    }


def _actions(user, company, entry, pending) -> dict:
    """What the caller may do NOW with this entry. The server decides again on each request."""
    manage = has_capability(user, company, tariff.CAP_MANAGE)
    approve = has_capability(user, company, tariff.CAP_APPROVE)
    own_pending = pending is not None and pending.proposed_by_id == user.pk
    return {
        'propose': manage and entry.is_active and pending is None,
        'deactivate': manage and entry.is_active,
        'reactivate': approve and not entry.is_active,
        'link_model': manage,
        'approve': (approve and entry.is_active and pending is not None
                    and (not own_pending or has_capability(user, company, tariff.CAP_APPROVE_OWN))),
        'reject': approve and pending is not None,
        'withdraw': manage and pending is not None and (own_pending or approve),
    }


def entry_row(entry, *, today) -> dict:
    current = tariff.effective_version(entry, on=today)
    pending = tariff.pending_version(entry)
    return {
        'id': entry.pk, 'brand': entry.brand, 'model_name': entry.model_name, 'capacity': entry.capacity,
        'sim': entry.sim, 'region': entry.region, 'is_active': entry.is_active,
        'product_model': entry.product_model_id,
        'product_model_name': entry.product_model.name if entry.product_model_id else '',
        'current': version_payload(current) if current else None,
        'pending': version_payload(pending) if pending else None,
        'updated_at': entry.updated_at,
    }


def entry_detail(user, company, entry) -> dict:
    today = tariff.company_today(company)
    row = entry_row(entry, today=today)
    pending = tariff.pending_version(entry)
    versions = entry.versions.select_related('decided_by', 'proposed_by', 'previous').order_by('-number')
    row.update({
        'deactivated_at': entry.deactivated_at, 'deactivated_by': _person(entry.deactivated_by),
        'deactivation_reason': entry.deactivation_reason,
        'created_by': _person(entry.created_by), 'created_at': entry.created_at,
        'versions': [version_payload(v) for v in versions],
        'actions': _actions(user, company, entry, pending),
        'today': today.isoformat(),
    })
    return row


def _entries(company):
    return TradeInTariffEntry.objects.filter(company=company).select_related('product_model')


def _filtered(request, company):
    """The list's filters, shared with the export. (rows, error)."""
    rows = _entries(company)
    params = request.query_params
    for field in ('brand', 'capacity', 'sim', 'region'):
        value = (params.get(field) or '').strip()
        if value:
            rows = rows.filter(**{f'{field}__iexact': value})
    model = (params.get('model') or '').strip()
    if model:
        rows = rows.filter(model_name__icontains=model)
    term = (params.get('q') or '').strip()
    if term:
        for word in term.split()[:6]:
            rows = rows.filter(Q(brand__icontains=word) | Q(model_name__icontains=word) | Q(capacity__icontains=word)
                               | Q(sim__icontains=word) | Q(region__icontains=word))
    active = (params.get('active') or 'true').strip().lower()
    if active in ('true', '1'):
        rows = rows.filter(is_active=True)
    elif active in ('false', '0'):
        rows = rows.filter(is_active=False)
    elif active != 'all':
        return None, Response({'detail': 'Filtro «active» no válido (true, false o all).'},
                              status=status.HTTP_400_BAD_REQUEST)
    if (params.get('pending') or '').strip().lower() in ('true', '1'):
        rows = rows.filter(versions__status=TradeInTariffVersion.STATUS_PENDING)
    raw_model = (params.get('product_model') or '').strip()
    if raw_model:
        try:
            rows = rows.filter(product_model_id=int(raw_model))
        except ValueError:
            return None, Response({'detail': 'Modelo no válido.'}, status=status.HTTP_400_BAD_REQUEST)
    return rows.distinct(), None


class AdminTradeInTariffListView(APIView):
    """GET (list, search and filters) and POST (a new device with its first price, pending)."""

    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        if self.request.method == 'POST':
            return [AdminSalesQuoteWriteThrottle()]
        return [AdminSalesQuotesThrottle()]

    def get(self, request):
        company, error = _company(request, tariff.CAP_VIEW)
        if error:
            return error
        rows, error = _filtered(request, company)
        if error:
            return error
        try:
            page = max(1, int(request.query_params.get('page') or 1))
        except ValueError:
            page = 1
        count = rows.count()
        start = (page - 1) * PAGE_SIZE
        today = tariff.company_today(company)
        results = [entry_row(entry, today=today) for entry in rows[start:start + PAGE_SIZE]]
        pending = TradeInTariffVersion.objects.filter(company=company,
                                                      status=TradeInTariffVersion.STATUS_PENDING).count()
        brands = list(TradeInTariffEntry.objects.filter(company=company).order_by('brand')
                      .values_list('brand', flat=True).distinct())
        return Response({
            'results': results, 'count': count, 'page': page, 'page_size': PAGE_SIZE,
            'pending_count': pending, 'brands': brands, 'today': today.isoformat(),
            'can': {
                'manage': has_capability(request.user, company, tariff.CAP_MANAGE),
                'approve': has_capability(request.user, company, tariff.CAP_APPROVE),
            },
        })

    def post(self, request):
        company, error = _company(request, tariff.CAP_MANAGE)
        if error:
            return error
        try:
            entry, _version = tariff.create_entry(actor=request.user, company=company, data=_body(request),
                                                  request=request)
        except tariff.TariffError as exc:
            return _error(exc)
        return Response(entry_detail(request.user, company, entry), status=status.HTTP_201_CREATED)


def _entry(company, pk):
    return _entries(company).filter(pk=pk).first()


class AdminTradeInTariffDetailView(APIView):
    """GET (with every version) and PATCH (only the catalogue model link: the identity never changes)."""

    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        if self.request.method == 'PATCH':
            return [AdminSalesQuoteWriteThrottle()]
        return [AdminSalesQuotesThrottle()]

    def get(self, request, pk):
        company, error = _company(request, tariff.CAP_VIEW)
        if error:
            return error
        entry = _entry(company, pk)
        if entry is None:
            return _not_found()
        return Response(entry_detail(request.user, company, entry))

    def patch(self, request, pk):
        company, error = _company(request, tariff.CAP_MANAGE)
        if error:
            return error
        entry = _entry(company, pk)
        if entry is None:
            return _not_found()
        body = _body(request)
        refused = sorted(set(body) & set(tariff.IDENTITY_FIELDS))
        if refused:
            return Response({'detail': 'La identidad de un equipo del tarifario no cambia: desactívalo y crea otro.',
                             'code': 'identity_fixed', 'fields': refused}, status=status.HTTP_400_BAD_REQUEST)
        if 'product_model' not in body:
            return Response({'detail': 'Nada que cambiar.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            entry = tariff.link_model(actor=request.user, company=company, entry=entry,
                                      product_model=body.get('product_model'), request=request)
        except tariff.TariffError as exc:
            return _error(exc)
        return Response(entry_detail(request.user, company, _entry(company, entry.pk)))


class _EntryAction(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminSalesQuoteWriteThrottle]
    capability = tariff.CAP_MANAGE

    def act(self, request, company, entry):  # pragma: no cover - abstract
        raise NotImplementedError

    def post(self, request, pk):
        company, error = _company(request, self.capability)
        if error:
            return error
        entry = _entry(company, pk)
        if entry is None:
            return _not_found()
        try:
            result = self.act(request, company, entry)
        except tariff.TariffError as exc:
            return _error(exc)
        return Response(result, status=getattr(self, 'success_status', status.HTTP_200_OK))


class AdminTradeInTariffProposeView(_EntryAction):
    """POST a new price for the device: approved by the policy when not relevant, otherwise pending."""

    success_status = status.HTTP_201_CREATED

    def act(self, request, company, entry):
        version = tariff.propose_version(actor=request.user, company=company, entry=entry, data=_body(request),
                                         request=request)
        return {'version': version_payload(version), 'entry': entry_detail(request.user, company, _entry(company, entry.pk))}


class AdminTradeInTariffDeactivateView(_EntryAction):
    def act(self, request, company, entry):
        tariff.deactivate_entry(actor=request.user, company=company, entry=entry,
                                reason=_body(request).get('reason'), request=request)
        return entry_detail(request.user, company, _entry(company, entry.pk))


class AdminTradeInTariffReactivateView(_EntryAction):
    capability = tariff.CAP_APPROVE

    def act(self, request, company, entry):
        tariff.reactivate_entry(actor=request.user, company=company, entry=entry,
                                reason=_body(request).get('reason'), request=request)
        return entry_detail(request.user, company, _entry(company, entry.pk))


class _VersionAction(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminSalesQuoteWriteThrottle]
    capability = tariff.CAP_APPROVE

    def act(self, request, company, version):  # pragma: no cover - abstract
        raise NotImplementedError

    def post(self, request, pk):
        company, error = _company(request, self.capability)
        if error:
            return error
        version = (TradeInTariffVersion.objects.filter(company=company, pk=pk)
                   .select_related('entry').first())
        if version is None:
            return _not_found()
        try:
            self.act(request, company, version)
        except tariff.TariffError as exc:
            return _error(exc)
        version = TradeInTariffVersion.objects.select_related('decided_by', 'proposed_by', 'previous').get(pk=pk)
        return Response({'version': version_payload(version),
                         'entry': entry_detail(request.user, company, _entry(company, version.entry_id))})


class AdminTradeInTariffApproveView(_VersionAction):
    def act(self, request, company, version):
        tariff.approve_version(actor=request.user, company=company, version=version,
                               note=_body(request).get('note') or '', request=request)


class AdminTradeInTariffRejectView(_VersionAction):
    def act(self, request, company, version):
        tariff.reject_version(actor=request.user, company=company, version=version,
                              note=_body(request).get('note') or '', request=request)


class AdminTradeInTariffWithdrawView(_VersionAction):
    capability = tariff.CAP_MANAGE

    def act(self, request, company, version):
        tariff.withdraw_version(actor=request.user, company=company, version=version,
                                note=_body(request).get('note') or '', request=request)


class AdminTradeInTariffExportView(APIView):
    """GET the tariff (the list's filters apply) as the .xlsx that is also the import template."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminExportThrottle]

    def get(self, request):
        company, error = _company(request, tariff.CAP_VIEW)
        if error:
            return error
        rows, error = _filtered(request, company)
        if error:
            return error
        data = tariff_io.export_bytes(rows.select_related('product_model'))
        from .models import AdminAuditLog

        AdminAuditLog.log(actor=request.user, action='trade_in_tariff_exported', target_type='trade_in_tariff',
                          target_id='', metadata={'company_id': company.pk, 'rows': rows.count()},
                          request=request, company=company)
        response = HttpResponse(data, content_type=XLSX)
        response['Content-Disposition'] = f'attachment; filename="{EXPORT_FILENAME}"'
        return response


def import_payload(batch) -> dict:
    return {
        'id': batch.pk, 'status': batch.status, 'original_filename': batch.original_filename,
        'file_sha256': batch.file_sha256, 'summary': batch.summary, 'rows': batch.rows,
        'created_at': batch.created_at, 'created_by': _person(batch.created_by),
        'applied_at': batch.applied_at, 'applied_by': _person(batch.applied_by),
    }


class AdminTradeInTariffImportPreviewView(APIView):
    """POST multipart `file`: read and check every row; nothing of the tariff is written."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminImportThrottle]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        company, error = _company(request, tariff.CAP_MANAGE)
        if error:
            return error
        upload = request.FILES.get('file')
        if upload is None:
            return Response({'detail': 'Adjunta un archivo .xlsx.'}, status=status.HTTP_400_BAD_REQUEST)
        if (upload.size or 0) > MAX_TARIFF_UPLOAD_BYTES:
            return Response({'detail': f'El archivo pesa más de {MAX_TARIFF_UPLOAD_BYTES // 1024} KB, el límite del '
                                       'tarifario. Quita hojas o formatos que no sean el tarifario y vuelve a subirlo.',
                             'code': 'file_too_large'}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        try:
            batch = tariff_io.preview(actor=request.user, company=company, upload=upload, filename=upload.name,
                                      request=request)
        except xlsx_reader.XlsxError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except tariff.TariffError as exc:
            return _error(exc)
        return Response(import_payload(batch), status=status.HTTP_201_CREATED)


class AdminTradeInTariffImportApplyView(APIView):
    """POST: write what the preview staged, all or nothing."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminImportThrottle]

    def post(self, request, pk):
        company, error = _company(request, tariff.CAP_MANAGE)
        if error:
            return error
        batch = TradeInTariffImport.objects.filter(company=company, pk=pk).first()
        if batch is None:
            return _not_found()
        try:
            result = tariff_io.apply(actor=request.user, company=company, batch=batch, request=request)
        except tariff.TariffError as exc:
            return _error(exc)
        batch.refresh_from_db()
        return Response({'import': import_payload(batch), 'result': result})


def policy_payload(policy) -> dict:
    return {
        'tariff_approval_mode': policy.tariff_approval_mode,
        'tariff_increase_tolerance_percent': str(policy.tariff_increase_tolerance_percent),
        'updated_at': policy.updated_at, 'updated_by': _person(policy.updated_by),
        'modes': [{'value': value, 'label': label} for value, label in policy.APPROVAL_CHOICES],
    }


class AdminTradeInPolicyView(APIView):
    """GET (who sees the tariff) and PUT (an approver) the company's approval policy."""

    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        if self.request.method == 'PUT':
            return [AdminSalesQuoteWriteThrottle()]
        return [AdminSalesQuotesThrottle()]

    def get(self, request):
        company, error = _company(request, tariff.CAP_VIEW, tariff.CAP_APPROVE)
        if error:
            return error
        return Response(policy_payload(tariff.policy_of(company)))

    def put(self, request):
        company, error = _company(request, tariff.CAP_APPROVE)
        if error:
            return error
        try:
            policy = tariff.update_policy(actor=request.user, company=company, data=_body(request), request=request)
        except tariff.TariffError as exc:
            return _error(exc)
        return Response(policy_payload(policy))
