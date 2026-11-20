import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { FinishImagesManager } from '@/app/admin/components/FinishImagesManager';
import { ModelOptionOrder } from '@/app/admin/components/ModelOptionOrder';
import { SummaryRows } from '@/app/admin/components/ModelCommercialOverview';
import { ReferenceCatalogImport, lineLabel } from '@/app/admin/components/ReferenceCatalogImport';
import { provenanceErrors, type CommercialSummary, type FinishListing } from '@/app/admin/lib/catalog-presentation-api';
import { fetchWithAuth } from '@/app/lib/auth';

import { reply } from './support/device-commerce-fixtures';

/**
 * CATALOG-REFERENCE-01 · PR E — las pantallas del panel para personalizar el catálogo de cada empresa.
 *
 * Con la API simulada (las reglas las prueba Django):
 *   - «Catálogo de referencia»: filtros, vista previa con sus cuatro estados y sus avisos (también «incompleta»),
 *     un conflicto nunca se importa, un modelo no completo exige su aprobación, «Importar» viaja con un
 *     Idempotency-Key que se repite en un reintento, y sin `products.manage` sólo se previsualiza;
 *   - «Imágenes por acabado»: «Imagen pendiente» por color, la procedencia se valida antes del viaje, reordenar con
 *     el teclado deja el foco en la imagen movida, quitar pide confirmación, verificar la licencia dice quién;
 *   - «Orden en la tienda»: subir/bajar con el teclado guarda el orden del modelo;
 *   - «Configuración comercial»: sin `costs.view` no se ve un costo, aunque llegue.
 *
 * Nombres inventados: nada aquí es de un fabricante real ni de la tienda piloto.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

type Call = { method: string; path: string; body: unknown; headers: Record<string, string> };
let writes: Call[];
let handlers: Record<string, (call: Call) => Response | Promise<Response>>;

function mockApi(reads: Record<string, () => unknown>) {
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? 'GET').toUpperCase();
    const path = url.replace(/^.*\/api/, '').replace(/\?.*$/, '');
    if (method !== 'GET') {
      const body = init.body instanceof FormData ? init.body : JSON.parse(String(init.body ?? '{}'));
      const call = { method, path, body, headers: (init.headers ?? {}) as Record<string, string> };
      writes.push(call);
      const handler = handlers[`${method} ${path}`];
      return handler ? handler(call) : reply({}, 200);
    }
    const read = reads[path];
    return read ? reply(read()) : reply({}, 404);
  });
}

beforeEach(() => {
  writes = [];
  handlers = {};
});

// ---------------------------------------------------------------------------
// Catálogo de referencia
// ---------------------------------------------------------------------------

const ref = (key: string, name: string, line: string, generation: string, completeness = 'complete', linked: number | null = null) => ({
  key, name, manufacturer: 'Fabricante', family: 'Teléfono', line, generation, announced: '2025-09-09',
  completeness, missing: completeness === 'complete' ? [] : ['colores oficiales'], verified_at: '2026-11-20',
  dataset_version: '1', linked_model_id: linked,
});

const REFERENCE = [
  ref('fono-10', 'Fono 10', 'standard', '10'),
  ref('fono-10-pro', 'Fono 10 Pro', 'pro', '10'),
  ref('fono-2', 'Fono 2', 'original', '2', 'partial'),
  ref('fono-9', 'Fono 9', 'standard', '9', 'complete', 44),
];

const item = (key: string, name: string, state: string, extra: Record<string, unknown> = {}) => ({
  key, name, manufacturer: 'Fabricante', family: 'Teléfono', completeness: 'complete', missing: [], needs_approval: false,
  state, model_status: state === 'reused' ? 'reused' : 'new', model_id: null, model_name: '', link_model: false,
  reason: '', warnings: [], attributes_new: [], options: [
    { reference_key: 'cap-128', role: 'capacity', label: '128 GB', status: 'new', option_id: null, reason: '' },
  ], palette_add: ['cap-128'], exclusions_add: 0, changes: true, ...extra,
});

function previewOf(items: ReturnType<typeof item>[]) {
  const counts = { new: 0, reused: 0, conflict: 0, pending_review: 0 } as Record<string, number>;
  items.forEach((row) => { counts[row.state] += 1; });
  return { items, attributes: [], counts };
}

async function mountReference(canManage = true) {
  mockApi({ '/admin/catalog/reference/models/': () => ({ results: REFERENCE }) });
  render(<ReferenceCatalogImport canManage={canManage} companyId={null} />);
  await screen.findByRole('list', { name: 'Modelos de referencia' });
}

const pick = (name: string) => fireEvent.click(screen.getByRole('checkbox', { name }));

describe('Catálogo de referencia', () => {
  it('nombra la línea del dataset en palabras, sin vocabulario propio', () => {
    expect(lineLabel('pro_max')).toBe('Pro max');
    expect(lineLabel('')).toBe('—');
  });

  it('filtra por búsqueda, línea, generación, datos y «sólo los no importados»', async () => {
    await mountReference();
    const list = () => screen.getByRole('list', { name: 'Modelos de referencia' });
    expect(within(list()).getAllByRole('listitem')).toHaveLength(4);
    // Lo ya importado enlaza a su modelo.
    expect(within(list()).getByRole('link', { name: 'Importado · ver modelo' })).toHaveAttribute('href', '/admin/products/models/44');

    fireEvent.change(screen.getByLabelText('Línea'), { target: { value: 'pro' } });
    expect(within(list()).getAllByRole('listitem')).toHaveLength(1);
    fireEvent.change(screen.getByLabelText('Línea'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Generación'), { target: { value: '10' } });
    expect(within(list()).getAllByRole('listitem')).toHaveLength(2);
    fireEvent.change(screen.getByLabelText('Generación'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Datos'), { target: { value: 'partial' } });
    expect(within(list()).getByText('Fono 2')).toBeInTheDocument();
    expect(within(list()).getAllByRole('listitem')).toHaveLength(1);
    fireEvent.change(screen.getByLabelText('Datos'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Buscar'), { target: { value: 'fono 10' } });
    expect(within(list()).getAllByRole('listitem')).toHaveLength(2);
    fireEvent.change(screen.getByLabelText('Buscar'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Sólo los que aún no importé' }));
    expect(within(list()).queryByText('Fono 9')).not.toBeInTheDocument();
  });

  it('la vista previa muestra los cuatro estados y los avisos, también «incompleta»; nada se guarda', async () => {
    await mountReference();
    pick('Fono 10');
    pick('Fono 10 Pro');
    pick('Fono 2');
    pick('Fono 9');
    handlers['POST /admin/catalog/reference/preview/'] = () => reply(previewOf([
      item('fono-10', 'Fono 10', 'new'),
      item('fono-10-pro', 'Fono 10 Pro', 'conflict', { reason: 'El modelo «Fono 10 Pro» ya está unido a otra referencia (otra).' }),
      item('fono-2', 'Fono 2', 'pending_review', { completeness: 'partial', missing: ['colores oficiales'], needs_approval: true }),
      item('fono-9', 'Fono 9', 'reused', {
        model_id: 44, model_name: 'Fono 9',
        warnings: ['La paleta de «Fono 9» no tiene SIM: al importar, sus 2 variante(s) quedarán «incompleta» hasta que lleven SIM.'],
      }),
    ]));
    fireEvent.click(screen.getByRole('button', { name: 'Previsualizar (4)' }));
    const preview = await screen.findByRole('list', { name: 'Modelos a importar' });
    expect(writes.map((w) => w.path)).toEqual(['/admin/catalog/reference/preview/']);
    expect(writes[0].body).toEqual({ keys: ['fono-10', 'fono-10-pro', 'fono-2', 'fono-9'] });
    expect(within(preview).getByText('Nuevo')).toBeInTheDocument();
    expect(within(preview).getByText('Conflicto')).toBeInTheDocument();
    expect(within(preview).getByText('Pendiente de revisión')).toBeInTheDocument();
    expect(within(preview).getByText('Se reutiliza')).toBeInTheDocument();
    expect(within(preview).getByRole('note')).toHaveTextContent('quedarán «incompleta»');
    expect(within(preview).getByText(/ya está unido a otra referencia/)).toBeInTheDocument();
    expect(within(preview).getByText(/falta colores oficiales/)).toBeInTheDocument();
    // Con un conflicto y un modelo sin aprobar no se importa.
    expect(screen.getByRole('button', { name: /^Importar 4 modelo/ })).toBeDisabled();
  });

  it('un conflicto se quita de la selección, la aprobación es obligatoria y la importación viaja con Idempotency-Key', async () => {
    await mountReference();
    pick('Fono 10 Pro');
    pick('Fono 2');
    let previews = 0;
    handlers['POST /admin/catalog/reference/preview/'] = (call) => {
      previews += 1;
      const keys = (call.body as { keys: string[] }).keys;
      return reply(previewOf(keys.map((key) => (key === 'fono-2'
        ? item('fono-2', 'Fono 2', 'pending_review', { completeness: 'partial', missing: ['colores oficiales'], needs_approval: true })
        : item(key, 'Fono 10 Pro', 'conflict', { reason: 'Conflicto de nombre.' })))));
    };
    fireEvent.click(screen.getByRole('button', { name: 'Previsualizar (2)' }));
    await screen.findByText('Conflicto de nombre.');
    fireEvent.click(screen.getByRole('button', { name: 'Quitar Fono 10 Pro de la selección' }));
    await waitFor(() => expect(previews).toBe(2));
    await waitFor(() => expect(screen.queryByText('Conflicto de nombre.')).not.toBeInTheDocument());
    const importButton = await screen.findByRole('button', { name: /^Importar 1 modelo/ });
    expect(importButton).toBeDisabled();

    fireEvent.click(screen.getByRole('checkbox', { name: 'Apruebo importarlo así' }));
    expect(importButton).toBeEnabled();

    // Primer intento: falla la red; el reintento lleva LA MISMA clave.
    let attempt = 0;
    handlers['POST /admin/catalog/reference/apply/'] = () => {
      attempt += 1;
      if (attempt === 1) throw new TypeError('network');
      return reply({ created: { models: [{ key: 'fono-2', model_id: 70, name: 'Fono 2' }], attributes: [], options: [] },
        linked: { models: 0, options: 0 }, palette_added: 3, exclusions_added: 0,
        items: [{ key: 'fono-2', model_id: 70, state: 'pending_review' }], replayed: false });
    };
    fireEvent.click(importButton);
    expect(await screen.findByRole('alert')).toHaveTextContent('No hay conexión');
    fireEvent.click(screen.getByRole('button', { name: /^Importar 1 modelo/ }));
    expect(await screen.findByText('Importación terminada.')).toBeInTheDocument();
    const applies = writes.filter((w) => w.path === '/admin/catalog/reference/apply/');
    expect(applies).toHaveLength(2);
    expect(applies[0].body).toEqual({ keys: ['fono-2'], approve: ['fono-2'] });
    expect(applies[0].headers['Idempotency-Key']).toBeTruthy();
    expect(applies[1].headers['Idempotency-Key']).toBe(applies[0].headers['Idempotency-Key']);
    expect(screen.getByRole('link', { name: 'Fono 2' })).toHaveAttribute('href', '/admin/products/models/70');
  });

  it('sin products.manage se previsualiza pero no se importa ni se aprueba', async () => {
    await mountReference(false);
    pick('Fono 2');
    handlers['POST /admin/catalog/reference/preview/'] = () => reply(previewOf([
      item('fono-2', 'Fono 2', 'pending_review', { completeness: 'partial', needs_approval: true }),
    ]));
    fireEvent.click(screen.getByRole('button', { name: 'Previsualizar (1)' }));
    await screen.findByRole('list', { name: 'Modelos a importar' });
    expect(screen.queryByRole('button', { name: /^Importar/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: 'Apruebo importarlo así' })).not.toBeInTheDocument();
    expect(screen.getByText(/hace falta permiso para gestionar productos/)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Imágenes por acabado
// ---------------------------------------------------------------------------

type Source = {
  kind: string; kind_label: string; url: string; license: string; attribution: string; is_demo: boolean;
  license_verified: boolean; license_verified_at: string | null; license_verified_by: string | null;
};
const source = (over: Partial<Source> = {}): Source => ({
  kind: 'own_photo', kind_label: 'Foto propia', url: '', license: '', attribution: '', is_demo: false,
  license_verified: false, license_verified_at: null, license_verified_by: null, ...over,
});

const picture = (id: number, colour: number, sort_order: number, over: Record<string, unknown> = {}) => ({
  id, color_option_id: colour, url: `/api/storefront/images/img-${id}`, alt_text: '', is_primary: sort_order === 0,
  sort_order, width: 800, height: 800, source: source(), ...over,
});

function listingWith(blackImages: ReturnType<typeof picture>[]): FinishListing {
  return {
    finishes: [
      { option: { id: 21, label: 'Azul', swatch_hex: '#1F3A93' }, images: [], image_pending: true, not_colour: false },
      { option: { id: 22, label: 'Negro', swatch_hex: '#111111' }, images: blackImages as FinishListing['finishes'][number]['images'],
        image_pending: blackImages.length === 0, not_colour: false },
    ],
    max_per_colour: 12,
    source_kinds: [
      { value: 'own_photo', label: 'Foto propia' },
      { value: 'supplier_authorized', label: 'Material autorizado por proveedor' },
      { value: 'licensed_stock', label: 'Banco con licencia comercial' },
      { value: 'demo', label: 'Recurso demostrativo' },
    ],
  };
}

let gallery: ReturnType<typeof picture>[];

async function mountFinishes(canManage = true) {
  mockApi({ '/admin/catalog/models/5/finish-images/': () => listingWith(gallery) });
  render(<FinishImagesManager modelId={5} modelName="Equipo Z" canManage={canManage} companyId={null} />);
  await screen.findByRole('list', { name: 'Acabados' });
}

describe('Imágenes por acabado', () => {
  beforeEach(() => {
    gallery = [picture(1, 22, 0), picture(2, 22, 1, { source: source({ kind: 'demo', kind_label: 'Recurso demostrativo', license: 'Uso demostrativo', is_demo: true }) })];
  });

  it('cada color dice si tiene «Imagen pendiente» y el recurso demostrativo se rotula', async () => {
    await mountFinishes();
    const blue = screen.getByRole('listitem', { name: 'Azul' });
    expect(within(blue).getByText('Imagen pendiente')).toBeInTheDocument();
    const black = screen.getByRole('listitem', { name: 'Negro' });
    expect(within(black).getByText('2 imágenes')).toBeInTheDocument();
    expect(within(black).getByText('Demostrativa')).toBeInTheDocument();
    expect(within(black).getByText(/la tienda lo rotula «Imagen referencial»/)).toBeInTheDocument();
  });

  it('la procedencia se valida antes de subir: origen siempre, licencia salvo foto propia', async () => {
    expect(provenanceErrors({ source_kind: '', license: '', source_url: '', attribution: '' })).toHaveProperty('source_kind');
    expect(provenanceErrors({ source_kind: 'licensed_stock', license: ' ', source_url: '', attribution: '' })).toHaveProperty('license');
    expect(provenanceErrors({ source_kind: 'own_photo', license: '', source_url: '', attribution: '' })).toEqual({});
    expect(provenanceErrors({ source_kind: 'own_photo', license: '', source_url: 'ftp://x', attribution: '' })).toHaveProperty('source_url');

    await mountFinishes();
    fireEvent.click(screen.getByRole('button', { name: 'Subir imagen de Azul' }));
    const form = screen.getByRole('form', { name: 'Subir imagen de Azul' });
    const file = new File([new Uint8Array(10)], 'azul.png', { type: 'image/png' });
    fireEvent.change(within(form).getByLabelText('Elegir imagen'), { target: { files: [file] } });
    fireEvent.click(within(form).getByRole('button', { name: 'Subir imagen' }));
    expect(within(form).getByText('Indica de dónde sale la imagen.')).toBeInTheDocument();
    expect(within(form).getByLabelText('Origen de la imagen (obligatorio)')).toHaveAttribute('aria-invalid', 'true');

    fireEvent.change(within(form).getByLabelText('Origen de la imagen (obligatorio)'), { target: { value: 'licensed_stock' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Subir imagen' }));
    expect(within(form).getByText('Escribe la licencia o la autorización con la que se usa esta imagen.')).toBeInTheDocument();
    expect(writes).toHaveLength(0);

    handlers['POST /admin/catalog/models/5/finish-images/'] = () => reply(picture(9, 21, 0), 201);
    fireEvent.change(within(form).getByLabelText(/^Licencia o autorización/), { target: { value: 'Banco X, licencia comercial' } });
    fireEvent.change(within(form).getByLabelText('Texto alternativo'), { target: { value: 'Equipo Z azul de frente' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Subir imagen' }));
    await waitFor(() => expect(writes).toHaveLength(1));
    const sent = writes[0].body as FormData;
    expect(sent.get('color_option_id')).toBe('21');
    expect(sent.get('source_kind')).toBe('licensed_stock');
    expect(sent.get('license')).toBe('Banco X, licencia comercial');
    expect(sent.get('alt_text')).toBe('Equipo Z azul de frente');
    expect(sent.get('file')).toBeInstanceOf(File);
    expect(await screen.findByText('Imagen subida a Azul.')).toBeInTheDocument();
  });

  it('reordenar con el teclado guarda el orden y deja el foco en la imagen movida', async () => {
    const user = userEvent.setup();
    await mountFinishes();
    handlers['POST /admin/catalog/models/5/finish-images/order/'] = (call) => {
      const order = (call.body as { order: number[] }).order;
      gallery = order.map((id, index) => ({ ...gallery.find((g) => g.id === id)!, sort_order: index, is_primary: index === 0 }));
      return reply(listingWith(gallery));
    };
    const before = screen.getByRole('button', { name: 'Mover la imagen 2 de Negro antes' });
    before.focus();
    await user.keyboard('{Enter}');
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0].body).toEqual({ color_option_id: 22, order: [2, 1] });
    // La imagen movida es ahora la 1: su botón «antes» queda deshabilitado, el foco pasa a su «después».
    await waitFor(() => expect(screen.getByRole('button', { name: 'Mover la imagen 1 de Negro después' })).toHaveFocus());
    expect(screen.getByText('Imagen movida a la posición 1 de Negro.')).toBeInTheDocument();
  });

  it('quitar pide confirmación y la licencia verificada dice quién', async () => {
    await mountFinishes();
    fireEvent.click(screen.getByRole('button', { name: 'Quitar la imagen 1 de Negro' }));
    expect(writes).toHaveLength(0);
    handlers['DELETE /admin/catalog/models/5/finish-images/1/'] = () => {
      gallery = gallery.filter((g) => g.id !== 1);
      return reply(null, 204);
    };
    fireEvent.click(screen.getByRole('button', { name: 'Sí, quitar' }));
    await waitFor(() => expect(writes.map((w) => `${w.method} ${w.path}`)).toEqual(['DELETE /admin/catalog/models/5/finish-images/1/']));

    handlers['PATCH /admin/catalog/models/5/finish-images/2/'] = () => {
      gallery = gallery.map((g) => ({ ...g, source: { ...g.source, license_verified: true,
        license_verified_at: '2026-11-20T10:00:00Z', license_verified_by: 'Ana Ruiz' } }));
      return reply(gallery[0]);
    };
    expect(await screen.findByText('Imagen quitada de Negro.')).toBeInTheDocument();
    expect(screen.getByText('1 imagen')).toBeInTheDocument();
    const check = screen.getByRole('checkbox', { name: 'Licencia verificada: imagen 1 de Negro' });
    expect(check).toBeEnabled();
    fireEvent.click(check);
    await waitFor(() => expect(writes.at(-1)?.body).toEqual({ license_verified: true }));
    expect(await screen.findByText(/Sí, por Ana Ruiz el/)).toBeInTheDocument();
  });

  it('una foto propia sin verificar no es «Sin verificar»: la verificación no aplica y no se ofrece', async () => {
    // Fixture: imagen 1 = foto propia sin verificar; imagen 2 = demostrativa sin verificar.
    await mountFinishes();
    const black = screen.getByRole('listitem', { name: 'Negro' });
    const own = within(black).getByRole('listitem', { name: 'Imagen 1 de Negro' });
    expect(within(own).getByText('No aplica (foto propia)')).toHaveClass('text-muted');
    expect(within(own).queryByText('Sin verificar')).not.toBeInTheDocument();
    expect(within(own).queryByRole('checkbox', { name: /Licencia verificada/ })).not.toBeInTheDocument();
    const demo = within(black).getByRole('listitem', { name: 'Imagen 2 de Negro' });
    expect(within(demo).getByText('Sin verificar')).toBeInTheDocument();
    expect(within(demo).getByRole('checkbox', { name: 'Licencia verificada: imagen 2 de Negro' })).toBeInTheDocument();
  });

  it('el foco: confirmar quitar va a «Sí, quitar» y cancelar vuelve a «Quitar»', async () => {
    await mountFinishes();
    fireEvent.click(screen.getByRole('button', { name: 'Quitar la imagen 1 de Negro' }));
    expect(screen.getByRole('button', { name: 'Sí, quitar' })).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: 'Cancelar' }));
    expect(screen.getByRole('button', { name: 'Quitar la imagen 1 de Negro' })).toHaveFocus();
  });

  it('el foco: editar la procedencia va al primer campo; cancelar y guardar vuelven a «Editar procedencia»', async () => {
    await mountFinishes();
    fireEvent.click(screen.getByRole('button', { name: 'Editar procedencia: imagen 1 de Negro' }));
    const form = screen.getByRole('form', { name: 'Procedencia de la imagen 1 de Negro' });
    expect(within(form).getByLabelText('Origen de la imagen (obligatorio)')).toHaveFocus();
    fireEvent.click(within(form).getByRole('button', { name: 'Cancelar' }));
    expect(screen.getByRole('button', { name: 'Editar procedencia: imagen 1 de Negro' })).toHaveFocus();

    handlers['PATCH /admin/catalog/models/5/finish-images/1/'] = () => reply(gallery[0]);
    fireEvent.click(screen.getByRole('button', { name: 'Editar procedencia: imagen 1 de Negro' }));
    fireEvent.change(screen.getByLabelText('Atribución (opcional)'), { target: { value: 'Foto: tienda' } });
    fireEvent.click(screen.getByRole('button', { name: 'Guardar procedencia' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Editar procedencia: imagen 1 de Negro' })).toHaveFocus());
    expect(writes.at(-1)?.body).toMatchObject({ attribution: 'Foto: tienda', source_kind: 'own_photo' });
  });

  it('el foco tras quitar: la imagen que ocupa su lugar o, si no queda ninguna, el color', async () => {
    await mountFinishes();
    handlers['DELETE /admin/catalog/models/5/finish-images/1/'] = () => {
      gallery = gallery.filter((g) => g.id !== 1).map((g, index) => ({ ...g, sort_order: index, is_primary: index === 0 }));
      return reply(null, 204);
    };
    fireEvent.click(screen.getByRole('button', { name: 'Quitar la imagen 1 de Negro' }));
    fireEvent.click(screen.getByRole('button', { name: 'Sí, quitar' }));
    await waitFor(() => expect(screen.getByRole('listitem', { name: 'Imagen 1 de Negro' })).toHaveFocus());
    expect(screen.getByRole('listitem', { name: 'Imagen 1 de Negro' })).toHaveAttribute('data-image-id', '2');

    handlers['DELETE /admin/catalog/models/5/finish-images/2/'] = () => {
      gallery = [];
      return reply(null, 204);
    };
    fireEvent.click(screen.getByRole('button', { name: 'Quitar la imagen 1 de Negro' }));
    fireEvent.click(screen.getByRole('button', { name: 'Sí, quitar' }));
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Negro' })).toHaveFocus());
  });

  it('el foco tras quitar la última foto de un color fuera de la paleta: el título del panel, nunca la página', async () => {
    // Un color que salió de la paleta sólo aparece mientras tiene fotos: al quitar la última, sale del listado.
    let green = [picture(7, 23, 0)];
    mockApi({ '/admin/catalog/models/5/finish-images/': () => {
      const listing = listingWith(gallery);
      if (green.length) {
        listing.finishes.push({ option: { id: 23, label: 'Verde', swatch_hex: '#14532D' },
          images: green as FinishListing['finishes'][number]['images'], image_pending: false, not_colour: false });
      }
      return listing;
    } });
    render(<FinishImagesManager modelId={5} modelName="Equipo Z" canManage companyId={null} />);
    await screen.findByRole('list', { name: 'Acabados' });
    handlers['DELETE /admin/catalog/models/5/finish-images/7/'] = () => { green = []; return reply(null, 204); };
    fireEvent.click(screen.getByRole('button', { name: 'Quitar la imagen 1 de Verde' }));
    fireEvent.click(screen.getByRole('button', { name: 'Sí, quitar' }));
    expect(await screen.findByText('Imagen quitada de Verde.')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Verde' })).not.toBeInTheDocument();
    const title = screen.getByRole('heading', { name: 'Imágenes por acabado' });
    await waitFor(() => expect(title).toHaveFocus());
    expect(document.body).not.toHaveFocus();
  });

  it('el título de un color y el del panel muestran un anillo de foco cuando reciben el foco por programa', async () => {
    await mountFinishes();
    for (const heading of [screen.getByRole('heading', { name: 'Negro' }), screen.getByRole('heading', { name: 'Imágenes por acabado' })]) {
      expect(heading).toHaveAttribute('tabindex', '-1');
      // `focus:` (no `focus-visible:`): tras un clic en «Sí, quitar» el navegador no marcaría :focus-visible.
      expect(heading.className).not.toMatch(/(^|\s)focus:outline-none(\s|$)/);
      expect(heading.className).toMatch(/(^|\s)focus:outline-2(\s|$)/);
    }
  });

  it('desmarcar la licencia de una foto propia verificada no tira el foco: pasa a su tarjeta', async () => {
    gallery = [picture(1, 22, 0, { source: source({ license_verified: true, license_verified_by: 'Ana Ruiz',
      license_verified_at: '2026-11-20T10:00:00Z' }) }), gallery[1]];
    await mountFinishes();
    handlers['PATCH /admin/catalog/models/5/finish-images/1/'] = (call) => {
      expect(call.body).toEqual({ license_verified: false });
      gallery = gallery.map((g) => (g.id === 1 ? { ...g, source: source() } : g));
      return reply(gallery[0]);
    };
    const check = screen.getByRole('checkbox', { name: 'Licencia verificada: imagen 1 de Negro' });
    check.focus();
    fireEvent.click(check);
    // Una foto propia sin verificar ya no ofrece la casilla: el foco va a la tarjeta de esa imagen.
    await waitFor(() => expect(screen.queryByRole('checkbox', { name: 'Licencia verificada: imagen 1 de Negro' })).not.toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole('listitem', { name: 'Imagen 1 de Negro' })).toHaveFocus());
  });

  it('verificar la licencia de otra imagen deja el foco en su casilla', async () => {
    await mountFinishes();
    handlers['PATCH /admin/catalog/models/5/finish-images/2/'] = () => {
      gallery = gallery.map((g) => (g.id === 2 ? { ...g, source: { ...g.source, license_verified: true } } : g));
      return reply(gallery[1]);
    };
    const check = screen.getByRole('checkbox', { name: 'Licencia verificada: imagen 2 de Negro' });
    check.focus();
    fireEvent.click(check);
    await waitFor(() => expect(writes.at(-1)?.body).toEqual({ license_verified: true }));
    await waitFor(() => expect(screen.getByRole('checkbox', { name: 'Licencia verificada: imagen 2 de Negro' })).toBeChecked());
    expect(screen.getByRole('checkbox', { name: 'Licencia verificada: imagen 2 de Negro' })).toHaveFocus();
  });

  it('«Muestra sin configurar»: sin punto gris, y la empresa elige y guarda su muestra (G, orden 4)', async () => {
    mockApi({ '/admin/catalog/models/5/finish-images/': () => {
      const listing = listingWith(gallery);
      listing.finishes[0] = { ...listing.finishes[0], option: { ...listing.finishes[0].option, swatch_hex: '#9CA3AF', swatch_set: false } };
      return listing;
    } });
    render(<FinishImagesManager modelId={5} modelName="Equipo Z" canManage companyId={null} />);
    const blue = await screen.findByRole('listitem', { name: 'Azul' });
    expect(within(blue).getByText('Muestra sin configurar')).toBeInTheDocument();
    expect(blue.querySelector('[style*="background-color"]')).toBeNull();
    const save = within(blue).getByRole('button', { name: 'Guardar muestra' });
    expect(save).toBeDisabled();
    fireEvent.change(within(blue).getByLabelText('Elegir la muestra de Azul'), { target: { value: '#1f3a93' } });
    fireEvent.click(save);
    await waitFor(() => expect(writes.at(-1)).toMatchObject({ method: 'PATCH', path: '/admin/catalog/options/21/', body: { swatch_hex: '#1F3A93' } }));
    expect(await screen.findByText('Muestra de Azul guardada.')).toBeInTheDocument();
  });

  it('sin products.manage sólo se ve', async () => {
    await mountFinishes(false);
    expect(screen.queryByRole('button', { name: /^Subir imagen/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Mover/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Quitar/ })).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Orden en la tienda
// ---------------------------------------------------------------------------

describe('Orden en la tienda', () => {
  it('bajar un color con el teclado guarda el orden del modelo', async () => {
    const user = userEvent.setup();
    let colours = [
      { id: 21, label: 'Azul', swatch_hex: '#1F3A93', placed: false },
      { id: 22, label: 'Negro', swatch_hex: '#111111', placed: false },
      { id: 23, label: 'Rojo', swatch_hex: '#B91C1C', placed: false },
    ];
    const payload = () => ({ model_id: 5, has_palette: true, attributes: [
      { id: 3, name: 'Capacidad', kind: 'text', role: 'capacity', options: [{ id: 11, label: '128 GB', swatch_hex: '', placed: false }] },
      { id: 4, name: 'Color', kind: 'color', role: 'color', options: colours },
    ] });
    mockApi({ '/admin/catalog/models/5/option-order/': payload });
    handlers['PUT /admin/catalog/models/5/option-order/'] = (call) => {
      const ids = (call.body as { option_ids: number[] }).option_ids;
      colours = ids.map((id) => ({ ...colours.find((c) => c.id === id)!, placed: true }));
      return reply(payload());
    };
    render(<ModelOptionOrder modelId={5} canManage companyId={null} />);
    await screen.findByRole('list', { name: 'Orden de Color' });
    // Un atributo de una sola opción no tiene nada que ordenar.
    expect(screen.queryByRole('list', { name: 'Orden de Capacidad' })).not.toBeInTheDocument();
    screen.getByRole('button', { name: 'Bajar Azul' }).focus();
    await user.keyboard('{Enter}');
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0].body).toEqual({ attribute_id: 4, option_ids: [22, 21, 23] });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Bajar Azul' })).toHaveFocus());
    const order = screen.getByRole('list', { name: 'Orden de Color' });
    expect(within(order).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      expect.stringContaining('Negro'), expect.stringContaining('Azul'), expect.stringContaining('Rojo'),
    ]);
  });
});

// ---------------------------------------------------------------------------
// Configuración comercial
// ---------------------------------------------------------------------------

const SUMMARY: CommercialSummary = {
  model_id: 5,
  visibility: { is_published_online: true, is_featured: true, sort_order: 2 },
  content: { has_description: false, has_cover: true, reference_key: 'fono-10' },
  palette: { has_palette: true, options: { capacity: 2, color: 3, sim: 2, region: 1 }, exclusions: 1 },
  variants: { total: 3, new: 2, used: 1, published: 1, priced: 1, pending_drafts: 1, on_order: 1 },
  finishes: { colours: 3, pending: 2 },
  suppliers: [{ id: 8, name: 'Mayorista Uno', offers: 2, variants: 1, lowest_agreed_cost: '2800.00' }],
  promotions: null,
};

describe('Configuración comercial', () => {
  it('sin costs.view no muestra el costo aunque el servidor lo envíe; con él, sí', () => {
    const { unmount } = render(<SummaryRows summary={SUMMARY} canSeeCosts={false} />);
    expect(screen.getByText('Mayorista Uno')).toBeInTheDocument();
    expect(screen.queryByText(/costo/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/2[,.]?800/)).not.toBeInTheDocument();
    unmount();
    render(<SummaryRows summary={SUMMARY} canSeeCosts />);
    expect(screen.getByText(/costo acordado desde S\/ 2,800\.00/)).toBeInTheDocument();
  });

  it('resume cada parte y enlaza al editor que la guarda', () => {
    render(<SummaryRows summary={SUMMARY} canSeeCosts={false} />);
    expect(screen.getByText(/destacado \(va primero en la lista\)/)).toBeInTheDocument();
    expect(screen.getByText(/2 capacidades · 3 colores · 2 SIM · 1 región · 1 combinación que no existe/)).toBeInTheDocument();
    expect(screen.getByText(/3 colores · 2 con «Imagen pendiente»/)).toBeInTheDocument();
    expect(screen.getByText(/1 variante admite venta bajo pedido/)).toBeInTheDocument();
    expect(screen.getByText('No tienes permiso para ver promociones.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Ir a las imágenes: Imágenes por acabado' })).toHaveAttribute('href', '#acabados');
    expect(screen.getByRole('link', { name: 'Ir a las ofertas: Proveedores asociados' })).toHaveAttribute('href', '/admin/products/suppliers/offers');
    expect(screen.getByRole('link', { name: 'Ir a las promociones: Promociones aplicables' })).toHaveAttribute('href', '/admin/sales/promotions');
  });
});

// Keeps `act` imported for environments where userEvent needs it wrapped.
void act;
