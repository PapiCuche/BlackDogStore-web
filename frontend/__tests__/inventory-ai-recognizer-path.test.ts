/**
 * INV-AI-01 × INV-TEXT-02 — the first half of the end-to-end proof that what an AI provider could receive
 * comes ONLY from what the local recognizer produced (ADR docs/adr-inv-ai-structured.md).
 *
 * A realistic, de-identified WhatsApp supplier list (fake IMEI that passes Luhn, fake serial, fake phones, a
 * made-up name and e-mail, costs, quantities, battery) is run through the REAL recognizer
 * (`previewSupplierText`) and the REAL request builder (`matchPayload`). Their output is pinned in
 * `support/inventory-ai/recognizer-output.json`; the Django test
 * `store.test_inventory_ai_recognizer_path` reads that same file and carries it on to `propose`. If the
 * recognizer changes what it produces, this test fails and the fixture has to be regenerated on purpose:
 *   WRITE_INV_AI_FIXTURE=1 npx jest __tests__/inventory-ai-recognizer-path.test.ts
 */
import fs from "fs";
import path from "path";

import { matchPayload } from "../app/admin/lib/inventory-text-match";
import { previewSupplierText, type TextPreview } from "../app/admin/lib/inventory-text-preview";

const DIR = path.join(__dirname, "support", "inventory-ai");
const read = (name: string) => fs.readFileSync(path.join(DIR, name), "utf8");
const LISTS = { nuevos: "new", seminuevos: "used" } as const;
type Sensitive = { imeis: string[]; serials: string[]; phones: string[]; names: string[]; emails: string[];
  costs: string[]; quantities: string[]; battery: string[] };
const SENSITIVE: Sensitive = JSON.parse(read("sensitive.json"));

function recognize(): Record<string, TextPreview> {
  return Object.fromEntries(Object.entries(LISTS).map(([name, line]) => [
    name, previewSupplierText(read(`whatsapp-${name}.txt`), { line, origin: "supplier" }),
  ]));
}

const travelling = (previews: Record<string, TextPreview>) =>
  Object.fromEntries(Object.entries(previews).map(([name, preview]) => [name, matchPayload(preview.candidates)]));

const word = (value: string) => new RegExp(`(?<![0-9A-Za-z])${value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![0-9A-Za-z])`, "i");

describe("INV-AI-01: what may travel comes only from the local recognizer", () => {
  if (process.env.WRITE_INV_AI_FIXTURE === "1") {
    fs.writeFileSync(path.join(DIR, "recognizer-output.json"), JSON.stringify(travelling(recognize()), null, 2) + "\n");
  }

  it("the recognizer's output is exactly the pinned fixture the backend test reads", () => {
    expect(travelling(recognize())).toEqual(JSON.parse(read("recognizer-output.json")));
  });

  it("the fixture really exercises the sensitive values: the recognizer read them", () => {
    const candidates = Object.values(recognize()).flatMap(preview => preview.candidates);
    const lines = new Set(candidates.map(candidate => candidate.line));
    expect(lines).toEqual(new Set(["new", "used", "accessory"]));
    for (const imei of SENSITIVE.imeis) expect(candidates.some(candidate => candidate.imei === imei)).toBe(true);
    for (const serial of SENSITIVE.serials) {
      expect(candidates.some(candidate => candidate.serial === serial || candidate.source.includes(serial))).toBe(true);
    }
    const costs = new Set(candidates.map(candidate => candidate.supplierCost).filter(Boolean));
    expect(costs.size).toBeGreaterThanOrEqual(5);
    for (const cost of costs) expect(SENSITIVE.costs).toContain(cost);
    expect(candidates.some(candidate => candidate.batteryPercent !== null)).toBe(true);
    const sources = candidates.map(candidate => candidate.source).join("\n");
    for (const quantity of SENSITIVE.quantities) expect(sources).toContain(quantity);
  });

  // Quantities are the exception, pinned in the fixture as they are today: INV-TEXT-02 leaves «x3» and
  // «(quedan 2)» inside `model` (they reach our own catalogue search only). The backend half proves the AI
  // contract drops them: an unknown word makes the whole model ambiguous, so it is never sent.
  it("none of the identifiers, people's data, costs or battery is in what the recognizer lets travel", () => {
    const body = JSON.parse(read("recognizer-output.json")) as Record<string, ReturnType<typeof matchPayload>>;
    for (const payload of Object.values(body)) {
      for (const candidate of payload.candidates) {
        expect(Object.keys(candidate).sort()).toEqual(["capacityGb", "colour", "key", "kind", "line", "model"]);
      }
    }
    const text = JSON.stringify(body);
    const digits = text.replace(/\D/g, " ");
    for (const value of [...SENSITIVE.imeis, ...SENSITIVE.serials, ...SENSITIVE.phones, ...SENSITIVE.names,
      ...SENSITIVE.emails, ...SENSITIVE.battery]) {
      expect(text).not.toMatch(word(value));
    }
    for (const phone of SENSITIVE.phones) expect(digits.replace(/ /g, "")).not.toContain(phone);
    for (const imei of SENSITIVE.imeis) expect(digits.replace(/ /g, "")).not.toContain(imei);
    for (const cost of SENSITIVE.costs) expect(text).not.toMatch(word(cost));
  });

  it("the recognizer opens no network; its only requests are our own catalogue search and import draft", () => {
    const lib = path.join(__dirname, "..", "app", "admin", "lib");
    const network = /\bfetch\s*\(|XMLHttpRequest|WebSocket|EventSource|sendBeacon|\baxios\b|anthropic|openai|https?:\/\//;
    // Every inventory-text module, also those added later (INV-TEXT-03 added the draft): none opens a network
    // of its own; what travels goes through `fetchWithAuth` to OUR API, to the routes listed here.
    const modules = fs.readdirSync(lib).filter((name) => /^inventory-text-.*\.ts$/.test(name)).sort();
    expect(modules).toEqual(["inventory-text-draft.ts", "inventory-text-match.ts", "inventory-text-preview.ts",
      "inventory-text-stages.ts"]);
    for (const name of modules) expect(fs.readFileSync(path.join(lib, name), "utf8")).not.toMatch(network);
    for (const name of ["inventory-text-preview.ts", "inventory-text-stages.ts"]) {
      expect(fs.readFileSync(path.join(lib, name), "utf8")).not.toMatch(/fetchWithAuth/);
    }
    // The catalogue search (INV-TEXT-02), with the screen's company (INV-TEXT-03).
    const match = fs.readFileSync(path.join(lib, "inventory-text-match.ts"), "utf8");
    expect(match.match(/fetchWithAuth\(/g)).toHaveLength(1);
    expect(match).toContain("fetchWithAuth(`${API_BASE}/admin/inventory/text-preview/match/${company}`");
    // The import draft (INV-TEXT-03): one request helper, to the dry-run routes only.
    const draft = fs.readFileSync(path.join(lib, "inventory-text-draft.ts"), "utf8");
    expect(draft.match(/fetchWithAuth\(/g)).toHaveLength(1);
    expect(draft.match(/post\("\/[^"]*"/g)).toEqual([
      'post("/admin/inventory/text-preview/offer-draft/"', 'post("/admin/inventory/text-preview/offer-draft/file/"']);
  });
});
