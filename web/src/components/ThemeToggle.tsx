import { useEffect, useState } from "react";

type Mode = "light" | "dark";

export function ThemeToggle() {
  const [mode, setMode] = useState<Mode | null>(null);

  useEffect(() => {
    if (mode) document.documentElement.setAttribute("data-theme", mode);
  }, [mode]);

  const prefersDark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  const current = mode ?? (prefersDark ? "dark" : "light");

  return (
    <button
      className="btn"
      title="Toggle light / dark"
      onClick={() => setMode(current === "dark" ? "light" : "dark")}
    >
      {current === "dark" ? "☀︎ Light" : "☾ Dark"}
    </button>
  );
}
