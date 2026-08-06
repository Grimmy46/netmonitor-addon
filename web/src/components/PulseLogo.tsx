/** The NetMonitor mark — the same heartbeat tile as the favicon, for the app
 * header (fixed navy tile so it reads identically in light and dark themes). */
export function PulseLogo({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" style={{ flexShrink: 0 }}>
      <rect width="32" height="32" rx="7" fill="#101b33" />
      <path
        d="M4 17 H11 L14 7 L18.5 26 L21 17 H28"
        fill="none"
        stroke="#34d399"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
