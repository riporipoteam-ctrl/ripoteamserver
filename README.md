# Ripo Team Hugging Face Cloud PC

This project connects:

- **GitHub Pages** — the polished Ripo Team desktop and control centre
- **Hugging Face Gradio Space** — Linux x86-64 userspace and processes
- **noVNC** — clickable Linux desktop in a browser
- **Hermes Agent controls** — install, start, stop, logs and status
- **Cloudflare Worker** — optional always-reachable wake/proxy endpoint

## What is included

- Openbox desktop with app panel, file manager, terminals, text editor and Firefox
- Xvfb, x11vnc and noVNC
- FastAPI health/control endpoints
- VNC password and admin-token protection
- GitHub-to-Hugging-Face automatic deployment
- GitHub Pages automatic deployment
- Post-deployment Space health verification
- Optional Cloudflare Worker deployment
- Hermes installer and gateway controls

## Required private settings

Add these under **GitHub → Settings → Secrets and variables → Actions**:

- `HF_TOKEN` — Hugging Face write token
- `HF_VNC_PASSWORD` — a strong VNC password
- `HF_ADMIN_TOKEN` — a separate long random admin token

Never commit or paste these values into public files.

Then run the **Create or update Hugging Face Space** workflow.

## Availability

GitHub Pages stays available as a static frontend. When opened, it checks and wakes the Hugging Face Space, waits while the Space starts, and reconnects to the Linux desktop.

The project does not generate artificial scheduled traffic solely to evade Hugging Face's free-tier sleep policy. Official uninterrupted never-sleep operation requires eligible upgraded hardware.

See [`docs/DEPLOY.md`](docs/DEPLOY.md) for the full setup.
