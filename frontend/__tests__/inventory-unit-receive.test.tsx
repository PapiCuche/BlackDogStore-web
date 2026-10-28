import { act, fireEvent, render, screen, within } from '@testing-library/react';

import { UnitReceivePanel } from '@/app/admin/components/UnitReceivePanel';
import { InventoryTextPreview } from '@/app/admin/components/InventoryTextPreview';
import { planDraft, type DraftCandidate } from '@/app/admin/lib/inventory-text-draft';
import {
  receivePayload, receiveRows, type ReceiveJob,
} from '@/app/admin/lib/inventory-unit-receive';
import type { TextCandidate } from '@/app/admin/lib/inventory-text-preview';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * INV-FAST-B2 · «Revisar y registrar»: la lista revisada pasa por el importador OFICIAL de equipos (vista previa y
 * aplicar), sin copiar a Excel. Cada fila dice en qué estado está: borrador, listo, necesita revisión, registrado.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;
const reply = (status: number, body: unknown) =>
  ({ ok: status >= 200 && status < 300, status, json: async () => body, headers: new Headers() }) as Response;

const IMEI_1 = '490154203237518';
const IMEI_2 = '356938035643809';

function row(id: number, change: Partial<DraftCandidate> = {}): DraftCandidate {
  const base: TextCandidate = {
    id, source: 'iPhone 13', kind: 'device', line: 'used', origin: 'own', model: 'iPhone 13', capacityGb: 128,
    ramGb: null, batteryPercent: 91, colour: 'azul', sim: '', region: '', watts: null, length: '', generation: '',
    serial: `SER${id}`, imei: id === 1 ? IMEI_1 : IMEI_2, imei2: '', supplierCost: '1200', quantity: null,
    duplicateOf: null, warnings: [],
  };
  return { ...base, productId: 7, productName: 'iPhone 13 128 GB Azul Seminuevo', commercialPrice: '', confirmedQuantity: null, ...change };
}

const ROWS = [row(1), row(2), row(3, { serial: '' }), row(4, { duplicateOf: 2 })];
const SETTINGS = { branch: 'Principal', branchId: 11, reason: 'Compra WhatsApp' };

const JOB: ReceiveJob = {
  id: 90, status: 'previewed', import_type: 'units', cost_column_ignored: false,
  counts: { total: 2, create: 2, update: 0, no_change: 0, skip: 0, error: 0 },
  rows: [
    { key: '1', row: 2, action: 'create', errors: [], warnings: [], data: {} },
    { key: '2', row: 3, action: 'create', errors: [], warnings: ['Aviso del importador.'], data: {} },
  ],
  summary: {},
};
const APPLIED = {
  ...JOB, status: 'applied', summary: { applied: { units: 2, movements: 1 } },
  label_batches: [{ id: 5, status: 'ready', units: 2 }], label_error: false,
};

const calls = (part: string) => send.mock.calls.filter(([url]) => String(url).includes(part));
const bodyOf = (call: unknown[]) => JSON.parse(String((call[1] as RequestInit).body));

describe('lo que viaja y cada estado', () => {
  it('sólo viajan las filas que pueden registrarse, con la forma del importador', () => {
    const plan = planDraft(ROWS, 'own');
    expect(plan.included.map(r => r.id)).toEqual([1, 2]);
    const body = receivePayload(plan, SETTINGS, false);
    expect(body).toEqual({
      branch: 11, reason: 'Compra WhatsApp',
      rows: [
        { key: '1', product_id: 7, serial_number: 'SER1', imei: IMEI_1, imei2: '', condition: 'used', battery_health: '91' },
        { key: '2', product_id: 7, serial_number: 'SER2', imei: IMEI_2, imei2: '', condition: 'used', battery_health: '91' },
      ],
    });
    // The cost only travels for whoever sees costs.
    expect(receivePayload(plan, SETTINGS, true).rows[0]).toMatchObject({ cost: '1200' });
  });

  it('borrador -> listo -> registrado, y lo que no entra dice por qué', () => {
    const plan = planDraft(ROWS, 'own');
    const draft = receiveRows(ROWS, plan, null, false);
    expect(draft.map(r => r.status)).toEqual(['draft', 'draft', 'review', 'review']);
    expect(draft[2].reason).toMatch(/Falta el número de serie/);
    expect(draft[3].reason).toMatch(/duplicado/i);
    const ready = receiveRows(ROWS, plan, JOB, false);
    expect(ready.map(r => r.status)).toEqual(['ready', 'ready', 'review', 'review']);
    expect(ready[1].notes).toEqual(['Aviso del importador.']);
    const done = receiveRows(ROWS, plan, JOB, true);
    expect(done.map(r => r.status)).toEqual(['registered', 'registered', 'review', 'review']);
  });

  it('un dato de identidad que el catálogo no distingue (eSIM, región, RAM) se avisa en la fila', () => {
    const rows = [row(1, { sim: 'esim', region: 'PE', ramGb: 8 }), row(2)];
    const view = receiveRows(rows, planDraft(rows, 'own'), JOB, false);
    expect(view[0].notes).toEqual(['Del texto y sin reflejo en el catálogo: 8 GB de RAM, eSIM, región PE. Confírmalo antes de registrar.']);
    expect(view[1].notes).toEqual(['Aviso del importador.']);
  });

  it('un error del importador deja la fila en revisión con su mensaje', () => {
    const plan = planDraft(ROWS, 'own');
    const failed: ReceiveJob = {
      ...JOB, counts: { ...JOB.counts, create: 1, error: 1 },
      rows: [JOB.rows[0], { key: '2', row: 3, action: 'error', errors: ['El número de serie SER2 ya está registrado.'], warnings: [], data: {} }],
    };
    const view = receiveRows(ROWS, plan, failed, false);
    expect(view[1]).toMatchObject({ status: 'review', reason: 'El número de serie SER2 ya está registrado.' });
  });
});

function panel(props: Partial<React.ComponentProps<typeof UnitReceivePanel>> = {}) {
  const onKeepEditing = jest.fn();
  render(<UnitReceivePanel rows={ROWS} plan={planDraft(ROWS, 'own')} settings={SETTINGS} canSeeCosts={false}
    companyId={5} onKeepEditing={onKeepEditing} {...props} />);
  return onKeepEditing;
}

function route(job: unknown = JOB, applied: unknown = APPLIED, applyStatus = 200) {
  send.mockImplementation(async (url) => {
    const path = String(url);
    if (path.includes('/text-preview/units/preview/')) return reply(201, job);
    if (path.includes('/units/import/90/apply/')) return reply(applyStatus, applied);
    return reply(404, {});
  });
}

beforeEach(() => send.mockReset());

describe('el panel de recepción', () => {
  it('muestra cada fila con su estado antes de revisar y no llama a nadie', () => {
    panel();
    const table = screen.getByRole('table', { name: /Estado de cada fila/ });
    expect(within(table).getAllByText('Borrador')).toHaveLength(2);
    expect(within(table).getAllByText('Necesita revisión')).toHaveLength(2);
    expect(within(table).getByText(/Falta el número de serie/)).toBeInTheDocument();
    expect(within(table).getByText(/duplicado/i)).toBeInTheDocument();
    // Identifiers are shown masked.
    expect(table).not.toHaveTextContent(IMEI_1);
    expect(send).not.toHaveBeenCalled();
  });

  it('«Revisar y registrar» pide la vista previa oficial y no registra', async () => {
    route();
    panel();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Revisar y registrar' })); });
    expect(calls('/text-preview/units/preview/')).toHaveLength(1);
    expect(String(calls('/text-preview/units/preview/')[0][0])).toContain('?company=5');
    expect(bodyOf(calls('/text-preview/units/preview/')[0]).rows).toHaveLength(2);
    expect(calls('/apply/')).toHaveLength(0);
    expect(screen.getAllByText('Listo')).toHaveLength(2);
    expect(screen.getByText('Aviso del importador.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Registrar 2 equipos/ })).toBeDisabled();
  });

  it('registra sólo con la confirmación explícita, por el aplicar oficial', async () => {
    route();
    panel();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Revisar y registrar' })); });
    fireEvent.click(screen.getByLabelText(/Confirmo registrar 2 equipos en Principal/));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Registrar 2 equipos/ })); });
    expect(calls('/apply/')).toHaveLength(1);
    expect(String(calls('/apply/')[0][0])).toContain('/admin/inventory/units/import/90/apply/');
    expect(screen.getAllByText('Registrado')).toHaveLength(2);
    expect(screen.getByRole('status')).toHaveTextContent('2 equipos registrados');
  });

  it('un doble clic en «Registrar» aplica una sola vez', async () => {
    route();
    panel();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Revisar y registrar' })); });
    fireEvent.click(screen.getByLabelText(/Confirmo registrar/));
    const button = screen.getByRole('button', { name: /Registrar 2 equipos/ });
    await act(async () => { fireEvent.click(button); fireEvent.click(button); });
    expect(calls('/apply/')).toHaveLength(1);
  });

  it('después de registrar ofrece los siguientes pasos y avisa si las etiquetas fallaron', async () => {
    route(JOB, { ...APPLIED, label_batches: [], label_error: true });
    panel();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Revisar y registrar' })); });
    fireEvent.click(screen.getByLabelText(/Confirmo registrar/));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Registrar 2 equipos/ })); });
    expect(screen.getByRole('link', { name: /Etiquetas/ })).toHaveAttribute('href', '/admin/inventory/labels');
    expect(screen.getByRole('link', { name: /Publicación/ })).toHaveAttribute('href', '/admin/products/publication');
    expect(screen.getByText(/las etiquetas no se pudieron preparar/i)).toBeInTheDocument();
  });

  it('con filas que el importador rechaza no deja registrar y manda a corregir', async () => {
    route({
      ...JOB, counts: { ...JOB.counts, create: 1, error: 1 },
      rows: [JOB.rows[0], { key: '2', row: 3, action: 'error', errors: ['El IMEI ya está registrado en otro equipo de la empresa.'], warnings: [], data: {} }],
    });
    const keepEditing = panel();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Revisar y registrar' })); });
    expect(screen.getByText(/El IMEI ya está registrado/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Confirmo registrar/)).toBeNull();
    expect(screen.getByRole('button', { name: /Registrar/ })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Seguir corrigiendo' }));
    expect(keepEditing).toHaveBeenCalled();
  });

  it('si la vista previa falla lo dice y la fila sigue como borrador', async () => {
    send.mockImplementation(async () => reply(404, { detail: 'No encontrado.' }));
    panel();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Revisar y registrar' })); });
    expect(screen.getByRole('alert')).toHaveTextContent('No encontrado.');
    expect(screen.getAllByText('Borrador')).toHaveLength(2);
  });

  it('sin sucursal elegida no revisa', () => {
    panel({ settings: { ...SETTINGS, branchId: null } });
    expect(screen.getByRole('button', { name: 'Revisar y registrar' })).toBeDisabled();
    expect(screen.getByText(/Elige la sucursal/)).toBeInTheDocument();
  });

  it('guarda nada en el navegador', async () => {
    route();
    const set = jest.spyOn(Storage.prototype, 'setItem');
    panel();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Revisar y registrar' })); });
    expect(set).not.toHaveBeenCalled();
    set.mockRestore();
  });
});

describe('dentro de la vista previa de la lista', () => {
  const MATCH = { matches: { 1: { status: 'exact', reason: '', options: [{ product_id: 7, name: 'iPhone 13 128 GB Azul', condition_line: 'used', options: [] }] } } };

  async function ownDraft() {
    send.mockImplementation(async (url) => {
      const path = String(url);
      if (path.includes('/admin/inventory/branches/')) return reply(200, { results: [{ id: 11, name: 'Principal' }] });
      if (path.includes('/text-preview/match/')) return reply(200, MATCH);
      if (path.includes('/text-preview/units/preview/')) return reply(201, { ...JOB, rows: [JOB.rows[0]], counts: { ...JOB.counts, total: 1, create: 1 } });
      return reply(404, {});
    });
    render(<InventoryTextPreview canSeeCosts={false} canManageProducts canReceiveUnits companyId={5} />);
    fireEvent.change(screen.getByLabelText('Origen declarado'), { target: { value: 'own' } });
    fireEvent.change(screen.getByLabelText('Texto para reconocer'), {
      target: { value: `iPhone 13\n128 GB\nColor: azul\nNS: SER9999\nIMEI: ${IMEI_1}` } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Analizar/ })); });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Buscar en el catálogo' })); });
  }

  it('el borrador de equipos ofrece revisar y registrar, y sigue ofreciendo copiar a Excel', async () => {
    await ownDraft();
    fireEvent.change(screen.getByLabelText('Sucursal del borrador'), { target: { value: '11' } });
    fireEvent.change(screen.getByLabelText('Motivo del borrador'), { target: { value: 'Compra' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Generar borrador' })); });
    expect(screen.getByRole('button', { name: 'Copiar filas para la Carga de equipos' })).toBeInTheDocument();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Revisar y registrar' })); });
    const sent = bodyOf(calls('/text-preview/units/preview/')[0]);
    expect(sent.branch).toBe(11);
    expect(sent.rows[0]).toMatchObject({ product_id: 7, serial_number: 'SER9999', imei: IMEI_1 });
  });

  it('corregir el borrador cierra la revisión', async () => {
    await ownDraft();
    fireEvent.change(screen.getByLabelText('Sucursal del borrador'), { target: { value: '11' } });
    fireEvent.change(screen.getByLabelText('Motivo del borrador'), { target: { value: 'Compra' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Generar borrador' })); });
    fireEvent.change(screen.getByLabelText('Motivo del borrador'), { target: { value: 'Compra 2' } });
    expect(screen.queryByRole('button', { name: 'Revisar y registrar' })).toBeNull();
  });
});
