import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { CatalogModelsManager } from '@/app/admin/components/CatalogModelsManager';
import { UsedUnitsPanel } from '@/app/admin/components/UsedUnitsPanel';
import { INTERNAL_MODULES } from '@/app/admin/lib/internal-modules';
import { isValidSwatchHex } from '@/app/admin/lib/internal-api';
import { fetchWithAuth } from '@/app/lib/auth';

import { reply } from './support/device-commerce-fixtures';

/**
 * DEVICE-COMMERCE-01 · DC2 — el panel: modelos, atributos con muestra de
 * color, escala de estado y la ficha de cada seminuevo.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

type Call = { method: string; path: string; body: unknown };
let writes: Call[];

const ATTRIBUTES = [
  { id: 3, name: 'Capacidad', kind: 'text', sort_order: 0, options: [{ id: 11, label: '128 GB', swatch_hex: '', sort_order: 0 }] },
  { id: 4, name: 'Color', kind: 'color', sort_order: 1, options: [{ id: 21, label: 'Azul', swatch_hex: '#1F3A93', sort_order: 0 }] },
];

const UNIT = {
  id: 7, product_id: 52, product_name: 'Equipo X seminuevo', branch_id: 1, branch_name: 'Central',
  serial_number: 'SER123', imei: '356789012345678', imei2: null, condition: 'used', condition_label: 'Usado',
  status: 'available', status_label: 'Disponible', received_at: '2026-10-01T10:00:00Z', sold_at: null, order_id: null,
  notes: '', public_id: 'abc', battery_health: null, cosmetic_grade: null, cosmetic_grade_label: '', public_notes: '',
  sale_price: null, condition_line: 'used', photos: [{ id: 1, url: 'http://localhost/p.png', alt_text: '' }],
};

beforeEach(() => {
  writes = [];
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? 'GET').toUpperCase();
    if (method !== 'GET') {
      writes.push({ method, path: url.replace(/^.*\/api/, ''), body: init.body instanceof FormData ? init.body : JSON.parse(String(init.body ?? '{}')) });
      if (url.includes('/inventory/units/7/')) return reply({ ...UNIT, battery_health: 88, sale_price: '2599.00' });
      return reply({ id: 99, label: 'x', swatch_hex: '', sort_order: 9 }, 201);
    }
    if (url.includes('/admin/catalog/attributes/')) return reply(ATTRIBUTES);
    if (url.includes('/admin/catalog/models/')) return reply([]);
    if (url.includes('/admin/catalog/grades/')) return reply([{ id: 5, code: 'A', label: 'Excelente', description: '', sort_order: 0, is_active: true }]);
    if (url.includes('/admin/catalog/settings/')) return reply({ new_line_label: '', used_line_label: '' });
    if (url.includes('/admin/categories/')) return reply([]);
    if (url.includes('/admin/inventory/units/')) {
      return reply({ results: [UNIT], count: 1, page: 1, page_size: 25, statuses: [], conditions: [] });
    }
    return reply({}, 404);
  });
});

describe('muestra de color de una opción', () => {
  it('la regla es #RRGGBB y nada más', () => {
    expect(isValidSwatchHex('#1F2937')).toBe(true);
    expect(isValidSwatchHex('#abcdef')).toBe(true);
    for (const bad of ['1F2937', '#1F293', '#1F29377', '#GGGGGG', 'red', '']) expect(isValidSwatchHex(bad)).toBe(false);
  });

  async function colourAttribute() {
    render(<CatalogModelsManager canManage companyId={null} />);
    return screen.findByRole('listitem', { name: 'Color' });
  }

  it('un color mal escrito no se envía y se dice junto al campo', async () => {
    const color = await colourAttribute();
    fireEvent.change(within(color).getByLabelText('Opción'), { target: { value: 'Verde' } });
    fireEvent.change(within(color).getByLabelText('Color (#RRGGBB)'), { target: { value: '#12345' } });
    await act(async () => { fireEvent.click(within(color).getByRole('button', { name: 'Agregar opción' })); });
    expect(within(color).getByRole('alert')).toHaveTextContent('Usa el formato #RRGGBB');
    expect(within(color).getByLabelText('Color (#RRGGBB)')).toHaveAttribute('aria-invalid', 'true');
    expect(writes).toEqual([]);
  });

  it('la muestra se ve mientras se escribe, y un color válido se guarda', async () => {
    const color = await colourAttribute();
    const preview = within(color).getAllByTestId('swatch-preview').at(-1)!;
    fireEvent.change(within(color).getByLabelText('Opción'), { target: { value: 'Verde' } });
    fireEvent.change(within(color).getByLabelText('Color (#RRGGBB)'), { target: { value: '#14a44d' } });
    expect(preview).toHaveStyle({ backgroundColor: '#14a44d' });
    await act(async () => { fireEvent.click(within(color).getByRole('button', { name: 'Agregar opción' })); });
    expect(writes).toEqual([{ method: 'POST', path: '/admin/catalog/attributes/4/options/', body: { label: 'Verde', swatch_hex: '#14A44D' } }]);
  });

  it('FASE 1.2 · Y: una muestra aproximada se rotula y la casilla se guarda al editar', async () => {
    const blue = ATTRIBUTES[1].options[0] as Record<string, unknown>;
    blue.swatch_is_approximate = true;
    try {
      const color = await colourAttribute();
      expect(within(color).getByText('Aproximada')).toBeInTheDocument();
      fireEvent.click(within(color).getByRole('button', { name: 'Editar Azul' }));
      const check = within(color).getByRole('checkbox', { name: 'Muestra aproximada (pendiente de validación comercial)' });
      expect(check).toBeChecked();
      fireEvent.click(check);
      await act(async () => { fireEvent.click(within(color).getByRole('button', { name: 'Guardar' })); });
      expect(writes.at(-1)).toEqual({ method: 'PATCH', path: '/admin/catalog/options/21/',
        body: { label: 'Azul', swatch_hex: '#1F3A93', sort_order: 0, swatch_is_approximate: false } });
    } finally {
      delete blue.swatch_is_approximate;
    }
  });

  it('una opción de texto no pide color', async () => {
    render(<CatalogModelsManager canManage companyId={null} />);
    const capacity = await screen.findByRole('listitem', { name: 'Capacidad' });
    expect(within(capacity).queryByLabelText('Color (#RRGGBB)')).toBeNull();
  });

  it('sin `products.manage` no hay formularios', async () => {
    render(<CatalogModelsManager canManage={false} companyId={null} />);
    await screen.findByRole('listitem', { name: 'Color' });
    expect(screen.queryByRole('button', { name: 'Agregar opción' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Crear escala sugerida' })).toBeNull();
    expect(screen.queryByRole('button', { name: '+ Nuevo modelo' })).toBeNull();
  });

  /** INV-GRADES-01: the grades panel alone, answering every other read with an empty list. */
  function gradesServer(grade: Record<string, unknown>) {
    send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = String(input);
      const method = (init.method ?? 'GET').toUpperCase();
      if (method !== 'GET') {
        writes.push({ method, path: url.replace(/^.*\/api/, ''), body: JSON.parse(String(init.body ?? '{}')) });
        return reply(grade);
      }
      if (url.includes('/admin/catalog/grades/')) return reply([grade]);
      if (url.includes('/admin/catalog/settings/')) return reply({ new_line_label: '', used_line_label: '' });
      return reply([]);
    });
  }

  it('INV-GRADES-01: editar un grado sin uso envía nombre, descripción, orden y activo', async () => {
    gradesServer({ id: 5, code: 'A', label: 'Excelente', description: '', sort_order: 0, is_active: true, in_use: false });
    render(<CatalogModelsManager canManage companyId={null} />);
    const editButton = await screen.findByRole('button', { name: 'Editar Excelente' });
    await act(async () => { fireEvent.click(editButton); });
    fireEvent.change(screen.getByLabelText('Nombre de A'), { target: { value: 'Impecable' } });
    fireEvent.change(screen.getByLabelText('Orden de A'), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Activo' }));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Guardar' })); });
    expect(writes.map((w) => `${w.method} ${w.path}`)).toEqual(['PATCH /admin/catalog/grades/5/']);
    expect(writes[0].body).toEqual({ label: 'Impecable', description: '', sort_order: 3, is_active: false });
  });

  it('INV-GRADES-01: un grado que ya tienen equipos no deja cambiar el nombre ni lo envía', async () => {
    gradesServer({ id: 5, code: 'A', label: 'Excelente', description: '', sort_order: 0, is_active: true, in_use: true });
    render(<CatalogModelsManager canManage companyId={null} />);
    const editButton = await screen.findByRole('button', { name: 'Editar Excelente' });
    await act(async () => { fireEvent.click(editButton); });
    expect(screen.getByLabelText('Nombre de A')).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Descripción de A'), { target: { value: 'Leve' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Guardar' })); });
    expect(writes[0].body).toEqual({ description: 'Leve', sort_order: 0, is_active: true });
  });

  it('«Crear escala sugerida» llama a seed', async () => {
    render(<CatalogModelsManager canManage companyId={null} />);
    await act(async () => { fireEvent.click(await screen.findByRole('button', { name: 'Crear escala sugerida' })); });
    expect(writes.map((w) => `${w.method} ${w.path}`)).toEqual(['POST /admin/catalog/grades/seed/']);
  });
});

describe('Inventario › Seminuevos', () => {
  it('lista sólo la línea de seminuevos, con la batería sin lectura como «no informado»', async () => {
    render(<UsedUnitsPanel branch="all" canEdit canSeeCost={false} />);
    const row = (await screen.findByText('Equipo X seminuevo')).closest('tr') as HTMLElement;
    expect(within(row).getByText('no informado')).toBeInTheDocument();
    expect(within(row).getByText('356789012345678')).toBeInTheDocument();
    const listCall = send.mock.calls.map(([u]) => String(u)).find((u) => u.includes('/inventory/units/'))!;
    expect(listCall).toContain('line=used');
  });

  it('editar batería y precio manda sólo lo que cambió, y el precio pide confirmación', async () => {
    render(<UsedUnitsPanel branch="all" canEdit canManagePrice canSeeCost={false} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Editar SER123' }));
    const dialog = screen.getByRole('dialog', { name: 'Equipo X seminuevo' });
    fireEvent.change(within(dialog).getByLabelText('Batería (%)'), { target: { value: '88' } });
    fireEvent.change(within(dialog).getByLabelText(/Precio de venta \(S\/\)/), { target: { value: '2599' } });
    fireEvent.change(within(dialog).getByLabelText('Estado estético'), { target: { value: '5' } });
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Guardar' })); });
    expect(writes).toEqual([]);
    const confirm = within(dialog).getByRole('alertdialog', { name: 'Confirmar el precio de venta' });
    expect(confirm).toHaveTextContent('Vas a fijar el precio de venta en S/ 2,599.00');
    await act(async () => { fireEvent.click(within(confirm).getByRole('button', { name: 'Sí, guardar este precio' })); });
    expect(writes[0]).toEqual({
      method: 'PATCH', path: '/admin/inventory/units/7/',
      body: { battery_health: 88, cosmetic_grade: 5, sale_price: '2599' },
    });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('una batería imposible no se envía', async () => {
    render(<UsedUnitsPanel branch="all" canEdit canSeeCost={false} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Editar SER123' }));
    const dialog = screen.getByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Batería (%)'), { target: { value: '140' } });
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Guardar' })); });
    expect(within(dialog).getByRole('alert')).toHaveTextContent('0 a 100');
    expect(writes).toEqual([]);
  });

  it('las fotos se suben como archivo y se eliminan una por una', async () => {
    render(<UsedUnitsPanel branch="all" canEdit canSeeCost={false} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Editar SER123' }));
    const dialog = screen.getByRole('dialog');
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Eliminar foto 1' })); });
    expect(writes[0]).toMatchObject({ method: 'DELETE', path: '/admin/inventory/units/7/photos/1/' });
    const file = new File(['x'], 'frente.png', { type: 'image/png' });
    fireEvent.change(within(dialog).getByLabelText('Nueva foto'), { target: { files: [file] } });
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Subir foto' })); });
    expect(writes[1].path).toBe('/admin/inventory/units/7/photos/');
    expect((writes[1].body as FormData).get('file')).toBe(file);
  });

  it('sin `inventory.adjust` la ficha se ve pero no se cambia', async () => {
    render(<UsedUnitsPanel branch="all" canEdit={false} canSeeCost={false} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Ver SER123' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByLabelText('Batería (%)')).toBeDisabled();
    expect(within(dialog).queryByRole('button', { name: 'Guardar' })).toBeNull();
  });
});

describe('navegación', () => {
  it('«Modelos» y «Seminuevos» están en el menú, con la capacidad de su pantalla', () => {
    const models = INTERNAL_MODULES.find((m) => m.href === '/admin/products/models');
    const used = INTERNAL_MODULES.find((m) => m.href === '/admin/inventory/used');
    // COMMERCE-ADMIN-UX-01 renamed them after the owner's tree.
    expect(models).toMatchObject({ group: 'products', label: 'Modelos y variantes', requiredCapabilities: ['products.view'] });
    expect(used).toMatchObject({ group: 'inventory', label: 'Equipos seminuevos', requiredCapabilities: ['inventory.view'] });
  });
});
