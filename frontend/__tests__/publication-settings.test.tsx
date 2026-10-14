import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { PublicationSettings } from '@/app/admin/components/PublicationSettings';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * INVENTORY-UNIFIED-01 · decisión del propietario D2.
 *
 * Publicar en bloque (la acción de la lista y la carga de productos) pide que el
 * producto esté activo, con precio y con categoría. Una imagen sólo la pide la
 * empresa que lo configura aquí: una casilla, apagada al crearse, que se guarda
 * sola — sólo su campo viaja.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/api', () => ({ API_BASE: '/api' }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

let patches: { url: string; body: Record<string, unknown> }[];
let refusal: { status: number; body: unknown } | null;

beforeEach(() => {
  patches = [];
  refusal = null;
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const body = JSON.parse(String(init.body));
    patches.push({ url: String(input), body });
    if (refusal) return { ok: false, status: refusal.status, json: async () => refusal!.body } as Response;
    return {
      ok: true, status: 200,
      json: async () => ({ settings: { publication_requires_image: body.publication_requires_image } }),
    } as Response;
  });
});

function shown(props: Partial<React.ComponentProps<typeof PublicationSettings>> = {}) {
  const onSaved = jest.fn();
  render(<PublicationSettings companyId={7} requiresImage={false} canEdit onSaved={onSaved} {...props} />);
  return onSaved;
}

const box = () => screen.getByRole('checkbox', { name: 'Exigir imagen para publicar en bloque' });
const save = () => fireEvent.click(screen.getByRole('button', { name: 'Guardar regla de publicación' }));

test('empieza apagada, y dice qué se exige siempre y qué no cambia', () => {
  shown();
  expect(box()).not.toBeChecked();
  expect(screen.getByText(/activo, con precio de venta y con categoría/)).toBeInTheDocument();
  expect(screen.getByText(/La ficha de un producto publica como siempre/)).toBeInTheDocument();
});

test('guardar manda sólo esta opción, a esta empresa', async () => {
  const onSaved = shown();
  fireEvent.click(box());
  save();

  await waitFor(() => expect(onSaved).toHaveBeenCalled());
  expect(patches).toEqual([{
    url: '/api/admin/company-settings/?company=7',
    body: { publication_requires_image: true },
  }]);
  expect(await screen.findByText('Cambios guardados.')).toBeInTheDocument();
  expect(box()).toBeChecked();
});

test('quien no puede cambiarla la lee y no ve el botón', () => {
  shown({ requiresImage: true, canEdit: false });
  expect(box()).toBeChecked();
  expect(box()).toBeDisabled();
  expect(screen.queryByRole('button', { name: 'Guardar regla de publicación' })).toBeNull();
});

test('si el servidor se niega, se dice y no se da por guardado', async () => {
  refusal = { status: 403, body: { detail: 'No tienes permisos para esta acción.' } };
  const onSaved = shown();
  fireEvent.click(box());
  save();
  expect(await screen.findByRole('alert')).toHaveTextContent('No tienes permisos para esta acción.');
  expect(onSaved).not.toHaveBeenCalled();
  expect(screen.queryByText('Cambios guardados.')).toBeNull();
});
