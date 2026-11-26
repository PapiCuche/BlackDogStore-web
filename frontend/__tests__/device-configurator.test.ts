/**
 * SELECTOR-UX-01 — la lógica del configurador de equipos, compartida por la caja, el asistente de listas de proveedor y
 * «Registrar artículo».
 *
 *   * modo único: una capacidad, un color, una SIM y una región → UNA configuración (lo de la caja, sin cambios);
 *   * modo múltiple: capacidad, SIM y región en común y VARIOS colores → una configuración por color marcado, nunca un
 *     producto cartesiano que nadie declaró; un color que la SIM elegida excluye no desaparece: su tarjeta dice que no
 *     existe.
 */
import {
  chosenCombination, colourAttributeOf, multiOptionState, multiResults, pickCommon, toggleColour, type MultiPick,
} from '@/app/admin/lib/device-configurator';
import * as pos from '@/app/admin/lib/pos-device-picker';
import type { PosModelConfigurations } from '@/app/admin/lib/internal-api';
import { spaceFromPalette } from '@/app/admin/lib/supplier-list-selector';
import { BLACK, BLUE, CAP, CHIP, COL, ESIM, G128, G256, GREEN, PINK, SIM, selectorPalette } from './support/selector-catalog';

const space = spaceFromPalette(selectorPalette(), 'new')!;
const empty: MultiPick = { picked: {}, colours: [] };

describe('modo múltiple', () => {
  it('el paso de color es el del rol color', () => {
    expect(colourAttributeOf(space)?.id).toBe(COL);
  });

  it('un color marcado por persona = una configuración; nada más', () => {
    let pick = pickCommon(space, empty, CAP, G128);
    pick = pickCommon(space, pick, SIM, ESIM);
    pick = toggleColour(toggleColour(pick, BLUE), BLACK);
    const results = multiResults(space, pick);
    expect(results.map((r) => r.colourId)).toEqual([BLACK, BLUE]);
    expect(results.every((r) => r.problem === null && r.combination)).toBe(true);
    expect(results[0].combination?.variant?.product_id).toBe(900);
    expect(results[1].combination?.variant).toBeNull();
    expect(multiResults(space, toggleColour(pick, BLUE)).map((r) => r.colourId)).toEqual([BLACK]);
  });

  it('un paso común sin responder deja cada tarjeta incompleta y dice qué falta', () => {
    const pick = toggleColour(pickCommon(space, empty, CAP, G256), BLUE);
    expect(multiResults(space, pick)).toEqual([
      expect.objectContaining({ colourId: BLUE, problem: 'incomplete', combination: null, missing: ['Tipo de SIM'] }),
    ]);
  });

  it('una combinación excluida (Verde · Chip) es una tarjeta «no existe», nunca se descarta en silencio', () => {
    let pick = pickCommon(space, empty, CAP, G128);
    pick = pickCommon(space, pick, SIM, CHIP);
    pick = toggleColour(toggleColour(pick, GREEN), BLACK);
    expect(multiResults(space, pick).map((r) => [r.colourId, r.problem])).toEqual([[BLACK, null], [GREEN, 'incompatible']]);
  });

  it('lo de después nunca cierra un color: Verde sigue posible con 128 GB aunque Chip lo excluya', () => {
    const pick = pickCommon(space, pickCommon(space, empty, CAP, G128), SIM, CHIP);
    expect(multiOptionState(space, pick, COL, GREEN).possible).toBe(true);
    // A colour outside the palette is never a step's option at all.
    expect(colourAttributeOf(space)?.options.map((o) => o.id)).not.toContain(PINK);
  });

  it('cambiar la capacidad conserva los colores y la SIM mientras sigan existiendo', () => {
    let pick = pickCommon(space, empty, CAP, G128);
    pick = toggleColour(pickCommon(space, pick, SIM, ESIM), BLUE);
    pick = pickCommon(space, pick, CAP, G256);
    expect(pick.colours).toEqual([BLUE]);
    expect(pick.picked[SIM]).toBe(ESIM);
  });
});

describe('modo único: el contrato de la caja no cambia', () => {
  it('pos-device-picker sigue exportando la misma lógica, contando sólo los equipos propios vendibles', () => {
    const config = {
      attributes: space.attributes,
      combinations: space.combinations.map((combo) => ({
        key: combo.key, option_ids: combo.option_ids,
        variant: combo.variant ? { product_id: 900, name: 'V', price: '1.00', available: 3, is_active: true, pending_draft: false,
          sellable: true, is_serialized: true, requires_imei: true, on_order: null } : null,
      })),
    } as unknown as PosModelConfigurations;
    expect(pos.optionState(config, {}, CAP, G128)).toEqual({ possible: true, available: 3 });
    expect(pos.optionState(config, {}, CAP, G256)).toEqual({ possible: true, available: 0 });
    const picked = pos.autoFill(config, pos.pickOption(config, { [CAP]: G128, [COL]: BLACK }, SIM, ESIM));
    expect(chosenCombination(config, picked)).toBe(pos.chosenCombination(config, picked));
    expect(pos.chosenCombination(config, picked)?.variant?.product_id).toBe(900);
  });
});
