export function getStored(key, fallback = null) {
  try { return window.localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}
export function setStored(key, value) {
  try { window.localStorage.setItem(key, value); return true; } catch { return false; }
}
