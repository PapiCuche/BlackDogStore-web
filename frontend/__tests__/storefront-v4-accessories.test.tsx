import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { ModelConfiguratorV4 } from '@/app/components/v4/ModelConfiguratorV4';
import { StorefrontProvider } from '@/app/components/StorefrontProvider';
import { ROLE_ORDER } from '@/app/admin/lib/palette';
import { fetchWithAuth } from '@/app/lib/auth';
import { track } from '@/app/lib/analytics/service';
import {
  ATTRIBUTE_ROLE_ORDER,
  availabilityLabel,
  roleOrderedAttributes,
  sortedAttributes,
  type ModelAttribute,
} from '@/app/lib/catalog-models';
import { NEUTRAL_CONFIG } from '@/app/lib/storefront';

import { accessoryFixture, modelFixture, reply } from './support/device-commerce-fixtures';
import { imageSource } from './support/image-source';

/**
 * P2 · F3 en la ficha V4 — los accesorios compatibles y los selectores por rol.
 *
 * El bloque «Compatible con {modelo}» sale SÓLO de `model.accessories` (la API
 * pública, `accessory_compat.for_model_public`): sin accesorios, no hay bloque.
 * Cada tarjeta dice lo mismo que una variante —precio y disponibilidad con las
 * mismas palabras— y agrega al carrito por el mismo camino que la ficha de
 * producto (`addProductToCart`). Nunca cantidades ni costos.
 *
 * Los selectores siguen el orden de los roles (capacidad, color, SIM, región y
 * después los demás), y SIM y región sólo aparecen cuando las variantes del
 * modelo de verdad cambian en ellos.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/analytics/service', () => ({
  ...jest.requireActual('@/app/lib/analytics/service'),
  track: jest.fn(),
}));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;
const tracked = track as jest.MockedFunction<typeof track>;
const reads = jest.fn();

const MANAGED = (n: number) => `/api/storefront/images/${String(n).padStart(32, 'a')}`;

type Model = ReturnType<typeof modelFixture> & { accessories?: ReturnType<typeof accessoryFixture>[] };
let model: Model;

beforeEach(() => {
  model = modelFixture();
  reads.mockReset();
  reads.mockImplementation((input: RequestInfo | URL) =>
    Promise.resolve(String(input).includes('/catalog/models/equipo-x') ? reply(model) : reply({}, 404)));
  global.fetch = reads as unknown as typeof fetch;
  send.mockReset();
  send.mockResolvedValue(reply({ id: 1 }, 201));
  tracked.mockReset();
  window.localStorage.clear();
});

async function renderPage(initialLine?: string) {
  const view = render(
    <StorefrontProvider config={NEUTRAL_CONFIG}>
      <ModelConfiguratorV4 slug="equipo-x" initialLine={initialLine} />
    </StorefrontProvider>,
  );
  await screen.findByRole('heading', { level: 1, name: 'Equipo X' });
  return view;
}

const block = () => screen.getByRole('region', { name: 'Compatible con Equipo X' });
const card = (name: string) => within(block()).getByRole('article', { name });
const sentBodies = () => send.mock.calls.map(([, init]) => JSON.parse(String(init?.body)));

describe('bloque «Compatible con {modelo}»', () => {
  it('sin accesorios no hay bloque', async () => {
    model.accessories = [];
    const { container } = await renderPage();
    expect(screen.queryByRole('region', { name: /Compatible con/ })).toBeNull();
    expect(screen.queryByTestId('v4-accessories')).toBeNull();
    expect(container.textContent).not.toMatch(/Compatible con/);
  });

  it('un servidor anterior, sin el campo, tampoco dibuja el bloque', async () => {
    delete model.accessories;
    await renderPage();
    expect(screen.queryByTestId('v4-accessories')).toBeNull();
  });

  it('cada tarjeta: foto, nombre, precio, disponibilidad y enlace a la ficha del producto', async () => {
    model.accessories = [accessoryFixture()];
    await renderPage();
    expect(within(block()).getByRole('heading', { level: 2, name: 'Compatible con Equipo X' })).toBeInTheDocument();
    const funda = card('Funda X');
    expect(within(funda).getByRole('img')).toHaveAttribute('src', 'http://localhost/funda-x.png');
    expect(within(funda).getByText('Imagen referencial')).toBeInTheDocument();
    expect(within(funda).getByText('S/ 89.00')).toBeInTheDocument();
    expect(within(funda).getByText('En stock')).toBeInTheDocument();
    expect(within(funda).getByRole('link', { name: 'Funda X' })).toHaveAttribute('href', '/product/funda-x');
    expect(within(funda).getByRole('button', { name: /^Agregar al carrito/ })).toBeEnabled();
  });

  it('la foto principal de la galería del accesorio va primero; si la subió la tienda, sin rótulo', async () => {
    model.accessories = [accessoryFixture({
      images: [
        { url: MANAGED(2), alt_text: '', is_primary: false, width: 800, height: 800 },
        { url: MANAGED(1), alt_text: 'Funda X de frente', is_primary: true, width: 800, height: 800 },
      ],
    })];
    await renderPage();
    const funda = card('Funda X');
    expect(imageSource(within(funda).getByRole('img'))).toBe(MANAGED(1));
    expect(within(funda).queryByText('Imagen referencial')).toBeNull();
  });

  it('los accesorios en el orden de la API', async () => {
    model.accessories = [
      accessoryFixture({ id: 72, slug: 'cargador-x', name: 'Cargador X' }),
      accessoryFixture(),
    ];
    await renderPage();
    expect(within(block()).getAllByRole('article').map((el) => el.getAttribute('aria-label')))
      .toEqual(['Cargador X', 'Funda X']);
  });

  it('un accesorio agotado se muestra «Agotado» y su botón no agrega', async () => {
    model.accessories = [accessoryFixture({ available: false, availability: 'none' })];
    await renderPage();
    const funda = card('Funda X');
    expect(within(funda).getAllByText('Agotado').length).toBeGreaterThan(0);
    const button = within(funda).getByRole('button', { name: /^Agotado/ });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(send).not.toHaveBeenCalled();
  });

  it('un accesorio bajo pedido dice su plazo igual que una variante, con el precio bajo pedido', async () => {
    const onOrder = { lead_time_days: 5, price: '99.00' };
    model.accessories = [accessoryFixture({ available: true, availability: 'supplier', on_order: onOrder })];
    await renderPage();
    const funda = card('Funda X');
    const expected = availabilityLabel({ available: true, availability: 'supplier', on_order: onOrder });
    expect(expected).toBe('Bajo pedido — entrega en 5 días');
    expect(within(funda).getByText(expected)).toBeInTheDocument();
    expect(within(funda).getByText('S/ 99.00')).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(within(funda).getByRole('button', { name: /^Pedir bajo pedido/ }));
    });
    expect(sentBodies()[0]).toMatchObject({ product: 71, quantity: 1, on_order: true });
  });

  it('agregar al carrito usa la API del carrito con el id del producto', async () => {
    model.accessories = [accessoryFixture()];
    await renderPage();
    await act(async () => {
      fireEvent.click(within(card('Funda X')).getByRole('button', { name: /^Agregar al carrito/ }));
    });
    expect(send).toHaveBeenCalledTimes(1);
    expect(String(send.mock.calls[0][0])).toMatch(/\/cart\/add\/$/);
    expect(send.mock.calls[0][1]).toMatchObject({ method: 'POST' });
    const body = sentBodies()[0];
    expect(body).toMatchObject({ product: 71, quantity: 1 });
    expect(body).not.toHaveProperty('unit');
    expect(body).not.toHaveProperty('on_order');
    expect(typeof body.session_key).toBe('string');
    expect(within(card('Funda X')).getByRole('status')).toHaveTextContent('Agregado al carrito.');
  });

  it('una negativa del carrito se dice en la tarjeta', async () => {
    model.accessories = [accessoryFixture()];
    send.mockResolvedValueOnce(reply({ detail: 'No queda stock.', code: 'sold_out' }, 409));
    await renderPage();
    await act(async () => {
      fireEvent.click(within(card('Funda X')).getByRole('button', { name: /^Agregar al carrito/ }));
    });
    expect(within(card('Funda X')).getByRole('alert')).toHaveTextContent('No queda stock.');
  });

  it('se acabó mientras miraba y una oferta lo cubre: lo ofrece, no lo agrega solo', async () => {
    model.accessories = [accessoryFixture()];
    send.mockResolvedValueOnce(reply({
      detail: 'No hay stock suficiente.', code: 'on_order_available', on_order: { lead_time_days: 3, price: '95.00' },
    }, 409));
    await renderPage();
    await act(async () => {
      fireEvent.click(within(card('Funda X')).getByRole('button', { name: /^Agregar al carrito/ }));
    });
    expect(send).toHaveBeenCalledTimes(1);
    const offer = within(card('Funda X')).getByTestId('on-order-offer');
    await act(async () => {
      fireEvent.click(within(offer).getByRole('button', { name: /Pedir bajo pedido/ }));
    });
    expect(sentBodies()[1]).toMatchObject({ product: 71, quantity: 1, on_order: true });
  });

  it('nunca muestra cantidades ni costos, aunque llegaran', async () => {
    model.accessories = [accessoryFixture({ inventory: 37, quantity: 37, cost: '41.50', unit_cost: '41.50' })];
    await renderPage();
    const text = block().textContent ?? '';
    expect(text).not.toMatch(/37/);
    expect(text).not.toMatch(/41[.,]50/);
    expect(text).not.toMatch(/unidades|stock:\s*\d|costo/i);
  });
});

describe('accesorio en stock que la empresa deja pedir al proveedor (over_stock)', () => {
  const overStock = { lead_time_days: 7, price: '79.00', over_stock: true };

  it('ofrece «pedir al proveedor» aparte; el botón principal sigue agregando del stock', async () => {
    model.accessories = [accessoryFixture({ on_order: overStock })];
    await renderPage();
    const funda = card('Funda X');
    expect(within(funda).getByText('En stock')).toBeInTheDocument();
    expect(within(funda).getByText('S/ 89.00')).toBeInTheDocument();
    const option = within(funda).getByTestId('over-stock-offer');
    expect(option).toHaveTextContent('entrega en 7 días');
    expect(option).toHaveTextContent('S/ 79.00');
    // Nada se pide al proveedor sin que el comprador lo elija.
    expect(send).not.toHaveBeenCalled();
    await act(async () => {
      fireEvent.click(within(funda).getByRole('button', { name: /^Agregar al carrito/ }));
    });
    expect(sentBodies()[0]).not.toHaveProperty('on_order');
  });

  it('elegirlo agrega bajo pedido, al precio de la oferta', async () => {
    model.accessories = [accessoryFixture({ on_order: overStock })];
    await renderPage();
    const option = within(card('Funda X')).getByTestId('over-stock-offer');
    await act(async () => {
      fireEvent.click(within(option).getByRole('button', { name: /^Pedir al proveedor/ }));
    });
    expect(send).toHaveBeenCalledTimes(1);
    expect(sentBodies()[0]).toMatchObject({ product: 71, quantity: 1, on_order: true });
    expect(within(card('Funda X')).getByRole('status')).toHaveTextContent('Agregado al carrito bajo pedido.');
    const added = tracked.mock.calls.map(([event]) => event).filter((event) => event.name === 'ADD_TO_CART');
    expect(added).toHaveLength(1);
    expect(added[0]).toMatchObject({ value: 79 });
  });

  it('sin `over_stock: true` no se ofrece', async () => {
    model.accessories = [accessoryFixture({ on_order: { lead_time_days: 7, price: '79.00' } })];
    await renderPage();
    expect(within(card('Funda X')).queryByTestId('over-stock-offer')).toBeNull();
  });

  it('un accesorio sólo bajo pedido no lo ofrece (ya es bajo pedido)', async () => {
    model.accessories = [accessoryFixture({ availability: 'supplier', on_order: overStock })];
    await renderPage();
    expect(within(card('Funda X')).queryByTestId('over-stock-offer')).toBeNull();
  });
});

describe('varias ofertas a la vez: cada botón se nombra con su accesorio', () => {
  const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

  it('las ofertas tras una negativa', async () => {
    model.accessories = [
      accessoryFixture({ id: 72, slug: 'cargador-x', name: 'Cargador X' }),
      accessoryFixture(),
    ];
    const refusal = () => reply({
      detail: 'No hay stock suficiente.', code: 'on_order_available', on_order: { lead_time_days: 3, price: '95.00' },
    }, 409);
    send.mockResolvedValueOnce(refusal()).mockResolvedValueOnce(refusal());
    await renderPage();
    for (const name of ['Cargador X', 'Funda X']) {
      await act(async () => {
        fireEvent.click(within(card(name)).getByRole('button', { name: /^Agregar al carrito/ }));
      });
    }
    expect(within(block()).getAllByTestId('on-order-offer')).toHaveLength(2);
    for (const name of ['Cargador X', 'Funda X']) {
      const offer = within(card(name)).getByTestId('on-order-offer');
      const button = within(offer).getByRole('button');
      const visible = button.textContent ?? '';
      expect(visible).toBe('Pedir bajo pedido — entrega en 3 días');
      // El texto visible al comienzo del nombre accesible (WCAG 2.5.3), y el nombre del accesorio en él.
      expect(button).toHaveAccessibleName(new RegExp(`^${escape(visible)}.*${escape(name)}`));
      expect(screen.getAllByRole('button', { name: new RegExp(escape(name)) }).filter((b) => b === button)).toHaveLength(1);
    }
    const names = within(block()).getAllByRole('button', { name: /^Pedir bajo pedido — entrega en 3 días/ })
      .map((button) => button.getAttribute('aria-label'));
    expect(new Set(names).size).toBe(2);
    // Cada botón pide el suyo.
    await act(async () => {
      fireEvent.click(within(within(card('Funda X')).getByTestId('on-order-offer')).getByRole('button'));
    });
    expect(sentBodies()[2]).toMatchObject({ product: 71, on_order: true });
  });

  it('las opciones de pedir al proveedor', async () => {
    const overStock = { lead_time_days: 7, price: '79.00', over_stock: true };
    model.accessories = [
      accessoryFixture({ id: 72, slug: 'cargador-x', name: 'Cargador X', on_order: overStock }),
      accessoryFixture({ on_order: overStock }),
    ];
    await renderPage();
    for (const name of ['Cargador X', 'Funda X']) {
      const button = within(within(card(name)).getByTestId('over-stock-offer')).getByRole('button');
      const visible = button.textContent ?? '';
      expect(button).toHaveAccessibleName(new RegExp(`^${escape(visible)}.*${escape(name)}`));
    }
  });
});

describe('una negativa porque el stock cambió vuelve a leer el modelo', () => {
  it('«unit_taken»: la tarjeta deja de decir «En stock»', async () => {
    model.accessories = [accessoryFixture()];
    send.mockResolvedValueOnce(reply({ detail: 'Otra persona acaba de apartar ese equipo.', code: 'unit_taken' }, 409));
    await renderPage();
    const readsBefore = reads.mock.calls.length;
    expect(within(card('Funda X')).getByText('En stock')).toBeInTheDocument();
    // Lo que el servidor dirá en la nueva lectura.
    model = { ...model, accessories: [accessoryFixture({ available: false, availability: 'none' })] };
    await act(async () => {
      fireEvent.click(within(card('Funda X')).getByRole('button', { name: /^Agregar al carrito/ }));
    });
    expect(reads.mock.calls.length).toBeGreaterThan(readsBefore);
    await waitFor(() => expect(within(card('Funda X')).queryByText('En stock')).toBeNull());
    expect(within(card('Funda X')).getAllByText('Agotado').length).toBeGreaterThan(0);
    expect(within(card('Funda X')).getByRole('alert')).toHaveTextContent('Otra persona acaba de apartar ese equipo.');
  });

  it('otra negativa no vuelve a leer', async () => {
    model.accessories = [accessoryFixture()];
    send.mockResolvedValueOnce(reply({ detail: 'No se pudo.', code: 'too_many' }, 409));
    await renderPage();
    const readsBefore = reads.mock.calls.length;
    await act(async () => {
      fireEvent.click(within(card('Funda X')).getByRole('button', { name: /^Agregar al carrito/ }));
    });
    expect(reads.mock.calls.length).toBe(readsBefore);
  });
});

describe('carrito: una variante y un accesorio', () => {
  it('se agregan los dos, cada uno con su producto y su aviso', async () => {
    model.accessories = [accessoryFixture()];
    await renderPage();
    const summary = screen.getByTestId('v4-summary');
    await act(async () => {
      fireEvent.click(within(summary).getByRole('button', { name: 'Agregar al carrito' }));
    });
    await act(async () => {
      fireEvent.click(within(card('Funda X')).getByRole('button', { name: /^Agregar al carrito/ }));
    });
    expect(sentBodies().map((body) => body.product)).toEqual([41, 71]);
    expect(within(summary).getByRole('status')).toHaveTextContent('Agregado al carrito.');
    expect(within(card('Funda X')).getByRole('status')).toHaveTextContent('Agregado al carrito.');
    // Y la variante se puede volver a agregar después.
    await act(async () => {
      fireEvent.click(within(summary).getByRole('button', { name: 'Agregar al carrito' }));
    });
    expect(sentBodies().map((body) => body.product)).toEqual([41, 71, 41]);
  });

  it('el bloque también está en la línea de seminuevos', async () => {
    model.accessories = [accessoryFixture()];
    await renderPage('used');
    expect(block()).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Selectores por rol
// ---------------------------------------------------------------------------

type Role = 'capacity' | 'color' | 'sim' | 'region' | 'other';

function roleModel({ simDiffers, regionDiffers }: { simDiffers: boolean; regionDiffers: boolean }) {
  const attribute = (id: number, name: string, role: Role, sort_order: number, labels: [number, string][], kind = 'text') => ({
    id, name, kind, role, sort_order,
    options: labels.map(([optionId, label], index) => ({ id: optionId, label, swatch_hex: kind === 'color' ? '#000000' : '', sort_order: index })),
  });
  const variant = (product_id: number, options: Record<string, number>, price: string) => ({
    product_id, slug: `equipo-x-${product_id}`, name: `Equipo X ${product_id}`, options, price,
    image_url: '', images: [], available: true, availability: 'own_stock', sells_by_unit: true,
  });
  // `sort_order` contradicts the roles on purpose: the page reads the roles.
  const base = modelFixture();
  return {
    ...base,
    attributes: [
      attribute(6, 'Región', 'region', 0, [[41, 'Japón'], [42, 'EE. UU.']]),
      attribute(7, 'Pantalla', 'other', 1, [[51, 'Estándar']]),
      attribute(5, 'SIM', 'sim', 2, [[31, 'eSIM'], [32, 'Física']]),
      attribute(4, 'Color', 'color', 3, [[21, 'Azul'], [22, 'Blanco']], 'color'),
      attribute(3, 'Capacidad', 'capacity', 4, [[11, '128 GB'], [12, '256 GB']]),
    ],
    lines: { new: { label: base.lines.new.label, count: 3 }, used: { label: base.lines.used.label, count: 0 } },
    variants: [
      variant(81, { 3: 11, 4: 21, 5: 31, 6: 41, 7: 51 }, '3000.00'),
      variant(82, { 3: 12, 4: 21, 5: simDiffers ? 32 : 31, 6: regionDiffers ? 42 : 41, 7: 51 }, '3500.00'),
      variant(83, { 3: 11, 4: 22, 5: 31, 6: 41, 7: 51 }, '3100.00'),
    ],
    used_units: [],
  };
}

function pickerNames(): string[] {
  return screen.getAllByRole('radiogroup').map((group) => {
    const label = document.getElementById(group.getAttribute('aria-labelledby') ?? '');
    return label?.firstChild?.textContent?.trim() ?? '';
  });
}

describe('selectores en el orden de los roles', () => {
  it('capacidad, color, SIM, región y después los demás', async () => {
    model = roleModel({ simDiffers: true, regionDiffers: true }) as unknown as Model;
    await renderPage();
    expect(pickerNames()).toEqual(['Capacidad', 'Color', 'SIM', 'Región', 'Pantalla']);
  });

  it('SIM y región no aparecen cuando las variantes no cambian en ellos', async () => {
    model = roleModel({ simDiffers: false, regionDiffers: false }) as unknown as Model;
    await renderPage();
    expect(pickerNames()).toEqual(['Capacidad', 'Color', 'Pantalla']);
    // La elección sigue llegando a su variante.
    fireEvent.click(within(screen.getByRole('radiogroup', { name: 'Capacidad' })).getByRole('radio', { name: '256 GB' }));
    expect(screen.getByTestId('v4-price')).toHaveTextContent('S/ 3500.00');
    await act(async () => {
      fireEvent.click(within(screen.getByTestId('v4-summary')).getByRole('button', { name: 'Agregar al carrito' }));
    });
    expect(sentBodies()[0]).toMatchObject({ product: 82, quantity: 1 });
  });

  it('sólo la que cambia aparece', async () => {
    model = roleModel({ simDiffers: true, regionDiffers: false }) as unknown as Model;
    await renderPage();
    expect(pickerNames()).toEqual(['Capacidad', 'Color', 'SIM', 'Pantalla']);
  });

  it('un servidor que aún no manda roles: el orden de `sort_order`, como siempre', async () => {
    await renderPage();
    expect(pickerNames()).toEqual(['Condición', 'Capacidad', 'Color']);
  });
});

describe('el orden de los roles, en un solo sitio', () => {
  it('es el mismo que el del panel', () => {
    expect([...ATTRIBUTE_ROLE_ORDER]).toEqual(ROLE_ORDER);
  });

  it('roleOrderedAttributes: por rol, y dentro del mismo rol por sort_order; sin roles, igual que sortedAttributes', () => {
    const a = (id: number, sort_order: number, role?: string) =>
      ({ id, name: `A${id}`, kind: 'text', sort_order, options: [], ...(role ? { role } : {}) }) as ModelAttribute;
    expect(roleOrderedAttributes([a(1, 0, 'other'), a(2, 5, 'region'), a(3, 1, 'other'), a(4, 9, 'capacity')]).map((x) => x.id))
      .toEqual([4, 2, 1, 3]);
    // Un rol desconocido cuenta como «otro».
    expect(roleOrderedAttributes([a(1, 0, 'mystery'), a(2, 1, 'sim')]).map((x) => x.id)).toEqual([2, 1]);
    const plain = [a(1, 2), a(2, 0), a(3, 1)];
    expect(roleOrderedAttributes(plain).map((x) => x.id)).toEqual(sortedAttributes(plain).map((x) => x.id));
  });
});
