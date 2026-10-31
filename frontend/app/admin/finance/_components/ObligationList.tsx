"use client";

/**
 * The rows. Each one says what is owed, to whom, when, and offers only the actions the person may take: the
 * server re-checks every one of them, the interface just does not dangle what it knows will be refused.
 *
 * Rows are cards, not table rows: a card wraps at any width, so the page never scrolls sideways at 390 px.
 */
import { formatDate, relativeDue } from "../../lib/finance-dates";
import { isManagedHere, type Obligation } from "../../lib/finance-api";
import { Balance, StateBadge, linkButtonClass } from "./FinanceBits";
import { isOwed, type DetailPermissions } from "./ObligationDetail";

export function ObligationRow({
  item,
  today,
  permissions,
  onView,
  onPay,
  onSnooze,
  onEditRule,
}: {
  item: Obligation;
  today: string;
  permissions: DetailPermissions;
  onView: (item: Obligation, section?: "history") => void;
  onPay: (item: Obligation) => void;
  onSnooze: (item: Obligation) => void;
  onEditRule: (ruleId: number) => void;
}) {
  const owed = isOwed(item) && isManagedHere(item);
  const when = relativeDue(item.due_date, today);
  const meta = [item.payee_name || item.supplier?.name, item.category?.name, item.branch?.name ?? "General"]
    .filter(Boolean).join(" · ");
  return (
    <li data-obligation-id={item.id} className="rounded-xl border border-bd-border bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1 basis-56">
          <p className="break-words font-semibold text-foreground">{item.concept}</p>
          <p className="mt-0.5 break-words text-xs text-muted">{meta}</p>
          <p className="mt-1 text-sm text-foreground">
            {formatDate(item.due_date)}{" "}
            {when ? <span className="text-muted">({when})</span> : null}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <StateBadge state={item.state} />
            {item.partially_paid ? (
              <span className="rounded-full border border-bd-border px-2.5 py-0.5 text-xs font-semibold text-muted">Parcial</span>
            ) : null}
            {item.disputed ? (
              <span className="rounded-full border border-warning-border bg-warning-surface px-2.5 py-0.5 text-xs font-semibold text-warning">En disputa</span>
            ) : null}
            {item.snoozed_until ? (
              <span className="rounded-full border border-info-border bg-info-surface px-2.5 py-0.5 text-xs font-semibold text-info">
                Mis avisos pospuestos hasta {formatDate(item.snoozed_until)}
              </span>
            ) : null}
            {item.rule_id ? <span className="text-xs text-muted">Pago fijo</span> : null}
          </div>
        </div>
        <Balance item={item} />
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" className={linkButtonClass} onClick={() => onView(item)}
                aria-label={`Ver obligación: ${item.concept}`}>
          Ver obligación
        </button>
        {permissions.canRecord && owed ? (
          <button type="button" className={linkButtonClass} onClick={() => onPay(item)}
                  aria-label={`Registrar pago: ${item.concept}`}>
            Registrar pago
          </button>
        ) : null}
        {owed ? (
          <button type="button" className={linkButtonClass} onClick={() => onSnooze(item)}
                  aria-label={`Posponer aviso: ${item.concept}`}>
            Posponer aviso
          </button>
        ) : null}
        {permissions.canManage && item.rule_id ? (
          <button type="button" className={linkButtonClass} onClick={() => onEditRule(item.rule_id!)}
                  aria-label={`Editar regla: ${item.concept}`}>
            Editar regla
          </button>
        ) : null}
        <button type="button" className={linkButtonClass} onClick={() => onView(item, "history")}
                aria-label={`Ver historial: ${item.concept}`}>
          Ver historial
        </button>
      </div>
    </li>
  );
}
