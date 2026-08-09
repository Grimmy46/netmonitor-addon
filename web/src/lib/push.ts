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

export async function enablePush(): Promise<void> {
  if ((await Notification.requestPermission()) !== "granted")
    throw new Error("Notifications are blocked for this site — allow them in your browser settings.");
  const reg = await registration();
  const { public_key } = await api.vapidKey();
  const sub =
    (await reg.pushManager.getSubscription()) ??
    (await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: b64ToU8(public_key).buffer as ArrayBuffer,
    }));
  const json = sub.toJSON();
  await api.pushSubscribe({
    endpoint: sub.endpoint,
    keys: { p256dh: json.keys!.p256dh, auth: json.keys!.auth },
  });
}

export async function disablePush(): Promise<void> {
  const reg = await navigator.serviceWorker.getRegistration();
  const sub = await reg?.pushManager.getSubscription();
  if (sub) {
    await api.pushUnsubscribe(sub.endpoint).catch(() => {});
    await sub.unsubscribe();
  }
}
