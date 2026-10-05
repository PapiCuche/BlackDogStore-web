import { act, fireEvent, render, screen } from '@testing-library/react';

import { ProductCarousel } from '@/app/components/ProductCarousel';

/**
 * CAROUSEL-MOTION · el carrusel de productos, al tacto.
 *
 * Sigue siendo scroll nativo: el dedo, la rueda y el teclado son del
 * navegador. Lo que se fija aquí es lo que se nota sin saber por qué:
 *
 *  · no trabaja cuando no se ve;
 *  · la rueda vertical es de la página — no la secuestra ni se da por aludido;
 *  · con ratón se arrastra, y soltar tras arrastrar no abre un producto;
 *  · al tomar el control no da un salto: se asienta en una tarjeta;
 *  · el contador llega al final.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));

let frames: FrameRequestCallback[];
let intersect: (visible: boolean) => void;
const scrollTo = jest.fn();

class FakePointerEvent extends MouseEvent {
  pointerType: string;
  pointerId: number;
  constructor(type: string, init: MouseEventInit & { pointerType?: string; pointerId?: number } = {}) {
    super(type, init);
    this.pointerType = init.pointerType ?? 'mouse';
    this.pointerId = init.pointerId ?? 1;
  }
}

beforeEach(() => {
  frames = [];
  scrollTo.mockReset();
  (window as unknown as { PointerEvent: unknown }).PointerEvent = FakePointerEvent;
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false, media: query, addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {},
    }),
  });
  HTMLElement.prototype.scrollTo = scrollTo as unknown as typeof HTMLElement.prototype.scrollTo;
  HTMLElement.prototype.setPointerCapture = jest.fn();
  HTMLElement.prototype.releasePointerCapture = jest.fn();
  window.requestAnimationFrame = ((cb: FrameRequestCallback) => { frames.push(cb); return frames.length; }) as typeof window.requestAnimationFrame;
  window.cancelAnimationFrame = jest.fn();
  class Intersection {
    constructor(callback: (entries: { isIntersecting: boolean }[]) => void) {
      intersect = (visible) => act(() => callback([{ isIntersecting: visible }]));
    }
    observe() {}
    disconnect() {}
  }
  class Resize { observe() {} disconnect() {} }
  (window as unknown as { IntersectionObserver: unknown }).IntersectionObserver = Intersection;
  (window as unknown as { ResizeObserver: unknown }).ResizeObserver = Resize;
});

const product = (id: number) => ({ id, slug: `producto-${id}`, name: `Producto ${id}`, price: 100 * id, inventory: 5 });
const three = [product(1), product(2), product(3)];
const track = () => screen.getByRole('group', { name: /Lista de productos/ });

/** jsdom no mide: se le dan a la pista unas medidas con las que razonar. */
function measure(node: HTMLElement, { scrollWidth = 1200, clientWidth = 400, card = 184 } = {}) {
  Object.defineProperty(node, 'scrollWidth', { configurable: true, value: scrollWidth });
  Object.defineProperty(node, 'clientWidth', { configurable: true, value: clientWidth });
  for (const child of Array.from(node.children)) {
    (child as HTMLElement).getBoundingClientRect = () => ({ width: card } as DOMRect);
  }
}

function runFrame(time: number) {
  const pending = frames.splice(0);
  act(() => { pending.forEach((cb) => cb(time)); });
}

describe('avance automático', () => {
  it('no pide un solo fotograma mientras el carrusel no está a la vista', () => {
    render(<ProductCarousel products={three} />);
    expect(frames).toHaveLength(0);

    intersect(true);
    expect(frames).toHaveLength(1);
  });

  it('deja de trabajar al salir de la vista y vuelve al entrar', () => {
    render(<ProductCarousel products={three} />);
    intersect(true);
    runFrame(16);
    expect(frames).toHaveLength(1);

    intersect(false);
    runFrame(32);
    expect(frames).toHaveLength(0);

    intersect(true);
    expect(frames).toHaveLength(1);
  });

  it('la rueda vertical es de la página: ni se detiene ni la retiene', () => {
    render(<ProductCarousel products={three} />);
    intersect(true);

    const vertical = new WheelEvent('wheel', { deltaY: 120, deltaX: 2, bubbles: true, cancelable: true });
    act(() => { track().dispatchEvent(vertical); });

    expect(vertical.defaultPrevented).toBe(false);
    expect(screen.getByRole('button', { name: 'Pausar avance automático' })).toBeInTheDocument();
  });

  it('desplazar en horizontal sí es tomar el control, sin retener el gesto', () => {
    render(<ProductCarousel products={three} />);
    intersect(true);

    const horizontal = new WheelEvent('wheel', { deltaX: 80, deltaY: 3, bubbles: true, cancelable: true });
    act(() => { track().dispatchEvent(horizontal); });

    expect(horizontal.defaultPrevented).toBe(false);
    expect(screen.getByRole('button', { name: 'Reanudar avance automático' })).toBeInTheDocument();
  });

  it('al tomar el control se asienta en una tarjeta en vez de saltar', () => {
    render(<ProductCarousel products={three} />);
    const node = track();
    measure(node);
    intersect(true);
    expect(node.style.scrollSnapType).toBe('none');
    node.scrollLeft = 230;                       // a media tarjeta (184 + 16 de hueco)

    scrollTo.mockClear();
    act(() => { fireEvent.focus(screen.getAllByRole('link')[0]); });

    expect(scrollTo).toHaveBeenLastCalledWith({ left: 200, behavior: 'smooth' });
    // El ajuste a tarjeta no vuelve hasta que termina de asentarse.
    expect(node.style.scrollSnapType).toBe('none');
    act(() => { node.dispatchEvent(new Event('scrollend')); });
    expect(node.style.scrollSnapType).toBe('');
  });
});

describe('pausar y reanudar', () => {
  it('reanudar enseguida no deja que el ajuste a tarjeta vuelva a mitad del avance', () => {
    // El asentamiento de la pausa termina DESPUÉS de reanudar. Si entonces
    // devolviera el ajuste, cada paso del avance volvería a la tarjeta y la
    // fila no se movería nunca (así se veía en desarrollo, donde React monta
    // dos veces).
    jest.useFakeTimers();
    try {
      render(<ProductCarousel products={three} />);
      const node = track();
      measure(node);
      intersect(true);

      fireEvent.click(screen.getByRole('button', { name: 'Pausar avance automático' }));
      fireEvent.click(screen.getByRole('button', { name: 'Reanudar avance automático' }));
      expect(node.style.scrollSnapType).toBe('none');

      act(() => { node.dispatchEvent(new Event('scrollend')); });
      act(() => { jest.advanceTimersByTime(1000); });

      expect(node.style.scrollSnapType).toBe('none');
    } finally {
      jest.useRealTimers();
    }
  });
});

describe('Inicio y Fin', () => {
  it('saltar al inicio gana a un desplazamiento suave que seguía en marcha', () => {
    // Medido en Chromium: un `scrollTo` instantáneo dado a mitad de uno suave
    // no lo cancela. La fila llega al inicio y la animación la vuelve a mover
    // lo que le faltaba: se queda entre dos tarjetas. El salto se reafirma en
    // los fotogramas siguientes hasta que la fila se queda donde se pidió.
    render(<ProductCarousel products={three} />);
    const node = track();
    measure(node);
    node.scrollLeft = 300;
    scrollTo.mockClear();

    fireEvent.keyDown(node, { key: 'Home' });
    expect(scrollTo).toHaveBeenLastCalledWith({ left: 0, behavior: 'instant' });

    node.scrollLeft = 46;                         // lo que deja la animación que seguía viva
    scrollTo.mockClear();
    runFrame(16);
    expect(scrollTo).toHaveBeenLastCalledWith({ left: 0, behavior: 'instant' });

    node.scrollLeft = 0;
    scrollTo.mockClear();
    for (let i = 0; i < 6; i += 1) runFrame(32 + i * 16);
    expect(scrollTo).not.toHaveBeenCalled();
    expect(frames).toHaveLength(0);               // y deja de vigilar
  });

  it('otra intención de quien navega suelta la vigilancia del salto', () => {
    render(<ProductCarousel products={three} />);
    const node = track();
    measure(node);
    node.scrollLeft = 300;
    act(() => { node.dispatchEvent(new Event('scroll')); });   // la flecha «siguientes» queda habilitada

    fireEvent.keyDown(node, { key: 'Home' });
    fireEvent.click(screen.getByRole('button', { name: 'Productos siguientes' }), { detail: 1 });
    node.scrollLeft = 120;                        // el desplazamiento nuevo, en marcha
    scrollTo.mockClear();
    for (let i = 0; i < 4; i += 1) runFrame(16 + i * 16);

    expect(scrollTo).not.toHaveBeenCalledWith({ left: 0, behavior: 'instant' });
  });

  it('Fin salta al último tramo, no más allá', () => {
    render(<ProductCarousel products={three} />);
    const node = track();
    measure(node);                                // 1200 de contenido, 400 a la vista
    scrollTo.mockClear();

    fireEvent.keyDown(node, { key: 'End' });

    expect(scrollTo).toHaveBeenLastCalledWith({ left: 800, behavior: 'instant' });
  });
});

describe('arrastre con ratón', () => {
  function pointer(type: string, target: Element, init: Record<string, unknown>) {
    act(() => {
      target.dispatchEvent(new FakePointerEvent(type, { bubbles: true, cancelable: true, button: 0, ...init }));
    });
  }

  it('arrastrar mueve la fila lo que se movió el ratón', () => {
    render(<ProductCarousel products={three} />);
    const node = track();
    measure(node);
    node.scrollLeft = 100;

    pointer('pointerdown', node, { clientX: 300, pointerType: 'mouse' });
    pointer('pointermove', node, { clientX: 220, pointerType: 'mouse' });

    expect(node.scrollLeft).toBe(180);
    expect(node).toHaveAttribute('data-dragging', 'true');
    pointer('pointerup', node, { clientX: 220, pointerType: 'mouse' });
    expect(node).not.toHaveAttribute('data-dragging');
  });

  it('soltar tras arrastrar no abre el producto que quedó debajo', () => {
    render(<ProductCarousel products={three} />);
    const node = track();
    measure(node);
    const link = screen.getAllByRole('link')[1];

    pointer('pointerdown', link, { clientX: 300, pointerType: 'mouse' });
    pointer('pointermove', link, { clientX: 200, pointerType: 'mouse' });
    pointer('pointerup', link, { clientX: 200, pointerType: 'mouse' });
    const click = new MouseEvent('click', { bubbles: true, cancelable: true });
    act(() => { link.dispatchEvent(click); });

    expect(click.defaultPrevented).toBe(true);
  });

  it('un clic sin arrastre sigue siendo un clic', () => {
    render(<ProductCarousel products={three} />);
    const link = screen.getAllByRole('link')[1];
    link.addEventListener('click', (event) => event.preventDefault(), { once: true });   // jsdom no navega

    pointer('pointerdown', link, { clientX: 300, pointerType: 'mouse' });
    pointer('pointermove', link, { clientX: 298, pointerType: 'mouse' });
    pointer('pointerup', link, { clientX: 298, pointerType: 'mouse' });
    const reached = jest.fn();
    link.addEventListener('click', reached, { once: true });
    act(() => { link.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true })); });

    expect(reached).toHaveBeenCalled();
    expect(track()).not.toHaveAttribute('data-dragging');
  });

  it('el dedo es del navegador: con tacto no se intercepta nada', () => {
    render(<ProductCarousel products={three} />);
    const node = track();
    measure(node);
    node.scrollLeft = 100;

    pointer('pointerdown', node, { clientX: 300, pointerType: 'touch' });
    pointer('pointermove', node, { clientX: 200, pointerType: 'touch' });

    expect(node.scrollLeft).toBe(100);
    expect(node).not.toHaveAttribute('data-dragging');
  });
});

describe('posición', () => {
  it('cada tarjeta dice cuál es de cuántas', () => {
    render(<ProductCarousel products={three} />);
    expect(screen.getByRole('group', { name: '1 de 3' })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: '3 de 3' })).toBeInTheDocument();
  });

  it('al llegar al final el contador marca la última', () => {
    render(<ProductCarousel products={three} />);
    const node = track();
    measure(node);                                // 1200 de contenido, 400 a la vista
    node.scrollLeft = 800;                        // el final: dos tarjetas visibles

    act(() => { node.dispatchEvent(new Event('scroll')); });

    expect(screen.getByText('03 / 03')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Productos siguientes' })).toBeDisabled();
  });
});
