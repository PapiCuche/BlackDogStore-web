import { act, fireEvent, render, screen, within } from '@testing-library/react';

import ModelDetail from '@/app/components/ModelDetail';
import { fetchWithAuth } from '@/app/lib/auth';

import { modelFixture, reply } from './support/device-commerce-fixtures';

/**
 * P2 · F1 — la ficha V3 no cambia ni un nodo.
 *
 * La lógica de `ModelDetail` pasó a hooks (`lib/model-configurator.ts`) para
 * que la vista previa V4 use la misma. Estas instantáneas se tomaron con el
 * componente ANTERIOR a esa extracción: si el marcado de la V3 cambia en
 * cualquiera de sus estados —clases, atributos, textos u orden—, falla.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;
const reads = jest.fn();

type Model = ReturnType<typeof modelFixture> & { sold_out?: boolean };

function serve(model: Model | null, status = 200) {
  reads.mockImplementation((input: RequestInfo | URL) => {
    if (!String(input).includes('/catalog/models/equipo-x')) return Promise.resolve(reply({}, 404));
    if (model === null) return Promise.resolve(reply({}, status));
    return Promise.resolve(reply(model));
  });
}

beforeEach(() => {
  reads.mockReset();
  global.fetch = reads as unknown as typeof fetch;
  send.mockReset();
  send.mockResolvedValue(reply({ id: 1 }, 201));
  window.localStorage.clear();
});

async function renderLoaded(initialLine?: string) {
  const view = render(<ModelDetail slug="equipo-x" initialLine={initialLine} />);
  await screen.findByRole('heading', { level: 1, name: 'Equipo X' });
  return view;
}

const capacity = () => screen.getByRole('radiogroup', { name: 'Capacidad' });

describe('marcado de la ficha V3', () => {
  it('cargando', () => {
    reads.mockImplementation(() => new Promise(() => {}));
    const { container } = render(<ModelDetail slug="equipo-x" />);
    expect(container.innerHTML).toMatchSnapshot();
  });

  it('línea de nuevos, elección inicial', async () => {
    serve(modelFixture());
    const { container } = await renderLoaded();
    expect(container.innerHTML).toMatchSnapshot();
  });

  it('línea de nuevos, variante con galería y luego una agotada', async () => {
    serve(modelFixture());
    const { container } = await renderLoaded();
    fireEvent.click(within(capacity()).getByRole('radio', { name: '256 GB' }));
    fireEvent.click(screen.getByRole('button', { name: 'Ver imagen 2 de 2' }));
    expect(container.innerHTML).toMatchSnapshot();
    fireEvent.click(screen.getByRole('radio', { name: 'Blanco, agotado' }));
    expect(container.innerHTML).toMatchSnapshot();
  });

  it('línea de nuevos tras agregar al carrito', async () => {
    serve(modelFixture());
    const { container } = await renderLoaded();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Agregar al carrito' })); });
    await screen.findByRole('status');
    expect(container.innerHTML).toMatchSnapshot();
  });

  it('línea de nuevos con una negativa del servidor', async () => {
    serve(modelFixture());
    send.mockResolvedValueOnce(reply({ detail: 'Ya no quedan equipos de esta opción.', code: 'sold_out' }, 409));
    const { container } = await renderLoaded();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Agregar al carrito' })); });
    await screen.findByRole('alert');
    expect(container.innerHTML).toMatchSnapshot();
  });

  it('variante bajo pedido y oferta por encima del stock', async () => {
    const model = modelFixture();
    Object.assign(model.variants[3], {
      available: true, availability: 'supplier', on_order: { lead_time_days: 7, price: '4699.00' },
    });
    Object.assign(model.variants[0], { on_order: { lead_time_days: 5, price: '4100.00', over_stock: true } });
    serve(model);
    const { container } = await renderLoaded();
    expect(container.innerHTML).toMatchSnapshot();
    fireEvent.click(within(capacity()).getByRole('radio', { name: '256 GB' }));
    fireEvent.click(screen.getByRole('radio', { name: 'Blanco' }));
    expect(container.innerHTML).toMatchSnapshot();
  });

  it('el último equipo se fue y una oferta lo cubre', async () => {
    serve(modelFixture());
    send.mockResolvedValueOnce(reply({
      detail: 'Ya no hay en tienda.', code: 'on_order_available', on_order: { lead_time_days: 4, price: '4050.00' },
    }, 409));
    const { container } = await renderLoaded();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Agregar al carrito' })); });
    expect(container.innerHTML).toMatchSnapshot();
  });

  it('línea de seminuevos, con filtros y la galería de un equipo abierta', async () => {
    serve(modelFixture());
    const { container } = await renderLoaded('used');
    expect(container.innerHTML).toMatchSnapshot();
    fireEvent.click(screen.getByRole('button', { name: 'Ver las 2 fotos' }));
    fireEvent.change(screen.getByRole('combobox', { name: 'Color' }), { target: { value: 'Rojo' } });
    expect(container.innerHTML).toMatchSnapshot();
  });

  it('línea de seminuevos tras comprar un equipo', async () => {
    serve(modelFixture());
    const { container } = await renderLoaded('used');
    const buttons = screen.getAllByRole('button', { name: /Comprar este equipo/ });
    await act(async () => { fireEvent.click(buttons[0]); });
    await screen.findByRole('status');
    expect(container.innerHTML).toMatchSnapshot();
  });

  it('una sola línea, sin pestañas', async () => {
    const model = modelFixture();
    model.lines.used.count = 0;
    model.used_units = [];
    serve(model);
    const { container } = await renderLoaded();
    expect(container.innerHTML).toMatchSnapshot();
  });

  it('modelo agotado sin líneas', async () => {
    const model: Model = modelFixture();
    model.lines.new.count = 0;
    model.lines.used.count = 0;
    model.used_units = [];
    model.variants = model.variants.map((v) => ({ ...v, available: false, availability: 'none' }));
    model.sold_out = true;
    serve(model);
    const { container } = await renderLoaded();
    expect(container.innerHTML).toMatchSnapshot();
  });

  it('modelo inexistente', async () => {
    serve(null, 404);
    const { container } = render(<ModelDetail slug="equipo-x" />);
    await screen.findByText('Modelo no encontrado');
    expect(container.innerHTML).toMatchSnapshot();
  });

  it('error al leer el modelo', async () => {
    serve(null, 500);
    const { container } = render(<ModelDetail slug="equipo-x" />);
    await screen.findByRole('alert');
    expect(container.innerHTML).toMatchSnapshot();
  });
});
