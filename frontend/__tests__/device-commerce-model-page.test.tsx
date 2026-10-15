import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import ModelDetail from '@/app/components/ModelDetail';
import { availabilityLabel, initialSelection, sortedAttributes } from '@/app/lib/catalog-models';
import { fetchWithAuth } from '@/app/lib/auth';

import { LINE_NEW, LINE_USED, modelFixture, reply } from './support/device-commerce-fixtures';

/**
 * DEVICE-COMMERCE-01 · DC2 — la página de un modelo.
 *
 * Línea de nuevos: un selector por atributo; la combinación elegida es una
 * variante, y su precio, su foto y su disponibilidad la siguen. Lo que no lleva
 * a nada disponible se VE deshabilitado, no desaparece. Línea de seminuevos:
 * cada tarjeta es un equipo, y comprarlo manda ese equipo.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

let model: ReturnType<typeof modelFixture>;
const reads = jest.fn();

beforeEach(() => {
  model = modelFixture();
  reads.mockReset();
  global.fetch = reads.mockImplementation((input: RequestInfo | URL) => {
    if (String(input).includes('/catalog/models/equipo-x')) return Promise.resolve(reply(model));
    return Promise.resolve(reply({}, 404));
  }) as unknown as typeof fetch;
  send.mockReset();
  send.mockResolvedValue(reply({ id: 1 }, 201));
  window.localStorage.clear();
});

const price = () => screen.getByTestId('model-price').textContent;
const mainImage = () => within(screen.getByTestId('model-main-image')).getByRole('img');
const capacity = () => screen.getByRole('radiogroup', { name: 'Capacidad' });
const colour = () => screen.getByRole('radiogroup', { name: /^Color/ });

async function renderPage(initialLine?: string) {
  render(<ModelDetail slug="equipo-x" initialLine={initialLine} />);
  await screen.findByRole('heading', { level: 1, name: 'Equipo X' });
}

describe('selección de variante', () => {
  it('preselecciona la primera combinación disponible, con su precio y su foto', async () => {
    await renderPage();
    expect(within(capacity()).getByRole('radio', { name: '128 GB' })).toHaveAttribute('aria-checked', 'true');
    expect(within(colour()).getByRole('radio', { name: 'Azul' })).toHaveAttribute('aria-checked', 'true');
    expect(price()).toBe('S/ 3999.00');
    expect(mainImage()).toHaveAttribute('src', 'http://localhost/azul-128.png');
    expect(screen.getByTestId('model-availability')).toHaveTextContent('En stock');
  });

  it('el precio y la galería cambian con la capacidad', async () => {
    await renderPage();
    fireEvent.click(within(capacity()).getByRole('radio', { name: '256 GB' }));
    expect(price()).toBe('S/ 4499.00');
    expect(mainImage()).toHaveAttribute('src', 'http://localhost/azul-256-a.png');
    // Two photos: the second one can be chosen.
    fireEvent.click(screen.getByRole('button', { name: 'Ver imagen 2 de 2' }));
    expect(mainImage()).toHaveAttribute('src', 'http://localhost/azul-256-b.png');
  });

  it('una combinación sin stock o inexistente se ve deshabilitada y lo dice', async () => {
    await renderPage();
    // Rojo no existe en 128 GB.
    const rojo = within(colour()).getByRole('radio', { name: 'Rojo, no disponible' });
    expect(rojo).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(rojo);
    expect(price()).toBe('S/ 3999.00');

    // Blanco 256 GB existe pero no está disponible.
    fireEvent.click(within(capacity()).getByRole('radio', { name: '256 GB' }));
    expect(within(colour()).getByRole('radio', { name: 'Blanco, no disponible' })).toHaveAttribute('aria-disabled', 'true');
    expect(within(colour()).getByRole('radio', { name: 'Azul' })).not.toHaveAttribute('aria-disabled');
  });

  it('una capacidad que sólo existe en otro color reajusta el color en vez de quedar inalcanzable', async () => {
    await renderPage();
    fireEvent.click(within(capacity()).getByRole('radio', { name: '512 GB' }));
    expect(within(colour()).getByRole('radio', { name: 'Rojo' })).toHaveAttribute('aria-checked', 'true');
    expect(price()).toBe('S/ 5499.00');
    expect(mainImage()).toHaveAttribute('src', 'http://localhost/rojo-512.png');
  });

  it('los colores son muestras con nombre accesible, y el elegido se escribe', async () => {
    await renderPage();
    const swatches = within(colour()).getAllByRole('radio');
    expect(swatches.map((s) => s.getAttribute('aria-label'))).toEqual(['Azul', 'Blanco', 'Rojo, no disponible']);
    expect(colour()).toHaveAccessibleName('Color Azul');
    fireEvent.click(within(colour()).getByRole('radio', { name: 'Blanco' }));
    expect(colour()).toHaveAccessibleName('Color Blanco');
    expect(mainImage()).toHaveAttribute('src', 'http://localhost/blanco-128.png');
  });

  it('se maneja con el teclado: una sola parada de tabulación y flechas que saltan lo deshabilitado', async () => {
    await renderPage();
    const radios = within(capacity()).getAllByRole('radio');
    expect(radios.map((r) => r.tabIndex)).toEqual([0, -1, -1]);
    radios[0].focus();
    fireEvent.keyDown(radios[0], { key: 'ArrowRight' });
    expect(radios[1]).toHaveAttribute('aria-checked', 'true');
    expect(radios[1]).toHaveFocus();
    expect(price()).toBe('S/ 4499.00');

    // In 256 GB, Blanco is disabled: from Azul, the arrow goes past it (wraps to Azul, the only one).
    const colours = within(colour()).getAllByRole('radio');
    colours[0].focus();
    fireEvent.keyDown(colours[0], { key: 'ArrowRight' });
    expect(colours[0]).toHaveAttribute('aria-checked', 'true');
    expect(colours[1]).toHaveAttribute('aria-checked', 'false');
  });

  it('«Agregar al carrito» manda la variante elegida, sin equipo', async () => {
    await renderPage();
    fireEvent.click(within(capacity()).getByRole('radio', { name: '256 GB' }));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Agregar al carrito' })); });
    expect(send).toHaveBeenCalledTimes(1);
    const [url, init] = send.mock.calls[0];
    expect(String(url)).toMatch(/\/cart\/add\/$/);
    const body = JSON.parse(String(init?.body));
    expect(body).toMatchObject({ product: 43, quantity: 1 });
    expect(body).not.toHaveProperty('unit');
    expect(body.session_key).toMatch(/^guest-/);
    expect(await screen.findByRole('status')).toHaveTextContent('Agregado al carrito.');
  });

  it('la negativa del servidor se lee en una alerta, con sus palabras', async () => {
    send.mockResolvedValueOnce(reply({ detail: 'Ya no quedan equipos de esta opción.', code: 'sold_out' }, 409));
    await renderPage();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Agregar al carrito' })); });
    expect(await screen.findByRole('alert')).toHaveTextContent('Ya no quedan equipos de esta opción.');
  });

  it('las reglas de presentación están en un solo sitio', () => {
    expect(availabilityLabel({ available: true, availability: 'own_stock' })).toBe('En stock');
    expect(availabilityLabel({ available: false, availability: 'none' })).toBe('Sin stock');
    const attributes = sortedAttributes(modelFixture().attributes as never);
    expect(attributes.map((a) => a.name)).toEqual(['Capacidad', 'Color']);
    expect(initialSelection(modelFixture().variants as never, attributes)).toEqual({ '3': 11, '4': 21 });
  });
});

describe('las dos líneas', () => {
  it('se ofrecen como pestañas con el nombre que les da la empresa', async () => {
    await renderPage();
    const tabs = screen.getAllByRole('tab');
    expect(tabs.map((t) => t.textContent)).toEqual([LINE_NEW, LINE_USED]);
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true');
    expect(screen.queryByText('Nuevos')).toBeNull();
    expect(screen.queryByText('Seminuevos')).toBeNull();
  });

  it('una línea sin nada no se ofrece', async () => {
    model.lines.used.count = 0;
    model.used_units = [];
    await renderPage();
    expect(screen.queryByRole('tab')).toBeNull();
    expect(screen.getByRole('radiogroup', { name: 'Capacidad' })).toBeInTheDocument();
  });

  it('`?line=used` abre la de seminuevos', async () => {
    await renderPage('used');
    expect(screen.getByRole('tab', { name: LINE_USED })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getAllByRole('button', { name: 'Comprar este equipo' })).toHaveLength(2);
  });
});

describe('línea de seminuevos', () => {
  it('comprar un equipo manda ESE equipo', async () => {
    await renderPage();
    fireEvent.click(screen.getByRole('tab', { name: LINE_USED }));
    const card = screen.getByRole('article', { name: '256 GB · Rojo' });
    await act(async () => { fireEvent.click(within(card).getByRole('button', { name: 'Comprar este equipo' })); });
    const body = JSON.parse(String(send.mock.calls[0][1]?.body));
    expect(body).toMatchObject({ product: 53, quantity: 1, unit: '0b4c7a52-2222-4a6e-9b1c-000000000002' });
  });

  it('si otro lo tomó, se dice y se vuelve a leer el modelo', async () => {
    send.mockResolvedValueOnce(reply({ detail: 'Este equipo acaba de ser reservado por otra persona.', code: 'unit_taken' }, 409));
    await renderPage('used');
    expect(reads).toHaveBeenCalledTimes(1);
    const card = screen.getByRole('article', { name: '128 GB · Azul' });
    await act(async () => { fireEvent.click(within(card).getByRole('button', { name: 'Comprar este equipo' })); });
    expect(await screen.findByRole('alert')).toHaveTextContent('Este equipo acaba de ser reservado por otra persona.');
    await waitFor(() => expect(reads).toHaveBeenCalledTimes(2));
  });

  it('los filtros por opción dejan sólo lo que coincide', async () => {
    await renderPage('used');
    fireEvent.change(screen.getByLabelText('Color'), { target: { value: 'Rojo' } });
    expect(screen.getAllByRole('article')).toHaveLength(1);
    expect(screen.getByRole('article', { name: '256 GB · Rojo' })).toBeInTheDocument();
  });

  it('sin equipos, lo dice', async () => {
    model.used_units = [];
    await renderPage('used');
    expect(screen.getByText(/No hay equipos disponibles en esta línea/)).toBeInTheDocument();
  });
});

describe('modelo inexistente', () => {
  it('un 404 es «Modelo no encontrado», no un error', async () => {
    reads.mockImplementation(() => Promise.resolve(reply({ detail: 'No encontrado.' }, 404)));
    render(<ModelDetail slug="nada" />);
    expect(await screen.findByText('Modelo no encontrado')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
