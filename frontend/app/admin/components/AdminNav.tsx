"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

type NavItem = { href: string; label: string; exact?: boolean; roles?: string[] };

const NAV_ITEMS: NavItem[] = [
  { href: "/admin", label: "Dashboard", exact: true },
  { href: "/admin/users", label: "Usuarios", roles: ["admin", "superadmin"] },
  { href: "/admin/products", label: "Productos", roles: ["inventory", "sales", "admin", "superadmin"] },
  { href: "/admin/orders", label: "Órdenes", roles: ["inventory", "sales", "admin", "superadmin"] },
  { href: "/admin/audit-logs", label: "Auditoría", roles: ["admin", "superadmin"] },
];

type Props = { role?: string };

export function AdminNav({ role }: Props) {
  const pathname = usePathname();

  const visible = NAV_ITEMS.filter(
    (item) => !item.roles || (role && item.roles.includes(role)),
  );

  return (
    <nav className="flex gap-1 py-2 text-sm font-medium overflow-x-auto">
      {visible.map((item) => {
        const isActive = item.exact
          ? pathname === item.href
          : pathname.startsWith(item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            className={`whitespace-nowrap rounded-lg px-3.5 py-2 transition ${
              isActive
                ? "bg-white/10 text-white"
                : "text-zinc-400 hover:bg-white/5 hover:text-white"
            }`}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
