import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { TransferDevices, devicesPending } from '@/app/admin/inventory/transfers/_components/TransferDevices';
import type { StockTransfer } from '@/app/lib/inventory';
import * as inventory from '@/app/lib/inventory';

/** SERIAL-TRANSFER-01 — devices travel by reading them: added at the origin, read again at the destination. */
jest.mock('@/app/lib/inventory', () => ({
  ...jest.requireActual('@/app/lib/inventory'),
  addTransferUnit: jest.fn(),
  removeTransferUnit: jest.fn(),
  verifyTransferUnit: jest.fn(),
}));
const add = inventory.addTransferUnit as jest.Mock;
const remove = inventory.removeTransferUnit as jest.Mock;
const verify = inventory.verifyTransferUnit as jest.Mock;

const IMEI = '356938035643809';

function transfer(over: Partial<StockTransfer> = {}, verified = false): StockTransfer {
  return {
    id: 7, company: 1, source_branch: 1, source_branch_name: 'Centro', destination_branch: 2,
    destination_branch_name: 'Norte', status: 'draft', status_label: 'Borrador', reason: '', reference: '',
    items: [{
      id: 3, product: 10, product_name: 'iPhone 16 128 GB Negro', product_slug: 'ip16', quantity: 1, is_serialized: true,
      units: [{ id: 41, identifier: 'F2LX0001', serial_number: 'F2LX0001', imei: IMEI, label_code: 'U1', status: 'available',
        method: 'scanner', received_at: verified ? '2026-11-18T10:00:00Z' : null }],
    }],
    total_units: 1, units_verified: verified ? 1 : 0, created_by: 1, created_by_username: 'ana', created_at: '',
    dispatched_at: null, received_at: null, cancelled_at: null, updated_at: '', ...over,
  };
}

beforeEach(() => { add.mockReset(); remove.mockReset(); verify.mockReset(); });

test('in a draft a device is added by reading it, and the updated document is handed back', async () => {
  const updated = transfer();
  add.mockResolvedValue(updated);
  const onUpdated = jest.fn();
  render(<TransferDevices transfer={transfer({ items: [] })} mayTransfer onUpdated={onUpdated} />);
  const box = screen.getByLabelText(/Escanear equipo/);
  fireEvent.change(box, { target: { value: IMEI } });
  fireEvent.keyDown(box, { key: 'Enter' });
  await waitFor(() => expect(add).toHaveBeenCalledWith(7, IMEI, 'scanner'));
  expect(onUpdated).toHaveBeenCalledWith(updated);
  expect((box as HTMLInputElement).value).toBe('');
});

test('typed by hand it says so to the server', async () => {
  add.mockResolvedValue(transfer());
  render(<TransferDevices transfer={transfer({ items: [] })} mayTransfer onUpdated={jest.fn()} />);
  fireEvent.click(screen.getByLabelText(/Lo escribí a mano/));
  const box = screen.getByLabelText(/Escanear equipo/);
  fireEvent.change(box, { target: { value: IMEI } });
  fireEvent.keyDown(box, { key: 'Enter' });
  await waitFor(() => expect(add).toHaveBeenCalledWith(7, IMEI, 'manual'));
});

test('a refused read is said and nothing else changes', async () => {
  add.mockRejectedValue(new Error('Ese equipo no está en esta sucursal.'));
  const onUpdated = jest.fn();
  render(<TransferDevices transfer={transfer({ items: [] })} mayTransfer onUpdated={onUpdated} />);
  const box = screen.getByLabelText(/Escanear equipo/);
  fireEvent.change(box, { target: { value: 'XYZ' } });
  fireEvent.keyDown(box, { key: 'Enter' });
  expect(await screen.findByRole('alert')).toHaveTextContent('no está en esta sucursal');
  expect(onUpdated).not.toHaveBeenCalled();
});

test('a device of the draft can be removed', async () => {
  remove.mockResolvedValue(transfer({ items: [] }));
  render(<TransferDevices transfer={transfer()} mayTransfer onUpdated={jest.fn()} />);
  const list = screen.getByRole('list', { name: /Equipos de iPhone 16/ });
  fireEvent.click(within(list).getByRole('button', { name: /Quitar F2LX0001/ }));
  await waitFor(() => expect(remove).toHaveBeenCalledWith(7, 41));
});

test('in transit the destination reads each device and sees how many are left', async () => {
  verify.mockResolvedValue(transfer({ status: 'in_transit' }, true));
  render(<TransferDevices transfer={transfer({ status: 'in_transit' })} mayTransfer onUpdated={jest.fn()} />);
  expect(screen.getByText('0 de 1 equipos verificados')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Quitar/ })).toBeNull();
  const box = screen.getByLabelText(/Verificar llegada/);
  fireEvent.change(box, { target: { value: IMEI } });
  fireEvent.keyDown(box, { key: 'Enter' });
  await waitFor(() => expect(verify).toHaveBeenCalledWith(7, IMEI, 'scanner'));
});

test('devicesPending counts the devices still to read at the destination', () => {
  expect(devicesPending(transfer({ status: 'in_transit' }))).toBe(1);
  expect(devicesPending(transfer({ status: 'in_transit' }, true))).toBe(0);
  expect(devicesPending(transfer({ items: [] }))).toBe(0);
});

test('whoever can only look sees the devices and no reader', () => {
  render(<TransferDevices transfer={transfer()} mayTransfer={false} onUpdated={jest.fn()} />);
  expect(screen.getByText('F2LX0001')).toBeInTheDocument();
  expect(screen.queryByLabelText(/Escanear equipo/)).toBeNull();
});

test('the reader keeps the focus for the next read, and a refused code is selected to be replaced', async () => {
  add.mockResolvedValueOnce(transfer()).mockRejectedValueOnce(new Error('Ese equipo ya está en esta transferencia.'));
  render(<TransferDevices transfer={transfer({ items: [] })} mayTransfer onUpdated={jest.fn()} />);
  const box = screen.getByLabelText(/Escanear equipo/) as HTMLInputElement;
  box.focus();
  fireEvent.change(box, { target: { value: IMEI } });
  fireEvent.keyDown(box, { key: 'Enter' });
  await waitFor(() => expect(add).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(document.activeElement).toBe(box));
  expect(box).not.toBeDisabled();

  fireEvent.change(box, { target: { value: IMEI } });
  fireEvent.keyDown(box, { key: 'Enter' });
  expect(await screen.findByRole('alert')).toHaveTextContent('ya está');
  expect(document.activeElement).toBe(box);
  expect([box.selectionStart, box.selectionEnd]).toEqual([0, IMEI.length]);
});

test('device lines never become quantity rows of the draft', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { buildDraftRows, seedDraft } = require('@/app/admin/inventory/transfers/_components/transfer-draft');
  const items = [
    { id: 3, product: 10, product_name: 'iPhone', product_slug: 'ip', quantity: 1, is_serialized: true, units: [] },
    { id: 4, product: 20, product_name: 'Cable', product_slug: 'cable', quantity: 5, is_serialized: false },
  ];
  expect(buildDraftRows([], items).map((r: { id: number }) => r.id)).toEqual([20]);
  expect(seedDraft(items)).toEqual({ 20: '5' });
});
