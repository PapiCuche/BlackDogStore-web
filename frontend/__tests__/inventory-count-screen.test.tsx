/**
 * MIGRATION-D — la pantalla de recuento de accesorios.
 *
 * Se prueba contra el CONTRATO de `docs/adr-migration-count.md` (§3, §5, §6):
 * `counts-api` se simula. Lo que importa es qué se envía y qué se muestra:
 * el incremento suma uno y lleva el valor mostrado, el total reemplaza, un 409
 * nunca suma dos veces, blanco ≠ cero, la referencia antigua nunca se copia, y
 * desde aquí no se aprueba nada.
 */
import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';

import { CountWorkspace } from '@/app/admin/inventory/counts/_components/CountWorkspace';
import * as api from '@/app/admin/lib/counts-api';
import { fetchAdminProducts } from '@/app/lib/admin';
import type { CountDetail, CountLine } from '@/app/admin/lib/counts-api';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/admin', () => ({ fetchAdminProducts: jest.fn() }));
jest.mock('@/app/admin/lib/counts-api', () => ({
  ...jest.requireActual('@/app/admin/lib/counts-api'),
  fetchCountDetail: jest.fn(),
  scanCount: jest.fn(),
  setCountLines: jest.fn(),
  recountLines: jest.fn(),
  submitCountForReview: jest.fn(),
}));

const mocked = api as jest.Mocked<typeof api>;
const mockSearch = fetchAdminProducts as jest.Mock;
const { CountApiError } = jest.requireActual('@/app/admin/lib/counts-api') as typeof api;

function line(over: Partial<CountLine> = {}): CountLine {
  return {
    id: 101,
    product: 5,
    product_name: 'Cargador USB-C 20W',
    product_slug: 'cargador-usb-c-20w',
    theoretical_at_start: null,
    physical_quantity: null,
    theoretical_at_approval: null,
    difference: null,
    is_counted: false,
    note: '',
    updated_at: '2026-10-23T10:00:00Z',
    product_code: '7750001',
    product_codes: ['7750001'],
    reference_quantity: null,
    current_theoretical: 4,
    review_state: 'pending',
    ...over,
  };
}

function count(items: CountLine[], over: Partial<CountDetail> = {}): CountDetail {
  return {
    id: 9,
    company: 7,
    branch: 11,
    branch_name: 'Sucursal Centro',
    status: 'counting',
    status_label: 'En conteo',
    reason: '',
    items,
    counted_items: items.filter((i) => i.physical_quantity !== null).length,
    created_by: 1,
    created_by_username: 'ana',
    created_at: '2026-10-23T09:00:00Z',
    approved_at: null,
    cancelled_at: null,
    updated_at: '2026-10-23T09:00:00Z',
    kind: 'migration',
    ...over,
  };
}

function counted(qty: number, over: Partial<CountLine> = {}): CountLine {
  return line({
    physical_quantity: qty,
    is_counted: true,
    theoretical_at_start: 4,
    expected_difference: qty - 4,
    review_state: 'counted',
    ...over,
  });
}

function Harness({ initial, canSeeCosts = false }: { initial: CountDetail; canSeeCosts?: boolean }) {
  const [c, setC] = useState(initial);
  return <CountWorkspace count={c} onCountChange={setC} canCount canSeeCosts={canSeeCosts} />;
}

const scanField = () => screen.getByLabelText(/Código de barras o búsqueda|Buscar producto/);
const rowOf = (name: string) => screen.getByRole('row', { name: new RegExp(name) });

async function scan(code: string) {
  const field = scanField();
  await userEvent.clear(field);
  await userEvent.type(field, `${code}{Enter}`);
}

async function useIncrementMode() {
  await userEvent.click(screen.getByRole('radio', { name: /Sumar 1 por escaneo/ }));
}

beforeEach(() => {
  jest.clearAllMocks();
  mockSearch.mockResolvedValue({ count: 0, page: 1, page_size: 20, results: [] });
});

describe('modo incremental', () => {
  it('cada escaneo suma uno y envía el valor que la pantalla mostraba', async () => {
    mocked.scanCount
      .mockResolvedValueOnce({ result: 'incremented', line: counted(1) })
      .mockResolvedValueOnce({ result: 'incremented', line: counted(2) });
    render(<Harness initial={count([line()])} />);
    await useIncrementMode();

    await scan('7750001');
    expect(mocked.scanCount).toHaveBeenLastCalledWith(9, { code: '7750001', mode: 'increment', expected: null });
    expect(within(rowOf('Cargador USB-C 20W')).getByTestId('counted-cell')).toHaveTextContent('1');

    await scan('7750001');
    expect(mocked.scanCount).toHaveBeenLastCalledWith(9, { code: '7750001', mode: 'increment', expected: 1 });
    expect(within(rowOf('Cargador USB-C 20W')).getByTestId('counted-cell')).toHaveTextContent('2');
    expect(mocked.setCountLines).not.toHaveBeenCalled();
  });

  it('un 409 por valor distinto no suma dos veces: muestra el del servidor y no reintenta', async () => {
    mocked.scanCount.mockRejectedValueOnce(
      new CountApiError('El valor cambió.', 409, 'expected_mismatch', { line: counted(3) }),
    );
    render(<Harness initial={count([counted(2)])} />);
    await useIncrementMode();

    await scan('7750001');
    expect(mocked.scanCount).toHaveBeenCalledTimes(1);
    expect(mocked.scanCount).toHaveBeenCalledWith(9, { code: '7750001', mode: 'increment', expected: 2 });
    const cell = within(rowOf('Cargador USB-C 20W')).getByTestId('counted-cell');
    expect(cell).toHaveTextContent('3');
    expect(cell).not.toHaveTextContent('4');
    expect(screen.getByRole('alert')).toHaveTextContent(/no se sumó/i);

    mocked.scanCount.mockResolvedValueOnce({ result: 'incremented', line: counted(4) });
    await scan('7750001');
    expect(mocked.scanCount).toHaveBeenLastCalledWith(9, { code: '7750001', mode: 'increment', expected: 3 });
  });

  it('el modo incremental no se activa solo y se anuncia el cambio', async () => {
    render(<Harness initial={count([line()])} />);
    expect(screen.getByRole('radio', { name: /Cantidad total/ })).toBeChecked();
    expect(screen.getByRole('radio', { name: /Sumar 1 por escaneo/ })).not.toBeChecked();
    await useIncrementMode();
    expect(screen.getByRole('status')).toHaveTextContent(/cada escaneo suma 1/i);
  });
});

describe('cantidad total', () => {
  it('reemplaza lo contado y no lo suma', async () => {
    mocked.scanCount.mockResolvedValueOnce({ result: 'found', line: counted(5) });
    mocked.setCountLines.mockResolvedValueOnce(count([counted(3)]));
    render(<Harness initial={count([counted(5)])} />);

    await scan('7750001');
    expect(mocked.scanCount).toHaveBeenCalledWith(9, { code: '7750001', mode: 'lookup' });
    const qty = screen.getByLabelText('Cantidad contada');
    expect(qty).toHaveFocus();
    await userEvent.type(qty, '3{Enter}');

    expect(mocked.setCountLines).toHaveBeenCalledWith(9, [{ product: 5, physical_quantity: 3 }], true);
    expect(within(rowOf('Cargador USB-C 20W')).getByTestId('counted-cell')).toHaveTextContent('3');
    expect(scanField()).toHaveFocus();
  });
});

describe('blanco y cero', () => {
  it('blanco se lee «Sin contar» y un cero explícito «verificado: no hay»', () => {
    render(
      <Harness
        initial={count([
          line(),
          counted(0, { id: 102, product: 6, product_name: 'Funda MagSafe', product_code: '7750002', product_codes: ['7750002'] }),
        ])}
      />,
    );
    expect(within(rowOf('Cargador USB-C 20W')).getByTestId('counted-cell')).toHaveTextContent('Sin contar');
    const zero = within(rowOf('Funda MagSafe')).getByTestId('counted-cell');
    expect(zero).toHaveTextContent('0');
    expect(zero).toHaveTextContent(/verificado: no hay/i);
  });

  it('enviar el campo vacío no escribe nada; escribir 0 envía 0', async () => {
    mocked.setCountLines.mockResolvedValueOnce(count([counted(0)]));
    render(<Harness initial={count([line()])} />);
    await userEvent.click(within(screen.getByRole('region', { name: /Pendientes/ })).getByRole('button', { name: /Contar Cargador/ }));

    const qty = screen.getByLabelText('Cantidad contada');
    await userEvent.type(qty, '{Enter}');
    expect(mocked.setCountLines).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent(/escribe 0/i);

    await userEvent.type(qty, '0{Enter}');
    expect(mocked.setCountLines).toHaveBeenCalledWith(9, [{ product: 5, physical_quantity: 0 }], true);
  });
});

describe('referencia del sistema anterior', () => {
  it('se ve con su nombre y nunca se copia como cantidad contada', async () => {
    render(<Harness initial={count([line({ reference_quantity: 7 })])} />);
    expect(screen.getByRole('columnheader', { name: 'Referencia del sistema anterior' })).toBeInTheDocument();
    expect(within(rowOf('Cargador USB-C 20W')).getByTestId('reference-cell')).toHaveTextContent('7');
    expect(within(rowOf('Cargador USB-C 20W')).getByTestId('counted-cell')).toHaveTextContent('Sin contar');

    await userEvent.click(within(screen.getByRole('region', { name: /Pendientes/ })).getByRole('button', { name: /Contar Cargador/ }));
    expect(screen.getByLabelText('Cantidad contada')).toHaveValue(null);
    expect(screen.getByText(/Referencia del sistema anterior: 7/)).toBeInTheDocument();
  });

  it('en modo incremental el primer escaneo parte de «no contado», no de la referencia', async () => {
    mocked.scanCount.mockResolvedValueOnce({ result: 'incremented', line: counted(1, { reference_quantity: 7 }) });
    render(<Harness initial={count([line({ reference_quantity: 7 })])} />);
    await useIncrementMode();
    await scan('7750001');
    expect(mocked.scanCount).toHaveBeenCalledWith(9, { code: '7750001', mode: 'increment', expected: null });
  });

  it('sin referencia en ninguna línea no hay columna de referencia', () => {
    render(<Harness initial={count([line()])} />);
    expect(screen.queryByRole('columnheader', { name: 'Referencia del sistema anterior' })).not.toBeInTheDocument();
  });
});

describe('pendientes', () => {
  it('lista lo no contado y no excluido', () => {
    render(
      <Harness
        initial={count([
          line(),
          counted(2, { id: 102, product: 6, product_name: 'Funda MagSafe' }),
          line({ id: 103, product: 7, product_name: 'Cable Lightning', excluded: true, exclusion_reason: 'Descontinuado', review_state: 'excluded' }),
        ])}
      />,
    );
    const pending = screen.getByRole('region', { name: 'Pendientes (1)' });
    expect(within(pending).getByText('Cargador USB-C 20W')).toBeInTheDocument();
    expect(within(pending).queryByText('Funda MagSafe')).not.toBeInTheDocument();
    expect(within(pending).queryByText('Cable Lightning')).not.toBeInTheDocument();
  });
});

describe('líneas ambiguas', () => {
  it('se señalan y piden reconteo; el reconteo las devuelve a «no contado»', async () => {
    const ambiguous = counted(5, { review_state: 'ambiguous', review_detail: 'Hubo una venta mientras se contaba.' });
    mocked.recountLines.mockResolvedValueOnce(count([line()]));
    render(<Harness initial={count([ambiguous])} />);

    const prompt = screen.getByRole('region', { name: /necesitan reconteo/ });
    expect(prompt).toHaveTextContent('Hubo una venta mientras se contaba.');
    await userEvent.click(within(prompt).getByRole('button', { name: /Recontar Cargador/ }));
    expect(mocked.recountLines).toHaveBeenCalledWith(9, [5]);
    expect(within(rowOf('Cargador USB-C 20W')).getByTestId('counted-cell')).toHaveTextContent('Sin contar');
    expect(screen.getByRole('status')).toHaveTextContent(/vuelve a contar/i);
  });

  it('una línea ambigua no se corrige con un total simple', async () => {
    const ambiguous = counted(5, { review_state: 'ambiguous', review_detail: 'Hubo una venta.' });
    render(<Harness initial={count([ambiguous])} />);
    expect(within(rowOf('Cargador USB-C 20W')).queryByRole('button', { name: /^Corregir/ })).not.toBeInTheDocument();
  });
});

describe('equipos serializados', () => {
  it('un código de equipo se rechaza y remite al recuento de equipos', async () => {
    mocked.scanCount.mockRejectedValueOnce(
      new CountApiError('Es un equipo.', 400, 'serialized_product', { product: { id: 40, name: 'iPhone 15' } }),
    );
    render(<Harness initial={count([line()])} />);
    await scan('356789012345678');
    expect(screen.getByRole('alert')).toHaveTextContent(/iPhone 15/);
    expect(screen.getByRole('alert')).toHaveTextContent(/equipo por equipo/i);
    expect(screen.getByRole('link', { name: /Inventario › Equipos/ })).toHaveAttribute('href', '/admin/inventory/units');
    expect(mocked.setCountLines).not.toHaveBeenCalled();
  });

  it('en la búsqueda manual un serializado no se puede elegir', async () => {
    mocked.scanCount.mockRejectedValueOnce(new CountApiError('No existe.', 404, 'not_found'));
    mockSearch.mockResolvedValueOnce({
      count: 1, page: 1, page_size: 20,
      results: [{ id: 40, name: 'iPhone 15', is_serialized: true }],
    });
    render(<Harness initial={count([line()])} />);
    await scan('iphone');
    expect(await screen.findByText(/iPhone 15/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Elegir iPhone 15/ })).not.toBeInTheDocument();
    expect(screen.getByText(/se cuenta equipo por equipo/i)).toBeInTheDocument();
  });
});

describe('costos', () => {
  const withValue = () => count([counted(6, { difference_value: '12.50' })]);

  it('sin costs.view no hay columna ni valor, aunque el dato viaje', () => {
    render(<Harness initial={withValue()} />);
    expect(screen.queryByRole('columnheader', { name: /Valor/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/12\.50/)).not.toBeInTheDocument();
  });

  it('con costs.view se muestra', () => {
    render(<Harness initial={withValue()} canSeeCosts />);
    expect(screen.getByRole('columnheader', { name: /Valor de la diferencia/ })).toBeInTheDocument();
    expect(screen.getByText(/12\.50/)).toBeInTheDocument();
  });
});

describe('revisión', () => {
  it('enviar a revisión no es aprobar: queda «Pendiente de aprobación»', async () => {
    jest.spyOn(window, 'confirm').mockReturnValue(true);
    mocked.submitCountForReview.mockResolvedValueOnce(
      count([counted(5)], { status: 'review', status_label: 'En revisión' }),
    );
    render(<Harness initial={count([counted(5)])} />);
    await userEvent.click(screen.getByRole('button', { name: 'Enviar a revisión' }));
    expect(mocked.submitCountForReview).toHaveBeenCalledWith(9);
    expect(screen.getAllByText('Pendiente de aprobación').length).toBeGreaterThan(0);
    expect(scanField()).toBeDisabled();
  });
});

describe('accesibilidad y uso con lector', () => {
  it('campos con nombre, modos en un grupo, regiones vivas y foco que vuelve al escáner', async () => {
    mocked.scanCount.mockResolvedValueOnce({ result: 'incremented', line: counted(1) });
    render(<Harness initial={count([line()])} />);

    expect(scanField()).toHaveFocus();
    expect(screen.getByRole('radiogroup', { name: 'Modo de conteo' })).toBeInTheDocument();
    const status = screen.getByRole('status');
    expect(status).toHaveAttribute('aria-live', 'polite');
    expect(screen.getByTestId('count-alerts')).toHaveAttribute('aria-live', 'assertive');

    await useIncrementMode();
    await scan('7750001');
    expect(scanField()).toHaveFocus();
    expect(screen.getByRole('status')).toHaveTextContent(/Cargador USB-C 20W: 1/);
  });

  it('un código desconocido se anuncia como error y deja el foco en el escáner', async () => {
    mocked.scanCount.mockRejectedValueOnce(new CountApiError('Código no encontrado.', 404, 'not_found'));
    render(<Harness initial={count([line()])} />);
    await useIncrementMode();
    await scan('000');
    expect(screen.getByRole('alert')).toHaveTextContent(/no encontrado/i);
    expect(scanField()).toHaveFocus();
  });
});

describe('servidor de hoy (sin el contrato de C)', () => {
  it('busca por nombre, no llama a rutas nuevas y guarda el total sin campos nuevos', async () => {
    const legacy = count([], { kind: undefined });
    mockSearch.mockResolvedValueOnce({
      count: 1, page: 1, page_size: 20,
      results: [{ id: 5, name: 'Cargador USB-C 20W', is_serialized: false }],
    });
    mocked.setCountLines.mockResolvedValueOnce({ ...count([counted(2)]), kind: undefined });
    render(<Harness initial={legacy} />);

    expect(screen.getByRole('radio', { name: /Sumar 1 por escaneo/ })).toBeDisabled();
    await scan('cargador');
    expect(mocked.scanCount).not.toHaveBeenCalled();
    expect(mockSearch).toHaveBeenCalledWith({ search: 'cargador', page_size: 20 });
    await userEvent.click(await screen.findByRole('button', { name: /Elegir Cargador USB-C 20W/ }));
    await userEvent.type(screen.getByLabelText('Cantidad contada'), '2{Enter}');
    expect(mocked.setCountLines).toHaveBeenCalledWith(9, [{ product: 5, physical_quantity: 2 }], false);
    expect(mocked.recountLines).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Enviar a revisión' })).not.toBeInTheDocument();
  });
});

describe('sin aprobación desde esta pantalla', () => {
  it('ningún flujo de conteo llama a …/approve/', async () => {
    const { fetchWithAuth } = jest.requireMock('@/app/lib/auth') as { fetchWithAuth: jest.Mock };
    mocked.scanCount.mockResolvedValue({ result: 'incremented', line: counted(1) });
    render(<Harness initial={count([line()])} />);
    await useIncrementMode();
    await scan('7750001');
    await act(async () => {});
    expect(screen.queryByRole('button', { name: /Aprobar/ })).not.toBeInTheDocument();
    expect(fetchWithAuth.mock.calls.some(([url]) => String(url).includes('/approve/'))).toBe(false);
    expect(Object.keys(jest.requireActual('@/app/admin/lib/counts-api')).some((k) => /approv/i.test(k))).toBe(false);
  });
});
