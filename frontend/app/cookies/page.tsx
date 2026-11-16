import type { Metadata } from "next";
import { LegalDocument } from "../components/LegalDocument";
import { PrivacyNotice } from "../components/PrivacyNotice";
import { fetchLegalPolicy } from "../lib/legal-policies";
import { canonical } from "../lib/canonical";

export const dynamic = "force-dynamic";

const META: Metadata = {
  title: "Política de cookies",
  description: "Qué cookies usa esta tienda y cómo cambiar tus preferencias.",
};

export async function generateMetadata(): Promise<Metadata> {
  return { ...META, alternates: await canonical("/cookies") };
}

export default async function CookiesPage() {
  const policy = await fetchLegalPolicy("cookies");
  // Not published yet: the existing cookie and measurement notice, which already
  // describes the consent categories and the preferences button.
  return policy ? <LegalDocument kind="cookies" policy={policy} /> : <PrivacyNotice />;
}
