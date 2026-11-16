"""
INV-OPS-01 — the HTTP surface of «Recibir mercadería».

    POST /api/admin/inventory/receipts/preview/    read-only: writes nothing, stages nothing
    POST /api/admin/inventory/receipts/confirm/    header `Idempotency-Key` (1–64 characters)
    GET|PUT|DELETE /api/admin/inventory/receipts/draft/    INV-DRAFT-01: the author's unfinished draft

Authority, decided here, in this order:

  * BEFORE the body is read: `inventory.adjust` for both — the authority of manual entries and «Registrar
    equipos» (`inventory_views._company_context`, legacy bridge included). Receiving an EXISTING product needs
    nothing of the catalogue: creating products is not part of this;
  * WHILE the body is read (`receipt_services.normalise`, header first, then each line field by field): a cost
    needs `costs.view` (`may_view_costs`) and is refused with 403 `costs_forbidden` otherwise (DEC-INV-05: a
    missing permission is a 403); no response carries one. INV-COM-01A: a used device's own `sale_price` needs
    `products.manage` (`may_manage_products`, the authority the unit's PATCH asks), refused with 403
    `prices_forbidden` otherwise — the device can still be received without it; `public_notes` need only
    `inventory.adjust`. The first check that fails answers: a malformed field read before the cost or the
    price is a 400, one read after it never gets the chance (pinned in `test_inventory_receipts.CostTest`);
  * AFTER the body is read: the branch is a selection among the caller's grants (`resolve_branch_for_user`):
    another company's, an inactive one or one outside the grants answers 404, like one that does not exist;
    omitted means the caller's default branch.

IDEMPOTENT. The same author repeating a key with the same normalised body gets the first answer back (200,
`replayed: true`); with another body, 409. Both need the caller's CURRENT reach over the receipt's branch (the stored one),
checked first: 404 like any out-of-scope branch. A refused attempt leaves no trail, so its key can be used again.
Two requests with one key take turns on an advisory lock of (company, author, key). The trail is the audit
line of the receipt (no migration): the key, a fingerprint of the body and a record of ids and counts.
"""
from __future__ import annotations

from django.db import OperationalError, transaction
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import receipt_drafts as drafts
from . import receipt_services as receipts
from .inventory_views import CAP_INVENTORY_ADJUST, _LEGACY_INVENTORY_ADJUST_ROLES, _company_context, may_view_costs
from .stock_unit_views import may_manage_products
from .tenancy import BranchAccessError, NoBranchError, resolve_branch_for_user
from .throttles import AdminInventoryReportsThrottle, AdminReceiptDraftThrottle, AdminStockMovementsThrottle


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


def _stored_branch(trail) -> int:
    """
    The branch a stored receipt was made in. A trail that cannot say (no record, no id, a value that is not a
    plausible key) is an EXPLICIT DENIAL — the 404 of an out-of-scope branch — never a 500 and never a fall-back
    to the caller's default branch, which would authorize the replay against the wrong place.
    """
    metadata = trail.metadata if isinstance(trail.metadata, dict) else {}
    record = metadata.get('record')
    value = record.get('branch_id') if isinstance(record, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= receipts.MAX_ID:
        raise receipts.ReceiptError('Sucursal no encontrada o sin acceso.', status=404, code='not_found',
                                    field='branch')
    return value


class _ReceiptView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ['post', 'options']

    def context(self, request):
        """(company, normalised body, error response) — authority first, then the body."""
        company, error = _company_context(request, CAP_INVENTORY_ADJUST, _LEGACY_INVENTORY_ADJUST_ROLES)
        if error:
            return None, None, error
        try:
            # The default parsers stay (this route takes no files and is not one of the upload tiers of
            # `request_limits`); a body nested deeper than the interpreter can read is a 400, not a 500.
            try:
                body = request.data
            except RecursionError:
                raise receipts.ReceiptError('JSON no válido.') from None
            normalised = receipts.normalise(
                body, may_view_costs=may_view_costs(request), may_set_prices=may_manage_products(request))
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
                    # A replay READS the receipt: the caller must still reach the branch it was made in (the
                    # stored one — the body may omit it, and the current default can be another). Checked before
                    # the fingerprint, so a revoked operator learns nothing, not even that the key exists.
                    _branch(request, company, _stored_branch(earlier))
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
                # INV-DRAFT-01: the reception is made (now or by the earlier attempt this one replays), so the
                # author's draft has served its purpose. Same transaction: a refused receipt keeps it.
                drafts.discard(company=company, user=request.user)
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


class InventoryReceiptDraftView(APIView):
    """
    INV-DRAFT-01 — the caller's draft of «Recibir mercadería» in the company of the request.

        GET     {draft: {...payload, branch, updated_at, expires_at}} or {draft: null} (an expired one is deleted)
        PUT     {branch: id|null, payload: {...}} → upsert, expiry renewed (`RECEIPT_DRAFT_TTL_SECONDS`)
        DELETE  204, idempotent

    The authority of the receipt itself: `inventory.adjust` before anything (master users via `?company=`), a
    branch among the caller's grants (404 like preview otherwise), a cost only with `costs.view` (403
    `costs_forbidden`) and never read back without it. Only the author's own draft is ever read or deleted.
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminReceiptDraftThrottle]
    http_method_names = ['get', 'put', 'delete', 'options']

    def _company(self, request):
        return _company_context(request, CAP_INVENTORY_ADJUST, _LEGACY_INVENTORY_ADJUST_ROLES)

    @staticmethod
    def _reachable(request, company, branch_id):
        """The stored branch if the caller still reaches it, else None (the screen asks again)."""
        if branch_id is None:
            return None
        try:
            return resolve_branch_for_user(request.user, company, branch_id, allow_all=False).pk
        except (BranchAccessError, NoBranchError):
            return None

    def get(self, request):
        company, error = self._company(request)
        if error:
            return error
        draft = drafts.current(company=company, user=request.user)
        if draft is None:
            return Response({'draft': None}, status=status.HTTP_200_OK)
        return Response({'draft': drafts.present(
            draft, branch_id=self._reachable(request, company, draft.branch_id),
            may_view_costs=may_view_costs(request))}, status=status.HTTP_200_OK)

    def put(self, request):
        company, error = self._company(request)
        if error:
            return error
        try:
            try:
                body = request.data
            except RecursionError:
                raise receipts.ReceiptError('JSON no válido.') from None
            if not isinstance(body, dict) or hasattr(body, 'getlist'):
                raise receipts.ReceiptError('El cuerpo de la petición debe ser un objeto JSON.')
            if 'payload' not in body:
                raise receipts.ReceiptError('Falta el borrador.', field='payload')
            wanted = receipts._id(body.get('branch'), 'branch')
            payload = drafts.validate(body['payload'], may_view_costs=may_view_costs(request))
            branch = _branch(request, company, wanted) if wanted is not None else None
        except receipts.ReceiptError as exc:
            return _refusal(exc)
        draft = drafts.upsert(company=company, user=request.user, branch=branch, payload=payload)
        # The payload is not echoed: the screen already has it.
        return Response({'draft': {'branch': draft.branch_id, 'updated_at': draft.updated_at.isoformat(),
                                   'expires_at': draft.expires_at.isoformat()}}, status=status.HTTP_200_OK)

    def delete(self, request):
        company, error = self._company(request)
        if error:
            return error
        drafts.discard(company=company, user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
