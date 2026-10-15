import { act, fireEvent, render, screen } from '@testing-library/react';

import CartPage from '@/app/cart/page';
import { CartItemCard, isCartLineSellable } from '@/app/components/CartItemCard';
import ModelDetail from '@/app/components/ModelDetail';
import ProductDetail from '@/app/components/ProductDetail';
import { availabilityLabel, onOrderLabel } from '@/app/lib/catalog-models';
import { fetchWithAuth } from '@/app/lib/auth';

import { modelFixture, reply } from './support/device-commerce-fixtures';

/**
 * DEVICE-COMMERCE-01 · DC3 — «bajo pedido» en la tienda.
 *
 * Sin estante y con una oferta que lo cubre, el producto se ofrece bajo pedido:
 * con su plazo, a SU precio y con una acción explícita que lo pide así. Nunca se
 * ve quién lo provee ni a qué costo. En el carrito, una línea bajo pedido dice
 * su plazo, y si la oferta ya no la cubre bloquea el pago como cualquier otra
 * línea no disponible.
 */

jest.mock('@/app/lib/auth', () => ({
  fetchWithAuth: jest.fn(),
  getCurrentUser: jest.fn(() => Promise.resolve(null)),
}));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;
const reads = jest.fn();

beforeEach(() => {
  send.mockReset();
  send.mockResolvedValue(reply({ id: 1 }, 201));
  reads.mockReset();
  reads.mockImplementation(() => Promise.resolve(reply([])));
  global.fetch = reads as unknown as typeof fetch;
  window.localStorage.clear();
});

const bodyOf = (call: number) => JSON.parse(String(send.mock.calls[call][1]?.body));

const PRODUCT = { id: 3, slug: 'cargador', name: 'Cargador 20W', price: '99.00', inventory: 0 };

describe('ficha de producto', () => {
  async function renderProduct(extra: Record<string, unknown>) {
    await act(async () => { render(<ProductDetail product={{ ...PRODUCT, ...extra }} />); });
  }

  it('sin stock y con oferta: dice el plazo, muestra el precio bajo pedido y lo pide explícitamente', async () => {
    await renderProduct({ on_order: { lead_time_days: 5, price: '109.90' } });
    expect(screen.getByTestId('product-availability')).toHaveTextContent('Bajo pedido — entrega en 5 días');
    expect(screen.getByTestId('product-price')).toHaveTextContent('S/ 109.90');
    expect(screen.queryByText('S/ 99.00')).toBeNull();
    const action = screen.getByRole('button', { name: 'Pedir bajo pedido' });
    expect(action).toBeEnabled();
    await act(async () => { fireEvent.click(action); });
    expect(bodyOf(0)).toMatchObject({ product: 3, quantity: 1, on_order: true });
    expect(await screen.findByRole('status')).toHaveTextContent('Agregado al carrito bajo pedido.');
  });

  it('sin stock y sin oferta: deshabilitado', async () => {
    await renderProduct({ on_order: null });
    expect(screen.getByRole('button', { name: 'Sin stock' })).toBeDisabled();
    expect(screen.queryByText(/Bajo pedido/)).toBeNull();
  });

  it('con stock, la compra normal no lleva `on_order`', async () => {
    await renderProduct({ inventory: 4, on_order: { lead_time_days: 5, price: '109.90' } });
    expect(screen.getByTestId('product-price')).toHaveTextContent('S/ 99.00');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Agregar al carrito' })); });
    expect(bodyOf(0)).not.toHaveProperty('on_order');
  });

  it('si el estante no alcanza y una oferta sí, ofrece pedirlo bajo pedido y sólo lo pide al pulsarlo', async () => {
    send.mockResolvedValueOnce(reply({
      code: 'on_order_available', detail: 'No hay stock suficiente.', on_order: { lead_time_days: 3, price: '119.00' },
    }, 400));
    await renderProduct({ inventory: 2 });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Agregar al carrito' })); });
    expect(send).toHaveBeenCalledTimes(1);
    const offer = screen.getByTestId('on-order-offer');
    expect(offer).toHaveTextContent('S/ 119.00');
    expect(screen.queryByRole('alert')).toBeNull();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Pedir bajo pedido — entrega en 3 días' })); });
    expect(send).toHaveBeenCalledTimes(2);
    expect(bodyOf(1)).toMatchObject({ product: 3, on_order: true });
  });
});

describe('página de un modelo', () => {
  function withSupplierVariant() {
    const model = modelFixture();
    // 256 GB Blanco: no own device, an offer covers it.
    Object.assign(model.variants[3], {
      available: true, availability: 'supplier', on_order: { lead_time_days: 7, price: '4699.00' },
    });
    return model;
  }

  async function renderModel(model: ReturnType<typeof modelFixture>) {
    reads.mockImplementation((input: RequestInfo | URL) =>
      Promise.resolve(String(input).includes('/catalog/models/equipo-x') ? reply(model) : reply({}, 404)));
    render(<ModelDetail slug="equipo-x" />);
    await screen.findByRole('heading', { level: 1, name: 'Equipo X' });
  }

  it('una variante del proveedor dice su plazo, cuesta lo de la oferta y se pide bajo pedido', async () => {
    await renderModel(withSupplierVariant());
    fireEvent.click(screen.getByRole('radio', { name: '256 GB' }));
    fireEvent.click(screen.getByRole('radio', { name: 'Blanco' }));
    expect(screen.getByTestId('model-availability')).toHaveTextContent('Bajo pedido — entrega en 7 días');
    expect(screen.getByTestId('model-price')).toHaveTextContent('S/ 4699.00');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Pedir bajo pedido' })); });
    const body = bodyOf(0);
    expect(body).toMatchObject({ product: 44, quantity: 1, on_order: true });
    expect(body).not.toHaveProperty('unit');
  });

  it('una variante propia sigue sin `on_order`, y una sin nada sigue deshabilitada', async () => {
    await renderModel(modelFixture());
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Agregar al carrito' })); });
    expect(bodyOf(0)).not.toHaveProperty('on_order');
    fireEvent.click(screen.getByRole('radio', { name: '256 GB' }));
    expect(screen.getByRole('radio', { name: 'Blanco, no disponible' })).toHaveAttribute('aria-disabled', 'true');
  });

  it('las palabras están en un solo sitio', () => {
    expect(availabilityLabel({ available: true, availability: 'supplier', on_order: { lead_time_days: 1, price: '1' } }))
      .toBe('Bajo pedido — entrega en 1 día');
    expect(onOrderLabel(10)).toBe('Bajo pedido — entrega en 10 días');
  });
});

/**
 * Owner's decision — with the company's switch on, a web buyer may ask the
 * supplier although the shelf has it. The server says so with
 * `on_order.over_stock: true`. Own stock stays the default: the main button
 * never changes; the supplier is an extra, explicit, secondary choice.
 */
describe('pedir al proveedor aunque haya stock', () => {
  const OVER = { lead_time_days: 6, price: '104.50', over_stock: true };

  async function renderProduct(extra: Record<string, unknown>) {
    await act(async () => { render(<ProductDetail product={{ ...PRODUCT, inventory: 4, ...extra }} />); });
  }

  it('sin `over_stock` no hay opción del proveedor', async () => {
    await renderProduct({ on_order: { lead_time_days: 6, price: '104.50' } });
    expect(screen.queryByTestId('over-stock-offer')).toBeNull();
    expect(screen.queryByRole('button', { name: /Pedir al proveedor/ })).toBeNull();
  });

  it('con `over_stock` y con stock: el botón principal sigue siendo del stock, sin `on_order`', async () => {
    await renderProduct({ on_order: OVER });
    expect(screen.getByTestId('product-availability')).toHaveTextContent('En stock');
    expect(screen.getByTestId('product-price')).toHaveTextContent('S/ 99.00');
    const option = screen.getByTestId('over-stock-offer');
    expect(option).toHaveTextContent('¿Prefieres pedirlo al proveedor?');
    expect(option).toHaveTextContent('Se despacha desde el proveedor: entrega en 6 días · S/ 104.50 c/u');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Agregar al carrito' })); });
    expect(bodyOf(0)).not.toHaveProperty('on_order');
  });

  it('pulsar la opción del proveedor envía `on_order: true`', async () => {
    await renderProduct({ on_order: OVER });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Pedir al proveedor — entrega en 6 días' }));
    });
    expect(bodyOf(0)).toMatchObject({ product: 3, quantity: 1, on_order: true });
    expect(await screen.findByRole('status')).toHaveTextContent('Agregado al carrito bajo pedido.');
  });

  it('sin stock, `over_stock` no añade una segunda opción', async () => {
    await renderProduct({ inventory: 0, on_order: OVER });
    expect(screen.getByRole('button', { name: 'Pedir bajo pedido' })).toBeEnabled();
    expect(screen.queryByTestId('over-stock-offer')).toBeNull();
  });

  it('si la empresa lo apagó entretanto, se dice la negativa del servidor', async () => {
    send.mockResolvedValueOnce(reply({
      code: 'stock_available', detail: 'Hay stock propio: agrégalo desde el stock.',
    }, 409));
    await renderProduct({ on_order: OVER });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Pedir al proveedor — entrega en 6 días' }));
    });
    expect(await screen.findByRole('alert')).toHaveTextContent('Hay stock propio: agrégalo desde el stock.');
  });

  describe('página de un modelo', () => {
    async function renderModel(over: boolean) {
      const model = modelFixture();
      Object.assign(model.variants[0], {
        on_order: { lead_time_days: 8, price: '4100.00', ...(over ? { over_stock: true } : {}) },
      });
      reads.mockImplementation((input: RequestInfo | URL) =>
        Promise.resolve(String(input).includes('/catalog/models/equipo-x') ? reply(model) : reply({}, 404)));
      render(<ModelDetail slug="equipo-x" />);
      await screen.findByRole('heading', { level: 1, name: 'Equipo X' });
    }

    it('sin `over_stock` no hay opción del proveedor', async () => {
      await renderModel(false);
      expect(screen.queryByTestId('over-stock-offer')).toBeNull();
    });

    it('con `over_stock`: el principal sin `on_order`, la opción con `on_order: true` y sin equipo', async () => {
      await renderModel(true);
      expect(screen.getByTestId('model-price')).toHaveTextContent('S/ 3999.00');
      expect(screen.getByTestId('over-stock-offer'))
        .toHaveTextContent('Se despacha desde el proveedor: entrega en 8 días · S/ 4100.00 c/u');
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Agregar al carrito' })); });
      expect(bodyOf(0)).not.toHaveProperty('on_order');
      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: 'Pedir al proveedor — entrega en 8 días' }));
      });
      expect(bodyOf(1)).toMatchObject({ product: 41, quantity: 1, on_order: true });
      expect(bodyOf(1)).not.toHaveProperty('unit');
    });

    it('un 409 `stock_available` dice el motivo del servidor', async () => {
      send.mockResolvedValueOnce(reply({ code: 'stock_available', detail: 'Ya no se ofrece pedirlo al proveedor.' }, 409));
      await renderModel(true);
      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: 'Pedir al proveedor — entrega en 8 días' }));
      });
      expect(await screen.findByRole('alert')).toHaveTextContent('Ya no se ofrece pedirlo al proveedor.');
    });
  });
});

type Line = Parameters<typeof CartItemCard>[0];

const SUPPLIER_LINE: Line = {
  id: 11, quantity: 2, available: true, sells_by_unit: false, line_total: '219.80',
  product: { id: 3, name: 'Cargador 20W', price: '99.00', slug: 'cargador', image_url: '' },
  supply: { origin: 'supplier', available: true, lead_time_days: 5, price: '109.90' },
};
const GONE_LINE: Line = {
  ...SUPPLIER_LINE, id: 12, line_total: null,
  supply: { origin: 'supplier', available: false, lead_time_days: null, price: null },
};
const OWN_LINE: Line = {
  id: 13, quantity: 1, available: true, sells_by_unit: false, line_total: '50.00',
  product: { id: 4, name: 'Funda', price: '50.00', slug: 'funda', image_url: '' },
  supply: { origin: 'own' },
};

describe('carrito', () => {
  it('una línea bajo pedido dice su plazo y cuesta lo de la oferta', () => {
    render(<CartItemCard {...SUPPLIER_LINE} />);
    expect(screen.getByTestId('cart-line-supply')).toHaveTextContent('Bajo pedido — entrega en 5 días');
    expect(screen.getByText('S/ 109.90 c/u')).toBeInTheDocument();
    expect(screen.getByTestId('cart-line-total')).toHaveTextContent('S/ 219.80');
  });

  it('una línea que la oferta ya no cubre lo dice y no tiene importe', () => {
    render(<CartItemCard {...GONE_LINE} onRemove={jest.fn()} />);
    expect(screen.getByTestId('cart-line-supply')).toHaveTextContent('Ya no disponible bajo pedido');
    expect(screen.getByTestId('cart-line-total')).toHaveTextContent('—');
    expect(isCartLineSellable(GONE_LINE)).toBe(false);
    expect(isCartLineSellable(SUPPLIER_LINE)).toBe(true);
    expect(isCartLineSellable(OWN_LINE)).toBe(true);
  });

  it('bloquea el pago mientras haya una línea bajo pedido sin cubrir', async () => {
    reads.mockImplementation((input: RequestInfo | URL) =>
      Promise.resolve(reply(String(input).includes('/cart/') ? [GONE_LINE, OWN_LINE] : [])));
    render(<CartPage />);
    expect(await screen.findByText(/Algún producto bajo pedido ya no está disponible/)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Continuar al checkout' })).toBeNull();
    const subtotal = screen.getByText('Subtotal').nextElementSibling;
    expect(subtotal).toHaveTextContent('S/ 50.00');
  });

  it('con la oferta vigente, se puede pagar y suma el precio de la oferta', async () => {
    reads.mockImplementation((input: RequestInfo | URL) =>
      Promise.resolve(reply(String(input).includes('/cart/') ? [SUPPLIER_LINE, OWN_LINE] : [])));
    render(<CartPage />);
    expect(await screen.findByRole('link', { name: 'Continuar al checkout' })).toBeInTheDocument();
    expect(screen.getByText('Subtotal').nextElementSibling).toHaveTextContent('S/ 269.80');
  });

  it('subir la cantidad más allá de la oferta muestra la negativa del servidor', async () => {
    reads.mockImplementation((input: RequestInfo | URL) =>
      Promise.resolve(reply(String(input).includes('/cart/') ? [SUPPLIER_LINE] : [])));
    send.mockResolvedValueOnce(reply({ code: 'not_on_order', detail: 'La oferta sólo cubre 2 unidades.' }, 400));
    render(<CartPage />);
    const quantity = await screen.findByLabelText('Cantidad de Cargador 20W');
    await act(async () => { fireEvent.change(quantity, { target: { value: '5' } }); });
    expect(await screen.findByRole('alert')).toHaveTextContent('La oferta sólo cubre 2 unidades.');
  });
});
