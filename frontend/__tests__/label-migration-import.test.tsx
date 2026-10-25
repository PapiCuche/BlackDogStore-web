import { fireEvent, render, screen } from '@testing-library/react';

import {
  HistoryConfirmField,
  HistorySummaryPanel,
  historyClassLabel,
  historyCodesLabel,
  historyQuantitiesLabel,
} from '@/app/admin/components/ImportWizard';
import type { ImportJob } from '@/app/admin/lib/internal-api';

/**
 * LABEL-MIGRATION-01 / 02 · «base histórica».
 *
 * La pantalla sólo muestra lo que el servidor clasificó: cuántas filas hay que
 * revisar, cada código tal cual se guarda y las cantidades anteriores como
 * referencia. Datos ficticios.
 */

const history = {
  version: 1, matched: 2, new: 3, codes_kept: 4, codes_existing: 1, duplicates: 0, conflicts: 1, ambiguous: 0,
  rejected: 0, review: 2, without_code: 1, classes: { exact: 2, conflict: 1, new: 0 },
  legacy_code_as_barcode: false, old_quantity_columns: ['Cantidad anterior - Centro'],
};

function job(): ImportJob {
  return {
    id: 7, import_type: 'products', status: 'previewed', stock_mode: '', original_filename: 'h.xlsx',
    file_sha256: 'x', mapping: {}, options: { history_mode: true },
    counts: { total: 5, create: 3, update: 2, no_change: 0, skip: 0, error: 0 },
    summary: { history } as unknown as ImportJob['summary'],
    created_at: '', applied_at: null, created_by: '', applied_by: '', is_applicable: true,
  } as unknown as ImportJob;
}

const row = {
  action: 'create',
  data: {
    history: {
      classification: 'exact', flags: ['review'],
      codes: [{ field: 'barcode', code: '0750000000011', symbology: 'ean13', status: 'kept' },
              { field: 'code', code: 'abC1', symbology: 'internal', status: 'existing' }],
      old_quantities: [{ column: 'Cantidad anterior - Centro', value: '0' }],
    },
  },
};

test('the summary shows the server counts, the classes and the old quantities as reference only', () => {
  render(<HistorySummaryPanel job={job()} />);
  expect(screen.getByText('A revisar').nextSibling).toHaveTextContent('2');
  expect(screen.getByText('Conflictos').nextSibling).toHaveTextContent('1');
  expect(screen.getByText(/Conflicto:/)).toBeInTheDocument();
  expect(screen.queryByText(/Producto nuevo:/)).not.toBeInTheDocument();
  expect(screen.getByText(/sólo referencia para un recuento/)).toBeInTheDocument();
});

test('codes are shown exactly, and an internal code is never called a GTIN', () => {
  expect(historyCodesLabel(row)).toBe('0750000000011 (EAN-13, se conserva) · abC1 (interno, ya estaba)');
  expect(historyClassLabel(row)).toBe('Coincide · revisar');
  expect(historyQuantitiesLabel(row)).toBe('Cantidad anterior - Centro: 0');
});

test('the confirmation asks for the number of rows to review', () => {
  const onChange = jest.fn();
  const { rerender } = render(<HistoryConfirmField count={2} value="" onChange={onChange} />);
  fireEvent.change(screen.getByLabelText('Número de filas a revisar'), { target: { value: '2' } });
  expect(onChange).toHaveBeenCalledWith('2');
  rerender(<HistoryConfirmField count={0} value="" onChange={onChange} />);
  expect(screen.queryByLabelText('Número de filas a revisar')).not.toBeInTheDocument();
});
