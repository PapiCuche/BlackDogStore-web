"use client";

/**
 * Inline SVG icons for the internal control surface.
 *
 * Deliberately not a dependency: the project ships with only next/react/react-dom,
 * and pulling a full icon package for a dozen glyphs would be a poor trade. These
 * are 24×24 stroke icons on a shared grid so they sit consistently in the sidebar.
 *
 * Every icon is decorative — it always accompanies a text label, so it carries
 * aria-hidden and the label does the talking for assistive tech.
 */

type IconProps = { className?: string };

function Svg({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className ?? "h-[18px] w-[18px]"}
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

export function IconDashboard(p: IconProps) {
  return (
    <Svg {...p}>
      <rect x="3" y="3" width="7" height="9" rx="1.5" />
      <rect x="14" y="3" width="7" height="5" rx="1.5" />
      <rect x="14" y="12" width="7" height="9" rx="1.5" />
      <rect x="3" y="16" width="7" height="5" rx="1.5" />
    </Svg>
  );
}

export function IconSales(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M3 4h2l2.4 11.2a2 2 0 0 0 2 1.6h7.7a2 2 0 0 0 2-1.5L21 8H6" />
      <circle cx="10" cy="20" r="1.2" />
      <circle cx="18" cy="20" r="1.2" />
    </Svg>
  );
}

export function IconCash(p: IconProps) {
  return (
    <Svg {...p}>
      <rect x="2.5" y="6" width="19" height="12" rx="2" />
      <circle cx="12" cy="12" r="2.5" />
      <path d="M6 10v4M18 10v4" />
    </Svg>
  );
}

export function IconPurchases(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M4 7h16l-1 12a2 2 0 0 1-2 1.8H7A2 2 0 0 1 5 19z" />
      <path d="M9 7V5.5a3 3 0 0 1 6 0V7" />
    </Svg>
  );
}

export function IconCustomers(p: IconProps) {
  return (
    <Svg {...p}>
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3 20a6 6 0 0 1 12 0" />
      <path d="M16 11.2A3 3 0 0 0 16 5.3" />
      <path d="M18 20a5.5 5.5 0 0 0-3-4.9" />
    </Svg>
  );
}

export function IconProducts(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M12 2.8 20.5 7v10L12 21.2 3.5 17V7z" />
      <path d="M3.5 7 12 11.4 20.5 7M12 11.4V21.2" />
    </Svg>
  );
}

export function IconInventory(p: IconProps) {
  return (
    <Svg {...p}>
      <rect x="3" y="9" width="18" height="12" rx="1.6" />
      <path d="M3 9l2.2-5.2a1.5 1.5 0 0 1 1.4-.9h10.8a1.5 1.5 0 0 1 1.4.9L21 9" />
      <path d="M10 14h4" />
    </Svg>
  );
}

export function IconService(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M14.5 5.2a4.2 4.2 0 0 0 5.4 5.6l-8 8a2.6 2.6 0 0 1-3.7-3.7z" />
      <path d="M5.6 18.4h.01" />
    </Svg>
  );
}

export function IconReports(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M4 20V4" />
      <path d="M4 20h16" />
      <path d="M8 16V11M12.5 16V7M17 16v-3.5" />
    </Svg>
  );
}

export function IconAdministration(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M4 21V9.5L12 4l8 5.5V21" />
      <path d="M9.5 21v-6h5v6" />
    </Svg>
  );
}

export function IconAudit(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M6 3h8l4 4v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" />
      <path d="M14 3v4h4" />
      <path d="M8.5 13h7M8.5 16.5h4.5" />
    </Svg>
  );
}

export function IconPeople(p: IconProps) {
  return (
    <Svg {...p}>
      <circle cx="12" cy="8" r="3.4" />
      <path d="M5 20a7 7 0 0 1 14 0" />
    </Svg>
  );
}

export function IconMenu(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M4 7h16M4 12h16M4 17h16" />
    </Svg>
  );
}

export function IconClose(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M6 6l12 12M18 6L6 18" />
    </Svg>
  );
}

export function IconChevronDown(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M6 9.5l6 6 6-6" />
    </Svg>
  );
}

export function IconStore(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M4 10v9a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-9" />
      <path d="M3 10l1.6-5.2A1.4 1.4 0 0 1 6 4h12a1.4 1.4 0 0 1 1.4 0.8L21 10a3 3 0 0 1-6 0 3 3 0 0 1-6 0 3 3 0 0 1-6 0z" />
    </Svg>
  );
}

export function IconBranch(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M12 21s6.5-6 6.5-11a6.5 6.5 0 1 0-13 0C5.5 15 12 21 12 21z" />
      <circle cx="12" cy="10" r="2.4" />
    </Svg>
  );
}

export function IconShield(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M12 3l7.5 3v5.6c0 4.4-3.1 8.2-7.5 9.4-4.4-1.2-7.5-5-7.5-9.4V6z" />
      <path d="M9.3 12.2l1.9 1.9 3.6-3.8" />
    </Svg>
  );
}

export function IconAlert(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M12 3.8 21 19.5H3z" />
      <path d="M12 10v4M12 17h.01" />
    </Svg>
  );
}

export type IconComponent = (props: IconProps) => React.ReactElement;
