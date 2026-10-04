import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { CategoryImagesEditor } from '@/app/admin/settings/storefront/CategoryImagesEditor';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * La imagen de cada categoría se coloca desde el panel y se guarda al momento.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const mockFetch = fetchWithAuth as jest.Mock;

const URL_ = '/api/storefront/images/' + 'b'.repeat(32) + '/';
const reply = (status: number, body: unknown) => ({ ok: status < 300, status, json: async () => body });

beforeEach(() => mockFetch.mockReset());

it('sube la imagen de una categoría y la guarda en esa categoría', async () => {
  const patched: unknown[] = [];
  mockFetch.mockImplementation(async (url: string, init: RequestInit = {}) => {
    const u = String(url);
    if (u.includes('/admin/storefront/images/')) return reply(201, { url: URL_ });
    if (u.includes('/admin/categories/2/') && init.method === 'PATCH') {
      patched.push(JSON.parse(String(init.body)));
      return reply(200, { id: 2, name: 'iPhone', slug: 'iphone', image_url: URL_ });
    }
    return reply(200, [
      { id: 1, name: 'Accesorios', slug: 'accesorios', image_url: '' },
      { id: 2, name: 'iPhone', slug: 'iphone', image_url: '' },
    ]);
  });
  const onNotice = jest.fn();
  render(<CategoryImagesEditor companyId={7} canManage onNotice={onNotice} />);

  const input = await screen.findByLabelText('Subir iPhone');
  await userEvent.upload(input, new File([new Uint8Array([1])], 'iphone.png', { type: 'image/png' }));

  await waitFor(() => expect(patched).toEqual([{ image_url: URL_ }]));
  expect(await screen.findByRole('img', { name: 'iPhone' })).toHaveAttribute('src', URL_);
  expect(onNotice).toHaveBeenCalledWith('Imagen de «iPhone» guardada.');
});

it('dice el motivo si el servidor no deja guardar', async () => {
  mockFetch.mockImplementation(async (url: string, init: RequestInit = {}) => {
    const u = String(url);
    if (u.includes('/admin/storefront/images/')) return reply(201, { url: URL_ });
    if (init.method === 'PATCH') return reply(403, {});
    return reply(200, [{ id: 1, name: 'Accesorios', slug: 'accesorios', image_url: '' }]);
  });
  render(<CategoryImagesEditor companyId={7} canManage onNotice={jest.fn()} />);

  await userEvent.upload(
    await screen.findByLabelText('Subir Accesorios'),
    new File([new Uint8Array([1])], 'a.png', { type: 'image/png' }),
  );

  const card = (await screen.findByText('Accesorios')).closest('li')!;
  expect(await within(card).findByRole('alert')).toHaveTextContent('No tienes permisos para editar categorías.');
});

it('sin permiso sobre el catálogo sólo muestra', async () => {
  mockFetch.mockResolvedValue(reply(200, [{ id: 1, name: 'Accesorios', slug: 'accesorios', image_url: URL_ }]));
  render(<CategoryImagesEditor companyId={7} canManage={false} onNotice={jest.fn()} />);

  expect(await screen.findByRole('img', { name: 'Accesorios' })).toBeInTheDocument();
  expect(screen.queryByLabelText('Subir Accesorios')).toBeNull();
});
