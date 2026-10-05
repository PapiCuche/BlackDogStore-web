"use client";

/**
 * Ficha de cliente › Cuenta.
 *
 * Dice si la ficha está vinculada a una cuenta de la plataforma y, a quien
 * puede gestionar clientes del taller, le deja deshacer esa vinculación.
 *
 * Desvincular QUITA acceso: la cuenta deja de ver las reparaciones de este
 * cliente. No da acceso a nadie, y volver a vincular sigue pidiendo el enlace de
 * una orden y el documento del cliente. El servidor exige el motivo, comprueba
 * el permiso por su cuenta y lo deja en el registro de auditoría.
 */

import { useState } from "react";

import { unlinkCustomerAccount } from "@/app/lib/service-console";
import { internalButtonClass } from "./internal-ui";

type Props = {
  /** La empresa en la que se trabaja. Sin ella no hay a quién pedirlo. */
  slug: string | null;
  customerId: number;
  hasAccount: boolean;
  /** `service.customers.manage`. El servidor lo vuelve a comprobar. */
  canUnlink: boolean;
  onChanged: () => void;
};

export function CustomerAccountLink({ slug, customerId, hasAccount, canUnlink, onChanged }: Props) {
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const cleaned = reason.trim();

  async function unlink() {
    if (!slug || !cleaned) return;
    setBusy(true);
    setError(null);
    try {
      await unlinkCustomerAccount(slug, customerId, cleaned);
      setAsking(false);
      setReason("");
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo desvincular la cuenta.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <p className="text-[11px] font-semibold uppercase tracking-widest text-muted">Cuenta</p>
      <p className="mt-0.5 text-sm text-foreground">
        {hasAccount ? "Tiene cuenta en la plataforma" : "Sin cuenta"}
      </p>
      {hasAccount && canUnlink && slug && !asking ? (
        <button type="button" onClick={() => setAsking(true)} className={`${internalButtonClass} mt-2`}>
          Desvincular cuenta
        </button>
      ) : null}
      {asking ? (
        <div className="mt-3 space-y-2 rounded-lg border border-bd-border p-3">
          <p className="text-xs text-muted">
            La cuenta dejará de ver las reparaciones de este cliente. No se borra la cuenta ni la ficha.
            Para vincularla otra vez hará falta el enlace de una orden y el documento del cliente.
          </p>
          <label className="block text-xs font-semibold text-foreground" htmlFor={`unlink-reason-${customerId}`}>
            Motivo
          </label>
          <textarea
            id={`unlink-reason-${customerId}`}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            maxLength={300}
            rows={2}
            className="w-full rounded-lg border border-bd-border bg-surface px-3 py-2 text-sm text-foreground"
          />
          {error ? (
            <p role="alert" className="text-xs text-danger">{error}</p>
          ) : null}
          <div className="flex gap-2">
            <button type="button" disabled={busy || !cleaned} onClick={() => void unlink()} className={internalButtonClass}>
              Desvincular
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => { setAsking(false); setError(null); }}
              className={internalButtonClass}
            >
              Cancelar
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
