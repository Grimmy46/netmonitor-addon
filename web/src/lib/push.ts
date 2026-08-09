/** Browser-side Web Push plumbing: service-worker registration and
 * subscribe/unsubscribe against the NetMonitor API. */
import { api } from "../api/client";

export type PushSupport =
  | "ok" // can subscribe right now
  | "needs-install" // iPhone/iPad: must be added to the Home Screen first
  | "unsupported";

export function pushSupport(): PushSupport {
  const standalone =
    window.matchMedia?.("(display-mode: standalone)").matches ||
    (navigator as unknown as { standalone?: boolean }).standalone === true;
  const isIOS = /iP(hone|ad|od)/.test(navigator.userAgent);
  if (isIOS && !standalone) return "needs-install";
  if ("serviceWorker" in navigator && "PushManager" in window && "Notification" in window)
    return "ok";
  return "unsupported";
}

async function registration(): Promise<ServiceWorkerRegistration> {
  const reg = await navigator.serviceWorker.register("/sw.js");
  await navigator.serviceWorker.ready;
  return reg;
}

function b64ToU8(b64url: string): Uint8Array {
  const pad = "=".repeat((4 - (b64url.length % 4)) % 4);
  const raw = atob((b64url + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

/** Is THIS browser currently subscribed? */
export async function pushEnabled(): Promise<boolean> {
  if (pushSupport() !== "ok" || Notification.permission !== "granted") return false;
  try {
    const reg = await navigator.serviceWorker.getRegistration();
    return !!(await reg?.pushManager.getSubscription());
  } catch {
    return false;
  }
}

function withTimeout<T>(p: Promise<T>, ms: number, step: string): Promise<T> {
  return Promise.race([
    p,
    new Promise<T>((_, rej) =>
      setTimeout(() => rej(new Error(`${step} timed out (${Math.round(ms / 1000)}s) — close and reopen the app, then try again.`)), ms),
    ),
  ]);
}

/** Subscribe this device. Reports progress via onStep so a stall is visible,
 * and every stage has a timeout — it can fail loudly but never hang. */
export async function enablePush(onStep?: (s: string) => void): Promise<void> {
  onStep?.("Asking permission…");
  const perm = await withTimeout(
    Promise.resolve(Notification.requestPermission()),
    30000,
    "Permission prompt",
  );
  if (perm === "denied")
    throw new Error(
      "Notifications are blocked for NetMonitor on this device. iPhone: Settings → Apps → Notifications → NetMonitor → Allow. Then come back and try again.",
    );
  if (perm !== "granted") throw new Error("Permission prompt was dismissed — tap Turn on again.");

  onStep?.("Starting service worker…");
  const reg = await withTimeout(registration(), 15000, "Service worker");

  onStep?.("Fetching server key…");
  const { public_key } = await withTimeout(api.vapidKey(), 15000, "Server key fetch");

  onStep?.("Registering with the push service…");
  let sub = await reg.pushManager.getSubscription();
  if (!sub)
    sub = await withTimeout(
      reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: b64ToU8(public_key) as BufferSource,
      }),
      20000,
      "Push-service registration",
    );

  onStep?.("Saving to NetMonitor…");
  const json = sub.toJSON();
  if (!json.keys?.p256dh || !json.keys?.auth) {
    await sub.unsubscribe().catch(() => {});
    throw new Error("The push service returned an incomplete subscription — try Turn on again.");
  }
  await withTimeout(
    api.pushSubscribe({
      endpoint: sub.endpoint,
      keys: { p256dh: json.keys.p256dh, auth: json.keys.auth },
    }),
    15000,
    "Saving the subscription",
  );
}

export async function disablePush(): Promise<void> {
  const reg = await navigator.serviceWorker.getRegistration();
  const sub = await reg?.pushManager.getSubscription();
  if (sub) {
    await api.pushUnsubscribe(sub.endpoint).catch(() => {});
    await sub.unsubscribe();
  }
}
