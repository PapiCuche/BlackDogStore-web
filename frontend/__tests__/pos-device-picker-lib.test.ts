/**
 * CATALOG-REFERENCE-01 · PR F — la lógica del selector de equipos de la caja.
 *
 * Modelo → capacidad → color → SIM/región: sólo lo que la paleta admite menos las exclusiones (eso lo dice el
 * servidor en `combinations`); un rol con una sola opción no se pregunta; cada opción dice si es posible con lo ya
 * elegido y cuántos equipos propios hay en esta sucursal; el formulario «Llegó el equipo» se valida antes de enviar.
 */
import {
  autoFill,
  chosenCombination,
  effectivePick,
  optionState,
  pickOption,
  receiveProblems,
  stepsToAsk,
} from '@/app/admin/lib/pos-device-picker';
import type { PosModelConfigurations } from '@/app/admin/lib/internal-api';

const CAP = 1, COL = 2, SIM = 3, REG = 4;
const G128 = 11, G256 = 12, BLACK = 21, BLUE = 22, ESIM = 31, CHIP = 32, JA = 42;

function variant(id: number, available: number, over: Record<string, unknown> = {}) {
  return {
    product_id: id, name: `V${id}`, price: '3000.00', available, is_active: true, pending_draft: false, sellable: true,
    is_serialized: true, requires_imei: true, on_order: null, ...over,
  };
}

const CONFIG: PosModelConfigurations = {
  model: { id: 9, name: 'Teléfono Q' },
  line: 'new',
  has_palette: true,
  attributes: [
    { id: CAP, name: 'Capacidad', role: 'capacity', kind: 'text', varies: true,
      options: [{ id: G128, label: '128 GB', swatch_hex: '' }, { id: G256, label: '256 GB', swatch_hex: '' }] },
    { id: COL, name: 'Color', role: 'color', kind: 'color', varies: true,
      options: [{ id: BLACK, label: 'Negro', swatch_hex: '#111111' }, { id: BLUE, label: 'Azul', swatch_hex: '#1D4ED8' }] },
    { id: SIM, name: 'SIM', role: 'sim', kind: 'text', varies: true,
      options: [{ id: ESIM, label: 'eSIM', swatch_hex: '' }, { id: CHIP, label: 'Chip', swatch_hex: '' }] },
    { id: REG, name: 'Región', role: 'region', kind: 'text', varies: false,
      options: [{ id: JA, label: 'J/A', swatch_hex: '' }] },
  ],
  // 256 GB · Azul · Chip does not exist (excluded on the server: it never arrives here).
  combinations: [
    { key: 'a', option_ids: [G128, BLACK, ESIM, JA], variant: variant(100, 2) },
    { key: 'b', option_ids: [G128, BLACK, CHIP, JA], variant: null },
    { key: 'c', option_ids: [G128, BLUE, ESIM, JA], variant: variant(101, 0) },
    { key: 'd', option_ids: [G128, BLUE, CHIP, JA], variant: null },
    { key: 'e', option_ids: [G256, BLACK, ESIM, JA], variant: variant(102, 1) },
    { key: 'f', option_ids: [G256, BLACK, CHIP, JA], variant: null },
    { key: 'g', option_ids: [G256, BLUE, ESIM, JA], variant: null },
  ],
  truncated: false,
  can: { receive: true, create_variant: true, set_price: true, view_costs: false, choose_supplier: true, open_suppliers: true },
};

describe('los pasos', () => {
  it('pregunta sólo los roles que varían, en el orden de lectura', () => {
    expect(stepsToAsk(CONFIG).map((a) => a.role)).toEqual(['capacity', 'color', 'sim']);
  });

  it('un rol con una sola opción se elige solo', () => {
    expect(effectivePick(CONFIG, {})).toEqual({ [REG]: JA });
  });
});

describe('cada opción', () => {
  it('dice cuántos equipos propios hay con lo ya elegido', () => {
    expect(optionState(CONFIG, {}, CAP, G128)).toEqual({ possible: true, available: 2 });
    expect(optionState(CONFIG, {}, CAP, G256)).toEqual({ possible: true, available: 1 });
    expect(optionState(CONFIG, { [CAP]: G128 }, COL, BLUE)).toEqual({ possible: true, available: 0 });
  });

  it('una combinación excluida no es posible', () => {
    expect(optionState(CONFIG, { [CAP]: G256, [COL]: BLUE }, SIM, CHIP)).toEqual({ possible: false, available: 0 });
    expect(optionState(CONFIG, { [CAP]: G256, [COL]: BLUE }, SIM, ESIM).possible).toBe(true);
  });

  it('lo elegido después nunca cierra una opción de antes', () => {
    expect(optionState(CONFIG, { [CAP]: G256, [COL]: BLUE, [SIM]: ESIM }, CAP, G128).possible).toBe(true);
  });

  it('cambiar un paso borra lo elegido después que ya no encaja', () => {
    const picked = { [CAP]: G128, [COL]: BLUE, [SIM]: CHIP };
    expect(pickOption(CONFIG, picked, CAP, G256)).toEqual({ [CAP]: G256, [COL]: BLUE });
    expect(pickOption(CONFIG, picked, COL, BLACK)).toEqual({ [CAP]: G128, [COL]: BLACK, [SIM]: CHIP });
  });
});

describe('lo que queda con una sola salida', () => {
  it('se elige solo: 256 GB · Azul sólo existe con eSIM', () => {
    expect(autoFill(CONFIG, { [CAP]: G256, [COL]: BLUE })).toEqual({ [CAP]: G256, [COL]: BLUE, [SIM]: ESIM });
    expect(autoFill(CONFIG, { [CAP]: G128 })).toEqual({ [CAP]: G128 });
  });
});

describe('la combinación elegida', () => {
  it('existe sólo cuando cada rol tiene opción', () => {
    expect(chosenCombination(CONFIG, { [CAP]: G128, [COL]: BLACK })).toBeNull();
    expect(chosenCombination(CONFIG, { [CAP]: G128, [COL]: BLACK, [SIM]: ESIM })?.variant?.product_id).toBe(100);
    expect(chosenCombination(CONFIG, { [CAP]: G256, [COL]: BLUE, [SIM]: ESIM })?.variant).toBeNull();
  });
});

describe('«Llegó el equipo»', () => {
  const ok = { imei: '490154203237518', imei2: '', serial: '', price: '3900.00', cost: '' };

  it('un IMEI con su dígito de control pasa; otro no', () => {
    expect(receiveProblems(ok, { needsPrice: true })).toEqual({});
    expect(receiveProblems({ ...ok, imei: '490154203237519' }, { needsPrice: true }).imei).toMatch(/no es válido/);
    expect(receiveProblems({ ...ok, imei: '' }, { needsPrice: true }).imei).toMatch(/IMEI/);
  });

  it('sin IMEI basta la serie (un equipo que no tiene IMEI)', () => {
    expect(receiveProblems({ ...ok, imei: '', serial: 'C02XYZ' }, { needsPrice: true })).toEqual({});
  });

  it('el IMEI 2 es distinto del 1 y también se comprueba', () => {
    expect(receiveProblems({ ...ok, imei2: ok.imei }, { needsPrice: true }).imei2).toMatch(/distinto/);
    expect(receiveProblems({ ...ok, imei2: '123' }, { needsPrice: true }).imei2).toMatch(/15 dígitos/);
  });

  it('una variante nueva necesita precio de venta mayor que cero', () => {
    expect(receiveProblems({ ...ok, price: '' }, { needsPrice: true }).price).toMatch(/precio/);
    expect(receiveProblems({ ...ok, price: '0' }, { needsPrice: true }).price).toMatch(/mayor que cero/);
    expect(receiveProblems({ ...ok, price: '' }, { needsPrice: false })).toEqual({});
  });
});

describe('un modelo sin paleta (revisión de #375)', () => {
  // A = 128 GB · Negro; B = 256 GB · Negro · LL/A. La región no la lleva A: el servidor la marca `varies`.
  const LLA = 41;
  const LOOSE: PosModelConfigurations = {
    ...CONFIG,
    has_palette: false,
    attributes: [
      { ...CONFIG.attributes[0] },
      { ...CONFIG.attributes[1], varies: false, options: [{ id: BLACK, label: 'Negro', swatch_hex: '#111111' }] },
      { id: REG, name: 'Región', role: 'region', kind: 'text', varies: true, options: [{ id: LLA, label: 'LL/A', swatch_hex: '' }] },
    ],
    combinations: [
      { key: 'a', option_ids: [G128, BLACK], variant: variant(200, 1) },
      { key: 'b', option_ids: [G256, BLACK, LLA], variant: variant(201, 1) },
    ],
  };

  it('la variante que no lleva un atributo se puede elegir', () => {
    expect(autoFill(LOOSE, { [CAP]: G128 })).toEqual({ [CAP]: G128 });
    expect(optionState(LOOSE, { [CAP]: G128 }, REG, LLA).possible).toBe(false);
    expect(chosenCombination(LOOSE, { [CAP]: G128 })?.variant?.product_id).toBe(200);
  });

  it('la que sí lo lleva lo pide (o lo elige solo si es la única salida)', () => {
    expect(chosenCombination(LOOSE, { [CAP]: G256 })).toBeNull();
    expect(autoFill(LOOSE, { [CAP]: G256 })).toEqual({ [CAP]: G256, [REG]: LLA });
    expect(chosenCombination(LOOSE, { [CAP]: G256, [REG]: LLA })?.variant?.product_id).toBe(201);
  });
});
