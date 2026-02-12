---
title: FAQ
---

# FAQ

## Is PirateBox secure?

No. It is an open Wi-Fi by design. It is meant for local sharing, not secret keeping.

## Can I add a password?

You can, but that defeats the “walk up and share” idea. If you insist, configure `hostapd` with WPA2 like any other AP.

## Does it need the internet?

The app does not. The first boot image does need temporary internet access to install Python packages. After that, you can yank the cable.

## Why SQLite?

Because it is small, reliable, and boring. Those are virtues.

## Can I run it on something other than Raspberry Pi?

Yes. It is a FastAPI app, so it runs anywhere Python runs. The access point scripts are Pi-specific because hardware is not a fair fight.

## Where is the data stored?

SQLite at `PIRATEBOX_DB_PATH` and files under `PIRATEBOX_FILES_DIR`. If you forget, it is probably in `/var/lib/piratebox/data`.

## How do I reset everything?

Stop the app, delete the data directory, start again. It is the digital equivalent of lighting it on fire and pretending it never happened.
