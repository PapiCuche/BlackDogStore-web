"use client";

/**
 * Productos › Imágenes por reemplazar — P2 · F4 «Imágenes propias».
 *
 * `products.view` para llegar, como el endpoint. Subir en lote exige `products.manage`: sólo con él se enlaza.
 * IMG-GATE-01: debajo, «Salud de las imágenes» (sólo lectura, al pulsar «Revisar ahora»).
 */

import { AccessGuard } from "../../components/AccessGuard";
import { AdminShell } from "../../components/AdminShell";
import { ImageHealthPanel } from "../../components/ImageHealthPanel";
import { ImageSourcesReport } from "../../components/ImageSourcesReport";
import { PageHeader } from "../../components/internal-ui";
import type { InternalAccess } from "../../lib/internal-access";
import type { AuthUser } from "../../../lib/auth";

function ImagesContent({ user, access }: { user: AuthUser; access: InternalAccess }) {
  return (
    <AdminShell user={user}>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Productos"
          title="Imágenes por reemplazar"
          description="Modelos y productos que muestran imágenes que la tienda no subió, como fotos enlazadas del sitio de un fabricante. Reemplázalas por fotos propias."
        />
        <ImageSourcesReport
          canManage={access.can("products.manage", ["admin", "superadmin"])}
          companyId={access.dashboard?.company?.id ?? null}
        />
        {/* IMG-HEALTH (IMG-GATE-01): read-only, on demand. */}
        <ImageHealthPanel companyId={access.dashboard?.company?.id ?? null} />
      </div>
    </AdminShell>
  );
}

export default function CatalogImagesPage() {
  return (
    <AccessGuard capability="products.view" legacyRoles={["inventory", "sales", "admin", "superadmin"]}>
      {(access) => <ImagesContent user={access.user} access={access} />}
    </AccessGuard>
  );
}
