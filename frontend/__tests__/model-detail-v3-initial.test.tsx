import { act, render, screen, waitFor } from '@testing-library/react';

import ModelDetail from '@/app/components/ModelDetail';
import type { CatalogModel } from '@/app/lib/catalog-models';

import { modelFixture, reply } from './support/device-commerce-fixtures';

/**
 * SEO-AUDIT #2 — la ficha V3 recibe el modelo leído en el servidor (`initialModel`).
 *
 * Lo que se ve tras hidratar no cambia: con el primer dato, el marcado es EXACTAMENTE el de la ficha cargada
 * por el navegador (las instantáneas de `model-detail-v3-markup.test.tsx` siguen valiendo). No hay esqueleto
 * ni parpadeo, y el navegador sigue releyendo el modelo en silencio: precios y stock cambian mientras alguien
 * mira.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const reads = jest.fn();

beforeEach(() => {
  reads.mockReset();
  global.fetch = reads as unknown as typeof fetch;
  window.localStorage.clear();
});

const fixture = () => modelFixture() as unknown as CatalogModel;

/** `useId()` numbers differ between two mounts in one document; everything else must be identical. */
const withoutIds = (html: string) => html.replace(/_r_[0-9a-z]+_/g, '_r_');

async function loadedByTheBrowser(initialLine?: string) {
  reads.mockResolvedValue(reply(modelFixture()));
  const view = render(<ModelDetail slug="equipo-x" initialLine={initialLine} />);
  await screen.findByRole('heading', { level: 1, name: 'Equipo X' });
  const html = withoutIds(view.container.innerHTML);
  view.unmount();
  return html;
}

describe('ModelDetail con el modelo del servidor', () => {
  it.each([undefined, 'used'])('line=%s: el primer render es la ficha cargada, sin esqueleto', async (line) => {
    const expected = await loadedByTheBrowser(line);
    reads.mockReset();
    reads.mockImplementation(() => new Promise(() => {}));
    const { container } = render(<ModelDetail slug="equipo-x" initialLine={line} initialModel={fixture()} />);
    expect(screen.queryByLabelText('Cargando modelo')).toBeNull();
    expect(withoutIds(container.innerHTML)).toBe(expected);
  });

  it('el navegador vuelve a leer el modelo y muestra el precio nuevo', async () => {
    const fresh = modelFixture();
    fresh.variants[0].price = '3899.00';
    reads.mockResolvedValue(reply(fresh));
    render(<ModelDetail slug="equipo-x" initialModel={fixture()} />);
    expect(screen.getByTestId('model-price')).toHaveTextContent('S/ 3999.00');
    await act(async () => {});
    await waitFor(() => expect(screen.getByTestId('model-price')).toHaveTextContent('S/ 3899.00'));
    expect(reads).toHaveBeenCalledTimes(1);
    expect(String(reads.mock.calls[0][0])).toContain('/catalog/models/equipo-x');
  });

  it('si esa relectura falla, la ficha del servidor se queda y se avisa del error', async () => {
    reads.mockRejectedValue(new Error('down'));
    render(<ModelDetail slug="equipo-x" initialModel={fixture()} />);
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: 'Equipo X' })).toBeInTheDocument();
  });
});
