"use client";

/**
 * Internal control sidebar — Phase 2A.2.
 *
 * Renders only modules that are implemented, routable and reachable by the
 * caller (see internal-modules.ts). A module that does not exist never becomes a
 * link here; the honest roadmap lives on the dashboard instead.
 *
 * This is navigation, not authorisation. Every route it points at enforces its
 * own permissions, and every endpoint behind them re-checks server-side.
 */

import Link from "next/link";
import { BrandLogo } from "../../components/BrandLogo";
import { useEffect, useRef } from "react";
import { usePathname } from "next/navigation";
import { IconClose, IconDashboard } from "./icons";
import {
  navigableGroups,
  type ModuleAccessContext,
} from "../lib/internal-modules";

type Props = {
  access: ModuleAccessContext;
  /** The company being operated. Phase 3: the sidebar names IT, not a constant. */
  companyName?: string | null;
  /** Mobile drawer only: closes the panel after navigating. */
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
    `flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition ${
      active
        ? "bg-foreground/[0.08] font-medium text-foreground"
        : "text-muted hover:bg-foreground/[0.04] hover:text-foreground"
    }`;

  return (
    <div className="flex h-full flex-col">
      {/*
        LA MARCA VUELVE, EN SU SITIO.

        El panel llevaba la cabecera del escaparate encima, y al retirarla se
        quedó sin ninguna identidad: una consola gris que podría ser de
        cualquiera. El isotipo aquí ocupa 28 px, no compite con nada y sale del
        branding del TENANT — así que en otra empresa aparece la suya, no ésta.
      */}
      <div className="flex items-center justify-between border-b border-bd-border px-5 py-4">
        <div className="flex min-w-0 items-center gap-3">
          <BrandLogo
            placement="compact"
            surface="theme"
            className="h-7 w-7 shrink-0 object-contain"
            wordmarkClassName="sr-only"
          />
          <div className="min-w-0">
            <p className="text-[10px] font-semibold uppercase tracking-[0.25em] text-muted">
              Control interno
            </p>
            {companyName ? (
              <p className="mt-0.5 truncate text-sm font-semibold text-foreground">{companyName}</p>
            ) : null}
          </div>
        </div>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label="Cerrar menú"
            className="rounded-lg p-1.5 text-muted transition hover:bg-surface-2 hover:text-foreground lg:hidden"
          >
            <IconClose />
          </button>
        )}
      </div>

      <nav
        aria-label="Módulos del control interno"
        className="flex-1 overflow-y-auto px-3 py-4"
      >
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
              <p className="mb-1.5 flex items-center gap-2 px-3 text-[10px] font-semibold uppercase tracking-[0.2em] text-muted">
                <GroupIcon className="h-3.5 w-3.5" />
                {group.label}
              </p>
              <div className="space-y-0.5">
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

      {/* El enlace de vuelta a la tienda vive en la barra superior. Estaba
          también aquí abajo: dos caminos al mismo sitio, y el de abajo quedaba
          fuera de vista cuando la lista de módulos crece. */}
    </div>
  );
}

/** Desktop: sticky rail. Hidden below lg, where the drawer takes over. */
export function InternalSidebar({
  access,
  companyName,
}: {
  access: ModuleAccessContext;
  companyName?: string | null;
}) {
  return (
    <aside className="hidden w-[260px] shrink-0 border-r border-bd-border bg-background lg:block">
      <div className="sticky top-0 h-screen">
        <InternalSidebarContent access={access} companyName={companyName} />
      </div>
    </aside>
  );
}

/** What the topbar button points at with `aria-controls` while the drawer is open. */
export const MOBILE_SIDEBAR_ID = "internal-mobile-navigation";

/** Mobile: drawer over a dimmed backdrop. */
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
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;

    const panel = panelRef.current;
    const previousFocus = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panel?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }

      if (event.key !== "Tab" || !panel) return;
      const focusable = Array.from(
        panel.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((element) => !element.hasAttribute("hidden"));

      if (focusable.length === 0) {
        event.preventDefault();
        panel.focus();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;

      if (event.shiftKey && (active === first || active === panel)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div id={MOBILE_SIDEBAR_ID} className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true" aria-label="Navegación del control interno">
      <button
        type="button"
        aria-label="Cerrar navegación al hacer clic fuera"
        onClick={onClose}
        tabIndex={-1}
        className="absolute inset-0 h-full w-full bg-black/70"
      />
      <div
        ref={panelRef}
        tabIndex={-1}
        className="absolute left-0 top-0 h-full w-[280px] max-w-[85vw] border-r border-bd-border bg-background outline-none"
      >
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
