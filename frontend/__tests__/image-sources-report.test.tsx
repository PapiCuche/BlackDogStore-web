import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { ImageSourcesReport } from '@/app/admin/components/ImageSourcesReport';
import type { ImageSourceEntry, ImageSourcesReport as Report } from '@/app/admin/lib/internal-api';
import { INTERNAL_MODULES, sectionTabsFor, sidebarSections } from '@/app/admin/lib/internal-modules';
import { fetchWithAuth } from '@/app/lib/auth';

import { reply } from './support/device-commerce-fixtures';

/**
 * P2 · F4 — «Imágenes por reemplazar»: las fichas del catálogo cuyas imágenes no subió la tienda.
 *
 * Con la API simulada (las reglas las prueba Django en `test_catalog_image_sources.py`):
 *   - un resumen por tipo y por dominio, y la lista con un enlace para editar cada ficha;
 *   - el enlace a «Imágenes en lote» sólo con `products.manage`;
 *   - sin imágenes externas lo dice; cargando lo dice; un error se muestra con «Reintentar»;
 *   - pasa de página pidiendo la siguiente al servidor;
 *   - la entrada está en «Productos y variantes», con `products.view`.
 *
 * Nombres y dominios inventados: nada aquí es de Apple ni de la tienda piloto.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const entry = (over: Partial<ImageSourceEntry>): ImageSourceEntry => ({
  kind: 'product', id: 1, name: 'Funda clara', slug: 'funda-clara', host: 'imagenes.proveedor.example',
  hosts: ['imagenes.proveedor.example'], image_count: 1, model_id: null, is_published: true,
  admin_url: '/admin/products/1', ...over,
});

const MODEL = entry({ kind: 'model', id: 7, name: 'Teléfono X', slug: 'telefono-x', host: 'cdn.fabricante.example',
  hosts: ['cdn.fabricante.example'], admin_url: '/admin/products/models/7' });
const VARIANT = entry({ kind: 'variant', id: 21, name: 'Teléfono X 128 GB Negro', slug: 'tx-128', image_count: 2,
  host: 'cdn.fabricante.example', hosts: ['cdn.fabricante.example'], model_id: 7, admin_url: '/admin/products/21',
  is_published: false });
const CASE = entry({ id: 31, admin_url: '/admin/products/31' });

function report(results: ImageSourceEntry[], over: Partial<Report> = {}): Report {
  return {
    summary: {
      models: 1, variants: 1, products: 1, items: 3, images: 4,
      hosts: [
        { host: 'cdn.fabricante.example', items: 2, images: 3 },
        { host: 'imagenes.proveedor.example', items: 1, images: 1 },
      ],
    },
    count: results.length, page: 1, page_size: 50, results, ...over,
  };
}

let queries: URLSearchParams[];
let answer: (query: URLSearchParams) => Response | Promise<Response>;

beforeEach(() => {
  queries = [];
  answer = () => reply(report([MODEL, VARIANT, CASE]));
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    const path = url.replace(/^.*\/api/, '').replace(/\?.*$/, '');
    if (path !== '/admin/catalog/image-sources/') return reply({}, 404);
    const query = new URLSearchParams(url.includes('?') ? url.slice(url.indexOf('?') + 1) : '');
    queries.push(query);
    return answer(query);
  });
});

it('resume por tipo y por dominio, y enlaza cada ficha a su edición', async () => {
  render(<ImageSourcesReport companyId={9} canManage />);
  const summary = await screen.findByRole('region', { name: 'Resumen' });
  expect(summary).toHaveTextContent('1 modelo, 1 variante y 1 producto usan imágenes que la tienda no subió (4 imágenes).');
  const hosts = within(summary).getByRole('list', { name: 'Dominios externos' });
  expect(within(hosts).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
    'cdn.fabricante.example3 imágenes en 2 fichas',
    'imagenes.proveedor.example1 imagen en 1 ficha',
  ]);

  const list = screen.getByRole('list', { name: 'Fichas con imágenes externas' });
  const rows = within(list).getAllByRole('listitem');
  expect(rows).toHaveLength(3);
  expect(within(rows[0]).getByText('Modelo')).toBeInTheDocument();
  expect(within(rows[0]).getByRole('link', { name: 'Editar «Teléfono X»' })).toHaveAttribute('href', '/admin/products/models/7');
  expect(within(rows[1]).getByText('Variante')).toBeInTheDocument();
  expect(rows[1]).toHaveTextContent('2 imágenes');
  expect(rows[1]).toHaveTextContent('No publicado');
  expect(within(rows[1]).getByRole('link', { name: 'Editar «Teléfono X 128 GB Negro»' })).toHaveAttribute('href', '/admin/products/21');
  expect(within(rows[2]).getByText('Producto')).toBeInTheDocument();
  expect(rows[2]).toHaveTextContent('imagenes.proveedor.example');

  // The company is asked for in the query and checked by the server; the page size is the server's cap at most.
  expect(queries[0].get('company')).toBe('9');
  expect(queries[0].get('page')).toBe('1');
  expect(Number(queries[0].get('page_size'))).toBeLessThanOrEqual(200);
});

it('una ruta del sitio que no es una subida sale aparte de los dominios, explicada', async () => {
  answer = () => reply(report([entry({ id: 41, name: 'Cargador', host: '(ruta del sitio)', hosts: ['(ruta del sitio)'],
    admin_url: '/admin/products/41' })], {
    summary: { models: 0, variants: 0, products: 1, items: 1, images: 1, hosts: [{ host: '(ruta del sitio)', items: 1, images: 1 }] },
  }));
  render(<ImageSourcesReport companyId={9} canManage />);
  const hosts = await screen.findByRole('list', { name: 'Dominios externos' });
  expect(within(hosts).getByRole('listitem')).toHaveTextContent('Rutas de este sitio que no son subidas desde el panel');
  expect(screen.getByRole('list', { name: 'Fichas con imágenes externas' })).toHaveTextContent('Ruta de este sitio');
});

it('enlaza la subida de imágenes en lote sólo a quien puede cargarlas', async () => {
  const { unmount } = render(<ImageSourcesReport companyId={9} canManage />);
  expect(await screen.findByRole('link', { name: 'Subir imágenes en lote' })).toHaveAttribute('href', '/admin/inventory/bulk#imagenes');
  unmount();
  render(<ImageSourcesReport companyId={9} canManage={false} />);
  await screen.findByRole('region', { name: 'Resumen' });
  expect(screen.queryByRole('link', { name: 'Subir imágenes en lote' })).not.toBeInTheDocument();
  expect(screen.getByText(/Subir imágenes en lote exige permiso para administrar productos/)).toBeInTheDocument();
});

it('sin imágenes externas lo dice', async () => {
  answer = () => reply(report([], { summary: { models: 0, variants: 0, products: 0, items: 0, images: 0, hosts: [] } }));
  render(<ImageSourcesReport companyId={9} canManage />);
  expect(await screen.findByRole('status')).toHaveTextContent('Todas las imágenes del catálogo son propias');
  expect(screen.queryByRole('list', { name: 'Fichas con imágenes externas' })).not.toBeInTheDocument();
});

it('mientras carga lo dice, y un error se muestra con «Reintentar»', async () => {
  let release: (value: Response) => void = () => {};
  answer = () => new Promise<Response>((resolve) => { release = resolve; });
  render(<ImageSourcesReport companyId={9} canManage />);
  expect(screen.getByText('Cargando informe de imágenes…')).toBeInTheDocument();
  release(reply({ detail: 'Fallo de prueba.' }, 500));
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('Fallo de prueba.');

  answer = () => reply(report([MODEL]));
  fireEvent.click(within(alert).getByRole('button', { name: 'Reintentar' }));
  expect(await screen.findByRole('link', { name: 'Editar «Teléfono X»' })).toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('sin permiso lo dice el servidor y se muestra', async () => {
  answer = () => reply({ detail: 'No tienes permisos sobre el catálogo.' }, 403);
  render(<ImageSourcesReport companyId={9} canManage={false} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('No tienes permisos sobre el catálogo.');
});

it('pasa de página pidiéndola al servidor', async () => {
  const many = Array.from({ length: 50 }, (_, n) => entry({ id: 100 + n, name: `Correa ${n}`, admin_url: `/admin/products/${100 + n}` }));
  answer = (query) => (query.get('page') === '2'
    ? reply(report([CASE], { count: 51, page: 2 }))
    : reply(report(many, { count: 51, page: 1 })));
  render(<ImageSourcesReport companyId={9} canManage />);
  const pager = await screen.findByRole('navigation', { name: 'Páginas del informe' });
  expect(pager).toHaveTextContent('Página 1 de 2');
  expect(within(pager).getByRole('button', { name: 'Anterior' })).toBeDisabled();
  fireEvent.click(within(pager).getByRole('button', { name: 'Siguiente' }));
  await waitFor(() => expect(screen.getByRole('navigation', { name: 'Páginas del informe' })).toHaveTextContent('Página 2 de 2'));
  expect(queries.at(-1)?.get('page')).toBe('2');
  expect(within(screen.getByRole('list', { name: 'Fichas con imágenes externas' })).getAllByRole('listitem')).toHaveLength(1);
});

describe('la entrada del menú', () => {
  const mod = INTERNAL_MODULES.find((x) => x.id === 'products.images');

  it('es una pantalla del catálogo con `products.view`', () => {
    expect(mod).toMatchObject({
      group: 'products', label: 'Imágenes por reemplazar', href: '/admin/products/images',
      requiredCapabilities: ['products.view'], status: 'implemented',
    });
  });

  it('es una pestaña de «Productos y variantes»', () => {
    const sections = sidebarSections({
      capabilities: ['products.view'], legacyRole: null, isPlatformAdmin: false, hasCompanyContext: true,
    });
    const tabs = sectionTabsFor(sections, '/admin/products/images');
    expect(tabs?.label).toBe('Productos y variantes');
    expect(tabs?.tabs.find((t) => t.active)?.label).toBe('Imágenes por reemplazar');
  });
});
