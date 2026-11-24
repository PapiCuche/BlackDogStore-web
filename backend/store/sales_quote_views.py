"""
SALES-QUOTE-01 — `/api/admin/sales/quotes/`: commercial quotes for the internal panel.

AUTHORITY
---------
    sales.quotes.view     list, detail, history, PDF (A4 / 80 mm), print on the shop's roll
    sales.quotes.manage   create, edit (a draft in place; anything offered → a new revision),
                          duplicate, mark sent, record the customer's answer
    sales.quotes.approve  approve a draft
    sales.pos.use         convert into a counter sale (it IS one: the till's rules and throttle)

A manual discount still needs `sales.discounts.apply` and another seller `sales.pos.assign_seller`,
exactly as at the till (403 otherwise).

TENANT AND BRANCH. The company is resolved on the server; a quote of another company, or of a
branch the caller does not reach, answers like one that does not exist (404). Writes are throttled
like the till's sales; every transition leaves an `AdminAuditLog` with the company.
"""
from __future__ import annotations

from django.db.models import Q
from django.http import HttpResponse
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import inventory_services, pos_payloads, pos_services
from . import sales_quote_documents as documents
from . import sales_quote_services as quotes
from .models import AdminAuditLog, Printer, Product, SalesQuote, StockUnit
from .tenancy import CrossTenantError, NoTenantError, has_capability, resolve_company_for_user, visible_branches
from .throttles import AdminSalesQuotesThrottle, AdminSalesQuoteWriteThrottle

_NOT_FOUND = 'No encontrado.'
PAGE_SIZE = 25
_MAX_SEARCH = 25


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


def _scoped(user, company):
    """The quotes of `company` in the branches `user` reaches."""
    return SalesQuote.objects.filter(company=company, branch__in=visible_branches(user, company))


def _quote(request, company, pk):
    return (_scoped(request.user, company)
            .select_related('branch', 'customer', 'seller', 'approved_by', 'decided_by', 'converted_order',
                            'created_by', 'discount_authorized_by', 'company')
            .filter(pk=pk).first())


def _error(exc: quotes.QuoteError) -> Response:
    body = {'detail': str(exc), 'code': exc.code, **exc.extra}
    return Response(body, status=exc.status)


def _person(user) -> str:
    if user is None:
        return ''
    return pos_services.seller_display_name(user)


def _mask_document(number: str) -> str:
    from .device_identity import mask

    return mask(number) if number else ''


def _actions(user, company, quote) -> dict:
    """What the caller may do NOW with this revision. The server decides again on each request."""
    can_manage = has_capability(user, company, quotes.CAP_MANAGE)
    expired = quote.is_expired
    live = quote.status in SalesQuote.LIVE_STATUSES and not expired
    return {
        'edit': can_manage and quote.status == SalesQuote.STATUS_DRAFT,
        'revise': can_manage and quote.status in SalesQuote.REVISABLE_STATUSES,
        'duplicate': can_manage,
        'approve': (has_capability(user, company, quotes.CAP_APPROVE)
                    and quote.status == SalesQuote.STATUS_DRAFT and not expired),
        'send': can_manage and quote.status == SalesQuote.STATUS_APPROVED and not expired,
        'decide': can_manage and quote.status in (SalesQuote.STATUS_APPROVED, SalesQuote.STATUS_SENT)
        and not expired,
        'convert': has_capability(user, company, quotes.CAP_POS) and live,
        'print': True,
    }


def quote_row(quote) -> dict:
    """A quote in a list: what it is, for whom, how much, in which state. The document is masked."""
    return {
        'id': quote.pk, 'number': quote.number, 'label': quote.label, 'revision': quote.revision,
        'status': quote.effective_status, 'stored_status': quote.status,
        'status_label': dict(SalesQuote.STATUS_CHOICES).get(quote.effective_status, quote.effective_status),
        'branch': quote.branch_id, 'branch_name': quote.branch.name,
        'customer': quote.customer_id, 'customer_name': quote.customer_name,
        'customer_document': _mask_document(quote.customer_document_number),
        'seller_name': quote.seller_name_snapshot,
        'currency': quote.currency, 'total': str(quote.total),
        'valid_until': quote.valid_until.isoformat() if quote.valid_until else None,
        'created_at': quote.created_at, 'updated_at': quote.updated_at,
        'converted_order': quote.converted_order_id,
    }


def quote_detail(user, company, quote) -> dict:
    items = list(quote.items.select_related('stock_unit').order_by('sort_order', 'pk'))
    return {
        **quote_row(quote),
        'origin': quote.origin,
        'source_quote': quote.source_quote_id,
        'customer_document_type': quote.customer_document_type,
        # The full number: who may read a quote may read who it is for (it is on the paper).
        'customer_document_number': quote.customer_document_number,
        'customer_phone': quote.customer_phone, 'customer_email': quote.customer_email,
        'seller': quote.seller_id,
        'conditions': quote.conditions, 'notes': quote.notes,
        'subtotal': str(quote.subtotal), 'discount_amount': str(quote.discount_amount),
        'discount_source': quote.discount_source, 'discount_type': quote.discount_type,
        'discount_value': str(quote.discount_value) if quote.discount_value is not None else None,
        'discount_reason': quote.discount_reason,
        'discount_authorized_by': _person(quote.discount_authorized_by),
        'taxable_amount': str(quote.taxable_amount), 'tax_amount': str(quote.tax_amount),
        'tax_rate': str(quote.tax_rate), 'tax_treatment': quote.tax_treatment,
        'items': [{
            'id': item.pk, 'product': item.product_id, 'description': item.description,
            'details': documents.variant_details(item.variant_snapshot), 'code': item.code,
            'quantity': item.quantity, 'unit_price': str(item.unit_price), 'line_total': str(item.line_total),
            'on_order': item.on_order, 'supply_reason': item.supply_reason, 'priced_by': item.priced_by,
            'is_serialized': item.product.is_serialized,
            'unit': ({'id': item.stock_unit_id,
                      'imei': _mask_document(item.stock_unit.imei),
                      'serial_number': _mask_document(item.stock_unit.serial_number)}
                     if item.stock_unit_id else None),
        } for item in items],
        'created_by': _person(quote.created_by), 'approved_by': _person(quote.approved_by),
        'approved_at': quote.approved_at, 'sent_at': quote.sent_at,
        'decided_by': _person(quote.decided_by), 'decided_at': quote.decided_at,
        'decision_note': quote.decision_note, 'expired_at': quote.expired_at,
        'superseded_at': quote.superseded_at, 'converted_at': quote.converted_at,
        'history': [{
            'id': row.pk, 'revision': row.revision, 'label': row.label, 'status': row.effective_status,
            'total': str(row.total), 'created_at': row.created_at,
        } for row in quotes.history(quote).filter(branch__in=visible_branches(user, company))],
        'actions': _actions(user, company, quote),
    }


# ---------------------------------------------------------------------------
# Collection, context and pickers
# ---------------------------------------------------------------------------

class AdminSalesQuoteListView(APIView):
    """GET (list, `?status=&branch=&q=&page=`) and POST (create a draft)."""

    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        if self.request.method == 'POST':
            return [AdminSalesQuoteWriteThrottle()]
        return [AdminSalesQuotesThrottle()]

    def get(self, request):
        company, error = _company(request, quotes.CAP_VIEW)
        if error:
            return error
        rows = _scoped(request.user, company).select_related('branch')
        params = request.query_params
        wanted = (params.get('status') or '').strip()
        if wanted == 'current':
            rows = rows.exclude(status=SalesQuote.STATUS_SUPERSEDED)
        elif wanted == SalesQuote.STATUS_EXPIRED:
            from django.utils import timezone

            rows = rows.filter(Q(status=SalesQuote.STATUS_EXPIRED)
                               | Q(status__in=SalesQuote.LIVE_STATUSES, valid_until__lt=timezone.localdate()))
        elif wanted in SalesQuote.LIVE_STATUSES:
            from django.utils import timezone

            rows = rows.filter(status=wanted, valid_until__gte=timezone.localdate())
        elif wanted in dict(SalesQuote.STATUS_CHOICES):
            rows = rows.filter(status=wanted)
        elif wanted:
            return Response({'detail': 'Estado no reconocido.'}, status=status.HTTP_400_BAD_REQUEST)
        else:
            rows = rows.exclude(status=SalesQuote.STATUS_SUPERSEDED)
        raw_branch = (params.get('branch') or '').strip()
        if raw_branch:
            try:
                rows = rows.filter(branch_id=int(raw_branch))
            except ValueError:
                return Response({'detail': 'Sucursal no válida.'}, status=status.HTTP_400_BAD_REQUEST)
        term = (params.get('q') or '').strip()
        if term:
            rows = rows.filter(Q(number__icontains=term) | Q(customer_name__icontains=term)
                               | Q(customer_document_number__icontains=term))
        try:
            page = max(1, int(params.get('page') or 1))
        except ValueError:
            page = 1
        count = rows.count()
        start = (page - 1) * PAGE_SIZE
        results = [quote_row(q) for q in rows.order_by('-created_at', '-pk')[start:start + PAGE_SIZE]]
        return Response({'results': results, 'count': count, 'page': page, 'page_size': PAGE_SIZE})

    def post(self, request):
        company, error = _company(request, quotes.CAP_MANAGE)
        if error:
            return error
        data = request.data if isinstance(request.data, dict) else {}
        try:
            quote = quotes.create_quote(actor=request.user, company=company, data=data, request=request)
        except quotes.QuoteError as exc:
            return _error(exc)
        return Response(quote_detail(request.user, company, _quote(request, company, quote.pk)),
                        status=status.HTTP_201_CREATED)


class AdminSalesQuoteContextView(APIView):
    """GET — what this caller may do with quotes, before the screen opens."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminSalesQuotesThrottle]

    def get(self, request):
        company, error = _company(request, quotes.CAP_VIEW)
        if error:
            return error
        user = request.user
        branches = list(visible_branches(user, company).filter(is_active=True).order_by('name'))
        may_assign = has_capability(user, company, quotes.CAP_ASSIGN_SELLER)
        can_pos = has_capability(user, company, quotes.CAP_POS)
        return Response({
            'branches': [{'id': b.pk, 'name': b.name} for b in branches],
            'default_branch': pos_payloads.default_branch_for(company, branches),
            'default_validity_days': quotes.DEFAULT_VALIDITY_DAYS,
            'max_validity_days': quotes.MAX_VALIDITY_DAYS,
            'seller': {'id': user.pk, 'name': _person(user)},
            'sellers': ([{'id': m.user_id, 'name': _person(m.user)}
                         for m in pos_services.eligible_pos_sellers(company)[:200]] if may_assign else []),
            'payment_methods': pos_payloads.context_payload(
                company, branches, default_branch=None, actor=user, can_manage_customers=False,
                can_assign_seller=False, can_apply_discount=False, can_view_commissions=False,
                sellers=[])['payment_methods'],
            'receipt_options': pos_services.receipt_options(company, branches, user) if can_pos else [],
            'can_manage': has_capability(user, company, quotes.CAP_MANAGE),
            'can_approve': has_capability(user, company, quotes.CAP_APPROVE),
            'can_convert': can_pos,
            'can_apply_discount': has_capability(user, company, quotes.CAP_DISCOUNTS),
            'can_assign_seller': may_assign,
            'can_order_over_stock': has_capability(user, company, quotes.CAP_ORDERS_MANAGE),
            'can_search_customers': has_capability(user, company, 'service.customers.view'),
            'can_use_device_picker': can_pos,
        })


def _branch_param(request, company):
    try:
        return pos_services.resolve_pos_branch(request.user, company, request.query_params.get('branch')), None
    except pos_services.PosValidationError as exc:
        return None, Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class AdminSalesQuoteProductSearchView(APIView):
    """GET `?q=&branch=` — products to quote, with this branch's stock and price (the till's payload)."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminSalesQuotesThrottle]

    def get(self, request):
        company, error = _company(request, quotes.CAP_MANAGE)
        if error:
            return error
        branch, error = _branch_param(request, company)
        if error:
            return error
        term = (request.query_params.get('q') or '').strip()
        if len(term) < 2:
            return Response({'results': []})
        products = (
            Product.objects.filter(company=company, is_active=True)
            .exclude(Product.pending_draft_q())
            .filter(Q(name__icontains=term) | Q(barcodes__code__istartswith=term))
            .distinct().prefetch_related('barcodes')[:_MAX_SEARCH]
        )
        return Response({'results': [pos_payloads.product_payload(p, branch) for p in products]})


class AdminSalesQuoteUnitsView(APIView):
    """GET `?product=&branch=` — the used devices of a product on this shelf a quote may name (masked)."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminSalesQuotesThrottle]

    def get(self, request):
        company, error = _company(request, quotes.CAP_MANAGE)
        if error:
            return error
        branch, error = _branch_param(request, company)
        if error:
            return error
        try:
            product = Product.objects.filter(company=company, pk=int(request.query_params.get('product'))).first()
        except (TypeError, ValueError):
            product = None
        if product is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        units = (StockUnit.objects.filter(company=company, product=product, branch=branch,
                                          status=StockUnit.Status.AVAILABLE, sale_price__gt=0)
                 .select_related('cosmetic_grade').order_by('pk')[:100])
        return Response({'results': [{
            'id': unit.pk,
            'imei': _mask_document(unit.imei), 'serial_number': _mask_document(unit.serial_number),
            'sale_price': str(unit.sale_price),
            'grade': unit.cosmetic_grade.label if unit.cosmetic_grade_id else '',
            'battery_health': getattr(unit, 'battery_health', None),
            'ready': quotes.used_unit_till_refusal(unit) == '',
        } for unit in units]})


# ---------------------------------------------------------------------------
# One quote
# ---------------------------------------------------------------------------

class AdminSalesQuoteDetailView(APIView):
    """GET (detail with revisions) and PATCH (edit: a draft in place, anything offered → new revision)."""

    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        if self.request.method == 'PATCH':
            return [AdminSalesQuoteWriteThrottle()]
        return [AdminSalesQuotesThrottle()]

    def get(self, request, pk):
        company, error = _company(request, quotes.CAP_VIEW)
        if error:
            return error
        quote = _quote(request, company, pk)
        if quote is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        return Response(quote_detail(request.user, company, quote))

    def patch(self, request, pk):
        company, error = _company(request, quotes.CAP_MANAGE)
        if error:
            return error
        quote = _quote(request, company, pk)
        if quote is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        data = request.data if isinstance(request.data, dict) else {}
        try:
            updated = quotes.update_quote(actor=request.user, company=company, quote=quote, data=data,
                                          request=request)
        except quotes.QuoteError as exc:
            return _error(exc)
        return Response(quote_detail(request.user, company, _quote(request, company, updated.pk)),
                        status=status.HTTP_200_OK if updated.pk == quote.pk else status.HTTP_201_CREATED)


class _QuoteAction(APIView):
    """POST /<pk>/<action>/ — one transition. Subclasses name the capability and call the service."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminSalesQuoteWriteThrottle]
    capability = quotes.CAP_MANAGE
    created = False

    def act(self, request, company, quote, data):  # pragma: no cover - abstract
        raise NotImplementedError

    def post(self, request, pk):
        company, error = _company(request, self.capability)
        if error:
            return error
        quote = _quote(request, company, pk)
        if quote is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        data = request.data if isinstance(request.data, dict) else {}
        try:
            result = self.act(request, company, quote, data)
        except quotes.QuoteError as exc:
            return _error(exc)
        return Response(quote_detail(request.user, company, _quote(request, company, result.pk)),
                        status=status.HTTP_201_CREATED if self.created else status.HTTP_200_OK)


class AdminSalesQuoteDuplicateView(_QuoteAction):
    created = True

    def act(self, request, company, quote, data):
        return quotes.duplicate_quote(actor=request.user, company=company, quote=quote, request=request)


class AdminSalesQuoteReviseView(_QuoteAction):
    created = True

    def act(self, request, company, quote, data):
        return quotes.revise_quote(actor=request.user, company=company, quote=quote, data=data or None,
                                   request=request)


class AdminSalesQuoteApproveView(_QuoteAction):
    capability = quotes.CAP_APPROVE

    def act(self, request, company, quote, data):
        return quotes.approve_quote(actor=request.user, company=company, quote=quote, request=request)


class AdminSalesQuoteSendView(_QuoteAction):
    def act(self, request, company, quote, data):
        return quotes.send_quote(actor=request.user, company=company, quote=quote, request=request)


class AdminSalesQuoteAcceptView(_QuoteAction):
    def act(self, request, company, quote, data):
        return quotes.decide_quote(actor=request.user, company=company, quote=quote, accepted=True,
                                   note=data.get('note', ''), request=request)


class AdminSalesQuoteRejectView(_QuoteAction):
    def act(self, request, company, quote, data):
        return quotes.decide_quote(actor=request.user, company=company, quote=quote, accepted=False,
                                   note=data.get('note', ''), request=request)


class AdminSalesQuoteConvertView(APIView):
    """
    POST /<pk>/convert/ — the quote becomes a counter sale. Header `Idempotency-Key` (or body
    `idempotency_key`); body as the till's sale (payment_method, amount_received, terms_confirmed,
    receipt_type, …) plus `units` `{item id: [{id, method}]}`, `price_decision` and `expected_total`.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminSalesQuoteWriteThrottle]

    def post(self, request, pk):
        company, error = _company(request, quotes.CAP_VIEW)
        if error:
            return error
        quote = _quote(request, company, pk)
        if quote is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        data = request.data if isinstance(request.data, dict) else {}
        key = request.headers.get('Idempotency-Key') or data.get('idempotency_key')
        may_see_commission = has_capability(request.user, company, 'sales.commissions.view')
        try:
            order, created = quotes.convert_quote(actor=request.user, company=company, quote=quote, data=data,
                                                  idempotency_key=key, request=request)
        except quotes.QuotePriceChanged as exc:
            return Response({
                'detail': str(exc), 'code': exc.code,
                'quoted_total': str(exc.expected), 'current_total': str(exc.current),
                'can_keep_quoted': exc.can_keep_quoted,
                'preview': pos_payloads.preview_payload(exc.priced, may_see_commission=may_see_commission),
            }, status=status.HTTP_409_CONFLICT)
        except quotes.QuoteError as exc:
            return _error(exc)
        except inventory_services.InsufficientStockError as exc:
            # The whole sale rolled back: nothing charged, nothing moved, the quote untouched.
            return Response({'detail': str(exc), 'code': 'insufficient_stock'}, status=status.HTTP_409_CONFLICT)
        return Response({
            'quote': quote_detail(request.user, company, _quote(request, company, quote.pk)),
            'sale': pos_payloads.sale_payload(order, order.fulfillment_branch, created=created,
                                              may_see_commission=may_see_commission),
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class AdminSalesQuotePdfView(APIView):
    """GET /<pk>/pdf/?format=a4|ticket80 — the paper, in memory. Downloading is reading: audited, no state."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminSalesQuotesThrottle]

    def perform_content_negotiation(self, request, force=False):
        # `?format=a4|ticket80` names the PAPER, not a DRF renderer: never a 404 for an unknown renderer.
        return super().perform_content_negotiation(request, force=True)

    def get(self, request, pk):
        company, error = _company(request, quotes.CAP_VIEW)
        if error:
            return error
        quote = _quote(request, company, pk)
        if quote is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        kind = (request.query_params.get('format') or 'a4').strip()
        if kind not in ('a4', 'ticket80'):
            return Response({'detail': 'Formato no reconocido: a4 o ticket80.'}, status=status.HTTP_400_BAD_REQUEST)
        pdf = documents.render_a4_pdf(quote) if kind == 'a4' else documents.render_ticket_pdf(quote)
        AdminAuditLog.log(actor=request.user, action='sales_quote_pdf_downloaded', target_type='sales_quote',
                          target_id=quote.pk, metadata={'company_id': company.pk, 'format': kind,
                                                        'number': quote.label},
                          request=request, company=company)
        disposition = 'inline' if request.query_params.get('inline') == '1' else 'attachment'
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'{disposition}; filename="{documents.filename(quote, kind)}"'
        response['Cache-Control'] = 'no-store'
        return response


class AdminSalesQuotePrintView(APIView):
    """
    POST /<pk>/print/ — the 80 mm ticket on the shop's thermal printer, through the print queue.
    Body `{printer?}`; header `Idempotency-Key` (a double click prints once).
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminSalesQuoteWriteThrottle]

    def post(self, request, pk):
        from .printing import services as printing

        company, error = _company(request, quotes.CAP_VIEW)
        if error:
            return error
        quote = _quote(request, company, pk)
        if quote is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        data = request.data if isinstance(request.data, dict) else {}
        printers = Printer.objects.filter(company=company, branch=quote.branch, is_active=True,
                                          role=Printer.Role.TICKET)
        if data.get('printer') not in (None, ''):
            try:
                printer = printers.filter(pk=int(data['printer'])).first()
            except (TypeError, ValueError):
                printer = None
        else:
            printer = printers.filter(auto_print=True).first() or printers.first()
        if printer is None:
            return Response({'detail': 'Este local no tiene una impresora de tickets disponible.'},
                            status=status.HTTP_400_BAD_REQUEST)
        key = (request.headers.get('Idempotency-Key') or '').strip()
        if not key:
            return Response({'detail': 'Falta la cabecera Idempotency-Key.'}, status=status.HTTP_400_BAD_REQUEST)
        from .models import PrintJob

        existed = PrintJob.objects.filter(company=company, idempotency_key=f'quote:{key}'[:120]).exists()
        try:
            job = printing.enqueue_quote_ticket(quote=quote, printer=printer, key=key, actor=request.user)
        except printing.PrintConflict as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except printing.PrintError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if not existed:
            AdminAuditLog.log(actor=request.user, action='sales_quote_print_requested', target_type='print_job',
                              target_id=job.pk, metadata={'company_id': company.pk, 'quote_id': quote.pk,
                                                          'printer_id': printer.pk},
                              request=request, company=company)
        return Response({'id': job.pk, 'status': job.status, 'printer': printer.pk, 'printer_name': printer.name,
                         'sales_quote': quote.pk},
                        status=status.HTTP_200_OK if existed else status.HTTP_201_CREATED)
