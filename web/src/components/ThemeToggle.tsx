import { useEffect, useState } from "react";
import { getSkin, onSkinChange, setSkin, type Skin } from "../lib/skin";

type Mode = "light" | "dark";

/** Cycles Light → Dark → Holo → RCS. Holo is the HUD skin; RCS matches the
 * RCS inventory site (near-black sidebar, RCS red). See lib/skin. */
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
