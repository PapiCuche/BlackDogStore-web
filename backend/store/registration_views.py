"""
COMMERCE-ADMIN-UX-01 — the HTTP surface of «Registrar artículo».

    POST /api/admin/products/register/      header `Idempotency-Key`

Authority, decided here and BEFORE anything is written:

  * `products.manage` to register at all (the product form's authority, legacy
    bridge included);
  * `inventory.adjust` for a device or a quantity on a shelf — the inventory
    module's own resolution (`inventory_views._company_context`);
  * `costs.view` for any cost (`may_view_costs`, the one answer for costs);
  * every branch inside the caller's grants (`resolve_branch_for_user`): one
    outside them answers 404 and nothing is written.

IDEMPOTENT. A write needs an `Idempotency-Key`. The same author repeating the
same key with the same body gets the first answer back (200, `replayed`);
with another body, 409. A refused attempt leaves no trail, so the key can be
used again. The trail is the audit line of the registration (no migration):
its metadata keeps the key, a fingerprint of the body and the record — ids and
flags, never an amount. Two requests with one key take turns on an advisory
lock of (company, author, key), held until the transaction ends.

`dry_run: true` runs the very same services inside a transaction that is then
rolled back, so the review screen shows the real refusals (a duplicate IMEI, a
code of another article) and the publication problems — and nothing remains,
not even an audit line. It needs no key.
"""
from __future__ import annotations

import hashlib
import json

from django.db import connection, transaction
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import registration_services as registration
from .admin_views import CAP_PRODUCTS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES
from .admin_views import _company_context as _catalog_context
from .inventory_views import CAP_INVENTORY_ADJUST, _LEGACY_INVENTORY_ADJUST_ROLES, may_view_costs
from .inventory_views import _company_context as _inventory_context
from .tenancy import BranchAccessError, NoBranchError, resolve_branch_for_user
from .throttles import AdminProductWriteThrottle

MAX_KEY_LENGTH = 100


class _DryRun(Exception):
    """Raised inside the transaction of a dry run so that it is rolled back."""


def fingerprint(body: dict) -> str:
    canonical = {k: v for k, v in body.items() if k != 'dry_run'}
    text = json.dumps(canonical, sort_keys=True, separators=(',', ':'), default=str, ensure_ascii=False)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _lock(company_id, actor_id, key) -> None:
    if connection.vendor != 'postgresql':
        return
    digest = hashlib.sha256(f'article-register|{company_id}|{actor_id}|{key}'.encode()).digest()[:8]
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', [int.from_bytes(digest, 'big', signed=True)])


class AdminProductRegisterView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminProductWriteThrottle]
    http_method_names = ['post', 'options']

    def post(self, request):
        company, error = _catalog_context(request, CAP_PRODUCTS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES)
        if error:
            return error
        body = request.data if isinstance(request.data, dict) else {}
        dry_run = body.get('dry_run') is True

        key = (request.headers.get('Idempotency-Key') or '').strip()
        if not dry_run and not key:
            return Response({'detail': 'Falta la cabecera Idempotency-Key.', 'step': 'review'},
                            status=status.HTTP_400_BAD_REQUEST)
        if len(key) > MAX_KEY_LENGTH:
            return Response({'detail': 'La cabecera Idempotency-Key es demasiado larga.', 'step': 'review'},
                            status=status.HTTP_400_BAD_REQUEST)

        stock_company, stock_error = _inventory_context(request, CAP_INVENTORY_ADJUST, _LEGACY_INVENTORY_ADJUST_ROLES)
        may_adjust = stock_error is None and getattr(stock_company, 'pk', None) == company.pk

        def branch_for(branch_id):
            try:
                return resolve_branch_for_user(
                    request.user, company, None if branch_id in (None, '') else branch_id, allow_all=False,
                )
            except NoBranchError as exc:
                raise registration.RegistrationError(str(exc), step='stock', field='branch', status=403,
                                                     code='forbidden') from None
            except BranchAccessError:
                raise registration.RegistrationError('Sucursal no encontrada o sin acceso.', step='stock',
                                                     field='branch', status=404, code='not_found') from None

        def run():
            return registration.register(
                company=company, body=body, actor=request.user, request=request, may_adjust=may_adjust,
                may_view_costs=may_view_costs(request), branch_for=branch_for, labels=not dry_run,
            )

        try:
            if dry_run:
                answer = None
                try:
                    with transaction.atomic():
                        answer = registration.present(company, run(), dry_run=True)
                        raise _DryRun
                except _DryRun:
                    pass
                return Response(answer, status=status.HTTP_200_OK)

            stamp = fingerprint(body)
            with transaction.atomic():
                _lock(company.pk, request.user.pk, key)
                earlier = registration.previous(company, request.user, key)
                if earlier is not None:
                    if earlier.metadata.get('fingerprint') != stamp:
                        return Response(
                            {'detail': 'Esa clave ya registró otro artículo. Vuelve a empezar el registro.',
                             'step': 'review', 'code': 'idempotency_conflict'},
                            status=status.HTTP_409_CONFLICT)
                    return Response(
                        registration.present(company, earlier.metadata['record'], replayed=True),
                        status=status.HTTP_200_OK)
                record = run()
                registration.record_trail(company=company, actor=request.user, request=request, key=key,
                                          fingerprint=stamp, record=record)
                answer = registration.present(company, record)
        except registration.RegistrationError as exc:
            return Response(exc.body(), status=exc.status)
        return Response(answer, status=status.HTTP_201_CREATED)
