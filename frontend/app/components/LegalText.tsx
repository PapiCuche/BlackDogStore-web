import { Fragment } from "react";
import { linkify, parseLegalText } from "../lib/legal-text";

/**
 * LEGAL-POLICIES-01 — renders a legal document's plain text as React elements.
 * Never `dangerouslySetInnerHTML`: a `<script>` typed in the panel is shown as text.
 */
function Inline({ text }: { text: string }) {
  return (
    <>
      {linkify(text).map((segment, index) => {
        if (segment.kind === "email") {
          return <a key={index} href={`mailto:${segment.value}`}>{segment.value}</a>;
        }
        if (segment.kind === "url") {
          return <a key={index} href={segment.value} target="_blank" rel="noopener noreferrer">{segment.value}</a>;
        }
        return <Fragment key={index}>{segment.value}</Fragment>;
      })}
    </>
  );
}

export function LegalText({ text }: { text: string }) {
  const blocks = parseLegalText(text);
  return (
    <div className="legal-text space-y-4 text-[15px] leading-7 [overflow-wrap:anywhere] [&_a]:underline [&_a]:underline-offset-4 [&_h2]:mt-10 [&_h2]:text-xl [&_h2]:font-semibold [&_h3]:mt-6 [&_h3]:text-base [&_h3]:font-semibold [&_ul]:list-disc [&_ul]:space-y-1 [&_ul]:pl-6 [&_p]:whitespace-pre-line">
      {blocks.map((block, index) => {
        if (block.type === "h2") return <h2 key={index}><Inline text={block.text} /></h2>;
        if (block.type === "h3") return <h3 key={index}><Inline text={block.text} /></h3>;
        if (block.type === "ul") {
          return (
            <ul key={index}>
              {block.items.map((item, i) => <li key={i}><Inline text={item} /></li>)}
            </ul>
          );
        }
        return <p key={index}><Inline text={block.text} /></p>;
      })}
    </div>
  );
}
