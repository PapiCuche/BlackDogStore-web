import { luhnCheckDigit, previewSupplierText } from "@/app/admin/lib/inventory-text-preview";
import type { MatchResult } from "@/app/admin/lib/inventory-text-match";
import { blankUnit, buildReceiptBody, MAX_DEVICES, newLine, validateLines, type DraftLine, type ReceiptProduct } from "@/app/admin/lib/inventory-receipts";
import {
  applyChoice, applyMatches, classify, commitRows, editRow, evaluateRows, groupRows, nextPending, rowSignature, stageRows, summarize,
  type StagedRow,
} from "@/app/admin/lib/receive-list";

/**
 * INV-RECEIVE-UX-02 · la lista pegada del asistente de recepción: del texto a las filas, de las filas a las
 * variantes del catálogo y de las variantes a las líneas de la recepción. Todo con datos ficticios.
 */

const imei = (body: string) => body + luhnCheckDigit(body);
const IMEIS = Array.from({ length: 12 }, (_, i) => imei(String(35000000000000 + i * 137)));

function block(head: string, n: number, extra: string[] = []) {
  return [head, `Batería ${90 + (n % 8)}%`, `NS: FAKE${String(n).padStart(6, "0")}`, `IMEI ${IMEIS[n]}`, ...extra].join("\n");
}

/** Once seminuevos: tres iguales que existen, dos con la variante por crear, dos con el modelo por crear, dos sin color y dos más que existen. */
const ELEVEN = [
  block("iPhone 13 128 GB azul", 0), block("iPhone 13 128 GB azul", 1), block("iPhone 13 128 GB azul", 2),
  block("iPhone 13 256 GB negro", 3), block("iPhone 13 256 GB negro", 4),
  block("iPhone 12 64 GB blanco", 5), block("iPhone 12 64 GB blanco", 6),
  block("iPhone 14 Pro 128 GB morado", 7), block("iPhone 14 Pro 128 GB morado", 8),
  block("Galaxy S23 256 GB negro", 9), block("Galaxy S23 256 GB negro", 10),
].join("\n\n");

const product = (id: number, name: string, extra: Partial<ReceiptProduct> = {}): ReceiptProduct => ({
  id, name, is_serialized: true, requires_imei: true, condition_line: "used", ...extra,
});
const option = (p: ReceiptProduct) => ({
  product_id: p.id, name: p.name, condition_line: p.condition_line ?? "used", options: [],
  is_serialized: p.is_serialized, requires_imei: p.requires_imei,
});
const exact = (p: ReceiptProduct): MatchResult => ({ status: "exact", reason: "", options: [option(p)] });
const suggested = (p: ReceiptProduct): MatchResult => ({ status: "ambiguous", reason: "Coincide en parte (faltan opciones en la lista): confírmala.", options: [option(p)] });
const several = (...ps: ReceiptProduct[]): MatchResult => ({ status: "ambiguous", reason: "Varias variantes coinciden: elige una.", options: ps.map(option) });
const none: MatchResult = { status: "none", reason: "No existe esa variante del modelo.", options: [] };

const I13 = product(13, "iPhone 13 128 GB Azul Seminuevo");
const S23 = product(23, "Galaxy S23 256 GB Negro Seminuevo");

function staged(text = ELEVEN, line: "new" | "used" | "accessory" = "used") {
  return stageRows(previewSupplierText(text, { line, origin: "own" }), { costs: true });
}

describe("del texto a las filas", () => {
  it("reconoce los once equipos con sus datos físicos, sin inventar nada", () => {
    const rows = staged();
    expect(rows).toHaveLength(11);
    expect(rows[0]).toMatchObject({
      kind: "device", line: "used", condition: "used", model: "iPhone 13", capacityGb: 128, colour: "azul",
      serial: "FAKE000000", imei: IMEIS[0], battery: "90", product: null,
    });
    expect(rows[10]).toMatchObject({ model: "Galaxy S23", capacityGb: 256, colour: "negro", battery: "92" });
  });

  it("deja vacío lo que el texto no dice (batería, estado estético, costo)", () => {
    const [row] = staged("iPhone 13 128 GB azul\nNS: FAKE777777");
    expect(row).toMatchObject({ battery: "", grade: "", notes: "", cost: "", imei: "" });
  });

  it("un seminuevo escrito en una lista de nuevos se marca seminuevo y lo dice; lo contrario nunca se deduce", () => {
    const mixed = ["iPhone 13 128 GB azul seminuevo\nNS: FAKE000001", "iPhone 14 128 GB negro\nNS: FAKE000002"].join("\n\n");
    const asNew = staged(mixed, "new");
    expect(asNew.map((r) => [r.line, r.condition])).toEqual([["used", "used"], ["new", "new"]]);
    expect(asNew[0].hints.join(" ")).toMatch(/seminuevo/i);
    const asUsed = staged(mixed, "used");
    expect(asUsed.map((r) => r.line)).toEqual(["used", "used"]);
  });

  it("no arrastra avisos que la pantalla ya resuelve de otra forma (cantidad del proveedor, costo no indicado)", () => {
    const rows = staged();
    expect(rows.flatMap((r) => r.hints).join(" ")).not.toMatch(/proveedor|Cantidad no confirmada|Comprobar los identificadores/);
  });
});

describe("asociación al catálogo", () => {
  it("agrupa los equipos iguales: una sola decisión por combinación de modelo, capacidad, color y línea", () => {
    const groups = groupRows(staged());
    expect(groups.map((g) => g.rows.length)).toEqual([3, 2, 2, 2, 2]);
    expect(new Set(groups.map((g) => g.key)).size).toBe(5);
    expect(rowSignature(staged()[0])).toBe(rowSignature(staged()[1]));
  });

  it("clasifica la respuesta del servidor: exacta, sugerida, ambigua, inexistente, sin buscar", () => {
    expect(classify(exact(I13))).toBe("exact");
    expect(classify(suggested(I13))).toBe("suggested");
    expect(classify(several(I13, S23))).toBe("ambiguous");
    expect(classify(none)).toBe("missing");
    expect(classify(undefined)).toBe("unsearched");
  });

  it("asocia sola la coincidencia exacta; la sugerida y la ambigua esperan a una persona", () => {
    const rows = staged();
    const groups = groupRows(rows);
    const answers = {
      [groups[0].key]: exact(I13), [groups[1].key]: suggested(I13), [groups[2].key]: none,
      [groups[3].key]: several(I13, S23), [groups[4].key]: exact(S23),
    };
    const next = applyMatches(rows, answers);
    expect(next.filter((r) => r.product).map((r) => r.product!.id)).toEqual([13, 13, 13, 23, 23]);
    expect(next.filter((r) => !r.product)).toHaveLength(6);
  });

  it("una elección aplica a TODOS los equipos del grupo y a ninguno de otro grupo, y no copia sus datos físicos", () => {
    const rows = applyMatches(staged(), {});
    const groups = groupRows(rows);
    const next = applyChoice(rows, groups[1].key, I13);
    expect(next.filter((r) => r.product?.id === 13).map((r) => r.id)).toEqual(groups[1].rows.map((r) => r.id));
    expect(next.filter((r) => r.product).length).toBe(2);
    const [a, b] = groups[1].rows.map((r) => next.find((n) => n.id === r.id)!);
    expect(a.serial).not.toBe(b.serial);
    expect(a.imei).not.toBe(b.imei);
  });

  it("corregir un dato que define la variante (modelo, capacidad, color, línea) suelta la asociación de esa fila; lo demás no", () => {
    const rows = applyChoice(staged(), groupRows(staged())[0].key, I13);
    for (const change of [{ model: "iPhone 14" }, { capacityGb: 256 }, { colour: "rojo" }, { line: "new" as const }]) {
      const next = editRow(rows, rows[0].id, change);
      expect(next[0].product).toBeNull();
      expect(next[1].product?.id).toBe(13);
    }
    const physical = editRow(rows, rows[0].id, { serial: "NUEVA-1", battery: "88", notes: "ok" });
    expect(physical[0].product?.id).toBe(13);
    expect(physical[0]).toMatchObject({ serial: "NUEVA-1", battery: "88", notes: "ok" });
  });
});

describe("qué falta y qué bloquea", () => {
  const ctx = (rows: StagedRow[], lines: DraftLine[] = []) => ({ rows, lines, costs: true });

  it("cuenta cada fila en un solo grupo: listas, por asociar, por completar, duplicadas o inválidas", () => {
    let rows = staged();
    const groups = groupRows(rows);
    rows = applyChoice(rows, groups[0].key, I13);
    rows = applyChoice(rows, groups[4].key, S23);
    rows = rows.map((r, i) => (i === 1 ? { ...r, serial: "" } : r));
    rows = rows.map((r, i) => (i === 2 ? { ...r, imei: "123" } : r));
    const counts = summarize(evaluateRows(ctx(rows)));
    expect(counts).toEqual({ total: 11, ready: 3, catalog: 6, info: 1, invalid: 1 });
  });

  it("el caso de los once: sin variantes ninguna fila entra, pero el motivo es UNO por grupo de catálogo, no once errores de formato", () => {
    const rows = staged();
    const evaluation = evaluateRows(ctx(rows));
    expect(summarize(evaluation)).toMatchObject({ total: 11, catalog: 11, ready: 0, invalid: 0 });
    expect(evaluation.every((e) => e.issues.every((i) => i.kind === "catalog"))).toBe(true);
  });

  it("exige el IMEI sólo si el artículo elegido lo lleva", () => {
    const withImei = applyChoice(staged("iPhone 13 128 GB azul\nNS: FAKE777777"), groupRows(staged("iPhone 13 128 GB azul\nNS: FAKE777777"))[0].key, I13);
    expect(evaluateRows(ctx(withImei))[0].issues.map((i) => i.field)).toEqual(["imei"]);
    const noImei = applyChoice(withImei, groupRows(withImei)[0].key, product(14, "iPad", { requires_imei: false }));
    expect(evaluateRows(ctx(noImei))[0].issues).toEqual([]);
  });

  it("bloquea IMEI que no supera el dígito de control, batería fuera de rango y costo mal escrito", () => {
    let rows = applyChoice(staged(), groupRows(staged())[0].key, I13);
    rows = rows.map((r) => (r.product ? { ...r, imei: "490154203237510" } : r));
    expect(evaluateRows(ctx(rows))[0].issues).toContainEqual(expect.objectContaining({ kind: "invalid", field: "imei" }));
    rows = rows.map((r) => (r.product ? { ...r, imei: IMEIS[0], battery: "140" } : r));
    expect(evaluateRows(ctx(rows))[0].issues).toContainEqual(expect.objectContaining({ kind: "invalid", field: "battery" }));
    rows = rows.map((r) => (r.product ? { ...r, battery: "90", cost: "12,5x" } : r));
    expect(evaluateRows(ctx(rows))[0].issues).toContainEqual(expect.objectContaining({ kind: "invalid", field: "cost" }));
  });

  it("sin permiso de costos el costo no se valida ni se pide", () => {
    let rows = applyChoice(staged(), groupRows(staged())[0].key, I13);
    rows = rows.map((r) => (r.product ? { ...r, cost: "no-es-un-costo" } : r));
    expect(evaluateRows({ rows, lines: [], costs: false })[0].issues.some((i) => i.field === "cost")).toBe(false);
  });

  it("detecta repetidos dentro de la lista y contra lo que ya está en la recepción; el mismo modelo con identificadores distintos no es duplicado", () => {
    const base = applyChoice(staged(), groupRows(staged())[0].key, I13);
    expect(evaluateRows(ctx(base)).slice(0, 3).every((e) => e.issues.length === 0)).toBe(true);

    const sameImei = editRow(base, base[1].id, { imei: base[0].imei });
    const dup = evaluateRows(ctx(sameImei));
    expect(dup[0].issues).toContainEqual(expect.objectContaining({ kind: "invalid", field: "imei", text: expect.stringMatching(/repetid/i) }));
    expect(dup[1].issues).toContainEqual(expect.objectContaining({ kind: "invalid", field: "imei", text: expect.stringMatching(/repetid/i) }));
    expect(dup[2].issues).toEqual([]);

    const held: DraftLine = { ...newLine(I13), units: [{ ...blankUnit("used"), serial_number: "FAKE000002", imei: IMEIS[11] }] };
    const clash = evaluateRows(ctx(base, [held]));
    expect(clash[2].issues).toContainEqual(expect.objectContaining({ kind: "invalid", field: "serial", text: expect.stringMatching(/repetid/i) }));
    expect(clash[0].issues).toEqual([]);
  });

  it("un equipo asociado a un artículo por cantidad, o un accesorio a uno por serie, no pasa", () => {
    const rows = applyChoice(staged(), groupRows(staged())[0].key, product(99, "Cable", { is_serialized: false, requires_imei: false }));
    expect(evaluateRows(ctx(rows))[0].issues).toContainEqual(expect.objectContaining({ kind: "invalid", field: "product" }));
  });

  it("un accesorio pide su cantidad: no se adivina", () => {
    const [row] = staged("AirPods Pro 2 blanco\n", "accessory");
    expect(row.kind).toBe("accessory");
    const cable = product(7, "AirPods Pro 2 Blanco", { is_serialized: false, requires_imei: false, condition_line: "new" });
    const chosen = applyChoice([row], groupRows([row])[0].key, cable);
    expect(evaluateRows(ctx(chosen))[0].issues).toContainEqual(expect.objectContaining({ kind: "info", field: "quantity" }));
    expect(evaluateRows(ctx(chosen.map((r) => ({ ...r, quantity: "4" }))))[0].issues).toEqual([]);
  });

  it("«Resolver pendientes» lleva primero al catálogo y después a la primera fila con algo por corregir", () => {
    let rows = staged();
    const groups = groupRows(rows);
    expect(nextPending(rows, evaluateRows(ctx(rows)))).toEqual({ kind: "catalog", group: groups[0].key });

    for (const g of groups) rows = applyChoice(rows, g.key, product(50 + groups.indexOf(g), `Variante ${groups.indexOf(g)}`));
    rows = editRow(editRow(rows, rows[3].id, { imei: "123" }), rows[1].id, { serial: "" });
    expect(nextPending(rows, evaluateRows(ctx(rows)))).toEqual({ kind: "info", row: rows[1].id, field: "serial" });

    rows = editRow(rows, rows[1].id, { serial: "FAKE000999" });
    expect(nextPending(rows, evaluateRows(ctx(rows)))).toEqual({ kind: "invalid", row: rows[3].id, field: "imei" });

    rows = editRow(rows, rows[3].id, { imei: IMEIS[3] });
    expect(nextPending(rows, evaluateRows(ctx(rows)))).toBeNull();
  });
});

describe("de la lista a la recepción", () => {
  function allResolved() {
    let rows = staged();
    const groups = groupRows(rows);
    rows = applyChoice(rows, groups[0].key, I13);
    for (const g of groups.slice(1)) rows = applyChoice(rows, g.key, g === groups[4] ? S23 : product(40 + groups.indexOf(g), `Variante ${groups.indexOf(g)}`));
    return rows;
  }

  it("agrega los once como equipos de sus artículos, uno por fila, con batería y sin mezclar identificadores", () => {
    const rows = allResolved();
    const { lines, committed, left } = commitRows(rows, [], { costs: true });
    expect(committed).toHaveLength(11);
    expect(left).toEqual([]);
    expect(lines.map((l) => l.units.length)).toEqual([3, 2, 2, 2, 2]);
    const units = lines.flatMap((l) => l.units);
    expect(new Set(units.map((u) => u.imei)).size).toBe(11);
    expect(units[0]).toMatchObject({ serial_number: "FAKE000000", imei: IMEIS[0], condition: "used", battery: "90" });
  });

  it("lo que no está listo se queda en la lista, con su motivo; nada se pierde ni entra a medias", () => {
    const rows = allResolved().map((r, i) => (i === 4 ? { ...r, serial: "" } : r));
    const { lines, committed, left } = commitRows(rows, [], { costs: true });
    expect(committed).toHaveLength(10);
    expect(left.map((r) => r.id)).toEqual([rows[4].id]);
    expect(lines.flatMap((l) => l.units)).toHaveLength(10);
  });

  it("suma al renglón del mismo artículo si ya estaba en la recepción, sin tocar sus equipos", () => {
    const held: DraftLine = { ...newLine(I13), units: [{ ...blankUnit("used"), serial_number: "OTRO-1", imei: IMEIS[11] }] };
    const { lines } = commitRows(allResolved(), [held], { costs: true });
    expect(lines.filter((l) => l.product.id === 13)).toHaveLength(1);
    expect(lines.find((l) => l.product.id === 13)!.units.map((u) => u.serial_number)).toEqual(["OTRO-1", "FAKE000000", "FAKE000001", "FAKE000002"]);
  });

  it("respeta el tope de equipos por recepción: agrega hasta el tope, el resto queda a la vista", () => {
    const full: DraftLine = { ...newLine(S23), units: Array.from({ length: MAX_DEVICES - 4 }, (_, i) => ({ ...blankUnit("used"), serial_number: `X${i}`, imei: "" })) };
    const result = commitRows(allResolved(), [full], { costs: true });
    expect(result.committed).toHaveLength(4);
    expect(result.left).toHaveLength(7);
    expect(result.limit).toMatch(/200 equipos/);
  });

  it("el cuerpo que viaja lleva batería, estado estético y observaciones sólo si se escribieron; el costo sólo con permiso", () => {
    const rows = allResolved().map((r, i) => (i === 0 ? { ...r, grade: "7", notes: "  rayón leve  ", cost: "1200.50" } : r));
    const { lines } = commitRows(rows, [], { costs: true });
    const withCosts = buildReceiptBody({ branch: 1, supplier: null, document_ref: "", notes: "" }, lines, { costs: true }).body;
    const first = (withCosts.lines[0] as { units: Record<string, unknown>[] }).units[0];
    expect(first).toMatchObject({ battery_health: "90", cosmetic_grade: 7, notes: "rayón leve", unit_cost: "1200.50" });
    expect(Object.keys((withCosts.lines[0] as { units: Record<string, unknown>[] }).units[1])).not.toContain("cosmetic_grade");
    const without = buildReceiptBody({ branch: 1, supplier: null, document_ref: "", notes: "" }, lines, { costs: false }).body;
    expect(JSON.stringify(without)).not.toContain("unit_cost");
    expect(validateLines(lines, { costs: true })).toEqual({});
  });
});
