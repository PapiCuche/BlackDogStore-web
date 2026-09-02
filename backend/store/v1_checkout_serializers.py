"""
Input for the NATIVE checkout — `/api/v1/customer/<slug>/checkout/`.

WHY NOT `CheckoutInputSerializer`

The web serializer requires `session_key`, because a browser's basket lives on
the server keyed by that string. An app's basket lives on the device, so it
sends the items themselves. Making the app invent a session key to satisfy a
field it has no use for would be pretending to be a browser — and would tie a
native order to a session nobody will ever look up.

The validation RULES are shared where they are genuinely the same: the phone
format, the document rules and the conditional address requirements all come
from the same validators the web uses, so a Peruvian phone number is judged
identically on both.

⚠️  NO PRICE, NO TOTAL, NO DISCOUNT, NO STOCK, NO COMPANY, NO BRANCH.
Those fields are not merely ignored — they are absent from the contract, so a
client that sends one gets a 400 rather than the quiet impression it was
honoured. The server computes every figure from `Product.price` and
`Coupon.discount_percent` at checkout time.
"""
import re

from rest_framework import serializers

from .models import Order
# The SAME pattern the web checkout judges phones by. Imported rather than
# copied: a second regex would drift, and the drift would be a number the
# website accepts and the app rejects.
from .serializers import _PERU_PHONE_RE

# Never accepted, and rejected LOUDLY rather than dropped. A client sending a
# price is a client that believes it sets prices, and silence would let it keep
# believing that until the day the amounts disagreed.
FORBIDDEN_FIELDS = (
    'price', 'unit_price', 'subtotal', 'total', 'discount', 'discount_amount',
    'discount_percent', 'stock', 'inventory', 'company', 'company_id', 'company_slug',
    'branch', 'branch_id', 'fulfillment_branch', 'status', 'payment_status',
    'fulfillment_status', 'paid', 'user', 'user_id', 'customer', 'customer_id',
    'payment_reference', 'transaction_id', 'session_key',
)

MAX_LINES = 50
MAX_QUANTITY_PER_LINE = 99


class V1CheckoutItemSerializer(serializers.Serializer):
    """
    One basket line, as INTENT.

    A slug, not an id. An id is a small integer that exists in every tenant, so
    a leaked one is a plausible guess elsewhere; a slug resolved inside the
    caller's company either exists there or does not exist at all.
    """

    product_slug = serializers.CharField(max_length=255, trim_whitespace=True)
    quantity = serializers.IntegerField(min_value=1, max_value=MAX_QUANTITY_PER_LINE)


class V1CheckoutSerializer(serializers.Serializer):
    items = V1CheckoutItemSerializer(many=True, allow_empty=False, max_length=MAX_LINES)

    customer_name = serializers.CharField(max_length=255, min_length=2, trim_whitespace=True)
    customer_phone = serializers.CharField(max_length=30, trim_whitespace=True)
    document_type = serializers.ChoiceField(choices=Order.DocumentType.choices)
    document_number = serializers.CharField(max_length=20, trim_whitespace=True)

    delivery_method = serializers.ChoiceField(choices=Order.DeliveryMethod.choices)
    address_line = serializers.CharField(
        max_length=300, required=False, allow_blank=True, trim_whitespace=True,
    )
    city = serializers.CharField(
        max_length=100, required=False, allow_blank=True, trim_whitespace=True,
    )
    district = serializers.CharField(
        max_length=100, required=False, allow_blank=True, trim_whitespace=True,
    )
    reference = serializers.CharField(
        max_length=250, required=False, allow_blank=True, trim_whitespace=True,
    )
    notes = serializers.CharField(
        max_length=500, required=False, allow_blank=True, trim_whitespace=True,
    )

    receipt_type = serializers.ChoiceField(choices=Order.ReceiptType.choices)
    accepted_terms = serializers.BooleanField()
    accepted_warranty_policy = serializers.BooleanField()

    coupon_code = serializers.CharField(
        max_length=50, required=False, allow_blank=True, trim_whitespace=True,
    )

    # The client's request key. Opaque to the server, which only needs it to be
    # stable across retries of the same intent and different between intents.
    idempotency_key = serializers.CharField(max_length=100, min_length=8, trim_whitespace=True)

    # Optional: a buyer may want the receipt at a different address from the one
    # they log in with. NOT ownership — the order belongs to `request.user`.
    contact_email = serializers.EmailField(max_length=254, required=False, allow_blank=True)

    def validate_customer_phone(self, value):
        if not _PERU_PHONE_RE.match(re.sub(r'\s', '', value)):
            raise serializers.ValidationError(
                'Teléfono inválido. Ejemplos aceptados: 987654321, +51987654321, '
                '+51 987 654 321.'
            )
        return value

    def validate_accepted_terms(self, value):
        if value is not True:
            raise serializers.ValidationError('Debes aceptar los términos y condiciones.')
        return value

    def validate_accepted_warranty_policy(self, value):
        if value is not True:
            raise serializers.ValidationError('Debes aceptar la política de garantía.')
        return value

    def to_internal_value(self, data):
        if isinstance(data, dict):
            sent = [name for name in FORBIDDEN_FIELDS if name in data]
            if sent:
                raise serializers.ValidationError({
                    field: 'Este campo lo determina el servidor y no se acepta del cliente.'
                    for field in sent
                })
        return super().to_internal_value(data)

    def validate(self, attrs):
        errors = {}

        # A shipped order needs somewhere to ship to. Collecting in-store pickup
        # addresses would be asking for data nobody uses.
        if attrs['delivery_method'] != Order.DeliveryMethod.PICKUP_STORE:
            for field in ('address_line', 'city', 'district'):
                if not (attrs.get(field) or '').strip():
                    errors[field] = 'Requerido para envíos a domicilio.'

        # A factura is issued to a business, which in Peru means a RUC.
        if attrs['receipt_type'] == Order.ReceiptType.FACTURA:
            if attrs['document_type'] != Order.DocumentType.RUC:
                errors['document_type'] = 'Una factura requiere RUC.'

        number = (attrs.get('document_number') or '').strip()
        expected = {Order.DocumentType.DNI: 8, Order.DocumentType.RUC: 11}
        length = expected.get(attrs['document_type'])
        if length is not None:
            if not number.isdigit() or len(number) != length:
                errors['document_number'] = f'Debe tener {length} dígitos.'
        elif len(number) < 6:
            errors['document_number'] = 'Documento inválido.'

        if errors:
            raise serializers.ValidationError(errors)
        return attrs
