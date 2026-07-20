# NetMonitor — Home Assistant Add-on Setup Guide

## What You'll Need
- Home Assistant with **Supervisor** (this works on HA OS and HA Supervised — NOT HA Core or HA Container)
- A **GitHub account** (free) — HA loads custom add-ons from GitHub repos
- The `netmonitor-addon` folder from this download
- About 10 minutes

---

## Folder Structure
Your add-on folder should look exactly like this:
```
netmonitor-addon/
├── repository.json
└── netmonitor/
    ├── config.yaml
    ├── Dockerfile
    ├── run.sh
    └── network_tester.py
```

---

## Step 1 — Edit repository.json
Open `repository.json` and replace the placeholder values with your own:
```json
{
  "name": "NetMonitor Add-on Repository",
  "url": "https://github.com/YOUR_USERNAME/netmonitor-addon",
  "maintainer": "Your Name"
}
```
Replace `YOUR_USERNAME` with your actual GitHub username.

---

## Step 2 — Push to GitHub
1. Go to **github.com** → click **New repository**
2. Name it `netmonitor-addon`
3. Set it to **Public** (required for HA to read it)
4. Upload all the files maintaining the folder structure above
   - You can drag-and-drop the entire folder in the GitHub web interface
5. Note your repo URL — it will be: `https://github.com/YOUR_USERNAME/netmonitor-addon`

---

## Step 3 — Add the Repository to Home Assistant
1. In HA, go to **Settings → Add-ons → Add-on Store**
2. Click the **⋮ menu** (top right) → **Repositories**
3. Paste your GitHub URL: `https://github.com/YOUR_USERNAME/netmonitor-addon`
4. Click **Add** → **Close**
5. The store will refresh. Scroll to the bottom — you should see **"NetMonitor Add-on Repository"**

---

## Step 4 — Install the Add-on
1. Click **NetMonitor** in the add-on store
2. Click **Install** (this may take 1–2 minutes while Docker builds the image)
3. Once installed, go to the **Configuration** tab (no options needed — leave it as-is)
4. Go to the **Info** tab → toggle **Start on boot** ON → click **Start**

---

## Step 5 — Open the Dashboard
Once the add-on is running, open your browser and go to:
```
http://homeassistant.local:8088
```
Or if you use a static IP:
```
http://YOUR_HA_IP:8088
```

The dashboard will load and begin running tests immediately.

---

## Data Persistence
All data files are stored at `/config/netmonitor/` on your HA instance:
- `targets.json` — your saved targets (survives restarts & updates)
- `network_results.csv` — full historical log
- `latest_results.json` — current dashboard data

You can access these via the **Studio Code Server** add-on or via SSH.

---

## Updating the Add-on
To update (e.g. after uploading a new `network_tester.py` to GitHub):
1. Go to **Settings → Add-ons → NetMonitor**
2. Click **Update** (or uninstall and reinstall)

Your data in `/config/netmonitor/` is preserved across updates.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Add-on not appearing in store | Wait 30s and hard-refresh the page |
| "Failed to install" error | Check the Dockerfile — ensure the repo is Public |
| Dashboard not loading | Check the add-on Log tab for Python errors |
| Port 8088 blocked | Check your router/firewall; try accessing from within your local network first |
| Traceroute showing errors | Normal on some networks — ISPs block ICMP on certain hops |

---

## Optional: HA Dashboard Card
Add a Webpage card to your Lovelace dashboard to embed NetMonitor directly in HA:
1. Edit your dashboard → **Add Card** → **Webpage**
2. Set URL to: `http://YOUR_HA_IP:8088`
3. Save — NetMonitor will appear as a panel inside HA!
