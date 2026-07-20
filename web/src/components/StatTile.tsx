export function StatTile({
  label,
  value,
  unit,
}: {
  label: string;
  value: number | string | null;
  unit?: string;
}) {
  const isEmpty = value === null || value === undefined || value === "";
  return (
    <div className="tile">
      <span className={`val${isEmpty ? " muted" : ""}`}>
        {isEmpty ? "—" : value}
        {!isEmpty && unit ? <span style={{ fontSize: 12, color: "var(--ink-muted)" }}> {unit}</span> : null}
      </span>
      <span className="lbl">{label}</span>
    </div>
  );
}
