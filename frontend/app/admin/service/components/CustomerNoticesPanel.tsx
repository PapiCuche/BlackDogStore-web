"use client";

/**
 * What the customer was told about this order, and whether it reached them.
 *
 * Every state here is READ from the server. "Enviado" means a provider took the
 * message; "entregado" and "leído" are what the provider reported afterwards.
 * This panel never writes to a customer: it records their consent and asks the
 * server to try a failed message again.
 */

import { useState } from "react";

import {
  retryWhatsAppNotice, setWhatsAppConsent, type ServiceCustomerNotice,
} from "@/app/lib/service-console";

import { Button, Confirm, dateTime, ErrorNote } from "./ServiceUi";

const CAP_CUSTOMERS_MANAGE = "service.customers.manage";
const CAP_ORDERS_MANAGE = "service.orders.manage";

const EMAIL: Record<string, string> = {
  pending: "pendiente",
  sent: "enviado",
  failed: "fallido",
  skipped: "omitido (sin destinatario)",
};

const WHATSAPP: Record<string, string> = {
  pending: "pendiente de envío",
  sent: "enviado",
  delivered: "entregado",
  read: "leído",
  failed: "falló",
  skipped: "no enviado",
};

const WHATSAPP_TONE: Record<string, string> = {
  delivered: "text-success",
  read: "text-success",
  failed: "text-danger",
};

export function CustomerNoticesPanel({
  slug, orderId, customerId, notices, whatsapp, may, onChanged,
}: {
  slug: string;
  orderId: number;
  customerId: number;
  notices: ServiceCustomerNotice[];
  whatsapp: { enabled: boolean; customer_opt_in: boolean };
  may: (capability: string) => boolean;
  /** Something changed on the server: the caller reloads the order. */
  onChanged: () => void;
}) {
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function act(action: () => Promise<unknown>) {
    setWorking(true);
    setError(null);
    try {
      await action();
      onChanged();
    } catch (err) {
      setError(err);
    } finally {
      setWorking(false);
    }
  }

  return (
    <div className="space-y-4">
      {whatsapp.enabled ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-bd-border bg-background p-3">
          <p className="text-xs text-muted">
            {whatsapp.customer_opt_in
              ? "El cliente aceptó recibir avisos por WhatsApp."
              : "El cliente no ha aceptado recibir avisos por WhatsApp: no se le escribe."}
          </p>
          {may(CAP_CUSTOMERS_MANAGE) ? (
            whatsapp.customer_opt_in ? (
              <Confirm
                label="Registrar que ya no acepta"
                question="¿El cliente pidió no recibir más avisos?"
                disabled={working}
                onConfirm={() => void act(() => setWhatsAppConsent(slug, customerId, false))}
              />
            ) : (
              <Confirm
                label="Registrar que acepta"
                question="¿El cliente te dijo que acepta recibirlos?"
                tone="primary"
                disabled={working}
                onConfirm={() => void act(() => setWhatsAppConsent(slug, customerId, true))}
              />
            )
          ) : null}
        </div>
      ) : (
        <p className="text-xs text-muted">
          Esta empresa no tiene activados los avisos por WhatsApp. Los avisos quedan en la cuenta
          del cliente y, cuando corresponde, se envían por correo.
        </p>
      )}

      {notices.length === 0 ? (
        <p className="text-sm text-muted">Sin avisos registrados para esta orden.</p>
      ) : (
        <ul className="divide-y divide-bd-border">
          {notices.map((notice) => {
            const state = notice.whatsapp_status;
            const shown = whatsapp.enabled && state !== "not_applicable";
            return (
              <li key={notice.id} className="space-y-1 py-3 first:pt-0 last:pb-0">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="text-sm text-foreground">{notice.title}</span>
                  <span className="text-xs text-muted">{dateTime(notice.created_at)}</span>
                </div>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
                  {notice.email_status !== "not_applicable" ? (
                    <span>Correo: {EMAIL[notice.email_status] ?? notice.email_status}</span>
                  ) : null}
                  {shown ? (
                    <span className={WHATSAPP_TONE[state] ?? ""}>
                      WhatsApp: {WHATSAPP[state] ?? state}
                      {notice.whatsapp_recipient ? ` · ${notice.whatsapp_recipient}` : ""}
                    </span>
                  ) : null}
                  {shown && state === "failed" && may(CAP_ORDERS_MANAGE) ? (
                    <Button disabled={working} onClick={() => void act(() => retryWhatsAppNotice(slug, orderId, notice.id))}>
                      Reintentar WhatsApp
                    </Button>
                  ) : null}
                </div>
                {shown && notice.whatsapp_detail && (state === "failed" || state === "skipped") ? (
                  <p className="text-xs text-muted">Motivo: {notice.whatsapp_detail}</p>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
      <ErrorNote error={error} />
    </div>
  );
}
