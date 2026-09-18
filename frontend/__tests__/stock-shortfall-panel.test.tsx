import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { AdminOrderDetail, StockShortfallLine } from '@/app/lib/admin';

/**
 * INV-04 · el detalle muestra el faltante y ofrece resolverlo.
 *
 * El botón se ofrece SÓLO cuando el servidor dice que este operador puede
 * (canReprocess). Tras un reintento exitoso el panel usa el detalle recomputado
 * por el servidor — nunca finge cantidades en el cliente.
 */

const mockReprocess = jest.fn();
jest.mock('@/app/lib/admin', () => {
  const actual = jest.requireActual('@/app/lib/admin');
  return { ...actual, reprocessOrderStockExit: (...a: unknown[]) => mockReprocess(...a) };
});

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { StockShortfallPanel } = require('@/app/admin/components/StockShortfallPanel');

const line = (o: Partial<StockShortfallLine> = {}): StockShortfallLine => ({
  product_id: 1, product_name: 'Cable', ordered: 5, fulfilled: 0, shortfall: 5, ...o,
});

const detail = (o: Partial<AdminOrderDetail> = {}): AdminOrderDetail => ({
  id: 7, stock_shortfall: [], can_reprocess_stock_exit: false, created_movements: 0,
  ...o,
} as AdminOrderDetail);

beforeEach(() => {
  mockReprocess.mockReset();
  (window.confirm as unknown) = jest.fn(() => true);
});

describe('StockShortfallPanel', () => {
  it('no muestra nada cuando el pedido está cubierto', () => {
    const { container } = render(
      <StockShortfallPanel orderId={7} shortfall={[]} canReprocess={false} onResolved={jest.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('muestra una línea con pedido, atendido y faltante', () => {
    render(
      <StockShortfallPanel orderId={7} shortfall={[line({ fulfilled: 2, shortfall: 3 })]}
        canReprocess={false} onResolved={jest.fn()} />,
    );
    const section = screen.getByTestId('stock-shortfall-section');
    expect(section).toHaveTextContent('Cable');
    expect(section).toHaveTextContent('Faltante');
    // sin capability, no se ofrece la acción
    expect(screen.queryByTestId('reprocess-stock-exit-button')).toBeNull();
  });

  it('muestra varias líneas', () => {
    render(
      <StockShortfallPanel orderId={7}
        shortfall={[line({ product_id: 1, product_name: 'Cable' }),
                    line({ product_id: 2, product_name: 'Cargador', shortfall: 2 })]}
        canReprocess={false} onResolved={jest.fn()} />,
    );
    const rows = screen.getByTestId('stock-shortfall-section').querySelectorAll('tbody tr');
    expect(rows).toHaveLength(2);
  });

  it('ofrece el botón de reintento sólo con capability', () => {
    render(
      <StockShortfallPanel orderId={7} shortfall={[line()]} canReprocess onResolved={jest.fn()} />,
    );
    expect(screen.getByTestId('reprocess-stock-exit-button')).toBeInTheDocument();
  });

  it('reintenta, resuelve y avisa, entregando el detalle del servidor', async () => {
    const resolved = detail({ stock_shortfall: [], created_movements: 1 });
    mockReprocess.mockResolvedValue(resolved);
    const onResolved = jest.fn();
    render(
      <StockShortfallPanel orderId={7} shortfall={[line()]} canReprocess onResolved={onResolved} />,
    );
    fireEvent.click(screen.getByTestId('reprocess-stock-exit-button'));
    await waitFor(() => expect(onResolved).toHaveBeenCalledWith(resolved));
    expect(mockReprocess).toHaveBeenCalledWith(7);
    expect(screen.getByTestId('reprocess-feedback')).toHaveTextContent('ya no tiene faltante');
  });

  it('si sigue faltando stock, lo dice y no borra el incidente', async () => {
    const stillShort = detail({ stock_shortfall: [line({ fulfilled: 0, shortfall: 5 })], created_movements: 0 });
    mockReprocess.mockResolvedValue(stillShort);
    render(
      <StockShortfallPanel orderId={7} shortfall={[line()]} canReprocess onResolved={jest.fn()} />,
    );
    fireEvent.click(screen.getByTestId('reprocess-stock-exit-button'));
    await waitFor(() => expect(screen.getByTestId('stock-shortfall-section')).toHaveTextContent('todavía faltan'));
  });

  it('muestra el error del servidor y no llama onResolved', async () => {
    mockReprocess.mockRejectedValue(new Error('No tienes permisos para reprocesar la salida de stock.'));
    const onResolved = jest.fn();
    render(
      <StockShortfallPanel orderId={7} shortfall={[line()]} canReprocess onResolved={onResolved} />,
    );
    fireEvent.click(screen.getByTestId('reprocess-stock-exit-button'));
    await waitFor(() =>
      expect(screen.getByTestId('stock-shortfall-section')).toHaveTextContent('No tienes permisos'));
    expect(onResolved).not.toHaveBeenCalled();
  });

  it('deshabilita el botón mientras carga y no admite doble envío', async () => {
    let resolve!: (v: AdminOrderDetail) => void;
    mockReprocess.mockReturnValue(new Promise((r) => { resolve = r; }));
    render(
      <StockShortfallPanel orderId={7} shortfall={[line()]} canReprocess onResolved={jest.fn()} />,
    );
    const btn = screen.getByTestId('reprocess-stock-exit-button');
    fireEvent.click(btn);
    await waitFor(() => expect(btn).toBeDisabled());
    fireEvent.click(btn);  // ignorado mientras carga
    expect(mockReprocess).toHaveBeenCalledTimes(1);
    resolve(detail({ stock_shortfall: [], created_movements: 1 }));
    await waitFor(() => expect(btn).not.toBeDisabled());
  });
});
