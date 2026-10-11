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
};

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
};

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

/** Una línea con serie está lista cuando tiene un equipo por cada unidad. */
export function lineIsReady(line: PosLine): boolean {
  return !line.isSerialized || line.units.length === line.quantity;
}

/** Lo que viaja al servidor: de cada equipo, cuál es y cómo se identificó. */
export function saleItems(lines: PosLine[]) {
  return lines.map((line) =>
    line.isSerialized
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
};

/** El equipo que el servidor describió, tal como llega de una consulta. */
export type FoundUnit = { unit_id: number; serial_number: string; imei: string };

function newLine(product: LineProduct): PosLine {
  return {
    product: product.id, name: product.name, price: product.price, quantity: 1,
    available: product.available, isSerialized: Boolean(product.is_serialized),
    requiresImei: Boolean(product.requires_imei), units: [],
  };
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
  return lines.map((line) => {
    if (line.product !== product.id) return line;
    const quantity = line.isSerialized
      ? Math.max(line.quantity, line.units.length + 1)
      : line.quantity + 1;
    return { ...line, quantity, available: product.available };
  });
}

/** La cesta con ese equipo en la línea de su producto. Lanza si ya estaba. */
export function withUnit(lines: PosLine[], unit: FoundUnit, product: LineProduct, method: EntryMethod): PosLine[] {
  if (lines.some((line) => line.units.some((picked) => picked.id === unit.unit_id))) {
    throw new Error("Ese equipo ya está en esta venta.");
  }
  const picked: PickedUnit = { id: unit.unit_id, method, serial_number: unit.serial_number, imei: unit.imei };
  if (!lines.some((line) => line.product === product.id)) {
    return [...lines, { ...newLine(product), isSerialized: true, units: [picked] }];
  }
  return lines.map((line) => {
    if (line.product !== product.id) return line;
    const units = [...line.units, picked];
    return {
      ...line, isSerialized: true, units,
      quantity: Math.max(line.quantity, units.length), available: product.available,
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
    (total, line) => total + (line.isSerialized ? Math.max(0, line.quantity - line.units.length) : 0), 0,
  );
}
