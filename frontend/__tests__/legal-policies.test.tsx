/**
 * LEGAL-POLICIES-01 — the legal documents render as text, never as HTML; the
 * footer links all three; the public page falls back when nothing is published;
 * the admin editor saves, previews and publishes as two separate decisions.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { LegalText } from '@/app/components/LegalText';
import { LegalDocument } from '@/app/components/LegalDocument';
import { Footer } from '@/app/components/Footer';
import { StorefrontProvider } from '@/app/components/StorefrontProvider';
import { NEUTRAL_CONFIG } from '@/app/lib/storefront';
import { linkify, parseLegalText } from '@/app/lib/legal-text';
import { LEGAL_PAGES, fetchLegalPolicy } from '@/app/lib/legal-policies';
import { onOpenConsentPreferences } from '@/app/lib/consent';

jest.mock('@/app/lib/catalog-categories', () => ({
  useCatalogCategories: () => [],
  categoryHref: (slug: string) => `/product?category=${slug}`,
}));

describe('plain-text format', () => {
  test('headings, lists and paragraphs', () => {
    expect(parseLegalText('# Uno\nLínea a\nlínea b\n\n- x\n- y\n## Dos\nFin')).toEqual([
      { type: 'h2', text: 'Uno' },
      { type: 'p', text: 'Línea a\nlínea b' },
      { type: 'ul', items: ['x', 'y'] },
      { type: 'h3', text: 'Dos' },
      { type: 'p', text: 'Fin' },
    ]);
  });

  test('emails and https links become links; other schemes stay text', () => {
    expect(linkify('Escribe a a@b.pe o visita https://x.pe/privacy. javascript:alert(1)')).toEqual([
      { kind: 'text', value: 'Escribe a ' },
      { kind: 'email', value: 'a@b.pe' },
      { kind: 'text', value: ' o visita ' },
      { kind: 'url', value: 'https://x.pe/privacy' },
      { kind: 'text', value: '. javascript:alert(1)' },
    ]);
  });

  test('markup typed in the panel is shown as text, never executed', () => {
    const { container } = render(<LegalText text={'# Hola\n<script>alert(1)</script>\n<img src=x onerror=alert(2)>'} />);
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('img')).toBeNull();
    expect(container.textContent).toContain('<script>alert(1)</script>');
    expect(screen.getByRole('heading', { level: 2, name: 'Hola' })).toBeInTheDocument();
  });
});

describe('public document', () => {
  test('the cookie policy offers the existing preferences dialog and links the other documents', () => {
    render(<LegalDocument kind="cookies" policy={{ kind: 'cookies', text: '# Cookies\nTexto.', published_at: '2026-11-15T15:00:00Z' }} />);
    expect(screen.getByRole('heading', { level: 1, name: 'Política de cookies' })).toBeInTheDocument();
    const opened = jest.fn(); const stop = onOpenConsentPreferences(opened);
    fireEvent.click(screen.getByRole('button', { name: 'Cambiar preferencias de cookies' }));
    expect(opened).toHaveBeenCalledTimes(1); stop();
    const nav = screen.getByRole('navigation', { name: 'Otras políticas' });
    expect(within(nav).getByRole('link', { name: 'Política de privacidad' })).toHaveAttribute('href', '/privacy');
    expect(within(nav).getByRole('link', { name: 'Privacidad de mensajería y CRM' })).toHaveAttribute('href', '/privacy/whatsapp');
  });

  test('the messaging policy names WhatsApp, Instagram and Messenger in its title and keeps /privacy/whatsapp', () => {
    render(<LegalDocument kind="whatsapp" policy={{ kind: 'whatsapp', text: '# Canales\nTexto.', published_at: null }} />);
    expect(screen.getByRole('heading', { level: 1, name: 'Privacidad de mensajería (WhatsApp, Instagram y Messenger) y CRM' })).toBeInTheDocument();
    expect(LEGAL_PAGES.whatsapp.href).toBe('/privacy/whatsapp');
    const nav = screen.getByRole('navigation', { name: 'Otras políticas' });
    expect(within(nav).getByRole('link', { name: 'Política de privacidad' })).toHaveAttribute('href', '/privacy');
  });

  test('fetchLegalPolicy: published text, or null when the backend answers 404 or fails', async () => {
    const original = global.fetch;
    global.fetch = jest.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ kind: 'privacy', text: 'Texto', published_at: null }) })
      .mockResolvedValueOnce({ ok: false, json: async () => ({}) })
      .mockRejectedValueOnce(new Error('down')) as unknown as typeof fetch;
    await expect(fetchLegalPolicy('privacy')).resolves.toEqual({ kind: 'privacy', text: 'Texto', published_at: null });
    await expect(fetchLegalPolicy('cookies')).resolves.toBeNull();
    await expect(fetchLegalPolicy('whatsapp')).resolves.toBeNull();
    expect((global.fetch as jest.Mock).mock.calls[0][0]).toMatch(/\/storefront\/legal\/privacy\/$/);
    global.fetch = original;
  });
});

describe('footer', () => {
  test('links the three documents and the preferences once, without a duplicate privacy link', () => {
    const config = { ...NEUTRAL_CONFIG, policies: { ...NEUTRAL_CONFIG.policies, privacy_url: 'https://tienda.example/privacy' } };
    render(<StorefrontProvider config={config}><Footer /></StorefrontProvider>);
    const nav = screen.getByRole('navigation', { name: 'Privacidad' });
    expect(within(nav).getByRole('link', { name: 'Política de privacidad' })).toHaveAttribute('href', '/privacy');
    expect(within(nav).getByRole('link', { name: 'Política de cookies' })).toHaveAttribute('href', '/cookies');
    expect(within(nav).getByRole('link', { name: 'Privacidad de mensajería y CRM' })).toHaveAttribute('href', '/privacy/whatsapp');
    expect(within(nav).getByRole('button', { name: 'Preferencias de cookies' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Privacidad' })).toBeNull();
    expect(document.body.textContent).not.toContain('CMAU');
  });
});

const mockFetchPolicies = jest.fn();
const mockSave = jest.fn();
const mockPublish = jest.fn();
const mockUnpublish = jest.fn();
jest.mock('@/app/admin/lib/legal-policies-api', () => {
  const actual = jest.requireActual('@/app/admin/lib/legal-policies-api');
  return {
    ...actual,
    fetchLegalPolicies: (...a: unknown[]) => mockFetchPolicies(...a),
    saveLegalPolicyDraft: (...a: unknown[]) => mockSave(...a),
    publishLegalPolicy: (...a: unknown[]) => mockPublish(...a),
    unpublishLegalPolicy: (...a: unknown[]) => mockUnpublish(...a),
  };
});
jest.mock('@/app/admin/components/InternalControlGuard', () => ({
  InternalControlGuard: ({ children }: { children: (ctx: unknown) => React.ReactNode }) =>
    children({ user: { username: 'u' }, selectedCompanyId: 7, dashboard: { access: { capabilities: ['company.view', 'company.manage'] } } }),
}));
jest.mock('@/app/admin/components/AdminShell', () => ({
  AdminShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

function row(kind: string, over: Record<string, unknown> = {}) {
  return { kind, draft_text: '', published_text: '', is_published: false, published_at: null,
    has_unpublished_changes: false, updated_at: null, ...over };
}

describe('admin editor', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const Page = require('@/app/admin/settings/legal/page').default;

  beforeEach(() => {
    jest.clearAllMocks();
    mockFetchPolicies.mockResolvedValue({
      company: { id: 7, name: 'Tienda A' }, max_length: 40000, can_manage: true,
      policies: [row('privacy', { draft_text: '# Responsable\nTienda A' }), row('cookies'), row('whatsapp')],
    });
  });

  test('saves a draft without publishing, previews it, then publishes', async () => {
    render(<Page />);
    const box = await screen.findByLabelText(/Texto \(Política de privacidad\)/);
    expect(box).toHaveValue('# Responsable\nTienda A');
    expect(mockFetchPolicies).toHaveBeenCalledWith(7);

    fireEvent.change(box, { target: { value: '# Responsable\nTienda A\n<b>x</b>' } });
    mockSave.mockResolvedValue(row('privacy', { draft_text: '# Responsable\nTienda A\n<b>x</b>' }));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Guardar borrador' })); });
    expect(mockSave).toHaveBeenCalledWith('privacy', '# Responsable\nTienda A\n<b>x</b>', 7);
    expect(mockPublish).not.toHaveBeenCalled();
    expect(await screen.findByText(/Todavía no es público/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Vista previa' }));
    const preview = screen.getByLabelText('Vista previa');
    expect(within(preview).getByRole('heading', { level: 2, name: 'Responsable' })).toBeInTheDocument();
    expect(preview.querySelector('b')).toBeNull();

    mockPublish.mockResolvedValue(row('privacy', { draft_text: 'x', published_text: 'x', is_published: true }));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Publicar' })); });
    expect(mockPublish).toHaveBeenCalledWith('privacy', 7);
    expect(await screen.findByText(/Publicada\. La página pública/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Abrir página pública \(\/privacy\)/ })).toHaveAttribute('href', '/privacy');
  });

  test('shows the server validation errors', async () => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { LegalPolicyError } = jest.requireActual('@/app/admin/lib/legal-policies-api');
    render(<Page />);
    await screen.findByLabelText(/Texto \(Política de privacidad\)/);
    mockPublish.mockRejectedValue(new LegalPolicyError('x', { text: ['El texto todavía tiene campos sin completar: [RUC].'] }, 400));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Publicar' })); });
    expect(await screen.findByRole('alert')).toHaveTextContent('[RUC]');
  });

  test('a reader without company.manage sees the text read-only', async () => {
    mockFetchPolicies.mockResolvedValue({
      company: { id: 7, name: 'Tienda A' }, max_length: 40000, can_manage: false,
      policies: [row('privacy', { draft_text: 'Texto' }), row('cookies'), row('whatsapp')],
    });
    render(<Page />);
    const box = await screen.findByLabelText(/Texto \(Política de privacidad\)/);
    expect(box).toHaveAttribute('readonly');
    expect(screen.queryByRole('button', { name: 'Guardar borrador' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Publicar' })).toBeNull();
    await waitFor(() => expect(screen.getAllByText(/Sólo lectura/).length).toBe(3));
  });
});
