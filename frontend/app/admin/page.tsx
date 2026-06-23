"use client";

import Link from "next/link";
import { AdminGuard } from "./components/AdminGuard";
import { AdminShell } from "./components/AdminShell";
import type { AuthUser } from "../lib/auth";

function DashboardContent({ user }: { user: AuthUser }) {
  return (
    <AdminShell user={user}>
      <div className="space-y-8">
        <div>
          <h1 className="text-xl font-semibold text-white">Dashboard</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Resumen del panel administrativo.
          </p>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Link
            href="/admin/users"
            className="group rounded-xl border border-white/[0.06] bg-white/[0.02] p-6 transition hover:border-white/10 hover:bg-white/[0.04]"
          >
            <p className="text-xs font-semibold uppercase tracking-widest text-zinc-500">
              Usuarios
            </p>
            <p className="mt-2 text-lg font-semibold text-white">
              Gestión de usuarios
            </p>
            <p className="mt-1 text-sm text-zinc-500">
              Lista de usuarios registrados, búsqueda y asignación de roles.
            </p>
            <p className="mt-4 text-xs text-zinc-600 transition group-hover:text-zinc-400">
              Ver usuarios →
            </p>
          </Link>

          <Link
            href="/admin/audit-logs"
            className="group rounded-xl border border-white/[0.06] bg-white/[0.02] p-6 transition hover:border-white/10 hover:bg-white/[0.04]"
          >
            <p className="text-xs font-semibold uppercase tracking-widest text-zinc-500">
              Auditoría
            </p>
            <p className="mt-2 text-lg font-semibold text-white">
              Registro de acciones
            </p>
            <p className="mt-1 text-sm text-zinc-500">
              Historial de cambios de roles y acciones administrativas.
            </p>
            <p className="mt-4 text-xs text-zinc-600 transition group-hover:text-zinc-400">
              Ver registros →
            </p>
          </Link>
        </div>
      </div>
    </AdminShell>
  );
}

export default function AdminPage() {
  return <AdminGuard>{(user) => <DashboardContent user={user} />}</AdminGuard>;
}
