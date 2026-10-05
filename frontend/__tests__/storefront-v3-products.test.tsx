import { act, fireEvent, render, screen, within } from '@testing-library/react';

import { CartItemCard } from '@/app/components/CartItemCard';
import { ProductCard } from '@/app/components/ProductCard';
import { ProductCarousel } from '@/app/components/ProductCarousel';

/**
 * STOREFRONT-V3 · productos, carrusel y línea del carrito.
 *
 * Ninguno de estos componentes sabe qué vende la tienda. Sin fotografía se
 * sostienen solos; con «reducir movimiento» no se mueven; y el carrusel no es
 * más que los productos reales en fila, con sus enlaces intactos.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));

const PILOT = /black dog|iphone|apple|garant[ií]a/i;

let reducedMotion = false;
const scrollTo = jest.fn();
const frames: FrameRequestCallback[] = [];

beforeEach(() => {
  reducedMotion = false;
  scrollTo.mockClear();
  frames.length = 0;
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: query.includes('prefers-reduced-motion') ? reducedMotion : false,
      media: query, addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {},
    }),
  });
  HTMLElement.prototype.scrollTo = scrollTo as unknown as typeof HTMLElement.prototype.scrollTo;
  window.requestAnimationFrame = ((cb: FrameRequestCallback) => { frames.push(cb); return frames.length; }) as typeof window.requestAnimationFrame;
  window.cancelAnimationFrame = jest.fn();
  class Observer {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (window as unknown as { IntersectionObserver: unknown }).IntersectionObserver = Observer;
  (window as unknown as { ResizeObserver: unknown }).ResizeObserver = Observer;
});

const product = (id: number, extra: Record<string, unknown> = {}) => ({
  id, slug: `producto-${id}`, name: `Producto ${id}`, price: 100 * id, inventory: 5, ...extra,
});

describe('tarjeta de producto', () => {
  it('sin fotografía pinta un respaldo neutro y no promete nada', () => {
    const { container } = render(<ProductCard {...product(1)} />);
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByRole('link')).toHaveAttribute('href', '/product/producto-1');
    expect(screen.getByText('P')).toHaveAttribute('aria-hidden', 'true');
    expect(container.textContent ?? '').not.toMatch(PILOT);
  });

  it('con fotografía usa la del catálogo, con el nombre como texto alternativo', () => {
    render(<ProductCard {...product(2, { image_url: 'http://localhost/foto.png' })} />);
    expect(screen.getByRole('img', { name: 'Producto 2' })).toBeInTheDocument();
  });
});

describe('ficha de producto', () => {
  /* eslint-disable-next-line @typescript-eslint/no-require-imports */
  const ProductDetail = require('@/app/components/ProductDetail').default;

  beforeEach(() => {
    global.fetch = jest.fn(() => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) })) as unknown as typeof fetch;
  });

  it('sin fotografía pinta un respaldo neutro, sin datos de ninguna marca', async () => {
    let container: HTMLElement = document.body;
    await act(async () => {
      ({ container } = render(<ProductDetail product={{ ...product(7), description: '', category: { id: 1, name: 'Audio', slug: 'audio' } }} />));
    });
    expect(screen.getByRole('heading', { level: 1, name: 'Producto 7' })).toBeInTheDocument();
    expect(container.querySelector('img')).toBeNull();
    expect(container.textContent ?? '').not.toMatch(/black dog|iphone|apple/i);
    for (const link of screen.getAllByRole('link', { name: 'Audio' })) {
      expect(link).toHaveAttribute('href', '/product?category=audio');
    }
  });
});

describe('ficha de producto · galería', () => {
  /* eslint-disable-next-line @typescript-eslint/no-require-imports */
  const ProductDetail = require('@/app/components/ProductDetail').default;
  const A = '/api/storefront/images/' + 'a'.repeat(32);
  const B = '/api/storefront/images/' + 'b'.repeat(32);

  beforeEach(() => {
    global.fetch = jest.fn(() => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) })) as unknown as typeof fetch;
  });

  async function show(images: unknown[]) {
    await act(async () => {
      render(<ProductDetail product={{ ...product(7), image_url: A, images }} />);
    });
  }

  it('con varias imágenes muestra la principal y deja elegir las demás', async () => {
    await show([
      { url: A, alt_text: 'Vista frontal', is_primary: true, width: 120, height: 80 },
      { url: B, alt_text: '', is_primary: false, width: 120, height: 80 },
    ]);

    const main = screen.getByTestId('product-main-image').querySelector('img') as HTMLImageElement;
    expect(main.getAttribute('src')).toBe(A);
    expect(main).toHaveAttribute('alt', 'Vista frontal');

    const thumbs = within(screen.getByRole('group', { name: 'Imágenes del producto' })).getAllByRole('button');
    expect(thumbs).toHaveLength(2);
    expect(thumbs[0]).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(thumbs[1]);

    const swapped = screen.getByTestId('product-main-image').querySelector('img') as HTMLImageElement;
    expect(swapped.getAttribute('src')).toBe(B);
    // Sin texto alternativo, la imagen se describe con el nombre del producto.
    expect(swapped).toHaveAttribute('alt', 'Producto 7');
    expect(thumbs[1]).toHaveAttribute('aria-pressed', 'true');
  });

  it('con una sola imagen no hay miniaturas', async () => {
    await show([{ url: A, alt_text: '', is_primary: true, width: 120, height: 80 }]);
    expect(screen.queryByRole('group', { name: 'Imágenes del producto' })).not.toBeInTheDocument();
    expect(screen.getByTestId('product-main-image').querySelector('img')).not.toBeNull();
  });

  it('un producto sin galería sigue mostrando su dirección de siempre', async () => {
    await act(async () => {
      render(<ProductDetail product={{ ...product(7), image_url: 'http://localhost/foto.png' }} />);
    });
    expect(screen.getByRole('img', { name: 'Producto 7' })).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Imágenes del producto' })).not.toBeInTheDocument();
  });
});

describe('carrusel de productos', () => {
  const track = () => screen.getByRole('group', { name: /Lista de productos/ });

  it('sin productos no dibuja nada', () => {
    const { container } = render(<ProductCarousel products={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('con un producto lo muestra, sin controles ni avance', () => {
    render(<ProductCarousel products={[product(1)]} />);
    expect(screen.getByRole('link', { name: /Producto 1/ })).toHaveAttribute('href', '/product/producto-1');
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(frames).toHaveLength(0);
  });

  it('con varios conserva el enlace de cada uno y ofrece flechas', () => {
    render(<ProductCarousel products={[product(1), product(2), product(3)]} />);
    const region = screen.getByRole('region', { name: 'Productos destacados' });
    expect(within(region).getAllByRole('link').map((a) => a.getAttribute('href'))).toEqual([
      '/product/producto-1', '/product/producto-2', '/product/producto-3',
    ]);
    expect(screen.getByRole('button', { name: 'Productos siguientes' })).toHaveAttribute('aria-controls', track().id);
    expect(screen.getByRole('button', { name: 'Productos anteriores' })).toBeDisabled();
  });

  it('funciona con productos sin fotografía', () => {
    const { container } = render(<ProductCarousel products={[product(1), product(2)]} />);
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getAllByRole('link')).toHaveLength(2);
  });

  it('las flechas del teclado desplazan sin animación; Inicio y Fin saltan', () => {
    render(<ProductCarousel products={[product(1), product(2), product(3)]} />);
    scrollTo.mockClear();

    fireEvent.keyDown(track(), { key: 'ArrowRight' });
    expect(scrollTo).toHaveBeenLastCalledWith(expect.objectContaining({ behavior: 'instant' }));

    fireEvent.keyDown(track(), { key: 'Home' });
    expect(scrollTo).toHaveBeenLastCalledWith({ left: 0, behavior: 'instant' });

    fireEvent.keyDown(track(), { key: 'End' });
    expect(scrollTo).toHaveBeenLastCalledWith(expect.objectContaining({ behavior: 'instant' }));
  });

  it('una flecha dentro de una tarjeta es de la tarjeta', () => {
    render(<ProductCarousel products={[product(1), product(2)]} />);
    scrollTo.mockClear();
    fireEvent.keyDown(screen.getAllByRole('link')[0], { key: 'ArrowRight' });
    expect(scrollTo).not.toHaveBeenCalled();
  });

  it('avanza solo mientras nadie lo toca, y cede al enfocarlo', () => {
    render(<ProductCarousel products={[product(1), product(2)]} />);
    expect(frames.length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'Pausar avance automático' })).toBeInTheDocument();

    act(() => { fireEvent.focus(screen.getAllByRole('link')[0]); });
    expect(screen.getByRole('button', { name: 'Reanudar avance automático' })).toBeInTheDocument();
  });

  it('con «reducir movimiento» no hay avance automático ni desplazamiento suave', () => {
    reducedMotion = true;
    // jsdom no mide: se le da a la pista un ancho menor que su contenido para
    // que la flecha «siguiente» esté habilitada.
    const width = jest.spyOn(HTMLElement.prototype, 'scrollWidth', 'get').mockReturnValue(1200);
    const visible = jest.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(400);
    render(<ProductCarousel products={[product(1), product(2), product(3)]} />);
    width.mockRestore();
    visible.mockRestore();
    expect(frames).toHaveLength(0);

    scrollTo.mockClear();
    fireEvent.click(screen.getByRole('button', { name: 'Productos siguientes' }), { detail: 1 });
    expect(scrollTo).toHaveBeenLastCalledWith(expect.objectContaining({ behavior: 'instant' }));
  });
});

describe('línea del carrito', () => {
  const line = (id: number, extra: Record<string, unknown> = {}) => ({
    id, quantity: 1, product: { id, name: `Producto ${id}`, price: 50, slug: `producto-${id}`, ...extra },
  });

  it('el nombre lleva a la ficha del producto', () => {
    render(<CartItemCard {...line(1)} />);
    expect(screen.getByRole('link', { name: 'Producto 1' })).toHaveAttribute('href', '/product/producto-1');
  });

  it('cada línea nombra su propia cantidad y no repite identificadores', () => {
    const { container } = render(<><CartItemCard {...line(1)} /><CartItemCard {...line(2)} /></>);
    expect(screen.getByRole('spinbutton', { name: 'Cantidad de Producto 1' })).toBeInTheDocument();
    expect(screen.getByRole('spinbutton', { name: 'Cantidad de Producto 2' })).toBeInTheDocument();
    const ids = Array.from(container.querySelectorAll('[id]')).map((node) => node.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('la cantidad sigue avisando al carrito, y no acepta menos de una', () => {
    const onQuantityChange = jest.fn();
    render(<CartItemCard {...line(1)} onQuantityChange={onQuantityChange} />);
    const input = screen.getByRole('spinbutton');
    fireEvent.change(input, { target: { value: '3' } });
    expect(onQuantityChange).toHaveBeenLastCalledWith(3);
    fireEvent.change(input, { target: { value: '0' } });
    expect(onQuantityChange).toHaveBeenCalledTimes(1);
  });

  it('sin fotografía pinta el mismo respaldo neutro', () => {
    const { container } = render(<CartItemCard {...line(1)} />);
    expect(container.querySelector('img')).toBeNull();
  });
});
