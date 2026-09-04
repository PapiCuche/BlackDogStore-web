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
  customer: "border-bd-border text-muted",
  sales: "border-bd-border text-foreground/85",
  inventory: "border-bd-border text-foreground/85",
  technician: "border-bd-border text-foreground/85",
  admin: "border-bd-border text-foreground",
  superadmin: "border-bd-border text-foreground",
};

export function UsersTable({ users, currentUser, onRoleChange }: Props) {
  if (users.length === 0) {
    return (
      <div className="rounded-xl border border-bd-border bg-surface py-12 text-center text-muted">
        No se encontraron usuarios.
      </div>
    );
  }

  const canChangeRoles = isSuperAdmin(currentUser);

  return (
    <div className="overflow-x-auto rounded-xl border border-bd-border">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-bd-border bg-surface text-left text-xs font-semibold uppercase tracking-wider text-muted">
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
              className={`border-b border-bd-border transition hover:bg-surface ${
                i % 2 === 0 ? "" : "bg-surface"
              }`}
            >
              <td className="px-4 py-3 text-muted">{user.id}</td>
              <td className="px-4 py-3 font-medium text-foreground">
                {user.username}
                {user.id === currentUser.id && (
                  <span className="ml-2 text-[10px] text-muted">(tú)</span>
                )}
              </td>
              <td className="px-4 py-3 text-muted">{user.email || "—"}</td>
              <td className="px-4 py-3">
                <span
                  className={`inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                    user.is_active
                      ? "bg-surface text-foreground/85"
                      : "bg-red-500/10 text-red-400"
                  }`}
                >
                  {user.is_active ? "Activo" : "Inactivo"}
                </span>
              </td>
              <td className="px-4 py-3">
                <span
                  className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${
                    ROLE_BADGE[user.role] ?? "border-bd-border text-muted"
                  }`}
                >
                  {roleLabel(user.role)}
                </span>
              </td>
              <td className="px-4 py-3 text-xs text-muted">
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
