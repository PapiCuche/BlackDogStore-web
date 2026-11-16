import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import fs from 'fs';
import path from 'path';

import { ModelConfiguratorV4 } from '@/app/components/v4/ModelConfiguratorV4';
import { SupportStripV4 } from '@/app/components/v4/SupportStripV4';
import { StorefrontProvider } from '@/app/components/StorefrontProvider';
import { fetchWithAuth } from '@/app/lib/auth';
import { DEVICE_PHOTOS_LABEL, REFERENCE_IMAGE_LABEL, photoLabel } from '@/app/lib/model-configurator';
import { NEUTRAL_CONFIG, type StorefrontConfig } from '@/app/lib/storefront';

import { LINE_NEW, LINE_USED, modelFixture, reply } from './support/device-commerce-fixtures';

/**
 * P2 · F1 — la ficha V4 (vista previa `/v4/modelo/<slug>`).
 *
 * Misma conducta que la V3 (los hooks de `lib/model-configurator.ts`), otra
 * presentación. Lo que se fija aquí es lo que la ficha SIEMPRE dice —modelo,
 * opción, condición, precio, disponibilidad y fotos—, lo que sólo dice si la
 * tienda lo configuró —la garantía—, de dónde viene cada foto y que nada del
 * texto afirma lo que nadie configuró.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;
const reads = jest.fn();

const MANAGED = (n: number) => `/api/storefront/images/${String(n).padStart(32, 'a')}`;

type Model = ReturnType<typeof modelFixture> & { sold_out?: boolean };
let model: Model;

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

function config(extra: Partial<StorefrontConfig> = {}): StorefrontConfig {
  return {
    ...NEUTRAL_CONFIG,
    company: { ...NEUTRAL_CONFIG.company, name: 'Tienda Norte', slug: 'tienda-norte' },
    ...extra,
  };
}

async function renderPage(opts: { initialLine?: string; config?: StorefrontConfig } = {}) {
  const view = render(
    <StorefrontProvider config={opts.config ?? config()}>
      <ModelConfiguratorV4 slug="equipo-x" initialLine={opts.initialLine} />
    </StorefrontProvider>,
  );
  await screen.findByRole('heading', { level: 1, name: 'Equipo X' });
  return view;
}

const summary = () => screen.getByTestId('v4-summary');
const row = (term: string) => {
  const dt = within(summary()).getByText(term, { selector: 'dt' });
  return dt.nextElementSibling as HTMLElement;
};
const capacity = () => screen.getByRole('radiogroup', { name: 'Capacidad' });
const colour = () => screen.getByRole('radiogroup', { name: /^Color/ });
const gallery = () => screen.getByTestId('v4-gallery');

describe('lo que la ficha siempre dice', () => {
  it('nuevos: modelo, opción elegida, condición, precio, disponibilidad y foto', async () => {
    await renderPage();
    expect(row('Modelo')).toHaveTextContent('Equipo X');
    expect(row('Opción')).toHaveTextContent('Equipo X 41');
    expect(row('Condición')).toHaveTextContent(LINE_NEW);
    expect(row('Disponibilidad')).toHaveTextContent('En stock');
    expect(within(summary()).getByTestId('v4-price')).toHaveTextContent('S/ 3999.00');
    expect(within(gallery()).getAllByRole('img')[0]).toHaveAttribute('src', 'http://localhost/azul-128.png');
    expect(within(summary()).getByRole('button', { name: 'Agregar al carrito' })).toBeEnabled();
  });

  it('la opción, el precio, la disponibilidad y la foto siguen a la elección', async () => {
    await renderPage();
    fireEvent.click(within(capacity()).getByRole('radio', { name: '256 GB' }));
    expect(row('Opción')).toHaveTextContent('Equipo X 43');
    expect(within(summary()).getByTestId('v4-price')).toHaveTextContent('S/ 4499.00');
    expect(within(gallery()).getAllByRole('img')[0]).toHaveAttribute('src', 'http://localhost/azul-256-a.png');
    fireEvent.click(within(colour()).getByRole('radio', { name: 'Blanco, agotado' }));
    expect(row('Disponibilidad')).toHaveTextContent('Agotado');
    expect(within(summary()).getByRole('button', { name: 'Agotado' })).toBeDisabled();
  });

  it('seminuevos: el equipo elegido con su condición, su estado, su precio y sus fotos', async () => {
    await renderPage({ initialLine: 'used' });
    expect(row('Modelo')).toHaveTextContent('Equipo X');
    expect(row('Equipo')).toHaveTextContent('128 GB · Azul');
    expect(row('Condición')).toHaveTextContent(LINE_USED);
    expect(row('Estado')).toHaveTextContent('Excelente');
    expect(row('Disponibilidad')).toHaveTextContent('En stock');
    expect(within(summary()).getByTestId('v4-price')).toHaveTextContent('S/ 2599.00');
    // Sin lectura de batería no hay fila de batería: ni un número ni un hueco.
    expect(within(summary()).queryByText('Batería', { selector: 'dt' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /256 GB · Rojo/ }));
    expect(row('Equipo')).toHaveTextContent('256 GB · Rojo');
    expect(row('Batería')).toHaveTextContent('87%');
    expect(within(summary()).queryByText('Estado', { selector: 'dt' })).toBeNull();
    expect(within(gallery()).getAllByRole('img')[0]).toHaveAttribute('src', 'http://localhost/u2-a.png');
  });

  it('comprar un equipo seminuevo manda ese equipo', async () => {
    await renderPage({ initialLine: 'used' });
    fireEvent.click(screen.getByRole('button', { name: /256 GB · Rojo/ }));
    await act(async () => {
      fireEvent.click(within(summary()).getByRole('button', { name: 'Comprar este equipo' }));
    });
    const body = JSON.parse(String(send.mock.calls[0][1]?.body));
    expect(body).toMatchObject({ product: 53, unit: '0b4c7a52-2222-4a6e-9b1c-000000000002' });
    expect(await screen.findByRole('status')).toHaveTextContent('Equipo agregado al carrito.');
  });

  it('agregar una opción nueva manda la variante, sin equipo', async () => {
    await renderPage();
    await act(async () => {
      fireEvent.click(within(summary()).getByRole('button', { name: 'Agregar al carrito' }));
    });
    const body = JSON.parse(String(send.mock.calls[0][1]?.body));
    expect(body).toMatchObject({ product: 41, quantity: 1 });
    expect(body).not.toHaveProperty('unit');
  });

  it('cambiar de condición cambia la ficha entera', async () => {
    await renderPage();
    const lines = () => screen.getByRole('radiogroup', { name: 'Condición' });
    fireEvent.click(within(lines()).getByRole('radio', { name: LINE_USED }));
    expect(row('Condición')).toHaveTextContent(LINE_USED);
    expect(within(lines()).getByRole('radio', { name: LINE_USED })).toHaveAttribute('aria-checked', 'true');
    expect(within(lines()).getByRole('radio', { name: LINE_NEW })).toHaveAttribute('aria-checked', 'false');
  });

  it('un modelo inexistente lo dice', async () => {
    reads.mockImplementation(() => Promise.resolve(reply({}, 404)));
    render(<ModelConfiguratorV4 slug="equipo-x" />);
    expect(await screen.findByText('Modelo no encontrado')).toBeInTheDocument();
  });
});

describe('garantía', () => {
  it('sin texto ni enlace configurados no dice nada de garantía', async () => {
    const { container } = await renderPage();
    expect(screen.queryByTestId('v4-warranty')).toBeNull();
    expect(container.textContent).not.toMatch(/garant/i);
  });

  it('con texto configurado, lo muestra tal cual', async () => {
    await renderPage({
      config: config({ policies: { ...NEUTRAL_CONFIG.policies, warranty_text: 'Doce meses por defectos de fábrica.' } }),
    });
    expect(screen.getByTestId('v4-warranty')).toHaveTextContent('Doce meses por defectos de fábrica.');
  });

  it('con sólo el enlace configurado, enlaza la política', async () => {
    await renderPage({
      config: config({ policies: { ...NEUTRAL_CONFIG.policies, warranty_url: 'https://norte.example/garantia' } }),
    });
    expect(within(screen.getByTestId('v4-warranty')).getByRole('link'))
      .toHaveAttribute('href', 'https://norte.example/garantia');
  });

  it('un texto de sólo espacios cuenta como vacío', async () => {
    await renderPage({ config: config({ policies: { ...NEUTRAL_CONFIG.policies, warranty_text: '   ' } }) });
    expect(screen.queryByTestId('v4-warranty')).toBeNull();
  });
});

describe('rótulos de las fotos', () => {
  it('la regla en un solo sitio', () => {
    expect(photoLabel('http://localhost/azul.png', 'catalog')).toBe(REFERENCE_IMAGE_LABEL);
    expect(photoLabel('https://cdn.example.com/iphone.jpg', 'catalog')).toBe(REFERENCE_IMAGE_LABEL);
    expect(photoLabel(MANAGED(1), 'catalog')).toBeNull();
    expect(photoLabel(`https://tienda.example${MANAGED(1)}`, 'catalog')).toBeNull();
    expect(photoLabel(MANAGED(2), 'device')).toBe(DEVICE_PHOTOS_LABEL);
    // Una foto que no salió de la tienda no se presenta como de este equipo.
    expect(photoLabel('https://cdn.example.com/x.jpg', 'device')).toBe(REFERENCE_IMAGE_LABEL);
    expect(REFERENCE_IMAGE_LABEL).toBe('Imagen referencial');
    expect(DEVICE_PHOTOS_LABEL).toBe('Fotos de este equipo');
  });

  it('una imagen que no viene de las subidas de la tienda dice «Imagen referencial»', async () => {
    await renderPage();
    expect(within(gallery()).getByText('Imagen referencial')).toBeInTheDocument();
  });

  it('una imagen subida desde el panel no lleva rótulo', async () => {
    model.variants[0].image_url = MANAGED(3);
    await renderPage();
    expect(within(gallery()).queryByText('Imagen referencial')).toBeNull();
    expect(within(gallery()).queryByText('Fotos de este equipo')).toBeNull();
  });

  it('las fotos de un seminuevo dicen «Fotos de este equipo»', async () => {
    model.used_units[1].photos = [
      { url: MANAGED(4), alt_text: '', width: 800, height: 600 },
      { url: MANAGED(5), alt_text: '', width: 800, height: 600 },
    ];
    await renderPage({ initialLine: 'used' });
    fireEvent.click(screen.getByRole('button', { name: /256 GB · Rojo/ }));
    expect(within(gallery()).getByText('Fotos de este equipo')).toBeInTheDocument();
    expect(within(gallery()).queryByText('Imagen referencial')).toBeNull();
  });

  it('un seminuevo sin fotos no toma prestada la del modelo', async () => {
    await renderPage({ initialLine: 'used' });
    expect(within(gallery()).queryByRole('img')).toBeNull();
    expect(within(gallery()).getByText('Este equipo no tiene fotos publicadas.')).toBeInTheDocument();
  });
});

describe('teclado', () => {
  it('cada selector es un grupo de radios con una sola parada de tabulación y flechas', async () => {
    const user = userEvent.setup();
    await renderPage();
    const radios = within(capacity()).getAllByRole('radio');
    expect(radios.map((r) => r.tabIndex)).toEqual([0, -1, -1]);
    await user.tab();
    // Condición, luego la capacidad: los dos se alcanzan con Tab.
    const lineRadio = within(screen.getByRole('radiogroup', { name: 'Condición' })).getByRole('radio', { name: LINE_NEW });
    let guard = 0;
    while (document.activeElement !== lineRadio && guard++ < 20) await user.tab();
    expect(lineRadio).toHaveFocus();
    await user.tab();
    expect(radios[0]).toHaveFocus();
    await user.keyboard('{ArrowRight}');
    expect(radios[1]).toHaveFocus();
    expect(radios[1]).toHaveAttribute('aria-checked', 'true');
    expect(within(summary()).getByTestId('v4-price')).toHaveTextContent('S/ 4499.00');
    await user.tab();
    expect(within(colour()).getByRole('radio', { name: 'Azul' })).toHaveFocus();
  });

  it('una combinación inexistente se ve pero las flechas la saltan', async () => {
    await renderPage();
    const rojo = within(colour()).getByRole('radio', { name: 'Rojo, no disponible' });
    expect(rojo).toHaveAttribute('aria-disabled', 'true');
    const colours = within(colour()).getAllByRole('radio');
    colours[0].focus();
    fireEvent.keyDown(colours[0], { key: 'ArrowRight' });
    fireEvent.keyDown(colours[1], { key: 'ArrowRight' });
    expect(colours[0]).toHaveAttribute('aria-checked', 'true');
    expect(colours[2]).toHaveAttribute('aria-checked', 'false');
  });

  it('los equipos seminuevos se eligen con Tab y Enter', async () => {
    const user = userEvent.setup();
    await renderPage({ initialLine: 'used' });
    const second = screen.getByRole('button', { name: /256 GB · Rojo/ });
    second.focus();
    await user.keyboard('{Enter}');
    expect(second).toHaveAttribute('aria-pressed', 'true');
    expect(row('Equipo')).toHaveTextContent('256 GB · Rojo');
  });
});

describe('afirmaciones', () => {
  const VETOED = [/oficial/i, /100\s*%/, /impecable/i, /garantizad/i];

  it.each([
    ['nuevos', undefined],
    ['seminuevos', 'used'],
  ])('el texto de la ficha (%s) no afirma nada que nadie configuró', async (_name, line) => {
    const { container } = await renderPage({
      initialLine: line,
      config: config({
        contact: { ...NEUTRAL_CONFIG.contact, whatsapp_link: 'https://wa.me/51900000000', email: 'hola@norte.example' },
        services: [{ title: 'Cambio de pantalla' } as StorefrontConfig['services'][number]],
      }),
    });
    const text = container.textContent ?? '';
    for (const word of VETOED) expect(text).not.toMatch(word);
    expect(text).not.toMatch(/garant/i);
  });

  // P2 · F2: también las páginas de `app/v4` (portada y ficha) y el lector del servidor. Sólo código: en la
  // hoja de estilos «100%» es un ancho, no una afirmación.
  const walk = (dir: string): string[] => fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) =>
    entry.isDirectory() ? walk(path.join(dir, entry.name)) : [path.join(dir, entry.name)]);
  const v4Sources = () => [
    ...walk(path.join(process.cwd(), 'app/components/v4')),
    ...walk(path.join(process.cwd(), 'app/v4')),
    path.join(process.cwd(), 'app/lib/catalog-models-server.ts'),
  ].filter((file) => /\.tsx?$/.test(file));

  it('el código V4 no escribe ninguna frase vetada', () => {
    const files = v4Sources();
    expect(files.some((file) => file.endsWith(path.join('app', 'v4', 'page.tsx')))).toBe(true);
    expect(files.some((file) => file.endsWith('HomeV4.tsx'))).toBe(true);
    for (const file of files) {
      const source = fs.readFileSync(file, 'utf8');
      for (const word of VETOED) expect(`${file}: ${source}`).not.toMatch(word);
    }
  });

  it('la V4 usa a lo sumo tres pesos (400, 500, 700)', () => {
    for (const file of v4Sources()) {
      const source = fs.readFileSync(file, 'utf8');
      expect(`${file}: ${source}`).not.toMatch(/\bfont-(thin|extralight|light|semibold|extrabold|black)\b/);
    }
  });
});

describe('franja de soporte', () => {
  function renderStrip(extra: Partial<StorefrontConfig>) {
    return render(
      <StorefrontProvider config={config(extra)}>
        <SupportStripV4 />
      </StorefrontProvider>,
    );
  }

  it('sin contacto, sin garantía y sin servicios no se dibuja', () => {
    const { container } = renderStrip({});
    expect(container).toBeEmptyDOMElement();
  });

  it('el contacto: WhatsApp cuando hay número', () => {
    renderStrip({ contact: { ...NEUTRAL_CONFIG.contact, whatsapp_link: 'https://wa.me/51900000000', email: 'hola@norte.example' } });
    expect(screen.getByRole('link', { name: /WhatsApp/ })).toHaveAttribute('href', 'https://wa.me/51900000000');
  });

  it('el contacto: correo cuando no hay WhatsApp', () => {
    renderStrip({ contact: { ...NEUTRAL_CONFIG.contact, email: 'hola@norte.example' } });
    expect(screen.getByRole('link', { name: /hola@norte\.example/ })).toHaveAttribute('href', 'mailto:hola@norte.example');
  });

  it('la garantía sólo si hay enlace', () => {
    renderStrip({
      contact: { ...NEUTRAL_CONFIG.contact, email: 'hola@norte.example' },
      policies: { ...NEUTRAL_CONFIG.policies, warranty_text: 'Texto sin enlace.' },
    });
    expect(screen.queryByRole('link', { name: /garant/i })).toBeNull();
  });

  it('con enlace de garantía, lo enlaza', () => {
    renderStrip({ policies: { ...NEUTRAL_CONFIG.policies, warranty_url: 'https://norte.example/garantia' } });
    expect(screen.getByRole('link', { name: /garantía/i })).toHaveAttribute('href', 'https://norte.example/garantia');
  });

  it('reparaciones sólo si la tienda publicó servicios', () => {
    const { unmount } = renderStrip({ contact: { ...NEUTRAL_CONFIG.contact, email: 'hola@norte.example' } });
    expect(screen.queryByRole('link', { name: /Reparaciones/ })).toBeNull();
    unmount();
    renderStrip({ services: [{ title: 'Cambio de pantalla' } as StorefrontConfig['services'][number]] });
    expect(screen.getByRole('link', { name: /Reparaciones/ })).toHaveAttribute('href', '/services');
  });
});

describe('revisión independiente de #308', () => {
  describe('cambiar de condición no pierde el foco', () => {
    const lineChip = (name: string) =>
      within(screen.getByRole('radiogroup', { name: 'Condición' })).getByRole('radio', { name });

    it('con las flechas, el foco queda en la condición elegida', async () => {
      const user = userEvent.setup();
      await renderPage();
      lineChip(LINE_NEW).focus();
      await user.keyboard('{ArrowRight}');
      expect(lineChip(LINE_USED)).toHaveAttribute('aria-checked', 'true');
      expect(document.activeElement).toBe(lineChip(LINE_USED));
      await user.keyboard('{ArrowLeft}');
      expect(lineChip(LINE_NEW)).toHaveAttribute('aria-checked', 'true');
      expect(document.activeElement).toBe(lineChip(LINE_NEW));
    });

    it('con Espacio y con Intro, también', async () => {
      const user = userEvent.setup();
      await renderPage();
      lineChip(LINE_USED).focus();
      await user.keyboard(' ');
      expect(lineChip(LINE_USED)).toHaveAttribute('aria-checked', 'true');
      expect(document.activeElement).toBe(lineChip(LINE_USED));
      lineChip(LINE_NEW).focus();
      await user.keyboard('{Enter}');
      expect(lineChip(LINE_NEW)).toHaveAttribute('aria-checked', 'true');
      expect(document.activeElement).toBe(lineChip(LINE_NEW));
    });

    it('con un clic, también', async () => {
      const user = userEvent.setup();
      await renderPage();
      await user.click(lineChip(LINE_USED));
      expect(lineChip(LINE_USED)).toHaveAttribute('aria-checked', 'true');
      expect(document.activeElement).toBe(lineChip(LINE_USED));
    });
  });

  describe('el resumen de seminuevos nunca cambia de equipo por su cuenta', () => {
    const TAKEN = 'Ese equipo ya no está disponible.';

    it('tras «unit_taken» y la recarga, no ofrece otro equipo en su lugar', async () => {
      let served = 0;
      reads.mockImplementation((input: RequestInfo | URL) => {
        if (!String(input).includes('/catalog/models/equipo-x')) return Promise.resolve(reply({}, 404));
        served += 1;
        if (served === 1) return Promise.resolve(reply(model));
        const fresh = modelFixture();
        fresh.used_units = fresh.used_units.slice(1);
        fresh.lines.used.count = 1;
        return Promise.resolve(reply(fresh));
      });
      send.mockResolvedValueOnce(reply({ detail: TAKEN, code: 'unit_taken' }, 409));
      await renderPage({ initialLine: 'used' });
      expect(row('Equipo')).toHaveTextContent('128 GB · Azul');
      await act(async () => {
        fireEvent.click(within(summary()).getByRole('button', { name: 'Comprar este equipo' }));
      });
      await screen.findByText('1 equipo');
      // The device left; the other one is NOT silently put in its place.
      expect(screen.queryByTestId('v4-summary')).toBeNull();
      expect(screen.queryByRole('button', { name: 'Comprar este equipo' })).toBeNull();
      expect(screen.getByRole('button', { name: /256 GB · Rojo/ })).toHaveAttribute('aria-pressed', 'false');
      expect(screen.getByRole('alert')).toHaveTextContent(TAKEN);
      // Choosing again is the buyer's call.
      fireEvent.click(screen.getByRole('button', { name: /256 GB · Rojo/ }));
      expect(row('Equipo')).toHaveTextContent('256 GB · Rojo');
      expect(within(summary()).queryByRole('alert')).toBeNull();
    });

    it('si se cambia de equipo con una compra en curso, la respuesta no se pinta bajo el otro', async () => {
      let answer: (value: Response) => void = () => {};
      send.mockImplementationOnce(() => new Promise<Response>((resolve) => { answer = resolve; }));
      await renderPage({ initialLine: 'used' });
      fireEvent.click(within(summary()).getByRole('button', { name: 'Comprar este equipo' }));
      fireEvent.click(screen.getByRole('button', { name: /256 GB · Rojo/ }));
      expect(row('Equipo')).toHaveTextContent('256 GB · Rojo');
      await act(async () => { answer(reply({ detail: 'No se pudo reservar.', code: 'sold_out' }, 409)); });

      // Not under the device now on screen, whose button stays usable…
      expect(within(summary()).queryByRole('alert')).toBeNull();
      expect(within(summary()).getByRole('button', { name: 'Comprar este equipo' })).toBeEnabled();
      // …but said, naming the device it was about.
      const alert = screen.getByRole('alert');
      expect(alert).toHaveTextContent('No se pudo reservar.');
      expect(alert).toHaveTextContent('128 GB · Azul');

      // Back on that device, the summary says it.
      fireEvent.click(screen.getByRole('button', { name: /128 GB · Azul/ }));
      expect(within(summary()).getByRole('alert')).toHaveTextContent('No se pudo reservar.');
    });
  });
});

describe('P2 · F2 — dato inicial del servidor', () => {
  it('con el modelo del servidor, la ficha se pinta en el primer render y aun así se vuelve a leer', async () => {
    render(
      <StorefrontProvider config={config()}>
        <ModelConfiguratorV4 slug="equipo-x" initialModel={modelFixture() as never} />
      </StorefrontProvider>,
    );
    // Sin esperar: ya está.
    expect(screen.getByRole('heading', { level: 1, name: 'Equipo X' })).toBeInTheDocument();
    expect(within(summary()).getByTestId('v4-price')).toHaveTextContent('S/ 3999.00');
    await act(async () => {});
    expect(reads.mock.calls.filter(([url]) => String(url).includes('/catalog/models/equipo-x'))).toHaveLength(1);
  });

  it('«no existe» del servidor se dice en el primer render', () => {
    reads.mockImplementation(() => Promise.resolve(reply({}, 404)));
    render(<ModelConfiguratorV4 slug="equipo-x" initialModel={null} />);
    expect(screen.getByText('Modelo no encontrado')).toBeInTheDocument();
  });

  it('el catálogo de la ruta es la portada V4', async () => {
    await renderPage();
    const trail = screen.getByRole('navigation', { name: 'Ruta del modelo' });
    expect(within(trail).getByRole('link', { name: 'Catálogo' })).toHaveAttribute('href', '/v4');
  });

  it('«Ver catálogo» del modelo inexistente vuelve a la portada V4', async () => {
    reads.mockImplementation(() => Promise.resolve(reply({}, 404)));
    render(<ModelConfiguratorV4 slug="equipo-x" />);
    expect(await screen.findByRole('link', { name: 'Ver catálogo' })).toHaveAttribute('href', '/v4');
  });
});
