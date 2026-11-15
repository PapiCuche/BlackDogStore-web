import type { Metadata } from "next";
import Link from "next/link";
import { LegalDocument } from "../../components/LegalDocument";
import { fetchLegalPolicy } from "../../lib/legal-policies";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Privacidad de WhatsApp y CRM",
  description: "Cómo tratamos los datos de quienes nos escriben por WhatsApp.",
};

export default async function WhatsAppPrivacyPage() {
  const policy = await fetchLegalPolicy("whatsapp");
  if (policy) return <LegalDocument kind="whatsapp" policy={policy} />;
  // Controlled answer: nothing is claimed that the shop has not published.
  return (
    <main className="bg-background text-foreground">
      <article className="v3-container max-w-3xl px-4 py-14 sm:px-6 lg:py-20">
        <h1 className="font-display text-3xl font-bold">Privacidad de WhatsApp y CRM</h1>
        <p className="mt-6 text-[15px] leading-7">
          Esta tienda todavía no ha publicado este documento. Mientras tanto, consulta la{" "}
          <Link href="/privacy" className="underline underline-offset-4">política de privacidad</Link>{" "}
          o escríbenos desde <Link href="/contact" className="underline underline-offset-4">Contacto</Link>.
        </p>
      </article>
    </main>
  );
}
