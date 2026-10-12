import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import AccountPage from '@/app/account/page';
import { fetchWithAuth, getCurrentUser, resendVerification } from '@/app/lib/auth';
import { isSensitivePath } from '@/app/lib/analytics/privacy';

/**
 * CLIENT-SEC-01 · «Mi cuenta»: the buyer's own profile in this shop.
 *
 * The page shows what the server says and sends back only what changed. The
 * e-mail is the account's: shown with whether it was verified, never edited.
 * Whether «send the verification again» is offered is the server's answer.
 */

// ONE router object, as the real hook gives: the page loads the profile when it
// mounts, and a new object on every render would load it again over what was typed.
const push = jest.fn();
const mockRouter = { push };
jest.mock('next/navigation', () => ({ useRouter: () => mockRouter }));
jest.mock('@/app/lib/auth', () => ({
  fetchWithAuth: jest.fn(), getCurrentUser: jest.fn(), resendVerification: jest.fn(),
}));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;
const who = getCurrentUser as jest.MockedFunction<typeof getCurrentUser>;
const resend = resendVerification as jest.MockedFunction<typeof resendVerification>;

const PROFILE = {
  email: 'ana@correo.test', email_verified_at: null as string | null, email_verification_available: false,
  first_name: 'Ana', last_name: 'Torres', phone: '987654321', document_type: 'dni', document_number: '70000001',
};

let profile: typeof PROFILE;
let answer: { status: number; body: unknown } | null;
let patched: Record<string, string>[];

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

beforeEach(() => {
  profile = { ...PROFILE };
  answer = null;
  patched = [];
  push.mockReset();
  resend.mockReset();
  resend.mockResolvedValue({ detail: 'ok' });
  who.mockReset();
  who.mockResolvedValue({ id: 1, username: 'ana' } as never);
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    expect(String(input)).toMatch(/\/account\/profile\/$/);
    if ((init.method ?? 'GET') !== 'PATCH') return reply(200, profile);
    const body = JSON.parse(String(init.body));
    patched.push(body);
    if (answer) return reply(answer.status, answer.body);
    profile = { ...profile, ...body };
    return reply(200, profile);
  });
});

async function opened() {
  render(<AccountPage />);
  await screen.findByRole('heading', { name: 'Mis datos' });
}

const save = () => fireEvent.click(screen.getByRole('button', { name: 'Guardar' }));

test('someone who is not signed in is sent to sign in, and nothing is asked', async () => {
  who.mockResolvedValue(null);
  render(<AccountPage />);
  await waitFor(() => expect(push).toHaveBeenCalledWith('/auth'));
  expect(send).not.toHaveBeenCalled();
});

test('the profile the server keeps is what the form shows', async () => {
  await opened();
  expect(screen.getByLabelText('Nombres')).toHaveValue('Ana');
  expect(screen.getByLabelText('Apellidos')).toHaveValue('Torres');
  expect(screen.getByLabelText('Teléfono')).toHaveValue('987654321');
  expect(screen.getByLabelText('Tipo de documento')).toHaveValue('dni');
  expect(screen.getByLabelText('Número de documento')).toHaveValue('70000001');
});

test('the e-mail is shown and cannot be typed over', async () => {
  await opened();
  expect(screen.getByText('ana@correo.test')).toBeInTheDocument();
  expect(screen.queryByDisplayValue('ana@correo.test')).not.toBeInTheDocument();
  expect(screen.getAllByRole('textbox').map((input) => (input as HTMLInputElement).name || input.getAttribute('autocomplete')))
    .not.toContain('email');
});

test('an address nobody proved says so, and one that was proved says that', async () => {
  await opened();
  expect(screen.getByText('Sin verificar')).toBeInTheDocument();
  expect(screen.queryByText('Verificado')).not.toBeInTheDocument();
});

test('a verified address', async () => {
  profile.email_verified_at = '2026-10-01T10:00:00Z';
  await opened();
  expect(screen.getByText('Verificado')).toBeInTheDocument();
  expect(screen.queryByText('Sin verificar')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Reenviar verificación' })).not.toBeInTheDocument();
});

test('sending the verification again is offered only when the server says it would do something', async () => {
  await opened();
  expect(screen.queryByRole('button', { name: 'Reenviar verificación' })).not.toBeInTheDocument();
});

test('when it is available, it uses the existing action with the account\'s own address', async () => {
  profile.email_verification_available = true;
  await opened();
  fireEvent.click(screen.getByRole('button', { name: 'Reenviar verificación' }));
  await waitFor(() => expect(resend).toHaveBeenCalledWith('ana@correo.test'));
  expect(await screen.findByText(/te enviamos un nuevo enlace/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Reenviar verificación' })).toBeDisabled();
});

test('saving sends only what changed', async () => {
  await opened();
  fireEvent.change(screen.getByLabelText('Teléfono'), { target: { value: ' 912345678 ' } });
  save();
  expect(await screen.findByText('Datos guardados.')).toBeInTheDocument();
  expect(patched).toEqual([{ phone: '912345678' }]);
});

test('saving with nothing changed asks the server for nothing', async () => {
  await opened();
  save();
  expect(await screen.findByText('Datos guardados.')).toBeInTheDocument();
  expect(patched).toEqual([]);
});

test('a document travels as its type and its number together', async () => {
  await opened();
  fireEvent.change(screen.getByLabelText('Número de documento'), { target: { value: '70000002' } });
  save();
  await screen.findByText('Datos guardados.');
  expect(patched).toEqual([{ document_type: 'dni', document_number: '70000002' }]);
});

test('choosing no document takes the number off as well', async () => {
  await opened();
  fireEvent.change(screen.getByLabelText('Tipo de documento'), { target: { value: '' } });
  expect(screen.getByLabelText('Número de documento')).toBeDisabled();
  expect(screen.getByLabelText('Número de documento')).toHaveValue('');
  save();
  await screen.findByText('Datos guardados.');
  expect(patched).toEqual([{ document_type: '', document_number: '' }]);
});

test('what the server refuses is said next to its field, and nothing is called saved', async () => {
  answer = { status: 400, body: { phone: ['Teléfono inválido. Ejemplos aceptados: 987654321.'] } };
  await opened();
  fireEvent.change(screen.getByLabelText('Teléfono'), { target: { value: '12345' } });
  save();

  const field = screen.getByLabelText('Teléfono');
  await waitFor(() => expect(field).toHaveAttribute('aria-invalid', 'true'));
  const said = screen.getByText('Teléfono inválido. Ejemplos aceptados: 987654321.');
  expect(field).toHaveAttribute('aria-describedby', said.id);
  expect(screen.getByText('Revisa los datos marcados.')).toBeInTheDocument();
  expect(screen.queryByText('Datos guardados.')).not.toBeInTheDocument();
  expect(field).toHaveValue('12345');                       // what was typed stays to be corrected
});

test('a document that cannot be kept gets the server\'s one sentence, word for word', async () => {
  const sentence = 'No pudimos guardar ese documento. Revisa los datos o comunícate con la tienda.';
  answer = { status: 400, body: { document_number: [sentence] } };
  await opened();
  fireEvent.change(screen.getByLabelText('Número de documento'), { target: { value: '70000002' } });
  save();
  expect(await screen.findByText(sentence)).toBeInTheDocument();
  expect(screen.getByLabelText('Número de documento')).toHaveAttribute('aria-invalid', 'true');
});

test('too many saves in a row are told to wait', async () => {
  answer = { status: 429, body: { detail: 'Request was throttled. Expected available in 3000 seconds.' } };
  await opened();
  fireEvent.change(screen.getByLabelText('Teléfono'), { target: { value: '912345678' } });
  save();
  expect(await screen.findByText(/Inténtalo de nuevo más tarde/)).toBeInTheDocument();
  expect(screen.queryByText(/throttled/)).not.toBeInTheDocument();
});

test('a profile that cannot be loaded says so and shows no form', async () => {
  send.mockImplementation(async () => reply(404, { detail: 'Tienda no encontrada.' }));
  render(<AccountPage />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Tienda no encontrada.');
  expect(screen.queryByRole('button', { name: 'Guardar' })).not.toBeInTheDocument();
});

test('no measurement script shares a page with these fields', () => {
  expect(isSensitivePath('/account')).toBe(true);
  expect(isSensitivePath('/accounting')).toBe(false);
});
