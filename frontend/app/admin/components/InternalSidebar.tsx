"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { IconClose, IconDashboard } from "./icons";
import {
  navigableGroups,
  type ModuleAccessContext,
} from "../lib/internal-modules";

type Props = {
  access: ModuleAccessContext;
  companyName?: string | null;
  onNavigate?: () => void;
  onClose?: () => void;
};

function isActive(pathname: string, href: string): boolean {
  if (href === "/admin") return pathname === "/admin";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function InternalSidebarContent({
  access,
  companyName,
  onNavigate,
  onClose,
}: Props) {
  const pathname = usePathname();
  const groups = navigableGroups(access);

  const linkClass = (active: boolean) =>
    `flex items-center gap-2.5 rounded-lg border px-3 py-2.5 text-sm transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
      active
        ? "border-bd-border bg-surface-elevated font-semibold text-foreground"
        : "border-transparent text-muted-foreground hover:bg-surface hover:text-foreground"
    }`;

  return (
    <div className="flex h-full flex-col">
      <div className="flex min-h-[72px] items-center justify-between border-b border-bd-border px-5 py-4">
        <div className="min-w-0">
          <p className="text-[9px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
            Control interno
          </p>
          {companyName ? (
            <p className="mt-1 truncate text-sm font-semibold text-foreground">{companyName}</p>
          ) : (
            <p className="mt-1 text-sm font-semibold text-foreground">Administración</p>
          )}
        </div>
        {onClose ? (
          <button
            type="button"
            onClick={onClose}
            aria-label="Cerrar menú"
            className="rounded-lg p-2 text-muted-foreground transition hover:bg-surface-elevated hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent lg:hidden"
          >
            <IconClose />
          </button>
        ) : null}
      </div>

      <nav aria-label="Módulos del control interno" className="flex-1 overflow-y-auto px-3 py-4">
        <Link
          href="/admin"
          onClick={onNavigate}
          aria-current={isActive(pathname, "/admin") ? "page" : undefined}
          className={linkClass(isActive(pathname, "/admin"))}
        >
          <IconDashboard className="h-[18px] w-[18px] shrink-0" />
          Dashboard
        </Link>

        {groups.map(({ group, modules }) => {
          const GroupIcon = group.icon;
          return (
            <div key={group.id} className="mt-6">
              <p className="mb-2 flex items-center gap-2 px-3 text-[9px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                <GroupIcon className="h-3.5 w-3.5" />
                {group.label}
              </p>
              <div className="space-y-1">
                {modules.map((module) => {
                  const active = isActive(pathname, module.href!);
                  return (
                    <Link
                      key={module.id}
                      href={module.href!}
                      onClick={onNavigate}
                      aria-current={active ? "page" : undefined}
                      className={`${linkClass(active)} pl-8`}
                    >
                      {module.label}
                    </Link>
                  );
                })}
              </div>
            </div>
          );
        })}
      </nav>

      <div className="border-t border-bd-border px-3 py-3">
        <Link
          href="/"
          onClick={onNavigate}
          className="flex items-center gap-2 rounded-lg border border-transparent px-3 py-2.5 text-sm text-muted-foreground transition hover:bg-surface hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          ← Volver a la tienda
        </Link>
      </div>
    </div>
  );
}

export function InternalSidebar({
  access,
  companyName,
}: {
  access: ModuleAccessContext;
  companyName?: string | null;
}) {
  return (
    <aside className="hidden w-[272px] shrink-0 border-r border-bd-border bg-background lg:block">
      <div className="sticky top-0 h-screen">
        <InternalSidebarContent access={access} companyName={companyName} />
      </div>
    </aside>
  );
}

export function MobileSidebar({
  access,
  companyName,
  open,
  onClose,
}: {
  access: ModuleAccessContext;
  companyName?: string | null;
  open: boolean;
  onClose: () => void;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 lg:hidden">
      <button
        type="button"
        aria-label="Cerrar menú"
        onClick={onClose}
        className="absolute inset-0 h-full w-full bg-black/70"
      />
      <div className="absolute left-0 top-0 h-full w-[288px] max-w-[88vw] border-r border-bd-border bg-background">
        <InternalSidebarContent
          access={access}
          companyName={companyName}
          onNavigate={onClose}
          onClose={onClose}
        />
      </div>
    </div>
  );
}
