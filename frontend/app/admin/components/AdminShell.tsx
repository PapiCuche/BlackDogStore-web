"use client";

import Link from "next/link";
import { AdminNav } from "./AdminNav";
import { roleLabel, type AuthUser } from "../../lib/auth";

type Props = {
  user: AuthUser;
  children: React.ReactNode;
};

export function AdminShell({ user, children }: Props) {
  return (
    <div className="min-h-[calc(100vh-64px)] bg-zinc-950">
      {/* Admin identity bar */}
      <div className="border-b border-white/[0.06] bg-[#0a0a0a]">
        <div className="mx-auto max-w-7xl flex items-center justify-between px-6 py-3">
          <div className="flex items-center gap-3">
            <div className="h-7 w-[2px] rounded-full bg-white/20" />
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.25em] text-zinc-500">
                Panel administrativo
              </p>
              <p className="text-sm text-zinc-300">
                {user.first_name || user.username}
                <span className="ml-2 rounded border border-white/10 px-1.5 py-0.5 text-[10px] font-medium text-zinc-400">
                  {roleLabel(user.role)}
                </span>
              </p>
            </div>
          </div>
          <Link
            href="/"
            className="text-xs text-zinc-500 transition hover:text-white"
          >
            ← Volver a la tienda
          </Link>
        </div>
      </div>

      {/* Nav tabs */}
      <div className="border-b border-white/[0.04] bg-[#080808]">
        <div className="mx-auto max-w-7xl px-6">
          <AdminNav />
        </div>
      </div>

      {/* Page content */}
      <div className="mx-auto max-w-7xl px-6 py-8">
        {children}
      </div>
    </div>
  );
}
