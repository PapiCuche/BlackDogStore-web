/**
 * D-SCAN-4 (propietario, 2026-11-27) — lo que un lector o un pegado deja por accidente en los BORDES de un código
 * (espacios, tabuladores, CR, LF, NBSP) se quita; lo de DENTRO del código nunca se toca en el navegador (el servidor
 * aplica sus reglas de siempre: `device_identity`).
 *
 * Puertas del navegador que leen varios códigos pegados: «Recibir mercadería» (`receive-bulk`) y las filas de
 * escaneo (`scan-rows`, recepción de un pedido y «Registrar artículo»).
 */
import { parseUnitPaste } from '@/app/admin/lib/receive-bulk';
import { pastedCodes } from '@/app/admin/lib/scan-rows';

const IMEI_A = '356789012345672';
const IMEI_B = '490154203237518';
const NBSP = ' ';

describe('Recibir mercadería · lo pegado', () => {
  it('un CR solo (sin LF) también separa las líneas: dos IMEI en dos líneas son dos equipos', () => {
    const preview = parseUnitPaste(`${IMEI_A}\r${IMEI_B}\r`, []);
    expect(preview.units).toEqual([
      { serial: '', imei: IMEI_A, imei2: '' },
      { serial: '', imei: IMEI_B, imei2: '' },
    ]);
  });

  it('NBSP, tabuladores y CR/LF en los bordes se quitan; los guiones de una serie se conservan', () => {
    const preview = parseUnitPaste(`${NBSP}${IMEI_A}\t\r\n\t F2L-XK1/ABC${NBSP}\r\n`, []);
    expect(preview.units).toEqual([
      { serial: '', imei: IMEI_A, imei2: '' },
      { serial: 'F2L-XK1/ABC', imei: '', imei2: '' },
    ]);
    expect(preview.invalid).toEqual([]);
    expect(preview.rejected).toEqual([]);
  });

  it('el mismo código con y sin espacios en los bordes es un repetido, no dos equipos', () => {
    const preview = parseUnitPaste(`${IMEI_A}\n${NBSP}${IMEI_A}\t`, []);
    expect(preview.units).toHaveLength(1);
    expect(preview.duplicates).toHaveLength(1);
  });
});

describe('filas de escaneo · lo pegado', () => {
  it('cada línea sin sus bordes (NBSP, tabulador, CR), nada de dentro cambia', () => {
    expect(pastedCodes(`${NBSP}A-1 B\t\r\n\tC2${NBSP}\rD3\n`)).toEqual(['A-1 B', 'C2', 'D3']);
  });
});
