#!/usr/bin/env bash
# PirateBox Raspberry Pi image builder.
# It takes an official image and teaches it new (bad) habits.
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
WORK_DIR="${PIRATEBOX_IMAGE_WORKDIR:-/tmp/piratebox-image}"
OUT_DIR="${PIRATEBOX_IMAGE_OUTDIR:-${ROOT_DIR}/dist}"
IMAGE_URL="${PIRATEBOX_RPI_IMAGE_URL:-https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2025-12-04/2025-12-04-raspios-trixie-arm64-lite.img.xz}"
IMAGE_XZ="${WORK_DIR}/raspios.img.xz"
IMAGE_RAW="${WORK_DIR}/raspios.img"
MOUNT_ROOT="${WORK_DIR}/mnt/root"
MOUNT_BOOT="${WORK_DIR}/mnt/boot"

SUDO=""
if [[ ${EUID} -ne 0 ]]; then
  SUDO="sudo"
fi

LOOP_DEV=""

cleanup() {
  set +e
  if [[ -n "${LOOP_DEV}" ]]; then
    ${SUDO} umount "${MOUNT_BOOT}" 2>/dev/null || true
    ${SUDO} umount "${MOUNT_ROOT}" 2>/dev/null || true
    ${SUDO} losetup -d "${LOOP_DEV}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

rm -rf "${WORK_DIR}"
mkdir -p "${WORK_DIR}" "${OUT_DIR}" "${MOUNT_ROOT}" "${MOUNT_BOOT}"

echo "Downloading Raspberry Pi OS image from ${IMAGE_URL}."
curl -L "${IMAGE_URL}" -o "${IMAGE_XZ}"

echo "Extracting image."
xz -d -k "${IMAGE_XZ}"

if [[ ! -f "${IMAGE_RAW}" ]]; then
  echo "Expected ${IMAGE_RAW}, but it is missing."
  exit 1
fi

echo "Mapping partitions."
LOOP_DEV=$(${SUDO} losetup --show -fP "${IMAGE_RAW}")
BOOT_PART="${LOOP_DEV}p1"
ROOT_PART="${LOOP_DEV}p2"

if [[ ! -b "${BOOT_PART}" || ! -b "${ROOT_PART}" ]]; then
  echo "Partition devices not found for ${LOOP_DEV}."
  exit 1
fi

${SUDO} mount "${ROOT_PART}" "${MOUNT_ROOT}"
${SUDO} mount "${BOOT_PART}" "${MOUNT_BOOT}"

${SUDO} mkdir -p "${MOUNT_ROOT}/opt/piratebox"
${SUDO} rsync -a --delete \
  --exclude ".git" \
  --exclude ".idea" \
  --exclude ".venv" \
  --exclude "__pycache__" \
  --exclude ".pytest_cache" \
  --exclude "data" \
  "${ROOT_DIR}/" "${MOUNT_ROOT}/opt/piratebox/"

${SUDO} install -m 755 "${ROOT_DIR}/scripts/piratebox-firstboot.sh" "${MOUNT_ROOT}/usr/local/sbin/piratebox-firstboot"
${SUDO} install -m 644 "${ROOT_DIR}/scripts/piratebox-firstboot.service" "${MOUNT_ROOT}/etc/systemd/system/piratebox-firstboot.service"
${SUDO} install -m 644 "${ROOT_DIR}/scripts/piratebox.service" "${MOUNT_ROOT}/etc/systemd/system/piratebox.service"
${SUDO} install -m 644 "${ROOT_DIR}/scripts/piratebox-epaper.service" "${MOUNT_ROOT}/etc/systemd/system/piratebox-epaper.service"

${SUDO} mkdir -p "${MOUNT_ROOT}/etc/systemd/system/multi-user.target.wants"
${SUDO} ln -sf /etc/systemd/system/piratebox-firstboot.service \
  "${MOUNT_ROOT}/etc/systemd/system/multi-user.target.wants/piratebox-firstboot.service"

${SUDO} mkdir -p "${MOUNT_ROOT}/etc/default"
${SUDO} tee "${MOUNT_ROOT}/etc/default/piratebox" >/dev/null <<'ENV'
PIRATEBOX_NAME=PirateBox
PIRATEBOX_DATA_DIR=/var/lib/piratebox/data
PIRATEBOX_DB_PATH=/var/lib/piratebox/data/piratebox.db
PIRATEBOX_FILES_DIR=/var/lib/piratebox/data/files
PIRATEBOX_MAX_UPLOAD_MB=512
ENV

${SUDO} tee "${MOUNT_ROOT}/etc/default/piratebox-epaper" >/dev/null <<'ENV'
PIRATEBOX_EPD_INTERVAL=30
PIRATEBOX_EPD_ROTATE=0
PIRATEBOX_EPD_BUTTON_PINS=5,6,13,19
ENV

${SUDO} mkdir -p "${MOUNT_ROOT}/var/lib/piratebox/data/files"
${SUDO} sync

${SUDO} umount "${MOUNT_BOOT}"
${SUDO} umount "${MOUNT_ROOT}"
${SUDO} losetup -d "${LOOP_DEV}"
LOOP_DEV=""

FINAL_IMG="${OUT_DIR}/piratebox-raspios-lite.img"
FINAL_XZ="${OUT_DIR}/piratebox-raspios-lite.img.xz"

mv "${IMAGE_RAW}" "${FINAL_IMG}"

if [[ -f "${FINAL_XZ}" ]]; then
  rm -f "${FINAL_XZ}"
fi

xz -T0 -9 -c "${FINAL_IMG}" > "${FINAL_XZ}"

if [[ "${PIRATEBOX_IMAGE_KEEP_WORKDIR:-0}" != "1" ]]; then
  rm -rf "${WORK_DIR}"
fi

echo "Image ready: ${FINAL_XZ}"
