"""
CATALOG-REFERENCE-01 · PR F — the HTTP surface of the till's quick device sale (ADR §4.5).

    GET  /api/admin/pos/models/?q=&branch=                          the model search
    GET  /api/admin/pos/models/<pk>/configurations/?branch=         the picker: palette − exclusions, stock here
    POST /api/admin/pos/devices/receive/?branch=   Idempotency-Key  «Llegó el equipo»

and the same three under `/api/v1/internal/<slug>/sales/pos/` for native clients. Both surfaces call the same
functions of `pos_device_services` with the same facts, so their answers are EQUAL (a parity test says so).

Gates, in the order the till already uses them: the company (another company's model, option, supplier or order
line answers 404), `sales.pos.use` (403), the till's branch within the caller's grants (`resolve_pos_branch`, 400).
What the caller may do beyond that — receive, create a variant, see costs, change a price — is decided once
(`pos_device_services.authority_for`) and handed to the service as facts.
"""
from __future__ import annotations

import hashlib
import json

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import pos_device_services as quick
from .models import ProductModel
from .pos_views import CAP_POS, _branch_or_error, _context
from .throttles import AdminPosSaleThrottle, AdminPosThrottle
from .v1_pos_views import V1PosSurfaceMixin, _BranchRefused, _branch_error

_NOT_FOUND = {'detail': 'No encontrado.'}


def fingerprint(body, branch_id) -> str:
    text = json.dumps({'body': body, 'branch': branch_id}, sort_keys=True, separators=(',', ':'), default=str,
                      ensure_ascii=False)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _search(request, company, branch) -> Response:
    return Response({'results': quick.search_models(company, branch, request.query_params.get('q'))})


def _configurations(request, company, branch, pk) -> Response:
    model = ProductModel.objects.filter(company=company, pk=pk).first()
    if model is None:
        return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
    return Response(quick.configurations(company, model, branch, quick.authority_for(request.user, company),
                                         actor=request.user))


def _receive(request, company, branch) -> Response:
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
        answer, created = quick.receive_once(
            company=company, branch=branch, body=dict(body), actor=request.user, request=request,
            authority=quick.authority_for(request.user, company), key=key, fingerprint=fingerprint(body, branch.pk),
        )
    except quick.QuickSaleError as exc:
        return Response(exc.body(), status=exc.status)
    return Response(answer, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# /api/admin/pos/… — the Web till
# ---------------------------------------------------------------------------

class _AdminTill(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminPosThrottle]

    def till(self, request):
        company, error = _context(request, CAP_POS)
        if error:
            return None, None, error
        branch, error = _branch_or_error(request, company, request.query_params.get('branch'))
        if error:
            return None, None, error
        return company, branch, None


class AdminPosModelSearchView(_AdminTill):
    def get(self, request):
        company, branch, error = self.till(request)
        return error or _search(request, company, branch)


class AdminPosModelConfigurationsView(_AdminTill):
    def get(self, request, pk):
        company, branch, error = self.till(request)
        return error or _configurations(request, company, branch, pk)


class AdminPosDeviceReceiveView(_AdminTill):
    throttle_classes = [AdminPosSaleThrottle]
    http_method_names = ['post', 'options']

    def post(self, request):
        company, branch, error = self.till(request)
        return error or _receive(request, company, branch)


# ---------------------------------------------------------------------------
# /api/v1/internal/<slug>/sales/pos/… — native clients
# ---------------------------------------------------------------------------

class _V1Till(V1PosSurfaceMixin, APIView):
    def till(self, request):
        company = self.get_till(request)
        try:
            return company, self.resolve_branch(request, company, request.query_params.get('branch')), None
        except _BranchRefused as exc:
            return company, None, _branch_error(exc)


class V1PosModelSearchView(_V1Till):
    def get(self, request, company_slug=None):
        company, branch, error = self.till(request)
        return error or _search(request, company, branch)


class V1PosModelConfigurationsView(_V1Till):
    def get(self, request, pk, company_slug=None):
        company, branch, error = self.till(request)
        return error or _configurations(request, company, branch, pk)


class V1PosDeviceReceiveView(_V1Till):
    throttle_classes = [AdminPosSaleThrottle]
    http_method_names = ['post', 'options']

    def post(self, request, company_slug=None):
        company, branch, error = self.till(request)
        return error or _receive(request, company, branch)
