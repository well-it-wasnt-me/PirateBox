#!/usr/bin/env bash
# PirateBox Raspberry Pi Zero W image builder.
# Takes the official 32-bit image, installs everything under qemu, and hands back a box that
# boots with no internet at all. The Zero W has one radio; it cannot be online and an AP at once.
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
WORK_DIR="${PIRATEBOX_IMAGE_WORKDIR:-/tmp/piratebox-zero-image}"
OUT_DIR="${PIRATEBOX_IMAGE_OUTDIR:-${ROOT_DIR}/dist}"
IMAGE_URL="${PIRATEBOX_ZERO_IMAGE_URL:-https://downloads.raspberrypi.com/raspios_lite_armhf/images/raspios_lite_armhf-2026-10-06/2026-10-06-raspios-trixie-armhf-lite.img.xz}"
GROW_MB="${PIRATEBOX_ZERO_IMAGE_GROW_MB:-1024}"
SSID="${PIRATEBOX_SSID:-PirateBox}"
COUNTRY_CODE="${PIRATEBOX_COUNTRY_CODE:-}"
EPD_ROTATE="${PIRATEBOX_EPD_ROTATE:-0}"
IMAGE_XZ="${WORK_DIR}/raspios.img.xz"
IMAGE_RAW="${WORK_DIR}/raspios.img"
MOUNT_ROOT="${WORK_DIR}/mnt"
BOOT_MOUNT=""

SUDO=""
if [[ ${EUID} -ne 0 ]]; then
  SUDO="sudo"
fi

LOOP_DEV=""
CHROOT_MOUNTS=()
QEMU_IN_CHROOT=""

restore_chroot() {
  # Put the image back the way Raspberry Pi shipped it, minus our improvements.
  set +e
  ${SUDO} rm -f "${MOUNT_ROOT}/usr/sbin/policy-rc.d"
  if [[ -e "${MOUNT_ROOT}/etc/resolv.conf.piratebox-orig" || -L "${MOUNT_ROOT}/etc/resolv.conf.piratebox-orig" ]]; then
    ${SUDO} rm -f "${MOUNT_ROOT}/etc/resolv.conf"
    ${SUDO} mv "${MOUNT_ROOT}/etc/resolv.conf.piratebox-orig" "${MOUNT_ROOT}/etc/resolv.conf"
  fi
  if [[ -f "${MOUNT_ROOT}/etc/ld.so.preload.piratebox-orig" ]]; then
    ${SUDO} mv "${MOUNT_ROOT}/etc/ld.so.preload.piratebox-orig" "${MOUNT_ROOT}/etc/ld.so.preload"
  fi
  if [[ -n "${QEMU_IN_CHROOT}" ]]; then
    ${SUDO} rm -f "${MOUNT_ROOT}${QEMU_IN_CHROOT}"
    QEMU_IN_CHROOT=""
  fi
  local idx
  for ((idx = ${#CHROOT_MOUNTS[@]} - 1; idx >= 0; idx--)); do
    ${SUDO} umount -l "${CHROOT_MOUNTS[idx]}" 2>/dev/null
  done
  CHROOT_MOUNTS=()
  set -e
}

cleanup() {
  set +e
  restore_chroot
  if [[ -n "${LOOP_DEV}" ]]; then
    [[ -n "${BOOT_MOUNT}" ]] && ${SUDO} umount "${BOOT_MOUNT}" 2>/dev/null
    ${SUDO} umount "${MOUNT_ROOT}" 2>/dev/null
    ${SUDO} losetup -d "${LOOP_DEV}" 2>/dev/null
  fi
}
trap cleanup EXIT

for tool in curl xz losetup sfdisk e2fsck resize2fs rsync chroot truncate; do
  if ! command -v "${tool}" >/dev/null 2>&1 && ! ${SUDO} sh -c "command -v ${tool}" >/dev/null 2>&1; then
    echo "Missing ${tool}. Install it; this script will not improvise."
    exit 1
  fi
done

HOST_ARCH=$(uname -m)
QEMU_INTERP=""
if [[ "${HOST_ARCH}" != arm* && "${HOST_ARCH}" != aarch64 ]]; then
  if [[ ! -r /proc/sys/fs/binfmt_misc/qemu-arm ]]; then
    echo "No binfmt handler for ARM. Install qemu-user-static and binfmt-support (or your distro's equivalent)."
    exit 1
  fi
  QEMU_INTERP=$(awk '/^interpreter/ {print $2}' /proc/sys/fs/binfmt_misc/qemu-arm)
  if [[ -z "${QEMU_INTERP}" || ! -x "${QEMU_INTERP}" ]]; then
    echo "binfmt points at '${QEMU_INTERP}', which is not an executable. qemu is having a day."
    exit 1
  fi
fi

if findmnt -rn -o TARGET 2>/dev/null | grep -q "^${WORK_DIR}"; then
  echo "Something is still mounted under ${WORK_DIR}. Unmount it first; rm -rf on a mounted image ruins weekends."
  exit 1
fi
rm -rf "${WORK_DIR}"
mkdir -p "${WORK_DIR}" "${OUT_DIR}" "${MOUNT_ROOT}"

echo "Downloading Raspberry Pi OS Lite (32-bit) from ${IMAGE_URL}."
curl -fL --retry 3 "${IMAGE_URL}" -o "${IMAGE_XZ}"

echo "Extracting image."
xz -dc "${IMAGE_XZ}" > "${IMAGE_RAW}"
rm -f "${IMAGE_XZ}"

echo "Growing the image by ${GROW_MB} MB so Python fits. The SD card gets the rest on first boot."
truncate -s "+${GROW_MB}M" "${IMAGE_RAW}"
echo ", +" | sfdisk --no-reread --no-tell-kernel -N 2 "${IMAGE_RAW}"

echo "Mapping partitions."
LOOP_DEV=$(${SUDO} losetup --show -fP "${IMAGE_RAW}")
BOOT_PART="${LOOP_DEV}p1"
ROOT_PART="${LOOP_DEV}p2"

# Partition nodes show up when udev feels like it. Give it a few seconds of patience.
for _ in $(seq 1 10); do
  [[ -b "${BOOT_PART}" && -b "${ROOT_PART}" ]] && break
  ${SUDO} udevadm settle 2>/dev/null || sleep 1
done

if [[ ! -b "${BOOT_PART}" || ! -b "${ROOT_PART}" ]]; then
  echo "Partition devices not found for ${LOOP_DEV}."
  exit 1
fi

FSCK_RC=0
${SUDO} e2fsck -fy "${ROOT_PART}" || FSCK_RC=$?
if (( FSCK_RC > 1 )); then
  echo "e2fsck exited ${FSCK_RC}. The filesystem is in worse shape than our jokes."
  exit 1
fi
${SUDO} resize2fs "${ROOT_PART}"

${SUDO} mount "${ROOT_PART}" "${MOUNT_ROOT}"
if [[ -d "${MOUNT_ROOT}/boot/firmware" ]]; then
  BOOT_MOUNT="${MOUNT_ROOT}/boot/firmware"
else
  BOOT_MOUNT="${MOUNT_ROOT}/boot"
fi
${SUDO} mount "${BOOT_PART}" "${BOOT_MOUNT}"

echo "Copying PirateBox into the image."
${SUDO} mkdir -p "${MOUNT_ROOT}/opt/piratebox"
${SUDO} rsync -a --delete \
  --exclude ".git" \
  --exclude ".idea" \
  --exclude ".venv" \
  --exclude "__pycache__" \
  --exclude ".pytest_cache" \
  --exclude "/data" \
  --exclude "/dist" \
  --exclude "/site" \
  --exclude "/vendor" \
  "${ROOT_DIR}/" "${MOUNT_ROOT}/opt/piratebox/"

echo "Preparing the chroot. qemu is about to pretend to be a Zero, slowly."
${SUDO} mount -t proc proc "${MOUNT_ROOT}/proc"
CHROOT_MOUNTS+=("${MOUNT_ROOT}/proc")
for mount_point in sys dev dev/pts; do
  ${SUDO} mount --bind "/${mount_point}" "${MOUNT_ROOT}/${mount_point}"
  CHROOT_MOUNTS+=("${MOUNT_ROOT}/${mount_point}")
done

if [[ -e "${MOUNT_ROOT}/etc/resolv.conf" || -L "${MOUNT_ROOT}/etc/resolv.conf" ]]; then
  ${SUDO} mv "${MOUNT_ROOT}/etc/resolv.conf" "${MOUNT_ROOT}/etc/resolv.conf.piratebox-orig"
fi
${SUDO} cp -L /etc/resolv.conf "${MOUNT_ROOT}/etc/resolv.conf"

# Services must not start inside a chroot; they would be running on the wrong computer.
printf '#!/bin/sh\nexit 101\n' | ${SUDO} tee "${MOUNT_ROOT}/usr/sbin/policy-rc.d" >/dev/null
${SUDO} chmod 755 "${MOUNT_ROOT}/usr/sbin/policy-rc.d"

# Older images preload an ARM-optimized memcpy that qemu does not appreciate.
if [[ -f "${MOUNT_ROOT}/etc/ld.so.preload" ]]; then
  ${SUDO} mv "${MOUNT_ROOT}/etc/ld.so.preload" "${MOUNT_ROOT}/etc/ld.so.preload.piratebox-orig"
fi

if [[ -n "${QEMU_INTERP}" ]]; then
  ${SUDO} mkdir -p "${MOUNT_ROOT}$(dirname "${QEMU_INTERP}")"
  ${SUDO} install -m 755 "${QEMU_INTERP}" "${MOUNT_ROOT}${QEMU_INTERP}"
  QEMU_IN_CHROOT="${QEMU_INTERP}"
fi

INSTALL_ARGS=(--image-build --ssid "${SSID}" --rotate "${EPD_ROTATE}")
if [[ -n "${COUNTRY_CODE}" ]]; then
  INSTALL_ARGS+=(--country "${COUNTRY_CODE}")
fi

echo "Running the Zero installer inside the image."
${SUDO} chroot "${MOUNT_ROOT}" /usr/bin/env -i \
  PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  HOME=/root \
  LANG=C.UTF-8 \
  PIRATEBOX_WAVESHARE_REF="${PIRATEBOX_WAVESHARE_REF:-}" \
  /bin/bash /opt/piratebox/scripts/zero/install-zero.sh "${INSTALL_ARGS[@]}"

restore_chroot
${SUDO} sync

${SUDO} umount "${BOOT_MOUNT}"
${SUDO} umount "${MOUNT_ROOT}"
${SUDO} losetup -d "${LOOP_DEV}"
LOOP_DEV=""

FINAL_IMG="${OUT_DIR}/piratebox-zero-w-raspios-lite-armhf.img"
FINAL_XZ="${FINAL_IMG}.xz"

mv "${IMAGE_RAW}" "${FINAL_IMG}"
rm -f "${FINAL_XZ}"

echo "Compressing. xz is thorough, which is a polite word for slow."
xz -T0 -9 -c "${FINAL_IMG}" > "${FINAL_XZ}"

if [[ "${PIRATEBOX_IMAGE_KEEP_WORKDIR:-0}" != "1" ]]; then
  rm -rf "${WORK_DIR}"
fi

echo "Image ready: ${FINAL_XZ}"
