/**
 * INV-IMPORT-UX-04 · una importación previsualizada no es una importación aplicada, y la pantalla lo dice.
 *
 * El propietario vio «667 filas · 664 crear · 3 errores · SOLO PREVISUALIZADO» y no supo qué hacer. Aquí:
 * cada estado dice en lenguaje de tienda qué pasó y qué toca hacer; una vista previa vieja nunca se reactiva
 * desde el historial; corregir el Excel no obliga a rehacer columnas ni opciones.
 */
import { fireEvent, render, screen, within } from '@testing-library/react';

import { HistoryTable, PreviewStatus, importState } from '@/app/admin/components/ImportWizard';
import type { ImportJob } from '@/app/admin/lib/internal-api';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));

function job(over: Record<string, unknown> & { counts?: Partial<ImportJob['counts']> } = {}): ImportJob {
  const { counts, ...rest } = over;
  return {
    id: 12, import_type: 'products', status: 'previewed', stock_mode: '', original_filename: 'catalogo.xlsx', file_sha256: 'x',
    mapping: {}, options: {}, summary: {}, created_by: 'ana', applied_by: null, created_at: '2026-11-04T10:00:00Z',
    applied_at: null, is_applicable: false, rows: [], rows_truncated: false,
    counts: { total: 667, create: 664, update: 0, no_change: 0, skip: 0, error: 3, ...counts },
    ...rest,
  } as unknown as ImportJob;
}

describe('estado de una importación', () => {
  it('una vista previa con errores dice que no se importó nada y qué corregir', () => {
    const state = importState(job());
    expect(state.label).toBe('Vista previa con errores · no se importó nada');
    expect(state.sentence).toBe('667 filas analizadas. 664 propuestas para crear. 3 con errores que debes revisar.');
    expect(state.next).toBe('Corrige las 3 filas en tu Excel y vuelve a subirlo.');
  });

  it('una vista previa sin errores tampoco es una importación', () => {
    const state = importState(job({ counts: { error: 0 }, is_applicable: true }));
    expect(state.label).toBe('Vista previa · aún no se ha importado nada');
    expect(state.next).toMatch(/Continúa a la confirmación/);
  });

  it('una aplicada dice qué produjo y quién la aplicó', () => {
    const state = importState(job({
      status: 'applied', applied_by: 'luis', counts: { error: 0 }, summary: { applied: { created: 660, updated: 4 } },
    }));
    expect(state.label).toBe('Aplicada · luis');
    expect(state.sentence).toBe('667 filas analizadas. Se crearon 660 y se actualizaron 4.');
  });
});

describe('historial', () => {
  it('cada fila dice su estado, lo analizado y lo que toca hacer; nunca ofrece «Aplicar» una vista previa vieja', () => {
    render(<HistoryTable jobs={[job(), job({ id: 13, status: 'applied', applied_by: 'luis', counts: { error: 0 }, summary: { applied: { created: 664, updated: 0 } } })]} companyId={7} />);
    const rows = screen.getAllByRole('row').slice(1);
    expect(within(rows[0]).getByText('Vista previa con errores · no se importó nada')).toBeInTheDocument();
    expect(within(rows[0]).getByText(/667 filas analizadas/)).toBeInTheDocument();
    expect(within(rows[0]).getByText('Corrige las 3 filas en tu Excel y vuelve a subirlo.')).toBeInTheDocument();
    expect(within(rows[0]).getByRole('link', { name: /Descargar los 3 errores/ })).toBeInTheDocument();
    expect(within(rows[1]).getByText('Aplicada · luis')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Aplicar/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/Sólo previsualizado/i)).not.toBeInTheDocument();
  });
});

describe('vista previa en pantalla', () => {
  it('con errores: «aún no se ha importado nada», la frase de conteos y la acción principal', () => {
    const onUpload = jest.fn();
    render(<PreviewStatus job={job()} companyId={7} onUploadCorrected={onUpload} />);
    const region = screen.getByRole('region', { name: 'Estado de la importación' });
    expect(region).toHaveTextContent('Vista previa con errores · no se importó nada');
    expect(region).toHaveTextContent('667 filas analizadas. 664 propuestas para crear. 3 con errores que debes revisar.');
    expect(within(region).getByRole('link', { name: 'Ver los 3 errores (descargar)' })).toBeInTheDocument();
    fireEvent.click(within(region).getByRole('button', { name: 'Subir el archivo corregido' }));
    expect(onUpload).toHaveBeenCalled();
  });

  it('sin errores: dice que falta confirmar, sin botón de subir de nuevo como acción principal', () => {
    render(<PreviewStatus job={job({ counts: { error: 0 }, is_applicable: true })} companyId={7} onUploadCorrected={jest.fn()} />);
    expect(screen.getByRole('region', { name: 'Estado de la importación' })).toHaveTextContent('Vista previa · aún no se ha importado nada');
    expect(screen.queryByRole('link', { name: /errores/ })).not.toBeInTheDocument();
  });
});
