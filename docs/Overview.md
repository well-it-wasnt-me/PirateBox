---
title: Overview
---

# Overview

PirateBox is a local-only sharing box for Raspberry Pi. It sets up an open Wi-Fi network and hosts a FastAPI web app that lets people exchange files, chat messages, and forum posts. Everything stays on the device. No cloud, no account system, no sense of permanence beyond a microSD card.

## Core features

- Open Wi-Fi access point with captive portal.
- File upload and download.
- Anonymous chat room.
- Threaded forum.
- SQLite persistence on the Pi.
- Optional e-Paper HAT status display.

## Why it exists

Because not every network needs to be online, and not every file needs to be indexed by someone else. PirateBox is for events, classrooms, workshops, or anywhere you want sharing without the internet doing what it does.

## What you need

- Raspberry Pi 4 or 5 (5 is recommended).
- microSD card, 16 GB minimum.
- USB power supply that is not lying about its amps.
- Optional USB Wi-Fi adapter if you want a dedicated AP radio.
- Optional e-Paper HAT for the status display.

## How it works

1. The Pi broadcasts an open SSID.
2. Devices connect and get routed to the PirateBox web app.
3. The app serves HTML pages and a small API for chat and files.
4. Data is stored in SQLite and the file system under `PIRATEBOX_DATA_DIR`.

## The reality check

- Open Wi-Fi means anyone can connect. That is the point and also the risk.
- The Pi is the server and the storage. If it goes down, so does your sharing box.
- The only backup is the one you actually take.
