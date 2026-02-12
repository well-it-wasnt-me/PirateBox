---
title: E-Paper HAT
---

# E-Paper HAT

PirateBox can drive a 2.7 inch e-Paper HAT to show local status: CPU, memory, disk, uptime, IP, and PirateBox stats. It is quiet, readable, and stubborn. Like the rest of the project.

## Requirements

- SPI enabled on the Pi.
- A supported driver library.
- Python packages from `requirements-epaper.txt`.

## Quick install

```bash
sudo ./scripts/install-epd-driver.sh --driver auto
python3 -m venv /opt/piratebox/.venv
/opt/piratebox/.venv/bin/pip install -r requirements-epaper.txt
```

## Run manually

```bash
sudo /opt/piratebox/.venv/bin/python /opt/piratebox/scripts/epaper_hat.py --interval 30
```

## Systemd service

```bash
sudo cp /opt/piratebox/scripts/piratebox-epaper.service /etc/systemd/system/piratebox-epaper.service
sudo systemctl daemon-reload
sudo systemctl enable --now piratebox-epaper.service
```

## Button pins

Default pins use BCM numbering: `5,6,13,19`. Override with:

```bash
export PIRATEBOX_EPD_BUTTON_PINS=5,6,13,19
```

Disable buttons completely:

```bash
export PIRATEBOX_EPD_BUTTON_PINS=none
```

## Debugging

Run with `--debug` and watch for driver selection logs. If nothing shows up, the driver did not load. That is not a mystery, it is a missing dependency.
