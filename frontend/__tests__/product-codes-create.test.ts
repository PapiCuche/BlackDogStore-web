import { createAdminProduct } from '@/app/lib/admin';
import * as auth from '@/app/lib/auth';

/**
 * LABEL-01 · lo que el alta simple le dice a quien escribe un código: el
 * mensaje del servidor tal cual, no sus claves internas.
 */

jest.mock('@/app/lib/auth', () => ({
  ...jest.requireActual('@/app/lib/auth'),
  fetchWithAuth: jest.fn(),
}));

const fetchWithAuth = auth.fetchWithAuth as jest.Mock;
const answer = (status: number, body: unknown) =>
  ({ status, ok: status < 300, json: () => Promise.resolve(body) }) as unknown as Response;
const data = { name: 'Funda', price: '10.00', inventory: 0, code: 'ab' };

beforeEach(() => fetchWithAuth.mockReset());

it('un 400 del código muestra su detalle y no «code invalid_code»', async () => {
  fetchWithAuth.mockResolvedValueOnce(answer(400, {
    detail: 'El código debe tener entre 4 y 64 caracteres imprimibles.', field: 'code', code: 'invalid_code',
  }));
  await expect(createAdminProduct(data)).rejects.toThrow(/^El código debe tener entre 4 y 64 caracteres imprimibles\.$/);
});

it('un 400 de campos del formulario sigue juntando sus mensajes', async () => {
  fetchWithAuth.mockResolvedValueOnce(answer(400, { name: ['Este campo es requerido.'] }));
  await expect(createAdminProduct(data)).rejects.toThrow('Este campo es requerido.');
});

it('un 409 nombra el producto que ya tiene el código', async () => {
  fetchWithAuth.mockResolvedValueOnce(answer(409, {
    detail: 'El código «FUNDA-01» ya identifica a «Funda A» en esta empresa.', field: 'code', code: 'code_conflict',
  }));
  await expect(createAdminProduct(data)).rejects.toThrow('Funda A');
});
