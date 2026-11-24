"use client";

import { unitLabel } from "../../lib/unit-entry";

type SoldItem = {
  product: number;
  name: string;
  units?: { serial_number: string; imei: string; imei2: string; method: string }[];
};

/**
 * The devices that left with a sale, on the success screen: «<product>: IMEI …».
 *
 * STOCK-UX-WALK — the line wraps between words (`break-words`). With `break-all` a 390 px phone cut the product name
 * in the middle of a word («Inter nacional»); an IMEI of 15 digits fits whole on its own line.
 */
export function PosSoldUnits({ items }: { items: SoldItem[] }) {
  return (
    <>
      {items.flatMap((item) =>
        (item.units ?? []).map((sold) => (
          <p key={`${item.product}-${sold.serial_number}-${sold.imei}`} className="break-words">
            {item.name}: <span className="font-mono">{unitLabel(sold)}</span>
          </p>
        )),
      )}
    </>
  );
}
