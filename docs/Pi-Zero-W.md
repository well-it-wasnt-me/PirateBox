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
- refreshes only when counts, clients, the IP, the AP state or disk usage change, or the temperature moves 5 C,
- refreshes at least once an hour anyway, so the uptime is not fiction,
- puts the panel into deep sleep after every frame.

If you list several pages in `PIRATEBOX_EPD_PAGES`, they rotate on each refresh, since nobody can press
a button that does not exist.

Preview it without any hardware:

```bash
python3 scripts/epaper_hat.py --preview /tmp/epd --preview-size 250x122 --pages dashboard
```

Settings live in `/etc/default/piratebox-epaper`. See [E-Paper HAT](Epaper.md) for every knob.

### Until it is ready

The dashboard only appears once a phone can actually use the box: the AP is broadcasting, `10.0.0.1`
is assigned, DHCP is listening and the web app answers. Until then the panel shows a checklist instead:

```
PIRATEBOX                     UP 1m
FIRST BOOT SETUP
[x] RADIO        [ ] WIFI PirateBox
[ ] IP           [ ] DHCP
[x] WEB
A FEW MINUTES ON A ZERO. MAKE TEA.
THE URL SHOWS UP HERE WHEN READY.
```

A black box is done, a hollow one is still coming, a red one has given up. The title says which phase
it is in: `FIRST BOOT SETUP`, `STARTING UP`, or, once nobody is working on it any more, `AP OFFLINE`
(with `WHY RFKILL`, `WLAN0 DOWN`, `NO IP`, `NOT BROADCASTING` or `NO WLAN0`), `DHCP DOWN` or
`WEB APP DOWN`. The bottom line says where to look next; for a dead AP that is
`piratebox-ap-status.txt` on the SD card's boot partition.

The panel samples every 10 seconds while not ready, and the moment everything turns black it draws
the dashboard right away instead of waiting out the usual 3 minutes. That is one early refresh per
start of the display service, not a habit; set `PIRATEBOX_EPD_READY_REFRESH=0` if you would rather
honour Waveshare's 180 seconds to the letter. The display reads all of this from the kernel (sysfs,
`ip`, `iw`, `/proc/net/udp`) and a TCP knock on the web port, so it works even when the web app does not.

The address shown is the one on the AP interface (`PIRATEBOX_WIFI_IFACE`, normally `wlan0`, so
`10.0.0.1`), never one from the hostname. Older versions fell back to `127.0.1.1`, which is `/etc/hosts`
politely lying. A preview on a laptop shows `AP OFFLINE`, because a laptop is not an access point.

The panel cannot help before the display service starts. On the very first power-on that is after the
card has been resized and the Pi has rebooted once; until then you see whatever it showed last.

## How the access point survives a reboot

First boot and every boot after it are different jobs, done by different units.

**First boot, once.** `piratebox-zero-firstboot.service` reads `piratebox.txt`, starts the web app and
the display, then runs `scripts/pi-setup.sh`, which:

1. tells NetworkManager to leave `wlan0` alone (`/etc/NetworkManager/conf.d/99-piratebox.conf`),
2. switches NetworkManager's saved Wi-Fi radio state **on** (`nmcli radio wifi on`), see below,
3. installs `piratebox-ap-ip.service` (pins `10.0.0.1/24` on `wlan0` and unblocks rfkill),
4. writes `/etc/hostapd/hostapd.conf` and `/etc/dnsmasq.d/piratebox.conf`, enables `hostapd` and `dnsmasq`,
5. installs and enables `piratebox-ap-health.service`, then runs the health check once and logs the verdict.

Then it syncs, writes `/var/lib/piratebox/.zero_firstboot_done` and disables itself. The marker means
"the configuration is on disk", not "the radio works"; checking the radio is the health check's job.
If the power dies before the marker, the next boot simply runs provisioning again. It rewrites the same
files, so running it twice is boring rather than harmful.

**Every boot after that.** No provisioning, just enabled units, in this order:

`NetworkManager` -> `piratebox-ap-ip` -> `hostapd` + `dnsmasq` -> `piratebox-ap-health`

`piratebox.service` and `piratebox-epaper.service` start alongside and do not depend on the radio.

### Why the radio state matters

Raspberry Pi OS ships with `WirelessEnabled=false` in `/var/lib/NetworkManager/NetworkManager.state`
until a Wi-Fi country is set, and NetworkManager re-applies that switch to the kernel rfkill **every
time it starts**, unmanaged interfaces included. Earlier versions of PirateBox only flipped it when
`raspi-config` accepted the `COUNTRY` from `piratebox.txt`. With `COUNTRY` empty (the shipped default)
or not a real ISO code (`UK` instead of `GB`), the first boot worked, because the setup script
unblocked the radio after restarting NetworkManager, and every boot after it was a race that
NetworkManager could win: hostapd running, `10.0.0.1` assigned, radio blocked, no SSID, and
`127.0.1.1` on the display. Provisioning now switches the saved state on, and `piratebox-ap-ip`
is ordered after NetworkManager, so there is nothing left to race.

### The health check

`piratebox-ap-health.service` runs `/usr/local/sbin/piratebox-ap-health` once per boot. It ignores
what the units claim and checks what the kernel says:

- `wlan0` exists and its rfkill switch is off,
- `10.0.0.1/24` is on `wlan0`,
- `hostapd` is enabled and running, and `iw dev wlan0 info` shows `type AP` with an SSID on air,
- `dnsmasq` is enabled and running.

It repairs only the failing part: `rfkill unblock wlan`, `ip addr replace`, `systemctl restart hostapd`
or `systemctl restart dnsmasq`. A running hostapd resumes by itself after an unblock, so it gets one
round to do that before being restarted. Four repair rounds, 15 seconds apart, then it gives up until
the next boot and the unit shows as failed. It never edits configuration, never re-runs first boot, and
leaves a unit alone if someone disabled it. Run it by hand any time:

```bash
sudo piratebox-ap-health --settle 0                 # check and repair
sudo piratebox-ap-health --settle 0 --attempts 0    # just check, touch nothing
systemctl status piratebox-ap-health
```

### Country code

`COUNTRY` must be a two-letter ISO 3166 code (`IT`, `US`, `DE`, `GB`, not `UK`). It goes to `raspi-config`
(kernel regulatory domain) and to `hostapd.conf` (`country_code=`). A code `raspi-config` rejects is
logged and still written to `hostapd.conf` as given; nothing is swapped for another country. Leaving it
empty leaves the kernel on its conservative world regulatory domain. The AP still works on channel 6,
but set the real country anyway; the radio is in your country, not in "the world".

## Where things live

- App: `/opt/piratebox` (venv at `/opt/piratebox/.venv`, using the system packages)
- Waveshare driver: `/opt/piratebox/vendor/waveshare_epd`
- Data: `/var/lib/piratebox/data`
- Upload spool: `/var/lib/piratebox/tmp` (because `/tmp` is RAM, and RAM is the one thing we lack)
- Settings: `/etc/default/piratebox`, `/etc/default/piratebox-epaper`
- First-boot settings: `/boot/firmware/piratebox.txt`
- First-boot log: `/var/log/piratebox-zero-firstboot.log`
- AP health log: `/var/log/piratebox-ap-health.log` (one previous generation kept as `.1`)
- Last boot's AP verdict: `/boot/firmware/piratebox-ap-status.txt` (FAT, readable from any computer)
- Live AP status for the display: `/run/piratebox/ap-status` (gone at every reboot, on purpose)

## Troubleshooting

### No PirateBox Wi-Fi

On the box (over SSH, if you can reach it some other way, or once the AP is back):

```bash
systemctl status piratebox-ap-health hostapd dnsmasq piratebox-ap-ip
tail -n 50 /var/log/piratebox-ap-health.log
rfkill list
grep WirelessEnabled /var/lib/NetworkManager/NetworkManager.state
iw dev wlan0 info
journalctl -b -u NetworkManager -u hostapd -u piratebox-ap-health
```

`rfkill list` showing `Soft blocked: yes` for the Wi-Fi together with `WirelessEnabled=false` is the
bug described above. `sudo nmcli radio wifi on` fixes it for good on a box set up by an older version.
To change `SSID` or `COUNTRY` later, edit `piratebox.txt` and re-run first boot on purpose:

```bash
sudo rm -f /var/lib/piratebox/.zero_firstboot_done
sudo systemctl start piratebox-zero-firstboot.service
```

That is for applying new settings. Recovering a radio is the health check's job and does not need it.

### No Wi-Fi, no console, only the SD card

Power the Pi off, take the card out, and put it in another computer.

**Any computer (Windows, macOS, Linux)** can read the FAT boot partition (`bootfs`):

- `piratebox-ap-status.txt` is the last boot's verdict: `STATE`, the problems seen, what was tried.
  If it is missing, either the box predates the health check or the health check never ran.
- `piratebox.txt`: check `COUNTRY`. Empty, or not an ISO code, is the classic trigger.
- `cmdline.txt`: `cfg80211.ieee80211_regdom=XX` at the end means `raspi-config` accepted a country.
  No such entry means it never did, and an older box will have been left with the radio switched off.

**A Linux machine** (or a Linux VM with the card passed through) can also read the root partition
(`rootfs`, ext4). Find the partition with `lsblk`; below it is `/dev/sdX2`, so check yours first.

```bash
sudo mkdir -p /mnt/piratebox
sudo mount /dev/sdX2 /mnt/piratebox
cat /mnt/piratebox/var/lib/NetworkManager/NetworkManager.state
cat /mnt/piratebox/var/lib/systemd/rfkill/*wlan*
tail -n 50 /mnt/piratebox/var/log/piratebox-zero-firstboot.log
tail -n 50 /mnt/piratebox/var/log/piratebox-ap-health.log
ls /mnt/piratebox/var/log/journal 2>/dev/null && \
  sudo journalctl -D /mnt/piratebox/var/log/journal -b -0 -u NetworkManager -u hostapd
```

The journal is only there if it was stored on disk (`/var/log/journal` exists). If not, it lived in RAM
and died with the last boot, which is why PirateBox keeps its own log files. `-b -0` is the last boot
recorded on the card; `--list-boots` shows the rest.

If `NetworkManager.state` says `WirelessEnabled=false`, switch it on and let the next boot clean up
after itself:

```bash
sudo sed -i 's/^WirelessEnabled=false/WirelessEnabled=true/' \
  /mnt/piratebox/var/lib/NetworkManager/NetworkManager.state
sync
sudo umount /mnt/piratebox
```

Put the card back and power up. This edits NetworkManager's own state file, the same line
`raspi-config` edits when it sets a country, and leaves everything else alone. Nothing to delete,
no marker to remove.

To bring a box set up by an older version up to date without internet: copy the new `scripts/`
folder over `/opt/piratebox/scripts/` (from the mounted card, or with `scp` once the AP is up), then
re-run the AP setup with your own SSID and country. The packages are already installed, so nothing is
downloaded; hostapd restarts, so an SSH session over the AP drops for a few seconds.

```bash
sudo /opt/piratebox/scripts/pi-setup.sh --iface wlan0 --ssid PirateBox --country IT
sudo systemctl restart piratebox-epaper
```

### Is it the AP or the e-paper?

The display reads the kernel directly and does not depend on the web app, so:

- A checklist with red boxes (`AP OFFLINE`, `DHCP DOWN`, `WEB APP DOWN`): the display is fine and is
  telling you which part is not. See above.
- `STARTING UP` / `FIRST BOOT SETUP` for more than about 5 minutes: first boot or the health check is
  stuck halfway, so nothing has declared a verdict. Read `piratebox-ap-status.txt` and the logs.
- `http://10.0.0.1` but no SSID on your phone: the AP was up at the last refresh. The panel
  redraws at most every 3 minutes, so give it one cycle, then believe the phone.
- Blank, frozen, or an uptime that never changes: that is the display. See below.
- `127.0.1.1`: an old `epaper_hat.py`. The new one does not print that address anywhere.

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
