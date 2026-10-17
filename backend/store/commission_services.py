"""
FINANCIAL-CLOSURE (owner, 2026-10-16) — settling a seller's commission.

«Comisión de venta cancelada: anular únicamente comisión todavía no liquidada.
Si ya se liquidó, registrar ajuste compensatorio o saldo recuperable,
conservando historial y evitando doble ajuste.»

A commission is ACCRUED at the sale (`pos_services`), VOIDED if the sale is
annulled before it is paid out (`order_cancellation`), and SETTLED once the
company pays it to the seller (here). A settled row is history: nothing edits
or deletes it afterwards — an annulment then writes a `CommissionAdjustment`
(`order_cancellation.compensate_commission`).

THERE IS NO PAYROLL FLOW YET. This is the one writer of «settled», so that the
day a liquidation screen exists it cannot settle twice, settle a voided row or
settle the commission of a sale that is no longer a sale. Authority is the
caller's (the future flow); the service checks the state.
"""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from .models import AdminAuditLog, Order, SalesCommission


class CommissionError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def settle(commission, user, *, request=None) -> SalesCommission:
    """Mark an ACCRUED commission of a live sale as paid out to its seller. Once."""
    actor = user if getattr(user, 'is_authenticated', False) else None
    with transaction.atomic():
        # The order first, the lock a cancellation takes: the two queue.
        order = Order.objects.select_for_update(of=('self',)).get(pk=commission.order_id)
        locked = SalesCommission.objects.select_for_update().get(pk=commission.pk)
        if locked.status != SalesCommission.STATUS_ACCRUED:
            raise CommissionError('not_accrued', 'Sólo se liquida una comisión devengada.')
        if order.status == Order.Status.CANCELLED:
            raise CommissionError('sale_cancelled', 'La venta de esta comisión fue anulada.')
        locked.status = SalesCommission.STATUS_SETTLED
        locked.settled_at = timezone.now()
        locked.settled_by = actor
        locked.save(update_fields=['status', 'settled_at', 'settled_by', 'updated_at'])
        AdminAuditLog.log(
            actor=actor, action='commission_settled', target_type='sales_commission', target_id=locked.pk,
            metadata={'commission_id': locked.pk, 'order_id': order.pk, 'seller_id': locked.seller_id,
                      'amount': str(locked.amount)},
            request=request, company=order.company,
        )
    return locked


__all__ = ['CommissionError', 'settle']
