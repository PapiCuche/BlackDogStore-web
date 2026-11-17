/**
 * INV-INTAKE-GUARD-01 — una variante de un modelo de equipo empieza «por equipo»; pasarla a «por cantidad» exige leer
 * un aviso; una variante seminueva entra siempre por equipo; el estado del equipo se ofrece según la línea.
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { useState } from 'react';

import { ReceivingLinesEditor, toReceiptProduct } from '@/app/admin/components/ReceivingLinesEditor';
import { receiveHref } from '@/app/admin/lib/receive-links';
import {
  conditionsFor, countsByUnit, locksTracking, newLine, validateLines, type DraftLine, type ReceiptPreview,
  type ReceiptProduct,
} from '@/app/admin/lib/inventory-receipts';
import type { CosmeticGradeRow } from '@/app/admin/lib/internal-api';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));

const newVariant: ReceiptProduct = {
  id: 7, name: 'iPhone 17 256 GB Negro', is_serialized: false, requires_imei: false, can_change_tracking: true,
  condition_line: 'new', product_model_id: 3,
};
const usedVariant: ReceiptProduct = { ...newVariant, id: 9, name: 'iPhone 17 256 GB Seminuevo', condition_line: 'used' };
const stockedVariant: ReceiptProduct = { ...newVariant, id: 10, name: 'iPhone 17 con stock', can_change_tracking: false };
const accessory: ReceiptProduct = {
  id: 8, name: 'Funda', is_serialized: false, requires_imei: false, can_change_tracking: true, condition_line: 'new',
  product_model_id: null,
};

describe('newLine', () => {
  it('una variante de modelo sin stock empieza por equipo, con un equipo nuevo en blanco', () => {
    const line = newLine(newVariant);
    expect(countsByUnit(line)).toBe(true);
    expect(line.units).toHaveLength(1);
    expect(line.units[0].condition).toBe('new');
  });

  it('una variante seminueva empieza por equipo con estado «Usado»', () => {
    const line = newLine(usedVariant);
    expect(countsByUnit(line)).toBe(true);
    expect(line.units[0].condition).toBe('used');
  });

  it('un accesorio (sin modelo) sigue por cantidad', () => {
    const line = newLine(accessory);
    expect(countsByUnit(line)).toBe(false);
    expect(line.quantity).toBe('1');
  });

  it('una variante que ya tiene stock por cantidad no puede cambiar y sigue por cantidad', () => {
    expect(countsByUnit(newLine(stockedVariant))).toBe(false);
  });
});

describe('toReceiptProduct', () => {
  it('lleva el modelo de la variante', () => {
    const base = { id: 1, name: 'X', slug: 'x', price: '0.00', inventory: 0, is_serialized: false };
    expect(toReceiptProduct({ ...base, product_model_id: 4 }).product_model_id).toBe(4);
    expect(toReceiptProduct(base).product_model_id ?? null).toBeNull();
  });
});

describe('conditionsFor', () => {
  const values = (p: ReceiptProduct) => conditionsFor(p).map((c) => c.value);
  it('una variante nueva no ofrece «Usado»; una seminueva no ofrece «Nuevo»', () => {
    expect(values(newVariant)).toEqual(['new', 'open_box', 'refurbished']);
    expect(values(usedVariant)).toEqual(['open_box', 'refurbished', 'used']);
  });
  it('un producto fuera del catálogo de modelos ofrece los cuatro estados', () => {
    expect(values({ ...accessory, is_serialized: true })).toEqual(['new', 'open_box', 'refurbished', 'used']);
  });
  it('conserva el estado ya elegido aunque no corresponda, para que se vea y se corrija', () => {
    expect(conditionsFor(newVariant, 'used').map((c) => c.value)).toContain('used');
  });
});

describe('locksTracking', () => {
  it('reconoce el aviso del servidor por su código', () => {
    const line = { codes: ['quantity_locks_tracking'] } as unknown as ReceiptPreview['lines'][number];
    expect(locksTracking(line)).toBe(true);
    expect(locksTracking({ ...line, codes: [] })).toBe(false);
    expect(locksTracking({ ...line, codes: undefined })).toBe(false);
  });
});

function Harness({ product }: { product: ReceiptProduct }) {
  const [lines, setLines] = useState<DraftLine[]>([newLine(product)]);
  return (
    <ReceivingLinesEditor
      lines={lines} onLines={setLines} canCosts={false} canManageProducts={false}
      problems={null} focus={null} onCreateArticle={() => undefined}
    />
  );
}

describe('ReceivingLinesEditor', () => {
  it('una variante de modelo aparece con «Contar por equipo» ya marcado', () => {
    render(<Harness product={newVariant} />);
    const card = screen.getByRole('article', { name: newVariant.name });
    expect(within(card).getByText('Equipos con serie')).toBeTruthy();
    expect((within(card).getByRole('checkbox', { name: /Contar por equipo/ }) as HTMLInputElement).checked).toBe(true);
  });

  it('desmarcarlo no cambia nada hasta confirmar el aviso de que no se puede deshacer', () => {
    render(<Harness product={newVariant} />);
    const card = screen.getByRole('article', { name: newVariant.name });
    fireEvent.click(within(card).getByRole('checkbox', { name: /Contar por equipo/ }));
    const warning = within(card).getByRole('alert');
    expect(warning.textContent).toMatch(/no se podrá/);
    expect(warning.textContent).toMatch(/stock/);
    // Still by device until the person confirms.
    expect(within(card).getByText('Equipos con serie')).toBeTruthy();
    fireEvent.click(within(warning).getByRole('button', { name: /Mantener por equipo/ }));
    expect(within(card).queryByRole('alert')).toBeNull();
    expect(within(card).getByText('Equipos con serie')).toBeTruthy();

    fireEvent.click(within(card).getByRole('checkbox', { name: /Contar por equipo/ }));
    fireEvent.click(within(within(card).getByRole('alert')).getByRole('button', { name: /recibir por cantidad/ }));
    expect(within(card).getByText('Por cantidad')).toBeTruthy();
    expect((within(card).getByRole('checkbox', { name: /Contar por equipo/ }) as HTMLInputElement).checked).toBe(false);
    // The choice stays said while the line is by quantity.
    expect(within(card).getByRole('note').textContent).toMatch(/no se podrá/);
  });

  it('una variante seminueva no se puede pasar a cantidad', () => {
    render(<Harness product={usedVariant} />);
    const card = screen.getByRole('article', { name: usedVariant.name });
    const box = within(card).getByRole('checkbox', { name: /Contar por equipo/ }) as HTMLInputElement;
    expect(box.checked).toBe(true);
    expect(box.disabled).toBe(true);
    expect(card.textContent).toMatch(/seminuevo entra siempre por equipo/);
  });

  it('un accesorio no ofrece «Contar por equipo» marcado ni muestra el aviso', () => {
    render(<Harness product={accessory} />);
    const card = screen.getByRole('article', { name: accessory.name });
    expect(within(card).getByText('Por cantidad')).toBeTruthy();
    expect(within(card).queryByRole('alert')).toBeNull();
    expect(within(card).queryByRole('note')).toBeNull();
  });

  it('el selector de estado de una variante nueva no ofrece «Usado»', () => {
    render(<Harness product={newVariant} />);
    const select = screen.getByLabelText('Estado del equipo') as HTMLSelectElement;
    const labels = Array.from(select.options).map((o) => o.textContent);
    expect(labels).not.toContain('Usado');
    expect(labels).toContain('Nuevo');
  });
});

describe('receiveHref', () => {
  it('una variante de un modelo de equipo se recibe en el asistente; lo demás, como antes', () => {
    expect(receiveHref({ id: 7, is_serialized: false, product_model_id: 3 })).toBe('/admin/inventory/receive?product=7');
    expect(receiveHref({ id: 7, is_serialized: true, product_model_id: 3 })).toBe('/admin/inventory/receive?product=7');
    expect(receiveHref({ id: 8, is_serialized: true, product_model_id: null })).toBe('/admin/inventory/units?register=1&product=8');
    expect(receiveHref({ id: 9, is_serialized: false })).toBe('/admin/inventory/movements?receive=1&product=9');
  });
});

describe('estado estético en una variante nueva (revisión)', () => {
  const GRADES = [{ id: 5, code: 'excelente', label: 'Excelente' }] as unknown as CosmeticGradeRow[];
  const graded = (): DraftLine => {
    const line = newLine(newVariant);
    return { ...line, units: [{ ...line.units[0], serial_number: 'S1', condition: 'open_box', grade: '5' }] };
  };

  function GradeHarness() {
    const [lines, setLines] = useState<DraftLine[]>([graded()]);
    return (
      <ReceivingLinesEditor
        lines={lines} onLines={setLines} canCosts={false} canManageProducts={false} grades={GRADES}
        problems={validateLines(lines, { costs: false })} focus={null} onCreateArticle={() => undefined}
      />
    );
  }

  it('un estado estético copiado de la lista se ve, se dice y se puede quitar', () => {
    render(<GradeHarness />);
    const select = screen.getByLabelText('Estado estético') as HTMLSelectElement;
    expect(select.value).toBe('5');
    expect(screen.getByText(/variante nueva.*estado estético|estado estético.*seminuevo/)).toBeTruthy();
    fireEvent.change(select, { target: { value: '' } });
    expect(screen.queryByLabelText('Estado estético')).toBeNull();
  });

  it('validateLines lo marca antes de ir al servidor', () => {
    const line = graded();
    expect(validateLines([line], { costs: false })[line.key].units[line.units[0].key].row).toMatch(/estado estético/);
    const clean = { ...line, units: [{ ...line.units[0], grade: '' }] };
    expect(validateLines([clean], { costs: false })[clean.key]).toBeUndefined();
  });
});
