/**
 * SERIAL-PICK-01 — decir QUÉ equipo.
 *
 * Un producto con serie se vende equipo por equipo: alguien lo identifica, con
 * el lector o escribiéndolo a mano cuando el lector no lee. Aquí está lo que el
 * panel hace con eso antes de preguntar al servidor, que es quien decide si ese
 * equipo se puede vender: cómo llegó el código, si un IMEI escrito a mano está
 * bien escrito, y qué lleva una línea de la caja.
 */

export type EntryMethod = "scanner" | "manual";

/** Un equipo ya identificado en una línea de la caja. */
export type PickedUnit = {
  id: number;
  method: EntryMethod;
  serial_number: string;
  imei: string;
  /** INV-COM-01C: su precio propio, cuando se cobra equipo por equipo. Lo fija el servidor; aquí sólo se muestra. */
  price?: string | null;
  battery_health?: number | null;
  cosmetic_grade?: string | null;
  condition_label?: string;
};

/** INV-COM-01C: lo que se le dice a quien cobra cuando un seminuevo no tiene precio. */
export const UNPRICED_UNIT_MESSAGE =
  "Este seminuevo todavía no tiene precio de venta. Solicita que un usuario autorizado lo configure.";

/** Un precio de equipo usable: positivo y con dinero de verdad (el servidor vuelve a decidirlo). */
export const unitPriceValid = (price: string | null | undefined): price is string =>
  typeof price === "string" && /^\d+(\.\d{1,2})?$/.test(price) && Number(price) > 0;

/** DC3 — lo que cuesta y tarda un producto bajo pedido. `null`: ninguna oferta lo cubre. */
export type OnOrderTerms = { lead_time_days: number; price: string };

export type PosLine = {
  product: number;
  name: string;
  price: string;
  quantity: number;
  available: number;
  /** Se vende equipo por equipo: hay que identificar cada uno. */
  isSerialized: boolean;
  requiresImei: boolean;
  units: PickedUnit[];
  /** INV-COM-01C: `unit` = cada equipo se cobra a su precio propio; el `price` de la línea no se usa. */
  pricedBy?: "unit" | "product";
  /**
   * DC3: la línea entera es bajo pedido — al precio de la oferta, sin equipos
   * (no hay ninguno todavía). Una línea es TODA estante o TODA bajo pedido.
   */
  onOrder?: boolean;
  /** DC3: plazo de entrega de la oferta, para decirlo en la línea. */
  leadTimeDays?: number | null;
  /** DC3: la oferta del producto, si la hay: permite pasar la línea a bajo pedido. */
  offer?: OnOrderTerms | null;
  /**
   * Bajo pedido aunque el estante la cubra: por qué (obligatorio para eso, y
   * sólo quien tiene `sales.orders.manage` puede). Viaja recortado como `supply_reason`.
   */
  supplyReason?: string;
};

/** El servidor no guarda más que esto del motivo. */
export const SUPPLY_REASON_MAX = 200;

/** DC3: el servidor rechaza mezclar estante y bajo pedido de un mismo producto; esto lo dice antes. */
export const MIXED_SUPPLY_MESSAGE =
  "Ese producto ya está en la venta desde otro origen: una línea es toda del estante o toda bajo pedido.";

/** Un lector escribe cada carácter en pocos milisegundos; una persona, no. */
const SCANNER_MAX_GAP_MS = 60;
const SCANNER_MIN_KEYS = 6;

/**
 * Cómo llegó un código, por el ritmo de sus teclas.
 *
 * Un lector es un teclado que escribe a ráfaga. Sin teclas que medir —un
 * pegado, un autocompletado— no hay cómo saber que lo leyó una máquina, y se
 * toma por escrito a mano: es el camino que valida y pide confirmar.
 */
export function entryMethod(gapsMs: number[]): EntryMethod {
  if (gapsMs.length < SCANNER_MIN_KEYS) return "manual";
  return gapsMs.every((gap) => gap <= SCANNER_MAX_GAP_MS) ? "scanner" : "manual";
}

/**
 * El ritmo de un campo, atado al texto que tiene.
 *
 * `typed` es lo que las teclas medidas escribieron. Si el campo acaba teniendo
 * otra cosa —un pegado, un retroceso, un vaciado con el ratón— lo medido ya no
 * habla de ese texto y se olvida: un código pegado después de una lectura no
 * hereda el ritmo del lector.
 */
export type KeyRhythm = { typed: string; gaps: number[]; last: number | null };

export function newRhythm(): KeyRhythm {
  return { typed: "", gaps: [], last: null };
}

export function resetRhythm(rhythm: KeyRhythm): void {
  rhythm.typed = "";
  rhythm.gaps = [];
  rhythm.last = null;
}

/** Una tecla pulsada. Sólo cuentan las que escriben un carácter. */
export function rhythmKey(rhythm: KeyRhythm, key: string, now: number): void {
  if (key.length !== 1) return;
  if (rhythm.last !== null) rhythm.gaps.push(now - rhythm.last);
  rhythm.last = now;
  rhythm.typed += key;
}

/** El campo cambió. Si no es lo que las teclas escribieron, lo medido se olvida. */
export function rhythmChanged(rhythm: KeyRhythm, value: string): void {
  if (value !== rhythm.typed) resetRhythm(rhythm);
}

/** Cómo llegó `text`: del lector sólo si lo tecleó entero, a ráfaga. */
export function rhythmMethod(rhythm: KeyRhythm, text: string): EntryMethod {
  return rhythm.typed.trim() === text.trim() ? entryMethod(rhythm.gaps) : "manual";
}

const SEPARATORS = /[\s.\-/]/g;

function luhnDigit(body: string): number {
  let total = 0;
  for (let index = 0; index < body.length; index += 1) {
    let digit = Number(body[body.length - 1 - index]);
    if (index % 2 === 0) {
      digit *= 2;
      if (digit > 9) digit -= 9;
    }
    total += digit;
  }
  return (10 - (total % 10)) % 10;
}

/**
 * Qué le pasa a un IMEI escrito a mano, o `null` si está bien escrito.
 *
 * 15 dígitos, y el último es su dígito de control: un IMEI se comprueba solo,
 * y por eso es lo que se escribe a mano. Que exista y se pueda vender lo dice
 * el servidor; esto sólo evita preguntarle por un número que no puede ser.
 */
export function imeiProblem(value: string): string | null {
  const digits = value.replace(SEPARATORS, "");
  if (!/^\d{15}$/.test(digits)) return "Un IMEI tiene 15 dígitos.";
  if (new Set(digits).size === 1 || luhnDigit(digits.slice(0, 14)) !== Number(digits[14])) {
    return "Ese IMEI no es válido: revisa los dígitos.";
  }
  return null;
}

/**
 * INV-ID-01 — con 14 dígitos exactos, el IMEI completo con su dígito de
 * control (Luhn); en cualquier otro caso `null`. Nunca toca un número de 15
 * dígitos: ése se valida (`imeiProblem`), no se corrige. Luhn sólo dice que el
 * número está bien formado, no que sea de ese equipo.
 */
export function imeiCompletion(value: string): string | null {
  const digits = value.replace(SEPARATORS, "");
  if (!/^\d{14}$/.test(digits) || new Set(digits).size === 1) return null;
  return `${digits}${luhnDigit(digits)}`;
}

/** Lo que el servidor también exige del par: el segundo, distinto y después del primero. */
export function imeiPairProblem(imei: string, imei2: string): string | null {
  const first = imei.replace(SEPARATORS, "");
  const second = imei2.replace(SEPARATORS, "");
  if (!second) return null;
  if (!first) return "Indica primero el IMEI principal.";
  return first === second ? "El segundo IMEI es distinto del primero." : null;
}

/** Una línea con serie está lista cuando tiene un equipo por cada unidad. */
export function lineIsReady(line: PosLine): boolean {
  return line.onOrder === true || !line.isSerialized || line.units.length === line.quantity;
}

/** Lo que cuesta la línea: equipo por equipo si se cobra así; si no, precio × cantidad. */
export function lineTotal(line: PosLine): number {
  if (line.pricedBy === "unit" && !line.onOrder) {
    return line.units.reduce((sum, unit) => sum + (unitPriceValid(unit.price) ? Number(unit.price) : 0), 0);
  }
  return Number(line.price) * line.quantity;
}

/**
 * INV-COM-01C — the price a device shows is the one the server just charged for it. A ficha edited after the device
 * was scanned is corrected from the preview's lines; the same array comes back when nothing differs.
 */
export function withPreviewPrices(lines: PosLine[], preview: { lines: { unit?: number | null; price: string }[] }): PosLine[] {
  const byUnit = new Map<number, string>();
  for (const row of preview.lines) if (row.unit) byUnit.set(row.unit, row.price);
  let changed = false;
  const next = lines.map((line) => {
    if (line.pricedBy !== "unit" || !line.units.some((u) => byUnit.has(u.id) && byUnit.get(u.id) !== u.price)) return line;
    changed = true;
    return { ...line, units: line.units.map((u) => (byUnit.has(u.id) ? { ...u, price: byUnit.get(u.id) ?? u.price } : u)) };
  });
  return changed ? next : lines;
}

/** Cuántos equipos de la cesta se cobran por su precio propio y todavía no lo tienen. */
export function unpricedUnits(lines: PosLine[]): number {
  return lines.reduce(
    (total, line) => total + (line.pricedBy === "unit" && !line.onOrder
      ? line.units.filter((unit) => !unitPriceValid(unit.price)).length : 0),
    0,
  );
}

/** El motivo tal como viaja: recortado y sin pasar del máximo. Vacío: no hay motivo. */
function cleanReason(reason: string | undefined): string {
  return (reason ?? "").trim().slice(0, SUPPLY_REASON_MAX);
}

/** Una línea bajo pedido, con su motivo si lo tiene (igual en la vista previa y en la venta). */
function onOrderItem(line: PosLine) {
  const reason = cleanReason(line.supplyReason);
  return {
    product: line.product, quantity: line.quantity, on_order: true as const,
    ...(reason ? { supply_reason: reason } : {}),
  };
}

/**
 * La línea es bajo pedido aunque el estante de la sucursal cubre su cantidad:
 * el servidor la acepta sólo con un motivo (y a quien puede pedirlo así).
 */
export function needsSupplyReason(line: PosLine): boolean {
  return line.onOrder === true && line.available > 0 && line.available >= line.quantity;
}

/** Cuántas líneas bajo pedido con estante esperan todavía su motivo. */
export function missingSupplyReasons(lines: PosLine[]): number {
  return lines.filter((line) => needsSupplyReason(line) && !cleanReason(line.supplyReason)).length;
}

/** La cesta con el motivo de esa línea (sin pasar del máximo). */
export function withSupplyReason(lines: PosLine[], productId: number, reason: string): PosLine[] {
  return lines.map((line) =>
    line.product === productId ? { ...line, supplyReason: reason.slice(0, SUPPLY_REASON_MAX) } : line,
  );
}

/**
 * Lo que viaja al servidor: de cada equipo, cuál es y cómo se identificó. Una
 * línea bajo pedido viaja con `on_order` y sin equipos, aunque el producto tenga serie.
 */
export function saleItems(lines: PosLine[]) {
  return lines.map((line) =>
    line.onOrder
      ? onOrderItem(line)
      : line.isSerialized
        ? { product: line.product, quantity: line.quantity, units: line.units.map(({ id, method }) => ({ id, method })) }
        : { product: line.product, quantity: line.quantity },
  );
}

/** Lo que se pide para calcular el total: cantidades y, si es el caso, `on_order`. */
export function previewItems(lines: PosLine[]) {
  return lines.map((line) =>
    line.onOrder
      ? onOrderItem(line)
      // INV-COM-01C: un seminuevo se precia por equipo, así que la vista previa también dice cuáles son.
      : line.pricedBy === "unit" && line.units.length
        ? { product: line.product, quantity: line.quantity, units: line.units.map(({ id, method }) => ({ id, method })) }
        : { product: line.product, quantity: line.quantity },
  );
}

export function unitLabel(unit: { imei?: string; serial_number: string }): string {
  return [unit.imei ? `IMEI ${unit.imei}` : "", `Serie ${unit.serial_number}`].filter(Boolean).join(" · ");
}

// ── La cesta de la caja ─────────────────────────────────────────────────────

/** Lo que la caja sabe de un producto al añadirlo. */
export type LineProduct = {
  id: number;
  name: string;
  price: string;
  available: number;
  is_serialized?: boolean;
  requires_imei?: boolean;
  /** DC3: la oferta vigente que lo cubre bajo pedido, o null. */
  on_order?: OnOrderTerms | null;
  /** INV-COM-01C: `unit` = sus equipos se cobran a su precio propio. */
  priced_by?: "unit" | "product";
};

/** El equipo que el servidor describió, tal como llega de una consulta. */
export type FoundUnit = {
  unit_id: number; serial_number: string; imei: string;
  priced_by?: "unit" | "product"; sale_price?: string | null;
  battery_health?: number | null; cosmetic_grade?: string | null; condition_label?: string;
};

function newLine(product: LineProduct): PosLine {
  return {
    product: product.id, name: product.name, price: product.price, quantity: 1,
    available: product.available, isSerialized: Boolean(product.is_serialized),
    requiresImei: Boolean(product.requires_imei), units: [],
    onOrder: false, leadTimeDays: null, offer: product.on_order ?? null,
    pricedBy: product.priced_by === "unit" ? "unit" : "product",
  };
}

/** Si añadir ese producto desde ese origen mezclaría orígenes en la cesta: el motivo, o null. */
export function mixedSupplyProblem(lines: PosLine[], productId: number, onOrder: boolean): string | null {
  const existing = lines.find((line) => line.product === productId);
  if (!existing) return null;
  return Boolean(existing.onOrder) === onOrder ? null : MIXED_SUPPLY_MESSAGE;
}

/**
 * DC3 — la cesta con una unidad más de ese producto BAJO PEDIDO, al precio de
 * su oferta y sin equipos. Lanza si el producto no tiene oferta o si ya está
 * en la venta desde el estante.
 */
export function withOnOrderProduct(lines: PosLine[], product: LineProduct): PosLine[] {
  const offer = product.on_order;
  if (!offer) throw new Error(`${product.name} no se puede vender bajo pedido.`);
  const problem = mixedSupplyProblem(lines, product.id, true);
  if (problem) throw new Error(problem);
  const existing = lines.find((line) => line.product === product.id);
  if (existing) {
    return lines.map((line) => (line.product === product.id
      ? { ...line, quantity: line.quantity + 1, price: offer.price, leadTimeDays: offer.lead_time_days, offer }
      : line));
  }
  return [...lines, {
    ...newLine(product),
    price: offer.price, isSerialized: false, requiresImei: false,
    onOrder: true, leadTimeDays: offer.lead_time_days, offer,
  }];
}

/**
 * DC3 — la línea del estante pasa ENTERA a bajo pedido (el estante no cubre la
 * cantidad). Pierde sus equipos: bajo pedido no hay ninguno todavía.
 */
export function lineToOnOrder(lines: PosLine[], productId: number): PosLine[] {
  return lines.map((line) => {
    if (line.product !== productId || line.onOrder || !line.offer) return line;
    return {
      ...line, price: line.offer.price, isSerialized: false, requiresImei: false, units: [],
      onOrder: true, leadTimeDays: line.offer.lead_time_days,
    };
  });
}

/**
 * La cesta con ese producto añadido.
 *
 * Sin serie suma una unidad. Con serie abre un hueco para un equipo, y no abre
 * otro mientras ese siga vacío: lo que se cuenta son equipos, no pulsaciones.
 */
export function withProduct(lines: PosLine[], product: LineProduct): PosLine[] {
  const existing = lines.find((line) => line.product === product.id);
  if (!existing) return [...lines, newLine(product)];
  // DC3: del estante no se suma a una línea bajo pedido.
  if (existing.onOrder) throw new Error(MIXED_SUPPLY_MESSAGE);
  return lines.map((line) => {
    if (line.product !== product.id) return line;
    const quantity = line.isSerialized
      ? Math.max(line.quantity, line.units.length + 1)
      : line.quantity + 1;
    return { ...line, quantity, available: product.available, offer: product.on_order ?? line.offer ?? null };
  });
}

/** La cesta con ese equipo en la línea de su producto. Lanza si ya estaba. */
export function withUnit(lines: PosLine[], unit: FoundUnit, product: LineProduct, method: EntryMethod): PosLine[] {
  if (lines.some((line) => line.units.some((picked) => picked.id === unit.unit_id))) {
    throw new Error("Ese equipo ya está en esta venta.");
  }
  // DC3: un equipo del estante no entra en una línea bajo pedido.
  if (lines.some((line) => line.product === product.id && line.onOrder)) {
    throw new Error(MIXED_SUPPLY_MESSAGE);
  }
  // INV-COM-01C: un seminuevo sin precio propio no entra en la venta: el servidor tampoco lo cobraría.
  const byUnit = unit.priced_by === "unit" || product.priced_by === "unit";
  if (byUnit && !unitPriceValid(unit.sale_price)) throw new Error(UNPRICED_UNIT_MESSAGE);
  const picked: PickedUnit = {
    id: unit.unit_id, method, serial_number: unit.serial_number, imei: unit.imei,
    ...(byUnit ? {
      price: unit.sale_price ?? null, battery_health: unit.battery_health ?? null,
      cosmetic_grade: unit.cosmetic_grade ?? null, condition_label: unit.condition_label,
    } : {}),
  };
  if (!lines.some((line) => line.product === product.id)) {
    return [...lines, { ...newLine(product), isSerialized: true, units: [picked], ...(byUnit ? { pricedBy: "unit" as const } : {}) }];
  }
  return lines.map((line) => {
    if (line.product !== product.id) return line;
    const units = [...line.units, picked];
    return {
      ...line, isSerialized: true, units,
      quantity: Math.max(line.quantity, units.length), available: product.available,
      ...(byUnit ? { pricedBy: "unit" as const } : {}),
    };
  });
}

/**
 * El equipo, si es del modelo de esa línea. Lanza diciendo qué es si no.
 *
 * El servidor rechaza la venta de todos modos; esto lo dice mientras la persona
 * todavía tiene el equipo en la mano.
 */
export function unitOfLine<T extends { product_id: number; product_name: string }>(unit: T, line: PosLine): T {
  if (unit.product_id !== line.product) {
    throw new Error(`Ese equipo es un ${unit.product_name}, no un ${line.name}.`);
  }
  return unit;
}

/** Quita un equipo: su hueco queda pendiente, para leer el correcto. */
export function withoutUnit(lines: PosLine[], productId: number, unitId: number): PosLine[] {
  return lines.map((line) =>
    line.product === productId ? { ...line, units: line.units.filter((picked) => picked.id !== unitId) } : line,
  );
}

/** Cambia la cantidad de una línea; 0 la quita. Nunca deja más equipos que unidades. */
export function withQuantity(lines: PosLine[], productId: number, quantity: number): PosLine[] {
  if (quantity <= 0) return lines.filter((line) => line.product !== productId);
  return lines.map((line) =>
    line.product === productId ? { ...line, quantity, units: line.units.slice(0, quantity) } : line,
  );
}

/** Cuántos equipos faltan por identificar en toda la cesta. */
export function pendingUnits(lines: PosLine[]): number {
  return lines.reduce(
    (total, line) => total + (line.isSerialized && !line.onOrder ? Math.max(0, line.quantity - line.units.length) : 0), 0,
  );
}
