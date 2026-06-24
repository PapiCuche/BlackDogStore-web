export const STORE_PHONE = "+51 936 449 536";
export const STORE_WHATSAPP = "51936449536";
export const STORE_RUC = "20610159886";
export const STORE_LEGAL_NAME = "CMAU CORP E.I.R.L.";
export const STORE_ADDRESS = "Octavio Muñoz Najar 238, Tienda 104";

export const TERMS_TEXT =
  "Declaro que la información ingresada es correcta y acepto que Black Dog Store, " +
  `operado por ${STORE_LEGAL_NAME} con RUC ${STORE_RUC}, utilice estos datos ` +
  "únicamente para procesar mi compra, coordinar la entrega y emitir el comprobante " +
  "correspondiente.";

export const WARRANTY_TEXT =
  "Acepto la política de garantía de Black Dog Store. Entiendo que la garantía puede " +
  "variar según el tipo de producto, condición del equipo y términos informados al " +
  "momento de la compra.";

export const DELIVERY_DESCRIPTIONS: Record<string, string> = {
  pickup_store:
    `Podrás recoger tu pedido en nuestra tienda ubicada en ${STORE_ADDRESS}. ` +
    `Te contactaremos al ${STORE_PHONE} para coordinar la entrega.`,
  delivery_arequipa:
    "Realizamos delivery dentro de Arequipa. El equipo de Black Dog Store se comunicará " +
    "contigo para coordinar la entrega.",
  national_shipping:
    "Realizamos envíos a nivel nacional. El equipo de Black Dog Store confirmará los " +
    "datos de envío antes del despacho.",
};

export const DOCUMENT_TYPE_LABELS: Record<string, string> = {
  dni: "DNI",
  ruc: "RUC",
  ce: "Carnet de Extranjería",
};

export const DELIVERY_METHOD_LABELS: Record<string, string> = {
  pickup_store: "Recojo en tienda",
  delivery_arequipa: "Delivery Arequipa",
  national_shipping: "Envío nacional",
};

export const RECEIPT_TYPE_LABELS: Record<string, string> = {
  boleta: "Boleta",
  factura: "Factura",
};
