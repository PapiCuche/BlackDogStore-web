import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { InventoryAISettings } from '@/app/admin/components/InventoryAISettings';
import { aiImportNotice, previewWeeklyImport } from '@/app/admin/lib/supplier-import-api';
import { fetchInventoryAISettings, updateInventoryAISettings } from '@/app/lib/inventory-ai';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * INV-SWITCH-01 · el interruptor de IA del inventario: por empresa en
 * Configuración, y por importación al subir la lista semanal. Sin proveedor
 * configurado nada se envía a ninguna IA: el servidor lo dice y se sigue sin ella.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

const OFF = { enabled: false, global_enabled: false, state: 'disabled', state_label: 'Desactivada', can_manage: true };

beforeEach(() => send.mockReset());

it('lee y cambia el interruptor de la empresa con un booleano', async () => {
  send.mockResolvedValue(reply(200, OFF));
  await fetchInventoryAISettings();
  expect(String(send.mock.calls[0][0])).toContain('/admin/inventory/ai-settings/');
  await updateInventoryAISettings(true);
  expect(send.mock.calls[1][1]?.method).toBe('PATCH');
  expect(JSON.parse(String(send.mock.calls[1][1]?.body))).toEqual({ enabled: true });
});

it('Configuración muestra el estado y quien administra puede activarla', async () => {
  send.mockResolvedValueOnce(reply(200, OFF)).mockResolvedValueOnce(reply(200, {
    ...OFF, enabled: true, state: 'not_configured', state_label: 'No configurada',
  }));
  render(<InventoryAISettings />);
  const toggle = await screen.findByRole('checkbox', { name: 'Usar reconocimiento con IA' });
  expect(screen.getByText('Desactivada')).toBeInTheDocument();
  expect(toggle).not.toBeChecked();
  fireEvent.click(toggle);
  expect(await screen.findByText('No configurada')).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: 'Usar reconocimiento con IA' })).toBeChecked();
  expect(screen.getByText(/se lee sin IA/)).toBeInTheDocument();
});

it('quien sólo ve la configuración no puede cambiarla', async () => {
  send.mockResolvedValue(reply(200, { ...OFF, can_manage: false }));
  render(<InventoryAISettings />);
  expect(await screen.findByRole('checkbox', { name: 'Usar reconocimiento con IA' })).toBeDisabled();
});

it('una negativa del servidor se muestra y el interruptor vuelve a como estaba', async () => {
  send.mockResolvedValueOnce(reply(200, OFF)).mockResolvedValueOnce(reply(403, { detail: 'Sin permiso' }));
  render(<InventoryAISettings />);
  const toggle = await screen.findByRole('checkbox', { name: 'Usar reconocimiento con IA' });
  fireEvent.click(toggle);
  expect(await screen.findByText('Sin permiso')).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole('checkbox', { name: 'Usar reconocimiento con IA' })).not.toBeChecked());
});

it('la vista previa semanal pide IA sólo si se marcó para esa importación', async () => {
  send.mockResolvedValue(reply(200, { id: 1, rows: [] }));
  const file = new File(['x'], 'ofertas.xlsx');
  await previewWeeklyImport(file, 7);
  expect((send.mock.calls[0][1]?.body as FormData).has('use_ai')).toBe(false);
  await previewWeeklyImport(file, 7, { useAi: true });
  expect((send.mock.calls[1][1]?.body as FormData).get('use_ai')).toBe('true');
});

it('el aviso dice por qué no se usó IA, sin fingir que se usó', () => {
  expect(aiImportNotice(undefined)).toBeNull();
  expect(aiImportNotice({ requested: false, allowed: false, reason: 'not_requested', used: false })).toBeNull();
  expect(aiImportNotice({ requested: true, allowed: false, reason: 'not_configured', used: false }))
    .toBe('IA no configurada: el archivo se leyó sin IA.');
  expect(aiImportNotice({ requested: true, allowed: false, reason: 'company_off', used: false }))
    .toBe('La IA está desactivada para esta empresa: el archivo se leyó sin IA.');
  expect(aiImportNotice({ requested: true, allowed: true, reason: null, used: false }))
    .toBe('El archivo se leyó sin IA.');
});
