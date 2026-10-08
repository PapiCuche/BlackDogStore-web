/**
 * MAIL-TEMPLATE-01, fase 3A — el código de 6 dígitos del correo de verificación.
 *
 * El enlace del correo sigue siendo el camino principal; el código es para quien
 * lee el correo en un dispositivo y se registra en otro. LO QUE ESTAS PRUEBAS
 * FIJAN es que escribirlo no moleste:
 *   · se puede pegar tal como viene («123 456», «123-456»);
 *   · se envía solo al completar los seis dígitos, una vez;
 *   · el teclado y el autocompletado del móvil lo reconocen como un código;
 *   · un fallo se dice con amabilidad y sin revelar si la cuenta existe;
 *   · pedir otro código tiene una espera a la vista.
 *
 * Cuántos intentos, cuánto dura y quién puede verificar lo decide el servidor
 * (`test_verification_code.py`).
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ThemeProvider } from '@/app/components/ThemeProvider';
import { StorefrontProvider } from '@/app/components/StorefrontProvider';
import { NEUTRAL_CONFIG } from '@/app/lib/storefront';

const mockPush = jest.fn();
let mockSearch = new URLSearchParams();
jest.mock('next/navigation', () => ({
  useRouter: () => ({
    push: mockPush, replace: jest.fn(), refresh: jest.fn(),
    back: jest.fn(), forward: jest.fn(), prefetch: jest.fn(),
  }),
  usePathname: () => '/',
  useSearchParams: () => mockSearch,
  useParams: () => ({}),
}));

const mockVerifyCode = jest.fn();
const mockResend = jest.fn();
const mockRegister = jest.fn();
const mockLogin = jest.fn();
const mockVerifyLink = jest.fn();
jest.mock('@/app/lib/auth', () => ({
  verifyEmailCode: (...args: unknown[]) => mockVerifyCode(...args),
  resendVerification: (...args: unknown[]) => mockResend(...args),
  verifyEmail: (...args: unknown[]) => mockVerifyLink(...args),
  register: (...args: unknown[]) => mockRegister(...args),
  login: (...args: unknown[]) => mockLogin(...args),
  getCurrentUser: () => Promise.resolve(null),
  fetchWithAuth: jest.fn(),
  hasInternalAccess: () => Promise.resolve(false),
  forgetInternalAccess: jest.fn(),
}));
jest.mock('@/app/auth/components/DevQuickLogin', () => ({ DevQuickLogin: () => null }));
jest.mock('@/app/components/GoogleSignIn', () => ({ GoogleSignIn: () => null }));

/* eslint-disable @typescript-eslint/no-require-imports */
const { VerificationCode, cleanCode } = require('@/app/auth/VerificationCode');
const AuthPage = require('@/app/auth/page').default;
const VerifyEmailPage = require('@/app/auth/verify-email/page').default;
/* eslint-enable @typescript-eslint/no-require-imports */

const EMAIL = 'ana@correo.test';
const REFUSED = 'El código no es válido o ya venció. Pide uno nuevo.';

function wrapped(node: React.ReactNode) {
  return render(
    <ThemeProvider>
      <StorefrontProvider config={NEUTRAL_CONFIG}>{node}</StorefrontProvider>
    </ThemeProvider>,
  );
}

const field = () => screen.getByLabelText('Código de verificación') as HTMLInputElement;
const type = (value: string) => fireEvent.change(field(), { target: { value } });

beforeAll(() => {
  if (!window.matchMedia) {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: () => ({
        matches: false, addEventListener: () => {}, removeEventListener: () => {},
        addListener: () => {}, removeListener: () => {},
      }),
    });
  }
});

beforeEach(() => {
  for (const mock of [mockPush, mockVerifyCode, mockResend, mockRegister, mockLogin, mockVerifyLink]) mock.mockReset();
  mockVerifyCode.mockResolvedValue({ detail: 'Correo verificado correctamente. Ya puedes iniciar sesión.' });
  mockResend.mockResolvedValue({ detail: 'ok' });
  mockSearch = new URLSearchParams();
  window.sessionStorage.clear();
});

afterEach(() => {
  jest.useRealTimers();
});

describe('lo que se escribe o se pega', () => {
  it.each([
    ['123456', '123456'],
    ['123 456', '123456'],
    ['123-456', '123456'],
    [' 1 2 3 – 4 5 6 ', '123456'],
    ['12a3b4', '1234'],
    ['1234567890', '123456'],
    ['', ''],
  ])('«%s» se lee como «%s»', (typed, read) => {
    expect(cleanCode(typed)).toBe(read);
  });
});

describe('el campo del código', () => {
  it('es un código para el teclado y el autocompletado del móvil', () => {
    render(<VerificationCode email={EMAIL} onVerified={jest.fn()} />);
    expect(field().getAttribute('autocomplete')).toBe('one-time-code');
    expect(field().getAttribute('inputmode')).toBe('numeric');
    expect(screen.getByText(/ana@correo\.test/)).toBeInTheDocument();
  });

  it('no envía nada hasta tener los seis dígitos, y entonces envía solo', async () => {
    const onVerified = jest.fn();
    render(<VerificationCode email={EMAIL} onVerified={onVerified} />);

    type('12345');
    expect(mockVerifyCode).not.toHaveBeenCalled();

    type('123456');
    await waitFor(() => expect(mockVerifyCode).toHaveBeenCalledTimes(1));
    expect(mockVerifyCode).toHaveBeenCalledWith(EMAIL, '123456');
    await waitFor(() => expect(onVerified).toHaveBeenCalledTimes(1));
  });

  it('acepta el código pegado con espacios o guiones', async () => {
    const first = render(<VerificationCode email={EMAIL} onVerified={jest.fn()} />);

    type('123 456');
    expect(field().value).toBe('123456');
    await waitFor(() => expect(mockVerifyCode).toHaveBeenCalledWith(EMAIL, '123456'));
    first.unmount();

    render(<VerificationCode email={EMAIL} onVerified={jest.fn()} />);
    type('654-321');
    await waitFor(() => expect(mockVerifyCode).toHaveBeenCalledWith(EMAIL, '654321'));
  });

  it('un código rechazado se dice con amabilidad, se borra y no se reenvía solo', async () => {
    const onVerified = jest.fn();
    mockVerifyCode.mockRejectedValue(new Error(REFUSED));
    render(<VerificationCode email={EMAIL} onVerified={onVerified} />);

    type('000000');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(REFUSED);
    expect(field().value).toBe('');
    expect(field()).not.toBeDisabled();
    expect(onVerified).not.toHaveBeenCalled();

    await act(async () => { await Promise.resolve(); });
    expect(mockVerifyCode).toHaveBeenCalledTimes(1);
  });

  it('lo que dice de un fallo no cuenta si la cuenta existe', async () => {
    // Lo que el servidor conteste, la pantalla no añade nada propio sobre la cuenta.
    mockVerifyCode.mockRejectedValue(new Error(REFUSED));
    render(<VerificationCode email={EMAIL} onVerified={jest.fn()} />);
    type('000000');
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).not.toMatch(/no existe|no está registrad|ya está verificad|cuenta/i);
  });

  it('mientras comprueba un código no deja escribir ni envía otro', async () => {
    let finish: (value: { detail: string }) => void = () => {};
    mockVerifyCode.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    render(<VerificationCode email={EMAIL} onVerified={jest.fn()} />);

    type('123456');
    await waitFor(() => expect(field()).toBeDisabled());
    type('654321');
    expect(mockVerifyCode).toHaveBeenCalledTimes(1);
    await act(async () => { finish({ detail: 'ok' }); });
  });
});

describe('pedir otro código', () => {
  it('tiene una espera de 60 segundos a la vista, y vuelve a empezar al pedirlo', async () => {
    jest.useFakeTimers();
    render(<VerificationCode email={EMAIL} onVerified={jest.fn()} />);

    const waiting = screen.getByRole('button', { name: /Enviar otro código en 60 s/ });
    expect(waiting).toBeDisabled();

    act(() => { jest.advanceTimersByTime(59_000); });
    expect(screen.getByRole('button', { name: /Enviar otro código en 1 s/ })).toBeDisabled();

    act(() => { jest.advanceTimersByTime(1_000); });
    const ready = screen.getByRole('button', { name: 'Enviar otro código' });
    expect(ready).not.toBeDisabled();

    await act(async () => { fireEvent.click(ready); });
    expect(mockResend).toHaveBeenCalledWith(EMAIL);
    expect(screen.getByRole('button', { name: /Enviar otro código en 60 s/ })).toBeDisabled();
    // El servidor no dice si envió: la pantalla tampoco afirma que el código anterior murió.
    expect(screen.getByRole('status')).toHaveTextContent(/te enviamos un código nuevo/i);
    expect(screen.getByRole('status')).toHaveTextContent(/usa el más reciente/i);
    expect(screen.getByRole('status').textContent).not.toMatch(/ya no sirve/i);
  });

  it('mientras se comprueba un código no se puede pedir otro', async () => {
    jest.useFakeTimers();
    let finish: (value: { detail: string }) => void = () => {};
    mockVerifyCode.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    render(<VerificationCode email={EMAIL} onVerified={jest.fn()} />);
    act(() => { jest.advanceTimersByTime(60_000); });
    expect(screen.getByRole('button', { name: 'Enviar otro código' })).not.toBeDisabled();

    type('123456');
    expect(screen.getByRole('button', { name: 'Enviar otro código' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Enviar otro código' }));
    expect(mockResend).not.toHaveBeenCalled();
    await act(async () => { finish({ detail: 'ok' }); });
  });

  it('si no se pudo pedir, lo dice y deja volver a intentarlo', async () => {
    jest.useFakeTimers();
    mockResend.mockRejectedValue(new Error('Demasiadas solicitudes.'));
    render(<VerificationCode email={EMAIL} onVerified={jest.fn()} />);
    act(() => { jest.advanceTimersByTime(60_000); });

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Enviar otro código' })); });
    expect(screen.getByRole('alert')).toHaveTextContent(/No pudimos enviar otro código/);
    expect(screen.getByRole('button', { name: 'Enviar otro código' })).not.toBeDisabled();
  });
});

describe('dónde aparece', () => {
  it('al registrarse, la pantalla pide el código del correo recién escrito', async () => {
    mockRegister.mockResolvedValue({ detail: 'ok', requires_verification: true });
    wrapped(<AuthPage />);

    fireEvent.click(await screen.findByRole('button', { name: 'Crear una ahora' }));
    fireEvent.change(screen.getByLabelText('Usuario'), { target: { value: 'ana' } });
    fireEvent.change(screen.getByLabelText('Correo electrónico'), { target: { value: EMAIL } });
    fireEvent.change(screen.getByLabelText('Contraseña'), { target: { value: 'Una-clave-larga-91' } });
    fireEvent.change(screen.getByLabelText('Confirmar contraseña'), { target: { value: 'Una-clave-larga-91' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Registrarme' })); });

    expect(await screen.findByLabelText('Código de verificación')).toBeInTheDocument();
    expect(screen.getByText(/ana@correo\.test/)).toBeInTheDocument();
    expect(screen.getByText(/abre el enlace del correo/i)).toBeInTheDocument();      // el enlace sigue siendo el camino

    type('123456');
    await waitFor(() => expect(mockVerifyCode).toHaveBeenCalledWith(EMAIL, '123456'));
    expect(await screen.findByRole('heading', { name: 'Iniciar sesión' })).toBeInTheDocument();
    expect(screen.getByText(/Correo verificado/)).toBeInTheDocument();
    expect(mockLogin).not.toHaveBeenCalled();                                         // verificar no inicia sesión
  });

  it('quien abre la página de verificación sin enlace puede escribir su correo y su código', async () => {
    wrapped(<VerifyEmailPage />);

    type('123456');
    expect(mockVerifyCode).not.toHaveBeenCalled();                                    // falta el correo

    // El código ya estaba escrito: escribir el correo después no lo pierde.
    expect(screen.getByRole('button', { name: 'Verificar' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Correo electrónico'), { target: { value: EMAIL } });
    fireEvent.click(screen.getByRole('button', { name: 'Verificar' }));
    await waitFor(() => expect(mockVerifyCode).toHaveBeenCalledWith(EMAIL, '123456'));
    expect(await screen.findByRole('heading', { name: 'Correo verificado' })).toBeInTheDocument();
    expect(mockVerifyLink).not.toHaveBeenCalled();
  });

  it('con el enlace del correo, la página sigue verificando sola', async () => {
    mockVerifyLink.mockResolvedValue({ detail: 'Correo verificado correctamente. Ya puedes iniciar sesión.' });
    mockSearch = new URLSearchParams({ token: 'EL-TOKEN-DEL-ENLACE' });
    wrapped(<VerifyEmailPage />);

    // Desde el primer instante: quien llega por el enlace no ve un campo que rellenar.
    expect(screen.getByRole('heading', { name: 'Verificando correo' })).toBeInTheDocument();
    expect(screen.queryByLabelText('Código de verificación')).toBeNull();

    expect(await screen.findByRole('heading', { name: 'Correo verificado' })).toBeInTheDocument();
    expect(mockVerifyLink).toHaveBeenCalledWith('EL-TOKEN-DEL-ENLACE');
    expect(screen.queryByLabelText('Código de verificación')).toBeNull();
  });
});
