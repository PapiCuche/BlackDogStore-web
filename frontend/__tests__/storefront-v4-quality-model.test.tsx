import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import fs from 'fs';
import path from 'path';

import { ModelConfiguratorV4 } from '@/app/components/v4/ModelConfiguratorV4';
import { StorefrontProvider } from '@/app/components/StorefrontProvider';
import { fetchWithAuth } from '@/app/lib/auth';
import { NEUTRAL_CONFIG, type StorefrontConfig } from '@/app/lib/storefront';

import { LINE_USED, modelFixture, reply } from './support/device-commerce-fixtures';

/**
 * V4-QA — accesibilidad y rendimiento móvil de la ficha V4.
 *
 *  · al elegir otra opción, un lector de pantalla oye la opción, su precio y su
 *    disponibilidad (una sola región viva, no el precio suelto sin contexto);
 *  · los filtros de seminuevos son un grupo de radios, como los demás selectores;
 *  · la tira de miniaturas deja sitio al anillo de foco (2 px + 3 px de margen)
 *    que su `overflow-x-auto` recortaría;
 *  · «Cargando modelo…» se anuncia (un `aria-label` en un `div` no se lee);
 *  · la ruta marca la página actual.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;
const reads = jest.fn();

let model: ReturnType<typeof modelFixture>;

beforeEach(() => {
  model = modelFixture();
  reads.mockReset();
  reads.mockImplementation((input: RequestInfo | URL) =>
    Promise.resolve(String(input).includes('/catalog/models/equipo-x') ? reply(model) : reply({}, 404)));
  global.fetch = reads as unknown as typeof fetch;
  send.mockReset();
  send.mockResolvedValue(reply({ id: 1 }, 201));
  window.localStorage.clear();
});

function config(): StorefrontConfig {
  return { ...NEUTRAL_CONFIG, company: { ...NEUTRAL_CONFIG.company, name: 'Tienda Norte', slug: 'tienda-norte' } };
}

async function renderPage(initialLine?: string) {
  render(
    <StorefrontProvider config={config()}>
      <ModelConfiguratorV4 slug="equipo-x" initialLine={initialLine} />
    </StorefrontProvider>,
  );
  await screen.findByRole('heading', { level: 1, name: 'Equipo X' });
}

const summary = () => screen.getByTestId('v4-summary');
const announcement = () => within(summary()).getByTestId('v4-choice-announcement');

describe('la elección se anuncia con su precio y su disponibilidad', () => {
  it('nuevos: una región viva, discreta y atómica dice opción, precio y disponibilidad', async () => {
    await renderPage();
    const live = announcement();
    expect(live).toHaveAttribute('aria-live', 'polite');
    expect(live).toHaveAttribute('aria-atomic', 'true');
    expect(live).toHaveClass('sr-only');

    fireEvent.click(within(screen.getByRole('radiogroup', { name: 'Capacidad' })).getByRole('radio', { name: '256 GB' }));
    expect(live).toHaveTextContent('Equipo X 43');
    expect(live).toHaveTextContent('S/ 4499.00');
    expect(live).toHaveTextContent('En stock');

    fireEvent.click(within(screen.getByRole('radiogroup', { name: /^Color/ })).getByRole('radio', { name: 'Blanco, agotado' }));
    expect(live).toHaveTextContent('Equipo X 44');
    expect(live).toHaveTextContent('Agotado');
  });

  it('el precio visible no es una segunda región viva (se oiría dos veces y sin contexto)', async () => {
    await renderPage();
    expect(within(summary()).getByTestId('v4-price')).not.toHaveAttribute('aria-live');
  });

  it('seminuevos: elegir un equipo anuncia ese equipo y su precio', async () => {
    await renderPage('used');
    fireEvent.click(screen.getByRole('button', { name: /256 GB · Rojo/ }));
    const live = announcement();
    expect(live).toHaveAttribute('aria-live', 'polite');
    expect(live).toHaveTextContent('256 GB · Rojo');
    expect(live).toHaveTextContent('S/ 2899.00');
    expect(within(summary()).getByTestId('v4-price')).not.toHaveAttribute('aria-live');
  });
});

describe('los filtros de seminuevos son un grupo de radios', () => {
  it('«Todas» elegida al empezar, una sola parada de tabulación y flechas que filtran', async () => {
    const user = userEvent.setup();
    await renderPage('used');
    const group = screen.getByRole('radiogroup', { name: 'Capacidad' });
    const radios = within(group).getAllByRole('radio');
    expect(radios.map((radio) => radio.textContent)).toEqual(['Todas', '128 GB', '256 GB']);
    expect(radios[0]).toHaveAttribute('aria-checked', 'true');
    expect(radios.map((radio) => radio.tabIndex)).toEqual([0, -1, -1]);

    radios[0].focus();
    await user.keyboard('{ArrowRight}');
    expect(radios[1]).toHaveFocus();
    expect(radios[1]).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByText('1 equipo')).toBeInTheDocument();
  });
});

describe('foco visible en la tira de miniaturas', () => {
  it('la tira desplazable deja 6 px alrededor de cada miniatura para el anillo', async () => {
    await renderPage();
    fireEvent.click(within(screen.getByRole('radiogroup', { name: 'Capacidad' })).getByRole('radio', { name: '256 GB' }));
    const strip = screen.getByRole('group', { name: 'Elegir foto' });
    expect(strip).toHaveClass('overflow-x-auto');
    expect(strip).toHaveClass('p-1.5');
    // Sin mover nada: el relleno se compensa con margen negativo a los lados.
    expect(strip).toHaveClass('-mx-1.5');
  });
});

describe('estados y ruta', () => {
  it('mientras se lee el modelo, «Cargando modelo…» es un estado anunciado', () => {
    reads.mockImplementation(() => new Promise(() => {}));
    render(<ModelConfiguratorV4 slug="equipo-x" />);
    expect(screen.getByRole('status')).toHaveTextContent('Cargando modelo…');
  });

  it('la ruta marca el modelo como la página actual', async () => {
    await renderPage();
    const trail = screen.getByRole('navigation', { name: 'Ruta del modelo' });
    expect(within(trail).getByText('Equipo X')).toHaveAttribute('aria-current', 'page');
  });
});

describe('toques en el móvil', () => {
  const css = fs.readFileSync(path.join(process.cwd(), 'app/v4/v4.css'), 'utf8');

  it('chips, miniaturas, equipos y botón principal no esperan al doble toque', () => {
    expect(css).toMatch(/\[data-design="v4"\] :is\(\.v4-chip, \.v4-thumb, \.v4-unit, \.v4-cta\) \{\s*touch-action: manipulation;/);
  });
});

it('la condición elegida sigue siendo un radio (sin regresión del selector de líneas)', async () => {
  await renderPage();
  expect(within(screen.getByRole('radiogroup', { name: 'Condición' })).getByRole('radio', { name: LINE_USED }))
    .toHaveAttribute('aria-checked', 'false');
});
