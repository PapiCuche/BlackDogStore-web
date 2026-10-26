"use client";

import { storageGet, storageSet } from "../../lib/browser-storage";
// INV-SWITCH-01 — «Modo rápido» and «Modo avanzado» of the inventory.
//
// Two views over the SAME screens and services. The quick one only lists the
// day's tasks and links to the screens that already do them; the advanced one
// is the dashboard as it always was. Nothing here reads or writes stock. The
// choice is a per-browser convenience: unreadable storage means advanced.

import Link from "next/link";
import { useSyncExternalStore } from "react";

export type InventoryMode = "quick" | "advanced";
const KEY = "bds-inventory-mode";

export function readInventoryMode(): InventoryMode {
  try {
    return storageGet("local", KEY) === "quick" ? "quick" : "advanced";
  } catch {
    return "advanced";
  }
}

const listeners = new Set<() => void>();
let fallback: InventoryMode | null = null;

function remember(mode: InventoryMode) {
  try {
    storageSet("local", KEY, mode);
  } catch {
    // Private window or blocked storage: the choice lasts this visit only.
    fallback = mode;
  }
  listeners.forEach((notify) => notify());
}

function subscribe(notify: () => void) {
  listeners.add(notify);
  return () => { listeners.delete(notify); };
}

/** The chosen view; «advanced» on the server and until the browser says otherwise. */
export function useInventoryMode(): [InventoryMode, (mode: InventoryMode) => void] {
  const mode = useSyncExternalStore(subscribe, () => fallback ?? readInventoryMode(), () => "advanced" as InventoryMode);
  return [mode, remember];
}

export function InventoryModeSwitch({ mode, onChange }: { mode: InventoryMode; onChange: (mode: InventoryMode) => void }) {
  const choose = (next: InventoryMode) => {
    remember(next);
    onChange(next);
  };
  return (
    <div className="inline-flex rounded-lg border border-bd-border p-0.5" role="group" aria-label="Vista del inventario">
      {([["quick", "Modo rápido"], ["advanced", "Modo avanzado"]] as const).map(([value, label]) => (
        <button
          key={value}
          type="button"
          aria-pressed={mode === value}
          onClick={() => choose(value)}
          className={`rounded-md px-3 py-1.5 text-xs font-semibold transition ${
            mode === value ? "bg-surface-2 text-foreground" : "text-muted hover:text-foreground"
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

type Task = { href: string; title: string; hint: string };

export function QuickInventoryTasks({ canSeeSuppliers, canRegister }: { canSeeSuppliers: boolean; canRegister: boolean }) {
  const tasks: Task[] = [
    { href: "/admin/inventory/stock", title: "Consultar stock y precios", hint: "Qué hay, dónde y a qué precio." },
    { href: "/admin/inventory/bulk", title: "Subir lista o Excel", hint: "Cargas masivas con vista previa, y exportar el inventario." },
    ...(canSeeSuppliers
      ? [{ href: "/admin/products/suppliers/offers", title: "Ofertas de proveedores", hint: "Lista semanal: previsualizar antes de aplicar." }]
      : []),
    { href: "/admin/inventory/units", title: "Equipos con serie e IMEI", hint: "Buscar, registrar y escanear equipos." },
    { href: "/admin/inventory/used", title: "Seminuevos", hint: "Equipos usados con su batería y estado." },
    ...(canRegister ? [{ href: "/admin/products/register", title: "Registrar producto", hint: "Alta manual de un producto o equipo." }] : []),
  ];
  return (
    <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-label="Tareas rápidas de inventario">
      {tasks.map((task) => (
        <li key={task.href}>
          <Link
            href={task.href}
            className="block h-full rounded-xl border border-bd-border bg-surface p-4 transition hover:bg-surface-2"
          >
            <span className="block text-sm font-semibold text-foreground">{task.title}</span>
            <span className="mt-1 block text-xs text-muted">{task.hint}</span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
