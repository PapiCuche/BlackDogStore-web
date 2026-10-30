"use client";

/**
 * INV-OPS-01 — Centro de operaciones del inventario.
 *
 * La entrada principal del módulo: cinco tareas del día a día en tarjetas
 * grandes, cada una con una frase que dice qué hace. Una tarea que el usuario
 * no puede hacer no se esconde: se muestra desactivada con el motivo, para que
 * una persona nueva sepa a quién pedírselo. Todas las pantallas avanzadas
 * siguen a un clic, en «Modo avanzado».
 *
 * Esto es presentación: cada pantalla y cada endpoint vuelven a decidir.
 */

import Link from "next/link";

import { buildAccessContext } from "./AdminShell";
import type { InternalAccess } from "../lib/internal-access";
import { canAccessModule, INTERNAL_MODULES } from "../lib/internal-modules";

type Task = {
  id: string;
  title: string;
  hint: string;
  href: string;
  allowed: boolean;
  /** Said when `allowed` is false: what is missing and whom to ask. */
  reason: string;
};

const ASK = "Pídelo a quien administra el inventario.";

export function operationTasks(access: InternalAccess): Task[] {
  const adjust = access.can("inventory.adjust", ["inventory", "admin", "superadmin"]);
  const view = access.can("inventory.view", ["inventory", "admin", "superadmin"]);
  const labels = access.can("inventory.labels.print", ["inventory", "admin", "superadmin"]);
  const products = access.can("products.view", ["admin", "superadmin"]);
  return [
    {
      id: "receive", title: "Recibir mercadería",
      hint: "Llegó una compra: anota qué trajo, revisa y deja el stock y las etiquetas listos.",
      href: "/admin/inventory/receive", allowed: adjust && products,
      reason: adjust
        ? "Para recibir necesitas poder ver productos: el primer paso es buscar el artículo. Pídelo a quien administra el sistema."
        : `Tu usuario no puede registrar ingresos de stock. ${ASK}`,
    },
    {
      id: "quick", title: "Registrar un equipo rápidamente",
      hint: "Un solo equipo con su serie e IMEI, sin armar una recepción completa.",
      href: "/admin/inventory/units?register=1", allowed: adjust,
      reason: `Tu usuario no puede registrar equipos. ${ASK}`,
    },
    {
      id: "count", title: "Recuento físico",
      hint: "Cuenta lo que hay en el estante y corrige las diferencias con el sistema.",
      href: "/admin/inventory/counts", allowed: adjust,
      reason: `Tu usuario no puede hacer recuentos. ${ASK}`,
    },
    {
      id: "labels", title: "Etiquetas pendientes",
      hint: "Imprime o revisa las etiquetas de código de barras que están por pegar.",
      href: "/admin/inventory/labels", allowed: labels,
      reason: `Tu usuario no puede imprimir etiquetas. ${ASK}`,
    },
    {
      id: "stock", title: "Consultar stock",
      hint: "Mira cuánto hay de cada producto en cada sucursal.",
      href: "/admin/inventory/stock", allowed: view,
      reason: `Tu usuario no puede ver el inventario. ${ASK}`,
    },
  ];
}

/**
 * Advanced screens, by the task they serve (NAV-UX-02) and with a plain-language gloss where the name is jargon.
 * The sidebar keeps its own short list; this is the longer one, for the person who is looking for something specific.
 */
const ADVANCED: { id: string; title: string; items: { id: string; gloss?: string }[] }[] = [
  {
    id: "look", title: "Consultar",
    items: [{ id: "inventory.stock" }, { id: "inventory.availability" }, { id: "inventory.serial" }, { id: "inventory.used" }],
  },
  {
    id: "move", title: "Mover y contar",
    items: [
      { id: "inventory.movements", gloss: "Kardex: el historial de cada entrada, salida y ajuste de stock." },
      { id: "inventory.transfers" },
      { id: "inventory.counts" },
    ],
  },
  {
    id: "tools", title: "Cargas con Excel, análisis y reposición",
    items: [
      { id: "inventory.tools" }, { id: "inventory.bulk" }, { id: "inventory.import" }, { id: "inventory.units.import" },
      { id: "inventory.costs" }, { id: "inventory.reports" }, { id: "inventory.replenishment" },
    ],
  },
  { id: "labels", title: "Etiquetas", items: [{ id: "inventory.labels.settings" }] },
];

export function InventoryOperationsHub({ access }: { access: InternalAccess }) {
  const tasks = operationTasks(access);
  const ctx = buildAccessContext(access.user, access.dashboard);
  const advanced = ADVANCED.flatMap((group) => {
    const items = group.items.flatMap(({ id, gloss }) => {
      const mod = INTERNAL_MODULES.find((m) => m.id === id);
      return mod && mod.href && canAccessModule(mod, ctx) ? [{ mod, gloss: gloss ?? mod.description }] : [];
    });
    return items.length ? [{ ...group, items }] : [];
  });

  return (
    <div className="space-y-8">
      <ul aria-label="Tareas del inventario" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {tasks.map((task) => (
          <li key={task.id} className="min-w-0">
            {task.allowed ? (
              <Link
                href={task.href}
                className="flex h-full min-h-[8rem] flex-col justify-between rounded-2xl border border-bd-border bg-surface p-5 transition hover:border-foreground/30 hover:bg-surface-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
              >
                <span className="block text-base font-bold text-foreground">{task.title}</span>
                <span className="mt-2 block text-sm leading-6 text-muted">{task.hint}</span>
              </Link>
            ) : (
              <div
                aria-disabled="true"
                className="flex h-full min-h-[8rem] flex-col justify-between rounded-2xl border border-dashed border-bd-border bg-surface/50 p-5"
              >
                <span className="block text-base font-bold text-muted">{task.title}</span>
                <span className="mt-2 block text-sm leading-6 text-muted">{task.hint}</span>
                <span className="mt-2 block text-xs font-semibold text-warning">{task.reason}</span>
              </div>
            )}
          </li>
        ))}
      </ul>

      {advanced.length > 0 ? (
        <section aria-labelledby="inv-advanced" className="rounded-xl border border-bd-border bg-surface p-5">
          <h2 id="inv-advanced" className="text-sm font-semibold text-foreground">Modo avanzado</h2>
          <p className="mt-1 text-xs text-muted">
            Todas las pantallas detalladas del inventario, por tarea, por si necesitas algo más específico.
          </p>
          <div className="mt-4 space-y-5">
            {advanced.map((group) => (
              <div key={group.id}>
                <h3 className="text-xs font-semibold uppercase tracking-[0.15em] text-muted">{group.title}</h3>
                <ul className="mt-2 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  {group.items.map(({ mod, gloss }) => (
                    <li key={mod.id} className="min-w-0">
                      <Link href={mod.href!} className="block h-full rounded-lg border border-bd-border px-3 py-2.5 transition hover:bg-surface-2">
                        <span className="block text-sm font-semibold text-foreground">{mod.label}</span>
                        <span className="mt-0.5 block text-xs text-muted">{gloss}</span>
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
