import { luhnCheckDigit, previewSupplierText } from "@/app/admin/lib/inventory-text-preview";
import { matchPayload, type MatchResult } from "@/app/admin/lib/inventory-text-match";
import { blankUnit, buildReceiptBody, MAX_DEVICES, newLine, validateLines, type DraftLine, type ReceiptProduct } from "@/app/admin/lib/inventory-receipts";
import {
  applyChoice, applyMatches, classify, commitRows, editRow, evaluateRows, groupRows, MAX_PROPOSAL_ROWS, MAX_STAGED, nextPending,
  chunk, leadIssue, nextRowId, nextScanTarget, optionToProduct, proposalRowsFor, rowSignature, stageRows, summarize, type StagedRow,
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


describe('revisión independiente · segunda pegada, ids, batería de nuevos, artículos inactivos', () => {
  const ctx = (rows: StagedRow[], lines: DraftLine[] = [], costs = true) => ({ rows, lines, costs });

  it('applyMatches enlaza también las filas nuevas de una combinación que ya se había buscado, respeta lo ya elegido y no cambia nada si no hay nada que enlazar', () => {
    const first = applyMatches(staged(), {});
    const answers = { [groupRows(first)[0].key]: exact(I13) };
    const linked = applyMatches(first, answers);
    expect(linked.filter((r) => r.product).length).toBe(3);
    // The same combination pasted again: its new rows are linked by the answer already held.
    const again = [...linked, ...stageRows(previewSupplierText(block("iPhone 13 128 GB azul", 11), { line: "used", origin: "own" }), { costs: true }, 50)];
    const relinked = applyMatches(again, answers);
    expect(relinked.at(-1)?.product?.id).toBe(13);
    // A choice the person made is kept; an unchanged list is returned as is (no render loop).
    const chosen = applyChoice(again, groupRows(again)[0].key, S23);
    expect(applyMatches(chosen, answers).filter((r) => r.product?.id === 23)).toHaveLength(4);
    expect(applyMatches(relinked, answers)).toBe(relinked);
  });

  it('un exacto con más de una opción nunca se enlaza solo', () => {
    const odd: MatchResult = { status: "exact", reason: "", options: [option(I13), option(S23)] };
    expect(classify(odd)).toBe("ambiguous");
    const rows = staged();
    expect(applyMatches(rows, { [groupRows(rows)[0].key]: odd }).some((r) => r.product)).toBe(false);
  });

  it("los ids de una nueva pegada nunca chocan con los que ya hay", () => {
    const rows = staged();
    expect(nextRowId(rows)).toBe(12);
    expect(nextRowId([])).toBe(1);
    expect(nextRowId([], 40)).toBe(40);
    expect(nextRowId(rows, 3)).toBe(12);
  });

  it("un equipo nuevo no lleva batería ni estado estético: se conservan en la fila, pero no se muestran ni viajan", () => {
    let rows = applyChoice(staged(), groupRows(staged())[0].key, I13);
    rows = editRow(rows, rows[0].id, { grade: "7" });
    expect(rows[0]).toMatchObject({ battery: "90", grade: "7" });
    const asNew = editRow(rows, rows[0].id, { line: "new" });
    expect(asNew[0]).toMatchObject({ condition: "new", battery: "90", grade: "7" });
    const open = editRow(rows, rows[0].id, { condition: "new" });
    expect(open[0]).toMatchObject({ battery: "90", grade: "7" });
    expect(commitRows(open.map((r) => ({ ...r, product: I13 })), [], { costs: true }).lines[0].units[0]).toMatchObject({ battery: "", grade: "" });
    // Even if a value stays in a row that is new, the unit built from it carries none.
    const sneaky = rows.map((r) => ({ ...r, condition: "new" }));
    const { lines } = commitRows(sneaky, [], { costs: true });
    expect(lines[0].units[0]).toMatchObject({ battery: "", grade: "" });
    const manual: DraftLine = { ...newLine(I13), units: [{ ...blankUnit("new"), serial_number: "A1B2", imei: IMEIS[0], battery: "93", grade: "7" }] };
    const body = buildReceiptBody({ branch: 1, supplier: null, document_ref: "", notes: "" }, [manual], { costs: false }).body;
    expect(JSON.stringify(body)).not.toMatch(/battery_health|cosmetic_grade/);
    // And a hidden battery value on a new unit is never a reason to block it.
    expect(validateLines([{ ...manual, units: [{ ...manual.units[0], battery: "999" }] }], { costs: false })).toEqual({});
  });

  it("cambiar la línea de un grupo no pisa un estado puesto a mano (caja abierta, reacondicionado…)", () => {
    const rows = applyChoice(staged(), groupRows(staged())[0].key, I13);
    const custom = editRow(rows, rows[0].id, { condition: "open_box" });
    expect(editRow(custom, custom[0].id, { line: "new" })[0].condition).toBe("open_box");
    expect(editRow(rows, rows[0].id, { line: "new" })[0].condition).toBe("new");
  });

  it("un artículo desactivado se avisa en la fila, no sólo en la revisión", () => {
    const dead = { ...I13, is_active: false };
    const rows = applyChoice(staged(), groupRows(staged())[0].key, dead);
    expect(evaluateRows(ctx(rows))[0].issues).toContainEqual(expect.objectContaining({ kind: "invalid", field: "product", text: expect.stringMatching(/desactivado/) }));
    expect(optionToProduct({ ...option(I13), is_active: false }, "device").is_active).toBe(false);
  });

  it("la línea del artículo elegido distinta de la de la fila es un aviso, no un bloqueo", () => {
    const newish = product(77, "iPhone 13 128 GB Azul", { condition_line: "new" });
    const rows = applyChoice(staged(), groupRows(staged())[0].key, newish);
    const first = evaluateRows(ctx(rows))[0];
    expect(first.issues).toContainEqual(expect.objectContaining({ kind: "warn", field: "product" }));
    expect(first.bucket).toBe("ready");
    expect(commitRows(rows, [], { costs: true }).committed).toContain(rows[0].id);
    expect(nextPending(rows, evaluateRows(ctx(rows)))).toMatchObject({ kind: "catalog" });
  });

  it("los accesorios con costos distintos no se funden en una línea ni pierden su costo; con el mismo costo sí suman", () => {
    const cable = product(7, "Cable USB-C", { is_serialized: false, requires_imei: false, condition_line: "new" });
    const make = (qty: string, cost: string) => {
      const [row] = staged("Cable USB-C 1m blanco\n", "accessory");
      return { ...row, product: cable, quantity: qty, cost };
    };
    const rows = [make("5", "10.00"), { ...make("5", "99.00"), id: 2 }, { ...make("3", "10.00"), id: 3 }];
    const { lines, committed } = commitRows(rows, [], { costs: true });
    expect(committed).toHaveLength(3);
    expect(lines.map((l) => [l.quantity, l.unitCost])).toEqual([["8", "10.00"], ["5", "99.00"]]);
    // A cost typed on a row that joins an UNCOSTED line does not re-price the earlier units.
    const plain: DraftLine = { ...newLine(cable), quantity: "4", unitCost: "" };
    expect(commitRows([make("5", "10.00")], [plain], { costs: true }).lines.map((l) => [l.quantity, l.unitCost]))
      .toEqual([["4", ""], ["5", "10.00"]]);
    // A quantity over the cap never sums into one line.
    const big = commitRows([make("60000", ""), { ...make("60000", ""), id: 2 }], [], { costs: true });
    expect(big.lines.map((l) => l.quantity)).toEqual(["60000", "60000"]);
  });

  it("respeta el tope de líneas por recepción y lo dice", () => {
    const lines = Array.from({ length: 50 }, (_, i) => ({ ...newLine(product(900 + i, `A${i}`)), units: [] as DraftLine["units"] }));
    const rows = applyChoice(staged(), groupRows(staged())[0].key, I13);
    const result = commitRows(rows, lines, { costs: true });
    expect(result.committed).toEqual([]);
    expect(result.limit).toMatch(/50 artículos/);
  });

  it("sin permiso de costos el costo pegado no entra a la fila, el aviso no muestra importes y nada viaja", () => {
    const text = "iPhone 13 128 GB azul 1800 2300\nNS: FAKE000001";
    const [row] = stageRows(previewSupplierText(text, { line: "used", origin: "own" }), { costs: false });
    expect(row.cost).toBe("");
    expect(row.hints.join(" ")).not.toMatch(/1800|2300/);
    const withCost = { ...row, product: I13, cost: "999.00", imei: IMEIS[0] };
    expect(commitRows([withCost], [], { costs: false }).lines[0].units[0].cost).toBe("");
  });

  it("las filas que piden catálogo van de a una por combinación, sólo equipos, y por debajo del tope del servidor", () => {
    const rows = staged();
    expect(proposalRowsFor(rows).map((r) => r.id)).toEqual(rows.map((r) => r.id));
    const accessory = staged("AirPods Pro 2 blanco\n", "accessory");
    expect(proposalRowsFor(accessory)).toEqual([]);
    const many = Array.from({ length: MAX_PROPOSAL_ROWS + 20 }, (_, i) => ({ ...rows[0], id: 1000 + i }));
    expect(proposalRowsFor(many)).toHaveLength(1);
    expect(MAX_STAGED).toBeGreaterThanOrEqual(MAX_DEVICES);
  });
});


describe('lector de códigos y lotes', () => {
  it('Enter en un campo opcional vacío sigue DESPUÉS de esa fila, no salta al principio de la lista', () => {
    const noImei = product(50, 'Tableta', { requires_imei: false });
    let rows = applyChoice(staged(), groupRows(staged())[0].key, noImei);
    rows = rows.map((r) => ({ ...r, serial: '', imei: '' }));
    // serial of row 2 typed; the optional IMEI is not part of the scan order: next is row 3's serial.
    const typed = editRow(rows, rows[1].id, { serial: 'SN-2' });
    expect(nextScanTarget(typed, rows[1].id, 'imei')).toEqual({ row: rows[2].id, field: 'serial' });
    // The last field of the list wraps to what is still empty, never to itself.
    expect(nextScanTarget(typed, rows[1].id, 'serial')).toEqual({ row: rows[2].id, field: 'serial' });
    const full = rows.map((r) => ({ ...r, serial: 'S' + r.id }));
    expect(nextScanTarget(full, rows[0].id, 'serial')).toBeNull();
  });

  it('un IMEI obligatorio vacío es lo siguiente de la MISMA fila', () => {
    const rows = applyChoice(staged(), groupRows(staged())[0].key, I13).map((r, i) => (i === 0 ? { ...r, imei: '' } : r));
    expect(nextScanTarget(rows, rows[0].id, 'serial')).toEqual({ row: rows[0].id, field: 'imei' });
  });

  it('parte una lista en tandas sin perder ni repetir nada', () => {
    const parts = chunk(Array.from({ length: 250 }, (_, i) => i), 100);
    expect(parts.map((p) => p.length)).toEqual([100, 100, 50]);
    expect(parts.flat()).toEqual(Array.from({ length: 250 }, (_, i) => i));
    expect(chunk([], 100)).toEqual([]);
  });
});


describe('segunda revisión independiente', () => {
  it('los accesorios con costos alternados se funden con la línea de SU costo, no sólo con la primera', () => {
    const cable = product(7, 'Cable USB-C', { is_serialized: false, requires_imei: false, condition_line: 'new' });
    const make = (id: number, cost: string) => {
      const [row] = staged('Cable USB-C 1m blanco\n', 'accessory');
      return { ...row, id, product: cable, quantity: '1', cost };
    };
    const { lines } = commitRows([make(1, '10'), make(2, '20'), make(3, '20'), make(4, '10')], [], { costs: true });
    expect(lines.map((l) => [l.quantity, l.unitCost])).toEqual([['2', '10'], ['2', '20']]);
  });

  it('un equipo nuevo pegado con batería en el texto no la conserva y lo dice', () => {
    const [row] = staged(block('iPhone 13 128 GB azul', 0), 'new');
    expect(row).toMatchObject({ line: 'new', condition: 'new', battery: '' });
    expect(row.hints.join(' ')).toMatch(/batería/i);
  });

  it('el motivo que se muestra es el que bloquea: primero lo inválido, luego lo que falta, al final el aviso', () => {
    const nonSerialNew = product(78, 'Cable', { is_serialized: false, requires_imei: false, condition_line: 'new' });
    const rows = applyChoice(staged(), groupRows(staged())[0].key, nonSerialNew);
    const issues = evaluateRows({ rows, lines: [], costs: true })[0].issues;
    expect(issues.map((i) => i.kind)).toEqual(expect.arrayContaining(['warn', 'invalid']));
    expect(leadIssue(issues)?.kind).toBe('invalid');
    expect(leadIssue([{ kind: 'warn', field: 'product', text: 'a' }, { kind: 'info', field: 'serial', text: 'b' }])?.kind).toBe('info');
    expect(leadIssue([{ kind: 'warn', field: 'product', text: 'a' }])?.kind).toBe('warn');
    expect(leadIssue([{ kind: 'catalog', field: 'product', text: 'c' }])).toBeUndefined();
    expect(leadIssue([])).toBeUndefined();
  });
});

describe('puerta de seguridad final · artículo de la combinación y batería de quien cambia de estado', () => {
  it('una fila nueva de una combinación con artículo elegido a mano hereda ESE artículo, no la respuesta exacta del servidor', () => {
    const rows = staged();
    const group = groupRows(rows)[0];
    const chosen = applyChoice(rows, group.key, S23);
    const added: StagedRow = { ...rows[0], id: 99, serial: "FAKEEXTRA", imei: IMEIS[11], product: null };
    const out = applyMatches([...chosen, added], { [group.key]: exact(I13) });
    expect(out.find((r) => r.id === 99)?.product?.id).toBe(23);
    expect(out.filter((r) => rowSignature(r) === group.key).map((r) => r.product?.id)).toEqual([23, 23, 23, 23]);
  });

  it('sin artículo en la combinación, la respuesta exacta sigue asociando sola', () => {
    const rows = staged();
    const group = groupRows(rows)[0];
    expect(applyMatches(rows, { [group.key]: exact(I13) }).filter((r) => rowSignature(r) === group.key).every((r) => r.product?.id === 13)).toBe(true);
  });

  it('pasar un equipo a nuevo y volver a seminuevo no pierde la batería ni el estado estético medidos', () => {
    const rows = staged();
    const id = rows[0].id;
    const graded = editRow(rows, id, { grade: "7" });
    const asNew = editRow(graded, id, { condition: "new" });
    expect(asNew.find((r) => r.id === id)).toMatchObject({ condition: "new", battery: rows[0].battery, grade: "7" });
    const back = editRow(asNew, id, { condition: "used" });
    expect(back.find((r) => r.id === id)).toMatchObject({ condition: "used", battery: rows[0].battery, grade: "7" });
  });

  it('mientras es nuevo, esa batería no se envía ni bloquea', () => {
    const rows = staged();
    const id = rows[0].id;
    const asNew = editRow(applyChoice(rows, groupRows(rows)[0].key, I13), id, { condition: "new", battery: "abc" });
    const row = asNew.find((r) => r.id === id)!;
    const item = evaluateRows({ rows: asNew, lines: [], costs: true }).find((e) => e.row.id === id)!;
    expect(item.issues.some((i) => i.field === "battery")).toBe(false);
    const done = commitRows([{ ...row, product: I13 }], [], { costs: true });
    expect(done.lines[0].units[0]).toMatchObject({ battery: "", grade: "" });
  });
});

describe('puerta de seguridad final · el color tampoco lleva identificadores al catálogo', () => {
  it('un color con un IMEI o una serie pegados sale limpio en la búsqueda', () => {
    const rows = staged(block('iPhone 13 128 GB azul', 0));
    const dirty = { ...rows[0], colour: `azul ${IMEIS[0]}` };
    const body = JSON.stringify(matchPayload([dirty]));
    expect(body).not.toContain(IMEIS[0]);
    expect(JSON.parse(body).candidates[0].colour).toBe("azul");
    const named = { ...rows[0], colour: "azul medianoche" };
    expect(matchPayload([named]).candidates[0].colour).toBe("azul medianoche");
  });
});

describe('puerta de seguridad final · filas en espera', () => {
  it('una fila en espera no entra a la recepción aunque esté lista, y sigue en la lista', () => {
    const rows = applyChoice(staged(), groupRows(staged())[0].key, I13).slice(0, 3);
    const held = rows[0].id;
    const done = commitRows(rows, [], { costs: true, hold: [held] });
    expect(done.committed).toEqual([rows[1].id, rows[2].id]);
    expect(done.left.map((r) => r.id)).toEqual([held]);
    expect(done.lines[0].units).toHaveLength(2);
  });

  it('un duplicado contra una fila en espera sigue siendo duplicado', () => {
    const rows = applyChoice(staged(), groupRows(staged())[0].key, I13).slice(0, 2);
    const twin = rows.map((r, i) => (i === 1 ? { ...r, imei: rows[0].imei } : r));
    const done = commitRows(twin, [], { costs: true, hold: [twin[0].id] });
    expect(done.committed).toEqual([]);
  });
});
