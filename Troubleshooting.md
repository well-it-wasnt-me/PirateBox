---
title: Troubleshooting
---

# Troubleshooting

Things fail. PirateBox is honest about it. Here are the common ones.

## The Wi-Fi network does not show up

- Check that `hostapd` is running: `systemctl status hostapd`.
- Confirm the Wi-Fi interface name with `ip -br link`.
- If you use a USB adapter, ensure it supports AP mode with `iw list`.

## Captive portal does not appear

- Manually browse to `http://10.0.0.1:8080`.
- Some devices cache captive portal results. Toggle Wi-Fi or reboot the device.
- If you want captive portal detection to work better, run the app on port 80.

## DNS redirects do not work

- Verify `dnsmasq` is running: `systemctl status dnsmasq`.
- Check `/etc/dnsmasq.d/piratebox.conf` for typos.
- Make sure `address=/#/10.0.0.1` is present.

## PirateBox service does not start

- `systemctl status piratebox.service` for logs.
- If you use the systemd unit, ensure `/opt/piratebox/.venv` exists.
- Reinstall dependencies with `/opt/piratebox/.venv/bin/pip install -r requirements.txt`.

## First-boot image never starts PirateBox

- First boot needs temporary internet access to install Python packages.
- Check `/var/log/piratebox-firstboot.log` for errors.
- Re-run first boot:

```bash
sudo rm -f /var/lib/piratebox/.firstboot_done
sudo systemctl start piratebox-firstboot.service
```

## File uploads fail

- Check `PIRATEBOX_MAX_UPLOAD_MB`.
- Confirm free disk space with `df -h`.
- Verify `PIRATEBOX_FILES_DIR` exists and is writable.

## Chat messages do not appear

- Make sure you are not blocked by the browser cache.
- Check the API endpoint directly: `http://10.0.0.1:8080/api/chat/messages`.
- Look for errors in the app logs.

## The e-Paper display is blank

- Confirm SPI is enabled in `raspi-config`.
- Run the script manually: `sudo ./scripts/epaper_hat.py --debug`.
- Install GPIO libraries: `sudo apt install python3-rpi-lgpio python3-spidev`.

## You want it online

This is an offline-first project. If you must add internet, do so knowingly and accept that it defeats the point.
