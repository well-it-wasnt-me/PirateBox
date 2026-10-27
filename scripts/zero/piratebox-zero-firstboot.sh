#!/usr/bin/env bash
# PirateBox Zero W first-boot provisioning.
# The installer did the heavy lifting; this flips the only radio from client to access point.
set -euo pipefail

MARKER="/var/lib/piratebox/.zero_firstboot_done"
LOG_FILE="/var/log/piratebox-zero-firstboot.log"
INSTALL_DIR="/opt/piratebox"

exec > >(tee -a "${LOG_FILE}") 2>&1

if [[ -f "${MARKER}" ]]; then
  echo "First boot already completed. Nothing to see here."
  exit 0
fi

SSID="PirateBox"
COUNTRY=""
EPD_ROTATE="0"
EPD_ENABLE="1"

BOOT_CONFIG=""
for candidate in /boot/firmware/piratebox.txt /boot/piratebox.txt; do
  if [[ -f "${candidate}" ]]; then
    BOOT_CONFIG="${candidate}"
    break
  fi
done

read_boot_config() {
  # Parse KEY=value lines as data. Sourcing a file anyone can edit in Notepad is how you get pirated.
  local line key value
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line%$'\r'}"
    [[ "${line}" =~ ^[[:space:]]*(#|$) ]] && continue
    [[ "${line}" == *=* ]] || continue
    key="${line%%=*}"
    value="${line#*=}"
    key="${key//[[:space:]]/}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    case "${key}" in
      SSID) SSID="${value}" ;;
      COUNTRY) COUNTRY="${value}" ;;
      EPD_ROTATE) EPD_ROTATE="${value}" ;;
      EPD_ENABLE) EPD_ENABLE="${value}" ;;
      *) echo "Ignoring unknown setting '${key}'. Creativity noted." ;;
    esac
  done < "$1"
}

if [[ -n "${BOOT_CONFIG}" ]]; then
  echo "Reading ${BOOT_CONFIG}."
  read_boot_config "${BOOT_CONFIG}"
fi

if [[ -z "${SSID}" || ${#SSID} -gt 32 ]]; then
  echo "SSID '${SSID}' is not 1-32 characters. Falling back to PirateBox."
  SSID="PirateBox"
fi
if [[ -n "${COUNTRY}" && ! "${COUNTRY}" =~ ^[A-Za-z]{2}$ ]]; then
  echo "COUNTRY '${COUNTRY}' is not a two-letter code. Ignoring it."
  COUNTRY=""
fi
case "${EPD_ROTATE}" in
  0|90|180|270) ;;
  *)
    echo "EPD_ROTATE '${EPD_ROTATE}' is not 0/90/180/270. Using 0."
    EPD_ROTATE="0"
    ;;
esac

mkdir -p /var/lib/piratebox/data/files /var/lib/piratebox/tmp

# Tell the e-paper this is provisioning, not a failure. /run forgets it on reboot, which is the point.
# If provisioning dies halfway, say so, instead of leaving "starting" up there until the heat death.
mkdir -p /run/piratebox
printf 'STATE=checking\nREASON=provisioning\n' > /run/piratebox/ap-status
trap 'rc=$?; if (( rc != 0 )); then printf "STATE=offline\nREASON=provisioning-failed\n" > /run/piratebox/ap-status; fi' EXIT

echo "Starting the web app first, so a broken radio does not take the whole box down with it."
systemctl enable piratebox.service
systemctl restart piratebox.service

if [[ "${EPD_ENABLE}" == "1" ]]; then
  if [[ -f /etc/default/piratebox-epaper ]]; then
    sed -i "s/^PIRATEBOX_EPD_ROTATE=.*/PIRATEBOX_EPD_ROTATE=${EPD_ROTATE}/" /etc/default/piratebox-epaper
  fi
  systemctl enable piratebox-epaper.service
  systemctl restart piratebox-epaper.service || true
else
  echo "E-paper disabled. The box will be mysterious about its status."
  systemctl disable --now piratebox-epaper.service || true
fi

echo "Turning wlan0 into '${SSID}'. Any Wi-Fi client session on it ends here."
SETUP_ARGS=(--iface "${PIRATEBOX_WIFI_IFACE:-wlan0}" --ssid "${SSID}")
if [[ -n "${COUNTRY}" ]]; then
  SETUP_ARGS+=(--country "${COUNTRY}")
fi
"${INSTALL_DIR}/scripts/pi-setup.sh" "${SETUP_ARGS[@]}"

# The marker means "configuration is on disk", so make sure it is, before claiming so. Whether the
# radio is actually broadcasting is piratebox-ap-health's job, on this boot and every one after it.
sync
mkdir -p "$(dirname "${MARKER}")"
touch "${MARKER}"
sync

systemctl disable piratebox-zero-firstboot.service || true

echo "First boot provisioning complete. From now on piratebox-ap-health.service checks the AP on every boot."
echo "Join '${SSID}' and open http://10.0.0.1/"
