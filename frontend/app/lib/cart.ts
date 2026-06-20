export function emitCartChange() {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event("cartChange"));
}

export function getSessionKey(): string {
  if (typeof window === "undefined") {
    return "guest-session";
  }

  const existing = window.localStorage.getItem("blackdog_session_key");
  if (existing) {
    return existing;
  }

  const newKey = `guest-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  window.localStorage.setItem("blackdog_session_key", newKey);
  return newKey;
}
