"use client";

import { roleLabel, isSuperAdmin, type AuthUser } from "../../lib/auth";
import { RoleSelect } from "./RoleSelect";
import { formatAdminDate, type AdminUser } from "../../lib/admin";

type Props = {
  users: AdminUser[];
  currentUser: AuthUser;
  onRoleChange: (userId: number, newRole: string) => Promise<void>;
};

const ROLE_BADGE: Record<string, string> = {
  customer: "border-zinc-700 text-zinc-400",
  sales: "border-zinc-600 text-zinc-300",
  inventory: "border-zinc-600 text-zinc-300",
  technician: "border-zinc-600 text-zinc-300",
  admin: "border-white/30 text-white",
  superadmin: "border-white/50 text-white",
};

export function UsersTable({ users, currentUser, onRoleChange }: Props) {
  if (users.length === 0) {
    return (
      <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] py-12 text-center text-zinc-500">
        No se encontraron usuarios.
      </div>
    );
  }

  const canChangeRoles = isSuperAdmin(currentUser);

  return (
    <div className="overflow-x-auto rounded-xl border border-white/[0.06]">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-white/[0.06] bg-white/[0.02] text-left text-xs font-semibold uppercase tracking-wider text-zinc-500">
            <th className="px-4 py-3">ID</th>
            <th className="px-4 py-3">Usuario</th>
            <th className="px-4 py-3">Email</th>
            <th className="px-4 py-3">Estado</th>
            <th className="px-4 py-3">Rol</th>
            <th className="px-4 py-3">Registro</th>
            {canChangeRoles && <th className="px-4 py-3">Cambiar rol</th>}
          </tr>
        </thead>
        <tbody>
          {users.map((user, i) => (
            <tr
              key={user.id}
              className={`border-b border-white/[0.04] transition hover:bg-white/[0.02] ${
                i % 2 === 0 ? "" : "bg-white/[0.01]"
              }`}
            >
              <td className="px-4 py-3 text-zinc-500">{user.id}</td>
              <td className="px-4 py-3 font-medium text-zinc-200">
                {user.username}
                {user.id === currentUser.id && (
                  <span className="ml-2 text-[10px] text-zinc-600">(tú)</span>
                )}
              </td>
              <td className="px-4 py-3 text-zinc-400">{user.email || "—"}</td>
              <td className="px-4 py-3">
                <span
                  className={`inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                    user.is_active
                      ? "bg-white/5 text-zinc-300"
                      : "bg-red-500/10 text-red-400"
                  }`}
                >
                  {user.is_active ? "Activo" : "Inactivo"}
                </span>
              </td>
              <td className="px-4 py-3">
                <span
                  className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${
                    ROLE_BADGE[user.role] ?? "border-zinc-700 text-zinc-400"
                  }`}
                >
                  {roleLabel(user.role)}
                </span>
              </td>
              <td className="px-4 py-3 text-xs text-zinc-500">
                {formatAdminDate(user.date_joined)}
              </td>
              {canChangeRoles && (
                <td className="px-4 py-3">
                  <RoleSelect
                    userId={user.id}
                    currentRole={user.role}
                    isSelf={user.id === currentUser.id}
                    onRoleChange={onRoleChange}
                  />
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
