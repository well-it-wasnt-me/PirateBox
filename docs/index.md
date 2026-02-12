---
title: PirateBox Docs
---

# PirateBox Docs

PirateBox is a local-only sharing box for Raspberry Pi. You join an open Wi-Fi, hit a local web page, and trade files, chat messages, or forum threads without the internet meddling in your business. It is a small rebellion against the cloud, and like most rebellions, it is mostly paperwork.

## Quick links

- [Overview](Overview.md)
- [Install on Raspberry Pi](Install-Raspberry-Pi.md)
- [Prebuilt Image](Prebuilt-Image.md)
- [Architecture](Architecture.md)
- [Configuration](Configuration.md)
- [Operations](Operations.md)
- [Troubleshooting](Troubleshooting.md)
- [E-Paper HAT](Epaper.md)
- [Development](Development.md)
- [FAQ](FAQ.md)

## What this is

PirateBox is an offline-first web app that runs on a Raspberry Pi and serves a captive portal, a file drop, a chat room, and a forum. Everything lives in SQLite on the device. No accounts, no cloud, no excuses.

## What this is not

- A secure dropbox for secrets. It is open Wi-Fi by design.
- A high-availability system. The Pi is the single point of failure and also the single point of truth.
- A substitute for backups. You still have to do those yourself.

## If you are in a hurry

1. Flash Raspberry Pi OS Lite (64-bit).
2. Run the setup script or follow the manual steps in [Install on Raspberry Pi](Install-Raspberry-Pi.md).
3. Connect to the PirateBox Wi-Fi and open `http://10.0.0.1:8080`.

If you want a prebuilt image, read [Prebuilt Image](Prebuilt-Image.md). It does more work up front so you can pretend you are efficient.
