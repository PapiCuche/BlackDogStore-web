"use client";

/**
 * The three things a reader must know before reading a number here.
 *
 *   1. What this is NOT: complete accounting, or a filed declaration. Always
 *      shown, even before the first answer arrives.
 *   2. Which rules are PROVISIONAL (`policy.status`), pending the owner.
 *   3. Whether buyers' documents are masked — the server decides (`unmasked`).
 */
import { policyLines, type Envelope } from "../../lib/accounting-api";

export const DECLARATION_NOTICE =
  "Nada de lo que aparece aquí es una declaración presentada ante SUNAT, ni una conciliación con SUNAT o el SIRE. " +
  "Tener XML o CDR no significa «declarado»; un cierre interno tampoco.";

export function AccountingNotices({ envelope }: { envelope: Envelope | null }) {
  const provisional = envelope?.policy.status === "provisional";
  return (
    <div className="space-y-3">
      <aside
        aria-label="Alcance del centro contable"
        className="rounded-xl border border-bd-border bg-surface px-4 py-3 text-sm leading-6 text-foreground"
      >
        <p className="font-semibold">Sólo lectura · no es una declaración</p>
        <p className="mt-1 text-muted">{DECLARATION_NOTICE}</p>
        {envelope?.notice ? <p className="mt-1 text-muted">{envelope.notice}</p> : null}
      </aside>

      {provisional && envelope ? (
        <aside
          aria-labelledby="accounting-policy-title"
          data-testid="accounting-policy-banner"
          className="rounded-xl border border-warning-border bg-warning-surface px-4 py-3 text-sm leading-6"
        >
          <p id="accounting-policy-title" className="font-semibold text-warning">
            Reglas provisionales, pendientes de decisión del propietario
          </p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-foreground">
            {policyLines(envelope.policy).map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          {envelope.policy.pending_owner_decision.length ? (
            <p className="mt-1 text-xs text-muted">
              Pendientes: {envelope.policy.pending_owner_decision.join(", ")}. Las cifras pueden cambiar cuando se decidan.
            </p>
          ) : null}
        </aside>
      ) : null}

      {envelope && !envelope.unmasked ? (
        <p className="text-xs text-muted" data-testid="accounting-masked-note">
          Los documentos de los compradores se muestran enmascarados. Verlos completos requiere el permiso
          «Exportar el centro contable».
        </p>
      ) : null}
    </div>
  );
}
