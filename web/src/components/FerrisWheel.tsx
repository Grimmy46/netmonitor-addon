/** Decorative Ferris wheel watermark behind the RCS pages (slowly turning). */
export function FerrisWheel() {
  const R = 140, cx = 160, cy = 160, n = 16;
  const spokes = Array.from({ length: n }, (_, i) => (i * 2 * Math.PI) / n);
  return (
    <div className="rcs-wheel" aria-hidden>
      <svg viewBox="0 0 320 400" width="100%" height="100%">
        <g fill="none" stroke="currentColor" strokeLinecap="round">
          {/* A-frame legs + base */}
          <path d="M160 160 L70 385 M160 160 L250 385 M95 330 L225 330" strokeWidth="7" />
          <path d="M45 388 H275" strokeWidth="8" />
          <g className="rcs-wheel-spin" style={{ transformOrigin: `${cx}px ${cy}px` }}>
            <circle cx={cx} cy={cy} r={R} strokeWidth="6" />
            <circle cx={cx} cy={cy} r={R - 22} strokeWidth="3" />
            <circle cx={cx} cy={cy} r={22} strokeWidth="5" />
            {spokes.map((a, i) => (
              <line key={i} x1={cx} y1={cy} x2={cx + R * Math.cos(a)} y2={cy + R * Math.sin(a)} strokeWidth="2.5" />
            ))}
            {spokes.map((a, i) => (
              <path key={`b${i}`} d={`M${cx + (R - 22) * Math.cos(a)} ${cy + (R - 22) * Math.sin(a)} L${cx + R * Math.cos(a + Math.PI / n)} ${cy + R * Math.sin(a + Math.PI / n)}`} strokeWidth="2" />
            ))}
          </g>
        </g>
        {/* gondolas hang level while the wheel turns */}
        <g className="rcs-wheel-spin" style={{ transformOrigin: `${cx}px ${cy}px` }} fill="currentColor">
          {spokes.map((a, i) => {
            const x = cx + R * Math.cos(a), y = cy + R * Math.sin(a);
            return (
              <g key={i} transform={`translate(${x} ${y})`}>
                <g className="rcs-wheel-unspin">
                  <line x1="0" y1="0" x2="0" y2="9" stroke="currentColor" strokeWidth="2" />
                  <rect x="-10" y="9" width="20" height="14" rx="4" />
                </g>
              </g>
            );
          })}
        </g>
        <circle cx={cx} cy={cy} r="8" fill="currentColor" />
      </svg>
    </div>
  );
}
