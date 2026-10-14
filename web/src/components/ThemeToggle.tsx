import { useEffect, useState } from "react";
import { getSkin, onSkinChange, setSkin, type Skin } from "../lib/skin";

type Mode = "light" | "dark";
const THEME_KEY = "nm-theme";

/** The staff app's saved Light/Dark choice (null = follow the OS). */
export function storedMode(): Mode | null {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return v === "dark" || v === "light" ? v : null;
  } catch {
    return null;
  }
}

/** Re-apply the staff app's own theme (e.g. after leaving the guest view). */
export function applyStoredTheme(): void {
  const m = storedMode();
  if (m) document.documentElement.setAttribute("data-theme", m);
  else document.documentElement.removeAttribute("data-theme");
}

/** Cycles Light → Dark → Holo → RCS. Holo is the HUD skin; RCS matches the
 * RCS inventory site (near-black sidebar, RCS red). See lib/skin. */
export function ThemeToggle() {
  const [mode, setModeState] = useState<Mode | null>(() => storedMode());
  const setMode = (m: Mode) => {
    try { localStorage.setItem(THEME_KEY, m); } catch { /* private mode */ }
    setModeState(m);
  };
  const [skin, setSkinState] = useState<Skin>(getSkin());

  useEffect(() => {
    if (mode) document.documentElement.setAttribute("data-theme", mode);
    else document.documentElement.removeAttribute("data-theme");
  }, [mode]);
  useEffect(() => onSkinChange(setSkinState), []);

  const prefersDark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  const current = mode ?? (prefersDark ? "dark" : "light");

  const next = () => {
    if (skin === "rcs") { setSkin("pro"); setMode("light"); return; }
    if (skin === "holo") { setSkin("rcs"); setMode("light"); return; }
    if (current === "light") { setMode("dark"); return; }
    setSkin("holo");
  };
  const label = skin === "rcs" ? "☀︎ Light" : skin === "holo" ? "◆ RCS" : current === "dark" ? "◉ Holo" : "☾ Dark";

  return (
    <button className="btn" title="Light → Dark → Holo → RCS" onClick={next}>
      {label}
    </button>
  );
}
