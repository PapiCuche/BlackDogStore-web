"use client";

import { AccessGuard } from "../components/AccessGuard";
import { AdminShell } from "../components/AdminShell";
import { ProductsManager } from "../components/ProductsManager";

export default function AdminProductsPage() {
  return (
    <AccessGuard capability="products.view" legacyRoles={["inventory", "sales", "admin", "superadmin"]}>
      {(access) => (
        <AdminShell user={access.user}>
          <ProductsManager canManage={access.can("products.manage", ["admin", "superadmin"])} />
        </AdminShell>
      )}
    </AccessGuard>
  );
}
