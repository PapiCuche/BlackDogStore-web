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
import { EvidenceGallery } from "../../components/EvidenceGallery";
import {
  CAP_DELIVERY_MANAGE,
  CAP_PAYMENTS_MANAGE,
  CAP_DIAGNOSTIC_MANAGE,
  CAP_ORDERS_MANAGE,
  CAP_ORDERS_VIEW,
  CAP_QUALITY_MANAGE,
  CAP_REPAIR_MANAGE,
  PAYMENT_METHODS,
  ServiceApiError,
  addQuoteItem,
  assignTechnician,
  cancelQuote,
  completeRepair,
  createDiagnostic,
  createQuote,
  failQualityCheck,
  fetchDelivery,
  fetchServicePayments,
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
  recordDelivery,
  recordServicePayment,
  reverseServicePayment,
  recordPartUsage,
  recordQualityResult,
  removeQuoteItem,
  resumeRepair,
  reversePartUsage,
  startQualityCheck,
  startRepair,
  transitionServiceOrder,
  updateExecution,
  type ServiceDelivery,
  type ServicePayment,
  type ServicePaymentSummary,
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
  delivery: ServiceDelivery | null;
  payments: ServicePayment[];
  paymentSummary: ServicePaymentSummary;
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
      const [order, history, diagnostics, quotes, execution, parts, candidates, quality, qualityHistory, delivery, payments, assignment] =
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
          fetchDelivery(slug, orderId),
          fetchServicePayments(slug, orderId),
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
        delivery: delivery.delivery,
        payments: payments.results,
        paymentSummary: payments.summary,
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
        <Panel><p className="text-sm text-muted">Selecciona una empresa.</p></Panel>
      </AdminShell>
    );
  }
  if (!may(CAP_ORDERS_VIEW)) {
    return (
      <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
        <Panel>
          <p className="text-sm text-muted">
            Tu cuenta no tiene permiso para ver el servicio técnico de esta empresa.
          </p>
        </Panel>
      </AdminShell>
    );
  }
  if (loading && !data) {
    return (
      <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
        <Panel><p className="text-sm text-muted">Cargando orden…</p></Panel>
      </AdminShell>
    );
  }
  if (!data) {
    return (
      <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
        <Panel>
          <p className="text-sm text-danger">
            {error instanceof Error ? error.message : "No se encontró la orden."}
          </p>
          <div className="mt-3">
            <Link href="/admin/service" className="text-xs text-muted hover:underline">
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
            <Link href="/admin/service" className="text-xs text-muted hover:underline">
              ← Órdenes de servicio
            </Link>
            <h1 className="mt-1 text-xl font-semibold">{order.number}</h1>
            <p className="mt-1 text-sm text-muted">
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
        <PaymentSection data={data} may={may} busy={busy} run={run} slug={slug} orderId={orderId} />
        <DeliverySection data={data} may={may} busy={busy} run={run} slug={slug} orderId={orderId} />
        <Panel
          title="Evidencias"
          subtitle="Fotografías del estado del equipo. Nacen internas: compartirlas con el cliente es una acción aparte."
        >
          <EvidenceGallery slug={slug} orderId={orderId} may={may} />
        </Panel>
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
          <dt className="text-xs text-muted">Falla reportada</dt>
          <dd className="mt-1 text-foreground/85">{order.reported_issue || "—"}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted">Condición física</dt>
          <dd className="mt-1 text-foreground/85">{order.physical_condition || "—"}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted">Accesorios recibidos</dt>
          <dd className="mt-1 text-foreground/85">{order.received_accessories || "—"}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted">Notas internas</dt>
          <dd className="mt-1 text-foreground/85">{order.internal_notes || "—"}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted">Recibido</dt>
          <dd className="mt-1 text-muted">
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
        <p className="text-sm text-muted">
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
      <p className="mb-3 text-sm text-muted">
        Actual: {data.order.technician_name || "sin asignar"}
      </p>
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-xs text-muted">
          Asignar a
          <select
            value={technicianId}
            onChange={(e) => setTechnicianId(e.target.value)}
            className="mt-1 rounded-lg border border-bd-border bg-background/40 px-3 py-2 text-sm text-foreground"
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
        <p className="text-sm text-muted">Todavía no hay diagnóstico.</p>
      ) : (
        <div className="space-y-3">
          {data.diagnostics.map((d) => (
            <div key={d.id} className="rounded-xl border border-bd-border p-4">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-muted">
                  Revisión {d.revision} · {dateTime(d.created_at)}
                  {d.diagnosed_by_name ? ` · ${d.diagnosed_by_name}` : ""}
                </span>
                <Pill label={d.status_label} tone={d.finalized_at ? "good" : "neutral"} />
              </div>
              <p className="mt-2 text-sm text-foreground/85">{d.description}</p>
              {d.recommended_action ? (
                <p className="mt-1 text-sm text-muted">
                  Acción recomendada: {d.recommended_action}
                </p>
              ) : null}
              {d.internal_notes ? (
                <p className="mt-1 text-xs text-muted">{d.internal_notes}</p>
              ) : null}
            </div>
          ))}
        </div>
      )}

      {canManage ? (
        <div className="mt-4 space-y-3 border-t border-bd-border pt-4">
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
        <p className="text-sm text-muted">Todavía no hay cotización.</p>
      ) : (
        <div className="space-y-4">
          {data.quotes.map((q) => (
            <div key={q.id} className="rounded-xl border border-bd-border p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-xs text-muted">
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
                      <tr key={item.id} className="border-t border-bd-border">
                        <td className="py-2 pr-3 text-muted">
                          {item.description}
                          <span className="ml-2 text-[11px] text-muted">
                            {item.item_type_label}
                          </span>
                        </td>
                        <td className="py-2 pr-3 text-right text-muted">
                          {item.quantity} × {item.unit_price}
                        </td>
                        {/* Server-computed. Never recalculated here. */}
                        <td className="py-2 text-right text-foreground/85">{item.line_total}</td>
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
                <p className="mt-3 text-sm text-muted">Sin líneas todavía.</p>
              )}

              <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-bd-border pt-3">
                <span className="text-xs text-muted">
                  Subtotal {q.subtotal} · descuento {q.discount_amount}
                </span>
                <span className="text-sm font-semibold text-foreground">
                  {/* The quote carries its OWN currency, frozen when it was
                      created. Rendering it with the company's current setting
                      would restate an old price in a unit nobody agreed. */}
                  {q.currency} {q.total}
                </span>
              </div>

              {q.decision ? (
                <p className="mt-2 text-xs text-muted">
                  El cliente respondió {dateTime(q.decision.decided_at)}
                  {q.decision.reason ? ` — “${q.decision.reason}”` : ""}
                </p>
              ) : null}

              {canManage && q.is_editable ? (
                <div className="mt-3 space-y-3 border-t border-bd-border pt-3">
                  <div className="grid gap-2 md:grid-cols-4">
                    <label className="text-xs text-muted">
                      Tipo
                      <select
                        value={itemType}
                        onChange={(e) => setItemType(e.target.value)}
                        className="mt-1 w-full rounded-lg border border-bd-border bg-background/40 px-3 py-2 text-sm text-foreground"
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
        <div className="mt-4 border-t border-bd-border pt-4">
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
          <p className="text-sm text-muted">Nadie ha empezado todavía.</p>
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
            <span className="text-xs text-muted">
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
            <p className="text-xs text-muted">
              Finalizado {dateTime(execution.completed_at)}
              {execution.completed_by_name ? ` · ${execution.completed_by_name}` : ""}
            </p>
          ) : null}

          {execution.is_completed || !canManage ? (
            <>
              <p className="text-sm text-foreground/85">{execution.work_performed || "—"}</p>
              {execution.internal_notes ? (
                <p className="text-xs text-muted">{execution.internal_notes}</p>
              ) : null}
            </>
          ) : (
            <div className="space-y-3">
              <Field label="Trabajo realizado" value={work} onChange={setWork} textarea />
              <Field label="Notas internas" value={notes} onChange={setNotes} textarea />
              <label className="block text-xs text-muted">
                Resultado
                <select
                  value={result}
                  onChange={(e) => setResult(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-bd-border bg-background/40 px-3 py-2 text-sm text-foreground md:w-64"
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
                <div className="space-y-2 border-t border-bd-border pt-3">
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
                <div className="border-t border-bd-border pt-3">
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
              <div key={c.quote_item_id} className="rounded-xl border border-bd-border p-4">
                <p className="text-sm text-foreground/85">{c.description}</p>
                <p className="mt-1 text-xs text-muted">
                  Aprobados {c.approved_quantity} · usados {c.used_quantity} ·
                  {" "}disponibles aquí {c.available_in_branch}
                </p>
                {c.outstanding_quantity === 0 ? (
                  <p className="mt-2 text-xs text-muted">Ya se usó todo lo aprobado.</p>
                ) : c.available_in_branch === 0 ? (
                  <p className="mt-2 text-xs text-danger">
                    Sin stock en la sucursal de esta reparación.
                  </p>
                ) : (
                  <div className="mt-2 flex flex-wrap items-end gap-2">
                    <label className="text-xs text-muted">
                      Cantidad
                      <input
                        value={qty}
                        onChange={(e) =>
                          setQuantities((q) => ({ ...q, [c.quote_item_id]: e.target.value }))
                        }
                        className="mt-1 w-24 rounded-lg border border-bd-border bg-background/40 px-3 py-2 text-sm text-foreground"
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

      <div className="mt-4 border-t border-bd-border pt-4">
        <h3 className="text-xs uppercase tracking-wide text-muted">Consumidos</h3>
        {active.length === 0 ? (
          <p className="mt-2 text-sm text-muted">Todavía no se usó ningún repuesto.</p>
        ) : (
          <ul className="mt-2 space-y-2">
            {active.map((usage) => (
              <li key={usage.id} className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm text-foreground/85">
                  {usage.description} <span className="text-muted">×{usage.quantity}</span>
                  <span className="ml-2 text-[11px] text-muted">
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
            <h3 className="text-xs uppercase tracking-wide text-muted">Deshechos</h3>
            <ul className="mt-1 space-y-1">
              {reversed.map((usage) => (
                <li key={usage.id} className="text-xs text-muted">
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
          <p className="text-sm text-muted">Este equipo no ha pasado control de calidad.</p>
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
            <span className="text-sm text-muted">{check.template_name}</span>
            <Pill
              label={check.status_label}
              tone={check.status === "passed" ? "good" : check.status === "failed" ? "bad" : "neutral"}
            />
          </div>
          <p className="text-xs text-muted">
            Iniciado {dateTime(check.started_at)}
            {check.checked_by_name ? ` · ${check.checked_by_name}` : ""}
          </p>

          <ul className="space-y-3 border-t border-bd-border pt-3">
            {check.items.map((item) => (
              <li key={item.id} className="space-y-2">
                <p className="text-sm text-foreground/85">
                  {item.label}
                  {item.is_required ? "" : <span className="text-muted"> (opcional)</span>}
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
                  <p className="text-xs text-muted">
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
                  <p className="text-xs text-muted">{item.notes}</p>
                ) : null}
              </li>
            ))}
          </ul>

          {canManage && check.is_open ? (
            <div className="space-y-3 border-t border-bd-border pt-3">
              <p className="text-xs text-muted">
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
        <div className="mt-4 border-t border-bd-border pt-4">
          <h3 className="text-xs uppercase tracking-wide text-muted">Controles anteriores</h3>
          <ul className="mt-2 space-y-1">
            {data.qualityHistory.slice(1).map((past) => (
              <li key={past.id} className="flex items-center justify-between gap-2 text-xs text-muted">
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

const PAYMENT_STATUS_LABEL: Record<string, { label: string; tone: "neutral" | "good" | "warn" | "bad" }> = {
  no_quote: { label: "Sin cotización aprobada", tone: "neutral" },
  unpaid: { label: "Sin pagos", tone: "warn" },
  partial: { label: "Pago parcial", tone: "warn" },
  paid: { label: "Pagado", tone: "good" },
  overpaid: { label: "Pagado de más", tone: "warn" },
};

/**
 * The money on this repair. M12B.
 *
 * THIS SCREEN DOES NO ARITHMETIC. Every figure below is a string the server
 * computed and this component prints. Parsing them into numbers to show a
 * running total would create a second answer to "how much is owed" that can
 * disagree with the first — and the one that disagrees is always the one a
 * customer is looking at.
 *
 * `outstanding` and `quoted_total` can be NULL, and null is not zero: it means
 * the shop has not agreed a price yet. Drawing "S/ 0.00" there would tell
 * somebody the repair is free.
 *
 * A REVERSAL IS NOT A REFUND, and the copy says so. It marks a row as written
 * in error; whether money went back over the counter is between the shop and
 * the customer, and this platform cannot return any.
 */
function PaymentSection({ data, may, busy, run, slug, orderId }: SectionProps) {
  const canManage = may(CAP_PAYMENTS_MANAGE);
  const summary = data.paymentSummary;
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState<string>(PAYMENT_METHODS[0].value);
  const [reference, setReference] = useState("");
  // Held OUTSIDE render state, as everywhere else here: a retry must resend the
  // SAME key, and a key that changed on re-render would be no key at all.
  const [keys] = useState<Map<string, string>>(() => new Map());

  function keyFor(shape: string): string {
    const existing = keys.get(shape);
    if (existing) return existing;
    const minted = makeIdempotencyKey(shape);
    keys.set(shape, minted);
    return minted;
  }

  const badge = PAYMENT_STATUS_LABEL[summary.payment_status]
    ?? { label: summary.payment_status, tone: "neutral" as const };
  const settled = summary.outstanding === "0.00";
  const canPayMore = summary.outstanding !== null && !settled;

  return (
    <Panel
      title="Pago del servicio"
      subtitle="Registro de dinero recibido en mostrador. El saldo lo calcula el servidor."
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <dl className="grid flex-1 gap-3 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-xs text-muted">Total aprobado</dt>
            <dd className="mt-0.5 text-foreground/85">
              {summary.quoted_total === null
                ? "Sin cotización aprobada"
                : `${summary.currency} ${summary.quoted_total}`}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Pagado</dt>
            <dd className="mt-0.5 text-foreground/85">
              {summary.currency} {summary.confirmed_paid}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Saldo pendiente</dt>
            <dd className="mt-0.5 text-foreground/85">
              {summary.outstanding === null
                ? "—"
                : `${summary.currency} ${summary.outstanding}`}
            </dd>
          </div>
        </dl>
        <Pill label={badge.label} tone={badge.tone} />
      </div>

      {summary.payment_status === "overpaid" ? (
        <p className="mt-3 text-xs text-warning">
          Se recibió {summary.currency} {summary.credit} de más. No se devuelve nada
          automáticamente: esta plataforma no puede reembolsar.
        </p>
      ) : null}

      {canManage && canPayMore ? (
        <div className="mt-4 space-y-3 border-t border-bd-border pt-4">
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label={`Importe (${summary.currency})`} value={amount} onChange={setAmount} />
            <label className="text-xs text-muted">
              Medio
              <select
                value={method}
                onChange={(e) => setMethod(e.target.value)}
                className="mt-1 w-full rounded-lg border border-bd-border bg-background/40 px-3 py-2 text-sm text-foreground"
              >
                {PAYMENT_METHODS.map((m) => (
                  <option key={m.value} value={m.value}>{m.label}</option>
                ))}
              </select>
            </label>
            <Field label="Referencia" value={reference} onChange={setReference} placeholder="Nº de operación" />
          </div>
          <Confirm
            label="Registrar pago"
            question={`¿Registrar ${summary.currency} ${amount.trim() || "…"}? No se puede editar después.`}
            tone="primary"
            disabled={busy || amount.trim() === ""}
            onConfirm={() => void run(async () => {
              const value = amount.trim();
              await recordServicePayment(slug, orderId, {
                amount: value,
                method,
                ...(reference.trim() ? { reference: reference.trim() } : {}),
                idempotency_key: keyFor(`${orderId}:${value}:${method}:${reference.trim()}`),
              });
              setAmount("");
              setReference("");
            })}
          />
        </div>
      ) : null}

      {data.payments.length > 0 ? (
        <ul className="mt-4 space-y-2 border-t border-bd-border pt-4">
          {data.payments.map((payment) => (
            <li key={payment.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
              <span className={payment.is_reversed ? "text-muted line-through" : "text-foreground/85"}>
                {payment.currency} {payment.amount}
                <span className="text-muted">
                  {" · "}
                  {PAYMENT_METHODS.find((m) => m.value === payment.method)?.label ?? payment.method}
                  {payment.reference ? ` · ${payment.reference}` : ""}
                </span>
              </span>
              <span className="flex items-center gap-2 text-xs text-muted">
                {dateTime(payment.received_at)}
                {payment.received_by_name ? ` · ${payment.received_by_name}` : ""}
                {payment.is_reversed ? (
                  <Pill label="Reversado" tone="bad" />
                ) : canManage ? (
                  <Confirm
                    label="Reversar"
                    question="¿Marcar este pago como registrado por error? No devuelve dinero."
                    tone="danger"
                    disabled={busy}
                    onConfirm={() => void run(() =>
                      reverseServicePayment(slug, orderId, payment.id, "")
                    )}
                  />
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      <p className="mt-4 border-t border-bd-border pt-3 text-xs text-muted">
        Un pago no se edita ni se borra: se reversa, y ambos hechos quedan.
        Reversar NO devuelve dinero — esta plataforma no puede hacerlo.
      </p>
    </Panel>
  );
}

/**
 * The handover.
 *
 * THIS SCREEN DOES NOT COLLECT MONEY, and the copy says so out loud rather than
 * leaving the counter to assume. The platform has no way to charge for a repair
 * — `PaymentTransaction` is bound to an e-commerce order by a non-null FK — so a
 * "Cobrado" checkbox here would be a lie the shop believes. Service payment is
 * its own phase.
 */
function DeliverySection({ data, may, busy, run, slug, orderId }: SectionProps) {
  const canManage = may(CAP_DELIVERY_MANAGE);
  const delivery = data.delivery;
  const ready = data.order.status === "ready_for_pickup";
  // A PREVIEW, never authority. The server re-checks the policy and the balance
  // inside the delivery transaction; this only spares somebody a 409 they
  // cannot act on from this panel. If the two ever disagree, the server wins.
  const blockedByBalance =
    data.paymentSummary.payment_status !== "paid"
    && data.paymentSummary.payment_status !== "overpaid"
    && data.paymentSummary.requires_payment_before_delivery;
  const [recipient, setRecipient] = useState("");
  const [notes, setNotes] = useState("");
  // Held OUTSIDE render state, exactly as the parts section does: a retry must
  // resend the SAME key, and a key that changed on re-render would be no key.
  const [keys] = useState<Map<string, string>>(() => new Map());

  function keyFor(shape: string): string {
    const existing = keys.get(shape);
    if (existing) return existing;
    const minted = makeIdempotencyKey(shape);
    keys.set(shape, minted);
    return minted;
  }

  return (
    <Panel
      title="Entrega"
      subtitle="Quién se llevó el equipo y cuándo. No registra cobro: esta plataforma
                todavía no puede cobrar una reparación."
    >
      {delivery !== null ? (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-sm text-foreground/85">{delivery.recipient_name}</span>
            <Pill label="Entregado" tone="good" />
          </div>
          <p className="text-xs text-muted">
            {dateTime(delivery.delivered_at)}
            {delivery.delivered_by_name ? ` · ${delivery.delivered_by_name}` : ""}
          </p>
          {delivery.notes ? <p className="text-xs text-muted">{delivery.notes}</p> : null}
          <p className="pt-2 text-xs text-muted">
            El registro no se puede editar ni borrar. Una entrega es un hecho con fecha.
          </p>
        </div>
      ) : !ready ? (
        <p className="text-sm text-muted">
          Solo se entrega un equipo que aprobó el control de calidad.
        </p>
      ) : blockedByBalance ? (
        <div className="space-y-2">
          <p className="text-sm text-warning">
            Esta empresa exige el pago antes de entregar. Saldo pendiente:{" "}
            {data.paymentSummary.currency} {data.paymentSummary.outstanding ?? "—"}.
          </p>
          <p className="text-xs text-muted">
            El servidor vuelve a comprobarlo al entregar, así que registrar el pago
            arriba es lo que habilita esta acción — ocultar el botón no bastaría.
          </p>
        </div>
      ) : !canManage ? (
        <p className="text-sm text-muted">
          Este equipo está listo. Tu cuenta no tiene permiso para registrar la entrega.
        </p>
      ) : (
        <div className="space-y-3">
          <Field
            label="Quién recibe"
            value={recipient}
            onChange={setRecipient}
            placeholder="Nombre de quien se lleva el equipo"
          />
          <Field label="Observaciones" value={notes} onChange={setNotes} textarea />
          <Confirm
            label="Registrar entrega"
            question={`¿Entregar el equipo a ${recipient.trim() || "…"}? No se puede deshacer.`}
            tone="primary"
            disabled={busy || recipient.trim() === ""}
            onConfirm={() => void run(async () => {
              const name = recipient.trim();
              await recordDelivery(slug, orderId, {
                recipient_name: name,
                ...(notes.trim() ? { notes: notes.trim() } : {}),
                idempotency_key: keyFor(`${orderId}:${name}`),
              });
              setRecipient("");
              setNotes("");
            })}
          />
        </div>
      )}
    </Panel>
  );
}

function HistorySection({ history }: { history: ServiceHistoryEntry[] }) {
  return (
    <Panel title="Historial" subtitle="Inmutable. Escrito por el servidor en cada cambio.">
      {history.length === 0 ? (
        <p className="text-sm text-muted">Sin eventos.</p>
      ) : (
        <ul className="space-y-2">
          {[...history].reverse().map((event) => (
            <li key={event.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
              <span className="text-muted">
                {event.status_label}
                {event.comment ? <span className="text-muted"> — {event.comment}</span> : null}
              </span>
              <span className="text-xs text-muted">
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
