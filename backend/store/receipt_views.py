"""
INV-OPS-01 — the HTTP surface of «Recibir mercadería».

    POST /api/admin/inventory/receipts/preview/    read-only: writes nothing, stages nothing
    POST /api/admin/inventory/receipts/confirm/    header `Idempotency-Key` (1–64 characters)

Authority, decided here and BEFORE anything is read from the body:

  * `inventory.adjust` for both — the authority of manual entries and «Registrar equipos»
    (`inventory_views._company_context`, legacy bridge included). Receiving an EXISTING product needs
    nothing of the catalogue: creating products is not part of this;
  * the branch is a selection among the caller's grants (`resolve_branch_for_user`): another company's, an
    inactive one or one outside the grants answers 404, like one that does not exist; omitted means the
    caller's default branch;
  * a cost needs `costs.view` (`may_view_costs`) and is refused with 400 otherwise; no response carries one.

IDEMPOTENT. The same author repeating a key with the same normalised body gets the first answer back (200,
`replayed: true`); with another body, 409. A refused attempt leaves no trail, so its key can be used again.
Two requests with one key take turns on an advisory lock of (company, author, key). The trail is the audit
line of the receipt (no migration): the key, a fingerprint of the body and a record of ids and counts.
"""
from __future__ import annotations

from django.db import OperationalError, transaction
from rest_framework import permissions, status
from rest_framework.exceptions import ParseError
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView

from . import receipt_services as receipts
from .inventory_views import CAP_INVENTORY_ADJUST, _LEGACY_INVENTORY_ADJUST_ROLES, _company_context, may_view_costs
from .tenancy import BranchAccessError, NoBranchError, resolve_branch_for_user
from .throttles import AdminInventoryReportsThrottle, AdminStockMovementsThrottle


class ReceiptJSONParser(JSONParser):
    """JSON only; a body nested deeper than the interpreter can read is a 400, not a 500."""

    def parse(self, stream, media_type=None, parser_context=None):
        try:
            return super().parse(stream, media_type, parser_context)
        except RecursionError:
            raise ParseError('JSON no válido.') from None


def _refusal(exc: receipts.ReceiptError) -> Response:
    return Response(exc.body(), status=exc.status)


def _branch(request, company, wanted):
    try:
        return resolve_branch_for_user(request.user, company, wanted, allow_all=False)
    except NoBranchError as exc:
        raise receipts.ReceiptError(str(exc), status=403, code='forbidden', field='branch') from None
    except BranchAccessError:
        raise receipts.ReceiptError('Sucursal no encontrada o sin acceso.', status=404, code='not_found',
                                    field='branch') from None


class _ReceiptView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [ReceiptJSONParser]
    http_method_names = ['post', 'options']

    def context(self, request):
        """(company, normalised body, error response) — authority first, then the body."""
        company, error = _company_context(request, CAP_INVENTORY_ADJUST, _LEGACY_INVENTORY_ADJUST_ROLES)
        if error:
            return None, None, error
        try:
            normalised = receipts.normalise(request.data, may_view_costs=may_view_costs(request))
        except receipts.ReceiptError as exc:
            return None, None, _refusal(exc)
        return company, normalised, None


class InventoryReceiptPreviewView(_ReceiptView):
    throttle_classes = [AdminInventoryReportsThrottle]

    def post(self, request):
        company, normalised, error = self.context(request)
        if error:
            return error
        try:
            branch = _branch(request, company, normalised['branch'])
            resolved = receipts.resolve(company, normalised, branch=branch)
            return Response(receipts.preview(company, resolved, normalised), status=status.HTTP_200_OK)
        except receipts.ReceiptError as exc:
            return _refusal(exc)


class InventoryReceiptConfirmView(_ReceiptView):
    throttle_classes = [AdminStockMovementsThrottle]

    def post(self, request):
        company, normalised, error = self.context(request)
        if error:
            return error

        key = (request.headers.get('Idempotency-Key') or '').strip()
        if not key or len(key) > receipts.MAX_KEY or not key.isascii() or not key.isprintable():
            return Response(
                {'detail': 'Falta la cabecera Idempotency-Key (de 1 a 64 caracteres).', 'code': 'idempotency_key'},
                status=status.HTTP_400_BAD_REQUEST)
        stamp = receipts.fingerprint(normalised)

        replay = None
        try:
            with transaction.atomic():
                receipts.lock(company.pk, request.user.pk, key)
                earlier = receipts.previous(company, request.user, key)
                if earlier is not None:
                    if earlier.metadata.get('fingerprint') != stamp:
                        raise receipts.ReceiptError(
                            'Esa clave ya confirmó otra recepción. Vuelve a empezar la recepción.',
                            status=status.HTTP_409_CONFLICT, code='idempotency_conflict')
                    replay = earlier
                else:
                    branch = _branch(request, company, normalised['branch'])
                    resolved = receipts.resolve(company, normalised, branch=branch)
                    record, trail = receipts.confirm(
                        company=company, resolved=resolved, normalised=normalised, actor=request.user,
                        request=request, key=key, stamp=stamp)
        except receipts.ReceiptError as exc:
            return _refusal(exc)
        except OperationalError as exc:
            # A deadlock with another receipt over the same shelf or device: the whole transaction is rolled
            # back, the key stays free, and the person is told to try again.
            if not receipts.is_busy(exc):
                raise
            return Response({'detail': 'Otra recepción está en curso; vuelve a intentarlo.', 'code': 'busy'},
                            status=status.HTTP_409_CONFLICT)

        # The answer is read AFTER the commit: a failing read can neither undo a reception nor fake one.
        if replay is not None:
            return Response(receipts.present(company, replay.metadata['record'], replay, replayed=True),
                            status=status.HTTP_200_OK)
        return Response(receipts.present(company, record, trail), status=status.HTTP_201_CREATED)
