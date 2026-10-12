"use client";

/**
 * «Mi cuenta» — the buyer's own profile in this shop (CLIENT-SEC-01).
 *
 * Name, phone and identity document, as the shop keeps them for this account.
 * The record is made by the first save; opening this page creates nothing. The
 * e-mail is the account's own and is only shown, with whether it was verified.
 *
 * Nobody is asked here for a photograph of a document or of their face.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import {
  PROFILE_FIELDS, ProfileError, fetchProfile, saveProfile,
  type AccountProfile, type DocumentType, type ProfileFields,
} from "../lib/account";
import { getCurrentUser, resendVerification } from "../lib/auth";

const DOCUMENT_TYPES: { value: DocumentType; label: string }[] = [
  { value: "", label: "Sin documento" },
  { value: "dni", label: "DNI" },
  { value: "ruc", label: "RUC" },
  { value: "ce", label: "Carnet de Extranjería" },
];

const inputClass =
  "mt-1.5 w-full rounded-xl border border-bd-border bg-background px-3 py-2.5 text-sm text-foreground outline-none transition-colors focus:border-foreground/25 aria-[invalid=true]:border-danger-border";

function formOf(profile: AccountProfile): ProfileFields {
  return {
    first_name: profile.first_name,
    last_name: profile.last_name,
    phone: profile.phone,
    document_type: profile.document_type,
    document_number: profile.document_number,
  };
}

export default function AccountPage() {
  const router = useRouter();
  const [profile, setProfile] = useState<AccountProfile | null>(null);
  const [form, setForm] = useState<ProfileFields | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<keyof ProfileFields, string>>>({});
  const [resend, setResend] = useState<"idle" | "sending" | "sent" | "failed">("idle");

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const user = await getCurrentUser();
      if (cancelled) return;
      if (!user) {
        router.push("/auth");
        return;
      }
      try {
        const loaded = await fetchProfile();
        if (cancelled) return;
        setProfile(loaded);
        setForm(formOf(loaded));
      } catch (err) {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : "No se pudo cargar tu perfil.");
      }
    })();
    return () => { cancelled = true; };
  }, [router]);

  function change<K extends keyof ProfileFields>(field: K, value: ProfileFields[K]) {
    setForm((current) => (current ? { ...current, [field]: value } : current));
    setSaved(false);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!profile || !form) return;
    const changes: Partial<ProfileFields> = {};
    for (const field of PROFILE_FIELDS) {
      const value = form[field].trim();
      if (value !== profile[field]) (changes as Record<string, string>)[field] = value;
    }
    // A document is its type AND its number: one never travels without the other.
    if ("document_type" in changes || "document_number" in changes) {
      changes.document_type = form.document_type;
      changes.document_number = form.document_type ? form.document_number.trim() : "";
    }
    setError(null);
    setFieldErrors({});
    if (Object.keys(changes).length === 0) {
      setSaved(true);
      return;
    }
    setSaving(true);
    try {
      const updated = await saveProfile(changes);
      setProfile(updated);
      setForm(formOf(updated));
      setSaved(true);
    } catch (err) {
      setSaved(false);
      setError(err instanceof Error ? err.message : "No se pudo guardar tu perfil.");
      if (err instanceof ProfileError) setFieldErrors(err.fields);
    } finally {
      setSaving(false);
    }
  }

  async function sendVerification() {
    if (!profile) return;
    setResend("sending");
    try {
      await resendVerification(profile.email);
      setResend("sent");
    } catch {
      setResend("failed");
    }
  }

  function describedBy(field: keyof ProfileFields) {
    return fieldErrors[field] ? `account-${field}-error` : undefined;
  }

  function problem(field: keyof ProfileFields) {
    return fieldErrors[field] ? (
      <span id={`account-${field}-error`} role="alert" className="mt-1 block text-xs text-danger">
        {fieldErrors[field]}
      </span>
    ) : null;
  }

  const verified = Boolean(profile?.email_verified_at);

  return (
    <main className="mx-auto w-full max-w-3xl space-y-6 px-4 py-10 sm:py-14">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">Mi cuenta</h1>
        <p className="mt-2 text-sm text-muted">
          Tus datos en esta tienda.{" "}
          <Link href="/orders" className="font-semibold text-foreground underline underline-offset-4">
            Ver mis pedidos
          </Link>
        </p>
      </header>

      {loadError ? (
        <p role="alert" className="rounded-xl border border-danger-border bg-danger-surface px-4 py-3 text-sm text-danger">
          {loadError}
        </p>
      ) : null}

      {!profile && !loadError ? <p className="text-sm text-muted" role="status">Cargando…</p> : null}

      {profile && form ? (
        <>
          <section aria-labelledby="account-email-title" className="rounded-2xl border border-bd-border bg-surface p-5">
            <h2 id="account-email-title" className="text-sm font-semibold text-foreground">Correo de la cuenta</h2>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <span className="break-all text-sm text-foreground">{profile.email}</span>
              <span
                className={
                  verified
                    ? "rounded-full border border-success-border bg-success-surface px-2.5 py-0.5 text-xs font-semibold text-success"
                    : "rounded-full border border-bd-border px-2.5 py-0.5 text-xs font-semibold text-muted"
                }
              >
                {verified ? "Verificado" : "Sin verificar"}
              </span>
            </div>
            <p className="mt-2 text-xs text-muted">
              Es el correo con el que entras y recuperas tu cuenta. No se cambia desde aquí.
            </p>
            {!verified && profile.email_verification_available ? (
              <div className="mt-3">
                <button
                  type="button"
                  onClick={sendVerification}
                  disabled={resend === "sending" || resend === "sent"}
                  className="rounded-full border border-bd-border px-4 py-2 text-xs font-semibold text-foreground transition hover:border-foreground/25 disabled:opacity-60"
                >
                  {resend === "sending" ? "Enviando…" : "Reenviar verificación"}
                </button>
                {resend === "sent" ? (
                  <p role="status" className="mt-2 text-xs text-muted">Si el correo está pendiente de verificar, te enviamos un nuevo enlace.</p>
                ) : null}
                {resend === "failed" ? (
                  <p role="alert" className="mt-2 text-xs text-danger">No se pudo enviar el correo. Inténtalo de nuevo.</p>
                ) : null}
              </div>
            ) : null}
          </section>

          <form onSubmit={submit} noValidate className="rounded-2xl border border-bd-border bg-surface p-5">
            <h2 className="text-sm font-semibold text-foreground">Mis datos</h2>
            <p className="mt-1 text-xs text-muted">
              Los usa la tienda para tus pedidos y comprobantes. No pedimos fotos de tu documento.
            </p>

            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <div>
                <label className="block text-xs text-muted">
                  Nombres
                  <input
                    value={form.first_name}
                    onChange={(e) => change("first_name", e.target.value)}
                    autoComplete="given-name"
                    maxLength={120}
                    aria-invalid={Boolean(fieldErrors.first_name)}
                    aria-describedby={describedBy("first_name")}
                    className={inputClass}
                  />
                </label>
                {problem("first_name")}
              </div>
              <div>
                <label className="block text-xs text-muted">
                  Apellidos
                  <input
                    value={form.last_name}
                    onChange={(e) => change("last_name", e.target.value)}
                    autoComplete="family-name"
                    maxLength={120}
                    aria-invalid={Boolean(fieldErrors.last_name)}
                    aria-describedby={describedBy("last_name")}
                    className={inputClass}
                  />
                </label>
                {problem("last_name")}
              </div>
            </div>

            <div className="mt-3">
              <label className="block text-xs text-muted">
                Teléfono
                <input
                  type="tel"
                  inputMode="tel"
                  value={form.phone}
                  onChange={(e) => change("phone", e.target.value)}
                  autoComplete="tel"
                  placeholder="987654321"
                  maxLength={30}
                  aria-invalid={Boolean(fieldErrors.phone)}
                  aria-describedby={describedBy("phone")}
                  className={inputClass}
                />
              </label>
              {problem("phone")}
            </div>

            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <div>
                <label className="block text-xs text-muted">
                  Tipo de documento
                  <select
                    value={form.document_type}
                    onChange={(e) => change("document_type", e.target.value as DocumentType)}
                    aria-invalid={Boolean(fieldErrors.document_type)}
                    aria-describedby={describedBy("document_type")}
                    className={inputClass}
                  >
                    {DOCUMENT_TYPES.map((option) => (
                      <option key={option.value} value={option.value}>{option.label}</option>
                    ))}
                  </select>
                </label>
                {problem("document_type")}
              </div>
              <div>
                <label className="block text-xs text-muted">
                  Número de documento
                  <input
                    value={form.document_type ? form.document_number : ""}
                    onChange={(e) => change("document_number", e.target.value)}
                    disabled={!form.document_type}
                    autoComplete="off"
                    spellCheck={false}
                    inputMode={form.document_type === "ce" ? "text" : "numeric"}
                    maxLength={20}
                    aria-invalid={Boolean(fieldErrors.document_number)}
                    aria-describedby={describedBy("document_number")}
                    className={`${inputClass} disabled:opacity-60`}
                  />
                </label>
                {problem("document_number")}
              </div>
            </div>

            {error ? (
              <p role="alert" className="mt-3 rounded-xl border border-danger-border bg-danger-surface px-4 py-3 text-sm text-danger">
                {error}
              </p>
            ) : null}
            {saved ? <p role="status" className="mt-3 text-sm text-muted">Datos guardados.</p> : null}

            <button
              type="submit"
              disabled={saving}
              className="mt-4 rounded-full bg-foreground px-5 py-2.5 text-xs font-bold uppercase tracking-wider text-background transition hover:bg-foreground/90 disabled:opacity-60"
            >
              {saving ? "Guardando…" : "Guardar"}
            </button>
          </form>
        </>
      ) : null}
    </main>
  );
}
