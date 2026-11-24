"use client";

/**
 * Productos › Modelos — DEVICE-COMMERCE-01 · DC2.
 *
 * `products.view` para llegar; crear o cambiar exige `products.manage`. El
 * servidor comprueba las dos; aquí sólo se decide qué controles se pintan.
 */

import Link from "next/link";
import { useCallback, useState } from "react";

import { AccessGuard } from "../../components/AccessGuard";
import { AddressQuery } from "../../components/AddressQuery";
import { modelsAnchorFor } from "../../lib/models-sections";
import { AdminShell } from "../../components/AdminShell";
import { CatalogModelsManager } from "../../components/CatalogModelsManager";
import { CatalogReadinessPanel } from "../../components/CatalogReadinessPanel";
import { PalettesToBuildPanel } from "../../components/PalettesToBuildPanel";
import { PageHeader, internalButtonClass } from "../../components/internal-ui";
import type { InternalAccess } from "../../lib/internal-access";
import type { AuthUser } from "../../../lib/auth";

function ModelsContent({ user, access }: { user: AuthUser; access: InternalAccess }) {
  // NAV-SALES-01: «Productos › Colores» opens this page with `?seccion=colores`, also while it is already open.
  const [goTo, setGoTo] = useState<{ anchor: string; request: number } | null>(null);
  const onQuery = useCallback((query: string) => {
    const anchor = modelsAnchorFor(query);
    if (anchor) setGoTo((previous) => ({ anchor, request: (previous?.request ?? 0) + 1 }));
  }, []);
  return (
    <AdminShell user={user}>
      <AddressQuery onChange={onQuery} />
      <div className="space-y-6">
        <PageHeader
          eyebrow="Productos"
          title="Modelos"
          description="Agrupa en un modelo los productos que son opciones de un mismo equipo —capacidad, color, nuevo o seminuevo— para venderlos desde una sola página."
          actions={
            // CATALOG-REFERENCE-01 · PR E: import verified models instead of typing them by hand.
            <Link href="/admin/products/models/reference" className={`inline-flex min-h-[44px] items-center ${internalButtonClass}`}>
              Catálogo de referencia
            </Link>
          }
        />
        {/* CATALOG-READINESS-01: lo primero de la página, qué falta antes de registrar el inventario. */}
        <CatalogReadinessPanel companyId={access.dashboard?.company?.id ?? null} />
        {/* PALETTE-SUGGEST-01: los modelos sin paleta, con la que proponen sus variantes; se confirma uno por uno. */}
        <PalettesToBuildPanel
          companyId={access.dashboard?.company?.id ?? null}
          canManage={access.can("products.manage", ["admin", "superadmin"])}
        />
        <CatalogModelsManager
          canManage={access.can("products.manage", ["admin", "superadmin"])}
          companyId={access.dashboard?.company?.id ?? null}
          goTo={goTo}
        />
      </div>
    </AdminShell>
  );
}

export default function CatalogModelsPage() {
  return (
    <AccessGuard capability="products.view" legacyRoles={["inventory", "sales", "admin", "superadmin"]}>
      {(access) => <ModelsContent user={access.user} access={access} />}
    </AccessGuard>
  );
}
