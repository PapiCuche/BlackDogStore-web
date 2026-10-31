/**
 * INV-TEXT-03 · PR A — mensajes de WhatsApp SINTÉTICOS para el reconocedor local.
 *
 * Todo es inventado: proveedores, personas, series y precios. Los IMEI tienen 15 dígitos y pasan Luhn (el
 * reconocedor los valida), pero salen de un prefijo de prueba (`35999900…`) y se calculan aquí: ninguno es
 * de un equipo real ni se copia de un chat.
 */
import { luhnCheckDigit } from "@/app/admin/lib/inventory-text-preview";

/** A fake IMEI that passes Luhn: test prefix 35999900, then `n` in six digits, then the check digit. */
export function fakeImei(n: number): string {
  const body = `35999900${String(n).padStart(6, "0")}`;
  return body + luhnCheckDigit(body);
}

/** A fake serial, Apple-shaped (letters and digits, 12 characters). */
export const fakeSerial = (n: number): string => `FAKE${String(n).padStart(4, "0")}TEST`;

/** Exported chat (iOS): «[31/10/26, 10:15] Nombre: …». A mixed list: new, used and accessories. */
export const IOS_MIXED = [
  "[31/10/26, 10:15] Proveedor Ficticio: Buenos días 👋 les paso stock de hoy 🔥",
  "[31/10/26, 10:15] Proveedor Ficticio: 📱 *iPhone 15 Pro 256GB Titanio Natural* nuevo - S/ 4,250.00",
  "📱 iPhone 14 128gb Negro sem bat 89% S/2500",
  "✅ IPH 13 128 azul seminuevo 87% 1850 soles",
  "• Cable USB-C x5 S/ 25",
  "• Cargador 20W x 2 45",
  "– AirPods Pro 2 2 unidades 750",
  "[31/10/26, 10:16] Proveedor Ficticio: Cualquier consulta me escriben 🙏",
].join("\n");

/** Exported chat (Android): «31/10/2026 10:15 - Nombre: …», multi-line messages, labelled fields. */
export const ANDROID_LABELLED = [
  "31/10/2026 10:15 - Vendedor Demo: iPhone 12 64gb blanco",
  "Precio: 2500",
  "Bat: 85%",
  `IMEI: ${fakeImei(1)}`,
  `NS: ${fakeSerial(1)}`,
  "31/10/2026 10:16 - Vendedor Demo: iPhone 11 128 negro",
  "Costo: 1200",
  "Precio de venta: 1650",
  "Estado: B",
  `IMEI1 ${fakeImei(2)}`,
  `IMEI2 ${fakeImei(3)}`,
  `Serie ${fakeSerial(2)}`,
].join("\n");

/** One message, one line, several devices, the ways people separate them. */
export const MANY_ON_ONE_LINE = "iPhone 13 128 azul 1800, iPhone 12 64 negro 1300 / iPhone 11 64 blanco 950";

/** A long message (~300 lines): headers, noise and products, to prove the reader does not slow down or drift. */
export function longMessage(products = 150): string {
  const out: string[] = ["[31/10/26, 09:00] Proveedor Ficticio: *LISTA ACTUALIZADA* ✅"];
  for (let i = 0; i < products; i++) {
    out.push(i % 3 === 0 ? `• iPhone 15 128gb negro x${(i % 4) + 1} S/ 3,${String(100 + i).padStart(3, "0")}.00` :
      i % 3 === 1 ? `• Cable USB-C 1m x${(i % 5) + 1} ${20 + (i % 9)}` : `IPH 13 128 azul sem bat ${80 + (i % 20)}% ${1700 + i}`);
    out.push(i % 2 ? "" : "--------");
  }
  return out.join("\n");
}

/** What the person might get from a commercial (customer-facing) list: ONLY a «Precio:», never a cost. */
export const COMMERCIAL_PRICE_ONLY = "iPhone 13 Pro 256GB grafito\nPrecio: 3299\nBatería 91%";
