import { imeiIsValid, luhnCheckDigit, previewSupplierText, MAX_TEXT_CHARS } from "@/app/admin/lib/inventory-text-preview";

const used = { line: "used" as const, origin: "supplier" as const };
const fresh = { line: "new" as const, origin: "supplier" as const };

describe("INV-TEXT-01, texto sin red ni escritura", () => {
  const valid = (fourteen: string) => fourteen + luhnCheckDigit(fourteen);

  it("calcula Luhn y valida 15 dígitos sin fingir que el IMEI existe", () => {
    expect(luhnCheckDigit("49015420323751")).toBe("8");
    expect(imeiIsValid("490154203237518")).toBe(true);
    expect(imeiIsValid("490154203237510")).toBe(false);
    expect(luhnCheckDigit("00000000000000")).toBeNull();
    expect(imeiIsValid("000000000000000")).toBe(false);
  });

  it("extrae una ficha seminueva multilínea; nunca confirma cantidad", () => {
    const imei = valid("49015420323751");
    const second = valid("35693803564380");
    const result = previewSupplierText(
      ["iPhone 13", "128 GB", "Batería 93%", "NS: ABC1234T", "Versión: Chip",
        "Color: azul medianoche", "IMEI 1: " + imei, "IMEI 2: " + second].join("\n"), used,
    );
    expect(result.candidates).toHaveLength(1);
    const [row] = result.candidates;
    expect(row).toMatchObject({
      line: "used", origin: "supplier", capacityGb: 128, batteryPercent: 93,
      serial: "ABC1234T", imei, imei2: second, colour: "azul medianoche",
      sim: "chip", quantity: null, supplierCost: "",
    });
    expect(row.warnings).toEqual(expect.arrayContaining([
      expect.stringContaining("Cantidad no confirmada"), expect.stringContaining("Costo"),
    ]));
  });

  it("separa nuevos abreviados y trata precios como costo, nunca precio de venta", () => {
    const result = previewSupplierText("S25fe 8/256 1930 navy\nIPH 17 pro chip 256gb LLA 4370 blanco", fresh);
    expect(result.candidates).toHaveLength(2);
    expect(result.candidates.map(row => row.supplierCost)).toEqual(["1930", "4370"]);
    expect(result.candidates.map(row => row.capacityGb)).toEqual([256, 256]);
    expect(result.candidates.map(row => row.ramGb)).toEqual([8, null]);
    expect(result.candidates.map(row => row.quantity)).toEqual([null, null]);
  });

  it("filas repetidas no generan cantidades; se señala cuál es duplicada", () => {
    const r = previewSupplierText("S25fe 8/256 1930 navy\nS25fe 8/256 1930 navy", fresh).candidates;
    expect(r).toHaveLength(2);
    expect(r[0].duplicateOf).toBeNull();
    expect(r[1].duplicateOf).toBe(1);
    expect(r[1].warnings.join(" ")).toContain("no sumar unidades");
  });

  it("no fusiona dos seminuevos con series diferentes", () => {
    const r = previewSupplierText(
      "iPhone 13\nNS: ABC1234T\n\niPhone 13\nNS: ABC5678T", used,
    ).candidates;
    expect(r).toHaveLength(2);
    expect(r.every(item => item.duplicateOf === null)).toBe(true);
  });

  it("rechaza etiquetas de nuevo que contradicen RFB, y marca IMEI falso", () => {
    const r = previewSupplierText(
      "iPhone 14 RFB 128gb 2800 negro\nIMEI 1: 490154203237510", fresh,
    ).candidates;
    expect(r[0].warnings.join(" ")).toContain("contradice");
    expect(r[0].warnings.join(" ")).toContain("Luhn");
  });

  it("separa mensajes por modelo y omite encabezados Hoy/Ayer", () => {
    const r = previewSupplierText("Ayer\nIPH 15 128gb 2300 negro\nHoy\nIPH 16 128gb 2600 negro", fresh);
    expect(r.candidates).toHaveLength(2);
    expect(r.discardedLines).toBe(2);
  });

  it("la declaración stock propio no da de alta ni valida equipos por inferencia", () => {
    const r = previewSupplierText("iPhone 13\n128gb", { line: "used", origin: "own" }).candidates[0];
    expect(r.quantity).toBeNull();
    expect(r.warnings.join(" ")).toContain("identificadores físicos");
  });

  it("recorta lotes grandes y bloquea texto excesivo", () => {
    const r = previewSupplierText(Array(301).fill("IPH 15 128gb 2300 negro").join("\n"), fresh);
    expect(r.candidates).toHaveLength(300);
    expect(r.truncated).toBe(true);
    expect(() => previewSupplierText("a".repeat(MAX_TEXT_CHARS + 1), fresh)).toThrow("límite");
  });

  it("rechaza origen o clasificación ajena al contrato", () => {
    expect(() => previewSupplierText("IPH 15", { line: "unknown" as "new", origin: "supplier" })).toThrow();
  });
});

describe("revisión pre-fusión: lo que viaja al catálogo", () => {
  it("un modelo editado con un IMEI o una serie no los envía", () => {
    const { matchPayload } = jest.requireActual("@/app/admin/lib/inventory-text-match");
    const body = matchPayload([
      { id: 1, kind: "device", line: "used", model: "iPhone 13 IMEI: 356789012345678", capacityGb: 128, colour: "azul" },
      { id: 2, kind: "device", line: "used", model: "iPhone 13 NS: F2LXK9ABCD", capacityGb: 128, colour: "azul" },
      { id: 3, kind: "device", line: "used", model: "iPhone 13 3567 8901 2345 678", capacityGb: 128, colour: "azul" },
      { id: 4, kind: "accessory", line: "accessory", model: "Cargador USB-C 20W", capacityGb: null, colour: "" },
    ]);
    const text = JSON.stringify(body);
    expect(text).not.toMatch(/356789|5678|F2LX|ABCD|IMEI|"NS/);
    expect(body.candidates.map((c: { model: string }) => c.model)).toEqual(
      ["iPhone 13", "iPhone 13", "iPhone 13", "Cargador USB-C 20W"]);
  });
});

describe("revisión pre-fusión: nombres reales no se toman por identificadores", () => {
  it.each(["Cable Thunderbolt-4", "Apple Watch Serie 45mm", "Apple Watch Serie 9", "Funda Snoopy iPhone",
    "Galaxy Z Flip6 Snapdragon", "Cargador 35W-USB-C", "iPad Air M2 11", "Galaxy S24 Ultra"])("%s", model => {
    const { matchPayload } = jest.requireActual("@/app/admin/lib/inventory-text-match");
    const body = matchPayload([{ id: 1, kind: "accessory", line: "accessory", model, capacityGb: null, colour: "" }]);
    expect(body.candidates[0].model).toBe(model);
  });
});
