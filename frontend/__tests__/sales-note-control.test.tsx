import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

/**
 * SALES-NOTE-CONTROL-01 — administrar notas de venta emitidas.
 *
 * Lo que se fija:
 *  - la marca ANULADA (y «Prueba» cuando ése fue el motivo) en la lista y en el panel;
 *  - «Anular» sólo existe si el servidor dice `can_void` (el usuario Master) y
 *    la nota sigue vigente;
 *  - el diálogo exige tipo de motivo y motivo (5 caracteres sin contar
 *    espacios), y dice que anular NO cancela el pedido, NO devuelve dinero y NO
 *    es una baja SUNAT;
 *  - los filtros viajan al servidor como vigente/anulada/prueba, fechas,
 *    sucursal y usuario;
 *  - las observaciones se añaden y se muestran como historial; no se editan.
 */

const mockFetchNotes = jest.fn();
const mockFetchDetail = jest.fn();
const mockVoid = jest.fn();
const mockRemark = jest.fn();
jest.mock('@/app/lib/inventory', () => {
  const actual = jest.requireActual('@/app/lib/inventory');
  return {
    ...actual,
    fetchSalesNotes: (...args: unknown[]) => mockFetchNotes(...args),
    fetchSalesNoteDetail: (...args: unknown[]) => mockFetchDetail(...args),
    voidSalesNote: (...args: unknown[]) => mockVoid(...args),
    addSalesNoteRemark: (...args: unknown[]) => mockRemark(...args),
  };
});

/* eslint-disable @typescript-eslint/no-require-imports */
const { buildSalesNoteQuery } = require('@/app/lib/inventory');
const { SalesNoteStatusBadge } = require('@/app/admin/components/sales-notes/SalesNoteStatusBadge');
const { SalesNoteVoidDialog } = require('@/app/admin/components/sales-notes/SalesNoteVoidDialog');
const { SalesNoteRemarks } = require('@/app/admin/components/sales-notes/SalesNoteRemarks');
const { SalesNotesScreen } = require('@/app/admin/components/sales-notes/SalesNotesScreen');
const { SalesNotePanel } = require('@/app/admin/components/SalesNotePanel');
/* eslint-enable @typescript-eslint/no-require-imports */

function note(overrides: Record<string, unknown> = {}) {
  return {
    id: 7,
    order: 42,
    number: 'NV-000001',
    status: 'issued',
    status_label: 'Emitida',
    issued_at: '2026-10-01T15:00:00Z',
    created_at: '2026-10-01T15:00:00Z',
    created_by: 3,
    created_by_username: 'ana.ventas',
    pdf_generated_at: null,
    order_total: '98.00',
    customer_name: 'Rosa Quispe',
    is_void: false,
    voided_at: null,
    voided_by_username: null,
    void_reason_type: '',
    void_reason_type_label: '',
    void_reason: '',
    order_status: 'paid',
    branch_id: 1,
    branch_name: 'Centro',
    ...overrides,
  };
}

const VOID = {
  status: 'void', status_label: 'Anulada', is_void: true, voided_at: '2026-10-14T15:00:00Z',
  voided_by_username: 'master', void_reason_type: 'prueba', void_reason_type_label: 'Prueba',
  void_reason: 'Nota emitida durante las pruebas',
};

function listing(overrides: Record<string, unknown> = {}) {
  return {
    results: [note(), note({ id: 8, number: 'NV-000002', ...VOID })],
    count: 2,
    page: 1,
    page_size: 25,
    can_void: true,
    can_remark: true,
    branches: [{ id: 1, name: 'Centro' }, { id: 2, name: 'Norte' }],
    issuers: [{ id: 3, username: 'ana.ventas' }],
    notice: 'Documento de control interno, sin validez tributaria',
    ...overrides,
  };
}

beforeEach(() => {
  mockFetchNotes.mockReset();
  mockFetchDetail.mockReset();
  mockVoid.mockReset();
  mockRemark.mockReset();
});

describe('buildSalesNoteQuery', () => {
  it('sends only the filters that were chosen', () => {
    expect(buildSalesNoteQuery({})).toBe('');
    expect(buildSalesNoteQuery({
      status: 'prueba', date_from: '2026-10-01', date_to: '2026-10-14', branch: 2, user: 3, page: 2,
    })).toBe('?status=prueba&date_from=2026-10-01&date_to=2026-10-14&branch=2&user=3&page=2');
  });
});

describe('SalesNoteStatusBadge', () => {
  it('marks a void note ANULADA, and says Prueba when that was the reason', () => {
    const { rerender } = render(<SalesNoteStatusBadge note={note()} />);
    expect(screen.getByText('Vigente')).toBeInTheDocument();
    expect(screen.queryByText('ANULADA')).toBeNull();

    rerender(<SalesNoteStatusBadge note={note(VOID)} />);
    expect(screen.getByText('ANULADA')).toBeInTheDocument();
    expect(screen.getByText('Prueba')).toBeInTheDocument();

    rerender(<SalesNoteStatusBadge note={note({ ...VOID, void_reason_type: 'error', void_reason_type_label: 'Error de emisión' })} />);
    expect(screen.getByText('ANULADA')).toBeInTheDocument();
    expect(screen.queryByText('Prueba')).toBeNull();
  });
});

describe('SalesNoteVoidDialog', () => {
  function open() {
    const onVoided = jest.fn();
    const onClose = jest.fn();
    render(<SalesNoteVoidDialog note={note()} onClose={onClose} onVoided={onVoided} />);
    return { onVoided, onClose, dialog: screen.getByRole('dialog') };
  }

  it('says what voiding is not', () => {
    const { dialog } = open();
    expect(dialog).toHaveTextContent('NV-000001');
    expect(dialog).toHaveTextContent(/no cancela el pedido/i);
    expect(dialog).toHaveTextContent(/no devuelve dinero/i);
    expect(dialog).toHaveTextContent(/SUNAT/);
    expect(dialog).toHaveTextContent(/conserva su número/i);
  });

  it('needs a reason type and a reason of at least 5 characters', () => {
    const { dialog } = open();
    const confirm = within(dialog).getByRole('button', { name: 'Anular nota' });
    expect(confirm).toBeDisabled();

    // A valid reason without a type is not enough.
    fireEvent.change(within(dialog).getByLabelText('Motivo'), { target: { value: 'Nota   de prueba' } });
    expect(confirm).toBeDisabled();

    fireEvent.change(within(dialog).getByLabelText('Motivo'), { target: { value: '   abc    ' } });
    fireEvent.click(within(dialog).getByLabelText('Prueba'));
    expect(confirm).toBeDisabled();

    fireEvent.change(within(dialog).getByLabelText('Motivo'), { target: { value: 'Nota   de prueba' } });
    expect(confirm).toBeEnabled();
  });

  it('sends the type and the normalised reason, and hands back the void note', async () => {
    mockVoid.mockResolvedValue(note(VOID));
    const { dialog, onVoided } = open();
    fireEvent.click(within(dialog).getByLabelText('Error de emisión'));
    fireEvent.change(within(dialog).getByLabelText('Motivo'), { target: { value: '  Monto   mal digitado ' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Anular nota' }));

    await waitFor(() => expect(onVoided).toHaveBeenCalled());
    expect(mockVoid).toHaveBeenCalledWith(7, { reason_type: 'error', reason: 'Monto mal digitado' });
  });

  it('shows the server refusal and stays open', async () => {
    mockVoid.mockRejectedValue(new Error('La nota NV-000001 ya está anulada.'));
    const { dialog, onVoided } = open();
    fireEvent.click(within(dialog).getByLabelText('Prueba'));
    fireEvent.change(within(dialog).getByLabelText('Motivo'), { target: { value: 'Nota de prueba' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Anular nota' }));

    expect(await within(dialog).findByRole('alert')).toHaveTextContent('ya está anulada');
    expect(onVoided).not.toHaveBeenCalled();
  });

  it('makes the reason type one tab stop that arrows move through', () => {
    const { dialog } = open();
    const radios = within(dialog).getAllByRole('radio');
    expect(within(dialog).getByRole('radiogroup', { name: 'Tipo de motivo' })).toBeInTheDocument();
    // Nothing chosen: only the first is reachable with Tab.
    expect(radios.map((radio) => radio.getAttribute('tabindex'))).toEqual(['0', '-1', '-1']);

    fireEvent.keyDown(radios[0], { key: 'ArrowRight' });
    expect(radios[1]).toBeChecked();
    expect(radios[1]).toHaveFocus();
    expect(radios.map((radio) => radio.getAttribute('tabindex'))).toEqual(['-1', '0', '-1']);

    fireEvent.keyDown(radios[1], { key: 'ArrowLeft' });
    fireEvent.keyDown(radios[0], { key: 'ArrowLeft' });
    expect(radios[2]).toBeChecked();
  });

  it('closes with Escape', () => {
    const { onClose } = open();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });
});

describe('SalesNoteRemarks', () => {
  const remarks = [
    { id: 1, text: 'Nota de prueba del arranque', author: 'master', created_at: '2026-10-14T15:00:00Z' },
  ];

  it('shows the history and adds a remark without editing the old ones', async () => {
    mockRemark.mockResolvedValue({ id: 2, text: 'Confirmado', author: 'ana.ventas', created_at: '2026-10-14T16:00:00Z' });
    render(<SalesNoteRemarks noteId={7} remarks={remarks} canRemark />);
    expect(screen.getByText('Nota de prueba del arranque')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /editar|borrar|eliminar/i })).toBeNull();

    fireEvent.change(screen.getByLabelText('Nueva observación'), { target: { value: '  Confirmado ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Añadir observación' }));

    expect(await screen.findByText('Confirmado')).toBeInTheDocument();
    expect(mockRemark).toHaveBeenCalledWith(7, 'Confirmado');
    expect(screen.getByText('Nota de prueba del arranque')).toBeInTheDocument();
  });

  it('offers no form to whoever may not add remarks', () => {
    render(<SalesNoteRemarks noteId={7} remarks={[]} canRemark={false} />);
    expect(screen.queryByLabelText('Nueva observación')).toBeNull();
    expect(screen.getByText(/sin observaciones/i)).toBeInTheDocument();
  });
});

describe('SalesNotesScreen', () => {
  it('lists the notes with the ANULADA mark and offers «Anular» only to the master on live notes', async () => {
    mockFetchNotes.mockResolvedValue(listing());
    render(<SalesNotesScreen />);

    const live = await screen.findByTestId('sales-note-row-7');
    const voided = screen.getByTestId('sales-note-row-8');
    expect(within(voided).getByText('ANULADA')).toBeInTheDocument();
    expect(within(live).getByRole('button', { name: 'Anular' })).toBeInTheDocument();
    expect(within(voided).queryByRole('button', { name: 'Anular' })).toBeNull();
  });

  it('hides «Anular» from everyone the server does not name as master', async () => {
    mockFetchNotes.mockResolvedValue(listing({ can_void: false }));
    render(<SalesNotesScreen />);
    await screen.findByTestId('sales-note-row-7');
    expect(screen.queryByRole('button', { name: 'Anular' })).toBeNull();
  });

  it('sends the filters to the server', async () => {
    mockFetchNotes.mockResolvedValue(listing());
    render(<SalesNotesScreen />);
    await screen.findByTestId('sales-note-row-7');

    fireEvent.change(screen.getByLabelText('Estado'), { target: { value: 'prueba' } });
    await waitFor(() => expect(mockFetchNotes).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'prueba', page: 1 })));
    fireEvent.change(screen.getByLabelText('Sucursal'), { target: { value: '2' } });
    await waitFor(() => expect(mockFetchNotes).toHaveBeenLastCalledWith(expect.objectContaining({ branch: 2 })));
    fireEvent.change(screen.getByLabelText('Emitida por'), { target: { value: '3' } });
    await waitFor(() => expect(mockFetchNotes).toHaveBeenLastCalledWith(expect.objectContaining({ user: 3 })));
    fireEvent.change(screen.getByLabelText('Desde'), { target: { value: '2026-10-01' } });
    fireEvent.change(screen.getByLabelText('Hasta'), { target: { value: '2026-10-14' } });
    await waitFor(() => expect(mockFetchNotes).toHaveBeenLastCalledWith(
      expect.objectContaining({ date_from: '2026-10-01', date_to: '2026-10-14' })));
  });

  it('voids through the dialog and shows the note as ANULADA', async () => {
    mockFetchNotes.mockResolvedValue(listing());
    mockVoid.mockResolvedValue({ ...note(VOID), remarks: [], can_void: true, can_remark: true });
    render(<SalesNotesScreen />);
    const live = await screen.findByTestId('sales-note-row-7');

    fireEvent.click(within(live).getByRole('button', { name: 'Anular' }));
    const dialog = screen.getByRole('dialog');
    fireEvent.click(within(dialog).getByLabelText('Prueba'));
    fireEvent.change(within(dialog).getByLabelText('Motivo'), { target: { value: 'Nota de prueba' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Anular nota' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(within(screen.getByTestId('sales-note-row-7')).getByText('ANULADA')).toBeInTheDocument();
  });
});

describe('SalesNotesScreen · filtro «Vigentes»', () => {
  it('drops a note voided while the list shows only live notes', async () => {
    mockFetchNotes.mockResolvedValue(listing({ results: [note()], count: 1 }));
    mockVoid.mockResolvedValue({ ...note(VOID), remarks: [], can_void: true, can_remark: true });
    render(<SalesNotesScreen />);
    await screen.findByTestId('sales-note-row-7');
    fireEvent.change(screen.getByLabelText('Estado'), { target: { value: 'vigente' } });
    await waitFor(() => expect(mockFetchNotes).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'vigente' })));
    const calls = mockFetchNotes.mock.calls.length;

    fireEvent.click(within(await screen.findByTestId('sales-note-row-7')).getByRole('button', { name: 'Anular' }));
    const dialog = screen.getByRole('dialog');
    fireEvent.click(within(dialog).getByLabelText('Prueba'));
    fireEvent.change(within(dialog).getByLabelText('Motivo'), { target: { value: 'Nota de prueba' } });
    mockFetchNotes.mockResolvedValue(listing({ results: [], count: 0 }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Anular nota' }));

    await waitFor(() => expect(screen.queryByTestId('sales-note-row-7')).toBeNull());
    expect(mockFetchNotes.mock.calls.length).toBeGreaterThan(calls);
  });
});

describe('SalesNotePanel (detalle del pedido)', () => {
  it('marks the note of the order ANULADA with its reason', async () => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const inventory = require('@/app/lib/inventory');
    jest.spyOn(inventory, 'fetchSalesNote').mockResolvedValue(note(VOID));
    render(<SalesNotePanel orderId={42} isPaid />);
    expect(await screen.findByText('ANULADA')).toBeInTheDocument();
    expect(screen.getByText(/Nota emitida durante las pruebas/)).toBeInTheDocument();
  });
});
