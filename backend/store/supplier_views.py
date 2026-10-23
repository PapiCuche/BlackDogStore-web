"""
DEVICE-COMMERCE-01 · DC3 — the panel's surface for suppliers, offers and the
supply of orders sold «bajo pedido».

    /api/admin/suppliers/                         products.view / products.manage
    /api/admin/suppliers/<pk>/
    /api/admin/suppliers/settlement/              + costs.view (read only, PAYABLES-01 hook)
    /api/admin/supplier-offers/                   ?supplier= &product= &state=active|expired|inactive
    /api/admin/supplier-offers/<pk>/              (+ /deactivate/, /history/)
    /api/admin/suppliers/<pk>/product-refs/       GET products.view · POST products.manage
                                                  (the supplier's own codes, SUPPLIER-DEVICE-FLOW-02)
    /api/admin/products/<pk>/sale-mode/           products.manage
    /api/admin/orders/<pk>/supply/<line>/confirm|receive|fail/
                                                  sales.orders.manage (+ inventory.adjust to receive)

The company is the caller's; a row of another company answers 404 like one that
does not exist. An offer's agreed cost travels and is written only with
`costs.view` (DC1): without it the key is absent and a sent value is ignored.
"""
from __future__ import annotations

from django.db import transaction
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import supply_services
from .admin_views import (
    CAP_INVENTORY_ADJUST, CAP_ORDERS_MANAGE, CAP_ORDERS_VIEW, CAP_PRODUCTS_MANAGE, CAP_PRODUCTS_VIEW,
    _LEGACY_MANAGE_CATALOG_ROLES, _LEGACY_VIEW_CATALOG_ROLES, _LEGACY_VIEW_ORDERS_ROLES, _company_context,
    _paginate,
)
from .cost_services import user_may_view_costs
from .models import AdminAuditLog, OrderItem, Product, Supplier, SupplierOffer
from .tenancy import has_capability, visible_branch_ids

_NOT_FOUND = {'detail': 'No encontrado.'}


def _view(request):
    return _company_context(request, CAP_PRODUCTS_VIEW, _LEGACY_VIEW_CATALOG_ROLES)


def _manage(request):
    return _company_context(request, CAP_PRODUCTS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES)


def _error(exc: supply_services.SupplyError) -> Response:
    code = {'not_found': status.HTTP_404_NOT_FOUND, 'ref_conflict': status.HTTP_409_CONFLICT}.get(
        exc.code, status.HTTP_400_BAD_REQUEST)
    return Response(exc.body(), status=code)


def _flag(value) -> bool:
    return value in (True, 'true', 'True', '1', 1)


def supplier_payload(supplier) -> dict:
    return {
        'id': supplier.pk, 'name': supplier.name, 'document_type': supplier.document_type,
        'document_number': supplier.document_number, 'contact_name': supplier.contact_name,
        'phone': supplier.phone, 'email': supplier.email, 'notes': supplier.notes,
        'is_active': supplier.is_active, 'offer_count': supplier.offers.count(),
    }


def offer_state(offer, on=None) -> str:
    on = on or supply_services.today()
    if not offer.is_active:
        return 'inactive'
    if offer.valid_until < on:
        return 'expired'
    if offer.valid_from > on:
        return 'scheduled'
    if offer.confirmed_quantity is None or not offer.availability_confirmed:
        return 'unconfirmed'
    if offer.remaining <= 0:
        return 'exhausted'
    return 'active'


def offer_payload(offer, *, with_cost: bool) -> dict:
    data = {
        'id': offer.pk, 'supplier_id': offer.supplier_id, 'supplier_name': offer.supplier.name,
        'product_id': offer.product_id, 'product_name': offer.product.name, 'product_slug': offer.product.slug,
        'commercial_price': str(offer.commercial_price), 'confirmed_quantity': offer.confirmed_quantity,
        'committed_quantity': offer.committed_quantity, 'remaining': offer.remaining_known,
        'availability_confirmed': offer.availability_confirmed,
        'valid_from': offer.valid_from.isoformat(), 'valid_until': offer.valid_until.isoformat(),
        'lead_time_days': offer.lead_time_days, 'is_active': offer.is_active,
        'deactivated_reason': offer.deactivated_reason, 'state': offer_state(offer),
        'updated_at': offer.updated_at.isoformat() if offer.updated_at else None,
    }
    if with_cost:
        data['agreed_cost'] = str(offer.agreed_cost) if offer.agreed_cost is not None else None
    return data


class AdminSupplierListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company, error = _view(request)
        if error:
            return error
        qs = Supplier.objects.filter(company=company)
        search = (request.query_params.get('search') or '').strip()
        if search:
            qs = qs.filter(name__icontains=search)
        active = (request.query_params.get('active') or '').strip().lower()
        if active in ('true', 'false'):
            qs = qs.filter(is_active=active == 'true')
        page, meta = _paginate(qs.order_by('name', 'pk'), request)
        return Response({**meta, 'results': [supplier_payload(row) for row in page]})

    def post(self, request):
        company, error = _manage(request)
        if error:
            return error
        fields = {key: request.data[key] for key in supply_services._SUPPLIER_FIELDS if key in request.data}
        try:
            supplier = supply_services.create_supplier(company=company, actor=request.user, request=request,
                                                       **fields)
        except supply_services.SupplyError as exc:
            return _error(exc)
        return Response(supplier_payload(supplier), status=status.HTTP_201_CREATED)


class AdminSupplierDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        company, error = _view(request)
        if error:
            return error
        supplier = Supplier.objects.filter(company=company, pk=pk).first()
        if supplier is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        return Response(supplier_payload(supplier))

    def patch(self, request, pk):
        company, error = _manage(request)
        if error:
            return error
        supplier = Supplier.objects.filter(company=company, pk=pk).first()
        if supplier is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        allowed = (*supply_services._SUPPLIER_FIELDS, 'is_active')
        fields = {key: request.data[key] for key in allowed if key in request.data}
        try:
            supplier = supply_services.update_supplier(supplier, actor=request.user, request=request, **fields)
        except supply_services.SupplyError as exc:
            return _error(exc)
        return Response(supplier_payload(supplier))


class AdminSupplierSettlementView(APIView):
    """
    PAYABLES-01 hook, READ ONLY: «Pendiente de liquidar al proveedor» — what was
    received on order, per supplier, at the frozen cost. No debt is recorded.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company, error = _view(request)
        if error:
            return error
        if not user_may_view_costs(request.user, company):
            return Response({'detail': 'Se requiere permiso para ver costos.'}, status=status.HTTP_403_FORBIDDEN)
        # Branch-scoped by the order's fulfilment branch, like every sales figure: a
        # caller limited to some branches sees what those branches received.
        return Response({'results': supply_services.pending_settlement(
            company, branch_ids=visible_branch_ids(request.user, company))})


_OFFER_FIELDS = ('supplier_id', 'product_id', 'agreed_cost', 'commercial_price', 'confirmed_quantity',
                 'valid_from', 'valid_until', 'lead_time_days', 'availability_confirmed')


class AdminOfferListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company, error = _view(request)
        if error:
            return error
        qs = SupplierOffer.objects.filter(company=company).select_related('supplier', 'product')
        params = request.query_params
        for key, field in (('supplier', 'supplier_id'), ('product', 'product_id')):
            value = supply_services._pk(params.get(key))
            if params.get(key) not in (None, ''):
                qs = qs.filter(**{field: value}) if value is not None else qs.none()
        today = supply_services.today()
        state = (params.get('state') or '').strip()
        if state == 'active':
            qs = qs.filter(is_active=True, valid_until__gte=today)
        elif state == 'expired':
            qs = qs.filter(valid_until__lt=today)
        elif state == 'inactive':
            qs = qs.filter(is_active=False)
        page, meta = _paginate(qs.order_by('-valid_until', 'pk'), request)
        with_cost = user_may_view_costs(request.user, company)
        return Response({**meta, 'results': [offer_payload(row, with_cost=with_cost) for row in page]})

    def post(self, request):
        company, error = _manage(request)
        if error:
            return error
        from . import supplier_offer_import

        fields = {key: request.data[key] for key in _OFFER_FIELDS if key in request.data}
        with_cost = user_may_view_costs(request.user, company)
        try:
            with transaction.atomic():
                # SUPPLIER-DEVICE-FLOW-02: never two active offers of one supplier and variant over one day.
                supplier_offer_import.guard_offer_overlap(company, fields)
                offer = supply_services.create_offer(company=company, actor=request.user, request=request,
                                                     may_view_costs=with_cost, **fields)
        except supply_services.SupplyError as exc:
            return _error(exc)
        return Response(offer_payload(offer, with_cost=with_cost), status=status.HTTP_201_CREATED)


def _offer(company, pk):
    return SupplierOffer.objects.filter(company=company, pk=pk).select_related('supplier', 'product').first()


class AdminOfferDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        company, error = _view(request)
        if error:
            return error
        offer = _offer(company, pk)
        if offer is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        return Response(offer_payload(offer, with_cost=user_may_view_costs(request.user, company)))

    def patch(self, request, pk):
        company, error = _manage(request)
        if error:
            return error
        offer = _offer(company, pk)
        if offer is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        from . import supplier_offer_import

        allowed = (*_OFFER_FIELDS[2:], 'is_active')
        fields = {key: request.data[key] for key in allowed if key in request.data}
        with_cost = user_may_view_costs(request.user, company)
        try:
            with transaction.atomic():
                supplier_offer_import.guard_offer_overlap(company, fields, current=offer)
                offer = supply_services.update_offer(offer, actor=request.user, request=request,
                                                     may_view_costs=with_cost, **fields)
        except supply_services.SupplyError as exc:
            return _error(exc)
        return Response(offer_payload(_offer(company, pk), with_cost=with_cost))


class AdminOfferDeactivateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        company, error = _manage(request)
        if error:
            return error
        offer = _offer(company, pk)
        if offer is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        supply_services.deactivate_offer(offer, actor=request.user, request=request,
                                         reason=request.data.get('reason') or '')
        return Response(offer_payload(_offer(company, pk), with_cost=user_may_view_costs(request.user, company)))


class AdminOfferHistoryView(APIView):
    """What happened to one offer: the audit trail, newest first. Never its cost."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        company, error = _view(request)
        if error:
            return error
        offer = _offer(company, pk)
        if offer is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        rows = (AdminAuditLog.objects.filter(company=company, target_type='supplier_offer', target_id=str(offer.pk))
                .select_related('actor').order_by('-created_at', '-pk')[:200])
        return Response({'results': [{
            'action': row.action, 'at': row.created_at.isoformat(),
            'actor': row.actor.get_username() if row.actor_id else None,
            'metadata': row.metadata,
        } for row in rows]})


class AdminProductSaleModeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk):
        company, error = _manage(request)
        if error:
            return error
        product = Product.objects.filter(company=company, pk=pk).first()
        if product is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        try:
            product = supply_services.set_sale_mode(product, request.data.get('sale_mode'), actor=request.user,
                                                    request=request)
        except supply_services.SupplyError as exc:
            return _error(exc)
        return Response({'id': product.pk, 'sale_mode': product.sale_mode})


# ---------------------------------------------------------------------------
# The supply of an order
# ---------------------------------------------------------------------------

def supply_line_payload(item, *, with_cost: bool = False) -> dict:
    """
    An on-order line as the panel follows it (SUPPLIER-DEVICE-FLOW-02): what
    was sold, to which branch, from which supplier; its costs (agreed, real,
    difference) only `with_cost` (`costs.view`, DC1). Never on a public API.
    """
    offer = item.supplier_offer
    branch = item.order.fulfillment_branch
    payload = {
        'id': item.pk, 'supply_origin': item.supply_origin, 'supply_status': item.supply_status,
        'supply_note': item.supply_note, 'lead_time_days': item.lead_time_days,
        'supplier_offer_id': item.supplier_offer_id,
        'supplier_name': offer.supplier.name if offer is not None else '',
        'order_id': item.order_id, 'product_id': item.product_id, 'product_name': item.product.name,
        'quantity': item.quantity, 'price': str(item.price),
        'fulfillment_branch': {'id': branch.pk, 'name': branch.name} if branch is not None else None,
    }
    if with_cost:
        payload.update(supply_services.line_costs(item))
    return payload


class AdminOrderSupplyActionView(APIView):
    """
    POST /api/admin/orders/<pk>/supply/<line>/<action>/ — confirm, receive or fail an
    on-order line, or record the real cost of a received one (`real-cost`:
    what receiving takes + costs.view; owner's decision D2).
    """
    permission_classes = [permissions.IsAuthenticated]
    ACTIONS = ('confirm', 'receive', 'fail', 'real-cost')

    def post(self, request, pk, line_pk, action):
        from .tenancy import visible_orders

        if action not in self.ACTIONS:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        company, error = _company_context(request, CAP_ORDERS_VIEW, _LEGACY_VIEW_ORDERS_ROLES)
        if error:
            return error
        order = visible_orders(request.user, company).filter(pk=pk).first()
        line = OrderItem.objects.filter(order=order, pk=line_pk).first() if order is not None else None
        if line is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        if not has_capability(request.user, company, CAP_ORDERS_MANAGE):
            return Response({'detail': 'No tienes permiso para administrar pedidos.'},
                            status=status.HTTP_403_FORBIDDEN)
        if action in ('receive', 'real-cost') and not has_capability(request.user, company, CAP_INVENTORY_ADJUST):
            return Response({'detail': 'Recibir mercadería requiere permiso para mover inventario.'},
                            status=status.HTTP_403_FORBIDDEN)
        if action == 'real-cost' and not user_may_view_costs(request.user, company):
            return Response({'detail': 'Registrar el costo real requiere el permiso «Ver costos y márgenes».'},
                            status=status.HTTP_403_FORBIDDEN)
        try:
            if action == 'confirm':
                line = supply_services.confirm_line(line, actor=request.user, company=company, request=request,
                                                    offer_id=request.data.get('offer_id'))
            elif action == 'receive':
                units = request.data.get('units')
                line = supply_services.receive_line(line, actor=request.user, company=company, request=request,
                                                    units=units if isinstance(units, list) else None,
                                                    unit_cost=request.data.get('unit_cost'))
            elif action == 'real-cost':
                line = supply_services.record_real_cost(line, actor=request.user, company=company, request=request,
                                                        unit_cost=request.data.get('unit_cost'))
            else:
                line = supply_services.fail_line(line, actor=request.user, company=company, request=request,
                                                 reason=request.data.get('reason') or '',
                                                 cause=request.data.get('cause') or None)
        except supply_services.SupplyError as exc:
            if exc.code == 'already_received':
                # SUPPLIER-DEVICE-FLOW-02: a repeated reception is not a wrong
                # request — it already happened. 409, with what it brought in.
                return Response({**exc.body(), 'units': getattr(exc, 'units', [])}, status=status.HTTP_409_CONFLICT)
            if exc.code in ('return_open', 'already_real', 'not_received'):
                return Response(exc.body(), status=status.HTTP_409_CONFLICT)
            if exc.code in ('identity_mismatch', 'incident_open'):
                # INV-USED-EXT-01 (D4): nothing was received; the incident is for a superior to resolve.
                return Response({**exc.body(), 'incident_id': getattr(exc, 'incident_id', None)},
                                status=status.HTTP_409_CONFLICT)
            return _error(exc)
        line = OrderItem.objects.select_related(
            'supplier_offer__supplier', 'product', 'order__fulfillment_branch').get(pk=line.pk)
        return Response(supply_line_payload(line, with_cost=user_may_view_costs(request.user, company)))


# ---------------------------------------------------------------------------
# The weekly Excel
# ---------------------------------------------------------------------------

def _xlsx(payload: bytes, filename: str):
    from .import_views import _xlsx as as_download

    return as_download(payload, filename)


class AdminOfferTemplateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        _company, error = _view(request)
        if error:
            return error
        from . import supplier_offer_import

        return _xlsx(supplier_offer_import.template_bytes(), supplier_offer_import.TEMPLATE_FILENAME)


class AdminOfferExportView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company, error = _view(request)
        if error:
            return error
        from . import supplier_offer_import

        payload = supplier_offer_import.export_bytes(
            company=company, include_cost=user_may_view_costs(request.user, company))
        return _xlsx(payload, supplier_offer_import.EXPORT_FILENAME)


class AdminOfferImportPreviewView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        company, error = _manage(request)
        if error:
            return error
        from . import supplier_offer_import, xlsx_reader

        upload = request.FILES.get('file')
        if upload is None:
            return Response({'detail': 'Adjunta el archivo.'}, status=status.HTTP_400_BAD_REQUEST)
        complete_supplier = None
        if _flag(request.data.get('complete_snapshot')):
            # INV-SNAPSHOT-01: a complete list switches offers off: the whole company in scope, and
            # ONE supplier of THIS company (another tenant's is simply not there).
            from .models import Supplier
            from .tenancy import has_company_wide_scope

            if not has_company_wide_scope(request.user, company):
                return Response({'detail': 'Una lista completa es de toda la empresa: tu acceso está limitado a '
                                           'algunas sucursales.'}, status=status.HTTP_403_FORBIDDEN)
            complete_supplier = Supplier.objects.filter(company=company, pk=supply_services._pk(request.data.get('supplier_id'))).first()
            if complete_supplier is None:
                return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        try:
            job = supplier_offer_import.preview(company=company, actor=request.user, upload=upload,
                                                filename=getattr(upload, 'name', '') or 'ofertas.xlsx',
                                                complete_supplier=complete_supplier)
        except (supplier_offer_import.OfferImportError, xlsx_reader.XlsxError) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        payload = supplier_offer_import.job_payload(job, include_cost=user_may_view_costs(request.user, company))
        # INV-SWITCH-01: what was decided about AI for this import. The file was read the local way either way.
        from . import inventory_ai
        from .tenancy import has_company_wide_scope

        requested = _flag(request.data.get('use_ai'))
        # Review P2: the switch is the whole company's (`settings.manage` + company-wide scope), so is
        # using it: a branch-limited buyer previews the same file, without AI.
        permitted = has_company_wide_scope(request.user, company)
        payload['ai'] = inventory_ai.import_payload(
            inventory_ai.decide(company, request.user, requested=requested, permitted=permitted), requested=requested)
        return Response(payload)


class AdminOfferImportApplyView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        company, error = _manage(request)
        if error:
            return error
        from . import supplier_offer_import
        from .models import BulkImportJob

        job = BulkImportJob.objects.filter(company=company, pk=pk,
                                           import_type=BulkImportJob.SUPPLIER_OFFERS).first()
        if job is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        # SUPPLIER-DEVICE-FLOW-02: a file that changes a commercial price needs ONE of
        # these (409 `price_confirmation_required` without; 400 with both).
        snapshot = bool((job.summary or {}).get('snapshot'))
        if snapshot:
            from .tenancy import has_company_wide_scope

            if not has_company_wide_scope(request.user, company):
                return Response({'detail': 'Una lista completa es de toda la empresa: tu acceso está limitado a '
                                           'algunas sucursales.'}, status=status.HTTP_403_FORBIDDEN)
        count = request.data.get('confirm_deactivations')
        try:
            result = supplier_offer_import.apply(
                job=job, actor=request.user, request=request,
                accept_price_changes=_flag(request.data.get('accept_price_changes')),
                keep_prices=_flag(request.data.get('keep_prices')),
                confirm_reapply=_flag(request.data.get('confirm_reapply')),
                all_messages_included=request.data.get('all_messages_included') is True,
                confirm_deactivations=count if isinstance(count, int) and not isinstance(count, bool) else None)
        except supplier_offer_import.OfferImportError as exc:
            code = (status.HTTP_409_CONFLICT if exc.code in ('price_confirmation_required', 'already_applied',
                                                              'snapshot_confirmation_required',
                                                              'deactivation_confirmation_required')
                    else status.HTTP_400_BAD_REQUEST)
            return Response({'detail': str(exc), 'code': exc.code, **exc.extra}, status=code)
        return Response(result)


class AdminSupplierProductRefView(APIView):
    """
    The supplier's own codes for the shop's variants. POST maps one (the
    preview's «elige la variante»): `{supplier_code, product_id, replace?}` →
    201 new / 200 already so; 409 `ref_conflict` when the code names another
    variant and `replace` is not true. Company-scoped: another company's
    supplier or product answers 404.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        company, error = _view(request)
        if error:
            return error
        from . import supplier_offer_import
        from .models import SupplierProductRef

        supplier = Supplier.objects.filter(company=company, pk=pk).first()
        if supplier is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        refs = (SupplierProductRef.objects.filter(company=company, supplier=supplier).select_related('product')
                .order_by('supplier_code', 'pk')[:500])
        return Response({'results': [supplier_offer_import.ref_payload(ref) for ref in refs]})

    def post(self, request, pk):
        company, error = _manage(request)
        if error:
            return error
        from . import supplier_offer_import

        supplier = Supplier.objects.filter(company=company, pk=pk).first()
        product_id = supply_services._pk(request.data.get('product_id'))
        product = Product.objects.filter(company=company, pk=product_id).first()
        if supplier is None or product is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        try:
            ref, changed = supplier_offer_import.save_product_ref(
                company=company, supplier=supplier, supplier_code=request.data.get('supplier_code'), product=product,
                actor=request.user, request=request, replace=_flag(request.data.get('replace')))
        except supply_services.SupplyError as exc:
            return _error(exc)
        return Response(supplier_offer_import.ref_payload(ref),
                        status=status.HTTP_201_CREATED if changed else status.HTTP_200_OK)
