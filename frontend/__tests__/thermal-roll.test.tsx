import { render, screen, within } from '@testing-library/react';

import type { ThermalPreview } from '@/app/admin/lib/document-preview-api';
import { ThermalRoll } from '@/app/admin/settings/documents/ThermalRoll';

const roll = (lines: ThermalPreview['lines']): ThermalPreview => ({
  document: 'sales_note', paper_width_mm: 80, columns: 48, dots: 576, dots_per_mm: 8, sample: 'MUESTRA · NO FISCAL', lines,
});

test('el rollo es una figura con su leyenda y un <pre> de 48 columnas con nombre', () => {
  render(<ThermalRoll roll={roll([])} label="Nota" />);
  const figure = screen.getByRole('figure', { name: /rollo de 80 mm, 48 columnas/ });
  const pre = within(figure).getByLabelText('Ticket térmico de muestra: Nota');
  expect(pre.tagName).toBe('PRE');
  expect(pre).toHaveStyle({ width: '48ch' });
  expect(pre).toHaveClass('bg-white', 'text-black');               // papel blanco y tinta negra en los dos temas
});

test('cada renglón conserva alineación, negrita y letra doble; uno vacío ocupa su alto', () => {
  render(<ThermalRoll roll={roll([
    { type: 'text', text: 'Izquierda', align: 'left', bold: false, large: false },
    { type: 'text', text: 'Centro', align: 'center', bold: true, large: false },
    { type: 'text', text: 'TOTAL', align: 'right', bold: true, large: true },
    { type: 'text', text: '', align: 'left', bold: false, large: false },
  ])} label="Nota" />);
  expect(screen.getByText('Izquierda')).toHaveClass('text-left');
  expect(screen.getByText('Izquierda')).not.toHaveClass('font-bold');
  expect(screen.getByText('Centro')).toHaveClass('text-center', 'font-bold');
  expect(screen.getByText('Centro')).not.toHaveClass('text-[2em]');
  expect(screen.getByText('TOTAL')).toHaveClass('text-right', 'font-bold', 'text-[2em]');
  expect(screen.getAllByTestId('thermal-line')[3].textContent).toBe('\u00a0');
});

test('el logo es el mapa de puntos a escala del rollo: 12 puntos por columna', () => {
  render(<ThermalRoll roll={roll([{ type: 'image', align: 'right', width: 192, height: 192, png: 'AAAA' }])} label="Nota" />);
  const logo = screen.getByRole('img', { name: 'Logotipo de 24 × 24 mm (192 × 192 puntos)' });
  expect(logo).toHaveStyle({ width: '16ch' });
  expect(screen.getByTestId('thermal-image')).toHaveClass('text-right');
});

test('el avance del papel deja renglones en blanco y el corte se señala', () => {
  render(<ThermalRoll roll={roll([{ type: 'feed', lines: 3 }, { type: 'qr', align: 'center', payload: 'x' }, { type: 'cut' }])} label="Nota" />);
  const pre = screen.getByLabelText('Ticket térmico de muestra: Nota');
  expect(pre.children).toHaveLength(5);
  expect(screen.getByTestId('thermal-qr')).toHaveTextContent('[Código QR]');
  expect(screen.getByText('corte del papel')).toBeInTheDocument();
});
