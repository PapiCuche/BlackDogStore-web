/**
 * INV-INTAKE-BULK-01 — el paso «Artículos» de «Recibir mercadería» con muchos equipos: «Pegar varios» en una línea
 * por equipo, «Aplicar a todos», +1 al volver a escanear un accesorio; ningún prefijo de serie se propone quitar (D-SCAN-1).
 * Sin red: el buscador de productos se simula.
 */
import React, { useState } from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ReceivingLinesEditor } from '@/app/admin/components/ReceivingLinesEditor';
import { blankUnit, newLine, type DraftLine, type ReceiptProduct } from '@/app/admin/lib/inventory-receipts';
import type { CosmeticGradeRow } from '@/app/admin/lib/internal-api';
import * as admin from '@/app/lib/admin';

jest.mock('next/navigation', () => ({ usePathname: () => '/admin/inventory/receive', useRouter: () => ({ push: jest.fn() }) }));
jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/admin', () => ({ ...jest.requireActual('@/app/lib/admin'), fetchAdminProducts: jest.fn() }));

const search = admin.fetchAdminProducts as jest.Mock;

const IMEI_A = '356789012345672';
const IMEI_B = '490154203237518';
const IMEI_C = '352099001761481';

const USED_PHONE: ReceiptProduct = { id: 5, name: 'iPhone 13 128GB Seminuevo', is_serialized: true, requires_imei: true, condition_line: 'used' };
const NEW_PHONE: ReceiptProduct = { id: 6, name: 'iPhone 16 128GB', is_serialized: true, requires_imei: true, condition_line: 'new' };
const GRADES: CosmeticGradeRow[] = [
  { id: 2, code: 'A', label: 'Como nuevo', description: '', sort_order: 1, is_active: true },
  { id: 3, code: 'B', label: 'Muy bueno', description: '', sort_order: 2, is_active: true },
];

let latest: DraftLine[] = [];
function Harness({ initial, canCosts = true, canManageProducts = true }: { initial: DraftLine[]; canCosts?: boolean; canManageProducts?: boolean }) {
  const [lines, setLines] = useState(initial);
  return (
    <ReceivingLinesEditor
      lines={lines} onLines={(next) => { latest = next; setLines(next); }} canCosts={canCosts} canManageProducts={canManageProducts}
      problems={null} focus={null} onCreateArticle={() => {}} grades={GRADES}
    />
  );
}

async function renderEditor(initial: DraftLine[], opts: { canCosts?: boolean; canManageProducts?: boolean } = {}) {
  const u = userEvent.setup();
  latest = initial;
  await act(async () => { render(<Harness initial={initial} {...opts} />); });
  return u;
}

const cardOf = (name: string) => screen.getByRole('article', { name });
const serialBoxes = (card: HTMLElement) => within(card).getAllByLabelText(/Número de serie/) as HTMLInputElement[];

beforeEach(() => { search.mockReset(); latest = []; });

describe('«Pegar varios» en una línea por equipo', () => {
  it('muestra la vista previa con conteos y enmascarado, y sólo agrega al confirmar', async () => {
    const u = await renderEditor([newLine(USED_PHONE)]);
    const card = cardOf(USED_PHONE.name);
    await u.click(within(card).getByRole('button', { name: 'Pegar varios' }));
    const box = within(card).getByLabelText(/Series o IMEI, uno por línea/);
    fireEvent.change(box, { target: { value: `F2LXK1ABCD12 ${IMEI_A}\n${IMEI_B}\n${IMEI_B}\n356789012345679\nG7QXK1ABCD99` } });
    await u.click(within(card).getByRole('button', { name: 'Revisar lo pegado' }));

    const preview = within(card).getByRole('region', { name: 'Vista previa de lo pegado' });
    expect(within(preview).getByText(/Se agregarán 3 equipos: 2 con IMEI y 2 con serie/)).toBeInTheDocument();
    // SERIAL-OPTIONAL-01: el artículo lleva IMEI, así que el equipo sin serie entra por su IMEI.
    expect(within(preview).getByText(/1 sin número de serie: entra por su IMEI/)).toBeInTheDocument();
    expect(within(preview).queryByText(/la serie es obligatoria/)).not.toBeInTheDocument();
    expect(within(preview).getByText(/Repetidos en lo pegado/)).toBeInTheDocument();
    expect(within(preview).getByText(/No se reconocen/)).toBeInTheDocument();
    // Masked like the review: never the whole IMEI in the preview.
    expect(preview.textContent).toContain('••••7518');
    expect(preview.textContent).toContain('••••5679');
    expect(preview.textContent).not.toContain(IMEI_B);
    expect(preview.textContent).not.toContain('356789012345679');
    // Nothing added yet.
    expect(latest[0].units).toHaveLength(1);

    await u.click(within(preview).getByRole('button', { name: 'Agregar 3 equipos' }));
    const serials = serialBoxes(cardOf(USED_PHONE.name)).map((b) => b.value);
    expect(serials).toEqual(['F2LXK1ABCD12', '', 'G7QXK1ABCD99']); // the untouched blank row is gone
    expect(latest[0].units.map((x) => x.imei)).toEqual([IMEI_A, IMEI_B, '']);
    expect(latest[0].units.every((x) => x.condition === 'used')).toBe(true);
    expect(screen.getByText('Se agregaron 3 equipos.')).toBeInTheDocument();
    expect(within(cardOf(USED_PHONE.name)).getByLabelText(/Series o IMEI, uno por línea/)).not.toBeVisible();
  });

  it('lo que ya está en la línea no se agrega otra vez', async () => {
    const line = newLine(NEW_PHONE);
    line.units = [{ ...blankUnit('new'), serial_number: 'F2LXK1ABCD12', imei: IMEI_A }];
    const u = await renderEditor([line]);
    const card = cardOf(NEW_PHONE.name);
    await u.click(within(card).getByRole('button', { name: 'Pegar varios' }));
    fireEvent.change(within(card).getByLabelText(/Series o IMEI/), { target: { value: `${IMEI_A}\n${IMEI_C}` } });
    await u.click(within(card).getByRole('button', { name: 'Revisar lo pegado' }));
    const preview = within(card).getByRole('region', { name: 'Vista previa de lo pegado' });
    expect(within(preview).getByText(/Ya están en esta línea/)).toBeInTheDocument();
    expect(preview.textContent).toContain('••••5672');
    expect(within(preview).getByText(/Se agregará 1 equipo/)).toBeInTheDocument();
  });

  it('con más de 500 códigos no ofrece agregar nada', async () => {
    const u = await renderEditor([newLine(NEW_PHONE)]);
    const card = cardOf(NEW_PHONE.name);
    await u.click(within(card).getByRole('button', { name: 'Pegar varios' }));
    const text = Array.from({ length: 501 }, (_v, i) => `SER${String(i).padStart(5, '0')}`).join('\n');
    fireEvent.change(within(card).getByLabelText(/Series o IMEI/), { target: { value: text } });
    await u.click(within(card).getByRole('button', { name: 'Revisar lo pegado' }));
    expect(within(card).getByRole('alert')).toHaveTextContent(/501 códigos.*máximo es 500/);
    expect(within(card).queryByRole('button', { name: /^Agregar \d+ equipos?$/ })).toBeNull();
  });

  it('D-SCAN-1: una serie de 13 con «S» se agrega tal cual; no se ofrece quitar nada (sin fuente verificada)', async () => {
    const u = await renderEditor([newLine(NEW_PHONE)]);
    const card = cardOf(NEW_PHONE.name);
    await u.click(within(card).getByRole('button', { name: 'Pegar varios' }));
    fireEvent.change(within(card).getByLabelText(/Series o IMEI/), { target: { value: 'SF2LXK1ABCD12' } });
    await u.click(within(card).getByRole('button', { name: 'Revisar lo pegado' }));
    expect(within(card).queryByRole('checkbox', { name: /Quitar la «S»/ })).toBeNull();
    expect(card).not.toHaveTextContent(/caja de Apple/);
    await u.click(within(card).getByRole('button', { name: 'Agregar 1 equipo' }));
    expect(latest[0].units.map((x) => x.serial_number)).toEqual(['SF2LXK1ABCD12']);
  });
});

describe('«Aplicar a todos»', () => {
  function usedLineWith(units: Partial<ReturnType<typeof blankUnit>>[]): DraftLine {
    const line = newLine(USED_PHONE);
    line.units = units.map((over) => ({ ...blankUnit('used'), ...over }));
    return line;
  }

  it('sin costs.view no ofrece el costo; sin products.manage, tampoco el precio', async () => {
    const u = await renderEditor([usedLineWith([{ serial_number: 'AAAA1111' }])], { canCosts: false, canManageProducts: false });
    const card = cardOf(USED_PHONE.name);
    await u.click(within(card).getByRole('button', { name: 'Aplicar a todos' }));
    const panel = within(card).getByRole('group', { name: 'Aplicar a todos los equipos de esta línea' });
    expect(within(panel).queryByLabelText(/Costo/)).toBeNull();
    expect(within(panel).queryByLabelText(/Precio/)).toBeNull();
    expect(within(panel).getByLabelText(/Batería/)).toBeInTheDocument();
    expect(within(panel).getByLabelText(/Estado estético/)).toBeInTheDocument();
  });

  it('una línea nueva sin costs.view no tiene nada que aplicar: no hay botón', async () => {
    await renderEditor([newLine(NEW_PHONE)], { canCosts: false });
    expect(within(cardOf(NEW_PHONE.name)).queryByRole('button', { name: 'Aplicar a todos' })).toBeNull();
  });

  it('una línea nueva con costs.view sólo aplica el costo', async () => {
    const u = await renderEditor([newLine(NEW_PHONE)]);
    const card = cardOf(NEW_PHONE.name);
    await u.click(within(card).getByRole('button', { name: 'Aplicar a todos' }));
    const panel = within(card).getByRole('group', { name: 'Aplicar a todos los equipos de esta línea' });
    expect(within(panel).getByLabelText(/Costo/)).toBeInTheDocument();
    expect(within(panel).queryByLabelText(/Batería/)).toBeNull();
    expect(within(panel).queryByLabelText(/Precio/)).toBeNull();
  });

  it('por defecto sólo completa lo vacío; «Reemplazar» escribe en todos', async () => {
    const u = await renderEditor([usedLineWith([{ serial_number: 'AAAA1111', cost: '900' }, { serial_number: 'BBBB2222' }, {}])]);
    const card = cardOf(USED_PHONE.name);
    await u.click(within(card).getByRole('button', { name: 'Aplicar a todos' }));
    const panel = within(card).getByRole('group', { name: 'Aplicar a todos los equipos de esta línea' });
    await u.type(within(panel).getByLabelText(/Costo/), '1000');
    await u.type(within(panel).getByLabelText(/Precio/), '1899');
    await u.selectOptions(within(panel).getByLabelText(/Estado estético/), '3');
    await u.type(within(panel).getByLabelText(/Batería/), '88');
    const overwrite = within(panel).getByRole('checkbox', { name: /Reemplazar también lo que ya está escrito/ });
    expect(overwrite).not.toBeChecked();
    await u.click(within(panel).getByRole('button', { name: 'Aplicar a los 2 equipos' }));
    expect(latest[0].units.map((x) => x.cost)).toEqual(['900', '1000', '']);
    expect(latest[0].units.map((x) => x.price)).toEqual(['1899', '1899', '']);
    expect(latest[0].units.map((x) => x.grade)).toEqual(['3', '3', '']);
    expect(latest[0].units.map((x) => x.battery)).toEqual(['88', '88', '']);
    expect(within(panel).getByRole('status')).toHaveTextContent(/2 equipos.*1 valor ya escrito se mantuvo/);

    await u.click(overwrite);
    await u.click(within(panel).getByRole('button', { name: 'Aplicar a los 2 equipos' }));
    expect(latest[0].units.map((x) => x.cost)).toEqual(['1000', '1000', '']);
  });

  it('un valor mal escrito no se aplica y se dice', async () => {
    const u = await renderEditor([usedLineWith([{ serial_number: 'AAAA1111' }])]);
    const card = cardOf(USED_PHONE.name);
    await u.click(within(card).getByRole('button', { name: 'Aplicar a todos' }));
    const panel = within(card).getByRole('group', { name: 'Aplicar a todos los equipos de esta línea' });
    await u.type(within(panel).getByLabelText(/Costo/), '2,500');
    await u.click(within(panel).getByRole('button', { name: 'Aplicar a 1 equipo' }));
    expect(within(panel).getByRole('alert')).toHaveTextContent(/2 decimales/);
    expect(latest[0].units[0].cost).toBe('');
  });
});

describe('D-SCAN-1 · la serie de una fila', () => {
  it('una serie de 13 con «S» queda como se escribió: ningún prefijo de una marca se propone quitar', async () => {
    const u = await renderEditor([newLine(NEW_PHONE)]);
    const card = cardOf(NEW_PHONE.name);
    const serial = serialBoxes(card)[0];
    await u.type(serial, 'SF2LXK1ABCD12');
    expect(serial.value).toBe('SF2LXK1ABCD12');
    expect(within(card).queryByRole('button', { name: /Quitar la S/ })).toBeNull();
    expect(card).not.toHaveTextContent(/caja de Apple/);
  });
});

describe('volver a escanear un accesorio', () => {
  const CABLE = {
    id: 9, name: 'Cable USB-C 1m', slug: 'p-9', price: '10.00', inventory: 0, primary_code: '7501234567890',
    is_serialized: false, requires_imei: false, condition_line: 'new', search_exact: true,
  };
  const page = (rows: object[]) => ({ count: rows.length, page: 1, page_size: 8, results: rows });
  const picker = () => screen.getByRole('combobox', { name: /Buscar el artículo/ });

  it('el mismo código otra vez suma +1 a su línea y lo anuncia', async () => {
    const u = await renderEditor([]);
    search.mockResolvedValueOnce(page([CABLE]));
    await u.type(picker(), '7501234567890{Enter}');
    await screen.findByRole('article', { name: CABLE.name });
    expect(screen.getByLabelText('Cantidad')).toHaveValue('1');

    search.mockResolvedValueOnce(page([CABLE]));
    await u.type(picker(), '7501234567890{Enter}');
    expect(await screen.findByText(/«Cable USB-C 1m» ya está en la lista: \+1, ahora 2 unidades\./)).toHaveAttribute('role', 'status');
    expect(screen.getByLabelText('Cantidad')).toHaveValue('2');
    expect(screen.getAllByRole('article')).toHaveLength(1);
    // The scanner keeps reading: the cursor stays in the search.
    expect(picker()).toHaveFocus();

    search.mockResolvedValueOnce(page([CABLE]));
    await u.type(picker(), '7501234567890{Enter}');
    expect(await screen.findByText(/ahora 3 unidades/)).toBeInTheDocument();
    expect(screen.getByLabelText('Cantidad')).toHaveValue('3');
  });

  it('elegido por nombre (no por su código exacto) no suma: sólo lleva a su línea', async () => {
    const u = await renderEditor([]);
    search.mockResolvedValueOnce(page([CABLE]));
    await u.type(picker(), '7501234567890{Enter}');
    await screen.findByRole('article', { name: CABLE.name });
    search.mockResolvedValueOnce(page([{ ...CABLE, search_exact: false }]));
    await u.type(picker(), 'Cabl{Enter}');
    expect(await screen.findByText(/ya está en la lista: sigue completándolo ahí/)).toBeInTheDocument();
    expect(screen.getByLabelText('Cantidad')).toHaveValue('1');
  });

  it('un equipo con serie no suma: lleva a su línea como antes', async () => {
    const PHONE = { ...CABLE, id: 5, name: 'iPhone 15 128GB', is_serialized: true, requires_imei: true, primary_code: 'IP15' };
    const u = await renderEditor([]);
    search.mockResolvedValueOnce(page([PHONE]));
    await u.type(picker(), 'IP15{Enter}');
    await screen.findByRole('article', { name: PHONE.name });
    search.mockResolvedValueOnce(page([PHONE]));
    await u.type(picker(), 'IP15{Enter}');
    expect(await screen.findByText(/ya está en la lista: sigue completándolo ahí/)).toBeInTheDocument();
    expect(latest[0].units).toHaveLength(1);
  });
});

describe('revisión independiente', () => {
  const page = (rows: object[]) => ({ count: rows.length, page: 1, page_size: 8, results: rows });
  const picker = () => screen.getByRole('combobox', { name: /Buscar el artículo/ });
  const accessory = (inventory: number) => ({
    id: 9, name: 'Cable USB-C 1m', slug: 'p-9', price: '10.00', inventory, primary_code: '7501234567890',
    is_serialized: false, requires_imei: false, condition_line: 'new', search_exact: true,
  });

  it.each([
    ['con stock (sin «Contar por equipo»)', 5],
    ['sin stock (con la casilla «Contar por equipo»)', 0],
  ])('H1 · sólo teclado, %s: cada lectura llega al buscador y suma +1', async (_name, inventory) => {
    const u = await renderEditor([]);
    act(() => picker().focus());
    for (const expected of ['1', '2', '3']) {
      search.mockResolvedValueOnce(page([accessory(inventory)]));
      await u.keyboard('7501234567890{Enter}');
      expect(await screen.findByLabelText('Cantidad')).toHaveValue(expected);
      expect(picker()).toHaveFocus();
    }
    expect(screen.getAllByRole('article')).toHaveLength(1);
    expect(screen.getByText(/ahora 3 unidades/)).toBeInTheDocument();
  });

  it('H1 · una cantidad fuera de rango no se toca: avisa', async () => {
    const line = newLine({ id: 9, name: 'Cable USB-C 1m', is_serialized: false, requires_imei: false });
    line.quantity = '100000';
    const u = await renderEditor([line]);
    act(() => picker().focus());
    search.mockResolvedValueOnce(page([accessory(5)]));
    await u.keyboard('7501234567890{Enter}');
    expect(await screen.findByText(/No se sumó/)).toHaveAttribute('role', 'status');
    expect(screen.getByLabelText('Cantidad')).toHaveValue('100000');
  });

  it('M1 · cerrar el panel o pasar a «Aplicar a todos» no borra lo pegado', async () => {
    const u = await renderEditor([newLine(USED_PHONE)]);
    const card = cardOf(USED_PHONE.name);
    await u.click(within(card).getByRole('button', { name: 'Pegar varios' }));
    fireEvent.change(within(card).getByLabelText(/Series o IMEI/), { target: { value: 'AAAA1111' } });
    await u.click(within(card).getByRole('button', { name: /Pegar varios/ }));
    await u.click(within(card).getByRole('button', { name: 'Aplicar a todos' }));
    expect(within(card).getByRole('button', { name: /Pegar varios \(texto sin agregar\)/ })).toBeInTheDocument();
    await u.click(within(card).getByRole('button', { name: /Pegar varios/ }));
    expect(within(card).getByLabelText(/Series o IMEI/)).toHaveValue('AAAA1111');
  });

  it('M2 y L2 · la vista previa lista las líneas no reconocidas y los conflictos, enmascarados', async () => {
    const line = newLine(NEW_PHONE);
    line.units = [{ ...blankUnit('new'), serial_number: 'F2LXK1ABCD12' }];
    const u = await renderEditor([line]);
    const card = cardOf(NEW_PHONE.name);
    await u.click(within(card).getByRole('button', { name: 'Pegar varios' }));
    fireEvent.change(within(card).getByLabelText(/Series o IMEI/), { target: { value: `iPhone 13 NEGRO ${IMEI_C}\nF2LXK1ABCD12 ${IMEI_A}` } });
    await u.click(within(card).getByRole('button', { name: 'Revisar lo pegado' }));
    const preview = within(card).getByRole('region', { name: 'Vista previa de lo pegado' });
    expect(within(preview).getByText(/Líneas no reconocidas/)).toBeInTheDocument();
    expect(within(preview).getByText(/Chocan con un equipo/)).toBeInTheDocument();
    expect(preview.textContent).toContain('••••1481');
    expect(preview.textContent).toContain('serie ••••CD12 · IMEI ••••5672');
    expect(preview.textContent).not.toContain(IMEI_C);
    expect(preview.textContent).not.toContain(IMEI_A);
    expect(within(preview).getByText('No hay equipos nuevos para agregar.', { exact: false })).toBeInTheDocument();
  });

  it('L3 · tras «Agregar» el cursor va a la serie del primer equipo agregado', async () => {
    const line = newLine(NEW_PHONE);
    line.units = [{ ...blankUnit('new'), serial_number: 'AAAA1111' }];
    const u = await renderEditor([line]);
    const card = cardOf(NEW_PHONE.name);
    await u.click(within(card).getByRole('button', { name: 'Pegar varios' }));
    fireEvent.change(within(card).getByLabelText(/Series o IMEI/), { target: { value: 'BBBB2222\nCCCC3333' } });
    await u.click(within(card).getByRole('button', { name: 'Revisar lo pegado' }));
    await u.click(within(card).getByRole('button', { name: 'Agregar 2 equipos' }));
    const serials = serialBoxes(cardOf(NEW_PHONE.name));
    expect(serials.map((b) => b.value)).toEqual(['AAAA1111', 'BBBB2222', 'CCCC3333']);
    expect(serials[1]).toHaveFocus();
  });
});
