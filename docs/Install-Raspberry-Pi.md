---
title: Install on Raspberry Pi
---

# Install on Raspberry Pi

This guide assumes Raspberry Pi OS Lite (64-bit) and works well on Raspberry Pi 5. The goal is an open Wi-Fi access point that keeps traffic local and points clients to PirateBox.

## Before you start

- Use a decent power supply. Random phone chargers have a habit of lying.
- Have a keyboard and monitor, or enable SSH during imaging.
- Decide if you want to run via Docker or systemd. Docker is convenient. Systemd is honest.

## Quick script (optional)

Run the setup script to apply the access point and offline steps automatically:

```bash
sudo ./scripts/pi-setup.sh --iface wlan0
```

To run Docker Compose at the end:

```bash
sudo ./scripts/pi-setup.sh --iface wlan0 --run-compose
```

If you use the script, it handles the hostapd/dnsmasq config, static IP, and offline rules. You can skip to the test section.

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

The web app will be available at `http://10.0.0.1:8080` when the AP is up.
If you want the app on port 80 for captive portals, change `docker-compose.yml` to `"80:8080"`.
The app serves a small captive portal welcome page at `/captive` and redirects common OS connectivity checks there.
The OK button sets the device as acknowledged and attempts to open the main UI.

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

### Using an external USB Wi-Fi adapter

If you want the PirateBox access point to run on a USB Wi-Fi adapter, identify the adapter interface name and use it everywhere instead of `wlan0`.

Find the interface name:

```bash
ip -br link
iw dev
```

Common names are `wlan1` or `wlan2`. In the steps below, replace `wlan0` with your adapter interface (for example, `wlan1`).

Notes:

- Make sure the adapter supports AP mode (`iw list` shows `AP` under "Supported interface modes").
- You can keep the built-in Wi-Fi (`wlan0`) for admin access or leave it unused.

### Manual steps

1. Install packages:

```bash
sudo apt install -y hostapd dnsmasq
sudo systemctl unmask hostapd
```

2. Give `wlan0` a static IP using your networking stack:

Option A (dhcpcd, common on Lite):

```bash
sudo tee -a /etc/dhcpcd.conf >/dev/null <<'EOF'
# PirateBox static IP
interface wlan0
static ip_address=10.0.0.1/24
nohook wpa_supplicant
EOF
sudo systemctl restart dhcpcd
```

Option B (NetworkManager):

```bash
sudo nmcli dev set wlan0 managed yes
sudo nmcli con add type wifi ifname wlan0 con-name piratebox-ap autoconnect yes ssid PirateBox
sudo nmcli con modify piratebox-ap ipv4.addresses 10.0.0.1/24 ipv4.method manual
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

## Step 5: Keep it offline

Make sure the access point does not forward traffic to the internet:

```bash
sudo sh -c 'printf "net.ipv4.ip_forward=0\n" > /etc/sysctl.d/99-piratebox.conf'
sudo sysctl -p /etc/sysctl.d/99-piratebox.conf
```

If the Pi has an ethernet uplink, block forwarding from Wi-Fi:

```bash
sudo iptables -A FORWARD -i wlan0 -o eth0 -j DROP
sudo iptables -A FORWARD -i eth0 -o wlan0 -j DROP
```

(Optional) Persist iptables using `iptables-persistent`.

## Step 6: Start services

```bash
sudo systemctl enable --now hostapd dnsmasq
```

## Step 7: Run PirateBox

If you are using Docker, start the stack (or re-run after changing ports):

```bash
docker compose up -d --build
```

If you are using systemd, confirm the service is enabled and running:

```bash
sudo systemctl status piratebox.service
```

## Step 8: Test

1. Connect a phone or laptop to the PirateBox Wi-Fi.
2. Open a browser. A captive portal welcome page should appear; tap OK to enter.
3. Upload a file, post a chat message, and create a forum thread.

## Optional: Prebuilt image

If you want an image that boots and installs PirateBox on first startup, see `Prebuilt-Image.md` or build it yourself:

```bash
./scripts/build_rpi_image.sh
```

The image includes a first-boot systemd unit that installs Python deps and enables `piratebox.service`.
It assumes temporary internet access on first boot to pull Python packages, then you can unplug it.

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
- If you use a different Pi OS networking stack, adjust the Wi-Fi static IP steps accordingly.
- The Pi is the single point of failure. Keep a spare SD card if you want to sleep at night.
