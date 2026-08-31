"""
Serializers for the native authentication contract — `/api/v1/auth/`.

Kept apart from `auth_serializers.py` on purpose. Those belong to the web
frontend, whose login posts a USERNAME and receives its JWTs in HttpOnly
cookies. This contract takes an EMAIL and returns tokens in the body, because a
native app has no cookie jar and its users know their email, not the username a
registration form generated for them.

Two contracts, two files. Changing one must not be able to move the other.
"""
from django.contrib.auth.models import User
from rest_framework import serializers


class V1LoginSerializer(serializers.Serializer):
    """Shape only. Credential checking happens in the view, never here."""

    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False, style={'input_type': 'password'})

    def validate_email(self, value):
        return value.strip().lower()


class V1RefreshSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class V1LogoutSerializer(serializers.Serializer):
    # Optional: logout is best-effort. A client that has already lost its refresh
    # token must still be able to say "I am done" without inventing one.
    refresh = serializers.CharField(required=False, allow_blank=True)


class V1CompanyRelationSerializer(serializers.Serializer):
    """One company the authenticated user has a server-verified relation with."""

    slug = serializers.CharField()
    name = serializers.CharField()
    relation = serializers.CharField()


class V1UserSerializer(serializers.ModelSerializer):
    """
    The identity a native client is allowed to see about itself.

    Deliberately NOT the admin view of a user: no permissions, no capabilities,
    no group membership, no staff flags, no internal role beyond the coarse one
    the app uses to choose a home screen.
    """

    role = serializers.SerializerMethodField()
    is_email_verified = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name',
            'role', 'is_email_verified',
        ]

    def get_role(self, obj):
        """
        The coarse role from `UserProfile`, defaulting to customer.

        `UserProfile.role` is still the authoritative permission source in this
        installation (see the transition note on `Membership`). It is reported
        here only so the app can pick a starting screen — never so the app can
        decide what the user may do. That decision stays on the server.
        """
        profile = getattr(obj, 'profile', None)
        return getattr(profile, 'role', 'customer') or 'customer'

    def get_is_email_verified(self, obj):
        """
        This installation has NO separate `is_email_verified` column.

        Registration creates the account with `is_active=False` and
        `VerifyEmailView` flips it to `True` — verification and activation are
        the same fact here. So this is always `True` for anyone who successfully
        authenticated, because an inactive user cannot obtain a token at all.

        It is reported anyway because the mobile session model has the field and
        because BR-001B may split the two concepts; a client written against this
        contract then keeps working.
        """
        return bool(obj.is_active)


class V1CompanyRefSerializer(serializers.Serializer):
    slug = serializers.CharField()
    name = serializers.CharField()


class V1AccessContextSerializer(serializers.Serializer):
    """
    Where one identity may act inside one company.

    `customer` and `member` are INDEPENDENT booleans, not a role. The same
    person can buy from a company and work for it, and collapsing that into one
    field would force a client to choose which of two true things to believe.

    `capabilities` travels for PRESENTATION only (DEC-MOBILE-008): it decides
    which tab is drawn, never whether an operation is allowed. Every internal
    endpoint re-resolves capabilities server-side.
    """

    company = V1CompanyRefSerializer()
    customer = serializers.BooleanField()
    member = serializers.BooleanField()
    capabilities = serializers.ListField(child=serializers.CharField())


class V1PlatformSerializer(serializers.Serializer):
    """
    Platform-master status, reported SEPARATELY from any company.

    Being a platform administrator is not membership of every tenant, and this
    field grants nothing: `access_contexts` is still built from real rows.
    Acting on a company as platform master stays an explicit, audited act on the
    internal surface.
    """

    is_master = serializers.BooleanField()
