/**
 * SERIAL-OPTIONAL-01 — what names a device to a person: its serial, or its IMEI when it entered without one (an
 * article that carries an IMEI may enter by it alone). Never «null».
 */
export const unitIdentifier = (unit: { serial_number?: string | null; imei?: string | null }): string =>
  unit.serial_number || (unit.imei ? `IMEI ${unit.imei}` : "");
