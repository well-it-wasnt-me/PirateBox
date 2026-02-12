# Raspberry Pi 5 setup (PirateBox)

This guide assumes Raspberry Pi OS Lite (64-bit). It builds an **open Wi-Fi** that routes users to the local PirateBox web app (no internet access). The internet can mind its own business.

## Quick script (optional)

Run the setup script to apply the steps below automatically:

```bash
sudo ./scripts/pi-setup.sh --iface wlan0
```

To run Docker Compose at the end:

```bash
sudo ./scripts/pi-setup.sh --iface wlan0 --run-compose
```

## 1) Install OS + packages

1. Flash Raspberry Pi OS Lite (64-bit) to your SD card.
2. Boot the Pi and log in.
3. Update and install dependencies:

```bash
sudo apt update
sudo apt install -y hostapd dnsmasq docker.io docker-compose-plugin
sudo systemctl unmask hostapd
```

Enable Docker at boot:

```bash
sudo systemctl enable docker
```

## 2) Give wlan0 a static IP

**Option A (dhcpcd, common on Lite):**

Edit `/etc/dhcpcd.conf` and add:

```
interface wlan0
static ip_address=10.0.0.1/24
nohook wpa_supplicant
```

Then restart:

```bash
sudo systemctl restart dhcpcd
```

**Option B (NetworkManager):**

```
sudo nmcli dev set wlan0 managed yes
sudo nmcli con add type wifi ifname wlan0 con-name piratebox-ap autoconnect yes ssid PirateBox
sudo nmcli con modify piratebox-ap ipv4.addresses 10.0.0.1/24 ipv4.method manual
```

## 2.5) Using an external USB Wi-Fi adapter

If you want the PirateBox access point to run on a **USB Wi-Fi adapter**, identify the adapter interface name and use it everywhere instead of `wlan0`.

Find the interface name:

```bash
ip -br link
iw dev
```

Common names are `wlan1` or `wlan2`. In the steps below, **replace `wlan0` with your adapter interface** (for example, `wlan1`).

Notes:

- Make sure the adapter supports **AP mode** (`iw list` shows `AP` under “Supported interface modes”).  
- You can keep the built-in Wi-Fi (`wlan0`) for admin access or leave it unused.

## 3) Configure hostapd (open Wi-Fi)

Create `/etc/hostapd/hostapd.conf`:

```
interface=wlan0
driver=nl80211
ssid=PirateBox
hw_mode=g
channel=6
auth_algs=1
wpa=0
wmm_enabled=0
ignore_broadcast_ssid=0
```

Point hostapd to that file in `/etc/default/hostapd`:

```
DAEMON_CONF="/etc/hostapd/hostapd.conf"
```

## 4) Configure dnsmasq (DHCP + DNS capture)

Create `/etc/dnsmasq.d/piratebox.conf`:

```
interface=wlan0
dhcp-range=10.0.0.10,10.0.0.200,255.255.255.0,12h
dhcp-option=option:router,10.0.0.1
dhcp-option=option:dns-server,10.0.0.1
address=/#/10.0.0.1
```

## 5) Keep the network offline

Make sure the access point does **not** forward traffic to the internet:

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

## 6) Start services

```bash
sudo systemctl enable hostapd dnsmasq
sudo systemctl restart hostapd dnsmasq
```

## 7) Run PirateBox with Docker

From the repo directory:

```bash
docker compose up -d --build
```

The web app will be available at:

- `http://10.0.0.1:8080`

If you want the app on port 80 for captive portals, change `docker-compose.yml` to `"80:8080"`.
The app serves a small captive portal welcome page at `/captive` and redirects common OS
connectivity checks there. The **OK, UNDERSTOOD** button sets the device as "acknowledged"
and attempts to close the portal and open the main UI.

## Optional: systemd service for the web app

If you want the app to start on boot without Docker:

```bash
sudo cp ./scripts/piratebox.service /etc/systemd/system/piratebox.service
sudo nano /etc/systemd/system/piratebox.service
```

Update `User=` and `WorkingDirectory=` to match your Pi. The unit expects a venv at `/opt/piratebox/.venv`; adjust `ExecStart=` if your path differs. Then:

```bash
sudo apt install -y python3-venv python3-pip
sudo python3 -m venv /opt/piratebox/.venv
sudo /opt/piratebox/.venv/bin/pip install -r /opt/piratebox/requirements.txt
sudo systemctl daemon-reload
sudo systemctl enable --now piratebox.service
```

Optional environment overrides:

```bash
sudo tee /etc/default/piratebox >/dev/null <<'EOF'
PIRATEBOX_NAME=PirateBox
PIRATEBOX_DATA_DIR=/var/lib/piratebox/data
PIRATEBOX_DB_PATH=/var/lib/piratebox/data/piratebox.db
PIRATEBOX_FILES_DIR=/var/lib/piratebox/data/files
PIRATEBOX_MAX_UPLOAD_MB=512
EOF
```

## 8) Test

1. Connect a phone/laptop to the **PirateBox** Wi-Fi.
2. Open a browser. A captive portal welcome page should appear; tap **OK, UNDERSTOOD** to enter.
3. Upload a file, post a chat message, and create a forum thread.

---

If you use a different Pi OS networking stack, adjust the Wi-Fi static IP steps accordingly.

## Optional: Prebuilt image

If you want an image that boots and installs PirateBox on first startup, use the CI artifact or build it yourself:

```bash
./scripts/build_rpi_image.sh
```

The image includes a first-boot systemd unit that installs Python deps and enables `piratebox.service`. It assumes temporary internet access on first boot to pull Python packages, then you can unplug it and enjoy the silence.
