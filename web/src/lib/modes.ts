import type { ModeEffects, NetworkModeKey } from "../api/client";

/** The three main dashboard tabs. Off-season has no tab of its own. */
export type Tab = "setup" | "open" | "teardown";
export const TABS: { key: Tab; label: string }[] = [
  { key: "setup", label: "Setup" },
  { key: "open", label: "Open" },
  { key: "teardown", label: "Teardown" },
];

export const MODE_LABEL: Record<NetworkModeKey, string> = {
  setup: "Setup",
  open: "Open",
  teardown: "Teardown",
  offseason: "Off-season",
};

export const MODE_BLURB: Record<NetworkModeKey, string> = {
  setup: "Building the lot. Faults stay quiet while the crew works.",
  open: "Gates open. Full monitoring and alerts.",
  teardown: "Packing up. Alerts off, track what comes back.",
  offseason: "Between seasons. Everything quiet.",
};

/** The live tab for a mode (off-season has none). */
export function tabForMode(m: NetworkModeKey | null | undefined): Tab | null {
  return m === "setup" || m === "open" || m === "teardown" ? m : null;
}

/**
 * What each mode normally does, from the NetMon 3 plan. Used only to preview a
 * switch in the confirm dialog; the server's `effects` is the source of truth
 * and replaces this as soon as the switch is saved.
 */
export const EXPECTED_EFFECTS: Record<NetworkModeKey, ModeEffects> = {
  setup: { alerts: false, ap_restart: false, bot: "deploy", group_posts: "setup progress" },
  open: { alerts: true, ap_restart: true, bot: null, group_posts: "outages" },
  teardown: { alerts: false, ap_restart: false, bot: "return", group_posts: "teardown progress" },
  offseason: { alerts: false, ap_restart: false, bot: null, group_posts: "none" },
};

const cap = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

/** One plain-English line per effect, in a fixed order. */
export function describeEffects(e: ModeEffects | null | undefined): { key: keyof ModeEffects; label: string; value: string }[] {
  if (!e) return [];
  const posts = String(e.group_posts ?? "").replace(/_/g, " ").trim();
  return [
    { key: "alerts", label: "Fault alerts", value: e.alerts ? "On" : "Paused" },
    { key: "ap_restart", label: "Daily AP restart", value: e.ap_restart ? "On" : "Off" },
    {
      key: "bot",
      label: "Photo bot",
      value: e.bot === "deploy" ? "Marks devices deployed" : e.bot === "return" ? "Marks devices returned" : "Off",
    },
    { key: "group_posts", label: "RCS-IT group posts", value: !posts || posts.toLowerCase() === "none" ? "None" : cap(posts) },
  ];
}

export function shortWhen(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
