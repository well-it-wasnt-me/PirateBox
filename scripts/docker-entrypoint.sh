#!/bin/sh
# PirateBox container entrypoint.
# Docker creates missing bind mounts as root, then acts surprised when appuser cannot write.
set -e

DB_DIR=$(dirname "${PIRATEBOX_DB_PATH:-/data/piratebox.db}")
FILES_DIR="${PIRATEBOX_FILES_DIR:-/data/files}"

if [ "$(id -u)" = "0" ]; then
  mkdir -p "${DB_DIR}" "${FILES_DIR}"
  for dir in "${DB_DIR}" "${FILES_DIR}"; do
    # Only recurse when ownership is wrong; chowning a full SD card on every boot is a hobby, not a fix.
    if [ "$(stat -c %U "${dir}")" != "appuser" ]; then
      chown -R appuser:appuser "${dir}"
    fi
  done
  exec setpriv --reuid=appuser --regid=appuser --init-groups "$@"
fi

exec "$@"
