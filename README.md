---
title: AdScreen Converter
emoji: 🎬
colorFrom: blue
colorTo: indigo
sdk: streamlit
sdk_version: 1.56.0
app_file: app.py
pinned: false
---

# AdScreen Converter

Automated multi-format video export for digital-out-of-home advertising.
Converts source creative into the exact dimensions, durations and bitrates
required by each mall screen template.

## Running locally

```bash
bash setup.sh   # first time only
bash run.sh
```

Opens at http://localhost:8501

## Hosting

Deployed as a Hugging Face Space (free CPU tier, 16 GB RAM).

- `requirements.txt` — Python dependencies
- `packages.txt` — apt packages (`ffmpeg`)
- `.streamlit/config.toml` — static file serving, used for downloads
- The YAML block at the top of this file is Space configuration; leave it in place.

Screen templates live in `templates/` as JSON, one file per mall screen.
