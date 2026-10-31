import { normalizeAmount, withoutAmounts } from "@/app/admin/lib/inventory-text-stages";
import { previewSupplierText, type TextCandidate, type TextPreviewOptions } from "@/app/admin/lib/inventory-text-preview";

import {
  ANDROID_LABELLED, COMMERCIAL_PRICE_ONLY, fakeImei, fakeSerial, IOS_MIXED, longMessage, MANY_ON_ONE_LINE,
} from "./support/whatsapp-lists";

/**
 * INV-TEXT-03 · PR A — listas pegadas desde WhatsApp en «Recibir mercadería». Todo con datos sintéticos
 * (`support/whatsapp-lists.ts`). Lo que el texto no dice con claridad queda vacío y a la vista: un «Precio»
 * sin calificar nunca es costo ni precio de venta, y ni la capacidad, ni la batería, ni un IMEI son importes.
 */

const own = (line: TextPreviewOptions["line"] = "used"): TextPreviewOptions => ({ line, origin: "own" });

function rows(text: string, options = own()): TextCandidate[] {
  return previewSupplierText(text, options).candidates;
}
function one(text: string, options = own()): TextCandidate {
  const all = rows(text, options);
  expect(all).toHaveLength(1);
  return all[0];
}

const money = (row: TextCandidate) => ({
  supplierCost: row.supplierCost, costFrom: row.costFrom, salePrice: row.salePrice, ambiguousAmounts: row.ambiguousAmounts,
});

describe("importes: costo, precio de venta y lo que no se sabe", () => {
  it("«Precio: 2500» sin calificar es ambiguo: ni costo ni precio de venta (el error que vio el propietario)", () => {
    const row = one("iPhone 12 64gb blanco\nPrecio: 2500");
    expect(money(row)).toEqual({ supplierCost: "", costFrom: "", salePrice: "", ambiguousAmounts: ["2500"] });
    expect(row.warnings.join(" ")).toMatch(/no se sabe si es costo o precio de venta/);
  });

  it.each([
    ["Costo: 1200", "1200"], ["costo 1200", "1200"], ["Costo de compra: S/ 1,200.00", "1200.00"],
    ["costo proveedor 1200", "1200"], ["Precio de compra: 1200", "1200"], ["Precio proveedor S/1200", "1200"],
  ])("«%s» es candidato a costo", (label, value) => {
    expect(money(one(`iPhone 11 128 negro\n${label}`))).toEqual({ supplierCost: value, costFrom: "label", salePrice: "", ambiguousAmounts: [] });
  });

  it.each([
    ["Precio de venta: 1650", "1650"], ["precio venta 1650", "1650"], ["Precio público S/ 1,650", "1650"],
    ["Precio final al cliente: 1650", "1650"], ["Precio al cliente 1650", "1650"], ["PVP 1650", "1650"],
  ])("«%s» es candidato a precio de venta, nunca costo", (label, value) => {
    expect(money(one(`iPhone 11 128 negro\n${label}`))).toEqual({ supplierCost: "", costFrom: "", salePrice: value, ambiguousAmounts: [] });
  });

  it("costo y precio de venta en la misma línea, cada uno a su sitio; nada se calcula del otro", () => {
    const row = one("iPhone 14 Pro 128GB morado sem batería 90% estado A precio venta 2900 costo 2400");
    expect(money(row)).toEqual({ supplierCost: "2400", costFrom: "label", salePrice: "2900", ambiguousAmounts: [] });
    expect(row).toMatchObject({ model: "iPhone 14 Pro", capacityGb: 128, colour: "morado", batteryPercent: 90, statedLine: "used", gradeText: "A" });
  });

  it("sólo un precio de venta: el costo queda vacío (no se deduce margen ni se copia)", () => {
    const row = one("iPhone 11 128 negro\nPrecio de venta: 1650", { line: "used", origin: "supplier" });
    expect(row.supplierCost).toBe("");
    expect(row.salePrice).toBe("1650");
  });

  it("una lista comercial con «Precio:» no deja costo en ningún flujo", () => {
    for (const origin of ["own", "supplier"] as const) {
      const row = one(COMMERCIAL_PRICE_ONLY, { line: "used", origin });
      expect(money(row)).toEqual({ supplierCost: "", costFrom: "", salePrice: "", ambiguousAmounts: ["3299"] });
    }
  });

  it("el único importe sin etiqueta de una lista de proveedor sigue siendo candidato a costo, marcado como deducido", () => {
    const row = one("IPH 15 128 2300 negro", own("new"));
    expect(money(row)).toEqual({ supplierCost: "2300", costFrom: "lone", salePrice: "", ambiguousAmounts: [] });
  });

  it("dos importes sin etiqueta: ninguno se elige", () => {
    const row = one("IPH 15 128gb 2300 2400 negro", own("new"));
    expect(money(row)).toEqual({ supplierCost: "", costFrom: "", salePrice: "", ambiguousAmounts: ["2300", "2400"] });
    expect(row.warnings.join(" ")).toContain("2300, 2400");
  });

  it("un costo indicado más otro importe suelto: el suelto queda por revisar", () => {
    const row = one("iPhone 13 128 azul 1850 costo 1500");
    expect(money(row)).toEqual({ supplierCost: "1500", costFrom: "label", salePrice: "", ambiguousAmounts: ["1850"] });
  });

  it("un importe en dólares no es un costo en soles: no se asigna y se avisa", () => {
    const row = one("iPhone 13 128 azul $ 450");
    expect(money(row)).toEqual({ supplierCost: "", costFrom: "", salePrice: "", ambiguousAmounts: [] });
    expect(row.warnings.join(" ")).toMatch(/dólares/);
  });

  it.each([
    ["S/ 2,500.00", "2500.00"], ["S/2500", "2500"], ["S/. 2500", "2500"], ["2.500", "2500"], ["2,500", "2500"],
    ["2500 soles", "2500"], ["2.500,00", "2500.00"], ["S/3.299", "3299"], ["1850.5", "1850.50"],
  ])("lee el importe «%s» como %s", (written, value) => {
    expect(one(`iPhone 13 128 azul ${written}`).supplierCost).toBe(value);
  });

  it("normaliza importes sin inventar decimales y rechaza lo que no es un importe", () => {
    expect(["2500", "2,500", "2.500", "4,250.00", "2.500,00", "12,50", "12.5", "1,5"].map(normalizeAmount))
      .toEqual(["2500", "2500", "2500", "4250.00", "2500.00", "12.50", "12.50", "1.50"]);
    expect(["", "abc", "12345678", "2,50,0", "1.2.3"].map(normalizeAmount)).toEqual([null, null, null, null, null]);
  });

  it("ni la capacidad, ni la batería, ni un IMEI, ni la cantidad se leen como importe", () => {
    const imei = fakeImei(10);
    const row = one(`iPhone 13 128GB azul 89% x1 ${imei} S/ 1,850`);
    expect(money(row)).toEqual({ supplierCost: "1850", costFrom: "lone", salePrice: "", ambiguousAmounts: [] });
    expect(row).toMatchObject({ capacityGb: 128, batteryPercent: 89, statedQuantity: 1, imei });
  });

  it("los avisos sin permiso de costos no llevan importes", () => {
    const row = one("iPhone 12 64gb blanco\nPrecio: 2500\n");
    expect(row.warnings.map(withoutAmounts).join(" ")).not.toMatch(/2500/);
    expect(withoutAmounts("Costos ambiguos (2300, 2400): indicar cuál es el costo.")).toBe("Costos ambiguos: indicar cuál es el costo.");
    expect(withoutAmounts("Importe 50 demasiado bajo para un equipo: revisar si es el costo.")).toBe("Importe demasiado bajo para un equipo: revisar si es el costo.");
  });
});

describe("WhatsApp: prefijos, emojis, viñetas, abreviaturas y separadores", () => {
  it("una lista mezclada de iOS: nuevo, seminuevos y accesorios, cada campo en su sitio", () => {
    const { candidates, unrecognized } = previewSupplierText(IOS_MIXED, own("used"));
    expect(candidates.map((r) => ({
      model: r.model, kind: r.kind, capacityGb: r.capacityGb, colour: r.colour, battery: r.batteryPercent,
      cost: r.supplierCost, stated: r.statedLine, quantity: r.statedQuantity,
    }))).toEqual([
      { model: "iPhone 15 Pro", kind: "device", capacityGb: 256, colour: "titanio natural", battery: null, cost: "4250.00", stated: "new", quantity: null },
      { model: "iPhone 14", kind: "device", capacityGb: 128, colour: "negro", battery: 89, cost: "2500", stated: "used", quantity: null },
      { model: "iPhone 13", kind: "device", capacityGb: 128, colour: "azul", battery: 87, cost: "1850", stated: "used", quantity: null },
      { model: "Cable USB-C", kind: "accessory", capacityGb: null, colour: "", battery: null, cost: "25", stated: "", quantity: 5 },
      { model: "Cargador 20W", kind: "accessory", capacityGb: null, colour: "", battery: null, cost: "45", stated: "", quantity: 2 },
      { model: "AirPods Pro 2", kind: "accessory", capacityGb: null, colour: "", battery: null, cost: "750", stated: "", quantity: 2 },
    ]);
    expect(unrecognized.map((u) => u.reason)).toEqual(["Texto sin producto", "Texto sin producto"]);
  });

  it("mensajes de Android de varias líneas: cada campo etiquetado, IMEI 1/2 y serie con y sin dos puntos", () => {
    const [first, second] = rows(ANDROID_LABELLED);
    expect(first).toMatchObject({
      model: "iPhone 12", capacityGb: 64, colour: "blanco", batteryPercent: 85, imei: fakeImei(1), serial: fakeSerial(1),
      supplierCost: "", salePrice: "", ambiguousAmounts: ["2500"],
    });
    expect(second).toMatchObject({
      model: "iPhone 11", capacityGb: 128, colour: "negro", imei: fakeImei(2), imei2: fakeImei(3), serial: fakeSerial(2),
      supplierCost: "1200", salePrice: "1650", gradeText: "B", ambiguousAmounts: [],
    });
  });

  it("varios equipos en una línea, separados por coma o barra", () => {
    expect(rows(MANY_ON_ONE_LINE).map((r) => [r.model, r.capacityGb, r.colour, r.supplierCost])).toEqual([
      ["iPhone 13", 128, "azul", "1800"], ["iPhone 12", 64, "negro", "1300"], ["iPhone 11", 64, "blanco", "950"],
    ]);
    // A case for another phone is ONE accessory, not a case plus a phone.
    expect(rows("Funda iPhone 15, iPhone 14 transparente 35").map((r) => r.kind)).toEqual(["accessory"]);
  });

  it.each([
    ["Cable USB-C x5", 5], ["Cable USB-C x 5", 5], ["Cable USB-C ×5", 5], ["Cable USB-C 5 unidades", 5],
    ["Cable USB-C 5 und", 5], ["Cable USB-C 5 uds.", 5], ["Cable USB-C cantidad: 5", 5], ["5x Cable USB-C", 5],
    ["Cable USB-C", null],
  ])("cantidad de «%s»", (text, quantity) => {
    const row = one(text, own("accessory"));
    expect(row.model).toBe("Cable USB-C");
    expect(row.statedQuantity).toBe(quantity);
    // The offer flow's contract: a confirmed quantity is never read from a text.
    expect(row.quantity).toBeNull();
  });

  it("«iPhone X 64GB» no es una cantidad de 64", () => {
    const row = one("iPhone X 64GB plata");
    expect(row).toMatchObject({ statedQuantity: null, capacityGb: 64 });
  });

  it("cantidades distintas en la misma fila: no se elige ninguna", () => {
    const row = one("Cable USB-C x2 3 unidades", own("accessory"));
    expect(row.statedQuantity).toBeNull();
    expect(row.warnings.join(" ")).toMatch(/Cantidades distintas/);
  });

  it.each([
    ["iPhone 13 128 azul nvo", "new"], ["iPhone 13 128 azul NUEVO sellado", "new"], ["iPhone 13 128 azul sem", "used"],
    ["iPhone 13 128 azul semi nuevo", "used"], ["iPhone 13 128 azul usado", "used"], ["iPhone 13 128 azul como nuevo", "used"],
    ["iPhone 13 128 azul", ""],
  ])("la línea escrita en «%s»: %s", (text, stated) => {
    const row = one(text);
    expect(row.statedLine).toBe(stated);
    expect(row.model).toBe("iPhone 13");
  });

  it("dice nuevo y seminuevo a la vez: no se decide", () => {
    const row = one("iPhone 13 128 azul nuevo seminuevo");
    expect(row.statedLine).toBe("");
    expect(row.warnings.join(" ")).toMatch(/nuevo y seminuevo/);
  });

  it.each([
    ["bat 89%", 89], ["Bat: 89%", 89], ["batería 89", 89], ["Batería: 89 %", 89], ["89%", 89], ["salud de batería 89%", 89],
  ])("batería «%s»", (text, battery) => {
    const row = one(`iPhone 13 128 azul ${text}`);
    expect(row.batteryPercent).toBe(battery);
    expect(row.model).toBe("iPhone 13");
  });

  it("la batería en su propia línea («Bat: 85%») es un campo del equipo, no una línea perdida", () => {
    const preview = previewSupplierText("iPhone 13 128 azul\nBat: 85%", own());
    expect(preview.candidates[0].batteryPercent).toBe(85);
    expect(preview.unrecognized).toEqual([]);
  });

  it.each([["estado A", "A"], ["Estado: A+", "A+"], ["grado B", "B"], ["9/10", "9/10"], ["estado excelente", "excelente"]])(
    "estado estético «%s»", (text, grade) => {
      const row = one(`iPhone 13 128 azul ${text}`);
      expect(row.gradeText).toBe(grade);
      expect(row.ramGb).toBeNull();
      expect(row.model).toBe("iPhone 13");
    });

  it("RAM y almacenamiento: «8/256» y «8GB RAM 256GB»; «9/10» no es RAM", () => {
    expect(one("Galaxy S24 8/256 negro")).toMatchObject({ ramGb: 8, capacityGb: 256 });
    expect(one("Galaxy S24 8GB RAM 256GB negro")).toMatchObject({ ramGb: 8, capacityGb: 256, model: "Galaxy S24" });
  });

  it("notas: lo que va entre paréntesis y «con caja», fuera del modelo", () => {
    const row = one("iPhone 13 128 azul con caja (detalle en el marco) 1850");
    expect(row.model).toBe("iPhone 13");
    expect(row.notes).toBe("con caja; detalle en el marco");
    const field = one("iPhone 13 128 azul\nObs: pantalla cambiada");
    expect(field.notes).toBe("pantalla cambiada");
  });

  it("una nota con un IMEI o una serie no se toma como nota (el identificador no se copia)", () => {
    const row = one(`iPhone 13 128 azul (IMEI ${fakeImei(5)})`);
    expect(row.notes).toBe("");
    expect(row.imei).toBe(fakeImei(5));
  });

  it("un IMEI sin etiqueta que pasa Luhn se toma y se marca para revisar; uno que no pasa no", () => {
    const row = one(`iPhone 13 128 azul ${fakeImei(7)}`);
    expect(row.imei).toBe(fakeImei(7));
    expect(row.imeiFrom).toBe("bare");
    const good = fakeImei(7);
    const bad = one(`iPhone 13 128 azul ${good.slice(0, 14)}${(Number(good[14]) + 1) % 10}`);
    expect(bad.imei).toBe("");
    expect(bad.model).toBe("iPhone 13");
  });

  it("separadores irregulares y mayúsculas no ensucian el nombre", () => {
    const row = one("IPHONE 15 PRO MAX | 256 GB | Titanio Azul | S/ 5,499");
    expect(row).toMatchObject({ model: "iPhone 15 Pro Max", capacityGb: 256, colour: "titanio azul", supplierCost: "5499" });
    expect(one("*iPhone 16* 128gb rosa ➡️ S/3.299")).toMatchObject({ model: "iPhone 16", colour: "rosa", supplierCost: "3299" });
  });

  it("varias capacidades o varios colores en una línea: no se elige uno", () => {
    const capacities = one("iPhone 15 128GB 256GB negro");
    expect(capacities.capacityGb).toBeNull();
    expect(capacities.warnings.join(" ")).toMatch(/Varias capacidades/);
    const colours = one("iPhone 15 128GB negro azul");
    expect(colours.colour).toBe("");
    expect(colours.warnings.join(" ")).toMatch(/Varios colores/);
  });

  it("filas sin datos y datos inválidos: vacíos y con aviso, nada inventado", () => {
    const row = one("iPhone 13\nIMEI: 123456789012345\nBat: 140%");
    expect(row).toMatchObject({ capacityGb: null, colour: "", supplierCost: "", salePrice: "" });
    expect(row.warnings.join(" ")).toMatch(/Luhn/);
    expect(row.warnings.join(" ")).toMatch(/Batería fuera de rango/);
  });

  it("un mensaje largo (~300 líneas) se lee entero, rápido y sin mezclar filas", () => {
    const started = Date.now();
    const { candidates } = previewSupplierText(longMessage(150), own("used"));
    expect(Date.now() - started).toBeLessThan(1500);
    expect(candidates).toHaveLength(150);
    expect(candidates.filter((r) => r.kind === "accessory").every((r) => r.model === "Cable USB-C 1m" && r.statedQuantity !== null)).toBe(true);
    expect(candidates.filter((r) => r.model === "iPhone 15").every((r) => r.capacityGb === 128 && /^3\d{3}\.00$/.test(r.supplierCost))).toBe(true);
    expect(candidates.filter((r) => r.model === "iPhone 13").every((r) => r.statedLine === "used" && r.batteryPercent !== null)).toBe(true);
  });

  it("duplicados del mismo texto se marcan, nunca se suman", () => {
    const [a, b] = rows("Cable USB-C x5 25\nCable USB-C x5 25", own("accessory"));
    expect(b.duplicateOf).toBe(a.id);
    expect([a.statedQuantity, b.statedQuantity]).toEqual([5, 5]);
  });
});
