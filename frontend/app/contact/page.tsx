import { StoreInformation } from "../components/StoreInformation";

export const metadata = { title: "Contacto", alternates: { canonical: "/contact" } };

export default function ContactPage() {
  return <StoreInformation contactPage />;
}
