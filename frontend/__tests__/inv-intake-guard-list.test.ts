/**
 * INV-INTAKE-GUARD-01 (revisión independiente) — la lista pegada trata igual que el resto del asistente a una línea
 * que va «por equipo» aunque su artículo todavía no se controle por serie, y una variante elegida de la lista lleva su
 * modelo.
 */
import { luhnCheckDigit } from "@/app/admin/lib/inventory-text-preview";
import { newLine, type DraftLine, type ReceiptProduct } from "@/app/admin/lib/inventory-receipts";
import { commitRows, evaluateRows, optionToProduct, type StagedRow } from "@/app/admin/lib/receive-list";

const imei = (body: string) => body + luhnCheckDigit(body);
const IMEI = imei("35693803564" + "512");

/** A variant of a model, not yet counted by serial and with nothing on any shelf: its line starts «por equipo». */
const VARIANT: ReceiptProduct = {
  id: 70, name: "iPhone 16 128 GB Negro", is_serialized: false, requires_imei: false, can_change_tracking: true,
  condition_line: "new", product_model_id: 4,
};
const PHONE: ReceiptProduct = { id: 71, name: "iPhone 16 256 GB", is_serialized: true, requires_imei: true, condition_line: "new" };

const row = (over: Partial<StagedRow>): StagedRow => ({
  id: 1, kind: "device", line: "new", condition: "new", model: "iPhone 16", capacityGb: 128, colour: "negro",
  ramGb: null, sim: "", region: "", serial: "", imei: "", imei2: "", battery: "", grade: "", notes: "", cost: "",
  price: "", publicNotes: "", quantity: "", source: "", hints: [], product: null, ...over,
} as StagedRow);

function byUnitLine(): DraftLine {
  const line = newLine(VARIANT);
  expect(line.byUnit).toBe(true);
  return { ...line, units: [{ ...line.units[0], serial_number: "SER0001", imei: IMEI }] };
}

describe("IMEI repetido", () => {
  it("cuenta los equipos de una línea «por equipo» aunque el artículo aún no vaya por serie", () => {
    const evaluation = evaluateRows({
      rows: [row({ serial: "SER9999", imei: IMEI, product: PHONE })], lines: [byUnitLine()], costs: false,
    });
    expect(evaluation[0].issues.some((i) => i.field === "imei" && /repetid/.test(i.text))).toBe(true);
  });
});

describe("cantidades de la lista frente a la recepción", () => {
  it("una línea «por equipo» no es una cantidad a la que sumar, ni al revisar ni al agregar", () => {
    const lines = [byUnitLine()];
    const accessory = row({ kind: "accessory", line: "accessory", quantity: "2", product: VARIANT });
    const [evaluated] = evaluateRows({ rows: [accessory], lines, costs: false });
    expect(evaluated.merge).toBeUndefined();
    expect(evaluated.issues.some((i) => i.field === "merge")).toBe(false);
    const commit = commitRows([accessory], lines, { costs: false });
    // The device line stays as it was; the quantity is its own line.
    expect(commit.lines).toHaveLength(2);
    expect(commit.lines[0].units[0].serial_number).toBe("SER0001");
    expect(commit.lines[1]).toMatchObject({ quantity: "2", byUnit: false });
  });
});

describe("optionToProduct", () => {
  it("lleva el modelo de la variante, para que la línea reciba las mismas restricciones", () => {
    const option = { product_id: 70, name: VARIANT.name, condition_line: "new", options: [], is_serialized: false,
      requires_imei: false, product_model_id: 4 };
    expect(optionToProduct(option, "device").product_model_id).toBe(4);
    expect(optionToProduct({ ...option, product_model_id: null }, "accessory").product_model_id ?? null).toBeNull();
  });
});
