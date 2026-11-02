/**
 * INV-TEXT-03 · PR A — la revisión de una lista de WhatsApp en «Recibir mercadería», en la pantalla real del
 * asistente: importes sin etiqueta clara, dudas y correcciones visibles, «Siguiente error» con teclado, la cantidad
 * de un accesorio que ya estaba en la recepción, quitar y deshacer, y el texto que no se pierde. El servidor es un
 * catálogo falso en memoria. Datos sintéticos (`support/whatsapp-lists.ts`).
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ReceivingWizard, type ReceivingAccess } from '@/app/admin/components/ReceivingWizard';
import * as receipts from '@/app/admin/lib/inventory-receipts';
import * as internalApi from '@/app/admin/lib/internal-api';
import { forgetBranch } from '@/app/admin/lib/last-branch';
import { fetchWithAuth } from '@/app/lib/auth';

import { fakeImei, fakeSerial } from './support/whatsapp-lists';

const mockRouter = { push: jest.fn(), replace: jest.fn(), refresh: jest.fn(), back: jest.fn(), forward: jest.fn(), prefetch: jest.fn() };
jest.mock('next/navigation', () => ({ usePathname: () => '/admin/inventory/receive', useRouter: () => mockRouter }));
jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/admin', () => ({
  ...jest.requireActual('@/app/lib/admin'),
  fetchAdminProducts: jest.fn(async () => ({ count: 0, page: 1, page_size: 8, results: [] })),
  fetchAdminProductDetail: jest.fn(),
}));
jest.mock('@/app/admin/lib/internal-api', () => ({ ...jest.requireActual('@/app/admin/lib/internal-api'), fetchSuppliers: jest.fn() }));

const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;
const reply = (status: number, body: unknown) =>
  ({ ok: status >= 200 && status < 300, status, json: async () => body, headers: new Headers() }) as Response;

type Article = { id: number; name: string; model: string; capacityGb: number | null; colour: string; line: string; serial: boolean };
const CATALOGUE: Article[] = [
  { id: 12, name: 'iPhone 12 64 GB Blanco Seminuevo', model: 'iPhone 12', capacityGb: 64, colour: 'blanco', line: 'used', serial: true },
  { id: 13, name: 'iPhone 13 128 GB Azul Seminuevo', model: 'iPhone 13', capacityGb: 128, colour: 'azul', line: 'used', serial: true },
  { id: 70, name: 'Cable USB-C', model: 'Cable USB-C', capacityGb: null, colour: '', line: 'accessory', serial: false },
];
const sig = (v: { model: string; capacityGb: number | null; colour: string; line: string }) =>
  [v.model.toLowerCase(), v.capacityGb, v.colour.toLowerCase(), v.line].join('|');

let seen: { url: string; body: Record<string, unknown> }[];
function route(url: string, init?: RequestInit): Response {
  const body = init?.body ? JSON.parse(String(init.body)) : {};
  seen.push({ url, body });
  if (url.includes('/text-preview/match/')) {
    const matches: Record<string, unknown> = {};
    for (const c of body.candidates as { key: string; model: string; capacityGb: number | null; colour: string; line: string }[]) {
      const found = CATALOGUE.find((a) => sig(a) === sig(c));
      matches[c.key] = found
        ? { status: 'exact', reason: '', options: [{ product_id: found.id, name: found.name, condition_line: found.line === 'used' ? 'used' : 'new', options: [], is_serialized: found.serial, requires_imei: found.serial, is_active: true }] }
        : { status: 'none', reason: 'No existe esa variante del modelo.', options: [] };
    }
    return reply(200, { matches });
  }
  if (url.includes('/catalog/grades/')) return reply(200, { results: [{ id: 5, code: 'grado-b', label: 'B', description: '', sort_order: 1, is_active: true }] });
  return reply(404, { detail: 'no' });
}

const ACCESS: ReceivingAccess = { costs: true, manageProducts: true, viewProducts: true, labels: true };
async function start(access: Partial<ReceivingAccess> = {}) {
  const u = userEvent.setup();
  await act(async () => {
    render(<ReceivingWizard access={{ ...ACCESS, ...access }} scope={{ user: 1, company: 7 }} branches={[{ id: 1, name: 'Miraflores' }]} defaultBranch={1} />);
  });
  await u.click(screen.getByRole('button', { name: /Siguiente: artículos/ }));
  await screen.findByRole('heading', { name: '¿Qué llegó?' });
  await u.click(screen.getByRole('button', { name: /^Pegar una lista/ }));
  return u;
}
const box = () => screen.getByLabelText('Texto para reconocer') as HTMLTextAreaElement;
async function paste(text: string) {
  if (!screen.queryByLabelText('Texto para reconocer')) fireEvent.click(screen.getByRole('button', { name: 'Vaciar el texto' }));
  fireEvent.change(box(), { target: { value: text } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Analizar y preparar' })); });
}
const summary = () => screen.getByLabelText('Resumen del reconocimiento').textContent ?? '';
const addButton = () => screen.getByRole('button', { name: /a la recepción$|Nada listo todavía/ });

beforeEach(() => {
  window.scrollTo = jest.fn();
  window.history.replaceState(null, '');
  forgetBranch();
  receipts.forgetStashedDraft();
  seen = [];
  send.mockReset().mockImplementation(async (url, init) => route(String(url), init as RequestInit));
  (internalApi.fetchSuppliers as jest.Mock).mockReset().mockResolvedValue({ count: 0, page: 1, page_size: 25, results: [] });
});
afterEach(() => jest.restoreAllMocks());

const USED_12 = (n: number, extra: string[] = []) =>
  ['iPhone 12 64gb blanco sem', `NS: ${fakeSerial(n)}`, `IMEI: ${fakeImei(n)}`, ...extra].join('\n');

describe('importes: «Precio:» sin calificar nunca entra como costo', () => {
  it('la fila espera la decisión; «Son costos» la toma para todas y se ve como corregido', async () => {
    const u = await start();
    await paste([USED_12(1, ['Precio: 2500']), USED_12(2, ['Precio: 2400'])].join('\n\n'));
    await waitFor(() => expect(summary()).toMatch(/0 filas completas · 2 necesitan corrección/));
    expect(screen.getAllByText('Importe por decidir')).toHaveLength(2);
    // Where the person is: pasted and analysed, now reviewing fields (the catalogue already answered).
    const steps = within(screen.getByRole('list', { name: 'Pasos de la lista pegada' })).getAllByRole('listitem');
    expect(steps.map((li) => li.textContent)).toEqual(['✓ 1. Pegar mensaje', '✓ 2. Analizar', '3. Revisar campos', '✓ 4. Asociar catálogo', '5. Preparar ingreso', '6. Confirmar']);
    expect(steps[2]).toHaveAttribute('aria-current', 'step');
    expect(addButton()).toHaveTextContent('Nada listo todavía');
    const group = screen.getByRole('group', { name: 'Importes sin etiqueta clara' });
    await u.click(within(group).getByRole('button', { name: 'Son costos (2)' }));
    await waitFor(() => expect(summary()).toMatch(/2 filas completas/));
    expect(screen.getAllByText(/✓ Corregido: importe/)).toHaveLength(2);
    await u.click(screen.getAllByText(/^Equipo 1$/)[0]);
    expect(screen.getByLabelText('Costo del equipo (opcional)')).toHaveValue('2500');
    expect(screen.queryByLabelText(/Precio de venta del equipo/)).toHaveValue('');
  });

  it('por fila: «Es el precio de venta» va al precio propio del seminuevo y el costo queda vacío', async () => {
    const u = await start();
    await paste(USED_12(3, ['Precio: 2500']));
    await u.click(await screen.findByText(/^Equipo 1$/));
    const decision = await screen.findByRole('group', { name: 'Importe sin etiqueta clara' });
    expect(decision).toHaveTextContent('S/ 2500');
    await u.click(within(decision).getByRole('button', { name: 'Es el precio de venta' }));
    expect(screen.getByLabelText(/Precio de venta del equipo/)).toHaveValue('2500');
    expect(screen.getByLabelText('Costo del equipo (opcional)')).toHaveValue('');
  });

  it('sin permiso de costos ni de precios no se ofrece decidir ni se muestran cifras', async () => {
    const u = await start({ costs: false, manageProducts: false });
    await paste(USED_12(4, ['Precio: 2500']));
    await u.click(await screen.findByText(/^Equipo 1$/));
    expect(screen.queryByRole('group', { name: 'Importes sin etiqueta clara' })).not.toBeInTheDocument();
    expect(screen.getByText(/importe sin etiqueta clara: no se usa/)).toBeInTheDocument();
    expect(screen.queryByLabelText('Costo del equipo (opcional)')).not.toBeInTheDocument();
  });
});

describe('la cantidad de un accesorio que ya está en la recepción', () => {
  it('«Cable x5» sobre una línea de 1: no se suma solo; «Reemplazar» deja 5', async () => {
    const u = await start();
    await paste('Cable USB-C x1');
    await waitFor(() => expect(addButton()).toHaveTextContent('Agregar 1 equipo listo a la recepción'));
    await u.click(addButton());
    expect(screen.getByLabelText('Cantidad')).toHaveValue('1');
    await paste('Cable USB-C x5');
    await waitFor(() => expect(summary()).toMatch(/1 necesita corrección/));
    expect(addButton()).toHaveTextContent('Nada listo todavía');
    await u.click(screen.getByText(/^Accesorio 1$/));
    const decision = screen.getByRole('group', { name: 'Cantidad que ya está en la recepción' });
    await u.click(within(decision).getByRole('button', { name: 'Reemplazar (quedan 5)' }));
    await u.click(addButton());
    expect(screen.getByLabelText('Cantidad')).toHaveValue('5');
  });

  it('«Sumar» deja 6, sólo porque se eligió', async () => {
    const u = await start();
    await paste('Cable USB-C x1');
    await waitFor(() => expect(addButton()).not.toBeDisabled());
    await u.click(addButton());
    await paste('Cable USB-C x5');
    await u.click(await screen.findByText(/^Accesorio 1$/));
    await u.click(screen.getByRole('button', { name: 'Sumar (quedan 6)' }));
    await u.click(addButton());
    expect(screen.getByLabelText('Cantidad')).toHaveValue('6');
  });
});

describe('revisar campos sin salir de la lista', () => {
  it('«Siguiente error» (Alt+N) recorre lo pendiente y avanza; el único importe sin etiqueta espera «Es el costo»', async () => {
    const u = await start();
    await paste(['IPH 13 128 azul sem 1850', `NS: ${fakeSerial(8)}`, `IMEI: ${fakeImei(8)}`, '', 'iPhone 12 64gb blanco sem', 'Precio: 1700'].join('\n'));
    // INV-TEXT-03 review: the lone 1850 is not written as the cost by itself: both rows wait for a decision.
    await waitFor(() => expect(summary()).toMatch(/0 filas completas · 2 necesitan corrección/));
    const next = screen.getByRole('button', { name: /^Siguiente error \(4\)/ });
    expect(next).toHaveAttribute('aria-keyshortcuts', 'Alt+N');
    await u.click(next);
    await waitFor(() => expect(document.activeElement).toHaveAttribute('data-field', 'amount'));
    expect(document.activeElement).toHaveTextContent('S/ 1850');
    fireEvent.keyDown(document.activeElement!, { key: 'n', code: 'KeyN', altKey: true });
    await waitFor(() => expect(document.activeElement).toHaveAttribute('data-field', 'serial'));
    fireEvent.keyDown(document.activeElement!, { key: 'n', code: 'KeyN', altKey: true });
    await waitFor(() => expect(document.activeElement).toHaveAttribute('data-field', 'imei'));
    fireEvent.keyDown(document.activeElement!, { key: 'n', code: 'KeyN', altKey: true });
    await waitFor(() => expect(document.activeElement).toHaveAttribute('data-field', 'amount'));
    fireEvent.keyDown(document.activeElement!, { key: 'n', code: 'KeyN', altKey: true });
    // Round again: the first row's amount.
    await waitFor(() => expect(document.activeElement).toHaveTextContent('S/ 1850'));
    await u.click(within(document.activeElement as HTMLElement).getByRole('button', { name: 'Es el costo' }));
    await waitFor(() => expect(summary()).toMatch(/1 fila completa/));
    expect(screen.getAllByText(/✓ Corregido: importe/).length).toBeGreaterThan(0);
  });

  it('una cantidad leída del texto se confirma con «Es correcto»', async () => {
    const u = await start();
    await paste('Cable USB-C x3');
    await waitFor(() => expect(summary()).toMatch(/1 con datos por confirmar/));
    await u.click(screen.getByText(/^Accesorio 1$/));
    expect(screen.getByText('Cantidad tomada del texto (3): confírmala.')).toBeInTheDocument();
    await u.click(screen.getByRole('button', { name: 'Es correcto' }));
    expect(screen.getAllByText(/✓ Corregido: cantidad/).length).toBeGreaterThan(0);
  });

  it('sin permiso de costos y con dos importes sin etiqueta, sólo se puede no usarlos (nada a ciegas)', async () => {
    const u = await start({ costs: false });
    await paste(['iPhone 12 64gb blanco sem', `NS: ${fakeSerial(12)}`, `IMEI: ${fakeImei(12)}`, 'Precio: 1800', 'Valor: 2000'].join('\n'));
    await u.click(await screen.findByText(/^Equipo 1$/));
    const decision = await screen.findByRole('group', { name: 'Importe sin etiqueta clara' });
    expect(decision).toHaveTextContent('varios importes sin etiqueta clara');
    expect(decision).not.toHaveTextContent(/1800|2000/);
    expect(within(decision).queryByRole('button', { name: 'Es el precio de venta' })).not.toBeInTheDocument();
    await u.click(within(decision).getByRole('button', { name: 'No usarlo' }));
    expect(screen.queryByRole('group', { name: 'Importe sin etiqueta clara' })).not.toBeInTheDocument();
  });

  it('«Son costos» en bloque no pisa un costo indicado y lo dice', async () => {
    const u = await start();
    await paste([USED_12(13, ['Precio: 2500 (costo 2100)']), USED_12(14, ['Precio: 2400'])].join('\n\n'));
    const group = await screen.findByRole('group', { name: 'Importes sin etiqueta clara' });
    expect(group).toHaveTextContent('1 fila ya tiene un costo indicado');
    await u.click(within(group).getByRole('button', { name: 'Son costos (1)' }));
    await u.click(screen.getAllByText(/^Equipo 1$/)[0]);
    expect(screen.getByLabelText('Costo del equipo (opcional)')).toHaveValue('2100');
  });

  it('quitar una fila no toca las demás y se puede deshacer', async () => {
    const u = await start();
    await paste([USED_12(5), USED_12(6)].join('\n\n'));
    await waitFor(() => expect(summary()).toMatch(/2 filas completas/));
    await u.click(screen.getAllByText(/^Equipo 1$/)[0]);
    await u.click(screen.getByRole('button', { name: 'Quitar el equipo 1 de la lista' }));
    await waitFor(() => expect(summary()).toMatch(/Reconocidos: 1 equipo/));
    await u.click(screen.getByRole('button', { name: 'Deshacer' }));
    await waitFor(() => expect(summary()).toMatch(/Reconocidos: 2 equipos/));
  });

  it('el estado estético del texto se enlaza con el de la empresa que dice lo mismo', async () => {
    const u = await start();
    await paste(USED_12(7, ['Estado: B']));
    await u.click(await screen.findByText(/^Equipo 1$/));
    await waitFor(() => expect(screen.getByLabelText('Estado estético')).toHaveValue('5'));
  });
});

describe('el texto pegado no se pierde ni se duplica', () => {
  it('se conserva tras analizar; volver a analizarlo no agrega nada dos veces, salvo que se pida', async () => {
    const u = await start();
    await paste(USED_12(9));
    // Once analysed: shown masked (no full IMEI on screen), the full text on request.
    expect(screen.queryByLabelText('Texto para reconocer')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Texto analizado')).not.toHaveTextContent(fakeImei(9));
    expect(screen.getByLabelText('Texto analizado')).toHaveTextContent(fakeImei(9).slice(-4));
    await u.click(screen.getByRole('button', { name: 'Mostrar texto' }));
    expect(box()).toHaveValue(USED_12(9));
    expect(screen.getByRole('button', { name: 'Ya analizado' })).toBeDisabled();
    // Edited and analysed again: the row already in the list is held back, the new one enters.
    await paste([USED_12(9), USED_12(10)].join('\n\n'));
    await waitFor(() => expect(summary()).toMatch(/Reconocidos: 2 equipos/));
    expect(screen.getByText(/1 fila repetida no se agregó/)).toBeInTheDocument();
    await u.click(screen.getByRole('button', { name: 'Agregarla igual' }));
    await waitFor(() => expect(summary()).toMatch(/Reconocidos: 3 equipos/));
    // The same serial twice is still caught as a duplicate.
    expect(screen.getAllByText(/repetido en la recepción/).length).toBeGreaterThan(0);
  });

  it('texto sin analizar: «Siguiente» no avanza y lo dice; vaciarlo lo quita', async () => {
    const u = await start();
    fireEvent.change(box(), { target: { value: 'iPhone 12 64gb blanco' } });
    expect(screen.getByText(/Sin analizar todavía/)).toBeInTheDocument();
    await u.click(screen.getByRole('button', { name: /Siguiente: revisar/ }));
    expect(await screen.findByText(/Hay texto pegado sin analizar/)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '¿Qué llegó?' })).toBeInTheDocument();
    await u.click(screen.getByRole('button', { name: 'Vaciar el texto' }));
    expect(box()).toHaveValue('');
  });

  it('el texto sigue ahí al volver del paso anterior', async () => {
    const u = await start();
    fireEvent.change(box(), { target: { value: 'Cable USB-C x3' } });
    await u.click(screen.getByRole('button', { name: /^Atrás/ }));
    await u.click(await screen.findByRole('button', { name: /Siguiente: artículos/ }));
    await u.click(await screen.findByRole('button', { name: /^Pegar una lista/ }));
    expect(box()).toHaveValue('Cable USB-C x3');
  });

  it('nada del texto viaja: al catálogo sólo modelo, capacidad, color y línea', async () => {
    await start();
    await paste(USED_12(11, ['Precio: 2500', 'Obs: pantalla cambiada']));
    await waitFor(() => expect(seen.some((c) => c.url.includes('/text-preview/match/'))).toBe(true));
    const sent = JSON.stringify(seen.map((c) => c.body));
    expect(sent).not.toMatch(new RegExp(`${fakeImei(11)}|${fakeSerial(11)}|2500|pantalla`));
  });

  it('P2-A: un IMEI con espacios (formato de iOS) no aparece como importe ni se muestra completo', async () => {
    await start();
    const a = fakeImei(912345);
    const spaced = `${a.slice(0, 2)} ${a.slice(2, 8)} ${a.slice(8, 14)} ${a.slice(14)}`;
    await paste(['iPhone 12 64gb blanco sem', `NS: ${fakeSerial(20)}`, `IMEI: ${spaced}`, 'Costo: 1500'].join('\n'));
    await waitFor(() => expect(summary()).toMatch(/1 fila completa/));
    expect(screen.queryByRole('group', { name: /Importe/ })).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain(spaced);
    expect(document.body.textContent?.replace(/\D/g, '')).not.toContain(a);
  });
});

describe('una fila que se suma a otra en espera', () => {
  it('lo dice en la propia fila: entra sólo junto con esa fila', async () => {
    const u = await start();
    // The first row waits for its «x 25» to be confirmed, so both can be set aside.
    await paste('Cable USB-C x 25\nCable USB-C x5');
    await waitFor(() => expect(summary()).toMatch(/Reconocidos: 0 equipos y 2 accesorios/));
    await u.click(screen.getByText(/^Accesorio 2$/));
    expect(screen.getByText(/Esta fila entra sólo junto con la fila 1\./)).toBeInTheDocument();
    // Both set aside: the second says that its partner waits too.
    await u.click(screen.getByRole('button', { name: /Dejar estas 2 filas para otra recepción/ }));
    expect(screen.getByText(/Esta fila entra sólo junto con la fila 1, que está en espera/)).toBeInTheDocument();
  });
});

describe('cuarta revisión: un IMEI tras un precio no aparece en la pantalla', () => {
  it.each([
    ['2-6-6-1', (i: string) => `${i.slice(0, 2)} ${i.slice(2, 8)} ${i.slice(8, 14)} ${i.slice(14)}`],
    ['3×5', (i: string) => i.match(/.{3}/g)!.join(' ')],
  ])('precio + IMEI %s: ni en importes ni en el texto de la página', async (_name, shape) => {
    const u = await start();
    const a = fakeImei(424242);
    await paste(`iPhone 12 64gb blanco sem 1500 ${shape(a)}\nNS: ${fakeSerial(30)}`);
    await u.click(await screen.findByText(/^Equipo 1$/));
    await u.click(screen.getByText('Ver texto original (enmascarado)'));
    const page = document.body.textContent ?? '';
    expect(page.replace(/\D/g, '')).not.toContain(a);
    for (const group of shape(a).split(' ')) expect(page).not.toContain(`S/ ${group} `);
  });
});

