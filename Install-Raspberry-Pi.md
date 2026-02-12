---
title: Install on Raspberry Pi
---

# Install on Raspberry Pi

This guide assumes Raspberry Pi OS Lite (64-bit). The goal is an open Wi-Fi access point that keeps traffic local and points clients to PirateBox.

## Before you start

- Use a decent power supply. Random phone chargers have a habit of lying.
- Have a keyboard and monitor, or enable SSH during imaging.
- Decide if you want to run via Docker or systemd. Docker is convenient. Systemd is honest.

## Step 1: Flash the OS

1. Download Raspberry Pi OS Lite (64-bit).
2. Flash it to a microSD card using Raspberry Pi Imager or Balena Etcher.
3. If you want SSH, enable it in the Imager settings.
4. Boot the Pi and log in.

## Step 2: Update packages

```bash
sudo apt update
sudo apt upgrade -y
```

## Step 3: Install PirateBox

### Option A: Docker

```bash
sudo apt install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
```

Clone the repo and start the stack:

```bash
git clone https://github.com/your-org/piratebox.git
cd piratebox
docker compose up -d --build
```

### Option B: systemd + venv

```bash
sudo apt install -y python3-venv python3-pip
```

```bash
git clone https://github.com/your-org/piratebox.git
cd piratebox
python3 -m venv /opt/piratebox/.venv
/opt/piratebox/.venv/bin/pip install -r requirements.txt
```

Install the unit file:

```bash
sudo cp ./scripts/piratebox.service /etc/systemd/system/piratebox.service
sudo systemctl daemon-reload
sudo systemctl enable --now piratebox.service
```

## Step 4: Configure the access point

You can run the helper script or configure manually. The script is faster and less educational.

### Option A: Run the script

```bash
sudo ./scripts/pi-setup.sh --iface wlan0
```

### Option B: Manual steps

1. Install packages:

```bash
sudo apt install -y hostapd dnsmasq
sudo systemctl unmask hostapd
```

2. Give `wlan0` a static IP using your networking stack:

```bash
sudo tee -a /etc/dhcpcd.conf >/dev/null <<'EOF'
# PirateBox static IP
interface wlan0
static ip_address=10.0.0.1/24
nohook wpa_supplicant
EOF
sudo systemctl restart dhcpcd
```

3. Configure hostapd:

```bash
sudo tee /etc/hostapd/hostapd.conf >/dev/null <<'EOF'
interface=wlan0
driver=nl80211
ssid=PirateBox
hw_mode=g
channel=6
auth_algs=1
wpa=0
wmm_enabled=0
ignore_broadcast_ssid=0
EOF
```

```bash
sudo tee /etc/default/hostapd >/dev/null <<'EOF'
DAEMON_CONF="/etc/hostapd/hostapd.conf"
EOF
```

4. Configure dnsmasq:

```bash
sudo tee /etc/dnsmasq.d/piratebox.conf >/dev/null <<'EOF'
interface=wlan0
dhcp-range=10.0.0.10,10.0.0.200,255.255.255.0,12h
dhcp-option=option:router,10.0.0.1
dhcp-option=option:dns-server,10.0.0.1
address=/#/10.0.0.1
EOF
```

5. Keep it offline:

```bash
sudo sh -c 'printf "net.ipv4.ip_forward=0\n" > /etc/sysctl.d/99-piratebox.conf'
sudo sysctl -p /etc/sysctl.d/99-piratebox.conf
```

6. Start services:

```bash
sudo systemctl enable --now hostapd dnsmasq
```

## Step 5: Test

1. Join the `PirateBox` Wi-Fi from a phone or laptop.
2. Open a browser and go to `http://10.0.0.1:8080`.
3. Upload a file, send a chat, create a forum thread, and accept your new role as local sysadmin.

## Optional: Set environment defaults

```bash
sudo tee /etc/default/piratebox >/dev/null <<'EOF'
PIRATEBOX_NAME=PirateBox
PIRATEBOX_DATA_DIR=/var/lib/piratebox/data
PIRATEBOX_DB_PATH=/var/lib/piratebox/data/piratebox.db
PIRATEBOX_FILES_DIR=/var/lib/piratebox/data/files
PIRATEBOX_MAX_UPLOAD_MB=512
EOF
```

## Notes

- For captive portal friendliness, running on port 80 helps. `app/main.py` uses port 80 by default when started directly.
- If you change the SSID or subnet, update the docs and your expectations.
- The Pi is the single point of failure. Keep a spare SD card if you want to sleep at night.
