/**
 * INV-S5-01 — la primera recepción decide que un producto se cuenta por equipo (decisión S5 del propietario).
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { useState } from 'react';

import { ReceivingLinesEditor, toReceiptProduct } from '@/app/admin/components/ReceivingLinesEditor';
import {
  blankUnit, buildReceiptBody, canChooseUnits, countingPatch, countsByUnit, newLine, validateLines,
  type DraftLine, type ReceiptOrigin, type ReceiptProduct,
} from '@/app/admin/lib/inventory-receipts';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));

const variant: ReceiptProduct = {
  id: 7, name: 'iPhone 17 256 GB Negro', is_serialized: false, requires_imei: false, can_change_tracking: true,
};
const stocked: ReceiptProduct = { ...variant, id: 8, name: 'Cable con stock', can_change_tracking: false };
const phone: ReceiptProduct = { id: 5, name: 'iPhone 15', is_serialized: true, requires_imei: true };
const origin: ReceiptOrigin = { branch: 2, supplier: null, document_ref: '', notes: '' };

describe('reglas de la línea', () => {
  it('sólo ofrece «por equipo» a un producto sin control por serie y sin stock', () => {
    expect(canChooseUnits(variant)).toBe(true);
    expect(canChooseUnits(stocked)).toBe(false);
    expect(canChooseUnits(phone)).toBe(false);
  });

  it('al elegir «por equipo» la línea pide equipos y se envía como «units»', () => {
    const line = { ...newLine(variant), ...countingPatch(newLine(variant), true) } as DraftLine;
    expect(countsByUnit(line)).toBe(true);
    expect(line.units).toHaveLength(1);
    const filled: DraftLine = { ...line, units: [{ ...blankUnit(), serial_number: 'S5A1', imei: '356938035643809' }] };
    const { body } = buildReceiptBody(origin, [filled], { costs: false });
    expect(body.lines[0]).toMatchObject({ kind: 'units', product: 7 });
    expect(validateLines([{ ...line, units: [blankUnit()] }], { costs: false })[line.key].line).toMatch(/al menos un equipo/);
  });

  it('volver a «por cantidad» conserva la cantidad o pone 1', () => {
    const line = { ...newLine(variant), ...countingPatch(newLine(variant), true) } as DraftLine;
    const back = { ...line, ...countingPatch(line, false) } as DraftLine;
    expect(countsByUnit(back)).toBe(false);
    expect(buildReceiptBody(origin, [back], { costs: false }).body.lines[0]).toMatchObject({ kind: 'quantity', quantity: 1 });
  });
});

describe('toReceiptProduct', () => {
  it('sin el dato del servidor, sólo un producto sin serie y sin stock puede cambiar', () => {
    const base = { id: 1, name: 'X', slug: 'x', price: '0.00' };
    expect(toReceiptProduct({ ...base, inventory: 0, is_serialized: false }).can_change_tracking).toBe(true);
    expect(toReceiptProduct({ ...base, inventory: 3, is_serialized: false }).can_change_tracking).toBe(false);
    expect(toReceiptProduct({ ...base, inventory: 0, is_serialized: true }).can_change_tracking).toBe(false);
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
  it('muestra «Contar por equipo» y, al marcarlo, cambia la línea a equipos con serie', () => {
    render(<Harness product={variant} />);
    const card = screen.getByRole('article', { name: variant.name });
    expect(within(card).getByText('Por cantidad')).toBeTruthy();
    fireEvent.click(within(card).getByRole('checkbox', { name: /Contar por equipo/ }));
    expect(within(card).getByText('Equipos con serie')).toBeTruthy();
  });

  it('no lo muestra para un producto con stock ni para uno que ya va por serie', () => {
    const { unmount } = render(<Harness product={stocked} />);
    expect(screen.queryByRole('checkbox', { name: /Contar por equipo/ })).toBeNull();
    unmount();
    render(<Harness product={phone} />);
    expect(screen.queryByRole('checkbox', { name: /Contar por equipo/ })).toBeNull();
  });
});
