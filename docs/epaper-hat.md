# Optional e-Paper HAT status display

This module drives the 2.7 inch e-Paper HAT (264x176, Rev 2.1) and shows the PirateBox logo plus local stats (CPU, temp, disk, etc.). It also supports four buttons if you wire them and configure GPIO pins.

## Hardware notes

- Enable SPI on the Pi (`raspi-config` → Interface Options → SPI).
- The HAT connects via SPI and is powered from the Pi header.
- The display resolution is 264x176.

## Install

1. Install the e-paper driver library (choose one):
   - `rpi_epd2in7` (third-party library), or
   - `waveshare_epd` from Waveshare examples.

2. (Optional) Use the install script to fetch drivers:

```bash
sudo ./scripts/install-epd-driver.sh --driver auto
```

3. Install the optional Python deps (Pillow + gpiozero). A venv is strongly recommended:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-epaper.txt
```

If GPIO libraries fail to import on Python 3.13, use the system Python (3.11/3.12) or a venv based on it.
On Raspberry Pi 5, `RPi.GPIO` often fails; install `python3-rpi-lgpio` (or `pip install rpi-lgpio`) so the Waveshare driver can import `RPi.GPIO` via the lgpio backend.

### Troubleshooting: “Cannot determine SOC peripheral base address”

This usually means the GPIO library cannot read the Pi’s device tree or `RPi.GPIO` doesn’t support the Pi model:

- Ensure you’re running on the **host Pi OS**, not inside a container.
- Use system Python (3.11/3.12) with `python3-rpi-lgpio` and `python3-spidev`.
- Confirm `/proc/device-tree` exists and SPI is enabled (`sudo raspi-config`).

## Run

```bash
sudo ./scripts/epaper_hat.py --interval 30 --rotate 0
```

Logo path can be overridden with `--logo` or `PIRATEBOX_EPD_LOGO`.
Driver preference can be forced with `--driver waveshare_epd` or `PIRATEBOX_EPD_DRIVER=waveshare_epd`.
If the Waveshare library is installed, the script tries `epd2in7` first, then `epd2in7_V2`.
Override with `PIRATEBOX_EPD_WAVESHARE_MODULE=epd2in7_V2` (or `epd2in7`) if your panel needs it.
You can also set `PIRATEBOX_EPD_FONT=/path/to/font.ttf` to use a specific TTF font.
For smoother updates, the script uses partial refresh when the driver supports it and triggers
a full refresh every `PIRATEBOX_EPD_FULL_REFRESH_EVERY` loops (set to `0` to disable).
If partial refresh is not available, you can reduce flashing by enabling refresh-on-change.

Refresh-on-change defaults:

```bash
PIRATEBOX_EPD_REFRESH_ON_CHANGE=1
PIRATEBOX_EPD_REFRESH_CPU_DELTA=5
PIRATEBOX_EPD_REFRESH_TEMP_DELTA=1
PIRATEBOX_EPD_REFRESH_MEM_DELTA=2
PIRATEBOX_EPD_REFRESH_DISK_DELTA=1
PIRATEBOX_EPD_REFRESH_COUNT_DELTA=1
PIRATEBOX_EPD_REFRESH_IP=1
```

When enabled, the display only refreshes if those deltas are exceeded or a button forces it.

## Buttons (optional)

Buttons default to the Waveshare HAT pins (BCM 5,6,13,19). Override or disable as needed.

Set button GPIO pins (BCM numbering):

```bash
export PIRATEBOX_EPD_BUTTON_PINS=5,6,13,19
```

Disable buttons entirely:

```bash
export PIRATEBOX_EPD_BUTTON_PINS=none
```

Default actions:

- Key 1: status page (also refreshes it)
- Key 2: network page
- Key 3: box contents page
- Key 4: sleep/wake display

## Systemd service (optional)

1. Copy the unit file and adjust paths/user:

```bash
sudo cp ./scripts/piratebox-epaper.service /etc/systemd/system/piratebox-epaper.service
sudo nano /etc/systemd/system/piratebox-epaper.service
```

The unit expects a venv at `/opt/piratebox/.venv` by default. Adjust `ExecStart=` if you live elsewhere.

2. (Optional) Create an environment file for overrides:

```bash
sudo tee /etc/default/piratebox-epaper >/dev/null <<'EOF'
PIRATEBOX_EPD_INTERVAL=30
PIRATEBOX_EPD_ROTATE=0
PIRATEBOX_EPD_BUTTON_PINS=5,6,13,19
EOF
```

3. Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now piratebox-epaper.service
```

4. Check status/logs:

```bash
systemctl status piratebox-epaper.service
journalctl -u piratebox-epaper.service -f
```

## Data sources

- CPU temp: `/sys/class/thermal/thermal_zone0/temp`
- CPU usage: `/proc/stat`
- Memory: `/proc/meminfo`
- Disk: filesystem stats on `PIRATEBOX_DATA_DIR`
- PirateBox counts: SQLite at `PIRATEBOX_DB_PATH`
