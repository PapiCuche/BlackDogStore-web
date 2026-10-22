import { previewSupplierText, type TextCandidate, type TextPreviewOptions } from "@/app/admin/lib/inventory-text-preview";

/**
 * INV-TEXT-02 · listas reales de proveedores, campo por campo. Los datos se
 * leen por su posición y significado, no por cuántos dígitos tienen. Lo que
 * no se puede saber se deja vacío y se avisa: nada se inventa.
 */

const fresh = { line: "new" as const, origin: "supplier" as const };
const used = { line: "used" as const, origin: "supplier" as const };

function one(text: string, options: TextPreviewOptions = fresh): TextCandidate {
  const { candidates } = previewSupplierText(text, options);
  expect(candidates).toHaveLength(1);
  return candidates[0];
}

const pick = (row: TextCandidate) => ({
  kind: row.kind, line: row.line, model: row.model, capacityGb: row.capacityGb, ramGb: row.ramGb, sim: row.sim,
  region: row.region, colour: row.colour, supplierCost: row.supplierCost, quantity: row.quantity,
});

describe("las ocho líneas de la orden, cada campo", () => {
  it("IPH 16 pro max 512gb esim 5890 titanio desierto", () => {
    expect(pick(one("IPH 16 pro max 512gb esim 5890 titanio desierto"))).toEqual({
      kind: "device", line: "new", model: "iPhone 16 Pro Max", capacityGb: 512, ramGb: null, sim: "esim",
      region: "", colour: "titanio desierto", supplierCost: "5890", quantity: null,
    });
  });

  it("S25fe 8/256 1930 navy", () => {
    expect(pick(one("S25fe 8/256 1930 navy"))).toEqual({
      kind: "device", line: "new", model: "Galaxy S25 FE", capacityGb: 256, ramGb: 8, sim: "",
      region: "", colour: "navy", supplierCost: "1930", quantity: null,
    });
  });

  it("IPH 17 pro chip 256gb LLA 4370 blanco", () => {
    expect(pick(one("IPH 17 pro chip 256gb LLA 4370 blanco"))).toEqual({
      kind: "device", line: "new", model: "iPhone 17 Pro", capacityGb: 256, ramGb: null, sim: "chip",
      region: "LL/A", colour: "blanco", supplierCost: "4370", quantity: null,
    });
  });

  it("- *IPH 15 128 2300 negro* -  (WhatsApp, capacidad sin GB)", () => {
    expect(pick(one("- *IPH 15 128 2300 negro* -"))).toEqual({
      kind: "device", line: "new", model: "iPhone 15", capacityGb: 128, ramGb: null, sim: "",
      region: "", colour: "negro", supplierCost: "2300", quantity: null,
    });
  });

  it("AirPods Pro 2 850 blanco", () => {
    expect(pick(one("AirPods Pro 2 850 blanco"))).toEqual({
      kind: "accessory", line: "accessory", model: "AirPods Pro 2", capacityGb: null, ramGb: null, sim: "",
      region: "", colour: "blanco", supplierCost: "850", quantity: null,
    });
  });

  it("Apple Pencil 2da generacion 380", () => {
    const row = one("Apple Pencil 2da generacion 380");
    expect(pick(row)).toMatchObject({ kind: "accessory", model: "Apple Pencil 2da generación", supplierCost: "380",
      capacityGb: null, colour: "" });
    expect(row.generation).toBe("2");
  });

  it("Cable USB-C 1m 45 blanco", () => {
    const row = one("Cable USB-C 1m 45 blanco");
    expect(pick(row)).toMatchObject({ kind: "accessory", model: "Cable USB-C 1m", supplierCost: "45", colour: "blanco" });
    expect(row.length).toBe("1m");
  });

  it("Cargador USB-C 20W 65", () => {
    const row = one("Cargador USB-C 20W 65");
    expect(pick(row)).toMatchObject({ kind: "accessory", model: "Cargador USB-C 20W", supplierCost: "65", colour: "" });
    expect(row.watts).toBe(20);
  });

  it("las ocho juntas, sin líneas en blanco, son ocho candidatos sin duplicados", () => {
    const text = [
      "IPH 16 pro max 512gb esim 5890 titanio desierto", "S25fe 8/256 1930 navy",
      "IPH 17 pro chip 256gb LLA 4370 blanco", "- *IPH 15 128 2300 negro* -", "AirPods Pro 2 850 blanco",
      "Apple Pencil 2da generacion 380", "Cable USB-C 1m 45 blanco", "Cargador USB-C 20W 65",
    ].join("\n");
    const { candidates, unrecognized } = previewSupplierText(text, fresh);
    expect(candidates.map(row => [row.model, row.supplierCost])).toEqual([
      ["iPhone 16 Pro Max", "5890"], ["Galaxy S25 FE", "1930"], ["iPhone 17 Pro", "4370"], ["iPhone 15", "2300"],
      ["AirPods Pro 2", "850"], ["Apple Pencil 2da generación", "380"], ["Cable USB-C 1m", "45"],
      ["Cargador USB-C 20W", "65"],
    ]);
    expect(candidates.every(row => row.duplicateOf === null && row.quantity === null)).toBe(true);
    expect(unrecognized).toEqual([]);
  });
});

describe("lo que no se puede saber no se inventa", () => {
  it("sin costo: vacío y aviso", () => {
    const row = one("IPH 15 128gb negro");
    expect(row.supplierCost).toBe("");
    expect(row.warnings.join(" ")).toContain("Costo de proveedor no indicado");
  });

  it("dos importes en la línea: ninguno se elige, se pide revisar", () => {
    const row = one("IPH 15 128gb 2300 2400 negro");
    expect(row.supplierCost).toBe("");
    expect(row.warnings.join(" ")).toContain("2300, 2400");
  });

  it("la capacidad no se toma por costo ni el costo por capacidad", () => {
    expect(pick(one("IPH 15 pro 256 3900"))).toMatchObject({ capacityGb: 256, supplierCost: "3900" });
    expect(pick(one("IPH 15 pro 3900 256gb"))).toMatchObject({ capacityGb: 256, supplierCost: "3900" });
  });

  it("vatios, longitud y generación no son costo", () => {
    expect(one("Cargador 35W dual 120").supplierCost).toBe("120");
    expect(one("Cable lightning 2m 30").supplierCost).toBe("30");
    expect(one("AirPods 4 590").model).toBe("AirPods 4");
  });

  it("una línea incompleta queda como candidata con aviso, sin rellenar", () => {
    const row = one("IPH 15");
    expect(pick(row)).toMatchObject({ model: "iPhone 15", capacityGb: null, colour: "", supplierCost: "" });
  });

  it("un modelo no reconocido se lista con su motivo, no se descarta en silencio", () => {
    const { candidates, unrecognized } = previewSupplierText("Nokia 3310 150\nIPH 15 128 2300 negro", fresh);
    expect(candidates.map(row => row.model)).toEqual(["iPhone 15"]);
    expect(unrecognized).toEqual([{ line: "Nokia 3310 150", reason: "Modelo no reconocido" }]);
  });

  it("un color que no está en el vocabulario se queda en el nombre", () => {
    const row = one("IPH 15 128 2300 lila pastel");
    expect(row.colour).toBe("");
    expect(row.model).toBe("iPhone 15 lila pastel");
  });
});

describe("variantes y duplicados", () => {
  it("variantes parecidas no se fusionan: región, capacidad, SIM y modelo", () => {
    const rows = previewSupplierText([
      "IPH 15 128 LLA 2300 negro", "IPH 15 128 ZAA 2300 negro", "IPH 15 256 LLA 2300 negro",
      "IPH 15 128 LLA esim 2300 negro", "IPH 15 plus 128 LLA 2300 negro",
    ].join("\n"), fresh).candidates;
    expect(rows.map(row => row.duplicateOf)).toEqual([null, null, null, null, null]);
    expect(rows.map(row => row.region)).toEqual(["LL/A", "ZA/A", "LL/A", "LL/A", "LL/A"]);
  });

  it("filas repetidas, aunque cambien el formato, se marcan sin sumar unidades", () => {
    const rows = previewSupplierText("IPH 15 128gb 2300 negro\n*iph 15 128 2300 NEGRO*", fresh).candidates;
    expect(rows[1].duplicateOf).toBe(1);
    expect(rows.every(row => row.quantity === null)).toBe(true);
  });
});

describe("seminuevos y WhatsApp", () => {
  it("ficha de seminuevo de varias líneas con serie, IMEI 1, IMEI 2 y batería", () => {
    const row = one([
      "iPhone 13", "128 GB", "Batería 93%", "NS: XXXX0000T", "Versión: Chip", "Color: azul medianoche",
      "IMEI 1: 490154203237518", "IMEI 2: 356938035643809", "Costo: 1650",
    ].join("\n"), used);
    expect(row).toMatchObject({
      kind: "device", line: "used", model: "iPhone 13", capacityGb: 128, batteryPercent: 93, serial: "XXXX0000T",
      sim: "chip", colour: "azul medianoche", imei: "490154203237518", imei2: "356938035643809",
      supplierCost: "1650", quantity: null,
    });
  });

  it("texto de WhatsApp con ruido: productos reconocidos y el resto contado con su motivo", () => {
    const text = [
      "[22/10/26, 10:15:03] Proveedor X: Buenos días 👋", "Lista actualizada hoy", "22/10/2026",
      "*IPH 14 128 2050 medianoche*", "• IPH 14 PLUS 256 2600 azul ✅", "precios sujetos a cambio", "Gracias!",
    ].join("\n");
    const { candidates, unrecognized } = previewSupplierText(text, fresh);
    expect(candidates.map(pick)).toEqual([
      { kind: "device", line: "new", model: "iPhone 14", capacityGb: 128, ramGb: null, sim: "", region: "",
        colour: "medianoche", supplierCost: "2050", quantity: null },
      { kind: "device", line: "new", model: "iPhone 14 Plus", capacityGb: 256, ramGb: null, sim: "", region: "",
        colour: "azul", supplierCost: "2600", quantity: null },
    ]);
    expect(unrecognized.map(item => item.reason)).toEqual(
      ["Texto sin producto", "Encabezado", "Fecha", "Texto sin producto", "Texto sin producto"]);
  });

  it("un guion que es parte del modelo o del identificador se conserva", () => {
    expect(one("Cable USB-C a Lightning 1m 55").model).toBe("Cable USB-C a Lightning 1m");
    expect(one("iPhone 13\nNS: AB-12345\nIMEI 1: 490154203237518", used).serial).toBe("AB-12345");
  });
});

describe('IMEI sin dos puntos', () => {
  it('se lee igual, y el IMEI 2 no se confunde con el 1', () => {
    const row = one('iPhone 13\nIMEI 490154203237518\nIMEI 2 356938035643809', used);
    expect([row.imei, row.imei2]).toEqual(['490154203237518', '356938035643809']);
  });

  it('un IMEI que empieza en 1 no pierde su primer dígito, y un número detrás no se suma', () => {
    expect(one('iPhone 13\nIMEI 135938035643801 500', used).imei).toBe('135938035643801');
    expect(one('iPhone 13\nIMEI 1 490154203237518', used).imei).toBe('490154203237518');
  });
});
