import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { ModelAccessoriesPanel } from '@/app/admin/components/ModelAccessoriesPanel';
import type { ModelAccessory } from '@/app/admin/lib/internal-api';
import { fetchWithAuth } from '@/app/lib/auth';

import { reply } from './support/device-commerce-fixtures';

/**
 * P2 · F3 — «Accesorios compatibles» en la página de un modelo.
 *
 * Con la API simulada (las reglas las prueba Django en `test_accessory_compat.py`):
 *   - lista los accesorios vinculados en su orden y avisa del que la tienda no muestra;
 *   - sin vínculos dice que no hay ninguno;
 *   - buscar y agregar, subir, bajar y quitar cambian sólo el borrador; nada se envía hasta «Guardar accesorios»;
 *   - guardar envía la lista ORDENADA entera a PUT …/accessories/ y muestra lo que respondió el servidor;
 *   - un rechazo del servidor se muestra como alerta y conserva el borrador;
 *   - un error al leer se muestra con «Reintentar»;
 *   - sin `products.manage` es de sólo lectura.
 *
 * Nombres inventados: nada aquí es de Apple ni de la tienda piloto.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const accessory = (id: number, name: string, price: string, shown_online = true): ModelAccessory => ({
  id, name, slug: `acc-${id}`, price, image_url: '', is_active: true, is_published_online: shown_online,
  shown_online, is_accessory: true,
});

const CASE = accessory(31, 'Funda clara', '59.90');
const GLASS = accessory(32, 'Lámina de vidrio', '29.90', false);
const CHARGER = accessory(33, 'Cargador 20 W', '89.00');

type Call = { method: string; path: string; body: Record<string, unknown> | null; query: string };
let calls: Call[];
let linked: ModelAccessory[];
let putStatus: number;
let failRead: boolean;

function mockApi() {
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? 'GET').toUpperCase();
    const path = url.replace(/^.*\/api/, '').replace(/\?.*$/, '');
    const query = url.includes('?') ? url.slice(url.indexOf('?') + 1) : '';
    const body = init.body ? JSON.parse(String(init.body)) : null;
    calls.push({ method, path, body, query });
    if (path === '/admin/catalog/models/5/accessories/') {
      if (method === 'GET') {
        if (failRead) return reply({ detail: 'Fallo de prueba.' }, 500);
        return reply({ model_id: 5, accessories: linked });
      }
      const expected = body?.expected_ids as number[] | undefined;
      if (expected && expected.join(',') !== linked.map((a) => a.id).join(',')) {
        return reply({ detail: 'Otra persona cambió los accesorios de este modelo mientras editabas.',
          code: 'accessories_changed', field: 'accessory_ids' }, 409);
      }
      if (putStatus !== 200) {
        return reply({ detail: 'Una variante de un modelo es un equipo, no un accesorio.', code: 'not_an_accessory',
          field: 'accessory_ids' }, putStatus);
      }
      const ids = (body?.accessory_ids ?? []) as number[];
      const all = [CASE, GLASS, CHARGER];
      linked = ids.map((id) => all.find((a) => a.id === id)!).filter(Boolean);
      return reply({ model_id: 5, accessories: linked, changed: true });
    }
    if (path === '/admin/catalog/accessories/') {
      const search = new URLSearchParams(query).get('search') ?? '';
      const all = [CHARGER, CASE, GLASS];
      return reply({ results: all.filter((a) => a.name.toLowerCase().includes(search.toLowerCase())) });
    }
    return reply({}, 404);
  });
}

beforeEach(() => {
  calls = [];
  linked = [CASE, GLASS];
  putStatus = 200;
  failRead = false;
  mockApi();
});

const writes = () => calls.filter((c) => c.method !== 'GET');

async function mount(canManage = true) {
  render(<ModelAccessoriesPanel modelId={5} companyId={7} canManage={canManage} />);
  const region = await screen.findByRole('region', { name: 'Accesorios compatibles' });
  return region;
}

function names(region: HTMLElement) {
  const list = within(region).getByRole('list', { name: 'Accesorios de este modelo' });
  return within(list).getAllByRole('listitem').map((item) => within(item).getByTestId('accessory-name').textContent);
}

describe('Accesorios compatibles', () => {
  it('lista los vinculados en orden y avisa del que la tienda no muestra', async () => {
    const region = await mount();
    await waitFor(() => expect(names(region)).toEqual(['Funda clara', 'Lámina de vidrio']));
    expect(calls[0].query).toBe('company=7');
    expect(within(region).getByText('S/ 59.90')).toBeInTheDocument();
    const glass = within(region).getAllByRole('listitem')[1];
    expect(within(glass).getByText('No se muestra en la tienda')).toBeInTheDocument();
  });

  it('sin vínculos dice que no hay ninguno', async () => {
    linked = [];
    const region = await mount();
    expect(await within(region).findByText('Este modelo aún no tiene accesorios compatibles.')).toBeInTheDocument();
  });

  it('busca, agrega, reordena y quita sin enviar nada hasta guardar; guardar envía la lista ordenada', async () => {
    const region = await mount();
    await waitFor(() => expect(names(region)).toHaveLength(2));
    const save = within(region).getByRole('button', { name: 'Guardar accesorios' });
    expect(save).toBeDisabled();

    fireEvent.change(within(region).getByLabelText('Buscar accesorio'), { target: { value: 'carga' } });
    fireEvent.click(within(region).getByRole('button', { name: 'Buscar' }));
    const results = await within(region).findByRole('list', { name: 'Resultados de la búsqueda' });
    expect(calls.find((c) => c.path === '/admin/catalog/accessories/')?.query).toBe('company=7&search=carga');
    fireEvent.click(within(results).getByRole('button', { name: 'Agregar «Cargador 20 W»' }));
    expect(names(region)).toEqual(['Funda clara', 'Lámina de vidrio', 'Cargador 20 W']);
    // Lo que ya está en la lista no se ofrece dos veces.
    expect(within(results).getByRole('button', { name: 'Agregar «Cargador 20 W»' })).toBeDisabled();

    fireEvent.click(within(region).getByRole('button', { name: 'Subir «Cargador 20 W»' }));
    expect(names(region)).toEqual(['Funda clara', 'Cargador 20 W', 'Lámina de vidrio']);
    fireEvent.click(within(region).getByRole('button', { name: 'Bajar «Funda clara»' }));
    expect(names(region)).toEqual(['Cargador 20 W', 'Funda clara', 'Lámina de vidrio']);
    fireEvent.click(within(region).getByRole('button', { name: 'Quitar «Lámina de vidrio»' }));
    expect(names(region)).toEqual(['Cargador 20 W', 'Funda clara']);
    // The first cannot go up, the last cannot go down.
    expect(within(region).getByRole('button', { name: 'Subir «Cargador 20 W»' })).toBeDisabled();
    expect(within(region).getByRole('button', { name: 'Bajar «Funda clara»' })).toBeDisabled();

    expect(writes()).toEqual([]);
    expect(within(region).getByText('Hay cambios sin guardar.')).toBeInTheDocument();
    expect(save).toBeEnabled();
    fireEvent.click(save);
    expect(await within(region).findByText('Accesorios guardados.')).toBeInTheDocument();
    expect(writes()).toEqual([{
      method: 'PUT', path: '/admin/catalog/models/5/accessories/', body: { accessory_ids: [33, 31], expected_ids: [31, 32] },
      query: 'company=7',
    }]);
    expect(names(region)).toEqual(['Cargador 20 W', 'Funda clara']);
    expect(save).toBeDisabled();
  });

  it('si otra persona guardó antes (409), lo dice, vuelve a cargar la lista y no conserva el borrador viejo', async () => {
    const region = await mount();
    await waitFor(() => expect(names(region)).toHaveLength(2));
    fireEvent.click(within(region).getByRole('button', { name: 'Quitar «Funda clara»' }));
    // Someone else saves meanwhile.
    linked = [CHARGER];
    fireEvent.click(within(region).getByRole('button', { name: 'Guardar accesorios' }));
    const alert = await within(region).findByRole('alert');
    expect(alert).toHaveTextContent('Otra persona cambió los accesorios de este modelo mientras editabas.');
    await waitFor(() => expect(names(region)).toEqual(['Cargador 20 W']));
    expect(within(region).queryByText('Hay cambios sin guardar.')).toBeNull();
    expect(within(region).getByRole('button', { name: 'Guardar accesorios' })).toBeDisabled();
    expect(linked).toEqual([CHARGER]);
    // The reloaded list is the one the next save expects.
    fireEvent.click(within(region).getByRole('button', { name: 'Quitar «Cargador 20 W»' }));
    fireEvent.click(within(region).getByRole('button', { name: 'Guardar accesorios' }));
    expect(await within(region).findByText('Accesorios guardados.')).toBeInTheDocument();
    expect(writes().at(-1)?.body).toEqual({ accessory_ids: [], expected_ids: [33] });
  });

  it('el foco no se pierde al subir, bajar, agregar o quitar', async () => {
    const region = await mount();
    await waitFor(() => expect(names(region)).toHaveLength(2));
    const upGlass = within(region).getByRole('button', { name: 'Subir «Lámina de vidrio»' });
    upGlass.focus();
    fireEvent.click(upGlass);
    // Now first: «Subir» is disabled, so focus goes to its «Bajar».
    expect(names(region)).toEqual(['Lámina de vidrio', 'Funda clara']);
    await waitFor(() => expect(within(region).getByRole('button', { name: 'Bajar «Lámina de vidrio»' })).toHaveFocus());
    fireEvent.click(within(region).getByRole('button', { name: 'Bajar «Lámina de vidrio»' }));
    await waitFor(() => expect(within(region).getByRole('button', { name: 'Subir «Lámina de vidrio»' })).toHaveFocus());

    fireEvent.change(within(region).getByLabelText('Buscar accesorio'), { target: { value: 'carga' } });
    fireEvent.click(within(region).getByRole('button', { name: 'Buscar' }));
    const results = await within(region).findByRole('list', { name: 'Resultados de la búsqueda' });
    fireEvent.click(within(results).getByRole('button', { name: 'Agregar «Cargador 20 W»' }));
    await waitFor(() => expect(within(region).getByLabelText('Buscar accesorio')).toHaveFocus());

    fireEvent.click(within(region).getByRole('button', { name: 'Quitar «Funda clara»' }));
    // The next row's «Quitar» takes the focus; after the last one, the search box.
    await waitFor(() => expect(within(region).getByRole('button', { name: 'Quitar «Lámina de vidrio»' })).toHaveFocus());
    fireEvent.click(within(region).getByRole('button', { name: 'Quitar «Cargador 20 W»' }));
    await waitFor(() => expect(within(region).getByLabelText('Buscar accesorio')).toHaveFocus());
  });

  it('descartar vuelve a lo guardado', async () => {
    const region = await mount();
    await waitFor(() => expect(names(region)).toHaveLength(2));
    fireEvent.click(within(region).getByRole('button', { name: 'Quitar «Funda clara»' }));
    expect(names(region)).toEqual(['Lámina de vidrio']);
    fireEvent.click(within(region).getByRole('button', { name: 'Descartar cambios' }));
    expect(names(region)).toEqual(['Funda clara', 'Lámina de vidrio']);
    expect(writes()).toEqual([]);
  });

  it('un rechazo del servidor se muestra como alerta y conserva el borrador', async () => {
    putStatus = 400;
    const region = await mount();
    await waitFor(() => expect(names(region)).toHaveLength(2));
    fireEvent.click(within(region).getByRole('button', { name: 'Quitar «Funda clara»' }));
    fireEvent.click(within(region).getByRole('button', { name: 'Guardar accesorios' }));
    const alert = await within(region).findByRole('alert');
    expect(alert).toHaveTextContent('Una variante de un modelo es un equipo, no un accesorio.');
    expect(names(region)).toEqual(['Lámina de vidrio']);
    expect(within(region).getByRole('button', { name: 'Guardar accesorios' })).toBeEnabled();
  });

  it('un error al leer se muestra con «Reintentar»', async () => {
    failRead = true;
    const region = await mount();
    const alert = await within(region).findByRole('alert');
    expect(alert).toHaveTextContent('Fallo de prueba.');
    failRead = false;
    fireEvent.click(within(region).getByRole('button', { name: 'Reintentar' }));
    await waitFor(() => expect(names(region)).toEqual(['Funda clara', 'Lámina de vidrio']));
  });

  it('sin products.manage es de sólo lectura', async () => {
    const region = await mount(false);
    await waitFor(() => expect(names(region)).toHaveLength(2));
    expect(within(region).queryByLabelText('Buscar accesorio')).toBeNull();
    expect(within(region).queryByRole('button', { name: /Quitar|Subir|Bajar|Guardar/ })).toBeNull();
  });
});
