# Pi Zero W Edition

The original PirateBox ran on a pocket router. This edition runs on a Raspberry Pi Zero W with a
Waveshare 2.13 inch e-Paper HAT (B) V4: one CPU core, 512 MB of RAM, one Wi-Fi radio, a screen that
can print in red, and not a single button. It is the smallest PirateBox that still has opinions.

## What you need

- Raspberry Pi Zero W. Yes, the old one. A Zero 2 W runs the same 32-bit image; it is just less impressed.
- microSD card, 8 GB minimum. 16 GB if people are going to share actual files.
- Waveshare 2.13 inch e-Paper HAT (B) **V4**: 250x122, black/white/red, SPI, no keys.
  The black/white-only "2.13 inch V4" is a different panel with a different driver.
- A micro-USB power supply that delivers what it promises.

The Zero W is ARMv6, so it only boots the **32-bit** Raspberry Pi OS. The 64-bit image will not even
say hello.

## Option A: prebuilt image (no internet needed, ever)

The image already contains every package, so the first boot works fully offline. That matters: the
Zero W has one radio, and it cannot be online and an access point at the same time.

1. Grab `piratebox-zero-w-raspios-lite-armhf.img.xz` from the GitHub release, or build it (below).
2. Flash it with Raspberry Pi Imager: **Choose OS -> Use custom**.
3. In **OS customisation**, set a username and password and enable SSH.
   Skip the Wi-Fi settings; the radio is about to become the access point anyway.
4. Before ejecting the card, open the boot partition (it is FAT, any computer can read it) and edit
   `piratebox.txt`:

```ini
SSID=PirateBox
COUNTRY=IT
EPD_ROTATE=0
EPD_ENABLE=1
```

5. Put the card in the Zero, attach the HAT, power it up, and wait. The first boot resizes the card,
   reboots, then brings up the access point. On a Zero that takes a few minutes. Make tea.
6. Join the `PirateBox` Wi-Fi and open `http://10.0.0.1/`. The e-paper shows the same address in red,
   for people who do not read documentation.

## Option B: installer on an existing Raspberry Pi OS

Use this if you already have a Zero running Raspberry Pi OS Lite (32-bit, trixie).
The installer needs internet once, to download packages.

1. Flash Raspberry Pi OS Lite (32-bit). In Imager's OS customisation, configure Wi-Fi **and** SSH;
   the installer downloads over that Wi-Fi.
2. SSH in and run:

```bash
sudo apt install -y git
git clone https://github.com/well-it-wasnt-me/PirateBox.git
cd PirateBox
sudo ./scripts/zero/install-zero.sh --ssid PirateBox --country IT
```

3. The installer does everything while you are still online, then reboots. On the way back up,
   `wlan0` stops being a Wi-Fi client and becomes the access point, so your SSH session ends.
   That is not a crash, that is the plan.
4. Join the `PirateBox` Wi-Fi and SSH back in with `ssh <user>@10.0.0.1`.

Installer options:

| Option | Meaning |
| --- | --- |
| `--ssid <name>` | SSID to broadcast (default `PirateBox`) |
| `--country <CC>` | Wi-Fi country code, e.g. `IT` or `US`. Set it; unset radios sulk. |
| `--rotate <deg>` | E-paper rotation: `0`, `90`, `180`, `270` |
| `--no-epaper` | Skip the display entirely |
| `--no-reboot` | Do not reboot; the access point starts on the next boot instead |

Everything comes from apt. Raspberry Pi OS ships FastAPI, Pillow and friends prebuilt for ARMv6, while
pip would happily spend an afternoon compiling Rust on a single core. Nothing is pip-installed.

## Build the image yourself

You need a Linux machine (x86_64 is fine), `sudo`, about 6 GB of free space, and qemu user emulation
(`qemu-user-static` + `binfmt-support` on Debian/Ubuntu, `qemu-user-static` + `qemu-user-static-binfmt`
on Arch).

```bash
./scripts/zero/build-zero-image.sh
```

The script downloads the official 32-bit Raspberry Pi OS Lite image, grows it a little, copies the repo
to `/opt/piratebox`, runs `scripts/zero/install-zero.sh` inside the image under qemu, and compresses the
result to `dist/piratebox-zero-w-raspios-lite-armhf.img.xz`. qemu pretending to be a Zero is slow;
expect a coffee, not an espresso.

Build knobs:

- `PIRATEBOX_ZERO_IMAGE_URL` to use a different base image.
- `PIRATEBOX_ZERO_IMAGE_GROW_MB` to add more than 1024 MB of headroom.
- `PIRATEBOX_SSID`, `PIRATEBOX_COUNTRY_CODE`, `PIRATEBOX_EPD_ROTATE` to bake defaults into `piratebox.txt`.
- `PIRATEBOX_WAVESHARE_REF` to fetch the Waveshare driver from a different commit.
- `PIRATEBOX_IMAGE_OUTDIR`, `PIRATEBOX_IMAGE_WORKDIR`, `PIRATEBOX_IMAGE_KEEP_WORKDIR=1` work as in
  [Prebuilt Image](Prebuilt-Image.md).

CI builds this image next to the regular one on every release.

## The e-paper dashboard

No buttons means no pages to flip, so the Zero gets a single landscape dashboard:

- a red header with the box name and uptime,
- the URL in bold red, because it is the only line anyone reads,
- the SSID and how many devices are connected,
- file, chat and forum post counts,
- a disk bar (red past 90%) and the CPU temperature (red past 70 C).

Tri-color panels are slow and fussy. A refresh takes about 15 seconds of dramatic flashing, Waveshare
says no more than one every 180 seconds, and the panel should sleep between refreshes. So the display:

- samples stats every minute, but refreshes at most every 3 minutes,
- refreshes only when counts, clients, the IP or disk usage change, or the temperature moves 5 C,
- refreshes at least once an hour anyway, so the uptime is not fiction,
- puts the panel into deep sleep after every frame.

If you list several pages in `PIRATEBOX_EPD_PAGES`, they rotate on each refresh, since nobody can press
a button that does not exist.

Preview it without any hardware:

```bash
python3 scripts/epaper_hat.py --preview /tmp/epd --preview-size 250x122 --pages dashboard
```

Settings live in `/etc/default/piratebox-epaper`. See [E-Paper HAT](Epaper.md) for every knob.

## Where things live

- App: `/opt/piratebox` (venv at `/opt/piratebox/.venv`, using the system packages)
- Waveshare driver: `/opt/piratebox/vendor/waveshare_epd`
- Data: `/var/lib/piratebox/data`
- Upload spool: `/var/lib/piratebox/tmp` (because `/tmp` is RAM, and RAM is the one thing we lack)
- Settings: `/etc/default/piratebox`, `/etc/default/piratebox-epaper`
- First-boot settings: `/boot/firmware/piratebox.txt`
- First-boot log: `/var/log/piratebox-zero-firstboot.log`

## Troubleshooting

### No PirateBox Wi-Fi

- Check the first-boot log: `cat /var/log/piratebox-zero-firstboot.log`.
- Check hostapd: `journalctl -u hostapd -b`.
- `rfkill list` should show the Wi-Fi as unblocked. Setting `COUNTRY` usually fixes that.
- Re-run first boot after editing `piratebox.txt`:

```bash
sudo rm -f /var/lib/piratebox/.zero_firstboot_done
sudo systemctl start piratebox-zero-firstboot.service
```

### The e-paper stays blank

- SPI must be on: `ls /dev/spidev0.0`. If it is missing, check `dtparam=spi=on` in `/boot/firmware/config.txt` and reboot.
- Run it by hand and read what it says:

```bash
sudo systemctl stop piratebox-epaper
sudo /opt/piratebox/.venv/bin/python /opt/piratebox/scripts/epaper_hat.py --debug
```

- Make sure the panel really is the (B) V4. Older (B) revisions use different drivers and will sit there
  looking blank and innocent.

### The dashboard is upside down

Set `PIRATEBOX_EPD_ROTATE=180` in `/etc/default/piratebox-epaper`, then
`sudo systemctl restart piratebox-epaper`. Wait for the refresh. Patiently.

### Everything is slow

It is a Zero. One core at 1 GHz serving a web app, a database, an access point and a display is already
a small miracle. Keep uploads modest (the default cap is 256 MB) and expect a classroom, not a stadium.
