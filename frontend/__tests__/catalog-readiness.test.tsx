import { readFileSync, existsSync } from 'fs';
import path from 'path';

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { CatalogReadinessPanel } from '@/app/admin/components/CatalogReadinessPanel';
import type { CatalogReadiness, CatalogReadinessCheck } from '@/app/admin/lib/internal-api';
import { fetchWithAuth } from '@/app/lib/auth';

import { reply } from './support/device-commerce-fixtures';

/**
 * CATALOG-READINESS-01 — «Preparación del catálogo».
 *
 * Con la API simulada (las reglas las prueba Django):
 *   - cada comprobación pendiente muestra su estado en texto, su título, su detalle y un enlace a la pantalla
 *     que lo corrige; las muestras de modelos enlazan a cada modelo;
 *   - lo que ya está listo queda agrupado y plegado;
 *   - cargando, vacío, error (con «Reintentar») y 403, en español;
 *   - todo listo: un mensaje breve de éxito; nada pendiente obligatorio pero con avisos: otro mensaje;
 *   - la pantalla sólo LEE: una única petición GET con la empresa del contexto.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const check = (over: Partial<CatalogReadinessCheck>): CatalogReadinessCheck => ({
  key: 'x', status: 'ok', kind: 'required', title: 'Algo', detail: 'Detalle.', action: '/admin/products/models',
  action_label: 'Ver modelos', counts: {}, samples: [], ...over,
});

const PENDING: CatalogReadiness = {
  checks: [
    check({ key: 'role_capacity', status: 'missing', title: 'Rol Capacidad',
      detail: '«Capacidad» no tiene el rol Capacidad. Cámbialo en «Atributos y opciones».',
      action: '/admin/products/models#atributos', action_label: 'Revisar atributos' }),
    check({ key: 'palette', status: 'missing', title: 'Paleta de cada modelo',
      detail: '2 de 2 modelos no tienen paleta.', action: '/admin/products/models/7#paleta', action_label: 'Elegir paleta',
      samples: [{ id: 7, name: 'Equipo Uno', path: '/admin/products/models/7#paleta' },
        { id: 8, name: 'Equipo Dos', path: '/admin/products/models/8#paleta' }] }),
    check({ key: 'branches', status: 'ok', title: 'Sucursal activa', detail: '2 sucursales activas.',
      action: '/admin/branches', action_label: 'Ver sucursales' }),
    check({ key: 'role_sim', status: 'warning', kind: 'optional', title: 'Rol SIM (opcional)',
      detail: 'Sólo hace falta si vendes variantes por SIM/región.', action: '/admin/products/models#atributos',
      action_label: 'Revisar atributos (SIM)' }),
    check({ key: 'aliases', status: 'ok', kind: 'info', title: 'Vocabulario de proveedores', detail: 'Aún no hay alias.',
      action: '/admin/products/models#vocabulario', action_label: 'Ver vocabulario' }),
  ],
  summary: { ok: 2, warning: 1, missing: 2 },
  ready: false,
};

function answer(status: number, body: unknown) {
  send.mockResolvedValue(reply(body, status));
}

beforeEach(() => send.mockReset());

describe('CatalogReadinessPanel', () => {
  it('reads the checklist once, with the company of the context, and never writes', async () => {
    answer(200, PENDING);
    render(<CatalogReadinessPanel companyId={5} />);
    await screen.findByText('Rol Capacidad');
    expect(send).toHaveBeenCalledTimes(1);
    const [url, init] = send.mock.calls[0];
    expect(String(url)).toMatch(/\/admin\/catalog\/readiness\/\?company=5$/);
    expect((init?.method ?? 'GET').toUpperCase()).toBe('GET');
  });

  it('shows each pending check with its status in words, its detail and a link to fix it', async () => {
    answer(200, PENDING);
    render(<CatalogReadinessPanel companyId={5} />);
    await screen.findByText('Rol Capacidad');
    const region = screen.getByRole('region', { name: 'Preparación del catálogo' });
    const pending = within(region).getByRole('list', { name: 'Pendientes' });
    const items = within(pending).getAllByRole('listitem').filter((li) => li.parentElement === pending);
    expect(items).toHaveLength(3);

    const capacity = items[0];
    expect(within(capacity).getByText('Falta')).toBeInTheDocument();
    expect(within(capacity).getByText(/«Capacidad» no tiene el rol Capacidad/)).toBeInTheDocument();
    expect(within(capacity).getByRole('link', { name: /Revisar atributos/ })).toHaveAttribute('href', '/admin/products/models#atributos');

    const palette = items[1];
    expect(within(palette).getByRole('link', { name: /Elegir paleta/ })).toHaveAttribute('href', '/admin/products/models/7#paleta');
    expect(within(palette).getByRole('link', { name: 'Equipo Dos' })).toHaveAttribute('href', '/admin/products/models/8#paleta');

    const sim = items[2];
    expect(within(sim).getByText('Revisar')).toBeInTheDocument();
    expect(within(sim).getByText('Opcional')).toBeInTheDocument();

    expect(within(region).getByText(/2 por completar · 1 por revisar · 2 listas/)).toBeInTheDocument();
    expect(within(region).queryByText(/Todo listo/)).not.toBeInTheDocument();
  });

  it('groups what is already done in a collapsed section', async () => {
    answer(200, PENDING);
    render(<CatalogReadinessPanel companyId={5} />);
    const summary = await screen.findByText('Ya está listo (2)');
    const details = summary.closest('details') as HTMLDetailsElement;
    expect(details).not.toBeNull();
    expect(details.open).toBe(false);
    expect(within(details).getByText('Sucursal activa')).toBeInTheDocument();
    expect(within(details).getByText('Informativo')).toBeInTheDocument();
    expect(within(details).getByRole('link', { name: /Ver sucursales/ })).toHaveAttribute('href', '/admin/branches');
  });

  it('says so briefly when everything is ready', async () => {
    answer(200, {
      checks: [check({ key: 'branches', title: 'Sucursal activa' }), check({ key: 'grades', title: 'Escala' })],
      summary: { ok: 2, warning: 0, missing: 0 }, ready: true,
    });
    render(<CatalogReadinessPanel companyId={5} />);
    expect(await screen.findByRole('status')).toHaveTextContent('Todo listo: ya puedes registrar tu inventario');
    expect(screen.queryByRole('list', { name: 'Pendientes' })).not.toBeInTheDocument();
  });

  it('with nothing required missing but some warnings, says the essentials are ready', async () => {
    answer(200, {
      checks: [check({ key: 'role_sim', status: 'warning', kind: 'optional', title: 'Rol SIM (opcional)' }),
        check({ key: 'branches', title: 'Sucursal activa' })],
      summary: { ok: 1, warning: 1, missing: 0 }, ready: true,
    });
    render(<CatalogReadinessPanel companyId={5} />);
    expect(await screen.findByRole('status')).toHaveTextContent('Lo indispensable está listo');
    expect(screen.getByRole('list', { name: 'Pendientes' })).toBeInTheDocument();
  });

  it('shows a loading state while it reads', async () => {
    let resolve: (value: Response) => void = () => {};
    send.mockReturnValue(new Promise<Response>((r) => { resolve = r; }));
    render(<CatalogReadinessPanel companyId={5} />);
    expect(screen.getByText('Revisando el catálogo…')).toBeInTheDocument();
    resolve(reply(PENDING));
    await screen.findByText('Rol Capacidad');
    expect(screen.queryByText('Revisando el catálogo…')).not.toBeInTheDocument();
  });

  it('shows an empty state when there is nothing to check', async () => {
    answer(200, { checks: [], summary: { ok: 0, warning: 0, missing: 0 }, ready: true });
    render(<CatalogReadinessPanel companyId={5} />);
    expect(await screen.findByText('No hay comprobaciones para mostrar.')).toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('shows the error and retries on demand', async () => {
    send.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    render(<CatalogReadinessPanel companyId={5} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('No se pudo revisar la preparación del catálogo.');
    answer(200, PENDING);
    fireEvent.click(screen.getByRole('button', { name: 'Reintentar' }));
    await screen.findByText('Rol Capacidad');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(send).toHaveBeenCalledTimes(2);
  });

  it('shows the server refusal in Spanish', async () => {
    answer(403, { detail: 'No tienes permiso para ver el catálogo.' });
    render(<CatalogReadinessPanel companyId={5} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('No tienes permiso para ver el catálogo.');
  });

  it('without a company in the context the server decides, and a new company reads again', async () => {
    answer(200, PENDING);
    const { rerender } = render(<CatalogReadinessPanel companyId={null} />);
    await screen.findByText('Rol Capacidad');
    expect(String(send.mock.calls[0][0])).toMatch(/\/admin\/catalog\/readiness\/$/);
    answer(200, PENDING);
    rerender(<CatalogReadinessPanel companyId={9} />);
    await waitFor(() => expect(send).toHaveBeenCalledTimes(2));
    expect(String(send.mock.calls[1][0])).toMatch(/company=9$/);
  });
});

describe('where the checklist lives and where it sends people', () => {
  const root = path.join(__dirname, '..');
  const read = (file: string) => readFileSync(path.join(root, file), 'utf8');

  it('is the first thing on the models page', () => {
    const page = read('app/admin/products/models/page.tsx');
    expect(page.indexOf('<CatalogReadinessPanel')).toBeGreaterThan(-1);
    expect(page.indexOf('<CatalogReadinessPanel')).toBeLessThan(page.indexOf('<CatalogModelsManager'));
  });

  // The paths `backend/store/catalog_readiness.py` sends: every anchor and every page must exist.
  it.each([
    ['app/admin/components/CatalogModelsManager.tsx', 'id="atributos"'],
    ['app/admin/components/CatalogModelsManager.tsx', 'id="escala"'],
    ['app/admin/components/CatalogVocabulary.tsx', 'id="vocabulario"'],
    ['app/admin/components/ModelPalettePanel.tsx', 'id="paleta"'],
    ['app/admin/components/CatalogModelEditor.tsx', 'id="variantes"'],
  ])('%s carries %s', (file, anchor) => {
    expect(read(file)).toContain(anchor);
  });

  it.each(['branches', 'products/categories', 'products/new', 'products/models', 'products/models/[id]'])(
    '/admin/%s exists', (route) => {
      expect(existsSync(path.join(root, 'app/admin', route, 'page.tsx'))).toBe(true);
    },
  );
});
