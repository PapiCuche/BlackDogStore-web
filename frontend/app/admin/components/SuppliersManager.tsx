"use client";

/**
 * Productos › Proveedores — DEVICE-COMMERCE-01 · DC3.
 *
 * Who sells to the shop what it sells «bajo pedido». Seeing needs
 * `products.view`; creating, editing or switching a supplier off needs
 * `products.manage`, which the server checks on every write. Without it the
 * forms are simply not drawn.
 *
 * «Pendiente de liquidar al proveedor» is read-only and comes only to
 * `costs.view`: what was sold on order and received, at the cost frozen when it
 * was sold. It informs; it is not a record of a debt.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  CatalogApiError,
  SUPPLIER_DOCUMENT_TYPE_LABELS,
  createSupplier,
  fetchSupplierSettlement,
  fetchSuppliers,
  updateSupplier,
  type SupplierRow,
  type SupplierSettlementRow,
  type SupplierWrite,
} from "../lib/internal-api";
import { EmptyBox, ErrorBox, Panel, Spinner, TableWrap, Td, Th, formatSoles } from "./InventoryUi";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

const message = (error: unknown, fallback: string) => (error instanceof Error ? error.message : fallback);

type Draft = {
  name: string;
  document_type: string;
  document_number: string;
  contact_name: string;
  phone: string;
  email: string;
  notes: string;
  is_active: boolean;
};

const EMPTY: Draft = {
  name: "", document_type: "", document_number: "", contact_name: "", phone: "", email: "", notes: "", is_active: true,
};

function draftOf(row: SupplierRow): Draft {
  return {
    name: row.name, document_type: row.document_type ?? "", document_number: row.document_number ?? "",
    contact_name: row.contact_name ?? "", phone: row.phone ?? "", email: row.email ?? "", notes: row.notes ?? "",
    is_active: row.is_active,
  };
}

function SupplierForm({
  initial, submitLabel, onSubmit, onCancel,
}: {
  initial: Draft;
  submitLabel: string;
  onSubmit: (body: SupplierWrite) => Promise<void>;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<Draft>(initial);
  const [fields, setFields] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!draft.name.trim()) { setFields({ name: "Escribe el nombre del proveedor." }); return; }
    setSaving(true);
    setFields({});
    setError(null);
    try {
      await onSubmit({
        name: draft.name.trim(),
        document_type: draft.document_type,
        document_number: draft.document_number.trim(),
        contact_name: draft.contact_name.trim(),
        phone: draft.phone.trim(),
        email: draft.email.trim(),
        notes: draft.notes,
        is_active: draft.is_active,
      });
    } catch (err) {
      setError(message(err, "No se pudo guardar el proveedor."));
      if (err instanceof CatalogApiError) {
        if (Object.keys(err.fields).length) setFields(err.fields);
        if (err.field) setFields((f) => ({ ...f, [err.field as string]: err.message }));
      }
    } finally {
      setSaving(false);
    }
  }

  const field = (key: keyof Draft, label: string, extra: React.InputHTMLAttributes<HTMLInputElement> = {}) => (
    <label className="block text-xs text-muted">
      {label}
      <input
        value={String(draft[key])}
        onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
        aria-invalid={fields[key] ? true : undefined}
        className={`mt-1.5 ${internalInputClass}`}
        {...extra}
      />
      {fields[key] ? <span className="mt-1 block text-danger">{fields[key]}</span> : null}
    </label>
  );

  return (
    <form onSubmit={submit} className="mb-6 grid gap-3 rounded-lg border border-bd-border bg-background p-4 md:grid-cols-2" aria-label={submitLabel} noValidate>
      {field("name", "Nombre o razón social")}
      <div className="grid grid-cols-[minmax(0,10rem)_minmax(0,1fr)] gap-3">
        <label className="block text-xs text-muted">
          Documento
          <select
            value={draft.document_type}
            onChange={(e) => setDraft({ ...draft, document_type: e.target.value })}
            className={`mt-1.5 ${internalInputClass}`}
          >
            {Object.entries(SUPPLIER_DOCUMENT_TYPE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
          {fields.document_type ? <span className="mt-1 block text-danger">{fields.document_type}</span> : null}
        </label>
        {field("document_number", "Número", { inputMode: "numeric", className: `mt-1.5 font-mono ${internalInputClass}` })}
      </div>
      {field("contact_name", "Persona de contacto")}
      {field("phone", "Teléfono", { type: "tel" })}
      {field("email", "Correo", { type: "email" })}
      <label className="flex items-center gap-2 self-end text-sm text-foreground">
        <input type="checkbox" checked={draft.is_active} onChange={(e) => setDraft({ ...draft, is_active: e.target.checked })} />
        Activo
      </label>
      <label className="block text-xs text-muted md:col-span-2">
        Notas internas
        <textarea value={draft.notes} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} rows={2} className={`mt-1.5 ${internalInputClass}`} />
      </label>
      {error ? <p role="alert" className="text-xs text-danger md:col-span-2">{error}</p> : null}
      <div className="flex justify-end gap-2 md:col-span-2">
        <button type="button" className={internalButtonClass} onClick={onCancel} disabled={saving}>Cancelar</button>
        <button type="submit" className={internalPrimaryButtonClass} disabled={saving}>{saving ? "Guardando…" : submitLabel}</button>
      </div>
    </form>
  );
}

function SuppliersSection({ canManage, companyId }: { canManage: boolean; companyId: number | null }) {
  const [rows, setRows] = useState<SupplierRow[] | null>(null);
  const [count, setCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [term, setTerm] = useState("");
  const [active, setActive] = useState<"" | "true" | "false">("");
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<number | null>(null);
  const [toggling, setToggling] = useState<number | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => setTerm(search.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  const load = useCallback(() => {
    fetchSuppliers({ search: term, active }, companyId).then(
      (page) => { setRows(page.results); setCount(page.count); setError(null); },
      (err) => setError(message(err, "No se pudieron cargar los proveedores.")),
    );
  }, [term, active, companyId]);

  useEffect(() => { load(); }, [load]);

  async function toggle(row: SupplierRow) {
    setToggling(row.id);
    setError(null);
    try {
      await updateSupplier(row.id, { is_active: !row.is_active }, companyId);
      load();
    } catch (err) {
      setError(message(err, "No se pudo cambiar el proveedor."));
    } finally {
      setToggling(null);
    }
  }

  const editingRow = rows?.find((row) => row.id === editing) ?? null;

  return (
    <Panel
      title="Proveedores"
      description="A quién le compras lo que vendes bajo pedido. Sus ofertas dicen a qué precio, cuántas unidades y en cuántos días."
      action={canManage && !creating && editing === null ? (
        <button type="button" className={internalPrimaryButtonClass} onClick={() => setCreating(true)}>+ Nuevo proveedor</button>
      ) : null}
    >
      {canManage && creating ? (
        <SupplierForm
          initial={EMPTY}
          submitLabel="Crear proveedor"
          onCancel={() => setCreating(false)}
          onSubmit={async (body) => {
            await createSupplier(body, companyId);
            setCreating(false);
            load();
          }}
        />
      ) : null}
      {canManage && editingRow ? (
        <SupplierForm
          key={editingRow.id}
          initial={draftOf(editingRow)}
          submitLabel="Guardar proveedor"
          onCancel={() => setEditing(null)}
          onSubmit={async (body) => {
            await updateSupplier(editingRow.id, body, companyId);
            setEditing(null);
            load();
          }}
        />
      ) : null}

      <div className="mb-4 flex flex-wrap items-end gap-3">
        <label className="block min-w-48 flex-1 text-xs text-muted">
          Buscar
          <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Nombre, documento o contacto" className={`mt-1.5 ${internalInputClass}`} />
        </label>
        <label className="block text-xs text-muted">
          Estado
          <select value={active} onChange={(e) => setActive(e.target.value as "" | "true" | "false")} className={`mt-1.5 ${internalInputClass}`}>
            <option value="">Todos</option>
            <option value="true">Activos</option>
            <option value="false">Inactivos</option>
          </select>
        </label>
        <Link href="/admin/products/suppliers/offers" className={internalButtonClass}>Ver ofertas</Link>
      </div>

      {error ? <div className="mb-4"><ErrorBox message={error} /></div> : null}
      {rows === null && !error ? <Spinner label="Cargando proveedores…" /> : null}
      {rows && rows.length === 0 ? (
        <EmptyBox message={canManage && !term && !active ? "Todavía no hay proveedores. Crea el primero para registrar sus ofertas." : "No hay proveedores que coincidan."} />
      ) : null}
      {rows && rows.length > 0 ? (
        <>
          <TableWrap>
            <thead>
              <tr className="border-b border-bd-border">
                <Th>Proveedor</Th><Th>Documento</Th><Th>Contacto</Th><Th right>Ofertas</Th><Th>Estado</Th>
                {canManage ? <Th right>Acciones</Th> : null}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className="border-b border-bd-border/60">
                  <Td>
                    <span className="font-semibold">{row.name}</span>
                    {row.notes ? <span className="block max-w-xs truncate text-xs text-muted">{row.notes}</span> : null}
                  </Td>
                  <Td muted>
                    {row.document_number
                      ? `${SUPPLIER_DOCUMENT_TYPE_LABELS[row.document_type] ?? row.document_type.toUpperCase()} ${row.document_number}`
                      : "—"}
                  </Td>
                  <Td muted>
                    {[row.contact_name, row.phone, row.email].filter(Boolean).join(" · ") || "—"}
                  </Td>
                  <Td right>
                    <Link href={`/admin/products/suppliers/offers?supplier=${row.id}`} className="underline-offset-4 hover:underline">
                      {row.offer_count}
                    </Link>
                  </Td>
                  <Td muted>{row.is_active ? "Activo" : "Inactivo"}</Td>
                  {canManage ? (
                    <Td right>
                      <span className="inline-flex gap-2">
                        <button type="button" className={internalButtonClass} onClick={() => { setCreating(false); setEditing(row.id); }} aria-label={`Editar ${row.name}`}>
                          Editar
                        </button>
                        <button
                          type="button"
                          className={internalButtonClass}
                          disabled={toggling === row.id}
                          onClick={() => void toggle(row)}
                          aria-label={`${row.is_active ? "Desactivar" : "Activar"} ${row.name}`}
                        >
                          {row.is_active ? "Desactivar" : "Activar"}
                        </button>
                      </span>
                    </Td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </TableWrap>
          <p className="mt-3 text-xs text-muted">{count === 1 ? "1 proveedor" : `${count} proveedores`}</p>
        </>
      ) : null}
    </Panel>
  );
}

/**
 * Read-only, `costs.view`. A 403 hides the section: the person may see
 * suppliers and still not what is owed to them.
 */
function SettlementSection({ companyId }: { companyId: number | null }) {
  const [rows, setRows] = useState<SupplierSettlementRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hidden, setHidden] = useState(false);

  useEffect(() => {
    fetchSupplierSettlement(companyId).then(
      (loaded) => { setRows(loaded); setError(null); },
      (err) => {
        if (err instanceof CatalogApiError && err.status === 403) setHidden(true);
        else setError(message(err, "No se pudo cargar lo pendiente de liquidar."));
      },
    );
  }, [companyId]);

  if (hidden) return null;

  return (
    <Panel
      title="Pendiente de liquidar al proveedor"
      description="Lo vendido bajo pedido y recibido, al costo acordado que quedó fijado en la venta. Es informativo: no registra una deuda ni un pago."
    >
      {error ? <ErrorBox message={error} /> : null}
      {rows === null && !error ? <Spinner label="Cargando…" /> : null}
      {rows && rows.length === 0 ? <EmptyBox message="No hay nada pendiente de liquidar." /> : null}
      {rows && rows.length > 0 ? (
        <TableWrap>
          <thead>
            <tr className="border-b border-bd-border">
              <Th>Proveedor</Th><Th right>Unidades</Th><Th right>Importe al costo</Th><Th>Observación</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.supplier_id} className="border-b border-bd-border/60">
                <Td>{row.supplier_name}</Td>
                <Td right>{row.units}</Td>
                <Td right>{formatSoles(row.amount)}</Td>
                <Td muted>
                  {row.units_without_cost > 0
                    ? `${row.units_without_cost} ${row.units_without_cost === 1 ? "unidad" : "unidades"} sin costo acordado: no suman al importe.`
                    : "—"}
                </Td>
              </tr>
            ))}
          </tbody>
        </TableWrap>
      ) : null}
    </Panel>
  );
}

export function SuppliersManager({
  canManage, canSeeCosts, companyId,
}: { canManage: boolean; canSeeCosts: boolean; companyId: number | null }) {
  return (
    <div className="space-y-6">
      <SuppliersSection canManage={canManage} companyId={companyId} />
      {canSeeCosts ? <SettlementSection companyId={companyId} /> : null}
    </div>
  );
}
