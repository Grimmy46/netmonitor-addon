/**
 * App-wide visual skin. "holo" turns the whole app into the JARVIS-style HUD
 * (CSS under :root[data-skin="holo"]); "pro" is the normal light/dark look.
 * Shared by the header theme button and the network map's Pro/Holo switch.
 */
export type Skin = "pro" | "holo" | "rcs";
const KEY = "nm-map-skin";
const EVT = "nm-skin";

export function getSkin(): Skin {
  try {
    const v = localStorage.getItem(KEY);
    return v === "holo" || v === "rcs" ? v : "pro";
  } catch {
    return "pro";
  }
}

export function applySkin(s: Skin = getSkin()): void {
  const el = document.documentElement;
  if (s === "holo" || s === "rcs") el.setAttribute("data-skin", s);
  else el.removeAttribute("data-skin");
}

export function setSkin(s: Skin): void {
  try {
    localStorage.setItem(KEY, s);
  } catch {
    /* private mode */
  }
  applySkin(s);
  window.dispatchEvent(new CustomEvent(EVT, { detail: s }));
}

export function onSkinChange(fn: (s: Skin) => void): () => void {
  const h = (e: Event) => fn((e as CustomEvent<Skin>).detail);
  window.addEventListener(EVT, h);
  return () => window.removeEventListener(EVT, h);
}
