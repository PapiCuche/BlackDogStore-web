import type { Metadata } from "next";
import { canonical } from "../lib/canonical";
import { StoreInformation } from "../components/StoreInformation";

export async function generateMetadata(): Promise<Metadata> {
  return { title: "Nosotros", alternates: await canonical("/about") };
}

export default function AboutPage() {
  return <StoreInformation />;
}
