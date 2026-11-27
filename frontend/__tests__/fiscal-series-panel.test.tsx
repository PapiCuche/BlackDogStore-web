import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { FiscalSeriesPanel } from '@/app/admin/settings/fiscal-series/FiscalSeriesPanel';
import * as api from '@/app/lib/fiscal-series';

/**
 * SUNAT-SERIES-PANEL (C3) — the master creates the production series from the panel.
 * The rules are the server's; the screen sends what was typed and shows what it answers.
 */

jest.mock('@/app/lib/fiscal-series', () => {
  const actual = jest.requireActual('@/app/lib/fiscal-series');
  return {
    ...actual,
    fetchFiscalSeries: jest.fn(),
    createFiscalSeries: jest.fn(),
    updateFiscalSeries: jest.fn(),
    deleteFiscalSeries: jest.fn(),
  };
});

const COMPANY = { id: 7, name: 'Black Dog', slug: 'black-dog-store', tax_id: '20100066603' };
const USED = {
  id: 1, company: 7, branch: null, document_type: '01', document_label: 'Factura', series: 'F001',
  environment: 'beta' as const, next_number: 41, is_active: true, used: true, next_number_editable: false,
  deletable: false,
};
const LISTING = {
  companies: [COMPANY],
  document_types: [{ value: '01', label: 'Factura', letters: ['F'] }, { value: '03', label: 'Boleta', letters: ['B'] }],
  environments: [{ value: 'beta', label: 'Beta' }, { value: 'production', label: 'Producción' }],
  company: COMPANY,
  branches: [{ id: 3, name: 'Miraflores' }],
  series: [USED],
};

const mocked = api as jest.Mocked<typeof api>;

beforeEach(() => {
  jest.resetAllMocks();
  mocked.fetchFiscalSeries.mockResolvedValue(LISTING);
});

it('a used series shows its number and cannot be deleted', async () => {
  render(<FiscalSeriesPanel />);
  expect(await screen.findByText('F001')).toBeInTheDocument();
  expect(screen.queryByLabelText('Siguiente número de F001')).not.toBeInTheDocument();
  expect(screen.getByText('41')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Borrar' })).not.toBeInTheDocument();
});

it('creates a production series with what was typed, and says the production warning', async () => {
  mocked.createFiscalSeries.mockResolvedValue({ ...USED, id: 2, environment: 'production', used: false });
  render(<FiscalSeriesPanel />);
  await screen.findByText('F001');
  await userEvent.selectOptions(screen.getByLabelText('Ambiente'), 'production');
  expect(screen.getByText(/Serie de PRODUCCIÓN/)).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText(/^Serie/), 'f001');
  await userEvent.click(screen.getByRole('button', { name: 'Crear serie' }));
  await waitFor(() => expect(mocked.createFiscalSeries).toHaveBeenCalledWith({
    company: 7, branch: null, document_type: '01', series: 'F001', environment: 'production', next_number: 1,
    is_active: true,
  }));
});

it('shows the reason the server gives', async () => {
  mocked.createFiscalSeries.mockRejectedValue(new api.FiscalSeriesError('Revisa', 400, {
    is_active: 'Ya hay una serie de factura activa en la empresa para «beta» (F001). Desactívala primero.',
  }));
  render(<FiscalSeriesPanel />);
  await screen.findByText('F001');
  await userEvent.type(screen.getByLabelText(/^Serie/), 'F002');
  await userEvent.click(screen.getByRole('button', { name: 'Crear serie' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Desactívala primero');
});
