"use client";

/**
 * Notas de venta — SALES-NOTE-CONTROL-01.
 *
 * Documento interno de control, sin validez tributaria. Se emite desde el
 * pedido o el POS; aquí se consulta, se anula (sólo el usuario Master) y se
 * anotan observaciones internas.
 */
import { AccessGuard } from "../../components/AccessGuard";
import { AdminShell } from "../../components/AdminShell";
import { PageHeader } from "../../components/internal-ui";
import { SalesNotesScreen } from "../../components/sales-notes/SalesNotesScreen";
import { SALES_NOTE_NOTICE } from "../../../lib/inventory";

export default function SalesNotesPage() {
  // Misma autoridad que la nota en el detalle del pedido: `sales.notes.manage`,
  // o en el puente legacy los roles que pueden emitirla.
  return (
    <AccessGuard capability="sales.notes.manage" legacyRoles={["sales", "admin", "superadmin"]}>
      {(access) => (
        <AdminShell user={access.user}>
          <div className="space-y-6">
            <PageHeader
              eyebrow="Ventas"
              title="Notas de venta"
              description={`${SALES_NOTE_NOTICE} Una nota anulada se conserva con su número; anularla no cancela el pedido ni devuelve dinero.`}
            />
            <SalesNotesScreen />
          </div>
        </AdminShell>
      )}
    </AccessGuard>
  );
}
