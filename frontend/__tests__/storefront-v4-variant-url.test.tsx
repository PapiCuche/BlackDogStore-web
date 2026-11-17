import { act, fireEvent, render, screen, within } from '@testing-library/react';
import fs from 'fs';
import path from 'path';

import ModelDetail from '@/app/components/ModelDetail';
import { ModelConfiguratorV4 } from '@/app/components/v4/ModelConfiguratorV4';
import { StorefrontProvider } from '@/app/components/StorefrontProvider';
import { selectionForVariant, sortedAttributes, type CatalogModel } from '@/app/lib/catalog-models';
import { NEUTRAL_CONFIG } from '@/app/lib/storefront';

import { LINE_NEW, LINE_USED, modelFixture, reply } from './support/device-commerce-fixtures';

/**
 * V4-VARIANT-URL — en el navegador.
 *
 *  · la variante que manda el servidor es la elegida desde el primer render;
 *  · cada vez que la elección cambia, la dirección dice la variante nueva con
 *    `router.replace` (sin entrada nueva en el historial, sin saltar arriba),
 *    conservando las demás consultas (UTM);
 *  · al entrar no se reescribe nada;
 *  · la V3 no toca la dirección.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));

const replace = jest.fn();
const push = jest.fn();
jest.mock('next/navigation', () => ({
  useRouter: () => ({ replace, push, refresh: jest.fn(), back: jest.fn(), forward: jest.fn(), prefetch: jest.fn() }),
  usePathname: () => window.location.pathname,
  useSearchParams: () => new URLSearchParams(window.location.search),
}));

const reads = jest.fn();
let model: ReturnType<typeof modelFixture>;

beforeEach(() => {
  model = modelFixture();
  reads.mockReset();
  reads.mockImplementation((input: RequestInfo | URL) =>
    Promise.resolve(String(input).includes('/catalog/models/equipo-x') ? reply(model) : reply({}, 404)));
  global.fetch = reads as unknown as typeof fetch;
  replace.mockReset();
  push.mockReset();
  window.history.replaceState(null, '', '/v4/modelo/equipo-x?utm_source=ig&utm_medium=social');
});

async function renderV4(initialVariant?: string) {
  render(
    <StorefrontProvider config={NEUTRAL_CONFIG}>
      <ModelConfiguratorV4 slug="equipo-x" initialModel={modelFixture() as never} initialVariant={initialVariant} />
    </StorefrontProvider>,
  );
  await act(async () => {});
}

const summary = () => screen.getByTestId('v4-summary');
const price = () => within(summary()).getByTestId('v4-price');
const capacity = () => screen.getByRole('radiogroup', { name: 'Capacidad' });
const colour = () => screen.getByRole('radiogroup', { name: /^Color/ });
const chip = (group: HTMLElement, name: RegExp | string) => within(group).getByRole('radio', { name });

describe('la variante del servidor', () => {
  it('es la elegida desde el primer render', () => {
    render(
      <StorefrontProvider config={NEUTRAL_CONFIG}>
        <ModelConfiguratorV4 slug="equipo-x" initialModel={modelFixture() as never} initialVariant="equipo-x-43" />
      </StorefrontProvider>,
    );
    expect(price()).toHaveTextContent('S/ 4499.00');
    expect(chip(capacity(), '256 GB')).toHaveAttribute('aria-checked', 'true');
    expect(chip(colour(), 'Azul')).toHaveAttribute('aria-checked', 'true');
  });

  it('una que no es de este modelo deja la elección de siempre', async () => {
    await renderV4('equipo-y-43');
    expect(price()).toHaveTextContent('S/ 3999.00');
  });

  it('al entrar no se reescribe la dirección', async () => {
    await renderV4('equipo-x-43');
    await renderV4();
    expect(replace).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
  });
});

describe('cambiar la elección cambia la dirección', () => {
  it('con replace, sin saltar arriba, conservando las UTM', async () => {
    await renderV4();
    fireEvent.click(chip(capacity(), '512 GB'));
    expect(price()).toHaveTextContent('S/ 5499.00');
    expect(replace).toHaveBeenCalledTimes(1);
    expect(replace).toHaveBeenCalledWith(
      '/v4/modelo/equipo-x?utm_source=ig&utm_medium=social&variante=equipo-x-45', { scroll: false },
    );
    expect(push).not.toHaveBeenCalled();
  });

  it('reemplaza el valor que ya estaba, en su sitio, y conserva el ancla', async () => {
    window.history.replaceState(null, '', '/v4/modelo/equipo-x?utm_source=ig&variante=equipo-x-43&utm_campaign=x#fotos');
    await renderV4('equipo-x-43');
    fireEvent.click(chip(colour(), /^Blanco/));
    expect(replace).toHaveBeenLastCalledWith(
      '/v4/modelo/equipo-x?utm_source=ig&variante=equipo-x-44&utm_campaign=x#fotos', { scroll: false },
    );
  });

  it('elegir la misma opción otra vez no reescribe', async () => {
    window.history.replaceState(null, '', '/v4/modelo/equipo-x?variante=equipo-x-43');
    await renderV4('equipo-x-43');
    fireEvent.click(chip(capacity(), '256 GB'));
    expect(replace).not.toHaveBeenCalled();
  });

  it('al volver de la línea de seminuevos sigue la última variante elegida', async () => {
    await renderV4();
    fireEvent.click(chip(capacity(), '512 GB'));
    const line = screen.getByRole('radiogroup', { name: 'Condición' });
    fireEvent.click(within(line).getByRole('radio', { name: LINE_USED }));
    fireEvent.click(within(line).getByRole('radio', { name: LINE_NEW }));
    expect(price()).toHaveTextContent('S/ 5499.00');
    expect(replace).toHaveBeenCalledTimes(1);
  });
});

describe('selectionForVariant', () => {
  const attributes = () => sortedAttributes((modelFixture() as unknown as CatalogModel).attributes);
  const variants = () => (modelFixture() as unknown as CatalogModel).variants;

  it('las opciones de la variante con ese slug', () => {
    expect(selectionForVariant(variants(), attributes(), 'equipo-x-45')).toEqual({ '3': 13, '4': 23 });
  });

  it.each([undefined, '', 'no-existe', 'EQUIPO-X-45'])('nada para %p', (slug) => {
    expect(selectionForVariant(variants(), attributes(), slug)).toBeNull();
  });

  it('nada si otra variante con las mismas opciones va antes (no se podría elegir)', () => {
    const list = variants();
    list.push({ ...list[0], slug: 'gemela' });
    expect(selectionForVariant(list, attributes(), 'gemela')).toBeNull();
  });
});

describe('la V3 no cambia', () => {
  it('elegir una opción en la ficha V3 no toca la dirección', async () => {
    window.history.replaceState(null, '', '/modelo/equipo-x?utm_source=ig');
    render(<ModelDetail slug="equipo-x" />);
    await screen.findByRole('heading', { level: 1, name: 'Equipo X' });
    fireEvent.click(within(screen.getByRole('radiogroup', { name: 'Capacidad' })).getByRole('radio', { name: /512 GB/ }));
    expect(replace).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
    expect(window.location.search).toBe('?utm_source=ig');
  });

  it('ni la ficha V3 ni su página leen ?variante=', () => {
    for (const file of ['app/components/ModelDetail.tsx', 'app/modelo/[slug]/page.tsx']) {
      expect(fs.readFileSync(path.join(process.cwd(), file), 'utf8')).not.toMatch(/variante|initialVariant/);
    }
  });
});
