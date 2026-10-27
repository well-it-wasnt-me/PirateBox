# Configuration

PirateBox is configured with environment variables. You can set them in your shell, in `/etc/default/piratebox`, or in Docker Compose. Choose your preferred flavor of control.

## App settings

- `PIRATEBOX_NAME` (default: `PirateBox`)
- `PIRATEBOX_DATA_DIR` (default: `./data`)
- `PIRATEBOX_DB_PATH` (default: `${PIRATEBOX_DATA_DIR}/piratebox.db`)
- `PIRATEBOX_FILES_DIR` (default: `${PIRATEBOX_DATA_DIR}/files`)
- `PIRATEBOX_MAX_UPLOAD_MB` (default: `512`)
- `PIRATEBOX_MAX_NICKNAME_LEN` (default: `32`)
- `PIRATEBOX_MAX_MESSAGE_LEN` (default: `500`)
- `PIRATEBOX_MAX_THREAD_TITLE_LEN` (default: `120`)
- `PORT` (default: `80` when running `python app/main.py`)

## Docker Compose example

```yaml
services:
  piratebox:
    environment:
      PIRATEBOX_NAME: "PirateBox"
      PIRATEBOX_DB_PATH: "/data/piratebox.db"
      PIRATEBOX_FILES_DIR: "/data/files"
```

## systemd defaults

If you use systemd, set defaults in `/etc/default/piratebox`:

```bash
PIRATEBOX_NAME=PirateBox
PIRATEBOX_DATA_DIR=/var/lib/piratebox/data
PIRATEBOX_DB_PATH=/var/lib/piratebox/data/piratebox.db
PIRATEBOX_FILES_DIR=/var/lib/piratebox/data/files
PIRATEBOX_MAX_UPLOAD_MB=512
```

## E-Paper settings

- `PIRATEBOX_EPD_DRIVER` (default: `auto`)
- `PIRATEBOX_EPD_WAVESHARE_MODULE` (default: auto-detect)
- `PIRATEBOX_EPD_INTERVAL` (default: `30` seconds)
- `PIRATEBOX_EPD_ROTATE` (default: `0`)
- `PIRATEBOX_EPD_BUTTON_PINS` (default: `5,6,13,19`)
- `PIRATEBOX_EPD_FONT` (default: DejaVu Sans if available)
- `PIRATEBOX_EPD_REFRESH_ON_CHANGE` (default: `1`)
- `PIRATEBOX_EPD_FULL_REFRESH_EVERY` (default: `10`)
- `PIRATEBOX_EPD_PAGES` (default: `status,network,piratebox`; also `dashboard`)
- `PIRATEBOX_EPD_ORIENTATION` (default: `auto`; also `portrait`, `landscape`)
- `PIRATEBOX_EPD_MIN_REFRESH` (default: `0`, or `180` on tri-color panels)
- `PIRATEBOX_EPD_MAX_AGE` (default: `3600` seconds; `0` disables)
- `PIRATEBOX_EPD_ENABLE` (default: `0`; first-boot image only)
- `PIRATEBOX_WIFI_IFACE` (default: `wlan0`; the AP interface whose address, state and clients are shown)
- `PIRATEBOX_EPD_AP_GRACE` (default: `300` seconds; how long after boot a down AP counts as "starting"
  when the health check has not said anything yet)
- `PIRATEBOX_EPD_WEB_PORT` (default: `PORT`, else `80`; where the startup checklist knocks for the web
  app; `0` skips the check)
- `PIRATEBOX_EPD_READY_REFRESH` (default: `1`; draw the first "ready" frame immediately, once per start,
  instead of waiting out `PIRATEBOX_EPD_MIN_REFRESH`)

## Wireless settings

If you use `scripts/pi-setup.sh`, the following environment variables control the AP setup:

- `PIRATEBOX_WIFI_IFACE` (default: `wlan0`)
- `PIRATEBOX_SSID` (default: `PirateBox`)
- `PIRATEBOX_GATEWAY_IP` (default: `10.0.0.1`)
- `PIRATEBOX_CIDR` (default: `24`)
- `PIRATEBOX_DHCP_START` (default: `10.0.0.10`)
- `PIRATEBOX_DHCP_END` (default: `10.0.0.200`)
- `PIRATEBOX_CHANNEL` (default: `6`)
- `PIRATEBOX_HW_MODE` (default: `g`)
- `PIRATEBOX_COUNTRY_CODE` (default: empty; also `--country`)

`pi-setup.sh` only installs Docker when you pass `--run-compose`.

It also installs `piratebox-ap-health.service`, which checks and, within limits, repairs the AP on
every boot. Options for running it by hand: `--iface`, `--address`, `--attempts` (default `4`, `0`
only checks), `--interval` (default `15` s), `--settle` (default `10` s).

## Pi Zero W settings

The [Pi Zero W Edition](Pi-Zero-W.md) reads `piratebox.txt` from the boot partition on first boot
(`SSID`, `COUNTRY`, `EPD_ROTATE`, `EPD_ENABLE`) and ships its own defaults in `scripts/zero/`.
It also sets `TMPDIR=/var/lib/piratebox/tmp` so uploads do not spool into a RAM-backed `/tmp`.

## The cynical truth

Every config knob adds complexity. Change only what you need, or you will spend your weekend arguing with yourself.
