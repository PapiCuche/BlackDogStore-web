"use client";

/**
 * El editor de una integración: borrador → prueba → activación.
 *
 * UN SECRETO NO VUELVE. De uno guardado esta pantalla sólo sabe que está; no hay
 * un campo del que leerlo. Lo que se escribe vive en el estado de este
 * componente hasta que se guarda y entonces se olvida: no va a `localStorage`,
 * ni a la dirección, ni a ningún sitio que sobreviva a la pantalla.
 *
 * Lo que no se reemplaza no se reenvía: el servidor conserva lo que tenía.
 *
 * El formulario se dibuja con los campos que declara el proveedor. Aquí no hay
 * ningún `if` con el nombre de uno.
 */

import { useCallback, useEffect, useState } from "react";

import {
  activateIntegration, fetchIntegration, importIntegrationFromEnvironment, IntegrationError, isUnverified, MODE_LABELS,
  REVOKE_WORD, revokeIntegration, saveIntegrationDraft, setIntegrationEnabled, STATE_TONES, stateLabel,
  testIntegration, testStatusLabel,
  type Integration, type IntegrationField, type IntegrationRow,
} from "@/app/lib/integrations";

import { Button, Confirm, ErrorNote, Panel, Pill, dateTime } from "../../service/components/ServiceUi";

const INPUT =
  "mt-1.5 w-full rounded-xl border border-bd-border bg-background px-3 py-2.5 text-sm text-foreground placeholder:text-muted/60 outline-none transition-colors focus:border-foreground/25 disabled:opacity-60";
const LABEL = "block text-xs text-foreground/50";
const MASK = "••••••••••";

type Values = Record<string, unknown>;
type TestResult = { ok: boolean; status: string; message: string };

function startingValues(fields: IntegrationField[], integration: Integration): Values {
  const stored = (integration.draft ?? integration.active)?.public ?? integration.env?.public ?? {};
  const values: Values = {};
  for (const field of fields) {
    if (field.secret) continue;
    values[field.name] = stored[field.name] ?? field.default ?? (field.kind === "bool" ? false : "");
  }
  return values;
}

function size(bytes: number): string {
  return bytes >= 1024 ? `${Math.floor(bytes / 1024)} KiB` : `${bytes} bytes`;
}

/** The file as the base64 a JSON body carries. Read here; it never touches a URL. */
function asBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("No se pudo leer el archivo."));
    reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
    reader.readAsDataURL(file);
  });
}

/** A small button whose accessible name says WHICH secret it acts on: there is one per secret. */
function NamedButton({
  name, onClick, disabled, children,
}: { name: string; onClick: () => void; disabled?: boolean; children: React.ReactNode }) {
  return (
    <button
      type="button" aria-label={name} onClick={onClick} disabled={disabled}
      className="rounded-lg border border-bd-border px-3 py-1.5 text-[11px] text-muted transition-colors hover:border-foreground/25 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
    >
      {children}
    </button>
  );
}

function PublicInput({
  field, id, value, onChange, disabled,
}: { field: IntegrationField; id: string; value: unknown; onChange: (value: unknown) => void; disabled: boolean }) {
  if (field.kind === "choice") {
    return (
      <select id={id} value={String(value ?? "")} disabled={disabled} onChange={(e) => onChange(e.target.value)} className={INPUT}>
        {(field.choices ?? []).map((choice) => (
          <option key={choice.value} value={choice.value}>{choice.label}</option>
        ))}
      </select>
    );
  }
  if (field.kind === "bool") {
    return (
      <input
        id={id} type="checkbox" checked={Boolean(value)} disabled={disabled}
        onChange={(e) => onChange(e.target.checked)} className="mt-2 h-4 w-4"
      />
    );
  }
  if (field.kind === "textarea") {
    return (
      <textarea id={id} rows={3} value={String(value ?? "")} disabled={disabled} onChange={(e) => onChange(e.target.value)} className={INPUT} />
    );
  }
  if (field.kind === "int") {
    return (
      <input
        id={id} type="number" inputMode="numeric" value={String(value ?? "")} disabled={disabled}
        onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))} className={INPUT}
      />
    );
  }
  return (
    <input
      id={id} type={field.kind === "email" ? "email" : "text"} value={String(value ?? "")} disabled={disabled}
      spellCheck={false} autoComplete="off" onChange={(e) => onChange(e.target.value)} className={INPUT}
    />
  );
}

export function IntegrationEditor({
  id, companyId, onBack,
}: { id: string; companyId: number | null; onBack: () => void }) {
  const [data, setData] = useState<Integration | null>(null);
  const [fields, setFields] = useState<IntegrationField[]>([]);
  const [testFields, setTestFields] = useState<IntegrationField[]>([]);
  const [values, setValues] = useState<Values>({});
  // What was typed for a secret and not saved yet. Emptied on every load.
  const [typed, setTyped] = useState<Record<string, string>>({});
  const [fileNames, setFileNames] = useState<Record<string, string>>({});
  const [replacing, setReplacing] = useState<Record<string, boolean>>({});
  const [removed, setRemoved] = useState<Record<string, boolean>>({});
  const [dirty, setDirty] = useState(false);
  const [testOptions, setTestOptions] = useState<Record<string, string>>({});
  const [result, setResult] = useState<TestResult | null>(null);
  const [confirmWord, setConfirmWord] = useState("");
  const [revokeWord, setRevokeWord] = useState("");
  const [enableWord, setEnableWord] = useState("");
  const [interruptWord, setInterruptWord] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [working, setWorking] = useState(false);

  /** Show what the server holds now, and forget everything that was typed. */
  const show = useCallback((loaded: Integration) => {
    // Only a detail answer carries the fields; the answers of an action repeat them too.
    const declared = loaded.fields ?? [];
    setData(loaded);
    setFields(declared);
    setTestFields(loaded.test_fields ?? []);
    setValues(startingValues(declared, loaded));
    setTyped({});
    setFileNames({});
    setReplacing({});
    setRemoved({});
    setDirty(false);
    setConfirmWord("");
    setRevokeWord("");
    setEnableWord("");
    setInterruptWord("");
    setFieldErrors({});
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchIntegration(id, companyId).then(
      (loaded) => { if (!cancelled) show(loaded); },
      (err) => { if (!cancelled) setError(err); },
    );
    return () => { cancelled = true; };
  }, [id, companyId, show]);

  /** One request at a time, with its failure shown where it belongs. */
  async function run(action: () => Promise<void>) {
    setWorking(true);
    setError(null);
    setNotice("");
    setFieldErrors({});
    try {
      await action();
    } catch (err) {
      setError(err);
      if (err instanceof IntegrationError) {
        setFieldErrors(err.fields);
        if (err.status === 409) {
          // Somebody else got there first: what is on screen is no longer what is stored.
          await fetchIntegration(id, companyId).then(show, () => undefined);
        }
      }
    } finally {
      setWorking(false);
    }
  }

  if (!data) {
    return (
      <div className="space-y-4">
        <div><Button onClick={onBack}>← Integraciones</Button></div>
        {error ? <ErrorNote error={error} /> : <p className="text-sm text-muted">Cargando…</p>}
      </div>
    );
  }

  const { draft, active } = data;
  const stored: IntegrationRow | null = draft ?? active;
  const confirmation = draft?.activation_confirmation ?? null;
  const reactivation = active && !active.enabled ? active.activation_confirmation : null;
  // Something is in flight that stopping or replacing what runs would cut off.
  const interruption = data.interruption;
  const interrupts = Boolean(interruption && interruptWord !== interruption.word);
  const acknowledge = interruption ? interruptWord : "";
  const mode = (draft ?? active)?.mode || data.env?.mode || "";
  const locked = working || !data.store_available;
  const label = (name: string) => fields.find((field) => field.name === name)?.label ?? name;

  const edit = (name: string, value: unknown) => {
    setValues((previous) => ({ ...previous, [name]: value }));
    setDirty(true);
  };
  const type = (name: string, value: string) => {
    setTyped((previous) => ({ ...previous, [name]: value }));
    setDirty(true);
  };

  async function pickFile(field: IntegrationField, file: File | undefined) {
    setFieldErrors((previous) => ({ ...previous, [field.name]: "" }));
    if (!file) return;
    if (field.max_bytes && file.size > field.max_bytes) {
      setFieldErrors((previous) => ({ ...previous, [field.name]: `El archivo supera el máximo de ${size(field.max_bytes ?? 0)}.` }));
      return;
    }
    try {
      type(field.name, await asBase64(file));
      setFileNames((previous) => ({ ...previous, [field.name]: file.name }));
    } catch (err) {
      setFieldErrors((previous) => ({ ...previous, [field.name]: err instanceof Error ? err.message : "No se pudo leer el archivo." }));
    }
  }

  const save = () => run(async () => {
    const secrets: Record<string, string | null> = {};
    for (const field of fields.filter((f) => f.secret)) {
      if (removed[field.name]) secrets[field.name] = null;
      else if (typed[field.name]) secrets[field.name] = typed[field.name];
    }
    show(await saveIntegrationDraft(id, { public: values, secrets, ...(draft ? { version: draft.version } : {}) }, companyId));
    setResult(null);
    setNotice("Borrador guardado.");
  });

  const test = (target: "draft" | "active") => run(async () => {
    const options = Object.fromEntries(Object.entries(testOptions).filter(([, value]) => value.trim()));
    const answer = await testIntegration(id, target, target === "draft" ? options : {}, companyId);
    show(answer.integration);
    setResult({ ok: answer.ok, status: answer.status, message: answer.message });
  });

  const activate = () => run(async () => {
    if (!draft) return;
    show(await activateIntegration(id, draft.version, { confirm: confirmation ? confirmWord : "", acknowledge }, companyId));
    setResult(null);
    setNotice("Configuración activada.");
  });

  const act = (request: () => Promise<Integration>, done: string) => run(async () => {
    show(await request());
    setResult(null);
    setNotice(done);
  });

  function secretField(field: IntegrationField) {
    const inputId = `integration-${id}-${field.name}`;
    const isStored = Boolean(stored?.secrets[field.name]?.configured);

    if (removed[field.name]) {
      return (
        <div key={field.name}>
          <span className={LABEL}>{field.label}</span>
          <p className="mt-1.5 flex flex-wrap items-center gap-2 text-sm text-warning">
            Se quitará al guardar.
            <Button onClick={() => setRemoved((previous) => ({ ...previous, [field.name]: false }))}>Deshacer</Button>
          </p>
        </div>
      );
    }
    if (isStored && !replacing[field.name]) {
      return (
        <div key={field.name}>
          <span className={LABEL}>{field.label}</span>
          <p className="mt-1.5 flex flex-wrap items-center gap-2 text-sm text-foreground">
            <span aria-hidden="true" className="font-mono tracking-widest">{MASK}</span>
            <span className="text-xs text-success">Configurada</span>
            <NamedButton
              name={`Reemplazar ${field.label}`} disabled={locked}
              onClick={() => setReplacing((previous) => ({ ...previous, [field.name]: true }))}
            >
              Reemplazar
            </NamedButton>
          </p>
        </div>
      );
    }
    return (
      <div key={field.name}>
        <label htmlFor={inputId} className={LABEL}>{field.label}</label>
        {field.kind === "file" ? (
          <>
            <input
              id={inputId} type="file" disabled={locked} className={INPUT}
              onChange={(e) => void pickFile(field, e.target.files?.[0])}
            />
            {fileNames[field.name] ? <p className="mt-1 text-xs text-foreground">{fileNames[field.name]}</p> : null}
          </>
        ) : (
          <input
            id={inputId} type="password" autoComplete="new-password" spellCheck={false} disabled={locked}
            value={typed[field.name] ?? ""} onChange={(e) => type(field.name, e.target.value)} className={INPUT}
          />
        )}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div><Button onClick={onBack}>← Integraciones</Button></div>

      <header className="space-y-2">
        <h2 className="font-display text-lg font-semibold tracking-wide text-foreground">
          {data.company ? `${data.label} · ${data.company.name}` : data.label}
        </h2>
        <p className="text-sm text-muted">{data.description}</p>
        <div className="flex flex-wrap items-center gap-2">
          <Pill label={stateLabel(data)} tone={STATE_TONES[data.state]} />
          {MODE_LABELS[mode] ? <Pill label={MODE_LABELS[mode]} tone={mode === "production" ? "bad" : "neutral"} /> : null}
        </div>
      </header>

      {!data.store_available ? (
        <p role="alert" className="rounded-xl border border-danger-border bg-danger-surface px-4 py-3 text-sm text-danger">
          Este servidor no tiene la clave raíz del almacén de secretos (APP_CONFIG_ENCRYPTION_KEY): no se puede
          guardar ni leer ninguna credencial. La pone quien administra el servidor; no se configura desde aquí.
        </p>
      ) : null}

      {interruption ? (
        <div role="alert" className="rounded-xl border border-warning-border bg-warning-surface px-4 py-3">
          <p className="text-sm text-warning">{interruption.message}</p>
          <label htmlFor={`integration-${id}-interrupt`} className={`${LABEL} mt-3`}>Confirmación de la interrupción</label>
          <input
            id={`integration-${id}-interrupt`} value={interruptWord} autoComplete="off" spellCheck={false}
            onChange={(e) => setInterruptWord(e.target.value)} className={`${INPUT} md:w-64`}
          />
        </div>
      ) : null}

      {data.source === "env" && data.env ? (
        <Panel title="Configurado mediante entorno" subtitle="Esta integración funciona hoy con las variables de entorno del servidor. Lo que actives aquí las sustituye, sin reiniciar nada.">
          <p className="text-xs text-muted">
            {data.env.secrets.length
              ? `El entorno tiene: ${data.env.secrets.map(label).join(", ")}. Sus valores no se muestran.`
              : "El entorno no aporta ningún secreto a esta integración."}
          </p>
          {!draft ? (
            <div className="mt-4">
              <Button disabled={locked} onClick={() => void act(() => importIntegrationFromEnvironment(id, companyId), "Copiado a un borrador. Pruébalo y actívalo.")}>
                Copiar a la consola
              </Button>
            </div>
          ) : null}
        </Panel>
      ) : null}

      {active ? (
        <Panel title="En uso" subtitle="Lo que el sistema utiliza ahora mismo.">
          <p className="text-xs text-muted">
            {active.last_tested_at
              ? `Última prueba: ${testStatusLabel(active.last_test_status)} · ${dateTime(active.last_tested_at)}`
              : "Sin probar"}
            {active.updated_by ? ` · cambiada por ${active.updated_by} el ${dateTime(active.updated_at)}` : ""}
          </p>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button disabled={working} onClick={() => void test("active")}>Probar la configuración en uso</Button>
            {active.enabled ? (
              <Confirm
                label="Desactivar" tone="danger" disabled={working || interrupts}
                question="¿Desactivarla? El sistema deja de usarla ahora mismo y no recurre al entorno."
                onConfirm={() => void act(() => setIntegrationEnabled(id, false, { acknowledge }, companyId), "Desactivada.")}
              />
            ) : (
              <Button
                tone="primary" disabled={working || Boolean(reactivation && enableWord !== reactivation.word)}
                onClick={() => void act(() => setIntegrationEnabled(id, true, { confirm: reactivation ? enableWord : "" }, companyId), "Activada de nuevo.")}
              >
                Volver a activar
              </Button>
            )}
          </div>
          {reactivation ? (
            <div className="mt-4">
              <p className="text-sm text-warning">{reactivation.message}</p>
              <label htmlFor={`integration-${id}-enable`} className={`${LABEL} mt-3`}>Confirmación para volver a activar</label>
              <input
                id={`integration-${id}-enable`} value={enableWord} autoComplete="off" spellCheck={false}
                onChange={(e) => setEnableWord(e.target.value)} className={`${INPUT} md:w-64`}
              />
            </div>
          ) : null}
        </Panel>
      ) : null}

      <Panel
        title={draft ? "Borrador" : active ? "Cambiar la configuración" : "Configurar"}
        subtitle="Se guarda como borrador, se prueba y sólo entonces se activa. Hasta activarlo, nada cambia para el sistema."
      >
        <div className="grid gap-4 md:grid-cols-2">
          {fields.map((field) => {
            const inputId = `integration-${id}-${field.name}`;
            return (
              <div key={field.name}>
                {field.secret ? secretField(field) : (
                  <>
                    <label htmlFor={inputId} className={LABEL}>{field.label}</label>
                    <PublicInput
                      field={field} id={inputId} value={values[field.name]} disabled={locked}
                      onChange={(value) => edit(field.name, value)}
                    />
                  </>
                )}
                {field.secret && stored?.secrets[field.name]?.configured && !field.required && !removed[field.name] && !replacing[field.name] ? (
                  <p className="mt-1">
                    <NamedButton
                      name={`Quitar ${field.label}`} disabled={locked}
                      onClick={() => { setRemoved((previous) => ({ ...previous, [field.name]: true })); setDirty(true); }}
                    >
                      Quitar
                    </NamedButton>
                  </p>
                ) : null}
                {field.secret && replacing[field.name] ? (
                  <p className="mt-1">
                    <Button onClick={() => { setReplacing((previous) => ({ ...previous, [field.name]: false })); setTyped((previous) => ({ ...previous, [field.name]: "" })); }}>
                      Cancelar
                    </Button>
                  </p>
                ) : null}
                {field.help ? <p className="mt-1 text-xs text-muted">{field.help}</p> : null}
                {fieldErrors[field.name] ? <p className="mt-1 text-xs text-danger">{fieldErrors[field.name]}</p> : null}
              </div>
            );
          })}
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-3">
          <Button tone="primary" disabled={locked} onClick={() => void save()}>Guardar borrador</Button>
          {notice ? <span role="status" className="text-xs text-success">{notice}</span> : null}
        </div>

        <div className="mt-6 border-t border-bd-border pt-5">
          {testFields.length ? (
            <div className="mb-4 grid gap-4 md:grid-cols-2">
              {testFields.map((field) => {
                const inputId = `integration-${id}-test-${field.name}`;
                return (
                  <div key={field.name}>
                    <label htmlFor={inputId} className={LABEL}>{field.label}</label>
                    <input
                      id={inputId} type={field.kind === "email" ? "email" : "text"} value={testOptions[field.name] ?? ""}
                      disabled={working} autoComplete="off" className={INPUT}
                      onChange={(e) => setTestOptions((previous) => ({ ...previous, [field.name]: e.target.value }))}
                    />
                    {field.help ? <p className="mt-1 text-xs text-muted">{field.help}</p> : null}
                    {fieldErrors[field.name] ? <p className="mt-1 text-xs text-danger">{fieldErrors[field.name]}</p> : null}
                  </div>
                );
              })}
            </div>
          ) : null}
          <div className="flex flex-wrap items-center gap-3">
            <Button disabled={working || !draft || dirty} onClick={() => void test("draft")}>Probar conexión</Button>
            {draft && dirty ? <span className="text-xs text-muted">Guarda los cambios antes de probar.</span> : null}
            {draft && !dirty && !result && draft.last_tested_at ? (
              <span className="text-xs text-muted">
                {`Última prueba del borrador: ${testStatusLabel(draft.last_test_status)} · ${dateTime(draft.last_tested_at)}`}
              </span>
            ) : null}
          </div>
          {result ? (
            <p role="status" className={`mt-3 rounded-lg border px-3 py-2 text-sm ${isUnverified(result.status) ? "border-warning-border bg-warning-surface text-warning" : result.ok ? "border-success-border text-success" : "border-danger-border bg-danger-surface text-danger"}`}>
              <strong className="font-semibold">{testStatusLabel(result.status)}</strong>
              {result.message ? <span className="ml-2 text-xs">{result.message}</span> : null}
            </p>
          ) : null}
        </div>

        <div className="mt-6 border-t border-bd-border pt-5">
          {confirmation ? (
            <div className="mb-4">
              <p className="text-sm text-warning">{confirmation.message}</p>
              <label htmlFor={`integration-${id}-confirm`} className={`${LABEL} mt-3`}>Confirmación</label>
              <input
                id={`integration-${id}-confirm`} value={confirmWord} autoComplete="off" spellCheck={false}
                onChange={(e) => setConfirmWord(e.target.value)} className={`${INPUT} md:w-64`}
              />
            </div>
          ) : null}
          <Confirm
            label="Activar" tone="primary"
            disabled={working || dirty || interrupts || !draft?.validated || Boolean(confirmation && confirmWord !== confirmation.word)}
            question={active ? "¿Sustituir la configuración en uso por este borrador?" : "¿Activar esta configuración?"}
            onConfirm={() => void activate()}
          />
          {draft && !draft.validated ? (
            <span className="ml-3 text-xs text-muted">Se activa lo que pasó la prueba: prueba primero lo que está guardado.</span>
          ) : null}
        </div>
      </Panel>

      {active || draft ? (
        <Panel title="Revocar" subtitle="Borra de la consola las credenciales de esta integración y su borrador. No se puede deshacer; habrá que escribirlas otra vez.">
          <label htmlFor={`integration-${id}-revoke`} className={LABEL}>{`Escribe ${REVOKE_WORD} para borrar esta configuración`}</label>
          <div className="mt-1.5 flex flex-wrap items-center gap-3">
            <input
              id={`integration-${id}-revoke`} value={revokeWord} autoComplete="off" spellCheck={false}
              onChange={(e) => setRevokeWord(e.target.value)}
              className="w-56 rounded-xl border border-bd-border bg-background px-3 py-2.5 text-sm text-foreground outline-none focus:border-foreground/25"
            />
            <Button
              tone="danger" disabled={working || interrupts || revokeWord !== REVOKE_WORD}
              onClick={() => void act(() => revokeIntegration(id, { confirm: revokeWord, acknowledge }, companyId), "Revocada.")}
            >
              Revocar
            </Button>
          </div>
        </Panel>
      ) : null}

      <ErrorNote error={error} />
    </div>
  );
}
