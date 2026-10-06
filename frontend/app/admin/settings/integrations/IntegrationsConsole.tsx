"use client";

/**
 * Configuración › Integraciones — la consola del MASTER.
 *
 * Una tarjeta por servicio externo, con su estado y su última prueba; «Configurar»
 * abre el editor. La pantalla no conoce a ningún proveedor: los nombres, los
 * campos y lo que se le puede pedir a una prueba los declara el servidor.
 *
 * Quién puede estar aquí lo decide el servidor en cada llamada (403 a quien no
 * sea master). La página que monta este componente ni siquiera lo monta para
 * los demás.
 */

import { useCallback, useEffect, useState } from "react";

import {
  fetchIntegrations, STATE_LABELS, STATE_TONES, stateLabel, testStatusLabel,
  type CompanyIntegrationSummary, type Integration, type IntegrationSummary,
} from "@/app/lib/integrations";

import { Button, ErrorNote, Pill, dateTime } from "../../service/components/ServiceUi";
import { IntegrationEditor } from "./IntegrationEditor";

const CATEGORY_LABELS: Record<string, string> = {
  email: "Correo", payments: "Pagos", messaging: "Mensajería", identity: "Acceso", fiscal: "Facturación",
};
const CATEGORY_ORDER = ["email", "payments", "messaging", "identity", "fiscal"];

export const MASTER_NOTICE =
  "Estas credenciales controlan servicios externos de la empresa. Sólo usuarios MASTER pueden modificarlas.";

type Selection = { id: string; companyId: number | null };

const isPerCompany = (item: IntegrationSummary): item is CompanyIntegrationSummary => item.scope === "company";

function Card({ label, description, children }: { label: string; description: string; children: React.ReactNode }) {
  return (
    <article aria-label={label} className="flex flex-col rounded-xl border border-bd-border bg-surface p-5">
      <h3 className="font-display text-sm font-semibold tracking-wide text-foreground">{label}</h3>
      <p className="mt-1 text-xs text-muted">{description}</p>
      <div className="mt-4 flex flex-1 flex-col justify-end gap-3">{children}</div>
    </article>
  );
}

function PlatformCard({ item, onConfigure }: { item: Integration; onConfigure: () => void }) {
  const row = item.active ?? item.draft;
  return (
    <Card label={item.label} description={item.description}>
      <div className="flex flex-wrap items-center gap-2">
        <Pill label={stateLabel(item)} tone={STATE_TONES[item.state]} />
        {item.source === "env" ? <span className="text-[11px] text-muted">Configurado mediante entorno</span> : null}
      </div>
      <p className="text-xs text-muted">
        {row?.last_tested_at
          ? `Última prueba: ${testStatusLabel(row.last_test_status)} · ${dateTime(row.last_tested_at)}`
          : "Sin probar"}
      </p>
      <div><Button tone="primary" onClick={onConfigure}>Configurar</Button></div>
    </Card>
  );
}

function CompanyCard({
  item, onConfigure,
}: { item: CompanyIntegrationSummary; onConfigure: (companyId: number) => void }) {
  const [companyId, setCompanyId] = useState("");
  const configured = item.companies.filter((c) => c.state !== "NOT_CONFIGURED").length;
  const selectId = `integration-company-${item.id}`;
  return (
    <Card label={item.label} description={item.description}>
      <p className="text-xs text-muted">
        {`${configured} de ${item.companies.length} ${item.companies.length === 1 ? "empresa configurada" : "empresas configuradas"}`}
      </p>
      <div>
        <label htmlFor={selectId} className="block text-xs text-foreground/50">Empresa</label>
        <select
          id={selectId}
          value={companyId}
          onChange={(e) => setCompanyId(e.target.value)}
          className="mt-1.5 w-full rounded-xl border border-bd-border bg-background px-3 py-2.5 text-sm text-foreground outline-none focus:border-foreground/25"
        >
          <option value="">Elige una empresa…</option>
          {item.companies.map((company) => (
            <option key={company.id} value={company.id}>
              {`${company.name} — ${STATE_LABELS[company.state]}`}
            </option>
          ))}
        </select>
      </div>
      <div>
        <Button tone="primary" disabled={!companyId} onClick={() => onConfigure(Number(companyId))}>Configurar</Button>
      </div>
    </Card>
  );
}

export function IntegrationsConsole() {
  const [items, setItems] = useState<IntegrationSummary[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] = useState<Selection | null>(null);

  const load = useCallback(() => {
    let cancelled = false;
    fetchIntegrations().then(
      (loaded) => { if (!cancelled) { setItems(loaded.results); setError(null); } },
      (err) => { if (!cancelled) setError(err); },
    );
    return () => { cancelled = true; };
  }, []);

  useEffect(() => (selected ? undefined : load()), [load, selected]);

  if (selected) {
    return <IntegrationEditor id={selected.id} companyId={selected.companyId} onBack={() => setSelected(null)} />;
  }

  const groups = CATEGORY_ORDER
    .concat((items ?? []).map((item) => item.category).filter((c) => !CATEGORY_ORDER.includes(c)))
    .filter((category, index, all) => all.indexOf(category) === index)
    .map((category) => ({ category, members: (items ?? []).filter((item) => item.category === category) }))
    .filter((group) => group.members.length);

  return (
    <div className="space-y-6">
      <p role="note" className="rounded-xl border border-warning-border bg-warning-surface px-4 py-3 text-sm text-warning">
        {MASTER_NOTICE}
      </p>
      <ErrorNote error={error} />
      {!items && !error ? <p className="text-sm text-muted">Cargando…</p> : null}
      {groups.map(({ category, members }) => (
        <section key={category} className="space-y-3">
          <h2 className="font-display text-base font-semibold tracking-wide text-foreground">
            {CATEGORY_LABELS[category] ?? category}
          </h2>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {members.map((item) => (isPerCompany(item) ? (
              <CompanyCard key={item.id} item={item} onConfigure={(companyId) => setSelected({ id: item.id, companyId })} />
            ) : (
              <PlatformCard key={item.id} item={item} onConfigure={() => setSelected({ id: item.id, companyId: null })} />
            )))}
          </div>
        </section>
      ))}
    </div>
  );
}
