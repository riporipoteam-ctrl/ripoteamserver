---
title: Ripo Team Cloud PC
emoji: 🖥️
colorFrom: indigo
colorTo: blue
sdk: gradio
app_file: app_server_v2.py
python_version: 3.12
fullWidth: true
header: mini
suggested_hardware: zero-a10g
pinned: false
---

# Ripo Team Cloud PC

Ripo Team's browser-accessible Cloud PC and TikTok AI backend.

The Space starts through `app_server_v2.py`, which mounts the server-browser TikTok connection flow and the server-only TikTok LIVE broadcaster on top of the existing backend. TikTok Login Kit credentials remain configured through Hugging Face Variables and Secrets.
