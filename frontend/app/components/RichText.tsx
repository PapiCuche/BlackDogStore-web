"use client";

/**
 * CATALOG-RICH-DESC-01 — a product or model description, formatted.
 *
 * Renders the closed Markdown subset of `markdown-lite` as React elements: titles, paragraphs, bold, italics,
 * lists, separators and links with a safe destination. There is no `dangerouslySetInnerHTML`: any HTML a
 * description carries is shown as text. `collapsible` folds a long description behind «Leer más» so the
 * purchase area stays in view.
 */

import { Fragment, useId, useMemo, useState, type ReactNode } from "react";
import { markdownToPlainText, parseMarkdown, type Block, type Inline } from "../lib/markdown-lite";

const LINK = "font-semibold text-foreground underline underline-offset-4 hover:opacity-80 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary";

function renderInline(nodes: Inline[], key = "i"): ReactNode[] {
  return nodes.map((node, index) => {
    const k = `${key}-${index}`;
    switch (node.t) {
      case "text": return <Fragment key={k}>{node.v}</Fragment>;
      case "br": return <br key={k} />;
      case "strong": return <strong key={k} className="font-semibold text-foreground">{renderInline(node.c, k)}</strong>;
      case "em": return <em key={k} className="italic">{renderInline(node.c, k)}</em>;
      case "code": return <code key={k} className="rounded bg-surface px-1 py-0.5 font-mono text-[0.92em] text-foreground">{node.v}</code>;
      case "link": {
        const external = /^(https?:)?\/\//i.test(node.href);
        return (
          <a
            key={k}
            href={node.href}
            className={LINK}
            {...(external ? { target: "_blank", rel: "nofollow noopener noreferrer ugc" } : {})}
          >
            {renderInline(node.c, k)}
          </a>
        );
      }
    }
  });
}

function renderBlocks(blocks: Block[], key = "b"): ReactNode[] {
  return blocks.map((block, index) => {
    const k = `${key}-${index}`;
    switch (block.t) {
      case "h":
        return block.level === 2
          ? <h2 key={k} className="mt-6 text-base font-semibold leading-snug text-foreground first:mt-0 sm:text-lg">{renderInline(block.c, k)}</h2>
          : <h3 key={k} className="mt-5 text-sm font-semibold leading-snug text-foreground first:mt-0 sm:text-base">{renderInline(block.c, k)}</h3>;
      case "p":
        return <p key={k} className="mt-3 first:mt-0">{renderInline(block.c, k)}</p>;
      case "quote":
        return <blockquote key={k} className="mt-3 border-l-2 border-bd-border pl-4 first:mt-0">{renderInline(block.c, k)}</blockquote>;
      case "pre":
        return <pre key={k} className="mt-3 whitespace-pre-wrap break-words rounded-lg bg-surface p-3 font-mono text-[0.9em] first:mt-0">{block.v}</pre>;
      case "hr":
        return <hr key={k} className="my-5 border-bd-border" />;
      case "ul":
      case "ol": {
        const items = block.items.map((item, i) => (
          <li key={`${k}-${i}`} className="pl-1">
            {renderInline(item.c, `${k}-${i}`)}
            {item.children.length ? renderBlocks(item.children, `${k}-${i}-c`) : null}
          </li>
        ));
        return block.t === "ul"
          ? <ul key={k} className="mt-3 list-disc space-y-1.5 pl-5 marker:text-muted first:mt-0 [&_ol]:mt-1.5 [&_ul]:mt-1.5">{items}</ul>
          : <ol key={k} start={block.start} className="mt-3 list-decimal space-y-1.5 pl-5 marker:text-muted first:mt-0 [&_ol]:mt-1.5 [&_ul]:mt-1.5">{items}</ol>;
      }
    }
  });
}

type Props = {
  source: string | null | undefined;
  className?: string;
  /** Fold descriptions whose text is longer than this behind «Leer más». */
  collapsible?: boolean;
  collapseAfter?: number;
  testId?: string;
};

export function RichText({ source, className = "", collapsible = false, collapseAfter = 600, testId }: Props) {
  const blocks = useMemo(() => parseMarkdown(source), [source]);
  const long = useMemo(() => collapsible && markdownToPlainText(source).length > collapseAfter, [collapsible, collapseAfter, source]);
  const [open, setOpen] = useState(false);
  const id = useId();
  if (!blocks.length) return null;
  const folded = long && !open;
  return (
    <div className={className} data-testid={testId}>
      <div
        id={id}
        className={folded ? "relative max-h-72 overflow-hidden [mask-image:linear-gradient(to_bottom,black_70%,transparent)]" : undefined}
      >
        {renderBlocks(blocks)}
      </div>
      {long ? (
        <button
          type="button"
          aria-expanded={open}
          aria-controls={id}
          onClick={() => setOpen((v) => !v)}
          className="mt-3 inline-flex min-h-11 items-center text-sm font-bold text-foreground underline underline-offset-4 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
        >
          {open ? "Leer menos" : "Leer más"}
        </button>
      ) : null}
    </div>
  );
}
