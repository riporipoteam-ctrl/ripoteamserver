---
title: Ripo Team Cloud PC
emoji: 🖥️
colorFrom: indigo
colorTo: blue
sdk: gradio
app_file: app_server.py
python_version: 3.12
fullWidth: true
header: mini
suggested_hardware: zero-a10g
pinned: false
---

# Ripo Team Cloud PC

Ripo Team's browser-accessible Cloud PC and TikTok AI backend.

The Space starts the existing Cloud PC application through `app_server.py`, which mounts the TikTok control bridge plus the server-browser TikTok connection flow on top of the normal backend. TikTok Login Kit credentials remain configured through Hugging Face Variables and Secrets.
