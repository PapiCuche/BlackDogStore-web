"""
BR-005A — the technical-service domain. ONE rule, one place.

WHAT LIVES HERE
---------------
Everything that decides what happens to a repair order: how it is created, how
it is numbered, which states it may move between, what that movement records,
and who is responsible for it. The v1 views establish WHO is asking and WHICH
company; they then call this module and render what it returns.

AUTHORITY IS NOT CHECKED HERE
-----------------------------
Same contract as `inventory_services` and `order_fulfillment_services`: every
function below assumes the caller has already established that the actor belongs
to the company and holds the capability the action needs, and that any branch,
customer or device it was handed belongs to that company. This module enforces
the DOMAIN's rules — the lifecycle, the history, the lock — not the tenant's.

The invariants it does re-check are the ones a caller cannot be trusted to have
checked, because getting them wrong corrupts data rather than leaking it: that a
device belongs to its customer, that a technician is staff of the company, that a
transition is legal.

THE HISTORY IS THE RECORD
-------------------------
`RepairOrder.status` is a projection kept for cheap listing. `RepairStatusHistory`
is the evidence, it is append-only, and both are written in the same transaction.
Nothing outside this module writes either one.
"""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from . import sequences
from .models import (
    AdminAuditLog,
    Customer,
    Device,
    Membership,
    RepairOrder,
    RepairStatusCode,
    RepairStatusHistory,
    RepairStatusSetting,
    TechnicianAssignment,
)


class ServiceError(Exception):
    """A service-domain rule was broken. Views render this as HTTP 400."""


class InvalidTransitionError(ServiceError):
    """The requested lifecycle move is not one this state allows."""


class TechnicianNotEligibleError(ServiceError):
    """The proposed technician is not active staff of this company."""


# ---------------------------------------------------------------------------
# The lifecycle
# ---------------------------------------------------------------------------

#: The state machine. ONE definition, on the server, and the client is told what
#: it may do rather than deciding for itself — the same rule M6 established for
#: order fulfilment. A transition map duplicated in TypeScript drifts the first
#: time this one changes, and the drift shows up as a button that fails, which
#: reads as a broken app rather than as a policy.
#:
#: M8 stops at WAITING_APPROVAL on purpose. Everything past it needs a quote to
#: approve, and a quote needs a diagnosis. Offering APPROVED today would let an
#: order enter a state no code can act on.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    RepairStatusCode.RECEIVED: (
        RepairStatusCode.DIAGNOSING,
        RepairStatusCode.CANCELLED,
    ),
    RepairStatusCode.DIAGNOSING: (
        RepairStatusCode.WAITING_APPROVAL,
        RepairStatusCode.CANCELLED,
    ),
    RepairStatusCode.WAITING_APPROVAL: (
        RepairStatusCode.CANCELLED,
    ),
    # Terminal in M8. A cancelled order is not reopened; the device comes back
    # with a new order, which is also how the shop actually works.
    RepairStatusCode.CANCELLED: (),
}

#: States that end an order's life. Reaching one stamps `closed_at`.
TERMINAL_STATES: frozenset[str] = frozenset({RepairStatusCode.CANCELLED})

#: The initial state. Not a parameter anywhere: an order is born when a device
#: is received, and there is no other way for one to begin.
INITIAL_STATE: str = RepairStatusCode.RECEIVED


def available_transitions(repair_order) -> list[str]:
    """The states this order may move to right now. The server's answer."""
    return list(TRANSITIONS.get(repair_order.status, ()))


def is_transition_allowed(from_status: str, to_status: str) -> bool:
    return to_status in TRANSITIONS.get(from_status, ())


# ---------------------------------------------------------------------------
# Per-company presentation
# ---------------------------------------------------------------------------

def status_settings(company) -> dict[str, RepairStatusSetting]:
    """
    This company's presentation of every lifecycle code, keyed by code.

    A READ, and it creates nothing. Seeding belongs to provisioning and to the
    data migration; a read path that fills in what is missing destroys the
    ability to answer "was this company provisioned?" — the rule
    `company_settings.py` states for exactly the same reason.
    """
    return {
        setting.code: setting
        for setting in RepairStatusSetting.objects.filter(company=company)
    }


def status_label(company, code: str, settings_by_code=None) -> str:
    """
    What `company` calls `code`, falling back to the platform's own wording.

    The fallback is not a shrug: a company provisioned before its statuses were
    seeded must still see a legible screen, and `RepairStatusCode.labels` is the
    same Spanish the rest of the platform uses. What it must never do is invent
    a NEW meaning — the code is unchanged, only the word is.
    """
    settings_by_code = settings_by_code if settings_by_code is not None else status_settings(company)
    setting = settings_by_code.get(code)
    if setting is not None and setting.label:
        return setting.label
    return dict(RepairStatusCode.choices).get(code, code)


def is_status_customer_visible(company, code: str, settings_by_code=None) -> bool:
    """
    Whether an event ARRIVING at `code` is shown to the customer by default.

    Absent configuration means visible. A customer who is told nothing about
    their own device is worse served than one who sees a state the shop would
    rather have hidden, and the shop can hide it explicitly.
    """
    settings_by_code = settings_by_code if settings_by_code is not None else status_settings(company)
    setting = settings_by_code.get(code)
    return True if setting is None else setting.is_customer_visible


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------

def find_possible_duplicate_devices(company, *, serial_number='', imei='', exclude_pk=None):
    """
    Devices of `company` that may be the same object, by serial or IMEI.

    A DETECTOR, NOT A CONSTRAINT, and that is the decision. A unique index on
    serial numbers looks obviously right and is wrong here in four different
    ways: the same device legitimately returns to the shop and is registered
    once, so uniqueness must not block re-use; serials are transcribed by hand
    from a sticker under a battery and are mistyped; many devices have no
    readable serial at all, so blank must be free to repeat; and two tenants
    may each hold the same second-hand phone, which a global constraint would
    forbid outright.

    So the database allows it and the operator is warned. If evidence later
    shows duplicates are a real problem for a real shop, a tenant-scoped
    partial constraint can be added then, with data to justify its shape.
    """
    serial = (serial_number or '').strip().upper()
    imei_value = (imei or '').strip()
    if not serial and not imei_value:
        return Device.objects.none()

    queryset = Device.objects.filter(company=company)
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)

    from django.db.models import Q

    criteria = Q(pk__in=[])
    if serial:
        criteria |= Q(serial_number=serial)
    if imei_value:
        criteria |= Q(imei=imei_value)

    return queryset.filter(criteria).select_related('customer')[:5]


@transaction.atomic
def create_device(*, company, customer, actor=None, request=None, **fields) -> Device:
    """
    Register a device for a customer of THIS company.

    The customer is passed as an object the caller already resolved inside the
    tenant; this function re-checks the relationship anyway, because a device
    filed under the wrong client is handed to the wrong person later.
    """
    if customer.company_id != company.pk:
        raise ServiceError('El cliente no pertenece a esta empresa.')

    device = Device(company=company, customer=customer, created_by=actor, **fields)
    device.save()

    AdminAuditLog.log(
        actor=actor,
        action='service_device_created',
        target_type='device',
        target_id=device.pk,
        metadata={
            'customer_id': customer.pk,
            'device_type': device.device_type,
            'brand': device.brand,
            'model': device.model,
        },
        request=request,
        company=company,
    )
    return device


# ---------------------------------------------------------------------------
# Repair orders
# ---------------------------------------------------------------------------

@transaction.atomic
def create_repair_order(
    *,
    company,
    branch,
    customer,
    device,
    reported_issue: str,
    physical_condition: str = '',
    received_accessories: str = '',
    internal_notes: str = '',
    actor=None,
    request=None,
) -> RepairOrder:
    """
    Receive a device into the workshop and open its order.

    WHAT THE SERVER DECIDES, whatever the client sent: the company, the number,
    the ordinal, the initial state, who received it and when. None of those is a
    parameter of the API payload, and the serializer has no field for any of
    them.

    THE FIRST HISTORY EVENT IS WRITTEN HERE, in the same transaction. An order
    whose timeline began at its first change would be missing the only event
    everybody agrees happened: the device arrived.

    LOCK ORDER. The sequence row is the only thing locked, because the order
    does not exist yet to be locked. That is consistent with the platform's
    stated discipline — document first, sequence second — since there is no
    document row to take first.
    """
    if branch.company_id != company.pk:
        raise ServiceError('La sucursal no pertenece a esta empresa.')
    if customer.company_id != company.pk:
        raise ServiceError('El cliente no pertenece a esta empresa.')
    if device.company_id != company.pk:
        raise ServiceError('El equipo no pertenece a esta empresa.')
    if device.customer_id != customer.pk:
        raise ServiceError('El equipo no pertenece a este cliente.')

    order = RepairOrder(
        company=company,
        branch=branch,
        customer=customer,
        device=device,
        number='',
        sequence_value=0,
        status=INITIAL_STATE,
        reported_issue=reported_issue,
        physical_condition=physical_condition,
        received_accessories=received_accessories,
        internal_notes=internal_notes,
        received_by=actor,
        received_at=timezone.now(),
    )

    sequence = sequences.resolve_sequence_for_repair_order(order)
    value, formatted = sequences.allocate(sequence)
    order.sequence_value = value
    order.number = formatted
    order.save()

    _append_history(
        order,
        from_status='',
        to_status=INITIAL_STATE,
        actor=actor,
        origin=RepairStatusHistory.ORIGIN_INTERNAL,
        comment='',
    )

    AdminAuditLog.log(
        actor=actor,
        action='service_order_created',
        target_type='repair_order',
        target_id=order.pk,
        metadata={
            'number': order.number,
            'branch_id': branch.pk,
            'customer_id': customer.pk,
            'device_id': device.pk,
            'status': order.status,
        },
        request=request,
        company=company,
    )
    return order


@transaction.atomic
def transition_repair_order(
    *,
    repair_order,
    to_status: str,
    actor=None,
    comment: str = '',
    origin: str = RepairStatusHistory.ORIGIN_INTERNAL,
    request=None,
) -> RepairOrder:
    """
    Move an order to `to_status`, recording why.

    THE ROW IS LOCKED FIRST, and that is the whole point of the function. Two
    technicians looking at the same order both see "en diagnóstico" and both
    press a button; without the lock, both read the same `from_status`, both
    append an event claiming to start from it, and the history ends up asserting
    two different things happened from one state. With it, the second one
    re-reads the winner's state and is told its move is no longer legal.

    The projection and the evidence are written together or not at all.
    """
    locked = RepairOrder.objects.select_for_update().get(pk=repair_order.pk)

    from_status = locked.status
    if to_status == from_status:
        # Explicit, rather than a silent no-op. A no-op would write a history
        # event saying nothing changed, and an exception at least tells the
        # operator their screen was stale.
        raise InvalidTransitionError('La orden ya está en ese estado.')

    if not is_transition_allowed(from_status, to_status):
        raise InvalidTransitionError('Ese cambio de estado no está permitido.')

    locked.status = to_status
    update_fields = ['status', 'updated_at']
    if to_status in TERMINAL_STATES:
        locked.closed_at = timezone.now()
        update_fields.append('closed_at')
    locked.save(update_fields=update_fields)

    _append_history(
        locked,
        from_status=from_status,
        to_status=to_status,
        actor=actor,
        origin=origin,
        comment=comment,
    )

    AdminAuditLog.log(
        actor=actor,
        action='service_order_status_changed',
        target_type='repair_order',
        target_id=locked.pk,
        metadata={
            'number': locked.number,
            'from_status': from_status,
            'to_status': to_status,
            'origin': origin,
        },
        request=request,
        company=locked.company,
    )
    return locked


def _append_history(order, *, from_status, to_status, actor, origin, comment):
    """The only writer of `RepairStatusHistory`. Called inside a transaction."""
    return RepairStatusHistory.objects.create(
        repair_order=order,
        company_id=order.company_id,
        from_status=from_status,
        to_status=to_status,
        actor=actor,
        origin=origin,
        comment=comment or '',
        # Frozen at write time. A company that changes its visibility policy
        # tomorrow must not retroactively reveal — or hide — what a customer
        # was already shown.
        is_customer_visible=is_status_customer_visible(order.company, to_status),
    )


def customer_visible_history(repair_order):
    """The events a customer may see. Filtered on the SERVER, always."""
    return repair_order.status_history.filter(is_customer_visible=True).order_by(
        'created_at', 'pk',
    )


# ---------------------------------------------------------------------------
# Technicians
# ---------------------------------------------------------------------------

def eligible_technicians(company):
    """
    Who may be assigned work in `company`.

    ACTIVE STAFF OF THIS COMPANY, and nothing weaker. Not "users the app
    knows", not "people with a technician-sounding role" — `UserProfile.role`
    is a legacy label and has never been authority. Membership is the fact that
    somebody works here; this queryset is the only definition the module uses,
    and the assignment endpoint offers exactly these people.
    """
    from django.contrib.auth import get_user_model

    User = get_user_model()
    return User.objects.filter(
        memberships__company=company,
        memberships__is_active=True,
        is_active=True,
    ).distinct().order_by('first_name', 'last_name', 'username')


@transaction.atomic
def assign_technician(*, repair_order, technician, actor=None, request=None) -> TechnicianAssignment:
    """
    Make `technician` responsible for the order, closing whoever had it.

    REASSIGNMENT IS TWO ROWS, never an edit. The previous assignment is stamped
    `unassigned_at` and stays; the new one is inserted. Editing the old row in
    place would answer "who has it" and destroy "who had it", which is the
    question that matters when work was done badly.

    Cross-tenant assignment is refused here rather than at the view, because it
    is a data-integrity rule: an order carrying a technician who does not work
    at the company is corrupt regardless of who asked for it.
    """
    locked = RepairOrder.objects.select_for_update().get(pk=repair_order.pk)

    is_staff_here = Membership.objects.filter(
        user=technician, company=locked.company, is_active=True,
    ).exists()
    if not is_staff_here or not technician.is_active:
        raise TechnicianNotEligibleError(
            'Esa persona no forma parte del personal activo de esta empresa.'
        )

    current = locked.assignments.filter(unassigned_at__isnull=True).first()
    if current is not None:
        if current.technician_id == technician.pk:
            return current
        current.unassigned_at = timezone.now()
        current.save(update_fields=['unassigned_at'])

    assignment = TechnicianAssignment.objects.create(
        repair_order=locked,
        company_id=locked.company_id,
        technician=technician,
        assigned_by=actor,
        assigned_at=timezone.now(),
    )

    AdminAuditLog.log(
        actor=actor,
        action='service_order_technician_assigned',
        target_type='repair_order',
        target_id=locked.pk,
        metadata={'number': locked.number, 'technician_id': technician.pk},
        request=request,
        company=locked.company,
    )
    return assignment


@transaction.atomic
def unassign_technician(*, repair_order, actor=None, request=None):
    """Release the order. Returns the closed assignment, or None if there was none."""
    locked = RepairOrder.objects.select_for_update().get(pk=repair_order.pk)

    current = locked.assignments.filter(unassigned_at__isnull=True).first()
    if current is None:
        return None

    current.unassigned_at = timezone.now()
    current.save(update_fields=['unassigned_at'])

    AdminAuditLog.log(
        actor=actor,
        action='service_order_technician_unassigned',
        target_type='repair_order',
        target_id=locked.pk,
        metadata={'number': locked.number, 'technician_id': current.technician_id},
        request=request,
        company=locked.company,
    )
    return current


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------

def customer_owned_repair_orders(user, company):
    """
    The repair orders a signed-in CUSTOMER may read. Ownership by FK, never email.

    The sibling of `tenancy.customer_owned_orders`, and it is deliberately
    narrower: a repair order always has a `Customer`, so there is no snapshot
    field to fall back on and no reason to invent one. An email match would hand
    a household's devices to whoever typed the address at the counter.
    """
    if user is None or not user.is_authenticated:
        return RepairOrder.objects.none()

    customer_ids = Customer.objects.filter(
        company=company, user=user,
    ).values_list('pk', flat=True)

    return RepairOrder.objects.filter(
        company=company, customer_id__in=list(customer_ids),
    )
