import { fetchIntegration, fetchIntegrations, saveIntegrationDraft } from '@/app/lib/integrations';
import { API_BASE } from '@/app/lib/api';
import { SELECTED_COMPANY_KEY, fetchWithAuth } from '@/app/lib/auth';

/**
 * Una integración de la PLATAFORMA no lleva empresa.
 *
 * EL DEFECTO. `fetchWithAuth` añade la empresa elegida en el panel a toda llamada
 * bajo `/admin/` que no la traiga. La consola de integraciones también vive bajo
 * `/admin/`, y su API rechaza el parámetro en las integraciones de la plataforma
 * (correo, pasarela): «Esta integración es de la plataforma: no lleva empresa.»
 * Un MASTER tiene que elegir empresa para abrir el panel, así que no podía
 * configurar el correo ni los pagos.
 *
 * La consola decide ella misma cuándo va una empresa: la nombra en las
 * integraciones que son de cada empresa y la calla en las demás.
 */

const mockFetch = jest.fn();
// Sólo las llamadas al panel: una escritura pide antes su token CSRF.
const urls = () => mockFetch.mock.calls.map(([url]) => String(url)).filter((url) => url.includes('/admin/'));
const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });

beforeEach(() => {
  mockFetch.mockReset();
  mockFetch.mockResolvedValue(ok({}));
  global.fetch = mockFetch as unknown as typeof fetch;
  window.localStorage.setItem(SELECTED_COMPANY_KEY, '1');
});

afterEach(() => window.localStorage.clear());

test('a platform integration is read and saved without the company chosen in the panel', async () => {
  await fetchIntegrations();
  await fetchIntegration('smtp');
  await saveIntegrationDraft('smtp', { public: {}, secrets: {} });

  expect(urls()).toEqual([
    `${API_BASE}/admin/integrations/`,
    `${API_BASE}/admin/integrations/smtp/`,
    `${API_BASE}/admin/integrations/smtp/draft/`,
  ]);
});

test('a company integration carries the company the console names, not the one chosen in the panel', async () => {
  await fetchIntegration('whatsapp_cloud', 9);

  expect(urls()).toEqual([`${API_BASE}/admin/integrations/whatsapp_cloud/?company=9`]);
});

test('every other panel call still carries the company chosen in the panel', async () => {
  await fetchWithAuth(`${API_BASE}/admin/products/`);

  expect(urls()).toEqual([`${API_BASE}/admin/products/?company=1`]);
});
