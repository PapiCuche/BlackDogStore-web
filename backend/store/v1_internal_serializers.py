"""
Serializers for the INTERNAL surface — `/api/v1/internal/`.

A FOURTH SET, AND THE SEPARATION IS THE WHOLE POINT.

  public    a shop window            `v1_serializers`
  customer  your own records         `v1_customer_serializers`
  internal  the COMPANY's records    this file
  admin web the browser panel        `serializers` (legacy, unchanged)

The customer contract is deliberately narrow. If the same type were widened to
carry what staff need — the buyer's phone, their document, the delivery address
— then one day a customer screen would render a field it was never meant to
have, because nothing in the type system would object.

So internal gets its own allowlist, and it is still an ALLOWLIST: adding a
column to `Order` does not add it here.
"""
from rest_framework import serializers

from .models import Order, OrderItem


class V1InternalOrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True, default='')
    product_slug = serializers.CharField(source='product.slug', read_only=True, default='')

    class Meta:
        model = OrderItem
        fields = ['id', 'product_name', 'product_slug', 'quantity', 'price']


class V1InternalOrderListSerializer(serializers.ModelSerializer):
    """The row in a list. Enough to triage, not enough to be a data export."""

    status_label = serializers.CharField(source='get_status_display', read_only=True)
    fulfillment_status_label = serializers.CharField(
        source='get_fulfillment_status_display', read_only=True,
    )
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            'id',
            'customer_name',
            'status', 'status_label',
            'fulfillment_status', 'fulfillment_status_label',
            'total',
            'created_at', 'paid_at',
            'item_count',
        ]
        read_only_fields = fields

    def get_item_count(self, obj) -> int:
        return sum(item.quantity for item in obj.items.all())


class V1InternalOrderDetailSerializer(serializers.ModelSerializer):
    """
    One order, as the people who have to fulfil it need to see it.

    This DOES carry the buyer's contact and delivery details, unlike the
    customer serializer — someone has to phone them and someone has to ship it.
    That is exactly why it is a separate type: the need is real for staff and
    absent for the buyer, who typed it all in themselves.
    """

    items = V1InternalOrderItemSerializer(many=True, read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    fulfillment_status_label = serializers.CharField(
        source='get_fulfillment_status_display', read_only=True,
    )
    delivery_method_label = serializers.CharField(
        source='get_delivery_method_display', read_only=True,
    )
    receipt_type_label = serializers.CharField(source='get_receipt_type_display', read_only=True)
    document_type_label = serializers.CharField(source='get_document_type_display', read_only=True)
    fulfillment_branch_name = serializers.CharField(
        source='fulfillment_branch.name', read_only=True, default='',
    )

    class Meta:
        model = Order
        fields = [
            'id',
            # Payment and operations, kept as the two independent facts they are.
            'status', 'status_label',
            'fulfillment_status', 'fulfillment_status_label',
            'total', 'discount_amount', 'coupon_code',
            'created_at', 'paid_at',
            # Who bought it — needed to call them about their order.
            'customer_name', 'customer_email', 'customer_phone',
            'document_type', 'document_type_label', 'document_number',
            'receipt_type', 'receipt_type_label',
            # Where it goes.
            'delivery_method', 'delivery_method_label',
            'address_line', 'city', 'district', 'reference',
            'notes',
            'fulfillment_branch_name',
            'items',
        ]
        read_only_fields = fields

    # ── Absent on purpose ───────────────────────────────────────────────────
    #
    # stripe_session_id, stripe_payment_intent_id
    #     Payment-processor handles. Nothing internal staff can do with one, and
    #     everything an attacker with a leaked response could.
    # payment_error, email_send_error
    #     Raw operational strings. They belong in logs and monitoring, not in a
    #     mobile response — a provider message can carry a key or an internal
    #     hostname.
    # cart_session_key
    #     A session handle.
    # company_snapshot, company, user, customer
    #     Internal identifiers and structure.
    # confirmation_email_sent_at, internal_notification_sent_at
    #     Bookkeeping about our own systems, not about the order.


class V1InternalFulfillmentSerializer(serializers.Serializer):
    """
    A fulfilment change.

    `note` is recorded on the audit entry, not on the order: it explains why
    someone moved a state, and that belongs to the trail rather than to the
    sale.
    """

    fulfillment_status = serializers.ChoiceField(choices=Order.FulfillmentStatus.choices)
    note = serializers.CharField(required=False, allow_blank=True, max_length=500, default='')
