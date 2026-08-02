---
title: Ripo Team Cloud PC
emoji: 🖥️
colorFrom: indigo
colorTo: blue
sdk: gradio
app_file: app.py
python_version: 3.12
fullWidth: true
header: mini
suggested_hardware: zerogpu
pinned: false
---

# Ripo Team Cloud PC

A browser-accessible Linux desktop built for a Hugging Face Gradio Space.

It starts:

- Xvfb virtual display
- Openbox plus an LXDE-style panel and file manager
- xterm and LXTerminal
- x11vnc VNC server
- noVNC static client
- FastAPI WebSocket-to-VNC proxy
- Gradio control panel
- optional Hermes Agent installer and gateway controls

## Secrets

Set these in the Space settings:

- `VNC_PASSWORD` — password entered in noVNC
- `ADMIN_TOKEN` — protects install/start/stop API actions

## Open the desktop

Visit:

```text
https://YOUR-SPACE.hf.space/novnc/vnc.html?autoconnect=true&resize=scale&path=websockify
```

The standard Hugging Face Space page opens the Ripo Team control panel. The GitHub Pages frontend embeds the noVNC URL directly.

## Hermes

Open the Linux terminal and run:

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --skip-browser
source ~/.bashrc
hermes setup
hermes gateway
```

The control panel can install Hermes automatically, but setup still requires your own model/provider credentials.
