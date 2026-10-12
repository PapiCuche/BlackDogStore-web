"use client";

/**
 * DELIVERY-01 — «Usar una dirección guardada», at checkout.
 *
 * A CONVENIENCE OF THE BROWSER AND NOTHING ELSE. Choosing an entry of the
 * address book only FILLS the address fields the form already has; what is
 * sent to the server is the same form, with the same fields, as if the buyer
 * had typed them. No address id travels.
 *
 * Only for a signed-in buyer, only doors (an agency is not an address this
 * form can take), and it can never get in the way of buying: if the book
 * cannot be read, the select is simply not there.
 */

import { useEffect, useRef, useState } from "react";

import { addressSummary, fetchAddressBook, type SavedAddress } from "../lib/addresses";

type Props = {
  /** Whether somebody is signed in. A guest has no book. */
  enabled: boolean;
  /** Whether the form's address fields are all empty right now. */
  isEmpty: boolean;
  /** Fill the form from this entry. */
  onUse: (entry: SavedAddress) => void;
  className?: string;
  labelClassName?: string;
};

export function SavedAddressSelect({ enabled, isEmpty, onUse, className, labelClassName }: Props) {
  const [doors, setDoors] = useState<SavedAddress[]>([]);
  const [chosen, setChosen] = useState("");
  // What the form looked like when the book arrived, and the latest filler:
  // read once on arrival, so typing afterwards is never overwritten.
  const emptyOnArrival = useRef(isEmpty);
  const fill = useRef(onUse);
  useEffect(() => {
    emptyOnArrival.current = isEmpty;
    fill.current = onUse;
  });

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    void (async () => {
      try {
        const book = await fetchAddressBook();
        if (cancelled) return;
        const found = book.results.filter((entry) => entry.kind === "address");
        setDoors(found);
        // The default one, preselected — only into a form nobody has typed in.
        const preferred = found.find((entry) => entry.is_default);
        if (preferred && emptyOnArrival.current) {
          setChosen(String(preferred.id));
          fill.current(preferred);
        }
      } catch {
        // No book, no select. Buying goes on.
      }
    })();
    return () => { cancelled = true; };
  }, [enabled]);

  if (!enabled || doors.length === 0) return null;

  return (
    <div>
      <label htmlFor="checkout-saved-address" className={labelClassName}>Usar una dirección guardada</label>
      <select
        id="checkout-saved-address"
        value={chosen}
        onChange={(event) => {
          setChosen(event.target.value);
          const entry = doors.find((door) => String(door.id) === event.target.value);
          if (entry) onUse(entry);
        }}
        className={className}
      >
        <option value="">Escribir otra dirección</option>
        {doors.map((door) => (
          <option key={door.id} value={door.id}>
            {door.label ? `${door.label} — ${addressSummary(door)}` : addressSummary(door)}
          </option>
        ))}
      </select>
    </div>
  );
}
