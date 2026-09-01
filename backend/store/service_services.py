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

from decimal import Decimal

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from . import sequences
from .models import (
    AdminAuditLog,
    CompanySettings,
    Customer,
    Device,
    Membership,
    Product,
    RepairDiagnostic,
    RepairOrder,
    RepairQuote,
    RepairQuoteDecision,
    RepairQuoteItem,
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
        RepairStatusCode.APPROVED,
        RepairStatusCode.REJECTED,
        # Withdrawing the quote puts the order back where a new revision is
        # composed. Event-only — see `EVENT_ONLY_EDGES`.
        RepairStatusCode.DIAGNOSING,
        RepairStatusCode.CANCELLED,
    ),
    # M9. The customer said go ahead; the work has not started, because there is
    # no execution module yet. An approved order can still be cancelled — a
    # customer changes their mind, a part turns out to be unobtainable — and
    # that is a decision the shop records, not a state it is trapped in.
    RepairStatusCode.APPROVED: (
        RepairStatusCode.CANCELLED,
    ),
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
EVENT_ONLY_STATES: frozenset[str] = frozenset({
    RepairStatusCode.WAITING_APPROVAL,
    RepairStatusCode.APPROVED,
    RepairStatusCode.REJECTED,
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
TERMINAL_STATES: frozenset[str] = frozenset({RepairStatusCode.CANCELLED})

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
    return record
