# HP box → NetMonitor agent host (Ubuntu Server + Docker)

Turns the HP currently running Home Assistant OS into a clean Linux host for the
NetMonitor 2.0 local agent.

> ⚠️ **This erases the drive**, including Home Assistant and the NetMonitor 1.0
> add-on + its data. Do Step 0 first. Your live 1.0 monitor goes offline once you
> wipe, until the 2.0 agent is running — that's expected.

You'll need: the HP, a **monitor** + **USB keyboard** (for the install only), an
**ethernet cable**, a **USB stick ≥ 4 GB** (its contents get erased), and your Mac.

---

## Step 0 — Back up Home Assistant (do NOT skip)
1. Open Home Assistant (the HAOS web UI on the 10.42 IP).
2. **Settings → System → Backups → Create backup** → choose **Full backup**.
3. When it finishes, **download** the `.tar` file to your Mac and keep it safe.
   (This lets you restore HA elsewhere later if you ever want to.)
4. While you're in HA: **Settings → System → Network** — jot down the current
   **IP / gateway / DNS** so you can reproduce the network setup if needed.

## Step 1 — Make the Ubuntu installer USB (on your Mac)
1. Download **Ubuntu Server 24.04 LTS** (.iso): https://ubuntu.com/download/server
2. Install **balenaEtcher**: https://etcher.balena.io
3. Open Etcher → **Flash from file** (the .iso) → **Select target** (your USB stick)
   → **Flash**. Takes a few minutes.

## Step 2 — Boot the HP from the USB
1. Plug into the HP: the USB stick, monitor, keyboard, and ethernet.
2. Power on and immediately tap **F9** repeatedly (HP boot menu). If F9 doesn't
   show a menu, try **Esc** first, or **F10** for BIOS setup.
3. Choose the **USB stick** as the boot device.
4. If it refuses to boot the USB: enter BIOS (**F10**) → disable **Secure Boot**
   and/or enable **USB boot / Legacy support**, save, retry. (Usually not needed —
   Ubuntu 24.04 boots with Secure Boot on.)

## Step 3 — Install Ubuntu Server
Follow the text installer:
1. Language, keyboard.
2. **Network:** easiest is to leave it on DHCP for now (we pin the IP later in
   Step 5). It should pick up an address automatically.
3. Skip the proxy and mirror screens (defaults are fine).
4. **Storage: "Use an entire disk"** → select the HP's **internal disk** →
   continue. Review the summary — it will show it's about to format the disk
   (this is the wipe) → **Done** → **Continue** to confirm.
5. **Profile:** your name; server name **`netmon-agent`**; a username; a password.
6. **✅ Check "Install OpenSSH server"** — important, so you can manage it
   headless over SSH afterward.
7. Skip the "featured snaps" list.
8. Let it install → **Reboot Now** → unplug the USB when prompted.

## Step 4 — First login + Docker
1. From your Mac's Terminal, SSH in (use the IP the box shows on its console after
   reboot, or find it in UniFi):
   ```bash
   ssh <username>@<hp-ip>
   ```
2. Update the system:
   ```bash
   sudo apt update && sudo apt upgrade -y
   ```
3. Install Docker (official script; includes Compose):
   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker $USER
   ```
4. **Log out and back in** (so the docker group applies), then verify:
   ```bash
   docker run hello-world
   ```
   Seeing "Hello from Docker!" means it's ready.

## Step 5 — Pin the IP (recommended: DHCP reservation in UniFi)
Rather than a hand-set static, reserve the HP's address in UniFi so it never
changes: UniFi → the site's **Client** list → find `netmon-agent` (by its MAC) →
**Fixed IP** → pick an address on the LAN that can reach both your **UniFi
gateway/console** and the **internet**. (The agent needs both: the internet to
reach the cloud, and the local UniFi console for deep per-device stats later.)

---

## Done — what's next
Once `docker run hello-world` works and you can SSH in, the host is ready. Tell me
and we'll deploy the agent container onto it (Phase 2). Because OpenSSH is on, I
can also drive setup commands through your Mac if you want, instead of you typing
them by hand.

**Note:** none of this blocks Phase 1 (the UniFi cloud integration) — that runs
without this box. So there's no rush; take Step 0 slowly.
