"use client";

import { useState } from "react";
import { ALL_ROLES } from "../../lib/admin";

type Props = {
  userId: number;
  currentRole: string;
  isSelf: boolean;
  onRoleChange: (userId: number, newRole: string) => Promise<void>;
};

export function RoleSelect({ userId, currentRole, isSelf, onRoleChange }: Props) {
  const [selected, setSelected] = useState(currentRole);
  const [pending, setPending] = useState(false);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; msg: string } | null>(null);

  const hasChange = selected !== currentRole && !isSelf;

  function handleChange(e: React.ChangeEvent<HTMLSelectElement>) {
    setSelected(e.target.value);
    setPending(true);
    setFeedback(null);
  }

  function handleCancel() {
    setSelected(currentRole);
    setPending(false);
    setFeedback(null);
  }

  async function handleConfirm() {
    setSaving(true);
    setFeedback(null);
    try {
      await onRoleChange(userId, selected);
      setFeedback({ type: "success", msg: "Rol actualizado." });
      setPending(false);
    } catch (err: unknown) {
      setFeedback({
        type: "error",
        msg: err instanceof Error ? err.message : "Error al actualizar.",
      });
      setSelected(currentRole);
      setPending(false);
    } finally {
      setSaving(false);
    }
  }

  if (isSelf) {
    return (
      <span className="text-xs text-zinc-500 italic">Propio rol</span>
    );
  }

  return (
    <div className="flex flex-col gap-1.5">
      <select
        value={selected}
        onChange={handleChange}
        disabled={saving}
        className="rounded-lg border border-white/10 bg-zinc-900 px-2 py-1.5 text-xs text-zinc-200 focus:border-white/30 focus:outline-none disabled:opacity-50"
      >
        {ALL_ROLES.map((r) => (
          <option key={r.value} value={r.value}>
            {r.label}
          </option>
        ))}
      </select>

      {hasChange && !saving && (
        <div className="flex gap-1">
          <button
            onClick={handleConfirm}
            className="rounded px-2 py-1 text-[10px] font-semibold bg-white text-black transition hover:bg-zinc-200"
          >
            Guardar
          </button>
          <button
            onClick={handleCancel}
            className="rounded px-2 py-1 text-[10px] font-semibold border border-white/10 text-zinc-400 transition hover:text-white"
          >
            Cancelar
          </button>
        </div>
      )}
      {saving && <p className="text-[10px] text-zinc-500">Guardando…</p>}
      {feedback && !pending && (
        <p className={`text-[10px] ${feedback.type === "success" ? "text-zinc-300" : "text-red-400"}`}>
          {feedback.msg}
        </p>
      )}
    </div>
  );
}
