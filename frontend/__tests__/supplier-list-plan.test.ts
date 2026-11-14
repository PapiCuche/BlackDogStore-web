import { amountChoices, usesDollarSign, type AmountContext } from '@/app/admin/lib/supplier-list-plan';
import type { SupplierAmount } from '@/app/admin/lib/supplier-list-grammar';

/**
 * B1-1 · segunda revisión (N1): un importe escrito con «$» sólo puede ser costo si la moneda de la lista se escribe con
 * «$» (lo dice el código ISO, no una lista de países) y es la de la empresa. Nunca una conversión 1:1 en silencio.
 */

const dollar: SupplierAmount = { key: 'L1-A1:2295@$', raw: '2295', value: '2295', currency: null, dollarSign: true, problem: null, label: null };
const ctx = (change: Partial<AmountContext>): AmountContext => ({
  canSeeCosts: true, canViewCompany: true, companyCurrency: 'PEN', listCurrency: 'PEN', ...change,
});

describe('«$» y la moneda de la lista', () => {
  it('qué monedas se escriben con «$» sale del código, no de una lista', () => {
    expect(usesDollarSign('USD')).toBe(true);
    expect(usesDollarSign('MXN')).toBe(true);
    expect(usesDollarSign('PEN')).toBe(false);
    expect(usesDollarSign('EUR')).toBe(false);
  });

  it('«$2295» en una lista en soles: nunca costo (sería convertir 1:1), sólo referencia o ignorar', () => {
    const choice = amountChoices(dollar, ctx({}));
    expect(choice.allowed).toEqual(['reference', 'ignore']);
    expect(choice.why).toMatch(/«\$»/);
  });

  it('«$2295» en una lista en dólares de una empresa que trabaja en dólares: puede ser costo', () => {
    expect(amountChoices(dollar, ctx({ companyCurrency: 'USD', listCurrency: 'USD' })).allowed).toContain('cost');
  });

  it('«$2295» en una lista en pesos de una empresa en pesos: puede ser costo', () => {
    expect(amountChoices(dollar, ctx({ companyCurrency: 'MXN', listCurrency: 'MXN' })).allowed).toContain('cost');
  });

  it('sin company.view no se sabe la moneda de la empresa: costo bloqueado, y se dice por qué', () => {
    const plain: SupplierAmount = { ...dollar, dollarSign: false, key: 'L1-A1:2295' };
    const choice = amountChoices(plain, ctx({ canViewCompany: false, companyCurrency: null }));
    expect(choice.allowed).toEqual(['reference', 'ignore']);
    expect(choice.why).toMatch(/permiso para ver la configuración de la empresa/);
  });
});
