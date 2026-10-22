import { act, fireEvent, render, screen, within } from '@testing-library/react';

import { InventoryTextPreview } from '@/app/admin/components/InventoryTextPreview';
import { maskedSource, matchPayload } from '@/app/admin/lib/inventory-text-match';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * INV-TEXT-02 · revisión editable y conciliación con variantes reales. Lo que se
 * corrige queda en la vista previa; al servidor sólo viaja modelo, capacidad,
 * color, tipo y línea para buscar la variante. Nada se escribe.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;
const reply = (status: number, body: unknown) =>
  ({ ok: status >= 200 && status < 300, status, json: async () => body }) as Response;

const EXACT = { status: 'exact', reason: '', options: [{ product_id: 7, name: 'iPhone 15 128 GB Negro', condition_line: 'new', options: ['128 GB', 'Negro'] }] };
const AMBIGUOUS = { status: 'ambiguous', reason: 'Varias variantes coinciden: elige una.', options: [
  { product_id: 8, name: 'iPhone 15 Plus 128 GB Negro', condition_line: 'new', options: [] },
  { product_id: 9, name: 'iPhone 15 Plus 128 GB Azul', condition_line: 'new', options: [] }] };

beforeEach(() => send.mockReset());

function analyze(text: string, canSeeCosts = true) {
  render(<InventoryTextPreview canSeeCosts={canSeeCosts} />);
  fireEvent.change(screen.getByLabelText('Línea declarada'), { target: { value: 'new' } });
  fireEvent.change(screen.getByLabelText('Texto para reconocer'), { target: { value: text } });
  fireEvent.click(screen.getByRole('button', { name: /Analizar/ }));
}

it('el pedido al servidor lleva sólo los campos permitidos', () => {
  const body = matchPayload([{ id: 1, kind: 'device', line: 'new', model: 'iPhone 15', capacityGb: 128, colour: 'negro' }]);
  expect(body).toEqual({ candidates: [{ key: '1', kind: 'device', line: 'new', model: 'iPhone 15', capacityGb: 128, colour: 'negro' }] });
});

it('el texto original se muestra con el IMEI enmascarado', () => {
  expect(maskedSource('IMEI 1: 490154203237518')).toBe('IMEI 1: ••••7518');
});

it('una corrección cambia lo que se busca, y la variante exacta queda elegida', async () => {
  send.mockResolvedValue(reply(200, { matches: { 1: EXACT } }));
  analyze('IPH 15 128 2300 negor');
  // «negor» is not a known colour, so it stayed in the name: the person corrects both.
  expect(screen.getByLabelText('Modelo del registro 1')).toHaveValue('iPhone 15 negor');
  fireEvent.change(screen.getByLabelText('Modelo del registro 1'), { target: { value: 'iPhone 15' } });
  fireEvent.change(screen.getByLabelText('Color del registro 1'), { target: { value: 'negro' } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Buscar en el catálogo' })); });
  const sent = JSON.parse(String(send.mock.calls[0][1]?.body));
  expect(sent.candidates[0]).toMatchObject({ model: 'iPhone 15', capacityGb: 128, colour: 'negro' });
  expect(JSON.stringify(sent)).not.toContain('2300');
  expect(screen.getByLabelText('Variante del registro 1')).toHaveValue('7');
});

it('si varias coinciden, ninguna se elige sola', async () => {
  send.mockResolvedValue(reply(200, { matches: { 1: AMBIGUOUS } }));
  analyze('IPH 15 plus 128 2600');
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Buscar en el catálogo' })); });
  expect(screen.getByLabelText('Variante del registro 1')).toHaveValue('');
  expect(screen.getByText('Varias variantes coinciden: elige una.')).toBeInTheDocument();
});

it('cada registro deja ver su texto original y se marca revisado; no hay botón para aplicar', () => {
  analyze('iPhone 13\nIMEI 1: 490154203237518\nCosto: 1650');
  fireEvent.click(screen.getByText('Ver texto original'));
  expect(screen.getByText(/IMEI 1: ••••7518/)).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText('Revisado registro 1'));
  expect(screen.getByRole('status')).toHaveTextContent('1 de 1 revisados');
  expect(screen.queryByRole('button', { name: /Aplicar|Guardar|Importar/ })).toBeNull();
});

it('sin permiso de costos no se puede ver ni corregir el costo', () => {
  analyze('IPH 15 128 2300 negro', false);
  expect(screen.queryByLabelText('Costo del registro 1')).toBeNull();
  expect(within(screen.getAllByRole('row')[1]).queryByText('2300')).toBeNull();
});

describe('correcciones de la revisión', () => {
  it('corregir un registro después de buscar borra su resultado anterior', async () => {
    send.mockResolvedValue(reply(200, { matches: { 1: EXACT } }));
    analyze('IPH 15 128 2300 negro');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Buscar en el catálogo' })); });
    expect(screen.getByLabelText('Variante del registro 1')).toHaveValue('7');
    fireEvent.change(screen.getByLabelText('Capacidad del registro 1'), { target: { value: '256' } });
    expect(screen.queryByLabelText('Variante del registro 1')).toBeNull();
  });

  it('una respuesta que llega después de volver a analizar se ignora', async () => {
    let answer: (value: Response) => void = () => {};
    send.mockReturnValue(new Promise<Response>(resolve => { answer = resolve; }));
    analyze('IPH 15 128 2300 negro');
    fireEvent.click(screen.getByRole('button', { name: 'Buscar en el catálogo' }));
    fireEvent.change(screen.getByLabelText('Texto para reconocer'), { target: { value: 'IPH 16 128 2600 negro' } });
    fireEvent.click(screen.getByRole('button', { name: /Analizar/ }));
    await act(async () => { answer(reply(200, { matches: { 1: EXACT } })); });
    expect(screen.queryByLabelText('Variante del registro 1')).toBeNull();
  });

  it('una capacidad fuera de rango no se envía y se avisa en su fila', async () => {
    send.mockResolvedValue(reply(200, { matches: {} }));
    analyze('IPH 15 128 2300 negro');
    fireEvent.change(screen.getByLabelText('Capacidad del registro 1'), { target: { value: '9999' } });
    expect(screen.getByText('Capacidad fuera de rango.')).toBeInTheDocument();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Buscar en el catálogo' })); });
    expect(JSON.parse(String(send.mock.calls[0][1]?.body)).candidates[0].capacityGb).toBeNull();
  });

  it('el texto original enmascara también series y números partidos', () => {
    expect(maskedSource('NS: F2LXK9ABCD')).toBe('NS: ••••CD');
    expect(maskedSource('IMEI 4901/5420/3237/518')).toBe('IMEI ••••7518');
    expect(maskedSource('IPH 15 128 2300 negro')).toBe('IPH 15 128 2300 negro');
  });
});

describe('revisión final', () => {
  it('una corrección hecha mientras se busca no recibe el resultado viejo, y no borra otras elecciones', async () => {
    let answer: (value: Response) => void = () => {};
    send.mockReturnValueOnce(new Promise<Response>(resolve => { answer = resolve; }));
    analyze('IPH 15 128 2300 negro\nIPH 15 plus 128 2600 negro');
    fireEvent.click(screen.getByRole('button', { name: 'Buscar en el catálogo' }));
    fireEvent.change(screen.getByLabelText('Capacidad del registro 1'), { target: { value: '256' } });
    await act(async () => { answer(reply(200, { matches: { 1: EXACT, 2: AMBIGUOUS } })); });
    expect(screen.queryByLabelText('Variante del registro 1')).toBeNull();
    expect(screen.getByLabelText('Variante del registro 2')).toHaveValue('');
  });

  it('el texto original oculta también una serie sin dígitos tras su etiqueta', () => {
    expect(maskedSource('NS: ABCDEFGHIJ')).toBe('NS: ••••IJ');
    expect(maskedSource('NS: AB12C')).toBe('NS: ••••');
  });

  it('corregir la capacidad quita su aviso y vuelve a calcular los duplicados', () => {
    analyze('IPH 15 2300 negro\nIPH 15 256 2300 negro');
    expect(screen.getAllByRole('row')[1]).toHaveTextContent('Capacidad no indicada');
    fireEvent.change(screen.getByLabelText('Capacidad del registro 1'), { target: { value: '256' } });
    expect(screen.getAllByRole('row')[1]).not.toHaveTextContent('Capacidad no indicada');
    expect(screen.getAllByRole('row')[2]).toHaveTextContent('Posible duplicado del registro 1');
  });
});
