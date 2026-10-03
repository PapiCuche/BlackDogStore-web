"use client";

import { useEffect, useId, useRef, useState, type ComponentProps } from "react";
import { ProductCard } from "./ProductCard";

type Product = ComponentProps<typeof ProductCard>;

const GAP = 16;
/** Píxeles por milisegundo: un avance que acompaña, no que arrastra. */
const DRIFT = 0.018;

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined"
    && typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * Los productos REALES del catálogo, en una fila que se recorre.
 *
 * Scroll nativo, no un carrusel reescrito: conserva la inercia del dedo, los
 * enlaces de cada tarjeta y el foco del navegador. Encima lleva tres cosas —
 * flechas, teclado y un avance lento— y cualquiera de ellas cede en cuanto la
 * persona toca, enfoca o desplaza.
 *
 * No supone nada del producto: sin imagen, la tarjeta pinta su propio
 * respaldo. Con «reducir movimiento» no hay avance ni desplazamiento animado.
 */
export function ProductCarousel({ products }: { products: Product[] }) {
  const track = useRef<HTMLDivElement>(null);
  const id = useId();
  const several = products.length > 1;
  const [playing, setPlaying] = useState(true);
  const [position, setPosition] = useState({ start: true, end: true, index: 1 });
  const hovered = useRef(false);
  const direction = useRef(1);

  function takeControl() {
    setPlaying(false);
    const node = track.current;
    if (node) node.scrollTo({ left: node.scrollLeft, behavior: "instant" });
  }

  // Avance automático: sólo con varios productos, a la vista, sin cursor
  // encima, con la pestaña activa y sin «reducir movimiento».
  useEffect(() => {
    const node = track.current;
    if (!playing || !node || !several || prefersReducedMotion()) return;
    if (!("IntersectionObserver" in window)) return;

    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    let visible = false;
    const observer = new IntersectionObserver((entries) => { visible = entries[0].isIntersecting; }, { threshold: 0.5 });
    observer.observe(node);
    // El ajuste a tarjeta pelearía con un desplazamiento continuo.
    node.style.scrollSnapType = "none";
    let offset = node.scrollLeft;
    let previous = 0;
    let frame = 0;
    const advance = (time: number) => {
      const elapsed = previous ? Math.min(time - previous, 50) : 0;
      previous = time;
      if (visible && !hovered.current && !document.hidden && !preference.matches) {
        const max = node.scrollWidth - node.clientWidth;
        if (max > 2) {
          if (offset >= max) direction.current = -1;
          else if (offset <= 0) direction.current = 1;
          offset = Math.max(0, Math.min(max, offset + direction.current * elapsed * DRIFT));
          node.scrollTo({ left: offset, behavior: "instant" });
        }
      }
      frame = requestAnimationFrame(advance);
    };
    frame = requestAnimationFrame(advance);
    return () => {
      cancelAnimationFrame(frame);
      node.style.scrollSnapType = "";
      observer.disconnect();
    };
  }, [playing, several]);

  // Dónde está: para deshabilitar la flecha del extremo y contar la posición.
  useEffect(() => {
    const node = track.current;
    if (!node) return;
    const update = () => {
      const width = node.firstElementChild?.getBoundingClientRect().width || 1;
      const next = {
        start: node.scrollLeft < 2,
        end: node.scrollLeft >= node.scrollWidth - node.clientWidth - 2,
        index: Math.min(products.length, Math.round(node.scrollLeft / (width + GAP)) + 1),
      };
      setPosition((current) => (
        current.start === next.start && current.end === next.end && current.index === next.index
          ? current : next
      ));
    };
    update();
    node.addEventListener("scroll", update, { passive: true });
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(update) : null;
    observer?.observe(node);
    return () => {
      observer?.disconnect();
      node.removeEventListener("scroll", update);
    };
  }, [products.length]);

  function navigate(step: number, instant = false) {
    const node = track.current;
    if (!node) return;
    const width = (node.firstElementChild?.getBoundingClientRect().width || node.clientWidth) + GAP;
    node.scrollTo({
      left: node.scrollLeft + step * width,
      behavior: instant || prefersReducedMotion() ? "instant" : "smooth",
    });
  }

  function jump(to: "start" | "end") {
    const node = track.current;
    if (!node) return;
    node.scrollTo({ left: to === "start" ? 0 : node.scrollWidth, behavior: "instant" });
  }

  if (products.length === 0) return null;

  const fromControl = (target: EventTarget) => Boolean((target as HTMLElement).closest?.(".v3-autoplay-control"));

  return (
    <section
      className="v3-carousel mt-8"
      aria-label="Productos destacados"
      aria-roledescription="carrusel"
      onPointerEnter={() => { hovered.current = true; }}
      onPointerLeave={() => { hovered.current = false; }}
      onPointerDownCapture={(event) => { if (!fromControl(event.target)) takeControl(); }}
      onWheelCapture={takeControl}
      onFocusCapture={(event) => { if (!fromControl(event.target)) takeControl(); }}
    >
      <div
        ref={track}
        id={id}
        className="v3-carousel-track"
        tabIndex={0}
        role="group"
        aria-label="Lista de productos; usa las flechas para explorar"
        onKeyDown={(event) => {
          // Las flechas dentro de una tarjeta son de la tarjeta.
          if (event.target !== event.currentTarget) return;
          if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
            event.preventDefault();
            navigate(event.key === "ArrowRight" ? 1 : -1, true);
          } else if (event.key === "Home" || event.key === "End") {
            event.preventDefault();
            jump(event.key === "Home" ? "start" : "end");
          }
        }}
      >
        {products.map((product) => (
          <div key={product.id} className="v3-carousel-item">
            <ProductCard {...product} />
          </div>
        ))}
      </div>

      {several ? (
        <div className="mt-5 flex items-center justify-between gap-4">
          <p className="text-xs text-muted">
            Desliza para explorar <span aria-hidden="true">↔</span>
          </p>
          <div className="flex items-center gap-3">
            <button
              type="button"
              className="v3-carousel-control v3-autoplay-control"
              aria-label={playing ? "Pausar avance automático" : "Reanudar avance automático"}
              aria-pressed={!playing}
              onClick={() => setPlaying(!playing)}
            >
              <span aria-hidden="true">{playing ? "Ⅱ" : "▷"}</span>
            </button>
            <span className="text-xs tabular-nums text-muted" aria-live={playing ? "off" : "polite"} aria-atomic="true">
              {String(position.index).padStart(2, "0")} / {String(products.length).padStart(2, "0")}
            </span>
            <button
              type="button"
              className="v3-carousel-control"
              aria-label="Productos anteriores"
              aria-controls={id}
              disabled={position.start}
              onClick={(event) => navigate(-1, event.detail === 0)}
            >
              <span aria-hidden="true">←</span>
            </button>
            <button
              type="button"
              className="v3-carousel-control"
              aria-label="Productos siguientes"
              aria-controls={id}
              disabled={position.end}
              onClick={(event) => navigate(1, event.detail === 0)}
            >
              <span aria-hidden="true">→</span>
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
