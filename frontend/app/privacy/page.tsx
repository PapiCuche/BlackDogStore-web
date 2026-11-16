import type { Metadata } from "next";
import { LegalDocument } from "../components/LegalDocument";
import { PrivacyNotice } from "../components/PrivacyNotice";
import { fetchLegalPolicy } from "../lib/legal-policies";
import { canonical } from "../lib/canonical";

// The published text can change from the panel at any time: never prerendered.
export const dynamic = "force-dynamic";

const META: Metadata = {
  title: "Política de privacidad",
  description: "Cómo tratamos tus datos personales y cómo ejercer tus derechos.",
};

export async function generateMetadata(): Promise<Metadata> {
  return { ...META, alternates: await canonical("/privacy") };
}

export default async function PrivacyPage() {
  const policy = await fetchLegalPolicy("privacy");
  // Not published yet: the existing notice (identity, consent and measurement)
  // stays, so this address never answers with an empty page.
  return policy ? <LegalDocument kind="privacy" policy={policy} /> : <PrivacyNotice />;
}
