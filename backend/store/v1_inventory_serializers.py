"""
Serializers for INTERNAL inventory — `/api/v1/internal/<slug>/inventory/`.

An allowlist, like every other v1 contract. Adding a column to `BranchStock` or
`StockMovement` does not add it to a response.

WHAT IS DELIBERATELY ABSENT: cost, margin and supplier. Not because they are
secret from warehouse staff, but because **the system has no cost model at all**
— there is no purchase price anywhere in `Product` or `BranchStock`. A field
claiming to be a cost would be a number with a false name on it, which is worse
than no number. The same reasoning the service layer already applies to
`inventory_value`, which it labels `sale_price` rather than pretending to be
capital invested.
"""
from rest_framework import serializers

from .models import Branch, BranchStock, StockMovement


class V1BranchSerializer(serializers.ModelSerializer):
    """A branch the caller may actually operate. Never the company's full list."""

    class Meta:
        model = Branch
        fields = ['id', 'name']


class V1StockRowSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True, default='')
    product_slug = serializers.CharField(source='product.slug', read_only=True, default='')
    branch_id = serializers.IntegerField(read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True, default='')
    is_low_stock = serializers.SerializerMethodField()
    is_out_of_stock = serializers.SerializerMethodField()

    class Meta:
        model = BranchStock
        fields = [
            'id',
            'product_name', 'product_slug',
            'branch_id', 'branch_name',
            'quantity', 'minimum_stock',
            'is_low_stock', 'is_out_of_stock',
            'updated_at',
        ]
        read_only_fields = fields

    def get_is_out_of_stock(self, obj) -> bool:
        return obj.quantity <= 0

    def get_is_low_stock(self, obj) -> bool:
        """
        Mirrors `inventory_services.low_stock_filter` exactly.

        The per-row minimum wins wherever it is configured; the global threshold
        is the fallback. Restating the rule here rather than importing a queryset
        expression is unavoidable — a `Q` object cannot be evaluated per
        instance — so the tests assert the two agree.
        """
        threshold = self.context.get('low_stock_threshold', 5)
        if obj.quantity <= 0:
            return False
        if obj.minimum_stock > 0:
            return obj.quantity <= obj.minimum_stock
        return obj.quantity <= threshold


class V1StockMovementSerializer(serializers.ModelSerializer):
    """
    One Kardex line.

    Carries `stock_before` and `stock_after` because a movement without them is
    a claim nobody can check: the whole value of a Kardex is that each line
    reconciles with the one before it.
    """

    product_name = serializers.CharField(source='product.name', read_only=True, default='')
    product_slug = serializers.CharField(source='product.slug', read_only=True, default='')
    branch_name = serializers.CharField(source='branch.name', read_only=True, default='')
    movement_type_label = serializers.CharField(
        source='get_movement_type_display', read_only=True,
    )
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = StockMovement
        fields = [
            'id',
            'product_name', 'product_slug',
            'branch_id', 'branch_name',
            'movement_type', 'movement_type_label',
            'quantity', 'stock_before', 'stock_after',
            'reason',
            'reference_type',
            'actor_name',
            'created_at',
        ]
        read_only_fields = fields

    def get_actor_name(self, obj) -> str:
        """
        Who did it, by display name — never the account's email or id.

        Traceability needs a person, not a credential. An empty string for a
        system-generated movement is honest: the payment pipeline has no actor.
        """
        actor = obj.actor
        if actor is None:
            return ''
        full = f'{actor.first_name} {actor.last_name}'.strip()
        return full or actor.username

    # Absent on purpose: `metadata` (free-form and unaudited), `order`,
    # `transfer`, `inventory_count` and `reference_id` — internal identifiers
    # that would let a client walk to records this surface does not serve.


class V1StockAdjustmentSerializer(serializers.Serializer):
    """
    A request to MOVE stock, never to set it.

    There is deliberately no `quantity_after` or `new_quantity` field. Letting a
    client state the final figure would make the app the authority on a number
    two people may be changing at once; a delta is a request the server can
    apply safely under a lock, and it is also what the Kardex needs to record.
    """

    product_slug = serializers.CharField(max_length=255, trim_whitespace=True)
    branch_id = serializers.IntegerField()
    movement_type = serializers.CharField(max_length=32, trim_whitespace=True)
    quantity = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(max_length=255, trim_whitespace=True)

    def validate_movement_type(self, value):
        # Only the types the domain accepts by hand. `sale_exit` and the
        # transfer pair are refused by the service too — a hand-written
        # `transfer_out` with no matching `transfer_in` would be stock that
        # simply vanished from the company.
        if value not in StockMovement.MANUAL_TYPES:
            raise serializers.ValidationError(
                'Ese tipo de movimiento no puede registrarse manualmente.',
            )
        return value

    def validate_reason(self, value):
        if not value.strip():
            raise serializers.ValidationError('El motivo es obligatorio.')
        return value.strip()
