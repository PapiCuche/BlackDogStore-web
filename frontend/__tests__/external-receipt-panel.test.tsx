import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ExternalReceiptPanel } from '@/app/admin/components/ExternalReceiptPanel';
import { ReceiptCoverageNotice } from '@/app/admin/components/ReceiptCoverageNotice';
import type { ExternalReceipt, ExternalReceiptBlock } from '@/app/lib/external-receipts';

/**
 * NOTE-NOT-SUBSTITUTE — registrar la boleta o factura emitida fuera del sistema.
 *
 * La pantalla dice «registrado», nunca «aceptado»: el sistema no consultó SUNAT.
 * Lo que se puede hacer lo decide el servidor (`can_register`, `can_void`).
 */

jest.mock('@/app/lib/external-receipts', () => ({
  fetchExternalReceipts: jest.fn(),
  registerExternalReceipt: jest.fn(),
  voidExternalReceipt: jest.fn(),
}));

// eslint-disable-next-line @typescript-eslint/no-require-imports
const api = require('@/app/lib/external-receipts') as {
  fetchExternalReceipts: jest.Mock; registerExternalReceipt: jest.Mock; voidExternalReceipt: jest.Mock;
};

const PENDING_WARNING = 'Esta venta no tiene comprobante electrónico emitido por el sistema: emite la boleta o factura por SUNAT (SOL u otro medio) al cobrar o entregar.';
const EXTERNAL_WARNING = 'Comprobante externo registrado: Boleta EB01-17 (SUNAT · SEE-SOL (Clave SOL)), emitido el 19/11/2026. El sistema no verifica su aceptación en SUNAT.';

const ROW: ExternalReceipt = {
  id: 9, document_type: '03', document_type_label: 'Boleta de venta (03)', series: 'EB01', number: 17,
  document_id: 'EB01-17', issue_date: '2026-11-19', channel: 'sunat_sol', channel_label: 'SUNAT · SEE-SOL (Clave SOL)',
  reference: 'Consulta SOL', status: 'registered', status_label: 'Registrado', issued_after_payment: false,
  registered_by: 'caja', registered_at: '2026-11-19T15:00:00Z', voided_by: null, voided_at: null, void_reason: '',
};

const block = (over: Partial<ExternalReceiptBlock>): ExternalReceiptBlock => ({
  registrations: [],
  coverage: { state: 'pending', integration_enabled: false, warning: PENDING_WARNING, receipt: '' },
  can_register: true, can_void: true,
  document_types: [{ value: '01', label: 'Factura (01)' }, { value: '03', label: 'Boleta de venta (03)' }],
  channels: [{ value: 'sunat_sol', label: 'SUNAT · SEE-SOL (Clave SOL)' }, { value: 'other', label: 'Otro medio' }],
  notice: 'Registra aquí la boleta o factura que emitiste fuera del sistema.',
  ...over,
});

beforeEach(() => jest.resetAllMocks());

it('registers the receipt and hands the new coverage to the order screen', async () => {
  api.fetchExternalReceipts.mockResolvedValue(block({}));
  const registered = block({
    registrations: [ROW], can_register: false,
    coverage: { state: 'external', integration_enabled: false, warning: EXTERNAL_WARNING, receipt: 'EB01-17' },
  });
  api.registerExternalReceipt.mockResolvedValue(registered);
  const onCoverage = jest.fn();
  const user = userEvent.setup();
  render(<ExternalReceiptPanel orderId={5} onCoverage={onCoverage} />);

  const form = await screen.findByRole('form', { name: 'Registrar comprobante externo' });
  await user.selectOptions(screen.getByLabelText('Tipo'), '03');
  await user.type(screen.getByLabelText('Serie'), 'EB01');
  await user.type(screen.getByLabelText('Número'), '17');
  await user.type(screen.getByLabelText('Fecha de emisión'), '2026-11-19');
  await user.type(screen.getByLabelText('Referencia verificable'), 'Consulta SOL');
  await user.click(screen.getByRole('button', { name: 'Registrar comprobante externo' }));

  await waitFor(() => expect(api.registerExternalReceipt).toHaveBeenCalledWith(5, expect.objectContaining({
    document_type: '03', series: 'EB01', number: '17', issue_date: '2026-11-19', channel: 'sunat_sol',
    reference: 'Consulta SOL',
  })));
  expect(onCoverage).toHaveBeenCalledWith(registered.coverage);
  expect(form).not.toBeInTheDocument();
  const row = screen.getByTestId('external-receipt-9');
  expect(row).toHaveTextContent('EB01-17');
  expect(row).toHaveTextContent('Registrado (no verificado en SUNAT por el sistema)');
  expect(row.textContent?.toLowerCase()).not.toContain('aceptad');
});

it('voids a registration with its reason', async () => {
  api.fetchExternalReceipts.mockResolvedValue(block({ registrations: [ROW], can_register: false }));
  api.voidExternalReceipt.mockResolvedValue(block({ registrations: [{ ...ROW, status: 'void', void_reason: 'Número mal escrito' }] }));
  const user = userEvent.setup();
  render(<ExternalReceiptPanel orderId={5} />);

  await user.click(await screen.findByRole('button', { name: 'Anular registro' }));
  await user.type(screen.getByLabelText(/Motivo de la anulación/), 'Número mal escrito');
  await user.click(screen.getAllByRole('button', { name: 'Anular registro' }).at(-1)!);

  await waitFor(() => expect(api.voidExternalReceipt).toHaveBeenCalledWith(5, 9, 'Número mal escrito'));
  expect(await screen.findByText('Anulado')).toBeInTheDocument();
});

it('says why the server refused', async () => {
  api.fetchExternalReceipts.mockResolvedValue(block({}));
  api.registerExternalReceipt.mockRejectedValue(new Error('Esta venta ya tiene registrado boleta EB01-17.'));
  const user = userEvent.setup();
  render(<ExternalReceiptPanel orderId={5} />);
  await screen.findByRole('form', { name: 'Registrar comprobante externo' });
  await user.type(screen.getByLabelText('Serie'), 'EB01');
  await user.type(screen.getByLabelText('Número'), '17');
  await user.type(screen.getByLabelText('Fecha de emisión'), '2026-11-19');
  await user.type(screen.getByLabelText('Referencia verificable'), 'SOL');
  await user.click(screen.getByRole('button', { name: 'Registrar comprobante externo' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('ya tiene registrado');
});

it('shows nothing to whoever may not see receipts, or when there is nothing to do', async () => {
  api.fetchExternalReceipts.mockResolvedValue(null);
  const { container, unmount } = render(<ExternalReceiptPanel orderId={5} />);
  await waitFor(() => expect(api.fetchExternalReceipts).toHaveBeenCalled());
  expect(container).toBeEmptyDOMElement();
  unmount();

  api.fetchExternalReceipts.mockResolvedValue(block({ can_register: false }));
  const second = render(<ExternalReceiptPanel orderId={5} />);
  await waitFor(() => expect(api.fetchExternalReceipts).toHaveBeenCalledTimes(2));
  expect(second.container).toBeEmptyDOMElement();
});

it('the order notice reads «registrado», not «aceptado»', () => {
  render(<ReceiptCoverageNotice coverage={{ state: 'external', integration_enabled: false, warning: EXTERNAL_WARNING, receipt: 'EB01-17' }} />);
  const notice = screen.getByTestId('receipt-coverage');
  expect(notice).toHaveAttribute('data-state', 'external');
  expect(notice).toHaveTextContent('Comprobante externo registrado');
  expect(screen.queryByRole('alert')).toBeNull();
});
