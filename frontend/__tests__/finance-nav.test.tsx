import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import fs from 'fs';
import path from 'path';

/**
 * FIN-REMIND-01 — por dónde se llega a «Pagos fijos y vencimientos»:
 *  - la entrada «Finanzas» del menú, con la capacidad que pide la pantalla y ninguna otra puerta;
 *  - la campana y la bandeja llevan un aviso de vencimiento a `/admin/finance?obligation=<id>`,
 *    construido con campos estructurados (nunca con una dirección guardada).
 */
jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/admin/components/InternalControlGuard', () => ({
  InternalControlGuard: ({ children }: { children: (ctx: unknown) => React.ReactNode }) =>
    children({ dashboard: { company: { slug: 'tienda', name: 'Tienda' } } }),
}));

/* eslint-disable @typescript-eslint/no-require-imports */
const { NotificationBell, targetHref } = require('@/app/admin/components/NotificationBell');
const NotificationsPage = require('@/app/admin/notifications/page').default;
const modules = require('@/app/admin/lib/internal-modules');
/* eslint-enable @typescript-eslint/no-require-imports */

const { fetchWithAuth } = jest.requireMock('@/app/lib/auth') as { fetchWithAuth: jest.Mock };

const ctx = (capabilities: string[], extra: Record<string, unknown> = {}) => ({
  capabilities, legacyRole: null, isPlatformAdmin: false, hasCompanyContext: true, ...extra,
});

describe('menu entry', () => {
  const entry = modules.INTERNAL_MODULES.find((m: { id: string }) => m.id === 'finance.obligations');

  it('is one implemented module that asks for finance.view and lives at /admin/finance', () => {
    expect(entry).toMatchObject({
      href: '/admin/finance', requiredCapabilities: ['finance.view'], status: 'implemented', label: 'Finanzas',
    });
    expect(entry.legacyRoles).toBeUndefined();
    expect(modules.INTERNAL_MODULES.filter((m: { href?: string }) => m.href === '/admin/finance')).toHaveLength(1);
  });

  it('has the page it points to', () => {
    expect(fs.existsSync(path.join(__dirname, '..', 'app', 'admin', 'finance', 'page.tsx'))).toBe(true);
  });

  it('shows up in the sidebar for whoever holds finance.view, and for nobody else', () => {
    const find = (access: ReturnType<typeof ctx>) => modules.sidebarSections(access)
      .flatMap((s: { label: string; items: { id: string; label: string; href: string | null }[] }) => s.items.map((i) => ({ section: s.label, ...i })))
      .find((i: { href: string | null }) => i.href === '/admin/finance');
    expect(find(ctx(['finance.view']))).toMatchObject({ label: 'Finanzas', section: 'Compras' });
    expect(find(ctx([]))).toBeUndefined();
    // Another finance capability is not the way in; neither is the legacy role.
    expect(find(ctx(['finance.manage']))).toBeUndefined();
    expect(find(ctx([], { legacyRole: 'admin' }))).toBeUndefined();
    expect(find(ctx([], { isPlatformAdmin: true }))).toBeDefined();
  });

  it('is not in the roadmap of what is still missing', () => {
    const upcoming = modules.upcomingByGroup().flatMap((g: { modules: { id: string }[] }) => g.modules.map((m) => m.id));
    expect(upcoming).not.toContain('finance.obligations');
  });
});

describe('targetHref for a reminder', () => {
  it('sends a finance_obligation notice to that payment', () => {
    expect(targetHref({ target_type: 'finance_obligation', target_id: 42 })).toBe('/admin/finance?obligation=42');
  });

  it('builds nothing without an id, and keeps the other targets as they were', () => {
    expect(targetHref({ target_type: 'finance_obligation', target_id: null })).toBeNull();
    expect(targetHref({ target_type: 'repair_order', target_id: 25 })).toBe('/admin/service/orders/25');
    expect(targetHref({ target_type: 'announcement', target_id: 3 })).toBe('/admin/communications/3');
  });

  it('puts only the id in the address', () => {
    expect(targetHref({ target_type: 'finance_obligation', target_id: '7&x=1' as unknown as number }))
      .toBe('/admin/finance?obligation=7%26x%3D1');
  });
});

const REMINDER = {
  id: 11, title: 'Pago vencido: Alquiler del local', body: 'Pendiente: S/ 2,500.00', priority: 'warning',
  target_type: 'finance_obligation', target_id: 42, read_at: null, created_at: '2026-10-31T08:00:00Z',
};

describe('the bell and the inbox', () => {
  beforeEach(() => {
    fetchWithAuth.mockReset().mockImplementation((url: string) =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve(url.includes('unread-count') ? { unread: 1 } : { results: [REMINDER], count: 1 }),
      }));
  });

  it('the bell links the reminder to its payment and marks it read on click', async () => {
    render(<NotificationBell slug="tienda" />);
    await userEvent.click(screen.getByLabelText('Notificaciones'));
    const link = await screen.findByRole('link', { name: /Pago vencido: Alquiler del local/ });
    expect(link).toHaveAttribute('href', '/admin/finance?obligation=42');
  });

  it('the full inbox uses the same link', async () => {
    render(<NotificationsPage />);
    const row = (await screen.findByText('Pago vencido: Alquiler del local')).closest('li') as HTMLElement;
    await waitFor(() => expect(within(row).getByRole('link', { name: 'Abrir' })).toHaveAttribute('href', '/admin/finance?obligation=42'));
  });
});
