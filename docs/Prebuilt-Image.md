# Prebuilt Image

PirateBox can be shipped as a Raspberry Pi OS image that runs a first-boot installer and starts the service automatically. It is the closest thing to a one-click install that does not require belief in magic.

## What the image includes

- Official Raspberry Pi OS Lite (64-bit) base image.
- PirateBox source under `/opt/piratebox`.
- A first-boot systemd unit that installs Python dependencies and enables the service.
- Default environment values in `/etc/default/piratebox`.

## Where to get it

- CI builds an `.img.xz` artifact when the workflow runs.
- You can build locally using `scripts/build_rpi_image.sh`.

## Build it yourself

```bash
./scripts/build_rpi_image.sh
```

The script will:

1. Download the official Raspberry Pi OS Lite image.
2. Mount the partitions.
3. Copy the PirateBox repo into `/opt/piratebox`.
4. Install the first-boot unit and environment defaults.
5. Repack the image as `piratebox-raspios-lite.img.xz`.

## Build script knobs

- `PIRATEBOX_RPI_IMAGE_URL` to override the official image URL.
- `PIRATEBOX_IMAGE_OUTDIR` to change the output directory.
- `PIRATEBOX_IMAGE_WORKDIR` to change the scratch space.
- `PIRATEBOX_IMAGE_KEEP_WORKDIR=1` to keep the scratch space for debugging.

## First boot behavior

The image runs `/usr/local/sbin/piratebox-firstboot` on the first boot. It will:

- Install `python3-venv` and `python3-pip`.
- Create a venv at `/opt/piratebox/.venv`.
- Install Python requirements.
- Enable and start `piratebox.service`.

## Temporary internet requirement

The first boot needs temporary internet access to install Python packages. After that, you can keep the network offline. If you boot without internet, the service will not start until you rerun the first-boot script or provide the packages manually. That is not a bug, it is physics.

## Customize defaults before first boot

Edit these files in the image if you want different defaults:

- `/etc/default/piratebox`
- `/etc/default/piratebox-epaper`

## Re-run first boot

If you need to rerun provisioning:

```bash
sudo rm -f /var/lib/piratebox/.firstboot_done
sudo systemctl start piratebox-firstboot.service
```
