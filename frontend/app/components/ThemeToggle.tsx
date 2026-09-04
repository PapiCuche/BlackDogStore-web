"use client";

/**
 * M12E — Automático / Claro / Oscuro.
 *
 * TRES OPCIONES VISIBLES, no un interruptor de dos. Un botón que alterna
 * claro↔oscuro no puede expresar «sigue a mi sistema», y ésa es la opción por
 * defecto: quien tiene el portátil en automático espera que la web también.
 *
 * Los `<button>` son botones de verdad, no `<div onClick>`: el foco, el
 * teclado y el lector de pantalla vienen de serie y no hay que reimplementarlos.
 */

import { useEffect, useRef, useState } from "react";
import { THEME_LABELS, useTheme, type ThemeChoice } from "./ThemeProvider";

const ORDER: ThemeChoice[] = ["system", "light", "dark"];

function Icon({ choice, className }: { choice: ThemeChoice; className?: string }) {
  if (choice === "light") {
    return (
      <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
        <circle cx="12" cy="12" r="4" strokeWidth={1.8} />
        <path strokeLinecap="round" strokeWidth={1.8} d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </svg>
    );
  }
  if (choice === "dark") {
    return (
      <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z" />
      </svg>
    );
  }
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
      <rect x="2.5" y="4" width="19" height="13" rx="2" strokeWidth={1.8} />
      <path strokeLinecap="round" strokeWidth={1.8} d="M8 20h8" />
    </svg>
  );
}

export function ThemeToggle({ className = "" }: { className?: string }) {
  const { choice, setChoice } = useTheme();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onPointer(e: MouseEvent) {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={box} className={`relative ${className}`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Tema: ${THEME_LABELS[choice]}`}
        // 44px de lado: el mínimo táctil razonable. Un icono de 32 en móvil se
        // falla con el pulgar más veces de las que se acierta.
        className="flex h-11 w-11 items-center justify-center rounded-full border border-bd-border text-muted transition hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
      >
        <Icon choice={choice} className="h-5 w-5" />
      </button>

      {open ? (
        <div
          role="menu"
          aria-label="Tema"
          className="absolute right-0 z-50 mt-2 w-44 overflow-hidden rounded-xl border border-bd-border bg-surface shadow-2xl"
        >
          {ORDER.map((option) => (
            <button
              key={option}
              type="button"
              role="menuitemradio"
              aria-checked={choice === option}
              onClick={() => {
                setChoice(option);
                setOpen(false);
              }}
              className={`flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-sm transition hover:bg-surface-2 focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary ${
                choice === option ? "text-foreground" : "text-muted"
              }`}
            >
              <Icon choice={option} className="h-4 w-4 shrink-0" />
              {THEME_LABELS[option]}
              {choice === option ? (
                <span aria-hidden="true" className="ml-auto text-primary">
                  ✓
                </span>
              ) : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
