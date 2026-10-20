import { act, fireEvent, render, screen, within } from '@testing-library/react';

import CartPage from '@/app/cart/page';
import { CartItemCard, isCartLineSellable, isCartLineSoldOut } from '@/app/components/CartItemCard';
import { CatalogModels } from '@/app/components/CatalogModels';
import ModelDetail from '@/app/components/ModelDetail';
import { ProductCard } from '@/app/components/ProductCard';
import ProductDetail from '@/app/components/ProductDetail';
import {
  SOLD_OUT_LABEL,
  availabilityLabel,
  isOptionAvailable,
  isOptionSelectable,
  selectOption,
  sortedAttributes,
  type ModelAttribute,
  type Variant,
} from '@/app/lib/catalog-models';
import { fetchWithAuth } from '@/app/lib/auth';

import { modelFixture, reply } from './support/device-commerce-fixtures';

/**
 * SUPPLIER-DEVICE-FLOW-02 · C — PUBLICADO NO ES DISPONIBLE (decisión del
 * propietario, 2026-10-20).
 *
 * Lo agotado sigue en la tienda: una variante agotada se puede ELEGIR para ver
 * sus fotos, sus especificaciones y su último precio, y dice «Agotado» sin
 * dejar comprarla; un modelo sin nada que vender se ve con su foto y
 * «Agotado»; la tarjeta, la ficha y el carrito dicen la misma palabra. Lo que
 * no existe (una combinación sin variante) sigue sin poder elegirse.
 */

jest.mock('@/app/lib/auth', () => ({
  fetchWithAuth: jest.fn(),
  getCurrentUser: jest.fn(() => Promise.resolve(null)),
}));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;
const reads = jest.fn();

let model: ReturnType<typeof modelFixture> & { sold_out?: boolean };

beforeEach(() => {
  model = modelFixture();
  send.mockReset();
  send.mockResolvedValue(reply({ id: 1 }, 201));
  reads.mockReset();
  reads.mockImplementation((input: RequestInfo | URL) =>
    Promise.resolve(String(input).includes('/catalog/models/equipo-x') ? reply(model) : reply({}, 404)));
  global.fetch = reads as unknown as typeof fetch;
  window.localStorage.clear();
});

const attributes = () => sortedAttributes(modelFixture().attributes as ModelAttribute[]);
const variants = () => modelFixture().variants as Variant[];
const capacity = () => screen.getByRole('radiogroup', { name: 'Capacidad' });
const colour = () => screen.getByRole('radiogroup', { name: /^Color/ });
const mainImage = () => within(screen.getByTestId('model-main-image')).getByRole('img');

async function renderModel() {
  render(<ModelDetail slug="equipo-x" />);
  await screen.findByRole('heading', { level: 1, name: 'Equipo X' });
}

describe('reglas de selección', () => {
  it('una combinación agotada se puede elegir; una que no existe, no', () => {
    const [cap, col] = [0, 1];
    const at256 = { '3': 12, '4': 21 };
    // Blanco 256 GB exists (sold out): selectable, but not available.
    expect(isOptionSelectable(variants(), attributes(), at256, col, 22)).toBe(true);
    expect(isOptionAvailable(variants(), attributes(), at256, col, 22)).toBe(false);
    // Rojo 256 GB does not exist at all.
    expect(isOptionSelectable(variants(), attributes(), at256, col, 23)).toBe(false);
    expect(isOptionSelectable(variants(), attributes(), {}, cap, 12)).toBe(true);
  });

  it('elegir lo agotado lo respeta; lo que sigue se reajusta primero a lo disponible', () => {
    const chosen = selectOption(variants(), attributes(), { '3': 12, '4': 21 }, 1, 22);
    expect(chosen).toEqual({ '3': 12, '4': 22 });
    // From Blanco 128 GB to 256 GB: Blanco 256 is sold out, Azul 256 is for sale.
    expect(selectOption(variants(), attributes(), { '3': 11, '4': 22 }, 0, 12)).toEqual({ '3': 12, '4': 21 });
  });

  it('sin nada disponible, el reajuste cae en lo que existe en vez de dejar la elección vacía', () => {
    const allGone = variants().map((v) => ({ ...v, available: false, availability: 'none' }));
    expect(selectOption(allGone, attributes(), { '3': 11, '4': 21 }, 0, 13)).toEqual({ '3': 13, '4': 23 });
  });

  it('la palabra es «Agotado», en un solo sitio', () => {
    expect(SOLD_OUT_LABEL).toBe('Agotado');
    expect(availabilityLabel({ available: false, availability: 'none' })).toBe('Agotado');
    expect(availabilityLabel({ available: false, availability: '' })).toBe('Agotado');
  });
});

describe('página de un modelo', () => {
  it('una variante agotada se elige y muestra su foto y su precio, sin dejar comprarla', async () => {
    await renderModel();
    fireEvent.click(within(capacity()).getByRole('radio', { name: '256 GB' }));
    const blanco = within(colour()).getByRole('radio', { name: 'Blanco, agotado' });
    expect(blanco).not.toHaveAttribute('aria-disabled');
    fireEvent.click(blanco);
    expect(blanco).toHaveAttribute('aria-checked', 'true');
    expect(colour()).toHaveAccessibleName('Color Blanco');
    expect(screen.getByTestId('model-price')).toHaveTextContent('S/ 4499.00');
    expect(mainImage()).toHaveAttribute('src', 'http://localhost/blanco-256.png');
    expect(screen.getByTestId('model-availability')).toHaveTextContent('Agotado');
    const action = screen.getByRole('button', { name: 'Agotado' });
    expect(action).toBeDisabled();
    await act(async () => { fireEvent.click(action); });
    expect(send).not.toHaveBeenCalled();
  });

  it('volver a una opción disponible vuelve a dejar comprar', async () => {
    await renderModel();
    fireEvent.click(within(capacity()).getByRole('radio', { name: '256 GB' }));
    fireEvent.click(within(colour()).getByRole('radio', { name: 'Blanco, agotado' }));
    fireEvent.click(within(colour()).getByRole('radio', { name: 'Azul' }));
    expect(screen.getByRole('button', { name: 'Agregar al carrito' })).toBeEnabled();
  });

  it('las flechas pasan por lo agotado y saltan lo que no existe', async () => {
    await renderModel();
    fireEvent.click(within(capacity()).getByRole('radio', { name: '256 GB' }));
    const colours = within(colour()).getAllByRole('radio');
    colours[0].focus();
    fireEvent.keyDown(colours[0], { key: 'ArrowRight' });
    expect(colours[1]).toHaveAttribute('aria-checked', 'true');
    fireEvent.keyDown(colours[1], { key: 'ArrowRight' });
    // Rojo 256 GB does not exist: the arrow wraps to Azul.
    expect(colours[0]).toHaveAttribute('aria-checked', 'true');
  });

  it('un modelo sin nada que vender se ve con su foto y «Agotado», sin acción de compra', async () => {
    model.variants = model.variants.map((v) => ({ ...v, available: false, availability: 'none' }));
    model.used_units = [];
    model.lines.used.count = 0;
    model.sold_out = true;
    await renderModel();
    expect(screen.getByTestId('model-sold-out')).toHaveTextContent('Agotado');
    // Still a page to look at: options and last price.
    expect(screen.getByTestId('model-price')).toHaveTextContent('S/ 3999.00');
    expect(screen.getByRole('button', { name: 'Agotado' })).toBeDisabled();
  });

  it('un modelo sólo de seminuevos sin equipos muestra su foto y «Agotado», nunca un equipo', async () => {
    model.variants = [];
    model.used_units = [];
    model.lines = { new: { label: 'De caja', count: 0 }, used: { label: 'Reacondicionados', count: 0 } };
    model.sold_out = true;
    await renderModel();
    expect(screen.getByTestId('model-sold-out')).toHaveTextContent('Agotado');
    expect(mainImage()).toHaveAttribute('src', 'http://localhost/modelo.png');
    expect(screen.queryByRole('button', { name: 'Comprar este equipo' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Agregar al carrito' })).toBeNull();
  });
});

describe('listas y fichas', () => {
  it('la tarjeta de un producto sin stock ni oferta dice «Agotado»', () => {
    render(<ProductCard id={1} slug="cable" name="Cable" price="59.00" inventory={0} on_order={null} />);
    expect(screen.getByText('Agotado')).toBeInTheDocument();
    expect(screen.queryByText('Sin stock')).toBeNull();
    // Its last price is still shown.
    expect(screen.getByText(/59\.00/)).toBeInTheDocument();
  });

  it('con una oferta sigue siendo «Bajo pedido»', () => {
    render(<ProductCard id={1} slug="cable" name="Cable" price="59.00" inventory={0}
      on_order={{ lead_time_days: 3, price: '65.00' }} />);
    expect(screen.getByText('Bajo pedido')).toBeInTheDocument();
    expect(screen.queryByText('Agotado')).toBeNull();
  });

  it('la ficha de un producto agotado lo dice y no deja comprar', async () => {
    await act(async () => {
      render(<ProductDetail product={{ id: 3, slug: 'cable', name: 'Cable', price: '59.00', inventory: 0, on_order: null }} />);
    });
    expect(screen.getByTestId('product-availability')).toHaveTextContent('Agotado');
    expect(screen.getByRole('button', { name: 'Agotado' })).toBeDisabled();
  });

  it('la tarjeta de un modelo agotado lo dice, con su foto', async () => {
    reads.mockImplementation(() => Promise.resolve(reply({
      lines: { new: { label: 'Nuevos', count: 1 }, used: { label: 'Seminuevos', count: 1 } },
      results: [
        { slug: 'viejo', name: 'Teléfono Viejo', image_url: 'http://localhost/viejo.png', category: null,
          from_price: null, has_new: false, has_used: true, sold_out: true },
        { slug: 'nuevo', name: 'Teléfono Nuevo', image_url: '', category: null,
          from_price: '3000.00', has_new: true, has_used: false, sold_out: false },
      ],
    })));
    await act(async () => { render(<CatalogModels category="telefonos" line="" onLineChange={jest.fn()} />); });
    const old = await screen.findByRole('link', { name: /Teléfono Viejo/ });
    expect(within(old).getByText('Agotado')).toBeInTheDocument();
    expect(within(old).getByRole('img')).toHaveAttribute('src', 'http://localhost/viejo.png');
    expect(within(screen.getByRole('link', { name: /Teléfono Nuevo/ })).queryByText('Agotado')).toBeNull();
  });
});

describe('carrito', () => {
  const SOLD_OUT_LINE = {
    id: 9, quantity: 1, available: true, supply: { origin: 'own' as const },
    product: { id: 5, name: 'Cable', price: '59.00', slug: 'cable', image_url: '', inventory: 0 },
  };

  it('una línea propia que se quedó sin estante dice «Agotado» y no se paga', () => {
    expect(isCartLineSoldOut(SOLD_OUT_LINE)).toBe(true);
    expect(isCartLineSellable(SOLD_OUT_LINE)).toBe(false);
    render(<CartItemCard {...SOLD_OUT_LINE} onRemove={jest.fn()} />);
    expect(screen.getByTestId('cart-line-sold-out')).toHaveTextContent('Agotado');
    expect(screen.getByTestId('cart-line-total')).toHaveTextContent('—');
    expect(screen.getByRole('button', { name: 'Eliminar Cable' })).toBeInTheDocument();
  });

  it('con estante, o sin dato de estante, o bajo pedido, no es «Agotado»', () => {
    expect(isCartLineSoldOut({ ...SOLD_OUT_LINE, product: { ...SOLD_OUT_LINE.product, inventory: 2 } })).toBe(false);
    expect(isCartLineSoldOut({ ...SOLD_OUT_LINE, product: { ...SOLD_OUT_LINE.product, inventory: undefined } })).toBe(false);
    expect(isCartLineSoldOut({
      ...SOLD_OUT_LINE, supply: { origin: 'supplier', available: true, lead_time_days: 3, price: '65.00' },
    })).toBe(false);
    // A device line holds its own device: the shelf left is not this line's.
    expect(isCartLineSoldOut({ ...SOLD_OUT_LINE, sells_by_unit: true })).toBe(false);
  });

  it('el carrito no la suma y no deja seguir al pago hasta quitarla', async () => {
    reads.mockImplementation((input: RequestInfo | URL) =>
      Promise.resolve(reply(String(input).includes('/cart/') ? [SOLD_OUT_LINE] : [])));
    await act(async () => { render(<CartPage />); });
    expect(await screen.findByTestId('cart-line-sold-out')).toHaveTextContent('Agotado');
    const summary = screen.getByRole('heading', { name: 'Resumen' }).closest('aside')!;
    expect(within(summary).getByText(/Quita lo que ya no está disponible para continuar/)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Continuar al checkout' })).toBeNull();
  });
});
