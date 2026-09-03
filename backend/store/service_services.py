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

import hashlib
import json
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Max, Sum
from django.utils import timezone

from . import inventory_services, sequences
from .models import (
    AdminAuditLog,
    BranchStock,
    Notification,
    CompanySettings,
    Customer,
    Device,
    Membership,
    PartUsage,
    Product,
    QualityCheck,
    RepairDelivery,
    QualityCheckItem,
    QualityChecklistTemplate,
    QualityCheckStatus,
    QualityResultCode,
    RepairDiagnostic,
    RepairExecution,
    RepairOrder,
    RepairQuote,
    RepairQuoteDecision,
    RepairQuoteItem,
    RepairResultCode,
    RepairStatusCode,
    RepairStatusHistory,
    RepairStatusSetting,
    StockMovement,
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
        RepairStatusCode.APPROVED,
        RepairStatusCode.REJECTED,
        # Withdrawing the quote puts the order back where a new revision is
        # composed. Event-only — see `EVENT_ONLY_EDGES`.
        RepairStatusCode.DIAGNOSING,
        RepairStatusCode.CANCELLED,
    ),
    # M9 said go ahead; M10 built the bench. An approved order moves on when a
    # technician STARTS, which is an event, not a selector — see
    # `EVENT_ONLY_STATES`. It can still be cancelled: a customer changes their
    # mind, a part turns out to be unobtainable, and that is a decision the shop
    # records rather than a state it is trapped in.
    RepairStatusCode.APPROVED: (
        RepairStatusCode.IN_REPAIR,
        RepairStatusCode.CANCELLED,
    ),
    # M10 — the bench. Work pauses when a part is missing and resumes when it
    # arrives; both are things somebody does, so both are event-only. Finishing
    # is `complete_repair`. Cancelling mid-repair is legitimate and generic: a
    # device turns out to be unrepairable and the shop says so.
    RepairStatusCode.IN_REPAIR: (
        RepairStatusCode.WAITING_PARTS,
        RepairStatusCode.REPAIRED,
        RepairStatusCode.CANCELLED,
    ),
    RepairStatusCode.WAITING_PARTS: (
        RepairStatusCode.IN_REPAIR,
        RepairStatusCode.CANCELLED,
    ),
    # M11 built the inspection. A finished repair goes to quality control, and
    # that is an event — opening a real checklist — not a dropdown.
    RepairStatusCode.REPAIRED: (
        RepairStatusCode.QUALITY_CONTROL,
        RepairStatusCode.CANCELLED,
    ),
    # Passing sends it forward; failing sends it back to the bench with a NEW
    # execution. Both are events, and both are the recorded outcome of somebody
    # having actually tested the device.
    RepairStatusCode.QUALITY_CONTROL: (
        RepairStatusCode.READY_FOR_PICKUP,
        RepairStatusCode.IN_REPAIR,
        RepairStatusCode.CANCELLED,
    ),
    # M12 built the handover. A device that passed its tests leaves with
    # somebody, and that is an event with a name attached — not a dropdown.
    RepairStatusCode.READY_FOR_PICKUP: (
        RepairStatusCode.DELIVERED,
        RepairStatusCode.CANCELLED,
    ),
    # Terminal. The device is gone. A warranty claim is a NEW order citing this
    # one, never an edit to it — the same shape M11 used for a rework, and the
    # reason nothing here is ever reopened.
    RepairStatusCode.DELIVERED: (),
    # A rejection is not the end of the conversation. The usual next move is a
    # second opinion and a cheaper quote, so the order goes BACK to diagnosis
    # and the rejected revision stays exactly where it is. Closing it outright
    # is the other legitimate answer, and both are the shop's to choose — which
    # is why neither happens automatically.
    RepairStatusCode.REJECTED: (
        RepairStatusCode.DIAGNOSING,
        RepairStatusCode.CANCELLED,
    ),
    # Terminal. A cancelled order is not reopened; the device comes back with a
    # new order, which is also how the shop actually works.
    RepairStatusCode.CANCELLED: (),
}

#: States that MAY NOT be reached by moving an order.
#:
#: This is the heart of M9. Before it, `waiting_approval` meant only that
#: somebody pressed a button; now it means "a frozen, published quote is waiting
#: for an answer", and the only way to produce that meaning is to publish one.
#: `approved` and `rejected` are likewise the recorded outcome of a customer
#: deciding — not something staff can assert on their behalf.
#:
#: The edges still exist in `TRANSITIONS`, because they are real edges of the
#: machine and the event operations travel along them. What this set removes is
#: the GENERIC path: `transition_repair_order` refuses them, and
#: `available_transitions` does not offer them, so the app cannot draw a button
#: that fabricates a customer's decision.
#: M10 adds three more, on the same principle. Starting work is a fact about a
#: workbench — somebody opened the device — and `start_repair` is what records
#: it, together with the execution row that gives `in_repair` its meaning.
#: Pausing for parts and finishing are the same kind of fact. A dropdown that
#: could assert any of them would let an order claim work that nobody did.
#: M11 adds two more, on the same principle. `quality_control` means a real
#: checklist is open against a specific completed execution; `ready_for_pickup`
#: means a technician ran that checklist and everything required came back
#: acceptable. Neither is a claim staff can make by choosing it from a list.
EVENT_ONLY_STATES: frozenset[str] = frozenset({
    RepairStatusCode.WAITING_APPROVAL,
    RepairStatusCode.APPROVED,
    RepairStatusCode.REJECTED,
    RepairStatusCode.IN_REPAIR,
    RepairStatusCode.WAITING_PARTS,
    RepairStatusCode.REPAIRED,
    RepairStatusCode.QUALITY_CONTROL,
    RepairStatusCode.READY_FOR_PICKUP,
    # M12. Handing a device over is a fact about a counter, recorded with the
    # name of whoever took it. A dropdown could assert it with nobody's name on
    # it at all.
    RepairStatusCode.DELIVERED,
})

#: Edges that exist for one operation only, even though their TARGET is an
#: ordinary state.
#:
#: Leaving `waiting_approval` for `diagnosing` is how a withdrawn quote returns
#: an order to the bench, and `cancel_quote` does both halves together. Offering
#: it as a plain button would let somebody move the order and leave a quote the
#: customer can still see and answer — an order in diagnosis with a live
#: proposal against it, which is exactly the inconsistency M9 exists to prevent.
EVENT_ONLY_EDGES: frozenset[tuple[str, str]] = frozenset({
    (RepairStatusCode.WAITING_APPROVAL, RepairStatusCode.DIAGNOSING),
})

#: States that end an order's life. Reaching one stamps `closed_at`.
#: M12 adds `DELIVERED`: the device left, and the order is finished. Warranty
#: will be a re-entry — a new order that cites this one — so closing this one
#: takes nothing away from it.
TERMINAL_STATES: frozenset[str] = frozenset({
    RepairStatusCode.CANCELLED,
    RepairStatusCode.DELIVERED,
})

#: The initial state. Not a parameter anywhere: an order is born when a device
#: is received, and there is no other way for one to begin.
INITIAL_STATE: str = RepairStatusCode.RECEIVED


def available_transitions(repair_order) -> list[str]:
    """
    The states a person may move this order to right now. The server's answer.

    Event-only states are filtered out: they are reached by publishing a quote
    or by a customer deciding on one, and offering them as buttons would let the
    app produce a state whose meaning nothing had established.
    """
    return [
        state for state in TRANSITIONS.get(repair_order.status, ())
        if state not in EVENT_ONLY_STATES
        and (repair_order.status, state) not in EVENT_ONLY_EDGES
    ]


def is_transition_allowed(from_status: str, to_status: str) -> bool:
    """Whether the MACHINE allows this edge, ignoring who may travel it."""
    return to_status in TRANSITIONS.get(from_status, ())


def is_generic_transition_allowed(from_status: str, to_status: str) -> bool:
    """Whether a person may make this move through the generic endpoint."""
    return (
        to_status not in EVENT_ONLY_STATES
        and (from_status, to_status) not in EVENT_ONLY_EDGES
        and is_transition_allowed(from_status, to_status)
    )


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

    if (from_status, to_status) in EVENT_ONLY_EDGES:
        raise InvalidTransitionError(
            'Para devolver la orden a diagnóstico, anula la cotización enviada.'
        )

    if to_status in EVENT_ONLY_STATES:
        # Reachable, but not this way. `waiting_approval` needs a published
        # quote behind it and `approved`/`rejected` need a customer's decision;
        # letting the generic endpoint set them would make the state mean
        # nothing again, which is exactly what M9 exists to fix.
        raise InvalidTransitionError(
            'Ese estado solo se alcanza publicando una cotización o registrando '
            'la decisión del cliente.'
        )

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

    # M12B — the customer follows the lifecycle, and the shop learns when an
    # order becomes collectable. Both are decided from the status the order
    # ACTUALLY reached, never from what the caller asked for.
    _notify(_emit_status_changed, order=locked, to_status=to_status)
    return locked


def _apply_transition(order, *, to_status, actor, origin, comment, request=None):
    """
    Move a LOCKED order along an edge of the machine, bypassing the generic gate.

    Only the event operations call this — publishing a quote and recording a
    customer's decision — and each has already established that the event
    justifying the move actually happened. The edge itself is still validated:
    an event cannot invent a transition the machine does not have.
    """
    from_status = order.status
    if not is_transition_allowed(from_status, to_status):
        raise InvalidTransitionError('Ese cambio de estado no está permitido.')

    order.status = to_status
    update_fields = ['status', 'updated_at']
    if to_status in TERMINAL_STATES:
        order.closed_at = timezone.now()
        update_fields.append('closed_at')
    order.save(update_fields=update_fields)

    _append_history(
        order, from_status=from_status, to_status=to_status,
        actor=actor, origin=origin, comment=comment,
    )
    return order


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
# ---------------------------------------------------------------------------
# M12B — emisión de eventos de notificación
# ---------------------------------------------------------------------------
#
# These run INSIDE the business transaction they belong to, on purpose: the
# event is as durable as the change it describes, and a rollback takes both.
# The e-mail attempt is scheduled by the notification layer with `on_commit`,
# so it only happens if the change survived.
#
# NOTHING HERE MAY RAISE INTO THE DOMAIN. A repair that was delivered is
# delivered whether or not anybody could be told, and a notification failure
# aborting the transaction would make the notice more important than the work.


def _notify(fn, *args, **kwargs):
    """Emit, and never let the attempt break the operation that caused it."""
    try:
        return fn(*args, **kwargs)
    except Exception:  # noqa: BLE001 — see the section docstring
        import logging
        logging.getLogger(__name__).exception('no se pudo emitir la notificación')
        return None


def _customer_of(order):
    """The customer row an order belongs to, or None."""
    return getattr(order, 'customer', None)


def _order_label(order) -> str:
    return f'Orden {order.number}'


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

    # M12B — the technician who now has it. Keyed on the ASSIGNMENT, so a
    # reassignment back and forth produces one notice per assignment and a
    # re-run of the same one produces none.
    _notify(
        _emit_assignment_created, order=locked, assignment=assignment,
        technician=technician,
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


# ---------------------------------------------------------------------------
# BR-005B — diagnosis, quotes and the customer's decision
# ---------------------------------------------------------------------------

class DiagnosticError(ServiceError):
    """A diagnosis rule was broken. Views render this as HTTP 400."""


class QuoteError(ServiceError):
    """A quoting rule was broken. Views render this as HTTP 400."""


class QuoteDecisionConflict(ServiceError):
    """This quote already carries a different decision. Views render 409."""


#: Lifecycle states in which a technician may compose a diagnosis or a quote.
#:
#: `received` is deliberately excluded: an order nobody has started looking at
#: has nothing to diagnose, and the move to `diagnosing` is the act of picking
#: it up. `waiting_approval` is excluded because a quote is already out there —
#: composing a second one means going back to diagnosis first, and that is a
#: decision somebody makes rather than a side effect of opening a form.
QUOTABLE_STATES: frozenset[str] = frozenset({RepairStatusCode.DIAGNOSING})


def company_currency(company) -> str:
    """
    The currency a quote freezes. NEVER from the client.

    Read from `CompanySettings`. A blank row falls back to the field's own
    declared default rather than to a hardcoded string — the platform's default
    is a platform fact, and copying it here would create a second place for it
    to drift.
    """
    settings_row = getattr(company, 'settings', None)
    configured = (getattr(settings_row, 'currency', '') or '').strip()
    if configured:
        return configured.upper()
    return str(CompanySettings._meta.get_field('currency').default).upper()


# ---------------------------------------------------------------------------
# Diagnosis
# ---------------------------------------------------------------------------

def latest_diagnostic(repair_order):
    """The most recent revision, or None."""
    return repair_order.diagnostics.order_by('-revision', '-pk').first()


@transaction.atomic
def create_diagnostic(
    *,
    repair_order,
    description: str,
    recommended_action: str,
    root_cause: str = '',
    internal_notes: str = '',
    actor=None,
    request=None,
) -> RepairDiagnostic:
    """
    Open a new DRAFT diagnosis on an order that is being diagnosed.

    THE REVISION IS ALLOCATED UNDER A LOCK. Two technicians opening a form at
    the same moment would otherwise both read "the last revision is 1" and both
    try to write 2; the unique constraint would reject one of them with an
    IntegrityError, which is a 500 dressed as concurrency control. The order row
    is locked first — the same discipline the rest of this module follows.

    `diagnosed_by` is the AUTHENTICATED ACTOR. There is no parameter for it.
    """
    locked = RepairOrder.objects.select_for_update().get(pk=repair_order.pk)

    if locked.status not in QUOTABLE_STATES:
        raise DiagnosticError(
            'Solo se puede diagnosticar una orden que está en diagnóstico.'
        )

    next_revision = (
        locked.diagnostics.aggregate(top=Max('revision'))['top'] or 0
    ) + 1

    diagnostic = RepairDiagnostic(
        company_id=locked.company_id,
        repair_order=locked,
        revision=next_revision,
        status=RepairDiagnostic.STATUS_DRAFT,
        description=description,
        root_cause=root_cause,
        recommended_action=recommended_action,
        internal_notes=internal_notes,
        diagnosed_by=actor,
    )
    diagnostic.save()

    AdminAuditLog.log(
        actor=actor,
        action='service_diagnostic_created',
        target_type='repair_diagnostic',
        target_id=diagnostic.pk,
        metadata={
            'repair_order_id': locked.pk,
            'number': locked.number,
            'revision': diagnostic.revision,
        },
        request=request,
        company=locked.company,
    )
    return diagnostic


@transaction.atomic
def update_diagnostic(*, diagnostic, actor=None, request=None, **fields) -> RepairDiagnostic:
    """
    Edit a DRAFT diagnosis.

    The model refuses to save a finalized row at all, so this is the friendly
    error rather than the last line of defence.
    """
    locked = RepairDiagnostic.objects.select_for_update().get(pk=diagnostic.pk)
    if locked.is_finalized:
        raise DiagnosticError(
            'Un diagnóstico finalizado no se puede modificar. Crea una revisión nueva.'
        )

    for name in ('description', 'root_cause', 'recommended_action', 'internal_notes'):
        if name in fields and fields[name] is not None:
            setattr(locked, name, fields[name])
    locked.save()
    return locked


def _finalize_diagnostic(diagnostic, *, actor=None, request=None) -> RepairDiagnostic:
    """
    Freeze a diagnosis. Called from `publish_quote`, inside its transaction.

    `update_fields` is what gets past the model's own guard — the row is
    becoming evidence, and this is the write that makes it so.
    """
    if diagnostic.is_finalized:
        return diagnostic

    diagnostic.status = RepairDiagnostic.STATUS_FINALIZED
    diagnostic.finalized_at = timezone.now()
    diagnostic.save(update_fields=['status', 'finalized_at', 'updated_at'])

    AdminAuditLog.log(
        actor=actor,
        action='service_diagnostic_finalized',
        target_type='repair_diagnostic',
        target_id=diagnostic.pk,
        metadata={
            'repair_order_id': diagnostic.repair_order_id,
            'revision': diagnostic.revision,
        },
        request=request,
        company=diagnostic.company,
    )
    return diagnostic


# ---------------------------------------------------------------------------
# Quotes
# ---------------------------------------------------------------------------

def _money(value) -> Decimal:
    """
    Everything monetary as `Decimal`, quantised to cents.

    Never float. `0.1 + 0.2` is the oldest bug in commercial software and a
    quote is a number somebody agrees to.
    """
    return Decimal(str(value)).quantize(Decimal('0.01'))


def recalculate_quote(quote) -> RepairQuote:
    """
    Recompute every total from the lines. THE SERVER'S ARITHMETIC, always.

    An internal user composing a quote chooses `quantity` and `unit_price` —
    that is what writing a quote is. The multiplication and the sums are not
    theirs to send: a client that could post its own total could post one that
    does not match its own lines.

    `tax_amount` is left exactly as it is, which today means zero. This platform
    models no tax anywhere — no rate, no regime, no configuration — and
    computing 18% here because the pilot is Peruvian would be writing one
    country's law into a SaaS schema.
    """
    subtotal = Decimal('0.00')
    for item in quote.items.all():
        line = _money(item.quantity * item.unit_price)
        if item.line_total != line:
            RepairQuoteItem.objects.filter(pk=item.pk).update(line_total=line)
        subtotal += line

    subtotal = _money(subtotal)
    discount = _money(quote.discount_amount or Decimal('0.00'))
    if discount > subtotal:
        raise QuoteError('El descuento no puede superar el subtotal.')

    tax = _money(quote.tax_amount or Decimal('0.00'))
    total = _money(subtotal - discount + tax)

    RepairQuote.objects.filter(pk=quote.pk).update(
        subtotal=subtotal, discount_amount=discount, tax_amount=tax, total=total,
        updated_at=timezone.now(),
    )
    quote.subtotal, quote.discount_amount, quote.tax_amount, quote.total = (
        subtotal, discount, tax, total,
    )
    return quote


@transaction.atomic
def create_quote(
    *,
    repair_order,
    diagnostic=None,
    valid_until=None,
    customer_notes: str = '',
    internal_notes: str = '',
    discount_amount=None,
    actor=None,
    request=None,
) -> RepairQuote:
    """
    Open a new DRAFT quote. The revision is the server's, under a lock.

    The currency is frozen from the company's settings here, at composition
    time, so a later settings change cannot restate a price somebody was quoted.
    """
    locked = RepairOrder.objects.select_for_update().get(pk=repair_order.pk)

    if locked.status not in QUOTABLE_STATES:
        raise QuoteError('Solo se puede cotizar una orden que está en diagnóstico.')

    if diagnostic is not None and diagnostic.repair_order_id != locked.pk:
        raise QuoteError('El diagnóstico es de otra orden.')

    next_revision = (locked.quotes.aggregate(top=Max('revision'))['top'] or 0) + 1

    quote = RepairQuote(
        company_id=locked.company_id,
        repair_order=locked,
        diagnostic=diagnostic,
        revision=next_revision,
        status=RepairQuote.STATUS_DRAFT,
        currency=company_currency(locked.company),
        discount_amount=_money(discount_amount or Decimal('0.00')),
        valid_until=valid_until,
        customer_notes=customer_notes,
        internal_notes=internal_notes,
        created_by=actor,
    )
    quote.save()

    AdminAuditLog.log(
        actor=actor,
        action='service_quote_created',
        target_type='repair_quote',
        target_id=quote.pk,
        metadata={
            'repair_order_id': locked.pk,
            'number': locked.number,
            'revision': quote.revision,
            'currency': quote.currency,
        },
        request=request,
        company=locked.company,
    )
    return quote


@transaction.atomic
def update_quote(*, quote, actor=None, request=None, **fields) -> RepairQuote:
    """Edit a DRAFT quote's header and recompute. Refuses anything else."""
    locked = RepairQuote.objects.select_for_update().get(pk=quote.pk)
    if not locked.is_editable:
        raise QuoteError(
            'Una cotización enviada no se puede modificar. Crea una revisión nueva.'
        )

    if 'diagnostic' in fields and fields['diagnostic'] is not None:
        diagnostic = fields['diagnostic']
        if diagnostic.repair_order_id != locked.repair_order_id:
            raise QuoteError('El diagnóstico es de otra orden.')
        locked.diagnostic = diagnostic
    for name in ('customer_notes', 'internal_notes', 'valid_until'):
        if name in fields:
            setattr(locked, name, fields[name])
    if 'discount_amount' in fields and fields['discount_amount'] is not None:
        discount = _money(fields['discount_amount'])
        # Checked HERE so the caller gets a domain error. The model raises too,
        # and that is the guarantee — but a guarantee is not an explanation.
        if discount > _money(locked.subtotal or Decimal('0.00')):
            raise QuoteError('El descuento no puede superar el subtotal.')
        locked.discount_amount = discount

    locked.save()
    return recalculate_quote(locked)


@transaction.atomic
def add_quote_item(
    *,
    quote,
    description: str,
    quantity,
    unit_price,
    item_type: str = RepairQuoteItem.TYPE_LABOR,
    product=None,
    sort_order: int = 0,
) -> RepairQuoteItem:
    """
    Add a line to a DRAFT quote.

    A `product` is a REFERENCE and nothing more: the description and the price
    are copied here and never re-read, and no stock moves. Quoting a part is not
    taking one off a shelf — the phase that consumes parts will do that.
    """
    locked = RepairQuote.objects.select_for_update().get(pk=quote.pk)
    if not locked.is_editable:
        raise QuoteError('No se puede modificar una cotización enviada.')

    if product is not None and product.company_id != locked.company_id:
        raise QuoteError('El producto no pertenece a esta empresa.')

    quantity = Decimal(str(quantity))
    unit_price = _money(unit_price)
    if quantity <= 0:
        raise QuoteError('La cantidad debe ser mayor que cero.')
    if unit_price < 0:
        raise QuoteError('El precio no puede ser negativo.')

    item = RepairQuoteItem(
        quote=locked,
        item_type=item_type,
        description=description,
        quantity=quantity,
        unit_price=unit_price,
        line_total=_money(quantity * unit_price),
        product=product,
        sort_order=sort_order,
    )
    item.save()
    recalculate_quote(locked)
    return item


@transaction.atomic
def remove_quote_item(*, item) -> RepairQuote:
    """Drop a line from a DRAFT quote and recompute."""
    quote = RepairQuote.objects.select_for_update().get(pk=item.quote_id)
    if not quote.is_editable:
        raise QuoteError('No se puede modificar una cotización enviada.')
    item.delete()
    return recalculate_quote(quote)


@transaction.atomic
def publish_quote(*, quote, actor=None, request=None) -> RepairQuote:
    """
    Send a quote to the customer, and put the order in front of them.

    THE ONE OPERATION THAT PRODUCES `waiting_approval`. Everything it does
    happens together or not at all:

      1. lock the order, then the quote — the platform's fixed lock order;
      2. refuse unless the order is being diagnosed and the quote is a draft;
      3. refuse a quote with no lines: an empty quote is not a proposal;
      4. recompute the totals from the lines, one last time;
      5. freeze the diagnosis it was built from;
      6. mark the quote SENT and stamp `sent_at` server-side;
      7. move the order to `waiting_approval`, writing its history;
      8. record the audit entry.

    A ZERO TOTAL IS ALLOWED. A courtesy assessment and a no-charge diagnosis are
    real things a shop does, and requiring `total > 0` would make somebody type
    a cent to describe free work.

    `sent` MEANS "AVAILABLE TO THE CUSTOMER". It does not mean an email left, a
    WhatsApp arrived or a push was delivered — none of those channels exists,
    and claiming delivery the product cannot perform would be worse than saying
    nothing. Recorded as pending.
    """
    locked_order = RepairOrder.objects.select_for_update().get(pk=quote.repair_order_id)
    locked_quote = RepairQuote.objects.select_for_update().get(pk=quote.pk)

    if locked_order.status not in QUOTABLE_STATES:
        raise QuoteError('Solo se puede publicar desde una orden en diagnóstico.')
    if not locked_quote.is_editable:
        raise QuoteError('Esta cotización ya fue enviada.')
    if not locked_quote.items.exists():
        raise QuoteError('Una cotización sin líneas no se puede enviar.')

    recalculate_quote(locked_quote)

    if locked_quote.diagnostic_id is not None:
        _finalize_diagnostic(locked_quote.diagnostic, actor=actor, request=request)

    locked_quote.status = RepairQuote.STATUS_SENT
    locked_quote.sent_at = timezone.now()
    locked_quote.save(update_fields=['status', 'sent_at', 'updated_at'])

    _apply_transition(
        locked_order,
        to_status=RepairStatusCode.WAITING_APPROVAL,
        actor=actor,
        origin=RepairStatusHistory.ORIGIN_INTERNAL,
        comment='',
        request=request,
    )

    AdminAuditLog.log(
        actor=actor,
        action='service_quote_published',
        target_type='repair_quote',
        target_id=locked_quote.pk,
        metadata={
            'repair_order_id': locked_order.pk,
            'number': locked_order.number,
            'revision': locked_quote.revision,
            'total': str(locked_quote.total),
            'currency': locked_quote.currency,
        },
        request=request,
        company=locked_order.company,
    )

    # M12B — the customer now has something to decide. Keyed on the quote AND
    # its revision: republishing revision 2 must not notify twice, but a
    # genuinely new revision is genuinely new news.
    _notify(_emit_quote_available, order=locked_order, quote=locked_quote)
    return locked_quote


@transaction.atomic
def cancel_quote(*, quote, actor=None, request=None) -> RepairQuote:
    """
    Withdraw a quote the customer has not answered.

    The order goes BACK to diagnosis, because that is where a new revision is
    composed. A quote the customer already decided on is evidence and cannot be
    withdrawn — that is what a decision means.
    """
    locked_order = RepairOrder.objects.select_for_update().get(pk=quote.repair_order_id)
    locked_quote = RepairQuote.objects.select_for_update().get(pk=quote.pk)

    if locked_quote.status not in (RepairQuote.STATUS_DRAFT, RepairQuote.STATUS_SENT):
        raise QuoteError('Esta cotización ya no se puede anular.')

    was_sent = locked_quote.status == RepairQuote.STATUS_SENT
    locked_quote.status = RepairQuote.STATUS_CANCELLED
    locked_quote.cancelled_at = timezone.now()
    locked_quote.save(update_fields=['status', 'cancelled_at', 'updated_at'])

    if was_sent and locked_order.status == RepairStatusCode.WAITING_APPROVAL:
        _apply_transition(
            locked_order,
            to_status=RepairStatusCode.DIAGNOSING,
            actor=actor,
            origin=RepairStatusHistory.ORIGIN_INTERNAL,
            comment='',
            request=request,
        )

    AdminAuditLog.log(
        actor=actor,
        action='service_quote_cancelled',
        target_type='repair_quote',
        target_id=locked_quote.pk,
        metadata={
            'repair_order_id': locked_order.pk,
            'revision': locked_quote.revision,
        },
        request=request,
        company=locked_order.company,
    )
    return locked_quote


# ---------------------------------------------------------------------------
# The customer's decision
# ---------------------------------------------------------------------------

def customer_visible_quote(repair_order):
    """
    The quote this order's customer may see, or None.

    Only a quote that was actually sent. A draft is the shop thinking out loud,
    and a cancelled one was withdrawn — but an expired or already-decided quote
    IS returned, because hiding it would make somebody believe it never existed.
    """
    return (
        repair_order.quotes
        .filter(status__in=(
            RepairQuote.STATUS_SENT,
            RepairQuote.STATUS_APPROVED,
            RepairQuote.STATUS_REJECTED,
        ))
        .order_by('-revision', '-pk')
        .first()
    )


@transaction.atomic
def record_quote_decision(
    *,
    quote,
    customer,
    user,
    decision: str,
    reason: str = '',
    request=None,
) -> RepairQuoteDecision:
    """
    Record what the customer answered, once and for all.

    IDEMPOTENT FOR THE SAME ANSWER, CONFLICTING FOR THE OPPOSITE ONE. A double
    tap on a slow connection is one decision, so a repeat of the same answer
    returns the record that already exists. A different answer arriving later is
    a conflict the caller has to see — silently overwriting it would let the
    second tap of two racing devices decide the outcome.

    The uniqueness is the database's: `RepairQuoteDecision.quote` is a
    OneToOne. A check in Python is a check a race walks straight through, and
    the order row is locked first so two requests serialise here rather than
    both reading "no decision yet".

    THE CHANNEL IS NOT A PARAMETER. A decision made through the authenticated
    customer surface is `customer_account`. Recording "they approved by phone"
    is a different endpoint with different authority, and it does not exist yet.

    THE IP COMES FROM THE PLATFORM'S AUTHORITY. `client_ip.get_client_ip()`
    respects `TRUSTED_PROXY_COUNT`; reading `X-Forwarded-For` here would let the
    caller choose which address their approval was recorded under, which is
    precisely the hole P0-B closed.
    """
    from .client_ip import get_client_ip

    if decision not in (
        RepairQuoteDecision.DECISION_APPROVE, RepairQuoteDecision.DECISION_REJECT,
    ):
        raise QuoteError('Decisión desconocida.')

    locked_order = RepairOrder.objects.select_for_update().get(pk=quote.repair_order_id)
    locked_quote = RepairQuote.objects.select_for_update().get(pk=quote.pk)

    existing = RepairQuoteDecision.objects.filter(quote=locked_quote).first()
    if existing is not None:
        if existing.decision == decision:
            return existing
        raise QuoteDecisionConflict(
            'Esta cotización ya tiene una respuesta registrada.'
        )

    if locked_quote.status != RepairQuote.STATUS_SENT:
        raise QuoteError('Esta cotización no está esperando una respuesta.')
    if locked_quote.is_expired:
        raise QuoteError('Esta cotización venció y ya no se puede responder.')
    if locked_quote.repair_order_id != locked_order.pk:
        raise QuoteError('La cotización no corresponde a esta orden.')

    approving = decision == RepairQuoteDecision.DECISION_APPROVE
    now = timezone.now()

    locked_quote.status = (
        RepairQuote.STATUS_APPROVED if approving else RepairQuote.STATUS_REJECTED
    )
    if approving:
        locked_quote.approved_at = now
        stamp = 'approved_at'
    else:
        locked_quote.rejected_at = now
        stamp = 'rejected_at'
    locked_quote.save(update_fields=['status', stamp, 'updated_at'])

    record = RepairQuoteDecision.objects.create(
        company_id=locked_order.company_id,
        repair_order=locked_order,
        quote=locked_quote,
        customer=customer,
        user=user,
        decision=decision,
        channel=RepairQuoteDecision.CHANNEL_CUSTOMER_ACCOUNT,
        reason=reason or '',
        quoted_total=locked_quote.total,
        currency=locked_quote.currency,
        ip_address=get_client_ip(request) if request is not None else None,
        decided_at=now,
    )

    # The history says WHAT happened, never the customer's words. A free-text
    # reason living in a customer-visible timeline is one policy change away
    # from being published; it stays on the decision record, where the internal
    # surface reads it.
    _apply_transition(
        locked_order,
        to_status=(
            RepairStatusCode.APPROVED if approving else RepairStatusCode.REJECTED
        ),
        actor=user,
        origin=RepairStatusHistory.ORIGIN_CUSTOMER,
        comment='',
        request=request,
    )

    AdminAuditLog.log(
        actor=user,
        action=(
            'service_quote_approved' if approving else 'service_quote_rejected'
        ),
        target_type='repair_quote',
        target_id=locked_quote.pk,
        metadata={
            'repair_order_id': locked_order.pk,
            'number': locked_order.number,
            'revision': locked_quote.revision,
            'total': str(locked_quote.total),
            'currency': locked_quote.currency,
            'channel': record.channel,
        },
        request=request,
        company=locked_order.company,
    )

    # M12B — the shop learns the answer. The customer is NOT notified: they
    # just pressed the button, and telling somebody what they themselves did
    # a second ago is noise. Their timeline keeps the decision.
    _notify(
        _emit_quote_decision, order=locked_order, quote=locked_quote,
        approved=(record.decision == RepairQuoteDecision.DECISION_APPROVE),
    )
    return record


# ===========================================================================
# M10 / BR-005C — executing the repair, and consuming the parts it needs
# ===========================================================================
#
# THE LOCK ORDER, WHICH IS THE WHOLE RISK OF THIS PHASE
# -----------------------------------------------------
# M10 is the first operation that touches two aggregates that already have
# their own locking disciplines. Getting the sequence wrong does not fail a
# test; it deadlocks a till against a workbench on a Saturday afternoon.
#
# Both existing disciplines say the same thing, so there was nothing to
# reconcile — only something to obey:
#
#   · `service_services` locks the DOCUMENT first: `RepairOrder`, then
#     `RepairQuote`. Never the other way round.
#   · `inventory_services` locks the document first too (`StockTransfer`,
#     `InventoryCount`), then `BranchStock` rows in ascending
#     `(branch_id, product_id)` through `_locked_branch_stocks`, and
#     deliberately never locks `Product` — locking the article would serialise
#     every branch of a chain against every other for the same part.
#
# So M10's order is the concatenation, with nothing invented:
#
#       RepairOrder  →  RepairExecution  →  PartUsage  →  BranchStock
#
# `BranchStock` is always LAST and is always taken by
# `inventory_services.create_stock_movement`, which is the only function in
# this codebase that writes stock. Nothing here touches `BranchStock.quantity`
# or `Product.inventory` — those have exactly one writer and this module is not
# it. `Product` is never locked.
#
# WHY THE PART GOES THROUGH INVENTORY AND NOT AROUND IT
# ------------------------------------------------------
# `create_stock_movement` maintains the Kardex, the branch row and the
# `Product.inventory` aggregate in one transaction. `Product.inventory` has no
# database check constraint, so a second implementation that got it wrong would
# corrupt the number the storefront reads with nothing to raise. Service
# orchestrates; inventory mutates.


class RepairExecutionError(ServiceError):
    """The bench operation is not legal in this state. Views render 400."""


class PartUsageError(ServiceError):
    """A part cannot be consumed or reversed as asked. Views render 400."""


class StockUnavailableError(PartUsageError):
    """
    The order's own branch does not hold enough of the part.

    Its own class, and its own HTTP status, because it is the one failure here
    that is nobody's mistake: the shop simply does not have the piece today.
    The caller's next move is to order it and pause the repair, not to correct
    a bad request — so it must be distinguishable without reading Spanish
    prose. Same reason the POS answers 409 for the same condition.
    """


class _PartUsageRaceLost(Exception):
    """
    Internal. Another caller committed this idempotency key while we worked.

    Never leaves this module. It exists so the losing attempt can unwind its
    OWN stock movement — by letting the transaction roll back — before the
    outer wrapper replays the winner's row. Returning the winner from inside
    the transaction would commit an orphan decrement, which is precisely the
    double-consumption this whole mechanism exists to prevent.
    """


class IdempotencyConflict(ServiceError):
    """
    The key has been used, for a different request.

    Not an error about parts: an error about a client reusing a key it minted
    for something else. Replaying the SAME request returns the original row and
    raises nothing at all.
    """


#: States in which a technician may record work and consume parts.
#:
#: `waiting_parts` is included deliberately: the moment the missing piece
#: arrives is exactly when somebody records it, and forcing a resume first
#: would make the pause a trap rather than a note.
WORKABLE_STATES: frozenset[str] = frozenset({
    RepairStatusCode.IN_REPAIR,
    RepairStatusCode.WAITING_PARTS,
})

#: The only state a repair may START from. The customer said yes; nothing
#: earlier means that and nothing later needs saying twice.
STARTABLE_STATES: frozenset[str] = frozenset({RepairStatusCode.APPROVED})

#: Fields a caller may change on an open execution.
#:
#: Everything absent is either the server's (`started_at`, `started_by`,
#: `completed_at`, `completed_by`, `company`, `repair_order`) or a decision
#: rather than a draft (finishing). DEC-020's rule, once more: having a field is
#: being able to fill it in.
EDITABLE_EXECUTION_FIELDS: frozenset[str] = frozenset({
    'work_performed', 'result', 'internal_notes',
})


def approved_quote(repair_order):
    """
    The quote this repair is authorised by, or None.

    The LATEST approved revision. A shop that quoted twice and had the second
    approved is working to the second one, and the first is history.
    """
    return (
        repair_order.quotes
        .filter(status=RepairQuote.STATUS_APPROVED)
        .order_by('-revision', '-pk')
        .first()
    )


def open_execution(repair_order):
    """The execution currently being worked on, or None."""
    return (
        repair_order.executions
        .filter(completed_at__isnull=True)
        .order_by('-started_at', '-pk')
        .first()
    )


def latest_execution(repair_order):
    """The most recent execution, open or finished, or None."""
    return repair_order.executions.order_by('-started_at', '-pk').first()


def executions_for(repair_order):
    """Every execution on this order, newest first."""
    return repair_order.executions.all()


@transaction.atomic
def start_repair(*, repair_order, actor=None, request=None):
    """
    Begin the work. APPROVED → IN_REPAIR, and an execution row to hold it.

    ONE OPERATION, NOT A STATUS WRITE. `transition_repair_order` refuses
    `in_repair` outright — it is in `EVENT_ONLY_STATES` — because moving the
    order without opening an execution would produce an order that claims to be
    on a bench with no record of who put it there or when.

    THE APPROVAL IS RE-CHECKED HERE even though M9 already guarantees it. The
    order's status alone is a projection; this reads the quote and its decision
    row, because `in_repair` is the first state that spends the customer's
    money and "the status said approved" is not the same sentence as "somebody
    approved it". Defence in depth costs one query.

    IDEMPOTENT IN THE ONLY WAY THAT HELPS: a second call while work is already
    open returns the SAME execution instead of raising, so a double tap or a
    retried request does not produce an error the technician has to interpret.
    Starting an order that is not approved is still a refusal.
    """
    locked = RepairOrder.objects.select_for_update().get(pk=repair_order.pk)

    existing = open_execution(locked)
    if existing is not None and locked.status in WORKABLE_STATES:
        return existing

    if locked.status not in STARTABLE_STATES:
        raise RepairExecutionError(
            'Solo se puede iniciar una reparación aprobada por el cliente.'
        )

    quote = approved_quote(locked)
    if quote is None:
        raise RepairExecutionError(
            'No hay una cotización aprobada para esta orden.'
        )
    decision = RepairQuoteDecision.objects.filter(quote=quote).first()
    if decision is None or decision.decision != RepairQuoteDecision.DECISION_APPROVE:
        raise RepairExecutionError(
            'No hay una aprobación registrada del cliente para esta cotización.'
        )

    execution = RepairExecution(
        company_id=locked.company_id,
        repair_order=locked,
        started_at=timezone.now(),
        started_by=actor,
    )
    execution.save()

    _apply_transition(
        locked,
        to_status=RepairStatusCode.IN_REPAIR,
        actor=actor,
        origin=RepairStatusHistory.ORIGIN_INTERNAL,
        comment='',
        request=request,
    )

    AdminAuditLog.log(
        actor=actor,
        action='service_repair_started',
        target_type='repair_execution',
        target_id=execution.pk,
        metadata={
            'repair_order_id': locked.pk,
            'number': locked.number,
            'quote_id': quote.pk,
            'revision': quote.revision,
        },
        request=request,
        company=locked.company,
    )
    return execution


@transaction.atomic
def update_execution(*, execution, actor=None, request=None, **fields):
    """
    Amend the bench notes while the work is open.

    NOT AUDITED, deliberately. This is a draft somebody types into over an
    afternoon, and an audit row per keystroke is an audit log nobody reads. The
    write that matters — finishing — is audited, and after it this function
    refuses.
    """
    locked = RepairExecution.objects.select_for_update().get(pk=execution.pk)
    if locked.completed_at is not None:
        raise RepairExecutionError('Un trabajo finalizado no se puede modificar.')

    unknown = set(fields) - EDITABLE_EXECUTION_FIELDS
    if unknown:
        raise RepairExecutionError(
            f'Campos no editables: {", ".join(sorted(unknown))}.'
        )

    result = fields.get('result')
    if result is not None and result != '':
        if result not in RepairResultCode.values:
            raise RepairExecutionError('Ese resultado no existe.')

    for name, value in fields.items():
        setattr(locked, name, value)
    locked.save(update_fields=[*fields, 'updated_at'])
    return locked


@transaction.atomic
def pause_for_parts(*, repair_order, actor=None, comment='', request=None):
    """
    IN_REPAIR → WAITING_PARTS. An explicit act, never a side effect.

    A failed consumption does NOT move an order here. That was the tempting
    design and it is wrong: a request that fails for a technical reason must not
    change the lifecycle behind the operator's back, or the shop discovers its
    own state by reading error logs. Running out of a part answers 409; pausing
    the repair is a separate decision somebody takes, having seen it.
    """
    locked = RepairOrder.objects.select_for_update().get(pk=repair_order.pk)
    if locked.status != RepairStatusCode.IN_REPAIR:
        raise RepairExecutionError(
            'Solo se puede pausar una reparación en curso.'
        )
    if open_execution(locked) is None:
        raise RepairExecutionError('Esta orden no tiene un trabajo abierto.')

    _apply_transition(
        locked,
        to_status=RepairStatusCode.WAITING_PARTS,
        actor=actor,
        origin=RepairStatusHistory.ORIGIN_INTERNAL,
        comment=comment or '',
        request=request,
    )
    AdminAuditLog.log(
        actor=actor,
        action='service_repair_paused_for_parts',
        target_type='repair_order',
        target_id=locked.pk,
        metadata={'number': locked.number},
        request=request,
        company=locked.company,
    )
    return locked


@transaction.atomic
def resume_repair(*, repair_order, actor=None, request=None):
    """WAITING_PARTS → IN_REPAIR. The piece arrived."""
    locked = RepairOrder.objects.select_for_update().get(pk=repair_order.pk)
    if locked.status != RepairStatusCode.WAITING_PARTS:
        raise RepairExecutionError(
            'Solo se puede reanudar una reparación en espera de repuestos.'
        )
    if open_execution(locked) is None:
        raise RepairExecutionError('Esta orden no tiene un trabajo abierto.')

    _apply_transition(
        locked,
        to_status=RepairStatusCode.IN_REPAIR,
        actor=actor,
        origin=RepairStatusHistory.ORIGIN_INTERNAL,
        comment='',
        request=request,
    )
    AdminAuditLog.log(
        actor=actor,
        action='service_repair_resumed',
        target_type='repair_order',
        target_id=locked.pk,
        metadata={'number': locked.number},
        request=request,
        company=locked.company,
    )
    return locked


@transaction.atomic
def complete_repair(
    *, repair_order, work_performed=None, result=None, internal_notes=None,
    actor=None, request=None,
):
    """
    Finish the bench work. IN_REPAIR → REPAIRED, and the execution freezes.

    WHAT `REPAIRED` DOES NOT MEAN: checked, ready to collect, notified, paid,
    delivered, warranted. It means a technician put the device down. M11 is
    quality control and M12 is handover; neither exists, and naming this state
    "listo para entregar" would promise both.

    WHAT IT REQUIRES: an open execution, a description of what was actually
    done, and a result. A repair with no record of the work is a repair that
    cannot be argued about later — and `work_performed` is deliberately not
    seeded from the diagnosis, so an empty one means nobody wrote anything
    rather than that somebody accepted a default.

    Half-finished part consumption blocks it. A usage row exists or it does
    not; there is no partial state to strand, and the check below is there to
    catch a future path that introduces one.
    """
    locked = RepairOrder.objects.select_for_update().get(pk=repair_order.pk)
    if locked.status != RepairStatusCode.IN_REPAIR:
        raise RepairExecutionError(
            'Solo se puede finalizar una reparación en curso.'
        )

    execution = open_execution(locked)
    if execution is None:
        raise RepairExecutionError('Esta orden no tiene un trabajo abierto.')
    locked_execution = (
        RepairExecution.objects.select_for_update().get(pk=execution.pk)
    )
    if locked_execution.completed_at is not None:
        raise RepairExecutionError('Este trabajo ya está finalizado.')

    work = (
        locked_execution.work_performed if work_performed is None
        else work_performed
    )
    outcome = locked_execution.result if result is None else result
    notes = (
        locked_execution.internal_notes if internal_notes is None
        else internal_notes
    )

    if not (work or '').strip():
        raise RepairExecutionError(
            'Describe el trabajo realizado antes de finalizar.'
        )
    if outcome not in RepairResultCode.values:
        raise RepairExecutionError('Indica el resultado de la reparación.')

    locked_execution.work_performed = work
    locked_execution.result = outcome
    locked_execution.internal_notes = notes
    locked_execution.completed_at = timezone.now()
    locked_execution.completed_by = actor
    locked_execution.save(update_fields=[
        'work_performed', 'result', 'internal_notes', 'completed_at',
        'completed_by', 'updated_at',
    ])

    _apply_transition(
        locked,
        to_status=RepairStatusCode.REPAIRED,
        actor=actor,
        origin=RepairStatusHistory.ORIGIN_INTERNAL,
        comment='',
        request=request,
    )

    parts = (
        PartUsage.objects
        .filter(execution=locked_execution, reversed_at__isnull=True)
        .aggregate(lines=Sum('quantity'))
    )
    AdminAuditLog.log(
        actor=actor,
        action='service_repair_completed',
        target_type='repair_execution',
        target_id=locked_execution.pk,
        metadata={
            'repair_order_id': locked.pk,
            'number': locked.number,
            'result': outcome,
            'parts_consumed': parts['lines'] or 0,
        },
        request=request,
        company=locked.company,
    )
    return locked_execution


# ---------------------------------------------------------------------------
# Parts
# ---------------------------------------------------------------------------

def _usage_fingerprint(*, quote_item_id: int, quantity: int) -> str:
    """
    A stable SHA-256 of what was asked for.

    Same shape the POS sale and the native checkout already use: the key says
    "this is the same attempt", the fingerprint says "and it is asking for the
    same thing". Replaying with a changed quantity under a reused key is a
    client bug, and answering it with the original row would silently ignore
    what the second request actually said.
    """
    canonical = json.dumps(
        {'quote_item': int(quote_item_id), 'quantity': int(quantity)},
        sort_keys=True, separators=(',', ':'),
    )
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _consumed_for_item(quote_item_id: int) -> int:
    """Units of one quoted line already consumed and not reversed."""
    return (
        PartUsage.objects
        .filter(quote_item_id=quote_item_id, reversed_at__isnull=True)
        .aggregate(used=Sum('quantity'))['used'] or 0
    )


def _integral_quantity(value) -> int:
    """
    A quoted decimal quantity as whole units, or a refusal.

    `RepairQuoteItem.quantity` is a Decimal because a quote can legitimately
    price 1.5 hours of labour. `BranchStock.quantity` is an integer with a
    non-negative check constraint. Half a battery is not representable, and
    rounding one into somebody's inventory silently is how a shelf ends up
    disagreeing with a shelf.
    """
    amount = Decimal(value)
    if amount != amount.to_integral_value():
        raise PartUsageError(
            'Un repuesto se consume en unidades enteras.'
        )
    return int(amount)


def part_candidates(repair_order) -> list[dict]:
    """
    The parts this repair MAY consume, and how many are left to consume.

    A service-shaped answer, not an inventory one. It names only the `part`
    lines of the approved quote, and for each the number still outstanding and
    what the order's own branch happens to hold. It does NOT expose the Kardex,
    costs, other branches, or anything about the catalogue beyond the line the
    customer already saw — a technician needs `service.repair.manage`, never
    `inventory.view`, and this is why that is honest rather than a loophole.
    """
    quote = approved_quote(repair_order)
    if quote is None:
        return []

    items = list(
        quote.items
        .filter(item_type=RepairQuoteItem.TYPE_PART, product__isnull=False)
        .select_related('product')
        .order_by('sort_order', 'pk')
    )
    if not items:
        return []

    on_hand = {
        row.product_id: row.quantity
        for row in BranchStock.objects.filter(
            branch_id=repair_order.branch_id,
            product_id__in=[i.product_id for i in items],
        )
    }

    rows = []
    for item in items:
        try:
            approved_units = _integral_quantity(item.quantity)
        except PartUsageError:
            # A fractional PART line cannot be consumed, but it must not hide
            # the rest of the list. It is offered with nothing outstanding and
            # the reason attached.
            approved_units = 0
        used = _consumed_for_item(item.pk)
        rows.append({
            'quote_item_id': item.pk,
            'product_id': item.product_id,
            'description': item.description,
            'approved_quantity': approved_units,
            'used_quantity': used,
            'outstanding_quantity': max(approved_units - used, 0),
            'available_in_branch': on_hand.get(item.product_id, 0),
        })
    return rows


def record_part_usage(
    *, repair_order, quote_item, quantity, idempotency_key='',
    actor=None, request=None,
):
    """
    Book ONE approved part out of the order's OWN branch. The heart of M10.

    This wrapper is NOT the transaction — `_record_part_usage` is. It exists for
    exactly one case: two callers racing the same idempotency key. The loser's
    INSERT fails, its whole transaction rolls back (taking its stock movement
    with it), and only then is it safe to hand back the row the winner wrote.
    """
    key = (idempotency_key or '').strip()
    try:
        return _record_part_usage(
            repair_order=repair_order, quote_item=quote_item, quantity=quantity,
            idempotency_key=key, actor=actor, request=request,
        )
    except _PartUsageRaceLost:
        winner = (
            PartUsage.objects
            .filter(company_id=repair_order.company_id, idempotency_key=key)
            .first()
        )
        if winner is None:
            raise PartUsageError('No se pudo registrar el consumo.') from None
        expected = _usage_fingerprint(
            quote_item_id=quote_item.pk, quantity=int(quantity),
        )
        if winner.request_fingerprint != expected:
            raise IdempotencyConflict(
                'Esa clave ya se usó para un consumo diferente.'
            ) from None
        return winner


@transaction.atomic
def _record_part_usage(
    *, repair_order, quote_item, quantity, idempotency_key='',
    actor=None, request=None,
):
    """
    The transactional body. See `record_part_usage` for the wrapper's job.

    LOCK ORDER — RepairOrder → RepairExecution → PartUsage → BranchStock.
    `BranchStock` is taken last and only by `create_stock_movement`. See the
    block comment at the top of this section; do not reorder these lines.

    THE BRANCH IS `repair_order.branch` AND THERE IS NO PARAMETER FOR IT. A
    technician cannot spend another shop's stock: there is no transfer in this
    flow, so it would be units moving on paper that nobody carried. If the part
    is elsewhere, the answer is 409 and somebody decides what to do about it.

    THE PART MUST TRACE TO AN APPROVED `part` LINE. Not a product id — a line
    the customer was quoted and said yes to. An extra part nobody approved goes
    back through diagnosis and a new quote, which is slower and is the whole
    difference between a bill and a surprise.

    IDEMPOTENT AT THE DATABASE. The unique constraint decides, not a
    read-then-write: two tablets retrying the same confirmation are a race the
    application cannot win in Python. Same key + same request replays the
    original row; same key + different request is a conflict.
    """
    locked = RepairOrder.objects.select_for_update().get(pk=repair_order.pk)
    if locked.status not in WORKABLE_STATES:
        raise PartUsageError(
            'Solo se pueden consumir repuestos en una reparación en curso.'
        )

    execution = open_execution(locked)
    if execution is None:
        raise PartUsageError('Esta orden no tiene un trabajo abierto.')
    locked_execution = (
        RepairExecution.objects.select_for_update().get(pk=execution.pk)
    )
    if locked_execution.completed_at is not None:
        raise PartUsageError('Este trabajo ya está finalizado.')

    key = (idempotency_key or '').strip()
    units = int(quantity)
    if units <= 0:
        raise PartUsageError('La cantidad debe ser mayor que cero.')

    # --- idempotency FIRST, before any rule that could have moved ---
    #
    # A replayed request is answered from the key, not re-judged. Validating
    # first would fail the retry of a consumption that already succeeded — the
    # quota it filled is now full BECAUSE of it — and report the retry as
    # "already consumed" instead of handing back what happened. That reads as a
    # bug to a technician who tapped once and lost a connection.
    fingerprint = _usage_fingerprint(quote_item_id=quote_item.pk, quantity=units)
    if key:
        existing = (
            PartUsage.objects
            .filter(company_id=locked.company_id, idempotency_key=key)
            .first()
        )
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise IdempotencyConflict(
                    'Esa clave ya se usó para un consumo diferente.'
                )
            return existing

    # --- the line must belong to the approved quote of THIS order ---
    quote = approved_quote(locked)
    if quote is None:
        raise PartUsageError('No hay una cotización aprobada para esta orden.')
    if quote_item.quote_id != quote.pk:
        raise PartUsageError(
            'Ese repuesto no pertenece a la cotización aprobada de esta orden.'
        )
    if quote_item.item_type != RepairQuoteItem.TYPE_PART:
        raise PartUsageError('Esa línea de la cotización no es un repuesto.')
    if quote_item.product_id is None:
        raise PartUsageError(
            'Esa línea no está asociada a un producto del inventario.'
        )
    product = quote_item.product
    if product.company_id != locked.company_id:
        raise PartUsageError('El producto no pertenece a esta empresa.')

    approved_units = _integral_quantity(quote_item.quantity)
    already = _consumed_for_item(quote_item.pk)
    if already + units > approved_units:
        raise PartUsageError(
            f'La cotización aprobó {approved_units} unidad(es) de '
            f'"{quote_item.description}" y ya se consumieron {already}.'
        )

    # --- stock. The ONE writer, taken last. ---
    try:
        movement = inventory_services.create_stock_movement(
            branch=locked.branch,
            product_id=product.pk,
            movement_type=StockMovement.SERVICE_EXIT,
            quantity=units,
            reason=f'Reparación {locked.number}',
            actor=actor,
            reference_type='repair_order',
            reference_id=str(locked.pk),
            metadata={
                'repair_order_number': locked.number,
                'quote_item_id': quote_item.pk,
                'execution_id': locked_execution.pk,
            },
        )
    except inventory_services.InsufficientStockError as exc:
        raise StockUnavailableError(str(exc)) from exc
    except inventory_services.InventoryError as exc:
        raise PartUsageError(str(exc)) from exc

    usage = PartUsage(
        company_id=locked.company_id,
        repair_order=locked,
        execution=locked_execution,
        quote_item=quote_item,
        product=product,
        branch=locked.branch,
        quantity=units,
        stock_movement=movement,
        description=quote_item.description or product.name,
        actor=actor,
        idempotency_key=key,
        request_fingerprint=fingerprint if key else '',
    )
    try:
        # Its own savepoint: an IntegrityError marks the enclosing transaction
        # for rollback, and querying inside it afterwards raises
        # TransactionManagementError instead of answering. Same shape as the
        # checkout's recovery path.
        with transaction.atomic():
            usage.save()
    except IntegrityError:
        if not key:
            raise
        # Somebody else committed this key between our pre-check and our
        # INSERT. Unwind — the wrapper replays theirs.
        raise _PartUsageRaceLost() from None

    AdminAuditLog.log(
        actor=actor,
        action='service_part_used',
        target_type='part_usage',
        target_id=usage.pk,
        metadata={
            'repair_order_id': locked.pk,
            'number': locked.number,
            'product_id': product.pk,
            'quantity': units,
            'branch_id': locked.branch_id,
            'stock_movement_id': movement.pk,
        },
        request=request,
        company=locked.company,
    )
    return usage


@transaction.atomic
def reverse_part_usage(*, usage, reason='', actor=None, request=None):
    """
    Undo a consumption by COMPENSATING it. Never by deleting it.

    WHAT THIS IS FOR: a technician recorded the wrong part, or the wrong
    number of them, and catches it before finishing. The original row and its
    original movement stay exactly as written; a second movement puts the units
    back and this row is stamped with when and by whom.

    WHAT THIS IS NOT FOR: getting a fitted battery back onto a shelf after the
    job is done. Once the execution is complete the usage is frozen — a
    completed repair's parts are evidence, and a shop that can quietly un-consume
    them can quietly change what a customer was charged for. Returns after
    completion are a real business need and they need their own phase, with a
    physical inspection step that does not exist yet.

    IDEMPOTENT: reversing twice returns the same row. It does not put the units
    back a second time, and it does not raise — a double tap on "deshacer" is
    not an error worth a dialog.
    """
    locked_order = RepairOrder.objects.select_for_update().get(
        pk=usage.repair_order_id,
    )
    locked_execution = RepairExecution.objects.select_for_update().get(
        pk=usage.execution_id,
    )
    locked_usage = PartUsage.objects.select_for_update().get(pk=usage.pk)

    if locked_usage.reversed_at is not None:
        return locked_usage

    if locked_execution.completed_at is not None:
        raise PartUsageError(
            'Un repuesto de un trabajo finalizado no se puede revertir.'
        )
    if locked_order.status not in WORKABLE_STATES:
        raise PartUsageError(
            'Solo se pueden revertir repuestos de una reparación en curso.'
        )

    movement = inventory_services.create_stock_movement(
        branch=locked_usage.branch,
        product_id=locked_usage.product_id,
        movement_type=StockMovement.SERVICE_RETURN,
        quantity=locked_usage.quantity,
        reason=(reason or '').strip() or f'Reverso · reparación {locked_order.number}',
        actor=actor,
        reference_type='repair_order',
        reference_id=str(locked_order.pk),
        metadata={
            'repair_order_number': locked_order.number,
            'reverses_part_usage_id': locked_usage.pk,
            'reverses_stock_movement_id': locked_usage.stock_movement_id,
        },
    )

    locked_usage.reversed_at = timezone.now()
    locked_usage.reversed_by = actor
    locked_usage.reversal_movement = movement
    locked_usage.reversal_reason = (reason or '')[:300]
    locked_usage.save(update_fields=[
        'reversed_at', 'reversed_by', 'reversal_movement', 'reversal_reason',
    ])

    AdminAuditLog.log(
        actor=actor,
        action='service_part_usage_reversed',
        target_type='part_usage',
        target_id=locked_usage.pk,
        metadata={
            'repair_order_id': locked_order.pk,
            'number': locked_order.number,
            'product_id': locked_usage.product_id,
            'quantity': locked_usage.quantity,
            'stock_movement_id': movement.pk,
        },
        request=request,
        company=locked_order.company,
    )
    return locked_usage


def part_usages_for(repair_order):
    """Every part booked against this order, newest first, reversals included."""
    return (
        repair_order.part_usages
        .select_related('product', 'quote_item', 'actor', 'reversed_by')
        .all()
    )


# ===========================================================================
# M11 / BR-005D — quality control, and the rework a failure starts
# ===========================================================================
#
# THE LOCK ORDER IS UNCHANGED AND EXTENDED THE SAME WAY:
#
#     RepairOrder → RepairExecution → QualityCheck → QualityCheckItem
#
# Document first, then the row that belongs to it, exactly as M9 and M10 do.
# Nothing here touches BranchStock — quality control does not move inventory,
# and a failed test is not a returned part.


class QualityError(ServiceError):
    """A quality-control rule was broken. Views render this as HTTP 400."""


#: The only state a quality check may START from. The technician finished; there
#: is something to inspect.
INSPECTABLE_STATES: frozenset[str] = frozenset({RepairStatusCode.REPAIRED})


def active_quality_template(company, device_type: str = ''):
    """
    The checklist this company runs for this kind of device, or None.

    Most specific first: a template for `laptop` beats the company's general
    one. A company with neither has no checklist, and `start_quality_check`
    refuses rather than inventing a list — a platform that made up what to test
    would be a platform asserting it knows the shop's trade.
    """
    rows = QualityChecklistTemplate.objects.filter(company=company, is_active=True)
    if device_type:
        specific = rows.filter(device_type=device_type).first()
        if specific is not None:
            return specific
    return rows.filter(device_type='').first()


def open_quality_check(repair_order):
    """The inspection currently under way, or None."""
    return (
        repair_order.quality_checks
        .filter(status=QualityCheckStatus.IN_PROGRESS)
        .order_by('-started_at', '-pk')
        .first()
    )


def latest_quality_check(repair_order):
    """The most recent inspection, open or settled, or None."""
    return repair_order.quality_checks.order_by('-started_at', '-pk').first()


def quality_checks_for(repair_order):
    """Every inspection on this order, newest first."""
    return repair_order.quality_checks.all()


@transaction.atomic
def start_quality_check(*, repair_order, actor=None, request=None):
    """
    Open an inspection. REPAIRED → QUALITY_CONTROL, with a snapshot of the list.

    THE SNAPSHOT IS THE POINT. The items are COPIED off the template, not
    referenced, so an administrator who edits the checklist tomorrow does not
    rewrite what was tested today. A report that re-rendered old inspections
    through the current template would be quietly changing history.

    IDEMPOTENT IN THE ONLY USEFUL WAY: a second call while an inspection is
    already open returns the SAME one. A double tap is not an error worth a
    dialog, and the partial unique constraint makes the race safe regardless.
    """
    locked = RepairOrder.objects.select_for_update().get(pk=repair_order.pk)

    existing = open_quality_check(locked)
    if existing is not None and locked.status == RepairStatusCode.QUALITY_CONTROL:
        return existing

    if locked.status not in INSPECTABLE_STATES:
        raise QualityError(
            'Solo se puede controlar una reparación que el técnico ya terminó.'
        )

    execution = (
        locked.executions.filter(completed_at__isnull=False)
        .order_by('-completed_at', '-pk')
        .first()
    )
    if execution is None:
        raise QualityError('Esta orden no tiene un trabajo finalizado que revisar.')

    template = active_quality_template(
        locked.company, getattr(locked.device, 'device_type', '') or '',
    )
    if template is None:
        raise QualityError(
            'Esta empresa no tiene una lista de control configurada.'
        )
    items = list(template.items.all().order_by('sort_order', 'pk'))
    if not items:
        raise QualityError('La lista de control no tiene puntos que revisar.')

    check = QualityCheck(
        company_id=locked.company_id,
        repair_order=locked,
        execution=execution,
        template=template,
        template_name=template.name,
        checked_by=actor,
        started_at=timezone.now(),
    )
    check.save()

    QualityCheckItem.objects.bulk_create([
        QualityCheckItem(
            quality_check=check,
            code=item.code,
            label=item.label,
            is_required=item.is_required,
            sort_order=item.sort_order,
        )
        for item in items
    ])

    _apply_transition(
        locked,
        to_status=RepairStatusCode.QUALITY_CONTROL,
        actor=actor,
        origin=RepairStatusHistory.ORIGIN_INTERNAL,
        comment='',
        request=request,
    )

    AdminAuditLog.log(
        actor=actor,
        action='service_quality_started',
        target_type='quality_check',
        target_id=check.pk,
        metadata={
            'repair_order_id': locked.pk,
            'number': locked.number,
            'execution_id': execution.pk,
            'template': template.name,
            'items': len(items),
        },
        request=request,
        company=locked.company,
    )
    return check


@transaction.atomic
def record_quality_result(*, item, result: str, notes: str = '', actor=None):
    """
    Answer ONE point of an open checklist.

    Not audited per keystroke: this is a technician working down a list, and an
    audit row per tick is an audit log nobody reads. The writes that matter —
    opening, passing, failing — are all audited.
    """
    locked_item = QualityCheckItem.objects.select_for_update().get(pk=item.pk)
    check = QualityCheck.objects.select_for_update().get(pk=locked_item.quality_check_id)

    if check.status != QualityCheckStatus.IN_PROGRESS:
        raise QualityError('Este control de calidad ya está cerrado.')
    if result not in QualityResultCode.values:
        raise QualityError('Ese resultado no existe.')

    locked_item.result = result
    locked_item.notes = (notes or '')[:300]
    locked_item.save(update_fields=['result', 'notes', 'updated_at'])
    return locked_item


def unresolved_required_items(check):
    """Required points nobody has answered yet."""
    return check.items.filter(is_required=True, result='')


def failing_items(check):
    """Points that came back FAIL. `not_applicable` is an answer, not a failure."""
    return check.items.filter(result=QualityResultCode.FAIL)


@transaction.atomic
def pass_quality_check(*, repair_order, notes: str = '', actor=None, request=None):
    """
    The device passed. QUALITY_CONTROL → READY_FOR_PICKUP, atomically.

    THE SERVER DECIDES, NOT THE CALLER. There is no field anywhere that lets a
    client assert `status='passed'`: this reads the items and refuses if any
    required one is unanswered or any one failed. A checklist whose result could
    be sent by whoever filled it in is a checklist that proves nothing.

    WHAT `READY_FOR_PICKUP` MEANS: the device passed its tests and may go to the
    handover stage. It does NOT mean the customer was told — this platform has
    no notification channel — and it does not mean anything was paid, collected
    or closed. M12 is the handover.
    """
    locked = RepairOrder.objects.select_for_update().get(pk=repair_order.pk)
    if locked.status != RepairStatusCode.QUALITY_CONTROL:
        raise QualityError('Esta orden no está en control de calidad.')

    check = open_quality_check(locked)
    if check is None:
        raise QualityError('Esta orden no tiene un control de calidad abierto.')
    locked_check = QualityCheck.objects.select_for_update().get(pk=check.pk)

    pending = list(unresolved_required_items(locked_check))
    if pending:
        raise QualityError(
            f'Faltan {len(pending)} punto(s) obligatorio(s) por responder.'
        )
    failed = list(failing_items(locked_check))
    if failed:
        raise QualityError(
            f'{len(failed)} punto(s) no pasaron. Envía el equipo de vuelta a '
            'reparación en lugar de aprobar el control.'
        )

    locked_check.status = QualityCheckStatus.PASSED
    locked_check.completed_at = timezone.now()
    locked_check.completed_by = actor
    if notes:
        locked_check.notes = notes
    locked_check.save(update_fields=[
        'status', 'completed_at', 'completed_by', 'notes', 'updated_at',
    ])

    _apply_transition(
        locked,
        to_status=RepairStatusCode.READY_FOR_PICKUP,
        actor=actor,
        origin=RepairStatusHistory.ORIGIN_INTERNAL,
        comment='',
        request=request,
    )

    AdminAuditLog.log(
        actor=actor,
        action='service_quality_passed',
        target_type='quality_check',
        target_id=locked_check.pk,
        metadata={
            'repair_order_id': locked.pk,
            'number': locked.number,
            'execution_id': locked_check.execution_id,
        },
        request=request,
        company=locked.company,
    )
    return locked_check


@transaction.atomic
def fail_quality_check(*, repair_order, notes: str = '', actor=None, request=None):
    """
    The device did not pass. QUALITY_CONTROL → IN_REPAIR, with a NEW execution.

    AT LEAST ONE POINT MUST HAVE FAILED. Sending a device back with nothing
    marked wrong would leave a technician a rework order and no way to know what
    to rework.

    THE PREVIOUS EXECUTION IS NOT REOPENED. It is finished, immutable evidence
    of what was done and what it cost in parts, and its `PartUsage` rows stay
    exactly where they are. The rework is a SECOND execution, which M10's
    partial unique constraint was designed to allow.

    WHY THE NEW EXECUTION IS OPENED HERE rather than waiting for somebody to
    press "start" again: the device is already on the bench and the person who
    just failed it knows what is wrong. Leaving the order in `in_repair` with no
    open execution would be a trap — `record_part_usage` refuses without one, so
    the technician's next action would fail for a reason nobody explained.

    NO STOCK MOVES. A part that failed a test is still physically fitted; a
    failed inspection is not a returned component. If the rework needs a part
    nobody approved, it goes back through diagnosis and a new quote exactly as
    M10 requires — this function opens no shortcut around that.
    """
    locked = RepairOrder.objects.select_for_update().get(pk=repair_order.pk)
    if locked.status != RepairStatusCode.QUALITY_CONTROL:
        raise QualityError('Esta orden no está en control de calidad.')

    check = open_quality_check(locked)
    if check is None:
        raise QualityError('Esta orden no tiene un control de calidad abierto.')
    locked_check = QualityCheck.objects.select_for_update().get(pk=check.pk)

    failed = list(failing_items(locked_check))
    if not failed:
        raise QualityError(
            'Marca al menos un punto como falla antes de devolver el equipo a '
            'reparación.'
        )

    locked_check.status = QualityCheckStatus.FAILED
    locked_check.completed_at = timezone.now()
    locked_check.completed_by = actor
    if notes:
        locked_check.notes = notes
    locked_check.save(update_fields=[
        'status', 'completed_at', 'completed_by', 'notes', 'updated_at',
    ])

    _apply_transition(
        locked,
        to_status=RepairStatusCode.IN_REPAIR,
        actor=actor,
        origin=RepairStatusHistory.ORIGIN_INTERNAL,
        comment='',
        request=request,
    )

    rework = RepairExecution(
        company_id=locked.company_id,
        repair_order=locked,
        started_at=timezone.now(),
        started_by=actor,
    )
    rework.save()

    AdminAuditLog.log(
        actor=actor,
        action='service_quality_failed',
        target_type='quality_check',
        target_id=locked_check.pk,
        metadata={
            'repair_order_id': locked.pk,
            'number': locked.number,
            'failed_items': [i.code for i in failed],
            'previous_execution_id': locked_check.execution_id,
            'rework_execution_id': rework.pk,
        },
        request=request,
        company=locked.company,
    )
    return locked_check


# ===========================================================================
# M12 / BR-005E — the handover
# ===========================================================================
#
# LOCK ORDER, unchanged and extended the same way: RepairOrder, then the row
# that belongs to it. Nothing here touches stock, quality or a quote.
#
# THERE IS NO PAYMENT CHECK, AND THAT IS A FINDING RATHER THAN AN OMISSION.
# `PaymentTransaction.order` is a non-null FK to the e-commerce `Order`, with no
# tenant column and no generic relation, so a `RepairOrder` cannot be paid
# through it as the schema stands. Delivery therefore does not verify a balance
# and does not pretend one was settled. Writing `paid = True` here to make the
# flow look finished would be the worst kind of lie: the kind a shop believes.
# Service payment is its own phase.


class DeliveryError(ServiceError):
    """The handover is not legal in this state. Views render this as HTTP 400."""


class DeliveryConflict(ServiceError):
    """
    The idempotency key has been used, for a different handover.

    Its own class and its own status, because it is not bad input: it is a
    client reusing a key it minted for something else. Replaying the SAME
    request returns the original record and raises nothing.
    """


#: The only state a repair may be handed over from. It passed its tests.
DELIVERABLE_STATES: frozenset[str] = frozenset({RepairStatusCode.READY_FOR_PICKUP})


def _delivery_fingerprint(*, recipient_name: str) -> str:
    """
    A stable SHA-256 of what was asked for.

    Same shape as `_usage_fingerprint`: the key says "this is the same
    attempt", the fingerprint says "and it is handing the device to the same
    person". A replay under a reused key naming somebody ELSE is a client bug,
    and answering it with the original record would silently ignore what the
    second request actually said.
    """
    canonical = json.dumps(
        {'recipient': (recipient_name or '').strip()},
        sort_keys=True, separators=(',', ':'),
    )
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def delivery_for(repair_order):
    """The handover on this order, or None."""
    return RepairDelivery.objects.filter(repair_order=repair_order).first()


def deliver_repair(
    *, repair_order, recipient_name: str, notes: str = '',
    idempotency_key: str = '', actor=None, request=None,
):
    """
    The device leaves with somebody. READY_FOR_PICKUP → DELIVERED.

    This wrapper is NOT the transaction — `_deliver_repair` is. It exists for
    one case: two counters racing the same idempotency key. The loser's INSERT
    fails, its whole transaction rolls back, and only then is it safe to hand
    back the record the winner wrote. Same shape M10 used for a part.
    """
    key = (idempotency_key or '').strip()
    try:
        return _deliver_repair(
            repair_order=repair_order, recipient_name=recipient_name, notes=notes,
            idempotency_key=key, actor=actor, request=request,
        )
    except _DeliveryRaceLost:
        winner = (
            RepairDelivery.objects
            .filter(company_id=repair_order.company_id, idempotency_key=key)
            .first()
        )
        if winner is None:
            raise DeliveryError('No se pudo registrar la entrega.') from None
        expected = _delivery_fingerprint(recipient_name=recipient_name)
        if winner.request_fingerprint != expected:
            raise DeliveryConflict(
                'Esa clave ya se usó para una entrega diferente.'
            ) from None
        return winner


class _DeliveryRaceLost(Exception):
    """Internal. Another counter committed this key while we worked."""


@transaction.atomic
def _deliver_repair(
    *, repair_order, recipient_name: str, notes: str = '',
    idempotency_key: str = '', actor=None, request=None,
):
    """
    The transactional body. See `deliver_repair` for the wrapper's job.

    ONE OPERATION, NOT A STATUS WRITE. `transition_repair_order` refuses
    `delivered` outright — it is in `EVENT_ONLY_STATES` — because moving the
    order without recording WHO took the device would be a handover with nobody
    on the other side of it.
    """
    locked = RepairOrder.objects.select_for_update().get(pk=repair_order.pk)

    key = (idempotency_key or '').strip()
    name = (recipient_name or '').strip()
    if not name:
        raise DeliveryError('Registra quién recibió el equipo.')

    # Idempotency FIRST, before any rule that could have moved. A replayed
    # request is answered from the key, not re-judged: the order is `delivered`
    # now BECAUSE of it, and re-validating would refuse the retry of something
    # that already worked.
    fingerprint = _delivery_fingerprint(recipient_name=name)
    if key:
        existing = (
            RepairDelivery.objects
            .filter(company_id=locked.company_id, idempotency_key=key)
            .first()
        )
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise DeliveryConflict(
                    'Esa clave ya se usó para una entrega diferente.'
                )
            return existing

    already = delivery_for(locked)
    if already is not None:
        raise DeliveryError('Este equipo ya fue entregado.')

    if locked.status not in DELIVERABLE_STATES:
        raise DeliveryError(
            'Solo se puede entregar un equipo que aprobó el control de calidad.'
        )

    delivery = RepairDelivery(
        company_id=locked.company_id,
        repair_order=locked,
        delivered_by=actor,
        recipient_name=name,
        notes=notes or '',
        delivered_at=timezone.now(),
        idempotency_key=key,
        request_fingerprint=fingerprint if key else '',
    )
    try:
        # Its own savepoint: an IntegrityError marks the enclosing transaction
        # for rollback, and querying inside it afterwards raises rather than
        # answering.
        with transaction.atomic():
            delivery.save()
    except IntegrityError:
        if not key:
            raise
        raise _DeliveryRaceLost() from None

    # Terminal, so `_apply_transition` stamps `closed_at`.
    _apply_transition(
        locked,
        to_status=RepairStatusCode.DELIVERED,
        actor=actor,
        origin=RepairStatusHistory.ORIGIN_INTERNAL,
        comment='',
        request=request,
    )

    AdminAuditLog.log(
        actor=actor,
        action='service_repair_delivered',
        target_type='repair_delivery',
        target_id=delivery.pk,
        metadata={
            'repair_order_id': locked.pk,
            'number': locked.number,
            # The recipient's NAME is not in the audit metadata. The delivery row
            # holds it, and copying personal data into a second table means two
            # places to honour a deletion request from.
            'branch_id': locked.branch_id,
        },
        request=request,
        company=locked.company,
    )
    return delivery


# ---------------------------------------------------------------------------
# M12B — los emisores concretos
# ---------------------------------------------------------------------------
#
# THE TEXTS ARE WRITTEN HERE, FROM SAFE MATERIAL ONLY: the event, the
# customer-visible status label, and the order number. Never from
# `internal_notes`, a diagnosis, a QC note, a cost, a supplier or the
# technician's name. A notification is a summary that says "go and look"; the
# detail lives in its module, behind its own authorisation.


def _emit_assignment_created(*, order, assignment, technician):
    from . import notification_events as ev
    from . import notification_services as notif

    notif.emit(
        company=order.company,
        event_type=ev.SERVICE_ASSIGNMENT_CREATED,
        # Keyed on the ASSIGNMENT row: a reassignment is a new row and new
        # news; re-running the same assignment is neither.
        event_key=ev.event_key(ev.SERVICE_ASSIGNMENT_CREATED, 'assignment', assignment.pk),
        title='Nueva reparación asignada',
        body=f'{_order_label(order)} · {order.branch.name}',
        target_type='repair_order', target_id=order.pk,
        users=[technician],
        priority=Notification.Priority.ACTION,
    )


def _emit_quote_available(*, order, quote):
    from . import notification_events as ev
    from . import notification_services as notif

    customer = _customer_of(order)
    if customer is None:
        return
    notif.emit(
        company=order.company,
        event_type=ev.SERVICE_QUOTE_AVAILABLE,
        # The revision is part of the identity: republishing revision 2 is a
        # replay, but revision 3 is a different proposal.
        event_key=ev.event_key(
            ev.SERVICE_QUOTE_AVAILABLE, 'quote', quote.pk, quote.revision,
        ),
        title='Tienes una cotización pendiente de revisión',
        body=f'{_order_label(order)} · revisa y aprueba o rechaza.',
        target_type='repair_order', target_id=order.pk,
        customers=[customer],
        priority=Notification.Priority.ACTION,
    )


#: What the customer is told when the order reaches each state.
#:
#: ONLY THE STATES WORTH A MESSAGE. `diagnosing` and `quality_control` are real
#: and visible in the timeline, but a notification for each would train people
#: to ignore them. Anything absent here still moves the order; it just does not
#: interrupt anybody.
_CUSTOMER_STATUS_MESSAGES = {
    RepairStatusCode.WAITING_PARTS: (
        'Tu reparación está esperando un repuesto',
        'Te avisaremos en cuanto podamos continuar.',
    ),
    RepairStatusCode.READY_FOR_PICKUP: (
        'Tu equipo está listo para recoger',
        'Puedes pasar a retirarlo cuando quieras.',
    ),
    RepairStatusCode.DELIVERED: (
        'Tu equipo fue entregado',
        'Gracias por confiar en nosotros.',
    ),
}


def _emit_status_changed(*, order, to_status):
    """
    One occurrence, one event, however many audiences it concerns.

    An order becoming collectable is a SINGLE thing that happened. The customer
    should hear it and so should whoever can hand the device back — but two
    events would mean two rows describing one moment, and the replay guard
    would then only protect each of them from itself. `emit()` fans one event
    out to both audiences, which is what the model was shaped for.
    """
    from . import notification_events as ev
    from . import notification_services as notif

    message = _CUSTOMER_STATUS_MESSAGES.get(to_status)
    customer = _customer_of(order)
    is_ready = to_status == RepairStatusCode.READY_FOR_PICKUP

    if message is None and not is_ready:
        return

    event_type = (
        ev.SERVICE_DELIVERED if to_status == RepairStatusCode.DELIVERED
        else ev.SERVICE_READY_FOR_PICKUP if is_ready
        else ev.SERVICE_STATUS_CHANGED
    )

    staff = []
    if is_ready:
        # Only the people who can actually complete the handover, and only in
        # the branch holding the device.
        staff = notif.resolve_internal_recipients(
            order.company, capability='service.delivery.manage', branch=order.branch,
        )

    if is_ready:
        title, body = 'Tu equipo está listo para recoger', 'Puedes pasar a retirarlo cuando quieras.'
    else:
        title, body = message

    notif.emit(
        company=order.company,
        event_type=event_type,
        # (order, status): an order that returns to `waiting_parts` twice says
        # it once, because from the customer's side it is the same sentence and
        # the repeat reads as a mistake.
        event_key=ev.event_key(event_type, 'repair_order', order.pk, to_status),
        title=title,
        body=f'{body} {_order_label(order)}.',
        target_type='repair_order', target_id=order.pk,
        users=staff,
        customers=[customer] if customer is not None else [],
        priority=Notification.Priority.ACTION if is_ready else Notification.Priority.INFO,
    )


def _emit_quote_decision(*, order, quote, approved):
    """The customer decided. The shop needs to know; the customer already does."""
    from . import notification_events as ev
    from . import notification_services as notif

    event_type = ev.SERVICE_QUOTE_APPROVED if approved else ev.SERVICE_QUOTE_REJECTED
    technician = order.assignments.filter(unassigned_at__isnull=True).first()
    recipients = [technician.technician] if technician else []
    if not recipients:
        recipients = notif.resolve_internal_recipients(
            order.company, capability='service.orders.manage', branch=order.branch,
        )
    notif.emit(
        company=order.company,
        event_type=event_type,
        event_key=ev.event_key(event_type, 'quote', quote.pk, quote.revision),
        title='Cotización aprobada' if approved else 'Cotización rechazada',
        body=f'{_order_label(order)} · el cliente respondió.',
        target_type='repair_order', target_id=order.pk,
        users=recipients,
        priority=Notification.Priority.ACTION,
    )
