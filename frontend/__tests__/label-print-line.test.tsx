/** LABEL-04 — after goods come in: «printing on X» or «Enviar a la térmica». */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { NextStepBar, extractLabelBatch } from '@/app/admin/components/NextStepBar';

const mockSend = jest.fn();
jest.mock('@/app/admin/lib/printing-api', () => ({
  ...jest.requireActual('@/app/admin/lib/printing-api'),
  sendLabelBatch: (...a: unknown[]) => mockSend(...a),
}));

const PRODUCT = { id: 7, name: 'Cable USB-C 1 m' };

beforeEach(() => mockSend.mockReset());

describe('extractLabelBatch (LABEL-04)', () => {
  it('reads the id and whether the batch went to the label printer', () => {
    expect(extractLabelBatch({ label_batch: { id: 12, printing: true, printer_name: 'Stickers' } }))
      .toEqual({ count: 1, href: '/admin/inventory/labels', id: 12, printing: true, printerName: 'Stickers' });
  });

  it('keeps an older server answer as it was', () => {
    expect(extractLabelBatch({ label_batch: { id: 12 } }))
      .toEqual({ count: 1, href: '/admin/inventory/labels', id: 12 });
  });
});

describe('LabelPrintLine', () => {
  it('says where the labels are printing when they went on their own', () => {
    render(<NextStepBar product={PRODUCT}
      labelBatch={{ count: 1, href: '/admin/inventory/labels', id: 12, printing: true, printerName: 'Stickers' }} />);
    expect(screen.getByText('Se están imprimiendo en «Stickers».')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Enviar a la térmica' })).toBeNull();
  });

  it('sends them on demand and says where they went', async () => {
    mockSend.mockResolvedValue({ printer_name: 'Stickers' });
    render(<NextStepBar product={PRODUCT}
      labelBatch={{ count: 1, href: '/admin/inventory/labels', id: 12, printing: false }} />);

    fireEvent.click(screen.getByRole('button', { name: 'Enviar a la térmica' }));

    await waitFor(() => expect(screen.getByText('Enviadas a «Stickers».')).toBeInTheDocument());
    expect(mockSend).toHaveBeenCalledWith(12);
    expect(screen.queryByRole('button', { name: 'Enviar a la térmica' })).toBeNull();
  });

  it('shows why it could not send them and lets the person try again', async () => {
    mockSend.mockRejectedValue(new Error('Este local no tiene una impresora de etiquetas.'));
    render(<NextStepBar product={PRODUCT}
      labelBatch={{ count: 1, href: '/admin/inventory/labels', id: 12, printing: false }} />);

    fireEvent.click(screen.getByRole('button', { name: 'Enviar a la térmica' }));

    await waitFor(() => expect(screen.getByRole('alert'))
      .toHaveTextContent('Este local no tiene una impresora de etiquetas.'));
    expect(screen.getByRole('button', { name: 'Enviar a la térmica' })).toBeEnabled();
  });

  it('offers nothing when the branch has no label printer with a roll (the server would refuse it)', () => {
    render(<NextStepBar product={PRODUCT}
      labelBatch={{ count: 1, href: '/admin/inventory/labels', id: 12, printing: false, labelPrinter: false }} />);
    expect(screen.queryByRole('button', { name: 'Enviar a la térmica' })).toBeNull();
  });

  it('says a failed send failed and offers to send it again', () => {
    expect(extractLabelBatch({ label_batch: { id: 12, printing: false, print_status: 'failed', label_printer: true } }))
      .toEqual({ count: 1, href: '/admin/inventory/labels', id: 12, printing: false, printStatus: 'failed',
        labelPrinter: true });
    render(<NextStepBar product={PRODUCT}
      labelBatch={{ count: 1, href: '/admin/inventory/labels', id: 12, printing: false, printStatus: 'failed',
        labelPrinter: true }} />);
    expect(screen.getByRole('alert')).toHaveTextContent('No se pudieron imprimir');
    expect(screen.getByRole('button', { name: 'Enviar a la térmica' })).toBeEnabled();
  });

  it('offers nothing to someone who cannot print labels, or for several batches', () => {
    const { rerender } = render(<NextStepBar product={PRODUCT} showLabels={false}
      labelBatch={{ count: 1, href: '/admin/inventory/labels', id: 12, printing: false }} />);
    expect(screen.queryByRole('button', { name: 'Enviar a la térmica' })).toBeNull();
    rerender(<NextStepBar product={PRODUCT} labelBatch={{ count: 3, href: '/admin/inventory/labels' }} />);
    expect(screen.queryByRole('button', { name: 'Enviar a la térmica' })).toBeNull();
  });
});
