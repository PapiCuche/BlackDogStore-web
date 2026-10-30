"use client";

/**
 * NAV-UX-02 — «Más herramientas»: las herramientas avanzadas del inventario, agrupadas por la TAREA que hacen y no
 * por el nombre de la pantalla. No es una función nueva: cada tarjeta lleva a una pantalla que ya existía, con su
 * ruta y su permiso. Una herramienta que el usuario no puede abrir no se muestra (el menú tampoco la muestra);
 * esconder un enlace no autoriza nada y cada pantalla y cada servidor deciden por su cuenta.
 */

import Link from "next/link";

import type { ModuleAccessContext } from "../lib/internal-modules";
import { canAccessModule, INTERNAL_MODULES } from "../lib/internal-modules";

type ToolGroup = {
  id: string;
  title: string;
  hint: string;
  tools: { id: string; gloss: string }[];
};

export const TOOL_GROUPS: ToolGroup[] = [
  {
    id: "load",
    title: "Cargar o sacar datos con Excel",
    hint: "Para muchos artículos, equipos o cantidades a la vez. Cada carga se revisa antes de aplicarse.",
    tools: [
      { id: "inventory.bulk", gloss: "El punto de partida: importar, exportar, subir imágenes en lote y ver el historial de cargas." },
      { id: "products.import", gloss: "Crea y edita artículos y accesorios desde Excel. No mueve stock." },
      { id: "inventory.import", gloss: "Ajusta cantidades desde Excel. Deja un movimiento en el Kardex por cada ajuste." },
      { id: "inventory.units.import", gloss: "Registra equipos con serie e IMEI desde Excel: una fila, un equipo." },
    ],
  },
  {
    id: "analyse",
    title: "Revisar cómo va el negocio",
    hint: "Sólo lectura: no cambian existencias.",
    tools: [
      { id: "inventory.costs", gloss: "Margen comercial, inventario al costo y costo de cada equipo." },
      { id: "inventory.reports", gloss: "Bajo stock, rotación y valorización estimada." },
    ],
  },
  {
    id: "restock",
    title: "Reponer",
    hint: "Qué pedir según el mínimo y el objetivo de cada sucursal.",
    tools: [{ id: "inventory.replenishment", gloss: "Sugerencias de reposición por sucursal." }],
  },
];

export function visibleToolGroups(ctx: ModuleAccessContext) {
  return TOOL_GROUPS.flatMap((group) => {
    const tools = group.tools.flatMap(({ id, gloss }) => {
      const mod = INTERNAL_MODULES.find((m) => m.id === id);
      return mod && mod.href && canAccessModule(mod, ctx) ? [{ mod, gloss }] : [];
    });
    return tools.length ? [{ ...group, tools }] : [];
  });
}

export function InventoryToolsHub({ access }: { access: ModuleAccessContext }) {
  const groups = visibleToolGroups(access);
  if (!groups.length) {
    return (
      <p role="status" className="rounded-xl border border-dashed border-bd-border p-5 text-sm text-muted">
        Tu usuario no tiene herramientas avanzadas disponibles. Pídelas a quien administra el inventario.
      </p>
    );
  }
  return (
    <div className="space-y-8">
      {groups.map((group) => (
        <section key={group.id} aria-labelledby={`tools-${group.id}`}>
          <h2 id={`tools-${group.id}`} className="text-sm font-semibold text-foreground">{group.title}</h2>
          <p className="mt-1 text-xs text-muted">{group.hint}</p>
          <ul className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {group.tools.map(({ mod, gloss }) => (
              <li key={mod.id} className="min-w-0">
                <Link
                  href={mod.href!}
                  className="flex h-full min-h-[5.5rem] flex-col justify-between rounded-xl border border-bd-border bg-surface p-4 transition hover:border-foreground/30 hover:bg-surface-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
                >
                  <span className="block text-sm font-semibold text-foreground">{mod.label}</span>
                  <span className="mt-1 block text-xs leading-5 text-muted">{gloss}</span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
