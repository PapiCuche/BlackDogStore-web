import type { Metadata } from "next";
import { canonical } from "../lib/canonical";
import { StoreInformation } from "../components/StoreInformation";

export async function generateMetadata(): Promise<Metadata> {
  return { title: "Contacto", alternates: await canonical("/contact") };
}

export default function ContactPage() {
  return <StoreInformation contactPage />;
}
