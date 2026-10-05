import { fireEvent, render, screen, within } from '@testing-library/react';

import { ImageHelp, SheetPicker } from '@/app/admin/components/ImportWizard';
import type { InspectedSheet } from '@/app/admin/lib/internal-api';

/**
 * IMPORT-TEMPLATE-HELP · «¿Cómo preparo las imágenes?»
 *
 * La duda de siempre es si la foto se pega en el Excel. La pantalla lo
 * responde antes de adjuntar nada, con un ejemplo corto, y los límites que
 * muestra son los que el servidor le dijo, no unos escritos aquí.
 */

const RULES = { separator: '|', formats: ['PNG', 'JPG', 'JPEG', 'WEBP'], max_per_product: 12, max_file_mb: 8, max_files: 200, max_total_mb: 100 };

function sheet(overrides: Partial<InspectedSheet>): InspectedSheet {
  return {
    name: 'Productos', header_row: 1, headers: ['Nombre', 'Precio de venta'], sample_rows: 2,
    detected: 'Plantilla de la plataforma', preset: 'products_platform', mapping: { name: 0 },
    warehouse_columns: [], notes: [], signature: 's', profile: null, help: false, ...overrides,
  };
}

test('the explanation is one click away and starts closed', () => {
  render(<ImageHelp rules={null} />);

  const toggle = screen.getByRole('button', { name: '¿Cómo preparo las imágenes?' });
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByText(/no se pegan dentro de Excel/i)).toBeNull();

  fireEvent.click(toggle);

  expect(toggle).toHaveAttribute('aria-expanded', 'true');
  const help = screen.getByRole('region', { name: 'Cómo preparar las imágenes' });
  for (const said of [
    /no se pegan dentro de Excel/i, /sólo los nombres/i, /Imagen principal/, /separados por/,
    /ZIP/, /deben coincidir/i,
  ]) {
    expect(within(help).getAllByText(said).length).toBeGreaterThan(0);
  }
  // Un ejemplo corto, con la forma exacta de las dos celdas.
  expect(within(help).getByText('iphone16-front.webp')).toBeInTheDocument();
  expect(within(help).getByText('iphone16-front.webp|iphone16-back.webp|iphone16-side.webp')).toBeInTheDocument();
});

test('the limits shown are the ones the server reported', () => {
  render(<ImageHelp rules={{ ...RULES, max_per_product: 5, max_file_mb: 3, max_files: 40 }} defaultOpen />);

  const help = screen.getByRole('region', { name: 'Cómo preparar las imágenes' });
  expect(within(help).getByText(/PNG, JPG, JPEG, WEBP/)).toBeInTheDocument();
  expect(within(help).getByText(/5 imágenes por producto/)).toBeInTheDocument();
  expect(within(help).getByText(/3 MB por imagen/)).toBeInTheDocument();
  expect(within(help).getByText(/40 archivos/)).toBeInTheDocument();
});

test('without the server\'s limits it invents none', () => {
  render(<ImageHelp rules={null} defaultOpen />);
  expect(screen.queryByText(/MB por imagen/)).toBeNull();
});

test('the help sheets of the template are shown and cannot be chosen', () => {
  const onChoose = jest.fn();
  const sheets = [
    sheet({}),
    sheet({ name: 'Instrucciones', help: true, detected: '', preset: '', mapping: {} }),
    sheet({ name: 'Ejemplo', help: true, detected: '', preset: '', mapping: {} }),
  ];
  render(<SheetPicker sheets={sheets} selected={sheets[0]} onChoose={onChoose} />);

  const instructions = screen.getByRole('button', { name: /Instrucciones/ });
  expect(instructions).toBeDisabled();
  expect(instructions).toHaveTextContent(/Hoja de ayuda de la plantilla · no se importa/);
  fireEvent.click(instructions);
  expect(onChoose).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: /Productos/ }));
  expect(onChoose).toHaveBeenCalledWith(sheets[0]);
});
