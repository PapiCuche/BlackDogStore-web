/**
 * SERIAL-PICK-01 — decir QUÉ equipo, en la caja y al entregar un pedido.
 *
 * Un producto con serie no se vende como «uno de ellos»: alguien identifica el
 * equipo, con el lector o escribiéndolo a mano cuando el lector no lee. LO QUE
 * ESTAS PRUEBAS FIJAN del panel:
 *   · un código leído por el lector entra solo; uno escrito a mano se valida
 *     (IMEI: 15 dígitos y su dígito de control) y se CONFIRMA viendo qué equipo es;
 *   · el servidor recibe cómo se identificó cada equipo;
 *   · una línea con serie no se cobra hasta tener sus equipos.
 *
 * Qué equipo se puede vender y a quién lo decide el servidor (`test_serial_pick.py`).
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

import { UnitEntry } from '@/app/admin/components/UnitEntry';
import { OrderUnitsPanel } from '@/app/admin/components/OrderUnitsPanel';
import {
  entryMethod, imeiProblem, lineIsReady, saleItems, unitLabel, type PosLine,
  withProduct, withUnit, withoutUnit, withQuantity, pendingUnits, unitOfLine,
  newRhythm, rhythmKey, rhythmChanged, rhythmMethod,
} from '@/app/admin/lib/unit-entry';
import * as admin from '@/app/lib/admin';

jest.mock('@/app/lib/admin', () => ({
  ...jest.requireActual('@/app/lib/admin'),
  lookupOrderUnit: jest.fn(),
  identifyOrderUnit: jest.fn(),
  forgetOrderUnit: jest.fn(),
}));
const api = admin as jest.Mocked<typeof admin>;

const IMEI = '356938035643809';            // dígito de control correcto
const unit = (extra: Partial<admin.UnitDescription> = {}): admin.UnitDescription => ({
  unit_id: 7, product_id: 3, product_name: 'iPhone 16 Pro 256 GB Titanio', serial_number: 'F2LXK1NEW2',
  imei: IMEI, imei2: '', condition: 'new', condition_label: 'Nuevo', requires_imei: true, ...extra,
});

describe('cómo llegó el código', () => {
  it('teclas a ráfaga son un lector; teclas a ritmo de persona, a mano', () => {
    expect(entryMethod([8, 9, 7, 8, 10, 9, 8, 7, 9, 8, 9, 8, 7, 9])).toBe('scanner');
    expect(entryMethod([180, 220, 150, 300, 210, 190])).toBe('manual');
  });

  it('pegado, o sin teclas que medir, cuenta como a mano', () => {
    expect(entryMethod([])).toBe('manual');
    expect(entryMethod([9])).toBe('manual');
  });

  it('una pausa en medio de una ráfaga no la convierte en lector', () => {
    expect(entryMethod([8, 9, 400, 9, 8, 350, 9])).toBe('manual');
  });
});

describe('el ritmo es el del texto que se envía', () => {
  const scan = (text: string, gap = 8) => {
    const rhythm = newRhythm();
    let now = 1000;
    for (const char of text) { now += gap; rhythmKey(rhythm, char, now); }
    rhythmChanged(rhythm, text);
    return rhythm;
  };

  it('lo que el lector tecleó entero es del lector', () => {
    expect(rhythmMethod(scan(IMEI), IMEI)).toBe('scanner');
    expect(rhythmMethod(scan(IMEI, 200), IMEI)).toBe('manual');
  });

  it('un pegado después de una lectura no hereda su ritmo', () => {
    const rhythm = scan(IMEI);
    rhythmChanged(rhythm, '');                       // el campo se vació con el ratón
    rhythmChanged(rhythm, '490154203237518');        // y alguien pegó otro código
    expect(rhythmMethod(rhythm, '490154203237518')).toBe('manual');
  });

  it('ni siquiera pegando el mismo código', () => {
    const rhythm = scan(IMEI);
    rhythmChanged(rhythm, '');
    rhythmChanged(rhythm, IMEI);
    expect(rhythmMethod(rhythm, IMEI)).toBe('manual');
  });

  it('corregir a mano lo que leyó el lector lo vuelve escrito a mano', () => {
    const rhythm = scan(IMEI);
    rhythmChanged(rhythm, IMEI.slice(0, -1));        // retroceso
    rhythmKey(rhythm, '9', 5000);
    rhythmChanged(rhythm, IMEI);
    expect(rhythmMethod(rhythm, IMEI)).toBe('manual');
  });

  it('sólo cuentan las teclas que escriben', () => {
    const rhythm = newRhythm();
    let now = 1000;
    for (const char of 'F2LXK1NEW2') { now += 8; rhythmKey(rhythm, 'Shift', now); now += 8; rhythmKey(rhythm, char, now); }
    rhythmChanged(rhythm, 'F2LXK1NEW2');
    expect(rhythmMethod(rhythm, 'F2LXK1NEW2')).toBe('scanner');
  });
});

describe('un IMEI escrito a mano', () => {
  it('son 15 dígitos con su dígito de control', () => {
    expect(imeiProblem(IMEI)).toBeNull();
    expect(imeiProblem('35 693803 564380 9')).toBeNull();
    expect(imeiProblem('35-693803-564380-9')).toBeNull();
  });

  it('dice qué le falta', () => {
    expect(imeiProblem('35693803564380')).toMatch(/15 dígitos/);
    expect(imeiProblem('3569380356438090')).toMatch(/15 dígitos/);
    expect(imeiProblem('35693803564380A')).toMatch(/15 dígitos/);
    expect(imeiProblem('356938035643800')).toMatch(/no es válido/i);          // dígito de control
    expect(imeiProblem('000000000000000')).toMatch(/no es válido/i);
  });
});

describe('la línea de la caja', () => {
  const line = (extra: Partial<PosLine> = {}): PosLine => ({
    product: 3, name: 'iPhone 16', price: '4500.00', quantity: 1, available: 2,
    isSerialized: true, requiresImei: true, units: [], ...extra,
  });
  const picked = { id: 7, method: 'manual' as const, serial_number: 'F2LXK1NEW2', imei: IMEI };

  it('con serie no está lista hasta tener sus equipos', () => {
    expect(lineIsReady(line())).toBe(false);
    expect(lineIsReady(line({ units: [picked] }))).toBe(true);
    expect(lineIsReady(line({ quantity: 2, units: [picked] }))).toBe(false);
  });

  it('sin serie está lista siempre', () => {
    expect(lineIsReady(line({ isSerialized: false, quantity: 5 }))).toBe(true);
  });

  it('la venta lleva cada equipo con cómo se identificó, y nada más de él', () => {
    expect(saleItems([line({ units: [picked] }), line({ product: 9, isSerialized: false, quantity: 2 })])).toEqual([
      { product: 3, quantity: 1, units: [{ id: 7, method: 'manual' }] },
      { product: 9, quantity: 2 },
    ]);
  });

  it('un equipo se nombra por su IMEI, o por su serie si no tiene', () => {
    expect(unitLabel(picked)).toBe(`IMEI ${IMEI} · Serie F2LXK1NEW2`);
    expect(unitLabel({ ...picked, imei: '' })).toBe('Serie F2LXK1NEW2');
  });
});

describe('la cesta de la caja', () => {
  const phone = { id: 3, name: 'iPhone 16', price: '4500.00', available: 2, barcode: '', is_serialized: true, requires_imei: true };
  const cable = { id: 9, name: 'Cable USB-C', price: '60.00', available: 10, barcode: '775', is_serialized: false, requires_imei: false };
  const device = (id: number, imei: string) => ({
    unit_id: id, product_id: 3, product_name: 'iPhone 16', serial_number: `S${id}`, imei, imei2: '',
    condition: 'new', condition_label: 'Nuevo', requires_imei: true,
  });

  it('un producto sin serie suma cantidad al repetirse', () => {
    const lines = withProduct(withProduct([], cable), cable);
    expect(lines).toHaveLength(1);
    expect(lines[0]).toMatchObject({ quantity: 2, isSerialized: false, units: [] });
  });

  it('un producto con serie entra pidiendo un equipo, y repetirlo no pide otro', () => {
    const lines = withProduct(withProduct([], phone), phone);
    expect(lines).toHaveLength(1);
    expect(lines[0]).toMatchObject({ quantity: 1, isSerialized: true, units: [] });
    expect(pendingUnits(lines)).toBe(1);
  });

  it('un equipo ocupa el hueco pendiente de su línea', () => {
    const lines = withUnit(withProduct([], phone), device(7, IMEI), phone, 'scanner');
    expect(lines[0].quantity).toBe(1);
    expect(lines[0].units).toEqual([{ id: 7, method: 'scanner', serial_number: 'S7', imei: IMEI }]);
    expect(pendingUnits(lines)).toBe(0);
  });

  it('un equipo escaneado sin línea crea la suya, y el segundo la amplía', () => {
    let lines = withUnit([], device(7, IMEI), phone, 'scanner');
    lines = withUnit(lines, device(8, '490154203237518'), phone, 'manual');
    expect(lines).toHaveLength(1);
    expect(lines[0].quantity).toBe(2);
    expect(lines[0].units.map((u) => [u.id, u.method])).toEqual([[7, 'scanner'], [8, 'manual']]);
  });

  it('el mismo equipo dos veces se rechaza con su motivo', () => {
    const lines = withUnit([], device(7, IMEI), phone, 'scanner');
    expect(() => withUnit(lines, device(7, IMEI), phone, 'scanner')).toThrow(/ya está en esta venta/);
  });

  it('quitar un equipo deja su hueco pendiente, no borra la línea', () => {
    const lines = withoutUnit(withUnit([], device(7, IMEI), phone, 'scanner'), 3, 7);
    expect(lines[0]).toMatchObject({ quantity: 1, units: [] });
    expect(pendingUnits(lines)).toBe(1);
  });

  it('un equipo de otro modelo no llena la línea de este', () => {
    const [line] = withProduct([], phone);
    expect(unitOfLine(device(7, IMEI), line)).toMatchObject({ unit_id: 7 });
    expect(() => unitOfLine({ ...device(8, IMEI), product_id: 99, product_name: 'iPhone 15' }, line))
      .toThrow('Ese equipo es un iPhone 15, no un iPhone 16.');
  });

  it('bajar la cantidad no deja más equipos que unidades', () => {
    let lines = withUnit([], device(7, IMEI), phone, 'scanner');
    lines = withUnit(lines, device(8, '490154203237518'), phone, 'scanner');
    lines = withQuantity(lines, 3, 1);
    expect(lines[0].quantity).toBe(1);
    expect(lines[0].units.map((u) => u.id)).toEqual([7]);
    expect(withQuantity(lines, 3, 0)).toEqual([]);
  });
});

describe('escribir o escanear un equipo', () => {
  const type = (value: string, gapMs: number) => {
    const field = screen.getByLabelText('IMEI o serie del equipo');
    let now = 1000;
    const spy = jest.spyOn(performance, 'now');
    for (const char of value) {
      now += gapMs;
      spy.mockReturnValue(now);
      fireEvent.keyDown(field, { key: char });
    }
    fireEvent.change(field, { target: { value } });
    spy.mockReturnValue(now + gapMs);
    fireEvent.keyDown(field, { key: 'Enter' });
    spy.mockRestore();
  };

  it('lo que lee el lector entra solo, sin confirmar', async () => {
    const lookup = jest.fn().mockResolvedValue(unit());
    const onPicked = jest.fn();
    render(<UnitEntry lookup={lookup} onPicked={onPicked} />);

    type(IMEI, 8);

    await waitFor(() => expect(onPicked).toHaveBeenCalledWith(unit(), 'scanner'));
    expect(lookup).toHaveBeenCalledWith(IMEI, 'scanner');
    expect(screen.queryByRole('button', { name: /Confirmar/ })).toBeNull();
  });

  it('lo escrito a mano se muestra para confirmar, con modelo, estado, IMEI y serie', async () => {
    const lookup = jest.fn().mockResolvedValue(unit());
    const onPicked = jest.fn();
    render(<UnitEntry lookup={lookup} onPicked={onPicked} />);

    type(IMEI, 200);

    const card = await screen.findByRole('group', { name: 'Confirma el equipo' });
    expect(lookup).toHaveBeenCalledWith(IMEI, 'manual');
    expect(card).toHaveTextContent('iPhone 16 Pro 256 GB Titanio');
    expect(card).toHaveTextContent('Nuevo');
    expect(card).toHaveTextContent(IMEI);
    expect(card).toHaveTextContent('F2LXK1NEW2');
    expect(onPicked).not.toHaveBeenCalled();                                 // todavía nadie dijo que sí

    fireEvent.click(screen.getByRole('button', { name: 'Confirmar este equipo' }));
    expect(onPicked).toHaveBeenCalledWith(unit(), 'manual');
  });

  it('a mano se puede decir que no es ese', async () => {
    const onPicked = jest.fn();
    render(<UnitEntry lookup={jest.fn().mockResolvedValue(unit())} onPicked={onPicked} />);
    type(IMEI, 200);
    await screen.findByRole('group', { name: 'Confirma el equipo' });

    fireEvent.click(screen.getByRole('button', { name: 'No es este' }));

    expect(onPicked).not.toHaveBeenCalled();
    expect(screen.queryByRole('group', { name: 'Confirma el equipo' })).toBeNull();
  });

  it('un IMEI mal escrito no llega al servidor', async () => {
    const lookup = jest.fn();
    render(<UnitEntry requiresImei lookup={lookup} onPicked={jest.fn()} />);

    type('356938035643800', 200);

    expect(await screen.findByRole('alert')).toHaveTextContent(/no es válido/i);
    expect(lookup).not.toHaveBeenCalled();
  });

  it('a mano, un equipo con IMEI no se escribe por su serie', async () => {
    const lookup = jest.fn();
    render(<UnitEntry requiresImei lookup={lookup} onPicked={jest.fn()} />);
    type('F2LXK1NEW2', 200);
    expect(await screen.findByRole('alert')).toHaveTextContent(/15 dígitos/);
    expect(lookup).not.toHaveBeenCalled();
  });

  it('un producto sin IMEI admite una serie de sólo números, y decide el servidor', async () => {
    const laptop = unit({ imei: '', serial_number: '123456789012345', requires_imei: false });
    const lookup = jest.fn().mockResolvedValue(laptop);
    render(<UnitEntry requiresImei={false} lookup={lookup} onPicked={jest.fn()} />);
    type('123456789012345', 200);
    await screen.findByRole('group', { name: 'Confirma el equipo' });
    expect(lookup).toHaveBeenCalledWith('123456789012345', 'manual');
  });

  it('pegar después de una lectura pide confirmar', async () => {
    const lookup = jest.fn().mockResolvedValue(unit());
    const onPicked = jest.fn();
    render(<UnitEntry lookup={lookup} onPicked={onPicked} />);
    type(IMEI, 8);
    await waitFor(() => expect(onPicked).toHaveBeenCalledTimes(1));

    const field = screen.getByLabelText('IMEI o serie del equipo');
    fireEvent.change(field, { target: { value: '490154203237518' } });      // pegado: ninguna tecla
    fireEvent.keyDown(field, { key: 'Enter' });

    await screen.findByRole('group', { name: 'Confirma el equipo' });
    expect(lookup).toHaveBeenLastCalledWith('490154203237518', 'manual');
    expect(onPicked).toHaveBeenCalledTimes(1);
  });

  it('lo que el servidor rechaza se dice con sus palabras', async () => {
    const lookup = jest.fn().mockRejectedValue(new Error('Ese equipo está vendido: no se puede vender.'));
    render(<UnitEntry lookup={lookup} onPicked={jest.fn()} />);
    type(IMEI, 8);
    expect(await screen.findByRole('alert')).toHaveTextContent('Ese equipo está vendido');
  });

  it('hay un botón para decir que se escribe a mano aunque se teclee deprisa', async () => {
    const lookup = jest.fn().mockResolvedValue(unit());
    render(<UnitEntry lookup={lookup} onPicked={jest.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'El lector no lee: escribirlo a mano' }));
    type(IMEI, 8);
    await screen.findByRole('group', { name: 'Confirma el equipo' });
    expect(lookup).toHaveBeenCalledWith(IMEI, 'manual');
  });
});

describe('los equipos de un pedido web', () => {
  const order = (extra: Partial<admin.AdminOrderDetail> = {}) => ({
    id: 42, units_pending: 1, units_can_change: true,
    items: [
      { id: 1, product_id: 3, product_name: 'iPhone 16', quantity: 1, price: '4500.00', subtotal: '4500.00',
        is_serialized: true, units: [], units_pending: 1 },
      { id: 2, product_id: 9, product_name: 'Cable', quantity: 2, price: '49.00', subtotal: '98.00',
        is_serialized: false, units: [], units_pending: 0 },
    ],
    ...extra,
  }) as unknown as admin.AdminOrderDetail;

  const identified = { id: 5, serial_number: 'F2LXK1NEW2', imei: IMEI, imei2: '', method: 'manual', method_label: 'Ingreso manual',
    identified_at: '2026-10-11T10:00:00Z', identified_by: 'pedidos' };

  beforeEach(() => {
    api.lookupOrderUnit.mockReset();
    api.identifyOrderUnit.mockReset();
    api.forgetOrderUnit.mockReset();
  });

  it('dice qué falta identificar, y no aparece si el pedido no lleva equipos', () => {
    const { rerender } = render(<OrderUnitsPanel order={order()} onChanged={jest.fn()} />);
    expect(screen.getByText(/Falta identificar 1 equipo/)).toBeInTheDocument();
    expect(screen.getByText('iPhone 16')).toBeInTheDocument();
    expect(screen.queryByText('Cable')).toBeNull();

    const plain = order({ units_pending: 0 });
    plain.items = [plain.items[1]];
    rerender(<OrderUnitsPanel order={plain} onChanged={jest.fn()} />);
    expect(screen.queryByText(/equipo/i)).toBeNull();
  });

  it('identifica el equipo que se entrega y muestra el pedido como quedó', async () => {
    const after = order({ units_pending: 0 });
    after.items[0] = { ...after.items[0], units: [identified], units_pending: 0 } as admin.AdminOrderItem;
    api.lookupOrderUnit.mockResolvedValue(unit());
    api.identifyOrderUnit.mockResolvedValue(after);
    const onChanged = jest.fn();
    render(<OrderUnitsPanel order={order()} onChanged={onChanged} />);

    fireEvent.click(screen.getByRole('button', { name: 'El lector no lee: escribirlo a mano' }));
    fireEvent.change(screen.getByLabelText('IMEI o serie del equipo'), { target: { value: IMEI } });
    fireEvent.keyDown(screen.getByLabelText('IMEI o serie del equipo'), { key: 'Enter' });
    await screen.findByRole('group', { name: 'Confirma el equipo' });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Confirmar este equipo' })); });

    expect(api.lookupOrderUnit).toHaveBeenCalledWith(42, IMEI, 'manual');
    expect(api.identifyOrderUnit).toHaveBeenCalledWith(42, IMEI, 'manual');
    expect(onChanged).toHaveBeenCalledWith(after);
  });

  it('muestra los identificados con cómo y quién, y deja quitar uno mientras no salió', async () => {
    const done = order({ units_pending: 0 });
    done.items[0] = { ...done.items[0], units: [identified], units_pending: 0 } as admin.AdminOrderItem;
    api.forgetOrderUnit.mockResolvedValue(order());
    const onChanged = jest.fn();
    render(<OrderUnitsPanel order={done} onChanged={onChanged} />);

    expect(screen.getByText(new RegExp(IMEI))).toBeInTheDocument();
    expect(screen.getByText(/Ingreso manual/)).toBeInTheDocument();
    expect(screen.getByText(/pedidos/)).toBeInTheDocument();
    expect(screen.queryByLabelText('IMEI o serie del equipo')).toBeNull();     // nada pendiente: nada que pedir

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Quitar el equipo/ })); });
    expect(api.forgetOrderUnit).toHaveBeenCalledWith(42, 5);
    expect(onChanged).toHaveBeenCalled();
  });

  it('lo que ya salió de la tienda no se cambia', () => {
    const left = order({ units_pending: 0, units_can_change: false });
    left.items[0] = { ...left.items[0], units: [identified], units_pending: 0 } as admin.AdminOrderItem;
    render(<OrderUnitsPanel order={left} onChanged={jest.fn()} />);
    expect(screen.queryByRole('button', { name: /Quitar el equipo/ })).toBeNull();
  });

  it('lo que ya salió no se quita aunque el pedido vuelva a «en preparación»', () => {
    const back = order({ units_pending: 0, units_can_change: true });
    back.items[0] = { ...back.items[0], units: [{ ...identified, can_remove: false }], units_pending: 0 } as admin.AdminOrderItem;
    render(<OrderUnitsPanel order={back} onChanged={jest.fn()} />);
    expect(screen.getByText(new RegExp(IMEI))).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Quitar el equipo/ })).toBeNull();
  });

  it('quien sólo consulta ve lo que falta, sin dónde escribirlo', () => {
    render(<OrderUnitsPanel order={order({ units_can_change: false })} onChanged={jest.fn()} />);
    expect(screen.getByText(/Falta identificar 1 equipo/)).toBeInTheDocument();
    expect(screen.queryByLabelText('IMEI o serie del equipo')).toBeNull();
  });
});
