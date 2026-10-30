"use client";

/**
 * NAV-UX-02 — the tab bar across the screens of one destination («Stock y equipos», «Proveedores», «Etiquetas»…).
 *
 * It repeats, inside the page, what the sidebar keeps collapsed, so that moving between related screens does not
 * mean going back to the menu. It is navigation, not authorisation: it lists what the sidebar lists (same access
 * rule) and every screen and endpoint still decides for itself. It scrolls inside its own strip, never the page.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";

import {
  activeModuleHref,
  sectionTabsFor,
  sidebarHrefs,
  sidebarSections,
  type ModuleAccessContext,
} from "../lib/internal-modules";
import { useLocationSearch } from "../lib/use-location-search";

export function SectionTabs({ access }: { access: ModuleAccessContext }) {
  const pathname = usePathname();
  const [search, setSearch] = useLocationSearch(pathname);
  const sections = sidebarSections(access);
  const current = sectionTabsFor(sections, activeModuleHref(pathname, search, sidebarHrefs(sections)));
  if (!current) return null;

  return (
    <nav aria-label={`Secciones de ${current.label}`} className="-mx-1 mb-5 overflow-x-auto px-1">
      <ul className="flex min-w-max gap-1 border-b border-bd-border">
        {current.tabs.map((tab) => (
          <li key={tab.id}>
            <Link
              href={tab.href}
              onClick={() => setSearch(tab.href.includes("?") ? `?${tab.href.split("?")[1]}` : "")}
              aria-current={tab.active ? "page" : undefined}
              className={`-mb-px flex min-h-11 items-center whitespace-nowrap border-b-2 px-3.5 text-sm transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] ${
                tab.active
                  ? "border-foreground font-semibold text-foreground"
                  : "border-transparent text-muted hover:border-bd-border hover:text-foreground"
              }`}
            >
              {tab.label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
