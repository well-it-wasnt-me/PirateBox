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

export DEBIAN_FRONTEND=noninteractive

echo "Installing dependencies (because hope is not a package)."
apt-get update
apt-get install -y hostapd dnsmasq docker.io docker-compose-plugin
apt-get install -y dhcpcd || true
systemctl enable --now dhcpcd || true
systemctl restart hostapd dnsmasq || true
systemctl unmask hostapd
systemctl enable docker

DHCPD_OK=0
if command -v dhcpcd >/dev/null 2>&1 && systemctl list-unit-files | grep -q "^dhcpcd.service"; then
  if systemctl is-active --quiet dhcpcd; then
    DHCPD_OK=1
  fi
fi

if [[ "${DHCPD_OK}" == "1" && -f /etc/dhcpcd.conf ]]; then
  if ! grep -q "PirateBox static IP" /etc/dhcpcd.conf; then
    cat <<EOF >> /etc/dhcpcd.conf

# PirateBox static IP (managed by scripts/pi-setup.sh)
interface ${WIFI_IFACE}
static ip_address=${STATIC_IP}
nohook wpa_supplicant
EOF
  fi
  systemctl restart dhcpcd || true
elif command -v nmcli >/dev/null 2>&1; then
  nmcli dev set "${WIFI_IFACE}" managed yes || true
  if ! nmcli -t -f NAME connection show | grep -q "^piratebox-ap$"; then
    nmcli con add type wifi ifname "${WIFI_IFACE}" con-name piratebox-ap autoconnect yes ssid "${SSID}"
  fi
  nmcli con modify piratebox-ap ipv4.addresses "${STATIC_IP}" ipv4.method manual ipv6.method ignore
elif systemctl list-unit-files | grep -q "^systemd-networkd.service"; then
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
  echo "No dhcpcd or nmcli found. Configure ${WIFI_IFACE} with ${STATIC_IP} manually."
fi

HOSTAPD_CONF="/etc/hostapd/hostapd.conf"
{
  if [[ -n ${COUNTRY_CODE} ]]; then
    echo "country_code=${COUNTRY_CODE}"
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
echo "Web UI: http://${GATEWAY_IP}:8080"

do_compose() {
  # Run docker compose if a file is available.
  local dir="${1}"
  if [[ ! -f "${dir}/docker-compose.yml" ]]; then
    echo "No docker-compose.yml in ${dir}. Skipping compose run."
    return
  fi
  echo "Starting PirateBox via docker compose."
  docker compose -f "${dir}/docker-compose.yml" up -d --build
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
