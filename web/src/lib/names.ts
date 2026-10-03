/**
 * UniFi device names at RCS mix a location, an asset tag and the model, e.g.
 * "[Vivs Geni](0903) USW Pro Max 24 PoE" or "Kiosk 4 (0916) USW Pro Max 24 PoE".
 * Split them so lists can show "Vivs Geni  #0903" and keep the model aside.
 */
export type NameParts = { label: string; tag: string | null; full: string };

const MODEL_TAIL =
  /\s*\b(USW[\s-][\w\s.-]*|US-[\w-]+|UXG[\w-]*|UDM[\w-]*|UAP[\w-]*|U6[\s-](Lite|LR|Pro|Mesh|Plus|Enterprise|IW|Extender)[\w\s-]*|U7[\s-][\w\s-]*|AC Mesh[\w\s-]*|U6 Mesh)\s*$/i;

export function splitName(full: string | null | undefined): NameParts {
  const raw = (full ?? "").trim();
  if (!raw) return { label: "Unnamed", tag: null, full: raw };
  let s = raw;
  let tag: string | null = null;
  const t = s.match(/[([](\d{3,4})[)\]]/);
  if (t) {
    tag = t[1];
    s = s.replace(t[0], " ");
  }
  const tidy = (x: string) => x.replace(/[[\]()|]/g, " ").replace(/\s+/g, " ").trim();
  const brackets = [...s.matchAll(/\[([^\]]+)\]/g)].map((m) => tidy(m[1])).filter(Boolean);
  const outside = tidy(s.replace(/\[[^\]]*\]/g, " ").replace(MODEL_TAIL, ""));
  let label = [outside, ...brackets].filter(Boolean).join(" · ");
  if (!label) label = tidy(s);
  if (!label) return { label: tag ? `#${tag}` : raw, tag: null, full: raw };
  return { label, tag, full: raw };
}
