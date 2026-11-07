"use client";

/**
 * CATALOG-RICH-DESC-01 — the description editor of products and models.
 *
 * A text field with a formatting bar and a preview. What is saved is Markdown text (the same field as before):
 * the bar writes the marks (`**negrita**`, `## Título`, `- viñeta`…) so nobody has to know them, and anyone who
 * does can type them. A paste from ChatGPT keeps its titles, bold and lists (`paste-to-markdown`). The preview
 * is exactly what the store shows (`RichText`). Undo and redo cover typing, the bar and pastes.
 *
 * Nothing is interpreted as HTML: the field holds text, and the store renders it as React elements.
 */

import { useCallback, useId, useRef, useState, type ClipboardEvent, type KeyboardEvent, type ReactNode } from "react";
import { RichText } from "../../components/RichText";
import { DESCRIPTION_MAX_LENGTH } from "../../lib/markdown-lite";
import { markdownForPaste } from "../../lib/paste-to-markdown";

type Props = {
  id: string;
  label: ReactNode;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  maxLength?: number;
  rows?: number;
  labelClassName?: string;
  inputClassName?: string;
};

type Edit = { value: string; start: number; end: number };

const BUTTON = "inline-flex min-h-9 min-w-9 items-center justify-center rounded-md border border-transparent px-2 text-sm text-foreground hover:border-bd-border hover:bg-background focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-primary disabled:cursor-not-allowed disabled:opacity-40";

/** Wrap the selection in `mark` (or remove it when the selection is already wrapped). */
function toggleWrap(e: Edit, mark: string, placeholder: string): Edit {
  const { value, start, end } = e;
  const selected = value.slice(start, end);
  if (selected.startsWith(mark) && selected.endsWith(mark) && selected.length >= mark.length * 2) {
    const inner = selected.slice(mark.length, selected.length - mark.length);
    return { value: value.slice(0, start) + inner + value.slice(end), start, end: start + inner.length };
  }
  if (value.slice(start - mark.length, start) === mark && value.slice(end, end + mark.length) === mark) {
    return { value: value.slice(0, start - mark.length) + selected + value.slice(end + mark.length), start: start - mark.length, end: end - mark.length };
  }
  // Marks hug the words: spaces selected around the text stay outside.
  const text = selected || placeholder;
  const lead = text.match(/^\s*/)![0];
  const tail = text.match(/\s*$/)![0];
  const core = text.trim() || placeholder;
  const insert = `${lead}${mark}${core}${mark}${tail}`;
  const s = start + lead.length + mark.length;
  return { value: value.slice(0, start) + insert + value.slice(end), start: s, end: s + core.length };
}

/** The whole lines touched by the selection. */
function lineRange(value: string, start: number, end: number) {
  const from = value.lastIndexOf("\n", start - 1) + 1;
  let to = value.indexOf("\n", end > start && value[end - 1] === "\n" ? end - 1 : end);
  if (to === -1) to = value.length;
  return { from, to };
}

const HEADING_PREFIX = /^#{1,6}\s+/;
const LIST_PREFIX = /^(\s*)([-*+•]|\d{1,9}[.)])\s+/;

function setHeading(e: Edit, level: 2 | 3): Edit {
  const { from, to } = lineRange(e.value, e.start, e.end);
  const lines = e.value.slice(from, to).split("\n");
  const prefix = `${"#".repeat(level)} `;
  const all = lines.every((l) => l.startsWith(prefix));
  const next = lines.map((l) => {
    const bare = l.replace(HEADING_PREFIX, "").replace(LIST_PREFIX, "");
    return all ? bare : bare.trim() ? prefix + bare : l;
  }).join("\n");
  const value = e.value.slice(0, from) + next + e.value.slice(to);
  return { value, start: from, end: from + next.length };
}

function setList(e: Edit, ordered: boolean): Edit {
  const { from, to } = lineRange(e.value, e.start, e.end);
  const lines = e.value.slice(from, to).split("\n");
  const marker = ordered ? /^\d{1,9}[.)]\s+/ : /^[-*+•]\s+/;
  const all = lines.filter((l) => l.trim()).every((l) => marker.test(l));
  let n = 0;
  const next = lines.map((l) => {
    if (!l.trim()) return l;
    const bare = l.replace(LIST_PREFIX, "").replace(HEADING_PREFIX, "");
    if (all) return bare;
    n += 1;
    return `${ordered ? `${n}.` : "-"} ${bare}`;
  }).join("\n");
  const value = e.value.slice(0, from) + next + e.value.slice(to);
  return { value, start: from, end: from + next.length };
}

function insertLink(e: Edit): Edit {
  const text = e.value.slice(e.start, e.end).trim() || "texto del enlace";
  const url = "https://";
  const insert = `[${text}](${url})`;
  const value = e.value.slice(0, e.start) + insert + e.value.slice(e.end);
  const s = e.start + text.length + 3;
  return { value, start: s, end: s + url.length };
}

function insertBlock(e: Edit, block: string): Edit {
  const before = e.value.slice(0, e.start).replace(/\s+$/, "");
  const after = e.value.slice(e.end).replace(/^\s+/, "");
  const head = before ? `${before}\n\n` : "";
  const value = `${head}${block}${after ? `\n\n${after}` : "\n\n"}`;
  const pos = head.length + block.length + 2;
  return { value, start: Math.min(pos, value.length), end: Math.min(pos, value.length) };
}

function insertText(e: Edit, text: string): Edit {
  const value = e.value.slice(0, e.start) + text + e.value.slice(e.end);
  const pos = e.start + text.length;
  return { value, start: pos, end: pos };
}

const TOOLS: { label: string; text: ReactNode; run: (e: Edit) => Edit; title: string }[] = [
  { label: "Negrita", title: "Negrita (Ctrl+B)", text: <b>B</b>, run: (e) => toggleWrap(e, "**", "texto en negrita") },
  { label: "Cursiva", title: "Cursiva (Ctrl+I)", text: <i className="font-serif">I</i>, run: (e) => toggleWrap(e, "*", "texto en cursiva") },
  { label: "Título", title: "Título", text: <span className="text-xs font-bold">H2</span>, run: (e) => setHeading(e, 2) },
  { label: "Subtítulo", title: "Subtítulo", text: <span className="text-xs font-bold">H3</span>, run: (e) => setHeading(e, 3) },
  { label: "Lista con viñetas", title: "Lista con viñetas", text: <span aria-hidden>•≡</span>, run: (e) => setList(e, false) },
  { label: "Lista numerada", title: "Lista numerada", text: <span aria-hidden className="text-xs">1.≡</span>, run: (e) => setList(e, true) },
  { label: "Enlace", title: "Enlace", text: <span aria-hidden className="text-xs underline">link</span>, run: (e) => insertLink(e) },
  { label: "Separador", title: "Línea separadora", text: <span aria-hidden>―</span>, run: (e) => insertBlock(e, "---") },
];

export function DescriptionEditor({
  id, label, value, onChange, disabled = false, maxLength = DESCRIPTION_MAX_LENGTH, rows = 10,
  labelClassName = "block text-xs text-muted", inputClassName = "",
}: Props) {
  const area = useRef<HTMLTextAreaElement>(null);
  const [tab, setTab] = useState<"write" | "preview">("write");
  const [history, setHistory] = useState<{ stack: string[]; index: number }>({ stack: [value], index: 0 });
  /** When the last typed change was recorded: typing within 800 ms is one step of the history. */
  const typedAt = useRef(0);
  const helpId = useId();
  const countId = useId();

  // A value set from outside (a reset, the server's answer) becomes the current step.
  if (history.stack[history.index] !== value) {
    setHistory((h) => {
      const stack = [...h.stack.slice(0, h.index + 1), value].slice(-200);
      return { stack, index: stack.length - 1 };
    });
  }

  const record = useCallback((next: string, coalesce: boolean) => {
    const now = Date.now();
    const join = coalesce && now - typedAt.current < 800;
    typedAt.current = coalesce ? now : 0;
    setHistory((h) => {
      if (join && h.index > 0) {
        const stack = [...h.stack.slice(0, h.index), next];
        return { stack, index: stack.length - 1 };
      }
      const stack = [...h.stack.slice(0, h.index + 1), next].slice(-200);
      return { stack, index: stack.length - 1 };
    });
  }, []);

  const apply = useCallback((edit: Edit) => {
    record(edit.value, false);
    onChange(edit.value);
    requestAnimationFrame(() => {
      const el = area.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(edit.start, edit.end);
    });
  }, [onChange, record]);

  const current = (): Edit => {
    const el = area.current;
    return { value, start: el?.selectionStart ?? value.length, end: el?.selectionEnd ?? value.length };
  };

  const step = (delta: number) => {
    const index = history.index + delta;
    if (index < 0 || index >= history.stack.length) return;
    typedAt.current = 0;
    setHistory({ stack: history.stack, index });
    onChange(history.stack[index]);
  };

  const canUndo = history.index > 0;
  const canRedo = history.index < history.stack.length - 1;

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    const mod = event.metaKey || event.ctrlKey;
    if (!mod) return;
    const key = event.key.toLowerCase();
    if (key === "z") { event.preventDefault(); step(event.shiftKey ? 1 : -1); return; }
    if (key === "y") { event.preventDefault(); step(1); return; }
    if (key === "b") { event.preventDefault(); apply(toggleWrap(current(), "**", "texto en negrita")); return; }
    if (key === "i") { event.preventDefault(); apply(toggleWrap(current(), "*", "texto en cursiva")); }
  };

  const onPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const md = markdownForPaste(event.clipboardData.getData("text/html"), event.clipboardData.getData("text/plain"));
    if (md === null) {
      // The browser pastes the plain text itself; the next change is its own step in the history.
      typedAt.current = 0;
      return;
    }
    event.preventDefault();
    apply(insertText(current(), md));
  };

  const tools = TOOLS;

  const tooLong = value.length > maxLength;

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-2">
        <label htmlFor={id} className={labelClassName}>{label}</label>
        <div role="tablist" aria-label="Modo del editor" className="inline-flex rounded-md border border-bd-border p-0.5 text-xs">
          {(["write", "preview"] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              role="tab"
              aria-selected={tab === mode}
              onClick={() => setTab(mode)}
              className={`min-h-8 rounded px-3 font-semibold ${tab === mode ? "bg-foreground text-background" : "text-muted hover:text-foreground"}`}
            >
              {mode === "write" ? "Escribir" : "Vista previa"}
            </button>
          ))}
        </div>
      </div>
      <div className="mt-1.5 overflow-hidden rounded-lg border border-bd-border bg-surface">
        <div role="toolbar" aria-label="Formato de la descripción" className="flex flex-wrap items-center gap-0.5 border-b border-bd-border px-1.5 py-1">
          {tools.map((tool) => (
            <button
              key={tool.label}
              type="button"
              className={BUTTON}
              aria-label={tool.label}
              title={tool.title}
              disabled={disabled || tab !== "write"}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => apply(tool.run(current()))}
            >
              {tool.text}
            </button>
          ))}
          <span aria-hidden className="mx-1 h-5 w-px bg-bd-border" />
          <button type="button" className={BUTTON} aria-label="Deshacer" title="Deshacer (Ctrl+Z)" disabled={disabled || !canUndo} onClick={() => step(-1)}>↶</button>
          <button type="button" className={BUTTON} aria-label="Rehacer" title="Rehacer (Ctrl+Shift+Z)" disabled={disabled || !canRedo} onClick={() => step(1)}>↷</button>
        </div>
        <textarea
          ref={area}
          id={id}
          value={value}
          rows={rows}
          disabled={disabled}
          hidden={tab !== "write"}
          aria-describedby={`${helpId} ${countId}`}
          aria-invalid={tooLong || undefined}
          onChange={(e) => { record(e.target.value, true); onChange(e.target.value); }}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          spellCheck
          className={`block w-full resize-y border-0 bg-transparent px-3 py-2.5 text-sm leading-6 text-foreground focus:outline-none ${inputClassName}`}
        />
        {tab === "preview" ? (
          <div role="tabpanel" aria-label="Vista previa de la descripción" className="max-h-[32rem] min-h-40 overflow-y-auto px-4 py-3 text-sm leading-7 text-muted">
            {value.trim() ? <RichText source={value} /> : <p className="text-muted">Todavía no hay descripción.</p>}
          </div>
        ) : null}
      </div>
      <div className="mt-1.5 flex flex-wrap justify-between gap-2 text-xs text-muted">
        <p id={helpId}>
          Puedes pegar el texto de ChatGPT: los títulos, las negritas y las listas se conservan. También puedes escribir
          {" "}<code>**negrita**</code>, <code>*cursiva*</code>, <code>## Título</code>, <code>- viñeta</code> o <code>1. paso</code>.
        </p>
        <p id={countId} className={tooLong ? "font-semibold text-danger" : undefined} aria-live="polite">
          {value.length.toLocaleString("es-PE")} / {maxLength.toLocaleString("es-PE")} caracteres
          {tooLong ? " · demasiado larga: acórtala para poder guardar" : ""}
        </p>
      </div>
    </div>
  );
}
