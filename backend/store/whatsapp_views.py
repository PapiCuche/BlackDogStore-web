"""
WHATSAPP-NOTIFY — the HTTP surface.

Three audiences that share nothing:

  * the PROVIDER, calling the webhook with a signature;
  * a tenant ADMINISTRATOR, reading and changing the non-secret settings;
  * workshop STAFF, recording consent and retrying a message from an order.
"""
from __future__ import annotations

import json

from django.http import HttpResponse
from rest_framework import permissions, status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from . import whatsapp_services as wa
from .models import Customer, Notification, NotificationDelivery
from .tenancy import resolve_public_storefront_company
from .throttles import AdminOrdersThrottle, AdminOrderStatusChangeThrottle, WhatsAppWebhookThrottle
from .v1_internal_views import V1InternalSurfaceMixin
from .v1_service_views import CAP_ORDERS_MANAGE, V1ServiceSurfaceMixin

CAP_CUSTOMERS_MANAGE = 'service.customers.manage'
CAP_SETTINGS_VIEW = 'settings.view'
CAP_SETTINGS_MANAGE = 'settings.manage'


class WhatsAppWebhookView(APIView):
    """
    What the provider calls. No session: the SIGNATURE is the credential.

    Every refusal is the same bare 403 — unknown company, company without
    WhatsApp, missing secret, bad signature — so the endpoint cannot be used to
    learn which companies exist or which have the channel configured.

    A valid report is always answered 200, including one about a message this
    company does not know: the provider retries anything else for days.
    """

    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [WhatsAppWebhookThrottle]

    def _config(self, company_slug):
        company = resolve_public_storefront_company(company_slug)
        config = wa.config_for(company) if company is not None else None
        return company, config

    def get(self, request, company_slug=None):
        _company, config = self._config(company_slug)
        params = request.query_params
        if (
            config is None
            or params.get('hub.mode') != 'subscribe'
            or not wa.verify_token_matches(config, params.get('hub.verify_token'))
        ):
            return HttpResponse(status=403)
        # Echoed as plain text and nothing else, which is what the handshake is.
        return HttpResponse(str(params.get('hub.challenge') or ''), content_type='text/plain')

    def post(self, request, company_slug=None):
        company, config = self._config(company_slug)
        body = request.body
        if config is None or not wa.signature_is_valid(
            config, body, request.headers.get('X-Hub-Signature-256', ''),
        ):
            return HttpResponse(status=403)
        try:
            payload = json.loads(body or b'{}')
        except ValueError:
            return HttpResponse(status=400)
        if isinstance(payload, dict):
            wa.process_webhook(company, payload)
        return HttpResponse(status=200)


class WhatsAppSettingsView(V1InternalSurfaceMixin, APIView):
    """GET — what is configured and what is missing. PATCH — the tenant's part of it."""

    throttle_classes = [AdminOrdersThrottle]

    def get(self, request, company_slug=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_SETTINGS_VIEW)
        return Response(wa.settings_payload(company))

    def patch(self, request, company_slug=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_SETTINGS_MANAGE)
        data = request.data if isinstance(request.data, dict) else {}
        try:
            return Response(wa.update_settings(
                company, actor=request.user, data=data, request=request,
            ))
        except wa.WhatsAppSettingsError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


def consent_payload(customer) -> dict:
    return {
        'whatsapp_opt_in': wa.has_consent(customer),
        'whatsapp_opt_in_at': customer.whatsapp_opt_in_at,
        'whatsapp_opt_in_source': customer.whatsapp_opt_in_source or None,
        'whatsapp_opt_out_at': customer.whatsapp_opt_out_at,
    }


class WhatsAppConsentView(V1ServiceSurfaceMixin, APIView):
    """
    POST — the customer agreed to be messaged, or no longer does.

    Recorded by the person they told, with the date. A phone number on file is
    not consent, and this is the only place the answer is written from the
    panel.
    """

    http_method_names = ['post']
    throttle_classes = [AdminOrderStatusChangeThrottle]

    def post(self, request, company_slug=None, pk=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_CUSTOMERS_MANAGE)
        customer = Customer.objects.filter(company=company, pk=pk).first()
        if customer is None:
            raise NotFound('No encontrado.')
        opt_in = request.data.get('opt_in')
        if not isinstance(opt_in, bool):
            return Response(
                {'detail': 'Indica si el cliente acepta recibir avisos por WhatsApp.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        updated = wa.set_consent(
            customer, opt_in=opt_in, source='counter', actor=request.user, request=request,
        )
        return Response(consent_payload(updated))


class WhatsAppRetryView(V1ServiceSurfaceMixin, APIView):
    """POST — try again a WhatsApp message of THIS order that did not go out."""

    http_method_names = ['post']
    throttle_classes = [AdminOrderStatusChangeThrottle]

    def post(self, request, company_slug=None, pk=None, notification_id=None):
        from .v1_service_serializers import customer_notification_payload

        company = self.get_internal_company()
        self.require_capability(company, CAP_ORDERS_MANAGE)
        order = self.get_order(company, pk)
        delivery = NotificationDelivery.objects.filter(
            channel=NotificationDelivery.Channel.WHATSAPP,
            notification_id=notification_id,
            notification__company=company,
            notification__audience=Notification.Audience.CUSTOMER,
            notification__target_type='repair_order',
            notification__target_id=order.pk,
        ).first()
        if delivery is None:
            raise NotFound('No encontrado.')
        wa.retry(delivery, actor=request.user, request=request)
        note = Notification.objects.select_related('event').prefetch_related('deliveries').get(
            pk=notification_id,
        )
        return Response(customer_notification_payload(note))
