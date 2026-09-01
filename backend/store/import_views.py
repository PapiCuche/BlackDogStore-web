"""
The import API — Phase C1.4.

TWO CALLS PER IMPORT, ALWAYS
----------------------------
`POST …/preview/` uploads and stages. `POST …/<id>/apply/` commits. There is no
single-shot endpoint, and adding one would defeat the whole design: the point of
staging is that a person sees what will happen to six hundred rows of their
catalogue before it happens.

AUTHORITY (§57, §58)
--------------------
Products need `products.manage`. Stock needs `inventory.adjust` AND access to
every branch being written — the two axes, because "may move stock" and "may
move stock HERE" are different questions and a chain has staff for whom the
answer differs.

A MASTER acts inside an explicit company. `?company=<id>` is resolved by
`resolve_company_for_user`, never taken as ground truth from the client, and
there is no cross-company import: importing "everywhere at once" is not an
operation anybody should be one mis-click away from.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.http import HttpResponse
from rest_framework import permissions, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from . import (
    import_exports,
    import_formats,
    import_services,
    stock_import_services,
    xlsx_reader,
)
from .models import (
    AdminAuditLog,
    Branch,
    BulkImportJob,
    BulkImportRow,
    ImportMappingProfile,
)
from .pos_views import _NOT_FOUND, _context
from .tenancy import (
    CrossTenantError,
    NoTenantError,
    has_branch_access,
    has_capability,
    resolve_company_for_user,
    visible_branches,
)

logger = logging.getLogger(__name__)

CAP_PRODUCTS = 'products.manage'
CAP_STOCK = 'inventory.adjust'

MAX_PREVIEW_ROWS = 300


def _import_context(request):
    """
    Resolve the company, and which KINDS of import this caller may see.

    Returns `(company, granted, error_response)` where `granted` is a subset of
    `{'products', 'stock'}`.

    WHY THE CAPABILITY IS NOT DECIDED BY THE URL
    --------------------------------------------
    The history endpoint used to pick its capability from `?type=` and then
    query without filtering by type, so `products.manage` alone returned stock
    jobs — filenames, row counts, branch names and who ran them. The two
    authorities are separate on purpose (moving stock is not editing a
    catalogue), and neither implies the other.

    Callers get 403 only when they hold NEITHER. Holding one is enough to reach
    the endpoint; what they then see is filtered by what they hold.
    """
    raw = request.query_params.get('company')
    requested_id = None
    if raw not in (None, ''):
        try:
            requested_id = int(raw)
        except (TypeError, ValueError):
            return None, set(), Response(
                {'detail': 'Parámetro "company" inválido.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
    try:
        company = resolve_company_for_user(request.user, requested_id)
    except CrossTenantError:
        return None, set(), Response(
            {'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND,
        )
    except NoTenantError as exc:
        return None, set(), Response(
            {'detail': str(exc)}, status=status.HTTP_403_FORBIDDEN,
        )

    granted = set()
    if has_capability(request.user, company, CAP_PRODUCTS):
        granted.add(BulkImportJob.PRODUCTS)
    if has_capability(request.user, company, CAP_STOCK):
        granted.add(BulkImportJob.STOCK)
    if not granted:
        return None, set(), Response(
            {'detail': 'No tienes permisos para esta operación.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return company, granted, None


def _job_payload(job, *, rows=False, limit=MAX_PREVIEW_ROWS):
    payload = {
        'id': job.pk,
        'import_type': job.import_type,
        'status': job.status,
        'stock_mode': job.stock_mode,
        'original_filename': job.original_filename,
        'file_sha256': job.file_sha256,
        'mapping': job.mapping_snapshot,
        'options': job.options_snapshot,
        'counts': {
            'total': job.rows_total, 'create': job.rows_create,
            'update': job.rows_update, 'no_change': job.rows_no_change,
            'skip': job.rows_skip, 'error': job.rows_error,
        },
        'summary': job.summary,
        'created_at': job.created_at,
        'applied_at': job.applied_at,
        'created_by': getattr(job.created_by, 'username', ''),
        'applied_by': getattr(job.applied_by, 'username', ''),
        'is_applicable': job.is_applicable,
    }
    if rows:
        # Errors first: a preview of six hundred rows is read for what is wrong
        # with it, and burying twelve errors under four hundred clean lines is
        # the same as not reporting them.
        queryset = job.rows.all()
        ordered = list(queryset.filter(action=BulkImportRow.ERROR)[:limit])
        remaining = limit - len(ordered)
        if remaining > 0:
            ordered += list(queryset.exclude(action=BulkImportRow.ERROR)[:remaining])
        payload['rows'] = [{
            'sheet': r.sheet_name, 'row': r.row_number, 'action': r.action,
            'match_key': r.match_key, 'data': r.normalized_data,
            'errors': r.errors, 'warnings': r.warnings,
        } for r in ordered]
        payload['rows_truncated'] = job.rows_total > len(ordered)
    return payload


def _error(exc, code=status.HTTP_400_BAD_REQUEST):
    return Response({'detail': str(exc)}, status=code)


# =============================================================================
# Inspection — what is in this file?
# =============================================================================

class AdminImportInspectView(APIView):
    """
    Open an upload and report its shape without staging anything.

    Feeds the wizard's first two steps: which sheets exist, which columns, what
    format we think it is. Nothing is stored — not even a job — because a person
    picking the wrong file should not leave a row behind.
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        import_type = request.data.get('import_type') or BulkImportJob.PRODUCTS
        capability = CAP_PRODUCTS if import_type == BulkImportJob.PRODUCTS else CAP_STOCK
        company, error = _context(request, capability)
        if error:
            return error

        upload = request.FILES.get('file')
        if upload is None:
            return _error('Adjunta un archivo .xlsx.')
        try:
            data = xlsx_reader.check_upload(upload, filename=upload.name)
            workbook, notes = xlsx_reader.load_workbook(data)
        except xlsx_reader.XlsxError as exc:
            return _error(exc)

        sheets = []
        for name in workbook.sheetnames:
            for header_row in (1, 2):
                # SAMPLE, deliberately: this endpoint answers "what does this
                # file look like", so stopping after a handful of rows is the
                # point. Its truncation flag means "there is more of this file",
                # NOT "the file is too long" — that judgement belongs to the
                # preview, which reads in FULL_IMPORT mode and refuses the whole
                # file rather than trimming it.
                headers, rows, _sampled = xlsx_reader.read_rows(
                    workbook, name, header_row=header_row,
                    limit=xlsx_reader.SAMPLE_ROWS, mode=xlsx_reader.SAMPLE,
                )
                detected = import_formats.detect(import_type, name, headers)
                if detected:
                    break
            sheets.append({
                'name': name,
                'header_row': (detected or {}).get('header_row', 1),
                'headers': [str(h) for h in headers],
                'sample_rows': len(rows),
                'detected': (detected or {}).get('label', ''),
                'preset': (detected or {}).get('preset', ''),
                'mapping': (detected or {}).get('mapping', {}),
                'warehouse_columns': [
                    {'index': i, 'header': h}
                    for i, h in (detected or {}).get('warehouse_columns', [])
                ],
                'notes': (detected or {}).get('notes', []),
                'signature': import_formats.header_signature(import_type, name, headers),
            })

        profiles = {
            p.header_signature: {'id': p.pk, 'name': p.name,
                                 'mapping': p.mapping, 'options': p.options}
            for p in ImportMappingProfile.objects.filter(
                company=company, import_type=import_type, is_active=True,
            )
        }
        for sheet in sheets:
            sheet['profile'] = profiles.get(sheet['signature'])

        return Response({
            'import_type': import_type,
            'reader_notes': notes,
            'sheets': sheets,
            'fields': (
                import_formats.PRODUCT_FIELDS
                if import_type == BulkImportJob.PRODUCTS
                else import_formats.STOCK_FIELDS
            ),
            'branches': [
                {'id': b.pk, 'name': b.name}
                for b in visible_branches(request.user, company)
            ],
        })


# =============================================================================
# Products
# =============================================================================

class AdminProductImportPreviewView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        company, error = _context(request, CAP_PRODUCTS)
        if error:
            return error
        upload = request.FILES.get('file')
        if upload is None:
            return _error('Adjunta un archivo .xlsx.')

        mapping = _json_field(request.data.get('mapping'))
        options = _json_field(request.data.get('options')) or {}
        header_row = _int_or_none(request.data.get('header_row'))
        try:
            job = import_services.preview_products(
                company=company, actor=request.user, upload=upload,
                filename=upload.name,
                sheet_name=request.data.get('sheet_name') or None,
                header_row=header_row, mapping=mapping, options=options,
            )
        except (xlsx_reader.XlsxError, import_services.ImportError_) as exc:
            return _error(exc)

        _remember_profile(company, request.user, job, BulkImportJob.PRODUCTS)
        return Response(_job_payload(job, rows=True), status=status.HTTP_201_CREATED)


class AdminProductImportApplyView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        company, error = _context(request, CAP_PRODUCTS)
        if error:
            return error
        job = BulkImportJob.objects.filter(
            company=company, pk=pk, import_type=BulkImportJob.PRODUCTS,
        ).first()
        if job is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

        try:
            job, changed = import_services.apply_products(job=job, actor=request.user)
        except import_services.ImportError_ as exc:
            return _error(exc)

        if changed:
            AdminAuditLog.log(
                actor=request.user, action='product_import_applied',
                target_type='bulk_import_job', target_id=job.pk,
                metadata={
                    'company_id': company.pk, 'job_id': job.pk,
                    'rows_total': job.rows_total, 'rows_create': job.rows_create,
                    'rows_update': job.rows_update,
                    'file_sha256': job.file_sha256,
                },
                request=request, company=company,
            )
        return Response(_job_payload(job))


# =============================================================================
# Stock
# =============================================================================

class AdminStockImportPreviewView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        company, error = _context(request, CAP_STOCK)
        if error:
            return error
        upload = request.FILES.get('file')
        if upload is None:
            return _error('Adjunta un archivo .xlsx.')

        branch_map = _json_field(request.data.get('branch_map')) or {}
        denied = _branch_access_error(request.user, company, branch_map.values())
        if denied:
            return denied

        try:
            job = stock_import_services.preview_stock(
                company=company, actor=request.user, upload=upload,
                filename=upload.name, branch_map=branch_map,
                mode=request.data.get('mode') or BulkImportJob.MODE_RECONCILE,
                sheet_name=request.data.get('sheet_name') or None,
                header_row=_int_or_none(request.data.get('header_row')),
                mapping=_json_field(request.data.get('mapping')),
            )
        except (xlsx_reader.XlsxError, stock_import_services.StockImportError) as exc:
            return _error(exc)

        _remember_profile(company, request.user, job, BulkImportJob.STOCK)
        return Response(_job_payload(job, rows=True), status=status.HTTP_201_CREATED)


class AdminStockImportApplyView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        company, error = _context(request, CAP_STOCK)
        if error:
            return error
        job = BulkImportJob.objects.filter(
            company=company, pk=pk, import_type=BulkImportJob.STOCK,
        ).first()
        if job is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

        # Re-checked at APPLY, not only at preview. Branch access can be removed
        # between the two, and this is the call that moves the stock.
        branch_ids = (job.mapping_snapshot or {}).get('branch_map', {}).values()
        denied = _branch_access_error(request.user, company, branch_ids)
        if denied:
            return denied

        try:
            job, changed, movements = stock_import_services.apply_stock(
                job=job, actor=request.user,
            )
        except stock_import_services.StockImportError as exc:
            return _error(exc)

        if changed:
            AdminAuditLog.log(
                actor=request.user, action='inventory_import_applied',
                target_type='bulk_import_job', target_id=job.pk,
                metadata={
                    'company_id': company.pk, 'job_id': job.pk,
                    'mode': job.stock_mode,
                    'branch_ids': sorted({int(b) for b in branch_ids}),
                    'rows_total': job.rows_total,
                    'movements': len(movements),
                    'file_sha256': job.file_sha256,
                },
                request=request, company=company,
            )
        return Response(_job_payload(job))


# =============================================================================
# History, error report, downloads
# =============================================================================

class AdminImportHistoryView(APIView):
    """§59 — what was imported, by whom, and what it did."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company, granted, error = _import_context(request)
        if error:
            return error

        import_type = request.query_params.get('type') or ''
        if import_type:
            if import_type not in dict(BulkImportJob.TYPE_CHOICES):
                return _error('Tipo de importación inválido.')
            if import_type not in granted:
                return Response(
                    {'detail': 'No tienes permisos para esta operación.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            visible = {import_type}
        else:
            # No type asked for: show what this caller is entitled to and
            # nothing else. Never the union of both because they hold one.
            visible = granted

        queryset = (
            BulkImportJob.objects
            .filter(company=company, import_type__in=sorted(visible))
            .select_related('created_by', 'applied_by')
        )
        return Response({'results': [_job_payload(job) for job in queryset[:50]]})


class AdminImportJobView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        company, job, error = _job_or_error(request, pk)
        if error:
            return error
        return Response(_job_payload(job, rows=True))


class AdminImportErrorReportView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        company, job, error = _job_or_error(request, pk)
        if error:
            return error

        response = HttpResponse(
            import_exports.error_report_csv(job), content_type='text/csv; charset=utf-8',
        )
        response['Content-Disposition'] = (
            f'attachment; filename="errores-importacion-{job.pk}.csv"'
        )
        return response


class AdminProductTemplateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company, error = _context(request, CAP_PRODUCTS)
        if error:
            return error
        return _xlsx(import_exports.product_template_bytes(), 'plantilla-productos.xlsx')


class AdminInventoryExportView(APIView):
    """§61 — download, count, re-upload."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company, error = _context(request, CAP_STOCK)
        if error:
            return error

        allowed = list(visible_branches(request.user, company))
        raw = request.query_params.get('branches') or ''
        if raw.strip():
            try:
                wanted = {int(x) for x in raw.split(',') if x.strip()}
            except ValueError:
                return _error('Parámetro "branches" inválido.')
            branches = [b for b in allowed if b.pk in wanted]
            if len(branches) != len(wanted):
                return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        else:
            branches = allowed
        if not branches:
            return _error('No tienes acceso a ninguna sucursal.')

        include = request.query_params.get('quantities', 'current') != 'blank'
        payload = import_exports.inventory_export_bytes(
            company=company, branches=branches, include_quantities=include,
        )
        suffix = 'actual' if include else 'para-conteo'
        return _xlsx(payload, f'inventario-{suffix}.xlsx')


# =============================================================================
# helpers
# =============================================================================

def _job_or_error(request, pk):
    """
    Resolve one import job without leaking whether it exists elsewhere.

    ORDER MATTERS, AND THIS IS WHY
    ------------------------------
    The obvious version looks up the job by primary key FIRST, then decides
    which capability to demand from the job it found. That turns the endpoint
    into an oracle: probing an id you have no right to gets 403 when a job of
    that kind exists somewhere and 404 when nothing does — so a caller can
    enumerate other tenants' job ids by reading the difference.

    So: resolve the company from the CALLER, look for the job INSIDE it (a job
    belonging to anyone else is simply not there, and answers 404 exactly like
    an id nobody ever used), and only then check the capability for that job's
    kind — a question about a record already known to be theirs.
    """
    company, granted, error = _import_context(request)
    if error:
        return None, None, error

    job = BulkImportJob.objects.filter(company=company, pk=pk).first()
    if job is None:
        return None, None, Response(
            {'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND,
        )
    if job.import_type not in granted:
        return None, None, Response(
            {'detail': 'No tienes permisos para esta operación.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return company, job, None


def _xlsx(payload: bytes, filename: str) -> HttpResponse:
    response = HttpResponse(
        payload,
        content_type=(
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        ),
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def _json_field(raw):
    """A field that arrives as JSON text inside a multipart form."""
    import json

    if raw in (None, ''):
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _int_or_none(raw):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _branch_access_error(user, company, branch_ids):
    """
    Both axes: the branch is this company's, and this user may write to it.

    Resolved by walking DOWN from the company, so another tenant's branch is
    absent from the candidate set rather than rejected by a comparison somebody
    could later remove.
    """
    try:
        wanted = {int(b) for b in branch_ids}
    except (TypeError, ValueError):
        return _error('Sucursal inválida.')
    if not wanted:
        return _error('Indica a qué sucursal corresponde cada columna de almacén.')

    owned = {
        b.pk: b for b in Branch.objects.filter(company=company, pk__in=wanted)
    }
    if set(owned) != wanted:
        return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
    for branch in owned.values():
        if not has_branch_access(user, branch):
            return Response(
                {'detail': f'No tienes acceso a la sucursal {branch.name}.'},
                status=status.HTTP_403_FORBIDDEN,
            )
    return None


def _remember_profile(company, actor, job, import_type):
    """
    Store the mapping under this file's header signature, so tomorrow is one click.

    Best-effort: a profile that fails to save must never lose a preview the
    operator is about to act on. Failing loudly here would throw away real work
    to protect a convenience.
    """
    snapshot = job.mapping_snapshot or {}
    headers = snapshot.get('headers') or []
    if not headers or not snapshot.get('mapping'):
        return
    signature = import_formats.header_signature(
        import_type, snapshot.get('sheet_name', ''), headers,
    )
    try:
        with transaction.atomic():
            ImportMappingProfile.objects.update_or_create(
                company=company, import_type=import_type,
                header_signature=signature,
                defaults={
                    'name': snapshot.get('sheet_name') or job.original_filename or 'Formato',
                    'mapping': snapshot.get('mapping') or {},
                    'options': job.options_snapshot or {},
                    'is_active': True,
                    'created_by': actor,
                },
            )
    except Exception:
        # Best-effort on purpose: a remembered mapping is a convenience, and
        # losing a preview the operator is about to act on to protect it would
        # be the wrong trade. But swallowing it entirely means the feature can
        # be broken for months with nobody able to tell, so the failure is
        # logged — WITHOUT the file's contents, which are the tenant's data and
        # have no business in a log line.
        logger.warning(
            'No se pudo guardar el perfil de mapeo (empresa=%s, tipo=%s, '
            'trabajo=%s).', company.pk, import_type, job.pk, exc_info=True,
        )
