/**
 * LEGAL-POLICIES-01 — the plain-text format of the legal documents.
 *
 * The panel stores PLAIN TEXT. This turns it into blocks that React renders as
 * elements, so nothing typed in the panel is ever interpreted as HTML:
 *
 *   # Título        → heading (h2)
 *   ## Subtítulo    → sub-heading (h3)
 *   - elemento      → list item (consecutive lines form one list)
 *   blank line      → new paragraph; a single line break stays inside it
 *
 * Shared by the public pages and the panel's preview, so what the editor
 * previews is exactly what the public reads.
 */

export type LegalBlock =
  | { type: "h2"; text: string }
  | { type: "h3"; text: string }
  | { type: "p"; text: string }
  | { type: "ul"; items: string[] };

export function parseLegalText(raw: string): LegalBlock[] {
  const blocks: LegalBlock[] = [];
  let paragraph: string[] = [];
  let list: string[] = [];

  const flushParagraph = () => {
    if (paragraph.length) blocks.push({ type: "p", text: paragraph.join("\n") });
    paragraph = [];
  };
  const flushList = () => {
    if (list.length) blocks.push({ type: "ul", items: list });
    list = [];
  };

  for (const rawLine of (raw ?? "").replace(/\r\n?/g, "\n").split("\n")) {
    const line = rawLine.trimEnd();
    const trimmed = line.trim();
    if (!trimmed) {
      flushParagraph();
      flushList();
      continue;
    }
    const heading = /^(#{1,2})\s+(.+)$/.exec(trimmed);
    if (heading) {
      flushParagraph();
      flushList();
      blocks.push({ type: heading[1].length === 1 ? "h2" : "h3", text: heading[2].trim() });
      continue;
    }
    const item = /^[-•*]\s+(.+)$/.exec(trimmed);
    if (item) {
      flushParagraph();
      list.push(item[1].trim());
      continue;
    }
    flushList();
    paragraph.push(trimmed);
  }
  flushParagraph();
  flushList();
  return blocks;
}

export type LegalSegment = { kind: "text" | "email" | "url"; value: string };

/** Emails and https links become links; everything else stays text. */
export function linkify(text: string): LegalSegment[] {
  const pattern = /([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})|(https:\/\/[^\s<>"')]+[^\s<>"').,;:])/g;
  const out: LegalSegment[] = [];
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    const index = match.index ?? 0;
    if (index > last) out.push({ kind: "text", value: text.slice(last, index) });
    out.push(match[1] ? { kind: "email", value: match[1] } : { kind: "url", value: match[2] });
    last = index + match[0].length;
  }
  if (last < text.length) out.push({ kind: "text", value: text.slice(last) });
  return out;
}
