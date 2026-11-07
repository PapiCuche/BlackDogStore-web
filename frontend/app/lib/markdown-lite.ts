/**
 * CATALOG-RICH-DESC-01 — the closed Markdown subset of product and model descriptions.
 *
 * A description is stored as Markdown text (the same TextField as before, no migration). This module turns it
 * into a small tree that `RichText` renders as React elements. NOTHING here produces HTML: a `<script>` or an
 * `onerror=` in a description is text and React escapes it. Links keep only safe destinations (`safeHref`).
 *
 * Supported, on purpose and nothing else:
 *   blocks  — `#`/`##` (h2), `###`… (h3), paragraphs, `-`/`*`/`+`/`•` lists, `1.`/`1)` lists (one nested level
 *             or more by indentation), `---` separators, `>` quotes, ``` fenced blocks (shown as text);
 *   inline  — `**bold**`/`__bold__`, `*italic*`/`_italic_` (an `_` inside a word is a letter: snake_case stays),
 *             `` `code` ``, `[text](url)`, `\` escapes, and a line break inside a paragraph.
 * A description written before this change (plain text with line breaks) renders as before: paragraphs with
 * their line breaks. Unbalanced markers are left as the characters they are.
 */

export type Inline =
  | { t: "text"; v: string }
  | { t: "strong"; c: Inline[] }
  | { t: "em"; c: Inline[] }
  | { t: "code"; v: string }
  | { t: "link"; href: string; c: Inline[] }
  | { t: "br" };

export type ListItem = { c: Inline[]; children: Block[] };

export type Block =
  | { t: "h"; level: 2 | 3; c: Inline[] }
  | { t: "p"; c: Inline[] }
  | { t: "ul"; items: ListItem[] }
  | { t: "ol"; start: number; items: ListItem[] }
  | { t: "hr" }
  | { t: "quote"; c: Inline[] }
  | { t: "pre"; v: string };

/** The longest description the server accepts (`DESCRIPTION_MAX_LENGTH` in the backend). */
export const DESCRIPTION_MAX_LENGTH = 20000;

const SAFE_SCHEMES = new Set(["http:", "https:", "mailto:", "tel:"]);

/** A link destination that cannot run code: http(s), mailto, tel, a path of this site or an anchor. */
export function safeHref(raw: string): string | null {
  // Browsers ignore control characters and spaces inside a scheme («java\tscript:»): so do we, before looking.
  const href = raw.trim().replace(/[\u0000-\u001F\u007F\s]+/g, "");
  if (!href) return null;
  if (href.startsWith("#")) return href;
  if (href.startsWith("/") && !href.startsWith("//") && !href.startsWith("/\\")) return href;
  const scheme = /^([a-z][a-z0-9+.-]*):/i.exec(href);
  if (!scheme) return null;
  return SAFE_SCHEMES.has(`${scheme[1].toLowerCase()}:`) ? href : null;
}

// ── Inline ─────────────────────────────────────────────────────────────────────────────────────────────────────

const ESCAPABLE = "\\`*_{}[]()#+-.!>|~";

function isSpace(ch: string | undefined) {
  return ch === undefined || /\s/.test(ch);
}

function isWordChar(ch: string | undefined) {
  return ch !== undefined && /[\p{L}\p{N}]/u.test(ch);
}

function pushText(out: Inline[], v: string) {
  if (!v) return;
  const last = out[out.length - 1];
  if (last && last.t === "text") last.v += v;
  else out.push({ t: "text", v });
}

/** Index of the closing delimiter `delim` for an opener that ends at `from`, or -1. */
function findCloser(src: string, from: number, delim: string): number {
  const ch = delim[0];
  for (let i = from; i < src.length; i++) {
    if (src[i] === "\\") { i++; continue; }
    if (src[i] === "`") {
      const end = src.indexOf("`", i + 1);
      if (end > 0) { i = end; continue; }
    }
    if (src.startsWith(delim, i)) {
      // `**` must not be the start of a longer run used as a different delimiter («***»).
      const before = src[i - 1];
      const after = src[i + delim.length];
      if (isSpace(before)) continue; // a closer is never preceded by a space
      if (ch === "_" && isWordChar(after)) continue; // «snake_case_words» are letters
      if (delim.length === 1 && after === ch) { i++; continue; }
      return i;
    }
  }
  return -1;
}

export function parseInline(src: string): Inline[] {
  const out: Inline[] = [];
  let i = 0;
  while (i < src.length) {
    const ch = src[i];
    if (ch === "\\" && i + 1 < src.length && ESCAPABLE.includes(src[i + 1])) {
      pushText(out, src[i + 1]);
      i += 2;
      continue;
    }
    if (ch === "\n") {
      out.push({ t: "br" });
      i++;
      continue;
    }
    if (ch === "`") {
      const end = src.indexOf("`", i + 1);
      if (end > i + 1) {
        out.push({ t: "code", v: src.slice(i + 1, end) });
        i = end + 1;
        continue;
      }
    }
    if (ch === "[") {
      // The destination may hold one level of parentheses («javascript:alert(1)» must be recognised to be refused).
      const m = /^\[([^\]\n]{1,300})\]\(((?:[^()\s]|\([^()\s]*\)){1,2000})(?:\s+"[^"\n]*")?\)/.exec(src.slice(i));
      if (m) {
        const href = safeHref(m[2]);
        const label = parseInline(m[1]);
        if (href) out.push({ t: "link", href, c: label });
        else label.forEach((node) => (node.t === "text" ? pushText(out, node.v) : out.push(node)));
        i += m[0].length;
        continue;
      }
    }
    if (ch === "*" || ch === "_") {
      const double = src[i + 1] === ch;
      const delim = double ? ch + ch : ch;
      const open = i + delim.length;
      const before = src[i - 1];
      const after = src[open];
      const canOpen = !isSpace(after) && !(ch === "_" && isWordChar(before)) && after !== ch;
      if (canOpen) {
        const close = findCloser(src, open, delim);
        if (close > open) {
          const inner = parseInline(src.slice(open, close));
          out.push(double ? { t: "strong", c: inner } : { t: "em", c: inner });
          i = close + delim.length;
          continue;
        }
      }
      // Not a delimiter: the run is text («2 * 3», «**» alone, «___»).
      let j = i;
      while (src[j] === ch) j++;
      pushText(out, src.slice(i, j));
      i = j;
      continue;
    }
    let j = i + 1;
    while (j < src.length && !"\\\n`[*_".includes(src[j])) j++;
    pushText(out, src.slice(i, j));
    i = j;
  }
  return out;
}

// ── Blocks ─────────────────────────────────────────────────────────────────────────────────────────────────────

const HEADING = /^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$/;
const RULE = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
const BULLET = /^( *)([-*+•●▪])[ \t]+(.*)$/;
const ORDERED = /^( *)(\d{1,9})[.)][ \t]+(.*)$/;
const QUOTE = /^ {0,3}>[ \t]?(.*)$/;
const FENCE = /^ {0,3}(```|~~~)/;

type Marker = { indent: number; ordered: boolean; number: number; text: string };

function listMarker(line: string): Marker | null {
  const b = BULLET.exec(line);
  if (b) return { indent: b[1].length, ordered: false, number: 0, text: b[3] };
  const o = ORDERED.exec(line);
  if (o) return { indent: o[1].length, ordered: true, number: Number(o[2]), text: o[3] };
  return null;
}

function isBlockStart(line: string) {
  return HEADING.test(line) || RULE.test(line) || QUOTE.test(line) || FENCE.test(line) || listMarker(line) !== null;
}

/** Parse the list that starts at `lines[start]`; returns the block and the index after it. */
function parseList(lines: string[], start: number, baseIndent: number): [Block, number] {
  const first = listMarker(lines[start])!;
  const items: ListItem[] = [];
  let i = start;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      // A blank line keeps the list open only if the next non-blank line continues it.
      let k = i + 1;
      while (k < lines.length && !lines[k].trim()) k++;
      const next = k < lines.length ? listMarker(lines[k]) : null;
      if (next && next.indent >= baseIndent && (next.indent > baseIndent || next.ordered === first.ordered)) { i = k; continue; }
      break;
    }
    const m = listMarker(line);
    if (m && m.indent >= baseIndent && m.indent < baseIndent + 2) {
      if (m.ordered !== first.ordered) break;
      items.push({ c: parseInline(m.text.trim()), children: [] });
      i++;
      continue;
    }
    if (m && m.indent >= baseIndent + 2 && items.length) {
      const [child, next] = parseList(lines, i, m.indent);
      items[items.length - 1].children.push(child);
      i = next;
      continue;
    }
    if (m && m.indent < baseIndent) break;
    if (!m && /^\s+/.test(line) && items.length && !isBlockStart(line.trim())) {
      // An indented line continues the item above («lazy» continuation).
      const item = items[items.length - 1];
      item.c.push({ t: "text", v: " " }, ...parseInline(line.trim()));
      i++;
      continue;
    }
    break;
  }
  const block: Block = first.ordered ? { t: "ol", start: first.number || 1, items } : { t: "ul", items };
  return [block, i];
}

export function parseMarkdown(source: string | null | undefined): Block[] {
  const lines = String(source ?? "").replace(/\r\n?/g, "\n").replace(/\t/g, "    ").split("\n");
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  const flush = () => {
    if (paragraph.length) blocks.push({ t: "p", c: parseInline(paragraph.join("\n").trim()) });
    paragraph = [];
  };
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { flush(); i++; continue; }
    const fence = FENCE.exec(line);
    if (fence) {
      flush();
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trimStart().startsWith(fence[1])) body.push(lines[i++]);
      i++;
      blocks.push({ t: "pre", v: body.join("\n") });
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      flush();
      blocks.push({ t: "h", level: heading[1].length <= 2 ? 2 : 3, c: parseInline(heading[2]) });
      i++;
      continue;
    }
    if (RULE.test(line)) { flush(); blocks.push({ t: "hr" }); i++; continue; }
    const marker = listMarker(line);
    if (marker && (paragraph.length === 0 || !marker.ordered || marker.number === 1)) {
      flush();
      const [list, next] = parseList(lines, i, marker.indent);
      blocks.push(list);
      i = next;
      continue;
    }
    const quote = QUOTE.exec(line);
    if (quote) {
      flush();
      const body: string[] = [];
      while (i < lines.length && QUOTE.test(lines[i])) body.push(QUOTE.exec(lines[i++])![1]);
      blocks.push({ t: "quote", c: parseInline(body.join("\n").trim()) });
      continue;
    }
    paragraph.push(line.trim());
    i++;
  }
  flush();
  return blocks;
}

// ── Plain text (search engines, cards) ─────────────────────────────────────────────────────────────────────────

function inlineText(nodes: Inline[]): string {
  return nodes.map((n) => (n.t === "text" || n.t === "code" ? n.v : n.t === "br" ? " " : inlineText(n.c))).join("");
}

function blockText(block: Block): string[] {
  switch (block.t) {
    case "h": case "p": case "quote": return [inlineText(block.c)];
    case "pre": return [block.v];
    case "hr": return [];
    case "ul": case "ol": return block.items.flatMap((item) => [inlineText(item.c), ...item.children.flatMap(blockText)]);
  }
}

/** The description as one line of plain text, without any marker, cut at `max` characters on a word. */
export function markdownToPlainText(source: string | null | undefined, max?: number): string {
  const text = parseMarkdown(source).flatMap(blockText).map((s) => s.trim()).filter(Boolean)
    .map((s) => (/[.!?:;…]$/.test(s) ? s : `${s}.`)).join(" ").replace(/\s+/g, " ").trim();
  if (!max || text.length <= max) return text;
  const cut = text.slice(0, max - 1);
  const space = cut.lastIndexOf(" ");
  return `${(space > max * 0.6 ? cut.slice(0, space) : cut).replace(/[\s.,;:]+$/, "")}…`;
}
