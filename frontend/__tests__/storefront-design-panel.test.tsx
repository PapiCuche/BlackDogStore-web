import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { InternalContext } from '@/app/admin/components/InternalControlGuard';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * P2 · F5a — el interruptor «Diseño de la tienda» en Configuración › Escaparate.
 *
 * V3 es el diseño de todas las tiendas. Elegir V4 cambia la tienda pública
 * entera, así que el panel lo explica, enlaza la vista previa `/v4` y pide una
 * confirmación expresa antes de guardar V4. Volver a V3 no pide nada.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/admin/components/InternalControlGuard', () => ({
  InternalControlGuard: ({ children }: { children: (ctx: InternalContext) => React.ReactNode }) =>
    children((globalThis as { __ctx?: InternalContext }).__ctx as InternalContext),
}));
jest.mock('@/app/admin/components/AdminShell', () => ({
  AdminShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

/* eslint-disable @typescript-eslint/no-require-imports */
const StorefrontSettingsPage = require('@/app/admin/settings/storefront/page').default;
/* eslint-enable @typescript-eslint/no-require-imports */

const mockFetch = fetchWithAuth as jest.Mock;
const reply = (status: number, body: unknown) => ({ ok: status < 300, status, json: async () => body });

const PAGE = {
  hero_variant: 'dark', hero_image_url: '', services_image_url: '', location_image_url: '',
  hero_eyebrow: '', hero_title: 'Titular', hero_subtitle: '',
  hero_primary_cta_label: '', hero_primary_cta_url: '', hero_secondary_cta_label: '', hero_secondary_cta_url: '',
  services_hero_title: '', services_hero_subtitle: '', services_warranty_note: '',
  storefront_design: 'v3',
};

function context(capabilities: string[], mode: 'all' | 'selected' = 'all'): InternalContext {
  return {
    user: { id: 1, username: 'x' },
    dashboard: {
      company: { id: 3, slug: 'taller', name: 'Taller' },
      access: { is_platform_admin: false, capabilities },
      branch_scope: { mode, branches: mode === 'selected' ? [{ id: 11, name: 'Sucursal 1' }] : [] },
    },
    selectedCompanyId: 3, selectCompany: () => undefined, reload: () => undefined,
  } as unknown as InternalContext;
}

let stored: Record<string, string>;
let patches: Record<string, unknown>[];

beforeEach(() => {
  stored = { ...PAGE };
  patches = [];
  mockFetch.mockReset();
  mockFetch.mockImplementation(async (url: string, init: RequestInit = {}) => {
    const u = String(url);
    if (u.includes('/admin/storefront/page/')) {
      if (init.method === 'PATCH') {
        const body = JSON.parse(String(init.body));
        patches.push(body);
        if ('storefront_design' in body && !['v3', 'v4', ''].includes(body.storefront_design)) {
          return reply(400, { detail: 'Datos inválidos.', errors: { storefront_design: ['Elige un valor válido.'] } });
        }
        stored = { ...stored, ...body };
      }
      return reply(200, { page: stored });
    }
    if (u.includes('/admin/storefront/campaigns/')) return reply(200, { company: { id: 3 }, slots: [], results: [] });
    if (u.includes('/admin/categories/')) return reply(200, []);
    return reply(200, { results: [] });
  });
  (globalThis as { __ctx?: InternalContext }).__ctx = context(['company.view', 'company.manage']);
});

afterEach(() => jest.restoreAllMocks());

async function designGroup() {
  return screen.findByRole('radiogroup', { name: 'Diseño de la tienda' });
}

describe('el control', () => {
  it('muestra las dos opciones, con la V3 elegida por defecto', async () => {
    render(<StorefrontSettingsPage />);
    const group = await designGroup();
    expect(within(group).getByRole('radio', { name: 'V3 (actual)' })).toBeChecked();
    expect(within(group).getByRole('radio', { name: 'V4 (vista previa)' })).not.toBeChecked();
  });

  it('explica qué es la V4 y enlaza la vista previa /v4', async () => {
    render(<StorefrontSettingsPage />);
    const group = await designGroup();
    const section = group.closest('form')!;
    expect(section).toHaveTextContent(/V4 está en vista previa/i);
    expect(section).toHaveTextContent(/Manual de Marca V4/i);
    expect(section).toHaveTextContent(/adopt/i);
    const link = within(section).getByRole('link', { name: /Ver la V4/i });
    expect(link).toHaveAttribute('href', '/v4');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link.getAttribute('rel')).toMatch(/noopener/);
  });

  it('dice qué sigue en V3 y cómo volver atrás', async () => {
    render(<StorefrontSettingsPage />);
    const section = (await designGroup()).closest('form')!;
    expect(section).toHaveTextContent(/carrito/i);
    expect(section).toHaveTextContent(/V3/);
    expect(section).toHaveTextContent(/volver/i);
  });

  it('quien sólo puede ver la configuración ve el valor pero no lo cambia', async () => {
    (globalThis as { __ctx?: InternalContext }).__ctx = context(['company.view']);
    stored = { ...PAGE, storefront_design: 'v4' };
    render(<StorefrontSettingsPage />);
    const group = await designGroup();
    expect(within(group).getByRole('radio', { name: 'V4 (vista previa)' })).toBeChecked();
    for (const radio of within(group).getAllByRole('radio')) expect(radio).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Guardar diseño' })).toBeNull();
  });
});

describe('alcance de sucursales (WRITE-SCOPE-01)', () => {
  it('quien gestiona la empresa pero sólo algunas sucursales ve el diseño y la portada sin poder cambiarlos', async () => {
    (globalThis as { __ctx?: InternalContext }).__ctx = context(['company.view', 'company.manage'], 'selected');
    render(<StorefrontSettingsPage />);
    const group = await designGroup();
    for (const radio of within(group).getAllByRole('radio')) expect(radio).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Guardar diseño' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Guardar portada' })).toBeNull();
    expect(screen.getByText(/afectan a toda la empresa y tu acceso está limitado a algunas sucursales/i)).toBeInTheDocument();
  });
});

describe('la espera', () => {
  it('el control dice cuánto tarda el cambio en la tienda (15 s), no «hasta un minuto»', async () => {
    render(<StorefrontSettingsPage />);
    const form = (await designGroup()).closest('form')!;
    expect(form).toHaveTextContent(/15 segundos/);
    expect(form).not.toHaveTextContent(/un minuto/);
    expect(form).toHaveTextContent(/recarg/i);
  });
});

describe('la confirmación', () => {
  it('elegir V4 pide confirmación y, si se acepta, guarda sólo el diseño', async () => {
    const confirm = jest.spyOn(window, 'confirm').mockReturnValue(true);
    render(<StorefrontSettingsPage />);
    await userEvent.click(within(await designGroup()).getByRole('radio', { name: 'V4 (vista previa)' }));
    await userEvent.click(screen.getByRole('button', { name: 'Guardar diseño' }));

    expect(confirm).toHaveBeenCalledTimes(1);
    expect(confirm.mock.calls[0][0]).toMatch(/V4/);
    await waitFor(() => expect(patches).toEqual([{ storefront_design: 'v4' }]));
    expect(await screen.findByText(/La tienda usa ahora la V4/i)).toBeInTheDocument();
  });

  it('si no se confirma, no se guarda nada y la V3 sigue elegida', async () => {
    const confirm = jest.spyOn(window, 'confirm').mockReturnValue(false);
    render(<StorefrontSettingsPage />);
    await userEvent.click(within(await designGroup()).getByRole('radio', { name: 'V4 (vista previa)' }));
    await userEvent.click(screen.getByRole('button', { name: 'Guardar diseño' }));

    expect(confirm).toHaveBeenCalledTimes(1);
    expect(patches).toEqual([]);
    expect(within(await designGroup()).getByRole('radio', { name: 'V3 (actual)' })).toBeChecked();
  });

  it('volver a V3 no pide confirmación', async () => {
    const confirm = jest.spyOn(window, 'confirm').mockReturnValue(true);
    stored = { ...PAGE, storefront_design: 'v4' };
    render(<StorefrontSettingsPage />);
    await userEvent.click(within(await designGroup()).getByRole('radio', { name: 'V3 (actual)' }));
    await userEvent.click(screen.getByRole('button', { name: 'Guardar diseño' }));

    expect(confirm).not.toHaveBeenCalled();
    await waitFor(() => expect(patches).toEqual([{ storefront_design: 'v3' }]));
    expect(await screen.findByText(/La tienda vuelve a la V3/i)).toBeInTheDocument();
  });

  it('sin cambios, el botón no guarda', async () => {
    render(<StorefrontSettingsPage />);
    await designGroup();
    expect(screen.getByRole('button', { name: 'Guardar diseño' })).toBeDisabled();
  });

  it('un rechazo del servidor se dice bajo el control', async () => {
    jest.spyOn(window, 'confirm').mockReturnValue(true);
    mockFetch.mockImplementation(async (url: string, init: RequestInit = {}) => {
      const u = String(url);
      if (u.includes('/admin/storefront/page/') && init.method === 'PATCH') {
        return reply(400, { detail: 'Datos inválidos.', errors: { storefront_design: ['Elige un valor válido.'] } });
      }
      if (u.includes('/admin/storefront/page/')) return reply(200, { page: stored });
      if (u.includes('/admin/storefront/campaigns/')) return reply(200, { company: { id: 3 }, slots: [], results: [] });
      if (u.includes('/admin/categories/')) return reply(200, []);
      return reply(200, { results: [] });
    });
    render(<StorefrontSettingsPage />);
    await userEvent.click(within(await designGroup()).getByRole('radio', { name: 'V4 (vista previa)' }));
    await userEvent.click(screen.getByRole('button', { name: 'Guardar diseño' }));
    const form = (await designGroup()).closest('form')!;
    expect(await within(form).findByText('Elige un valor válido.')).toBeInTheDocument();
  });
});

describe('guardar la portada no toca el diseño', () => {
  it('el formulario de la portada no envía storefront_design', async () => {
    stored = { ...PAGE, storefront_design: 'v4' };
    render(<StorefrontSettingsPage />);
    await designGroup();
    await userEvent.click(screen.getByRole('button', { name: 'Guardar portada' }));
    await waitFor(() => expect(patches).toHaveLength(1));
    expect(patches[0]).not.toHaveProperty('storefront_design');
    expect(patches[0]).toHaveProperty('hero_title', 'Titular');
  });

  it('cambiar el diseño no borra lo que se estaba escribiendo en la portada', async () => {
    jest.spyOn(window, 'confirm').mockReturnValue(true);
    render(<StorefrontSettingsPage />);
    await designGroup();
    const title = document.getElementById('f-hero_title') as HTMLTextAreaElement;
    await userEvent.clear(title);
    await userEvent.type(title, 'Nuevo titular');
    await userEvent.click(within(await designGroup()).getByRole('radio', { name: 'V4 (vista previa)' }));
    await userEvent.click(screen.getByRole('button', { name: 'Guardar diseño' }));
    await waitFor(() => expect(patches).toEqual([{ storefront_design: 'v4' }]));
    expect(document.getElementById('f-hero_title')).toHaveValue('Nuevo titular');
  });
});
