// Status is shown as a dot + text label, never color alone (accessibility).
const LABELS: Record<string, string> = {
  online: "Online",
  degraded: "Degraded",
  offline: "Offline",
  unknown: "Unknown",
};

export function StatusPill({ status }: { status: string }) {
  const s = status in LABELS ? status : "unknown";
  return (
    <span className={`pill ${s}`}>
      <span className="dot" />
      {LABELS[s]}
    </span>
  );
}
