"use client";

/**
 * INVENTORY-UNIFIED-01 — el centro de «Gestión masiva de inventario».
 *
 * REÚNE, NO MEZCLA. Importar sigue siendo tres cargas distintas —productos,
 * stock, equipos— y cada tarjeta manda a su asistente de siempre. Lo nuevo es
 * tenerlas a la vista junto a exportar, las imágenes en lote y el historial, y
 * un asistente que dice a cuál de ellas va un archivo.
 *
 * Cada sección se ofrece según lo que la persona puede hacer. Eso sólo evita
 * enseñar lo que respondería 403: quién puede qué lo decide el servidor.
 */
import Link from "next/link";
import { useEffect, useId, useState } from "react";
import {
  fetchImportHistory,
  whichImport,
  type ImportJob,
  type ImportSuggestion,
} from "../lib/internal-api";
import { BulkImageImport } from "./BulkImageImport";
import { IMPORT_KINDS, type ImportKind } from "./ImportKindNotice";
import { HistoryTable } from "./ImportWizard";
import { InventoryExports } from "./InventoryExportPanel";
import { InventoryTextPreview } from "./InventoryTextPreview";
import { internalButtonClass } from "./internal-ui";

export type BulkCentreAccess = {
  /** `products.manage`: carga de productos e imágenes. */
  productsManage: boolean;
  /** `inventory.adjust`: carga de stock y de equipos. */
  inventoryAdjust: boolean;
  /** `products.view`: descargar el catálogo. */
  productsView: boolean;
  /** `inventory.view`: descargar stock y equipos. */
  inventoryView: boolean;
  /** `costs.view` (DC1): el costo en las descargas. Ajustar inventario no lo implica. */
  costsView: boolean;
};

function Section({
  name, description, id, children,
}: { name: string; description: string; id?: string; children: React.ReactNode }) {
  return (
    <section role="region" aria-label={name} id={id} className="space-y-4 scroll-mt-24">
      <div>
        <h2 className="font-display text-base font-semibold tracking-wide text-foreground">{name}</h2>
        <p className="mt-0.5 text-xs text-muted">{description}</p>
      </div>
      {children}
    </section>
  );
}

/** A dónde manda cada respuesta del asistente. Lo que no es una carga no manda a ninguna. */
const DESTINATIONS: Partial<Record<ImportSuggestion["suggested_import_type"], { label: string; href: string }>> = {
  products: IMPORT_KINDS.products,
  stock: IMPORT_KINDS.stock,
  units: IMPORT_KINDS.units,
  images: { label: "Imágenes", href: "#imagenes" },
};

/**
 * «¿Qué archivo tengo?» — se elige UN libro y el servidor dice a qué carga va.
 *
 * No carga nada: el archivo vuelve a elegirse en el asistente de su carga, que
 * es quien lo previsualiza. Aquí sólo se responde «a dónde».
 */
export function ImportAssistant({ companyId }: { companyId: number | null }) {
  const inputId = useId();
  const [fileName, setFileName] = useState("");
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<ImportSuggestion | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function ask(file: File) {
    setFileName(file.name);
    setBusy(true);
    setAnswer(null);
    setError(null);
    try {
      setAnswer(await whichImport(companyId, file));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No se pudo leer el archivo.");
    } finally {
      setBusy(false);
    }
  }

  const destination = answer ? DESTINATIONS[answer.suggested_import_type] : undefined;

  return (
    <div className="space-y-3 rounded-xl border border-bd-border bg-surface p-4 sm:p-5">
      <p className="text-sm text-muted">
        Elige un archivo de Excel y te decimos a qué carga corresponde. Sólo mira los nombres de las
        hojas y las cabeceras: no carga nada ni guarda el archivo.
      </p>
      <label htmlFor={inputId} className="block text-sm font-semibold text-foreground">
        Elegir un archivo de Excel
      </label>
      <input
        id={inputId}
        type="file"
        accept=".xlsx"
        disabled={busy}
        onChange={(event) => {
          const selected = event.target.files?.[0];
          event.target.value = "";
          if (selected) void ask(selected);
        }}
        className="block w-full text-sm text-muted file:mr-4 file:rounded-xl file:border-0 file:bg-primary file:px-4 file:py-2 file:text-sm file:font-semibold file:text-background"
      />
      {busy ? <p role="status" className="text-sm text-muted">Revisando {fileName}…</p> : null}
      {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
      {answer ? (
        <div role="status" className="rounded-lg border border-bd-border bg-background p-4 text-sm">
          <p className="break-all text-xs text-muted">{fileName}</p>
          <p className="mt-1 text-foreground">{answer.suggestion_detail}</p>
          {destination ? (
            <Link href={destination.href} className={`${internalButtonClass} mt-3 inline-block`}>
              Ir a {destination.label}
            </Link>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function History({ companyId }: { companyId: number | null }) {
  const [jobs, setJobs] = useState<ImportJob[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // Sin tipo: el servidor devuelve las cargas que esta persona puede ver, y sólo esas.
    fetchImportHistory(companyId)
      .then((list) => { if (!cancelled) setJobs(list); })
      .catch((caught) => {
        if (!cancelled) setError(caught instanceof Error ? caught.message : "No se pudo cargar el historial.");
      });
    return () => { cancelled = true; };
  }, [companyId]);

  if (error) return <p className="text-sm text-danger">{error}</p>;
  if (jobs === null) return <p className="text-sm text-muted">Cargando el historial…</p>;
  return <HistoryTable jobs={jobs} companyId={companyId} />;
}

const CARD_ORDER: ImportKind[] = ["products", "stock", "units"];

export function BulkInventoryCentre({ companyId, can }: { companyId: number | null; can: BulkCentreAccess }) {
  const canImport = can.productsManage || can.inventoryAdjust;
  const canExport = can.productsView || can.inventoryView;
  const cards = CARD_ORDER.filter((kind) => (kind === "products" ? can.productsManage : can.inventoryAdjust));

  if (!canImport && !canExport) {
    return (
      <p className="rounded-lg border border-bd-border bg-surface px-4 py-3 text-sm text-muted">
        No tienes permisos para la gestión masiva de inventario en esta empresa.
      </p>
    );
  }

  return (
    <div className="space-y-10">
      {canImport ? (
        <Section name="¿Qué archivo tengo?" description="Si no sabes a qué carga va un archivo, pregúntalo antes de subirlo.">
          <ImportAssistant companyId={companyId} />
          <InventoryTextPreview canSeeCosts={can.costsView} canManageProducts={can.productsManage}
            canReceiveUnits={can.inventoryAdjust} companyId={companyId} />
        </Section>
      ) : null}

      {canImport ? (
        <Section name="Importar" description="Tres cargas distintas. Cada una hace una cosa y no hace el trabajo de otra.">
          <ul className="grid gap-3 md:grid-cols-3">
            {cards.map((kind) => (
              <li key={kind} className="flex flex-col justify-between gap-3 rounded-xl border border-bd-border bg-surface p-4">
                <div>
                  <h3 className="text-sm font-semibold text-foreground">{IMPORT_KINDS[kind].label}</h3>
                  <p className="mt-1 text-sm text-foreground">{IMPORT_KINDS[kind].does}</p>
                  <p className="mt-1 text-xs text-muted">{IMPORT_KINDS[kind].doesNot}</p>
                </div>
                <Link
                  href={IMPORT_KINDS[kind].href}
                  aria-label={`Abrir ${IMPORT_KINDS[kind].label}`}
                  className={`${internalButtonClass} self-start`}
                >
                  Abrir
                </Link>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {canExport ? (
        <Section name="Exportar" description="El catálogo, el stock por sucursal y los equipos con serie: para volver a cargar o como reporte.">
          <InventoryExports
            companyId={companyId}
            canCatalogue={can.productsView}
            canInventory={can.inventoryView}
            canSeeCost={can.costsView}
          />
        </Section>
      ) : null}

      {can.productsManage ? (
        <Section
          id="imagenes"
          name="Imágenes"
          description="Añadir imágenes a productos que ya existen, muchas a la vez. No crea ni edita productos."
        >
          <BulkImageImport companyId={companyId} />
        </Section>
      ) : null}

      {canImport ? (
        <Section name="Historial" description="Qué se cargó, quién lo hizo y qué produjo. Los errores de cada carga se pueden descargar.">
          <History companyId={companyId} />
        </Section>
      ) : null}
    </div>
  );
}
