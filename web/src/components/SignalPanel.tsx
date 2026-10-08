import { useEffect, useRef, useState } from "react";
import { api, type SignalGroup, type SignalMsg, type SignalNameSuggestion, type SignalStatus } from "../api/client";
import type { MorningCfg } from "../api/client";
import { API_BASE } from "../api/client";

/** Settings → Signal: link the bot, pick chats, import history, search, names. */
export function SignalPanel() {
  const [st, setSt] = useState<SignalStatus | null>(null);
  const [err, setErr] = useState("");
  const [qr, setQr] = useState("");
  const [groups, setGroups] = useState<SignalGroup[] | null>(null);
  const [watched, setWatched] = useState<SignalStatus["watched"]>({});
  const [alertGroup, setAlertGroup] = useState<string>("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [impName, setImpName] = useState("");
  const [q, setQ] = useState("");
  const [results, setResults] = useState<SignalMsg[] | null>(null);
  const [names, setNames] = useState<SignalNameSuggestion[] | null>(null);
  const poll = useRef<number | undefined>(undefined);

  const load = async () => {
    try {
      const s = await api.signalStatus();
      setSt(s);
      setWatched(s.watched || {});
      setAlertGroup(s.alert_group_id || "");
      setErr("");
      return s;
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); return null; }
  };
  useEffect(() => { load(); return () => window.clearInterval(poll.current); }, []);
  useEffect(() => {
    if (st?.number && groups === null) api.signalGroups().then(setGroups).catch((e) => setMsg(String(e instanceof Error ? e.message : e)));
  }, [st?.number, groups]);

  async function startLink() {
    setBusy(true); setMsg("");
    try {
      const r = await fetch("/integrations/signal/link-qr", { credentials: "same-origin" });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`);
      setQr(URL.createObjectURL(await r.blob()));
      window.clearInterval(poll.current);
      poll.current = window.setInterval(async () => {
        const s = await load();
        if (s?.number) { window.clearInterval(poll.current); setQr(""); setMsg("Linked. Now pick the chats to follow."); }
      }, 3000);
    } catch (e) { setMsg(`Couldn't get a link code: ${e instanceof Error ? e.message : e}`); }
    finally { setBusy(false); }
  }

  const toggle = (g: SignalGroup) => setWatched((w) => {
    const n = { ...w };
    if (n[g.id]) delete n[g.id];
    else n[g.id] = { name: g.name, role: /hard|equip|gear|inventory/i.test(g.name) ? "hardware" : /deploy|setup|install/i.test(g.name) ? "deploy" : "other" };
    return n;
  });
  async function save() {
    setBusy(true); setMsg("");
    try { await api.signalWatch(watched, alertGroup || null); await load(); setMsg("Saved. New messages in these chats are archived from now on."); }
    catch (e) { setMsg(String(e instanceof Error ? e.message : e)); }
    finally { setBusy(false); }
  }
  async function importFile(f: File | undefined) {
    if (!f) return;
    setBusy(true); setMsg("");
    try {
      const r = await api.signalImport(f, impName);
      setMsg(`Imported ${r.added} new message${r.added === 1 ? "" : "s"} (${r.parsed} read) from ${r.chats.join(", ")}.`);
      await load();
    } catch (e) { setMsg(`Import failed: ${e instanceof Error ? e.message : e}`); }
    finally { setBusy(false); }
  }
  async function search(e?: React.FormEvent) {
    e?.preventDefault();
    try { setResults((await api.signalMessages(q)).messages); } catch (x) { setMsg(String(x instanceof Error ? x.message : x)); }
  }

  const total = st ? Object.values(st.counts || {}).reduce((a, b) => a + b, 0) : 0;
  const hwSet = Object.values(watched).some((v) => v.role === "hardware") || Object.keys(st?.counts || {}).some((k) => k.startsWith("import:"));

  return (
    <>
      <h3 style={{ margin: "4px 0 6px", fontSize: 15 }}>Signal</h3>
      <p style={{ marginTop: 0 }}>
        NetMonitor joins your Signal as a <strong>linked device</strong> (like Signal Desktop). It saves only the group
        chats you tick: the deploy log, the equipment list. It can also post alerts into a group.
      </p>
      {err ? <div className="banner err">{err}</div> : null}
      {st && !st.service ? (
        <div className="banner err">The Signal service isn't running on the server yet. It starts with the next deploy; try again in a few minutes.</div>
      ) : null}

      {st?.service && !st.number ? (
        <div className="banner" style={{ marginBottom: 12 }}>
          <b>Not linked yet.</b>
          <ol style={{ margin: "6px 0 8px 18px", padding: 0, fontSize: 13 }}>
            <li>Tap <b>Show link code</b>.</li>
            <li>On your phone: Signal → Settings → <b>Linked devices</b> → <b>Link new device</b> → scan the code.</li>
          </ol>
          {qr ? <img src={qr} alt="Signal link code" style={{ width: 240, height: 240, background: "#fff", padding: 8, borderRadius: 8 }} /> : null}
          <div><button className="btn btn-primary" onClick={startLink} disabled={busy}>{qr ? "New code" : "Show link code"}</button></div>
          {qr ? <p className="sub" style={{ fontSize: 12 }}>Waiting for you to scan… the code expires after about a minute.</p> : null}
        </div>
      ) : null}

      {st?.number ? (
        <div className="banner" style={{ marginBottom: 12 }}>
          Linked to <b>{st.number}</b> · {total} message{total === 1 ? "" : "s"} saved
          {st.last_receive_at ? <> · checked {new Date(st.last_receive_at).toLocaleTimeString()}</> : null}
          {st.last_error ? <div style={{ color: "var(--critical)", fontSize: 12 }}>⚠ {st.last_error}</div> : null}
          <div className="sub" style={{ fontSize: 12, marginTop: 4 }}>To unlink: Signal → Settings → Linked devices → NetMonitor → Unlink.</div>
        </div>
      ) : null}

      {st?.number ? (
        <>
          <h4 style={{ margin: "12px 0 6px" }}>Chats to follow</h4>
          {groups === null ? <p className="sub">Loading groups…</p> : null}
          {groups?.map((g) => {
            const w = watched[g.id];
            return (
              <div key={g.id} className="banner" style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, opacity: w ? 1 : 0.75 }}>
                <input type="checkbox" checked={!!w} onChange={() => toggle(g)} />
                <b style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis" }}>{g.name}</b>
                <span className="sub" style={{ fontSize: 12 }}>{st.counts[g.id] ?? 0} saved</span>
                {w ? (
                  <select value={w.role} onChange={(e) => setWatched((x) => ({ ...x, [g.id]: { ...w, role: e.target.value as "deploy" } }))}>
                    <option value="deploy">Deploy log</option>
                    <option value="hardware">Equipment list</option>
                    <option value="other">Other</option>
                  </select>
                ) : null}
              </div>
            );
          })}
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", margin: "10px 0" }}>
            <span className="sub" style={{ fontSize: 13 }}>Post alerts in:</span>
            <select value={alertGroup} onChange={(e) => setAlertGroup(e.target.value)} style={{ flex: 1, minWidth: 160 }}>
              <option value="">— don't post alerts —</option>
              {groups?.map((g) => <option key={g.id} value={g.send_id}>{g.name}</option>)}
            </select>
            <button className="btn btn-primary" onClick={save} disabled={busy}>Save</button>
            {st.alert_group_id ? <button className="btn" onClick={() => api.signalTest().then(() => setMsg("Test sent.")).catch((e) => setMsg(String(e.message ?? e)))}>Send test</button> : null}
          </div>
          <p className="sub" style={{ fontSize: 12, marginTop: 0 }}>Alerts come from your own account, so your phone won't buzz for them. Everyone else in the group gets notified.</p>
        </>
      ) : null}

      <h4 style={{ margin: "16px 0 6px" }}>Import past chats</h4>
      <p style={{ marginTop: 0 }}>
        A linked bot only sees messages from now on. For older chats: on your Mac, export them from Signal Desktop with{" "}
        <a href="https://github.com/carderne/signal-export" target="_blank" rel="noreferrer">signal-export</a>{" "}
        (<code>sigexport ~/signal-chats</code>), then upload that chat's <code>chat.md</code> or a zip of the folder. Pasted text with “[date time] Name: message” lines works too.
      </p>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <input placeholder="Chat name (e.g. Equipment List)" value={impName} onChange={(e) => setImpName(e.target.value)} style={{ flex: 1, minWidth: 180 }} />
        <label className={`btn ${busy ? "disabled" : ""}`} style={{ cursor: "pointer" }}>
          Upload file…
          <input type="file" hidden accept=".md,.txt,.zip" disabled={busy} onChange={(e) => { importFile(e.target.files?.[0]); e.target.value = ""; }} />
        </label>
      </div>
      <p className="sub" style={{ fontSize: 12 }}>Imported chats with “hardware”, “equipment” or “inventory” in the name are read as the equipment list.</p>

      {msg ? <div className="banner" style={{ margin: "10px 0" }}>{msg}</div> : null}

      <h4 style={{ margin: "16px 0 6px" }}>Search saved chats</h4>
      <form onSubmit={search} style={{ display: "flex", gap: 8 }}>
        <input placeholder="e.g. midway switch, 0903, fiber" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: 1 }} />
        <button className="btn">Search</button>
      </form>
      {results ? (
        <div style={{ marginTop: 8, maxHeight: 320, overflowY: "auto" }}>
          {results.length === 0 ? <p className="sub">Nothing found.</p> : null}
          {results.map((m) => (
            <div key={m.id} className="banner" style={{ marginBottom: 6, fontSize: 13 }}>
              <div className="sub" style={{ fontSize: 11 }}>{new Date(m.at).toLocaleString()} · {m.group} · {m.sender}{m.attachments ? ` · 📎${m.attachments}` : ""}</div>
              <div style={{ whiteSpace: "pre-wrap" }}>{m.body}</div>
            </div>
          ))}
        </div>
      ) : null}

      <MorningReport />
      <ClosingEarly />

      <h4 style={{ margin: "16px 0 6px" }}>Names from the equipment list</h4>
      <p style={{ marginTop: 0 }}>
        Lines in the equipment list that mention a device's tag (like 0903) or MAC become suggested names.
        {!hwSet ? " Tick a chat as “Equipment list” or import one first." : ""}
      </p>
      <button className="btn" onClick={() => api.signalNames().then(setNames).catch((e) => setMsg(String(e.message ?? e)))}>Find name suggestions</button>
      {names ? (
        <div style={{ marginTop: 8 }}>
          {names.length === 0 ? <p className="sub">No matches yet.</p> : null}
          {names.map((n) => (
            <div key={n.mac} className="banner" style={{ marginBottom: 6, fontSize: 13 }}>
              <div><span className="sub">{n.current || n.mac}</span> → <b>{n.proposed}</b></div>
              <div className="sub" style={{ fontSize: 11 }}>“{n.line}” · {n.from} · {new Date(n.at).toLocaleDateString()}</div>
            </div>
          ))}
          {names.length ? <p className="sub" style={{ fontSize: 12 }}>Review only for now. Renaming in UniFi comes once we've checked these against your real list.</p> : null}
        </div>
      ) : null}
    </>
  );
}


/** NETBOT's daily opening report to the alert group. */
function MorningReport() {
  const [cfg, setCfg] = useState<MorningCfg | null>(null);
  const [preview, setPreview] = useState("");
  const [last, setLast] = useState<string | null>(null);
  const [route, setRoute] = useState<{ event: string | null; due: string | null; tz?: string }>({ event: null, due: null });
  const [msg, setMsg] = useState("");
  const load = () => api.morning().then((r) => { setCfg(r.config); setPreview(r.preview); setLast(r.last); setRoute({ event: r.route?.event ?? null, due: r.due_today, tz: r.route?.tz }); }).catch((e) => setMsg(String(e.message ?? e)));
  useEffect(() => { load(); }, []);
  if (!cfg) return null;
  const save = async (patch: Partial<MorningCfg>) => {
    setMsg("Saving…");
    try { const r = await api.morningSave(patch); setCfg(r.config); setMsg("Saved"); }
    catch (e) { setMsg(String((e as Error).message ?? e)); }
  };
  const sendNow = async () => {
    if (!window.confirm("Send the opening report to the alert group now?")) return;
    setMsg("Sending…");
    try { const r = await api.morningSendNow(); setPreview(r.text); setMsg("Sent to the group"); }
    catch (e) { setMsg(String((e as Error).message ?? e)); }
  };
  return (
    <>
      <h4 style={{ margin: "16px 0 6px" }}>Daily opening report (NETBOT)</h4>
      <p style={{ marginTop: 0 }}>Posts kiosks, printers, switches, APs and WAN status to the alert group once a day. Skipped while the show is closed.
        {route.event ? <> At <b>{route.event}</b> it goes out 5 min after the gates open — {route.due ? `today ${new Date(route.due).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", timeZone: route.tz || cfg.tz })}` : "fair closed today"}. The time below is used between fairs.</> : null}</p>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <input type="checkbox" checked={cfg.enabled} onChange={(e) => save({ enabled: e.target.checked })} /> On
        </label>
        <label>Time <input type="time" value={cfg.time} onChange={(e) => setCfg({ ...cfg, time: e.target.value })} onBlur={() => save({ time: cfg.time })} /></label>
        <label>Time zone{" "}
          <select value={cfg.tz} onChange={(e) => save({ tz: e.target.value })}>
            {Array.from(new Set([cfg.tz, "America/Phoenix", "America/Los_Angeles", "America/Denver", "America/Chicago", "America/New_York"])).map((z) => <option key={z} value={z}>{z.replace("America/", "").replace("_", " ")}</option>)}
          </select>
        </label>
        <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <input type="checkbox" checked={cfg.speedtest} onChange={(e) => save({ speedtest: e.target.checked })} /> Speed test 10 min before
        </label>
        {cfg.speedtest ? (
          <span className="sub" style={{ display: "flex", gap: 6, alignItems: "center" }}>
            target ↓<input type="number" style={{ width: 64 }} value={cfg.min_down} onChange={(e) => setCfg({ ...cfg, min_down: Number(e.target.value) })} onBlur={() => save({ min_down: cfg.min_down })} />
            ↑<input type="number" style={{ width: 56 }} value={cfg.min_up} onChange={(e) => setCfg({ ...cfg, min_up: Number(e.target.value) })} onBlur={() => save({ min_up: cfg.min_up })} /> Mbps
          </span>
        ) : null}
        <button className="btn" onClick={load}>Refresh preview</button>
        <button className="btn" onClick={sendNow}>Send now</button>
        <span className="sub">{msg}{last ? ` · last sent ${last}` : ""}</span>
      </div>
      <pre style={{ whiteSpace: "pre-wrap", fontSize: 12, background: "var(--surface-2, rgba(127,127,127,.08))", padding: 10, borderRadius: 8, marginTop: 8 }}>{preview}</pre>
    </>
  );
}


/** How the team tells NETBOT we're closing early. */
function ClosingEarly() {
  const [tok, setTok] = useState<string | null>(null);
  const [msg, setMsg] = useState("");
  const get = (rotate = false) => api.shortcutLink(rotate).then((r) => setTok(r.token)).catch((e) => setMsg(String(e.message ?? e)));
  const url = (a: string) => `${API_BASE}/network/closure/shortcut/${tok}/${a}`;
  return (
    <>
      <h4 style={{ margin: "16px 0 6px" }}>Closing early</h4>
      <ul style={{ marginTop: 0, paddingLeft: 18, fontSize: 13 }}>
        <li>Anyone in the alert group can send <b>closed</b> (or “closing early”) — alerts pause until the gates open next. <b>open</b> undoes it, <b>status</b> posts the report now.</li>
        <li>From 5 PM, if 40%+ of kiosks drop at once, NETBOT pauses alerts and asks the group. Reply <b>closed</b> to confirm or <b>no</b> if it's an outage.</li>
        <li>Weather heads-up: NETBOT posts National Weather Service wind, dust, storm and flood alerts for the fairgrounds on fair days.</li>
        <li>After 10:30 PM, a mass kiosk drop is treated as normal closing (no question asked).</li>
      </ul>
      <b style={{ fontSize: 13 }}>Phone shortcut / NFC tag</b>
      <p className="sub" style={{ marginTop: 4, fontSize: 12 }}>
        iPhone: Shortcuts → New Shortcut → <i>Get Contents of URL</i>, paste the “closed” link, set Method to POST. Add it to your Home Screen,
        or Shortcuts → Automation → NFC to run it by tapping a tag. Keep the link private — anyone with it can pause alerts.
      </p>
      {tok ? (
        <div style={{ fontSize: 12, wordBreak: "break-all" }}>
          <div>Closed: <code>{url("closed")}</code> <button className="btn" onClick={() => navigator.clipboard.writeText(url("closed"))}>Copy</button></div>
          <div style={{ marginTop: 4 }}>Open: <code>{url("open")}</code> <button className="btn" onClick={() => navigator.clipboard.writeText(url("open"))}>Copy</button></div>
          <button className="btn" style={{ marginTop: 6 }} onClick={() => window.confirm("Make new links? The old ones stop working.") && get(true)}>New links</button>
        </div>
      ) : <button className="btn" onClick={() => get()}>Show shortcut links</button>}
      <span className="sub">{msg}</span>
    </>
  );
}
