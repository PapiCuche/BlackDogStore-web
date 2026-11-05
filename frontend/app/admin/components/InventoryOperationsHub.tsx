"use client";

/**
 * INV-OPS-01 — Centro de operaciones del inventario.
 *
 * La entrada principal del módulo: las tareas del día a día en tarjetas
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
  const manageProducts = access.can("products.manage", ["admin", "superadmin"]);
  const approve = access.can("inventory.counts.approve", ["inventory", "admin", "superadmin"]);
  // Transferring needs two branches the person reaches; unknown scope (legacy) does not block here: the page decides.
  const reached = access.dashboard?.branch_scope?.branches;
  const oneBranch = Array.isArray(reached) && reached.length < 2;
  // INV-OPS-UX-04: the day's tasks in the order a shop does them — receive, count, look, move, import — then the
  // two quick ones. Each card opens the ONE screen for that task; none of them is a second way to do it.
  return [
    {
      id: "receive", title: "Recibir mercadería",
      hint: manageProducts
        ? "Llegó una compra: pega la lista de WhatsApp o busca artículo por artículo, escribe el precio de cada seminuevo y confirma una vez."
        : "Llegó una compra: pega la lista de WhatsApp o busca artículo por artículo y confirma una vez. Los seminuevos quedan pendientes de precio.",
      href: "/admin/inventory/receive", allowed: adjust && products,
      reason: adjust
        ? "Para recibir necesitas poder ver productos: el primer paso es buscar el artículo. Pídelo a quien administra el sistema."
        : `Tu usuario no puede registrar ingresos de stock. ${ASK}`,
    },
    {
      id: "count", title: "Recuento físico",
      hint: "Elige la sucursal, cuenta con el lector o por nombre, y quien aprueba ajusta las diferencias.",
      // Who only approves counts reaches them too: the screen offers approval, not counting.
      href: "/admin/inventory/counts", allowed: adjust || approve,
      reason: `Tu usuario no puede hacer ni aprobar recuentos. ${ASK}`,
    },
    {
      id: "stock", title: "Consultar stock",
      hint: "Mira cuánto hay de cada producto en cada sucursal.",
      href: "/admin/inventory/stock", allowed: view,
      reason: `Tu usuario no puede ver el inventario. ${ASK}`,
    },
    {
      id: "movements", title: "Ver movimientos",
      hint: "El historial de cada entrada, salida y ajuste de stock (Kardex), con quién y cuándo.",
      href: "/admin/inventory/movements", allowed: view,
      reason: `Tu usuario no puede ver el inventario. ${ASK}`,
    },
    {
      id: "transfers", title: "Transferir entre sucursales",
      hint: "Envía stock de una sucursal a otra y confirma cuando llega.",
      href: "/admin/inventory/transfers", allowed: adjust && !oneBranch,
      reason: oneBranch
        ? "Para transferir hacen falta al menos dos sucursales a tu alcance. Pide que te asignen otra si la necesitas."
        : `Tu usuario puede ver las transferencias (en «Modo avanzado») pero no crearlas: hace falta mover inventario. ${ASK}`,
    },
    {
      id: "import", title: "Importar archivos",
      hint: "Cargas con Excel: productos del catálogo, cantidades o equipos. Primero se ve qué haría; nada se aplica sin confirmar.",
      href: "/admin/inventory/bulk", allowed: adjust || manageProducts,
      reason: `Tu usuario no puede importar: hace falta mover inventario o administrar productos. Para descargar el catálogo o el stock, usa «Modo avanzado». ${ASK}`,
    },
    {
      id: "quick", title: "Registrar un equipo rápidamente",
      hint: "Un solo equipo con su serie e IMEI, sin armar una recepción completa.",
      href: "/admin/inventory/units?register=1", allowed: adjust,
      reason: `Tu usuario no puede registrar equipos. ${ASK}`,
    },
    {
      id: "labels", title: "Etiquetas pendientes",
      hint: "Imprime o revisa las etiquetas de código de barras que están por pegar.",
      href: "/admin/inventory/labels", allowed: labels,
      reason: `Tu usuario no puede imprimir etiquetas. ${ASK}`,
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
