import { render, screen } from '@testing-library/react';
import { FiscalDocumentPanel } from '@/app/admin/components/FiscalDocumentPanel';
import { fetchWithAuth } from '@/app/lib/auth';
jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));

beforeEach(() => {
  jest.mocked(fetchWithAuth).mockResolvedValue({ ok: true, status: 200, json: async () => ({
    id: 1, identifier: 'B001-1', status: 'generated', status_label: 'Generado',
    environment_label: 'BETA', currency: 'PEN', total: '100.00', has_xml: false,
    can_submit: true, can_retry: false, can_download_pdf: false,
  }) } as Response);
});

it('offers signing a generated boleta before submission', async () => {
  render(<FiscalDocumentPanel orderId={1} isPaid receiptType="boleta" canIssue />);
  expect(await screen.findByRole('button', { name: 'Firmar comprobante' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Enviar a SUNAT' })).not.toBeInTheDocument();
});

it('keeps a read-only operator from seeing issuance actions', async () => {
  render(<FiscalDocumentPanel orderId={1} isPaid receiptType="factura" canIssue={false} />);
  await screen.findByText('B001-1');
  expect(screen.queryByRole('button', { name: 'Firmar comprobante' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Enviar a SUNAT' })).not.toBeInTheDocument();
});

it('renders a boleta for an operator who can only consult it', async () => {
  render(<FiscalDocumentPanel orderId={1} isPaid receiptType="boleta" canIssue={false} />);
  expect(await screen.findByText('B001-1')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Firmar comprobante' })).not.toBeInTheDocument();
});

it('does not offer individual submission for a signed boleta', async () => {
  jest.mocked(fetchWithAuth).mockResolvedValue({ ok: true, status: 200, json: async () => ({
    id: 1, identifier: 'B001-1', status: 'signed', status_label: 'Firmado',
    environment_label: 'BETA', currency: 'PEN', total: '100.00', has_xml: true,
    can_submit: true, can_retry: true, can_download_pdf: true,
  }) } as Response);
  render(<FiscalDocumentPanel orderId={1} isPaid receiptType="boleta" canIssue />);
  await screen.findByText('B001-1');
  expect(screen.queryByRole('button', { name: 'Enviar a SUNAT' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Reintentar el mismo comprobante' })).not.toBeInTheDocument();
  expect(screen.getByText(/Resumen Diario/)).toBeInTheDocument();
});
