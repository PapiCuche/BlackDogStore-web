/**
 * SCAN-TO-STOCK-01 — lo que «Registrar equipo por escaneo» hace con cada lectura (`lib/scan-intake`).
 *
 * El servidor dice qué es un código; aquí se fija a qué casilla va, sin adivinar: un dudoso se pregunta, un código
 * comercial elige la variante, un EID nunca es un IMEI, una lectura repetida no cambia nada, un Enter doble no hace
 * nada y ninguna lectura confirma el ingreso. IMEI sintéticos.
 */
import {
  EMPTY_IDENTIFIERS,
  ReadQueue,
  applyOutcome,
  decide,
  expectedSlot,
  isTerminator,
  reviewProblems,
  type Identifiers,
  type ScanCheck,
  type ScanReading,
} from "@/app/admin/lib/scan-intake";

const IMEI_1 = "350000000000014";
const IMEI_2 = "350000000000022";

function reading(over: Partial<ScanReading>): ScanReading {
  return { value: "", kind: "unknown", certain: false, alternatives: [], hint: "", problem: "", detail: "", ...over };
}

function check(over: Partial<ScanReading>, rest: Partial<ScanCheck> = {}): ScanCheck {
  return { reading: reading(over), product: null, unit: null, conflict: null, serial_elsewhere: "", ...rest };
}

const imeiRead = (value: string, hint = "") => check({ value, kind: "imei", certain: true, hint, detail: "IMEI válido." });
const plan = { requiresImei: true, noImei2: false, noSerial: false };

describe("expectedSlot", () => {
  it("espera IMEI 1, luego IMEI 2, luego la serie", () => {
    expect(expectedSlot(EMPTY_IDENTIFIERS, plan)).toBe("imei");
    expect(expectedSlot({ ...EMPTY_IDENTIFIERS, imei: IMEI_1 }, plan)).toBe("imei2");
    expect(expectedSlot({ imei: IMEI_1, imei2: IMEI_2, serial: "" }, plan)).toBe("serial");
    expect(expectedSlot({ imei: IMEI_1, imei2: IMEI_2, serial: "QZ7K" }, plan)).toBeNull();
  });

  it("salta lo que la persona dijo que el equipo no tiene", () => {
    expect(expectedSlot({ ...EMPTY_IDENTIFIERS, imei: IMEI_1 }, { ...plan, noImei2: true })).toBe("serial");
    expect(expectedSlot({ ...EMPTY_IDENTIFIERS, imei: IMEI_1 }, { ...plan, noImei2: true, noSerial: true })).toBeNull();
  });

  it("sin IMEI (una laptop) espera la serie", () => {
    expect(expectedSlot(EMPTY_IDENTIFIERS, { requiresImei: false, noImei2: false, noSerial: false })).toBe("serial");
  });
});

describe("decide — lecturas consecutivas", () => {
  it("dos IMEI seguidos llenan IMEI 1 y después IMEI 2", () => {
    let ids: Identifiers = EMPTY_IDENTIFIERS;
    const first = decide(imeiRead(IMEI_1), ids);
    expect(first).toMatchObject({ type: "filled", slot: "imei", value: IMEI_1 });
    ids = applyOutcome(ids, first);
    const second = decide(imeiRead(IMEI_2), ids);
    expect(second).toMatchObject({ type: "filled", slot: "imei2", value: IMEI_2 });
    ids = applyOutcome(ids, second);
    const serial = decide(check({ value: "QZ7K2M9XW1", kind: "serial", certain: true }), ids);
    expect(applyOutcome(ids, serial)).toEqual({ imei: IMEI_1, imei2: IMEI_2, serial: "QZ7K2M9XW1" });
  });

  it("una lectura repetida (el lector disparó dos veces) no cambia nada y lo dice", () => {
    const ids = { ...EMPTY_IDENTIFIERS, imei: IMEI_1 };
    const outcome = decide(imeiRead(IMEI_1), ids);
    expect(outcome.type).toBe("repeat");
    expect(applyOutcome(ids, outcome)).toBe(ids);
  });

  it("IMEI 1 igual a IMEI 2 nunca se coloca", () => {
    const ids = { ...EMPTY_IDENTIFIERS, imei: IMEI_1 };
    expect(decide(imeiRead(IMEI_1, "imei2"), ids).type).toBe("repeat");
  });

  it("un tercer IMEI se rechaza", () => {
    expect(decide(imeiRead("350000000000030"), { imei: IMEI_1, imei2: IMEI_2, serial: "" }).type).toBe("rejected");
  });

  it("un IMEI rotulado «IMEI2» va a IMEI 2", () => {
    expect(decide(imeiRead(IMEI_2, "imei2"), EMPTY_IDENTIFIERS)).toMatchObject({ type: "filled", slot: "imei2" });
  });
});

describe("decide — lo que no es un IMEI", () => {
  it("un EID se rechaza sin ofrecer «es el IMEI»", () => {
    const outcome = decide(check({ value: "8".repeat(32), kind: "eid", certain: true, detail: "Es el EID" }), EMPTY_IDENTIFIERS);
    expect(outcome).toMatchObject({ type: "rejected", message: "Es el EID" });
  });

  it("un MEID pregunta, y nunca ofrece IMEI", () => {
    const outcome = decide(
      check({ value: "35000000000001", kind: "meid", alternatives: ["serial"], detail: "Parece un MEID" }), EMPTY_IDENTIFIERS);
    expect(outcome).toMatchObject({ type: "ask", options: ["serial"] });
  });

  it("15 dígitos sin dígito de control válido no llenan el IMEI: se pregunta si es la serie", () => {
    const outcome = decide(
      check({ value: "350000000000015", kind: "unknown", problem: "imei_check_digit", alternatives: ["serial"], detail: "No supera" }),
      EMPTY_IDENTIFIERS);
    expect(outcome.type).toBe("ask");
    expect(applyOutcome(EMPTY_IDENTIFIERS, outcome)).toEqual(EMPTY_IDENTIFIERS);
  });

  it("un código comercial elige la variante y nunca llena una casilla", () => {
    const product = {
      id: 7, name: "Teléfono Q 128", model_id: 3, condition_line: "new", is_serialized: true, requires_imei: true,
      is_active: true, archived: false, option_ids: [1, 2],
    };
    const outcome = decide(check({ value: "194253000001", kind: "gtin", certain: true }, { product }), EMPTY_IDENTIFIERS);
    expect(outcome).toMatchObject({ type: "product", product });
    expect(applyOutcome(EMPTY_IDENTIFIERS, outcome)).toEqual(EMPTY_IDENTIFIERS);
  });

  it("un código comercial sin producto en la empresa se rechaza: nunca es la serie (revisión B2)", () => {
    expect(decide(check({ value: "194253000001", kind: "gtin", certain: true }), EMPTY_IDENTIFIERS).type).toBe("rejected");
  });

  it("15 dígitos con Luhn sin esperar un IMEI preguntan IMEI o serie (revisión B3)", () => {
    const outcome = decide(
      check({ value: IMEI_1, kind: "imei", certain: false, alternatives: ["imei", "serial"], detail: "¿IMEI o serie?" }),
      { imei: "350000000000030", imei2: IMEI_2, serial: "" });
    expect(outcome).toMatchObject({ type: "ask", options: ["imei", "serial"] });
  });

  it("fuera de sus sucursales sólo se dice que existe", () => {
    const unit = { standing: "elsewhere" as const, detail: "Ese identificador ya está registrado en un equipo de otra sucursal" };
    expect(decide(check({ value: IMEI_1, kind: "imei", certain: true }, { unit }), EMPTY_IDENTIFIERS))
      .toEqual({ type: "rejected", message: unit.detail });
  });

  it("una serie que existe en una variante de otra sucursal avisa sin nombrarla", () => {
    const outcome = decide(
      check({ value: "QZ7K2M9XW1", kind: "serial", certain: true }, { serial_elsewhere_hidden: true }), EMPTY_IDENTIFIERS);
    expect(outcome.type === "filled" && outcome.warning).toContain("otra sucursal");
  });

  it("un identificador ya registrado (vendido, de baja o en estante) se rechaza con lo que dijo el servidor", () => {
    const unit = { standing: "sold" as const, status: "sold", status_label: "Vendido", detail: "Es de un equipo vendido" };
    expect(decide(check({ value: IMEI_1, kind: "imei", certain: true }, { unit }), EMPTY_IDENTIFIERS))
      .toEqual({ type: "rejected", message: "Es de un equipo vendido" });
  });

  it("una serie dudosa se pregunta; una cierta llena la serie y avisa si existe en otra variante", () => {
    expect(decide(check({ value: "1234567890", kind: "serial", alternatives: ["serial"] }), EMPTY_IDENTIFIERS).type).toBe("ask");
    const outcome = decide(
      check({ value: "QZ7K2M9XW1", kind: "serial", certain: true }, { serial_elsewhere: "Otra variante" }), EMPTY_IDENTIFIERS);
    expect(outcome).toMatchObject({ type: "filled", slot: "serial" });
    expect(outcome.type === "filled" && outcome.warning).toContain("Otra variante");
  });
});

describe("isTerminator — doble Enter y Tab", () => {
  it("con el campo vacío ninguna tecla termina (un Enter doble no hace nada)", () => {
    expect(isTerminator("Enter", "enter", "")).toBe(false);
    expect(isTerminator("Enter", "enter", "   ")).toBe(false);
  });

  it("Tab con modificadores (Shift+Tab) o con poco escrito deja salir del campo (revisión F9)", () => {
    expect(isTerminator("Tab", "tab", IMEI_1, { shiftKey: true })).toBe(false);
    expect(isTerminator("Tab", "both", IMEI_1, { ctrlKey: true })).toBe(false);
    expect(isTerminator("Tab", "tab", "abc")).toBe(false);
    expect(isTerminator("Enter", "enter", IMEI_1, { metaKey: true })).toBe(false);
    expect(isTerminator("Enter", "enter", "abc")).toBe(true);
  });

  it("Enter o Tab según la configuración", () => {
    expect(isTerminator("Enter", "enter", IMEI_1)).toBe(true);
    expect(isTerminator("Tab", "enter", IMEI_1)).toBe(false);
    expect(isTerminator("Tab", "tab", IMEI_1)).toBe(true);
    expect(isTerminator("Enter", "tab", IMEI_1)).toBe(false);
    expect(isTerminator("Tab", "both", IMEI_1)).toBe(true);
    expect(isTerminator("a", "both", IMEI_1)).toBe(false);
  });
});

describe("ReadQueue", () => {
  it("atiende las lecturas en orden aunque la primera tarde más", async () => {
    const queue = new ReadQueue();
    const seen: string[] = [];
    const slow = () => new Promise<void>((resolve) => setTimeout(() => { seen.push("a"); resolve(); }, 20));
    queue.push(slow);
    await queue.push(async () => { seen.push("b"); });
    expect(seen).toEqual(["a", "b"]);
  });

  it("una lectura que falla no detiene las siguientes", async () => {
    const queue = new ReadQueue();
    const seen: string[] = [];
    queue.push(async () => { throw new Error("red"); }).catch(() => undefined);
    await queue.push(async () => { seen.push("ok"); });
    expect(seen).toEqual(["ok"]);
  });
});

describe("reviewProblems", () => {
  const base = {
    ids: { ...EMPTY_IDENTIFIERS, imei: IMEI_1 }, plan, variantChosen: true, needsPrice: false, price: "", cost: "",
    waitingHere: false, orderChoice: null, stockReason: "",
  };

  it("con variante e IMEI 1 se puede confirmar", () => {
    expect(reviewProblems(base)).toEqual([]);
  });

  it("sin IMEI 1 o sin variante, no", () => {
    expect(reviewProblems({ ...base, ids: EMPTY_IDENTIFIERS })).toContain("Falta el IMEI 1.");
    expect(reviewProblems({ ...base, variantChosen: false })).toContain("Elige el modelo y la variante.");
  });

  it("un pedido pagado que espera aquí se resuelve antes: su pedido, o stock con motivo", () => {
    expect(reviewProblems({ ...base, waitingHere: true }).length).toBe(1);
    expect(reviewProblems({ ...base, waitingHere: true, orderChoice: "stock", stockReason: "no" }).length).toBe(1);
    expect(reviewProblems({ ...base, waitingHere: true, orderChoice: "stock", stockReason: "Cliente desistió" })).toEqual([]);
    expect(reviewProblems({ ...base, waitingHere: true, orderChoice: 12 })).toEqual([]);
  });

  it("una variante nueva sin permiso para crearla no se confirma (revisión F8)", () => {
    expect(reviewProblems({ ...base, createForbidden: true }).join(" ")).toContain("Administrar productos");
  });

  it("una variante nueva necesita precio; la batería es un porcentaje", () => {
    expect(reviewProblems({ ...base, needsPrice: true }).length).toBe(1);
    expect(reviewProblems({ ...base, battery: "120" })).toContain("La batería es un porcentaje entre 0 y 100.");
  });
});
