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
    image_import_services,
    import_assistant,
    import_exports,
    import_formats,
    import_media,
    import_services,
    stock_import_services,
    unit_import_services,
    xlsx_reader,
)
from .models import (
    AdminAuditLog,
    Branch,
    BulkImportJob,
    BulkImportRow,
    ImportMappingProfile,
)
from .inventory_views import may_view_costs
from .pos_views import _NOT_FOUND, _context
from .throttles import AdminImportThrottle
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
    `{'products', 'stock', 'units'}`.

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
        # Pictures of products are the catalogue's: the same authority.
        granted.add(BulkImportJob.PRODUCT_IMAGES)
    if has_capability(request.user, company, CAP_STOCK):
        granted.add(BulkImportJob.STOCK)
        # Registering devices is the same authority as moving stock.
        granted.add(BulkImportJob.UNITS)
    if not granted:
        return None, set(), Response(
            {'detail': 'No tienes permisos para esta operación.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return company, granted, None


#: `import_type` of an inspection that only asks where a file goes.
ASSISTANT = 'auto'

#: What somebody must be able to run for a suggestion to name it.
_SUGGESTION_NEEDS = {
    import_assistant.PRODUCTS: BulkImportJob.PRODUCTS,
    import_assistant.IMAGES: BulkImportJob.PRODUCTS,
    import_assistant.STOCK: BulkImportJob.STOCK,
    import_assistant.UNITS: BulkImportJob.UNITS,
}


def _granted_kinds(user, company) -> set:
    """The loads `user` may run in `company`. The same reading as `_import_context`."""
    granted = set()
    if has_capability(user, company, CAP_PRODUCTS):
        granted.add(BulkImportJob.PRODUCTS)
    if has_capability(user, company, CAP_STOCK):
        granted.update((BulkImportJob.STOCK, BulkImportJob.UNITS))
    return granted


def _suggestion(workbook, granted) -> dict:
    """
    Where this workbook goes, for a caller who may run `granted`.

    Reads the first two rows of each sheet as candidate headers, and nothing
    below them. A load the caller may not run is not named: they are told they
    lack the permission, and the same thing whichever load it was.
    """
    sheets = []
    for name in workbook.sheetnames:
        candidates = []
        for header_row in (1, 2):
            headers, _rows, _more = xlsx_reader.read_rows(
                workbook, name, header_row=header_row, limit=1, mode=xlsx_reader.SAMPLE,
            )
            candidates.append(headers)
        sheets.append((name, candidates))
    found = import_assistant.classify(sheets)

    needed = _SUGGESTION_NEEDS.get(found['type'])
    if needed is not None and needed not in granted:
        return {
            'suggested_import_type': import_assistant.FORBIDDEN, 'suggestion_reason': 'no_permission',
            'suggestion_detail': import_assistant.NO_PERMISSION, 'suggestion_sheet': '',
        }
    return {
        'suggested_import_type': found['type'], 'suggestion_reason': found['reason'],
        'suggestion_detail': found['detail'], 'suggestion_sheet': found['sheet'],
    }


#: DC1 — what a staged row may carry that the system ALREADY knew about costs.
#: The value the file states is the uploader's own; these are the company's,
#: and travel only to `costs.view`.
_KNOWN_COST_KEYS = ('reference_cost_before', 'cost_average_before', 'cost_average_after')


def _without_known_costs(data):
    if not isinstance(data, dict):
        return data
    return {key: value for key, value in data.items() if key not in _KNOWN_COST_KEYS}


def _job_payload(job, *, rows=False, limit=MAX_PREVIEW_ROWS, include_cost=False):
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
        # Then what carries a WARNING: it will be written, and somebody should
        # see what was said about it. In a long file a warned row past the limit
        # would otherwise exist only in the database.
        queryset = job.rows.all()
        ordered = list(queryset.filter(action=BulkImportRow.ERROR)[:limit])
        fine = queryset.exclude(action=BulkImportRow.ERROR)
        for more in (fine.exclude(warnings=[]), fine.filter(warnings=[])):
            remaining = limit - len(ordered)
            if remaining > 0:
                ordered += list(more[:remaining])
        payload['rows'] = [{
            'sheet': r.sheet_name, 'row': r.row_number, 'action': r.action,
            'match_key': r.match_key,
            'data': r.normalized_data if include_cost else _without_known_costs(r.normalized_data),
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
    throttle_classes = [AdminImportThrottle]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        import_type = request.data.get('import_type') or BulkImportJob.PRODUCTS
        if import_type == ASSISTANT:
            # «¿Qué archivo tengo?»: whoever may run ANY load may ask. What they
            # are told is narrowed below to the loads they may run.
            company, granted, error = _import_context(request)
        else:
            capability = CAP_PRODUCTS if import_type == BulkImportJob.PRODUCTS else CAP_STOCK
            company, error = _context(request, capability)
            granted = None
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

        if granted is None:
            granted = _granted_kinds(request.user, company)
        suggestion = _suggestion(workbook, granted)
        if import_type == ASSISTANT:
            # The suggestion and the names of the sheets. No row, no header, no
            # job: this answers «where does it go», and nothing is kept.
            return Response({
                'import_type': ASSISTANT, **suggestion,
                'sheet_names': (
                    [] if suggestion['suggested_import_type'] == import_assistant.FORBIDDEN
                    else [str(name) for name in workbook.sheetnames]
                ),
            })

        sheets = []
        for name in workbook.sheetnames:
            # The «Instrucciones» / «Ejemplo» of a previous template: listed, so the
            # screen can say what they are, and never offered as data.
            is_help = (
                import_type == BulkImportJob.PRODUCTS and import_exports.is_help_sheet(name)
            )
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
                detected = None if is_help else import_formats.detect(import_type, name, headers)
                if detected or is_help:
                    break
            sheets.append({
                'name': name,
                'help': is_help,
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
            **suggestion,
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
            # How images travel, from the limits this server applies.
            'image_rules': (
                import_exports.image_rules() if import_type == BulkImportJob.PRODUCTS else None
            ),
        })


# =============================================================================
# Products
# =============================================================================

class AdminProductImportPreviewView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminImportThrottle]
    parser_classes = [MultiPartParser, FormParser]

    KIND_PRODUCTS, KIND_IMAGES = 'products', 'images'

    def _preview_images(self, request, company):
        """
        Stage a load of pictures only. Places nothing.

        The company is the caller's; each picture is matched to a product
        INSIDE it. The envelope of the bundle is checked before anything else
        is read, exactly as for a product load.
        """
        # What is not a mode is refused by the service, with everything else it checks.
        mode = request.data.get('mode') or image_import_services.MODE_ADD
        use_main = str(request.data.get('use_main') or '').strip().lower() in ('1', 'true', 'on', 'si', 'sí')
        try:
            images = import_media.collect(
                request.FILES.getlist('images'), request.FILES.get('images_zip'),
            )
        except import_media.ImportMediaError as exc:
            return _error(
                exc, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE if exc.too_large
                else status.HTTP_400_BAD_REQUEST,
            )
        upload = request.FILES.get('file')
        try:
            job = image_import_services.preview_images(
                company=company, actor=request.user, images=images, upload=upload,
                filename=upload.name if upload is not None else '', mode=mode, use_main=use_main,
            )
        except (xlsx_reader.XlsxError, image_import_services.ImageImportError,
                import_media.ImportMediaError) as exc:
            return _error(exc)
        return Response(_job_payload(job, rows=True, include_cost=may_view_costs(request)), status=status.HTTP_201_CREATED)

    def post(self, request):
        company, error = _context(request, CAP_PRODUCTS)
        if error:
            return error
        # INVENTORY-UNIFIED-01 — `kind=images`: pictures for products that
        # already exist, with no product workbook. It comes through THIS
        # address because this is the one route sized to carry a load of
        # pictures (`request_limits`, tier `product_import`).
        kind = request.data.get('kind') or self.KIND_PRODUCTS
        if kind == self.KIND_IMAGES:
            return self._preview_images(request, company)
        if kind != self.KIND_PRODUCTS:
            return _error('Tipo de carga no válido.')
        upload = request.FILES.get('file')
        if upload is None:
            return _error('Adjunta un archivo .xlsx.')

        mapping = _json_field(request.data.get('mapping'))
        options = _json_field(request.data.get('options')) or {}
        header_row = _int_or_none(request.data.get('header_row'))
        # BULK-MEDIA — the pictures travel WITH the workbook: several `images`
        # parts, an `images_zip`, or both. Their envelope is checked before the
        # workbook is read, so an archive that should not have arrived leaves
        # no job behind.
        try:
            images = import_media.collect(
                request.FILES.getlist('images'), request.FILES.get('images_zip'),
            )
        except import_media.ImportMediaError as exc:
            return _error(
                exc, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE if exc.too_large
                else status.HTTP_400_BAD_REQUEST,
            )
        try:
            job = import_services.preview_products(
                company=company, actor=request.user, upload=upload,
                filename=upload.name,
                sheet_name=request.data.get('sheet_name') or None,
                header_row=header_row, mapping=mapping, options=options,
                images=images,
            )
        except (xlsx_reader.XlsxError, import_services.ImportError_) as exc:
            return _error(exc)
        except import_media.ImportMediaError as exc:
            return _error(exc)

        _remember_profile(company, request.user, job, BulkImportJob.PRODUCTS)
        return Response(_job_payload(job, rows=True, include_cost=may_view_costs(request)), status=status.HTTP_201_CREATED)


class AdminProductImportApplyView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminImportThrottle]

    def post(self, request, pk):
        company, error = _context(request, CAP_PRODUCTS)
        if error:
            return error
        job = BulkImportJob.objects.filter(
            company=company, pk=pk, import_type=BulkImportJob.PRODUCTS,
        ).first()
        if job is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

        # LABEL-MIGRATION-01: how many rows to review, typed by whoever applies.
        # The service checks it; the browser only carries it.
        confirm_review = request.data.get('confirm_review') if hasattr(request.data, 'get') else None
        try:
            job, changed = import_services.apply_products(
                job=job, actor=request.user, confirm_review=confirm_review,
            )
        except import_services.ImportError_ as exc:
            return _error(exc)

        if changed:
            history = (job.summary or {}).get('history')
            AdminAuditLog.log(
                actor=request.user, action='product_import_applied',
                target_type='bulk_import_job', target_id=job.pk,
                metadata={
                    'company_id': company.pk, 'job_id': job.pk,
                    'rows_total': job.rows_total, 'rows_create': job.rows_create,
                    'rows_update': job.rows_update,
                    'file_sha256': job.file_sha256,
                    'images_added': (job.summary or {}).get('applied', {}).get('images_added', 0),
                    **({'history_mode': True, 'review_confirmed': history.get('review', 0)} if history else {}),
                },
                request=request, company=company,
            )
        return Response(_job_payload(job))


class AdminProductImagesImportApplyView(APIView):
    """
    POST /api/admin/products/images/import/<id>/apply/ — place the staged pictures.

    JSON only: no file travels here. `{"confirm": "REEMPLAZAR"}` is required
    for a job staged in `replace` mode, and for nothing else.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminImportThrottle]
    http_method_names = ['post', 'options']

    def post(self, request, pk):
        company, error = _context(request, CAP_PRODUCTS)
        if error:
            return error
        job = BulkImportJob.objects.filter(
            company=company, pk=pk, import_type=BulkImportJob.PRODUCT_IMAGES,
        ).first()
        if job is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        data = request.data if isinstance(request.data, dict) else {}
        try:
            job, _changed = image_import_services.apply_images(
                job=job, actor=request.user, confirmation=data.get('confirm'), request=request,
            )
        except image_import_services.ImageImportError as exc:
            return _error(exc)
        return Response(_job_payload(job))


class AdminProductImagesImportRulesView(APIView):
    """GET — how a picture file is named and what a load admits, for the screen."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        _company, error = _context(request, CAP_PRODUCTS)
        if error:
            return error
        return Response({**image_import_services.naming_rule(), 'image_rules': import_exports.image_rules()})


# =============================================================================
# Devices with a serial number — one row, one unit
# =============================================================================

class AdminUnitImportTemplateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company, error = _context(request, CAP_STOCK)
        if error:
            return error
        # The branches THIS person can load into: the list never names another.
        branches = list(
            visible_branches(request.user, company).filter(is_active=True).order_by('name').values_list('name', flat=True)
        )
        return _xlsx(unit_import_services.template_bytes(branches), unit_import_services.TEMPLATE_FILENAME)


class AdminUnitImportPreviewView(APIView):
    """
    Stage a device file (the «Equipos» template). Registers nothing.

    The company comes from the caller; each row's branch is looked up INSIDE
    that company and must be one the caller may operate in. Neither is taken
    from an id in the file.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminImportThrottle]
    parser_classes = [MultiPartParser, FormParser]
    http_method_names = ['post', 'options']

    def post(self, request):
        company, error = _context(request, CAP_STOCK)
        if error:
            return error
        upload = request.FILES.get('file')
        if upload is None:
            return _error('Adjunta un archivo .xlsx.')

        reachable = set(visible_branches(request.user, company).values_list('pk', flat=True))
        default_branch = None
        if request.data.get('branch') not in (None, ''):
            default_branch = _int_or_none(request.data.get('branch'))
            if default_branch is None:
                return _error('Sucursal inválida.')
            # Not this company's, or not one of theirs: the same answer.
            if default_branch not in reachable:
                return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

        try:
            job = unit_import_services.preview_units(
                company=company, actor=request.user, upload=upload, filename=upload.name,
                reachable_branch_ids=reachable, default_branch=default_branch,
                default_reason=request.data.get('reason') or '',
            )
        except (xlsx_reader.XlsxError, unit_import_services.UnitImportError) as exc:
            return _error(exc)
        return Response(_job_payload(job, rows=True, include_cost=may_view_costs(request)), status=status.HTTP_201_CREATED)


class AdminUnitImportApplyView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminImportThrottle]
    http_method_names = ['post', 'options']

    def post(self, request, pk):
        company, error = _context(request, CAP_STOCK)
        if error:
            return error
        job = BulkImportJob.objects.filter(
            company=company, pk=pk, import_type=BulkImportJob.UNITS,
        ).first()
        # Re-checked at APPLY: branch access can be removed after the preview,
        # and this is the call that registers the devices.
        if job is None or not _job_within_reach(request.user, company, job):
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

        try:
            job, _changed = unit_import_services.apply_units(job=job, actor=request.user, request=request)
        except unit_import_services.UnitImportError as exc:
            return _error(exc)
        return Response(_job_payload(job))


# =============================================================================
# Stock
# =============================================================================

class AdminStockImportPreviewView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminImportThrottle]
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
        return Response(_job_payload(job, rows=True, include_cost=may_view_costs(request)), status=status.HTTP_201_CREATED)


class AdminStockImportApplyView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminImportThrottle]

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
        return Response({'results': [
            _job_payload(job) for job in queryset[:50] if _job_within_reach(request.user, company, job)
        ]})


class AdminImportJobView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        company, job, error = _job_or_error(request, pk)
        if error:
            return error
        return Response(_job_payload(job, rows=True, include_cost=may_view_costs(request)))


class AdminImportErrorReportView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        company, job, error = _job_or_error(request, pk)
        if error:
            return error

        if request.query_params.get('kind') == 'history':
            # LABEL-MIGRATION-01 — the reconciliation report of a historical load.
            if not (job.options_snapshot or {}).get('history_mode'):
                return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
            response = HttpResponse(
                import_exports.history_report_csv(job), content_type='text/csv; charset=utf-8',
            )
            response['Content-Disposition'] = f'attachment; filename="conciliacion-importacion-{job.pk}.csv"'
            return response

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
        # Of THIS company: its categories are the list under «Categoría».
        if request.query_params.get('variant') == 'historical':
            # LABEL-MIGRATION-01 — the «base histórica» template.
            return _xlsx(import_exports.history_template_bytes(company), 'carga-base-historica.xlsx')
        return _xlsx(import_exports.product_template_bytes(company), 'carga-masiva-productos.xlsx')


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
    if not _job_within_reach(request.user, company, job):
        return None, None, Response(
            {'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND,
        )
    return company, job, None


def _job_within_reach(user, company, job) -> bool:
    """
    A job of DEVICES carries their serials and IMEIs, row by row.

    Those are readable in Inventario › Equipos only inside the branches the
    caller may operate in, so a staged file is too: a job that touches a
    branch out of the caller's reach is not there for them. Product and
    quantity jobs keep the rule they always had.
    """
    if job.import_type != BulkImportJob.UNITS:
        return True
    wanted = set((job.mapping_snapshot or {}).get('branch_ids') or [])
    if not wanted:
        return job.created_by_id == user.pk
    reachable = set(visible_branches(user, company).values_list('pk', flat=True))
    return wanted <= reachable


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
