#!/usr/bin/env bash
# PirateBox installer for the Raspberry Pi Zero W + Waveshare 2.13" e-Paper HAT (B) V4.
# One core, 512 MB of RAM, one radio, zero buttons. Ambition, scaled to fit.
set -euo pipefail

usage() {
  # Explain the options to the one person who reads usage text. Hi.
  cat <<'EOF'
PirateBox Zero W installer

Usage:
  sudo ./scripts/zero/install-zero.sh [options]

Run it on Raspberry Pi OS Lite (32-bit, trixie) on a Pi Zero W with internet access.
Everything is installed now; the Wi-Fi turns into the PirateBox access point on the next boot.

Options:
  --ssid <name>      SSID to broadcast (default: PirateBox)
  --country <CC>     Wi-Fi country code, e.g. IT or US (default: unset)
  --rotate <deg>     E-paper rotation: 0, 90, 180 or 270 (default: 0)
  --no-epaper        Skip the e-paper display (headless pirate)
  --no-reboot        Do not reboot at the end (the AP still starts on the next boot)
  --image-build      Internal: running inside an image chroot, so no reboot and no live checks
  -h, --help         Show help

Environment overrides:
  PIRATEBOX_SSID, PIRATEBOX_COUNTRY_CODE, PIRATEBOX_EPD_ROTATE, PIRATEBOX_WAVESHARE_REF
EOF
}

SSID=${PIRATEBOX_SSID:-PirateBox}
COUNTRY_CODE=${PIRATEBOX_COUNTRY_CODE:-}
EPD_ROTATE=${PIRATEBOX_EPD_ROTATE:-0}
EPD_ENABLE=1
REBOOT=1
IMAGE_BUILD=0

# Pinned so a surprise upstream refactor does not become our surprise too.
WAVESHARE_REF=${PIRATEBOX_WAVESHARE_REF:-d28b3c4cfc78eef1c6cd6e6e3013654e449d2864}
WAVESHARE_BASE="https://raw.githubusercontent.com/waveshareteam/e-Paper/${WAVESHARE_REF}/RaspberryPi_JetsonNano/python/lib/waveshare_epd"
WAVESHARE_FILES=(__init__.py epdconfig.py epd2in13b_V4.py)

INSTALL_DIR=/opt/piratebox
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SRC_DIR=$(cd -- "${SCRIPT_DIR}/../.." && pwd)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ssid)
      SSID="$2"
      shift 2
      ;;
    --country)
      COUNTRY_CODE="$2"
      shift 2
      ;;
    --rotate)
      EPD_ROTATE="$2"
      shift 2
      ;;
    --no-epaper)
      EPD_ENABLE=0
      shift
      ;;
    --no-reboot)
      REBOOT=0
      shift
      ;;
    --image-build)
      IMAGE_BUILD=1
      REBOOT=0
      shift
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
  echo "Run this with sudo. The Zero has 512 MB of RAM and no patience for permissions."
  exit 1
fi

if [[ -z "${SSID}" || ${#SSID} -gt 32 ]]; then
  echo "SSID must be 1-32 characters. Wi-Fi standards are stricter than we are."
  exit 1
fi
if [[ -n "${COUNTRY_CODE}" && ! "${COUNTRY_CODE}" =~ ^[A-Za-z]{2}$ ]]; then
  echo "Country code must be two letters, like IT or US."
  exit 1
fi
case "${EPD_ROTATE}" in
  0|90|180|270) ;;
  *)
    echo "Rotation must be 0, 90, 180 or 270. The panel only spins in right angles."
    exit 1
    ;;
esac

ARCH=$(dpkg --print-architecture)
if [[ "${ARCH}" != "armhf" ]]; then
  echo "Heads up: this is ${ARCH}, not armhf. A Zero W only boots 32-bit Raspberry Pi OS."
  echo "Continuing anyway, bravely and possibly pointlessly."
fi
if [[ "${IMAGE_BUILD}" == "0" && -r /proc/device-tree/model ]]; then
  MODEL=$(tr -d '\0' < /proc/device-tree/model)
  echo "Installing on: ${MODEL}"
  if [[ "${MODEL}" != *"Zero W"* ]]; then
    echo "Not a Zero W. It will probably work; it was just never this box's dream."
  fi
fi

export DEBIAN_FRONTEND=noninteractive

# Everything comes from apt: Raspbian ships FastAPI and pydantic-core prebuilt for ARMv6.
# pip would happily spend an afternoon compiling Rust on one core instead.
PACKAGES=(
  hostapd dnsmasq iw rfkill rsync curl ca-certificates
  python3 python3-venv
  python3-fastapi python3-uvicorn python3-jinja2 python3-python-multipart
)
if [[ "${EPD_ENABLE}" == "1" ]]; then
  PACKAGES+=(python3-pil python3-spidev python3-gpiozero fonts-dejavu-core)
fi

echo "Installing packages (because hope is not a package)."
apt-get update
apt-get install -y --no-install-recommends "${PACKAGES[@]}"
if [[ "${EPD_ENABLE}" == "1" ]]; then
  # gpiozero needs a pin backend. lgpio is the modern one, RPi.GPIO the vintage one; the Zero speaks both.
  apt-get install -y --no-install-recommends python3-lgpio \
    || apt-get install -y --no-install-recommends python3-rpi.gpio
fi

if [[ "$(realpath "${SRC_DIR}")" != "${INSTALL_DIR}" ]]; then
  echo "Copying PirateBox into ${INSTALL_DIR}."
  mkdir -p "${INSTALL_DIR}"
  rsync -a --delete \
    --exclude ".git" \
    --exclude ".idea" \
    --exclude ".venv" \
    --exclude "__pycache__" \
    --exclude ".pytest_cache" \
    --exclude "/data" \
    --exclude "/dist" \
    --exclude "/site" \
    --exclude "/vendor" \
    "${SRC_DIR}/" "${INSTALL_DIR}/"
fi

VENV="${INSTALL_DIR}/.venv"
if [[ -f "${VENV}/pyvenv.cfg" ]] && ! grep -q "include-system-site-packages = true" "${VENV}/pyvenv.cfg"; then
  echo "Existing venv cannot see the apt packages. Rebuilding it, no hard feelings."
  rm -rf "${VENV}"
fi
if [[ ! -x "${VENV}/bin/python" ]]; then
  # A venv that is just a window onto the system packages, so the stock units work unchanged.
  python3 -m venv --system-site-packages --without-pip "${VENV}"
fi

fetch_waveshare() {
  # Three files from Waveshare, not their 100 MB repo of Arduino demos.
  local dest="${INSTALL_DIR}/vendor/waveshare_epd"
  local tmp
  tmp=$(mktemp -d)
  local file
  for file in "${WAVESHARE_FILES[@]}"; do
    if ! curl -fsSL --retry 3 -o "${tmp}/${file}" "${WAVESHARE_BASE}/${file}"; then
      rm -rf "${tmp}"
      return 1
    fi
  done
  mkdir -p "${dest}"
  cp "${tmp}"/*.py "${dest}/"
  rm -rf "${tmp}"
}

if [[ "${EPD_ENABLE}" == "1" ]]; then
  echo "Fetching the Waveshare epd2in13b_V4 driver (ref ${WAVESHARE_REF})."
  if ! fetch_waveshare; then
    if [[ -f "${INSTALL_DIR}/vendor/waveshare_epd/epd2in13b_V4.py" ]]; then
      echo "Download failed; keeping the driver that is already here."
    else
      echo "Could not download the Waveshare driver and none is installed. The e-paper needs it."
      exit 1
    fi
  fi
fi

enable_spi() {
  # The HAT talks SPI, and Raspberry Pi OS ships with SPI off. Of course it does.
  local config=""
  local candidate
  for candidate in /boot/firmware/config.txt /boot/config.txt; do
    if [[ -f "${candidate}" ]] && ! grep -q "DO NOT EDIT" "${candidate}"; then
      config="${candidate}"
      break
    fi
  done
  if [[ -z "${config}" ]]; then
    echo "No config.txt found. Add dtparam=spi=on to it yourself."
    return
  fi
  if grep -qE '^[[:space:]]*dtparam=spi=on' "${config}"; then
    return
  fi
  if grep -qE '^[[:space:]]*#[[:space:]]*dtparam=spi=on' "${config}"; then
    sed -i -E 's/^[[:space:]]*#[[:space:]]*dtparam=spi=on/dtparam=spi=on/' "${config}"
  else
    printf '\n[all]\ndtparam=spi=on\n' >> "${config}"
  fi
  echo "SPI enabled in ${config}."
}

if [[ "${EPD_ENABLE}" == "1" ]]; then
  enable_spi
fi

install_config() {
  # Keep a Zero config the user already tuned; back up anything else before replacing it.
  local src="$1"
  local dest="$2"
  local first_line=""
  if [[ -f "${dest}" ]]; then
    IFS= read -r first_line < "${dest}" || true
  fi
  if [[ "${first_line}" == *"PirateBox Zero W"* ]]; then
    echo "Keeping existing ${dest}."
    return
  fi
  if [[ -f "${dest}" ]]; then
    cp "${dest}" "${dest}.bak.$(date +%Y%m%d%H%M%S)"
    echo "Backed up the old ${dest}. It meant well."
  fi
  install -m 644 "${src}" "${dest}"
}

mkdir -p /etc/default
install_config "${INSTALL_DIR}/scripts/zero/piratebox.env" /etc/default/piratebox
install_config "${INSTALL_DIR}/scripts/zero/piratebox-epaper.env" /etc/default/piratebox-epaper

mkdir -p /var/lib/piratebox/data/files /var/lib/piratebox/tmp

BOOT_DIR=/boot/firmware
if [[ ! -d "${BOOT_DIR}" ]]; then
  BOOT_DIR=/boot
fi
write_boot_config() {
  # Plain bash instead of sed, so an SSID with "&" or "/" stays an SSID.
  local line
  while IFS= read -r line || [[ -n "${line}" ]]; do
    case "${line}" in
      SSID=*) line="SSID=${SSID}" ;;
      COUNTRY=*) line="COUNTRY=${COUNTRY_CODE^^}" ;;
      EPD_ROTATE=*) line="EPD_ROTATE=${EPD_ROTATE}" ;;
      EPD_ENABLE=*) line="EPD_ENABLE=${EPD_ENABLE}" ;;
    esac
    printf '%s\n' "${line}"
  done < "${INSTALL_DIR}/scripts/zero/piratebox.txt" > "${BOOT_DIR}/piratebox.txt"
}

write_boot_config

install -m 644 "${INSTALL_DIR}/scripts/piratebox.service" /etc/systemd/system/piratebox.service
install -m 644 "${INSTALL_DIR}/scripts/piratebox-epaper.service" /etc/systemd/system/piratebox-epaper.service
install -m 755 "${INSTALL_DIR}/scripts/zero/piratebox-zero-firstboot.sh" /usr/local/sbin/piratebox-zero-firstboot
install -m 644 "${INSTALL_DIR}/scripts/zero/piratebox-zero-firstboot.service" \
  /etc/systemd/system/piratebox-zero-firstboot.service

if [[ "${IMAGE_BUILD}" == "0" ]]; then
  systemctl daemon-reload
fi
# Re-running the installer re-arms first boot, so new settings actually land.
rm -f /var/lib/piratebox/.zero_firstboot_done
systemctl enable piratebox-zero-firstboot.service

echo "Checking that the web stack imports. Trust, but run python."
(cd "${INSTALL_DIR}" && "${VENV}/bin/python" -c "import app.main")
if [[ "${EPD_ENABLE}" == "1" ]]; then
  "${VENV}/bin/python" -c "import PIL, spidev, gpiozero"
fi

if [[ "${IMAGE_BUILD}" == "1" ]]; then
  apt-get clean
fi

echo
echo "PirateBox Zero W installed."
echo "On the next boot, wlan0 stops being a Wi-Fi client and becomes '${SSID}'."
echo "Then: join '${SSID}', open http://10.0.0.1/, and use ssh <user>@10.0.0.1 for admin."
echo "Settings for that first boot live in ${BOOT_DIR}/piratebox.txt."

if [[ "${REBOOT}" == "1" ]]; then
  echo "Rebooting in 10 seconds. If you are connected over this Pi's Wi-Fi, this is goodbye."
  sleep 10
  systemctl reboot
fi
