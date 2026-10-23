import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { ProductCodesPanel } from '@/app/admin/components/ProductCodesPanel';
import { ProductForm } from '@/app/admin/components/ProductForm';
import * as codesApi from '@/app/admin/lib/product-codes-api';
import * as admin from '@/app/lib/admin';

/**
 * LABEL-01 · los códigos de un producto en el panel.
 *
 * El servidor decide qué código existe y quién puede agregarlos. El panel los
 * muestra, los copia y los descarga tal cual, y nunca inventa uno.
 */

jest.mock('@/app/admin/lib/product-codes-api', () => ({
  ...jest.requireActual('@/app/admin/lib/product-codes-api'),
  fetchProductCodes: jest.fn(),
  addProductCode: jest.fn(),
  generateProductCode: jest.fn(),
}));

jest.mock('@/app/lib/admin', () => ({
  ...jest.requireActual('@/app/lib/admin'),
  createAdminProduct: jest.fn(),
}));

const api = codesApi as jest.Mocked<typeof codesApi>;
const adminApi = admin as jest.Mocked<typeof admin>;

const row = (id: number, code: string, extra: Partial<codesApi.ProductCode> = {}): codesApi.ProductCode => ({
  id, code, symbology: 'unknown', symbology_label: 'Sin especificar', is_primary: false, is_active: true, ...extra,
});

beforeEach(() => {
  jest.clearAllMocks();
  URL.createObjectURL = jest.fn(() => 'blob:codigos');
  URL.revokeObjectURL = jest.fn();
});

describe('ver, copiar y descargar', () => {
  it('muestra los códigos tal cual, con ceros, mayúsculas y cuál es el principal', async () => {
    api.fetchProductCodes.mockResolvedValueOnce({
      results: [row(1, '0075012345678', { is_primary: true, symbology_label: 'EAN-13' }), row(2, 'abC-01'),
        row(3, 'VIEJO-1', { is_active: false })],
      can_manage: false,
    });
    render(<ProductCodesPanel productId={7} canManage={false} />);

    const items = await screen.findAllByRole('listitem');
    expect(items).toHaveLength(3);
    expect(within(items[0]).getByText('0075012345678')).toBeInTheDocument();
    expect(within(items[0]).getByText('Principal')).toBeInTheDocument();
    expect(within(items[1]).getByText('abC-01')).toBeInTheDocument();
    expect(within(items[2]).getByText('Desactivado')).toBeInTheDocument();
    expect(api.fetchProductCodes).toHaveBeenCalledWith(7, undefined);
  });

  it('copia el código exacto', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    api.fetchProductCodes.mockResolvedValueOnce({ results: [row(1, '0075012345678')], can_manage: false });
    render(<ProductCodesPanel productId={7} canManage={false} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Copiar el código 0075012345678' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('0075012345678'));
    expect(await screen.findByRole('status')).toHaveTextContent('copiado');
  });

  it('descarga los códigos activos, uno por línea', async () => {
    api.fetchProductCodes.mockResolvedValueOnce({
      results: [row(1, 'P000001', { is_primary: true }), row(2, '0075012345678'), row(3, 'OLD', { is_active: false })],
      can_manage: false,
    });
    render(<ProductCodesPanel productId={7} canManage={false} />);
    await screen.findAllByRole('listitem');

    fireEvent.click(screen.getByRole('button', { name: 'Descargar' }));
    const blob = (URL.createObjectURL as jest.Mock).mock.calls[0][0] as Blob;
    const text = await new Promise<string>((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.readAsText(blob);
    });
    expect(text).toBe('P000001\n0075012345678\n');
    expect(blob.type).toContain('text/plain');
    expect(codesApi.codesAsText([row(1, 'X1'), row(2, 'X2', { is_active: false })])).toBe('X1\n');
  });
});

describe('quién puede cambiar algo', () => {
  it('sin products.manage no ofrece agregar ni asignar', async () => {
    api.fetchProductCodes.mockResolvedValueOnce({ results: [], can_manage: false });
    render(<ProductCodesPanel productId={7} canManage />);
    await screen.findByText('Este producto no tiene códigos.');
    expect(screen.queryByRole('button', { name: 'Agregar' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Asignar código interno' })).not.toBeInTheDocument();
  });

  it('asigna un código interno sólo a un producto sin ninguno, y lo pide al servidor', async () => {
    api.fetchProductCodes.mockResolvedValueOnce({ results: [], can_manage: true });
    api.generateProductCode.mockResolvedValueOnce({ generated: true, code: 'P000001', results: [row(1, 'P000001')] });
    render(<ProductCodesPanel productId={7} canManage companyId={3} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Asignar código interno' }));
    expect(await screen.findByText('P000001')).toBeInTheDocument();
    expect(api.generateProductCode).toHaveBeenCalledWith(7, 3);
    expect(screen.queryByRole('button', { name: 'Asignar código interno' })).not.toBeInTheDocument();
  });

  it('agrega el código escrito y enseña el choque que nombra el servidor', async () => {
    api.fetchProductCodes.mockResolvedValueOnce({ results: [row(1, 'P000001')], can_manage: true });
    api.addProductCode.mockRejectedValueOnce(
      new codesApi.ProductCodeError('El código «FUNDA-01» ya identifica a «Funda A» en esta empresa.', 'code_conflict', 409));
    render(<ProductCodesPanel productId={7} canManage />);
    await screen.findByText('P000001');
    expect(screen.queryByRole('button', { name: 'Asignar código interno' })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Agregar un código'), { target: { value: 'FUNDA-01' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Agregar' })); });
    expect(api.addProductCode).toHaveBeenCalledWith(7, 'FUNDA-01', undefined);
    expect(screen.getByRole('alert')).toHaveTextContent('Funda A');
  });
});

describe('alta simple', () => {
  it('envía el código escrito tal cual y no envía ninguno si queda vacío', async () => {
    adminApi.createAdminProduct.mockResolvedValue({
      id: 9, name: 'Funda', slug: 'funda', description: '', price: '10.00', inventory: 0, image_url: '',
      category_id: null, category_name: null, is_active: true, is_published_online: true, created_at: '',
      updated_at: '', images: [],
    });
    const { unmount } = render(<ProductForm categories={[]} onSaved={jest.fn()} />);
    fireEvent.change(screen.getByLabelText(/Nombre/), { target: { value: 'Funda' } });
    fireEvent.change(screen.getByLabelText(/Precio/), { target: { value: '10' } });
    fireEvent.change(screen.getByLabelText(/Código/), { target: { value: ' 0075012345678 ' } });
    await act(async () => { fireEvent.submit(screen.getByLabelText(/Nombre/).closest('form')!); });
    expect(adminApi.createAdminProduct.mock.calls[0][0]).toMatchObject({ code: '0075012345678' });
    unmount();

    render(<ProductForm categories={[]} onSaved={jest.fn()} />);
    fireEvent.change(screen.getByLabelText(/Nombre/), { target: { value: 'Funda' } });
    fireEvent.change(screen.getByLabelText(/Precio/), { target: { value: '10' } });
    await act(async () => { fireEvent.submit(screen.getByLabelText(/Nombre/).closest('form')!); });
    expect(adminApi.createAdminProduct.mock.calls[1][0]).not.toHaveProperty('code');
  });
});
