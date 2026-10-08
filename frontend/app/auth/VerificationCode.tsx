"use client";

/**
 * El código de 6 dígitos del correo de verificación.
 *
 * EL ENLACE DEL CORREO SIGUE SIENDO EL CAMINO PRINCIPAL. Esto es para quien lee
 * el correo en un dispositivo y se registra en otro, y está hecho para no
 * molestar: se puede pegar tal como viene, se envía solo al completarlo y el
 * móvil lo reconoce como un código.
 *
 * Cuántos intentos admite, cuánto dura y quién queda verificado lo decide el
 * servidor. Aquí no se sabe —ni se dice— si la cuenta existe: un fallo se
 * muestra con las palabras del servidor, que son las mismas para todos.
 */

import { useEffect, useId, useRef, useState } from "react";
import { resendVerification, verifyEmailCode } from "../lib/auth";

const LENGTH = 6;
const RESEND_WAIT_SECONDS = 60;

/** Lo escrito o pegado, como dígitos: «123 456» y «123-456» son «123456». */
export function cleanCode(value: string): string {
  return value.replace(/\D/g, "").slice(0, LENGTH);
}

const inputClass =
  "mt-2 w-full rounded-xl border border-bd-border bg-surface px-4 py-3 text-sm text-foreground placeholder-muted focus:border-bd-border focus:outline-none";
const labelClass = "block text-xs font-bold uppercase tracking-wide text-muted";

export function VerificationCode({
  email: knownEmail,
  onVerified,
}: {
  /** El correo recién registrado. Sin él, se pregunta. */
  email?: string;
  onVerified: (detail: string) => void;
}) {
  const id = useId();
  const [typedEmail, setTypedEmail] = useState("");
  const [code, setCode] = useState("");
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // La espera se mide contra el reloj, no contando ticks: una pestaña en
  // segundo plano los pierde, y la cuenta se quedaría atrás.
  const [resendAt, setResendAt] = useState(() => Date.now() + RESEND_WAIT_SECONDS * 1000);
  const [now, setNow] = useState(() => Date.now());
  const [resending, setResending] = useState(false);
  const codeField = useRef<HTMLInputElement>(null);

  const email = (knownEmail ?? typedEmail).trim();

  // La espera para pedir otro: el servidor no envía dos en menos de un minuto,
  // y un botón que no hace nada es peor que uno que dice cuánto falta.
  const wait = Math.max(0, Math.ceil((resendAt - now) / 1000));
  useEffect(() => {
    if (wait <= 0) return;
    const timer = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, [wait]);

  async function submit(value: string) {
    if (checking || value.length !== LENGTH || !email) return;
    setChecking(true);
    setError(null);
    setNotice(null);
    try {
      const result = await verifyEmailCode(email, value);
      onVerified(result.detail);
    } catch (err: unknown) {
      // Se borra: el mismo código no va a servir, y dejarlo ahí invita a
      // reenviarlo. No se reintenta solo.
      setCode("");
      setError(err instanceof Error && err.message ? err.message : "No pudimos comprobar el código. Inténtalo de nuevo.");
      setChecking(false);
      window.setTimeout(() => codeField.current?.focus(), 0);
    }
  }

  function onCodeChange(raw: string) {
    if (checking) return;
    const value = cleanCode(raw);
    setCode(value);
    if (value.length === LENGTH) void submit(value);
  }

  async function resend() {
    // Tampoco mientras se comprueba un código: las dos peticiones tocan la
    // misma cuenta, y la que llegue segunda dejaría sin efecto a la primera.
    if (wait > 0 || resending || checking || !email) return;
    setResending(true);
    setError(null);
    setNotice(null);
    try {
      await resendVerification(email);
      // Lo mismo se haya enviado o no: el servidor tampoco lo dice. Por eso no
      // se afirma que el anterior murió — si no salió ninguno nuevo, sigue vivo.
      setNotice("Si ese correo tiene una cuenta por verificar, te enviamos un código nuevo. Usa el más reciente que tengas.");
      setCode("");
      setNow(Date.now());
      setResendAt(Date.now() + RESEND_WAIT_SECONDS * 1000);
    } catch {
      setError("No pudimos enviar otro código. Espera un momento y vuelve a intentarlo.");
    } finally {
      setResending(false);
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-sm leading-6 text-muted">
        {knownEmail ? (
          <>Te enviamos un correo a <strong className="text-foreground">{knownEmail}</strong>. </>
        ) : null}
        Abre el enlace del correo, o escribe aquí el código de 6 dígitos que trae. El código vence en 15 minutos.
      </p>

      {error && (
        <div role="alert" className="rounded-xl border border-danger-border bg-danger-surface p-4 text-sm text-danger">
          {error}
        </div>
      )}
      {notice && (
        <div role="status" className="rounded-xl border border-bd-border bg-surface p-4 text-sm text-foreground">
          {notice}
        </div>
      )}

      {knownEmail === undefined && (
        <div>
          <label htmlFor={`${id}-email`} className={labelClass}>Correo electrónico</label>
          <input
            id={`${id}-email`}
            type="email"
            value={typedEmail}
            onChange={(e) => setTypedEmail(e.target.value)}
            className={inputClass}
            autoComplete="email"
            placeholder="correo@ejemplo.com"
          />
        </div>
      )}

      <div>
        <label htmlFor={`${id}-code`} className={labelClass}>Código de verificación</label>
        <input
          id={`${id}-code`}
          ref={codeField}
          value={code}
          onChange={(e) => onCodeChange(e.target.value)}
          disabled={checking}
          inputMode="numeric"
          autoComplete="one-time-code"
          pattern="[0-9]*"
          // Sin `maxLength`: cortaría «123 456» antes de poder leerlo.
          className={`${inputClass} text-center font-mono text-2xl tracking-[0.4em]`}
          placeholder="••••••"
          aria-describedby={`${id}-hint`}
        />
        <p id={`${id}-hint`} className="mt-1.5 text-xs text-muted">
          {checking ? "Comprobando…" : "Puedes pegarlo. Se comprueba solo al completarlo."}
        </p>
      </div>

      {knownEmail === undefined && (
        // Quien escribe el código antes que el correo no tiene nada que lo envíe.
        <button
          type="button"
          onClick={() => void submit(code)}
          disabled={checking || code.length !== LENGTH || !email}
          className="w-full rounded-full bg-foreground px-6 py-3.5 text-sm font-semibold uppercase tracking-wide text-background transition hover:bg-foreground/90 disabled:opacity-50"
        >
          Verificar
        </button>
      )}

      <button
        type="button"
        onClick={() => void resend()}
        disabled={wait > 0 || resending || checking}
        className="text-sm font-bold text-foreground transition hover:text-foreground/85 disabled:font-normal disabled:text-muted"
      >
        {wait > 0 ? `Enviar otro código en ${wait} s` : "Enviar otro código"}
      </button>
    </div>
  );
}
