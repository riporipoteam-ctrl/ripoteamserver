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
- Keyless GitHub-to-Hugging-Face deployment through OIDC Trusted Publishers
- GitHub Pages automatic deployment
- Post-deployment Space health verification
- Optional Cloudflare Worker deployment
- Hermes installer and gateway controls

## One-time private setup

In the Hugging Face Space settings:

1. Add a **GitHub Actions Trusted Publisher** for repository `riporipoteam-ctrl/ripoteamserver`, branch `main`, workflow `sync-huggingface.yml`.
2. Add Space secret `VNC_PASSWORD` with a strong password.
3. Add Space secret `ADMIN_TOKEN` with a separate long random value.

No permanent `HF_TOKEN` needs to be stored in GitHub. Never commit or paste passwords into public files.

Then run the GitHub workflow **Create or update Hugging Face Space**.

## Availability

GitHub Pages stays available as a static frontend. When opened, it checks and wakes the Hugging Face Space, waits while the Space starts, and reconnects to the Linux desktop.

The project does not generate artificial scheduled traffic solely to evade Hugging Face's free-tier sleep policy. Official uninterrupted never-sleep operation requires eligible upgraded hardware.

See [`docs/DEPLOY.md`](docs/DEPLOY.md) for the full setup.
