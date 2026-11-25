"""
SCAN-TO-STOCK-01 — the HTTP surface of «Registrar equipo por escaneo» (`scan_intake`).

    POST /api/admin/inventory/scan/check/                                  what a code is, in THIS company
    GET  /api/admin/inventory/scan/models/?q=&branch=                      the model search (the till's)
    GET  /api/admin/inventory/scan/models/<pk>/configurations/?branch=     the till's picker + the used variants
    POST /api/admin/inventory/scan/receive/?branch=   Idempotency-Key      register the device (explicit confirm)

The inventory's authority, not the till's: `inventory.adjust` in the company (the stockroom registers devices
without holding `sales.pos.use`), and the branch within the caller's grants (`resolve_branch_for_user`, a branch
outside them is 404 like one that does not exist). What the caller may do beyond that — create a variant, see costs,
set a price or a warranty, name a supplier, deliver to an order — is decided once (`pos_device_services.authority_for`)
and handed to the services as facts, exactly as the till does.

A code travels in a POST body, never in a URL: IMEIs do not belong in access logs.
"""
from __future__ import annotations

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import pos_device_services as quick
from . import scan_intake
from .db_busy import answers_busy
from .inventory_views import CAP_INVENTORY_ADJUST, _LEGACY_INVENTORY_ADJUST_ROLES, _company_context
from .models import ProductModel
from .pos_device_views import fingerprint
from .tenancy import BranchAccessError, NoBranchError, resolve_branch_for_user
from .throttles import AdminInventoryReportsThrottle, AdminPosThrottle, AdminStockMovementsThrottle

MAX_CODE = 200
_NOT_FOUND = {'detail': 'No encontrado.'}


def _company(request):
    return _company_context(request, CAP_INVENTORY_ADJUST, _LEGACY_INVENTORY_ADJUST_ROLES)


def _branch(request, company):
    try:
        return resolve_branch_for_user(request.user, company, request.query_params.get('branch'),
                                       allow_all=False), None
    except NoBranchError as exc:
        return None, Response({'detail': str(exc)}, status=status.HTTP_403_FORBIDDEN)
    except BranchAccessError:
        return None, Response({'detail': 'Sucursal no encontrada o sin acceso.'}, status=status.HTTP_404_NOT_FOUND)


class ScanCheckView(APIView):
    """A scanner fires these in bursts: the till's lookup rate."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminPosThrottle]
    http_method_names = ['post', 'options']

    def post(self, request):
        company, error = _company(request)
        if error:
            return error
        data = request.data if isinstance(request.data, dict) else {}
        code = data.get('code')
        if not isinstance(code, str) or len(code) > MAX_CODE:
            return Response({'detail': 'Escanea o escribe un código.', 'code': 'invalid', 'field': 'code'},
                            status=status.HTTP_400_BAD_REQUEST)
        product_id = data.get('product_id')
        if product_id not in (None, ''):
            try:
                product_id = quick._pk(product_id, 'product_id', label='Variante')
            except quick.QuickSaleError as exc:
                return Response(exc.body(), status=exc.status)
        try:
            expected = data.get('expected') or ''
            answer = scan_intake.check(company=company, user=request.user, code=code, product_id=product_id or None,
                                       choice=data.get('choice'), expected=expected if isinstance(expected, str) else '?')
        except scan_intake.ScanIntakeError as exc:
            return Response(exc.body(), status=exc.status)
        return Response(answer)


class ScanModelSearchView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminInventoryReportsThrottle]

    def get(self, request):
        company, error = _company(request)
        if error:
            return error
        branch, error = _branch(request, company)
        if error:
            return error
        return Response({'results': quick.search_models(company, branch, request.query_params.get('q'))})


class ScanModelConfigurationsView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminInventoryReportsThrottle]

    def get(self, request, pk):
        company, error = _company(request)
        if error:
            return error
        branch, error = _branch(request, company)
        if error:
            return error
        model = ProductModel.objects.filter(company=company, pk=pk).first()
        if model is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        authority = quick.authority_for(request.user, company)
        answer = quick.configurations(company, model, branch, authority, actor=request.user)
        answer['used_variants'] = scan_intake.used_variants(company, model)
        answer['branch'] = {'id': branch.pk, 'name': branch.name}
        return Response(answer)


class ScanReceiveView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminStockMovementsThrottle]
    http_method_names = ['post', 'options']

    @answers_busy
    def post(self, request):
        company, error = _company(request)
        if error:
            return error
        branch, error = _branch(request, company)
        if error:
            return error
        key = (request.headers.get('Idempotency-Key') or '').strip()
        if not key:
            return Response({'detail': 'Falta la cabecera Idempotency-Key.', 'code': 'idempotency_key_required'},
                            status=status.HTTP_400_BAD_REQUEST)
        if len(key) > quick.MAX_KEY_LENGTH:
            return Response({'detail': 'La cabecera Idempotency-Key es demasiado larga.', 'code': 'invalid'},
                            status=status.HTTP_400_BAD_REQUEST)
        body = request.data if isinstance(request.data, dict) else None
        if body is None:
            return Response({'detail': 'Datos no válidos.', 'code': 'invalid'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            answer, created = scan_intake.receive_once(
                company=company, branch=branch, body=dict(body), actor=request.user, request=request,
                authority=quick.authority_for(request.user, company), key=key,
                fingerprint=fingerprint({**body, '_door': 'scan'}, branch.pk),
            )
        except scan_intake.ScanIntakeError as exc:
            return Response(exc.body(), status=exc.status)
        return Response(answer, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
