---
title: Development
---

# Development

PirateBox is a small FastAPI app. Development is straightforward, which is a rare kindness.

## Requirements

- Python 3.11+.
- Virtualenv.
- Patience.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

## Run locally

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```

## Lint

```bash
ruff check .
```

## Test

```bash
pytest
```

## Semversioning

We use Python Semantic Release to cut semver tags based on conventional commits. It updates `CHANGELOG.md` and bumps `VERSION`. If your commit messages are chaotic, so will be the releases.

Expected flow:

1. Merge conventional commits into `main`.
2. CI runs the `Semversioning` job.
3. Python Semantic Release tags the release and updates the changelog.

## Style notes

- Keep route handlers small and push persistence into `app/db.py`.
- Avoid external network calls; the project is offline-first.
- Favor clear, small diffs over ambitious rewrites.
