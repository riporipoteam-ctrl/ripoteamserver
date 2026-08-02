# Ripo Team Hugging Face Cloud PC

This project combines:

- **GitHub Pages** — polished Ripo Team desktop/control centre
- **Hugging Face Gradio Space** — actual Linux x86-64 userspace and processes
- **noVNC** — clickable Linux desktop in the browser
- **Hermes Agent controls** — install, start, stop, logs and status
- **Cloudflare Worker** — optional always-reachable wake/proxy endpoint

## Why this version uses Gradio instead of Docker

Hugging Face Gradio Spaces support Python dependencies through `requirements.txt` and Debian packages through `packages.txt`. This project uses that to install and launch Xvfb, Openbox, xterm, x11vnc and noVNC without requiring a Docker SDK Space.

## What works

- Real remote Linux x86-64 environment on Hugging Face compute
- Clickable browser desktop
- Terminal and shell commands
- Python, Git, build tools and FFmpeg
- VNC password protection
- GitHub Pages dashboard
- Space wake/reconnect handling
- Hermes installation and gateway controls
- Optional Cloudflare proxy

## Important limits

- The Hugging Face filesystem is ephemeral unless you add external persistence.
- A free Space can sleep after inactivity; opening the site wakes it.
- Hermes requires your own supported model provider or API credentials.
- This is suitable for an HTTP/WebSocket agent service, not a FiveM server or arbitrary public UDP service.
- The project intentionally uses wake-on-demand rather than artificial traffic intended only to defeat provider sleep limits.

See [`docs/DEPLOY.md`](docs/DEPLOY.md).
