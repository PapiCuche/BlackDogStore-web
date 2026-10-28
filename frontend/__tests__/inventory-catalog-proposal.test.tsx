import { act, fireEvent, render, screen, within } from '@testing-library/react';

import { CatalogProposalPanel } from '@/app/admin/components/CatalogProposalPanel';
import { InventoryTextPreview } from '@/app/admin/components/InventoryTextPreview';
import {
  approvable, approvalCounts, proposalRows, toggleModel, toggleVariant, type Proposal, type ProposalRow,
} from '@/app/admin/lib/inventory-catalog-proposal';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * INV-FAST-B1 · «Proponer lo que falta». El servidor propone y la persona aprueba; sólo lo aprobado se crea.
 * Viajan modelo, capacidad, color, línea y un «sí/no» de IMEI: nunca texto, costos, IMEI ni series.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;
const reply = (status: number, body: unknown) =>
  ({ ok: status >= 200 && status < 300, status, json: async () => body, headers: new Headers() }) as Response;

const IMEI = '490154203237518';

const ROWS: ProposalRow[] = [
  { id: 1, kind: 'device', line: 'used', model: 'Teléfono Y', capacityGb: 128, colour: 'negro' },
  { id: 2, kind: 'device', line: 'used', model: 'Teléfono Y', capacityGb: 256, colour: 'azul' },
  { id: 3, kind: 'device', line: 'used', model: 'Teléfono Q', capacityGb: null, colour: 'negro' },
  { id: 4, kind: 'accessory', line: 'accessory', model: 'Funda', capacityGb: null, colour: '' },
];

const PROPOSAL: Proposal = {
  plan: {
    models: [
      { ref: 'model:teléfono y', name: 'Teléfono Y', status: 'missing', model_id: null, row_keys: ['1', '2'] },
      { ref: 'model:teléfono x', name: 'Teléfono X', status: 'existing', model_id: 3, row_keys: ['5'] },
    ],
    options: [
      { ref: 'option:3:256 gb', attribute_id: 3, attribute_name: 'Capacidad', label: '256 GB', status: 'missing', option_id: null },
    ],
    variants: [
      {
        ref: 'v1', model_ref: 'model:teléfono y', line: 'used', option_refs: [], row_keys: ['1'], status: 'missing',
        product_id: null, suggested_name: 'Teléfono Y 128 GB Negro Seminuevo', suggested_price: null, requires_imei: true,
        creates: { model: true, options: [] },
      },
      {
        ref: 'v2', model_ref: 'model:teléfono y', line: 'used', option_refs: [], row_keys: ['2'], status: 'missing',
        product_id: null, suggested_name: 'Teléfono Y 256 GB Azul Seminuevo', suggested_price: '1500.00', requires_imei: true,
        creates: { model: true, options: ['option:3:256 gb'] },
      },
      {
        ref: 'v3', model_ref: 'model:teléfono x', line: 'used', option_refs: [], row_keys: ['5'], status: 'existing',
        product_id: 9, suggested_name: 'Teléfono X 128 GB Azul Seminuevo', suggested_price: null, requires_imei: true,
        creates: { model: false, options: [] },
      },
    ],
  },
  rows: [
    { key: '1', variant_ref: 'v1', model_ref: 'model:teléfono y', reason: '', warnings: [] },
    { key: '2', variant_ref: 'v2', model_ref: 'model:teléfono y', reason: '', warnings: [] },
    { key: '3', variant_ref: null, model_ref: 'model:teléfono q', reason: 'Falta la capacidad: sin ella no se propone una variante (escríbela en la fila).', warnings: [] },
  ],
  counts: {
    models_missing: 1, models_existing: 1, options_missing: 1, options_existing: 3, variants_missing: 2, variants_existing: 1,
    rows_resolved: 2, rows_unresolved: 1,
  },
};
const APPLIED = {
  created: { models: [{ ref: 'model:teléfono y', name: 'Teléfono Y' }], options: [{ ref: 'option:3:256 gb', label: '256 GB' }],
    variants: [{ ref: 'v1', name: 'a' }, { ref: 'v2', name: 'b' }] },
  existing: { models: [], variants: [] }, replayed: false,
};

const calls = (part: string) => send.mock.calls.filter(([url]) => String(url).includes(part));

beforeEach(() => {
  send.mockReset();
  send.mockImplementation(async (url) => {
    const path = String(url);
    if (path.includes('/catalog-proposal/apply/')) return reply(200, APPLIED);
    if (path.includes('/catalog-proposal/')) return reply(200, PROPOSAL);
    return reply(404, {});
  });
});

describe('lo que viaja', () => {
  it('sólo envía los equipos, sin texto, costos ni IMEI', () => {
    const sent = proposalRows(ROWS);
    expect(sent.map(row => row.key)).toEqual(['1', '2', '3']);
    expect(sent[0]).toEqual({ key: '1', kind: 'device', line: 'used', model: 'Teléfono Y', capacityGb: 128, colour: 'negro' });
    // A line without IMEI says nothing about the variant: no hint travels.
    expect(JSON.stringify(sent)).not.toMatch(/imei/i);
    expect(JSON.stringify(sent)).not.toContain(IMEI);
  });
});

describe('la aprobación', () => {
  it('lo que se puede aprobar son los que faltan, y los existentes no', () => {
    expect(approvable(PROPOSAL.plan)).toEqual({ models: ['model:teléfono y'], variants: ['v1', 'v2'] });
  });

  it('marcar una variante marca su modelo; desmarcar el modelo desmarca sus variantes', () => {
    const none = { models: [], variants: [] };
    expect(toggleVariant(PROPOSAL.plan, none, 'v1', true)).toEqual({ models: ['model:teléfono y'], variants: ['v1'] });
    expect(toggleModel(PROPOSAL.plan, approvable(PROPOSAL.plan), 'model:teléfono y', false)).toEqual({ models: [], variants: [] });
  });

  it('cuenta lo que se crearía, con las opciones una sola vez', () => {
    expect(approvalCounts(PROPOSAL.plan, approvable(PROPOSAL.plan))).toEqual({ models: 1, variants: 2, options: 1 });
    expect(approvalCounts(PROPOSAL.plan, { models: ['model:teléfono y'], variants: ['v1'] })).toEqual({ models: 1, variants: 1, options: 0 });
  });
});

function panel(props: Partial<React.ComponentProps<typeof CatalogProposalPanel>> = {}) {
  const onApplied = jest.fn();
  render(<CatalogProposalPanel rows={ROWS} companyId={5} allowed onApplied={onApplied} {...props} />);
  return onApplied;
}
const propose = async () => { await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Proponer lo que falta' })); }); };

describe('lo que el catálogo no distingue', () => {
  it('avisa por fila de eSIM, región o RAM: no son opciones y no se descartan en silencio', async () => {
    const rows: ProposalRow[] = [
      { ...ROWS[0], sim: 'esim', region: 'PE', ramGb: 8 },
      { ...ROWS[1] },
    ];
    render(<CatalogProposalPanel rows={rows} companyId={5} allowed onApplied={() => undefined} />);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Proponer lo que falta' })); });
    const notes = screen.getByRole('list', { name: 'Datos que no distinguen la variante' });
    expect(notes).toHaveTextContent('Registro 1');
    expect(notes).toHaveTextContent('8 GB de RAM, eSIM, región PE');
    expect(notes).toHaveTextContent('la variante se crea sin ese dato');
    expect(notes).not.toHaveTextContent('Registro 2');
  });
});

describe('el panel', () => {
  it('no ofrece nada sin permiso y lo dice', () => {
    panel({ allowed: false });
    expect(screen.queryByRole('button', { name: 'Proponer lo que falta' })).toBeNull();
    expect(screen.getByText(/requiere permiso/)).toBeInTheDocument();
  });

  it('no aparece sin filas', () => {
    const { container } = render(<CatalogProposalPanel rows={[]} allowed onApplied={() => undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('propone sin escribir y muestra la propuesta agrupada por modelo', async () => {
    panel();
    await propose();
    expect(calls('/catalog-proposal/')).toHaveLength(1);
    expect(String(calls('/catalog-proposal/')[0][0])).toContain('?company=5');
    const body = JSON.parse(String((calls('/catalog-proposal/')[0][1] as RequestInit).body));
    expect(Object.keys(body)).toEqual(['rows']);
    const list = screen.getByRole('list', { name: 'Propuesta por modelo' });
    expect(within(list).getByText('Teléfono Y')).toBeInTheDocument();
    // Nothing is ticked until the person approves it.
    expect(within(list).getByLabelText('Crear la variante Teléfono Y 256 GB Azul Seminuevo')).not.toBeChecked();
    expect(within(list).getByLabelText('Crear el modelo Teléfono Y')).not.toBeChecked();
    expect(screen.getByRole('button', { name: 'Crear lo aprobado' })).toBeDisabled();
    expect(within(list).getByText(/crea la opción 256 GB/)).toBeInTheDocument();
    expect(screen.getByText(/1 modelo\(s\), 1 opción\(es\) y 2 variante\(s\) por crear/)).toBeInTheDocument();
    // The variant that exists is shown, not offered.
    expect(within(list).queryByLabelText('Crear la variante Teléfono X 128 GB Azul Seminuevo')).toBeNull();
    expect(screen.getByRole('list', { name: 'Filas sin propuesta' })).toHaveTextContent('Registro 3: Falta la capacidad');
    expect(calls('/apply/')).toHaveLength(0);
  });

  it('crea sólo lo marcado, con su precio y una clave de idempotencia, y avisa para volver a buscar', async () => {
    const onApplied = panel();
    await propose();
    fireEvent.click(screen.getByLabelText('Crear la variante Teléfono Y 256 GB Azul Seminuevo'));
    fireEvent.change(screen.getByLabelText('Precio de venta de Teléfono Y 256 GB Azul Seminuevo'), { target: { value: '1650' } });
    expect(screen.getByText(/Se crearán 1 modelo\(s\), 1 opción\(es\) y 1 variante\(s\)/)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Sin IMEI: Teléfono Y 256 GB Azul Seminuevo'));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Crear lo aprobado' })); });
    const [url, init] = calls('/apply/')[0] as [string, RequestInit];
    expect(url).toContain('?company=5');
    const body = JSON.parse(String(init.body));
    expect(body.approve).toEqual({ models: ['model:teléfono y'], variants: ['v2'] });
    expect(body.prices).toEqual({ v2: '1650' });
    expect(body.without_imei).toEqual(['v2']);
    expect((init.headers as Record<string, string>)['Idempotency-Key']).toBeTruthy();
    expect(onApplied).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('status')).toHaveTextContent('Quedan ocultas de la web');
  });

  it('«Seleccionar todo» aprueba todo lo que falta y quitarlo deja el botón apagado', async () => {
    panel();
    await propose();
    const all = screen.getByLabelText('Seleccionar todo');
    expect(all).not.toBeChecked();
    expect(screen.getByRole('button', { name: 'Crear lo aprobado' })).toBeDisabled();
    fireEvent.click(all);
    expect(all).toBeChecked();
    expect(screen.getByRole('button', { name: 'Crear lo aprobado' })).toBeEnabled();
    fireEvent.click(all);
    expect(screen.getByRole('button', { name: 'Crear lo aprobado' })).toBeDisabled();
  });

  it('por defecto cada variante exige IMEI: «Sin IMEI» empieza sin marcar y no viaja', async () => {
    panel();
    await propose();
    fireEvent.click(screen.getByLabelText('Seleccionar todo'));
    expect(screen.getByLabelText('Sin IMEI: Teléfono Y 128 GB Negro Seminuevo')).not.toBeChecked();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Crear lo aprobado' })); });
    expect(JSON.parse(String((calls('/apply/')[0][1] as RequestInit).body)).without_imei).toEqual([]);
  });

  it('dice el motivo si el servidor lo rechaza y no avisa de nada creado', async () => {
    const onApplied = panel();
    await propose();
    fireEvent.click(screen.getByLabelText('Seleccionar todo'));
    send.mockImplementationOnce(async () => reply(403, { detail: 'No tienes permiso.' }));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Crear lo aprobado' })); });
    expect(screen.getByRole('alert')).toHaveTextContent('No tienes permiso.');
    expect(onApplied).not.toHaveBeenCalled();
  });

  it('un doble clic en «Crear» pide una sola vez', async () => {
    panel();
    await propose();
    fireEvent.click(screen.getByLabelText('Seleccionar todo'));
    const button = screen.getByRole('button', { name: 'Crear lo aprobado' });
    await act(async () => { fireEvent.click(button); fireEvent.click(button); });
    expect(calls('/apply/')).toHaveLength(1);
  });
});

describe('dentro de la vista previa', () => {
  const MATCH_NONE = { matches: { 1: { status: 'none', reason: 'No existe el modelo.', options: [] } } };

  async function analyse({ manage = true, units = true } = {}) {
    send.mockImplementation(async (url) => {
      const path = String(url);
      if (path.includes('/catalog-proposal/apply/')) return reply(200, APPLIED);
      if (path.includes('/catalog-proposal/')) return reply(200, PROPOSAL);
      if (path.includes('/text-preview/match/')) return reply(200, MATCH_NONE);
      return reply(200, { results: [] });
    });
    render(<InventoryTextPreview canSeeCosts canManageProducts={manage} canReceiveUnits={units} companyId={5} />);
    fireEvent.change(screen.getByLabelText('Origen declarado'), { target: { value: 'own' } });
    fireEvent.change(screen.getByLabelText('Texto para reconocer'), { target: { value: `iPhone 13 128 GB azul\nIMEI ${IMEI}` } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Analizar/ })); });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Buscar en el catálogo' })); });
  }

  it('una fila sin variante ofrece proponer; al crear vuelve a buscar sola', async () => {
    await analyse();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Proponer lo que falta' })); });
    const sent = JSON.parse(String((calls('/catalog-proposal/')[0][1] as RequestInit).body)).rows;
    expect(sent[0]).toMatchObject({ model: 'iPhone 13', capacityGb: 128 });
    expect(JSON.stringify(sent)).not.toContain(IMEI);
    const before = calls('/text-preview/match/').length;
    fireEvent.click(screen.getByLabelText('Seleccionar todo'));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Crear lo aprobado' })); });
    expect(calls('/text-preview/match/').length).toBe(before + 1);
  });

  it('sin permiso no hay botón de proponer', async () => {
    await analyse({ units: false });
    expect(screen.queryByRole('button', { name: 'Proponer lo que falta' })).toBeNull();
  });
});
