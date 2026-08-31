"""
Serializers for the CUSTOMER surface — `/api/v1/customer/`.

WHY A THIRD SET OF SERIALIZERS

There are now three audiences, and they are not the same reader:

  PUBLIC    — anyone. A shop window.
  CUSTOMER  — a client of ONE company, reading their OWN records.
  INTERNAL  — staff of a company, reading the company's records under a
              capability. (Not implemented yet — DEC-API-001.)

The legacy `OrderSerializer` belongs to the web frontend and lists
`stripe_session_id` among its fields. That is a payment-processor identifier,
and it has no business in a mobile response no matter who is asking. Reusing it
"because it is close enough" is exactly how internal data reaches a customer
screen.

So the customer contract is declared here, as an ALLOWLIST. Adding a field to
the model, or to the legacy serializer, does not add it to this response.
"""
from rest_framework import serializers

from .models import Order, OrderItem


class V1CustomerOrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True, default='')
    product_slug = serializers.CharField(source='product.slug', read_only=True, default='')
    image_url = serializers.CharField(source='product.image_url', read_only=True, default='')

    class Meta:
        model = OrderItem
        # `price` is the price PAID, frozen on the line. Not today's price, which
        # is what a customer looking at an old receipt would find confusing and
        # what a refund conversation would have to argue about.
        fields = ['id', 'product_name', 'product_slug', 'image_url', 'quantity', 'price']


class V1CustomerOrderSerializer(serializers.ModelSerializer):
    items = V1CustomerOrderItemSerializer(many=True, read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    fulfillment_status_label = serializers.CharField(
        source='get_fulfillment_status_display', read_only=True,
    )
    delivery_method_label = serializers.CharField(
        source='get_delivery_method_display', read_only=True,
    )

    class Meta:
        model = Order
        fields = [
            'id',
            'status',
            'status_label',
            # BR-003, closed for v1. The column has existed since Phase 2C; the
            # legacy serializer never exposed it, so a customer could see that a
            # payment succeeded and nothing about whether the goods had moved.
            'fulfillment_status',
            'fulfillment_status_label',
            'total',
            'discount_amount',
            'coupon_code',
            'delivery_method',
            'delivery_method_label',
            'created_at',
            'paid_at',
            'items',
        ]
        read_only_fields = fields

    # ── What is deliberately ABSENT, and why ────────────────────────────────
    #
    # stripe_session_id, stripe_payment_intent_id
    #     Payment-processor identifiers. Nothing a customer can do with one, and
    #     everything an attacker holding a leaked order could.
    # payment_error, email_send_error
    #     Operational diagnostics. "SMTP timeout" is not an order status.
    # cart_session_key
    #     A session handle. Handing it back is handing back a session.
    # confirmation_email_sent_at, internal_notification_sent_at
    #     Internal delivery bookkeeping about our own systems.
    # user, customer, company, fulfillment_branch, company_snapshot
    #     Internal identifiers and structure. A customer needs their order, not
    #     the shape of the business behind it.
    # customer_name, customer_email, customer_phone, document_*, address_*
    #     The buyer already knows these — they typed them. Echoing personal data
    #     back over the wire widens what a leaked response is worth without
    #     telling the reader anything new.
    # notes
    #     Ambiguous by name: it is the buyer's delivery note, but nothing stops
    #     an operator using it as an internal remark. Excluded until the domain
    #     says which it is.
