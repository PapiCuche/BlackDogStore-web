import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import AccountPage from '@/app/account/page';
import { fetchWithAuth, getCurrentUser } from '@/app/lib/auth';
import { isSensitivePath } from '@/app/lib/analytics/privacy';

/**
 * CLIENT-SEC-01 · «Mi cuenta»: the buyer's own profile in this shop.
 *
 * The page shows what the server says and sends back only what changed. The
 * e-mail is the account's: shown with whether it was verified, never edited,
 * and proved with a code sent to it. Whether that is offered, and whether the
 * document is the buyer's to change, are the server's answers.
 */

// ONE router object, as the real hook gives: the page loads the profile when it
// mounts, and a new object on every render would load it again over what was typed.
const push = jest.fn();
const mockRouter = { push };
jest.mock('next/navigation', () => ({ useRouter: () => mockRouter }));
jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn(), getCurrentUser: jest.fn() }));
// The address book has its own tests; here it would only add requests.
jest.mock('@/app/components/AddressBook', () => ({ AddressBook: () => null }));
jest.mock('@/app/components/LinkedAccounts', () => ({ LinkedAccounts: () => null }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;
const who = getCurrentUser as jest.MockedFunction<typeof getCurrentUser>;

const PROFILE = {
  // Most of these tests write a document, and that takes a proved address.
  email: 'ana@correo.test', email_verified_at: '2026-10-01T10:00:00Z' as string | null, email_verification_available: false,
  first_name: 'Ana', last_name: 'Torres', phone: '987654321', document_type: 'dni', document_number: '70000001',
  document_locked: false,
};

let profile: typeof PROFILE;
let answer: { status: number; body: unknown } | null;
let patched: Record<string, string>[];
let verification: { send: { status: number; body: unknown }; confirm: { status: number; body: unknown } };
let verifyCalls: { url: string; body: unknown }[];

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

beforeEach(() => {
  profile = { ...PROFILE };
  answer = null;
  patched = [];
  push.mockReset();
  verification = {
    send: { status: 200, body: { detail: 'Te enviamos un código a tu correo.', already_verified: false } },
    confirm: { status: 200, body: { detail: 'Correo verificado.', email_verified_at: '2026-10-12T18:00:00Z' } },
  };
  verifyCalls = [];
  who.mockReset();
  who.mockResolvedValue({ id: 1, username: 'ana' } as never);
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    if (url.includes('/account/verify-email/')) {
      verifyCalls.push({ url, body: JSON.parse(String(init.body)) });
      const step = url.endsWith('/send/') ? verification.send : verification.confirm;
      return reply(step.status, step.body);
    }
    expect(url).toMatch(/\/account\/profile\/$/);
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

function unverified() {
  profile.email_verified_at = null;
  profile.email_verification_available = true;
}

test('an address nobody proved says so', async () => {
  unverified();
  await opened();
  expect(screen.getByText('Sin verificar')).toBeInTheDocument();
  expect(screen.queryByText('Verificado')).not.toBeInTheDocument();
});

test('a verified address says that, and is offered nothing', async () => {
  await opened();
  expect(screen.getByText('Verificado')).toBeInTheDocument();
  expect(screen.queryByText('Sin verificar')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Verificar mi correo' })).not.toBeInTheDocument();
});

test('verifying is offered only when the server says there is something to verify', async () => {
  profile.email_verified_at = null;
  profile.email_verification_available = false;
  await opened();
  expect(screen.queryByRole('button', { name: 'Verificar mi correo' })).not.toBeInTheDocument();
});

test('a verified address is never offered a verification, whatever else the server says', async () => {
  profile.email_verification_available = true;
  await opened();
  expect(screen.queryByRole('button', { name: 'Verificar mi correo' })).not.toBeInTheDocument();
});

test('the code is asked for without saying where to send it, and typed back', async () => {
  unverified();
  await opened();
  fireEvent.click(screen.getByRole('button', { name: 'Verificar mi correo' }));

  const field = await screen.findByLabelText('Código de verificación');
  expect(verifyCalls).toEqual([{ url: expect.stringMatching(/\/account\/verify-email\/send\/$/), body: {} }]);
  expect(screen.getByRole('button', { name: 'Confirmar código' })).toBeDisabled();       // nothing typed yet
  fireEvent.change(field, { target: { value: ' 246810 ' } });
  fireEvent.click(screen.getByRole('button', { name: 'Confirmar código' }));

  expect(await screen.findByText('Verificado')).toBeInTheDocument();
  expect(verifyCalls[1]).toEqual({ url: expect.stringMatching(/\/account\/verify-email\/confirm\/$/), body: { code: '246810' } });
  expect(screen.queryByLabelText('Código de verificación')).not.toBeInTheDocument();
  // And with the address proved, the document can be written.
  expect(screen.getByLabelText('Tipo de documento')).toBeEnabled();
});

test('a wrong code is said and can be typed again', async () => {
  unverified();
  verification.confirm = { status: 400, body: { detail: 'El código no es válido o ya venció. Pide uno nuevo.' } };
  await opened();
  fireEvent.click(screen.getByRole('button', { name: 'Verificar mi correo' }));
  fireEvent.change(await screen.findByLabelText('Código de verificación'), { target: { value: '000000' } });
  fireEvent.click(screen.getByRole('button', { name: 'Confirmar código' }));

  expect(await screen.findByRole('alert')).toHaveTextContent('El código no es válido o ya venció');
  expect(screen.getByText('Sin verificar')).toBeInTheDocument();
  expect(screen.getByLabelText('Código de verificación')).toHaveValue('000000');
});

test('asking again too soon is told to wait, in words of ours', async () => {
  unverified();
  verification.send = { status: 429, body: { detail: 'Request was throttled. Expected available in 40 seconds.' } };
  await opened();
  fireEvent.click(screen.getByRole('button', { name: 'Verificar mi correo' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Espera un minuto');
  expect(screen.queryByText(/throttled/)).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Código de verificación')).not.toBeInTheDocument();
});

test('until the address is proved the document is not offered, and the page says why', async () => {
  unverified();
  await opened();
  expect(screen.getByLabelText('Tipo de documento')).toBeDisabled();
  expect(screen.getByLabelText('Número de documento')).toBeDisabled();
  expect(screen.getByText('Verifica tu correo para poder guardar tu documento.')).toBeInTheDocument();
  expect(screen.getByLabelText('Teléfono')).toBeEnabled();
});

test('a document the shop relies on is shown and not offered', async () => {
  profile.document_locked = true;
  await opened();
  expect(screen.getByLabelText('Tipo de documento')).toBeDisabled();
  expect(screen.getByLabelText('Número de documento')).toBeDisabled();
  expect(screen.getByLabelText('Número de documento')).toHaveValue('70000001');
  expect(screen.getByText(/El documento de tu ficha lo cambia la tienda/)).toBeInTheDocument();
});

test('a document on file cannot be taken off, only replaced', async () => {
  await opened();
  const options = Array.from(screen.getByLabelText('Tipo de documento').querySelectorAll('option')).map((o) => o.textContent);
  expect(options).toEqual(['DNI', 'RUC', 'Carnet de Extranjería']);
});

test('a file with no document may stay without one', async () => {
  profile.document_type = '';
  profile.document_number = '';
  await opened();
  const options = Array.from(screen.getByLabelText('Tipo de documento').querySelectorAll('option')).map((o) => o.textContent);
  expect(options[0]).toBe('Sin documento');
  expect(screen.getByLabelText('Número de documento')).toBeDisabled();
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
