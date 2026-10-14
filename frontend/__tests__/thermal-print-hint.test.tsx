import { render, screen } from '@testing-library/react';

/**
 * PRINT-THERMAL-01. Junto al PDF de 80 mm y a los botones de imprimir, una
 * frase: ese PDF se imprime con el controlador de la propia térmica (no con
 * «Generic PostScript», que la hace imprimir código como texto), y la
 * impresión directa en la térmica va por el agente de impresión.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const mockInventory = { fetchSalesNote: jest.fn() };
jest.mock('@/app/lib/inventory', () => ({
  ...jest.requireActual('@/app/lib/inventory'),
  fetchSalesNote: (...a: unknown[]) => mockInventory.fetchSalesNote(...a),
}));

/* eslint-disable @typescript-eslint/no-require-imports */
const { ThermalPrintHint, THERMAL_PRINT_HINT } = require('@/app/admin/components/ThermalPrintHint');
const { FiscalDocumentPanel } = require('@/app/admin/components/FiscalDocumentPanel');
const { SalesNotePanel } = require('@/app/admin/components/SalesNotePanel');
const { fetchWithAuth } = require('@/app/lib/auth');
/* eslint-enable @typescript-eslint/no-require-imports */

const HINT = /controlador de la propia térmica/;

it('dice con qué se imprime el PDF de 80 mm y por dónde va la impresión directa', () => {
  render(<ThermalPrintHint />);

  const hint = screen.getByText(HINT);
  expect(hint).toHaveTextContent('Generic PostScript');
  expect(hint).toHaveTextContent('agente de impresión');
  expect(THERMAL_PRINT_HINT.length).toBeLessThan(200);
});

it('acompaña al ticket de 80 mm del comprobante electrónico', async () => {
  jest.mocked(fetchWithAuth).mockResolvedValue({ ok: true, status: 200, json: async () => ({
    id: 1, identifier: 'B001-1', status: 'signed', status_label: 'Firmado',
    environment_label: 'BETA', currency: 'PEN', total: '100.00', has_xml: true,
    can_submit: false, can_retry: false, can_download_pdf: true,
  }) } as Response);

  render(<FiscalDocumentPanel orderId={1} isPaid receiptType="boleta" canIssue={false} />);

  expect(await screen.findByRole('button', { name: 'Ticket 80 mm' })).toBeInTheDocument();
  expect(screen.getByText(HINT)).toBeInTheDocument();
});

it('no aparece si el comprobante todavía no tiene PDF', async () => {
  jest.mocked(fetchWithAuth).mockResolvedValue({ ok: true, status: 200, json: async () => ({
    id: 1, identifier: 'B001-1', status: 'generated', status_label: 'Generado',
    environment_label: 'BETA', currency: 'PEN', total: '100.00', has_xml: false,
    can_submit: true, can_retry: false, can_download_pdf: false,
  }) } as Response);

  render(<FiscalDocumentPanel orderId={1} isPaid receiptType="boleta" canIssue={false} />);

  await screen.findByText('B001-1');
  expect(screen.queryByText(HINT)).toBeNull();
});

it('acompaña a los botones de la nota de venta', async () => {
  mockInventory.fetchSalesNote.mockResolvedValue({
    id: 3, number: 'NV-000001', status: 'issued', status_label: 'Emitida',
    created_at: '2026-10-14T10:00:00Z', created_by_username: 'caja',
  });

  render(<SalesNotePanel orderId={1} isPaid />);

  expect(await screen.findByRole('button', { name: 'Imprimir ticket 80 mm' })).toBeInTheDocument();
  expect(screen.getByText(HINT)).toBeInTheDocument();
});
