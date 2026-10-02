"use client";

import { useEffect, useRef, type ReactNode } from "react";

/**
 * Entradas al llegar a pantalla, para los bloques marcados con
 * `data-motion-reveal`.
 *
 * EL CONTENIDO ESTÁ SIEMPRE. No hay estado inicial oculto: sin JavaScript, sin
 * `IntersectionObserver` o con «reducir movimiento», la página se ve entera y
 * quieta. La animación es un adorno que corre UNA vez y no deja nada puesto.
 */
export function StorefrontMotion({ children }: { children: ReactNode }) {
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = root.current;
    if (!node || typeof window.matchMedia !== "function" || !("IntersectionObserver" in window)) return;
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (preference.matches) return;

    const animations = new Set<Animation>();
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        observer.unobserve(entry.target);
        if (typeof entry.target.animate !== "function") continue;
        const animation = entry.target.animate(
          [{ opacity: 0, transform: "translateY(18px)" }, { opacity: 1, transform: "translateY(0)" }],
          { duration: 520, easing: "cubic-bezier(.23,1,.32,1)" },
        );
        animations.add(animation);
        animation.onfinish = () => animations.delete(animation);
      }
    }, { threshold: 0.12 });
    node.querySelectorAll("[data-motion-reveal]").forEach((target) => observer.observe(target));

    // Cambiar la preferencia a mitad de página detiene lo que esté corriendo.
    const stop = () => {
      if (!preference.matches) return;
      observer.disconnect();
      animations.forEach((animation) => animation.cancel());
    };
    preference.addEventListener("change", stop);
    return () => {
      observer.disconnect();
      preference.removeEventListener("change", stop);
      animations.forEach((animation) => animation.cancel());
    };
  }, []);

  return <div ref={root}>{children}</div>;
}
