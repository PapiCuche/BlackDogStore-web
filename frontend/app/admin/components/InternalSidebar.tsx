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
import { ThemeToggle } from "../../components/ThemeToggle";
import { BrandLogo } from "../../components/BrandLogo";
import { useEffect, useId, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { IconChevronDown, IconClose, IconDashboard } from "./icons";
import {
  activeModuleHref,
  sidebarHrefs,
  sidebarSections,
  type ModuleAccessContext,
  type SidebarItem,
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

type EntryProps = {
  item: SidebarItem;
  activeHref: string | null;
  /** Set by hand since the last navigation; undefined follows the active page. */
  open: boolean | undefined;
  onToggle: (next: boolean) => void;
  onVisit: (href: string) => void;
  linkClass: (active: boolean, inside?: boolean) => string;
};

/**
 * One destination. With children it is a link (or, with no page of its own, a
 * button) plus a disclosure; the list is a real `<ul>` so a screen reader
 * announces how many places it holds.
 */
function SidebarEntry({ item, activeHref, open, onToggle, onVisit, linkClass }: EntryProps) {
  const ownActive = item.href !== null && item.href === activeHref;
  const childActive = item.children.some((child) => child.href === activeHref);
  const inside = ownActive || childActive;
  const expanded = open ?? inside;
  // One id per rendered entry: the rail and the drawer both render every destination.
  const listId = useId();
  // Inside through a child: the entry stays legible without taking the "you are here" pill.
  const rowClass = linkClass(ownActive, childActive);

  if (!item.children.length && item.href) {
    return (
      <li>
        <Link
          href={item.href}
          onClick={() => onVisit(item.href!)}
          aria-current={ownActive ? "page" : undefined}
          className={`${rowClass} pl-8`}
        >
          {item.label}
        </Link>
      </li>
    );
  }

  return (
    <li>
      <div className="flex items-stretch gap-0.5">
        {item.href ? (
          <Link
            href={item.href}
            onClick={() => onVisit(item.href!)}
            aria-current={ownActive ? "page" : undefined}
            className={`${rowClass} min-w-0 flex-1 pl-8`}
          >
            {item.label}
          </Link>
        ) : (
          <button
            type="button"
            onClick={() => onToggle(!expanded)}
            aria-expanded={expanded}
            aria-controls={listId}
            className={`${rowClass} min-h-[44px] min-w-0 flex-1 pl-8 text-left`}
          >
            {item.label}
          </button>
        )}
        {item.href ? (
          <button
            type="button"
            onClick={() => onToggle(!expanded)}
            aria-expanded={expanded}
            aria-controls={listId}
            aria-label={`Opciones de ${item.label}`}
            className="flex min-h-[44px] w-11 shrink-0 items-center justify-center rounded-lg text-muted transition hover:bg-surface hover:text-foreground"
          >
            <IconChevronDown className={`h-4 w-4 transition-transform ${expanded ? "rotate-180" : ""}`} />
          </button>
        ) : (
          <span aria-hidden="true" className="flex w-11 shrink-0 items-center justify-center text-muted">
            <IconChevronDown className={`h-4 w-4 transition-transform ${expanded ? "rotate-180" : ""}`} />
          </span>
        )}
      </div>
      {expanded ? (
        <ul id={listId} className="mb-1 mt-0.5 space-y-0.5">
          {item.children.map((child) => {
            const active = child.href === activeHref;
            return (
              <li key={child.id}>
                {child.caption ? (
                  <p className="mb-0.5 mt-2 pl-12 text-[10px] font-semibold uppercase tracking-[0.15em] text-muted">
                    {child.caption}
                  </p>
                ) : null}
                <Link
                  href={child.href}
                  onClick={() => onVisit(child.href)}
                  aria-current={active ? "page" : undefined}
                  className={`${linkClass(active)} pl-12`}
                >
                  {child.label}
                </Link>
              </li>
            );
          })}
        </ul>
      ) : null}
    </li>
  );
}

export function InternalSidebarContent({
  access,
  companyName,
  onNavigate,
  onClose,
}: Props) {
  const pathname = usePathname();
  const sections = sidebarSections(access);
  // COMMERCE-ADMIN-UX-01: some entries are a screen opened with a filter. The
  // query is read from the address after each navigation (no `useSearchParams`:
  // that would need a Suspense boundary around every admin page).
  const [search, setSearch] = useState("");
  useEffect(() => {
    const read = () => setSearch(typeof window === "undefined" ? "" : window.location.search);
    read();
    window.addEventListener("popstate", read);
    return () => window.removeEventListener("popstate", read);
  }, [pathname]);
  const activeHref = activeModuleHref(pathname, search, sidebarHrefs(sections));
  // NAV-UX-01: a destination with children opens while the person is inside it;
  // opening or closing it by hand lasts until the next navigation.
  const pageKey = `${pathname}${search}`;
  const [hand, setHand] = useState<{ page: string; open: Record<string, boolean> }>({ page: pageKey, open: {} });
  // What was opened by hand lasts until the next navigation: coming back to a page does not bring it back.
  if (hand.page !== pageKey) setHand({ page: pageKey, open: {} });
  const openByHand = hand.page === pageKey ? hand.open : {};

  const linkClass = (active: boolean, inside = false) =>
    `flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-sm transition ${
      active
        ? "bg-foreground font-semibold text-background"
        : inside
          ? "font-semibold text-foreground hover:bg-surface"
          : "text-muted hover:bg-surface hover:text-foreground"
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

        {sections.map(({ id, label, icon: SectionIcon, items }) => (
          <div key={id} className="mt-6">
            <p className="mb-1.5 flex items-center gap-2 px-3 text-[10px] font-semibold uppercase tracking-[0.2em] text-muted">
              <SectionIcon className="h-3.5 w-3.5" />
              {label}
            </p>
            <ul className="space-y-0.5">
              {items.map((item) => (
                <SidebarEntry
                  key={item.id}
                  item={item}
                  activeHref={activeHref}
                  open={openByHand[item.id]}
                  onToggle={(next) =>
                    setHand({ page: pageKey, open: { ...openByHand, [item.id]: next } })}
                  linkClass={linkClass}
                  onVisit={(href) => {
                    // Same screen, another filter: the path does not change, the query does.
                    const query = href.split("?")[1];
                    setSearch(query ? `?${query}` : "");
                    onNavigate?.();
                  }}
                />
              ))}
            </ul>
          </div>
        ))}
      </nav>

      {onClose ? <div className="flex items-center justify-between border-t border-bd-border px-4 py-3"><span className="text-sm text-muted">Apariencia</span><ThemeToggle /></div> : null}

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
    <aside className="hidden w-[260px] shrink-0 border-r border-bd-border bg-surface lg:block">
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
        data-no-press
        className="v3-backdrop absolute inset-0 h-full w-full bg-background/70"
      />
      <div
        ref={panelRef}
        tabIndex={-1}
        className="v3-drawer absolute left-0 top-0 h-full w-[280px] max-w-[85vw] border-r border-bd-border bg-background outline-none"
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
