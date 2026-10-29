"use client";

/**
 * Productos › Registro / Carga masiva — COMMERCE-ADMIN-UX-01.
 *
 * Un artículo, desde una sola pantalla (`ArticleRegistrationWizard`), o muchos
 * desde Excel por las cargas de siempre, que conservan su entrada y su ruta.
 *
 * `products.manage` para llegar: es lo mínimo para registrar algo. Lo demás
 * (equipos y stock: `inventory.adjust`; costos: `costs.view`) lo decide cada
 * paso, con el motivo a la vista, y el servidor otra vez en cada envío.
 */

import Link from "next/link";
import { useState } from "react";
import { AccessGuard } from "../../components/AccessGuard";
import { AdminShell } from "../../components/AdminShell";
import { ArticleRegistrationWizard } from "../../components/ArticleRegistrationWizard";
import { ErrorBox, Spinner } from "../../components/InventoryUi";
import { PageHeader } from "../../components/internal-ui";
import type { InternalAccess } from "../../lib/internal-access";
import { useBranchScope } from "../../lib/use-branch-scope";
import type { AuthUser } from "../../../lib/auth";

const BULK_LINKS = [
  { href: "/admin/inventory/bulk", label: "Gestión masiva de inventario", capability: ["products.view", "inventory.view"] },
  { href: "/admin/products/import", label: "Carga de productos (Excel)", capability: ["products.manage"] },
  { href: "/admin/inventory/units/import", label: "Carga de equipos (Excel)", capability: ["inventory.adjust"] },
  { href: "/admin/inventory/import", label: "Carga de stock (Excel)", capability: ["inventory.adjust"] },
  { href: "/admin/products/suppliers/offers", label: "Ofertas de proveedores (Excel)", capability: ["products.view"] },
];

function RegisterContent({ user, access }: { user: AuthUser; access: InternalAccess }) {
  const scope = useBranchScope({ preferAggregate: false });
  // INV-OPS-01: «Recibir mercadería › No existe: crear artículo» opens this with ?from=receive.
  const [fromReceive] = useState(
    () => typeof window !== "undefined" && new URLSearchParams(window.location.search).get("from") === "receive",
  );
  const can = {
    manage: access.can("products.manage", ["admin", "superadmin"]),
    adjust: access.can("inventory.adjust", ["inventory", "admin", "superadmin"]),
    costs: access.can("costs.view", ["admin", "superadmin"]),
  };
  const links = BULK_LINKS.filter((link) => link.capability.some((c) => access.can(c, ["admin", "superadmin"])));

  return (
    <AdminShell user={user}>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Productos"
          title="Registrar artículo"
          description="Equipos nuevos, seminuevos, accesorios y repuestos, o lo que vende un proveedor bajo pedido: todo desde aquí, en cuatro pasos."
        />
        {/* Sin `inventory.adjust` no hay sucursales que elegir: no es un error que mostrar. */}
        {scope.error && can.adjust ? <ErrorBox message={scope.error} /> : null}
        {scope.loading ? <Spinner label="Cargando sucursales…" /> : (
          <ArticleRegistrationWizard
            access={can}
            companyId={access.dashboard?.company?.id ?? null}
            branches={(scope.access?.results ?? []).map((b) => ({ id: b.id, name: b.name }))}
            returnToReceive={fromReceive}
          />
        )}
        {links.length ? (
          <section className="rounded-xl border border-bd-border bg-surface p-5">
            <h2 className="text-sm font-semibold text-foreground">Carga masiva</h2>
            <p className="mt-1 text-xs text-muted">Muchos artículos a la vez, desde Excel. Las plantillas de siempre siguen valiendo.</p>
            <ul className="mt-3 flex flex-wrap gap-2">
              {links.map((link) => (
                <li key={link.href}>
                  <Link href={link.href} className="inline-flex rounded-full border border-bd-border px-3 py-1.5 text-xs text-foreground transition hover:border-foreground/40">
                    {link.label}
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </div>
    </AdminShell>
  );
}

export default function RegisterArticlePage() {
  return (
    <AccessGuard capability="products.manage" legacyRoles={["admin", "superadmin"]}>
      {(access) => <RegisterContent user={access.user} access={access} />}
    </AccessGuard>
  );
}
