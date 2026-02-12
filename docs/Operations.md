# Operations

PirateBox is simple, but it still needs the usual sysadmin rituals: start, stop, check logs, and back things up before they vanish.

## Run with Docker

```bash
docker compose up -d --build
```

Stop:

```bash
docker compose down
```

Logs:

```bash
docker compose logs -f
```

## Run with systemd

Start:

```bash
sudo systemctl start piratebox.service
```

Stop:

```bash
sudo systemctl stop piratebox.service
```

Status:

```bash
systemctl status piratebox.service
```

Logs:

```bash
journalctl -u piratebox.service -f
```

## Backups

Your data lives in two places:

- SQLite database at `PIRATEBOX_DB_PATH`.
- Uploaded files at `PIRATEBOX_FILES_DIR`.

Back them up together. If you back up only one, you get a half-finished story.

## Upgrades

1. Stop the service.
2. Pull the latest code.
3. Reinstall requirements if needed.
4. Start the service.

Example:

```bash
sudo systemctl stop piratebox.service
cd /opt/piratebox
git pull
/opt/piratebox/.venv/bin/pip install -r requirements.txt
sudo systemctl start piratebox.service
```

## Health checks

- Open `http://10.0.0.1:8080` and ensure pages load.
- Upload and download a small file.
- Post a chat message and verify it appears.
- Create a forum thread and reply to it.

## Scheduled maintenance

- Reboot the Pi occasionally. It does not like being ignored forever.
- Check free disk space. Piracy fills storage fast.
- Keep a spare SD card with a known good image.
