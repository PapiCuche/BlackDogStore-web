import { SOLD_OUT_LABEL, availabilityLabel, isOnOrderVariant } from '@/app/lib/catalog-models';

/**
 * CATALOG-REFERENCE-01 · PR D (D-D-2) — «Bajo pedido» only when a purchasable supplier offer exists (DC3), and a
 * supplier's availability is never said as the shop's own stock. A delivery time is said only when the data has one.
 */

describe('disponibilidad de una variante', () => {
  it('stock propio: «En stock»', () => {
    expect(availabilityLabel({ available: true, availability: 'own_stock' })).toBe('En stock');
  });

  it('proveedor con oferta: «Bajo pedido» con su plazo', () => {
    const variant = { available: true, availability: 'supplier', on_order: { lead_time_days: 3, price: '10.00' } };
    expect(availabilityLabel(variant)).toBe('Bajo pedido — entrega en 3 días');
    expect(isOnOrderVariant(variant)).toBe(true);
  });

  it('proveedor sin oferta comprable: no es «Bajo pedido» ni stock, es «Agotado»', () => {
    for (const on_order of [null, undefined]) {
      const variant = { available: true, availability: 'supplier', on_order };
      expect(availabilityLabel(variant)).toBe(SOLD_OUT_LABEL);
      expect(isOnOrderVariant(variant)).toBe(false);
    }
  });

  it('sin plazo en los datos no se promete ninguno', () => {
    const variant = {
      available: true, availability: 'supplier',
      on_order: { lead_time_days: null as unknown as number, price: '10.00' },
    };
    expect(availabilityLabel(variant)).toBe('Bajo pedido');
  });

  it('lo del proveedor nunca se dice como stock propio', () => {
    for (const on_order of [null, { lead_time_days: 2, price: '1.00' }]) {
      expect(availabilityLabel({ available: true, availability: 'supplier', on_order })).not.toMatch(/stock/i);
    }
  });
});
