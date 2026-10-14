/**
 * INVENTORY-UNIFIED-01 — publicar y ocultar productos en bloque, en el panel.
 *
 * «En la web» es un dato del producto, no otra lista. Estas pruebas fijan lo
 * que la pantalla promete: la columna «E-commerce» aparte de «Estado», los
 * filtros rápidos, la selección por fila, por página y por «todo lo que
 * coincide» con el total exacto del servidor, la confirmación con la cuenta, y
 * el resultado dicho sin rodeos — también cuando una parte no se publicó o la
 * selección cambió mientras tanto.
 *
 * Qué se puede publicar y a quién se le permite lo decide el servidor
 * (`test_bulk_visibility.py`). Aquí sólo se comprueba que el panel pida lo
 * correcto y cuente lo que pasó.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { ProductsManager } from '@/app/admin/components/ProductsManager';
import * as admin from '@/app/lib/admin';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/admin', () => ({
  ...jest.requireActual('@/app/lib/admin'),
  fetchAdminProducts: jest.fn(),
  fetchAdminCategories: jest.fn(),
  bulkSetProductVisibility: jest.fn(),
  patchAdminProduct: jest.fn(),
}));

const api = admin as jest.Mocked<typeof admin>;
const { SelectionChangedError } = jest.requireActual('@/app/lib/admin') as typeof admin;

const PUBLISH = 'Publicar en e-commerce';
const HIDE = 'Ocultar del e-commerce';

const base: admin.AdminProduct = {
  id: 1, name: 'Funda', slug: 'funda', description: '', price: '59.00', inventory: 4,
  image_url: '', category_id: 3, category_name: 'Accesorios', is_active: true, is_published_online: true,
  is_serialized: false, publication_problems: [],
  created_at: '2026-10-13T00:00:00Z', updated_at: '2026-10-13T00:00:00Z',
};

// Publicada y sin categoría: ya está en la web, la acción en bloque no la toca.
const funda = { ...base, id: 1, name: 'Funda', publication_problems: ['no_category'] };
const flex = { ...base, id: 2, name: 'Flex de carga', slug: 'flex', is_published_online: false, publication_problems: ['no_image', 'no_category'] };
const cable = { ...base, id: 3, name: 'Cable', slug: 'cable', is_published_online: false };

const page = (results: admin.AdminProduct[], count = results.length) => ({ count, page: 1, page_size: 20, results });

async function open(options: { canManage?: boolean; results?: admin.AdminProduct[]; count?: number } = {}) {
  const results = options.results ?? [funda, flex, cable];
  api.fetchAdminProducts.mockResolvedValue(page(results, options.count ?? results.length));
  await act(async () => {
    render(<ProductsManager canManage={options.canManage ?? true} companyId={7} />);
  });
  await screen.findByText('Funda');
}

const lastQuery = () => api.fetchAdminProducts.mock.calls[api.fetchAdminProducts.mock.calls.length - 1][0]!;
const rowOf = (name: string) => screen.getByText(name).closest('tr')!;
const tick = (name: string) => fireEvent.click(screen.getByRole('checkbox', { name: `Seleccionar ${name}` }));
const tickPage = () => fireEvent.click(screen.getByRole('checkbox', { name: /Seleccionar los \d+ productos de esta página/ }));
const dialog = () => screen.getByRole('dialog');
const confirm = async () => {
  await act(async () => { fireEvent.click(within(dialog()).getByRole('button', { name: 'Confirmar' })); });
};

beforeEach(() => {
  jest.useFakeTimers();
  api.fetchAdminProducts.mockReset();
  api.fetchAdminCategories.mockReset().mockResolvedValue([
    { id: 3, name: 'Accesorios', slug: 'accesorios' }, { id: 5, name: 'Repuestos', slug: 'repuestos' },
  ]);
  api.bulkSetProductVisibility.mockReset();
});

afterEach(() => {
  jest.useRealTimers();
});

describe('la columna «E-commerce»', () => {
  it('dice de cada producto si está publicado o es sólo stock interno, aparte del estado', async () => {
    await open();

    expect(screen.getByRole('columnheader', { name: 'E-commerce' })).toBeInTheDocument();
    expect(within(rowOf('Funda')).getByText('Publicado')).toBeInTheDocument();
    expect(within(rowOf('Flex de carga')).getByText('Solo stock interno')).toBeInTheDocument();
    // Una etiqueta por dato: «Estado» ya no carga con la visibilidad.
    expect(within(rowOf('Flex de carga')).getAllByText('Solo stock interno')).toHaveLength(1);
    expect(within(rowOf('Flex de carga')).getByText('Activo')).toBeInTheDocument();
  });

  it('dice por qué uno interno no se publicaría en bloque', async () => {
    await open();

    expect(within(rowOf('Flex de carga')).getByText(/no tiene imagen, no tiene categoría/)).toBeInTheDocument();
    expect(within(rowOf('Cable')).queryByText(/No se publica en bloque/)).toBeNull();
    expect(within(rowOf('Funda')).queryByText(/No se publica en bloque/)).toBeNull();
  });
});

describe('los filtros rápidos', () => {
  it('Todos / Publicados / Internos piden al servidor esa visibilidad', async () => {
    await open();
    const chips = screen.getByRole('group', { name: 'Visibilidad en e-commerce' });
    expect(within(chips).getAllByRole('button').map((chip) => chip.textContent)).toEqual(['Todos', 'Publicados', 'Internos']);
    expect(within(chips).getByRole('button', { name: 'Todos' })).toHaveAttribute('aria-pressed', 'true');
    expect(lastQuery().is_published_online).toBeUndefined();

    await act(async () => { fireEvent.click(within(chips).getByRole('button', { name: 'Internos' })); });
    expect(lastQuery().is_published_online).toBe('false');
    expect(within(chips).getByRole('button', { name: 'Internos' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(chips).getByRole('button', { name: 'Todos' })).toHaveAttribute('aria-pressed', 'false');

    await act(async () => { fireEvent.click(within(chips).getByRole('button', { name: 'Publicados' })); });
    expect(lastQuery().is_published_online).toBe('true');

    await act(async () => { fireEvent.click(within(chips).getByRole('button', { name: 'Todos' })); });
    expect(lastQuery().is_published_online).toBeUndefined();
  });

  it('filtra por tipo: por serie o por cantidad', async () => {
    await open();
    const type = screen.getByLabelText('Tipo de producto') as HTMLSelectElement;
    expect([...type.options].map((option) => option.textContent)).toEqual(['Todos los tipos', 'Por serie', 'Por cantidad']);

    await act(async () => { fireEvent.change(type, { target: { value: 'true' } }); });
    expect(lastQuery().is_serialized).toBe('true');
    await act(async () => { fireEvent.change(type, { target: { value: 'false' } }); });
    expect(lastQuery().is_serialized).toBe('false');
  });

  it('conserva los filtros de categoría, estado y stock', async () => {
    await open();
    await act(async () => { fireEvent.change(screen.getByLabelText('Categoría'), { target: { value: '5' } }); });
    await act(async () => { fireEvent.change(screen.getByLabelText('Estado del producto'), { target: { value: 'false' } }); });
    await act(async () => { fireEvent.change(screen.getByLabelText('Estado de stock'), { target: { value: 'in_stock' } }); });
    expect(lastQuery()).toMatchObject({ category: '5', is_active: 'false', stock: 'in_stock' });
  });
});

describe('la selección', () => {
  it('sin permiso para gestionar productos no hay casillas ni acciones', async () => {
    await open({ canManage: false });
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0);
    expect(screen.queryByRole('button', { name: PUBLISH })).toBeNull();
    expect(screen.queryByRole('button', { name: HIDE })).toBeNull();
    // La columna se sigue viendo: es información, no una acción.
    expect(screen.getByRole('columnheader', { name: 'E-commerce' })).toBeInTheDocument();
  });

  it('cada fila tiene su casilla con nombre, y las acciones aparecen al marcar', async () => {
    await open();
    expect(screen.queryByRole('button', { name: PUBLISH })).toBeNull();

    tick('Flex de carga');

    expect(screen.getByRole('checkbox', { name: 'Seleccionar Flex de carga' })).toBeChecked();
    expect(screen.getByText('1 producto seleccionado')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: PUBLISH })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: HIDE })).toBeInTheDocument();

    tick('Flex de carga');
    expect(screen.queryByRole('button', { name: PUBLISH })).toBeNull();
  });

  it('la casilla de la cabecera marca y desmarca la página', async () => {
    await open();
    tickPage();
    expect(screen.getByText('3 productos seleccionados')).toBeInTheDocument();
    for (const name of ['Funda', 'Flex de carga', 'Cable']) {
      expect(screen.getByRole('checkbox', { name: `Seleccionar ${name}` })).toBeChecked();
    }
    tickPage();
    expect(screen.queryByText(/seleccionados?$/)).toBeNull();
  });

  it('con la página entera marcada y más resultados, ofrece todos con el total exacto', async () => {
    await open({ count: 45 });
    expect(screen.queryByRole('button', { name: /que coinciden/ })).toBeNull();
    tick('Funda');
    expect(screen.queryByRole('button', { name: /que coinciden/ })).toBeNull();

    tickPage();
    const all = screen.getByRole('button', { name: 'Seleccionar los 45 productos que coinciden' });
    fireEvent.click(all);

    expect(screen.getByText('Los 45 productos que coinciden están seleccionados.')).toBeInTheDocument();
    expect(screen.getByText('45 productos seleccionados')).toBeInTheDocument();
  });

  it('si la página ya es todo lo que hay, no ofrece nada más', async () => {
    await open({ count: 3 });
    tickPage();
    expect(screen.queryByRole('button', { name: /que coinciden/ })).toBeNull();
  });

  it('cambiar un filtro quita la selección', async () => {
    await open({ count: 45 });
    tickPage();
    fireEvent.click(screen.getByRole('button', { name: 'Seleccionar los 45 productos que coinciden' }));

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Internos' })); });
    await screen.findByText('Funda');

    expect(screen.queryByText(/seleccionados/)).toBeNull();
    expect(screen.getByRole('checkbox', { name: 'Seleccionar Funda' })).not.toBeChecked();
    expect(screen.queryByRole('button', { name: PUBLISH })).toBeNull();
  });

  it('escribir en la búsqueda también la quita', async () => {
    await open();
    tick('Funda');
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'cable' } });
    await act(async () => { jest.advanceTimersByTime(400); });
    await waitFor(() => expect(lastQuery().search).toBe('cable'));
    expect(screen.queryByText(/seleccionado/)).toBeNull();
  });

  it('cambiar de página también: no se confirma sobre filas que ya no se ven', async () => {
    await open({ count: 45 });
    tick('Funda');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Siguiente' })); });
    await screen.findByText('Funda');

    expect(lastQuery().page).toBe(2);
    expect(screen.queryByText(/seleccionado/)).toBeNull();
    expect(screen.getByRole('checkbox', { name: 'Seleccionar Funda' })).not.toBeChecked();
  });

  it('desmarcar una fila de «todo lo que coincide» deja la página menos esa fila', async () => {
    await open({ count: 45 });
    tickPage();
    fireEvent.click(screen.getByRole('button', { name: 'Seleccionar los 45 productos que coinciden' }));
    tick('Cable');

    expect(screen.getByText('2 productos seleccionados')).toBeInTheDocument();
    expect(screen.queryByText(/que coinciden están seleccionados/)).toBeNull();
  });

  it('«Quitar selección» la quita', async () => {
    await open();
    tick('Funda');
    fireEvent.click(screen.getByRole('button', { name: 'Quitar selección' }));
    expect(screen.getByRole('checkbox', { name: 'Seleccionar Funda' })).not.toBeChecked();
  });
});

describe('la confirmación', () => {
  it('dice la acción y la cuenta exacta, y sin confirmar no se manda nada', async () => {
    await open();
    tick('Flex de carga');
    tick('Cable');
    fireEvent.click(screen.getByRole('button', { name: PUBLISH }));

    expect(dialog()).toHaveAttribute('aria-modal', 'true');
    expect(dialog()).toHaveAccessibleName('Publicar 2 productos en e-commerce');
    expect(within(dialog()).getByText(/No cambia si están activos, ni su precio, ni su stock/)).toBeInTheDocument();
    // Decisión D2: la imagen sólo se exige si la empresa lo configuró; el diálogo no promete otra cosa.
    expect(within(dialog()).getByText(/activos, con precio y con categoría; con imagen sólo si la empresa lo exige/))
      .toBeInTheDocument();

    fireEvent.click(within(dialog()).getByRole('button', { name: 'Cancelar' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(api.bulkSetProductVisibility).not.toHaveBeenCalled();
    // Cancelar no pierde lo marcado.
    expect(screen.getByText('2 productos seleccionados')).toBeInTheDocument();
  });

  it('ocultar lo dice con sus palabras y en singular cuando es uno', async () => {
    await open();
    tick('Funda');
    fireEvent.click(screen.getByRole('button', { name: HIDE }));
    expect(dialog()).toHaveAccessibleName('Ocultar 1 producto del e-commerce');
    expect(within(dialog()).getByText(/Seguirá en inventario/)).toBeInTheDocument();
  });

  it('se abre con el foco en «Cancelar», Escape la cierra y el foco vuelve al botón', async () => {
    await open();
    tick('Funda');
    const trigger = screen.getByRole('button', { name: HIDE });
    trigger.focus();
    fireEvent.click(trigger);

    expect(within(dialog()).getByRole('button', { name: 'Cancelar' })).toHaveFocus();
    fireEvent.keyDown(window, { key: 'Escape' });

    expect(screen.queryByRole('dialog')).toBeNull();
    expect(trigger).toHaveFocus();
    expect(api.bulkSetProductVisibility).not.toHaveBeenCalled();
  });

  it('el foco no sale del diálogo con Tab', async () => {
    await open();
    tick('Funda');
    fireEvent.click(screen.getByRole('button', { name: HIDE }));
    const cancel = within(dialog()).getByRole('button', { name: 'Cancelar' });
    const accept = within(dialog()).getByRole('button', { name: 'Confirmar' });

    accept.focus();
    fireEvent.keyDown(window, { key: 'Tab' });
    expect(cancel).toHaveFocus();
    fireEvent.keyDown(window, { key: 'Tab', shiftKey: true });
    expect(accept).toHaveFocus();
  });

  it('confirmar manda los marcados', async () => {
    await open();
    api.bulkSetProductVisibility.mockResolvedValueOnce({ action: 'hide', changed: 2, unchanged: 0, refused: [], total: 2 });
    tick('Funda');
    tick('Cable');
    fireEvent.click(screen.getByRole('button', { name: HIDE }));
    await confirm();

    expect(api.bulkSetProductVisibility).toHaveBeenCalledWith('hide', { ids: [1, 3] }, 7);
  });

  it('«todo lo que coincide» manda el filtro de la lista y el total que se mostró, no los ids de la página', async () => {
    await open({ count: 45 });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Internos' })); });
    await act(async () => { fireEvent.change(screen.getByLabelText('Categoría'), { target: { value: '5' } }); });
    await act(async () => { fireEvent.change(screen.getByLabelText('Tipo de producto'), { target: { value: 'false' } }); });
    await screen.findByText('Funda');
    api.bulkSetProductVisibility.mockResolvedValueOnce({ action: 'publish', changed: 45, unchanged: 0, refused: [], total: 45 });

    tickPage();
    fireEvent.click(screen.getByRole('button', { name: 'Seleccionar los 45 productos que coinciden' }));
    fireEvent.click(screen.getByRole('button', { name: PUBLISH }));
    expect(dialog()).toHaveAccessibleName('Publicar 45 productos en e-commerce');
    await confirm();

    expect(api.bulkSetProductVisibility).toHaveBeenCalledWith('publish', {
      filter: { search: undefined, category: '5', is_active: undefined, is_published_online: 'false', is_serialized: 'false', stock: undefined },
      expectedCount: 45,
    }, 7);
  });

  it('mientras se aplica no se puede confirmar dos veces', async () => {
    await open();
    let finish: (value: admin.BulkVisibilityResult) => void = () => {};
    api.bulkSetProductVisibility.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    tick('Funda');
    fireEvent.click(screen.getByRole('button', { name: HIDE }));
    await confirm();

    expect(within(dialog()).getByRole('button', { name: /Aplicando/ })).toBeDisabled();
    await act(async () => { finish({ action: 'hide', changed: 1, unchanged: 0, refused: [], total: 1 }); });
    expect(api.bulkSetProductVisibility).toHaveBeenCalledTimes(1);
  });
});

describe('el resultado', () => {
  it('dice cuántos cambiaron y cuántos ya estaban, recarga la lista y quita la selección', async () => {
    await open();
    api.bulkSetProductVisibility.mockResolvedValueOnce({ action: 'hide', changed: 2, unchanged: 1, refused: [], total: 3 });
    const before = api.fetchAdminProducts.mock.calls.length;
    tickPage();
    fireEvent.click(screen.getByRole('button', { name: HIDE }));
    await confirm();

    const result = await screen.findByRole('status');
    expect(result).toHaveTextContent('Se ocultaron 2 productos del e-commerce.');
    expect(result).toHaveTextContent('1 ya estaba oculto.');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(api.fetchAdminProducts.mock.calls.length).toBeGreaterThan(before);
    expect(screen.queryByText(/seleccionados?$/)).toBeNull();
  });

  it('al publicar, nombra los que no se publicaron y por qué, en español', async () => {
    await open();
    api.bulkSetProductVisibility.mockResolvedValueOnce({
      action: 'publish', changed: 1, unchanged: 0, total: 3,
      refused: [
        { id: 2, name: 'Flex de carga', problems: ['no_image', 'no_category'] },
        { id: 9, name: 'Batería vieja', problems: ['inactive', 'no_price'] },
      ],
    });
    tickPage();
    fireEvent.click(screen.getByRole('button', { name: PUBLISH }));
    await confirm();

    const result = await screen.findByRole('status');
    expect(result).toHaveTextContent('Se publicó 1 producto en e-commerce.');
    expect(result).toHaveTextContent('2 no se publicaron');
    const refused = within(result).getAllByRole('listitem').map((item) => item.textContent);
    expect(refused).toEqual([
      'Flex de carga: no tiene imagen, no tiene categoría',
      'Batería vieja: está inactivo, no tiene precio de venta',
    ]);
  });

  it('si no cambió nada, lo dice', async () => {
    await open();
    api.bulkSetProductVisibility.mockResolvedValueOnce({ action: 'publish', changed: 0, unchanged: 1, refused: [], total: 1 });
    tick('Funda');
    fireEvent.click(screen.getByRole('button', { name: PUBLISH }));
    await confirm();

    const result = await screen.findByRole('status');
    expect(result).toHaveTextContent('No se publicó ningún producto.');
    expect(result).toHaveTextContent('1 ya estaba publicado.');
  });

  it('el resultado se puede cerrar', async () => {
    await open();
    api.bulkSetProductVisibility.mockResolvedValueOnce({ action: 'hide', changed: 1, unchanged: 0, refused: [], total: 1 });
    tick('Funda');
    fireEvent.click(screen.getByRole('button', { name: HIDE }));
    await confirm();
    await screen.findByRole('status');

    fireEvent.click(screen.getByRole('button', { name: 'Cerrar resultado' }));
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('empezar otra acción retira el resultado anterior', async () => {
    await open();
    api.bulkSetProductVisibility.mockResolvedValueOnce({ action: 'hide', changed: 1, unchanged: 0, refused: [], total: 1 });
    tick('Funda');
    fireEvent.click(screen.getByRole('button', { name: HIDE }));
    await confirm();
    await screen.findByRole('status');

    tick('Cable');
    fireEvent.click(screen.getByRole('button', { name: PUBLISH }));
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('si la selección cambió, explica el nuevo total, no hace nada y vuelve a leer la lista', async () => {
    await open({ count: 45 });
    api.bulkSetProductVisibility.mockRejectedValueOnce(new SelectionChangedError(47));
    const before = api.fetchAdminProducts.mock.calls.length;
    tickPage();
    fireEvent.click(screen.getByRole('button', { name: 'Seleccionar los 45 productos que coinciden' }));
    fireEvent.click(screen.getByRole('button', { name: HIDE }));
    await confirm();

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('ahora coinciden 47 productos');
    expect(alert).toHaveTextContent('No se cambió nada');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByRole('status')).toBeNull();
    expect(api.fetchAdminProducts.mock.calls.length).toBeGreaterThan(before);
    expect(screen.queryByText(/seleccionados/)).toBeNull();
  });

  it('otro error se muestra y la selección se conserva para reintentar', async () => {
    await open();
    api.bulkSetProductVisibility.mockRejectedValueOnce(new Error('No tienes permisos para publicar u ocultar productos.'));
    tick('Funda');
    fireEvent.click(screen.getByRole('button', { name: HIDE }));
    await confirm();

    expect(await screen.findByRole('alert')).toHaveTextContent('No tienes permisos');
    expect(screen.getByText('1 producto seleccionado')).toBeInTheDocument();
  });
});

describe('lo que se ve es lo último que se pidió', () => {
  // Revisión, grupo 9 (a)–(d): cuatro maneras de acabar confirmando sobre algo
  // que la pantalla ya no enseña.

  it('(a) una respuesta lenta de antes no pisa a la de después', async () => {
    await open();
    let slow: (value: ReturnType<typeof page>) => void = () => {};
    api.fetchAdminProducts
      .mockReturnValueOnce(new Promise((resolve) => { slow = resolve; }))     // «Internos», lenta
      .mockResolvedValueOnce(page([cable]));                                  // «Publicados», rápida
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Internos' })); });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Publicados' })); });
    await screen.findByText('Cable');

    await act(async () => { slow(page([flex], 99)); });

    expect(screen.getByText('Cable')).toBeInTheDocument();
    expect(screen.queryByText('Flex de carga')).toBeNull();
    expect(screen.getByText(/^1 productos en total/)).toBeInTheDocument();
  });

  it('(b) desactivar una fila quita la selección: la fila puede dejar de estar a la vista', async () => {
    api.patchAdminProduct.mockResolvedValueOnce({ ...funda, is_active: false });
    await open();
    tick('Funda');
    tick('Cable');
    await act(async () => { fireEvent.click(within(rowOf('Funda')).getByRole('button', { name: 'Desactivar' })); });
    await screen.findByText('Funda');

    expect(screen.queryByText(/seleccionado/)).toBeNull();
    expect(screen.queryByRole('button', { name: HIDE })).toBeNull();
  });

  it('(c) si la selección se vacía con la confirmación abierta, la confirmación se cierra', async () => {
    await open();
    tick('Funda');
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'cable' } });   // el filtro llega 300 ms después
    fireEvent.click(screen.getByRole('button', { name: HIDE }));
    expect(dialog()).toBeInTheDocument();

    await act(async () => { jest.advanceTimersByTime(400); });

    expect(screen.queryByRole('dialog')).toBeNull();
    expect(api.bulkSetProductVisibility).not.toHaveBeenCalled();
  });

  it('(d) si la acción vacía la última página, se vuelve a la última que tiene algo', async () => {
    await open({ count: 45 });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Siguiente' })); });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Siguiente' })); });
    await screen.findByText('Funda');
    expect(lastQuery().page).toBe(3);

    // Tras ocultar, quedan 40: la página 3 ya no existe.
    api.fetchAdminProducts.mockImplementation(async (params) => (
      params?.page === 3 ? { count: 40, page: 3, page_size: 20, results: [] } : page([cable], 40)
    ));
    api.bulkSetProductVisibility.mockResolvedValueOnce({ action: 'hide', changed: 3, unchanged: 0, refused: [], total: 3 });
    tickPage();
    fireEvent.click(screen.getByRole('button', { name: HIDE }));
    await confirm();

    await screen.findByText('Cable');
    expect(lastQuery().page).toBe(2);
    expect(screen.getByText('Página 2 de 2')).toBeInTheDocument();
    expect(screen.queryByText('No hay productos que coincidan.')).toBeNull();
  });
});

describe('la empresa sobre la que se actúa', () => {
  // Revisión N5. La empresa se leía de `localStorage` AL ENVIAR: con dos pestañas
  // en dos empresas, «los N que coinciden» —un filtro y una cuenta, sin ids—
  // podía caer en la otra si las cuentas coincidían. La lista y la acción llevan
  // ahora, explícita, la empresa para la que se abrió la pantalla.
  const KEY = 'internal-selected-company';

  afterEach(() => window.localStorage.removeItem(KEY));

  it('la lista se pide para la empresa de la pantalla', async () => {
    window.localStorage.setItem(KEY, '7');
    await open();
    expect(lastQuery().company).toBe(7);
  });

  it('otra pestaña cambia de empresa entre la lista y la confirmación: la acción va a la de la lista', async () => {
    window.localStorage.setItem(KEY, '7');
    await open({ count: 45 });
    api.bulkSetProductVisibility.mockResolvedValueOnce({ action: 'hide', changed: 45, unchanged: 0, refused: [], total: 45 });
    tickPage();
    fireEvent.click(screen.getByRole('button', { name: 'Seleccionar los 45 productos que coinciden' }));

    window.localStorage.setItem(KEY, '9');                 // la otra pestaña eligió otra empresa
    fireEvent.click(screen.getByRole('button', { name: HIDE }));
    await confirm();

    expect(api.bulkSetProductVisibility.mock.calls[0][2]).toBe(7);
    // Y la lista que se vuelve a leer después, también.
    expect(lastQuery().company).toBe(7);
  });

  it('sin empresa resuelta (operador antiguo) no se inventa ninguna', async () => {
    api.fetchAdminProducts.mockResolvedValue(page([funda]));
    await act(async () => { render(<ProductsManager canManage />); });
    await screen.findByText('Funda');
    expect(lastQuery().company).toBeUndefined();
  });
});

describe('la llamada al servidor', () => {
  const real = jest.requireActual('@/app/lib/admin') as typeof admin;
  /* eslint-disable-next-line @typescript-eslint/no-require-imports */
  const { fetchWithAuth } = require('@/app/lib/auth') as { fetchWithAuth: jest.Mock };
  const answer = (status: number, body: unknown) => ({ ok: status < 400, status, json: async () => body });

  beforeEach(() => fetchWithAuth.mockReset());

  it('por ids manda la acción y los ids', async () => {
    fetchWithAuth.mockResolvedValueOnce(answer(200, { action: 'hide', changed: 1, unchanged: 0, refused: [], total: 1 }));
    await real.bulkSetProductVisibility('hide', { ids: [4, 8] });

    const [url, init] = fetchWithAuth.mock.calls[0];
    expect(url).toMatch(/\/admin\/products\/bulk-visibility\/$/);
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ action: 'hide', ids: [4, 8] });
  });

  it('por filtro manda sólo los filtros puestos y el total esperado', async () => {
    fetchWithAuth.mockResolvedValueOnce(answer(200, { action: 'publish', changed: 0, unchanged: 0, refused: [], total: 0 }));
    await real.bulkSetProductVisibility('publish', {
      filter: { search: '', category: '5', is_active: undefined, is_published_online: 'false' }, expectedCount: 12,
    });
    expect(JSON.parse(fetchWithAuth.mock.calls[0][1].body)).toEqual({
      action: 'publish', filter: { category: '5', is_published_online: 'false' }, expected_count: 12,
    });
  });

  it('un 409 `selection_changed` es un error con el nuevo total', async () => {
    fetchWithAuth.mockResolvedValueOnce(answer(409, { code: 'selection_changed', current_count: 47, detail: 'x' }));
    await expect(real.bulkSetProductVisibility('hide', { filter: {}, expectedCount: 45 }))
      .rejects.toMatchObject({ name: 'SelectionChangedError', currentCount: 47 });
  });

  it('la lista pide la visibilidad y el tipo', async () => {
    fetchWithAuth.mockResolvedValueOnce(answer(200, page([])));
    await real.fetchAdminProducts({ is_published_online: 'false', is_serialized: 'true', page: 2 });
    const url = String(fetchWithAuth.mock.calls[0][0]);
    expect(url).toContain('is_published_online=false');
    expect(url).toContain('is_serialized=true');
  });

  it('la lista y la acción llevan la empresa en la dirección, y lo guardado en el navegador no la cambia', async () => {
    const { withSelectedCompany } = jest.requireActual('@/app/lib/auth') as typeof import('@/app/lib/auth');
    window.localStorage.setItem('internal-selected-company', '9');
    fetchWithAuth.mockResolvedValue(answer(200, page([])));

    await real.fetchAdminProducts({ company: 7, page: 1 });
    await real.bulkSetProductVisibility('hide', { filter: {}, expectedCount: 0 }, 7);

    const [list, bulk] = fetchWithAuth.mock.calls.map(([url]) => withSelectedCompany(String(url)));
    expect(list).toMatch(/\/admin\/products\/\?company=7&page=1$/);
    expect(bulk).toMatch(/\/admin\/products\/bulk-visibility\/\?company=7$/);
    // Sin empresa explícita sigue valiendo la del navegador, como en el resto del panel.
    await real.bulkSetProductVisibility('hide', { ids: [1] });
    expect(withSelectedCompany(String(fetchWithAuth.mock.calls[2][0]))).toMatch(/bulk-visibility\/\?company=9$/);
    window.localStorage.removeItem('internal-selected-company');
  });

  it('cada código de problema tiene su texto', () => {
    for (const code of ['inactive', 'no_price', 'no_image', 'no_category']) {
      expect(real.publicationProblemLabel(code)).not.toBe(code);
    }
    expect(real.publicationProblemLabel('algo_nuevo')).toBe('algo_nuevo');
  });
});
