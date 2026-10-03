import { useEffect, useState } from "react";
import { getSkin, onSkinChange, setSkin, type Skin } from "../lib/skin";

type Mode = "light" | "dark";

/** Cycles Light → Dark → Holo. Holo is the app-wide HUD skin (see lib/skin). */
export function ThemeToggle() {
  const [mode, setMode] = useState<Mode | null>(null);
  const [skin, setSkinState] = useState<Skin>(getSkin());

  useEffect(() => {
    if (mode) document.documentElement.setAttribute("data-theme", mode);
  }, [mode]);
  useEffect(() => onSkinChange(setSkinState), []);

  const prefersDark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  const current = mode ?? (prefersDark ? "dark" : "light");

  const next = () => {
    if (skin === "holo") { setSkin("pro"); setMode("light"); return; }
    if (current === "light") { setMode("dark"); return; }
    setSkin("holo");
  };
  const label = skin === "holo" ? "☀︎ Light" : current === "dark" ? "◉ Holo" : "☾ Dark";

  return (
    <button className="btn" title="Light → Dark → Holo" onClick={next}>
      {label}
    </button>
  );
}
