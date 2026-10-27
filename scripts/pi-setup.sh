#!/usr/bin/env bash
# PirateBox Pi setup helper.
# It sets up an offline AP because the internet is overrated and unreliable.
set -euo pipefail

usage() {
  # Explain the options like you didn't already read the README.
  cat <<'EOF'
PirateBox Pi setup script

Usage:
  sudo ./scripts/pi-setup.sh [options]

Options:
  --iface <wlanX>        Wi-Fi interface for the access point (default: wlan0)
  --ssid <name>          SSID to broadcast (default: PirateBox)
  --country <CC>         Wi-Fi regulatory country code, e.g. IT or US (default: unset)
  --run-compose          Build and start docker compose if a compose file is found
  --compose-dir <path>   Directory that contains docker-compose.yml
  -h, --help             Show help

Environment overrides:
  PIRATEBOX_WIFI_IFACE, PIRATEBOX_SSID, PIRATEBOX_GATEWAY_IP, PIRATEBOX_CIDR,
  PIRATEBOX_DHCP_START, PIRATEBOX_DHCP_END, PIRATEBOX_LAN_IFACE,
  PIRATEBOX_CHANNEL, PIRATEBOX_HW_MODE, PIRATEBOX_COUNTRY_CODE,
  PIRATEBOX_RUN_COMPOSE, PIRATEBOX_COMPOSE_DIR
EOF
}

WIFI_IFACE=${PIRATEBOX_WIFI_IFACE:-wlan0}
SSID=${PIRATEBOX_SSID:-PirateBox}
GATEWAY_IP=${PIRATEBOX_GATEWAY_IP:-10.0.0.1}
CIDR=${PIRATEBOX_CIDR:-24}
DHCP_START=${PIRATEBOX_DHCP_START:-10.0.0.10}
DHCP_END=${PIRATEBOX_DHCP_END:-10.0.0.200}
LAN_IFACE=${PIRATEBOX_LAN_IFACE:-eth0}
CHANNEL=${PIRATEBOX_CHANNEL:-6}
HW_MODE=${PIRATEBOX_HW_MODE:-g}
COUNTRY_CODE=${PIRATEBOX_COUNTRY_CODE:-}
RUN_COMPOSE=${PIRATEBOX_RUN_COMPOSE:-0}
COMPOSE_DIR=${PIRATEBOX_COMPOSE_DIR:-}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --iface)
      WIFI_IFACE="$2"
      shift 2
      ;;
    --ssid)
      SSID="$2"
      shift 2
      ;;
    --country)
      COUNTRY_CODE="$2"
      shift 2
      ;;
    --run-compose)
      RUN_COMPOSE=1
      shift
      ;;
    --compose-dir)
      COMPOSE_DIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Run this with sudo. The Pi does not do charity."
  exit 1
fi

STATIC_IP="${GATEWAY_IP}/${CIDR}"
COUNTRY_CODE=${COUNTRY_CODE^^}

export DEBIAN_FRONTEND=noninteractive

APT_UPDATED=0

apt_install() {
  # Run apt-get update once per script, not once per regret.
  if [[ "${APT_UPDATED}" == "0" ]]; then
    apt-get update
    APT_UPDATED=1
  fi
  apt-get install -y "$@"
}

pkg_installed() {
  # dpkg is the only witness that matters; dnsmasq-base ships a binary but no service.
  dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "install ok installed"
}

unit_on() {
  # A unit counts if it runs now or will run at boot. Timing is not a personality.
  systemctl is-active --quiet "$1" 2>/dev/null || systemctl is-enabled --quiet "$1" 2>/dev/null
}

echo "Installing dependencies (because hope is not a package)."
if pkg_installed hostapd && pkg_installed dnsmasq; then
  echo "hostapd and dnsmasq are already here. The mirrors get the day off."
else
  apt_install hostapd dnsmasq
fi

if [[ "${RUN_COMPOSE}" == "1" ]]; then
  if ! command -v docker >/dev/null 2>&1; then
    apt_install docker.io
  fi
  if ! docker compose version >/dev/null 2>&1 && ! command -v docker-compose >/dev/null 2>&1; then
    # Docker's repo calls it docker-compose-plugin, Debian calls it docker-compose. Naming is hard.
    apt_install docker-compose-plugin || apt_install docker-compose
  fi
  systemctl enable --now docker
fi

systemctl unmask hostapd

if command -v rfkill >/dev/null 2>&1; then
  # Raspberry Pi OS ships Wi-Fi soft-blocked until someone picks a country. Consider it picked.
  rfkill unblock wlan || true
fi
if [[ -n "${COUNTRY_CODE}" ]] && command -v raspi-config >/dev/null 2>&1; then
  raspi-config nonint do_wifi_country "${COUNTRY_CODE}" || true
fi

write_ap_ip_unit() {
  # Pin the gateway IP with a tiny unit instead of negotiating with a network manager.
  cat <<EOF > /etc/systemd/system/piratebox-ap-ip.service
# PirateBox access point address. Managed by scripts/pi-setup.sh.
[Unit]
Description=PirateBox access point address on ${WIFI_IFACE}
BindsTo=sys-subsystem-net-devices-${WIFI_IFACE}.device
After=sys-subsystem-net-devices-${WIFI_IFACE}.device
Before=hostapd.service dnsmasq.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=-/usr/sbin/rfkill unblock wlan
ExecStart=/sbin/ip addr replace ${STATIC_IP} dev ${WIFI_IFACE}

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable piratebox-ap-ip.service
  systemctl restart piratebox-ap-ip.service || true
}

if unit_on NetworkManager; then
  # NetworkManager and hostapd both want the radio. Only one of them gets to be the AP.
  mkdir -p /etc/NetworkManager/conf.d
  cat <<EOF > /etc/NetworkManager/conf.d/99-piratebox.conf
# PirateBox owns this interface now. Managed by scripts/pi-setup.sh.
[keyfile]
unmanaged-devices=interface-name:${WIFI_IFACE}
EOF
  if command -v nmcli >/dev/null 2>&1; then
    # Older versions of this script left a client-mode profile here. It never worked.
    nmcli connection delete piratebox-ap >/dev/null 2>&1 || true
    nmcli device disconnect "${WIFI_IFACE}" >/dev/null 2>&1 || true
  fi
  systemctl restart NetworkManager || true
  write_ap_ip_unit
elif unit_on dhcpcd && [[ -f /etc/dhcpcd.conf ]]; then
  if ! grep -q "PirateBox static IP" /etc/dhcpcd.conf; then
    cat <<EOF >> /etc/dhcpcd.conf

# PirateBox static IP (managed by scripts/pi-setup.sh)
interface ${WIFI_IFACE}
static ip_address=${STATIC_IP}
nohook wpa_supplicant
EOF
  fi
  systemctl restart dhcpcd || true
elif unit_on systemd-networkd; then
  mkdir -p /etc/systemd/network
  cat <<EOF > /etc/systemd/network/10-piratebox-ap.network
[Match]
Name=${WIFI_IFACE}

[Network]
Address=${STATIC_IP}
EOF
  systemctl enable --now systemd-networkd || true
  systemctl restart systemd-networkd || true
else
  echo "No NetworkManager, dhcpcd or networkd found. Pinning ${STATIC_IP} on ${WIFI_IFACE} ourselves."
  write_ap_ip_unit
fi

HOSTAPD_CONF="/etc/hostapd/hostapd.conf"
{
  if [[ -n ${COUNTRY_CODE} ]]; then
    echo "country_code=${COUNTRY_CODE}"
    echo "ieee80211d=1"
  fi
  cat <<EOF
interface=${WIFI_IFACE}
driver=nl80211
ssid=${SSID}
hw_mode=${HW_MODE}
channel=${CHANNEL}
auth_algs=1
wpa=0
wmm_enabled=0
ignore_broadcast_ssid=0
EOF
} > "${HOSTAPD_CONF}"

cat <<EOF > /etc/default/hostapd
DAEMON_CONF="${HOSTAPD_CONF}"
EOF

cat <<EOF > /etc/dnsmasq.d/piratebox.conf
interface=${WIFI_IFACE}
dhcp-range=${DHCP_START},${DHCP_END},255.255.255.0,12h
dhcp-option=option:router,${GATEWAY_IP}
dhcp-option=option:dns-server,${GATEWAY_IP}
address=/#/${GATEWAY_IP}
EOF

printf "net.ipv4.ip_forward=0\n" > /etc/sysctl.d/99-piratebox.conf
sysctl -p /etc/sysctl.d/99-piratebox.conf

if command -v iptables >/dev/null 2>&1 && ip link show "${LAN_IFACE}" >/dev/null 2>&1; then
  iptables -C FORWARD -i "${WIFI_IFACE}" -o "${LAN_IFACE}" -j DROP 2>/dev/null || \
    iptables -A FORWARD -i "${WIFI_IFACE}" -o "${LAN_IFACE}" -j DROP
  iptables -C FORWARD -i "${LAN_IFACE}" -o "${WIFI_IFACE}" -j DROP 2>/dev/null || \
    iptables -A FORWARD -i "${LAN_IFACE}" -o "${WIFI_IFACE}" -j DROP
fi

systemctl enable hostapd dnsmasq
systemctl restart hostapd dnsmasq

echo "PirateBox AP is up on ${WIFI_IFACE}. SSID: ${SSID}."
if [[ "${RUN_COMPOSE}" == "1" ]]; then
  echo "Web UI: http://${GATEWAY_IP}:8080"
else
  echo "Web UI: http://${GATEWAY_IP}/ (piratebox.service, port 80)"
fi

do_compose() {
  # Run docker compose if a file is available.
  local dir="${1}"
  if [[ ! -f "${dir}/docker-compose.yml" ]]; then
    echo "No docker-compose.yml in ${dir}. Skipping compose run."
    return
  fi
  echo "Starting PirateBox via docker compose."
  if docker compose version >/dev/null 2>&1; then
    docker compose -f "${dir}/docker-compose.yml" up -d --build
  else
    docker-compose -f "${dir}/docker-compose.yml" up -d --build
  fi
}

if [[ "${RUN_COMPOSE}" == "1" ]]; then
  if [[ -n "${COMPOSE_DIR}" ]]; then
    do_compose "${COMPOSE_DIR}"
  elif [[ -f "${PWD}/docker-compose.yml" ]]; then
    do_compose "${PWD}"
  else
    SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
    do_compose "${SCRIPT_DIR}/.."
  fi
fi
