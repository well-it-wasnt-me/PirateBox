#!/usr/bin/env bash
# Fake Raspberry Pi userland for the boot tests. One script, many names, zero radios.
# Symlink it as systemctl, rfkill, ip, iw, nmcli, raspi-config, ... and it plays each part,
# keeping the pretend kernel state in $FAKE_STATE and a pretend sysfs in $FAKE_SYSFS.
#
# Modelled on the real thing, not on wishful thinking:
# - NetworkManager writes its saved WirelessEnabled to rfkill on every start (nm-manager.c, 1.52).
# - hostapd survives an rfkill block and resumes beaconing on unblock (driver_nl80211.c, 2.10).
# - systemd-rfkill restores the saved state at boot and saves every change (rfkill.c, 257).
set -uo pipefail

S=${FAKE_STATE:?FAKE_STATE is not set}
SYS=${FAKE_SYSFS:?FAKE_SYSFS is not set}
NM_STATE=${FAKE_NM_STATE:-/var/lib/NetworkManager/NetworkManager.state}
UNIT_DIR=${FAKE_UNIT_DIR:-/etc/systemd/system}
NAME=$(basename "$0")

printf '%s %s\n' "${NAME}" "$*" >> "${S}/calls.log"

if [[ -n "${FAKE_CRASH_AT:-}" && "${NAME}" == "${FAKE_CRASH_AT}" ]]; then
  # Simulated power cut: everything in this boot dies mid-sentence, like the real thing.
  echo "${NAME} pulled the plug" >> "${S}/calls.log"
  kill -KILL 0
fi

get() { cat "${S}/$1" 2>/dev/null || echo "${2:-0}"; }
put() { printf '%s\n' "$2" > "${S}/$1"; }
unit_name() { local unit="${1%.service}"; echo "${unit}"; }
is_active() { [[ -f "${S}/units/$(unit_name "$1").active" ]]; }

sync_sys() {
  # Keep the pretend sysfs in step with the pretend kernel.
  local net="${SYS}/class/net/wlan0"
  if [[ "$(get wlan 1)" != "1" ]]; then
    rm -rf "${net}"
    return
  fi
  mkdir -p "${net}/phy80211/rfkill0"
  get rfkill 1 > "${net}/phy80211/rfkill0/soft"
  echo 0 > "${net}/phy80211/rfkill0/hard"
  if [[ "$(get beacon 0)" == "1" ]]; then
    echo 0x1003 > "${net}/flags"
  else
    echo 0x1002 > "${net}/flags"
  fi
}

set_rfkill() {
  put rfkill "$1"
  if [[ "$1" == "1" ]]; then
    # cfg80211 closes the interface; hostapd keeps running and says nothing useful.
    put beacon 0
  elif is_active hostapd; then
    put beacon 1
  fi
  if [[ -n "${FAKE_RFKILL_SAVE:-}" ]]; then
    mkdir -p "$(dirname "${FAKE_RFKILL_SAVE}")"
    echo "$1" > "${FAKE_RFKILL_SAVE}"
  fi
}

nm_radio_enabled() { ! grep -q '^WirelessEnabled=false' "${NM_STATE}" 2>/dev/null; }

nm_persist_radio_on() {
  mkdir -p "$(dirname "${NM_STATE}")"
  if grep -q '^WirelessEnabled=' "${NM_STATE}" 2>/dev/null; then
    sed -i 's/^WirelessEnabled=.*/WirelessEnabled=true/' "${NM_STATE}"
  else
    printf '[main]\nWirelessEnabled=true\n' >> "${NM_STATE}"
  fi
}

run_unit_execs() {
  # Execute a unit's ExecStartPre/ExecStart lines; fake commands win over real paths when we have one.
  local file="${UNIT_DIR}/$1.service" line cmd ignore rc=0
  local -a argv
  [[ -f "${file}" ]] || return 0
  while IFS= read -r line; do
    case "${line}" in
      ExecStartPre=*|ExecStart=*)
        cmd=${line#*=}
        ignore=0
        if [[ "${cmd}" == -* ]]; then
          ignore=1
          cmd=${cmd#-}
        fi
        read -ra argv <<< "${cmd}"
        if [[ -n "${FAKE_MOCKS:-}" && -e "${FAKE_MOCKS}/$(basename "${argv[0]}")" ]]; then
          argv[0]="${FAKE_MOCKS}/$(basename "${argv[0]}")"
        fi
        "${argv[@]}"
        rc=$?
        if (( rc != 0 && ignore == 0 )); then
          return "${rc}"
        fi
        ;;
    esac
  done < "${file}"
  return 0
}

start_unit() {
  local unit
  unit=$(unit_name "$1")
  if [[ -f "${S}/units/${unit}.fail" || -f "${S}/units/${unit}.failonce" ]]; then
    # .fail is a broken unit, .failonce a transient one: it fails exactly once and then behaves.
    rm -f "${S}/units/${unit}.active" "${S}/units/${unit}.failonce"
    return 1
  fi
  case "${unit}" in
    NetworkManager)
      touch "${S}/units/${unit}.active"
      if nm_radio_enabled; then set_rfkill 0; else set_rfkill 1; fi
      ;;
    hostapd)
      [[ "$(get wlan 1)" == "1" ]] || return 1
      if [[ -n "${FAKE_HOSTAPD_CONF:-}" && ! -s "${FAKE_HOSTAPD_CONF}" ]]; then
        return 1
      fi
      touch "${S}/units/${unit}.active"
      if [[ "$(get rfkill 1)" == "0" ]]; then put beacon 1; else put beacon 0; fi
      ;;
    piratebox-ap-ip)
      run_unit_execs "${unit}" || return 1
      touch "${S}/units/${unit}.active"
      ;;
    *)
      touch "${S}/units/${unit}.active"
      ;;
  esac
}

stop_unit() {
  local unit
  unit=$(unit_name "$1")
  rm -f "${S}/units/${unit}.active"
  if [[ "${unit}" == "hostapd" ]]; then
    put beacon 0
  fi
}

fake_systemctl() {
  local -a args=()
  local now=0 arg cmd unit rc=0
  for arg in "$@"; do
    case "${arg}" in
      --quiet|-q|--no-block) ;;
      --now) now=1 ;;
      *) args+=("${arg}") ;;
    esac
  done
  cmd=${args[0]:-}
  for unit in "${args[@]:1}"; do
    case "${cmd}" in
      is-active) is_active "${unit}" || return 3 ;;
      is-enabled) [[ -f "${S}/units/$(unit_name "${unit}").enabled" ]] || return 1 ;;
      enable)
        touch "${S}/units/$(unit_name "${unit}").enabled"
        (( now )) && { start_unit "${unit}" || rc=1; }
        ;;
      disable)
        rm -f "${S}/units/$(unit_name "${unit}").enabled"
        (( now )) && stop_unit "${unit}"
        ;;
      start) is_active "${unit}" || start_unit "${unit}" || rc=1 ;;
      restart)
        stop_unit "${unit}"
        start_unit "${unit}" || rc=1
        ;;
      stop) stop_unit "${unit}" ;;
      *) ;;
    esac
  done
  return "${rc}"
}

fake_ip() {
  case "$*" in
    "-4 -o addr show dev wlan0")
      [[ "$(get wlan 1)" == "1" ]] || return 1
      local addr
      while IFS= read -r addr; do
        [[ -n "${addr}" ]] && printf '3: wlan0    inet %s scope global wlan0\\       valid_lft forever preferred_lft forever\n' "${addr}"
      done < <(cat "${S}/addr" 2>/dev/null)
      ;;
    "addr replace "*" dev wlan0")
      [[ "$(get wlan 1)" == "1" ]] || return 1
      grep -qxF "$3" "${S}/addr" 2>/dev/null || echo "$3" >> "${S}/addr"
      ;;
    "link show wlan0") [[ "$(get wlan 1)" == "1" ]] ;;
    "link show "*) return 1 ;;
    *) return 0 ;;
  esac
}

fake_iw() {
  [[ "$(get wlan 1)" == "1" ]] || return 237
  case "$*" in
    "dev wlan0 info")
      if [[ "$(get beacon 0)" == "1" ]]; then
        printf 'Interface wlan0\n\tifindex 3\n\ttype AP\n\tssid PirateBox\n'
      else
        printf 'Interface wlan0\n\tifindex 3\n\ttype managed\n'
      fi
      ;;
    *) return 0 ;;
  esac
}

fake_nmcli() {
  case "$*" in
    "radio wifi on")
      is_active NetworkManager || return 8
      nm_persist_radio_on
      set_rfkill 0
      ;;
    *) return 0 ;;
  esac
}

fake_raspi_config() {
  # Only the slice of do_wifi_country that matters here: ISO codes or nothing.
  local country="${3:-}" file
  [[ "$1 $2" == "nonint do_wifi_country" ]] || return 0
  local known=" AT BE CH DE DK ES FI FR GB IE IT NL NO PL PT SE US "
  [[ -n "${country}" && "${known}" == *" ${country} "* ]] || return 1
  echo "${country}" > "${S}/regdom"
  if is_active NetworkManager; then
    fake_nmcli radio wifi on
  else
    set_rfkill 0
    [[ -f "${NM_STATE}" ]] && sed -i 's/^WirelessEnabled=.*/WirelessEnabled=true/' "${NM_STATE}"
  fi
  for file in /var/lib/systemd/rfkill/*:wlan; do
    [[ -e "${file}" ]] && echo 0 > "${file}"
  done
  return 0
}

fake_curl() {
  local out=""
  while [[ $# -gt 0 ]]; do
    if [[ "$1" == "-o" ]]; then
      out="$2"
      shift
    fi
    shift
  done
  [[ -n "${out}" ]] && echo "# fake download" > "${out}"
  return 0
}

fake_python() {
  # python3 -m venv makes a venv out of this very script; everything else "imports" fine.
  if [[ "${1:-}" == "-m" && "${2:-}" == "venv" ]]; then
    local dir="${*: -1}"
    mkdir -p "${dir}/bin"
    echo "include-system-site-packages = true" > "${dir}/pyvenv.cfg"
    ln -sf "${FAKE_MOCKS}/python3" "${dir}/bin/python"
  fi
  return 0
}

fake_boot() {
  # The tiniest init system: reset the volatile bits, then start what it is told, in that order.
  local unit rc
  case "$1" in
    reset)
      mkdir -p "${S}/units"
      rm -f "${S}"/units/*.active "${S}/addr" "${S}/beacon"
      put wlan "${FAKE_WLAN:-1}"
      put rfkill 1  # rfkill.default_state=0: every radio starts blocked
      if [[ -n "${FAKE_RFKILL_SAVE:-}" && -f "${FAKE_RFKILL_SAVE}" ]]; then
        set_rfkill "$(cat "${FAKE_RFKILL_SAVE}")"
      elif [[ -n "${FAKE_RFKILL_SAVE:-}" ]]; then
        set_rfkill 1
      fi
      ;;
    start)
      unit="$2"
      case "${unit}" in
        piratebox-zero-firstboot|piratebox-ap-health)
          if [[ "${unit}" == "piratebox-zero-firstboot" && -f /var/lib/piratebox/.zero_firstboot_done ]]; then
            echo skipped > "${S}/boot-${unit}.rc"
            return 0
          fi
          run_unit_execs "${unit}"
          rc=$?
          echo "${rc}" > "${S}/boot-${unit}.rc"
          (( rc == 0 )) && touch "${S}/units/${unit}.active"
          ;;
        *.target) ;;
        *) fake_systemctl start "${unit}" ;;
      esac
      ;;
  esac
}

rc=0
case "${NAME}" in
  systemctl) fake_systemctl "$@"; rc=$? ;;
  rfkill)
    case "${1:-}" in
      unblock) set_rfkill 0 ;;
      block) set_rfkill 1 ;;
    esac
    ;;
  ip) fake_ip "$@"; rc=$? ;;
  iw) fake_iw "$@"; rc=$? ;;
  nmcli) fake_nmcli "$@"; rc=$? ;;
  raspi-config) fake_raspi_config "$@"; rc=$? ;;
  dpkg-query) echo "install ok installed" ;;
  dpkg) echo armhf ;;
  curl) fake_curl "$@" ;;
  python3|python) fake_python "$@" ;;
  fakeboot) fake_boot "$@"; rc=$? ;;
  *) ;;  # apt-get, sysctl, iptables, sleep, udevadm: all agree, instantly
esac
sync_sys
exit "${rc}"
