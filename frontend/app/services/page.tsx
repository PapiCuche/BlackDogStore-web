import type { Metadata } from "next";
import { ServicesContent } from "./ServicesContent";

/**
 * M12F.1 — la descripción ya no afirma nada que no se pueda respaldar.
 *
 * Decía «Diagnóstico gratuito», y no hay ninguna política en el proyecto que lo
 * sostenga. Los metadatos son de las pocas cosas que siguen compiladas a
 * propósito —son estructura, no contenido comercial del taller— y por eso lo
 * que digan tiene que ser seguro para cualquier tenant.
 */
export const metadata: Metadata = {
  title: "Servicio técnico especializado en equipos Apple",
  description:
    "Reparación de equipos Apple: pantalla, batería, tapa trasera y glass. "
    + "Te indicamos qué repuesto se usará y en qué condiciones antes de empezar.",
};

export default function ServicesPage() {
  return <ServicesContent />;
}
