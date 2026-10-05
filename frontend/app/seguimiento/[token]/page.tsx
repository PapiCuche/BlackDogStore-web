import type { Metadata } from "next";

import { TrackingView } from "./TrackingView";

/**
 * El enlace ES la credencial: la página no se indexa y no entrega su dirección
 * a ningún otro sitio (el botón de WhatsApp sale de aquí).
 */
export const metadata: Metadata = {
  title: "Seguimiento de tu reparación",
  robots: { index: false, follow: false, nocache: true },
  referrer: "no-referrer",
};

export default async function TrackingPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <TrackingView token={token} />;
}
