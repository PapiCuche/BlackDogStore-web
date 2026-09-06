"use client";

type Props = { isActive: boolean };

export function ProductStatusBadge({ isActive }: Props) {
  return (
    <span
      className={`inline-block px-2 py-0.5 rounded text-xs font-medium border ${
        isActive
          ? "border-bd-border text-foreground bg-surface"
          : "border-danger-border text-danger bg-danger-surface"
      }`}
    >
      {isActive ? "Activo" : "Inactivo"}
    </span>
  );
}
