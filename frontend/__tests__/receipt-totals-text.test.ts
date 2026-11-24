/**
 * STOCK-UX-WALK — lo que la revisión y el cierre de «Recibir mercadería» dicen que entra.
 *
 * Recorrido real (1 equipo nuevo): la revisión decía «Entrarán 0 unidades por cantidad y 1 equipo identificado.
 * Se prepararán 1 etiqueta.» y la línea «Entran 1 equipo identificado». Un cero que no aporta nada y el verbo
 * en plural con uno solo.
 */
import { lineReceiveText, receiptTotalsText } from '@/app/admin/lib/inventory-receipts';

describe('receiptTotalsText', () => {
  it('un solo equipo: sin «0 unidades» y en singular', () => {
    expect(receiptTotalsText({ quantity_units: 0, devices: 1, labels: 1 }, 'future'))
      .toBe('Entrará 1 equipo identificado. Se preparará 1 etiqueta.');
  });

  it('varios equipos sin cantidades: plural y sin el cero', () => {
    expect(receiptTotalsText({ quantity_units: 0, devices: 11, labels: 11 }, 'future'))
      .toBe('Entrarán 11 equipos identificados. Se prepararán 11 etiquetas.');
  });

  it('sólo cantidades: no habla de equipos', () => {
    expect(receiptTotalsText({ quantity_units: 50, devices: 0, labels: 50 }, 'future'))
      .toBe('Entrarán 50 unidades por cantidad. Se prepararán 50 etiquetas.');
    expect(receiptTotalsText({ quantity_units: 1, devices: 0, labels: 0 }, 'future'))
      .toBe('Entrará 1 unidad por cantidad. No se preparan etiquetas.');
  });

  it('las dos cosas: sujeto compuesto, verbo en plural', () => {
    expect(receiptTotalsText({ quantity_units: 1, devices: 1, labels: 2 }, 'future'))
      .toBe('Entrarán 1 unidad por cantidad y 1 equipo identificado. Se prepararán 2 etiquetas.');
  });

  it('en pasado (cierre), sin punto final: la pantalla añade sucursal y proveedor', () => {
    expect(receiptTotalsText({ quantity_units: 0, devices: 1, labels: 1 }, 'past')).toBe('Entró 1 equipo identificado');
    expect(receiptTotalsText({ quantity_units: 3, devices: 1, labels: 4 }, 'past'))
      .toBe('Entraron 3 unidades por cantidad y 1 equipo identificado');
  });
});

describe('lineReceiveText', () => {
  it('concuerda el verbo con la cantidad de la línea', () => {
    expect(lineReceiveText('units', 1)).toBe('Entra 1 equipo identificado.');
    expect(lineReceiveText('units', 2)).toBe('Entran 2 equipos identificados.');
    expect(lineReceiveText('quantity', 1)).toBe('Entra 1 unidad.');
    expect(lineReceiveText('quantity', 5)).toBe('Entran 5 unidades.');
  });
});
