"use client";

/**
 * «Mis direcciones» — the buyer's address book in this shop (DELIVERY-01).
 *
 * Doors and agencies, who receives at each (who may be somebody else), and
 * which one is the default. The list is the server's; an entry is added,
 * corrected, chosen as default or taken out, and after each of those the list
 * is asked for again rather than patched here.
 *
 * Taking an entry out never changes an order: an order copies what it needs
 * when it is made.
 */

import { useCallback, useEffect, useState } from "react";

import {
  AddressError, addressSummary, createAddress, fetchAddressBook, makeDefaultAddress, removeAddress, updateAddress,
  type AddressBook as Book, type AddressDraft, type SavedAddress,
} from "../lib/addresses";

const inputClass =
  "mt-1.5 w-full rounded-xl border border-bd-border bg-background px-3 py-2.5 text-sm text-foreground outline-none transition-colors focus:border-foreground/25 aria-[invalid=true]:border-danger-border";
const smallButton =
  "rounded-full border border-bd-border px-3 py-1.5 text-xs font-semibold text-foreground transition hover:border-foreground/25 disabled:opacity-60";

const EMPTY: AddressDraft = {
  label: "", kind: "address", recipient_name: "", recipient_document_type: "", recipient_document_number: "",
  recipient_phone: "", address_line: "", district: "", city: "", department: "", reference: "",
  carrier: null, agency_name: "", agency_locality: "",
};

function draftOf(entry: SavedAddress): AddressDraft {
  return {
    label: entry.label, kind: entry.kind, recipient_name: entry.recipient_name,
    recipient_document_type: entry.recipient_document_type, recipient_document_number: entry.recipient_document_number,
    recipient_phone: entry.recipient_phone, address_line: entry.address_line, district: entry.district,
    city: entry.city, department: entry.department, reference: entry.reference,
    carrier: entry.carrier, agency_name: entry.agency_name, agency_locality: entry.agency_locality,
  };
}

/** What is sent: trimmed, and a document as its type and number together or not at all. */
function cleaned(draft: AddressDraft): AddressDraft {
  const text = (value: string) => value.trim();
  const documentType = draft.recipient_document_type;
  return {
    ...draft,
    label: text(draft.label), recipient_name: text(draft.recipient_name), recipient_phone: text(draft.recipient_phone),
    recipient_document_number: documentType ? text(draft.recipient_document_number) : "",
    address_line: text(draft.address_line), district: text(draft.district), city: text(draft.city),
    department: text(draft.department), reference: text(draft.reference),
    agency_name: text(draft.agency_name), agency_locality: text(draft.agency_locality),
  };
}

type Props = {
  /** The name to start a new entry's recipient from: the buyer's own. */
  defaultRecipient?: string;
};

export function AddressBook({ defaultRecipient = "" }: Props) {
  const [book, setBook] = useState<Book | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // `null`: no form open. `"new"`: adding. A number: correcting that entry.
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const [draft, setDraft] = useState<AddressDraft>(EMPTY);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    try {
      setBook(await fetchAddressBook());
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "No se pudieron cargar tus direcciones.");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const loaded = await fetchAddressBook();
        if (!cancelled) setBook(loaded);
      } catch (err) {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : "No se pudieron cargar tus direcciones.");
      }
    })();
    return () => { cancelled = true; };
  }, []);

  function open(target: number | "new", from: AddressDraft) {
    setEditing(target);
    setDraft(from);
    setError(null);
    setFieldErrors({});
  }

  function set<K extends keyof AddressDraft>(field: K, value: AddressDraft[K]) {
    setDraft((current) => ({ ...current, [field]: value }));
  }

  async function run(work: () => Promise<unknown>, { closes }: { closes: boolean }) {
    setBusy(true);
    setError(null);
    setFieldErrors({});
    try {
      await work();
      if (closes) setEditing(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar.");
      if (err instanceof AddressError) setFieldErrors(err.fields);
    } finally {
      setBusy(false);
    }
  }

  function save(event: React.FormEvent) {
    event.preventDefault();
    const body = cleaned(draft);
    void run(() => (editing === "new" ? createAddress(body) : updateAddress(editing as number, body)), { closes: true });
  }

  function remove(entry: SavedAddress) {
    if (!window.confirm(`¿Quitar «${entry.label || addressSummary(entry)}» de tus direcciones?`)) return;
    void run(() => removeAddress(entry.id), { closes: editing === entry.id });
  }

  const entries = book?.results ?? [];
  const full = book ? entries.length >= book.limit : false;

  function field(name: keyof AddressDraft, label: string, extra: React.InputHTMLAttributes<HTMLInputElement> = {}) {
    const id = `address-${name}`;
    return (
      <div>
        <label htmlFor={id} className="block text-xs text-muted">{label}</label>
        <input
          id={id}
          value={(draft[name] as string) ?? ""}
          onChange={(e) => set(name, e.target.value as never)}
          aria-invalid={Boolean(fieldErrors[name])}
          aria-describedby={fieldErrors[name] ? `${id}-error` : undefined}
          className={inputClass}
          {...extra}
        />
        {fieldErrors[name] ? (
          <span id={`${id}-error`} role="alert" className="mt-1 block text-xs text-danger">{fieldErrors[name]}</span>
        ) : null}
      </div>
    );
  }

  return (
    <section aria-labelledby="address-book-title" className="rounded-2xl border border-bd-border bg-surface p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="address-book-title" className="text-sm font-semibold text-foreground">Mis direcciones</h2>
        {book && editing === null ? (
          <button
            type="button"
            onClick={() => open("new", { ...EMPTY, recipient_name: defaultRecipient })}
            disabled={full}
            className={smallButton}
          >
            Agregar dirección
          </button>
        ) : null}
      </div>
      <p className="mt-1 text-xs text-muted">
        Dónde recibes tus pedidos en esta tienda: tu casa, tu trabajo o la agencia donde recoges. Puede recibir otra persona.
      </p>
      {full && editing === null ? (
        <p className="mt-2 text-xs text-muted">Llegaste al máximo de {book?.limit} direcciones. Quita una para agregar otra.</p>
      ) : null}

      {loadError ? <p role="alert" className="mt-3 text-xs text-danger">{loadError}</p> : null}
      {!book && !loadError ? <p role="status" className="mt-3 text-xs text-muted">Cargando…</p> : null}
      {book && entries.length === 0 && editing === null ? (
        <p className="mt-3 text-sm text-muted">Todavía no guardaste ninguna dirección.</p>
      ) : null}

      {entries.length > 0 ? (
        <ul className="mt-3 space-y-2">
          {entries.map((entry) => (
            <li key={entry.id} className="rounded-xl border border-bd-border bg-background p-3" data-testid="saved-address">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="break-words text-sm font-semibold text-foreground">
                    {entry.label || (entry.kind === "agency" ? "Agencia" : "Dirección")}
                    {entry.is_default ? (
                      <span className="ml-2 rounded-full border border-bd-border px-2 py-0.5 text-[11px] font-medium text-muted">
                        Predeterminada
                      </span>
                    ) : null}
                  </p>
                  <p className="mt-1 break-words text-xs text-muted">{addressSummary(entry)}</p>
                  <p className="mt-0.5 break-words text-xs text-muted">Recibe: {entry.recipient_name}</p>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {!entry.is_default ? (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void run(() => makeDefaultAddress(entry.id), { closes: false })}
                      className={smallButton}
                      aria-label={`Usar ${entry.label || addressSummary(entry)} como predeterminada`}
                    >
                      Predeterminar
                    </button>
                  ) : null}
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => open(entry.id, draftOf(entry))}
                    className={smallButton}
                    aria-label={`Editar ${entry.label || addressSummary(entry)}`}
                  >
                    Editar
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => remove(entry)}
                    className={smallButton}
                    aria-label={`Quitar ${entry.label || addressSummary(entry)}`}
                  >
                    Quitar
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      ) : null}

      {editing !== null ? (
        <form onSubmit={save} noValidate className="mt-4 border-t border-bd-border pt-4" aria-label="Formulario de dirección">
          <h3 className="text-sm font-semibold text-foreground">{editing === "new" ? "Nueva dirección" : "Editar dirección"}</h3>

          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            {field("label", "Nombre de la dirección (opcional)", { placeholder: "Casa, trabajo…", maxLength: 60 })}
            <div>
              <label htmlFor="address-kind" className="block text-xs text-muted">Tipo</label>
              <select
                id="address-kind"
                value={draft.kind}
                onChange={(e) => set("kind", e.target.value as AddressDraft["kind"])}
                className={inputClass}
              >
                <option value="address">Dirección (entrega a domicilio)</option>
                <option value="agency">Agencia (recojo en agencia)</option>
              </select>
            </div>
          </div>

          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            {field("recipient_name", "Quién recibe", { autoComplete: "name", maxLength: 255 })}
            {field("recipient_phone", "Teléfono de quien recibe", { type: "tel", inputMode: "tel", autoComplete: "tel", placeholder: "987654321", maxLength: 30 })}
            <div>
              <label htmlFor="address-recipient_document_type" className="block text-xs text-muted">Documento de quien recibe</label>
              <select
                id="address-recipient_document_type"
                value={draft.recipient_document_type}
                onChange={(e) => set("recipient_document_type", e.target.value as AddressDraft["recipient_document_type"])}
                aria-invalid={Boolean(fieldErrors.recipient_document_type)}
                className={inputClass}
              >
                <option value="">Sin documento</option>
                <option value="dni">DNI</option>
                <option value="ruc">RUC</option>
                <option value="ce">Carnet de Extranjería</option>
              </select>
              {fieldErrors.recipient_document_type ? (
                <span role="alert" className="mt-1 block text-xs text-danger">{fieldErrors.recipient_document_type}</span>
              ) : null}
            </div>
            {draft.recipient_document_type
              ? field("recipient_document_number", "Número de documento", { autoComplete: "off", maxLength: 20 })
              : null}
          </div>

          {draft.kind === "address" ? (
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <div className="sm:col-span-2">{field("address_line", "Dirección", { autoComplete: "street-address", maxLength: 300 })}</div>
              {field("district", "Distrito", { maxLength: 100 })}
              {field("city", "Ciudad", { autoComplete: "address-level2", maxLength: 100 })}
              {field("department", "Departamento (opcional)", { autoComplete: "address-level1", maxLength: 100 })}
              {field("reference", "Referencia (opcional)", { maxLength: 250 })}
            </div>
          ) : (
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <div>
                <label htmlFor="address-carrier" className="block text-xs text-muted">Agencia de transporte</label>
                <select
                  id="address-carrier"
                  value={draft.carrier ?? ""}
                  onChange={(e) => set("carrier", e.target.value ? Number(e.target.value) : null)}
                  aria-invalid={Boolean(fieldErrors.carrier)}
                  className={inputClass}
                >
                  <option value="">Elige una agencia</option>
                  {(book?.agency_carriers ?? []).map((carrier) => (
                    <option key={carrier.id} value={carrier.id}>{carrier.name}</option>
                  ))}
                </select>
                {fieldErrors.carrier ? <span role="alert" className="mt-1 block text-xs text-danger">{fieldErrors.carrier}</span> : null}
                {(book?.agency_carriers ?? []).length === 0 ? (
                  <span className="mt-1 block text-xs text-muted">Esta tienda todavía no tiene agencias configuradas.</span>
                ) : null}
              </div>
              {field("agency_name", "Sede de la agencia", { placeholder: "Agencia Centro", maxLength: 120 })}
              {field("agency_locality", "Localidad", { placeholder: "Ciudad, departamento", maxLength: 120 })}
              {field("department", "Departamento (opcional)", { maxLength: 100 })}
            </div>
          )}

          {error ? <p role="alert" className="mt-3 text-xs text-danger">{error}</p> : null}

          <div className="mt-4 flex flex-wrap gap-2">
            <button
              type="submit"
              disabled={busy}
              className="rounded-full bg-foreground px-5 py-2.5 text-xs font-bold uppercase tracking-wider text-background transition hover:bg-foreground/90 disabled:opacity-60"
            >
              {busy ? "Guardando…" : "Guardar dirección"}
            </button>
            <button type="button" disabled={busy} onClick={() => setEditing(null)} className={smallButton}>
              Cancelar
            </button>
          </div>
        </form>
      ) : error ? (
        <p role="alert" className="mt-3 text-xs text-danger">{error}</p>
      ) : null}
    </section>
  );
}
