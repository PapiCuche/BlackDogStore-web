"use client";

/**
 * Administración › Mensajería › WhatsApp.
 *
 * What a company's administrator decides: whether notices are sent, which
 * approved template each one uses, and the calling code assumed for numbers
 * written without one.
 *
 * THERE IS NO FIELD FOR A CREDENTIAL, ON PURPOSE. The token, the app secret and
 * the webhook verify token are set by whoever operates the installation; this
 * screen is told only whether each one is in place.
 */

import { useEffect, useState } from "react";

import {
  fetchWhatsAppSettings, updateWhatsAppSettings, type WhatsAppSettingsData,
} from "@/app/lib/messaging";

import { Button, Confirm, ErrorNote, Field, Panel } from "../../service/components/ServiceUi";

const MISSING: Record<string, string> = {
  access_token: "token de acceso",
  app_secret: "secreto de la aplicación",
  verify_token: "token de verificación del webhook",
  phone_number_id: "número de WhatsApp Business",
};

export function WhatsAppSettings({ slug, canManage }: { slug: string; canManage: boolean }) {
  const [data, setData] = useState<WhatsAppSettingsData | null>(null);
  const [templates, setTemplates] = useState<Record<string, string>>({});
  const [callingCode, setCallingCode] = useState("");
  const [language, setLanguage] = useState("es");
  const [error, setError] = useState<unknown>(null);
  const [working, setWorking] = useState(false);
  const [saved, setSaved] = useState(false);

  function show(loaded: WhatsAppSettingsData) {
    setData(loaded);
    setTemplates(loaded.templates);
    setCallingCode(loaded.default_calling_code);
    setLanguage(loaded.template_language);
  }

  useEffect(() => {
    let cancelled = false;
    fetchWhatsAppSettings(slug).then(
      (loaded) => { if (!cancelled) show(loaded); },
      (err) => { if (!cancelled) setError(err); },
    );
    return () => { cancelled = true; };
  }, [slug]);

  async function change(body: Parameters<typeof updateWhatsAppSettings>[1], announce = false) {
    setWorking(true);
    setError(null);
    setSaved(false);
    try {
      show(await updateWhatsAppSettings(slug, body));
      setSaved(announce);
    } catch (err) {
      setError(err);
    } finally {
      setWorking(false);
    }
  }

  if (!data) {
    return error ? <ErrorNote error={error} /> : <p className="text-sm text-muted">Cargando…</p>;
  }

  const origin = typeof window === "undefined" ? "" : window.location.origin;

  return (
    <div className="space-y-6">
      <Panel
        title="Estado"
        subtitle="Los avisos salen por la API oficial de WhatsApp Business, con el número y las plantillas aprobadas de tu empresa."
      >
        <p className="text-sm text-foreground">
          {data.enabled ? "Los avisos por WhatsApp están activados." : "Los avisos por WhatsApp están desactivados."}
        </p>
        <p className={`mt-2 text-sm ${data.ready ? "text-success" : "text-warning"}`}>
          {data.ready
            ? "Credenciales del proveedor: configuradas"
            : `Falta: ${data.missing.map((key) => MISSING[key] ?? key).join(", ")}`}
        </p>
        {!data.ready ? (
          <p className="mt-1 text-xs text-muted">
            Las credenciales no se escriben en esta pantalla: las configura quien administra la
            instalación, y aquí sólo se informa de si están.
          </p>
        ) : null}
        {canManage ? (
          <div className="mt-4">
            {data.enabled ? (
              <Confirm
                label="Desactivar avisos por WhatsApp"
                question="¿Dejar de enviar avisos por WhatsApp?"
                tone="danger"
                disabled={working}
                onConfirm={() => void change({ enabled: false })}
              />
            ) : (
              <Confirm
                label="Activar avisos por WhatsApp"
                question="¿Empezar a enviar avisos a los clientes que aceptaron?"
                tone="primary"
                disabled={working || !data.ready}
                onConfirm={() => void change({ enabled: true })}
              />
            )}
          </div>
        ) : null}
      </Panel>

      <Panel
        title="Plantillas"
        subtitle={`Nombre de la plantilla aprobada para cada aviso. Un aviso sin plantilla no se envía. Cada plantilla recibe, en este orden: ${data.template_parameters.map((name, index) => `{{${index + 1}}} ${name}`).join(", ")}.`}
      >
        <div className="grid gap-3 md:grid-cols-2">
          {data.events.map((event) => (
            <label key={event.code} className="block text-xs text-foreground/50">
              {event.label}
              <input
                value={templates[event.code] ?? ""}
                disabled={!canManage}
                onChange={(e) => setTemplates((prev) => ({ ...prev, [event.code]: e.target.value }))}
                placeholder="nombre_de_la_plantilla"
                spellCheck={false}
                className="mt-1.5 w-full rounded-xl border border-bd-border bg-background px-3 py-2.5 font-mono text-sm text-foreground outline-none transition-colors focus:border-foreground/25 disabled:opacity-60"
              />
            </label>
          ))}
        </div>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          {canManage ? (
            <>
              <Field label="Código de país por defecto" value={callingCode} onChange={setCallingCode} placeholder="Ej.: 51" />
              <Field label="Idioma de las plantillas" value={language} onChange={setLanguage} placeholder="es" />
            </>
          ) : (
            <p className="text-xs text-muted">
              Código de país por defecto: {data.default_calling_code || "ninguno"} · idioma: {data.template_language}
            </p>
          )}
        </div>
        <p className="mt-2 text-xs text-muted">
          El código de país se usa sólo para los teléfonos escritos sin él. Sin código, únicamente se
          escribe a números guardados con su prefijo internacional.
        </p>
        {canManage ? (
          <div className="mt-4 flex items-center gap-3">
            <Button
              tone="primary"
              disabled={working}
              onClick={() => void change({ templates, default_calling_code: callingCode, template_language: language }, true)}
            >
              Guardar
            </Button>
            {saved ? <span role="status" className="text-xs text-success">Guardado</span> : null}
          </div>
        ) : null}
      </Panel>

      <Panel
        title="Webhook"
        subtitle="La dirección que se registra en Meta para saber si cada mensaje se entregó y se leyó. Sólo acepta llamadas firmadas."
      >
        <p className="break-all font-mono text-xs text-foreground">{origin}{data.webhook_path}</p>
      </Panel>

      <ErrorNote error={error} />
    </div>
  );
}
