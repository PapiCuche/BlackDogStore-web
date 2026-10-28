/**
 * ECOM-READY-D1 — the writes and reads of the product sheet name their company in the
 * address, like the bulk action and PATCH: not whatever the browser holds when it sends.
 */
import { fetchAdminProductDetail, fetchProductPreview, uploadProductImage } from '@/app/lib/admin';
import * as auth from '@/app/lib/auth';

jest.mock('@/app/lib/auth', () => ({
  ...jest.requireActual('@/app/lib/auth'),
  fetchWithAuth: jest.fn(),
}));

const mAuth = auth as jest.Mocked<typeof auth>;

beforeEach(() => {
  mAuth.fetchWithAuth.mockReset();
  mAuth.fetchWithAuth.mockResolvedValue({ ok: true, status: 200, json: async () => ({}) } as Response);
});

const urlOf = () => String(mAuth.fetchWithAuth.mock.calls[0][0]);

describe('company in the address', () => {
  it('uploadProductImage', async () => {
    await uploadProductImage(5, new File(['x'], 'a.png', { type: 'image/png' }), 'alt', 7);
    expect(urlOf()).toMatch(/\/admin\/products\/5\/images\/\?company=7$/);
  });

  it('uploadProductImage without a company keeps its address', async () => {
    await uploadProductImage(5, new File(['x'], 'a.png', { type: 'image/png' }));
    expect(urlOf()).toMatch(/\/admin\/products\/5\/images\/$/);
  });

  it('fetchAdminProductDetail', async () => {
    await fetchAdminProductDetail(5, 7);
    expect(urlOf()).toMatch(/\/admin\/products\/5\/\?company=7$/);
  });

  it('fetchProductPreview', async () => {
    await fetchProductPreview(5, 7);
    expect(urlOf()).toMatch(/\/admin\/products\/5\/storefront-preview\/\?company=7$/);
  });
});
