# PirateBox

A local-only sharing box for Raspberry Pi 5. Connect to the open Wi-Fi, visit the local web page, and trade files, chat messages
or forum threads without any internet connection. It is the cloud-free future we were promised, minus the optimism.

## Features

- Open Wi-Fi access (no password required)
- File upload + download
- Anonymous chat room
- Threaded forum
- Captive portal welcome page (when served on port 80)
- SQLite persistence
- Dockerized Python app

## Stack

- Python (FastAPI)
- SQLite
- Docker / Docker Compose

## Quick start (Docker)

```bash
docker compose up -d --build
```

Visit `http://localhost:8080` from any device on the same network.

## Local dev (no Docker)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```

## Linting

```bash
pip install -r requirements.txt -r requirements-dev.txt
ruff check .
```

## Testing

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

## Raspberry Pi 5 setup

See `docs/pi-setup.md` for the Wi-Fi access point configuration and offline-only networking steps.
If you prefer a prebuilt image, the build script is in `scripts/build_rpi_image.sh`, and CI produces an `.img.xz` artifact.

## Wiki

Detailed docs live under `wiki/` and are published to GitHub Pages by CI.

## Optional e-Paper HAT status display

See `docs/epaper-hat.md` for the 2.7 inch e-Paper HAT module (logo + stats + buttons).

## Configuration

Environment variables (optional):

- `PIRATEBOX_NAME` (default: PirateBox)
- `PIRATEBOX_DATA_DIR` (default: `./data`)
- `PIRATEBOX_DB_PATH` (default: `/data/piratebox.db`)
- `PIRATEBOX_FILES_DIR` (default: `/data/files`)
- `PIRATEBOX_MAX_UPLOAD_MB` (default: `512`)
- `PIRATEBOX_MAX_MESSAGE_LEN` (default: `500`)
- `PIRATEBOX_MAX_THREAD_TITLE_LEN` (default: `120`)

## Data

When running locally, data is stored in `./data` by default. When running with Docker, files and the SQLite database are stored in `./data` on the host.

## Notes

This project is intentionally offline-first. No authentication is required, and all content stays on the local PirateBox network.

# Credits

> The PirateBox was designed in 2011 by David Darts, a professor at the Steinhardt School of Culture, Education and Human Development at New York University under Free Art License. 
> It has since become highly popular in Western Europe, particularly in France by Jean Debaecker, and its development is largely maintained by Matthias Strubel. 
> The usage of the PirateBox-Concept turns slowly away from common local filesharing to purposes in education, concerning public schools or private events like CryptoParties, 
> a crucial point also being circumvention of censorship since it can be operated behind strong physical barriers.
> On 17 November 2019, Matthias Strubel announced the closure of the Pirate Box project, citing more routers having locked firmware and browsers forcing https.
>> #### From [PirateBox - WikiPedia Page](https://en.wikipedia.org/wiki/PirateBox)
