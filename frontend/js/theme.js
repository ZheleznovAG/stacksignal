// "system" follows the OS (prefers-color-scheme in CSS); "light"/"dark" pin it via data-theme.

const KEY = "theme";

export function currentTheme() {
  try { return localStorage.getItem(KEY) || "system"; } catch { return "system"; }
}

export function applyTheme(value) {
  try { localStorage.setItem(KEY, value); } catch { /* private mode */ }
  if (value === "light" || value === "dark") document.documentElement.dataset.theme = value;
  else delete document.documentElement.dataset.theme;
}
