import { fetchWithAuth } from '@/app/lib/auth';
import {
  DEFAULT_DESIGN, DocumentDesignFieldError, changedFields, designChanges, draftProblems, fetchDocumentDesign,
  normaliseText, textProblem, updateDocumentDesign,
} from '@/app/admin/lib/document-design-api';
import { fetchDocumentPreview } from '@/app/admin/lib/document-preview-api';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = jest.mocked(fetchWithAuth);

function reply(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name: string) => headers[name] ?? null },
    json: async () => body,
    blob: async () => new Blob(['%PDF']),
  } as unknown as Response;
}

beforeEach(() => send.mockReset());

test('el mensaje final empieza vacío: no hay texto de ninguna tienda por defecto', () => {
  expect(DEFAULT_DESIGN.thank_you_message).toBe('');
  expect(DEFAULT_DESIGN.footer_text).toBe('');
  expect(JSON.stringify(DEFAULT_DESIGN).toLowerCase()).not.toMatch(/gracias|manada|black dog/);
});

test('los textos se normalizan como en el servidor', () => {
  expect(normaliseText('  Línea uno\n\n  línea\tdos  ')).toBe('Línea uno línea dos');
});

test.each([
  ['x'.repeat(121), 120, /Máximo 120/],
  ['Gracias <b>', 120, /texto plano/],
  ['Gracias 🐾', 120, /no puede imprimir: 🐾/],
  ['CO₂', 200, /no puede imprimir: ₂/],
])('un texto que el papel no imprimiría tal cual se explica: %s', (value, limit, message) => {
  expect(textProblem(value, limit)).toMatch(message);
});

test.each(['Gracias por su compra.', 'Atención: lunes–sábado · 9 a 18 h — €', 'Ñandú, acción, «comillas»'])(
  'lo que el papel sí imprime pasa: %s', (value) => {
    expect(textProblem(value, 200)).toBeNull();
  });

test('los límites cuentan caracteres, no bytes', () => {
  expect(textProblem('ñ'.repeat(120), 120)).toBeNull();
  expect(draftProblems({ ...DEFAULT_DESIGN, thank_you_message: 'ñ'.repeat(121) },
    { footer_text: 200, thank_you_message: 120 })).toHaveProperty('thank_you_message');
});

test('los cambios se dicen en palabras y sólo viajan los campos que cambian', () => {
  const draft = { ...DEFAULT_DESIGN, logo_size: 'large' as const, show_phone: false, thank_you_message: '  Hola  ' };
  expect(designChanges(DEFAULT_DESIGN, draft)).toEqual([
    { field: 'logo_size', label: 'Tamaño del logo', from: 'Mediano', to: 'Grande' },
    { field: 'show_phone', label: 'Teléfono', from: 'sí', to: 'no' },
    { field: 'thank_you_message', label: 'Mensaje final', from: '(vacío)', to: '«Hola»' },
  ]);
  expect(changedFields(DEFAULT_DESIGN, draft)).toEqual({ logo_size: 'large', show_phone: false, thank_you_message: 'Hola' });
  // Sólo espacios de más no es un cambio.
  expect(changedFields({ ...DEFAULT_DESIGN, footer_text: 'Pie' }, { ...DEFAULT_DESIGN, footer_text: ' Pie ' })).toEqual({});
});

test('GET y PATCH con ?company=; el PATCH lleva sólo el cambio', async () => {
  send.mockResolvedValue(reply(200, { design: DEFAULT_DESIGN }));
  await fetchDocumentDesign(4);
  expect(String(send.mock.calls[0][0])).toMatch(/\/admin\/document-design\/\?company=4$/);

  await updateDocumentDesign(4, { thank_you_message: 'Hola' });
  const [url, init] = send.mock.calls[1];
  expect(String(url)).toMatch(/\/admin\/document-design\/\?company=4$/);
  expect(init?.method).toBe('PATCH');
  expect(JSON.parse(String(init?.body))).toEqual({ thank_you_message: 'Hola' });
});

test('un 400 devuelve los mensajes por campo y dice que no se guardó', async () => {
  send.mockResolvedValue(reply(400, { footer_text: ['Sólo texto plano: sin los signos < ni >.'] }));
  const error = await updateDocumentDesign(null, { footer_text: '<b>' }).catch((e) => e);
  expect(error).toBeInstanceOf(DocumentDesignFieldError);
  expect(error.fields).toEqual({ footer_text: ['Sólo texto plano: sin los signos < ni >.'] });
  expect(error.message).toMatch(/No se guardó nada/);
});

test('un 403 al guardar se explica', async () => {
  send.mockResolvedValue(reply(403, { detail: 'x' }));
  await expect(updateDocumentDesign(null, { logo_size: 'small' })).rejects.toThrow(/company.manage/);
});

test('la vista previa envía el borrador junto al documento, y un 400 del borrador se dice', async () => {
  send.mockResolvedValue(reply(200, null, { 'Content-Type': 'application/pdf' }));
  await fetchDocumentPreview('sales_note', 2, undefined, { thank_you_message: 'Hola' });
  expect(JSON.parse(String(send.mock.calls[0][1]?.body))).toEqual({ document: 'sales_note', design: { thank_you_message: 'Hola' } });

  send.mockResolvedValue(reply(400, { design: { footer_text: ['Máximo 200 caracteres (tiene 201).'] } }));
  await expect(fetchDocumentPreview('sales_note', 2, undefined, { footer_text: 'x' }))
    .rejects.toThrow('El diseño tiene un error: Máximo 200 caracteres (tiene 201).');
});

test('un 429 de la vista previa trae los segundos de Retry-After', async () => {
  send.mockResolvedValue(reply(429, { detail: 'x' }, { 'Retry-After': '7' }));
  const error = await fetchDocumentPreview('sales_note', 2, undefined, { thank_you_message: 'Hola' }).catch((e) => e);
  expect(error.status).toBe(429);
  expect(error.retryAfter).toBe(7);
  send.mockResolvedValue(reply(429, { detail: 'x' }));
  expect((await fetchDocumentPreview('sales_note', 2).catch((e) => e)).retryAfter).toBeNull();
});

test('el correo del ticket de 80 mm empieza apagado y tiene su propio nombre', () => {
  expect(DEFAULT_DESIGN.show_email_on_roll).toBe(false);
  expect(designChanges(DEFAULT_DESIGN, { ...DEFAULT_DESIGN, show_email_on_roll: true })[0].label)
    .toBe('Correo en el ticket de 80 mm y la impresión térmica');
});
