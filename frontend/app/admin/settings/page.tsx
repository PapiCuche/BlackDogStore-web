"use client";

/**
 * Administración → Configuración — Phase 3.
 *
 * The screen where a business stops being described by constants in the code and
 * starts describing itself. What it edits reaches customer emails, receipt PDFs
 * and the public storefront, so two things matter more than the layout:
 *
 *   1. WHAT IT CANNOT REACH. `Company.slug` (routing) and `Company.is_active`
 *      (whether the business may transact) are platform decisions and are not on
 *      this form. They are not hidden — the endpoint behind it cannot write them.
 *
 *   2. ERRORS ARE SHOWN, NEVER SWALLOWED. Field-level messages come back from
 *      the backend and are rendered under the field they belong to: "el color
 *      debe tener el formato #RRGGBB" tells somebody what to fix.
 *
 * Everything saves in ONE request, so a rejected colour cannot leave the company
 * name already changed.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AdminShell } from "../components/AdminShell";
import { InternalControlGuard, type InternalContext } from "../components/InternalControlGuard";
import { DashboardSection } from "../components/dashboard-ui";
import { SequenceSettings } from "../components/SequenceSettings";
import {
  fetchCompanyConfiguration,
  updateCompanyConfiguration,
  type CompanyConfiguration,
} from "../lib/internal-api";

type Draft = Record<string, string>;

const FIELD = (label: string, name: string, extra?: Partial<FieldProps>): FieldProps => ({
  label,
  name,
  ...extra,
});

type FieldProps = {
  label: string;
  name: string;
  type?: "text" | "email" | "url" | "textarea" | "color";
  hint?: string;
  readOnly?: boolean;
  placeholder?: string;
};

const GENERAL: FieldProps[] = [
  FIELD("Nombre comercial", "name", { hint: "Como lo ven tus clientes." }),
  FIELD("Razón social", "legal_name"),
  FIELD("Identificación fiscal (RUC)", "tax_id"),
  FIELD("Zona horaria", "timezone", { placeholder: "America/Lima", hint: "Nombre IANA." }),
  FIELD("Moneda", "currency", {
    readOnly: true,
    hint: "El checkout cobra en la moneda configurada por la plataforma. Cambiarla aquí no cambiaría lo que la pasarela cobra.",
  }),
];

const CONTACT: FieldProps[] = [
  FIELD("Email de contacto", "contact_email", { type: "email" }),
  FIELD("Teléfono", "phone"),
  FIELD("WhatsApp", "whatsapp_number", {
    placeholder: "51987654321",
    hint: "Solo dígitos, con código de país y sin «+».",
  }),
  FIELD("Sitio web", "website_url", { type: "url" }),
  FIELD("Facebook", "facebook_url", { type: "url" }),
  FIELD("Instagram", "instagram_url", { type: "url" }),
  FIELD("Dirección legal", "legal_address"),
  FIELD("Ciudad", "city"),
  FIELD("País (ISO)", "country_code", { placeholder: "PE" }),
];

const BRANDING: FieldProps[] = [
  FIELD("Logo (URL)", "logo_url", { type: "url" }),
  FIELD("Primario", "primary_color", { type: "color" }),
  FIELD("Acento", "accent_color", { type: "color" }),
  FIELD("Fondo", "background_color", { type: "color" }),
  FIELD("Superficie", "surface_color", { type: "color" }),
  FIELD("Texto", "text_color", { type: "color" }),
  FIELD("Bordes", "border_color", { type: "color" }),
];

const POLICIES: FieldProps[] = [
  FIELD("Política de garantía", "warranty_policy_text", {
    type: "textarea",
    hint: "Texto plano. Aparece en los emails y PDFs de tus pedidos.",
  }),
  FIELD("Garantía (URL)", "warranty_policy_url", { type: "url" }),
  FIELD("Términos y condiciones (URL)", "terms_url", { type: "url" }),
  FIELD("Privacidad (URL)", "privacy_url", { type: "url" }),
];

const NOTIFICATIONS: FieldProps[] = [
  FIELD("Email de nuevas ventas", "order_notification_email", {
    type: "email",
    hint: "A dónde llegan los avisos de venta de ESTA empresa. Si está vacío no se envía ninguno — nunca se usa el correo de otra empresa.",
  }),
];

const ALL_FIELDS = [...GENERAL, ...CONTACT, ...BRANDING, ...POLICIES, ...NOTIFICATIONS];

function draftFrom(config: CompanyConfiguration): Draft {
  const s = config.settings as unknown as Record<string, unknown>;
  const c = config.company as unknown as Record<string, unknown>;
  const draft: Draft = {};
  for (const field of ALL_FIELDS) {
    const source = ["name", "legal_name", "tax_id"].includes(field.name) ? c : s;
    draft[field.name] = String(source[field.name] ?? "");
  }
  return draft;
}

function Field({
  field,
  value,
  error,
  disabled,
  onChange,
}: {
  field: FieldProps;
  value: string;
  error?: string;
  disabled: boolean;
  onChange: (name: string, value: string) => void;
}) {
  const base =
    "w-full rounded-lg border bg-background/40 px-3 py-2 text-sm text-foreground outline-none transition focus:border-bd-border disabled:opacity-50";
  const borderClass = error ? "border-danger-border" : "border-bd-border";

  return (
    <div>
      <label
        className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted"
        htmlFor={`cfg-${field.name}`}
      >
        {field.label}
      </label>

      {field.type === "textarea" ? (
        <textarea
          id={`cfg-${field.name}`}
          rows={4}
          maxLength={2000}
          className={`${base} ${borderClass}`}
          value={value}
          disabled={disabled || field.readOnly}
          onChange={(e) => onChange(field.name, e.target.value)}
        />
      ) : field.type === "color" ? (
        <div className="flex items-center gap-2">
          <input
            type="color"
            aria-label={field.label}
            className="h-9 w-12 shrink-0 cursor-pointer rounded border border-bd-border bg-background/40"
            value={/^#[0-9A-Fa-f]{6}$/.test(value) ? value : "#000000"}
            disabled={disabled}
            onChange={(e) => onChange(field.name, e.target.value.toUpperCase())}
          />
          <input
            id={`cfg-${field.name}`}
            className={`${base} ${borderClass} font-mono`}
            value={value}
            placeholder="#RRGGBB"
            disabled={disabled}
            onChange={(e) => onChange(field.name, e.target.value)}
          />
        </div>
      ) : (
        <input
          id={`cfg-${field.name}`}
          type={field.type === "email" ? "email" : field.type === "url" ? "url" : "text"}
          className={`${base} ${borderClass}`}
          value={value}
          placeholder={field.placeholder}
          disabled={disabled || field.readOnly}
          onChange={(e) => onChange(field.name, e.target.value)}
        />
      )}

      {error ? (
        <p className="mt-1.5 text-xs text-danger">{error}</p>
      ) : field.hint ? (
        <p className="mt-1.5 text-[11px] text-muted">{field.hint}</p>
      ) : null}
    </div>
  );
}

function BrandPreview({ draft, name }: { draft: Draft; name: string }) {
  const hex = (v: string, fallback: string) =>
    /^#[0-9A-Fa-f]{6}$/.test(v) ? v : fallback;
  const bg = hex(draft.background_color, "#0A0A0A");
  const surface = hex(draft.surface_color, "#141414");
  const text = hex(draft.text_color, "#FAFAFA");
  const border = hex(draft.border_color, "#262626");
  const primary = hex(draft.primary_color, "#FFFFFF");
  const accent = hex(draft.accent_color, "#A1A1AA");

  return (
    <div
      className="rounded-xl border p-5"
      style={{ background: bg, borderColor: border, color: text }}
    >
      <div className="flex items-center gap-3">
        {/^https?:\/\//.test(draft.logo_url) ? (
          /* eslint-disable-next-line @next/next/no-img-element */
          <img src={draft.logo_url} alt="" className="h-8 w-auto object-contain" />
        ) : null}
        <span className="font-display text-lg font-black uppercase tracking-tight">
          {name || "Tu empresa"}
        </span>
      </div>
      <div
        className="mt-4 rounded-lg border p-4"
        style={{ background: surface, borderColor: border }}
      >
        <p className="text-sm">Producto de ejemplo</p>
        <p className="mt-1 text-xs" style={{ color: accent }}>
          Así se verá tu tienda
        </p>
        <span
          className="mt-3 inline-block rounded-full px-4 py-1.5 text-xs font-bold uppercase tracking-widest"
          style={{ background: primary, color: bg }}
        >
          Comprar
        </span>
      </div>
    </div>
  );
}

function SettingsContent({ user, ctx }: { user: InternalContext["user"]; ctx: InternalContext }) {
  const companyId = ctx.dashboard?.company?.id ?? null;
  const [config, setConfig] = useState<CompanyConfiguration | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    const data = await fetchCompanyConfiguration(companyId);
    setConfig(data);
    setDraft(draftFrom(data));
    return data;
  }, [companyId]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        await load();
        if (!cancelled) setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "No se pudo cargar la configuración.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [load]);

  function set(name: string, value: string) {
    setSaved(false);
    setDraft((d) => ({ ...d, [name]: value }));
    setErrors((e) => {
      if (!(name in e)) return e;
      const next = { ...e };
      delete next[name];
      return next;
    });
  }

  async function save() {
    setSaving(true);
    setErrors({});
    setError(null);
    try {
      // `currency` is read-only server-side; sending it would be noise.
      const { currency: _readOnly, ...payload } = draft;
      const updated = await updateCompanyConfiguration(companyId, payload);
      setConfig(updated);
      setDraft(draftFrom(updated));
      setSaved(true);
    } catch (err) {
      const fieldErrors = (err as { fields?: Record<string, string> }).fields;
      if (fieldErrors) setErrors(fieldErrors);
      setError(err instanceof Error ? err.message : "No se pudo guardar.");
    } finally {
      setSaving(false);
    }
  }

  const canManage = config?.can_manage ?? false;
  const disabled = saving || !canManage;

  const sections: [string, string, FieldProps[]][] = [
    ["General", "Identidad de la empresa.", GENERAL],
    ["Contacto", "Cómo te encuentran tus clientes.", CONTACT],
    ["Branding", "Logo y paleta de la tienda pública.", BRANDING],
    ["Políticas", "Textos y enlaces que aparecen en tus documentos.", POLICIES],
    ["Notificaciones", "Avisos internos de esta empresa.", NOTIFICATIONS],
  ];

  return (
    <AdminShell user={user}>
      <div className="space-y-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-foreground">Configuración</h1>
            <p className="mt-1 text-sm text-muted">
              Identidad, branding y políticas de {config?.company.name ?? "tu empresa"}.
              Estos datos aparecen en tu tienda, tus emails y tus documentos.
            </p>
          </div>
          <Link
            href="/admin/branches"
            className="rounded-lg border border-bd-border px-3.5 py-2 text-sm text-foreground/85 transition hover:border-bd-border hover:text-foreground"
          >
            Sucursales →
          </Link>
        </div>

        {loading ? <p className="py-10 text-center text-muted">Cargando…</p> : null}

        {error && !Object.keys(errors).length ? (
          <div className="rounded-xl border border-danger-border bg-danger-surface px-5 py-4 text-sm text-danger">
            {error}
          </div>
        ) : null}

        {config && !canManage ? (
          <div className="rounded-lg border border-bd-border bg-surface px-4 py-3">
            <p className="text-sm text-muted">
              Puedes ver esta configuración pero no editarla. Se requiere la
              capacidad <code className="text-foreground/85">company.manage</code>.
            </p>
          </div>
        ) : null}

        {config && config.status.missing_count > 0 ? (
          <div className="rounded-xl border border-warning-border bg-amber-400/[0.06] px-5 py-4">
            <p className="text-sm font-medium text-warning">
              Falta configurar {config.status.missing_count} dato(s)
            </p>
            <p className="mt-1 text-xs text-warning">
              {config.status.missing.map((m) => m.label).join(" · ")}
            </p>
          </div>
        ) : null}

        {config ? (
          <>
            <div className="grid gap-6 lg:grid-cols-[1fr_20rem]">
              <div className="space-y-8">
                {sections.map(([title, description, fields]) => (
                  <DashboardSection key={title} title={title} description={description}>
                    <div className="grid gap-4 sm:grid-cols-2">
                      {fields.map((field) => (
                        <div
                          key={field.name}
                          className={field.type === "textarea" ? "sm:col-span-2" : ""}
                        >
                          <Field
                            field={field}
                            value={draft[field.name] ?? ""}
                            error={errors[field.name]}
                            disabled={disabled}
                            onChange={set}
                          />
                        </div>
                      ))}
                    </div>
                  </DashboardSection>
                ))}
                {/* Phase 2E — the counter lives behind its own endpoint, so
                    it saves separately from the fields above. That is the point:
                    an unrelated settings save must not be able to move a
                    document counter. */}
                <DashboardSection
                  title="Numeración interna"
                  description="Cómo se numeran tus notas de venta internas."
                >
                  <SequenceSettings companyId={companyId} />
                </DashboardSection>
              </div>

              <div className="space-y-4 lg:sticky lg:top-24 lg:self-start">
                <p className="text-[11px] font-semibold uppercase tracking-widest text-muted">
                  Vista previa
                </p>
                <BrandPreview draft={draft} name={draft.name ?? ""} />
                <p className="text-[11px] text-muted">
                  Aproximación de la tienda pública. Se guarda solo al confirmar.
                </p>

                <div className="rounded-xl border border-bd-border bg-surface p-4">
                  <p className="text-[11px] font-semibold uppercase tracking-widest text-muted">
                    Sucursal de despacho
                  </p>
                  <p className="mt-2 text-sm text-foreground/85">
                    {config.fulfillment_branch?.name ?? "Sin configurar"}
                  </p>
                  <p className="mt-1 text-[11px] text-muted">
                    De aquí sale el stock de las ventas online.
                  </p>
                  <Link
                    href="/admin/branches"
                    className="mt-3 inline-block text-xs text-muted underline underline-offset-4 transition hover:text-foreground"
                  >
                    Cambiar en Sucursales
                  </Link>
                </div>
              </div>
            </div>

            {canManage ? (
              <div className="flex flex-wrap items-center gap-3 border-t border-bd-border pt-6">
                <button
                  type="button"
                  onClick={() => void save()}
                  disabled={saving}
                  className="rounded-lg bg-foreground px-5 py-2.5 text-sm font-semibold text-background transition hover:bg-foreground/90 disabled:opacity-40"
                >
                  {saving ? "Guardando…" : "Guardar configuración"}
                </button>
                {saved ? (
                  <span className="text-sm text-muted">Cambios guardados.</span>
                ) : null}
                {Object.keys(errors).length > 0 ? (
                  <span className="text-sm text-danger">
                    Revisa los campos marcados.
                  </span>
                ) : null}
              </div>
            ) : null}
          </>
        ) : null}
      </div>
    </AdminShell>
  );
}

export default function SettingsPage() {
  return (
    <InternalControlGuard>
      {(ctx) => <SettingsContent user={ctx.user} ctx={ctx} />}
    </InternalControlGuard>
  );
}
