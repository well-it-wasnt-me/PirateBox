#!/usr/bin/env bash
# PirateBox access point health check.
# Runs once per boot, after every service has reported success, and checks whether any of it is true.
# No -e on purpose: a failed probe is the information we came for, not a reason to leave.
set -uo pipefail

usage() {
  # Explain the options to whoever is debugging a silent radio at 2 AM.
  cat <<'EOF'
PirateBox access point health check

Usage:
  sudo piratebox-ap-health [options]

Checks that the AP is actually broadcasting (not just that hostapd exited 0), repairs only the
part that is broken, and gives up after a bounded number of attempts.

Options:
  --iface <wlanX>       AP interface (default: wlan0)
  --address <ip/cidr>   AP address (default: 10.0.0.1/24)
  --attempts <n>        Repair rounds before giving up; 0 only checks (default: 4)
  --interval <sec>      Wait between repair rounds (default: 15)
  --settle <sec>        Wait before the first check (default: 10)
  -h, --help            Show help

Exit status: 0 healthy, 1 not healthy, 2 usage error.
Log: /var/log/piratebox-ap-health.log. Status for the e-paper: /run/piratebox/ap-status.
EOF
}

IFACE=${PIRATEBOX_WIFI_IFACE:-wlan0}
ADDRESS=${PIRATEBOX_AP_ADDRESS:-10.0.0.1/24}
ATTEMPTS=${PIRATEBOX_AP_HEALTH_ATTEMPTS:-4}
INTERVAL=${PIRATEBOX_AP_HEALTH_INTERVAL:-15}
SETTLE=${PIRATEBOX_AP_HEALTH_SETTLE:-10}
# Path overrides exist so tests can fake a kernel. Production never sets them.
SYSFS=${PIRATEBOX_SYSFS:-/sys}
RUN_DIR=${PIRATEBOX_RUN_DIR:-/run/piratebox}
LOG_FILE=${PIRATEBOX_AP_HEALTH_LOG:-/var/log/piratebox-ap-health.log}
BOOT_STATUS=${PIRATEBOX_AP_BOOT_STATUS-auto}
LOG_MAX_BYTES=262144

while [[ $# -gt 0 ]]; do
  case "$1" in
    --iface)
      IFACE="$2"
      shift 2
      ;;
    --address)
      ADDRESS="$2"
      shift 2
      ;;
    --attempts)
      ATTEMPTS="$2"
      shift 2
      ;;
    --interval)
      INTERVAL="$2"
      shift 2
      ;;
    --settle)
      SETTLE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      usage
      exit 2
      ;;
  esac
done

for value in "${ATTEMPTS}" "${INTERVAL}" "${SETTLE}"; do
  if [[ ! "${value}" =~ ^[0-9]+$ ]]; then
    echo "Attempts, interval and settle are whole numbers. '${value}' is a mood."
    exit 2
  fi
done
if [[ ! "${ADDRESS}" =~ ^[0-9.]+/[0-9]+$ ]]; then
  echo "Address must look like 10.0.0.1/24, not '${ADDRESS}'."
  exit 2
fi

NET_DIR="${SYSFS}/class/net/${IFACE}"
STATUS_FILE="${RUN_DIR}/ap-status"
PROBLEMS=()
SEEN=()
ACTIONS=()

if [[ "${BOOT_STATUS}" == "auto" ]]; then
  # Only the Zero edition keeps piratebox.txt on the FAT partition, and only it lacks a console.
  # A status file there is readable from any laptop that can read an SD card.
  BOOT_STATUS=""
  for candidate in /boot/firmware /boot; do
    if [[ -f "${candidate}/piratebox.txt" ]]; then
      BOOT_STATUS="${candidate}/piratebox-ap-status.txt"
      break
    fi
  done
fi

log() {
  # Journal gets the plain line, the log file gets a timestamp the Zero's missing RTC may invent.
  printf '%s\n' "$*"
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "${LOG_FILE}" 2>/dev/null || true
}

rotate_log() {
  # One generation of history. This is a log file, not an archive.
  local size
  size=$(stat -c %s "${LOG_FILE}" 2>/dev/null || echo 0)
  if (( size > LOG_MAX_BYTES )); then
    mv -f "${LOG_FILE}" "${LOG_FILE}.1" 2>/dev/null || true
  fi
}

write_status() {
  # State for the e-paper. Lives in /run, so it cannot survive a reboot and lie about the next one.
  local state="$1"
  local reason="$2"
  local tmp="${STATUS_FILE}.tmp"
  {
    echo "STATE=${state}"
    echo "REASON=${reason}"
    echo "IFACE=${IFACE}"
    echo "ADDRESS=${ADDRESS}"
  } > "${tmp}" 2>/dev/null && mv -f "${tmp}" "${STATUS_FILE}" 2>/dev/null
  return 0
}

write_boot_status() {
  # One short file per boot on the FAT partition: the only log a headless Zero can hand to Windows.
  local state="$1"
  [[ -n "${BOOT_STATUS}" ]] || return 0
  local tmp="${BOOT_STATUS}.tmp"
  local boot_id uptime
  boot_id=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo unknown)
  uptime=$(cut -d' ' -f1 /proc/uptime 2>/dev/null || echo unknown)
  {
    echo "PirateBox access point status, written once per boot by piratebox-ap-health."
    echo "The clock may be fiction (no RTC on a Zero); the boot id is not."
    echo
    echo "STATE=${state}"
    echo "PROBLEMS_SEEN=${SEEN[*]:-none}"
    echo "PROBLEMS_LEFT=${PROBLEMS[*]:-none}"
    echo "ACTIONS=${ACTIONS[*]:-none}"
    echo "IFACE=${IFACE}"
    echo "ADDRESS=${ADDRESS}"
    echo "CHECKED_AT=$(date '+%Y-%m-%d %H:%M:%S')"
    echo "UPTIME_SECONDS=${uptime}"
    echo "BOOT_ID=${boot_id}"
    echo
    echo "Full history: /var/log/piratebox-ap-health.log on the root partition."
  } > "${tmp}" 2>/dev/null || return 0
  mv -f "${tmp}" "${BOOT_STATUS}" 2>/dev/null || return 0
  sync "${BOOT_STATUS}" 2>/dev/null || true
}

unit_enabled() {
  # A disabled unit is a decision someone made. The health check does not overrule people.
  systemctl is-enabled --quiet "$1" 2>/dev/null
}

rfkill_blocked() {
  # The phy's own switch, soft or hard. NetworkManager and systemd-rfkill both like to flip it.
  local switch
  for switch in "${NET_DIR}"/phy80211/rfkill*/soft "${NET_DIR}"/phy80211/rfkill*/hard; do
    [[ -r "${switch}" ]] || continue
    if [[ "$(< "${switch}")" == "1" ]]; then
      return 0
    fi
  done
  return 1
}

has_address() {
  # Exact match on the configured address, so 10.0.0.10 does not count as 10.0.0.1.
  ip -4 -o addr show dev "${IFACE}" 2>/dev/null \
    | awk -v want="${ADDRESS}" '{ for (i = 1; i < NF; i++) if ($i == "inet" && $(i + 1) == want) found = 1 } END { exit !found }'
}

broadcasting() {
  # hostapd stays "active" while its radio is rfkilled, so ask the kernel what is really on air.
  local flags info
  flags=$(cat "${NET_DIR}/flags" 2>/dev/null) || return 1
  [[ "${flags}" =~ ^0x[0-9a-fA-F]+$ ]] || return 1
  (( flags & 0x1 )) || return 1
  info=$(iw dev "${IFACE}" info 2>/dev/null) || return 1
  grep -qE '^[[:space:]]*type AP[[:space:]]*$' <<< "${info}" || return 1
  grep -qE '^[[:space:]]*ssid ' <<< "${info}"
}

check() {
  # Collect everything that is wrong, in the order it has to be fixed.
  PROBLEMS=()
  if [[ ! -d "${NET_DIR}" ]]; then
    PROBLEMS+=("no-interface")
  else
    rfkill_blocked && PROBLEMS+=("rfkill-blocked")
    has_address || PROBLEMS+=("no-address")
    if ! unit_enabled hostapd.service; then
      PROBLEMS+=("hostapd-disabled")
    elif ! systemctl is-active --quiet hostapd.service; then
      PROBLEMS+=("hostapd-inactive")
    elif ! broadcasting; then
      PROBLEMS+=("not-broadcasting")
    fi
  fi
  if ! unit_enabled dnsmasq.service; then
    PROBLEMS+=("dnsmasq-disabled")
  elif ! systemctl is-active --quiet dnsmasq.service; then
    PROBLEMS+=("dnsmasq-inactive")
  fi
  local problem
  for problem in "${PROBLEMS[@]}"; do
    [[ " ${SEEN[*]} " == *" ${problem} "* ]] || SEEN+=("${problem}")
  done
}

act() {
  # Run one repair, log it, and keep going whatever it says.
  local label="$1"
  shift
  ACTIONS+=("${label}")
  if "$@" >/dev/null 2>&1; then
    log "  ${label}: ok"
  else
    log "  ${label}: failed (exit $?)"
  fi
}

repair() {
  # Fix only what check() complained about. Healthy services do not get restarted for company.
  local problem unblocked=0
  for problem in "${PROBLEMS[@]}"; do
    case "${problem}" in
      rfkill-blocked)
        act "rfkill-unblock" rfkill unblock wlan
        unblocked=1
        ;;
      no-address)
        act "address-replace" ip addr replace "${ADDRESS}" dev "${IFACE}"
        ;;
      hostapd-inactive)
        act "hostapd-restart" systemctl restart hostapd.service
        ;;
      not-broadcasting)
        # A running hostapd resumes by itself after an unblock. Give it one round first.
        if (( unblocked )); then
          log "  hostapd: waiting a round, it usually notices the unblock on its own"
        else
          act "hostapd-restart" systemctl restart hostapd.service
        fi
        ;;
      dnsmasq-inactive)
        act "dnsmasq-restart" systemctl restart dnsmasq.service
        ;;
      no-interface)
        log "  ${IFACE}: not there yet. Nothing to restart; drivers load when they load."
        ;;
      hostapd-disabled|dnsmasq-disabled)
        log "  ${problem}: left alone. Someone disabled it; run pi-setup.sh again if that was not you."
        ;;
    esac
  done
}

mkdir -p "${RUN_DIR}" 2>/dev/null || true
rotate_log

if command -v flock >/dev/null 2>&1 && exec 9> "${RUN_DIR}/ap-health.lock"; then
  # First boot runs this from pi-setup.sh while systemd may want it too. One radio, one doctor.
  if ! flock -w 600 9; then
    log "Another health check held the lock for 10 minutes. Not joining the queue."
    exit 1
  fi
fi

write_status checking ""
log "Checking the access point on ${IFACE} (${ADDRESS}), up to ${ATTEMPTS} repair rounds."
if (( SETTLE > 0 )); then
  sleep "${SETTLE}"
fi

round=0
while true; do
  check
  if (( ${#PROBLEMS[@]} == 0 )); then
    if (( round == 0 )); then
      log "AP healthy on ${IFACE}: broadcasting, ${ADDRESS} assigned, dnsmasq running. No repairs needed."
    else
      log "AP healthy on ${IFACE} after ${round} repair round(s): ${ACTIONS[*]}."
    fi
    write_status healthy ""
    write_boot_status healthy
    exit 0
  fi
  if (( round >= ATTEMPTS )); then
    break
  fi
  round=$((round + 1))
  log "Round ${round}/${ATTEMPTS}: ${PROBLEMS[*]}"
  write_status checking "${PROBLEMS[0]}"
  repair
  sleep "${INTERVAL}"
done

log "AP NOT healthy on ${IFACE} after ${round} repair round(s): ${PROBLEMS[*]}. Giving up until the next boot."
write_status offline "${PROBLEMS[0]}"
write_boot_status offline
exit 1
