"use client";

/**
 * H2 — one repair order, and everything the shop can do to it.
 *
 * THE SERVER OWNS THE MACHINE. `available_transitions` arrives computed and the
 * buttons below are exactly those, in the tenant's own words. There is no
 * transition table in this file: `received → diagnosing → …` is written down
 * once, on the server, and a copy here would drift the first time that one
 * changes — and the drift reads as a broken app rather than as a policy.
 *
 * Seven of the eleven lifecycle states are unreachable from that dropdown by
 * design: publishing a quote produces `waiting_approval`, the customer produces
 * `approved`/`rejected`, and starting, pausing, finishing, inspecting and
 * passing produce the rest. Each has its own operation, and each writes the row
 * that gives the state its meaning.
 *
 * THE SERVER OWNS THE ARITHMETIC. Quote totals, stock figures and the quality
 * verdict all arrive computed. This screen sends intentions and redraws.
 */

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import { AdminShell } from "../../../components/AdminShell";
import {
  InternalControlGuard,
  type InternalContext,
} from "../../../components/InternalControlGuard";
import { Button, Confirm, ErrorNote, Field, Panel, Pill, dateTime } from "../../components/ServiceUi";
import {
  CAP_DIAGNOSTIC_MANAGE,
  CAP_ORDERS_MANAGE,
  CAP_ORDERS_VIEW,
  CAP_QUALITY_MANAGE,
  CAP_REPAIR_MANAGE,
  ServiceApiError,
  addQuoteItem,
  assignTechnician,
  cancelQuote,
  completeRepair,
  createDiagnostic,
  createQuote,
  failQualityCheck,
  fetchServiceAssignmentOptions,
  fetchServiceDiagnostics,
  fetchServiceExecution,
  fetchServiceHistory,
  fetchServiceOrder,
  fetchServicePartCandidates,
  fetchServiceParts,
  fetchServiceQuality,
  fetchServiceQualityHistory,
  fetchServiceQuotes,
  makeIdempotencyKey,
  passQualityCheck,
  pauseForParts,
  publishQuote,
  recordPartUsage,
  recordQualityResult,
  removeQuoteItem,
  resumeRepair,
  reversePartUsage,
  startQualityCheck,
  startRepair,
  transitionServiceOrder,
  updateExecution,
  type ServiceDiagnostic,
  type ServiceExecution,
  type ServiceHistoryEntry,
  type ServiceOrderDetail,
  type ServicePartCandidate,
  type ServicePartUsage,
  type ServiceQualityCheck,
  type ServiceQuote,
} from "../../../../lib/service-console";

type Data = {
  order: ServiceOrderDetail;
  history: ServiceHistoryEntry[];
  diagnostics: ServiceDiagnostic[];
  quotes: ServiceQuote[];
  execution: ServiceExecution | null;
  parts: ServicePartUsage[];
  candidates: ServicePartCandidate[];
  quality: ServiceQualityCheck | null;
  qualityHistory: ServiceQualityCheck[];
  technicians: { id: number; name: string }[];
};

const RESULTS = [
  { value: "success", label: "Resuelto" },
  { value: "partial", label: "Resuelto parcialmente" },
  { value: "unresolved", label: "No resuelto" },
];

const QUALITY_RESULTS = [
  { value: "pass", label: "Correcto" },
  { value: "fail", label: "Falla" },
  { value: "not_applicable", label: "No aplica" },
];

const ITEM_TYPES = [
  { value: "labor", label: "Mano de obra" },
  { value: "part", label: "Repuesto" },
  { value: "service", label: "Servicio" },
];

function OrderContent({ ctx, orderId }: { ctx: InternalContext; orderId: number }) {
  const slug = ctx.dashboard?.company?.slug ?? null;
  const caps = ctx.dashboard?.access.capabilities ?? [];
  const may = (code: string) => caps.includes(code);

  const [data, setData] = useState<Data | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!slug) return;
    setLoading(true);
    setError(null);
    try {
      const [order, history, diagnostics, quotes, execution, parts, candidates, quality, qualityHistory, assignment] =
        await Promise.all([
          fetchServiceOrder(slug, orderId),
          fetchServiceHistory(slug, orderId),
          fetchServiceDiagnostics(slug, orderId),
          fetchServiceQuotes(slug, orderId),
          fetchServiceExecution(slug, orderId),
          fetchServiceParts(slug, orderId),
          fetchServicePartCandidates(slug, orderId),
          fetchServiceQuality(slug, orderId),
          fetchServiceQualityHistory(slug, orderId),
          may(CAP_ORDERS_MANAGE)
            ? fetchServiceAssignmentOptions(slug, orderId)
            : Promise.resolve({ current: null, technicians: [] }),
        ]);
      setData({
        order,
        history: history.results,
        diagnostics: diagnostics.results,
        quotes: quotes.results,
        execution: execution.execution,
        parts: parts.results,
        candidates: candidates.results,
        quality: quality.quality_check,
        qualityHistory: qualityHistory.results,
        technicians: assignment.technicians ?? [],
      });
    } catch (err) {
      // A 403 means the capability is gone, not that the app is broken. Reload
      // the context so the screen redraws against what the server now says.
      if (err instanceof ServiceApiError && err.isForbidden) ctx.reload();
      setError(err);
    } finally {
      setLoading(false);
    }
  }, [slug, orderId, ctx]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { void load(); }, [load]);

  /** Every write goes through here: one busy flag, one refetch, no optimism. */
  const run = useCallback(async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      await load();
    } catch (err) {
      if (err instanceof ServiceApiError && err.isForbidden) ctx.reload();
      setError(err);
    } finally {
      setBusy(false);
    }
  }, [load, ctx]);

  if (!slug) {
    return (
      <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
        <Panel><p className="text-sm text-white/60">Selecciona una empresa.</p></Panel>
      </AdminShell>
    );
  }
  if (!may(CAP_ORDERS_VIEW)) {
    return (
      <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
        <Panel>
          <p className="text-sm text-white/60">
            Tu cuenta no tiene permiso para ver el servicio técnico de esta empresa.
          </p>
        </Panel>
      </AdminShell>
    );
  }
  if (loading && !data) {
    return (
      <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
        <Panel><p className="text-sm text-white/50">Cargando orden…</p></Panel>
      </AdminShell>
    );
  }
  if (!data) {
    return (
      <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
        <Panel>
          <p className="text-sm text-rose-300">
            {error instanceof Error ? error.message : "No se encontró la orden."}
          </p>
          <div className="mt-3">
            <Link href="/admin/service" className="text-xs text-white/60 hover:underline">
              ← Volver a las órdenes
            </Link>
          </div>
        </Panel>
      </AdminShell>
    );
  }

  const { order } = data;

  return (
    <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
      <div className="space-y-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <Link href="/admin/service" className="text-xs text-white/40 hover:underline">
              ← Órdenes de servicio
            </Link>
            <h1 className="mt-1 text-xl font-semibold">{order.number}</h1>
            <p className="mt-1 text-sm text-white/50">
              {order.customer_name} · {order.device_summary} · {order.branch_name}
            </p>
          </div>
          <Pill label={order.status_label} />
        </div>

        <ErrorNote error={error} />

        <OrderSummary order={order} />
        <LifecycleSection order={order} may={may} busy={busy} run={run} slug={slug} />
        <AssignmentSection data={data} may={may} busy={busy} run={run} slug={slug} orderId={orderId} />
        <DiagnosticSection data={data} may={may} busy={busy} run={run} slug={slug} orderId={orderId} />
        <QuoteSection data={data} may={may} busy={busy} run={run} slug={slug} orderId={orderId} />
        <ExecutionSection data={data} may={may} busy={busy} run={run} slug={slug} orderId={orderId} />
        <PartsSection data={data} may={may} busy={busy} run={run} slug={slug} orderId={orderId} />
        <QualitySection data={data} may={may} busy={busy} run={run} slug={slug} orderId={orderId} />
        <HistorySection history={data.history} />
      </div>
    </AdminShell>
  );
}

// ---------------------------------------------------------------------------

function OrderSummary({ order }: { order: ServiceOrderDetail }) {
  return (
    <Panel title="Recepción">
      <dl className="grid gap-4 text-sm md:grid-cols-2">
        <div>
          <dt className="text-xs text-white/40">Falla reportada</dt>
          <dd className="mt-1 text-white/80">{order.reported_issue || "—"}</dd>
        </div>
        <div>
          <dt className="text-xs text-white/40">Condición física</dt>
          <dd className="mt-1 text-white/80">{order.physical_condition || "—"}</dd>
        </div>
        <div>
          <dt className="text-xs text-white/40">Accesorios recibidos</dt>
          <dd className="mt-1 text-white/80">{order.received_accessories || "—"}</dd>
        </div>
        <div>
          <dt className="text-xs text-white/40">Notas internas</dt>
          <dd className="mt-1 text-white/80">{order.internal_notes || "—"}</dd>
        </div>
        <div>
          <dt className="text-xs text-white/40">Recibido</dt>
          <dd className="mt-1 text-white/60">
            {dateTime(order.received_at)}
            {order.received_by_name ? ` · ${order.received_by_name}` : ""}
          </dd>
        </div>
      </dl>
    </Panel>
  );
}

type SectionProps = {
  data: Data;
  may: (c: string) => boolean;
  busy: boolean;
  run: (a: () => Promise<unknown>) => Promise<void>;
  slug: string;
  orderId: number;
};

function LifecycleSection({
  order, may, busy, run, slug,
}: { order: ServiceOrderDetail; may: (c: string) => boolean; busy: boolean; run: SectionProps["run"]; slug: string }) {
  const [comment, setComment] = useState("");
  if (!may(CAP_ORDERS_MANAGE)) return null;

  return (
    <Panel
      title="Mover la orden"
      subtitle="Solo los pasos que el servidor ofrece para el estado actual. Los demás
                tienen su propia operación, más abajo."
    >
      {order.available_transitions.length === 0 ? (
        <p className="text-sm text-white/50">
          Esta orden no tiene movimientos genéricos disponibles.
        </p>
      ) : (
        <div className="space-y-3">
          <Field label="Comentario (opcional)" value={comment} onChange={setComment} />
          <div className="flex flex-wrap gap-2">
            {order.available_transitions.map((t) => (
              <Confirm
                key={t.code}
                label={t.label}
                question={`¿Mover a "${t.label}"?`}
                disabled={busy}
                onConfirm={() =>
                  void run(async () => {
                    await transitionServiceOrder(slug, order.id, t.code, comment);
                    setComment("");
                  })
                }
              />
            ))}
          </div>
        </div>
      )}
    </Panel>
  );
}

function AssignmentSection({ data, may, busy, run, slug, orderId }: SectionProps) {
  const [technicianId, setTechnicianId] = useState("");
  if (!may(CAP_ORDERS_MANAGE)) return null;

  return (
    <Panel
      title="Técnico responsable"
      subtitle="Los candidatos los da el servidor: esta pantalla no puede averiguar
                quién es personal de una empresa."
    >
      <p className="mb-3 text-sm text-white/70">
        Actual: {data.order.technician_name || "sin asignar"}
      </p>
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-xs text-white/50">
          Asignar a
          <select
            value={technicianId}
            onChange={(e) => setTechnicianId(e.target.value)}
            className="mt-1 rounded-lg border border-white/[0.08] bg-black/40 px-3 py-2 text-sm text-white"
          >
            <option value="">—</option>
            {data.technicians.map((t) => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </select>
        </label>
        <Button
          disabled={busy || !technicianId}
          onClick={() => void run(async () => {
            await assignTechnician(slug, orderId, Number(technicianId));
            setTechnicianId("");
          })}
        >
          Asignar
        </Button>
        {data.order.technician_name ? (
          <Confirm
            label="Quitar"
            question="¿Dejar la orden sin técnico?"
            disabled={busy}
            onConfirm={() => void run(() => assignTechnician(slug, orderId, null))}
          />
        ) : null}
      </div>
    </Panel>
  );
}

function DiagnosticSection({ data, may, busy, run, slug, orderId }: SectionProps) {
  const [description, setDescription] = useState("");
  const [action, setAction] = useState("");
  const [notes, setNotes] = useState("");
  const canManage = may(CAP_DIAGNOSTIC_MANAGE);
  const latest = data.diagnostics[0] ?? null;

  return (
    <Panel title="Diagnóstico" subtitle="La revisión más reciente, y las anteriores intactas.">
      {data.diagnostics.length === 0 ? (
        <p className="text-sm text-white/50">Todavía no hay diagnóstico.</p>
      ) : (
        <div className="space-y-3">
          {data.diagnostics.map((d) => (
            <div key={d.id} className="rounded-xl border border-white/[0.06] p-4">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-white/40">
                  Revisión {d.revision} · {dateTime(d.created_at)}
                  {d.diagnosed_by_name ? ` · ${d.diagnosed_by_name}` : ""}
                </span>
                <Pill label={d.status_label} tone={d.finalized_at ? "good" : "neutral"} />
              </div>
              <p className="mt-2 text-sm text-white/80">{d.description}</p>
              {d.recommended_action ? (
                <p className="mt-1 text-sm text-white/60">
                  Acción recomendada: {d.recommended_action}
                </p>
              ) : null}
              {d.internal_notes ? (
                <p className="mt-1 text-xs text-white/40">{d.internal_notes}</p>
              ) : null}
            </div>
          ))}
        </div>
      )}

      {canManage ? (
        <div className="mt-4 space-y-3 border-t border-white/[0.06] pt-4">
          <Field label="Qué se encontró" value={description} onChange={setDescription} textarea />
          <Field label="Acción recomendada" value={action} onChange={setAction} textarea />
          <Field label="Notas internas" value={notes} onChange={setNotes} placeholder="No las ve el cliente" />
          <Button
            tone="primary"
            disabled={busy || !description.trim() || !action.trim()}
            onClick={() => void run(async () => {
              await createDiagnostic(slug, orderId, {
                description: description.trim(),
                recommended_action: action.trim(),
                ...(notes.trim() ? { internal_notes: notes.trim() } : {}),
              });
              setDescription(""); setAction(""); setNotes("");
            })}
          >
            {latest ? "Nueva revisión" : "Registrar diagnóstico"}
          </Button>
        </div>
      ) : null}
    </Panel>
  );
}


function QuoteSection({ data, may, busy, run, slug, orderId }: SectionProps) {
  const [itemType, setItemType] = useState("labor");
  const [description, setDescription] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [unitPrice, setUnitPrice] = useState("");
  const canManage = may(CAP_DIAGNOSTIC_MANAGE);
  const current = data.quotes[0] ?? null;

  return (
    <Panel
      title="Cotización"
      subtitle="Todos los importes los calcula el servidor. Esta pantalla no suma nada,
                ni siquiera para previsualizar."
    >
      {data.quotes.length === 0 ? (
        <p className="text-sm text-white/50">Todavía no hay cotización.</p>
      ) : (
        <div className="space-y-4">
          {data.quotes.map((q) => (
            <div key={q.id} className="rounded-xl border border-white/[0.06] p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-xs text-white/40">
                  Revisión {q.revision} · {q.created_by_name || "—"}
                  {q.sent_at ? ` · enviada ${dateTime(q.sent_at)}` : ""}
                </span>
                <Pill
                  label={q.status_label}
                  tone={q.status === "approved" ? "good" : q.status === "rejected" ? "bad" : "neutral"}
                />
              </div>

              {q.items.length > 0 ? (
                <table className="mt-3 w-full text-left text-sm">
                  <tbody>
                    {q.items.map((item) => (
                      <tr key={item.id} className="border-t border-white/[0.05]">
                        <td className="py-2 pr-3 text-white/70">
                          {item.description}
                          <span className="ml-2 text-[11px] text-white/30">
                            {item.item_type_label}
                          </span>
                        </td>
                        <td className="py-2 pr-3 text-right text-white/50">
                          {item.quantity} × {item.unit_price}
                        </td>
                        {/* Server-computed. Never recalculated here. */}
                        <td className="py-2 text-right text-white/80">{item.line_total}</td>
                        {canManage && q.is_editable ? (
                          <td className="py-2 pl-3 text-right">
                            <Confirm
                              label="Quitar"
                              question="¿Quitar la línea?"
                              tone="danger"
                              disabled={busy}
                              onConfirm={() => void run(() => removeQuoteItem(slug, orderId, q.id, item.id))}
                            />
                          </td>
                        ) : null}
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="mt-3 text-sm text-white/40">Sin líneas todavía.</p>
              )}

              <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-white/[0.06] pt-3">
                <span className="text-xs text-white/40">
                  Subtotal {q.subtotal} · descuento {q.discount_amount}
                </span>
                <span className="text-sm font-semibold text-white">
                  {/* The quote carries its OWN currency, frozen when it was
                      created. Rendering it with the company's current setting
                      would restate an old price in a unit nobody agreed. */}
                  {q.currency} {q.total}
                </span>
              </div>

              {q.decision ? (
                <p className="mt-2 text-xs text-white/50">
                  El cliente respondió {dateTime(q.decision.decided_at)}
                  {q.decision.reason ? ` — “${q.decision.reason}”` : ""}
                </p>
              ) : null}

              {canManage && q.is_editable ? (
                <div className="mt-3 space-y-3 border-t border-white/[0.06] pt-3">
                  <div className="grid gap-2 md:grid-cols-4">
                    <label className="text-xs text-white/50">
                      Tipo
                      <select
                        value={itemType}
                        onChange={(e) => setItemType(e.target.value)}
                        className="mt-1 w-full rounded-lg border border-white/[0.08] bg-black/40 px-3 py-2 text-sm text-white"
                      >
                        {ITEM_TYPES.map((t) => (
                          <option key={t.value} value={t.value}>{t.label}</option>
                        ))}
                      </select>
                    </label>
                    <Field label="Descripción" value={description} onChange={setDescription} />
                    <Field label="Cantidad" value={quantity} onChange={setQuantity} />
                    <Field label="Precio unitario" value={unitPrice} onChange={setUnitPrice} />
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      disabled={busy || !description.trim() || !unitPrice.trim()}
                      onClick={() => void run(async () => {
                        await addQuoteItem(slug, orderId, q.id, {
                          item_type: itemType,
                          description: description.trim(),
                          quantity: quantity.trim() || "1",
                          unit_price: unitPrice.trim(),
                        });
                        setDescription(""); setQuantity("1"); setUnitPrice("");
                      })}
                    >
                      Añadir línea
                    </Button>
                    <Confirm
                      label="Publicar al cliente"
                      question="¿Enviar esta cotización? Después no se puede editar."
                      tone="primary"
                      disabled={busy || q.items.length === 0}
                      onConfirm={() => void run(() => publishQuote(slug, orderId, q.id))}
                    />
                  </div>
                </div>
              ) : null}

              {canManage && q.status === "sent" ? (
                <div className="mt-3">
                  <Confirm
                    label="Anular cotización"
                    question="¿Anularla y volver a diagnóstico?"
                    tone="danger"
                    disabled={busy}
                    onConfirm={() => void run(() => cancelQuote(slug, orderId, q.id))}
                  />
                </div>
              ) : null}
            </div>
          ))}
        </div>
      )}

      {canManage ? (
        <div className="mt-4 border-t border-white/[0.06] pt-4">
          <Button
            disabled={busy}
            onClick={() => void run(() => createQuote(slug, orderId, {
              diagnostic_id: data.diagnostics[0]?.id ?? null,
            }))}
          >
            {current ? "Nueva revisión" : "Crear cotización"}
          </Button>
        </div>
      ) : null}
    </Panel>
  );
}

function ExecutionSection({ data, may, busy, run, slug, orderId }: SectionProps) {
  const execution = data.execution;
  const canManage = may(CAP_REPAIR_MANAGE);
  const [work, setWork] = useState("");
  const [notes, setNotes] = useState("");
  const [result, setResult] = useState("");
  const [pauseNote, setPauseNote] = useState("");
  const [seeded, setSeeded] = useState<number | null>(null);

  // Seed the editor ONCE per execution, from the server's copy. Re-seeding on
  // every render would fight whoever is typing.
  if (execution && seeded !== execution.id) {
    setSeeded(execution.id);
    setWork(execution.work_performed);
    setNotes(execution.internal_notes);
    setResult(execution.result);
  }

  const status = data.order.status;

  return (
    <Panel
      title="Trabajo técnico"
      subtitle="El banco de trabajo, aparte del ticket. Empezar y terminar son hechos,
                no opciones de un desplegable."
    >
      {execution === null ? (
        <>
          <p className="text-sm text-white/50">Nadie ha empezado todavía.</p>
          {canManage && status === "approved" ? (
            <div className="mt-3">
              <Confirm
                label="Iniciar reparación"
                question="¿Empezar? Quedará registrado a tu nombre."
                tone="primary"
                disabled={busy}
                onConfirm={() => void run(() => startRepair(slug, orderId))}
              />
            </div>
          ) : null}
        </>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs text-white/40">
              Iniciado {dateTime(execution.started_at)}
              {execution.started_by_name ? ` · ${execution.started_by_name}` : ""}
            </span>
            {execution.result_label ? (
              <Pill
                label={execution.result_label}
                tone={execution.result === "success" ? "good" : "warn"}
              />
            ) : null}
          </div>
          {execution.completed_at ? (
            <p className="text-xs text-white/40">
              Finalizado {dateTime(execution.completed_at)}
              {execution.completed_by_name ? ` · ${execution.completed_by_name}` : ""}
            </p>
          ) : null}

          {execution.is_completed || !canManage ? (
            <>
              <p className="text-sm text-white/80">{execution.work_performed || "—"}</p>
              {execution.internal_notes ? (
                <p className="text-xs text-white/40">{execution.internal_notes}</p>
              ) : null}
            </>
          ) : (
            <div className="space-y-3">
              <Field label="Trabajo realizado" value={work} onChange={setWork} textarea />
              <Field label="Notas internas" value={notes} onChange={setNotes} textarea />
              <label className="block text-xs text-white/50">
                Resultado
                <select
                  value={result}
                  onChange={(e) => setResult(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-white/[0.08] bg-black/40 px-3 py-2 text-sm text-white md:w-64"
                >
                  <option value="">—</option>
                  {RESULTS.map((r) => (
                    <option key={r.value} value={r.value}>{r.label}</option>
                  ))}
                </select>
              </label>

              <div className="flex flex-wrap gap-2">
                <Button
                  disabled={busy}
                  onClick={() => void run(() => updateExecution(slug, orderId, {
                    work_performed: work, internal_notes: notes,
                    ...(result ? { result } : {}),
                  }))}
                >
                  Guardar avance
                </Button>
                <Confirm
                  label="Finalizar trabajo técnico"
                  question="¿Finalizar? No significa listo para entregar."
                  tone="primary"
                  disabled={busy || !work.trim() || !result || status !== "in_repair"}
                  onConfirm={() => void run(() => completeRepair(slug, orderId, {
                    work_performed: work.trim(), result,
                    ...(notes.trim() ? { internal_notes: notes.trim() } : {}),
                  }))}
                />
              </div>

              {status === "in_repair" ? (
                <div className="space-y-2 border-t border-white/[0.06] pt-3">
                  <Field
                    label="Pausar por repuestos — motivo (opcional)"
                    value={pauseNote}
                    onChange={setPauseNote}
                  />
                  <Button
                    disabled={busy}
                    onClick={() => void run(async () => {
                      await pauseForParts(slug, orderId, pauseNote);
                      setPauseNote("");
                    })}
                  >
                    Pausar por repuestos
                  </Button>
                </div>
              ) : status === "waiting_parts" ? (
                <div className="border-t border-white/[0.06] pt-3">
                  <Button
                    tone="primary"
                    disabled={busy}
                    onClick={() => void run(() => resumeRepair(slug, orderId))}
                  >
                    Reanudar reparación
                  </Button>
                </div>
              ) : null}
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}

function PartsSection({ data, may, busy, run, slug, orderId }: SectionProps) {
  const canManage = may(CAP_REPAIR_MANAGE);
  const openExecution = data.execution && !data.execution.is_completed;
  const [quantities, setQuantities] = useState<Record<number, string>>({});
  // Held OUTSIDE render state per intention: a retry must resend the SAME key,
  // and a key that changed on re-render would be no key at all.
  const [keys] = useState<Map<string, string>>(() => new Map());

  function keyFor(shape: string): string {
    const existing = keys.get(shape);
    if (existing) return existing;
    const minted = makeIdempotencyKey(shape);
    keys.set(shape, minted);
    return minted;
  }

  const active = data.parts.filter((p) => !p.is_reversed);
  const reversed = data.parts.filter((p) => p.is_reversed);

  return (
    <Panel
      title="Repuestos"
      subtitle="Salen de la sucursal de ESTA orden. Esta pantalla no resta stock: el
                servidor lo hace y se vuelve a preguntar."
    >
      {canManage && openExecution && data.candidates.length > 0 ? (
        <div className="space-y-3">
          {data.candidates.map((c) => {
            const qty = quantities[c.quote_item_id] ?? "1";
            const shape = `${c.quote_item_id}x${qty}`;
            return (
              <div key={c.quote_item_id} className="rounded-xl border border-white/[0.06] p-4">
                <p className="text-sm text-white/80">{c.description}</p>
                <p className="mt-1 text-xs text-white/40">
                  Aprobados {c.approved_quantity} · usados {c.used_quantity} ·
                  {" "}disponibles aquí {c.available_in_branch}
                </p>
                {c.outstanding_quantity === 0 ? (
                  <p className="mt-2 text-xs text-white/40">Ya se usó todo lo aprobado.</p>
                ) : c.available_in_branch === 0 ? (
                  <p className="mt-2 text-xs text-rose-300">
                    Sin stock en la sucursal de esta reparación.
                  </p>
                ) : (
                  <div className="mt-2 flex flex-wrap items-end gap-2">
                    <label className="text-xs text-white/50">
                      Cantidad
                      <input
                        value={qty}
                        onChange={(e) =>
                          setQuantities((q) => ({ ...q, [c.quote_item_id]: e.target.value }))
                        }
                        className="mt-1 w-24 rounded-lg border border-white/[0.08] bg-black/40 px-3 py-2 text-sm text-white"
                      />
                    </label>
                    <Confirm
                      label="Registrar consumo"
                      question={`¿Descontar ${qty} del stock de esta sucursal?`}
                      tone="primary"
                      disabled={busy}
                      onConfirm={() => void run(() => recordPartUsage(slug, orderId, {
                        quote_item_id: c.quote_item_id,
                        quantity: Number(qty),
                        idempotency_key: keyFor(shape),
                      }))}
                    />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ) : null}

      <div className="mt-4 border-t border-white/[0.06] pt-4">
        <h3 className="text-xs uppercase tracking-wide text-white/40">Consumidos</h3>
        {active.length === 0 ? (
          <p className="mt-2 text-sm text-white/50">Todavía no se usó ningún repuesto.</p>
        ) : (
          <ul className="mt-2 space-y-2">
            {active.map((usage) => (
              <li key={usage.id} className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm text-white/80">
                  {usage.description} <span className="text-white/40">×{usage.quantity}</span>
                  <span className="ml-2 text-[11px] text-white/30">
                    {dateTime(usage.created_at)}
                    {usage.actor_name ? ` · ${usage.actor_name}` : ""}
                  </span>
                </span>
                {canManage && openExecution ? (
                  <Confirm
                    label="Deshacer"
                    question={`¿Devolver ${usage.quantity} al stock?`}
                    tone="danger"
                    disabled={busy}
                    onConfirm={() => void run(() => reversePartUsage(slug, orderId, usage.id, ""))}
                  />
                ) : null}
              </li>
            ))}
          </ul>
        )}

        {reversed.length > 0 ? (
          <div className="mt-3">
            <h3 className="text-xs uppercase tracking-wide text-white/30">Deshechos</h3>
            <ul className="mt-1 space-y-1">
              {reversed.map((usage) => (
                <li key={usage.id} className="text-xs text-white/40">
                  {usage.description} ×{usage.quantity} — devuelto {dateTime(usage.reversed_at)}
                  {usage.reversed_by_name ? ` · ${usage.reversed_by_name}` : ""}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </Panel>
  );
}

function QualitySection({ data, may, busy, run, slug, orderId }: SectionProps) {
  const canManage = may(CAP_QUALITY_MANAGE);
  const check = data.quality;
  const [notes, setNotes] = useState("");
  const [itemNotes, setItemNotes] = useState<Record<number, string>>({});
  const status = data.order.status;

  // A PREVIEW, never authority. The server reads the answers and refuses a pass
  // with an unanswered required point or any failure, whatever this shows.
  const pending = check?.is_open
    ? check.items.filter((i) => i.is_required && !i.result).length
    : 0;
  const failures = check?.is_open
    ? check.items.filter((i) => i.result === "fail").length
    : 0;

  return (
    <Panel
      title="Control de calidad"
      subtitle="La lista la manda el servidor, copiada al abrir el control. Editar la
                plantilla después no cambia lo que se probó."
    >
      {check === null ? (
        <>
          <p className="text-sm text-white/50">Este equipo no ha pasado control de calidad.</p>
          {canManage && status === "repaired" ? (
            <div className="mt-3">
              <Confirm
                label="Iniciar control de calidad"
                question="¿Copiar la lista de control y empezar?"
                tone="primary"
                disabled={busy}
                onConfirm={() => void run(() => startQualityCheck(slug, orderId))}
              />
            </div>
          ) : null}
        </>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-sm text-white/70">{check.template_name}</span>
            <Pill
              label={check.status_label}
              tone={check.status === "passed" ? "good" : check.status === "failed" ? "bad" : "neutral"}
            />
          </div>
          <p className="text-xs text-white/40">
            Iniciado {dateTime(check.started_at)}
            {check.checked_by_name ? ` · ${check.checked_by_name}` : ""}
          </p>

          <ul className="space-y-3 border-t border-white/[0.06] pt-3">
            {check.items.map((item) => (
              <li key={item.id} className="space-y-2">
                <p className="text-sm text-white/80">
                  {item.label}
                  {item.is_required ? "" : <span className="text-white/30"> (opcional)</span>}
                </p>
                {canManage && check.is_open ? (
                  <div className="flex flex-wrap gap-2">
                    {QUALITY_RESULTS.map((r) => (
                      <Button
                        key={r.value}
                        tone={item.result === r.value ? "primary" : "default"}
                        disabled={busy}
                        onClick={() => void run(() => recordQualityResult(slug, orderId, item.id, {
                          result: r.value,
                          ...(itemNotes[item.id] ? { notes: itemNotes[item.id] } : {}),
                        }))}
                      >
                        {r.label}
                      </Button>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-white/40">
                    {QUALITY_RESULTS.find((r) => r.value === item.result)?.label ?? "Sin responder"}
                  </p>
                )}
                {canManage && check.is_open && item.result === "fail" ? (
                  <Field
                    label="Qué falló"
                    value={itemNotes[item.id] ?? item.notes}
                    onChange={(v) => setItemNotes((n) => ({ ...n, [item.id]: v }))}
                    placeholder="Solo lo ve el taller"
                  />
                ) : null}
                {!check.is_open && item.notes ? (
                  <p className="text-xs text-white/30">{item.notes}</p>
                ) : null}
              </li>
            ))}
          </ul>

          {canManage && check.is_open ? (
            <div className="space-y-3 border-t border-white/[0.06] pt-3">
              <p className="text-xs text-white/40">
                {pending > 0
                  ? `Faltan ${pending} punto(s) obligatorio(s).`
                  : failures > 0
                    ? `${failures} punto(s) no pasaron.`
                    : "Todo lo obligatorio está respondido."}
              </p>
              <Field label="Observaciones internas" value={notes} onChange={setNotes} textarea />
              <div className="flex flex-wrap gap-2">
                <Confirm
                  label="Aprobar control de calidad"
                  question="¿Aprobar? El servidor verificará las respuestas."
                  tone="primary"
                  disabled={busy}
                  onConfirm={() => void run(async () => {
                    await passQualityCheck(slug, orderId, notes);
                    setNotes("");
                  })}
                />
                <Confirm
                  label="Enviar de vuelta a reparación"
                  question="¿Devolver al banco? Se abrirá un trabajo nuevo."
                  tone="danger"
                  disabled={busy}
                  onConfirm={() => void run(async () => {
                    await failQualityCheck(slug, orderId, notes);
                    setNotes("");
                  })}
                />
              </div>
            </div>
          ) : null}
        </div>
      )}

      {data.qualityHistory.length > 1 ? (
        <div className="mt-4 border-t border-white/[0.06] pt-4">
          <h3 className="text-xs uppercase tracking-wide text-white/30">Controles anteriores</h3>
          <ul className="mt-2 space-y-1">
            {data.qualityHistory.slice(1).map((past) => (
              <li key={past.id} className="flex items-center justify-between gap-2 text-xs text-white/40">
                <span>{dateTime(past.started_at)}{past.completed_by_name ? ` · ${past.completed_by_name}` : ""}</span>
                <Pill label={past.status_label} tone={past.status === "passed" ? "good" : "bad"} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </Panel>
  );
}

function HistorySection({ history }: { history: ServiceHistoryEntry[] }) {
  return (
    <Panel title="Historial" subtitle="Inmutable. Escrito por el servidor en cada cambio.">
      {history.length === 0 ? (
        <p className="text-sm text-white/50">Sin eventos.</p>
      ) : (
        <ul className="space-y-2">
          {[...history].reverse().map((event) => (
            <li key={event.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
              <span className="text-white/70">
                {event.status_label}
                {event.comment ? <span className="text-white/40"> — {event.comment}</span> : null}
              </span>
              <span className="text-xs text-white/30">
                {dateTime(event.created_at)}
                {event.actor_name ? ` · ${event.actor_name}` : ""}
                {event.is_customer_visible ? "" : " · interno"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

export default function ServiceOrderPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const orderId = Number(id);
  return (
    <InternalControlGuard>
      {(ctx) => <OrderContent ctx={ctx} orderId={orderId} />}
    </InternalControlGuard>
  );
}
