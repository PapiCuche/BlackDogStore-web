"""
SALES-NOTE-CONTROL-01 — the panel's view of issued internal sales notes.

    GET   /api/admin/sales-notes/                 list, filtered, paginated
    GET   /api/admin/sales-notes/{pk}/            one note with its remarks
    POST  /api/admin/sales-notes/{pk}/void/       platform master only
    POST  /api/admin/sales-notes/{pk}/remarks/    master or `sales.notes.manage`

READ-ONLY BY CONSTRUCTION. No route writes a note's number, dates, lines,
amounts, taxes or payment: PUT, PATCH and DELETE answer 405.

SCOPE. Every note is reached through `visible_orders()` — the boundary of every
order surface — so a note of another tenant, or of a branch the caller does
not operate, does not exist for them (404).
"""
from __future__ import annotations

from datetime import date

from django.db.models import Q
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import sales_note_control as control
from .inventory_views import (
    CAP_SALES_NOTES, _LEGACY_SALES_NOTES_ROLES, _NO_PERMISSION, _company_context, _paginate,
)
from .models import Order, SalesNote
from .sales_note_services import SALES_NOTE_CONTROL_LINE
from .serializers import SalesNoteRemarkSerializer, SalesNoteSerializer
from .tenancy import visible_branches, visible_orders
from .throttles import AdminSalesNotesThrottle

STATUS_LIVE, STATUS_VOID, STATUS_TEST = 'vigente', 'anulada', 'prueba'


def _context(request):
    company, error = _company_context(request, CAP_SALES_NOTES, _LEGACY_SALES_NOTES_ROLES)
    if error is not None and error.data.get('detail') == _NO_PERMISSION:
        error.data['detail'] = 'No tienes permisos sobre las notas de venta.'
    return company, error


def _scoped_notes(user, company):
    return (
        SalesNote.objects
        .filter(order__in=visible_orders(user, company))
        .select_related('order', 'order__fulfillment_branch', 'created_by', 'voided_by')
    )


def _note_or_404(request, company, pk):
    note = _scoped_notes(request.user, company).filter(pk=pk).first()
    if note is None:
        return None, Response({'detail': 'Nota de venta no encontrada.'}, status=status.HTTP_404_NOT_FOUND)
    return note, None


def _bad(detail):
    return Response({'detail': detail}, status=status.HTTP_400_BAD_REQUEST)


def _parse_date(raw):
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


#: Void as every paper reads it (`sales_note_services.is_void`): the status, or
#: an order that was cancelled.
_VOID_Q = Q(status=SalesNote.STATUS_VOID) | Q(order__status=Order.Status.CANCELLED)


class AdminSalesNoteListView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminSalesNotesThrottle]

    def get(self, request):
        company, error = _context(request)
        if error:
            return error
        notes = _scoped_notes(request.user, company)
        params = request.query_params

        wanted = (params.get('status') or '').strip().lower()
        if wanted == STATUS_LIVE:
            notes = notes.exclude(_VOID_Q)
        elif wanted == STATUS_VOID:
            notes = notes.filter(_VOID_Q)
        elif wanted == STATUS_TEST:
            # «Prueba» is a reason somebody wrote, never a guess about old notes.
            notes = notes.filter(status=SalesNote.STATUS_VOID, void_reason_type=SalesNote.VOID_REASON_TEST)
        elif wanted:
            return _bad('Estado no reconocido. Use: vigente, anulada o prueba.')

        for key, lookup in (('date_from', 'gte'), ('date_to', 'lte')):
            raw = (params.get(key) or '').strip()
            if not raw:
                continue
            day = _parse_date(raw)
            if day is None:
                return _bad('Fecha no válida. Use el formato AAAA-MM-DD.')
            notes = notes.filter(
                Q(**{f'issued_at__date__{lookup}': day})
                | Q(issued_at__isnull=True, **{f'created_at__date__{lookup}': day}))

        branches = list(visible_branches(request.user, company).order_by('name'))
        raw_branch = (params.get('branch') or '').strip()
        if raw_branch:
            try:
                branch_id = int(raw_branch)
            except ValueError:
                return _bad('Sucursal no válida.')
            # Outside the caller's reach is refused, not silently emptied.
            if branch_id not in {branch.pk for branch in branches}:
                return Response({'detail': 'No tienes acceso a esa sucursal.'}, status=status.HTTP_403_FORBIDDEN)
            notes = notes.filter(order__fulfillment_branch_id=branch_id)

        raw_user = (params.get('user') or '').strip()
        if raw_user:
            try:
                notes = notes.filter(created_by_id=int(raw_user))
            except ValueError:
                return _bad('Usuario no válido.')

        issuers = (
            _scoped_notes(request.user, company).exclude(created_by__isnull=True)
            .order_by('created_by__username').values_list('created_by_id', 'created_by__username').distinct()
        )
        page, meta = _paginate(notes.order_by('-issued_at', '-id'), request)
        return Response({
            'results': SalesNoteSerializer(page, many=True).data,
            **meta,
            'can_void': control.may_void(request.user, company),
            # Whoever reaches this list holds the note authority, and that is
            # what adding a remark asks.
            'can_remark': True,
            'branches': [{'id': branch.pk, 'name': branch.name} for branch in branches],
            'issuers': [{'id': pk, 'username': username} for pk, username in issuers],
            'notice': SALES_NOTE_CONTROL_LINE,
        })


class AdminSalesNoteDetailView(APIView):
    """GET only. There is no write of an issued note: PUT/PATCH/DELETE are 405."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminSalesNotesThrottle]

    def get(self, request, pk):
        company, error = _context(request)
        if error:
            return error
        note, error = _note_or_404(request, company, pk)
        if error:
            return error
        return Response(_detail(note, request.user, company))


def _detail(note, user, company):
    return {
        **SalesNoteSerializer(note).data,
        'remarks': SalesNoteRemarkSerializer(note.remarks.select_related('author'), many=True).data,
        'can_void': control.may_void(user, company),
        'can_remark': True,
        'notice': SALES_NOTE_CONTROL_LINE,
    }


class AdminSalesNoteVoidView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminSalesNotesThrottle]

    def post(self, request, pk):
        company, error = _context(request)
        if error:
            return error
        if not control.may_void(request.user, company):
            return Response(
                {'detail': 'Sólo el usuario Master puede anular una nota de venta.'},
                status=status.HTTP_403_FORBIDDEN)
        note, error = _note_or_404(request, company, pk)
        if error:
            return error
        try:
            control.void_sales_note(
                note, request.user, request.data.get('reason_type'), request.data.get('reason'), request=request)
        except control.SalesNoteControlError as exc:
            return Response(exc.body(), status=exc.http_status)
        note, _ = _note_or_404(request, company, pk)
        return Response(_detail(note, request.user, company))


class AdminSalesNoteRemarkView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminSalesNotesThrottle]

    def post(self, request, pk):
        company, error = _context(request)
        if error:
            return error
        note, error = _note_or_404(request, company, pk)
        if error:
            return error
        try:
            remark = control.add_remark(note, request.user, request.data.get('text'), request=request)
        except control.SalesNoteControlError as exc:
            return Response(exc.body(), status=exc.http_status)
        return Response(SalesNoteRemarkSerializer(remark).data, status=status.HTTP_201_CREATED)
