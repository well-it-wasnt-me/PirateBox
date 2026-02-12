#!/usr/bin/env bash
# PirateBox first-boot provisioning.
# It does the work once so you do not have to explain it twice.
set -euo pipefail

MARKER="/var/lib/piratebox/.firstboot_done"
LOG_FILE="/var/log/piratebox-firstboot.log"

exec > >(tee -a "${LOG_FILE}") 2>&1

if [[ -f "${MARKER}" ]]; then
  echo "First boot already completed. Nothing to see here."
  exit 0
fi

mkdir -p /var/lib/piratebox
DATA_DIR="${PIRATEBOX_DATA_DIR:-/var/lib/piratebox/data}"
mkdir -p "${DATA_DIR}" "${DATA_DIR}/files"

export DEBIAN_FRONTEND=noninteractive

echo "Updating packages and installing Python tooling."
apt-get update
apt-get install -y python3-venv python3-pip

if [[ ! -d /opt/piratebox/.venv ]]; then
  python3 -m venv /opt/piratebox/.venv
fi

/opt/piratebox/.venv/bin/pip install --upgrade pip
/opt/piratebox/.venv/bin/pip install -r /opt/piratebox/requirements.txt

if [[ "${PIRATEBOX_EPD_ENABLE:-0}" == "1" ]]; then
  /opt/piratebox/.venv/bin/pip install -r /opt/piratebox/requirements-epaper.txt || true
fi

cp /opt/piratebox/scripts/piratebox.service /etc/systemd/system/piratebox.service
systemctl daemon-reload
systemctl enable piratebox.service
systemctl start piratebox.service

if [[ "${PIRATEBOX_EPD_ENABLE:-0}" == "1" ]]; then
  cp /opt/piratebox/scripts/piratebox-epaper.service /etc/systemd/system/piratebox-epaper.service
  systemctl daemon-reload
  systemctl enable piratebox-epaper.service
  systemctl start piratebox-epaper.service
fi

mkdir -p "$(dirname "${MARKER}")"
touch "${MARKER}"

systemctl disable piratebox-firstboot.service || true

echo "First boot provisioning complete."
