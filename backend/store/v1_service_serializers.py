"""
Serializers for the technical-service surface.

TWO AUDIENCES, TWO CONTRACTS, AND THEY ARE NOT VERSIONS OF EACH OTHER.

The internal serializers describe a device on a bench to the people working on
it: who owns it, what they said was wrong, what the counter wrote down, who is
responsible, everything that has happened. The customer serializers describe the
same order to the person waiting for it, and they are narrower on purpose —
narrower in a way that cannot be widened by accident, because they are different
classes with different field lists rather than one class with a flag.

The temptation is always to write `if request.user.is_staff` inside one
serializer. That is one refactor away from returning a technician's private note
to a customer, and the failure is silent.
"""
from decimal import Decimal

from rest_framework import serializers

from .models import (
    Device,
    PartUsage,
    QualityCheck,
    QualityCheckItem,
    QualityResultCode,
    PaymentMethod,
    RepairDelivery,
    RepairPayment,
    RepairDiagnostic,
    RepairExecution,
    RepairOrder,
    RepairQuote,
    RepairQuoteDecision,
    RepairQuoteItem,
    RepairResultCode,
    RepairStatusHistory,
    RepairStatusSetting,
    TechnicianAssignment,
)


# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------

class V1RepairStatusSettingSerializer(serializers.ModelSerializer):
    """One lifecycle state as THIS company presents it."""

    class Meta:
        model = RepairStatusSetting
        fields = ('code', 'label', 'is_customer_visible', 'sort_order')
        read_only_fields = fields


# ---------------------------------------------------------------------------
# INTERNAL
# ---------------------------------------------------------------------------

class V1ServiceDeviceSerializer(serializers.ModelSerializer):
    device_type_label = serializers.CharField(source='get_device_type_display', read_only=True)
    customer_name = serializers.SerializerMethodField()
    display_name = serializers.CharField(read_only=True)

    class Meta:
        model = Device
        fields = (
            'id', 'customer', 'customer_name',
            'device_type', 'device_type_label', 'brand', 'model', 'display_name',
            'serial_number', 'imei', 'color', 'storage_capacity', 'notes',
            'created_at', 'updated_at',
        )
        read_only_fields = fields

    def get_customer_name(self, obj) -> str:
        return _customer_display(obj.customer)

    # ── Absent on purpose ────────────────────────────────────────────────────
    # `company`      the tenant is in the URL; echoing it invites a client to
    #                think it is a parameter.
    # `created_by`   who typed a record in is not information the app needs, and
    #                it is a staff member's identity travelling for no reason.


class V1ServiceCustomerSerializer(serializers.Serializer):
    """
    The thinnest possible customer, for choosing one during intake.

    NOT the CRM record. A receptionist opening an order needs to recognise a
    person and pick them; they do not need that person's address, their notes or
    their commercial history, and this endpoint is reachable with
    `service.customers.view` rather than with CRM authority.
    """

    id = serializers.IntegerField(read_only=True)
    display_name = serializers.SerializerMethodField()
    document_number = serializers.CharField(read_only=True)
    phone = serializers.CharField(read_only=True)

    def get_display_name(self, obj) -> str:
        return _customer_display(obj)

    # ── Absent on purpose ────────────────────────────────────────────────────
    # `email`, `address_line`, `district`, `city`, `notes`, `created_at`
    #                the CRM surface exists and enforces its own capability.
    #                Intake needs a name and a way to tell two people apart.


class V1ServiceAssignmentSerializer(serializers.ModelSerializer):
    technician_name = serializers.SerializerMethodField()

    class Meta:
        model = TechnicianAssignment
        fields = (
            'id', 'technician', 'technician_name',
            'assigned_at', 'unassigned_at',
        )
        read_only_fields = fields

    def get_technician_name(self, obj) -> str:
        return _user_display(obj.technician)

    # ── Absent on purpose ────────────────────────────────────────────────────
    # `assigned_by`  a second staff identity on every row, for no reader.
    # e-mail, phone  a technician's contact details are personnel data. The
    #                internal surface names them; it does not publish them.


class V1ServiceHistorySerializer(serializers.ModelSerializer):
    """The INTERNAL timeline. Everything, including the comments."""

    actor_name = serializers.SerializerMethodField()
    to_status_label = serializers.SerializerMethodField()

    class Meta:
        model = RepairStatusHistory
        fields = (
            'id', 'from_status', 'to_status', 'to_status_label',
            'origin', 'comment', 'is_customer_visible',
            'actor_name', 'created_at',
        )
        read_only_fields = fields

    def get_actor_name(self, obj) -> str:
        return _user_display(obj.actor)

    def get_to_status_label(self, obj) -> str:
        labels = self.context.get('status_labels') or {}
        return labels.get(obj.to_status, obj.to_status)


class V1ServiceOrderListSerializer(serializers.ModelSerializer):
    status_label = serializers.SerializerMethodField()
    customer_name = serializers.SerializerMethodField()
    device_summary = serializers.SerializerMethodField()
    branch_name = serializers.CharField(source='branch.name', read_only=True, default='')
    technician_name = serializers.SerializerMethodField()

    class Meta:
        model = RepairOrder
        fields = (
            'id', 'number', 'status', 'status_label',
            'customer', 'customer_name',
            'device', 'device_summary',
            'branch', 'branch_name',
            'technician_name',
            'received_at', 'closed_at', 'updated_at',
        )
        read_only_fields = fields

    def get_status_label(self, obj) -> str:
        labels = self.context.get('status_labels') or {}
        return labels.get(obj.status, obj.status)

    def get_customer_name(self, obj) -> str:
        return _customer_display(obj.customer)

    def get_device_summary(self, obj) -> str:
        return obj.device.display_name if obj.device_id else ''

    def get_technician_name(self, obj) -> str:
        """
        The CURRENT technician, or empty. Read from the assignment table.

        There is no `current_technician` column to drift out of step with the
        history — the list view prefetches the open assignment and this reads it.
        """
        assignment = next(
            (a for a in obj.assignments.all() if a.unassigned_at is None), None,
        )
        return _user_display(assignment.technician) if assignment else ''


class V1ServiceOrderDetailSerializer(V1ServiceOrderListSerializer):
    device_detail = V1ServiceDeviceSerializer(source='device', read_only=True)
    received_by_name = serializers.SerializerMethodField()
    history = serializers.SerializerMethodField()
    assignments = V1ServiceAssignmentSerializer(many=True, read_only=True)
    available_transitions = serializers.SerializerMethodField()

    class Meta(V1ServiceOrderListSerializer.Meta):
        fields = V1ServiceOrderListSerializer.Meta.fields + (
            'reported_issue', 'physical_condition', 'received_accessories',
            'internal_notes', 'received_by_name', 'device_detail',
            'history', 'assignments', 'available_transitions',
        )
        read_only_fields = fields

    def get_received_by_name(self, obj) -> str:
        return _user_display(obj.received_by)

    def get_history(self, obj):
        return V1ServiceHistorySerializer(
            obj.status_history.all().order_by('created_at', 'pk'),
            many=True, context=self.context,
        ).data

    def get_available_transitions(self, obj):
        """
        The moves this order may make, AS THE SERVER COMPUTES THEM.

        Not a table the app also keeps. A client with its own copy drifts the
        first time the machine changes, and the drift appears as a button that
        fails — which reads as a broken app rather than as a policy. The PATCH
        re-validates regardless of what was drawn.
        """
        from . import service_services

        labels = self.context.get('status_labels') or {}
        return [
            {'code': code, 'label': labels.get(code, code)}
            for code in service_services.available_transitions(obj)
        ]


# ---------------------------------------------------------------------------
# INTERNAL — write payloads
# ---------------------------------------------------------------------------

class V1ServiceDeviceCreateSerializer(serializers.Serializer):
    """
    Register a device. IDs are resolved inside the tenant by the view.

    There is no `company` field and there never will be: the tenant comes from
    the URL, and a company id in a body is a parameter somebody will eventually
    try to change.
    """

    customer_id = serializers.IntegerField()
    device_type = serializers.ChoiceField(choices=Device.TYPE_CHOICES)
    brand = serializers.CharField(max_length=80, trim_whitespace=True)
    model = serializers.CharField(max_length=120, trim_whitespace=True)
    serial_number = serializers.CharField(
        max_length=80, required=False, allow_blank=True, trim_whitespace=True,
    )
    imei = serializers.CharField(
        max_length=32, required=False, allow_blank=True, trim_whitespace=True,
    )
    color = serializers.CharField(max_length=40, required=False, allow_blank=True)
    storage_capacity = serializers.CharField(max_length=40, required=False, allow_blank=True)
    notes = serializers.CharField(max_length=2000, required=False, allow_blank=True)

    # ── Absent on purpose, and this list is the contract ─────────────────────
    # No `unlock_code`, no `pin`, no `pattern`, no `password`, no `apple_id`,
    # no `icloud_password`. Repair shops do ask for them. Storing one would make
    # this table a credential store with no encryption-at-rest decision, no
    # access policy, no retention rule and no deletion story — none of which
    # exist. The field arrives with the policy, not before it.


class V1ServiceOrderCreateSerializer(serializers.Serializer):
    """
    Receive a device. The payload is an INTENTION, not a record.

    Everything that identifies the order — its number, its state, who received
    it and when — is decided by `service_services.create_repair_order()`. There
    is no field here for any of them, which is the only way to guarantee a
    client cannot set one.
    """

    customer_id = serializers.IntegerField()
    device_id = serializers.IntegerField()
    branch_id = serializers.IntegerField()
    reported_issue = serializers.CharField(max_length=2000, trim_whitespace=True)
    physical_condition = serializers.CharField(
        max_length=2000, required=False, allow_blank=True,
    )
    received_accessories = serializers.CharField(
        max_length=1000, required=False, allow_blank=True,
    )
    internal_notes = serializers.CharField(
        max_length=2000, required=False, allow_blank=True,
    )

    # ── Absent on purpose ────────────────────────────────────────────────────
    # `number`, `status`, `received_by`, `received_at`, `closed_at`, `company`


class V1ServiceTransitionSerializer(serializers.Serializer):
    """Move an order. The target is validated against the machine, not here."""

    status = serializers.CharField(max_length=32, trim_whitespace=True)
    comment = serializers.CharField(max_length=1000, required=False, allow_blank=True)

    # ── Absent on purpose ────────────────────────────────────────────────────
    # `actor`, `origin`  the server knows who is calling and how. A client that
    #                    could set `origin='system'` could disown its own action.


class V1ServiceAssignmentWriteSerializer(serializers.Serializer):
    """
    Assign a technician, or release the order.

    `technician_id` only — never a user object, never an email. The view
    resolves the id against the company's own staff, so a foreign id is not
    found rather than found-then-refused.
    """

    technician_id = serializers.IntegerField(required=False, allow_null=True)


# ---------------------------------------------------------------------------
# CUSTOMER
# ---------------------------------------------------------------------------

class V1CustomerRepairEventSerializer(serializers.ModelSerializer):
    """
    One visible step of the customer's timeline.

    The SERVER filters. `is_customer_visible` is decided when the event is
    written and this serializer is only ever fed rows that passed it — the app
    receives no hidden event to accidentally render, which is a stronger
    guarantee than asking it not to.
    """

    status_label = serializers.SerializerMethodField()

    class Meta:
        model = RepairStatusHistory
        fields = ('id', 'status', 'status_label', 'occurred_at')
        read_only_fields = fields

    status = serializers.CharField(source='to_status', read_only=True)
    occurred_at = serializers.DateTimeField(source='created_at', read_only=True)

    def get_status_label(self, obj) -> str:
        labels = self.context.get('status_labels') or {}
        return labels.get(obj.to_status, obj.to_status)

    # ── Absent on purpose ────────────────────────────────────────────────────
    # `from_status`  a customer does not need the machine's internals.
    # `comment`      INTERNAL, always. This is where a technician writes what
    #                they actually think.
    # `actor`        who moved it is personnel data.
    # `origin`       an implementation detail of how the event was produced.


class V1CustomerRepairListSerializer(serializers.ModelSerializer):
    status_label = serializers.SerializerMethodField()
    device_summary = serializers.SerializerMethodField()

    class Meta:
        model = RepairOrder
        fields = (
            'id', 'number', 'status', 'status_label',
            'device_summary', 'received_at', 'closed_at', 'updated_at',
        )
        read_only_fields = fields

    def get_status_label(self, obj) -> str:
        labels = self.context.get('status_labels') or {}
        return labels.get(obj.status, obj.status)

    def get_device_summary(self, obj) -> str:
        return obj.device.display_name if obj.device_id else ''


class V1CustomerRepairDetailSerializer(V1CustomerRepairListSerializer):
    timeline = serializers.SerializerMethodField()

    class Meta(V1CustomerRepairListSerializer.Meta):
        fields = V1CustomerRepairListSerializer.Meta.fields + (
            'reported_issue', 'timeline',
        )
        read_only_fields = fields

    def get_timeline(self, obj):
        from . import service_services

        return V1CustomerRepairEventSerializer(
            service_services.customer_visible_history(obj),
            many=True, context=self.context,
        ).data

    # ── Absent on purpose, and every omission is a decision ──────────────────
    # `internal_notes`        where the shop writes what it would not say aloud.
    # `physical_condition`    written for the shop's protection, in the shop's
    #                         words; showing it invites a dispute at the counter
    #                         rather than resolving one.
    # `received_accessories`  same reason.
    # `assignments` / technician name, email or phone — personnel data. A
    #                         customer may be told their device is being worked
    #                         on; they are not told by whom unless the business
    #                         decides that, and that decision does not exist.
    # `branch`                where a device physically sits is an internal
    #                         logistics fact until somebody designs pickup.
    # `available_transitions` the customer moves nothing.
    # `customer`              they are the customer; echoing their own id back
    #                         is a foreign key looking for a use.


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _customer_display(customer) -> str:
    if customer is None:
        return ''
    if customer.customer_type == customer.TYPE_BUSINESS and customer.business_name:
        return customer.business_name
    name = f'{customer.first_name} {customer.last_name}'.strip()
    return name or customer.business_name or f'Cliente #{customer.pk}'


def _user_display(user) -> str:
    """
    A display name, and never an email.

    `get_full_name()` falls back to the username, which in this installation is
    generated rather than personal. An email address is a login credential and a
    contact channel; neither belongs in a payload that only needs to say who did
    something.
    """
    if user is None:
        return ''
    full = (user.get_full_name() or '').strip()
    return full or user.username


# ---------------------------------------------------------------------------
# BR-005B — diagnosis, quotes and the customer's decision
# ---------------------------------------------------------------------------

class V1ServiceDiagnosticSerializer(serializers.ModelSerializer):
    """The INTERNAL view of a diagnosis. Everything, including the private notes."""

    status_label = serializers.CharField(source='get_status_display', read_only=True)
    diagnosed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = RepairDiagnostic
        fields = (
            'id', 'revision', 'status', 'status_label',
            'description', 'root_cause', 'recommended_action', 'internal_notes',
            'diagnosed_by_name', 'created_at', 'updated_at', 'finalized_at',
        )
        read_only_fields = fields

    def get_diagnosed_by_name(self, obj) -> str:
        return _user_display(obj.diagnosed_by)

    # ── Absent on purpose ────────────────────────────────────────────────────
    # `company`, `repair_order`  both are in the URL that reached this row.
    # evidence / attachments     no storage provider exists (DEC-016), so there
    #                            is no field to serialise and none invented.


class V1ServiceQuoteItemSerializer(serializers.ModelSerializer):
    item_type_label = serializers.CharField(source='get_item_type_display', read_only=True)

    class Meta:
        model = RepairQuoteItem
        fields = (
            'id', 'item_type', 'item_type_label', 'description',
            'quantity', 'unit_price', 'line_total', 'product', 'sort_order',
        )
        read_only_fields = fields


class V1ServiceQuoteSerializer(serializers.ModelSerializer):
    """The INTERNAL view of a quote."""

    status_label = serializers.CharField(source='get_status_display', read_only=True)
    items = V1ServiceQuoteItemSerializer(many=True, read_only=True)
    created_by_name = serializers.SerializerMethodField()
    is_expired = serializers.BooleanField(read_only=True)
    is_editable = serializers.BooleanField(read_only=True)
    decision = serializers.SerializerMethodField()

    class Meta:
        model = RepairQuote
        fields = (
            'id', 'revision', 'status', 'status_label', 'diagnostic',
            'currency', 'subtotal', 'discount_amount', 'tax_amount', 'total',
            'valid_until', 'is_expired', 'is_editable',
            'customer_notes', 'internal_notes',
            'items', 'decision', 'created_by_name',
            'created_at', 'updated_at', 'sent_at',
            'approved_at', 'rejected_at', 'cancelled_at',
        )
        read_only_fields = fields

    def get_created_by_name(self, obj) -> str:
        return _user_display(obj.created_by)

    def get_decision(self, obj):
        """
        What the customer answered, for the people who need to act on it.

        The REASON is here and nowhere near the customer timeline: free text
        from a client is theirs, and a future visibility policy must not be able
        to publish it by accident.
        """
        record = getattr(obj, 'decision', None)
        if record is None:
            return None
        return {
            'decision': record.decision,
            'reason': record.reason,
            'channel': record.channel,
            'decided_at': record.decided_at,
        }


class V1ServiceDiagnosticWriteSerializer(serializers.Serializer):
    """
    Compose or edit a diagnosis.

    `root_cause` is optional on purpose: a technician often knows a laptop does
    not charge long before they know why, and a required field turns "I do not
    know yet" into a guess written down as fact.
    """

    description = serializers.CharField(max_length=4000, trim_whitespace=True)
    recommended_action = serializers.CharField(max_length=4000, trim_whitespace=True)
    root_cause = serializers.CharField(
        max_length=2000, required=False, allow_blank=True,
    )
    internal_notes = serializers.CharField(
        max_length=2000, required=False, allow_blank=True,
    )

    # ── Absent on purpose ────────────────────────────────────────────────────
    # `diagnosed_by` / `technician_id`  the authenticated actor, always. "I am
    #                 recording this" is the only claim M9 supports; recording a
    #                 diagnosis in somebody else's name is a business decision
    #                 nobody has made.
    # `status`        finalising happens by publishing a quote, not by asking.
    # `revision`      allocated server-side, under a lock.


class V1ServiceQuoteWriteSerializer(serializers.Serializer):
    """Compose or edit a DRAFT quote's header."""

    diagnostic_id = serializers.IntegerField(required=False, allow_null=True)
    valid_until = serializers.DateTimeField(required=False, allow_null=True)
    customer_notes = serializers.CharField(
        max_length=2000, required=False, allow_blank=True,
    )
    internal_notes = serializers.CharField(
        max_length=2000, required=False, allow_blank=True,
    )
    discount_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal('0.00'), required=False,
    )

    # ── Absent on purpose, and this list is the contract ─────────────────────
    # `revision`   server-side, under a lock.
    # `currency`   frozen from the company's settings. A currency chosen by the
    #              caller is a price in a unit nobody agreed to.
    # `subtotal`, `tax_amount`, `total`  the server's arithmetic. A client that
    #              could post its own total could post one its own lines do not
    #              add up to.
    # `status`, `sent_at`, `approved_at`, `rejected_at`  outcomes, not inputs.


class V1ServiceQuoteItemWriteSerializer(serializers.Serializer):
    """One line. `line_total` is computed, never sent."""

    item_type = serializers.ChoiceField(choices=RepairQuoteItem.TYPE_CHOICES)
    description = serializers.CharField(max_length=300, trim_whitespace=True)
    quantity = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal('0.01'),
    )
    unit_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal('0.00'),
    )
    product_id = serializers.IntegerField(required=False, allow_null=True)
    sort_order = serializers.IntegerField(required=False, min_value=0, max_value=32767)

    # ── Absent on purpose ────────────────────────────────────────────────────
    # `line_total`  quantity × unit_price, computed by the server. Accepting it
    #               would let a line say one thing and cost another.


# ---------------------------------------------------------------------------
# CUSTOMER
# ---------------------------------------------------------------------------

class V1CustomerQuoteItemSerializer(serializers.ModelSerializer):
    """One line, as the person paying for it needs to read it."""

    item_type_label = serializers.CharField(source='get_item_type_display', read_only=True)

    class Meta:
        model = RepairQuoteItem
        fields = (
            'id', 'item_type', 'item_type_label', 'description',
            'quantity', 'unit_price', 'line_total',
        )
        read_only_fields = fields

    # ── Absent on purpose ────────────────────────────────────────────────────
    # `product`  an internal catalogue id. A customer reading their quote has no
    #            use for it, and it is a handle into a catalogue they cannot see.
    # `sort_order`  presentation state of the internal editor.


class V1CustomerQuoteSerializer(serializers.ModelSerializer):
    """
    The quote as the customer sees it.

    A SEPARATE CLASS from the internal one, not a mode of it. The temptation is
    a single serializer with `if staff`; that is one refactor away from putting
    a technician's private note in front of a client, and the failure is silent.
    """

    status_label = serializers.CharField(source='get_status_display', read_only=True)
    items = V1CustomerQuoteItemSerializer(many=True, read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    can_be_decided = serializers.BooleanField(read_only=True)
    decision = serializers.SerializerMethodField()

    class Meta:
        model = RepairQuote
        fields = (
            'id', 'revision', 'status', 'status_label',
            'currency', 'subtotal', 'discount_amount', 'tax_amount', 'total',
            'valid_until', 'is_expired', 'can_be_decided',
            'customer_notes', 'items', 'decision', 'sent_at',
        )
        read_only_fields = fields

    def get_decision(self, obj):
        """
        Their own answer, echoed back so the app can render a settled quote.

        Only the answer and when — not the reason. The customer typed the reason
        and does not need it read back; leaving it out means no future change to
        this contract can start showing one person's words to another.
        """
        record = getattr(obj, 'decision', None)
        if record is None:
            return None
        return {'decision': record.decision, 'decided_at': record.decided_at}

    # ── Absent on purpose, and every omission is a decision ──────────────────
    # `internal_notes`     where the shop writes what it would not say aloud.
    # `diagnostic`         the diagnosis carries `internal_notes` and a staff
    #                      identity; the customer gets the RESULT, which is the
    #                      quote, not the working notes behind it.
    # `created_by_name`    a staff identity.
    # `is_editable`        internal editor state.
    # `cancelled_at`       a cancelled quote is not shown to a customer at all.
    # `repair_order`, `company`  both are in the URL that reached this row.


class V1CustomerQuoteDecisionSerializer(serializers.Serializer):
    """
    The customer's answer. Two fields, and one of them is optional.

    Everything else about the decision — who made it, for which customer, in
    which company, through which channel, at what total, from which IP — the
    server already knows, and a client that could state any of them could state
    a better-looking version of what happened.
    """

    decision = serializers.ChoiceField(
        choices=RepairQuoteDecision.DECISION_CHOICES,
    )
    reason = serializers.CharField(
        max_length=1000, required=False, allow_blank=True,
    )

    # ── Absent on purpose ────────────────────────────────────────────────────
    # `customer_id`, `company_id`, `user_id`  resolved from the session.
    # `amount`, `quoted_total`, `currency`    read from the frozen quote.
    # `status`, `approved_at`, `decided_at`   outcomes, not inputs.
    # `channel`                               `customer_account`, decided by the
    #                                         surface being used. A future
    #                                         endpoint may record "approved by
    #                                         phone"; it will have its own
    #                                         authority, not a string in a body.


# ---------------------------------------------------------------------------
# M10 / BR-005C — execution and parts. INTERNAL ONLY.
# ---------------------------------------------------------------------------
#
# There is deliberately no customer counterpart to anything below. A customer
# learns that their device is `in_repair` from the status and its tenant label,
# which they already receive; they do not learn which battery went in, what it
# cost the shop, which shelf it came off or who fitted it. The approved quote
# is what they were told and what they agreed to, and M9 already shows them
# that.
#
# The rule is structural, not a habit: `V1CustomerRepairDetailSerializer` is a
# closed allowlist with `read_only_fields = fields`, and nothing here is added
# to it.


class V1ServicePartUsageSerializer(serializers.ModelSerializer):
    """One part booked against a repair, and its reversal if it has one."""

    product_id = serializers.IntegerField(read_only=True)
    quote_item_id = serializers.IntegerField(read_only=True)
    stock_movement_id = serializers.IntegerField(read_only=True)
    actor_name = serializers.SerializerMethodField()
    reversed_by_name = serializers.SerializerMethodField()
    is_reversed = serializers.BooleanField(read_only=True)

    class Meta:
        model = PartUsage
        fields = (
            'id', 'quote_item_id', 'product_id', 'description', 'quantity',
            'stock_movement_id', 'actor_name', 'created_at',
            'is_reversed', 'reversed_at', 'reversed_by_name', 'reversal_reason',
        )
        read_only_fields = fields

    # `branch` is absent because there is only ever one answer — the order's —
    # and a field that can only hold one value invites a client to send it.
    # `company` likewise. `idempotency_key` and `request_fingerprint` are the
    # caller's own bookkeeping and echoing them back serves nothing.

    def get_actor_name(self, obj):
        user = obj.actor
        return user.get_full_name() or user.username if user else ''

    def get_reversed_by_name(self, obj):
        user = obj.reversed_by
        return user.get_full_name() or user.username if user else ''


class V1ServiceExecutionSerializer(serializers.ModelSerializer):
    """The bench record, with the parts it consumed."""

    parts = V1ServicePartUsageSerializer(source='part_usages', many=True, read_only=True)
    result_label = serializers.SerializerMethodField()
    started_by_name = serializers.SerializerMethodField()
    completed_by_name = serializers.SerializerMethodField()
    is_completed = serializers.BooleanField(read_only=True)

    class Meta:
        model = RepairExecution
        fields = (
            'id', 'started_at', 'completed_at', 'is_completed',
            'work_performed', 'result', 'result_label', 'internal_notes',
            'started_by_name', 'completed_by_name', 'parts',
            'created_at', 'updated_at',
        )
        read_only_fields = fields

    def get_result_label(self, obj):
        return obj.get_result_display() if obj.result else ''

    def get_started_by_name(self, obj):
        user = obj.started_by
        return user.get_full_name() or user.username if user else ''

    def get_completed_by_name(self, obj):
        user = obj.completed_by
        return user.get_full_name() or user.username if user else ''


class V1ServiceExecutionWriteSerializer(serializers.Serializer):
    """
    What a technician may change on an OPEN execution. Three fields.

    Absent on purpose: `started_at`, `started_by`, `completed_at`,
    `completed_by`, `company`, `repair_order`, `status`. The server knows when
    work began, who is calling, and what the order's state is; a bench clock
    somebody can set is not evidence, and having a field is being able to fill
    it in.
    """

    work_performed = serializers.CharField(
        max_length=4000, required=False, allow_blank=True,
    )
    result = serializers.ChoiceField(
        choices=RepairResultCode.choices, required=False, allow_blank=True,
    )
    internal_notes = serializers.CharField(
        max_length=2000, required=False, allow_blank=True,
    )


class V1ServiceExecutionCompleteSerializer(serializers.Serializer):
    """
    Finishing. Optional overrides for what was already typed, and nothing else.

    `result` is required here even though it is optional on the draft: a repair
    that ended has an outcome, and an unfinished one does not need to pretend.
    """

    work_performed = serializers.CharField(
        max_length=4000, required=False, allow_blank=False,
    )
    result = serializers.ChoiceField(choices=RepairResultCode.choices)
    internal_notes = serializers.CharField(
        max_length=2000, required=False, allow_blank=True,
    )


class V1ServicePausePartsSerializer(serializers.Serializer):
    """Why the bench stopped. One optional line, for the timeline."""

    comment = serializers.CharField(
        max_length=1000, required=False, allow_blank=True,
    )


class V1ServicePartUsageWriteSerializer(serializers.Serializer):
    """
    Consuming a part: WHICH APPROVED LINE, and HOW MANY. Nothing else.

    Absent on purpose and each for its own reason:

    · `branch_id` — the branch is the order's. There is no transfer in this
      flow, so consuming another shop's stock would move units on paper that
      nobody carried.
    · `product_id` — the product is the quoted line's. Accepting one would let
      a caller book a part the customer never approved against a line they did.
    · `unit_price`, `unit_cost`, `total` — the money conversation happened once,
      on the quote, and this does not reopen it.
    · `stock_before`, `stock_after`, `movement_type` — the inventory module
      computes those; a client that could state them could state a shelf that
      does not exist.
    · `company_id`, `actor` — the server knows who is calling.

    `idempotency_key` IS accepted, because only the client can mint it: it has
    to survive the client's own retry, and a key the server generates is a key
    that changes on every attempt.
    """

    quote_item_id = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1)
    idempotency_key = serializers.CharField(
        max_length=64, required=False, allow_blank=True,
    )


class V1ServicePartReversalSerializer(serializers.Serializer):
    """Undoing a consumption. A reason, for whoever reads the Kardex later."""

    reason = serializers.CharField(
        max_length=300, required=False, allow_blank=True,
    )


class V1ServicePartCandidateSerializer(serializers.Serializer):
    """
    A part this repair may still consume. NOT an inventory row.

    It carries the approved line, how much of it is left, and how many the
    order's own branch holds. It does not carry cost, other branches, the
    Kardex, or anything about the catalogue the customer was not already shown
    — which is why this surface needs `service.repair.manage` and never
    `inventory.view`.
    """

    quote_item_id = serializers.IntegerField(read_only=True)
    product_id = serializers.IntegerField(read_only=True)
    description = serializers.CharField(read_only=True)
    approved_quantity = serializers.IntegerField(read_only=True)
    used_quantity = serializers.IntegerField(read_only=True)
    outstanding_quantity = serializers.IntegerField(read_only=True)
    available_in_branch = serializers.IntegerField(read_only=True)


# ---------------------------------------------------------------------------
# M11 / BR-005D — quality control. INTERNAL ONLY.
# ---------------------------------------------------------------------------
#
# There is no customer counterpart to any of this, and the omission is
# structural. A customer sees that their device is in control de calidad and,
# later, that it is listo para recoger — both through the ordinary status and
# the tenant's own label. They do not see which points were tested, which one
# failed, what the technician wrote about it, or who ran the inspection.
#
# "Falló la cámara frontal" is a note between a shop and itself. Publishing it
# would turn every rework into an argument.


class V1ServiceQualityItemSerializer(serializers.ModelSerializer):
    """One point of the snapshot, and how it came out."""

    class Meta:
        model = QualityCheckItem
        fields = (
            'id', 'code', 'label', 'is_required', 'result', 'notes', 'sort_order',
        )
        read_only_fields = fields


class V1ServiceQualityCheckSerializer(serializers.ModelSerializer):
    """An inspection, with the list exactly as it was when it opened."""

    items = V1ServiceQualityItemSerializer(many=True, read_only=True)
    status_label = serializers.SerializerMethodField()
    checked_by_name = serializers.SerializerMethodField()
    completed_by_name = serializers.SerializerMethodField()
    execution_id = serializers.IntegerField(read_only=True)
    is_open = serializers.BooleanField(read_only=True)

    class Meta:
        model = QualityCheck
        fields = (
            'id', 'status', 'status_label', 'is_open', 'template_name',
            'notes', 'checked_by_name', 'completed_by_name', 'execution_id',
            'started_at', 'completed_at', 'items',
        )
        read_only_fields = fields

    # `template` (the FK) is absent: the snapshot IS the record, and handing a
    # client the template id invites it to render an old inspection through
    # today's list.

    def get_status_label(self, obj):
        return obj.get_status_display()

    def get_checked_by_name(self, obj):
        user = obj.checked_by
        return user.get_full_name() or user.username if user else ''

    def get_completed_by_name(self, obj):
        user = obj.completed_by
        return user.get_full_name() or user.username if user else ''


class V1ServiceQualityResultSerializer(serializers.Serializer):
    """
    Answering ONE point. A result and, optionally, why.

    Absent on purpose: the check's `status`. The server computes whether an
    inspection can pass by reading its items — a checklist whose verdict could
    be sent by whoever filled it in is a checklist that proves nothing.
    """

    result = serializers.ChoiceField(choices=QualityResultCode.choices)
    notes = serializers.CharField(max_length=300, required=False, allow_blank=True)


class V1ServiceQualityDecisionSerializer(serializers.Serializer):
    """Closing an inspection. One optional internal note, and nothing else."""

    notes = serializers.CharField(max_length=2000, required=False, allow_blank=True)


# ---------------------------------------------------------------------------
# M12 / BR-005E — the handover. INTERNAL ONLY.
# ---------------------------------------------------------------------------
#
# The customer sees that their device was `delivered`, through the ordinary
# status and their tenant's label. They do not see who released it, what the
# counter wrote in the internal note, or the name the counter recorded — which
# is often their own, and just as often a relative's or a courier's. None of
# that is theirs to read back off a screen.


class V1ServiceDeliverySerializer(serializers.ModelSerializer):
    """A handover, as the internal surface reads it."""

    delivered_by_name = serializers.SerializerMethodField()

    class Meta:
        model = RepairDelivery
        fields = (
            'id', 'recipient_name', 'notes', 'delivered_by_name',
            'delivered_at', 'created_at',
        )
        read_only_fields = fields

    # `idempotency_key` and `request_fingerprint` are absent: they are the
    # caller's own bookkeeping and echoing them back serves nothing.

    def get_delivered_by_name(self, obj):
        user = obj.delivered_by
        return user.get_full_name() or user.username if user else ''


class V1ServiceDeliveryWriteSerializer(serializers.Serializer):
    """
    Recording a handover: WHO TOOK IT, and optionally a note.

    Absent on purpose, each for its own reason:

    · `delivered_by`, `delivered_at`, `company`, `repair_order`, `status` — the
      server knows who is calling, what time it is, and what the order is. A
      counter clock somebody can set is not a record.
    · Any identity document. The platform cannot verify one, and storing
      personal data to support a claim this software cannot make is not a
      trade worth taking. See DEC-MOBILE-027 and the model docstring.
    · `conformity` / signature / photo. DEC-016 has no storage provider, and a
      flag with nothing behind it asserts consent nobody captured.
    · Anything about payment. This platform cannot charge for a repair.

    `idempotency_key` IS accepted, because only the client can mint one that
    survives the client's own retry.
    """

    recipient_name = serializers.CharField(max_length=160)
    notes = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    idempotency_key = serializers.CharField(
        max_length=64, required=False, allow_blank=True,
    )


# ---------------------------------------------------------------------------
# M12B / BR-005F — the payment ledger
# ---------------------------------------------------------------------------

class V1ServicePaymentSerializer(serializers.ModelSerializer):
    """
    One payment, as the internal surface reads it.

    AN ALLOWLIST, and the omissions are the point. `idempotency_key` and
    `request_fingerprint` are the caller's own bookkeeping. There is no provider
    payload, no authorization code, no gateway id and no card data anywhere in
    this model — a manual payment is a counter reporting what it received, and
    the online flow that would produce those fields does not exist yet.
    """

    received_by_name = serializers.SerializerMethodField()
    reversed_by_name = serializers.SerializerMethodField()
    is_reversed = serializers.BooleanField(read_only=True)

    class Meta:
        model = RepairPayment
        fields = (
            'id', 'amount', 'currency', 'method', 'reference', 'notes',
            'received_by_name', 'received_at', 'created_at',
            'is_reversed', 'reversed_at', 'reversed_by_name', 'reversal_reason',
        )
        read_only_fields = fields

    def get_received_by_name(self, obj):
        user = obj.received_by
        return user.get_full_name() or user.username if user else ''

    def get_reversed_by_name(self, obj):
        user = obj.reversed_by
        return user.get_full_name() or user.username if user else ''


class V1ServicePaymentWriteSerializer(serializers.Serializer):
    """
    Recording money: WHAT ARRIVED and HOW. Nothing else.

    No currency — it comes from the approved quote, and a client that could
    choose one could record a payment against a debt in another. No clock, no
    cashier, no company, no order: all server-owned. No status and no balance:
    those are arithmetic the server does over the rows.
    """

    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    method = serializers.ChoiceField(choices=PaymentMethod.choices)
    reference = serializers.CharField(
        max_length=120, required=False, allow_blank=True, default='',
    )
    notes = serializers.CharField(
        max_length=1000, required=False, allow_blank=True, default='',
    )
    idempotency_key = serializers.CharField(
        max_length=64, required=False, allow_blank=True, default='',
    )


class V1ServicePaymentReversalSerializer(serializers.Serializer):
    """Undoing a payment: WHY, and nothing else. Never an amount."""

    reason = serializers.CharField(
        max_length=300, required=False, allow_blank=True, default='',
    )


class V1ServicePaymentSummarySerializer(serializers.Serializer):
    """
    The balance, computed by the server and never sent by a client.

    `quoted_total` and `outstanding` are NULLABLE, and null is not zero: it
    means the shop has not agreed a price yet. Rendering that as 0.00 would tell
    a customer the repair is free.
    """

    currency = serializers.CharField()
    quoted_total = serializers.DecimalField(
        max_digits=12, decimal_places=2, allow_null=True,
    )
    confirmed_paid = serializers.DecimalField(max_digits=12, decimal_places=2)
    outstanding = serializers.DecimalField(
        max_digits=12, decimal_places=2, allow_null=True,
    )
    credit = serializers.DecimalField(max_digits=12, decimal_places=2)
    payment_status = serializers.CharField()
    #: The tenant's own policy, so a screen can explain a refusal it has not hit
    #: yet. It is a PREVIEW: the server re-checks inside the delivery
    #: transaction, and if the two ever disagree the server wins.
    requires_payment_before_delivery = serializers.BooleanField()


class V1CustomerPaymentSummarySerializer(serializers.Serializer):
    """
    What the CUSTOMER is told about the money. FIVE FIELDS, and it does not
    inherit from the internal one.

    Not inheriting is deliberate. A customer serializer that extends a
    backoffice one is a serializer that leaks the next field somebody adds
    upstream — the customer surface has been an explicit allowlist since M8 and
    stays one here.

    A customer may know what they agreed to, what they have paid and what is
    left: it is their own money and withholding it would be indefensible.

    They are NOT told who took the payment, by what means, against which
    voucher, when the till recorded it, or that a payment was reversed. A
    reversal is the shop correcting its own books; publishing it turns an
    internal correction into an accusation, and their balance already reflects
    it. `credit` is absent for the same reason: an overpayment is a
    conversation to have at a counter, not a number to surprise somebody with
    in an app — so the app says `paid`, which is true, and the shop raises it.
    """

    currency = serializers.CharField()
    quoted_total = serializers.DecimalField(
        max_digits=12, decimal_places=2, allow_null=True,
    )
    paid = serializers.DecimalField(max_digits=12, decimal_places=2)
    outstanding = serializers.DecimalField(
        max_digits=12, decimal_places=2, allow_null=True,
    )
    status = serializers.CharField()
