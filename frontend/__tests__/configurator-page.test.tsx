import { act, fireEvent, render, screen, within } from '@testing-library/react';

import { CROSSFADE_MS, MAX_WAIT_MS } from '@/app/components/v4/CrossfadeStage';
import { ModelConfiguratorV4 } from '@/app/components/v4/ModelConfiguratorV4';
import { StorefrontProvider } from '@/app/components/StorefrontProvider';
import { fetchWithAuth } from '@/app/lib/auth';
import type { CatalogModel, Variant } from '@/app/lib/catalog-models';
import { NEUTRAL_CONFIG } from '@/app/lib/storefront';

import { modelFixture, reply } from './support/device-commerce-fixtures';

/**
 * CATALOG-REFERENCE-01 · PR D — the configurator of a model, as a buyer uses it.
 *
 *  · a colour changes the photo to THAT finish's (PR C), never another colour's; without one, «Imagen pendiente»;
 *  · the capacity stays when the colour exists in it; otherwise it moves to the nearest one and the page says so,
 *    visibly and in the polite live region;
 *  · the address says the chosen variant (`?variante=`) without navigating;
 *  · an option no variant has is shown disabled; a supplier's availability is never said as stock, and «Bajo pedido»
 *    appears only with a purchasable offer;
 *  · the previous photo stays until the next one has loaded (no blank flash), and only then leaves.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
// The browser's address, as Next's router reads it (`replaceState` included).
jest.mock('next/navigation', () => ({
  useRouter: () => ({ replace: jest.fn(), push: jest.fn(), refresh: jest.fn(), back: jest.fn(), forward: jest.fn(), prefetch: jest.fn() }),
  usePathname: () => window.location.pathname,
  useSearchParams: () => new URLSearchParams(window.location.search),
}));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const FINISH = (n: number) => `/api/storefront/images/${String(n).padStart(32, 'e')}`;
const AZUL = 21, ROJO = 23;

/** The fixture as a PR C server sends it: roles, finish pictures for Azul and Rojo, none for Blanco. */
function finishModel(): CatalogModel {
  const data = modelFixture() as unknown as CatalogModel;
  data.attributes = data.attributes.map((a) => ({ ...a, role: a.kind === 'color' ? 'color' : 'capacity' }));
  const finishes: Record<number, Variant['gallery']> = {
    [AZUL]: [
      { url: FINISH(1), alt_text: 'Equipo X Azul', is_primary: true, width: 800, height: 800 },
      { url: FINISH(2), alt_text: 'Equipo X Azul, dorso', is_primary: false, width: 800, height: 800 },
    ],
    [ROJO]: [{ url: FINISH(3), alt_text: 'Equipo X Rojo', is_primary: true, width: 800, height: 800 }],
  };
  data.finish_images = Object.fromEntries(Object.entries(finishes).map(([k, v]) => [k, v ?? []]));
  data.variants = data.variants.map((variant) => {
    const gallery = finishes[variant.options['4']] ?? [];
    return { ...variant, image_url: '', images: [], gallery, image_pending: gallery.length === 0 };
  });
  return data;
}

let model: CatalogModel;
const reads = jest.fn();

beforeEach(() => {
  model = finishModel();
  reads.mockReset();
  reads.mockImplementation(() => new Promise(() => {}));
  global.fetch = reads as unknown as typeof fetch;
  send.mockReset();
  send.mockResolvedValue(reply({ id: 1 }, 201));
  window.history.replaceState(null, '', '/modelo/equipo-x?utm_source=ig');
});

function renderPage(opts: { catalogHref?: string; initialVariant?: string } = {}) {
  return render(
    <StorefrontProvider config={NEUTRAL_CONFIG}>
      <ModelConfiguratorV4 slug="equipo-x" initialModel={model} {...opts} />
    </StorefrontProvider>,
  );
}

const capacity = () => screen.getByRole('radiogroup', { name: 'Capacidad' });
const colour = () => screen.getByRole('radiogroup', { name: /^Color/ });
const stage = () => screen.getByTestId('v4-stage');
const stageImages = () => [...stage().querySelectorAll('img')];
const visibleStageImage = () => within(stage()).getByRole('img');
const announcement = () => screen.getByTestId('v4-choice-announcement');
const row = (term: string) => within(screen.getByTestId('v4-summary')).getByText(term, { selector: 'dt' }).nextElementSibling;

/** The browser finished loading every picture now in the stage. */
async function loadAll() {
  await act(async () => { for (const image of stageImages()) fireEvent.load(image); });
}

describe('elegir un color', () => {
  it('conserva la capacidad cuando el color existe en ella', () => {
    renderPage();
    fireEvent.click(within(colour()).getByRole('radio', { name: 'Blanco' }));
    expect(within(capacity()).getByRole('radio', { name: '128 GB' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.queryByTestId('v4-choice-adjusted')).toBeNull();
  });

  it('un color de otra capacidad se puede elegir: la capacidad pasa a la más cercana y se dice', () => {
    renderPage();
    const rojo = within(colour()).getByRole('radio', { name: 'Rojo' });
    expect(rojo).not.toHaveAttribute('aria-disabled');
    fireEvent.click(rojo);
    expect(within(capacity()).getByRole('radio', { name: '512 GB' })).toHaveAttribute('aria-checked', 'true');
    const said = 'Capacidad cambió a 512 GB: 128 GB no está disponible en Rojo.';
    expect(screen.getByTestId('v4-choice-adjusted')).toHaveTextContent(said);
    expect(announcement()).toHaveTextContent(said);
    expect(announcement()).toHaveAttribute('aria-live', 'polite');
    expect(row('Opción')).toHaveTextContent('Equipo X 45');
  });

  it('la foto pasa a la del acabado elegido, con sus miniaturas', async () => {
    renderPage();
    expect(visibleStageImage()).toHaveAttribute('alt', 'Equipo X Azul');
    expect(screen.getAllByRole('button', { name: /Ver imagen \d de 2/ })).toHaveLength(2);
    fireEvent.click(within(colour()).getByRole('radio', { name: 'Rojo' }));
    await loadAll();
    expect(visibleStageImage()).toHaveAttribute('alt', 'Equipo X Rojo');
    expect(screen.queryByRole('button', { name: /Ver imagen/ })).toBeNull();
  });

  it('sin foto de ese acabado, «Imagen pendiente» al instante: nunca la de otro color', () => {
    renderPage();
    fireEvent.click(within(colour()).getByRole('radio', { name: 'Blanco' }));
    expect(within(stage()).getByRole('img', { name: /Imagen pendiente/ })).toBeInTheDocument();
    expect(stage().innerHTML).not.toContain(FINISH(1).slice(-8));
    expect(stage().innerHTML).not.toContain(FINISH(3).slice(-8));
  });

  it('al cambiar de color la foto del color anterior sale al instante: caja neutra hasta que carga la nueva', async () => {
    renderPage();
    fireEvent.click(within(colour()).getByRole('radio', { name: 'Rojo' }));
    // Right after the click: no picture of Azul anywhere in the box, visible or not (D-C-1).
    expect(stage().innerHTML).not.toContain(FINISH(1).slice(-8));
    expect(stageImages()).toHaveLength(1);
    const [incoming] = stageImages();
    expect(incoming).toHaveAttribute('alt', 'Equipo X Rojo');
    expect(incoming.closest('[data-layer]')).toHaveAttribute('data-ready', 'false');
    // A neutral loading box, not «Imagen pendiente»: Rojo has a picture, it is on its way.
    expect(within(stage()).getByTestId('v4-stage-loading')).toBeInTheDocument();
    expect(within(stage()).queryByTestId('image-pending')).toBeNull();
    // `next/image` calls `onLoad` after decoding: a microtask later.
    await act(async () => { fireEvent.load(incoming); });
    expect(incoming.closest('[data-layer]')).toHaveAttribute('data-ready', 'true');
    expect(within(stage()).queryByTestId('v4-stage-loading')).toBeNull();
  });

  it('con movimiento reducido o sin él, ningún instante muestra el color anterior con el nuevo elegido', () => {
    jest.useFakeTimers();
    try {
      renderPage();
      fireEvent.click(within(colour()).getByRole('radio', { name: 'Rojo' }));
      for (const step of [0, 100, MAX_WAIT_MS + 1]) {
        act(() => { jest.advanceTimersByTime(step); });
        expect(stage().innerHTML).not.toContain(FINISH(1).slice(-8));
      }
      expect(stageImages()).toHaveLength(1);
      expect(stageImages()[0]).toHaveAttribute('alt', 'Equipo X Rojo');
    } finally {
      jest.useRealTimers();
    }
  });

  it('dentro del mismo acabado (otra miniatura) la anterior se queda hasta que carga la nueva', async () => {
    jest.useFakeTimers();
    try {
      renderPage();
      fireEvent.click(screen.getByRole('button', { name: 'Ver imagen 2 de 2' }));
      // Same finish (Azul): both in the box; the outgoing one is hidden from assistive technology.
      const [outgoing, incoming] = stageImages();
      expect(outgoing).toHaveAttribute('alt', '');
      expect(incoming).toHaveAttribute('alt', 'Equipo X Azul, dorso');
      expect(incoming.closest('[data-layer]')).toHaveAttribute('data-ready', 'false');
      await act(async () => { fireEvent.load(incoming); });
      act(() => { jest.advanceTimersByTime(CROSSFADE_MS + 1); });
      expect(stageImages()).toHaveLength(1);
      expect(visibleStageImage()).toHaveAttribute('alt', 'Equipo X Azul, dorso');
    } finally {
      jest.useRealTimers();
    }
  });
});

describe('rendimiento', () => {
  it('sólo la primera foto lleva prioridad; la siguiente carga ya y las miniaturas cuando se vean', () => {
    renderPage();
    const [first] = stageImages();
    expect(first).toHaveAttribute('fetchpriority', 'high');
    const thumbs = screen.getAllByRole('button', { name: /Ver imagen/ }).map((button) => button.querySelector('img'));
    for (const thumb of thumbs) expect(thumb).toHaveAttribute('loading', 'lazy');
    fireEvent.click(within(colour()).getByRole('radio', { name: 'Rojo' }));
    const [incoming] = stageImages();
    expect(incoming).toHaveAttribute('loading', 'eager');
    expect(incoming).not.toHaveAttribute('fetchpriority');
  });

  it('sólo se piden las fotos del acabado elegido; de los demás colores, sólo la miniatura de su chip', () => {
    renderPage();
    expect(stage().innerHTML).toContain('eeeeeee1');
    // G (owner's visual order 4): another colour's finish appears only as its chip's picture — 24 px, lazy — never
    // in the stage or the gallery's thumbnails.
    const others = [...document.querySelectorAll('img')].filter((img) => (img.getAttribute('srcset') ?? '').includes('eeeeeee3'));
    expect(others).toHaveLength(1);
    expect(others[0].closest('[data-testid="v4-chip-picture"]')).not.toBeNull();
    expect(others[0]).toHaveAttribute('sizes', '24px');
    expect(others[0]).toHaveAttribute('loading', 'lazy');
    expect(screen.getByTestId('v4-gallery').innerHTML).not.toContain('eeeeeee3');
  });
});

describe('recorrer los colores no arrastra la capacidad (revisión de #373)', () => {
  it('con las flechas: Rojo la lleva a 512 GB y volver a Azul la devuelve a 128 GB, y lo dice', () => {
    renderPage();
    const colours = within(colour()).getAllByRole('radio');
    colours[0].focus();
    fireEvent.keyDown(colours[0], { key: 'ArrowRight' }); // Blanco, 128 GB
    fireEvent.keyDown(within(colour()).getByRole('radio', { name: 'Blanco' }), { key: 'ArrowRight' }); // Rojo → 512 GB
    expect(within(capacity()).getByRole('radio', { name: '512 GB' })).toHaveAttribute('aria-checked', 'true');
    fireEvent.keyDown(within(colour()).getByRole('radio', { name: 'Rojo' }), { key: 'ArrowRight' }); // wraps to Azul
    expect(within(colour()).getByRole('radio', { name: 'Azul' })).toHaveAttribute('aria-checked', 'true');
    expect(within(capacity()).getByRole('radio', { name: '128 GB' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByTestId('v4-choice-adjusted')).toHaveTextContent('Capacidad vuelve a 128 GB.');
  });

  it('una opción cuyo nombre ya termina en punto («EE. UU.») no lleva otro', () => {
    model.attributes[1].options[0].label = '128 GB EE. UU.';
    renderPage();
    fireEvent.click(within(colour()).getByRole('radio', { name: 'Rojo' }));
    fireEvent.click(within(colour()).getByRole('radio', { name: 'Azul' }));
    expect(screen.getByTestId('v4-choice-adjusted')).toHaveTextContent(/^Capacidad vuelve a 128 GB EE\. UU\.$/);
  });

  it('el chip dice lo que hará pulsarlo con lo recordado: Blanco volvería a 256 GB, agotado', () => {
    renderPage();
    fireEvent.click(within(capacity()).getByRole('radio', { name: '256 GB' }));
    fireEvent.click(within(colour()).getByRole('radio', { name: 'Rojo' }));
    expect(within(capacity()).getByRole('radio', { name: '512 GB' })).toHaveAttribute('aria-checked', 'true');
    const blanco = within(colour()).getByRole('radio', { name: 'Blanco, agotado' });
    fireEvent.click(blanco);
    expect(within(capacity()).getByRole('radio', { name: '256 GB' })).toHaveAttribute('aria-checked', 'true');
    expect(row('Opción')).toHaveTextContent('Equipo X 44');
    expect(screen.getByRole('button', { name: 'Agotado' })).toBeDisabled();
  });

  it('una capacidad elegida a mano pasa a ser la que se recuerda', () => {
    renderPage();
    fireEvent.click(within(capacity()).getByRole('radio', { name: '256 GB' }));
    fireEvent.click(within(colour()).getByRole('radio', { name: 'Rojo' }));
    fireEvent.click(within(colour()).getByRole('radio', { name: 'Azul' }));
    expect(within(capacity()).getByRole('radio', { name: '256 GB' })).toHaveAttribute('aria-checked', 'true');
  });
});

describe('un modelo sin color (re-revisión de #373)', () => {
  it('cambiar de capacidad mantiene la portada en pantalla: es la misma foto, no otro acabado', () => {
    // No colour dimension: every variant shows the model's cover (D-PRC-8), resolved by the server.
    model.attributes = model.attributes.filter((attribute) => attribute.kind !== 'color');
    const cover = [{ url: FINISH(5), alt_text: 'Equipo X', is_primary: true, width: 800, height: 800 }];
    model.variants = model.variants
      .filter((variant) => [41, 43, 45].includes(variant.product_id))
      .map((variant) => ({ ...variant, options: { '3': variant.options['3'] }, gallery: cover, image_pending: false }));
    model.finish_images = {};
    renderPage();
    fireEvent.click(within(capacity()).getByRole('radio', { name: '256 GB' }));
    // Same picture: no reset, no loading surface, the cover stays shown and ready.
    expect(within(stage()).queryByTestId('v4-stage-loading')).toBeNull();
    expect(stageImages()).toHaveLength(1);
    expect(stageImages()[0].closest('[data-layer]')).toHaveAttribute('data-ready', 'true');
  });
});

describe('la dirección', () => {
  it('dice la variante elegida sin navegar y conserva lo demás', () => {
    renderPage();
    fireEvent.click(within(colour()).getByRole('radio', { name: 'Rojo' }));
    expect(window.location.pathname).toBe('/modelo/equipo-x');
    const params = new URLSearchParams(window.location.search);
    expect(params.get('variante')).toBe('equipo-x-45');
    expect(params.get('utm_source')).toBe('ig');
  });

  it('abre con la variante que nombra la dirección', () => {
    renderPage({ initialVariant: 'equipo-x-43' });
    expect(within(capacity()).getByRole('radio', { name: '256 GB' })).toHaveAttribute('aria-checked', 'true');
    expect(row('Opción')).toHaveTextContent('Equipo X 43');
  });
});

describe('volver a una foto después de «Imagen pendiente»', () => {
  it('no reaparece la del color anterior debajo de la nueva', () => {
    renderPage();
    fireEvent.click(within(colour()).getByRole('radio', { name: 'Blanco' }));
    expect(stageImages()).toHaveLength(0);
    fireEvent.click(within(colour()).getByRole('radio', { name: 'Rojo' }));
    // Only Rojo's picture (loading), never Azul's from before the pending finish.
    expect(stageImages()).toHaveLength(1);
    expect(stageImages()[0]).toHaveAttribute('alt', 'Equipo X Rojo');
  });
});

describe('volver con «Atrás» (revisión de #373)', () => {
  it('la variante que dice la dirección manda sobre la que el servidor leyó al principio', () => {
    // The page was served with ?variante=equipo-x-41; the buyer chose 256 GB (replaceState), left and came back:
    // the router restores the first payload (initialVariant 41) while the address says 43.
    window.history.replaceState(null, '', '/modelo/equipo-x?variante=equipo-x-43');
    renderPage({ initialVariant: 'equipo-x-41' });
    expect(within(capacity()).getByRole('radio', { name: '256 GB' })).toHaveAttribute('aria-checked', 'true');
    expect(row('Opción')).toHaveTextContent('Equipo X 43');
  });

  it('una dirección con una variante que no es de este modelo no cambia la del servidor', () => {
    window.history.replaceState(null, '', '/modelo/equipo-x?variante=otra-cosa');
    renderPage({ initialVariant: 'equipo-x-43' });
    expect(row('Opción')).toHaveTextContent('Equipo X 43');
  });
});

describe('la dirección, sin lectura del servidor', () => {
  it('si el servidor no pudo leer el modelo, el navegador abre la variante de ?variante=', async () => {
    window.history.replaceState(null, '', '/modelo/equipo-x?variante=equipo-x-43');
    reads.mockImplementation(() => Promise.resolve(reply(model)));
    render(
      <StorefrontProvider config={NEUTRAL_CONFIG}>
        <ModelConfiguratorV4 slug="equipo-x" />
      </StorefrontProvider>,
    );
    await screen.findByRole('heading', { level: 1, name: 'Equipo X' });
    expect(within(capacity()).getByRole('radio', { name: '256 GB' })).toHaveAttribute('aria-checked', 'true');
  });
});

describe('opciones que no existen', () => {
  it('una opción que ninguna variante nueva tiene se ve deshabilitada y las flechas la saltan', () => {
    model.attributes[0].options.push({ id: 29, label: 'Verde', swatch_hex: '#0A0', sort_order: 9 });
    renderPage();
    const verde = within(colour()).getByRole('radio', { name: 'Verde, no disponible' });
    expect(verde).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(verde);
    expect(verde).toHaveAttribute('aria-checked', 'false');
  });
});

describe('disponibilidad (D-D-2)', () => {
  it('«Bajo pedido» sólo con una oferta comprable, y el botón la pide', () => {
    Object.assign(model.variants[0], { available: true, availability: 'supplier', on_order: { lead_time_days: 7, price: '4199.00' } });
    renderPage();
    expect(row('Disponibilidad')).toHaveTextContent('Bajo pedido — entrega en 7 días');
    expect(screen.getByRole('button', { name: 'Pedir bajo pedido' })).toBeEnabled();
    expect(within(screen.getByTestId('v4-summary')).queryByText(/en stock/i)).toBeNull();
  });

  it('lo del proveedor sin oferta no se ofrece ni se dice stock', () => {
    Object.assign(model.variants[0], { available: true, availability: 'supplier', on_order: null });
    renderPage();
    expect(row('Disponibilidad')).toHaveTextContent('Agotado');
    expect(screen.queryByText(/bajo pedido/i)).toBeNull();
    expect(screen.getByRole('button', { name: 'Agotado' })).toBeDisabled();
  });

  it('sin plazo en los datos, no se promete ninguno', () => {
    Object.assign(model.variants[0], {
      available: true, availability: 'supplier', on_order: { lead_time_days: null, price: '4199.00' },
    });
    renderPage();
    expect(row('Disponibilidad')).toHaveTextContent(/^Bajo pedido$/);
  });
});

describe('precio por variante (G · matriz «cambio de precio por variante»)', () => {
  it('el precio sigue a la variante: cambia con el color solo y con la capacidad sola, y se anuncia', () => {
    // Same capacity, another colour, another price: the price is the variant's, never the model's.
    Object.assign(model.variants[1], { price: '4099.00' });
    renderPage();
    const price = () => screen.getByTestId('v4-price');
    expect(price()).toHaveTextContent('S/ 3999.00');
    fireEvent.click(within(colour()).getByRole('radio', { name: 'Blanco' }));
    expect(within(capacity()).getByRole('radio', { name: '128 GB' })).toHaveAttribute('aria-checked', 'true');
    expect(price()).toHaveTextContent('S/ 4099.00');
    expect(announcement()).toHaveTextContent('S/ 4099.00');
    fireEvent.click(within(colour()).getByRole('radio', { name: 'Azul' }));
    expect(price()).toHaveTextContent('S/ 3999.00');
    fireEvent.click(within(capacity()).getByRole('radio', { name: '256 GB' }));
    expect(price()).toHaveTextContent('S/ 4499.00');
    expect(announcement()).toHaveTextContent('S/ 4499.00');
  });

  it('bajo pedido, el precio es el de la oferta y su rótulo lo dice', () => {
    Object.assign(model.variants[0], { available: true, availability: 'supplier', on_order: { lead_time_days: 7, price: '4199.00' } });
    renderPage();
    expect(screen.getByText('Precio bajo pedido')).toBeInTheDocument();
    expect(screen.getByTestId('v4-price')).toHaveTextContent('S/ 4199.00');
  });
});

describe('un modelo sin stock propio (G · matriz)', () => {
  it('sin stock ni oferta: cada variante dice «Agotado», nada se compra y nunca «En stock»', () => {
    for (const variant of model.variants) Object.assign(variant, { available: false, availability: 'none', on_order: null });
    renderPage();
    for (const name of ['Azul', 'Blanco', 'Rojo']) {
      // Each chip says it is sold out in its accessible name too.
      fireEvent.click(within(colour()).getByRole('radio', { name: `${name}, agotado` }));
      expect(row('Disponibilidad')).toHaveTextContent('Agotado');
      expect(screen.getByRole('button', { name: 'Agotado' })).toBeDisabled();
    }
    expect(screen.queryByText(/en stock/i)).toBeNull();
    expect(screen.queryByText(/bajo pedido/i)).toBeNull();
  });

  it('sin stock propio pero con oferta: «Bajo pedido» en esa variante y «Agotado» en la que no la tiene', () => {
    for (const variant of model.variants) Object.assign(variant, { available: false, availability: 'none', on_order: null });
    Object.assign(model.variants[0], { available: true, availability: 'supplier', on_order: { lead_time_days: 5, price: '4099.00' } });
    renderPage();
    expect(row('Disponibilidad')).toHaveTextContent('Bajo pedido — entrega en 5 días');
    expect(screen.getByRole('button', { name: 'Pedir bajo pedido' })).toBeEnabled();
    fireEvent.click(within(colour()).getByRole('radio', { name: 'Blanco, agotado' }));
    expect(row('Disponibilidad')).toHaveTextContent('Agotado');
    expect(screen.getByRole('button', { name: 'Agotado' })).toBeDisabled();
    expect(screen.queryByText(/en stock/i)).toBeNull();
  });
});

describe('chips de color (G · orden visual 4 del propietario)', () => {
  const chip = (name: RegExp) => within(colour()).getByRole('radio', { name });

  it('foto del acabado → muestra de la empresa → sólo el nombre; nunca un punto gris inventado', () => {
    // Azul y Rojo tienen fotos (la de Rojo es demostrativa); Blanco no tiene foto ni muestra configurada.
    model.finish_images = { ...model.finish_images, [ROJO]: [{ ...model.finish_images![ROJO][0], is_demo: true }] };
    model.attributes = model.attributes.map((a) => (a.kind !== 'color' ? a : {
      ...a, options: a.options.map((o) => (o.label === 'Blanco' ? { ...o, swatch_hex: '' } : o)),
    }));
    renderPage();
    const azul = chip(/^Azul/);
    expect(within(azul).getByTestId('v4-chip-picture').innerHTML).toContain(FINISH(1).slice(-8));
    // A demo resource is never the chip: Rojo falls back to its company swatch.
    const rojo = chip(/^Rojo/);
    expect(within(rojo).queryByTestId('v4-chip-picture')).toBeNull();
    expect(rojo.querySelector('.v4-swatch')).toHaveStyle({ backgroundColor: '#B91C1C' });
    const blanco = chip(/^Blanco/);
    expect(blanco.querySelector('.v4-swatch')).toBeNull();
    expect(blanco).toHaveTextContent('Blanco');
  });

  it('FASE 1.2 · Y: dibuja la muestra que el servidor resolvió para este modelo y nunca dice «aproximada»', () => {
    // The server sends the model's own swatch in place of the company's; the shop never learns it is approximate.
    model.finish_images = { ...model.finish_images, [ROJO]: [] };
    model.attributes = model.attributes.map((a) => (a.kind !== 'color' ? a : {
      ...a, options: a.options.map((o) => (o.label === 'Rojo' ? { ...o, swatch_hex: '#C2410C' } : o)),
    }));
    renderPage();
    expect(chip(/^Rojo/).querySelector('.v4-swatch')).toHaveStyle({ backgroundColor: '#C2410C' });
    expect(screen.queryByText(/aproximad/i)).toBeNull();
  });

  it('la elección no se dice sólo con color: el chip elegido lo marca aria-checked y el nombre se escribe', () => {
    renderPage();
    expect(chip(/^Azul/)).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByText('Azul', { selector: 'span.v4-small' })).toBeInTheDocument();
  });
});

describe('la ruta pública', () => {
  it('«Catálogo» lleva al catálogo que diga la página', () => {
    renderPage({ catalogHref: '/product' });
    const crumbs = screen.getByRole('navigation', { name: 'Ruta del modelo' });
    expect(within(crumbs).getByRole('link', { name: 'Catálogo' })).toHaveAttribute('href', '/product');
  });
});
