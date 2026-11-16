/**
 * @jest-environment node
 *
 * P2-F2 (revisión): las lecturas del servidor de la vista previa V4 tienen tope de tiempo, como el sitemap y las
 * políticas. Sin él, una API colgada dejaba la página esperando ~300 s y el respaldo del navegador nunca corría.
 */
import { readCatalogModel, readCatalogModelList } from '@/app/lib/catalog-models-server';

const fetchMock = jest.fn();
beforeEach(() => {
  fetchMock.mockReset();
  global.fetch = fetchMock as unknown as typeof fetch;
});

it('cada lectura lleva una señal de aborto', async () => {
  fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => ({ name: 'X', results: [] }) });
  await readCatalogModel('equipo-a');
  await readCatalogModelList();
  for (const [, init] of fetchMock.mock.calls) expect((init as RequestInit).signal).toBeInstanceOf(AbortSignal);
});

it('un aborto por tiempo degrada a «error» y a null (la página cae al comportamiento de F1)', async () => {
  fetchMock.mockRejectedValue(new DOMException('The operation was aborted due to timeout', 'TimeoutError'));
  expect(await readCatalogModel('equipo-b')).toEqual({ status: 'error' });
  expect(await readCatalogModelList()).toBeNull();
});
