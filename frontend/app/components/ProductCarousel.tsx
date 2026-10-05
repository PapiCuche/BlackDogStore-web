"use client";

import { useEffect, useId, useRef, useState, type ComponentProps } from "react";
import { ProductCard } from "./ProductCard";

type Product = ComponentProps<typeof ProductCard>;

const GAP = 16;
/** Píxeles por milisegundo: un avance que acompaña, no que arrastra. */
const DRIFT = 0.018;
/** Lo que el ratón tiene que moverse para que un clic pase a ser un arrastre. */
const DRAG_THRESHOLD = 6;
/** Píxeles por milisegundo al soltar: por encima, es un impulso y avanza una tarjeta. */
const FLICK = 0.4;

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined"
    && typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * Los productos REALES del catálogo, en una fila que se recorre.
 *
 * Scroll nativo, no un carrusel reescrito: conserva la inercia del dedo, los
 * enlaces de cada tarjeta y el foco del navegador. Encima lleva cuatro cosas —
 * flechas, teclado, arrastre con ratón y un avance lento— y cualquiera de
 * ellas cede en cuanto la persona toca, enfoca o desplaza.
 *
 * LO QUE NO HACE, a propósito:
 *  · no retiene la rueda: ningún gesto lleva `preventDefault`, y la rueda
 *    vertical —que es de la página— ni siquiera detiene el avance;
 *  · no intercepta el dedo: con tacto manda el navegador, con su inercia;
 *  · no trabaja sin que se vea: fuera de pantalla no pide fotogramas;
 *  · no da saltos: al tomar el control se asienta en una tarjeta y sólo
 *    entonces vuelve el ajuste.
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
  const drag = useRef<{ id: number; x: number; left: number; time: number; lastX: number; active: boolean } | null>(null);
  const suppressClick = useRef(false);
  const [dragging, setDragging] = useState(false);
  /** Cancela la devolución pendiente del ajuste a tarjeta, si hay una. */
  const cancelSettle = useRef<(() => void) | null>(null);
  /** Cambia con cada intención nueva; un salto que vigila la suya se suelta. */
  const intent = useRef(0);

  /**
   * Salta a `left` y SE QUEDA AHÍ.
   *
   * Medido en Chromium: un `scrollTo` instantáneo dado a mitad de uno suave no
   * lo cancela. La fila llega al destino y la animación que seguía viva la
   * mueve lo que le faltaba, así que «Inicio» justo después de una flecha la
   * dejaba entre dos tarjetas. Ni asignar `scrollLeft` ni empezar otro
   * desplazamiento lo evitan; reafirmar el destino en los fotogramas
   * siguientes, sí. Se vigila hasta que la fila lleva tres fotogramas quieta
   * (con un tope), y se suelta en cuanto la persona pide otra cosa.
   */
  function jumpTo(node: HTMLElement, left: number) {
    const mine = ++intent.current;
    node.scrollTo({ left, behavior: "instant" });
    let still = 0;
    let budget = 45;
    const watch = () => {
      if (intent.current !== mine || !node.isConnected) return;
      if (Math.abs(node.scrollLeft - left) > 1) {
        node.scrollTo({ left, behavior: "instant" });
        still = 0;
      } else {
        still += 1;
      }
      budget -= 1;
      if (still < 3 && budget > 0) requestAnimationFrame(watch);
    };
    requestAnimationFrame(watch);
  }

  /**
   * Lleva la fila hasta `left` y devuelve el ajuste a tarjeta CUANDO LLEGA.
   *
   * Devolverlo de golpe la haría saltar. Y devolverlo tarde es peor: si para
   * entonces el avance o un arrastre ya volvieron a empezar, el ajuste pelearía
   * con ellos. Por eso quien empieza a mover la fila cancela lo pendiente.
   */
  function settle(node: HTMLElement, left: number) {
    cancelSettle.current?.();
    const finish = () => {
      cancel();
      node.style.scrollSnapType = "";
    };
    const fallback = window.setTimeout(finish, 600);
    const cancel = () => {
      node.removeEventListener("scrollend", finish);
      window.clearTimeout(fallback);
      if (cancelSettle.current === cancel) cancelSettle.current = null;
    };
    cancelSettle.current = cancel;
    node.addEventListener("scrollend", finish);
    node.scrollTo({ left, behavior: prefersReducedMotion() ? "instant" : "smooth" });
  }

  function cardWidth(node: HTMLElement): number {
    return (node.firstElementChild?.getBoundingClientRect().width || node.clientWidth) + GAP;
  }

  function takeControl() {
    setPlaying(false);
  }

  /** La persona pidió otra cosa: nada de lo anterior sigue mandando. */
  function newIntent() {
    intent.current += 1;
  }

  // Avance automático: sólo con varios productos, a la vista, sin cursor
  // encima, con la pestaña activa y sin «reducir movimiento». Fuera de la
  // vista el bucle SE DETIENE: no se pide un fotograma para no hacer nada.
  useEffect(() => {
    const node = track.current;
    if (!playing || !node || !several || prefersReducedMotion()) return;
    if (!("IntersectionObserver" in window)) return;

    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    let visible = false;
    let running = false;
    let offset = node.scrollLeft;
    let previous = 0;
    let frame = 0;

    const advance = (time: number) => {
      if (!visible || document.hidden) {
        running = false;
        return;
      }
      const elapsed = previous ? Math.min(time - previous, 50) : 0;
      previous = time;
      if (!hovered.current && !preference.matches) {
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
    const start = () => {
      if (running || !visible || document.hidden) return;
      running = true;
      previous = 0;
      offset = node.scrollLeft;
      frame = requestAnimationFrame(advance);
    };

    const observer = new IntersectionObserver((entries) => {
      visible = entries[0].isIntersecting;
      start();
    }, { threshold: 0.5 });
    observer.observe(node);
    document.addEventListener("visibilitychange", start);
    // El ajuste a tarjeta pelearía con un desplazamiento continuo — y también
    // el que una pausa reciente dejó pendiente de devolver.
    cancelSettle.current?.();
    node.style.scrollSnapType = "none";

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      document.removeEventListener("visibilitychange", start);
      const width = cardWidth(node);
      const max = Math.max(0, node.scrollWidth - node.clientWidth);
      settle(node, Math.max(0, Math.min(max || Infinity, Math.round(node.scrollLeft / width) * width)));
    };
  }, [playing, several]);

  // Dónde está: para deshabilitar la flecha del extremo y contar la posición.
  useEffect(() => {
    const node = track.current;
    if (!node) return;
    const update = () => {
      const width = node.firstElementChild?.getBoundingClientRect().width || 1;
      const end = node.scrollLeft >= node.scrollWidth - node.clientWidth - 2;
      const next = {
        start: node.scrollLeft < 2,
        end,
        // En el extremo se ven las últimas tarjetas: el contador dice la
        // última, no la primera de las que caben.
        index: end && node.scrollWidth > node.clientWidth
          ? products.length
          : Math.min(products.length, Math.round(node.scrollLeft / (width + GAP)) + 1),
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
    const max = Math.max(0, node.scrollWidth - node.clientWidth);
    const left = Math.max(0, Math.min(max || Infinity, node.scrollLeft + step * cardWidth(node)));
    if (instant || prefersReducedMotion()) {
      jumpTo(node, left);
      return;
    }
    newIntent();
    node.scrollTo({ left, behavior: "smooth" });
  }

  function jump(to: "start" | "end") {
    const node = track.current;
    if (!node) return;
    jumpTo(node, to === "start" ? 0 : Math.max(0, node.scrollWidth - node.clientWidth));
  }

  // Arrastre con RATÓN. El dedo y el lápiz no pasan por aquí: su scroll es el
  // del navegador, con su inercia, y reescribirlo sólo lo empeoraría.
  function onPointerDown(event: React.PointerEvent<HTMLDivElement>) {
    if (event.pointerType !== "mouse" || event.button !== 0 || drag.current) return;
    const node = track.current;
    if (!node) return;
    suppressClick.current = false;
    drag.current = {
      id: event.pointerId, x: event.clientX, left: node.scrollLeft,
      time: event.timeStamp, lastX: event.clientX, active: false,
    };
  }

  function onPointerMove(event: React.PointerEvent<HTMLDivElement>) {
    const state = drag.current;
    const node = track.current;
    if (!state || !node || event.pointerId !== state.id) return;
    const moved = event.clientX - state.x;
    if (!state.active) {
      if (Math.abs(moved) < DRAG_THRESHOLD) return;
      state.active = true;
      // A partir de aquí el arrastre es de la fila aunque el cursor salga.
      node.setPointerCapture?.(state.id);
      newIntent();
      cancelSettle.current?.();
      node.style.scrollSnapType = "none";
      setDragging(true);
    }
    state.lastX = event.clientX;
    state.time = event.timeStamp;
    node.scrollLeft = state.left - moved;
  }

  function onPointerEnd(event: React.PointerEvent<HTMLDivElement>) {
    const state = drag.current;
    const node = track.current;
    if (!state || event.pointerId !== state.id) return;
    drag.current = null;
    if (!state.active || !node) return;
    node.releasePointerCapture?.(state.id);
    setDragging(false);
    // El `click` que el navegador dispara al soltar no abre un producto.
    suppressClick.current = true;
    const width = cardWidth(node);
    const elapsed = Math.max(1, event.timeStamp - state.time);
    const velocity = (event.clientX - state.lastX) / elapsed;
    const flick = Math.abs(velocity) > FLICK ? -Math.sign(velocity) : 0;
    settle(node, Math.max(0, (Math.round(node.scrollLeft / width) + flick) * width));
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
      onPointerDownCapture={(event) => {
        if (fromControl(event.target)) return;
        // Un dedo o un clic sobre la fila es una intención nueva.
        if ((event.target as HTMLElement).closest?.("[data-carousel-track]")) newIntent();
        takeControl();
      }}
      // Sólo el gesto HORIZONTAL es de la fila. La rueda vertical es de la
      // página: no se retiene (aquí no hay `preventDefault`) ni detiene nada.
      onWheelCapture={(event) => {
        if (Math.abs(event.deltaX) <= Math.abs(event.deltaY)) return;
        newIntent();
        takeControl();
      }}
      onFocusCapture={(event) => { if (!fromControl(event.target)) takeControl(); }}
    >
      <div
        ref={track}
        id={id}
        data-carousel-track
        className="v3-carousel-track"
        tabIndex={0}
        role="group"
        aria-label="Lista de productos; usa las flechas para explorar"
        data-dragging={dragging ? "true" : undefined}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerEnd}
        onPointerCancel={onPointerEnd}
        onClickCapture={(event) => {
          if (!suppressClick.current) return;
          suppressClick.current = false;
          event.preventDefault();
          event.stopPropagation();
        }}
        onDragStart={(event) => event.preventDefault()}
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
        {products.map((product, index) => (
          <div
            key={product.id}
            className="v3-carousel-item"
            role="group"
            aria-roledescription="diapositiva"
            aria-label={`${index + 1} de ${products.length}`}
          >
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
