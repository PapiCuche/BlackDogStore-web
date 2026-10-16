"use client";

/**
 * Inventario › Costos y precios — COMMERCE-ADMIN-UX-01 · A1.
 *
 * EL INCIDENTE. «Un Administrador no encuentra "Margen comercial" ni "Valor al
 * costo"». El servidor los daba bien a quien tiene `costs.view`; no había dónde
 * mirarlos: el margen era una sección escondida en Ventas, el valor al costo una
 * tarjeta dentro de Stock, y las dos desaparecían en silencio al cargar o fallar.
 *
 * ESTA PANTALLA NO CALCULA NADA. Reúne lo que el servidor ya responde:
 *   · el margen comercial          → /api/admin/sales/margin/
 *   · el inventario al costo       → /api/admin/inventory/summary/
 *   · el promedio de cada sucursal → /api/admin/inventory/stock/ (claves de costo sólo con `costs.view`)
 *   · el costo de cada equipo      → /api/admin/inventory/units/ (`cost` sólo con `costs.view`)
 * Cada sección tiene su «Cargando…», su vacío y el error del servidor.
 *
 * CORREGIR. El precio y el costo de referencia, por el producto (`products.manage`;
 * el costo, además `costs.view`), enviando sólo lo cambiado. El costo de un equipo, si no se vendió ni se dio de baja, con
 * motivo obligatorio (`inventory.adjust` + `costs.view`). El promedio de una
 * sucursal NO se corrige aquí: requiere un movimiento de ajuste auditado que aún
 * no existe (PENDIENTE). Nada de esto toca lo ya vendido: cada venta guardó su costo.
 */

import { useCallback, useEffect, useId, useMemo, useState } from "react";
import { AdminShell } from "../../components/AdminShell";
import { AccessGuard } from "../../components/AccessGuard";
import { BranchSelector } from "../../components/BranchSelector";
import { COSTS_PERMISSION_HINT, SalesMarginSection } from "../../components/SalesMarginSection";
import {
  EmptyBox,
  ErrorBox,
  Panel,
  Spinner,
  StatCard,
  TableWrap,
  Td,
  Th,
  formatSoles,
} from "../../components/InventoryUi";
import {
  PageHeader,
  internalButtonClass,
  internalInputClass,
  internalPrimaryButtonClass,
} from "../../components/internal-ui";
import type { InternalAccess } from "../../lib/internal-access";
import { useBranchScope } from "../../lib/use-branch-scope";
import {
  fetchBranchStock,
  fetchInventorySummary,
  type BranchParam,
  type BranchStockRow,
  type InventorySummary,
} from "../../../lib/inventory";
import { correctStockUnitCost, fetchStockUnits, type StockUnit } from "../../../lib/stock-units";
import { patchAdminProduct } from "../../../lib/admin";

const ADMIN_ROLES = ["admin", "superadmin"] as const;

/** Where a branch row's cost comes from, in the owner's words. */
const ROW_SOURCE_LABEL: Record<NonNullable<BranchStockRow["cost_source"]>, string> = {
  average: "Promedio de la sucursal",
  unit: "Costo específico de cada equipo",
  reference: "Costo de referencia del catálogo (aún sin promedio)",
  unknown: "Costo pendiente",
};

type Load<T> = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; data: T };

function message(err: unknown, fallback: string) {
  return err instanceof Error && err.message ? err.message : fallback;
}

/**
 * Load something once per dependency change, and say how it went. An answer
 * belongs to the request that asked for it (`token`): until the current one
 * answers, the section says «Cargando…» rather than showing the last branch's.
 */
function useLoad<T>(load: () => Promise<T>, fallback: string, reloadKey = 0): Load<T> {
  const token = useMemo(() => ({ load, reloadKey }), [load, reloadKey]);
  const [answer, setAnswer] = useState<{ token: object; value: Load<T> } | null>(null);
  useEffect(() => {
    let cancelled = false;
    token.load()
      .then((data) => { if (!cancelled) setAnswer({ token, value: { kind: "ready", data } }); })
      .catch((err: unknown) => {
        if (!cancelled) setAnswer({ token, value: { kind: "error", message: message(err, fallback) } });
      });
    return () => { cancelled = true; };
  }, [token, fallback]);
  return answer && answer.token === token ? answer.value : { kind: "loading" };
}

function Region({ title, description, children }: {
  title: string; description?: string; children: React.ReactNode;
}) {
  return (
    <div role="region" aria-label={title}>
      <Panel title={title} description={description}>{children}</Panel>
    </div>
  );
}

function decimal(value: string | null | undefined) {
  return value === null || value === undefined ? "—" : formatSoles(value);
}

// ---------------------------------------------------------------------------
// Inventario al costo
// ---------------------------------------------------------------------------

function AtCostSection({ branch }: { branch: BranchParam }) {
  const load = useCallback(() => fetchInventorySummary(undefined, branch), [branch]);
  const state = useLoad<InventorySummary>(load, "No se pudo cargar el inventario al costo.");
  return (
    <Region
      title="Inventario al costo"
      description="Productos por cantidad al promedio de su sucursal; equipos con serie al costo de cada uno."
    >
      {state.kind === "loading" ? <Spinner label="Cargando…" /> : null}
      {state.kind === "error" ? <ErrorBox message={state.message} /> : null}
      {state.kind === "ready" ? (
        state.data.inventory_cost_value === undefined ? (
          <EmptyBox message={COSTS_PERMISSION_HINT} />
        ) : (
          <div className="grid gap-4 sm:grid-cols-3">
            <StatCard label="Valor al costo" value={formatSoles(state.data.inventory_cost_value)} hint="Promedio por sucursal y costo de cada equipo" />
            <StatCard
              label="Unidades sin costo"
              value={state.data.inventory_cost_pending_units ?? 0}
              emphasis={(state.data.inventory_cost_pending_units ?? 0) > 0}
              hint={`${state.data.inventory_cost_pending_units ?? 0} unidades sin costo, no incluidas (nunca se valoran en cero)`}
            />
            <StatCard label="Valor a precio de venta" value={formatSoles(state.data.inventory_value)} hint="Referencia: no es costo" />
          </div>
        )
      ) : null}
    </Region>
  );
}

// ---------------------------------------------------------------------------
// Costo promedio por sucursal, con precio y costo de referencia editables
// ---------------------------------------------------------------------------

function ProductEditor({ row, onDone, onCancel }: {
  row: BranchStockRow; onDone: () => void; onCancel: () => void;
}) {
  const [price, setPrice] = useState(row.product_price);
  const [referenceCost, setReferenceCost] = useState(row.product_reference_cost ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    // Only what this person changed: the row was read a while ago, and sending
    // its old price would undo somebody else's change made since.
    const changes: { price?: string; reference_cost?: string | null } = {};
    if (price.trim() !== row.product_price) changes.price = price.trim();
    const cost = referenceCost.trim();
    if (cost !== (row.product_reference_cost ?? "")) changes.reference_cost = cost || null;
    if (Object.keys(changes).length === 0) {
      onCancel();
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await patchAdminProduct(row.product, changes);
      onDone();
    } catch (err) {
      setError(message(err, "No se pudo guardar el producto."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-3 rounded-xl border border-bd-border bg-surface-2 p-4">
      <p className="text-sm font-semibold text-foreground">{row.product_name}</p>
      <div className="flex flex-wrap gap-3">
        <label className="text-xs font-semibold text-muted">
          Precio de venta
          <input aria-label="Precio de venta" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} className={`mt-1 block ${internalInputClass}`} />
        </label>
        <label className="text-xs font-semibold text-muted">
          Costo de referencia
          <input aria-label="Costo de referencia" inputMode="decimal" value={referenceCost} onChange={(e) => setReferenceCost(e.target.value)} className={`mt-1 block ${internalInputClass}`} />
        </label>
      </div>
      <p className="text-[11px] text-muted">
        El costo de referencia sólo se usa donde una sucursal aún no tiene promedio. Cambiarlo no reescribe ventas ni movimientos pasados.
      </p>
      {error ? <ErrorBox message={error} /> : null}
      <div className="flex gap-2">
        <button type="button" onClick={() => void save()} disabled={saving} className={internalPrimaryButtonClass}>
          {saving ? "Guardando…" : "Guardar producto"}
        </button>
        <button type="button" onClick={onCancel} className={internalButtonClass}>Cancelar</button>
      </div>
    </div>
  );
}

function BranchCostSection({ branch, canEditProducts }: { branch: BranchParam; canEditProducts: boolean }) {
  const [reloadKey, setReloadKey] = useState(0);
  const [editing, setEditing] = useState<BranchStockRow | null>(null);
  const load = useCallback(
    () => fetchBranchStock({ branch, status: "in_stock", page_size: 100 }),
    [branch],
  );
  const state = useLoad(load, "No se pudo cargar el costo por sucursal.", reloadKey);

  return (
    <Region
      title="Costo promedio por sucursal"
      description="Promedio ponderado de cada sucursal; las unidades que entraron sin costo se cuentan aparte."
    >
      {editing ? (
        <div className="mb-4">
          <ProductEditor
            row={editing}
            onCancel={() => setEditing(null)}
            onDone={() => { setEditing(null); setReloadKey((k) => k + 1); }}
          />
        </div>
      ) : null}
      {state.kind === "loading" ? <Spinner label="Cargando…" /> : null}
      {state.kind === "error" ? <ErrorBox message={state.message} /> : null}
      {state.kind === "ready" && state.data.results.length === 0 ? (
        <EmptyBox message="No hay productos en stock en esta selección." />
      ) : null}
      {state.kind === "ready" && state.data.results.length > 0 ? (
        <TableWrap>
          <thead className="border-b border-bd-border">
            <tr>
              <Th>Producto</Th>
              <Th>Sucursal</Th>
              <Th right>Cantidad</Th>
              <Th right>Costo promedio</Th>
              <Th>Origen del costo</Th>
              <Th right>Costo de referencia</Th>
              <Th right>Precio de venta</Th>
              {canEditProducts ? <Th> </Th> : null}
            </tr>
          </thead>
          <tbody>
            {state.data.results.map((row) => (
              <tr key={row.id} className="border-b border-bd-border/70 last:border-0">
                <Td>{row.product_name}</Td>
                <Td muted>{row.branch_name}</Td>
                <Td right>
                  {row.quantity}
                  {row.uncosted_quantity ? (
                    <span className="block text-[11px] text-warning">{row.uncosted_quantity} sin costo</span>
                  ) : null}
                </Td>
                <Td right>{row.product_is_serialized ? "—" : decimal(row.average_cost)}</Td>
                <Td muted>{row.cost_source ? ROW_SOURCE_LABEL[row.cost_source] : COSTS_PERMISSION_HINT}</Td>
                <Td right>{decimal(row.product_reference_cost)}</Td>
                <Td right>{formatSoles(row.product_price)}</Td>
                {canEditProducts ? (
                  <Td right>
                    <button type="button" onClick={() => setEditing(row)} className={internalButtonClass}>
                      Editar precio y costo
                    </button>
                  </Td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </TableWrap>
      ) : null}
      <p className="mt-3 text-[11px] text-muted">
        Corregir el promedio de una sucursal: pendiente. Se hará con un movimiento de ajuste auditado, nunca editando
        movimientos ni ventas pasadas.
      </p>
    </Region>
  );
}

// ---------------------------------------------------------------------------
// Costo de cada equipo, con «Corregir costo»
// ---------------------------------------------------------------------------

function CostCorrection({ unit, onDone, onCancel }: {
  unit: StockUnit; onDone: () => void; onCancel: () => void;
}) {
  const [cost, setCost] = useState(unit.cost ?? "");
  const [reason, setReason] = useState("");
  const reasonHint = useId();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    if (!reason.trim()) {
      setError("Indica el motivo de la corrección.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await correctStockUnitCost(unit.id, cost, reason.trim());
      onDone();
    } catch (err) {
      setError(message(err, "No se pudo corregir el costo."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-3 rounded-xl border border-bd-border bg-surface-2 p-4">
      <p className="text-sm font-semibold text-foreground">Corregir costo · {unit.product_name} · {unit.serial_number}</p>
      <div className="flex flex-wrap gap-3">
        <label className="text-xs font-semibold text-muted">
          Nuevo costo
          <input aria-label="Nuevo costo" inputMode="decimal" value={cost} onChange={(e) => setCost(e.target.value)} className={`mt-1 block ${internalInputClass}`} />
        </label>
        <label className="min-w-[16rem] flex-1 text-xs font-semibold text-muted">
          Motivo
          <input aria-label="Motivo" aria-describedby={reasonHint} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Ej. factura del proveedor" className={`mt-1 block w-full ${internalInputClass}`} />
          {/* The reason is audited as written, and the audit trail is read by more people than costs.view. */}
          <span id={reasonHint} className="mt-1 block text-[11px] font-normal text-warning">No escribas montos en el motivo: el historial lo ven personas sin permiso de costos</span>
        </label>
      </div>
      <p className="text-[11px] text-muted">Queda en la auditoría con su motivo. Un equipo vendido o dado de baja conserva su costo.</p>
      {error ? <ErrorBox message={error} /> : null}
      <div className="flex gap-2">
        <button type="button" onClick={() => void save()} disabled={saving} className={internalPrimaryButtonClass}>
          {saving ? "Guardando…" : "Guardar corrección"}
        </button>
        <button type="button" onClick={onCancel} className={internalButtonClass}>Cancelar</button>
      </div>
    </div>
  );
}

function DeviceCostSection({ branch, canCorrect }: { branch: BranchParam; canCorrect: boolean }) {
  const [reloadKey, setReloadKey] = useState(0);
  const [editing, setEditing] = useState<StockUnit | null>(null);
  const load = useCallback(() => fetchStockUnits({ branch }), [branch]);
  const state = useLoad(load, "No se pudieron cargar los equipos.", reloadKey);

  return (
    <Region title="Costo de cada equipo" description="Equipos con número de serie: cada uno tiene su propio costo.">
      {editing ? (
        <div className="mb-4">
          <CostCorrection
            unit={editing}
            onCancel={() => setEditing(null)}
            onDone={() => { setEditing(null); setReloadKey((k) => k + 1); }}
          />
        </div>
      ) : null}
      {state.kind === "loading" ? <Spinner label="Cargando…" /> : null}
      {state.kind === "error" ? <ErrorBox message={state.message} /> : null}
      {state.kind === "ready" && state.data.results.length === 0 ? (
        <EmptyBox message="No hay equipos con número de serie en esta selección." />
      ) : null}
      {state.kind === "ready" && state.data.results.length > 0 ? (
        <TableWrap>
          <thead className="border-b border-bd-border">
            <tr>
              <Th>Equipo</Th>
              <Th>Serie</Th>
              <Th>Sucursal</Th>
              <Th>Estado</Th>
              <Th right>Costo</Th>
              <Th>Origen del costo</Th>
              <Th> </Th>
            </tr>
          </thead>
          <tbody>
            {state.data.results.map((unit) => {
              const hidden = !("cost" in unit);
              const sold = unit.status === "sold";
              const writtenOff = unit.status === "written_off";
              return (
                <tr key={unit.id} className="border-b border-bd-border/70 last:border-0">
                  <Td>{unit.product_name}</Td>
                  <Td muted>{unit.serial_number}</Td>
                  <Td muted>{unit.branch_name}</Td>
                  <Td muted>{unit.status_label}</Td>
                  <Td right>{hidden ? "—" : decimal(unit.cost)}</Td>
                  <Td muted>
                    {hidden ? COSTS_PERMISSION_HINT : unit.cost ? "Costo específico del equipo" : "Costo pendiente"}
                  </Td>
                  <Td right>
                    {sold ? (
                      <span className="text-[11px] text-muted">Vendido: conserva el costo de su venta</span>
                    ) : writtenOff ? (
                      <span className="text-[11px] text-muted">Dado de baja: conserva su costo</span>
                    ) : canCorrect && !hidden ? (
                      <button type="button" onClick={() => setEditing(unit)} className={internalButtonClass}>
                        Corregir costo
                      </button>
                    ) : null}
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </TableWrap>
      ) : null}
    </Region>
  );
}

// ---------------------------------------------------------------------------
// The screen
// ---------------------------------------------------------------------------

function CostsAndPrices({ access }: { access: InternalAccess }) {
  const needsCompany = access.isPlatformAdmin && !access.hasCompanyContext;
  return (
    <AdminShell user={access.user}>
      <div className="space-y-8">
        <PageHeader
          eyebrow="Inventario"
          title="Costos y precios"
          description="Margen comercial, inventario al costo, costo promedio por sucursal y costo de cada equipo. Cada cifra dice de dónde sale su costo."
        />
        {needsCompany ? (
          // The master passes every permission but belongs to no company: every
          // endpoint behind this screen would answer 403 without one.
          <EmptyBox message="Elige una empresa en el selector del panel para ver sus costos y precios." />
        ) : (
          <CostsBody access={access} />
        )}
      </div>
    </AdminShell>
  );
}

function CostsBody({ access }: { access: InternalAccess }) {
  const scope = useBranchScope({ preferAggregate: true });
  const branch = scope.branch;
  const companyId = access.dashboard?.company?.id ?? null;
  const canEditProducts = access.can("products.manage", ADMIN_ROLES);
  const canCorrect = access.can("inventory.adjust", ["inventory", ...ADMIN_ROLES]);

  if (scope.loading) return <Spinner label="Cargando…" />;
  if (scope.error) return <ErrorBox message={scope.error} />;
  if (!scope.ready) {
    return <EmptyBox message="No tienes sucursales asignadas. Pide a un administrador de la empresa que te asigne al menos una." />;
  }

  return (
    <>
      <BranchSelector access={scope.access} value={scope.branch} onChange={scope.setBranch} />
      <SalesMarginSection companyId={companyId} branchId={typeof branch === "number" ? branch : null} canSeeCosts />
      <AtCostSection branch={branch} />
      <BranchCostSection branch={branch} canEditProducts={canEditProducts} />
      <DeviceCostSection branch={branch} canCorrect={canCorrect} />
    </>
  );
}

export default function CostsPage() {
  return (
    <AccessGuard capability="costs.view" legacyRoles={["admin", "superadmin"]} deniedMessage={COSTS_PERMISSION_HINT}>
      {(access) => <CostsAndPrices access={access} />}
    </AccessGuard>
  );
}
