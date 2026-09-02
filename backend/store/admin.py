from django.contrib import admin
from .models import (
    AccountToken, AdminAuditLog, Category, Product,
    Order, OrderItem, CartItem, PaymentTransaction, Review, Coupon, UserProfile,
    SalesNote, StockMovement,
    Branch, Company, Membership,
    CompanyArea, CompanyRole, MembershipRoleAssignment,
    BranchStock, InventoryCount, InventoryCountItem,
    MembershipBranchAccess, StockTransfer, StockTransferItem,
    CompanySettings, InternalSequence,
    Customer,
)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    """
    Phase 2B: categories belong to a company.

    The Django admin is the PLATFORM operator's tool, not the company-admin SaaS
    surface, so it deliberately shows every tenant. It is not subject to the
    company capability rules — admin access already implies operator trust.
    """

    list_display = ('id', 'name', 'slug', 'company')
    list_filter = ('company',)
    search_fields = ('name', 'slug', 'company__name')
    list_select_related = ('company',)
    autocomplete_fields = ('company',)
    prepopulated_fields = {'slug': ('name',)}

    def get_readonly_fields(self, request, obj=None):
        # Reparenting a category would drag its products' taxonomy into another
        # tenant. Locked once the row exists.
        base = super().get_readonly_fields(request, obj)
        return (*base, 'company') if obj else base


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    """Platform-operator view of the catalogue, across tenants."""

    list_display = ('id', 'name', 'company', 'price', 'inventory', 'category', 'is_active')
    prepopulated_fields = {'slug': ('name',)}
    list_filter = ('company', 'is_active', 'category')
    search_fields = ('name', 'slug', 'description', 'company__name')
    list_select_related = ('company', 'category')
    autocomplete_fields = ('company', 'category')

    def get_readonly_fields(self, request, obj=None):
        # A product carries history — orders, cart items, Kardex lines. Moving it
        # to another company after the fact would silently reassign all of it.
        base = super().get_readonly_fields(request, obj)
        return (*base, 'company') if obj else base

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Offer only categories of the product's own company."""
        if db_field.name == 'category':
            obj_id = request.resolver_match.kwargs.get('object_id') if request.resolver_match else None
            if obj_id:
                product = Product.objects.filter(pk=obj_id).first()
                if product is not None:
                    kwargs['queryset'] = Category.objects.filter(company=product.company_id)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('price',)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'customer_name', 'customer_email', 'total', 'discount_amount',
        'coupon_code', 'status', 'fulfillment_status', 'delivery_method',
        'receipt_type', 'paid', 'paid_at', 'created_at',
    )
    list_filter = ('status', 'fulfillment_status', 'delivery_method', 'receipt_type', 'paid', 'created_at')
    search_fields = ('customer_name', 'customer_email', 'coupon_code',
                     'document_number', 'customer_phone')
    readonly_fields = ('paid_at', 'payment_error',
                       'accepted_terms', 'accepted_warranty_policy',
                       'confirmation_email_sent_at', 'internal_notification_sent_at', 'email_send_error')
    fieldsets = (
        ('Identificación', {
            'fields': ('id', 'user', 'status', 'fulfillment_status', 'paid', 'paid_at'),
        }),
        ('Cliente', {
            'fields': ('customer_name', 'customer_email', 'customer_phone',
                       'document_type', 'document_number'),
        }),
        ('Económico', {
            'fields': ('total', 'discount_amount', 'coupon_code'),
        }),
        ('Entrega', {
            'fields': ('delivery_method', 'address_line', 'city', 'district', 'reference'),
        }),
        ('Comprobante y notas', {
            'fields': ('receipt_type', 'notes', 'accepted_terms', 'accepted_warranty_policy'),
        }),
        ('Emails transaccionales (Fase 4.1)', {
            'fields': ('confirmation_email_sent_at', 'internal_notification_sent_at', 'email_send_error'),
        }),
        ('Técnico', {
            'classes': ('collapse',),
            'fields': ('payment_error', 'cart_session_key'),
        }),
    )
    inlines = [OrderItemInline]


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'session_key', 'product', 'quantity', 'added_at')


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ('id', 'product', 'author_name', 'rating', 'created_at')
    list_filter = ('rating', 'product')
    search_fields = ('author_name', 'comment', 'product__name')


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ('id', 'code', 'discount_percent', 'is_active', 'expires_at')
    list_filter = ('is_active',)
    search_fields = ('code',)


@admin.register(AccountToken)
class AccountTokenAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'purpose', 'created_at', 'expires_at', 'used_at')
    list_filter = ('purpose',)
    search_fields = ('user__username', 'user__email')
    readonly_fields = ('token_hash', 'created_at', 'expires_at', 'used_at', 'user', 'purpose')


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'role', 'created_at', 'updated_at')
    list_filter = ('role',)
    search_fields = ('user__username', 'user__email')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(AdminAuditLog)
class AdminAuditLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'actor', 'action', 'target_type', 'target_id', 'ip_address', 'created_at')
    list_filter = ('action', 'target_type', 'created_at')
    search_fields = ('actor__username', 'target_id', 'action')
    readonly_fields = ('actor', 'action', 'target_type', 'target_id', 'metadata', 'ip_address', 'user_agent', 'created_at')


# ---------------------------------------------------------------------------
# Phase 6.0 — Kardex and internal sales notes
# ---------------------------------------------------------------------------

@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    """
    Read-only on purpose: stock must only move through store.inventory_services
    so that BranchStock, the Kardex and the Product.inventory compatibility
    aggregate stay consistent. Editing a movement here would desync all three,
    and a Kardex whose lines can be edited is not a Kardex.
    """
    list_display = (
        'id', 'created_at', 'company', 'branch', 'product', 'movement_type',
        'quantity', 'stock_before', 'stock_after', 'actor', 'order',
    )
    list_filter = ('movement_type', 'company', 'branch', 'created_at')
    search_fields = ('product__name', 'reason', 'reference_id', 'actor__username')
    readonly_fields = (
        'company', 'branch', 'product', 'movement_type', 'quantity',
        'stock_before', 'stock_after', 'reason', 'reference_type', 'reference_id',
        'order', 'transfer', 'inventory_count', 'actor', 'created_at', 'metadata',
    )
    list_select_related = ('company', 'branch', 'product', 'actor')
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SalesNote)
class SalesNoteAdmin(admin.ModelAdmin):
    """Internal sales notes. NOT SUNAT electronic receipts."""
    list_display = ('id', 'number', 'order', 'status', 'issued_at', 'created_by', 'pdf_generated_at')
    list_filter = ('status', 'created_at')
    search_fields = ('number', 'order__id', 'order__customer_name')
    readonly_fields = (
        'order', 'number', 'issued_at', 'created_at', 'created_by',
        'pdf_generated_at', 'metadata',
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# SaaS Phase 1 — Company / Branch / Membership
# ---------------------------------------------------------------------------

class BranchInline(admin.TabularInline):
    model = Branch
    extra = 0
    fields = ('name', 'address', 'phone', 'is_active')
    show_change_link = True


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    """
    Tenants. Deleting a company with operations is blocked by PROTECT on Branch
    and Membership — deactivate with `is_active` instead so history is preserved.
    """
    list_display = ('id', 'name', 'slug', 'legal_name', 'tax_id', 'is_active', 'created_at')
    list_filter = ('is_active', 'created_at')
    search_fields = ('name', 'legal_name', 'tax_id', 'slug')
    readonly_fields = ('created_at', 'updated_at')
    prepopulated_fields = {'slug': ('name',)}
    inlines = [BranchInline]

    def has_delete_permission(self, request, obj=None):
        # Never remove a tenant from the admin; deactivate it.
        return False

    def save_model(self, request, obj, form, change):
        """
        A company created here gets the same defaults as one created through the
        API — same service, no second copy of the preset list.

        An explicit call rather than a post_save signal: a signal would fire for
        every Company write anywhere (including migration 0015's historical
        model, and fixtures), which is both surprising and hard to test.
        """
        from .company_provisioning import provision_company_access_defaults

        super().save_model(request, obj, form, change)
        if not change:
            provision_company_access_defaults(obj, actor=request.user)


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'company', 'address', 'phone', 'is_active')
    list_filter = ('is_active', 'company')
    search_fields = ('name', 'address', 'company__name')
    readonly_fields = ('created_at', 'updated_at')
    list_select_related = ('company',)
    autocomplete_fields = ('company',)

    def get_readonly_fields(self, request, obj=None):
        # Reparenting a branch would silently move its future operations to
        # another tenant — lock the company once the row exists.
        base = super().get_readonly_fields(request, obj)
        return (*base, 'company') if obj else base


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'company', 'role', 'branch', 'is_active', 'created_at')
    list_filter = ('role', 'is_active', 'company')
    search_fields = ('user__username', 'user__email', 'company__name')
    readonly_fields = ('created_at', 'updated_at')
    list_select_related = ('user', 'company', 'branch')
    autocomplete_fields = ('company', 'branch')

    def get_readonly_fields(self, request, obj=None):
        # user + company identify the membership; changing either is a different
        # grant entirely. Create a new row instead.
        base = super().get_readonly_fields(request, obj)
        return (*base, 'user', 'company') if obj else base


# ---------------------------------------------------------------------------
# SaaS Phase 2A.1 — areas, roles and assignments
# ---------------------------------------------------------------------------

@admin.register(CompanyArea)
class CompanyAreaAdmin(admin.ModelAdmin):
    """Organisational areas. Areas grant no permissions — see the model."""
    list_display = ('id', 'name', 'company', 'slug', 'sort_order', 'is_active')
    list_filter = ('is_active', 'company')
    search_fields = ('name', 'slug', 'company__name')
    readonly_fields = ('created_at', 'updated_at')
    list_select_related = ('company',)
    autocomplete_fields = ('company',)

    def get_readonly_fields(self, request, obj=None):
        # Reparenting an area would drag its assignments into another tenant.
        base = super().get_readonly_fields(request, obj)
        return (*base, 'company') if obj else base

    def has_delete_permission(self, request, obj=None):
        # Deactivate instead: assignments reference areas.
        return False


@admin.register(CompanyRole)
class CompanyRoleAdmin(admin.ModelAdmin):
    """
    Company roles and their capabilities.

    Editing `capabilities` here bypasses the API's anti-escalation check, so this
    screen is for platform operators only — Django admin access already implies
    staff-level trust.
    """
    list_display = ('id', 'name', 'company', 'slug', 'capability_count', 'is_active')
    list_filter = ('is_active', 'company')
    search_fields = ('name', 'slug', 'company__name')
    readonly_fields = ('created_at', 'updated_at')
    list_select_related = ('company',)
    autocomplete_fields = ('company',)

    @admin.display(description='Capacidades')
    def capability_count(self, obj):
        return len(obj.capabilities or [])

    def get_readonly_fields(self, request, obj=None):
        base = super().get_readonly_fields(request, obj)
        return (*base, 'company') if obj else base

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MembershipRoleAssignment)
class MembershipRoleAssignmentAdmin(admin.ModelAdmin):
    """
    Which staff member holds which role, optionally in which area.

    `membership` and `role` are locked after creation: changing either is a
    different grant. The model's clean() blocks cross-tenant combinations.
    """
    list_display = ('id', 'membership', 'role', 'area', 'is_active', 'assigned_by', 'created_at')
    list_filter = ('is_active', 'role__company')
    search_fields = (
        'membership__user__username', 'role__name', 'area__name',
        'membership__company__name',
    )
    readonly_fields = ('created_at', 'updated_at')
    list_select_related = ('membership__user', 'membership__company', 'role', 'area')
    autocomplete_fields = ('role', 'area')

    def get_readonly_fields(self, request, obj=None):
        base = super().get_readonly_fields(request, obj)
        return (*base, 'membership', 'role') if obj else base

    def has_delete_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# SaaS Phase 2D — multi-branch inventory
# ---------------------------------------------------------------------------
#
# DJANGO ADMIN IS THE PLATFORM OPERATOR'S SURFACE, NOT A SECOND SaaS API.
#
# It exists here for inspection and for the rare repair a superuser has to make
# by hand. It deliberately does NOT let anyone move stock: quantities are
# read-only everywhere below, because a stock change with no Kardex line is the
# one thing the whole module is built to prevent, and "the admin can do it"
# would be a hole with a nice UI on it. Real work happens through the SaaS API,
# which checks capability and branch access.

@admin.register(BranchStock)
class BranchStockAdmin(admin.ModelAdmin):
    """
    Stock per branch. `quantity` is READ-ONLY — move stock through the API.

    The replenishment policy (minimum / target) IS editable: it is configuration,
    not stock, and changing it moves nothing.
    """

    list_display = (
        'id', 'branch', 'product', 'quantity', 'minimum_stock', 'target_stock',
        'updated_at',
    )
    list_filter = ('branch__company', 'branch')
    search_fields = ('product__name', 'product__slug', 'branch__name')
    readonly_fields = ('quantity', 'created_at', 'updated_at')
    list_select_related = ('branch', 'product')

    def has_add_permission(self, request):
        # Rows are created by the service layer the first time stock moves.
        # Creating one here would be an empty shelf nobody asked for.
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MembershipBranchAccess)
class MembershipBranchAccessAdmin(admin.ModelAdmin):
    """
    Which branches a membership may operate, while its mode is SELECTED.

    These rows are IGNORED in ALL mode — see Membership.branch_access_mode. A
    grant listed here therefore does not by itself prove someone has access.
    """

    list_display = ('id', 'membership', 'branch', 'is_active', 'granted_by', 'created_at')
    list_filter = ('is_active', 'branch__company', 'branch')
    search_fields = (
        'membership__user__username', 'branch__name', 'membership__company__name',
    )
    readonly_fields = ('created_at', 'updated_at')
    list_select_related = ('membership__user', 'membership__company', 'branch')

    def get_readonly_fields(self, request, obj=None):
        base = super().get_readonly_fields(request, obj)
        # Repointing a grant at a different membership or branch is a different
        # grant; make a new one so the audit trail keeps both.
        return (*base, 'membership', 'branch') if obj else base


class StockTransferItemInline(admin.TabularInline):
    model = StockTransferItem
    extra = 0
    readonly_fields = ('product', 'quantity')
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(StockTransfer)
class StockTransferAdmin(admin.ModelAdmin):
    """
    Read-only. A transfer moves stock, so it may only progress through the
    service layer — flipping `status` here would advance the paperwork without
    creating the movements, leaving units in two places at once.
    """

    list_display = (
        'id', 'created_at', 'company', 'source_branch', 'destination_branch',
        'status', 'created_by', 'dispatched_at', 'received_at',
    )
    list_filter = ('status', 'company', 'source_branch', 'destination_branch')
    search_fields = ('reference', 'reason', 'created_by__username')
    readonly_fields = (
        'company', 'source_branch', 'destination_branch', 'status', 'reason',
        'reference', 'created_by', 'dispatched_by', 'received_by', 'cancelled_by',
        'created_at', 'dispatched_at', 'received_at', 'cancelled_at', 'updated_at',
        'metadata',
    )
    inlines = [StockTransferItemInline]
    date_hierarchy = 'created_at'
    list_select_related = ('company', 'source_branch', 'destination_branch')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class InventoryCountItemInline(admin.TabularInline):
    model = InventoryCountItem
    extra = 0
    readonly_fields = (
        'product', 'theoretical_at_start', 'physical_quantity',
        'theoretical_at_approval', 'difference', 'note',
    )
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(InventoryCount)
class InventoryCountAdmin(admin.ModelAdmin):
    """
    Read-only, for the same reason as StockTransfer: approving a count writes
    correction movements, and a status changed by hand would skip them.

    The three theoretical/physical columns on each line are what makes a count
    auditable months later — see the InventoryCount docstring.
    """

    list_display = (
        'id', 'created_at', 'company', 'branch', 'status',
        'created_by', 'approved_by', 'approved_at',
    )
    list_filter = ('status', 'company', 'branch')
    search_fields = ('reason', 'created_by__username', 'approved_by__username')
    readonly_fields = (
        'company', 'branch', 'status', 'reason', 'created_by', 'approved_by',
        'cancelled_by', 'created_at', 'approved_at', 'cancelled_at', 'updated_at',
        'metadata',
    )
    inlines = [InventoryCountItemInline]
    date_hierarchy = 'created_at'
    list_select_related = ('company', 'branch')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# SaaS Phase 3 — company configuration
# ---------------------------------------------------------------------------

@admin.register(CompanySettings)
class CompanySettingsAdmin(admin.ModelAdmin):
    """
    A company's configuration, for the PLATFORM OPERATOR.

    `company` is locked after creation: settings are one-to-one with a company,
    and repointing them would move one business's identity onto another — the
    exact thing this phase exists to make impossible.

    The model's `clean()` runs on save, so a colour typed here is validated by
    the same rule the API uses. Django Admin is an operator's tool, not a second
    API with weaker checks.
    """

    list_display = (
        'company', 'contact_email', 'phone', 'order_notification_email',
        'currency', 'updated_at',
    )
    list_filter = ('currency',)
    search_fields = ('company__name', 'company__slug', 'contact_email', 'phone')
    list_select_related = ('company',)
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        ('Empresa', {'fields': ('company',)}),
        ('Contacto', {
            'fields': (
                'contact_email', 'phone', 'whatsapp_number', 'website_url',
                'facebook_url', 'instagram_url',
                'legal_address', 'city', 'country_code',
            ),
        }),
        ('Branding', {
            'fields': (
                'logo_url', 'primary_color', 'accent_color', 'background_color',
                'surface_color', 'text_color', 'border_color',
            ),
        }),
        ('Negocio', {'fields': ('timezone', 'currency')}),
        ('Políticas', {
            'fields': (
                'warranty_policy_text', 'warranty_policy_url', 'terms_url',
                'privacy_url',
            ),
        }),
        ('Notificaciones internas', {'fields': ('order_notification_email',)}),
        ('Auditoría', {'fields': ('created_at', 'updated_at')}),
    )

    def get_readonly_fields(self, request, obj=None):
        base = super().get_readonly_fields(request, obj)
        return (*base, 'company') if obj else base

    def has_delete_permission(self, request, obj=None):
        # Deleting settings would leave a live tenant with no identity and no
        # obvious way to notice. Deactivate the company instead.
        return False


# ---------------------------------------------------------------------------
# SaaS Phase 2E — internal document sequences
# ---------------------------------------------------------------------------

@admin.register(InternalSequence)
class InternalSequenceAdmin(admin.ModelAdmin):
    """
    A document counter, for the PLATFORM OPERATOR.

    `company`, `branch` and `document_type` are locked after creation: they are
    what makes a series that series, and repointing one would move a counter —
    and the documents that PROTECT-reference it — to another tenant.

    `next_value` becomes read-only once the series has issued. Django Admin is an
    operator's tool, not a second API with weaker rules: moving the counter
    backwards here would reissue identifiers already printed on documents, and
    the constraint added in 0029 would then reject the next note with an
    IntegrityError far from the cause.

    No delete: `SalesNote.sequence` is PROTECT, so the database refuses anyway.
    Retire a series with `is_active` — reactivating continues where it left off
    rather than restarting.
    """

    list_display = (
        'company', 'branch', 'document_type', 'prefix', 'padding',
        'next_value', 'preview', 'is_active',
    )
    list_filter = ('document_type', 'is_active', 'company')
    search_fields = ('company__name', 'company__slug', 'branch__name', 'prefix')
    list_select_related = ('company', 'branch')
    readonly_fields = ('preview', 'has_issued', 'created_at', 'updated_at')

    @admin.display(description='Próximo número')
    def preview(self, obj):
        return obj.preview

    def get_readonly_fields(self, request, obj=None):
        base = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            base += ['company', 'branch', 'document_type']
            if obj.has_issued:
                base.append('next_value')
        return tuple(base)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    """
    Platform-operator view of the CRM.

    `company` is locked after creation. A customer moved between companies would
    take their orders with them — `Order.company` would then disagree with
    `Order.customer.company`, and one tenant's sales history would be sitting in
    another tenant's CRM. Django Admin is an operator tool, not an exemption from
    the invariants the rest of the platform enforces.

    No delete: `Order.customer` is PROTECT, so the database refuses for anyone
    with history, and for anyone without it archiving is still the right answer.
    """

    list_display = (
        'display_name', 'company', 'customer_type', 'document_type',
        'document_number', 'is_active', 'created_at',
    )
    list_filter = ('company', 'customer_type', 'is_active', 'document_type')
    search_fields = (
        'first_name', 'last_name', 'business_name',
        'document_number', 'email', 'phone',
    )
    list_select_related = ('company',)
    readonly_fields = ('display_name', 'has_account', 'created_at', 'updated_at')
    raw_id_fields = ('user', 'created_by')

    @admin.display(description='Cliente')
    def display_name(self, obj):
        return obj.display_name

    def get_readonly_fields(self, request, obj=None):
        base = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            base.append('company')
        return tuple(base)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    """
    The payments ledger, READ ONLY.

    Nothing here is editable, and nothing is deletable. These rows are what an
    operator reconciles a disputed charge against; a screen that let someone
    correct an amount or a response code by hand would make them worthless as
    evidence of what the gateway actually said.

    The Order admin used to carry a "Técnico" panel with the gateway's
    identifiers on it. It no longer does — the identifiers moved here, where
    every attempt is visible instead of only the last one.
    """

    list_display = (
        'transaction_id', 'order', 'provider', 'status', 'amount', 'currency',
        'response_code', 'signature_verified', 'created_at', 'confirmed_at',
    )
    list_filter = ('provider', 'status', 'signature_verified', 'currency', 'created_at')
    search_fields = ('transaction_id', 'order_number', 'authorization_code',
                     'reference_number', 'provider_unique_id')
    list_select_related = ('order',)
    raw_id_fields = ('order',)
    date_hierarchy = 'created_at'

    def get_readonly_fields(self, request, obj=None):
        return tuple(f.name for f in self.model._meta.fields)

    def has_add_permission(self, request):
        # A payment is created by a checkout, never typed in.
        return False

    def has_delete_permission(self, request, obj=None):
        return False
