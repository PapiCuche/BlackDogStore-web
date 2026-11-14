/**
 * B1-1 · the company vocabulary the owner's training list is read with, built ONLY from
 * `docs/catalog-commercial/training-lines.json` (tenant A), exactly as the backend test builds it
 * (`backend/store/test_configuration_resolver.py`, `TrainingCatalog.build_tenant`):
 *
 *   * the four role attributes and every option a palette names;
 *   * «Blanco» as a colour of the COMPANY outside every palette (scenario A of the backend test);
 *   * the option aliases of the fixture, skipping those that fold like their label («esim», «borgona»);
 *   * the model word «iph» → «iphone».
 *
 * It is the shape of `GET /admin/catalog/vocabulary/`. The keys are written by hand the way the server's
 * `phrase_key` writes them, so a reader that folded differently from the server would fail here.
 */
import fs from 'fs';
import path from 'path';

import type { AttributeRole, CatalogVocabulary, VocabularyOption } from '@/app/admin/lib/internal-api';

export type TrainingLine = {
  n: number; text: string; model: string; capacity: string | null; sim: string | null; region_text: string | null;
  amount: number; candidates: { color_text: string | null; expected: string; missing?: string[] }[];
};

type Palette = Record<string, { capacity: string[]; color: string[]; sim: string[]; region: string[]; variants: Record<string, string>[] }>;

export type TrainingFixture = {
  tenant_a: { palette: Palette; aliases: Record<string, Record<string, string>> };
  lines: TrainingLine[];
};

export const FIXTURE_PATH = path.join(process.cwd(), '..', 'docs', 'catalog-commercial', 'training-lines.json');

export function trainingFixture(): TrainingFixture {
  return JSON.parse(fs.readFileSync(FIXTURE_PATH, 'utf8')) as TrainingFixture;
}

/** The server's `phrase_key`, written out for the few labels of the fixture (tested against the reader). */
const KEYS: Record<string, string> = {
  'iPhone 15': 'iphone 15', 'iPhone 17 Pro': 'iphone 17 pro', 'iPhone 17 Pro Max': 'iphone 17 pro max',
  'iPhone 18 Pro': 'iphone 18 pro', 'iPhone 18 Pro Max': 'iphone 18 pro max',
  '128 GB': '128 gb', '256 GB': '256 gb', '512 GB': '512 gb',
  Negro: 'negro', Azul: 'azul', Naranja: 'naranja', Plata: 'plata', 'Borgoña': 'borgona',
  'Glaciar blanco': 'glaciar blanco', 'Glaciar negro': 'glaciar negro', Blanco: 'blanco',
  eSIM: 'esim', Chip: 'chip', 'LL/A': 'll / a', 'J/A': 'j / a',
};

const ROLES: (keyof Palette[string] & AttributeRole)[] = ['capacity', 'color', 'sim', 'region'];

/**
 * `compounds: false` is the backend's «palette without compound colours» reading: no «Glaciar …» label anywhere.
 * `modelWords: false` leaves out the «iph» model word (a company that has not registered it yet).
 */
export function trainingVocabulary({ compounds = true, modelWords = true } = {}): CatalogVocabulary {
  const fixture = trainingFixture();
  let id = 100;
  const models = Object.keys(fixture.tenant_a.palette).map((name) => ({ id: ++id, name, key: KEYS[name], aliases: [] }));
  const options: Record<AttributeRole, VocabularyOption[]> = { capacity: [], color: [], sim: [], region: [], other: [] };
  const attribute: Record<string, number> = { capacity: 1, color: 2, sim: 3, region: 4 };
  const option = (role: AttributeRole, label: string): VocabularyOption => {
    let found = options[role].find((o) => o.label === label);
    if (!found) {
      found = { id: ++id, label, key: KEYS[label], attribute_id: attribute[role], swatch_hex: role === 'color' ? '#777777' : '', aliases: [], model_ids: [] };
      options[role].push(found);
    }
    return found;
  };
  for (const model of models) {
    const palette = fixture.tenant_a.palette[model.name];
    for (const role of ROLES) {
      for (const label of palette[role]) {
        if (!compounds && role === 'color' && label.includes(' ')) continue;
        option(role, label).model_ids.push(model.id);
      }
    }
  }
  option('color', 'Blanco');
  const folded = (text: string) => text.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase();
  for (const [role, pairs] of Object.entries(fixture.tenant_a.aliases)) {
    for (const [text, label] of Object.entries(pairs)) {
      const target = options[role as AttributeRole].find((o) => o.label === label);
      // As the backend: an alias that folds like its label is neither needed nor allowed.
      if (!target || folded(text) === target.key) continue;
      target.aliases.push({ id: ++id, alias: text });
    }
  }
  return {
    model_words: modelWords ? [{ id: ++id, alias: 'iph', replacement: 'iphone' }] : [],
    models, options, separator_words: ['and', 'e', 'y'],
  };
}

/** The model of the vocabulary called `name`. */
export function modelId(vocabulary: CatalogVocabulary, name: string): number {
  const found = vocabulary.models.find((m) => m.name === name);
  if (!found) throw new Error(`no model ${name}`);
  return found.id;
}

/**
 * What the server's resolver answers for the training lines (the statuses the fixture expects), for the screen
 * tests. Each row is matched to its fixture line by the text it carries, never by position. Existing variants get a
 * product id; `drafts` turns a valid configuration into an existing PENDING draft (after «Crear variante»).
 */
export function trainingResolveAnswer(
  rows: { key: string; model_text: string; colour_text?: string; capacity_text?: string; line?: string }[],
  { drafts = new Map<string, number>(), productOf = (n: number, index: number) => 1000 + n * 10 + index }: {
    drafts?: Map<string, number>; productOf?: (n: number, index: number) => number;
  } = {},
) {
  const fixture = trainingFixture();
  const vocabulary = trainingVocabulary();
  const answer = rows.map((row) => {
    const fits = (candidate: TrainingLine) => [row.model_text, row.colour_text, row.capacity_text]
      .every((field) => !field || candidate.text.includes(field));
    const line = fixture.lines.find((candidate) => `L${candidate.n}` === row.key && fits(candidate))
      ?? fixture.lines.find((candidate) => fits(candidate) && (row.colour_text ?? '') === candidate.candidates
        .map((c) => c.color_text ?? '').join(' ').replace(/^(borgoña azul) (negro)$/, '$1 y $2'));
    if (!line) {
      return { key: row.key, model: { text: row.model_text, status: 'unknown', via: null, model_id: null, model_name: null, candidates: [], extracted: [] },
        line: 'new', readings: [], error: '', configurations: [{
          status: 'modelo_desconocido', complete: false, colour_text: '', option_ids: [], options: [], product_id: null, product_name: null,
          draft_pending: false, missing: [], candidates: [],
          doubts: [{ role: 'model', text: row.model_text, reason: 'unknown', readings: [], actions: ['alias_of', 'keep_pending'] }],
        }] };
    }
    const model = vocabulary.models.find((m) => m.name === line.model)!;
    return {
      key: row.key, line: /^(semi|usad|used)/i.test(row.line ?? '') ? 'used' : 'new', readings: [], error: '',
      model: { text: row.model_text, status: 'model_word', via: 'model_word', model_id: model.id, model_name: model.name, candidates: [], extracted: [] },
      configurations: line.candidates.map((candidate, index) => {
        const draftKey = `${line.n}:${index}`;
        const drafted = drafts.get(draftKey);
        const status = drafted ? 'variante_existente' : candidate.expected;
        const existing = status === 'variante_existente';
        const product = existing ? drafted ?? productOf(line.n, index) : null;
        return {
          status, complete: existing || status === 'combinacion_valida_sin_variante', colour_text: candidate.color_text ?? '',
          option_ids: status === 'combinacion_valida_sin_variante' || existing ? [11, 22, 33, 40 + line.n * 3 + index] : [],
          options: candidate.color_text ? [{ role: 'color', text: candidate.color_text, option_id: 7, label: candidate.color_text, via: 'label', alias_id: null }] : [],
          product_id: product, product_name: product ? `${line.model} variante ${line.n}.${index + 1}` : null,
          draft_pending: Boolean(drafted), missing: candidate.missing ?? [],
          doubts: candidate.expected === 'fuera_de_paleta'
            ? [{ role: 'color', text: candidate.color_text, reason: 'outside', readings: [], actions: ['add_to_palette', 'keep_pending'] }] : [],
          candidates: [],
        };
      }),
    };
  });
  const summary: Record<string, number> = {};
  for (const row of answer) for (const c of row.configurations) summary[c.status] = (summary[c.status] ?? 0) + 1;
  return { rows: answer, summary };
}
