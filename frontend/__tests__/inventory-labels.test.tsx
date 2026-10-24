/**
 * LABEL-03 · «Etiquetas y códigos de barras».
 *
 * La pantalla elige productos y equipos de UNA sucursal, pide la vista previa y
 * descarga el PDF. Lo que se fija aquí: la selección viaja como ids y copias (el
 * servidor decide el resto), lo que está «Para revisar» se ve con su motivo, y la
 * descarga dice cuántas etiquetas salieron sin marcar nada como impreso.
 */
import { act, fireEvent, render, screen } from '@testing-library/react';

import { LabelsWorkbench } from '@/app/admin/inventory/labels/LabelsWorkbench';
import { labelsQuery } from '@/app/admin/inventory/labels/labels-api';
import { INTERNAL_MODULES } from '@/app/admin/lib/internal-modules';
import { fetchWithAuth } from '@/app/lib/auth';
import { fetchBranchStock } from '@/app/lib/inventory';
import { fetchSerializedProducts, fetchStockUnits } from '@/app/lib/stock-units';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/inventory', () => ({ fetchBranchStock: jest.fn() }));
jest.mock('@/app/lib/stock-units', () => ({ fetchStockUnits: jest.fn(), fetchSerializedProducts: jest.fn() }));

const fetchMock = fetchWithAuth as jest.Mock;

const preview = {
  labels: [
    {
      kind: 'accessory', ref: 11, title: 'Cable ficticio', status: 'ready', reasons: [], warnings: [],
      copies: 3, stickers: 1, pages: 3,
      layout: { key: 'accessory_30x20', name: 'Accesorio 30×20 mm', width_mm: 30, height_mm: 20 },
      code: { value: 'C000701', symbology: 'code128', kind: 'product_code', width_mm: 27.53, module_mm: 0.25, module_dots: 2 },
      lines: ['Cable ficticio', 'S/ 39.90', 'C000701'], has_logo: false, sensitive: false,
    },
    {
      kind: 'device', ref: 21, title: 'Teléfono ficticio', status: 'review',
      reasons: ['El equipo aún no tiene código interno de etiqueta (llega con LABEL-02).'], warnings: [],
      copies: 1, stickers: 0, pages: 0,
      layout: { key: 'device_commercial_50x25', name: 'Teléfono 50×25 mm', width_mm: 50, height_mm: 25 },
      code: null, lines: [], has_logo: false, sensitive: false,
    },
  ],
  summary: { ready: 1, review: 1, pages: 3 },
  permissions: { sensitive: false, device_profile: 'internal' },
};

beforeEach(() => {
  fetchMock.mockReset();
  (fetchBranchStock as jest.Mock).mockResolvedValue({
    results: [
      { id: 1, product: 11, product_name: 'Cable ficticio', quantity: 12 },
      { id: 2, product: 31, product_name: 'Teléfono ficticio (modelo)', quantity: 2 },
    ],
  });
  (fetchSerializedProducts as jest.Mock).mockResolvedValue({
    results: [{ id: 31, name: 'Teléfono ficticio (modelo)', requires_imei: true, price: '1.00' }],
  });
  (fetchStockUnits as jest.Mock).mockResolvedValue({
    results: [{ id: 21, product_name: 'Teléfono ficticio', serial_number: 'FICT01' }],
  });
});

async function pickBoth() {
  render(<LabelsWorkbench branch={3} />);
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Buscar' })); });
  fireEvent.click(screen.getByRole('button', { name: 'Agregar Cable ficticio' }));
  fireEvent.click(screen.getByRole('button', { name: 'Agregar equipo FICT01' }));
  fireEvent.change(screen.getByLabelText('Copias de Cable ficticio'), { target: { value: '3' } });
}

describe('Etiquetas y códigos de barras', () => {
  it('está en el menú de Inventario con inventory.view', () => {
    const entry = INTERNAL_MODULES.find((m) => m.id === 'inventory.labels');
    expect(entry).toMatchObject({
      group: 'inventory', href: '/admin/inventory/labels', requiredCapabilities: ['inventory.view'],
    });
  });

  it('la selección viaja como ids y copias, y la sucursal la elige quien mira', () => {
    expect(labelsQuery({ branch: 3, products: [{ id: 11, copies: 3 }], units: [21, 22] }))
      .toBe('?branch=3&products=11%3A3&units=21%2C22');
  });

  it('muestra lo listo y lo que hay que revisar, con su motivo', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => preview });
    await pickBoth();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Vista previa' })); });

    expect(fetchMock.mock.calls[0][0]).toContain('/admin/inventory/labels/preview/?branch=3&products=11%3A3&units=21');
    expect(screen.getByText('Lista')).toBeInTheDocument();
    expect(screen.getByText('Para revisar')).toBeInTheDocument();
    expect(screen.getByText(/llega con LABEL-02/)).toBeInTheDocument();
    expect(screen.getByText(/C000701 \(27.53 mm\)/)).toBeInTheDocument();
  });

  it('descarga el PDF y dice cuántas etiquetas salieron, sin marcar nada como impreso', async () => {
    const headers = new Map([['X-Labels-Pages', '3'], ['X-Labels-Review', '1']]);
    fetchMock.mockResolvedValue({
      ok: true, blob: async () => new Blob(['%PDF']), headers: { get: (k: string) => headers.get(k) ?? null },
    });
    const createObjectURL = jest.fn(() => 'blob:x');
    Object.assign(URL, { createObjectURL, revokeObjectURL: jest.fn() });
    const click = jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    await pickBoth();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Descargar PDF' })); });

    expect(fetchMock.mock.calls[0][0]).toContain('/admin/inventory/labels/pdf/?branch=3');
    expect(fetchMock.mock.calls[0][1]).toBeUndefined();          // un GET: nada se escribe
    expect(click).toHaveBeenCalled();
    expect(screen.getByRole('status')).toHaveTextContent('3 etiquetas; 1 quedan para revisar');
    expect(screen.getByText(/Descargarlo no marca nada/)).toBeInTheDocument();
    click.mockRestore();
  });

  it('los productos con número de serie no se ofrecen como accesorios', async () => {
    render(<LabelsWorkbench branch={3} />);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Buscar' })); });
    const accessories = screen.getByRole('list', { name: 'Accesorios encontrados' });
    expect(accessories).toHaveTextContent('Cable ficticio');
    expect(accessories).not.toHaveTextContent('Teléfono ficticio (modelo)');
  });

  it('con todo para revisar (409) muestra el motivo de cada etiqueta', async () => {
    fetchMock.mockResolvedValue({
      ok: false, status: 409,
      json: async () => ({ ...preview, summary: { ready: 0, review: 2, pages: 0 }, detail: 'Ninguna etiqueta está lista: todas necesitan revisión.' }),
    });
    await pickBoth();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Descargar PDF' })); });
    expect(screen.getByText('Ninguna etiqueta está lista: todas necesitan revisión.')).toBeInTheDocument();
    expect(screen.getByText(/llega con LABEL-02/)).toBeInTheDocument();
    expect(screen.getByText('Vista previa', { selector: 'h2, h3' })).toBeInTheDocument();
  });

  it('un error del servidor se muestra tal cual', async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 409, json: async () => ({ detail: 'Ninguna etiqueta está lista.' }) });
    await pickBoth();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Descargar PDF' })); });
    expect(screen.getByText('Ninguna etiqueta está lista.')).toBeInTheDocument();
  });
});
