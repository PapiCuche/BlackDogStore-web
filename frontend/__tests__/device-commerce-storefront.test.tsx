import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import CartPage from '@/app/cart/page';
import { CartItemCard, cartLineAmount } from '@/app/components/CartItemCard';
import { CatalogModels } from '@/app/components/CatalogModels';
import { UsedUnitCard } from '@/app/components/UsedUnitCard';
import { fetchWithAuth } from '@/app/lib/auth';

import { LINE_NEW, LINE_USED, modelFixture, reply } from './support/device-commerce-fixtures';

/**
 * DEVICE-COMMERCE-01 · DC2 — la tarjeta de un seminuevo, el carrito por equipo
 * y las pestañas de línea de una categoría.
 */

jest.mock('@/app/lib/auth', () => ({
  fetchWithAuth: jest.fn(),
  getCurrentUser: jest.fn(() => Promise.resolve(null)),
}));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;
const reads = jest.fn();

beforeEach(() => {
  send.mockReset();
  reads.mockReset();
  global.fetch = reads as unknown as typeof fetch;
});

describe('tarjeta de un equipo seminuevo', () => {
  const [sinLectura, conLectura] = modelFixture().used_units;

  it('una batería sin lectura dice «no informado», nunca un número', () => {
    const { container } = render(<UsedUnitCard unit={sinLectura} modelName="Equipo X" attributeOrder={['Capacidad', 'Color']} onBuy={jest.fn()} />);
    expect(screen.getByText('Batería: no informado')).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/Batería: \d/);
    expect(screen.getByText('Estado: Excelente')).toBeInTheDocument();
    expect(screen.getByText('Sin marcas visibles.')).toBeInTheDocument();
    expect(screen.getByText('Incluye caja.')).toBeInTheDocument();
    expect(screen.getByText('S/ 2599.00')).toBeInTheDocument();
  });

  it('con lectura, el porcentaje del servidor', () => {
    render(<UsedUnitCard unit={conLectura} modelName="Equipo X" onBuy={jest.fn()} />);
    expect(screen.getByText('Batería: 87%')).toBeInTheDocument();
  });

  it('del IMEI sólo los últimos dígitos que el servidor publicó', () => {
    const { container } = render(<UsedUnitCard unit={sinLectura} modelName="Equipo X" onBuy={jest.fn()} />);
    expect(screen.getByText('IMEI termina en 1234')).toBeInTheDocument();
    // No other run of digits that could be an identifier.
    expect(container.textContent).not.toMatch(/\d{5,}/);
  });

  it('la galería se abre a pedido', () => {
    render(<UsedUnitCard unit={conLectura} modelName="Equipo X" onBuy={jest.fn()} />);
    expect(screen.queryByRole('group', { name: 'Fotos del equipo' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Ver las 2 fotos' }));
    fireEvent.click(screen.getByRole('button', { name: 'Ver foto 2 de 2' }));
    expect(screen.getAllByRole('img')[0]).toHaveAttribute('src', 'http://localhost/u2-b.png');
  });

  it('«Comprar este equipo» entrega ese equipo', () => {
    const onBuy = jest.fn();
    render(<UsedUnitCard unit={sinLectura} modelName="Equipo X" onBuy={onBuy} />);
    fireEvent.click(screen.getByRole('button', { name: 'Comprar este equipo' }));
    expect(onBuy).toHaveBeenCalledWith(sinLectura);
  });
});

const UNITS = [
  {
    id: 'u-1', price: '2599.00', options: { Capacidad: '128 GB', Color: 'Azul' }, battery_health: null,
    cosmetic_grade: 'Excelente', imei_hint: '•••• 1234', held_until: '2026-10-14T15:45:00Z',
  },
  {
    id: 'u-2', price: '2899.50', options: { Capacidad: '128 GB', Color: 'Azul' }, battery_health: 91,
    cosmetic_grade: '', imei_hint: '•••• 5678', held_until: '2026-10-14T15:30:00Z',
  },
];
const BY_UNIT = {
  id: 9, quantity: 2, available: true, sells_by_unit: true, line_total: '5498.50',
  product: { id: 52, name: 'Equipo X seminuevo', price: '2599.00', slug: 'equipo-x-52', image_url: '' },
  units: UNITS,
};
const PLAIN = {
  id: 7, quantity: 2, available: true, sells_by_unit: false, line_total: '100.00',
  product: { id: 3, name: 'Funda', price: '50.00', slug: 'funda', image_url: '' },
};

describe('línea del carrito vendida por equipo', () => {
  it('usa `line_total`, no precio × cantidad, y no ofrece cantidad', () => {
    render(<CartItemCard {...BY_UNIT} onRemove={jest.fn()} onReleaseUnit={jest.fn()} />);
    expect(screen.getByTestId('cart-line-total')).toHaveTextContent('S/ 5498.50');
    expect(screen.queryByText('S/ 5198.00')).toBeNull();
    expect(screen.queryByRole('spinbutton')).toBeNull();
    expect(screen.getByText('2 equipos')).toBeInTheDocument();
  });

  it('cada equipo es una fila con lo suyo', () => {
    render(<CartItemCard {...BY_UNIT} onRemove={jest.fn()} onReleaseUnit={jest.fn()} />);
    const rows = within(screen.getByRole('list', { name: 'Equipos de Equipo X seminuevo' })).getAllByRole('listitem');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('128 GB · Azul');
    expect(rows[0]).toHaveTextContent('Batería: no informado');
    expect(rows[0]).toHaveTextContent('Estado: Excelente');
    expect(rows[0]).toHaveTextContent('IMEI termina en 1234');
    expect(rows[0]).toHaveTextContent('S/ 2599.00');
    expect(rows[1]).toHaveTextContent('Batería: 91%');
    expect(rows[1]).toHaveTextContent('S/ 2899.50');
    expect(rows[1]).not.toHaveTextContent('Estado:');
  });

  it('dice hasta cuándo está reservado, con la reserva que vence primero', () => {
    render(<CartItemCard {...BY_UNIT} />);
    const earliest = new Date('2026-10-14T15:30:00Z').toLocaleTimeString('es-PE', { hour: '2-digit', minute: '2-digit', hour12: false });
    expect(screen.getByText(`Reservado para ti hasta ${earliest}`)).toBeInTheDocument();
  });

  it('«Quitar» suelta ese equipo', () => {
    const onReleaseUnit = jest.fn();
    render(<CartItemCard {...BY_UNIT} onReleaseUnit={onReleaseUnit} />);
    fireEvent.click(screen.getByRole('button', { name: 'Quitar IMEI termina en 5678' }));
    expect(onReleaseUnit).toHaveBeenCalledWith('u-2');
  });

  it('el importe de una línea: `line_total` si llega, precio × cantidad si no', () => {
    expect(cartLineAmount({ quantity: 2, line_total: '5498.50', product: { price: '2599.00' } })).toBe(5498.5);
    expect(cartLineAmount({ quantity: 3, product: { price: '10.00' } })).toBe(30);
  });
});

describe('el carrito con una línea por equipo', () => {
  function cartIs(lines: unknown[]) {
    reads.mockImplementation((input: RequestInfo | URL) => Promise.resolve(reply(String(input).includes('/cart/') ? lines : [])));
  }

  it('el subtotal suma `line_total` de cada línea', async () => {
    cartIs([BY_UNIT, PLAIN]);
    render(<CartPage />);
    const subtotal = (await screen.findByText('Subtotal')).nextElementSibling;
    expect(subtotal).toHaveTextContent('S/ 5598.50');
  });

  it('«Quitar» llama a release-unit con el equipo y vuelve a leer el carrito', async () => {
    cartIs([BY_UNIT]);
    send.mockResolvedValue(reply({ released: 1 }));
    render(<CartPage />);
    const quitar = await screen.findByRole('button', { name: 'Quitar IMEI termina en 1234' });
    await act(async () => { fireEvent.click(quitar); });
    const [url, init] = send.mock.calls[0];
    expect(String(url)).toMatch(/\/cart\/release-unit\/$/);
    expect(init?.method).toBe('POST');
    expect(JSON.parse(String(init?.body))).toMatchObject({ unit: 'u-1' });
    expect(JSON.parse(String(init?.body)).session_key).toEqual(expect.any(String));
    await waitFor(() => expect(reads.mock.calls.filter(([u]) => String(u).includes('/cart/')).length).toBe(2));
  });
});

describe('pestañas de línea en una categoría', () => {
  function modelsAre(lines: { new: number; used: number }) {
    reads.mockImplementation(() => Promise.resolve(reply({
      lines: { new: { label: LINE_NEW, count: lines.new }, used: { label: LINE_USED, count: lines.used } },
      results: [{
        slug: 'equipo-x', name: 'Equipo X', image_url: '', category: { slug: 'telefonos', name: 'Teléfonos' },
        from_price: '3999.00', has_new: lines.new > 0, has_used: lines.used > 0,
      }],
    })));
  }

  it('con las dos líneas, pestañas con los nombres de la empresa', async () => {
    modelsAre({ new: 3, used: 2 });
    const onLineChange = jest.fn();
    render(<CatalogModels category="telefonos" line="" onLineChange={onLineChange} />);
    expect(await screen.findByRole('link', { name: /Equipo X/ })).toHaveAttribute('href', '/modelo/equipo-x');
    expect(screen.getByRole('button', { name: LINE_NEW })).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(screen.getByRole('button', { name: LINE_USED }));
    expect(onLineChange).toHaveBeenCalledWith('used');
    expect(String(reads.mock.calls[0][0])).toContain('category=telefonos');
    expect(screen.getByText('S/ 3999.00')).toBeInTheDocument();
  });

  it('con una sola línea, no hay pestañas', async () => {
    modelsAre({ new: 3, used: 0 });
    render(<CatalogModels category="telefonos" line="" onLineChange={jest.fn()} />);
    await screen.findByRole('link', { name: /Equipo X/ });
    expect(screen.queryByRole('button', { name: LINE_NEW })).toBeNull();
    expect(screen.queryByRole('button', { name: LINE_USED })).toBeNull();
  });

  it('la línea elegida viaja en la petición y en el enlace', async () => {
    modelsAre({ new: 3, used: 2 });
    render(<CatalogModels category="telefonos" line="used" onLineChange={jest.fn()} />);
    expect(await screen.findByRole('link', { name: /Equipo X/ })).toHaveAttribute('href', '/modelo/equipo-x?line=used');
    expect(String(reads.mock.calls[0][0])).toContain('line=used');
  });

  it('una categoría sin modelos no pinta nada', async () => {
    reads.mockImplementation(() => Promise.resolve(reply({ lines: { new: { label: LINE_NEW, count: 0 }, used: { label: LINE_USED, count: 0 } }, results: [] })));
    const onLoaded = jest.fn();
    const { container } = render(<CatalogModels category="accesorios" line="" onLineChange={jest.fn()} onLoaded={onLoaded} />);
    await waitFor(() => expect(onLoaded).toHaveBeenCalledWith(false));
    expect(container).toBeEmptyDOMElement();
  });
});
