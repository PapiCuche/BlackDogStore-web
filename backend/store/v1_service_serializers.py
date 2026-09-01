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
from rest_framework import serializers

from .models import (
    Device,
    RepairOrder,
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
