"use client";

type Props = { isActive: boolean };

export function ProductStatusBadge({ isActive }: Props) {
  return (
    <span
      className={`inline-block px-2 py-0.5 rounded text-xs font-medium border ${
        isActive
          ? "border-white/20 text-zinc-100 bg-white/5"
          : "border-red-900/40 text-red-400 bg-red-950/20"
      }`}
    >
      {isActive ? "Activo" : "Inactivo"}
    </span>
  );
}
