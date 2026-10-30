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
import { useEffect, useRef, useState } from "react";

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
  const activeHref = activeModuleHref(pathname, search, sidebarHrefs(sections));
  // Only on the screens themselves: below them (an edit form, a wizard, a record) the bar would sit above work
  // that is lost with one click, and those pages are reached from the list anyway.
  const onTheScreen = activeHref !== null && activeHref.split("?")[0] === pathname;
  const current = onTheScreen ? sectionTabsFor(sections, activeHref) : null;
  const strip = useRef<HTMLElement>(null);
  const [overflowing, setOverflowing] = useState(false);
  const activeTab = current?.tabs.find((tab) => tab.active)?.id;

  // On a narrow screen the strip scrolls inside itself: the current tab is brought into view and a fade says
  // there is more to the right.
  useEffect(() => {
    const node = strip.current;
    if (!node) return;
    node.querySelector<HTMLElement>('[aria-current="page"]')?.scrollIntoView?.({ inline: "nearest", block: "nearest" });
    const measure = () => setOverflowing(node.scrollWidth > node.clientWidth + 1 && node.scrollLeft + node.clientWidth < node.scrollWidth - 1);
    measure();
    node.addEventListener("scroll", measure, { passive: true });
    window.addEventListener("resize", measure);
    return () => { node.removeEventListener("scroll", measure); window.removeEventListener("resize", measure); };
  }, [activeTab, current?.id]);

  if (!current) return null;

  return (
    <nav
      ref={strip}
      aria-label={`Secciones de ${current.label}`}
      className="-mx-1 mb-5 overflow-x-auto px-1"
      style={overflowing ? { maskImage: "linear-gradient(to right, black calc(100% - 28px), transparent)", WebkitMaskImage: "linear-gradient(to right, black calc(100% - 28px), transparent)" } : undefined}
    >
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
