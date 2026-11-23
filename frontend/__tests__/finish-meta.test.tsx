import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { FinishImagesManager } from '@/app/admin/components/FinishImagesManager';
import type { FinishListing } from '@/app/admin/lib/catalog-presentation-api';
import { fetchWithAuth } from '@/app/lib/auth';

import { reply } from './support/device-commerce-fixtures';

/**
 * CATALOG FASE 1.2 · Y — «Imágenes por acabado» en el panel, con la API simulada (las reglas las prueba Django):
 *   - cada foto dice su vista (al subirla y después), y se edita con un selector con nombre accesible;
 *   - «Reemplazar foto» pide archivo y procedencia, viaja a la MISMA ruta de subida con `replace=<id>` (el proxy sólo
 *     admite cuerpos de imagen ahí) y deja el foco en la foto, que conserva su lugar;
 *   - la muestra se puede marcar «aproximada» (empresa y modelo), el modelo puede tener la suya, y el panel lo dice con
 *     «Aproximada»; el punto dibuja la muestra que resuelve el servidor (modelo → empresa → nada).
 *
 * Nombres inventados: nada aquí es de un fabricante real ni de la tienda piloto.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

type Call = { method: string; path: string; body: unknown };
let writes: Call[];
let handlers: Record<string, (call: Call) => Response | Promise<Response>>;
let listing: FinishListing;

function mockApi() {
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = String(input).replace(/^.*\/api/, '').replace(/\?.*$/, '');
    const method = (init.method ?? 'GET').toUpperCase();
    if (method !== 'GET') {
      const body = init.body instanceof FormData ? init.body : JSON.parse(String(init.body ?? '{}'));
      const call = { method, path, body };
      writes.push(call);
      const handler = handlers[`${method} ${path}`];
      return handler ? handler(call) : reply({}, 200);
    }
    // A new object per read, like a real server.
    return path === '/admin/catalog/models/5/finish-images/' ? reply(JSON.parse(JSON.stringify(listing))) : reply({}, 404);
  });
}

const source = { kind: 'own_photo', kind_label: 'Foto propia', url: '', license: '', attribution: '', is_demo: false,
  license_verified: false, license_verified_at: null, license_verified_by: null } as const;

const picture = (id: number, colour: number, order: number, view = '') => ({
  id, color_option_id: colour, url: `/api/storefront/images/img-${id}`, alt_text: '', view, is_primary: order === 0,
  sort_order: order, width: 800, height: 800, source: { ...source },
}) as FinishListing['finishes'][number]['images'][number];

function baseListing(): FinishListing {
  return {
    finishes: [
      { option: { id: 21, label: 'Azul', swatch_hex: '#1F3A93', swatch_set: true, swatch_is_approximate: false,
        model_swatch_hex: '', model_swatch_is_approximate: false, model_swatch_editable: true,
        shown_swatch_hex: '#1F3A93', shown_swatch_is_approximate: false }, images: [], image_pending: true, not_colour: false },
      { option: { id: 22, label: 'Negro', swatch_hex: '#111111', swatch_set: true, swatch_is_approximate: false,
        model_swatch_hex: '', model_swatch_is_approximate: false, model_swatch_editable: false,
        shown_swatch_hex: '#111111', shown_swatch_is_approximate: false },
      images: [picture(1, 22, 0, 'front'), picture(2, 22, 1)], image_pending: false, not_colour: false },
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

async function mount(canManage = true) {
  mockApi();
  render(<FinishImagesManager modelId={5} modelName="Equipo Z" canManage={canManage} companyId={null} />);
  await screen.findByRole('list', { name: 'Acabados' });
}

beforeEach(() => {
  writes = [];
  handlers = {};
  listing = baseListing();
});

describe('Vista de cada foto', () => {
  it('se elige al subir y viaja en el formulario', async () => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Subir imagen de Azul' }));
    const form = screen.getByRole('form', { name: 'Subir imagen de Azul' });
    const file = new File([new Uint8Array(10)], 'azul.png', { type: 'image/png' });
    fireEvent.change(within(form).getByLabelText('Elegir imagen'), { target: { files: [file] } });
    fireEvent.change(within(form).getByLabelText('Origen de la imagen (obligatorio)'), { target: { value: 'own_photo' } });
    fireEvent.change(within(form).getByLabelText('Vista (opcional)'), { target: { value: 'back' } });
    handlers['POST /admin/catalog/models/5/finish-images/'] = () => reply(picture(9, 21, 0, 'back'), 201);
    fireEvent.click(within(form).getByRole('button', { name: 'Subir imagen' }));
    await waitFor(() => expect(writes).toHaveLength(1));
    const sent = writes[0].body as FormData;
    expect(sent.get('view')).toBe('back');
    expect(sent.get('replace')).toBeNull();
  });

  it('cada tarjeta dice su vista y se cambia con un selector con nombre', async () => {
    await mount();
    const first = screen.getByRole('listitem', { name: 'Imagen 1 de Negro' });
    expect(within(first).getByText('Frontal', { selector: 'dd' })).toBeInTheDocument();
    const second = screen.getByRole('listitem', { name: 'Imagen 2 de Negro' });
    expect(within(second).getByText('Sin indicar', { selector: 'dd' })).toBeInTheDocument();
    const select = screen.getByRole('combobox', { name: 'Vista de la imagen 2 de Negro' });
    expect(select).toHaveValue('');
    handlers['PATCH /admin/catalog/models/5/finish-images/2/'] = () => reply(picture(2, 22, 1, 'side'));
    fireEvent.change(select, { target: { value: 'side' } });
    await waitFor(() => expect(writes.at(-1)).toMatchObject({
      method: 'PATCH', path: '/admin/catalog/models/5/finish-images/2/', body: { view: 'side' },
    }));
  });

  it('sin products.manage se lee la vista, pero no hay selector ni «Reemplazar foto»', async () => {
    await mount(false);
    expect(within(screen.getByRole('listitem', { name: 'Imagen 1 de Negro' })).getByText('Frontal', { selector: 'dd' })).toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: /^Vista de la/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Reemplazar foto/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Ajustar muestra/ })).not.toBeInTheDocument();
  });
});

describe('Reemplazar foto', () => {
  it('pide archivo y procedencia, va a la ruta de subida con replace y deja el foco en la foto', async () => {
    await mount();
    const button = screen.getByRole('button', { name: 'Reemplazar foto: imagen 1 de Negro' });
    fireEvent.click(button);
    const form = screen.getByRole('form', { name: 'Reemplazar la imagen 1 de Negro' });
    // Focus moves into the form, on its first field.
    await waitFor(() => expect(within(form).getByLabelText('Origen de la imagen (obligatorio)')).toHaveFocus());
    expect(within(form).getByText(/la verificación de la licencia empieza de nuevo/)).toBeInTheDocument();
    fireEvent.click(within(form).getByRole('button', { name: 'Reemplazar foto' }));
    expect(within(form).getByText('Elige la foto nueva.')).toBeInTheDocument();
    expect(within(form).getByText('Indica de dónde sale la imagen.')).toBeInTheDocument();
    expect(writes).toHaveLength(0);

    const file = new File([new Uint8Array(10)], 'nueva.png', { type: 'image/png' });
    fireEvent.change(within(form).getByLabelText('Elegir la foto nueva'), { target: { files: [file] } });
    fireEvent.change(within(form).getByLabelText('Origen de la imagen (obligatorio)'), { target: { value: 'licensed_stock' } });
    fireEvent.change(within(form).getByLabelText(/^Licencia o autorización/), { target: { value: 'Banco Z, licencia comercial' } });
    handlers['POST /admin/catalog/models/5/finish-images/'] = () => {
      listing.finishes[1].images[0] = { ...picture(1, 22, 0, 'front'), url: '/api/storefront/images/nueva' };
      return reply(listing.finishes[1].images[0]);
    };
    fireEvent.click(within(form).getByRole('button', { name: 'Reemplazar foto' }));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0].path).toBe('/admin/catalog/models/5/finish-images/');
    const sent = writes[0].body as FormData;
    expect(sent.get('replace')).toBe('1');
    expect(sent.get('source_kind')).toBe('licensed_stock');
    expect(sent.get('license')).toBe('Banco Z, licencia comercial');
    expect(sent.get('color_option_id')).toBeNull();
    expect(await screen.findByText('Imagen 1 de Negro reemplazada.')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('listitem', { name: 'Imagen 1 de Negro' })).toHaveFocus());
    expect(screen.queryByRole('form', { name: 'Reemplazar la imagen 1 de Negro' })).not.toBeInTheDocument();
  });

  it('cancelar devuelve el foco a su botón', async () => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Reemplazar foto: imagen 2 de Negro' }));
    const form = screen.getByRole('form', { name: 'Reemplazar la imagen 2 de Negro' });
    fireEvent.click(within(form).getByRole('button', { name: 'Cancelar' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Reemplazar foto: imagen 2 de Negro' })).toHaveFocus());
  });

  it('un rechazo del servidor sobre un campo se muestra bajo ese campo', async () => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Reemplazar foto: imagen 1 de Negro' }));
    const form = screen.getByRole('form', { name: 'Reemplazar la imagen 1 de Negro' });
    fireEvent.change(within(form).getByLabelText('Elegir la foto nueva'), { target: { files: [new File([new Uint8Array(4)], 'x.png', { type: 'image/png' })] } });
    fireEvent.change(within(form).getByLabelText('Origen de la imagen (obligatorio)'), { target: { value: 'own_photo' } });
    handlers['POST /admin/catalog/models/5/finish-images/'] = () => reply({ detail: 'La imagen está dañada.', field: 'file', code: 'invalid' }, 400);
    fireEvent.click(within(form).getByRole('button', { name: 'Reemplazar foto' }));
    expect(await within(form).findByText('La imagen está dañada.')).toBeInTheDocument();
  });
});

describe('Muestras aproximadas y muestra propia del modelo', () => {
  it('el punto dibuja la muestra resuelta y el panel dice «Aproximada»', async () => {
    listing.finishes[0].option = { ...listing.finishes[0].option, model_swatch_hex: '#2563EB',
      model_swatch_is_approximate: true, shown_swatch_hex: '#2563EB', shown_swatch_is_approximate: true };
    await mount();
    const blue = screen.getByRole('listitem', { name: 'Azul' });
    expect(within(blue).getByTestId('finish-swatch')).toHaveStyle({ backgroundColor: '#2563EB' });
    expect(within(blue).getByText('Aproximada')).toBeInTheDocument();
    expect(within(blue).getByText('Muestra propia de este modelo')).toBeInTheDocument();
    const black = screen.getByRole('listitem', { name: 'Negro' });
    expect(within(black).queryByText('Aproximada')).not.toBeInTheDocument();
  });

  it('un color sin muestra resuelta no dibuja punto', async () => {
    listing.finishes[0].option = { ...listing.finishes[0].option, swatch_hex: '#9CA3AF', swatch_set: false, shown_swatch_hex: '' };
    await mount();
    expect(within(screen.getByRole('listitem', { name: 'Azul' })).queryByTestId('finish-swatch')).toBeNull();
  });

  it('«Muestra sin configurar» se guarda marcada como aproximada', async () => {
    listing.finishes[0].option = { ...listing.finishes[0].option, swatch_hex: '#9CA3AF', swatch_set: false, shown_swatch_hex: '' };
    await mount();
    const group = screen.getByRole('group', { name: 'Muestra de Azul' });
    fireEvent.change(within(group).getByLabelText('Muestra de Azul (#RRGGBB)'), { target: { value: '#1f3a93' } });
    fireEvent.click(within(group).getByRole('checkbox', { name: 'Muestra aproximada (pendiente de validación comercial)' }));
    fireEvent.click(within(group).getByRole('button', { name: 'Guardar muestra' }));
    await waitFor(() => expect(writes.at(-1)).toEqual({
      method: 'PATCH', path: '/admin/catalog/options/21/', body: { swatch_hex: '#1F3A93', swatch_is_approximate: true },
    }));
  });

  it('«Ajustar muestra»: la de la empresa y la propia del modelo, con su casilla de aproximada', async () => {
    listing.finishes[0].option = { ...listing.finishes[0].option, swatch_is_approximate: true };
    await mount();
    const blue = screen.getByRole('listitem', { name: 'Azul' });
    const toggle = within(blue).getByRole('button', { name: 'Ajustar muestra de Azul' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    const region = document.getElementById(toggle.getAttribute('aria-controls') ?? '');
    expect(region).not.toBeNull();

    const company = within(region as HTMLElement).getByRole('group', { name: 'Muestra de la empresa (todos los modelos con Azul)' });
    expect(within(company).getByLabelText('Muestra de Azul para la empresa (#RRGGBB)')).toHaveValue('#1F3A93');
    expect(within(company).getByRole('checkbox', { name: 'Muestra aproximada (pendiente de validación comercial)' })).toBeChecked();

    const own = within(region as HTMLElement).getByRole('group', { name: 'Muestra solo para Equipo Z' });
    expect(within(own).getByText('Vacía: este modelo usa la muestra de la empresa.')).toBeInTheDocument();
    const save = within(own).getByRole('button', { name: 'Guardar muestra de este modelo' });
    expect(save).toBeDisabled();
    fireEvent.change(within(own).getByLabelText('Muestra de Azul solo en Equipo Z (#RRGGBB)'), { target: { value: '#2563eb' } });
    fireEvent.click(within(own).getByRole('checkbox', { name: 'Muestra aproximada (pendiente de validación comercial)' }));
    handlers['PATCH /admin/catalog/models/5/palette/'] = () => {
      listing.finishes[0].option = { ...listing.finishes[0].option, model_swatch_hex: '#2563EB',
        model_swatch_is_approximate: true, shown_swatch_hex: '#2563EB', shown_swatch_is_approximate: true };
      return reply({ model_id: 5, option_id: 21, label: 'Azul', swatch_hex: '#2563EB', swatch_is_approximate: true,
        shown_swatch_hex: '#2563EB', shown_swatch_is_approximate: true });
    };
    fireEvent.click(save);
    await waitFor(() => expect(writes.at(-1)).toEqual({
      method: 'PATCH', path: '/admin/catalog/models/5/palette/',
      body: { option_id: 21, swatch_hex: '#2563EB', swatch_is_approximate: true },
    }));
    expect(await screen.findByText('Muestra de Azul guardada para Equipo Z.')).toBeInTheDocument();
    expect(within(blue).getByText('Aproximada')).toBeInTheDocument();
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
  });

  it('volver a la muestra de la empresa envía la muestra vacía', async () => {
    listing.finishes[0].option = { ...listing.finishes[0].option, model_swatch_hex: '#2563EB', shown_swatch_hex: '#2563EB' };
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Ajustar muestra de Azul' }));
    const own = screen.getByRole('group', { name: 'Muestra solo para Equipo Z' });
    expect(within(own).getByText('Este modelo usa su propia muestra (#2563EB).')).toBeInTheDocument();
    fireEvent.click(within(own).getByRole('button', { name: 'Usar la muestra de Azul de la empresa' }));
    await waitFor(() => expect(writes.at(-1)).toEqual({
      method: 'PATCH', path: '/admin/catalog/models/5/palette/', body: { option_id: 21, swatch_hex: '' },
    }));
  });

  it('tras volver a la muestra de la empresa, el formulario se vacía y el foco queda en su campo', async () => {
    listing.finishes[0].option = { ...listing.finishes[0].option, model_swatch_hex: '#2563EB',
      model_swatch_is_approximate: true, shown_swatch_hex: '#2563EB', shown_swatch_is_approximate: true };
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Ajustar muestra de Azul' }));
    const own = () => screen.getByRole('group', { name: 'Muestra solo para Equipo Z' });
    const field = () => within(own()).getByLabelText('Muestra de Azul solo en Equipo Z (#RRGGBB)');
    const check = () => within(own()).getByRole('checkbox', { name: 'Muestra aproximada (pendiente de validación comercial)' });
    expect(field()).toHaveValue('#2563EB');
    expect(check()).toBeChecked();
    handlers['PATCH /admin/catalog/models/5/palette/'] = () => {
      listing.finishes[0].option = { ...listing.finishes[0].option, model_swatch_hex: '',
        model_swatch_is_approximate: false, shown_swatch_hex: '#1F3A93', shown_swatch_is_approximate: false };
      return reply({ model_id: 5, option_id: 21, label: 'Azul', swatch_hex: '', swatch_is_approximate: false,
        shown_swatch_hex: '#1F3A93', shown_swatch_is_approximate: false });
    };
    const clear = within(own()).getByRole('button', { name: 'Usar la muestra de Azul de la empresa' });
    clear.focus();
    fireEvent.click(clear);
    expect(await screen.findByText('Equipo Z vuelve a usar la muestra de Azul de la empresa.')).toBeInTheDocument();
    // The form shows what the server now holds: nothing of this model's own, not the old value to save again.
    await waitFor(() => expect(field()).toHaveValue(''));
    expect(check()).not.toBeChecked();
    expect(within(own()).getByText('Vacía: este modelo usa la muestra de la empresa.')).toBeInTheDocument();
    expect(within(own()).getByRole('button', { name: 'Guardar muestra de este modelo' })).toBeDisabled();
    // The button that had focus is gone: focus lands on the model's swatch field, never on the page.
    await waitFor(() => expect(document.activeElement).toBe(field()));
    expect(document.body).not.toHaveFocus();
  });

  it('la muestra de la empresa se vuelve a leer tras guardarla', async () => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Ajustar muestra de Azul' }));
    const company = () => screen.getByRole('group', { name: 'Muestra de la empresa (todos los modelos con Azul)' });
    const field = () => within(company()).getByLabelText('Muestra de Azul para la empresa (#RRGGBB)');
    fireEvent.change(field(), { target: { value: '#1e40af' } });
    handlers['PATCH /admin/catalog/options/21/'] = () => {
      // The server normalises it (upper case) and holds it as approximate.
      listing.finishes[0].option = { ...listing.finishes[0].option, swatch_hex: '#1E40AF', swatch_is_approximate: true,
        shown_swatch_hex: '#1E40AF', shown_swatch_is_approximate: true };
      return reply({ id: 21, label: 'Azul', swatch_hex: '#1E40AF', sort_order: 0, swatch_is_approximate: true });
    };
    fireEvent.click(within(company()).getByRole('button', { name: 'Guardar muestra de la empresa' }));
    expect(await screen.findByText('Muestra de Azul guardada para la empresa.')).toBeInTheDocument();
    await waitFor(() => expect(field()).toHaveValue('#1E40AF'));
    expect(within(company()).getByRole('checkbox', { name: 'Muestra aproximada (pendiente de validación comercial)' })).toBeChecked();
  });

  it('un color fuera de la paleta del modelo no ofrece muestra propia y dice por qué', async () => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Ajustar muestra de Negro' }));
    expect(screen.queryByRole('group', { name: 'Muestra solo para Equipo Z' })).not.toBeInTheDocument();
    expect(screen.getByText(/márcalo antes en «Opciones de este modelo»/)).toBeInTheDocument();
  });
});
