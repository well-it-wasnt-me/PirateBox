#!/usr/bin/env bash
# PirateBox e-Paper driver installer.
# It automates the part you'd rather not explain at a party.
set -euo pipefail

usage() {
  # Print help text for the impatient and the tired.
  cat <<'EOF'
PirateBox e-Paper driver installer

Usage:
  sudo ./scripts/install-epd-driver.sh --driver <rpi_epd2in7|waveshare_epd|auto>

Options:
  --driver <name>     Driver to install (default: auto)
  --venv <path>       Create/use a Python venv and install there
  --waveshare-dir <path>  Clone Waveshare repo here (default: /opt/waveshare-epd)
  --waveshare-repo <url>  Repo URL (default: https://github.com/waveshareteam/e-Paper.git)
  -h, --help          Show help

Environment overrides:
  PIRATEBOX_EPD_DRIVER, PIRATEBOX_EPD_VENV, PIRATEBOX_WAVESHARE_DIR,
  PIRATEBOX_WAVESHARE_REPO
EOF
}

DRIVER=${PIRATEBOX_EPD_DRIVER:-auto}
VENV_PATH=${PIRATEBOX_EPD_VENV:-}
WAVESHARE_DIR=${PIRATEBOX_WAVESHARE_DIR:-/opt/waveshare-epd}
WAVESHARE_REPO=${PIRATEBOX_WAVESHARE_REPO:-https://github.com/waveshareteam/e-Paper.git}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --driver)
      DRIVER="$2"
      shift 2
      ;;
    --venv)
      VENV_PATH="$2"
      shift 2
      ;;
    --waveshare-dir)
      WAVESHARE_DIR="$2"
      shift 2
      ;;
    --waveshare-repo)
      WAVESHARE_REPO="$2"
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
  echo "Run this with sudo. The e-paper will not install itself."
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git python3-pip python3-venv python3-rpi.gpio python3-rpi-lgpio python3-spidev python3-pil

PYTHON_BIN=/usr/bin/python3
PIP_BIN=("${PYTHON_BIN}" -m pip)
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd -- "${SCRIPT_DIR}/.." && pwd)

if [[ -n "${VENV_PATH}" ]]; then
  if [[ ! -d "${VENV_PATH}" ]]; then
    ${PYTHON_BIN} -m venv "${VENV_PATH}"
  fi
  PYTHON_BIN="${VENV_PATH}/bin/python"
  PIP_BIN=("${PYTHON_BIN}" -m pip)
fi

${PIP_BIN[@]} install --upgrade pip
${PIP_BIN[@]} install -r "${ROOT_DIR}/requirements-epaper.txt" || true
${PIP_BIN[@]} install RPi.GPIO spidev rpi-lgpio || true

install_rpi_epd2in7() {
  # Try pip names for the rpi_epd2in7 driver.
  if ${PIP_BIN[@]} install rpi-epd2in7; then
    return 0
  fi
  if ${PIP_BIN[@]} install rpi_epd2in7; then
    return 0
  fi
  echo "rpi_epd2in7 not found on pip. You may need to install manually."
  return 1
}

install_waveshare_epd() {
  # Clone or update Waveshare's repo and wire up the Python driver.
  if [[ ! -d "${WAVESHARE_DIR}" ]]; then
    git clone --depth 1 "${WAVESHARE_REPO}" "${WAVESHARE_DIR}"
  else
    git -C "${WAVESHARE_DIR}" pull --ff-only || true
  fi

  local driver_path="${WAVESHARE_DIR}/e-Paper/RaspberryPi_JetsonNano/python"
  if [[ -f "${driver_path}/setup.py" ]]; then
    ${PIP_BIN[@]} install "${driver_path}"
    return 0
  fi

  local lib_path="${driver_path}/lib"
  if [[ -d "${lib_path}" ]]; then
    local site=$(${PYTHON_BIN} - <<'PY'
import site
print(site.getsitepackages()[0])
PY
)
    mkdir -p "${site}"
    echo "${lib_path}" > "${site}/waveshare_epd.pth"
    return 0
  fi

  echo "Waveshare driver layout not found at ${driver_path}."
  return 1
}

case "${DRIVER}" in
  auto)
    echo "Attempting rpi_epd2in7 first, then waveshare_epd."
    install_rpi_epd2in7 || install_waveshare_epd
    ;;
  rpi_epd2in7)
    install_rpi_epd2in7
    ;;
  waveshare_epd)
    install_waveshare_epd
    ;;
  *)
    echo "Unknown driver: ${DRIVER}"
    usage
    exit 1
    ;;
esac

echo "Driver install complete."
