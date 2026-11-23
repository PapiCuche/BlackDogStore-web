import Link from "next/link";
import { LEGAL_PAGES, legalHeading, type LegalKind, type PublishedLegalPolicy } from "../lib/legal-policies";
import { CookiePreferencesButton } from "./CookiePreferencesButton";
import { LegalText } from "./LegalText";

function publishedOn(iso: string | null): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString("es-PE", { day: "numeric", month: "long", year: "numeric", timeZone: "America/Lima" });
}

/** LEGAL-POLICIES-01 — one published legal document, with links to the other two. */
export function LegalDocument({ kind, policy }: { kind: LegalKind; policy: PublishedLegalPolicy }) {
  const date = publishedOn(policy.published_at);
  const others = (Object.keys(LEGAL_PAGES) as LegalKind[]).filter((k) => k !== kind);
  return (
    <main className="bg-background text-foreground">
      <article className="v3-container max-w-3xl px-4 py-14 sm:px-6 lg:py-20">
        <h1 className="font-display text-3xl font-bold text-balance">{legalHeading(kind)}</h1>
        {date ? <p className="mt-3 text-sm text-muted">Publicada el {date}.</p> : null}
        <div className="mt-8">
          <LegalText text={policy.text} />
        </div>
        {kind === "cookies" ? (
          <section className="mt-10 border-t border-bd-border pt-6">
            <p className="text-sm text-muted">Puedes aceptar o rechazar las categorías opcionales en cualquier momento.</p>
            <CookiePreferencesButton className="mt-4" />
          </section>
        ) : null}
        <nav aria-label="Otras políticas" className="mt-12 flex flex-wrap gap-x-6 gap-y-2 border-t border-bd-border pt-6 text-sm">
          {others.map((k) => (
            <Link key={k} href={LEGAL_PAGES[k].href} className="underline underline-offset-4 hover:text-foreground">
              {LEGAL_PAGES[k].title}
            </Link>
          ))}
        </nav>
      </article>
    </main>
  );
}
