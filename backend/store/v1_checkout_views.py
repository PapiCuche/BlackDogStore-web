"""
The NATIVE checkout — `POST /api/v1/customer/<company_slug>/checkout/`.

CUSTOMER AUDIENCE (DEC-API-001). A person buying for themselves, with a session.
Not a point of sale: an employee does not acquire the ability to sell on the
company's behalf by signing in here. That will be an internal surface with its
own permission, and this endpoint must never grow into it.

WHY THIS REQUIRES A LOGIN WHEN THE WEB CHECKOUT DOES NOT

The browser storefront takes guest orders and always has; demanding an account
there would turn away buyers who do not want one. An app is different: it
already knows who is holding it, the order needs an owner so it can appear under
"mis pedidos", and there is no session cookie to hang an anonymous basket on.

Browsing stays public either way (DEC-MOBILE-006). The login is asked for at the
moment of purchase, not at the door.
"""
import hashlib
import json
import logging

import stripe
from django.db import IntegrityError, transaction
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import checkout_services as checkout
from .models import Order
from .throttles import CheckoutThrottle
from .v1_checkout_serializers import V1CheckoutSerializer
from .v1_customer_views import V1CustomerSurfaceMixin

logger = logging.getLogger(__name__)


def payload_fingerprint(validated: dict) -> str:
    """
    A stable hash of what was actually asked for.

    Only the fields that change the COMMERCIAL outcome. Two requests that differ
    in a delivery note are the same purchase retried; two that differ in a
    quantity are not, and answering the second with the first order would tell a
    client its new basket was accepted when it was not.

    Sorted and separator-pinned so that key order and Python's default spacing
    cannot change the hash for identical content.
    """
    material = {
        'items': sorted(
            (item['product_slug'], int(item['quantity'])) for item in validated['items']
        ),
        'coupon_code': (validated.get('coupon_code') or '').upper().strip(),
        'delivery_method': validated['delivery_method'],
        'receipt_type': validated['receipt_type'],
        'document_type': validated['document_type'],
        'document_number': (validated.get('document_number') or '').strip(),
    }
    canonical = json.dumps(material, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


class V1CustomerCheckoutView(V1CustomerSurfaceMixin, APIView):
    """
    Create one pending order and a hosted Stripe session for it.

    IDEMPOTENT BY CONTRACT. A double tap, a retry after a timeout, or a request
    that succeeded on the server while the connection died must all yield the
    SAME order and the same payment session. Three layers, and each covers what
    the others cannot:

      1. a lookup before doing any work — cheap, and handles the common retry;
      2. a UNIQUE constraint on (company, user, idempotency_key) — the only
         thing that holds when two requests race, because the database decides;
      3. Stripe's own idempotency key — for the case where the order was
         created, Stripe accepted the call, and the response never arrived.
    """

    throttle_classes = [CheckoutThrottle]

    def post(self, request, company_slug=None):
        # Resolves the company AND verifies this user is a client of it. Staff
        # who are not also customers get the same 404 as a stranger.
        company = self.get_customer_company()

        serializer = V1CheckoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data

        key = validated['idempotency_key']
        fingerprint = payload_fingerprint(validated)

        existing = Order.objects.filter(
            company=company, user=request.user, idempotency_key=key,
        ).first()
        if existing is not None:
            return self._replay(existing, fingerprint)

        try:
            checkout.require_stripe_configured()

            branch = checkout.resolve_fulfillment_branch(company)
            lines = checkout.resolve_lines_from_intents(company, validated['items'])
            subtotal = checkout.validate_lines_and_subtotal(branch, lines)
            pricing = checkout.price_checkout(
                company, subtotal, validated.get('coupon_code', ''),
            )
        except checkout.CheckoutError as exc:
            return Response(exc.as_payload(), status=exc.status_code)

        # The buyer may want the receipt at a different address from the one they
        # log in with. It is contact information, NEVER ownership: the order
        # belongs to `request.user` and M4's rules decide what they can read.
        contact_email = (validated.get('contact_email') or '').strip() or request.user.email

        try:
            order = checkout.create_pending_order(
                company=company,
                branch=branch,
                lines=lines,
                pricing=pricing,
                details=checkout.CustomerDetails(
                    name=validated['customer_name'],
                    email=contact_email,
                    phone=validated['customer_phone'],
                    document_type=validated['document_type'],
                    document_number=validated['document_number'],
                    delivery_method=validated['delivery_method'],
                    receipt_type=validated['receipt_type'],
                    accepted_terms=validated['accepted_terms'],
                    accepted_warranty_policy=validated['accepted_warranty_policy'],
                    address_line=validated.get('address_line', ''),
                    city=validated.get('city', ''),
                    district=validated.get('district', ''),
                    reference=validated.get('reference', ''),
                    notes=validated.get('notes', ''),
                ),
                actor=request.user,
                # NOT optional here, unlike the browser surface. An order with no
                # owner would never appear in this person's history.
                order_user=request.user,
                cart_session_key='',
                idempotency=checkout.IdempotencyStamp(key=key, fingerprint=fingerprint),
            )
        except IntegrityError:
            # Lost the race. Another request with this key committed first, so
            # the correct answer is that one — not a second order.
            with transaction.atomic():
                winner = Order.objects.filter(
                    company=company, user=request.user, idempotency_key=key,
                ).first()
            if winner is None:  # pragma: no cover — the constraint just fired
                raise
            return self._replay(winner, fingerprint)

        line_items = checkout.build_stripe_line_items(order, pricing.discount_multiplier)
        try:
            stripe_session = checkout.create_stripe_session(
                order, line_items, customer_email=contact_email,
            )
        except stripe.StripeError as exc:
            checkout.mark_stripe_failure(order, exc)
            return Response(
                {'detail': 'No pudimos iniciar el pago. Vuelve a intentarlo.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        order.stripe_session_id = stripe_session.id
        order.save(update_fields=['stripe_session_id'])

        return Response(
            {'order_id': order.id, 'checkout_url': stripe_session.url},
            status=status.HTTP_201_CREATED,
        )

    def _replay(self, order: Order, fingerprint: str):
        """
        Answer a repeated idempotency key.

        Same key, same basket → the original order. Same key, DIFFERENT basket →
        409, because silently returning the first would tell the client its
        second request was accepted. The client's fix is a new key, and saying so
        is more useful than a mystery.

        The payment URL is fetched from Stripe rather than stored: a checkout URL
        expires, and handing back a dead one would look like a broken payment
        rather than a stale link. `checkout_url` may legitimately be null, and
        the client then reads the order's real status.
        """
        if order.idempotency_fingerprint and order.idempotency_fingerprint != fingerprint:
            return Response(
                {
                    'detail': 'Esta solicitud ya se usó para otro pedido. '
                              'Vuelve a intentarlo desde el carrito.',
                    'order_id': order.id,
                },
                status=status.HTTP_409_CONFLICT,
            )

        url = None
        if order.stripe_session_id:
            try:
                checkout.require_stripe_configured()
                url = stripe.checkout.Session.retrieve(order.stripe_session_id).url
            except (stripe.StripeError, checkout.CheckoutError):
                # Not fatal: the order exists and the client can read its status.
                # No identifier is echoed back — a Stripe session id is not the
                # customer's to hold.
                logger.warning('v1 checkout replay: could not retrieve session for order %s', order.id)

        return Response(
            {'order_id': order.id, 'checkout_url': url},
            status=status.HTTP_200_OK,
        )
