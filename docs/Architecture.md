# Architecture

PirateBox is small on purpose. It is a FastAPI app with SQLite storage, a few templates, and some scripts to make Raspberry Pi behave. That is the whole magic trick.

## Repository layout

- `app/main.py` handles routes, templates, and HTTP responses.
- `app/db.py` handles SQLite access and file storage.
- `app/templates/` holds Jinja2 templates.
- `app/static/` holds CSS, JS, and images.
- `scripts/` contains setup scripts and systemd unit files.
- `tests/` contains pytest suites.
- `docs/` contains project documentation (published to GitHub Pages).

## Request flow

1. A device hits the Pi over Wi-Fi.
2. FastAPI handles the request.
3. Jinja2 renders HTML templates for pages.
4. API routes return JSON for chat and file lists.
5. `app/db.py` reads or writes SQLite rows.

## Data storage

- SQLite database at `PIRATEBOX_DB_PATH`.
- Uploaded files in `PIRATEBOX_FILES_DIR`.
- Defaults live under `/var/lib/piratebox/data` when you use the systemd service defaults.

## Captive portal behavior

The app answers common OS connectivity checks and redirects them to `/captive`. The acknowledgment cookie keeps devices from looping forever. It is a small lie to stop a bigger annoyance.

## Optional e-Paper status

The `scripts/epaper_hat.py` script reads system stats and PirateBox counts, then renders them to a 2.7" e-Paper HAT or a 2.13" tri-color one. It tries multiple driver libraries because hardware vendors are not required to coordinate their choices.

Pages draw on a paletted canvas (black, white, red) that gets split into one or two 1-bit layers at the end, so the same renderers serve mono and tri-color panels. Button callbacks only flip flags; the main loop owns the SPI bus.

## Pi Zero W edition

`scripts/zero/` holds a dedicated installer, an image builder, and a first-boot unit for the Pi Zero W. The installer pulls everything from apt (no pip, no compilers), and the image builder runs that same installer inside the image under qemu, so the resulting card boots offline. See [Pi Zero W Edition](Pi-Zero-W.md).

## What not to expect

- No external API calls.
- No accounts or authentication.
- No distributed systems. The Pi is enough for this size of ambition.
