/**
 * CATALOG-RICH-DESC-01 — what a paste puts into a description editor.
 *
 * ChatGPT gives two kinds of clipboard:
 *   - its «Copiar» button: Markdown as plain text — kept exactly as it is;
 *   - selecting the answer and copying it: rich HTML (`<h2>`, `<strong>`, `<ul>`…) — turned into the same Markdown.
 * Any other rich clipboard (a web page, Word, Google Docs) goes through the same conversion. Plain text is never
 * reformatted: what is not Markdown stays text.
 *
 * The HTML is parsed with `DOMParser` into an inert document: nothing in it runs, loads or is inserted in the
 * page. Only text and a closed set of structures are read; scripts, styles, frames, forms, images and every
 * attribute except a safe link destination are dropped.
 */

import { safeHref } from "./markdown-lite";

/** Tags whose presence means the HTML carries structure worth keeping (otherwise plain text wins). */
const STRUCTURE = /<(h[1-6]|ul|ol|li|strong|b|em|i|a|blockquote|hr)\b/i;

const DROP = new Set(["script", "style", "template", "noscript", "iframe", "frame", "object", "embed", "svg", "math",
  "img", "picture", "video", "audio", "canvas", "button", "input", "select", "textarea", "form", "head", "title", "meta", "link"]);

const BLOCKS = new Set(["p", "div", "section", "article", "header", "footer", "main", "aside", "figure", "figcaption",
  "table", "tbody", "thead", "tfoot", "tr", "dl", "dt", "dd", "address", "details", "summary", "center"]);

/** A text node as Markdown text: characters that would turn into formatting are escaped. */
function escapeText(text: string): string {
  return text.replace(/([\\`*_[\]])/g, "\\$1");
}

/** Escapes a line start that would read as a heading, a list or a quote («# 1», «- 2», «1. 3», «> 4»). */
function escapeLineStart(line: string): string {
  return line.replace(/^(\s*)(#{1,6}\s|[-+>]\s|\d+[.)]\s)/, (_m, space: string, mark: string) => `${space}\\${mark}`);
}

function styleSays(el: Element, prop: string, test: (value: string) => boolean) {
  const style = (el.getAttribute("style") || "").toLowerCase();
  const m = new RegExp(`${prop}\\s*:\\s*([^;]+)`).exec(style);
  return m ? test(m[1].trim()) : false;
}

const boldWeight = (v: string) => v === "bold" || v === "bolder" || Number(v) >= 600;
const normalWeight = (v: string) => v === "normal" || Number(v) > 0 && Number(v) < 600;

/** Inline content of a node, as one Markdown line (line breaks as «\n»). */
function inline(node: Node): string {
  if (node.nodeType === 3) return escapeText((node.textContent || "").replace(/\s+/g, " "));
  if (node.nodeType !== 1) return "";
  const el = node as Element;
  const tag = el.tagName.toLowerCase();
  if (DROP.has(tag)) return "";
  if (tag === "br") return "\n";
  const inner = Array.from(el.childNodes).map(inline).join("");
  const wrap = (mark: string) => {
    const text = inner.trim();
    if (!text) return inner;
    const lead = inner.startsWith(" ") ? " " : "";
    const tail = inner.endsWith(" ") ? " " : "";
    return `${lead}${mark}${text}${mark}${tail}`;
  };
  // Google Docs wraps a whole paste in <b style="font-weight:normal">: that is not bold.
  if ((tag === "strong" || tag === "b") && !styleSays(el, "font-weight", normalWeight)) return wrap("**");
  if ((tag === "em" || tag === "i") && !styleSays(el, "font-style", (v) => v === "normal")) return wrap("*");
  if (tag === "span" && styleSays(el, "font-weight", boldWeight)) return wrap("**");
  if (tag === "span" && styleSays(el, "font-style", (v) => v === "italic")) return wrap("*");
  if (tag === "code" || tag === "kbd" || tag === "samp") {
    const code = (el.textContent || "").replace(/`/g, "'").replace(/\s+/g, " ");
    return code.trim() ? `\`${code}\`` : "";
  }
  if (tag === "a") {
    const href = safeHref(el.getAttribute("href") || "");
    const text = inner.trim();
    if (!text) return "";
    return href && !href.startsWith("#") ? `[${text}](${href.replace(/\(/g, "%28").replace(/\)/g, "%29").replace(/\s/g, "%20")})` : inner;
  }
  return inner;
}

function cleanLines(text: string): string {
  return text.split("\n").map((l) => l.replace(/[  ]+/g, " ").trim()).filter((l, i, all) => l || (i > 0 && all[i - 1])).join("\n").trim();
}

function list(el: Element, depth: number): string {
  const ordered = el.tagName.toLowerCase() === "ol";
  let n = Number(el.getAttribute("start")) || 1;
  const pad = "   ".repeat(depth);
  const lines: string[] = [];
  for (const li of Array.from(el.children)) {
    if (li.tagName.toLowerCase() !== "li") continue;
    const nested: Element[] = [];
    const parts: string[] = [];
    for (const child of Array.from(li.childNodes)) {
      if (child.nodeType === 1 && ["ul", "ol"].includes((child as Element).tagName.toLowerCase())) nested.push(child as Element);
      else parts.push(child.nodeType === 1 && BLOCKS.has((child as Element).tagName.toLowerCase()) ? ` ${inline(child)} ` : inline(child));
    }
    const text = cleanLines(parts.join("")).replace(/\n+/g, " ");
    lines.push(`${pad}${ordered ? `${n++}.` : "-"} ${text}`);
    nested.forEach((sub) => lines.push(list(sub, depth + 1)));
  }
  return lines.join("\n");
}

function blocks(parent: Node, out: string[]) {
  let line = "";
  const flushLine = () => {
    const text = cleanLines(line);
    if (text) out.push(text.split("\n").map(escapeLineStart).join("\n"));
    line = "";
  };
  for (const node of Array.from(parent.childNodes)) {
    if (node.nodeType !== 1) { line += inline(node); continue; }
    const el = node as Element;
    const tag = el.tagName.toLowerCase();
    if (DROP.has(tag)) continue;
    const heading = /^h([1-6])$/.exec(tag);
    if (heading) {
      flushLine();
      const text = cleanLines(inline(el)).replace(/\n+/g, " ");
      if (text) out.push(`${Number(heading[1]) <= 2 ? "##" : "###"} ${text}`);
      continue;
    }
    if (tag === "ul" || tag === "ol") { flushLine(); const l = list(el, 0); if (l) out.push(l); continue; }
    if (tag === "hr") { flushLine(); out.push("---"); continue; }
    if (tag === "blockquote") {
      flushLine();
      const inner: string[] = [];
      blocks(el, inner);
      if (inner.length) out.push(inner.join("\n\n").split("\n").map((l) => `> ${l}`).join("\n"));
      continue;
    }
    if (tag === "pre") {
      flushLine();
      const text = (el.textContent || "").replace(/\n+$/, "");
      if (text.trim()) out.push("```\n" + text.replace(/```/g, "'''") + "\n```");
      continue;
    }
    if (tag === "li") { flushLine(); out.push(`- ${cleanLines(inline(el)).replace(/\n+/g, " ")}`); continue; }
    if (BLOCKS.has(tag) || tag === "body" || tag === "html") {
      flushLine();
      const hasBlocks = Array.from(el.children).some((c) => {
        const t = c.tagName.toLowerCase();
        return BLOCKS.has(t) || /^h[1-6]$/.test(t) || ["ul", "ol", "hr", "blockquote", "pre", "li"].includes(t);
      });
      if (hasBlocks) blocks(el, out);
      else { line = inline(el); flushLine(); }
      continue;
    }
    line += inline(el);
  }
  flushLine();
}

/** Rich clipboard HTML as canonical Markdown. */
export function htmlToMarkdown(html: string): string {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const out: string[] = [];
  blocks(doc.body, out);
  return out.join("\n\n").replace(/\n{3,}/g, "\n\n").trim();
}

/**
 * What to insert for a paste: the HTML converted when it carries structure; otherwise the plain text untouched
 * (only its line endings normalised). `null` means «let the browser paste it as it is».
 */
export function markdownForPaste(html: string | null | undefined, plain: string | null | undefined): string | null {
  if (html && STRUCTURE.test(html)) {
    const md = htmlToMarkdown(html);
    if (md) return md;
  }
  if (plain && plain.includes("\r")) return plain.replace(/\r\n?/g, "\n");
  return null;
}
