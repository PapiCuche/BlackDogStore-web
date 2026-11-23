import type { Metadata } from "next";
import Link from "next/link";
import { LegalDocument } from "../../components/LegalDocument";
import { fetchLegalPolicy, legalHeading } from "../../lib/legal-policies";
import { canonical } from "../../lib/canonical";

export const dynamic = "force-dynamic";

const HEADING = legalHeading("whatsapp");

const META: Metadata = {
  title: HEADING,
  description: "Cómo tratamos los datos de quienes nos escriben por WhatsApp, Instagram Direct o Facebook Messenger.",
};

export async function generateMetadata(): Promise<Metadata> {
  return { ...META, alternates: await canonical("/privacy/whatsapp") };
}

export default async function MessagingPrivacyPage() {
  const policy = await fetchLegalPolicy("whatsapp");
  if (policy) return <LegalDocument kind="whatsapp" policy={policy} />;
  // Controlled answer: nothing is claimed that the shop has not published.
  return (
    <main className="bg-background text-foreground">
      <article className="v3-container max-w-3xl px-4 py-14 sm:px-6 lg:py-20">
        <h1 className="font-display text-3xl font-bold text-balance">{HEADING}</h1>
        <p className="mt-6 text-[15px] leading-7">
          Esta tienda todavía no ha publicado este documento. Mientras tanto, consulta la{" "}
          <Link href="/privacy" className="underline underline-offset-4">política de privacidad</Link>{" "}
          o escríbenos desde <Link href="/contact" className="underline underline-offset-4">Contacto</Link>.
        </p>
      </article>
    </main>
  );
}
