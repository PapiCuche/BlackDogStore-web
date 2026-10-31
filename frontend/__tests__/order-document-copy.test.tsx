import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

/**
 * SALES-EMAIL-03 — «Enviar copia a otro correo»: la dirección dos veces, una
 * revisión que dice exactamente a quién y qué documento va, una confirmación
 * explícita y los estados enviando / enviado / error.
 */

const mockOptions = jest.fn();
const mockSend = jest.fn();
jest.mock('@/app/lib/admin', () => {
  const actual = jest.requireActual('@/app/lib/admin');
  return {
    ...actual,
    fetchOrderDocumentCopyOptions: (...args: unknown[]) => mockOptions(...args),
    sendOrderDocumentCopy: (...args: unknown[]) => mockSend(...args),
  };
});

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { OrderDocumentCopyPanel } = require('@/app/admin/components/OrderDocumentCopyPanel');
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { copyAddressProblem } = require('@/app/lib/document-copy');

const ADDRESS = 'contador@empresa-cliente.test';

const CONFIRMATION = {
  kind: 'confirmation', label: 'Correo de confirmación del pedido #42, con la constancia de pedido (PDF)',
  attachment_name: 'tienda-pedido-42.pdf', note: 'Documento interno del pedido. No es un comprobante electrónico.',
  available: true, unavailable_reason: '',
};
const NOTE = {
  kind: 'sales_note', label: 'Nota de venta interna NV-000012 (PDF)', attachment_name: 'tienda-nota-venta-nv-000012.pdf',
  note: 'Documento de control interno, sin validez tributaria. No es un comprobante electrónico.',
  available: true, unavailable_reason: '',
};
const FISCAL_MISSING = {
  kind: 'fiscal', label: 'Comprobante electrónico (PDF)', attachment_name: '', note: '',
  available: false, unavailable_reason: 'Esta venta no tiene comprobante electrónico.',
};

function options(documents = [CONFIRMATION, NOTE, FISCAL_MISSING], extra = {}) {
  return { order_id: 42, can_send: true, blocked_reason: '', documents, ...extra };
}

async function openDialog() {
  render(<OrderDocumentCopyPanel orderId={42} customerEmail="rosa.cliente@correo.test" />);
  fireEvent.click(screen.getByRole('button', { name: 'Enviar copia a otro correo' }));
  const dialog = await screen.findByRole('dialog');
  await within(dialog).findByText('Documento que se envía');
  return dialog;
}

function type(dialog: HTMLElement, to: string, again = to) {
  fireEvent.change(within(dialog).getByLabelText('Correo de destino'), { target: { value: to } });
  fireEvent.change(within(dialog).getByLabelText('Repite el correo de destino'), { target: { value: again } });
}

beforeEach(() => {
  mockOptions.mockReset();
  mockSend.mockReset();
  mockOptions.mockResolvedValue(options());
});

describe('la dirección', () => {
  it.each([
    ['', 'Escribe el correo de destino.'],
    ['ana@empresa.test, otro@empresa.test', 'Escribe una sola dirección de correo, sin nombre, comas ni punto y coma.'],
    ['ana@empresa.test;otro@empresa.test', 'Escribe una sola dirección de correo, sin nombre, comas ni punto y coma.'],
    ['Ana <ana@empresa.test>', 'Escribe una sola dirección de correo, sin nombre, comas ni punto y coma.'],
    ['ana@empresa.test\nBcc: x@fuera.test', 'El correo no puede tener saltos de línea.'],
    ['añá@empresa.test', 'El correo no puede tener espacios, tildes ni caracteres especiales.'],
    ['ana@empresa', 'Escribe un correo válido.'],
    [`${'a'.repeat(65)}@empresa.test`, 'La parte antes de la @ no puede pasar de 64 caracteres.'],
  ])('rechaza %j', (raw, message) => {
    expect(copyAddressProblem(raw, raw)).toBe(message);
  });

  it('exige escribirla dos veces igual, sin importar mayúsculas', () => {
    expect(copyAddressProblem(ADDRESS, 'otro@empresa.test')).toBe('Los dos correos no coinciden.');
    expect(copyAddressProblem(` ${ADDRESS} `, ADDRESS.toUpperCase())).toBeNull();
  });
});

describe('el diálogo', () => {
  it('lista los documentos con su nombre real y explica el que no existe', async () => {
    const dialog = await openDialog();
    expect(within(dialog).getByRole('radio', { name: /Nota de venta interna NV-000012/ })).toBeEnabled();
    const fiscal = within(dialog).getByRole('radio', { name: /Comprobante electrónico/ });
    expect(fiscal).toBeDisabled();
    expect(within(dialog).getByText('Esta venta no tiene comprobante electrónico.')).toBeInTheDocument();
    expect(within(dialog).getByText(/El correo del cliente/)).toHaveTextContent('rosa.cliente@correo.test');
    expect(dialog.textContent?.toLowerCase()).not.toMatch(/boleta|factura/);
  });

  it('no deja revisar una dirección inválida ni dos que no coinciden, y no envía nada', async () => {
    const dialog = await openDialog();
    type(dialog, 'ana@empresa.test, otro@empresa.test');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Revisar envío' }));
    expect(within(dialog).getByRole('alert')).toHaveTextContent('Escribe una sola dirección');
    type(dialog, ADDRESS, 'otro@empresa.test');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Revisar envío' }));
    expect(within(dialog).getByRole('alert')).toHaveTextContent('Los dos correos no coinciden.');
    expect(within(dialog).queryByTestId('document-copy-review')).toBeNull();
    expect(mockSend).not.toHaveBeenCalled();
  });

  it('revisa destinatario exacto y documento, y sólo envía al confirmar', async () => {
    let resolve: (value: unknown) => void = () => {};
    mockSend.mockReturnValue(new Promise((done) => { resolve = done; }));
    const dialog = await openDialog();
    fireEvent.click(within(dialog).getByRole('radio', { name: /Nota de venta interna/ }));
    type(dialog, ` ${ADDRESS} `, ADDRESS);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Revisar envío' }));

    const review = within(dialog).getByTestId('document-copy-review');
    expect(within(review).getByTestId('document-copy-recipient')).toHaveTextContent(ADDRESS);
    expect(review).toHaveTextContent('Nota de venta interna NV-000012 (PDF)');
    expect(review).toHaveTextContent('tienda-nota-venta-nv-000012.pdf');
    expect(review).toHaveTextContent('sin validez tributaria');
    expect(review).toHaveTextContent('Contiene datos personales y comerciales');
    expect(mockSend).not.toHaveBeenCalled();

    fireEvent.click(within(review).getByRole('button', { name: 'Confirmar y enviar' }));
    expect(mockSend).toHaveBeenCalledWith(42, { document: 'sales_note', to: ADDRESS, toConfirmation: ADDRESS });
    expect(within(review).getByRole('button', { name: 'Enviando…' })).toBeDisabled();
    expect(within(review).getByRole('button', { name: 'Volver y corregir' })).toBeDisabled();

    await act(async () => {
      resolve({ detail: 'ok', sent_to: ADDRESS, document: 'sales_note',
        document_label: 'Nota de venta interna NV-000012 (PDF)', attachment_name: 'x.pdf' });
    });
    expect(within(dialog).getByRole('status')).toHaveTextContent(
      `Copia enviada a ${ADDRESS}: Nota de venta interna NV-000012 (PDF).`);
  });

  it('muestra el error del servidor y deja volver a intentarlo', async () => {
    mockSend.mockRejectedValueOnce(new Error('No se pudo generar el PDF del documento. No se envió ningún correo.'));
    const dialog = await openDialog();
    type(dialog, ADDRESS);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Revisar envío' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirmar y enviar' }));
    await waitFor(() => expect(within(dialog).getByRole('alert')).toHaveTextContent('No se envió ningún correo'));
    expect(within(dialog).queryByRole('status')).toBeNull();
    expect(within(dialog).getByRole('button', { name: 'Confirmar y enviar' })).toBeEnabled();
    expect(mockSend).toHaveBeenCalledWith(42, { document: 'confirmation', to: ADDRESS, toConfirmation: ADDRESS });
  });

  it('volver y corregir conserva lo escrito', async () => {
    const dialog = await openDialog();
    type(dialog, ADDRESS);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Revisar envío' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Volver y corregir' }));
    expect(within(dialog).getByLabelText('Correo de destino')).toHaveValue(ADDRESS);
  });

  it('una venta que no se puede enviar lo dice y no ofrece el formulario', async () => {
    mockOptions.mockResolvedValue(options([], { can_send: false, blocked_reason: 'Solo se pueden enviar documentos de ventas pagadas.' }));
    render(<OrderDocumentCopyPanel orderId={42} customerEmail="" />);
    fireEvent.click(screen.getByRole('button', { name: 'Enviar copia a otro correo' }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('ventas pagadas');
    expect(within(dialog).queryByLabelText('Correo de destino')).toBeNull();
  });

  it('Escape cierra mientras no se envía', async () => {
    const dialog = await openDialog();
    expect(dialog).toBeInTheDocument();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
