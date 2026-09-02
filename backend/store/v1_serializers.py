"""
Serializers for the versioned public API — `/api/v1/`.

WHY THESE ARE SEPARATE FROM `serializers.py`

The legacy serializers belong to the web frontend. That is not an insult: it is
their contract, and it is allowed to change whenever the web team needs it to.
A mobile app ships through review queues and lives on devices for months, so it
cannot share a shape that is free to move underneath it.

So `/api/v1/` declares its OWN field lists. The read logic they wrap is shared
where it is genuinely the same computation, but the SHAPE is pinned here. A
field added to `ProductSerializer` for the web does not silently appear in the
mobile contract, and a field removed there fails loudly in these tests instead
of quietly emptying a screen in a shipped build.

WHAT IS DELIBERATELY ABSENT

Nothing internal. No cost, no margin, no supplier, no branch layout, no company
tax identity, no per-branch stock breakdown, no gateway identifiers. This is a
shop window: the only numbers on it are the ones a shopper is meant to read.
"""
from rest_framework import serializers

from .models import Category, Product


class V1CategorySerializer(serializers.ModelSerializer):
    """A category as the public catalogue exposes it."""

    class Meta:
        model = Category
        fields = ['id', 'name', 'slug']


class V1ProductSerializer(serializers.ModelSerializer):
    category = V1CategorySerializer(read_only=True)
    inventory = serializers.SerializerMethodField()
    average_rating = serializers.SerializerMethodField()
    review_count = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'slug', 'description', 'price',
            'inventory', 'category', 'image_url', 'average_rating', 'review_count',
        ]

    def get_inventory(self, obj):
        """
        SELLABLE units — what the fulfillment branch can actually ship.

        Read straight off the `available_stock` annotation, because every v1
        queryset comes from `company_storefront_products()` and is therefore
        always annotated. The legacy serializer carries fallbacks for callers
        that reach it without a storefront; v1 has no such caller, and inventing
        one here would mean a number that no longer matches what checkout can
        deliver.

        Zero when the annotation is missing rather than a company-wide total: an
        empty shelf is honest, a full one that cannot ship is not.
        """
        annotated = getattr(obj, 'available_stock', None)
        return annotated if annotated is not None else 0

    def get_average_rating(self, obj):
        reviews = obj.reviews.all()
        if not reviews:
            return None
        return round(sum(r.rating for r in reviews) / len(reviews), 1)

    def get_review_count(self, obj):
        return len(obj.reviews.all())
