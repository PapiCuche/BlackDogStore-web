"use client";

/**
 * POS-SVC-01 — take a device in for technical service from the till.
 *
 * The reception form is the workshop's own (`ServiceIntake`); the till only
 * makes the technician mandatory. Nothing here touches the basket: no product,
 * no charge, no receipt. The money for a repair is recorded later on the order,
 * against an approved quote.
 */

import { useEffect, useState } from "react";
import { ServiceIntake } from "../../service/components/ServiceIntake";
import { ErrorNote } from "../../service/components/ServiceUi";
import { fetchServiceContext, type ServiceContext } from "../../../lib/service-console";

export function PosServiceIntake({ slug, may }: {
  slug: string; may: (capability: string) => boolean;
}) {
  const [context, setContext] = useState<ServiceContext | null>(null);
  const [error, setError] = useState<unknown>(null);
  // A new key is a new form: nothing of the previous customer is carried over.
  const [round, setRound] = useState(0);

  useEffect(() => {
    let stale = false;
    fetchServiceContext(slug)
      .then((data) => { if (!stale) setContext(data); })
      .catch((err) => { if (!stale) setError(err); });
    return () => { stale = true; };
  }, [slug]);

  if (error) return <ErrorNote error={error} />;
  if (!context) return <p className="py-8 text-sm text-muted">Abriendo recepción…</p>;

  return (
    <ServiceIntake
      key={round}
      slug={slug}
      context={context}
      may={may}
      requireTechnician
      submitLabel="Crear servicio"
      onAnother={() => setRound((n) => n + 1)}
    />
  );
}
