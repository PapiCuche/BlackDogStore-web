"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_ITEMS = [
  { href: "/admin", label: "Dashboard", exact: true },
  { href: "/admin/users", label: "Usuarios" },
  { href: "/admin/products", label: "Productos" },
  { href: "/admin/audit-logs", label: "Auditoría" },
];

export function AdminNav() {
  const pathname = usePathname();

  return (
    <nav className="flex gap-1 py-2 text-sm font-medium overflow-x-auto">
      {NAV_ITEMS.map((item) => {
        const isActive = item.exact ? pathname === item.href : pathname.startsWith(item.href);
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
