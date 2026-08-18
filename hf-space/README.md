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

Ripo Team's browser-accessible Cloud PC, TikTok AI backend, and streamed Aug-25-2021 Rec Room runtime.

This Space stays on the free Gradio/ZeroGPU-compatible SDK. Rec Room uses a SHA256-pinned portable Wine amd64-wow64 runtime so the 64-bit game and the official 32-bit Windows Steam client can run without Debian i386/multilib packages.

TikTok Login Kit credentials remain configured through Hugging Face Variables and Secrets. Deployment verification checks the live server health and Rec Room runtime after rollout.
